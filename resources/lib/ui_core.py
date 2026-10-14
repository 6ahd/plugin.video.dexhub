# -*- coding: utf-8 -*-
"""UI foundation layer — extracted from plugin.py (v3.9.264 decomposition).

The listing primitives every route builds on: directory item construction,
directory finalization, id-seed merging, small prompts. Functions unchanged;
plugin.py re-exports every name so all call sites keep working.
"""
import json
import os
import sys
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from .i18n import tr

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
try:
    HANDLE = int(__import__('sys').argv[1]) if len(__import__('sys').argv) > 1 else -1
except Exception:
    HANDLE = -1


from .art import addon_fanart
from .context import WINDOW_ID, MULTI_WS_RE, _get_int_setting


def _plugin():
    """Use the active entry's explicit UI API without loading another router."""
    from .runtime_binding import current
    return current()


def _apply_sort_methods(*a, **k):
    return _plugin()._apply_sort_methods(*a, **k)

def _apply_unique_ids(*a, **k):
    return _plugin()._apply_unique_ids(*a, **k)

def _favorite_properties(*a, **k):
    return _plugin()._favorite_properties(*a, **k)

def _flush_pending_items(*a, **k):
    return _plugin()._flush_pending_items(*a, **k)

def _normalize_kodi_item_art(*a, **k):
    return _plugin()._normalize_kodi_item_art(*a, **k)

def _videoinfotag_apply(*a, **k):
    return _plugin()._videoinfotag_apply(*a, **k)


def _profile_path():
    try:
        path = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    except Exception:
        path = ''
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
    return path or xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/')


def _listing_page_size():
    return _get_int_setting('large_section_limit', 30, minimum=12, maximum=60)


def _menu_command(command):
    """v5.10.249: Container.Update ran only inside the Videos window, and a
    route that plays (Show sources) can never be listed: the menu's entries
    now run wherever the list is (skinui.items.menu_command)."""
    text = str(command or '')
    if not text.startswith('Container.Update('):
        return text
    try:
        from .skinui.items import menu_command
        return menu_command(text, None)
    except Exception:
        return text


