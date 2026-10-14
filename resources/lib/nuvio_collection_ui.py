# -*- coding: utf-8 -*-
"""Lazy Nuvio/Kaptain collection browser for Dex Hub 5.4.14.

Kept outside plugin.py so the normal Dex Hub cold path does not grow with the
large native collection adapter. Imported only when a Nuvio collection opens.
"""
import json
import re

import xbmcgui
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import collection_sets as _collections_mod
from . import plugin as _plugin
from . import selfhost_aio as _selfhost
from .dexhub import nuvio_contract as _contract

tr = _plugin.tr
error = _plugin.error
end_dir = _plugin.end_dir
add_item = _plugin.add_item
build_url = _plugin.build_url
root_art = _plugin.root_art
section_art = _plugin.section_art
collection_banner_art = _plugin.collection_banner_art
addon_fanart = _plugin.addon_fanart
WINDOW_ID = _plugin.WINDOW_ID
_wrap_tokenized_url = _plugin._wrap_tokenized_url
_collection_entry_preview = _plugin._collection_entry_preview
_clean_collection_view_enabled = _plugin._clean_collection_view_enabled
_open_addon_catalog_entry = _plugin._open_addon_catalog_entry
_listing_page_size = _plugin._listing_page_size
_content_click_path = _plugin._content_click_path
_build_source_picker_menu = _plugin._build_source_picker_menu
_pagination_hidden = getattr(_plugin, '_pagination_hidden', lambda: False)

def _nuvio_find_group_folder(set_id, group_id='', folder_id=''):
    """Return (set, group, folder) from a lazily imported Nuvio collection."""
    row = _collections_mod.get_set(set_id)
    if not row:
        return None, None, None
    group = None
    folder = None
    for entry in row.get('entries') or []:
        if str(entry.get('id') or '') == str(group_id or ''):
            group = entry
            break
    # A top-level nuvioFolder is also supported for hand-made/single-group JSON.
    if group is None and not group_id:
        for entry in row.get('entries') or []:
            if entry.get('kind') == 'nuvioFolder' and str(entry.get('id') or '') == str(folder_id or ''):
                folder = entry
                break
    if group and group.get('kind') == 'nuvioGroup':
        for candidate in (group.get('payload') or {}).get('folders') or []:
            if str(candidate.get('id') or '') == str(folder_id or ''):
                folder = candidate
                break
    return row, group, folder


def _compat_nuvio_source(source):
    """Upgrade legacy Nuvio ``catalogSources[]`` rows in-place for UI use.

    Account collections can still contain the legacy shape
    {addonId,type,catalogId}.  The native browser expects ``sourceKind``;
    without this adapter those folders rendered but their sources opened as an
    unsupported/empty type.
    """
    src = dict(source or {}) if isinstance(source, dict) else {}
    if not src.get('sourceKind') and (src.get('addonId') or src.get('addon_id')) and (src.get('catalogId') or src.get('catalog_id')):
        src['sourceKind'] = 'addonCatalog'
        src['addonId'] = src.get('addonId') or src.get('addon_id') or ''
        src['catalogId'] = src.get('catalogId') or src.get('catalog_id') or ''
        src['catalogType'] = src.get('catalogType') or src.get('type') or src.get('mediaType') or 'movie'
        src['mediaType'] = src.get('mediaType') or src.get('catalogType') or src.get('type') or 'movie'
        src['name'] = src.get('name') or src.get('title') or src.get('genre') or src.get('catalogId') or 'Catalog'
        if not isinstance(src.get('extra'), dict):
            src['extra'] = {}
    return src


def _nuvio_collection_meta_target(set_id='', group_id='', folder=None):
    root_id = str(group_id or ((folder or {}).get('id') if isinstance(folder, dict) else '') or 'root')
    return 'virtual.collection.nuvio:%s:%s' % (str(set_id or ''), root_id)


def _nuvio_apply_collection_meta(meta, media_type, meta_target=''):
    """Nuvio profile prefs by default; explicit collection picker wins."""
    row = dict(meta or {})
    try:
        from . import meta_source as _ms
        explicit = _ms.get_explicit_meta_source_for(meta_target) if meta_target else ''
        if explicit:
            return _ms.override_virtual_meta(
                meta_target, media_type, row, cache_only=True)
    except Exception:
        pass
    try:
        return (_plugin._apply_nuvio_metadata_preferences(media_type, [row]) or [row])[0]
    except Exception:
        return row


def _nuvio_source_label(source):
    source = _compat_nuvio_source(source)
    name = str(source.get('name') or 'Source').strip() or 'Source'
    sk = str(source.get('sourceKind') or '').strip()
    if sk == 'tmdb':
        suffix = 'TMDb %s' % str(source.get('tmdbSourceType') or 'DISCOVER').title()
    elif sk == 'traktIdList':
        suffix = 'Trakt'
    elif sk == 'addonCatalog':
        media_type = _contract.catalog_type(
            source.get('catalogType') or source.get('mediaType') or 'movie')
        provider = None
        catalog = None
        try:
            provider = _collections_mod.find_nuvio_provider_by_addon_id(source.get('addonId') or '')
            if provider:
                catalogs = _plugin._provider_catalogs(provider)
                wanted_id = str(source.get('catalogId') or '')
                catalog = _contract.match_catalog(catalogs, wanted_id, media_type)
                if catalog is None and ',' in wanted_id:
                    catalog = _contract.match_catalog(
                        catalogs, wanted_id.split(',', 1)[0], media_type)
        except Exception:
            provider = None
        # Nuvio's addon-source fallback uses its localized plural resources
        # (type_series_plural/type_movies), not the English navigation labels.
        fallback = tr('مسلسلات') if media_type in ('series', 'tv', 'show', 'anime') else tr('أفلام')
        base_name = str((catalog or {}).get('name') or fallback).strip() or fallback
        if base_name:
            base_name = base_name[:1].upper() + base_name[1:]
        genre = str(source.get('genre') or '').strip()
        name = '%s · %s' % (base_name, genre) if genre and genre.lower() != 'none' else base_name
        suffix = str((provider or {}).get('name') or source.get('addonId') or 'Addon')
    else:
        suffix = sk or 'Source'
    return name, suffix


