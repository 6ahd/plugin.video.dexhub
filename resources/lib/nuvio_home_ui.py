# -*- coding: utf-8 -*-
"""Kodi adapter for the canonical Nuvio Home snapshot.

This module is loaded lazily by ``plugin.py``. It resolves snapshot identities
to local transports but never invents rows from Dex Hub providers, never sorts
them locally and never borrows a child work poster for a Collection card.
"""
import json

import xbmc
import xbmcgui

from . import collection_sets as _collections
from . import betterposters_home as _bp_home
from . import plugin as _plugin
from .dexhub import nuvio_contract as _contract
from .dexhub import nuvio_home_mirror as _mirror
from .dexhub import nuvio_profile_prefs as _profile_prefs

WINDOW_ID = _plugin.WINDOW_ID
add_item = _plugin.add_item
addon_fanart = _plugin.addon_fanart
build_url = _plugin.build_url
catalog_art = _plugin.catalog_art
media_path = _plugin.media_path
collection_banner_art = _plugin.collection_banner_art
end_dir = _plugin.end_dir
root_art = _plugin.root_art
tr = _plugin.tr
_wrap_tokenized_url = _plugin._wrap_tokenized_url


def _catalog_label(item, catalog=None, prefs=None):
    """Use Nuvio's visible Home title, including its synced type suffix."""
    base = str((item or {}).get('custom_title') or
               (catalog or {}).get('name') or
               (item or {}).get('catalog_id') or 'Catalog')
    return _profile_prefs.format_catalog_row_title(
        base,
        (item or {}).get('type') or 'movie',
        prefs=prefs,
        type_labels={'movie': tr('فيلم'), 'series': tr('مسلسل')},
    )


def _collection_variants(collection_id):
    return _contract.collection_id_variants(collection_id)


def _cloud_entry(collection_id):
    cloud = _collections.get_set(_collections.NUVIO_CLOUD_ID) or {}
    variants = _collection_variants(collection_id)
    for entry in cloud.get('entries') or []:
        if str((entry or {}).get('id') or '').strip() in variants:
            return cloud, entry

    # Upgrade an older normalized cache in memory from the preserved native
    # object. The source object remains authoritative; this is not a Dex
    # reconstruction and the next sync persists the current normalizer.
    for raw in cloud.get('nuvio_raw') or []:
        if str((raw or {}).get('id') or '').strip() not in variants:
            continue
        try:
            parsed = _collections.parse_collection_json(
                json.dumps([raw], ensure_ascii=False))
            if parsed:
                return cloud, parsed[0]
        except Exception:
            break
    return cloud, None


def _catalog_match(item):
    addon_id = str((item or {}).get('addon_id') or '').strip()
    catalog_id = str((item or {}).get('catalog_id') or '').strip()
    media_type = _contract.catalog_type((item or {}).get('type') or '')
    if not (addon_id and catalog_id and media_type):
        return None, None
    provider = _collections.find_nuvio_provider_by_addon_id(addon_id)
    if not provider:
        return None, None
    catalogs = _plugin._provider_catalogs(provider)
    return provider, _contract.match_catalog(catalogs, catalog_id, media_type)


