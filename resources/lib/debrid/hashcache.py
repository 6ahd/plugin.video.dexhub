# -*- coding: utf-8 -*-
"""What each service answered about a torrent, kept a while on this device.

  cached      the service has it: plays at once          (kept 6 hours)
  uncached    the service does not have it               (kept 2 hours)
  failed      it was tried and did not play               (kept 6 hours)
A play that works marks the torrent cached; one that does not marks it
failed, so the next search shows the truth without asking again.
"""
import json
import os
import threading
import time

from . import accounts

KEEP = {'cached': 6 * 3600, 'uncached': 2 * 3600, 'failed': 6 * 3600}
LIMIT = 6000          # per service
_LOCK = threading.RLock()
_STATE = {}


def _file():
    return accounts.path('debrid_hashes.json')


def _load():
    with _LOCK:
        if 'data' not in _STATE:
            try:
                with open(_file(), 'r', encoding='utf-8') as handle:
                    _STATE['data'] = json.load(handle) or {}
            except Exception:
                _STATE['data'] = {}
            _STATE['dirty'] = False
        return _STATE['data']


def flush():
    with _LOCK:
        if not _STATE.get('dirty'):
            return
        data = _load()
        now = time.time()
        for service in list(data):
            rows = data[service]
            for key in [k for k, v in rows.items() if now - v[1] > KEEP.get(v[0], 3600)]:
                rows.pop(key, None)
            if len(rows) > LIMIT:
                for key, _v in sorted(rows.items(), key=lambda kv: kv[1][1])[:len(rows) - LIMIT]:
                    rows.pop(key, None)
        try:
            target = _file()
            folder = os.path.dirname(target)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            with open(target + '.tmp', 'w', encoding='utf-8') as handle:
                json.dump(data, handle)
            os.replace(target + '.tmp', target)
        except Exception as exc:
            accounts.log('hash cache not saved: %s' % exc, True)
        _STATE['dirty'] = False


def get(service, info_hash):
    """'cached', 'uncached', 'failed' or None."""
    row = _load().get(service, {}).get(str(info_hash or '').lower())
    if not row:
        return None
    if time.time() - row[1] > KEEP.get(row[0], 3600):
        return None
    return row[0]


def put(service, info_hash, state, save=True):
    if not info_hash:
        return
    with _LOCK:
        _load().setdefault(service, {})[str(info_hash).lower()] = [state, int(time.time())]
        _STATE['dirty'] = True
    if save:
        flush()


def put_many(service, states):
    with _LOCK:
        rows = _load().setdefault(service, {})
        now = int(time.time())
        for info_hash, state in states.items():
            if info_hash:
                old = rows.get(info_hash.lower())
                # a play that failed outranks a list that says "cached"
                if old and old[0] == 'failed' and state == 'cached' and now - old[1] < KEEP['failed']:
                    continue
                rows[info_hash.lower()] = [state, now]
        _STATE['dirty'] = True
    flush()


def reset():
    with _LOCK:
        _STATE.clear()
