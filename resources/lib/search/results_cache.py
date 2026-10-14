# -*- coding: utf-8 -*-
"""Short-lived per-provider source results.

v5.10.95 keeps the 5.10.84 cache but removes it from the first-result hot path:
non-empty answers become available in a small in-memory LRU immediately and
persistence is queued as optional background work.  Disk cleanup is throttled
instead of walking the cache directory after every provider response.
"""
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict

_PURGE_AFTER = 3600.0
_PURGE_INTERVAL = 600.0
_MEM_MAX = 32
_MEM = OrderedDict()
_MEM_LOCK = threading.RLock()
_DISK_LOCK = threading.Lock()
_LAST_PURGE = 0.0


def _minutes():
    try:
        import xbmcaddon
        return float(xbmcaddon.Addon().getSetting('source_results_cache_minutes') or 5)
    except Exception:
        return 5.0


def _folder():
    import xbmcaddon
    import xbmcvfs
    path = os.path.join(xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile')), 'source_results')
    os.makedirs(path, exist_ok=True)
    return path


def key(provider, media_type, request_id):
    provider = provider or {}
    ident = {
        'schema': 2,
        'p': str(provider.get('id') or provider.get('manifest_url') or provider.get('name') or ''),
        's': str((provider.get('_server') or {}).get('id') or ''),
        'm': str(media_type or ''),
        'r': str(request_id or ''),
        'c': [str(x) for x in (provider.get('_candidate_request_ids') or [])],
    }
    # Scope to connection/account identity, not just the stable source ID.
    # Only its digest reaches the filename; credentials are never logged.
    connection = {k: v for k, v in provider.items() if not k.startswith('_')}
    server = provider.get('_server') or {}
    connection['server'] = {k: server.get(k) for k in
        ('id', 'url', 'user_id', 'profile_id', 'token', 'profile_token', 'access_token')}
    ident['account'] = hashlib.sha256(json.dumps(connection, sort_keys=True,
                                                default=str).encode('utf-8')).hexdigest()
    if ident['s']:
        # Native targets identify the item through their own fields.
        ident['n'] = {k: v for k, v in provider.items()
                      if k.startswith('_') and k not in ('_server', '_force_stream_refresh',
                                                         '_resolved_request_id', '_candidate_request_ids')}
    if not ident['p']:
        return ''
    return hashlib.sha1(json.dumps(ident, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def _mem_get(cache_key, ttl):
    with _MEM_LOCK:
        entry = _MEM.get(cache_key)
        if not entry:
            return None
        if time.time() - float(entry.get('at') or 0) > ttl:
            _MEM.pop(cache_key, None)
            return None
        _MEM.move_to_end(cache_key)
        return entry


def _mem_put(cache_key, entry):
    with _MEM_LOCK:
        _MEM[cache_key] = entry
        _MEM.move_to_end(cache_key)
        while len(_MEM) > _MEM_MAX:
            _MEM.popitem(last=False)


def get(cache_key):
    ttl = _minutes() * 60.0
    if not cache_key or ttl <= 0:
        return None
    entry = _mem_get(cache_key, ttl)
    if entry is not None:
        return entry
    try:
        with open(os.path.join(_folder(), cache_key + '.json'), 'r', encoding='utf-8') as fh:
            entry = json.load(fh)
    except Exception:
        return None
    if time.time() - float(entry.get('at') or 0) > ttl:
        return None
    _mem_put(cache_key, entry)
    return entry


def _persist(cache_key, entry):
    global _LAST_PURGE
    try:
        folder = _folder()
        path = os.path.join(folder, cache_key + '.json')
        tmp = path + '.tmp'
        with _DISK_LOCK:
            text = json.dumps(entry, default=str, separators=(',', ':'))
            with open(tmp, 'w', encoding='utf-8') as fh:
                fh.write(text)
            os.replace(tmp, path)
            now = time.time()
            if now - _LAST_PURGE >= _PURGE_INTERVAL:
                _LAST_PURGE = now
                for name in os.listdir(folder):
                    if not name.endswith('.json'):
                        continue
                    full = os.path.join(folder, name)
                    try:
                        if now - os.path.getmtime(full) > _PURGE_AFTER:
                            os.remove(full)
                    except Exception:
                        pass
    except Exception:
        pass


def put(cache_key, payload, resolved_request_id=''):
    """Publish now, persist later.

    Cache persistence is an optimization, never a prerequisite for returning a
    source row.  The worker that fetched a provider therefore does not wait on
    JSON serialization, fsync/rename or a directory purge before the picker can
    display that provider's results.
    """
    if not cache_key or _minutes() <= 0 or not isinstance(payload, dict):
        return
    if not (payload.get('streams') or []):
        return
    entry = {
        'at': time.time(),
        'payload': payload,
        'resolved_request_id': resolved_request_id or '',
    }
    _mem_put(cache_key, entry)
    try:
        from ..runtime_tasks import submit_optional
        accepted = submit_optional(
            _persist, args=(cache_key, entry),
            key='source-results-cache:%s' % cache_key)
        if accepted:
            return
        # A full optional queue must never delay source delivery. The memory
        # entry is already valid for this interpreter; persistence can be
        # skipped for this one response.
        return
    except Exception:
        # Compatibility fallback for unusual import contexts where the shared
        # optional runner is unavailable.
        _persist(cache_key, entry)
