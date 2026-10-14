# -*- coding: utf-8 -*-
"""Bounded series metadata lookup; exact source video IDs are retained."""

def lookup(api, media, canonical, source=''):
    try:
        providers = api._provider_order_for_id_with_context(
            media, canonical, source_provider_id=source) or []
    except Exception:
        return {}, None
    try:
        timeout = min(6.0, max(1.0, float(api.fast_timeout('meta'))))
    except Exception:
        timeout = 6.0

    def read(provider):
        try:
            data = api.fetch_meta(provider, media, canonical, timeout_override=timeout) or {}
            meta = data.get('meta') or data.get('metas')
            if isinstance(meta, list):
                meta = meta[0] if meta else None
            return meta if isinstance(meta, dict) and meta.get('videos') else {}
        except Exception:
            return {}

    preferred = next((p for p in providers if source and p.get('id') == source), None)
    if preferred is not None:
        meta = read(preferred)
        if meta:
            return meta, preferred
        providers = [p for p in providers if p is not preferred]
    if len(providers) == 1:
        return read(providers[0]), providers[0]
    race = api.iter_parallel(read, providers, workers=2, timeout=timeout, lane='browse')
    try:
        for provider, meta in race:
            if isinstance(meta, dict) and meta.get('videos'):
                return meta, provider
    finally:
        close = getattr(race, 'close', None)
        if close:
            close()
    return {}, None
