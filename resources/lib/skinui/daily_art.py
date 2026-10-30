# -*- coding: utf-8 -*-
"""v5.10.448: a daily list's pictures are fetched again each day.

A Top 10, a Today or a Trending row changes every day, and some of their
catalogs draw the day into the picture under the same link (a ranked
poster). Kodi keeps a picture by its link, so such a row showed last week's.
Once a day per row, the pictures of its cards that Kodi stored before today
leave Kodi's texture cache (Textures.RemoveTexture), and the next showing
fetches them again. Nuvio Arc: "Daily catalogs refresh their
posters". Other rows keep their pictures as before.
"""
import datetime
import json
import re
import threading

import xbmc

from . import common as C

_DAILY = re.compile(r'\btop[ ._-]?(?:10|ten|20)\b|\btoday\b|\bdaily\b|\btrending\b|\bthis week\b|'
                    r'اليوم|يومي|الرائج|الأكثر رواج|توب\s*10|أفضل\s*10|الأعلى\s*10', re.I)
_ART = ('poster', 'thumb', 'landscape')


def is_daily(spec, ranked=False):
    title = ' '.join(str(spec.get(k) or '') for k in ('title', 'name', 'label'))
    return ranked or bool(_DAILY.search(title))


def refresh(tab, spec, items):
    """Once a day for this row: its cards' stale pictures leave Kodi's cache."""
    today = datetime.date.today().isoformat()
    prop = 'dhs.daily.%s.%s' % (tab, spec.get('id') or spec.get('key') or '')
    if C.prop(prop) == today:
        return
    C.set_prop(prop, today)
    urls = []
    for item in items or []:
        art = item.get('art') or {}
        for key in _ART:
            url = str(art.get(key) or '')
            if url.startswith('http') and url not in urls:
                urls.append(url)
    if urls:
        threading.Thread(target=_drop, args=(urls, today, spec.get('title') or ''),
                         name='DexHubDailyArt', daemon=True).start()


def _rpc(method, params):
    answer = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}))
    return (json.loads(answer or '{}') or {}).get('result') or {}


def _fetched_on(cached):
    """The day Kodi stored its copy (the file in its Thumbnails folder): Kodi
    leaves lasthashcheck empty for nearly every web picture, and never looks
    at those again."""
    import os
    import xbmcvfs
    try:
        stamp = os.path.getmtime(xbmcvfs.translatePath('special://thumbnails/' + cached))
    except OSError:
        return ''
    return datetime.date.fromtimestamp(stamp).isoformat()


def _drop(urls, today, title):
    dropped = 0
    for url in urls:
        try:
            found = _rpc('Textures.GetTextures', {'properties': ['cachedurl'],
                                                 'filter': {'field': 'url', 'operator': 'is', 'value': url}})
            for texture in found.get('textures') or []:
                fetched = _fetched_on(str(texture.get('cachedurl') or ''))
                if fetched and fetched < today:
                    _rpc('Textures.RemoveTexture', {'textureid': int(texture['textureid'])})
                    dropped += 1
        except Exception:
            continue
    if dropped:
        C.log('daily row "%s": %d picture(s) fetched again today' % (title, dropped))