def _account_provider_specs(snapshot):
    """Resolve only Nuvio's enabled account transports, in account order."""
    specs = []
    for addon in (snapshot or {}).get('addons') or []:
        if not isinstance(addon, dict):
            continue
        manifest_url = str(addon.get('manifest_url') or '').strip()
        try:
            provider = _collections._provider_for_manifest(manifest_url)
        except Exception:
            provider = None
        if not provider:
            continue
        manifest = provider.get('manifest') or {}
        addon_id = str(manifest.get('id') or '').strip()
        if not addon_id:
            continue
        catalogs = [dict(row) for row in (manifest.get('catalogs') or [])
                    if isinstance(row, dict)]
        def _manifest_order(row):
            try:
                return int((row or {}).get('order', 0) or 0)
            except Exception:
                return 0
        catalogs.sort(key=_manifest_order)
        specs.append({
            'addon_id': addon_id,
            # The account snapshot already says this add-on is enabled.
            # Dex's separate provider/catalog switches must not silently hide
            # rows from the Nuvio projection; showInHome is evaluated by the
            # compatibility contract below.
            'catalogs': catalogs,
            '_provider': provider,
        })

    # Compatibility for a pre-schema-2 mirror.  Do not fall back when the
    # authoritative account explicitly has zero enabled add-ons.
    if not specs and not (snapshot or {}).get('addons') and (snapshot or {}).get('items'):
        seen = set()
        for item in (snapshot or {}).get('items') or []:
            addon_id = str((item or {}).get('addon_id') or '').strip()
            if not addon_id or addon_id in seen:
                continue
            seen.add(addon_id)
            provider = _collections.find_nuvio_provider_by_addon_id(addon_id)
            if provider:
                specs.append({
                    'addon_id': addon_id,
                    'catalogs': list(_plugin._provider_catalogs(provider) or []),
                    '_provider': provider,
                })
    return specs


def _catalog_index(specs):
    out = {}
    for spec in specs or []:
        provider = (spec or {}).get('_provider')
        addon_id = str((spec or {}).get('addon_id') or '').strip()
        for catalog in (spec or {}).get('catalogs') or []:
            key = _contract.catalog_key(
                addon_id, (catalog or {}).get('type'), (catalog or {}).get('id'))
            if key and key not in out:
                out[key] = (provider, catalog)
    return out


def _collection_tab(item, media_type, summaries=None):
    cloud, entry = _cloud_entry(item.get('collection_id') or '')
    summary = (summaries or {}).get(str(item.get('collection_id') or ''))
    if summary is None:
        summary = _mirror.collection_summary(item.get('collection_id') or '')
    # Nuvio renders the Collection object's own title. custom_title belongs
    # to catalog rows; applying it to a Collection produced labels Nuvio never
    # shows and was one source of the user's "strange names" report.
    label = (_contract.collection_title(entry, summary) or
             str(item.get('collection_id') or 'Collection'))
    if not entry:
        return None

    fanart = _wrap_tokenized_url(
        entry.get('background') or summary.get('backdrop') or '')
    card = _wrap_tokenized_url(entry.get('poster') or summary.get('cover') or '')
    clearlogo = _wrap_tokenized_url(entry.get('clearlogo') or '')
    if card:
        art = {
            'icon': card, 'thumb': card, 'poster': card,
            'landscape': card, 'banner': card,
            'fanart': fanart or addon_fanart(),
        }
    else:
        # A Nuvio Collection has a backdrop, not a child-derived cover. Keep
        # that backdrop as focus art and use a neutral local navigation card.
        art = collection_banner_art(label, fanart=fanart or addon_fanart())
    if clearlogo:
        art['clearlogo'] = clearlogo
        art['logo'] = clearlogo
        art['tvshow.clearlogo'] = clearlogo
    set_id = (cloud or {}).get('id') or _collections.NUVIO_CLOUD_ID
    path = build_url(action='collection_entry_open', set_id=set_id,
                     entry_id=entry.get('id') or '', media_filter=media_type)
    return {
        'kind': 'collection',
        'key': str(item.get('key') or _contract.collection_key(
            item.get('collection_id'))),
        'label': label,
        'native_label': ('' if (_plugin._clean_collection_view_enabled() and
                                entry.get('hide_title')) else label),
        'media_type': media_type,
        'path': path,
        'is_folder': True,
        'art': art,
        'fanart': fanart,
        'entry': entry,
        'set_id': set_id,
        'item': dict(item),
        'info': {'title': label, 'plot': '', 'mediatype': 'video'},
        'context_menu': [
            (tr('تغيير مزود البوستر والميتاداتا'), 'RunPlugin(%s)' % build_url(
                action='collection_entry_meta_source',
                set_id=set_id,
                entry_id=entry.get('id') or '')),
        ],
        'properties': {
            'dexhub.tile_shape': 'landscape',
            'dexhub.collection_root': '1',
            'dexhub.collection_media': media_type,
            'dexhub.nuvio_home_row': '1',
            'dexhub.nuvio_order': str(item.get('order') or 0),
            'dexhub.clearlogo_as_title': '1',
        },
    }


