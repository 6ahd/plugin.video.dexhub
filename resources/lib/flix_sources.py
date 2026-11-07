# -*- coding: utf-8 -*-
"""Dex Hub's sources and add-ons for Flix, the native Apple TV screens.

Flix asks through JSON-RPC Files.GetDirectory, so each answer is a plugin
folder and nothing opens on screen:
  flix_sources  the same search as the sources page, as rows. scope=dexhub is
                the shared race, scope=kodi the Kodi add-ons' own lane (Red
                Light, POV...). A row's path plays that source in Kodi; its
                plot is JSON with what Flix shows.
  flix_addons   the source add-ons added here or from the phone page.
  flix_addon    op=add|remove, url: an add-on added or removed in Flix.
  flix_home     tab=all|movie|series: that page's rows in the Home's own order
                (spec_<tab>.json) with the titles it already keeps, so Flix
                shows Dex Hub's sections; a tile that is a folder opens in Kodi.
"""
import json
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

KODI_WAIT = 60
ROW_ITEMS = 40


def _log(message, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] flix: %s' % message, level)


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _targets(api, media, canonical, video_id, season, episode, meta):
    if season is None:
        return list(api._stream_provider_targets(media, canonical, meta=meta))
    direct = []
    from .dexhub import store
    for provider in store.list_providers():
        if provider.get('enabled') is False or api._source_left_out(provider):
            continue
        if api._stremio_provider.supports_stream(provider, media, item_id=video_id):
            direct.append((provider, video_id))
    from .search.episode_route import merge_targets
    return merge_targets(api._stream_provider_targets(media, canonical, meta=meta, season=season,
                                                      episode=episode), direct)


def _search(api, params):
    media = params.get('media_type') or 'movie'
    canonical = params.get('canonical_id') or ''
    title = params.get('title') or ''
    season, episode = _number(params.get('season')), _number(params.get('episode'))
    if season is None or episode is None:
        season = episode = None
    video_id = '%s:%d:%d' % (canonical, season, episode) if season is not None else ''
    providers = api._provider_order_for_id_with_context(media, canonical)
    meta, _cached = api._fast_meta_for_sources(media, canonical, providers, title=title or canonical)
    ids = api._merge_seed_ids(api.extract_ids(meta), api._get_tmdbh_seed_ids(canonical))
    name = title or meta.get('name') or meta.get('title') or canonical
    ids = api._enrich_stream_ids(ids, media, meta=meta, canonical_id=canonical, title=name)
    if season is not None and hasattr(api, '_show_imdb_for_episode'):
        canonical, video_id, ids = api._show_imdb_for_episode(canonical, video_id, ids)
    stream_meta = api._meta_with_seed_ids(meta, ids)
    targets = _targets(api, media, canonical, video_id, season, episode, stream_meta)
    shared, late = api._kodi_sources_lane(targets, media)
    context = dict(title=name)
    if season is not None:
        context.update(video_id=video_id, season=season, episode=episode, show_title=name)
    if params.get('scope') == 'kodi':
        if not late:
            return []
        return late.collect(api._kodi_rows_converter(media, canonical, meta, {}, '', ids, **context),
                            timeout=KODI_WAIT)
    rows = api._collect_stream_entries(shared, media, canonical, meta, {}, '', ids, **context)
    return api._finalize_stream_entries(rows, media_type=media, canonical_id=canonical, video_id=video_id)


def _row(entry):
    return {'name': str(entry.get('stream_name_raw') or entry.get('name') or ''),
            'description': str(entry.get('stream_desc_raw') or entry.get('label2') or ''),
            'addon': str(entry.get('source_addon') or entry.get('provider_name_raw') or 'Dex Hub'),
            'size': entry.get('stream_video_size') or 0}


def _sources(api, handle, params):
    rows = _search(api, params)
    base = 'plugin://%s/?' % xbmcaddon.Addon().getAddonInfo('id')
    for entry in rows:
        key = entry.get('stream_key')
        if not key:
            continue
        item = xbmcgui.ListItem(label=str(entry.get('name') or ''), offscreen=True)
        item.getVideoInfoTag().setPlot(json.dumps(_row(entry), ensure_ascii=False))
        xbmcplugin.addDirectoryItem(handle, base + urlencode({'action': 'play', 'stream_key': key}), item, False)
    _log('%s sources for %s (%s)' % (len(rows), params.get('canonical_id'), params.get('scope') or 'dexhub'))


