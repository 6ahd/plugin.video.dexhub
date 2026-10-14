# -*- coding: utf-8 -*-
"""Tiny adaptive provider performance index.

Keeps only rolling latency/success counters. It never stores titles, ids,
URLs, tokens or viewing activity. v5.10.95 updates the in-memory index on the
source worker and moves the JSON persistence off the first-result hot path.
"""
import atexit
import json
import os
import threading
import time

try:
    import xbmcvfs
    _BASE = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub')
except Exception:
    _BASE = os.path.expanduser('~/.kodi/userdata/addon_data/plugin.video.dexhub')

_PATH = os.path.join(_BASE, 'provider_performance.json')
_LOCK = threading.RLock()
_WRITE_LOCK = threading.Lock()
_CACHE = None
_MAX_ROWS = 96
_GEN = 0


def _provider_key(provider):
    p = provider or {}
    pid = str(p.get('id') or '').strip()
    if pid in ('__plex__', '__emby__', '__silo__', '__jellyfin__'):
        srv = p.get('_server') or {}
        return '%s:%s' % (pid, str(srv.get('id') or srv.get('name') or 'default'))
    return str(pid or p.get('base_url') or p.get('manifest_url') or p.get('name') or 'unknown')[:240]


def _load():
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        try:
            with open(_PATH, 'r', encoding='utf-8') as fh:
                raw = json.load(fh)
            _CACHE = raw if isinstance(raw, dict) else {}
        except Exception:
            _CACHE = {}
        return _CACHE


def _write_snapshot(data):
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        tmp = _PATH + '.tmp'
        with _WRITE_LOCK:
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(',', ':'))
            os.replace(tmp, _PATH)
    except Exception:
        pass


def _save_latest():
    """Coalesce a burst of provider completions into one small disk write."""
    # Let the other provider futures that completed in the same burst update
    # the shared in-memory table first.
    time.sleep(0.05)
    while True:
        with _LOCK:
            gen = _GEN
            data = {k: dict(v) if isinstance(v, dict) else v
                    for k, v in (_load() or {}).items()}
        _write_snapshot(data)
        with _LOCK:
            if gen == _GEN:
                return


def _queue_save():
    try:
        from ..runtime_tasks import submit_optional
        if submit_optional(_save_latest, key='provider-stats-save'):
            return
    except Exception:
        pass
    # Persistence is advisory. Do not synchronously stall the source worker;
    # atexit will flush the latest in-memory state if no optional slot exists.


def _flush_sync():
    try:
        with _LOCK:
            if _CACHE is None:
                return
            data = {k: dict(v) if isinstance(v, dict) else v
                    for k, v in _CACHE.items()}
        _write_snapshot(data)
    except Exception:
        pass


atexit.register(_flush_sync)


def record(provider, elapsed, success, result_count=0):
    global _GEN
    key = _provider_key(provider)
    now = int(time.time())
    with _LOCK:
        data = _load()
        row = dict(data.get(key) or {})
        samples = int(row.get('samples') or 0)
        old_ms = float(row.get('avg_ms') or 0.0)
        ms = max(1.0, min(float(elapsed or 0.0) * 1000.0, 120000.0))
        alpha = 0.35 if samples < 8 else 0.18
        row['avg_ms'] = round(ms if old_ms <= 0 else (old_ms * (1.0 - alpha) + ms * alpha), 1)
        row['samples'] = min(samples + 1, 100000)
        row['successes'] = min(int(row.get('successes') or 0) + (1 if success else 0), 100000)
        row['failures'] = min(int(row.get('failures') or 0) + (0 if success else 1), 100000)
        row['last_count'] = max(0, int(result_count or 0))
        row['updated_at'] = now
        data[key] = row
        if len(data) > _MAX_ROWS:
            keep = sorted(data.items(), key=lambda kv: int((kv[1] or {}).get('updated_at') or 0), reverse=True)[:_MAX_ROWS]
            data.clear(); data.update(keep)
        _GEN += 1
    _queue_save()


def score(provider):
    row = (_load().get(_provider_key(provider)) or {})
    samples = int(row.get('samples') or 0)
    if samples <= 0:
        # Native providers start early until real device measurements exist.
        pid = str((provider or {}).get('id') or '')
        return 600.0 if pid in ('__plex__', '__emby__', '__silo__', '__jellyfin__') else 1000.0
    success = int(row.get('successes') or 0)
    rate = float(success) / float(max(1, samples))
    latency = float(row.get('avg_ms') or 1500.0)
    empty_penalty = 500.0 if int(row.get('last_count') or 0) <= 0 else 0.0
    return latency + ((1.0 - rate) * 3000.0) + empty_penalty


def order_targets(targets):
    indexed = list(enumerate(targets or []))
    indexed.sort(key=lambda item: (score((item[1] or ({}, ''))[0]), item[0]))
    return [target for _, target in indexed]


def clear():
    global _CACHE, _GEN
    with _LOCK:
        _CACHE = {}
        _GEN += 1
        try:
            os.remove(_PATH)
        except Exception:
            pass