def _catalog_tab(item, media_type, catalog_index=None):
    provider, catalog = (catalog_index or {}).get(
        str(item.get('key') or ''), (None, None))
    if not provider or not catalog:
        provider, catalog = _catalog_match(item)
    prefs = _profile_prefs.normalized_tmdb()
    label = _catalog_label(item, catalog, prefs=prefs)
    if not provider or not catalog:
        # Nuvio resolves Home rows only from an installed manifest descriptor
        # with the exact addonId/type/catalogId triple. Do not fabricate a Dex
        # catalog or reuse a same-id catalog from the opposite media type.
        return None
    catalog_type = _contract.catalog_type(item.get('type')) or media_type
    manifest = provider.get('manifest') or {}
    art = catalog_art(label, catalog_type, manifest, catalog)
    provider_name = str(provider.get('name') or manifest.get('name') or '').strip()
    provider_identity_art = _plugin.provider_art(
        provider_name, manifest,
        provider.get('base_url') or provider.get('manifest_url') or '')
    provider_icon = (provider_identity_art.get('icon') or
                     provider_identity_art.get('thumb') or '')
    from_addon = (tr('من %s') % provider_name
                  if prefs.get('catalog_addon_name_enabled', True) and provider_name
                  else '')
    return {
        'kind': 'catalog',
        'key': str(item.get('key') or _contract.catalog_key(
            item.get('addon_id'), item.get('type'), item.get('catalog_id'))),
        'label': label,
        'native_label': label,
        'media_type': catalog_type,
        'path': build_url(
            action='catalog_all', provider_id=provider.get('id') or '',
            media_type=catalog_type, catalog_id=catalog.get('id') or '',
            label=label, nuvio='1'),
        'is_folder': True,
        'art': art,
        'fanart': art.get('fanart') or '',
        'provider': provider,
        'catalog': catalog,
        'provider_name': provider_name,
        'provider_icon': provider_icon,
        'item': dict(item),
        'info': {'title': label, 'tagline': from_addon,
                 'plot': catalog.get('description') or '', 'mediatype': 'video'},
        'context_menu': [
            (tr('تغيير مزود البوستر والميتاداتا'), 'RunPlugin(%s)' % build_url(
                action='meta_pick_for_provider', provider_id=provider.get('id') or '')),
        ],
        'properties': {
            'dexhub.tile_shape': 'landscape',
            'dexhub.nuvio_home_row': '1',
            'dexhub.nuvio_home_media': catalog_type,
            'dexhub.nuvio_addon_id': item.get('addon_id') or '',
            'dexhub.nuvio_catalog_id': item.get('catalog_id') or '',
            'dexhub.nuvio_addon_name': from_addon,
            'dexhub.nuvio_order': str(item.get('order') or 0),
            'provider_icon': provider_icon,
            'dexhub.provider_icon': provider_icon,
            'dexhub.provider_name': provider_name,
        },
    }


def _betterposters_art(key):
    path = media_path('%s.jpg' % str(key or ''))
    return {
        'icon': path, 'thumb': path, 'poster': path,
        'landscape': path, 'banner': path, 'fanart': path,
    }