def open_group_entry(set_id, entry, media_filter=''):
    """Render a Nuvio group, optionally split into Movies or TV.

    The split is metadata-only and therefore cheap enough for AF3 widgets: no
    source is fetched until its folder is opened.
    """
    payload = entry.get('payload') or {}
    folders = [f for f in (payload.get('folders') or []) if _collections_mod.folder_supports_media(f, media_filter)]
    if not folders:
        error(tr('لا توجد مجلدات داخل المجموعة'))
        return end_dir()
    win = xbmcgui.Window(WINDOW_ID)
    if entry.get('background'):
        win.setProperty('fanart', _wrap_tokenized_url(entry.get('background') or ''))
    for folder in folders:
        label = str(folder.get('name') or 'Folder')
        preview = _collection_entry_preview(folder)
        presentation = folder.get('payload') or {}
        # v5.4.17: Nuvio coverImageUrl is the visible AF3 card;
        # heroBackdropUrl is reserved for fanart/focus background.
        bg = _wrap_tokenized_url(folder.get('background') or preview.get('fanart') or '')
        explicit_card = _wrap_tokenized_url(folder.get('poster') or '')
        clearlogo = _wrap_tokenized_url(folder.get('clearlogo') or preview.get('clearlogo') or '')
        if explicit_card:
            art = {
                'icon': explicit_card, 'poster': explicit_card, 'thumb': explicit_card,
                'landscape': explicit_card, 'banner': explicit_card,
                'fanart': bg or entry.get('background') or addon_fanart(),
                'clearlogo': clearlogo,
            }
            tile_shape = 'landscape' if str(folder.get('layout') or '').lower() in ('wide','banner','landscape') else 'poster'
        else:
            # v5.4.20: coverless folders are navigation nodes. Give AF3 a
            # dedicated modern square icon instead of recycling hero fanart.
            art = section_art(label, media_type=media_filter or 'movie', fanart=bg or entry.get('background') or addon_fanart())
            tile_shape = 'square'
            if clearlogo:
                art['clearlogo'] = clearlogo
        if art.get('clearlogo'):
            art['logo'] = art['clearlogo']; art['tvshow.clearlogo'] = art['clearlogo']
        focus_gif = _wrap_tokenized_url(presentation.get('focusGifUrl') or '')
        if focus_gif and presentation.get('focusGifEnabled', True):
            art['animatedposter'] = focus_gif
            art['focusgif'] = focus_gif
        sources = (folder.get('payload') or {}).get('sources') or []
        matching_count = sum(1 for src in sources if _collections_mod.source_matches_media(src, media_filter))
        add_item(
            '' if (_clean_collection_view_enabled() and folder.get('hide_title') and explicit_card) else label,
            build_url(action='collection_group_folder_open', set_id=set_id,
                      group_id=entry.get('id') or '', folder_id=folder.get('id') or '',
                      media_filter=media_filter),
            info={'title': label, 'plot': tr('%d مصدر داخل هذا المجلد') % matching_count, 'mediatype': 'video'},
            art=art,
            context_menu=[
                (tr('تغيير مزود البوستر والميتاداتا'), 'RunPlugin(%s)' % build_url(
                    action='meta_pick_target',
                    target_key=_nuvio_collection_meta_target(set_id, entry.get('id') or '', folder),
                    title=tr('مزود البوستر والميتاداتا • %s') % (entry.get('name') or 'Nuvio Collection'))),
            ],
            properties={
                'dexhub.tile_shape': tile_shape,
                'dexhub.collection_folder': '1',
                'dexhub.clearlogo_as_title': '1',
                'dexhub.collection_media': media_filter or 'mixed',
                'dexhub.cover_emoji': str(presentation.get('coverEmoji') or ''),
                'dexhub.focus_gif': focus_gif,
                'dexhub.hero_video': str(presentation.get('heroVideoUrl') or ''),
                'dexhub.nuvio_tile_shape': str(presentation.get('tileShape') or ''),
            },
        )
    # v5.4.22: folders/categories get their own AF3 view bucket too.
    # Top-level Collections use `videos` (Row Landscape), these navigation
    # nodes use `files` (Icon/Tile), and the leaf works use movies/tvshows
    # (Row Poster). This prevents a view choice at one level changing another.
    return end_dir(content='files', cache=True)


def _native_aio_source(source):
    try:
        return _selfhost.native_source_for_catalog(source)
    except Exception:
        return None


def _default_work_poster(media_type='movie'):
    """Packaged last-resort poster for a real work item.

    Navigation art must never leak into leaf rows.  If every canonical art
    source is unavailable (offline cache miss, no TMDb key, no IMDb poster),
    use a neutral *work* poster so AF3 still renders a stable Poster rail.
    """
    asset = 'default_series_poster.png' if str(media_type or '').lower() in ('series','tv','show','tvshow') else 'default_movie_poster.png'
    try:
        return _plugin.media_path(asset)
    except Exception:
        try:
            return root_art('catalogs').get('poster') or ''
        except Exception:
            return ''


def _contextual_section_art(name, folder_name, media_type='movie', fanart='', wide=False):
    """Section art that borrows the parent folder's theme on generic names.

    v5.4.27 — "All" inside "Top Streaming Movies" used to render the generic
    media-grid fallback between real Netflix/Hulu covers. When a selector
    name resolves to the default icon, retry with the FOLDER name so the
    tile inherits its section's theme: All inside Top Streaming -> the
    streaming icon, All inside Networks -> the networks icon. Names with a
    theme of their own ("Action Movies") are never overridden.

    v5.4.28 — exports declare tileShape LANDSCAPE on these folders. When the
    folder is wide, a generic name gets the theme's true 16:9 banner asset
    (collection_banner_*) instead of a square icon, so "All" sits between
    the platform covers as a proper banner.
    """
    art = section_art(name, media_type=media_type, fanart=fanart)
    icon = str(art.get('icon') or '')
    if icon.endswith('section_genres.png') and folder_name:
        if wide:
            banner = collection_banner_art(folder_name, fanart=fanart)
            banner_icon = str(banner.get('poster') or '')
            if banner_icon and not banner_icon.endswith('collection_banner_default.png'):
                return banner
        parent = section_art(folder_name, media_type=media_type, fanart=fanart)
        parent_icon = str(parent.get('icon') or '')
        if parent_icon and not parent_icon.endswith('section_genres.png'):
            return parent
    return art


def _collection_show_all(set_id, group_id, source_count):
    if source_count < 2:
        return False
    try:
        row = _collections_mod.get_set(set_id) or {}
        for entry in row.get('entries') or []:
            if str(entry.get('id') or '') != str(group_id or ''):
                continue
            return bool((entry.get('payload') or {}).get('showAllTab', True))
    except Exception:
        pass
    return True


def _source_navigation_art(source, folder, set_id, background=''):
    """Identity art for a source tab; never a work/poster substitution."""
    source = _compat_nuvio_source(source)
    name, _suffix = _nuvio_source_label(source)
    media_type = str(source.get('mediaType') or source.get('catalogType') or 'movie').lower()
    if str(source.get('sourceKind') or '') == 'addonCatalog':
        try:
            provider = _collections_mod.find_nuvio_provider_by_addon_id(source.get('addonId') or '')
            if provider:
                manifest = provider.get('manifest') or {}
                return _plugin.provider_art(
                    provider.get('name') or manifest.get('name') or name,
                    manifest,
                    provider.get('base_url') or provider.get('manifest_url') or '')
        except Exception:
            pass
    wide = str(folder.get('layout') or '').strip().lower() in ('wide', 'landscape', 'banner')
    return _contextual_section_art(
        name, folder.get('name') or '', media_type=media_type,
        fanart=background or addon_fanart(), wide=wide)


def open_folder_entry(folder, set_id='', group_id='', media_filter=''):
    """Open a Nuvio folder without collapsing or rewriting its source tabs.

    Kodi has no native tab strip, so a multi-source Nuvio folder is represented
    as ``All`` followed by one navigation item per exact source. Source order,
    duplicate visible names and original addonId/catalogId identities are kept.
    """
    raw_sources = (folder.get('payload') or {}).get('sources') or []
    sources = [(idx, _compat_nuvio_source(src)) for idx, src in enumerate(raw_sources)
               if _collections_mod.source_matches_media(_compat_nuvio_source(src), media_filter)]
    if not sources:
        error(tr('لا توجد مصادر داخل هذا المجلد'))
        return end_dir()
    if len(sources) == 1:
        original_idx, source = sources[0]
        return _open_nuvio_source(folder, source, set_id, group_id, original_idx, page=1)

    bg = _wrap_tokenized_url(folder.get('background') or '')
    if bg:
        xbmcgui.Window(WINDOW_ID).setProperty('fanart', bg)
    folder_wide = str(folder.get('layout') or '').strip().lower() in ('wide', 'landscape', 'banner')
    tile_shape = 'landscape' if folder_wide else 'square'
    context_menu = [
        (tr('تغيير مزود البوستر والميتاداتا'), 'RunPlugin(%s)' % build_url(
            action='meta_pick_target',
            target_key=_nuvio_collection_meta_target(set_id, group_id, folder),
            title=tr('مزود البوستر والميتاداتا • %s') %
                  (folder.get('name') or 'Nuvio Collection'))),
    ]

    if _collection_show_all(set_id, group_id, len(sources)):
        indexes = ','.join(str(idx) for idx, _source in sources)
        all_label = tr('الكل')
        add_item(
            all_label,
            build_url(action='collection_nuvio_sources_open', set_id=set_id,
                      group_id=group_id, folder_id=folder.get('id') or '',
                      source_indexes=indexes, page='1'),
            info={'title': all_label,
                  'plot': tr('عرض موحّد لمصادر Nuvio بهذا الترتيب.'),
                  'mediatype': 'video'},
            art=_contextual_section_art(
                all_label, folder.get('name') or '', media_type=media_filter or 'movie',
                fanart=bg or addon_fanart(), wide=folder_wide),
            context_menu=context_menu,
            properties={'dexhub.tile_shape': tile_shape,
                        'dexhub.nuvio_all_tab': '1',
                        'dexhub.nuvio_source_order': '-1'},
        )

    for order, (idx, source) in enumerate(sources):
        name, suffix = _nuvio_source_label(source)
        add_item(
            name,
            build_url(action='collection_nuvio_source_open', set_id=set_id,
                      group_id=group_id, folder_id=folder.get('id') or '',
                      source_index=str(idx), page='1'),
            info={'title': name,
                  'plot': tr('مصدر %s داخل %s') % (suffix, folder.get('name') or ''),
                  'mediatype': 'video'},
            art=_source_navigation_art(source, folder, set_id, background=bg),
            context_menu=context_menu,
            properties={'dexhub.tile_shape': tile_shape,
                        'dexhub.nuvio_source_tab': '1',
                        'dexhub.nuvio_source_order': str(order),
                        'dexhub.nuvio_source_kind': str(source.get('sourceKind') or '')},
        )
    return end_dir(content='files', cache=True)


