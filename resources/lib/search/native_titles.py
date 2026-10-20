"""Native title enrichment, called inside the provider worker/budget only."""

import re


def candidates(meta, media_type='movie'):
    """Names already available locally, before any metadata network call."""
    show = str(media_type or '').casefold() in ('series', 'show', 'tv', 'episode', 'anime')
    show_keys = ('tvShowTitle', 'show_title', 'showname', 'show_name', 'seriesName', 'grandparentTitle', 'en_showname', 'ar_showname')
    keys = (show_keys if show else ()) + ('originalTitle', 'original_title', 'original_name', 'en_title', 'english_title', 'name', 'title', 'ar_title')
    out = []
    for key in keys:
        name = str((meta or {}).get(key) or '').strip()
        clean = re.sub(r'\s+(?:[—\-]\s*)?S\d{1,3}E\d{1,4}\s*$', '', name, flags=re.I).strip()
        for value in (clean, name):
            if value and not re.fullmatch(r'(?:tt\d+(?::\d+){0,2}|(?:imdb|tmdb|tvdb):.*)', value, flags=re.I) and value.casefold() not in [x.casefold() for x in out]:
                out.append(value)
    return out


def resolve(provider, media_type, tmdb):
    titles = list(provider.get('_titles') or [])
    title = provider.get('_title') or ''
    if title and title.casefold() not in [str(x).casefold() for x in titles]:
        titles.insert(0, title)
    if not provider.get('_enrich_titles'):
        return titles
    ids = provider.get('_ids') or {}
    kind = 'series' if str(media_type or '').strip().lower() in ('series', 'tv', 'show', 'anime') else 'movie'
    jellyfin = provider.get('id') == '__jellyfin__'
    lookups = ([('english_titles_for', ids.get('tmdb_id') or ''),
                ('titles_for_imdb', ids.get('imdb_id') or '')] if jellyfin else
               [('titles_for_imdb', ids.get('imdb_id') or '')])
    if provider.get('id') == '__plex__':
        lookups.append(('english_titles_for', ids.get('tmdb_id') or ''))
    for method, value in lookups:
        if not value:
            continue
        try:
            added = False
            for candidate in getattr(tmdb, method)(value, kind):
                if candidate and candidate.casefold() not in [str(x).casefold() for x in titles]:
                    titles.append(candidate)
                    added = True
            if jellyfin and added:
                break  # one metadata lookup is enough; stay in the server budget
        except Exception:
            # Metadata failure must not disable ID-based server lookup.
            pass
    if provider.get('id') in ('__plex__', '__jellyfin__'):
        import re
        for candidate in list(titles):
            clean = re.sub(r'\s+(?:[—\-]\s*)?S\d{1,3}E\d{1,4}\s*$', '', str(candidate), flags=re.I).strip()
            if clean and clean.casefold() not in [str(x).casefold() for x in titles]:
                titles.append(clean)
    return titles
