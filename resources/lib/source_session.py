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
        _SESSIONS[session_key] = value
        _purge_locked(value['_touched'])
        _CHANGED.notify_all()
    return True


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