def open_folder(set_id='', group_id='', folder_id='', media_filter=''):
    row, group, folder = _nuvio_find_group_folder(set_id, group_id, folder_id)
    if not row or not folder:
        error(tr('المجلد غير موجود'))
        return end_dir()
    return open_folder_entry(folder, set_id=set_id, group_id=group_id, media_filter=media_filter)


def _overrides(text):
    """The Home grid's choice for a TMDb Discover source (v5.10.107): only
    TMDb Discover parameter names, as plain strings."""
    try:
        data = json.loads(text) if text else {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        key = str(key)
        if re.match(r'^[a-z_]+(?:\.(?:gte|lte))?$', key) and key not in ('api_key', 'page', 'language'):
            out[key] = '' if value is None else str(value)[:64]
    return out


def open_source(set_id='', group_id='', folder_id='', source_index='0', page='1', tmdb=''):
    row, group, folder = _nuvio_find_group_folder(set_id, group_id, folder_id)
    if not row or not folder:
        error(tr('المجلد غير موجود'))
        return end_dir()
    sources = (folder.get('payload') or {}).get('sources') or []
    try:
        idx = max(0, int(source_index or 0))
    except Exception:
        idx = 0
    if idx >= len(sources):
        error(tr('المصدر غير موجود'))
        return end_dir()
    try:
        page_num = max(1, int(page or 1))
    except Exception:
        page_num = 1
    return _open_nuvio_source(folder, _compat_nuvio_source(sources[idx]), set_id, group_id, idx, page=page_num,
                              overrides=_overrides(tmdb))


def open_sources(set_id='', group_id='', folder_id='', source_indexes='', page='1'):
    row, group, folder = _nuvio_find_group_folder(set_id, group_id, folder_id)
    if not row or not folder:
        error(tr('المجلد غير موجود'))
        return end_dir()
    raw = (folder.get('payload') or {}).get('sources') or []
    wanted = []
    for token in str(source_indexes or '').split(','):
        try:
            idx = int(token.strip())
        except Exception:
            continue
        if 0 <= idx < len(raw):
            wanted.append((idx, _compat_nuvio_source(raw[idx])))
    if not wanted:
        error(tr('المصادر غير موجودة'))
        return end_dir()
    try:
        page_num = max(1, int(page or 1))
    except Exception:
        page_num = 1
    return _open_nuvio_sources_merged(folder, wanted, set_id, group_id, page=page_num)


def _lists_catalog(manifest, source):
    """False only when an add-on's manifest is known and does not list the
    source's catalog (a manifest that was not read gives the benefit of the doubt)."""
    catalogs = (manifest or {}).get('catalogs') if isinstance(manifest, dict) else None
    if not catalogs:
        return True
    cid = str((source or {}).get('catalogId') or '').strip()
    return any(isinstance(c, dict) and str(c.get('id') or '') == cid for c in catalogs)


_OWN_AIO = {}


def _own_aio_for(source):
    """The manifest link of the user's own AIOMetadata (Dex Community's link,
    else one among the user's add-ons) when it lists this catalog, or ''."""
    addon_id = str((source or {}).get('addonId') or '').lower()
    if 'aio' not in addon_id or 'meta' not in addon_id:
        return ''
    try:
        from .homeui import community as _community
        candidates = []
        url = _community.aio_url()
        if url:
            candidates.append((url, None))
        found = _community.installed_aio()
        if found:
            # v5.10.198: an installed one answers from the manifest saved with
            # it (no request; it was read again for every row after 5 minutes)
            candidates.append((found[1], found[2] if len(found) > 2 else None))
    except Exception:
        return ''
    for url, saved in candidates:
        manifest = saved if isinstance(saved, dict) and saved.get('catalogs') else _OWN_AIO.get(url)
        if manifest is None:
            try:
                # the linked one: its catalogs change rarely, kept a day
                from .dexhub.client import get_json
                manifest = get_json(url, ttl_seconds=24 * 3600) or {}
            except Exception:
                manifest = {}
            if not isinstance(manifest, dict):
                manifest = {}
            _OWN_AIO[url] = manifest
        if manifest.get('catalogs') and _lists_catalog(manifest, source):
            return url
    return ''


def _open_nuvio_source(folder, source, set_id='', group_id='', source_index=0, page=1, overrides=None):
    source = _compat_nuvio_source(source)
    sk = str((source or {}).get('sourceKind') or '').strip()
    if sk == 'addonCatalog':
        # v5.4.42: Nuvio's collection source is authoritative. If the exact
        # addon is installed, call that addon/catalog directly even when the
        # catalog id resembles an aio-metadata/TMDb shortcut. Older builds
        # converted those rows before checking the installed provider, which
        # changed both the source and its artwork/metadata compared with Nuvio.
        try:
            exact_provider = _collections_mod.find_nuvio_provider_by_addon_id(source.get('addonId') or '')
        except Exception:
            exact_provider = None

        # v5.10.195: an add-on that does not list this catalog cannot serve
        # it (an AIOMetadata of another configuration answered nothing)
        if exact_provider and not _lists_catalog((exact_provider or {}).get('manifest'), source):
            exact_provider = None
        # v5.10.195 (Dex Community): the user's own AIOMetadata first, when it
        # lists the catalog; then TMDb directly with the user's key
        own_aio = '' if exact_provider else _own_aio_for(source)
        # Only translate a known aio-style source when the original addon is
        # genuinely unavailable. This is a compatibility fallback, never the
        # normal path.
        if not exact_provider and not own_aio:
            native = _native_aio_source(source)
            if native:
                nk = str(native.get('sourceKind') or '')
                if nk == 'tmdb':
                    return _render_nuvio_tmdb_source(folder, native, set_id, group_id, source_index, page)
                if nk == 'traktIdList':
                    return _render_nuvio_trakt_source(folder, native, set_id, group_id, source_index, page)
        payload = {
            'addonId': source.get('addonId') or '', 'catalogId': source.get('catalogId') or '',
            'catalogType': source.get('catalogType') or source.get('mediaType') or 'movie',
            'genre': source.get('genre') or '', 'extra': source.get('extra') or {},
            # Mark this as a real Nuvio collection source.  Catalog rendering
            # then follows the synced Nuvio artwork/metadata prefs, while a
            # collection-specific long-press poster provider can override it.
            'nuvio': '1',
            'nuvioNoKnownFallback': '1',
            'metaTarget': _nuvio_collection_meta_target(set_id, group_id, folder),
        }
        # Keep the EXACT addon source saved by Nuvio.  Older builds replaced
        # every addonCatalog with the optional self-host manifest whenever it
        # was enabled, so a collection could open a different provider than
        # Nuvio.  Self-host is now only a last-resort when the original addon
        # is genuinely unavailable locally.
        original_provider = exact_provider
        if own_aio:
            payload['addonId'] = own_aio
            payload.pop('nuvioNoKnownFallback', None)
        elif not original_provider:
            try:
                if _selfhost.enabled() and _selfhost.manifest_url():
                    payload['addonId'] = _selfhost.manifest_url()
            except Exception:
                pass
        entry = dict(folder or {})
        entry['name'] = source.get('name') or folder.get('name') or 'Catalog'
        entry['kind'] = 'addonCatalog'; entry['payload'] = payload
        return _open_addon_catalog_entry(entry, payload)
    if sk == 'tmdb':
        return _render_nuvio_tmdb_source(folder, source, set_id, group_id, source_index, page,
                                         overrides=overrides)
    if sk == 'traktIdList':
        return _render_nuvio_trakt_source(folder, source, set_id, group_id, source_index, page)
    error(tr('نوع مصدر Nuvio غير مدعوم: %s') % sk)
    return end_dir()


def _fetch_nuvio_addon_source_page(source, page, page_size, meta_target=''):
    """Fetch one exact addonCatalog as data for Nuvio's All tab."""
    from .homeui import stremio_catalogs as SC
    provider = _collections_mod.find_nuvio_provider_by_addon_id(source.get('addonId') or '')
    if not provider:
        return [], False
    media_type = _contract.catalog_type(
        source.get('catalogType') or source.get('mediaType') or 'movie') or 'movie'
    catalog_id = str(source.get('catalogId') or '').strip()
    catalogs = _plugin._provider_catalogs(provider)
    catalog_def = _contract.match_catalog(catalogs, catalog_id, media_type)
    if catalog_def is None and ',' in catalog_id:
        catalog_def = _contract.match_catalog(
            catalogs, catalog_id.split(',', 1)[0], media_type)
    catalog_def = catalog_def or {'id': catalog_id, 'type': media_type, 'name': source.get('name') or catalog_id, 'extra': []}
    supports_skip = SC.supports(catalog_def, 'skip')
    declared_source = source.get('extra') if isinstance(source.get('extra'), dict) else {}
    params = {'extras': json.dumps(declared_source)}
    if source.get('genre'):
        params['genre'] = source['genre']
    extra = SC.request_extras(catalog_def, params)
    if SC.supports(catalog_def, 'limit'):
        extra.setdefault('limit', str(page_size))
    if supports_skip and int(page or 1) > 1:
        extra['skip'] = str(max(0, int(page or 1) - 1) * page_size)
    try:
        data = _plugin.fetch_catalog(
            provider, media_type, catalog_id, extra=extra,
            timeout_override=_plugin.fast_timeout('catalog'), retry=False,
            rate_wait=0.5) or {}
        raw = [row for row in (data.get('metas') or []) if isinstance(row, dict)]
    except Exception:
        return [], False
    if supports_skip:
        page_rows = raw[:page_size]
        has_more = bool(raw)
    else:
        start = max(0, int(page or 1) - 1) * page_size
        page_rows = raw[start:start + page_size]
        has_more = len(raw) > start + page_size
    enriched = _plugin._enrich_listing_metas_batch(
        provider, media_type, page_rows, nuvio_mode=True, meta_target=meta_target)
    return [{'_provider': provider, '_catalog': catalog_def,
             '_media_type': media_type, '_meta': meta}
            for meta in enriched if isinstance(meta, dict)], has_more


def _render_nuvio_addon_all(folder, indexed_sources, set_id='', group_id='', page=1):
    """Round-robin exact Stremio catalogs like Nuvio's native All tab."""
    page_size = max(20, _listing_page_size())
    meta_target = _nuvio_collection_meta_target(set_id, group_id, folder)
    source_rows = [None] * len(indexed_sources)
    has_more = False
    workers = min(4, len(indexed_sources))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {
            pool.submit(_fetch_nuvio_addon_source_page, source, page, page_size,
                        meta_target): pos
            for pos, (_idx, source) in enumerate(indexed_sources)
        }
        for future in as_completed(future_map):
            pos = future_map[future]
            try:
                rows, source_more = future.result()
            except Exception:
                rows, source_more = [], False
            source_rows[pos] = rows
            has_more = has_more or source_more
    source_rows = [rows or [] for rows in source_rows]

    merged = []
    seen = set()
    max_len = max([len(rows) for rows in source_rows] or [0])
    for pos in range(max_len):
        for rows in source_rows:
            if pos >= len(rows):
                continue
            wrapped = rows[pos]
            meta = wrapped.get('_meta') or {}
            key = (wrapped.get('_media_type') or '',
                   str(meta.get('id') or '') or '%s:%s' % (
                       meta.get('name') or meta.get('title') or '',
                       meta.get('year') or meta.get('releaseInfo') or ''))
            if key in seen:
                continue
            seen.add(key)
            merged.append(wrapped)
            if len(merged) >= page_size:
                break
        if len(merged) >= page_size:
            break

    first_fanart = ''
    found_movie = False
    found_series = False
    for wrapped in merged:
        provider = wrapped.get('_provider') or {}
        catalog_def = wrapped.get('_catalog') or {}
        media_type = wrapped.get('_media_type') or 'movie'
        meta = _plugin._normalize_meta_art_urls(provider, wrapped.get('_meta') or {})
        meta_id = meta.get('id')
        label = meta.get('name') or meta.get('title') or meta_id or 'Unknown'
        fallback = _plugin._item_fallback_art(
            provider, media_type, catalog_def, label)
        art = _plugin._resolve_meta_art_fast(
            provider, media_type, meta, fallback_art=fallback)
        if not first_fanart and art.get('fanart'):
            first_fanart = art.get('fanart')
        info = _plugin._meta_info(meta)
        ids = _plugin.extract_ids(meta)
        path, is_folder = _content_click_path(
            media_type=media_type, canonical_id=meta_id, title=label,
            tmdb_id=ids.get('tmdb_id') or '', imdb_id=ids.get('imdb_id') or '',
            tvdb_id=ids.get('tvdb_id') or '',
            source_provider_id=provider.get('id') or '', ui_seed=art)
        try:
            _plugin._meta_mem_put(
                media_type, meta_id, meta, source_provider_id=provider.get('id') or '')
        except Exception:
            pass
        add_item(
            label, path, is_folder=is_folder, info=info, art=art, ids=ids,
            properties={'dexhub.tile_shape': 'poster',
                        'dexhub.work_item': '1',
                        'dexhub.poster_item': '1',
                        'dexhub.metadata_source': 'nuvio-collection'},
            context_menu=[
                (tr('تغيير مزود البوستر والميتاداتا'), 'RunPlugin(%s)' % build_url(
                    action='meta_pick_target', target_key=meta_target,
                    title=tr('مزود البوستر والميتاداتا • %s') %
                          (folder.get('name') or 'Nuvio Collection'))),
            ] + _build_source_picker_menu(
                media_type=media_type, canonical_id=meta_id, title=label,
                source_provider_id=provider.get('id') or ''))
        media_bucket = _contract.normalize_media_type(media_type)
        found_series = found_series or media_bucket == 'series'
        found_movie = found_movie or media_bucket == 'movie'

    if first_fanart:
        try:
            xbmcgui.Window(WINDOW_ID).setProperty('fanart', first_fanart)
        except Exception:
            pass
    if has_more and merged and not _pagination_hidden():
        indexes = ','.join(str(idx) for idx, _source in indexed_sources)
        add_item(
            tr('المزيد'),
            build_url(action='collection_nuvio_sources_open', set_id=set_id,
                      group_id=group_id, folder_id=folder.get('id') or '',
                      source_indexes=indexes, page=str(int(page or 1) + 1)),
            art=root_art('catalogs'), info={'title': tr('المزيد')})
    content = 'videos' if found_movie and found_series else ('tvshows' if found_series else 'movies')
    return end_dir(content=content, cache=True)


def _fetch_tmdb_trakt_all_source(source, page, limit):
    kind = str(source.get('sourceKind') or '')
    if kind == 'tmdb':
        from . import tmdb_direct as _tmdb
        rows = _tmdb.nuvio_source_items(source, page=page, limit=20) or []
        has_more = (len(rows) >= 20 and
                    str(source.get('tmdbSourceType') or 'DISCOVER').upper() != 'COLLECTION')
        return rows, has_more
    if kind != 'traktIdList':
        return [], False
    mt = str(source.get('mediaType') or '').lower()
    list_type = ('shows' if mt in ('series', 'tv', 'show', 'anime') else
                 ('movies' if mt == 'movie' else 'movies,shows'))
    from . import trakt
    raw_rows = trakt.fetch_list_by_id(
        source.get('traktListId') or '', page=page, limit=limit,
        list_type=list_type) or []
    mapped = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        obj = raw.get('show') or raw.get('movie') or {}
        if not isinstance(obj, dict):
            continue
        is_show = bool(raw.get('show')) or str(raw.get('type') or '').lower() == 'show'
        mapped.append({
            '_type': 'series' if is_show else 'movie',
            'title': obj.get('title') or '',
            'year': obj.get('year') or '',
            'released': obj.get('released') or obj.get('first_aired') or '',
            'overview': obj.get('overview') or '',
            'ids': obj.get('ids') or {},
        })
    _prefetch_trakt_posters(mapped)
    rows = []
    for item in mapped:
        ids = item.get('ids') or {}
        tmdb_id = str(ids.get('tmdb') or '').strip()
        if not tmdb_id:
            continue
        rows.append({
            'media_type': item.get('_type') or 'movie',
            'tmdb_id': tmdb_id,
            'title': item.get('title') or '',
            'year': str(item.get('year') or ''),
            'released': item.get('released') or '',
            'releaseInfo': item.get('released') or item.get('year') or '',
            'overview': item.get('overview') or '',
            'poster': item.get('poster') or '',
            'backdrop': item.get('backdrop') or '',
            'rating': item.get('rating') or 0,
        })
    return rows, len(raw_rows) >= limit


def _render_nuvio_tmdb_trakt_all(folder, indexed_sources, set_id='', group_id='', page=1):
    """All tab for the mixed TMDb + Trakt folders shipped by Nuvio."""
    from . import tmdb_direct as _tmdb
    if not _tmdb._api_key():
        # Keep source tabs usable without pretending another provider is TMDb.
        return _open_nuvio_sources_merged_fallback(
            folder, indexed_sources, set_id, group_id, page)
    page_size = max(20, _listing_page_size())
    results = [None] * len(indexed_sources)
    more_flags = [False] * len(indexed_sources)
    workers = min(4, len(indexed_sources))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {
            pool.submit(_fetch_tmdb_trakt_all_source, source, page, page_size): pos
            for pos, (_idx, source) in enumerate(indexed_sources)
        }
        for future in as_completed(future_map):
            pos = future_map[future]
            try:
                results[pos], more_flags[pos] = future.result()
            except Exception:
                results[pos], more_flags[pos] = [], False
    rows = _round_robin_unique([result or [] for result in results], limit=page_size)
    more_url = ''
    if any(more_flags) and not _pagination_hidden():
        indexes = ','.join(str(idx) for idx, _source in indexed_sources)
        more_url = build_url(
            action='collection_nuvio_sources_open', set_id=set_id,
            group_id=group_id, folder_id=folder.get('id') or '',
            source_indexes=indexes, page=str(int(page or 1) + 1))
    return _render_tmdb_work_rows(
        folder, rows, source_name=folder.get('name') or '', more_url=more_url,
        meta_target=_nuvio_collection_meta_target(set_id, group_id, folder))


def _open_nuvio_sources_merged_fallback(folder, indexed_sources, set_id='', group_id='', page=1):
    """Exact ordered tabs for a source family Kodi cannot safely combine."""
    for idx, source in indexed_sources:
        name, suffix = _nuvio_source_label(source)
        add_item(name,
                 build_url(action='collection_nuvio_source_open', set_id=set_id,
                           group_id=group_id, folder_id=folder.get('id') or '',
                           source_index=str(idx), page=str(page)),
                 info={'title': name, 'plot': suffix, 'mediatype': 'video'},
                 art=_source_navigation_art(
                     source, folder, set_id,
                     background=folder.get('background') or addon_fanart()),
                 properties={'dexhub.tile_shape': 'square',
                             'dexhub.nuvio_source_tab': '1'})
    return end_dir(content='files', cache=True)


def _open_nuvio_sources_merged(folder, indexed_sources, set_id='', group_id='', page=1):
    """Open Nuvio's All tab without changing any available exact source."""
    indexed_sources = [(idx, src) for idx, src in (indexed_sources or []) if isinstance(src, dict)]
    if not indexed_sources:
        error(tr('لا توجد مصادر للدمج'))
        return end_dir()
    # Compatibility conversion is allowed only when the exact Nuvio add-on is
    # genuinely unavailable. An installed addonCatalog must remain that exact
    # addonId/catalogId source even when its ID resembles a TMDb shortcut.
    converted = []
    for idx, src in indexed_sources:
        if str(src.get('sourceKind') or '') == 'addonCatalog':
            try:
                exact = _collections_mod.find_nuvio_provider_by_addon_id(src.get('addonId') or '')
            except Exception:
                exact = None
            if exact:
                converted.append((idx, src))
            else:
                native = _native_aio_source(src)
                converted.append((idx, native or src))
        else:
            converted.append((idx, src))
    indexed_sources = converted
    kinds = set(str(src.get('sourceKind') or '').strip() for _idx, src in indexed_sources)
    if kinds == {'tmdb'}:
        return _render_nuvio_tmdb_sources_merged(folder, indexed_sources, set_id, group_id, page=page)
    if kinds == {'traktIdList'}:
        return _render_nuvio_trakt_sources_merged(folder, indexed_sources, set_id, group_id, page=page)
    if kinds == {'addonCatalog'}:
        return _render_nuvio_addon_all(folder, indexed_sources, set_id, group_id, page=page)
    if kinds and kinds.issubset({'tmdb', 'traktIdList'}):
        return _render_nuvio_tmdb_trakt_all(
            folder, indexed_sources, set_id, group_id, page=page)
    # Kodi cannot safely interleave arbitrary Stremio directories after they
    # render. Keep every exact source accessible in original order rather than
    # substituting a different provider or silently dropping a tab.
    return _open_nuvio_sources_merged_fallback(
        folder, indexed_sources, set_id, group_id, page)


def _render_tmdb_work_rows(folder, rows, source_name='', more_url='', meta_target=''):
    """Render canonical works as Posters with TMDb Helper enrichment."""
    rows = _plugin._nuvio_filter_unreleased(rows, treat_missing=True)
    found_series = False
    found_movie = False
    rendered = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        media_type = str(row.get('media_type') or 'movie').lower()
        media_type = 'series' if media_type in ('series', 'tv', 'show', 'anime') else 'movie'
        found_series |= media_type == 'series'
        found_movie |= media_type == 'movie'
        title = str(row.get('title') or '').strip()
        year = str(row.get('year') or '').strip()
        tmdb_id = str(row.get('tmdb_id') or '').strip()
        if not title or not tmdb_id:
            continue

        # The source row is already Nuvio/TMDb canonical data.  Keep it as
        # the seed and apply Nuvio's synced metadata/artwork toggles.  TMDb
        # Helper (or another metadata addon) is used only when the user picked
        # it explicitly for THIS collection.
        seed_meta = {
            'id': 'tmdb:%s' % tmdb_id,
            'name': title, 'title': title, 'year': year,
            'description': row.get('overview') or '', 'overview': row.get('overview') or '',
            'poster': row.get('poster') or '',
            'fanart': row.get('backdrop') or '', 'background': row.get('backdrop') or '',
            'landscape': row.get('backdrop') or '',
            'tmdb_id': tmdb_id, 'rating': row.get('rating'),
        }
        local_meta = _nuvio_apply_collection_meta(seed_meta, media_type, meta_target)
        title = str(local_meta.get('name') or local_meta.get('title') or title).strip() or title
        helper_year = local_meta.get('year')
        if helper_year not in (None, ''):
            year = str(helper_year)
        poster_url = local_meta.get('poster') or row.get('poster') or _default_work_poster(media_type)
        fanart_url = (local_meta.get('fanart') or local_meta.get('landscape') or
                      row.get('backdrop') or folder.get('background') or '')
        clearlogo_url = local_meta.get('clearlogo') or local_meta.get('logo') or ''
        art = {
            'poster': poster_url, 'thumb': poster_url, 'icon': poster_url,
            'fanart': fanart_url,
            'landscape': local_meta.get('landscape') or fanart_url,
            'banner': local_meta.get('banner') or fanart_url,
            'clearlogo': clearlogo_url,
        }
        if clearlogo_url:
            art['logo'] = clearlogo_url
            art['tvshow.clearlogo'] = clearlogo_url

        studio = local_meta.get('studio') or local_meta.get('studios') or ''
        seed = dict(art)
        if studio:
            seed['studio'] = studio
        try:
            merged_mem = dict(row)
            merged_mem.update(local_meta or {})
            if studio:
                merged_mem['studio'] = studio
            _plugin._meta_mem_put(media_type, 'tmdb:%s' % tmdb_id, merged_mem)
        except Exception:
            pass

        path, is_folder = _content_click_path(
            media_type=media_type, canonical_id='tmdb:%s' % tmdb_id,
            title=title, tmdb_id=tmdb_id, ui_seed=seed)

        info = {
            'title': title,
            'plot': local_meta.get('plot') or local_meta.get('overview') or row.get('overview') or '',
            'mediatype': 'tvshow' if media_type == 'series' else 'movie',
        }
        if year.isdigit():
            info['year'] = int(year)
        rating = local_meta.get('rating')
        if rating in (None, ''):
            rating = local_meta.get('imdbRating')
        if rating in (None, ''):
            rating = row.get('rating')
        try:
            if rating not in (None, '', 'N/A'):
                info['rating'] = float(rating)
        except Exception:
            pass
        if studio:
            info['studio'] = studio
        for src_key, dst_key in (
                ('genre', 'genre'), ('genres', 'genre'), ('mpaa', 'mpaa'),
                ('certification', 'mpaa'), ('country', 'country'),
                ('director', 'director'), ('directors', 'director'),
                ('writer', 'writer'), ('writers', 'writer'),
                ('premiered', 'premiered'), ('released', 'premiered'),
                ('tagline', 'tagline'), ('runtime', 'duration')):
            if dst_key not in info and local_meta.get(src_key) not in (None, '', [], {}):
                info[dst_key] = local_meta.get(src_key)

        # Keep the ListItem label for accessibility/search, while AF3 can use
        # the ClearLogo property as the visible title. The card itself is Poster.
        add_item(
            title, path, is_folder=is_folder, info=info, art=art,
            ids={'tmdb_id': tmdb_id},
            properties={
                'dexhub.tile_shape': 'poster',
                'dexhub.work_item': '1',
                'dexhub.clearlogo_as_title': '1',
                'dexhub.metadata_source': 'nuvio-collection',
                'dexhub.poster_item': '1',
                'dexhub.studio': ', '.join(studio) if isinstance(studio, (list, tuple)) else str(studio or ''),
                'dexhub.badge_line': ' • '.join([x for x in (year,
                    (('★ %.1f' % float(info.get('rating'))) if info.get('rating') not in (None, '') else ''),
                    (', '.join(studio) if isinstance(studio, (list, tuple)) else str(studio or ''))) if x]),
            },
            context_menu=([(tr('تغيير مزود البوستر والميتاداتا'), 'RunPlugin(%s)' % build_url(
                action='meta_pick_target', target_key=meta_target,
                title=tr('مزود البوستر والميتاداتا • %s') % (folder.get('name') or source_name or 'Nuvio Collection')))] if meta_target else []) +
                _build_source_picker_menu(media_type=media_type, canonical_id='tmdb:%s' % tmdb_id, title=title,
                                          source_provider_id=meta_target or 'virtual.collection.nuvio'))
        rendered += 1

    if more_url and rendered and not _pagination_hidden():
        add_item(tr('المزيد'), more_url, art=root_art('catalogs'), info={'title': tr('المزيد')})
    content = 'videos' if (found_series and found_movie) else ('tvshows' if found_series else 'movies')
    return end_dir(content=content, cache=True)


def _render_nuvio_tmdb_source(folder, source, set_id='', group_id='', source_index=0, page=1, overrides=None):
    from . import tmdb_direct as _tmdb
    if overrides and str(source.get('tmdbSourceType') or 'DISCOVER').upper() == 'DISCOVER':
        source = dict(source, dexOverrides=dict(overrides))
    name = str(source.get('name') or folder.get('name') or 'TMDb')
    if not _tmdb._api_key():
        add_item(tr('[COLOR yellow]أضف مفتاح TMDb من الإعدادات لفتح هذا المصدر[/COLOR]'),
                 build_url(action='open_settings'), is_folder=False, art=root_art('tmdb'),
                 info={'title': name,
                       'plot': tr('TMDb يجلب قائمة Kaptain المخصصة، وTMDb Helper يثري الأعمال والصور محليًا عند توفرها.')})
        return end_dir(content='files', cache=False)
    page_size = _listing_page_size()
    rows = _tmdb.nuvio_source_items(source, page=page, limit=page_size)
    if not rows:
        if source.get('dexOverrides'):
            # a choice made on the Home's grid that matches nothing: an empty
            # page, so the grid says so and offers to change the choice
            return end_dir(content='files', cache=False)
        add_item(tr('[COLOR yellow]لا توجد عناصر الآن[/COLOR]'), build_url(action='collection_set_browse', set_id=set_id),
                 is_folder=False, art=root_art('catalogs'), info={'title': name})
        return end_dir(content='files', cache=False)
    # TMDb API pages are up to 20 results even when Dex Hub's display page is
    # larger. Use that as the pagination signal instead of page_size.
    has_more = (len(rows) >= 20 and
                str(source.get('tmdbSourceType') or 'DISCOVER').upper() != 'COLLECTION')
    more_url = ''
    if has_more:
        more_args = dict(action='collection_nuvio_source_open', set_id=set_id, group_id=group_id,
                         folder_id=folder.get('id') or '', source_index=str(source_index), page=str(page + 1))
        if source.get('dexOverrides'):
            more_args['tmdb'] = json.dumps(source['dexOverrides'], sort_keys=True)
        more_url = build_url(**more_args)
    return _render_tmdb_work_rows(
        folder, rows, source_name=name, more_url=more_url,
        meta_target=_nuvio_collection_meta_target(set_id, group_id, folder))


def _round_robin_unique(row_lists, limit=40):
    out = []
    seen = set()
    row_lists = [list(rows or []) for rows in row_lists]
    max_len = max([len(rows) for rows in row_lists] or [0])
    for pos in range(max_len):
        for rows in row_lists:
            if pos >= len(rows):
                continue
            row = rows[pos]
            key = (str(row.get('media_type') or ''), str(row.get('tmdb_id') or ''))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            out.append(row)
            if len(out) >= max(1, int(limit or 40)):
                return out
    return out


def _render_nuvio_tmdb_sources_merged(folder, indexed_sources, set_id='', group_id='', page=1):
    from . import tmdb_direct as _tmdb
    if not _tmdb._api_key():
        return _render_nuvio_tmdb_source(folder, indexed_sources[0][1], set_id, group_id,
                                         indexed_sources[0][0], page)
    page_size = max(20, _listing_page_size())
    sources = [src for _idx, src in indexed_sources]
    results = [None] * len(sources)

    # Parallel fetch keeps a 4-variant genre folder close to a single request's
    # perceived latency. TMDb Direct still applies its own disk/memory cache.
    workers = min(4, len(sources))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {
            pool.submit(_tmdb.nuvio_source_items, src, page, 20): i
            for i, src in enumerate(sources)
        }
        for fut in as_completed(future_map):
            i = future_map[fut]
            try:
                results[i] = fut.result() or []
            except Exception:
                results[i] = []

    rows = _round_robin_unique(results, limit=page_size)
    if not rows:
        add_item(tr('[COLOR yellow]لا توجد عناصر الآن[/COLOR]'), build_url(action='collection_set_browse', set_id=set_id),
                 is_folder=False, art=root_art('catalogs'), info={'title': folder.get('name') or 'Collection'})
        return end_dir(content='files', cache=False)

    non_collection = [src for src in sources if str(src.get('tmdbSourceType') or 'DISCOVER').upper() != 'COLLECTION']
    has_more = bool(non_collection) and any(len(result or []) >= 20 for result in results)
    more_url = ''
    if has_more:
        indexes = ','.join(str(idx) for idx, _src in indexed_sources)
        more_url = build_url(action='collection_nuvio_sources_open', set_id=set_id, group_id=group_id,
                             folder_id=folder.get('id') or '', source_indexes=indexes, page=str(page + 1))
    return _render_tmdb_work_rows(
        folder, rows, source_name=folder.get('name') or '', more_url=more_url,
        meta_target=_nuvio_collection_meta_target(set_id, group_id, folder))


def _prefetch_trakt_posters(mapped_rows):
    """Seed already-cached TMDb artwork onto Trakt/Nuvio work rows.

    Platform folders still render works as posters, but opening one must not
    fan out into a TMDb detail request for every Trakt row. One batch cache
    read fills warm artwork; native row art/MetaHub/defaults cover cold rows.
    """
    from . import tmdb_direct as _tmdb
    if not _tmdb._api_key():
        return
    wanted = []
    seen = set()
    for row in mapped_rows or []:
        ids = row.get('ids') or {}
        tmdb_id = str(ids.get('tmdb') or '').strip()
        if not tmdb_id.isdigit():
            continue
        mt = 'tv' if str(row.get('_type') or '') == 'series' else 'movie'
        key = (tmdb_id, mt)
        if key not in seen:
            seen.add(key)
            wanted.append(key)
    if not wanted:
        return
    requests = [
        {'tmdb_id': tid, 'media_type': mt}
        for tid, mt in wanted
    ]
    try:
        cached = _tmdb.metas_for_cached_only(requests)
    except Exception:
        cached = []
    bundles = {
        key: (cached[idx] if idx < len(cached) else {})
        for idx, key in enumerate(wanted)
    }
    for row in mapped_rows or []:
        ids = row.get('ids') or {}
        tmdb_id = str(ids.get('tmdb') or '').strip()
        mt = 'tv' if str(row.get('_type') or '') == 'series' else 'movie'
        art = bundles.get((tmdb_id, mt)) or {}
        if not art:
            continue
        row['_tmdb_art'] = dict(art)
        row['_tmdb_meta'] = dict(art)
        # Canonical work art wins over any inherited/provider card.
        if art.get('poster'):
            row['poster'] = art.get('poster')
        if art.get('fanart'):
            row['backdrop'] = art.get('fanart')
        if art.get('clearlogo'):
            row['clearlogo'] = art.get('clearlogo')


def _render_nuvio_idlist_work_rows(folder, rows, more_url='', title='', meta_target=''):
    """Render Trakt/Nuvio leaf rows as real Movie/TV poster items.

    This intentionally does not call the generic virtual-list renderer.  A
    platform/category card is navigation artwork; it must never become the
    poster for a work.  TMDb Helper local metadata is preferred, then the
    canonical TMDb art bundle seeded above, then the row's own poster.
    """
    rows = _plugin._nuvio_filter_unreleased(rows, treat_missing=True)
    found_series = False
    found_movie = False
    rendered = 0
    folder_bg = _wrap_tokenized_url((folder or {}).get('background') or '')
    if folder_bg:
        try:
            xbmcgui.Window(WINDOW_ID).setProperty('fanart', folder_bg)
        except Exception:
            pass

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        media_type = 'series' if str(row.get('_type') or '').lower() in ('series','show','tv','anime') else 'movie'
        found_series |= media_type == 'series'
        found_movie |= media_type == 'movie'
        ids = dict(row.get('ids') or {}) if isinstance(row.get('ids'), dict) else {}
        tmdb_id = str(ids.get('tmdb') or '').strip()
        imdb_id = str(ids.get('imdb') or '').strip()
        tvdb_id = str(ids.get('tvdb') or '').strip()
        title_text = str(row.get('title') or '').strip()
        year = str(row.get('year') or row.get('release_year') or '').strip()
        if not title_text:
            continue

        canonical = ''
        if tmdb_id:
            canonical = 'tmdb:%s' % tmdb_id
        elif imdb_id:
            canonical = imdb_id if imdb_id.startswith('tt') else 'tt%s' % imdb_id
        elif tvdb_id:
            canonical = 'tvdb:%s' % tvdb_id
        else:
            continue

        tmdb_meta = dict(row.get('_tmdb_meta') or row.get('_tmdb_art') or {})
        base_meta = {
            'id': canonical, 'name': title_text, 'title': title_text, 'year': year,
            'description': row.get('overview') or row.get('description') or '',
            'overview': row.get('overview') or row.get('description') or '',
            'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'tvdb_id': tvdb_id,
        }
        try:
            local_meta = _plugin._nuvio_merge_tmdb_meta(
                base_meta, tmdb_meta, _plugin._nuvio_metadata_preferences())
        except Exception:
            local_meta = dict(base_meta)
            local_meta.update(tmdb_meta)
        # Per-collection picker is the only override above Nuvio's profile.
        try:
            from . import meta_source as _ms
            if meta_target and _ms.get_explicit_meta_source_for(meta_target):
                local_meta = _ms.override_virtual_meta(
                    meta_target, media_type, local_meta, cache_only=True)
        except Exception:
            pass
        title_text = str(local_meta.get('name') or local_meta.get('title') or title_text).strip() or title_text
        art_bundle = dict(row.get('_tmdb_art') or tmdb_meta or {})
        # A cold cache intentionally remains local here. Item details can warm
        # one work later; a collection render never starts per-card HTTP.

        poster = (local_meta.get('poster') or row.get('poster') or art_bundle.get('poster') or '')
        # Last resort may use MetaHub by IMDb, but NEVER the platform/folder cover.
        if not poster and imdb_id:
            try:
                poster = _plugin._METAHUB_POSTER % (imdb_id if imdb_id.startswith('tt') else 'tt%s' % imdb_id)
            except Exception:
                poster = ''
        poster = poster or _default_work_poster(media_type)
        fanart = (local_meta.get('fanart') or local_meta.get('landscape') or
                  row.get('backdrop') or art_bundle.get('fanart') or art_bundle.get('landscape') or folder_bg)
        clearlogo = (local_meta.get('clearlogo') or local_meta.get('logo') or
                     row.get('clearlogo') or art_bundle.get('clearlogo') or '')
        art = {
            'poster': poster, 'thumb': poster, 'icon': poster,
            'fanart': fanart,
            'landscape': local_meta.get('landscape') or art_bundle.get('landscape') or fanart,
            'banner': local_meta.get('banner') or fanart,
            'clearlogo': clearlogo,
        }
        if clearlogo:
            art['logo'] = clearlogo
            art['tvshow.clearlogo'] = clearlogo

        info = {
            'title': title_text,
            'plot': local_meta.get('plot') or local_meta.get('overview') or row.get('overview') or row.get('description') or '',
            'mediatype': 'tvshow' if media_type == 'series' else 'movie',
        }
        helper_year = local_meta.get('year')
        if helper_year not in (None, ''):
            year = str(helper_year)
        if year.isdigit():
            info['year'] = int(year)
        rating = local_meta.get('rating') or local_meta.get('imdbRating') or row.get('rating')
        try:
            if rating not in (None, '', 'N/A'):
                info['rating'] = float(rating)
        except Exception:
            pass
        studio = local_meta.get('studio') or local_meta.get('studios') or row.get('studio') or ''
        if studio:
            info['studio'] = studio
        for src_key, dst_key in (
                ('genre', 'genre'), ('genres', 'genre'), ('mpaa', 'mpaa'),
                ('certification', 'mpaa'), ('country', 'country'),
                ('director', 'director'), ('directors', 'director'),
                ('writer', 'writer'), ('writers', 'writer'),
                ('premiered', 'premiered'), ('released', 'premiered'),
                ('tagline', 'tagline'), ('runtime', 'duration')):
            value = local_meta.get(src_key)
            if dst_key not in info and value not in (None, '', [], {}):
                info[dst_key] = value

        seed = dict(art)
        if studio:
            seed['studio'] = studio
        try:
            merged_mem = dict(row)
            merged_mem.update(local_meta or {})
            merged_mem.update({'poster': poster, 'background': fanart, 'fanart': fanart, 'clearlogo': clearlogo})
            if studio:
                merged_mem['studio'] = studio
            _plugin._meta_mem_put(media_type, canonical, merged_mem, source_provider_id='virtual.collection.nuvio')
        except Exception:
            pass

        path, is_folder = _content_click_path(
            media_type=media_type, canonical_id=canonical, title=title_text,
            tmdb_id=tmdb_id, imdb_id=imdb_id if imdb_id.startswith('tt') else ('tt%s' % imdb_id if imdb_id else ''),
            tvdb_id=tvdb_id, source_provider_id='virtual.collection.nuvio', ui_seed=seed)
        props = {
            'dexhub.tile_shape': 'poster',
            'dexhub.work_item': '1',
            'dexhub.clearlogo_as_title': '1',
            'dexhub.metadata_source': 'nuvio-collection',
            'dexhub.poster_item': '1',
            'dexhub.studio': ', '.join(studio) if isinstance(studio, (list, tuple)) else str(studio or ''),
        }
        badge_parts = [year]
        if info.get('rating') not in (None, ''):
            try:
                badge_parts.append('★ %.1f' % float(info.get('rating')))
            except Exception:
                pass
        if studio:
            badge_parts.append(', '.join(studio) if isinstance(studio, (list, tuple)) else str(studio))
        props['dexhub.badge_line'] = ' • '.join(x for x in badge_parts if x)
        add_item(
            title_text, path, is_folder=is_folder, info=info, art=art,
            ids={'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'tvdb_id': tvdb_id},
            properties=props,
            context_menu=([(tr('تغيير مزود البوستر والميتاداتا'), 'RunPlugin(%s)' % build_url(
                action='meta_pick_target', target_key=meta_target,
                title=tr('مزود البوستر والميتاداتا • %s') % (folder.get('name') or title or 'Nuvio Collection')))] if meta_target else []) +
                _build_source_picker_menu(media_type=media_type, canonical_id=canonical, title=title_text,
                                          source_provider_id=meta_target or 'virtual.collection.nuvio'))
        rendered += 1

    if more_url and rendered and not _pagination_hidden():
        add_item(tr('المزيد'), more_url, art=root_art('catalogs'), info={'title': tr('المزيد')})
    content = 'videos' if (found_series and found_movie) else ('tvshows' if found_series else 'movies')
    return end_dir(content=content, cache=True)

def _render_nuvio_trakt_sources_merged(folder, indexed_sources, set_id='', group_id='', page=1):
    page_size = min(100, max(20, _listing_page_size()))
    source_rows = []
    has_more = False
    for _idx, source in indexed_sources:
        mt = str(source.get('mediaType') or '').lower()
        list_type = 'shows' if mt in ('series', 'tv', 'show', 'anime') else ('movies' if mt == 'movie' else 'movies,shows')
        from . import trakt
        raw_rows = trakt.fetch_list_by_id(source.get('traktListId') or '', page=page, limit=page_size, list_type=list_type) or []
        has_more |= len(raw_rows) >= page_size
        mapped = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            obj = raw.get('show') or raw.get('movie') or {}
            is_show = bool(raw.get('show')) or str(raw.get('type') or '').lower() == 'show'
            if not isinstance(obj, dict):
                continue
            mapped.append({
                '_type': 'series' if is_show else 'movie',
                'title': obj.get('title') or '',
                'year': obj.get('year') or '',
                'released': obj.get('released') or obj.get('first_aired') or '',
                'overview': obj.get('overview') or '',
                'ids': obj.get('ids') or {},
            })
        source_rows.append(mapped)

    # Match Nuvio's All tab: one result from each source in source order,
    # repeated round-robin, deduplicated by the work identity.
    merged = []
    seen = set()
    max_len = max([len(rows) for rows in source_rows] or [0])
    for pos in range(max_len):
        for rows in source_rows:
            if pos >= len(rows):
                continue
            row = rows[pos]
            ids = row.get('ids') or {}
            identity = str(ids.get('tmdb') or ids.get('imdb') or ids.get('trakt') or '')
            key = (str(row.get('_type') or ''), identity or '%s:%s' % (
                row.get('title') or '', row.get('year') or ''))
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= page_size:
                break
        if len(merged) >= page_size:
            break
    more_url = ''
    if has_more and not _pagination_hidden():
        indexes = ','.join(str(idx) for idx, _src in indexed_sources)
        more_url = build_url(action='collection_nuvio_sources_open', set_id=set_id, group_id=group_id,
                             folder_id=folder.get('id') or '', source_indexes=indexes, page=str(page + 1))
    _prefetch_trakt_posters(merged)
    return _render_nuvio_idlist_work_rows(
        folder, merged, more_url=more_url, title=folder.get('name') or 'Trakt',
        meta_target=_nuvio_collection_meta_target(set_id, group_id, folder))


def _render_nuvio_trakt_source(folder, source, set_id='', group_id='', source_index=0, page=1):
    name = str(source.get('name') or folder.get('name') or 'Trakt')
    mt = str(source.get('mediaType') or '').lower()
    list_type = 'shows' if mt in ('series','tv','show','anime') else ('movies' if mt == 'movie' else 'movies,shows')
    page_size = min(100, max(20, _listing_page_size()))
    from . import trakt
    raw_rows = trakt.fetch_list_by_id(source.get('traktListId') or '', page=page, limit=page_size, list_type=list_type)
    mapped = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        obj = row.get('show') or row.get('movie') or {}
        is_show = bool(row.get('show')) or str(row.get('type') or '').lower() == 'show'
        if not isinstance(obj, dict):
            continue
        mapped.append({'_type': 'series' if is_show else 'movie',
                       'title': obj.get('title') or '', 'year': obj.get('year') or '',
                       'released': obj.get('released') or obj.get('first_aired') or '',
                       'overview': obj.get('overview') or '', 'ids': obj.get('ids') or {}})
    more_url = ''
    if len(raw_rows) >= page_size and not _pagination_hidden():
        more_url = build_url(action='collection_nuvio_source_open', set_id=set_id, group_id=group_id,
                             folder_id=folder.get('id') or '', source_index=str(source_index), page=str(page + 1))
    _prefetch_trakt_posters(mapped)
    return _render_nuvio_idlist_work_rows(
        folder, mapped, more_url=more_url, title=name,
        meta_target=_nuvio_collection_meta_target(set_id, group_id, folder))
