# -*- coding: utf-8 -*-
"""Targets shared by the native and portable dependent catalog rows."""
from urllib.parse import parse_qsl, urlencode, urlsplit


def target_for(tile, click='', target='', include_works=False):
    from .skinui.common import url
    kind = tile.get('kind') or ''
    if tile.get('cw') or tile.get('season_card') or kind in ('channel', 'more', 'action', 'note'):
        return ''
    if kind == 'work':
        if not include_works:
            return ''
        media = tile.get('media_type') or ''
        if media not in ('movie', 'series'):
            return ''
        tid, iid = str(tile.get('tmdb_id') or ''), str(tile.get('imdb_id') or '')
        if not (tid.isdigit() or iid.startswith('tt')):
            return ''
        return url('skin_follow_related', tmdb=tid, imdb=iid, m=media)
    if kind == 'folder' or tile.get('folder'):
        path = target or tile.get('path') or ''
        return path if path.startswith(('plugin://', 'videodb://', 'library://')) else ''
    return ''


def load_target(target, media='movie'):
    from .external_catalog import browse
    from .homeui import rows as R
    from .skinui import common as C, rows as RW
    parsed = urlsplit(str(target or ''))
    params = dict(parse_qsl(parsed.query))
    if parsed.scheme == 'plugin' and parsed.netloc == C.ADDON_ID:
        # Old cached nav items may contain several skin_list wrappers.
        # Decode all of them before reaching the catalog dispatcher; a skin
        # action passed to the ordinary dispatcher otherwise opens Home.
        for _ in range(8):
            if params.get('action') != 'skin_list':
                break
            nested = dict(parse_qsl(params.get('r') or ''))
            if not nested or nested == params:
                return []
            params = nested
        action = params.get('action') or ''
        if not action or action == 'skin_list':
            return []
        if action == 'skin_catalog_hub':
            if params.get('target'):
                return load_target(params['target'], media)
            action = params['action'] = 'skin_folder'

        if action == 'skin_af3_catalog':
            return []  # retired cached collection route
        if action == 'skin_follow_related':
            from . import tmdb_direct as T
            tid = params.get('tmdb') or ''
            kind = 'tv' if params.get('m') == 'series' else 'movie'
            if not tid.isdigit() and str(params.get('imdb') or '').startswith('tt'):
                tid = T._resolve_tmdb_from_imdb(params['imdb'], kind, timeout=4, max_attempts=1)
            if not str(tid).isdigit():
                return []
            key = 'follow-recs:%s:%s' % (kind, tid)
            found = T._cache_get(key)
            if isinstance(found, list):
                return found
            data = T._request('/%s/%s/recommendations' % (kind, tid),
                              {'language': T._card_language()}, timeout=5, max_attempts=1)
            found = []
            for row in (data or {}).get('results') or []:
                item = T._row_from_tmdb(row, 'series' if kind == 'tv' else 'movie')
                item.update(kind='work', plot=item.get('overview') or '', fanart=item.get('backdrop') or '',
                            landscape=item.get('backdrop') or '', path=C.url('item_open', media_type='series' if kind == 'tv' else 'movie',
                                                   canonical_id='tmdb:%s' % row.get('id'), tmdb_id=row.get('id')))
                found.append(item)
            if found:
                T._cache_set(key, found, 1800)
            return found[:40]
        if action == 'skin_folder':
            from .catalog_navigation import folder_tiles
            return folder_tiles(params, RW.api())
        if action.startswith('skin_') or action in ('home', 'home_classic', 'home_ui', 'home_library', 'open_settings'):
            return []
        if action not in R.PLAYER_ACTIONS:
            found = R.capture_tiles(params, RW.api(), limit=40)[0]
            return found
        return []
    return browse(target)


def child_shape(tiles):
    """Catalog contents keep standard Home cards; no forced landscape for works."""
    folders = [t for t in tiles or [] if t.get('kind') != 'note']
    if folders and all(t.get('kind') != 'work' and (t.get('folder') or t.get('kind') == 'folder') for t in folders):
        from .homeui.rows import dominant_shape
        return dominant_shape(folders, 'landscape')
    return 'poster'
