# -*- coding: utf-8 -*-
"""O(1) dispatch for high-frequency routes.

The legacy dispatcher remains as a compatibility fallback. New routes should
be added here so plugin.py can shrink incrementally without changing Kodi URLs.
"""
_NOT_HANDLED = object()


def dispatch(action, params, api):
    p = params.get
    if action == 'home_shelf':
        # v5.10.179: the cards of a collection row the user made
        from ..homeui import shelf
        return shelf.route(params, api)
    if action == 'search':
        return api.search(p('media_type', 'movie'), p('provider_id'), query=p('query', ''))
    if action == 'nuvio_home_media': return api.nuvio_home_media(p('media', 'movie'))
    if action == 'betterposters_catalog': return api.betterposters_catalog(p('catalog_key', ''), p('media_type', 'movie'), p('page', '0'))
    if action == 'setup_center': return api.setup_center()
    if action == 'setup_connections': return api.setup_connections()
    if action == 'setup_playback_quality': return api.setup_playback_quality()
    if action == 'setup_performance': return api.setup_performance()
    if action == 'setup_playback': return api.setup_playback()
    if action == 'setup_subtitles': return api.setup_subtitles()
    if action == 'setup_quality': return api.setup_quality()
    if action == 'setup_stability': return api.setup_stability()
    if action == 'setup_diagnostics': return api.setup_diagnostics()
    if action == 'emby_menu': return api.emby_menu()
    if action == 'emby_login': return api.emby_login()
    if action == 'emby_logout': return api.emby_logout()
    if action == 'emby_continue': return api.emby_continue()
    if action == 'emby_library':
        return api.emby_library(p('key', ''), title=p('title', ''), library_type=p('library_type', ''), start=p('start', '0'), sort=p('sort', ''),
                                genre=p('genre', ''), unwatched=p('unwatched', ''), decade=p('decade', ''), rating=p('rating', ''))
    if action == 'emby_children': return api.emby_children(p('key', ''), title=p('title', ''), start=p('start', '0'))
    if action == 'emby_play': return api.emby_play(p('item_id', ''), ui_seed_key=p('ui_seed_key', ''))
    if action == 'silo_tools':
        from .. import silo_social
        return silo_social.dispatch(p('item_id', ''))
    if action == 'silo_inbox':
        from .. import island
        return island.inbox()
    if action == 'silo_menu': return api.silo_menu()
    if action == 'silo_login': return api.silo_login()
    if action == 'silo_qr_login': return api.silo_qr_login()
    if action == 'silo_change_profile': return api.silo_change_profile()
    if action == 'silo_logout': return api.silo_logout()
    if action == 'silo_toggle_sources': return api.silo_toggle_sources()
    if action == 'silo_home': return api.silo_home()
    if action.startswith('silo_') and action != 'silo_catalog' and \
            any(p(k) for k in ('sort', 'genre', 'unwatched', 'decade', 'rating')):
        return _NOT_HANDLED     # v5.10.198: a page's sort and filters (plugin._dispatch)
    if action == 'silo_section': return api.silo_section(p('section_id', ''), p('title', ''), p('offset', '0'), p('section_type', ''))
    if action == 'silo_library': return api.silo_library(p('library_id', ''), p('title', ''), p('media_type', ''))
    if action == 'silo_library_section': return api.silo_library_section(p('library_id', ''), p('section_id', ''), p('title', ''), p('offset', '0'), p('section_type', ''))
    if action == 'silo_collections': return api.silo_collections(p('library_id', ''), p('media_type', ''), p('title', ''))
    if action == 'silo_catalog':
        # v5.10.198: with the page's sort and filters (they were dropped here)
        return api.silo_catalog(p('library_id', ''), p('media_type', ''), p('title', ''), p('offset', '0'),
                                sort=p('sort', ''), genre=p('genre', ''), unwatched=p('unwatched', ''),
                                decade=p('decade', ''), rating=p('rating', ''))
    if action == 'silo_collection': return api.silo_collection(p('collection_id', ''), p('library_id', ''), p('media_type', ''), p('title', ''), p('offset', '0'))
    if action == 'silo_user_collections': return api.silo_user_collections()
    if action == 'silo_server_collections': return api.silo_server_collections()
    if action == 'silo_user_collection': return api.silo_user_collection(p('collection_id', ''), p('title', ''), p('offset', '0'))
    if action == 'silo_personal': return api.silo_personal(p('source', 'query'), p('title', ''), p('media_type', ''), p('offset', '0'))
    if action == 'silo_search': return api.silo_search(p('query', ''), p('media_type', ''), p('offset', '0'))
    if action == 'silo_series': return api.silo_series(p('series_id', ''), p('title', ''))
    if action == 'silo_episodes': return api.silo_episodes(p('series_id', ''), p('season', '0'), p('title', ''))
    if action == 'silo_play': return api.silo_play(p('item_id', ''), ui_seed_key=p('ui_seed_key', ''))
    if action == 'plex_menu': return api.plex_menu()
    if action == 'plex_login': return api.plex_login()
    if action == 'plex_logout': return api.plex_logout()
    if action == 'plex_refresh': return api.plex_refresh()
    if action == 'plex_servers': return api.plex_servers()
    if action == 'plex_server': return api.plex_server(p('server_id', ''))
    if action == 'plex_continue': return api.plex_continue(p('server_id', ''))
    if action == 'plex_library':
        return api.plex_library(p('server_id', ''), p('key', ''), title=p('title', ''), library_type=p('library_type', ''), start=p('start', '0'), sort=p('sort', ''),
                                genre=p('genre', ''), unwatched=p('unwatched', ''), decade=p('decade', ''))
    if action == 'plex_children': return api.plex_children(p('server_id', ''), p('key', ''), title=p('title', ''), start=p('start', '0'), ui_seed_key=p('ui_seed_key', ''))
    if action == 'plex_search': return api.plex_search(p('server_id', ''))
    if action == 'plex_play': return api.plex_play(p('server_id', ''), p('rating_key', ''), ui_seed_key=p('ui_seed_key', ''))
    if action == 'switch_source': return api.switch_source(p('pick', ''), p('rescan', ''))
    if action == 'nuvio_connect': return api._accounts_route_mod.connect_nuvio(api.ADDON, api.tr)
    if action == 'nuvio_configure': return api._accounts_route_mod.configure_nuvio(api.ADDON, api.tr, initial=False, run_initial_sync=False)
    if action == 'nuvio_profile': return api._accounts_route_mod.nuvio_profile_picker(api.ADDON, api.tr, force=True)
    if action == 'nuvio_login': return api._cloud_login('nuvio')
    if action == 'nuvio_qr_login': return api._cloud_qr_login('nuvio')
    if action == 'stremio_qr_login': return api._cloud_qr_login('stremio')
    if action == 'nuvio_logout': return api._cloud_logout('nuvio')
    if action == 'stremio_login': return api._cloud_login('stremio')
    if action == 'stremio_logout': return api._cloud_logout('stremio')
    if action == 'cloud_sync_now': return api.cloud_sync_now()
    return _NOT_HANDLED
