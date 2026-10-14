# -*- coding: utf-8 -*-
"""Dex Hub's search as a skin's search widgets (v5.10.111): skin_search.

Arctic Fuse 3's search window asks each search widget for the words typed so
far (the path ends in query=, the skin adds the words). One search serves
every Dex Hub widget of the window: the films widget and the series widget
ask at the same moment, the first runs Dex Hub's unified search (TMDb, the
add-ons, the media servers: plugin.hub_search_collect) and keeps the answer
for ten minutes, the other waits for it. The window asks again on every key,
so a query is looked up only once the typing has paused.

Results that came without a poster or a logo take them from what is already
on the device (TMDb Helper's database, Dex Hub's TMDb cache): no request per
title, the list stays quick.
"""
import os
import time

import xbmc

from . import common as C

_TTL = 600.0            # seconds a search's answer is kept
_PAUSE = 0.45           # the typing pause before a query is looked up
_WAIT = 30.0            # the longest wait for the other widget's search
_LIMIT = 40             # titles per widget
_ART_LOOKUPS = 24       # titles per widget that may take local art


def _cache(key):
    return os.path.join(C.folder('search'), '%s.json' % key)


def _cached(key):
    data = C.read_json(_cache(key))
    if data and time.time() - float(data.get('t') or 0) < _TTL:
        return data
    return None


def _lock(key):
    """True when this call runs the search (the lock file is new)."""
    path = _cache(key) + '.lock'
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) > _WAIT:
            os.remove(path)         # left by a search that never finished
    except Exception:
        pass
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except Exception:
        return False


def _unlock(key):
    try:
        os.remove(_cache(key) + '.lock')
    except Exception:
        pass


def _search(query, key):
    monitor = xbmc.Monitor()
    if not _lock(key):
        deadline = time.time() + _WAIT
        while time.time() < deadline:
            if monitor.waitForAbort(0.2):
                return {}
            data = _cached(key)
            if data is not None:
                return data
            if not os.path.exists(_cache(key) + '.lock'):
                break
        return _cached(key) or {}
    try:
        started = time.monotonic()
        from .. import plugin
        def stopped():
            return monitor.abortRequested() or C.prop('dhs.search.latest') != key

        def partial(found):
            if found and not stopped():
                C.write_json(_cache(key), {'t': time.time(), 'q': query,
                                         'status': 'searching', 'rows': list(found)})

        found, status = plugin.hub_search_collect(query, should_stop=stopped, partial=partial)
        if status == 'cancelled' or stopped():
            try:
                os.remove(_cache(key))
            except OSError:
                pass
            return {}
        data = {'t': time.time(), 'q': query, 'status': status, 'rows': list(found or [])}
        C.write_json(_cache(key), data)
        C.log('search "%s": %s, %d result(s) in %.0f ms' % (query, status, len(data['rows']),
                                                           (time.monotonic() - started) * 1000))
        return data
    except Exception as exc:
        C.log('search failed: %s' % exc, xbmc.LOGWARNING)
        return {}
    finally:
        _unlock(key)


def _local_art(tile):
    """A missing poster or logo from what the device already holds."""
    media = 'tv' if tile.get('media_type') == 'series' else 'movie'
    ids = dict(tmdb_id=tile.get('tmdb_id') or '', imdb_id=tile.get('imdb_id') or '')
    if not (ids['tmdb_id'] or ids['imdb_id']):
        return
    try:
        from ..art import get_art_bundle_from_db
        bundle = get_art_bundle_from_db(media_type=media, title=tile.get('title') or '',
                                        year=tile.get('year') or '', **ids) or {}
    except Exception:
        bundle = {}
    if not bundle.get('poster') or not bundle.get('clearlogo'):
        try:
            from ..tmdb_direct import meta_for_cached_only
            found = meta_for_cached_only(media_type='series' if media == 'tv' else 'movie',
                                         title=tile.get('title') or '', year=tile.get('year') or '',
                                         **ids) or {}
        except Exception:
            found = {}
        for key in ('poster', 'fanart', 'clearlogo'):
            if found.get(key) and not bundle.get(key):
                bundle[key] = found[key]
    for key in ('poster', 'fanart', 'clearlogo'):
        value = str(bundle.get(key) or '')
        if key == 'clearlogo' and value.lower().endswith('.svg'):
            value = value[:-4] + '.png' if 'image.tmdb.org' in value else ''
        if value and not tile.get(key):
            tile[key] = value
    if tile.get('fanart') and not tile.get('landscape'):
        tile['landscape'] = tile['fanart']


def _items(data, media):
    from ..homeui import rows as R
    from . import rows as SR
    ctx = SR.Context('all')
    ctx.batch = True
    spec = {'id': 'search', 'shape': 'poster', 'grid': True}
    out, looked = [], 0
    for entry in (data or {}).get('rows') or []:
        tile = R.search_tile(entry)
        if tile is None:
            continue
        kind = tile.get('media_type') or ''
        if media == 'movie' and kind != 'movie':
            continue
        if media == 'series' and kind != 'series':
            continue
        if media == 'people' and tile.get('kind') != 'nav':
            continue
        if (not tile.get('poster') or not tile.get('clearlogo')) and looked < _ART_LOOKUPS:
            looked += 1
            _local_art(tile)
        if not tile.get('poster') and tile.get('fanart'):
            tile['poster'] = tile['fanart']
        out.append(ctx.item(tile, spec))
        if len(out) >= _LIMIT:
            break
    ctx.flush_seeds()
    return out


def listing(params, handle):
    from . import items as I
    query = str(params.get('query') or '').strip()
    media = params.get('m') or 'all'
    if len(query) < 2:
        return I.directory(handle, [], content='movies')
    key = C.short_id(query.casefold())
    C.set_prop('dhs.search.latest', key)
    data = _cached(key)
    if data is None:
        # the window asks on every key: the words are looked up once the
        # typing has paused, a newer query wins
        C.set_prop('dhs.search.latest', key)
        if xbmc.Monitor().waitForAbort(_PAUSE) or C.prop('dhs.search.latest') != key:
            return I.directory(handle, [], content='movies')
        data = _search(query, key)
    # a search widget of Arctic Fuse 3 is clicked by Kodi itself (items.kodi_click)
    return I.directory(handle, _items(data, media), content='movies', kodi_clicks='widget')