def add_item(label, path, is_folder=True, info=None, art=None, properties=None, label2=None, ids=None, context_menu=None, is_favorite=None, cast=None):
    label = tr(label)
    label2 = tr(label2 or '') if label2 else ''
    # v5.10.117: offscreen. A listing's items go to Kodi (or to a Home row)
    # finished; without it every setter of every item waits for Kodi's GUI
    # lock, which the skin holds while it draws (twenty waits per title,
    # the longest while the Home animates)
    try:
        item = xbmcgui.ListItem(label=label, label2=label2, offscreen=True)
    except TypeError:
        item = xbmcgui.ListItem(label=label, label2=label2)
    art = _normalize_kodi_item_art(art)
    info = dict(info or {})
    for _k in ('title', 'plot', 'genre', 'tagline', 'status'):
        if isinstance(info.get(_k), str):
            info[_k] = tr(info.get(_k))
    # Last writer wins: Nuvio/server/widget callers cannot override a unified
    # selection after this point. Never translate the selected source's text.
    try:
        from . import render_metadata
        info, art = render_metadata.apply(info, art, ids=ids, path=path)
        policy_applied = info.pop('_dexhub_policy_applied', False)
        poster_applied = info.pop('_dexhub_poster_applied', False)
        source_poster = info.pop('_dexhub_source_poster', None)     # v5.10.288: the listing's own poster
        if policy_applied:
            cast = info.pop('cast', None)
        if policy_applied or poster_applied:
            properties = dict(properties or {})
            if source_poster is not None:
                # v5.10.288: the listing's own poster goes with the card (a
                # Home row re-dressed for another poster choice starts from it)
                properties['dexhub.source_poster'] = source_poster
            keys = ('poster', 'tvshow.poster', 'season.poster', 'thumb', 'thumbnail', 'cover', 'image', 'icon', 'tvshow.thumb')
            if policy_applied:
                keys += ('icon', 'image', 'cover', 'banner', 'clearart', 'tvshow.thumb',
                         'fanart', 'fanart_image', 'landscape', 'clearlogo', 'logo', 'tvshow.clearlogo')
            for key in keys:
                properties.pop(key, None)
            if policy_applied:
                studio = info.get('studio') or ''
                studio = ', '.join(str(v) for v in studio) if isinstance(studio, (list, tuple)) else str(studio)
                properties['dexhub.studio'] = studio
                properties['dexhub.metadata_source'] = ADDON.getSetting('global_meta_source_id') or ''
                properties['dexhub.badge_line'] = ' • '.join(str(v) for v in (
                    info.get('year'), ('★ %s' % info['rating']) if info.get('rating') else '', studio) if v)
        if info.get('title') and info.get('mediatype') in ('movie', 'tvshow', 'episode'):
            item.setLabel(str(info['title']))
        if 'cast' in info:
            cast = info.pop('cast')
    except Exception:
        pass
    # Pull cast out of info if caller stuffed it there (legacy callers do this).
    if cast is None and isinstance(info.get('cast'), list):
        cast = info.pop('cast')
    _apply_unique_ids(item, ids, info)
    used_videoinfotag = False
    if _plugin()._KODI_MAJOR >= 20 and info:
        used_videoinfotag = _videoinfotag_apply(item, info, ids=ids, cast=cast)
    if info and not used_videoinfotag:
        try:
            item.setInfo('video', info)
        except Exception:
            pass
    if art:
        try:
            from . import image_policy as _image_policy
            art = _image_policy.bound_art(art)
        except Exception:
            pass
        try:
            item.setArt(art)
        except Exception:
            pass
        if _plugin()._KODI_MAJOR >= 20:
            try:
                item.getVideoInfoTag(offscreen=True).setArtwork({k: v for k, v in (art or {}).items() if v})
            except Exception:
                pass
    fanart_image = (art or {}).get('fanart') or (art or {}).get('landscape') or (art or {}).get('poster') or addon_fanart()
    props = {'fanart_image': fanart_image}
    if ids:
        try:
            if ids.get('tmdb_id'):
                props['tmdb_id'] = str(ids.get('tmdb_id'))
            if ids.get('imdb_id'):
                props['imdb_id'] = str(ids.get('imdb_id'))
                props['imdb'] = str(ids.get('imdb_id'))
                props['IMDBNumber'] = str(ids.get('imdb_id'))
            if ids.get('tvdb_id'):
                props['tvdb_id'] = str(ids.get('tvdb_id'))
            mt_info = str((info or {}).get('mediatype') or '').lower()
            props['tmdb_type'] = 'tv' if mt_info in ('tvshow', 'season', 'episode') else 'movie'
        except Exception:
            pass
    try:
        if (not is_folder) and 'action=play_item' in str(path or ''):
            props.setdefault('IsPlayable', 'true')
    except Exception:
        pass
    if art:
        for art_key in ('clearlogo', 'logo', 'tvshow.clearlogo', 'clearart', 'landscape', 'banner', 'poster', 'tvshow.poster', 'season.poster', 'thumb', 'icon', 'thumbnail', 'cover', 'image', 'tvshow.thumb', 'fanart'):
            if art.get(art_key):
                props[art_key] = art.get(art_key)
    props.update(_favorite_properties(is_favorite))
    if properties:
        props.update(properties)
    try:
        item.setProperties(props)
    except Exception:
        pass
    if context_menu:
        try:
            # replaceItems=True so we OWN the menu. Previously False appended
            # to Kodi's defaults, which on some skins (Aura, Arctic Fuse,
            # Estuary MOD) injects a built-in 'Add to favourites' that the
            # skin then renders as a persistent heart overlay on EVERY
            # poster — making the heart meaningless as a "this is favourited"
            # marker. Replacing the menu also gives us full control over
            # the available actions.
            item.addContextMenuItems([(tr(lbl), _menu_command(act)) for (lbl, act) in list(context_menu)], replaceItems=True)
        except Exception:
            pass
    # v5.10.96: the Dex Hub Home window runs normal routes with a capture
    # sink on its own worker thread (see capture.py). The finished item goes
    # to that sink instead of Kodi's directory, and nothing else changes.
    try:
        from . import capture as _capture
        if _capture.active():
            _capture.record(path, is_folder, label, label2, info, art, ids, cast, props,
                            [(tr(lbl), act) for (lbl, act) in list(context_menu or [])],
                            item)
            return
    except Exception:
        pass
    try:
        from . import widget_cache as _widget_cache
        if _widget_cache.recording():
            _widget_cache.record(path, is_folder, label, label2, info, art, ids, cast, props,
                                 [(tr(lbl), act) for (lbl, act) in list(context_menu or [])])
    except Exception:
        pass
    _plugin()._PENDING_ITEMS.append((path, item, is_folder))


