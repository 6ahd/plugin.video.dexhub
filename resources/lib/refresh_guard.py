# -*- coding: utf-8 -*-
"""Trailers and previews never switch the TV's refresh rate (v5.10.156, issue #5).

With Kodi's "Adjust display refresh rate" on, a trailer in the small window
(or full screen) made the TV switch modes; on some CoreELEC builds that ends
as a black picture with sound only, and Back no longer gets out. Dex Hub turns
the setting off just before a trailer or preview plays and puts the user's
value back when it stops, or when real playback is asked for, so films and
episodes switch as before.

The saved value lives in a Home window property and in a small file, so a
crash or a power cut in the middle of a trailer still gets it back at the
next start (restore() runs from the service).
"""
import json
import os
import threading

import xbmc
import xbmcgui

SETTING = 'videoplayer.adjustrefreshrate'
PROP = 'dexhub.refresh.saved'
_lock = threading.RLock()


def _path():
    import xbmcvfs
    return os.path.join(xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/'),
                        'refresh_saved.json')


def _rpc(method, params):
    try:
        raw = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}))
        return json.loads(raw or '{}').get('result')
    except Exception:
        return None


def _get():
    result = _rpc('Settings.GetSettingValue', {'setting': SETTING})
    try:
        return int((result or {}).get('value'))
    except (TypeError, ValueError):
        return None


def _set(value):
    return _rpc('Settings.SetSettingValue', {'setting': SETTING, 'value': int(value)}) is True


def _clear(home):
    home.clearProperty(PROP)
    try:
        os.remove(_path())
    except OSError:
        pass


def suspend():
    with _lock:
        _suspend()


def _suspend():
    """Before a trailer or preview: refresh switching off (the user's value kept)."""
    try:
        home = xbmcgui.Window(10000)
        if home.getProperty(PROP):
            return                      # already off for an earlier trailer
        value = _get()
        if not value:
            return                      # off already (0) or unknown
        home.setProperty(PROP, str(value))
        try:
            path = _path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = '%s.%s.%s.tmp' % (path, os.getpid(), threading.get_ident())
            with open(tmp, 'w') as handle:
                json.dump({'value': value}, handle)
            os.replace(tmp, path)
        except Exception:
            # Never change a persistent Kodi setting without durable recovery.
            home.clearProperty(PROP)
            try:
                os.remove(tmp)
            except Exception:
                pass
            return
        if not _set(0):
            # A rejected write did not suspend anything; retain the user's setting.
            if _get() == value:
                _clear(home)
            return
        xbmc.log('[DexHub] refresh switching off while a trailer plays', xbmc.LOGINFO)
    except Exception:
        pass


def restore():
    with _lock:
        _restore()


def _restore():
    """The trailer stopped, or real playback is asked for: the user's value back."""
    try:
        home = xbmcgui.Window(10000)
        raw = home.getProperty(PROP)
        if not raw:
            try:
                with open(_path(), 'r') as handle:
                    raw = str((json.load(handle) or {}).get('value') or '')
            except Exception:
                raw = ''
        if not raw:
            return
        current = _get()
        if current is None:
            return
        if current != 0:
            # A user changed it, or it was restored already. Do not overwrite it.
            _clear(home)
            return
        if _set(int(raw)):
            _clear(home)
            xbmc.log('[DexHub] refresh switching back on', xbmc.LOGINFO)
    except Exception:
        pass