def _addons(handle):
    from .dexhub import store
    for row in store.list_providers() or []:
        url = str(row.get('manifest_url') or '')
        if url.startswith('http') and row.get('enabled') is not False:
            item = xbmcgui.ListItem(label=str(row.get('name') or url), offscreen=True)
            xbmcplugin.addDirectoryItem(handle, url, item, False)


def _addon_change(params):
    from .dexhub import api as dexhub_api, store
    url = params.get('url') or ''
    known = [row for row in store.list_providers() or [] if str(row.get('manifest_url') or '') == url]
    if params.get('op') == 'remove':
        for row in known:
            store.remove_provider(str(row.get('id') or ''))
    elif url and not known:
        dexhub_api.add_provider_from_url(url)
    from .phone_setup import _republish
    _republish('sources')


def _picture(art, *names):
    for name in names:
        value = str((art or {}).get(name) or '')
        if value.startswith('http'):
            return value
    return ''


def _tile(item):
    art = item.get('art') or {}
    info = item.get('info') or {}
    name = str(info.get('title') or item.get('label') or '').replace('\u200e', '').strip()
    path = str(item.get('path') or '')
    query = dict(parse_qsl(urlsplit(path).query))
    canonical = query.get('canonical_id') or ''
    imdb = str((item.get('ids') or {}).get('imdb') or '')
    if imdb.startswith('tt') and not canonical.startswith('tt'):
        canonical = imdb
    if canonical and query.get('media_type'):
        return {'id': canonical, 'type': 'series' if query['media_type'] in ('series', 'tv', 'show', 'anime') else 'movie',
                'name': name, 'poster': _picture(art, 'poster', 'thumb'), 'background': _picture(art, 'fanart', 'landscape'),
                'logo': _picture(art, 'clearlogo'), 'description': str(info.get('plot') or ''),
                'releaseInfo': str(info.get('year') or '')}
    if path and name:
        run = 'ActivateWindow(Videos,"%s",return)' if item.get('folder') else 'RunPlugin("%s")'
        return {'run': run % path, 'name': name, 'poster': _picture(art, 'poster', 'landscape', 'thumb', 'fanart')}
    return None


def _home(handle, params):
    from .skinui import board
    tab = params.get('tab') or 'all'
    shown = 0
    for spec in board.load_specs(tab):
        data = board.C.read_json('%s/%s_%s.json' % (board.C.folder('rows'), tab, spec.get('id')), {}) or {}
        tiles = [tile for tile in map(_tile, (data.get('items') or [])[:ROW_ITEMS]) if tile]
        if not tiles:
            continue
        row = {'id': str(spec.get('id') or ''), 'title': str(spec.get('title') or data.get('title') or ''),
               'shape': str(spec.get('shape') or data.get('shape') or 'poster'), 'items': tiles}
        item = xbmcgui.ListItem(label=row['title'], offscreen=True)
        item.getVideoInfoTag().setPlot(json.dumps(row, ensure_ascii=False))
        xbmcplugin.addDirectoryItem(handle, 'plugin://flix/row/%s' % row['id'], item, False)
        shown += 1
    _log('%d rows of %s' % (shown, tab))


def run():
    try:
        handle = int(sys.argv[1])
    except (IndexError, ValueError):
        handle = -1
    params = dict(parse_qsl((sys.argv[2] if len(sys.argv) > 2 else '').lstrip('?')))
    action = params.get('action')
    try:
        if action == 'flix_sources':
            from . import player_runtime
            _sources(player_runtime, handle, params)
        elif action == 'flix_addons':
            _addons(handle)
        elif action == 'flix_addon':
            _addon_change(params)
        elif action == 'flix_home':
            _home(handle, params)
    except Exception as exc:
        _log('%s failed: %r' % (action, exc), xbmc.LOGWARNING)
    finally:
        if handle >= 0:
            xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        try:
            from .runtime_cleanup import shutdown_loaded_pools
            shutdown_loaded_pools()
        except Exception:
            pass
