# -*- coding: utf-8 -*-
"""v5.10.376: image quality (Look › Image quality): Standard or High.

Two things kept the backdrops soft on a 1080p (or 4K) screen:
  * Kodi keeps every picture it caches at most 720 lines high (advancedsettings
    imageres, 720 by default; fanartres only for its own library's fanart), so
    a 1920 wide backdrop was cached at 1280 x 720 and drawn 1.5 times larger
  * Dex Hub asked TMDb for w1280 backdrops and w500 posters (image_policy)

High: Kodi keeps pictures up to 1080 lines with a finer JPEG (a block of
advancedsettings.xml between Dex Hub's markers, everything else in the file
kept as it is), TMDb backdrops at their original size and posters at w780, and
the backdrops and posters already cached small are dropped so they come again
sharp. Kodi reads advancedsettings when it starts: a restart is offered.
Standard: the block is taken out and the sizes go back to w1280 / w500.

High costs more memory and a slower first load of each picture: right for a
Shield, an Apple TV 4K, a Mac or a PC; Standard stays the default for small boxes.
"""
import json
import os
import re

import xbmc
import xbmcaddon
import xbmcvfs

CHOICES = ('standard', 'high')
START, END = '<!-- Dex Hub image quality -->', '<!-- /Dex Hub image quality -->'
HIGH = ('  %s\n  <imageres>1080</imageres>\n  <fanartres>1080</fanartres>\n'
        '  <imagequalityjpeg>2</imagequalityjpeg>\n  %s\n') % (START, END)
SIZES = {'standard': ('w500', 'w1280'), 'high': ('w780', 'original')}
_BLOCK = re.compile(r'[ \t]*' + re.escape(START) + r'.*?' + re.escape(END) + r'[ \t]*\n?', re.S)
# the cached pictures dropped on a change (Kodi fetches them again, at the new size)
_DROP = ('image.tmdb.org/t/p/', 'poster-cache/background', 'poster-cache/proxy/poster', 'assets.fanart.tv')


def _addon():
    from .profile_scope import ADDON_ID
    return xbmcaddon.Addon(ADDON_ID)


def _path():
    return xbmcvfs.translatePath('special://profile/advancedsettings.xml')


def current():
    value = (_addon().getSetting('image_quality') or '').strip().lower()
    if value in CHOICES:
        return value
    return 'high' if (_addon().getSetting('image_fanart_size') or '').strip().lower() == 'original' else 'standard'


def _read():
    try:
        with open(_path(), 'r', encoding='utf-8') as fh:
            return fh.read()
    except Exception:
        return ''


def _write(text):
    path = _path()
    tmp = path + '.dexhub.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
    os.replace(tmp, path)


def advanced(on):
    """Dex Hub's block in advancedsettings.xml. (changed, own): ``own`` when
    the file already sets imageres itself (then it is left as it is)."""
    text = _read()
    rest = _BLOCK.sub('', text)
    own = '<imageres>' in rest
    if on and not own:
        if '</advancedsettings>' in rest:
            new = rest.replace('</advancedsettings>', HIGH + '</advancedsettings>', 1)
        else:
            new = '<?xml version="1.0" encoding="UTF-8"?>\n<advancedsettings version="1.0">\n' + HIGH + '</advancedsettings>\n'
    else:
        new = rest
    if new != text:
        _write(new)
        return True, own
    return False, own


def _rpc(method, params):
    try:
        out = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
        return out.get('result')
    except Exception:
        return None


def drop_cached():
    """The backdrops and posters in Kodi's texture cache, removed (fetched
    again at the new size). Returns how many."""
    count = 0
    for needle in _DROP:
        found = _rpc('Textures.GetTextures', {'properties': ['url'],
                                             'filter': {'field': 'url', 'operator': 'contains', 'value': needle}})
        for row in (found or {}).get('textures') or []:
            if _rpc('Textures.RemoveTexture', {'textureid': row.get('textureid')}) == 'OK':
                count += 1
    return count


def apply(choice):
    """{'changed': advancedsettings changed, 'own': the file sets imageres
    itself, 'dropped': cached pictures removed}."""
    if choice not in CHOICES:
        raise ValueError('bad choice')
    addon = _addon()
    poster, fanart = SIZES[choice]
    addon.setSetting('image_quality', choice)
    addon.setSetting('image_poster_size', poster)
    addon.setSetting('image_fanart_size', fanart)
    changed, own = advanced(choice == 'high')
    dropped = drop_cached()
    xbmc.log('[DexHub] image quality %s: advancedsettings %s%s, %d cached picture(s) dropped' % (
        choice, 'changed' if changed else 'unchanged', ' (imageres set by the user)' if own else '', dropped), xbmc.LOGINFO)
    return {'changed': changed, 'own': own, 'dropped': dropped}
