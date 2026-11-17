# -*- coding: utf-8 -*-
"""Short-lived, in-process source result sessions.

The source scanner and ``SourcesWindow`` live in the same Python interpreter.
Persisting every growing result snapshot to SQLite therefore added JSON
encoding, a database connection and a commit after each provider without
providing any recovery benefit.  A bounded condition-backed store is both
lighter and lets the picker sleep until a real update arrives instead of
polling the cache database several times per second.
"""
import threading
import time
import uuid


_TTL_SECONDS = 30 * 60
_MAX_SESSIONS = 16
_LOCK = threading.RLock()
_CHANGED = threading.Condition(_LOCK)
_SESSIONS = {}
_CANCEL_CALLBACKS = {}
# v5.10.447: late rows (Kodi source add-ons search for longer than the shared
# race waits): kept per session and merged into every snapshot, so the live
# refresh worker's own updates never drop them; while any is still searching
# the session is not done
_LATE = {}          # session key -> {'entries': [...], 'pending': {name: deadline}, 'done': bool}


def _copy_payload(payload):
    value = dict(payload or {})
    value['entries'] = list(value.get('entries') or [])
    return value


def _purge_locked(now=None):
    now = float(now or time.monotonic())
    stale = [key for key, state in _SESSIONS.items()
             if now - float(state.get('_touched') or 0.0) >= _TTL_SECONDS]
    for key in stale:
        _SESSIONS.pop(key, None)
        _CANCEL_CALLBACKS.pop(key, None)
        _LATE.pop(key, None)
    if len(_SESSIONS) <= _MAX_SESSIONS:
        return
    oldest = sorted(
        _SESSIONS,
        key=lambda key: float((_SESSIONS.get(key) or {}).get('_touched') or 0.0),
    )
    for key in oldest[:max(0, len(_SESSIONS) - _MAX_SESSIONS)]:
        _SESSIONS.pop(key, None)
        _CANCEL_CALLBACKS.pop(key, None)


def create(entries, done=False, version=1):
    key = uuid.uuid4().hex
    payload = {
        'entries': list(entries or []),
        'done': bool(done),
        'version': int(version or 1),
        '_touched': time.monotonic(),
    }
    with _CHANGED:
        _SESSIONS[key] = payload
        _CANCEL_CALLBACKS[key] = []
        _purge_locked(payload['_touched'])
        _CHANGED.notify_all()
    return key


def _merge_late_locked(session_key, value):
    """The snapshot as the page sees it: the search's own rows, then the Kodi
    add-ons' rows; not done while one of them is still searching."""
    late = _LATE.get(session_key)
    if not late:
        return value
    seen = {str(e.get('stream_key') or id(e)) for e in value['entries'] if isinstance(e, dict)}
    value['entries'] = value['entries'] + [
        e for e in late['entries'] if str(e.get('stream_key') or id(e)) not in seen]
    now = time.monotonic()
    pending = [name for name, deadline in late['pending'].items() if deadline > now]
    if pending:
        value['done'] = False
        if not int(value.get('search_pending') or 0):
            value['search_last'] = ', '.join(pending)
        value['search_pending'] = int(value.get('search_pending') or 0) + len(pending)
    return value


def update(session_key, payload):
    if not session_key:
        return False
    value = _copy_payload(payload)
    value['_touched'] = time.monotonic()
    with _CHANGED:
        current = _SESSIONS.get(session_key)
        # A late provider completion must never replace a closed session and
        # silently make it active again.  This was the hole that allowed work
        # and UI updates to continue after Back.
        if current is None or bool(current.get('_closed')):
            return False
        if session_key in _LATE:
            _LATE[session_key]['base'] = _copy_payload(value)
            value = _merge_late_locked(session_key, value)
            # one counter for both writers: every snapshot is new to the page
            value['version'] = int(current.get('version') or 0) + 1
        _SESSIONS[session_key] = value
        _purge_locked(value['_touched'])
        _CHANGED.notify_all()
    return True


def late_begin(session_key, names, seconds, base_done=False):
    """Kodi add-ons that answer this session later (up to ``seconds``).
    base_done: no other search is still running for this session."""
    if not session_key:
        return False
    deadline = time.monotonic() + float(seconds)
    with _CHANGED:
        state = _SESSIONS.get(session_key)
        if state is None:
            return False
        late = _LATE.setdefault(session_key, {'entries': [], 'pending': {}, 'base': _copy_payload(state)})
        if base_done:
            late['base']['done'] = True
        for name in names:
            late['pending'][name] = deadline
        _republish_locked(session_key)
    return True


