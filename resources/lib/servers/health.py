# -*- coding: utf-8 -*-
"""Small local health router for Plex/Emby/Jellyfin/Silo servers.

It prevents a repeatedly offline server from consuming every search budget,
while never deleting accounts or disabling a server permanently.
"""
from __future__ import absolute_import
import atexit
import json
import os
import threading
import time

try:
    import xbmcvfs
    _PROFILE = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/')
except Exception:
    _PROFILE = ''
_PATH = os.path.join(_PROFILE, 'server_health.json') if _PROFILE else ''
_LOCK = threading.RLock()
_CACHE = None
_WRITE_LOCK = threading.Lock()
_SAVE_GEN = 0
_MAX = 64
# v5.10.91: bumped once to wipe empty streaks recorded before cut-short scans
# stopped counting as empty (see record_cut).
_SCHEMA = 2
_META = '__meta__'


def server_key(backend, server):
    server = server or {}
    ident = (server.get('id') or server.get('machine_id') or server.get('server_id')
             or server.get('url') or server.get('name') or 'unknown')
    return '%s:%s' % (str(backend or 'server').lower(), str(ident))


def _load():
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        data = {}
        try:
            if _PATH and os.path.exists(_PATH):
                with open(_PATH, 'r', encoding='utf-8') as fh:
                    raw = json.load(fh)
                    if isinstance(raw, dict): data = raw
        except Exception:
            data = {}
        _CACHE = data
        _migrate(data)
        return _CACHE


def _migrate(data):
    """v5.10.91: start every empty streak from zero, once.

    Up to 5.10.90 a lookup stopped by its time limit returned no streams and
    was recorded as a completed scan that found nothing. A slow server (the
    DexMedia log: id queries cut at 6s, 21s, 6s, 6s, 6s) therefore climbed the
    empty streak, stayed cold, kept the 6s budget, was cut again, and could
    never show a title it actually has. Those streaks cannot be told apart
    from genuine ones now, so they are cleared once; a server that really has
    nothing is cold again after five completed empty scans.
    """
    try:
        meta = data.get(_META) if isinstance(data.get(_META), dict) else {}
        if int(meta.get('v') or 0) >= _SCHEMA:
            return
        for key, row in list(data.items()):
            if key == _META or not isinstance(row, dict):
                continue
            row['empty_streak'] = 0
        # far-future last_seen so pruning in _save never drops the marker
        data[_META] = {'v': _SCHEMA, 'last_seen': 4102444800}
        _save(data)
    except Exception:
        pass


def _write_snapshot(data):
    if not _PATH:
        return
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        tmp = _PATH + '.tmp'
        with _WRITE_LOCK:
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(',', ':'))
            os.replace(tmp, _PATH)
    except Exception:
        pass


def _save_worker():
    # Coalesce native servers that finish in the same source-search burst.
    time.sleep(0.05)
    while True:
        with _LOCK:
            gen = _SAVE_GEN
            if _CACHE is None:
                return
            snap = {k: dict(v) if isinstance(v, dict) else v
                    for k, v in _CACHE.items()}
        _write_snapshot(snap)
        with _LOCK:
            if gen == _SAVE_GEN:
                return


def _queue_save():
    try:
        from ..runtime_tasks import submit_optional
        submit_optional(_save_worker, key='server-health-save')
    except Exception:
        pass


def _flush_sync():
    try:
        with _LOCK:
            if _CACHE is None:
                return
            snap = {k: dict(v) if isinstance(v, dict) else v
                    for k, v in _CACHE.items()}
        _write_snapshot(snap)
    except Exception:
        pass


atexit.register(_flush_sync)


def _save(data):
    """Update the tiny health table now; persist it off the source worker.

    Server standing is consumed from memory during this interpreter, so disk
    I/O is not required before a Plex/Emby/Jellyfin/Silo result can reach the
    picker.  The latest state is also flushed synchronously at interpreter
    shutdown for persistence across Kodi invocations.
    """
    global _CACHE, _SAVE_GEN
    if not _PATH:
        return
    try:
        if len(data) > _MAX:
            ordered = sorted(data.items(), key=lambda x: float((x[1] or {}).get('last_seen') or 0), reverse=True)
            data = dict(ordered[:_MAX])
            _CACHE = data
        _SAVE_GEN += 1
        _queue_save()
    except Exception:
        pass


def should_query(backend, server, now=None):
    row = _load().get(server_key(backend, server), {})
    return float((row or {}).get('cooldown_until') or 0) <= float(now or time.time())


def record_result(backend, server, elapsed, success, result_count=0):
    now = time.time()
    key = server_key(backend, server)
    with _LOCK:
        data = _load()
        row = dict(data.get(key) or {})
        old = float(row.get('latency_ms') or 0)
        current = max(1.0, float(elapsed or 0) * 1000.0)
        row['latency_ms'] = round(current if old <= 0 else (old * 0.7 + current * 0.3), 1)
        row['last_seen'] = now
        if success:
            row['successes'] = int(row.get('successes') or 0) + 1
            row['failures'] = 0
            row['last_success'] = now
            row['cooldown_until'] = 0
            row['last_results'] = int(result_count or 0)
            # v5.10.15: a server that answers correctly but never HAS the
            # content is not a failure, and the failure cooldown must not apply
            # to it — the user may add that library tomorrow. But it is not
            # free either: the 5.10.14 log shows one Plex server spending 18s
            # per search walking library sections and matching titles, then
            # returning zero, on every single search. Track the empty streak so
            # such a server can be scheduled last and given a tighter budget,
            # while a single hit resets it to full standing immediately.
            if int(result_count or 0) > 0:
                row['empty_streak'] = 0
                row['last_hit'] = now
            else:
                row['empty_streak'] = int(row.get('empty_streak') or 0) + 1
            # v5.10.91: a scan that ran to the end proves the server can finish.
            row['slow_streak'] = 0
        else:
            failures = int(row.get('failures') or 0) + 1
            row['failures'] = failures
            # 15s, 30s, 60s, then max 2 minutes. Temporary only.
            row['cooldown_until'] = now + min(120, 15 * (2 ** min(failures - 1, 3)))
        data[key] = row
        _save(data)


