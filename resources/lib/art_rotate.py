# -*- coding: utf-8 -*-
"""Artwork that changes (v5.10.595), as the Apple TV app, Netflix and Disney+
do: a title's poster and backdrop are taken from its TMDb images in turn
instead of one picture that never changes (the user, 20 Nov 2026).

Settings > Theme > Artwork rotation (art_rotation):
  daily    another picture each day (the default)
  6h       every six hours
  session  each time Kodi starts
  off      the source's own pictures, as before

The pick is the same on every card and on the title page for the period
(a hash of the title and the period), so a title does not change while it is
on screen. Posters keep text in the interface's language or English (the
title stays readable); backdrops are the textless ones first. Only the
better pictures take part (TMDb's votes). A title the user chose artwork for
keeps that choice. Nothing here waits on the network: a title whose images
are not cached yet keeps its picture and is fetched in the background
(kept a week, art_choice's cache), so it rotates from its next showing.
"""
import hashlib
import threading
import time

import xbmc

MODES = ('daily', '6h', 'session', 'off')
_MEMO = {'at': 0.0, 'mode': 'daily'}
_SESSION = 'dexhub.art.session'
_QUEUE = []
_QUEUED = set()
_LOCK = threading.Lock()
_WORKER = {'thread': None}
POSTERS, BACKDROPS = 8, 10        # the best ones that take part


def mode():
    now = time.time()
    if now - _MEMO['at'] < 5.0:
        return _MEMO['mode']
    value = 'daily'
    try:
        import xbmcaddon
        value = (xbmcaddon.Addon('plugin.video.dexhub').getSetting('art_rotation') or 'daily').strip().lower()
    except Exception:
        pass
    _MEMO['at'], _MEMO['mode'] = now, value if value in MODES else 'daily'
    return _MEMO['mode']


def _period():
    current = mode()
    if current == '6h':
        return 'h%d' % int(time.time() // 21600)
    if current == 'session':
        try:
            import xbmcgui
            home = xbmcgui.Window(10000)
            value = home.getProperty(_SESSION)
            if not value:
                value = str(int(time.time()))
                home.setProperty(_SESSION, value)
            return 's' + value
        except Exception:
            return 's'
    return time.strftime('d%Y%m%d')


def _kind(media):
    return 'tv' if str(media or '') in ('series', 'tv', 'tvshow', 'show', 'anime', 'season') else 'movie'


def _cached(tmdb, media):
    from . import tmdb_direct as T
    data = T._cache_get('dh-images-v1:%s:%s' % (_kind(media), tmdb))
    return data if isinstance(data, dict) else None


def _language():
    try:
        from .tmdb_direct import _card_language
        return (_card_language() or 'en')[:2].lower()
    except Exception:
        return 'en'


def _best(images, limit, langs):
    rows = [i for i in images or [] if isinstance(i, dict) and i.get('file_path')]
    if langs is not None:
        wanted = [i for i in rows if i.get('iso_639_1') in langs]
        rows = wanted or rows
    rows.sort(key=lambda i: (-(float(i.get('vote_average') or 0)), -(int(i.get('vote_count') or 0))))
    return rows[:limit]


def _choose(rows, key):
    if not rows:
        return ''
    digest = hashlib.md5(key.encode('utf-8')).hexdigest()
    return rows[int(digest[:8], 16) % len(rows)]['file_path']


def pick(tmdb, media):
    """{'poster': url, 'fanart': url} for this period, {} when rotation is
    off or the title's images are not known yet (then fetched behind)."""
    tmdb = str(tmdb or '').strip()
    if not tmdb.isdigit() or mode() == 'off':
        return {}
    data = _cached(tmdb, media)
    if data is None:
        _want(tmdb, media)
        return {}
    from .tmdb_direct import IMAGE_BASE_POSTER, IMAGE_BASE_BACKDROP
    key = '%s:%s:%s' % (_kind(media), tmdb, _period())
    lang = _language()
    out = {}
    poster = _choose(_best(data.get('posters'), POSTERS, (lang, 'en')), key + ':p')
    if poster:
        out['poster'] = IMAGE_BASE_POSTER + poster
    backdrop = _choose(_best(data.get('backdrops'), BACKDROPS, (None, '', 'xx')), key + ':b')
    if backdrop:
        out['fanart'] = IMAGE_BASE_BACKDROP + backdrop
    return out


def apply(art, tmdb, media):
    """A card's art with this period's pictures (the logo stays)."""
    chosen = pick(tmdb, media)
    if not chosen:
        return art
    art = dict(art)
    if chosen.get('poster') and art.get('poster'):
        if art.get('thumb') == art['poster']:
            art['thumb'] = chosen['poster']
        art['poster'] = chosen['poster']
    if chosen.get('fanart'):
        art['fanart'] = chosen['fanart']
        if art.get('landscape'):
            art['landscape'] = chosen['fanart']
    return art


# ------------------------------------------------------------- background
def _want(tmdb, media):
    key = (_kind(media), tmdb)
    with _LOCK:
        if key in _QUEUED or len(_QUEUE) > 400:
            return
        _QUEUED.add(key)
        _QUEUE.append(key)
        thread = _WORKER['thread']
        if thread is None or not thread.is_alive():
            thread = threading.Thread(target=_work, name='DexHubArtRotate')
            thread.daemon = True
            _WORKER['thread'] = thread
            thread.start()


def _work():
    """A few titles a second, the newest wanted first (what is on screen)."""
    from .art_choice import _images
    monitor = xbmc.Monitor()
    idle = 0
    while not monitor.abortRequested():
        with _LOCK:
            key = _QUEUE.pop() if _QUEUE else None
        if key is None:
            idle += 1
            if idle > 40 or monitor.waitForAbort(0.5):
                return
            continue
        idle = 0
        kind, tmdb = key
        try:
            _images(tmdb, 'series' if kind == 'tv' else 'movie')
        except Exception:
            pass
        if monitor.waitForAbort(0.25):
            return
