# -*- coding: utf-8 -*-
"""Cheap metadata profile checks shared by native and custom UI renderers."""

# v5.10.198: the routes of a media server's titles: a movie's or an
# episode's play route, a show's seasons or episodes (homeui.rows
# SERVER_FOLDER_ACTIONS)
SERVER_ACTIONS = frozenset((
    'plex_play', 'emby_play', 'jellyfin_play', 'silo_play',
    'plex_children', 'emby_children', 'jellyfin_children', 'silo_series', 'silo_episodes'))


def source_appearance():
    from .settings_cache import cached_addon
    addon = cached_addon()
    selected = (addon.getSetting('global_meta_source_id') or '').strip()
    if selected:
        return selected == 'native' and (addon.getSetting('global_meta_source_all') or 'true') == 'true'
    return 'native' in (addon.getSetting('default_meta_source') or '').lower()


def server_first():
    """v5.10.198: a server's titles (Plex, Emby, Jellyfin, Silo) keep their
    server's title, plot, artwork and logo; the chosen metadata source fills
    only what the server lacks (a logo, a backdrop, the cast). Setting
    server_metadata: 0 the server first (default), 1 the chosen source, as
    for catalogs."""
    from .settings_cache import cached_addon
    try:
        return (cached_addon().getSetting('server_metadata') or '0').strip() != '1'
    except Exception:
        return True


def server_title(tile):
    """True for a title read from a media server (its route, or the title
    page's mark)."""
    tile = tile or {}
    if tile.get('_server'):
        return True
    path = str(tile.get('path') or '')
    if 'action=' not in path or not path.startswith('plugin://plugin.video.dexhub'):
        return False
    from urllib.parse import parse_qsl, urlsplit
    try:
        action = dict(parse_qsl(urlsplit(path).query)).get('action') or ''
    except Exception:
        return False
    return action in SERVER_ACTIONS


def keep_server_meta(tile):
    """A server title whose own metadata stays (fill-only enrichment)."""
    return server_title(tile) and server_first()


def refresh_server_rows():
    """v5.10.198: the Servers' metadata choice changed: the Home's rows read
    from a media server drop their saved copies and are read again (their
    titles and art follow the new choice). Returns how many were dropped."""
    import os
    try:
        from .skinui import board
        from .skinui import common as C
    except Exception:
        return 0
    dropped = 0
    for tab in C.TABS:
        for spec in board.load_specs(tab):
            params = spec.get('params') if isinstance(spec.get('params'), dict) else {}
            action = str(params.get('action') or '')
            if action.split('_', 1)[0] not in ('plex', 'emby', 'jellyfin', 'silo') or not spec.get('id'):
                continue
            path = C.row_file(tab, spec['id'])
            for one in (path, path[:-5] + '.mar'):
                try:
                    os.remove(one)
                    dropped += 1
                except Exception:
                    pass
    if dropped:
        try:
            C.ask_republish('metadata')
        except Exception:
            pass
    return dropped
