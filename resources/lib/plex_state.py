# -*- coding: utf-8 -*-
"""Durable Plex account/server state, isolated from networking and UI.

One source of truth for Plex persistence.  A temporary empty Kodi setting or
warm interpreter must never look like logout, and cached server credentials
remain usable for native search when plex.tv is temporarily unavailable.
"""
from __future__ import absolute_import
import json, os, time
try:
    import xbmcvfs
except Exception:
    xbmcvfs = None

_BASE_SPECIAL = 'special://profile/addon_data/plugin.video.dexhub/'


def profile_dir():
    try:
        path = xbmcvfs.translatePath(_BASE_SPECIAL) if xbmcvfs is not None else ''
        if path:
            os.makedirs(path, exist_ok=True)
            return path
    except Exception:
        pass
    return ''


def _path(name):
    base = profile_dir()
    return os.path.join(base, name) if base else ''


def _read_json(name, default):
    path = _path(name)
    if not path:
        return default
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            value = json.load(fh)
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default


def _write_json(name, value):
    path = _path(name)
    if not path:
        return False
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(value, fh, ensure_ascii=False, separators=(',', ':'))
            fh.flush()
            try: os.fsync(fh.fileno())
            except Exception: pass
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception: pass
        return False


def load_account():
    value = _read_json('plex_account.json', {})
    return value if value.get('token') else {}


def save_account(value):
    return _write_json('plex_account.json', dict(value or {}))


def clear_account():
    for name in ('plex_account.json', 'plex_servers.json'):
        try:
            path = _path(name)
            if path and os.path.exists(path): os.remove(path)
        except Exception: pass


def load_server_cache():
    return _read_json('plex_servers.json', {})


def save_server_cache(servers, fetched_at=None):
    return _write_json('plex_servers.json', {
        'fetched_at': int(fetched_at or time.time()),
        'servers': list(servers or []),
    })


def cached_servers():
    value = load_server_cache()
    return list(value.get('servers') or [])


def has_usable_cached_servers():
    return any((s or {}).get('token') and (s or {}).get('connections')
               for s in cached_servers())
