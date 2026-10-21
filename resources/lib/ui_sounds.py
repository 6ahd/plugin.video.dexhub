# -*- coding: utf-8 -*-
"""v5.10.365: Kodi's navigation sounds from Dex Hub's Look settings.

Neither skin.dexhub nor Arctic Fuse 3 has sounds of its own: Kodi plays the
sound pack chosen in its settings (Kodi UI Sounds by default). Dex Hub UI
Sounds (resource.uisounds.dexhub, in the Dex Hub repository) is a soft, short
tick in the spirit of Apple TV.
"""
import json

import xbmc

PACK = 'resource.uisounds.dexhub'
KODI = 'resource.uisounds.kodi'
CHOICES = ('dexhub', 'kodi', 'off')


def _rpc(method, params):
    try:
        out = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
        return out.get('result')
    except Exception:
        return None


def _get(setting):
    result = _rpc('Settings.GetSettingValue', {'setting': setting}) or {}
    return result.get('value')


def _set(setting, value):
    return _rpc('Settings.SetSettingValue', {'setting': setting, 'value': value}) is not None


def current():
    """'dexhub', 'kodi', 'off' or 'other' (another pack)."""
    mode = _get('audiooutput.guisoundmode')
    skin = _get('lookandfeel.soundskin') or ''
    if mode == 0 or not skin:
        return 'off'
    if skin == PACK:
        return 'dexhub'
    if skin == KODI:
        return 'kodi'
    return 'other'


def apply(choice, notify=None):
    """True when Kodi now plays the chosen sounds."""
    if choice == 'off':
        return _set('audiooutput.guisoundmode', 0)
    pack = PACK if choice == 'dexhub' else KODI
    if not xbmc.getCondVisibility('System.HasAddon(%s)' % pack):
        xbmc.executebuiltin('InstallAddon(%s)' % pack, True)
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % pack):
            if notify:
                notify()
            return False
    xbmc.executebuiltin('EnableAddon(%s)' % pack)
    ok = _set('lookandfeel.soundskin', pack)
    if _get('audiooutput.guisoundmode') == 0:
        _set('audiooutput.guisoundmode', 1)     # only when nothing plays
    return ok