def _infer_content_type():
    """Infer Kodi content from the active route when a caller omitted it."""
    try:
        raw = sys.argv[2] if len(sys.argv) > 2 else ''
        params = dict(urllib.parse.parse_qsl(str(raw or '').lstrip('?'), keep_blank_values=True))
    except Exception:
        params = {}
    action = str(params.get('action') or '').strip().lower()
    media_type = str(params.get('media_type') or params.get('type') or '').strip().lower()
    bucket = str(params.get('bucket') or '').strip().lower()

    if action in {
            'episodes', 'nextup', 'next_up', 'calendar', 'plex_on_deck',
            'emby_nextup', 'emby_continue', 'simkl_continue'}:
        return 'episodes'
    if action in {'seasons', 'series_meta', 'show_seasons'}:
        return 'seasons'
    if action in {
            'continue', 'favorites', 'hub_search_menu', 'search_from_history',
            'people_search',
            'mdblist_watchlist_browse'}:
        return 'videos'

    if media_type in ('episode', 'episodes'):
        return 'episodes'
    if media_type in ('season', 'seasons'):
        return 'seasons'
    if media_type in ('series', 'show', 'shows', 'tv', 'tvshow', 'anime'):
        return 'tvshows'
    if media_type in ('movie', 'movies'):
        return 'movies'
    if media_type in ('video', 'videos', 'channel', 'channels', 'live', 'all', 'mixed'):
        return 'videos'

    if bucket in ('series', 'anime', 'turkish', 'korean'):
        return 'tvshows'
    if bucket == 'movies':
        return 'movies'
    if bucket in ('live', 'arabic'):
        return 'videos'
    return 'files'


def end_dir(content=None, cache=True, sortable=True):
    try:
        from . import capture as _capture
        if _capture.active():
            return
    except Exception:
        pass
    resolved_content = str(content or '').strip().lower() or _infer_content_type()
    xbmcplugin.setContent(HANDLE, resolved_content)
    _apply_sort_methods(resolved_content, sortable=sortable)
    _flush_pending_items()
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=cache)
    try:
        from . import widget_cache as _widget_cache
        _widget_cache.finish(resolved_content)
    except Exception:
        pass
    try:
        from . import metadata_warm
        metadata_warm.flush()
    except Exception:
        pass


def _merge_seed_ids(base_ids, seed_ids):
    """Non-empty seed IDs win over empty base IDs but never overwrite a resolved value."""
    base_ids = dict(base_ids or {})
    for k, v in (seed_ids or {}).items():
        if v and not base_ids.get(k):
            base_ids[k] = v
    return base_ids


def _get_tmdbh_seed_ids(canonical_id=None):
    """Return seed IDs only when the stash's canonical_id matches (or no canonical filter given)."""
    try:
        win = xbmcgui.Window(WINDOW_ID)
        raw = win.getProperty('dexhub.tmdbh_seed_ids') or ''
        scoped_for = win.getProperty('dexhub.tmdbh_seed_for') or ''
        if not raw:
            return {}
        # If caller specified a canonical_id and the stash was set for a different one, don't leak.
        if canonical_id and scoped_for and scoped_for != canonical_id:
            return {}
        return json.loads(raw) or {}
    except Exception:
        return {}


def _prompt_text(default_text, heading):
    kb = xbmc.Keyboard(default_text or '', tr(heading))
    kb.doModal()
    if not kb.isConfirmed():
        return None
    return (kb.getText() or '').strip()


def _derive_name_from_slug(slug):
    slug = str(slug or '').strip().strip('/')
    if not slug:
        return 'Collection'
    name = slug.replace('-', ' ').replace('_', ' ')
    return MULTI_WS_RE.sub(' ', name).strip().title() or 'Collection'