def late_add(session_key, name, entries, finished=True):
    """One Kodi add-on's rows: into the open page at once."""
    with _CHANGED:
        state = _SESSIONS.get(session_key)
        late = _LATE.get(session_key)
        if state is None or late is None or bool(state.get('_closed')):
            return False
        late['entries'].extend(e for e in entries or [] if isinstance(e, dict))
        if finished:
            late['pending'].pop(name, None)
        _republish_locked(session_key)
    return True


def _republish_locked(session_key):
    state = _SESSIONS.get(session_key)
    late = _LATE.get(session_key)
    if state is None or late is None:
        return
    merged = _merge_late_locked(session_key, _copy_payload(late['base']))
    merged['version'] = int(state.get('version') or 1) + 1
    merged['_touched'] = time.monotonic()
    if state.get('_closed'):
        merged['_closed'] = True
        merged['done'] = True
    _SESSIONS[session_key] = merged
    _CHANGED.notify_all()


def get(session_key, default=None):
    if not session_key:
        return default
    with _LOCK:
        _purge_locked()
        value = _SESSIONS.get(session_key)
        if value is None:
            return default
        value['_touched'] = time.monotonic()
        return _copy_payload(value)


def is_active(session_key):
    """True while the session's window still wants results.

    v5.10.15: background refresh workers outlived their window. A slow Plex
    server answering 25s after the user backed out still appended rows, wrote
    to a dead session and touched a destroyed window ("EXCEPTION: Window id
    does not exist"), and the invoker it belonged to was still running when the
    next click arrived — Kodi logged "waiting on thread" and then failed to
    resolve the item. Workers poll this every iteration and stop cleanly.

    An empty key means the caller is not session-tracked at all; report active
    so no existing untracked path is silently cut short.
    """
    if not session_key:
        return True
    with _LOCK:
        value = _SESSIONS.get(session_key)
        if value is None:
            return False
        return not bool(value.get('_closed'))


def close(session_key):
    """Signal every worker on this session to stop, keeping the last snapshot.

    The rows stay readable so a window that is mid-teardown can still paint,
    but ``is_active`` flips immediately and the waiters wake at once.
    """
    if not session_key:
        return False
    callbacks = []
    with _CHANGED:
        value = _SESSIONS.get(session_key)
        if value is None:
            return False
        value['_closed'] = True
        value['done'] = True
        value['_touched'] = time.monotonic()
        callbacks = list(_CANCEL_CALLBACKS.pop(session_key, []) or [])
        _CHANGED.notify_all()
    # Never invoke arbitrary cancellation code while holding the session lock.
    for callback in callbacks:
        try:
            callback()
        except Exception:
            pass
    return True


def register_cancel(session_key, callback):
    """Run ``callback`` immediately when this session is closed.

    Registration happens as the live worker takes ownership of its parallel
    race.  If Back won the race and closed the session first, invoke the
    callback here so queued futures are still cancelled deterministically.
    """
    if not session_key or not callable(callback):
        return False
    run_now = False
    with _LOCK:
        state = _SESSIONS.get(session_key)
        if state is None or bool(state.get('_closed')):
            run_now = True
        else:
            _CANCEL_CALLBACKS.setdefault(session_key, []).append(callback)
    if run_now:
        try:
            callback()
        except Exception:
            pass
        return False
    return True


def delete(session_key):
    """Drop a session outright. ``close`` is preferred while a window may read."""
    if not session_key:
        return False
    with _CHANGED:
        existed = _SESSIONS.pop(session_key, None) is not None
        _CANCEL_CALLBACKS.pop(session_key, None)
        _CHANGED.notify_all()
    return existed


def wait_for_update(session_key, since_version=0, timeout=0.5, default=None):
    """Sleep until ``version`` changes, then return the newest snapshot.

    The wait is signalled directly by the source refresh worker.  It has no
    disk I/O and wakes immediately on a provider result; ``timeout`` merely
    lets the dialog's shutdown flag be checked periodically.
    """
    if not session_key:
        return default
    deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
    with _CHANGED:
        _purge_locked()
        while True:
            value = _SESSIONS.get(session_key)
            if value is None:
                return default
            try:
                version = int(value.get('version') or 0)
            except Exception:
                version = 0
            if version != int(since_version or 0):
                value['_touched'] = time.monotonic()
                return _copy_payload(value)
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return default
            _CHANGED.wait(remaining)
