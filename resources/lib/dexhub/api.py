# -*- coding: utf-8 -*-
from . import client, store
from .links import manifest_link


def add_provider_from_url(manifest_url, name=None):
    manifest_url = manifest_link(manifest_url)
    manifest = client.validate_manifest(manifest_url)
    pname = name or manifest.get('name') or manifest.get('id') or 'Provider'
    return store.add_provider(pname, manifest_url, manifest)


def list_providers():
    return store.list_providers()


def get_provider(provider_id):
    return store.get_provider(provider_id)


def remove_provider(provider_id):
    return store.remove_provider(provider_id)


def fetch_provider_catalog(provider_id, media_type, catalog_id, extra=None):
    provider = store.get_provider(provider_id)
    if not provider:
        raise ValueError('Unknown provider')
    return client.fetch_catalog(provider, media_type, catalog_id, extra=extra or {})


def search_catalogs(media_type, query, provider_id=None):
    rows = [store.get_provider(provider_id)] if provider_id else store.list_providers()
    rows = [x for x in rows if x]
    seen = {}
    for provider in rows:
        for cat in provider.get('manifest', {}).get('catalogs', []):
            if not isinstance(cat, dict) or cat.get('type') != media_type:
                continue
            searchable = any((x.get('name') == 'search') for x in (cat.get('extra') or []) if isinstance(x, dict))
            if not searchable:
                continue
            try:
                data = client.fetch_catalog(provider, media_type, cat.get('id'), extra={'search': query})
            except Exception:
                continue
            for meta in data.get('metas') or []:
                item_id = meta.get('id')
                if item_id and item_id not in seen:
                    seen[item_id] = {'provider': provider, 'meta': meta, 'catalog': cat}
    return list(seen.values())


def provider_order_for_id(media_type, canonical_id, provider_id=None):
    rows = [store.get_provider(provider_id)] if provider_id else store.list_providers()
    rows = [x for x in rows if x]
    out = []
    for provider in rows:
        if client.supports_resource(provider, 'stream', media_type=media_type, item_id=canonical_id) or client.supports_resource(provider, 'meta', media_type=media_type, item_id=canonical_id):
            out.append(provider)
    return out


def get_best_meta(media_type, canonical_id, provider_id=None):
    for provider in provider_order_for_id(media_type, canonical_id, provider_id=provider_id):
        try:
            data = client.fetch_meta(provider, media_type, canonical_id)
            meta = data.get('meta') or data.get('metas')
            if isinstance(meta, list):
                meta = meta[0] if meta else None
            if meta:
                return meta
        except Exception:
            continue
    return {'id': canonical_id, 'type': media_type, 'name': canonical_id}


def get_stream_results(media_type, canonical_id, provider_id=None):
    out = []
    for provider in provider_order_for_id(media_type, canonical_id, provider_id=provider_id):
        try:
            data = client.fetch_streams(provider, media_type, canonical_id)
        except Exception:
            continue
        for row in data.get('streams') or []:
            stream_url = row.get('url') or row.get('externalUrl') or ''
            if not stream_url:
                continue
            out.append({'provider': provider, 'stream': row, 'stream_url': stream_url})
    return out
