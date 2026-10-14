"""Native title enrichment, called inside the provider worker/budget only."""


def resolve(provider, media_type, tmdb):
    titles = list(provider.get('_titles') or [])
    title = provider.get('_title') or ''
    if title and title.casefold() not in [str(x).casefold() for x in titles]:
        titles.insert(0, title)
    if not provider.get('_enrich_titles'):
        return titles
    ids = provider.get('_ids') or {}
    kind = 'series' if str(media_type or '').strip().lower() in ('series', 'tv', 'show', 'anime') else 'movie'
    lookups = [('titles_for_imdb', ids.get('imdb_id') or '')]
    if provider.get('id') == '__plex__':
        lookups.append(('english_titles_for', ids.get('tmdb_id') or ''))
    for method, value in lookups:
        if not value:
            continue
        try:
            for candidate in getattr(tmdb, method)(value, kind):
                if candidate and candidate.casefold() not in [str(x).casefold() for x in titles]:
                    titles.append(candidate)
        except Exception:
            # Metadata failure must not disable ID-based server lookup.
            pass
    if provider.get('id') == '__plex__':
        import re
        for candidate in list(titles):
            clean = re.sub(r'\s+(?:[—\-]\s*)?S\d{1,3}E\d{1,4}\s*$', '', str(candidate), flags=re.I).strip()
            if clean and clean.casefold() not in [str(x).casefold() for x in titles]:
                titles.append(clean)
    return titles