def _media_model(media_type, snapshot=None):
    snapshot = snapshot or _mirror.load()
    specs = _account_provider_specs(snapshot)
    prefs = _profile_prefs.normalized_tmdb()
    completed = _contract.complete_home_items(
        snapshot.get('items') or [], snapshot.get('collections') or [], specs,
        follow_addons_order=prefs.get('follow_addons_order', False))
    items = _contract.project_home_rows(
        completed, snapshot.get('collections') or [], media_type)
    index = _catalog_index(specs)
    summaries = {}
    for summary in snapshot.get('collections') or []:
        if not isinstance(summary, dict):
            continue
        for variant in _contract.collection_id_variants(summary.get('id')):
            summaries.setdefault(str(variant), summary)
    # Dex Hub-owned BetterPosters discovery rows are intentionally stable and
    # independent from the user's Nuvio snapshot. They are prepended, then all
    # Nuvio rows continue in the user's exact saved order.
    tabs = _bp_home.tabs(media_type, _plugin.ADDON, build_url, _betterposters_art)
    for item in items:
        tab = (_collection_tab(item, media_type, summaries=summaries)
               if item.get('is_collection')
               else _catalog_tab(item, media_type, catalog_index=index))
        if tab:
            tabs.append(tab)
    return snapshot, items, tabs



def _add_tab(tab):
    add_item(
        tab.get('native_label') or tab.get('label') or '',
        tab.get('path') or '', is_folder=bool(tab.get('is_folder', True)),
        info=tab.get('info') or {}, art=tab.get('art') or {},
        context_menu=tab.get('context_menu') or [],
        properties=tab.get('properties') or {})
    return True, tab.get('fanart') or ''


def render_media(media='movie', immersive=False):
    """Render Nuvio Movies/TV inside the active Kodi skin.

    ``immersive`` is retained only for compatibility with 5.7.0 URLs.  The
    active skin must own the media window; otherwise Arctic Fuse 3 cannot apply
    its configured browse layout, header, focus animations or view settings.
    """
    media_type = _mirror.normalize_media_type(media)
    if media_type not in ('movie', 'series'):
        media_type = 'movie'

    try:
        from .dexhub import nuvio_stremio_sync as _sync
        linked = _sync.Nuvio.is_linked()
        if linked and _mirror.is_stale(600):
            _plugin._submit_optional(
                _sync.pull_nuvio_home_to_local,
                key='nuvio-home-snapshot-refresh')
    except Exception:
        linked = False

    # Collections are untyped Home rows in Nuvio. Keep every one in both the
    # Movies and TV splits; filtering them by child sources changes Home. The
    # completion pass also appends manifest catalogs not yet present in the
    # saved order, matching Nuvio's own HomeViewModel rebuild.
    snapshot, items, tabs = _media_model(media_type)
    first_fanart = ''
    rendered = 0
    for tab in tabs:
        ok, fanart = _add_tab(tab)
        if ok:
            rendered += 1
        if fanart and not first_fanart:
            first_fanart = fanart

    if first_fanart:
        try:
            xbmcgui.Window(WINDOW_ID).setProperty('fanart', first_fanart)
        except Exception:
            pass

    if not rendered:
        if linked:
            label = (tr('Nuvio Home فارغ')
                     if not items and snapshot.get('complete')
                     else tr('تحديث Nuvio Home'))
            add_item(
                label, build_url(action='nuvio_pull_collections'), is_folder=False,
                art=root_art('refresh'),
                info={'title': label,
                      'plot': (tr('لا توجد صفوف مفعلة لهذا النوع في Nuvio Home.')
                               if not items else
                               tr('تعذر حل صفوف Nuvio من الإضافات والكوليكشنات الحالية. اضغط للمزامنة.'))})
        else:
            add_item(
                tr('ربط حساب Nuvio'), build_url(action='nuvio_connect'), is_folder=False,
                art=root_art('settings'),
                info={'title': tr('ربط حساب Nuvio'),
                      'plot': tr('اربط Nuvio لعرض نفس الهوم والكتالوجات والترتيب.')})
    # Keep the original generic-video content contract: it is the exact route
    # Arctic Fuse 3 previously styled correctly for these catalog folders.
    # Catalog result pages still declare movies/tvshows themselves.
    return end_dir(content='videos', cache=False)
