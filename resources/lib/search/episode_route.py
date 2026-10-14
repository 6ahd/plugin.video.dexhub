"""Dependency-free episode handoff and one-job-per-provider target assembly."""


def helper_episode_id(imdb_id, season, episode):
    value = str(imdb_id or '').strip()
    if value.startswith('imdb:'):
        value = value[5:]
    if value.isdigit():
        value = 'tt' + value
    try:
        s, e = int(season), int(episode)
    except (TypeError, ValueError):
        return ''
    if value.startswith('tt') and value[2:].isdigit() and s >= 0 and e > 0:
        return '%s:%d:%d' % (value, s, e)
    return ''


def merge_targets(generated, direct):
    """Keep manifest-ranked IDs first, retaining catalog IDs as fallbacks.

    A broad stream manifest is not evidence that a provider understands another
    provider's private catalog ID. Never let that direct job mask the standard
    ID job. Native server targets retain their independent identity/context.
    """
    result, positions = [], {}
    for provider, request_id in list(generated) + list(direct):
        if str(provider.get('id') or '').startswith('__'):
            result.append((provider, request_id))
            continue
        key = (provider.get('id'), provider.get('base_url'), provider.get('name'))
        candidates = list(provider.get('_candidate_request_ids') or [request_id])
        if key in positions:
            existing = result[positions[key]][0]['_candidate_request_ids']
            extra = [candidate for candidate in candidates if candidate and candidate not in existing]
            # The engine has three alternate slots. Reserve a slot for the
            # catalog-specific ID instead of silently putting it fifth.
            existing[:] = (existing[:1] + extra + existing[1:])[:4]
            continue
        clone = dict(provider)
        clone['_candidate_request_ids'] = list(dict.fromkeys(candidates))
        positions[key] = len(result)
        result.append((clone, clone['_candidate_request_ids'][0]))
    return result
