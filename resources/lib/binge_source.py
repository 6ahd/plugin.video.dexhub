# -*- coding: utf-8 -*-
"""Pure source-affinity helpers for next-episode playback.

The selected stream remains authoritative.  A next episode may reuse the
same provider/server and, when the provider exposes enough identity, prefer
the same internal addon, indexer, service and Stremio ``bingeGroup``.
"""


def _text(value):
    return str(value or '').strip()


def _norm(value):
    return _text(value).casefold()


def affinity_from_context(ctx):
    ctx = dict(ctx or {})
    hints = ctx.get('behaviorHints') or {}
    if not isinstance(hints, dict):
        hints = {}
    provider_id = _text(ctx.get('source_provider_id') or ctx.get('provider_id'))
    if provider_id in ('plex_native', 'emby_native', 'silo_native', 'jellyfin_native'):
        provider_id = '__%s__' % provider_id.split('_', 1)[0]
    return {
        'provider_id': provider_id,
        'provider_name': _text(ctx.get('provider_name')),
        'server_id': _text(ctx.get('server_id')),
        'binge_group': _text(hints.get('bingeGroup') or ctx.get('binge_group')),
        'source_addon': _text(ctx.get('source_addon')),
        'source_indexer': _text(ctx.get('source_indexer')),
        'source_service': _text(ctx.get('source_service')),
        'source_type': _text(ctx.get('source_type')),
    }


def filter_targets(targets, provider_id='', server_id=''):
    """Keep only the selected provider and native server, without fallback."""
    rows = list(targets or [])
    wanted_provider = _text(provider_id)
    wanted_server = _text(server_id)
    if wanted_provider:
        rows = [row for row in rows
                if _text((row[0] or {}).get('id')) == wanted_provider]
    if wanted_server:
        rows = [row for row in rows
                if _text(((row[0] or {}).get('_server') or {}).get('id')) == wanted_server]
    return rows


def prioritize_entries(entries, affinity):
    """Stable-sort rows so the closest stream family is selected first."""
    rows = list(entries or [])
    wanted = dict(affinity or {})

    def score(row):
        value = 0
        pairs = (
            ('binge_group', 'binge_group', 1000),
            ('source_addon', 'source_addon', 240),
            ('source_addon', 'addon', 240),
            ('source_indexer', 'source_indexer', 140),
            ('source_indexer', 'indexer', 140),
            ('source_service', 'source_service', 100),
            ('source_service', 'service_name', 100),
            ('source_type', 'source_type', 40),
            ('provider_name', 'provider_name_raw', 20),
        )
        matched = set()
        for wanted_key, row_key, points in pairs:
            token = (wanted_key, points)
            if token in matched:
                continue
            if _norm(wanted.get(wanted_key)) and (
                    _norm(wanted.get(wanted_key)) == _norm(row.get(row_key))):
                value += points
                matched.add(token)
        return value

    # Python's sort is stable, so the normal Dex ranking remains the tie-break.
    return sorted(rows, key=score, reverse=True)
