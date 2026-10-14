# -*- coding: utf-8 -*-
"""Pure Nuvio compatibility contract used by every Kodi adapter.

This module contains the parts of Nuvio's behavior that must not depend on
Kodi, Dex Hub artwork policy or a particular renderer: stable identities,
remote Home selection, pinned-Collection placement and exact catalog
matching.  Keeping those rules here prevents the Movies, TV and Collection
views from each growing a slightly different approximation of Nuvio.
"""


SHARED_HOME_PLATFORM = 'home_catalog_shared'


def catalog_type(value):
    """Return the raw Stremio/Nuvio catalog type used in stable identity."""
    return str(value or '').strip().lower()


def normalize_media_type(value):
    """Map an exact catalog type to Dex Hub's Movies/TV projection bucket."""
    value = catalog_type(value)
    if value in ('series', 'tv', 'show', 'shows', 'tvshow', 'anime'):
        return 'series'
    if value in ('movie', 'movies', 'film', 'films'):
        return 'movie'
    return value


def collection_id_variants(value):
    raw = str(value or '').strip()
    if not raw:
        return set()
    out = {raw}
    for prefix in ('collection:', 'collection::', 'collection_'):
        if raw.lower().startswith(prefix):
            bare = raw[len(prefix):].strip()
            if bare:
                out.add(bare)
                out.add('collection_' + bare)
    if not raw.startswith('collection_'):
        out.add('collection_' + raw)
    return out


def catalog_key(addon_id, media_type, catalog_id):
    addon_id = str(addon_id or '').strip()
    # Nuvio's legacy key uses the exact catalog.apiType.  Bucketing ``tv`` or
    # ``anime`` as ``series`` here would resolve a different descriptor.
    media_type = catalog_type(media_type)
    catalog_id = str(catalog_id or '').strip()
    if not (addon_id and media_type and catalog_id):
        return ''
    return '%s_%s_%s' % (addon_id, media_type, catalog_id)


def collection_key(collection_id):
    collection_id = str(collection_id or '').strip()
    return 'collection_%s' % collection_id if collection_id else ''


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def catalog_is_search_only(catalog):
    """Match Nuvio's required-search catalog exclusion rule.

    A catalog that merely *supports* search still belongs on Home.  It is
    search-only only when the ``search`` extra is required.  Manifests in the
    wild use both the Stremio ``extra[]`` object form and ``extraRequired``.
    """
    if not isinstance(catalog, dict):
        return False
    # v5.10.188: no set built per call (a Home's catalogs ask it thousands of times)
    for entry in catalog.get('extraRequired') or ():
        name = entry.get('name') if isinstance(entry, dict) else entry
        if name and str(name).strip().lower() == 'search':
            return True
    for entry in catalog.get('extra') or ():
        if isinstance(entry, dict):
            name = entry.get('name')
            if name and str(name).strip().lower() == 'search' and _truthy(
                    entry.get('isRequired', entry.get('required', False))):
                return True
    return False


def catalog_should_show_on_home(catalog):
    """Return Nuvio's Home visibility default for one manifest catalog."""
    catalog = catalog if isinstance(catalog, dict) else {}
    if catalog_is_search_only(catalog):
        return False
    # Nuvio defaults an omitted showInHome to visible.  This differs from the
    # mapped domain object's Boolean default, hence the explicit-key check.
    if 'showInHome' in catalog:
        return _truthy(catalog.get('showInHome'))
    if 'show_in_home' in catalog:
        return _truthy(catalog.get('show_in_home'))
    return True


def _normalize_collection_boundaries(order, owners):
    """Move Collections out of the middle of one add-on's catalog block."""
    result = list(order or [])
    changed = True
    while changed:
        changed = False
        index = 0
        while index < len(result):
            key = result[index]
            if not str(key).startswith('collection_'):
                index += 1
                continue
            previous = next((owners.get(result[pos])
                             for pos in range(index - 1, -1, -1)
                             if not str(result[pos]).startswith('collection_')), None)
            following = next((owners.get(result[pos])
                              for pos in range(index + 1, len(result))
                              if not str(result[pos]).startswith('collection_')), None)
            if previous is not None and previous == following:
                moved = result.pop(index)
                insert_at = index
                while (insert_at < len(result) and
                       not str(result[insert_at]).startswith('collection_') and
                       owners.get(result[insert_at]) == previous):
                    insert_at += 1
                result.insert(insert_at, moved)
                changed = changed or insert_at != index
            index += 1
    return result


