# -*- coding: utf-8 -*-
"""Nuvio / Stremio / Kaptain collection interoperability.

This module deliberately uses portable JSON instead of private account APIs.
It can import Kaptain/Nuvio collection exports, import Stremio manifest bundles,
and export Dex Hub providers + collections in a Nuvio-friendly shape.
"""
import json
import os
import time
from urllib.parse import urlparse

from ..dexhub.common import profile_path
from ..dexhub.links import manifest_link
from ..dexhub.safe_io import write_json
from .. import store
from .. import collection_sets
from ..client import validate_manifest

STATE_FILE = os.path.join(profile_path(), 'nuvio_sync_state.json')


def _read_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def state():
    out = _read_state()
    out.setdefault('sync_url', '')
    out.setdefault('last_sync', 0)
    out.setdefault('last_imported_providers', 0)
    out.setdefault('last_imported_collections', 0)
    return out


def set_sync_url(url):
    row = state()
    row['sync_url'] = str(url or '').strip()
    write_json(STATE_FILE, row)
    return row


def _manifest_urls_from_any(data):
    """Extract configured Stremio manifest URLs from common export shapes."""
    found = []
    seen = set()

    def add(value):
        value = manifest_link(value)            # v5.10.143: stremio:// links
        if not value.startswith(('http://', 'https://')):
            return
        if 'manifest' not in value.lower():
            return
        if value not in seen:
            seen.add(value)
            found.append(value)

    def walk(node):
        if isinstance(node, str):
            add(node)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for key in ('manifest_url', 'manifestUrl', 'manifestURL', 'transportUrl', 'url'):
            add(node.get(key))
        # Nuvio/Stremio exports often store a plain list under one of these.
        for key in ('addons', 'providers', 'manifests', 'stremioAddons', 'installedAddons', 'sources'):
            if key in node:
                walk(node.get(key))
        # Walk nested collection/config containers, but avoid traversing huge
        # metadata payloads indiscriminately.
        for key in ('profile', 'config', 'setup', 'data', 'payload'):
            if key in node and isinstance(node.get(key), (dict, list)):
                walk(node.get(key))

    walk(data)
    return found


def _kaptain_folder(entry, provider_by_id):
    kind = str(entry.get('kind') or '')
    payload = entry.get('payload') or {}
    sources = []
    if kind == 'multiCatalog':
        sources = payload.get('sources') or []
    elif kind == 'addonCatalog':
        sources = [payload]
    cleaned = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        addon_id = str(src.get('addonId') or src.get('addon_id') or '').strip()
        if not addon_id:
            provider_id = str(src.get('provider_id') or src.get('providerId') or '').strip()
            provider = provider_by_id.get(provider_id) or {}
            addon_id = str((provider.get('manifest') or {}).get('id') or '').strip()
        catalog_id = str(src.get('catalogId') or src.get('catalog_id') or '').strip()
        catalog_type = str(src.get('catalogType') or src.get('type') or 'movie').strip()
        catalog_id, catalog_type = collection_sets.normalize_catalog_id_and_type(catalog_id, catalog_type)
        if addon_id and catalog_id:
            cleaned.append({
                'addonId': addon_id,
                'type': catalog_type,
                'catalogId': catalog_id,
                'genre': src.get('genre') or '',
                'extra': src.get('extra') or {},
            })
    if not cleaned:
        return None
    folder = {
        'id': str(entry.get('id') or ''),
        'title': str(entry.get('name') or 'Collection'),
        'tileShape': 'LANDSCAPE' if str(entry.get('layout') or '').lower() == 'wide' else 'POSTER',
        'coverImageUrl': str(entry.get('background') or entry.get('poster') or ''),
        'clearLogoUrl': str(entry.get('clearlogo') or ''),
        'hideTitle': bool(entry.get('hide_title') or False),
        'catalogSources': cleaned,
    }
    return folder


def export_bundle():
    providers = store.list_providers() or []
    provider_by_id = {str(p.get('id') or ''): p for p in providers}
    addon_rows = []
    for provider in providers:
        manifest = provider.get('manifest') or {}
        addon_rows.append({
            'name': provider.get('name') or manifest.get('name') or 'Addon',
            'manifestUrl': provider.get('manifest_url') or '',
            'addonId': manifest.get('id') or '',
        })
    groups = []
    for row in collection_sets.list_sets() or []:
        folders = []
        for entry in row.get('entries') or []:
            folder = _kaptain_folder(entry, provider_by_id)
            if folder:
                folders.append(folder)
        if folders:
            groups.append({
                'id': str(row.get('id') or ''),
                'title': str(row.get('name') or 'Dex Hub'),
                'folders': folders,
            })
    return {
        'format': 'dexhub.nuvio.sync',
        'version': 1,
        'exportedAt': int(time.time()),
        'addons': addon_rows,
        # Kaptain/Nuvio-compatible collection group shape.
        'collections': groups,
        'folders': [f for group in groups for f in (group.get('folders') or [])],
    }


def import_payload(raw, source_label='Nuvio Sync', manifest_resolver=None):
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', 'replace')
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, (dict, list)):
        raise ValueError('ملف المزامنة غير صالح')

    provider_count = 0
    for manifest_url in _manifest_urls_from_any(data):
        try:
            manifest = validate_manifest(manifest_url)
            existing = False
            wanted = manifest_url.rstrip('/')
            for row in store.list_providers() or []:
                if str(row.get('manifest_url') or '').rstrip('/') == wanted:
                    existing = True
                    break
            if not existing:
                store.add_provider(manifest.get('name') or 'Stremio Addon', manifest_url, manifest)
                provider_count += 1
        except Exception:
            continue

    # Prefer explicit collections, otherwise let the permissive parser inspect
    # the whole payload (supports Kaptain groups/folders and Fusion widgets).
    collection_count = 0
    collection_data = data.get('collections') if isinstance(data, dict) else data
    try:
        text = json.dumps(collection_data if collection_data is not None else data, ensure_ascii=False)
        row = collection_sets.add_set_from_text(
            text,
            source_label=source_label,
            manifest_resolver=manifest_resolver,
        )
        collection_count = 1 if row else 0
    except Exception:
        # A provider-only Stremio export is still a valid import.
        collection_count = 0

    st = state()
    st.update({
        'last_sync': int(time.time()),
        'last_imported_providers': provider_count,
        'last_imported_collections': collection_count,
    })
    write_json(STATE_FILE, st)
    return {'providers': provider_count, 'collections': collection_count}


def pull(url, manifest_resolver=None):
    text = collection_sets.fetch_raw_text(url)
    return import_payload(text, source_label=_label_from_url(url), manifest_resolver=manifest_resolver)


def _label_from_url(url):
    try:
        tail = os.path.basename(urlparse(url).path or '').rsplit('.', 1)[0]
        return tail.replace('-', ' ').replace('_', ' ').strip().title() or 'Nuvio Sync'
    except Exception:
        return 'Nuvio Sync'
