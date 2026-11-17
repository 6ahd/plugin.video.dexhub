"""Native title enrichment, called inside the provider worker/budget only."""

import re

# v5.10.554: an id is never a name to search a library with. The 8 Nov log:
# a Silo row with no title reached Plex as the search text
# "silo:287b49cd-...:movie-tmdb-414906"; Plex answered with the first items
# of the library (11 Rebels, 12 Monkeys, ...) and checking those used up the
# server's 6 s before "The Batman" was ever asked. Only tt/imdb/tmdb/tvdb were
# left out before, and only for Jellyfin.
# ids never hold a space, so "Nas: Time Is Illmatic" stays a title
_ID_LIKE_RE = re.compile(
    r'(?:'
    r'tt\d+(?::\d+){0,2}'
    r'|(?:imdb|tmdb|tvdb|tvmaze|trakt|simkl|kitsu|mal|anilist|anidb|silo|plex|emby|jellyfin'
    r'|nas|kodi|dexhub|iptv|xtream)[:_]\S*'
    r'|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\S*'
    r'|\d{5,}'
    r')', re.I)
# a code like JSGB4701: capitals with two or more digits (ARGO, HEAT and
# M3GAN stay titles)
_CODE_RE = re.compile(r'(?=[A-Z0-9]*[A-Z])(?=(?:[A-Z]*\d){2})[A-Z0-9]{6,}')


def is_id_like(value):
    """True for ids and codes that are not a title (silo:/plex:/emby:/tt.../uuid)."""
    text = str(value or '').strip()
    if not text:
        return True
    return bool(_CODE_RE.fullmatch(text) or _ID_LIKE_RE.fullmatch(text))


def clean_titles(titles):
    """titles without ids, blanks and repeats, order kept."""
    out = []
    for value in titles or []:
        value = str(value or '').strip()
        if value and not is_id_like(value) and value.casefold() not in [x.casefold() for x in out]:
            out.append(value)
    return out


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
            if value and not is_id_like(value) and value.casefold() not in [x.casefold() for x in out]:
                out.append(value)
    return out


def resolve(provider, media_type, tmdb):
    titles = list(provider.get('_titles') or [])
    title = provider.get('_title') or ''
    if title and title.casefold() not in [str(x).casefold() for x in titles]:
        titles.insert(0, title)
    titles = clean_titles(titles)
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
                if candidate and not is_id_like(candidate) and candidate.casefold() not in [str(x).casefold() for x in titles]:
                    titles.append(candidate)
                    added = True
            if jellyfin and added:
                break  # one metadata lookup is enough; stay in the server budget
        except Exception:
            # Metadata failure must not disable ID-based server lookup.
            pass
    if provider.get('id') in ('__plex__', '__jellyfin__'):
        for candidate in list(titles):
            clean = re.sub(r'\s+(?:[—\-]\s*)?S\d{1,3}E\d{1,4}\s*$', '', str(candidate), flags=re.I).strip()
            if clean and clean.casefold() not in [str(x).casefold() for x in titles]:
                titles.append(clean)
    return titles