def complete_home_items(items, collections, addons,
                        follow_addons_order=False):
    """Augment a saved Home order with every currently available Nuvio row.

    This mirrors ``HomeViewModel.rebuildCatalogOrder`` when follow-addons-order
    is disabled: valid saved keys retain their relative order, then unsaved
    visible catalogs are appended in enabled-account/manifest order, followed
    by unsaved Collections in Collection storage order.  Disabled saved rows
    remain in the seen set so they cannot be accidentally re-enabled.

    ``addons`` is deliberately transport-neutral and accepts rows shaped as
    ``{'addon_id': manifest.id, 'catalogs': manifest.catalogs}``.
    """
    saved = [dict(row) for row in (items or []) if isinstance(row, dict)]
    out = []
    seen = set()
    saved_keys = []
    default_keys = []
    owners = {}

    for index, row in enumerate(saved):
        key = str(row.get('key') or '').strip()
        if not key:
            key = (collection_key(row.get('collection_id'))
                   if row.get('is_collection') else
                   catalog_key(row.get('addon_id'), row.get('type'),
                               row.get('catalog_id')))
        if not key or key in seen:
            continue
        row['key'] = key
        row['order'] = len(out)
        out.append(row)
        seen.add(key)
        saved_keys.append(key)

    for addon in addons if isinstance(addons, list) else []:
        if not isinstance(addon, dict) or addon.get('enabled') is False:
            continue
        addon_id = str(addon.get('addon_id') or addon.get('id') or '').strip()
        if not addon_id:
            continue
        for catalog in addon.get('catalogs') or []:
            if not isinstance(catalog, dict) or not catalog_should_show_on_home(catalog):
                continue
            media_type = catalog_type(
                catalog.get('type') or catalog.get('apiType') or
                catalog.get('rawType'))
            catalog_id = str(catalog.get('id') or '').strip()
            key = catalog_key(addon_id, media_type, catalog_id)
            if key and key not in default_keys:
                default_keys.append(key)
                owners[key] = addon_id
            if not key or key in seen:
                continue
            out.append({
                'addon_id': addon_id,
                'type': media_type,
                'catalog_id': catalog_id,
                'enabled': True,
                'order': len(out),
                'custom_title': '',
                'is_collection': False,
                'collection_id': '',
                'key': key,
            })
            seen.add(key)

    for summary in collections if isinstance(collections, list) else []:
        if not isinstance(summary, dict):
            continue
        collection_id = str(summary.get('id') or '').strip()
        key = collection_key(collection_id)
        if not key or key in seen:
            continue
        out.append({
            'addon_id': '',
            'type': '',
            'catalog_id': '',
            'enabled': True,
            'order': len(out),
            'custom_title': '',
            'is_collection': True,
            'collection_id': collection_id,
            'key': key,
        })
        seen.add(key)

    if follow_addons_order:
        collection_keys = [collection_key((row or {}).get('id'))
                           for row in (collections or []) if isinstance(row, dict)]
        collection_keys = list(dict.fromkeys(
            key for key in collection_keys if key))
        available = set(default_keys + collection_keys)
        saved_valid = []
        for key in saved_keys:
            if key in available and key not in saved_valid:
                saved_valid.append(key)
        if saved_valid:
            ordered = []
            pointer = 0
            collection_set = set(collection_keys)
            for key in saved_valid:
                if key in collection_set:
                    if key not in ordered:
                        ordered.append(key)
                    continue
                try:
                    target = default_keys.index(key)
                except ValueError:
                    target = -1
                if target >= 0:
                    while pointer <= target:
                        candidate = default_keys[pointer]
                        if candidate not in ordered:
                            ordered.append(candidate)
                        pointer += 1
            while pointer < len(default_keys):
                candidate = default_keys[pointer]
                if candidate not in ordered:
                    ordered.append(candidate)
                pointer += 1
            ordered.extend(key for key in collection_keys if key not in ordered)
            ordered = _normalize_collection_boundaries(ordered, owners)
        else:
            ordered = default_keys + collection_keys
        by_key = {row.get('key'): row for row in out}
        out = [dict(by_key[key]) for key in ordered if key in by_key]

    for index, row in enumerate(out):
        row['order'] = index
    return out


def _payload_items(row):
    payload = (row or {}).get('payload')
    payload = payload if isinstance(payload, dict) else {}
    items = payload.get('items')
    if not isinstance(items, list):
        items = payload.get('catalogs') if isinstance(payload.get('catalogs'), list) else []
    return items