def record_cut(backend, server, elapsed):
    """v5.10.91: the lookup was stopped by its time limit before it finished.

    That says the server is slow, not that it lacks the title, so the empty
    streak is left exactly as it was. A separate slow streak lets a server
    that keeps running out of time take the short budget and sort last,
    without ever being called empty. A lookup stopped because the user picked
    a source is not recorded at all (see source_engine).
    """
    now = time.time()
    key = server_key(backend, server)
    with _LOCK:
        data = _load()
        row = dict(data.get(key) or {})
        old = float(row.get('latency_ms') or 0)
        current = max(1.0, float(elapsed or 0) * 1000.0)
        row['latency_ms'] = round(current if old <= 0 else (old * 0.7 + current * 0.3), 1)
        row['last_seen'] = now
        row['last_cut'] = now
        row['slow_streak'] = int(row.get('slow_streak') or 0) + 1
        if row['slow_streak'] == SLOW_AFTER_CUTS:
            # entering the slow state: the next full-length try is a probe
            # SLOW_PROBE_SECONDS from now, not the very next search.
            row['last_probe'] = now
        data[key] = row
        _save(data)


COLD_AFTER_EMPTY = 5
COLD_BUDGET_SECONDS = 6.0
# v5.10.91: two lookups in a row stopped by the time limit make a server
# "slow": it gets the short budget like a cold one, but once every
# SLOW_PROBE_SECONDS it is given one full-length attempt so a server that got
# faster (or a library that was added) is noticed without resetting anything.
SLOW_AFTER_CUTS = 2
SLOW_PROBE_SECONDS = 1800.0


def slow_streak(backend, server):
    row = _load().get(server_key(backend, server), {}) or {}
    return int(row.get('slow_streak') or 0)


def standing(backend, server, full_seconds=0.0):
    """(budget_seconds, reason, streak) for one lookup, decided once.

    reason is '' (normal limit), 'empty' (cold: nothing found five scans
    running), 'slow' (stopped by the limit twice running) or 'probe' (a slow
    server's periodic full-length attempt). budget 0 means the normal limit.
    """
    key = server_key(backend, server)
    with _LOCK:
        data = _load()
        row = data.get(key) or {}
        empty = int(row.get('empty_streak') or 0)
        slow = int(row.get('slow_streak') or 0)
        if empty >= COLD_AFTER_EMPTY:
            return COLD_BUDGET_SECONDS, 'empty', empty
        if slow >= SLOW_AFTER_CUTS:
            now = time.time()
            if now - float(row.get('last_probe') or 0) >= SLOW_PROBE_SECONDS:
                row = dict(row)
                row['last_probe'] = now
                data[key] = row
                _save(data)
                return float(full_seconds or 0.0), 'probe', slow
            return COLD_BUDGET_SECONDS, 'slow', slow
    return float(full_seconds or 0.0), '', 0


def empty_streak(backend, server):
    row = _load().get(server_key(backend, server), {}) or {}
    return int(row.get('empty_streak') or 0)


def is_cold(backend, server):
    """True for a healthy server that has returned nothing many times running
    or (v5.10.91) keeps running out of time."""
    return (empty_streak(backend, server) >= COLD_AFTER_EMPTY
            or slow_streak(backend, server) >= SLOW_AFTER_CUTS)


def lookup_budget(backend, server, default_seconds=0.0):
    """Seconds a native lookup may spend on this server, 0 meaning unlimited.

    Only cold servers are capped. The cap is deliberately generous enough for
    a direct GUID/external-id match — the path that actually finds things —
    and only cuts the exhaustive title-scan fallback short.
    """
    if is_cold(backend, server):
        return COLD_BUDGET_SECONDS
    return float(default_seconds or 0.0)


def score(backend, server):
    row = _load().get(server_key(backend, server), {}) or {}
    cooldown = 1 if float(row.get('cooldown_until') or 0) > time.time() else 0
    failures = int(row.get('failures') or 0)
    latency = float(row.get('latency_ms') or 1500)
    has_results = 0 if int(row.get('last_results') or 0) > 0 else 1
    # Cold servers sort behind every warm one but stay in the scan, so a newly
    # added library still surfaces without the user resetting anything.
    cold = 1 if (int(row.get('empty_streak') or 0) >= COLD_AFTER_EMPTY
                 or int(row.get('slow_streak') or 0) >= SLOW_AFTER_CUTS) else 0
    return (cooldown, cold, failures, has_results, latency)


def order_native_targets(targets):
    def _key(target):
        provider = (target or ({}, ''))[0] or {}
        pid = provider.get('id')
        if pid == '__plex__': backend = 'plex'
        elif pid == '__emby__': backend = 'emby'
        elif pid == '__jellyfin__': backend = 'jellyfin'
        elif pid == '__silo__': backend = 'silo'
        else: return (0, 0, 0, 0)
        return score(backend, provider.get('_server') or {})
    return sorted(list(targets or []), key=_key)
