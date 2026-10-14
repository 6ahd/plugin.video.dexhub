# -*- coding: utf-8 -*-
"""Canonical, cache-first snapshot of Nuvio Home.

Nuvio stores Home presentation separately from the add-on and Collection
tables. The ordered ``items`` array owns visibility/custom catalog titles and
normal interleaving, while the Collection table owns ``pinToTop`` and native
Collection presentation. This module intentionally has no Kodi/UI imports:
sync writes one validated snapshot and every Dex Hub view reads that model.
"""
from .. import profile_scope as _scope
import os
import time

from .common import profile_path
from . import nuvio_contract as _contract
from .safe_io import read_json, write_json

MIRROR_PATH = _scope.ScopedPath('nuvio_home_mirror.json')
SCHEMA = 4


def _to_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


@_scope.bound
def normalize_media_type(value):
    return _contract.normalize_media_type(value)


@_scope.bound
def home_item_key(row):
    """Return the stable key used by Nuvio's Home settings datastore."""
    row = row or {}
    if row.get('is_collection'):
        cid = str(row.get('collection_id') or '').strip()
        return 'collection_%s' % cid if cid else ''
    addon_id = str(row.get('addon_id') or '').strip()
    # Stable Home identity uses Nuvio's exact catalog.apiType.  Projection to
    # Movies/TV happens later; normalizing ``tv``/``anime`` to ``series`` here
    # creates a second key when live manifest catalogs are appended.
    media_type = _contract.catalog_type(row.get('type') or '')
    catalog_id = str(row.get('catalog_id') or '').strip()
    if not (addon_id and media_type and catalog_id):
        return ''
    return _contract.catalog_key(addon_id, media_type, catalog_id)


@_scope.bound
def normalize_item(row, index=0):
    if not isinstance(row, dict):
        return None
    collection_id = str(row.get('collection_id') or row.get('collectionId') or '').strip()
    addon_id = str(row.get('addon_id') or row.get('addonId') or '').strip()
    catalog_id = str(row.get('catalog_id') or row.get('catalogId') or '').strip()
    raw_type = str(row.get('type') or row.get('media_type') or row.get('mediaType') or '').strip().lower()

    explicit_flag = row.get('is_collection', row.get('isCollection', None))
    is_collection = bool(explicit_flag) or bool(collection_id) or raw_type in ('collection', 'collections')
    if not collection_id and is_collection:
        collection_id = catalog_id
        for prefix in ('collection:', 'collection::', 'collection_'):
            if collection_id.lower().startswith(prefix):
                collection_id = collection_id[len(prefix):].strip()
                break

    # Preserve Nuvio's exact catalog API type in the stable identity.  The
    # Movies/TV projection buckets it later without rewriting the source.
    media_type = _contract.catalog_type(raw_type)
    if is_collection:
        if not collection_id:
            return None
        addon_id = ''
        catalog_id = ''
        media_type = ''
    elif not (addon_id and catalog_id and media_type):
        return None

    item = {
        'addon_id': addon_id,
        'type': media_type,
        'catalog_id': catalog_id,
        'enabled': bool(row.get('enabled', True)),
        'order': _to_int(row.get('order'), index),
        'custom_title': str(row.get('custom_title') or row.get('customTitle') or '').strip(),
        'is_collection': is_collection,
        'collection_id': collection_id,
        '_index': int(index),
    }
    item['key'] = home_item_key(item)
    return item


def _normalize_addons(rows):
    out = []
    for idx, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict) or row.get('enabled') is False:
            continue
        url = str(row.get('manifest_url') or row.get('manifestUrl') or
                  row.get('transportUrl') or row.get('url') or '').strip()
        if not url.lower().startswith(('http://', 'https://')):
            continue
        out.append({
            'manifest_url': url,
            'name': str(row.get('name') or '').strip(),
            'sort_order': _to_int(row.get('sort_order', row.get('sortOrder')), idx),
        })
    out.sort(key=lambda row: int(row.get('sort_order') or 0))
    return out