def select_home_snapshot(rows, current_hide_unreleased=False):
    """Select remote Home exactly like Nuvio TV's sync service.

    A populated shared payload wins.  Otherwise the newest populated legacy
    TV/mobile payload wins, with shared/legacy empty rows used only as a final
    fallback.  ``hide_unreleased_content`` is independent and comes from the
    newest row that explicitly contains it.  Empty payloads are deliberately
    marked incomplete because Nuvio TV preserves the current local Home when
    a remote Home payload is empty.
    """
    normalized = []
    for source in rows if isinstance(rows, (list, tuple)) else []:
        if not isinstance(source, dict):
            continue
        payload = source.get('payload')
        if not isinstance(payload, dict):
            payload = {}
        item_rows = _payload_items({'payload': payload})
        normalized.append({
            'platform': str(source.get('platform') or ''),
            'payload': dict(payload),
            'updated_at': str(source.get('updated_at') or ''),
            'has_items': bool(item_rows),
            'has_hide_unreleased': bool(source.get(
                'has_hide_unreleased',
                'hide_unreleased_content' in payload or 'hideUnreleasedContent' in payload)),
        })

    if not normalized:
        return {
            'platform': '', 'payload': {'items': []}, 'updated_at': '',
            'has_items': False, 'complete': False,
        }

    shared = next((row for row in normalized
                   if row.get('platform') == SHARED_HOME_PLATFORM), None)
    legacy = [row for row in normalized
              if row.get('platform') != SHARED_HOME_PLATFORM]
    populated_legacy = [row for row in legacy if row.get('has_items')]

    if shared and shared.get('has_items'):
        selected = shared
    elif populated_legacy:
        selected = max(populated_legacy, key=lambda row: row.get('updated_at') or '')
    elif shared:
        selected = shared
    else:
        selected = max(legacy, key=lambda row: row.get('updated_at') or '')

    payload = dict(selected.get('payload') or {})
    hide_rows = [row for row in normalized if row.get('has_hide_unreleased')]
    if hide_rows:
        newest_hide = max(hide_rows, key=lambda row: row.get('updated_at') or '')
        newest_payload = newest_hide.get('payload') or {}
        hide_value = newest_payload.get(
            'hide_unreleased_content', newest_payload.get('hideUnreleasedContent', False))
    else:
        hide_value = bool(current_hide_unreleased)
    payload['hide_unreleased_content'] = bool(hide_value)

    out = dict(selected)
    out['payload'] = payload
    out['complete'] = bool(out.get('has_items'))
    return out



def _item_for_collection(items, collection_id):
    wanted = collection_id_variants(collection_id)
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get('is_collection'):
            continue
        if collection_id_variants(item.get('collection_id')) & wanted:
            return item
    return None


def project_home_rows(items, collections, media_type):
    """Project Nuvio Home into the user's Movies/TV split.

    Nuvio inserts every ``pinToTop`` Collection first, in Collection storage
    order, before walking the saved Home order.  Non-pinned Collections remain
    interleaved.  Collections are untyped and therefore intentionally appear
    in both Dex Hub splits, while catalog rows retain their exact media type.
    """
    items = [dict(row) for row in (items or []) if isinstance(row, dict)]
    collections = [dict(row) for row in (collections or []) if isinstance(row, dict)]
    wanted_type = normalize_media_type(media_type)
    out = []
    pinned_ids = set()

    for summary in collections:
        if not summary.get('pin_to_top'):
            continue
        cid = str(summary.get('id') or '').strip()
        if not cid:
            continue
        item = _item_for_collection(items, cid)
        if item is not None and item.get('enabled') is False:
            continue
        row = dict(item or {
            'addon_id': '', 'type': '', 'catalog_id': '',
            'enabled': True, 'order': -1, 'custom_title': '',
            'is_collection': True, 'collection_id': cid,
            'key': collection_key(cid),
        })
        out.append(row)
        pinned_ids.update(collection_id_variants(cid))

    for item in items:
        if item.get('enabled') is False:
            continue
        if item.get('is_collection'):
            if collection_id_variants(item.get('collection_id')) & pinned_ids:
                continue
            out.append(dict(item))
        elif normalize_media_type(item.get('type')) == wanted_type:
            out.append(dict(item))
    return out


def match_catalog(catalogs, catalog_id, media_type):
    """Resolve the exact Nuvio catalog identity; never cross media types."""
    wanted_id = str(catalog_id or '').strip()
    wanted_type = catalog_type(media_type)
    if not (wanted_id and wanted_type):
        return None
    for catalog in catalogs if isinstance(catalogs, list) else []:
        if not isinstance(catalog, dict):
            continue
        if (str(catalog.get('id') or '').strip() == wanted_id and
                catalog_type(catalog.get('type')) == wanted_type):
            return catalog
    return None


def collection_title(entry=None, summary=None):
    """Nuvio Collection titles come from the Collection object itself."""
    return str((entry or {}).get('name') or
               (summary or {}).get('title') or
               (summary or {}).get('name') or '').strip()
