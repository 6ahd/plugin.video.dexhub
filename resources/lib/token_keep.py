# -*- coding: utf-8 -*-
"""Account sign-ins kept in the add-on's own settings too (v5.10.337).

The Nuvio, Stremio and Trakt sign-ins live in JSON files in the add-on's
profile folder. On Apple TV, Kodi keeps that folder under Library/Caches,
which tvOS may empty when it needs space; only the settings files survive.
A lost token signed the account out quietly: what was watched stopped
reaching Nuvio, and the library mirrored from it was gone. Each token is
also written to a hidden setting, and read back when its file is missing.
"""
import base64
import json
import time

_IDS = {'nuvio': 'nuvio_token_keep', 'stremio': 'stremio_token_keep',
        'trakt': 'trakt_token_keep'}
_LAST = {}
_EMPTY = {}       # name: when the setting was last found empty (not signed in)


def _addon():
    import xbmcaddon
    return xbmcaddon.Addon()


def _log(msg):
    try:
        import xbmc
        xbmc.log('[DexHub] sign-in kept: %s' % msg, xbmc.LOGINFO)
    except Exception:
        pass


def _encode(data):
    raw = json.dumps(data, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii')


def _decode(value):
    try:
        data = json.loads(base64.urlsafe_b64decode(str(value).encode('ascii')).decode('utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def keep(name, data):
    """Copy a token just saved into its hidden setting (only when it changed)."""
    sid = _IDS.get(name)
    if not sid or not isinstance(data, dict) or not data:
        return
    try:
        value = _encode(data)
        _EMPTY.pop(name, None)
        if _LAST.get(name) == value:
            return
        addon = _addon()
        if addon.getSetting(sid) != value:
            addon.setSetting(sid, value)
        _LAST[name] = value
    except Exception:
        pass


def forget(name):
    """Sign out: the kept copy goes too, or it would sign the account back in."""
    sid = _IDS.get(name)
    if not sid:
        return
    _LAST.pop(name, None)
    try:
        addon = _addon()
        if addon.getSetting(sid):
            addon.setSetting(sid, '')
    except Exception:
        pass


def restore(name, write):
    """The kept token when its file is gone; ``write(data)`` puts the file back."""
    sid = _IDS.get(name)
    if not sid or time.time() - _EMPTY.get(name, 0) < 30:
        return {}
    try:
        value = _addon().getSetting(sid)
    except Exception:
        return {}
    if not value:
        _EMPTY[name] = time.time()
        return {}
    data = _decode(value)
    if not data:
        return {}
    try:
        write(data)
        _LAST[name] = value
        _log('%s sign-in restored (its file was missing)' % name)
    except Exception:
        pass
    return data