def _normalize_collections(rows):
    """Keep the Nuvio fields that control Home placement/presentation."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get('id') or '').strip()
        if not cid:
            continue
        out.append({
            'id': cid,
            'title': str(row.get('title') or row.get('name') or '').strip(),
            # Accept both remote Nuvio JSON and our already-normalized cache.
            # ``load()`` re-normalizes the file; omitting these aliases used to
            # erase Collection art and pinToTop after the first reload.
            'backdrop': str(row.get('backdropImageUrl') or
                            row.get('backdropImageURL') or
                            row.get('backdrop') or '').strip(),
            'cover': str(row.get('coverImageUrl') or row.get('cover') or '').strip(),
            'show_all_tab': bool(row.get('showAllTab', row.get('show_all_tab', True))),
            'pin_to_top': bool(row.get('pinToTop', row.get('pin_to_top', False))),
            'focus_glow_enabled': bool(row.get(
                'focusGlowEnabled', row.get('focus_glow_enabled', True))),
            'view_mode': str(row.get('viewMode') or row.get('view_mode') or 'TABBED_GRID'),
        })
    return out


@_scope.bound
def normalize_payload(payload, platform='', addons=None, collections=None, complete=False):
    payload = payload if isinstance(payload, dict) else {}
    raw_items = payload.get('items')
    if not isinstance(raw_items, list):
        raw_items = payload.get('catalogs') if isinstance(payload.get('catalogs'), list) else []
    items = []
    for idx, row in enumerate(raw_items):
        item = normalize_item(row, idx)
        if item:
            items.append(item)
    items.sort(key=lambda row: (int(row.get('order') or 0), int(row.get('_index') or 0)))
    for row in items:
        row.pop('_index', None)
    return {
        'schema': SCHEMA,
        'platform': str(platform or payload.get('platform') or ''),
        'complete': bool(complete),
        'hide_unreleased_content': bool(payload.get(
            'hide_unreleased_content', payload.get('hideUnreleasedContent', False))),
        'items': items,
        'addons': _normalize_addons(addons),
        'collections': _normalize_collections(collections),
    }


@_scope.bound
def save(payload, platform='', updated_at='', addons=None, collections=None, complete=False):
    """Save a validated snapshot, retaining last-good data on failed pulls.

    Nuvio TV treats an empty remote Home as non-applicable and preserves the
    current local Home. Add-ons, Collections and the standalone unreleased
    flag still update independently around that retained item order.
    """
    current = load()
    data = normalize_payload(payload, platform=platform, addons=addons,
                             collections=collections, complete=complete)
    data['updated_at'] = str(updated_at or '')
    data['saved_at'] = int(time.time())
    if not data.get('items') and not complete and current.get('items'):
        data['items'] = list(current.get('items') or [])
        data['complete'] = bool(current.get('complete', True))
    if addons is None:
        data['addons'] = list(current.get('addons') or [])
    if collections is None:
        data['collections'] = list(current.get('collections') or [])
    write_json(MIRROR_PATH, data)
    _LOADED['key'] = ()
    return data


_LOADED = {'key': (), 'value': None}


def _file_key():
    try:
        st = os.stat(MIRROR_PATH)
        return (_scope.key(), st.st_mtime_ns, st.st_size)
    except OSError:
        return (_scope.key(), None)


@_scope.bound
def load():
    """The snapshot (callers only read it). v5.10.188: read and normalized
    once per change of the file; a Home publish read it on every tab."""
    key = _file_key()
    if key == _LOADED['key'] and _LOADED['value'] is not None:
        return _LOADED['value']
    value = _load_file()
    _LOADED.update(key=key, value=value)
    return value


def _load_file():
    data = read_json(MIRROR_PATH, {}) or {}
    if not isinstance(data, dict) or not isinstance(data.get('items') or [], list):
        return {}
    norm = normalize_payload(
        {'items': data.get('items') or [],
         'hide_unreleased_content': data.get('hide_unreleased_content', False)},
        platform=data.get('platform') or '',
        addons=data.get('addons') or [],
        collections=data.get('collections') or [],
        complete=bool(data.get('complete', bool(data.get('items')))),
    )
    norm['saved_at'] = _to_int(data.get('saved_at'), 0)
    norm['updated_at'] = str(data.get('updated_at') or '')
    # A schema upgrade means the installed provider manifests and synced
    # presentation fields need one fresh pull before the mirror is canonical.
    norm['needs_refresh'] = _to_int(data.get('schema'), 0) < SCHEMA
    return norm



@_scope.bound
def collection_summary(collection_id):
    variants = _contract.collection_id_variants(collection_id)
    for row in load().get('collections') or []:
        if _contract.collection_id_variants((row or {}).get('id')) & variants:
            return dict(row)
    return {}


@_scope.bound
def clear():
    for path in (MIRROR_PATH, MIRROR_PATH + '.bak'):
        try:
            os.remove(path)
        except Exception:
            pass


@_scope.bound
def age_seconds(now=None):
    saved = _to_int(load().get('saved_at'), 0)
    if not saved:
        return 10 ** 9
    return max(0, int((now or time.time()) - saved))


@_scope.bound
def is_stale(max_age=600):
    return bool(load().get('needs_refresh')) or age_seconds() > int(max_age)
