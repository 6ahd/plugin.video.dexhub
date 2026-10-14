# -*- coding: utf-8 -*-
"""Bound TMDb image sizes on every list item, whatever produced the URL.

v5.10.87. Plex posters were being served resized (5.10.82), but in the lists
the poster shown is usually not Plex's: server_art_from_tmdb replaces it with
TMDb artwork from TMDb Helper's database, and Trakt image dicts are read
largest first (full, then original). Those URLs can be TMDb "original" files:
a 2000x3000 poster and a 3840x2160 backdrop for a tile a few hundred pixels
wide, downloaded, decoded and kept in the texture cache on the TV box.

This rewrites only the size segment of image.tmdb.org URLs, only downwards
(a w342 poster stays w342), and only for keys whose role is known. w500 for
posters and w1280 for backdrops are the sizes Kodi's own TMDb scraper and
TMDb Helper use by default; on a 1080p interface they are indistinguishable
from the originals. image_poster_size and image_fanart_size (hidden settings)
change the caps; "original" turns the cap off.
"""
import re
import time

_TMDB = re.compile(r'^(https?://image\.tmdb\.org/t/p/)([A-Za-z0-9_]+)(/.+)$')
POSTER_KEYS = frozenset(('poster', 'thumb', 'icon', 'thumbnail', 'cover', 'image',
                         'tvshow.poster', 'season.poster', 'tvshow.thumb', 'keyart'))
FANART_KEYS = frozenset(('fanart', 'landscape', 'fanart_image', 'tvshow.fanart',
                         'tvshow.landscape', 'season.fanart'))
LOGO_KEYS = frozenset(('clearlogo', 'logo', 'tvshow.clearlogo', 'clearart',
                       'tvshow.clearart', 'discart', 'characterart'))
BANNER_KEYS = frozenset(('banner', 'tvshow.banner', 'season.banner'))
_CACHE = {'at': 0.0, 'poster': 'w500', 'fanart': 'w1280'}


_PAIR = re.compile(r'^w(\d+)_and_h\d+')


def _pixels(token):
    token = str(token or '').lower()
    if token == 'original':
        return 10 ** 6
    if token[:1] in ('w', 'h') and token[1:].isdigit():
        return int(token[1:])
    # v5.10.133: the IPTV panels' TMDb links, w600_and_h900_bestv2 and the like
    pair = _PAIR.match(token)
    if pair:
        return int(pair.group(1))
    return None


def _caps():
    now = time.monotonic()
    if now - _CACHE['at'] > 30.0:
        _CACHE['at'] = now
        try:
            import xbmcaddon
            addon = xbmcaddon.Addon()
            poster = (addon.getSetting('image_poster_size') or 'w500').strip().lower()
            fanart = (addon.getSetting('image_fanart_size') or 'w1280').strip().lower()
            _CACHE['poster'] = poster if _pixels(poster) else 'w500'
            _CACHE['fanart'] = fanart if _pixels(fanart) else 'w1280'
        except Exception:
            pass
    return _CACHE['poster'], _CACHE['fanart']


def cap_url(url, cap):
    """The same URL with its TMDb size reduced to `cap`, or unchanged."""
    if not isinstance(url, str) or 'image.tmdb.org/t/p/' not in url:
        return url
    match = _TMDB.match(url)
    if not match:
        return url
    current, wanted = _pixels(match.group(2)), _pixels(cap)
    if current is None or wanted is None or current <= wanted:
        return url
    return match.group(1) + cap + match.group(3)


def bound_art(art):
    if not isinstance(art, dict) or not art:
        return art
    poster, fanart = _caps()
    out = None
    for key, value in art.items():
        if not isinstance(value, str) or 'image.tmdb.org/t/p/' not in value:
            continue
        lowered = str(key).lower()
        if lowered in FANART_KEYS:
            new = cap_url(value, fanart)
        elif lowered in LOGO_KEYS:
            new = cap_url(value, 'w500')
        elif lowered in BANNER_KEYS:
            new = cap_url(value, 'w1000')
        elif lowered in POSTER_KEYS:
            new = cap_url(value, poster)
        else:
            continue
        if new != value:
            if out is None:
                out = dict(art)
            out[key] = new
    return out if out is not None else art
