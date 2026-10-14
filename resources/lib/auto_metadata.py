"""Auto completes exact identities, field by field, without changing playback.

Directory rendering reads caches only. The Home worker, focused title and
bounded catalog warm-up may fetch missing data. Explicit sources stay separate.
"""
import re
import time
from urllib.parse import parse_qsl, urlsplit

# Aliases describe one fact: an existing plot must also protect description.
GROUPS = (
    ('description', 'overview', 'plot'), ('genres', 'genre'),
    ('year', 'releaseInfo', 'released', 'premiered'),
    ('rating', 'imdbRating', 'voteAverage', 'score'), ('runtime', 'duration'),
    ('poster', 'posterUrl', 'posterURL'), ('background', 'fanart', 'backdrop', 'landscape'),
    ('clearlogo', 'logo'), ('cast', 'credits_cast'), ('studio', 'studios', 'networks'),
    ('tagline',), ('director',), ('writer',), ('country',), ('status',),
    ('number_of_seasons',), ('studio_logos',), ('certification', 'mpaa'),
)
CORE = GROUPS[:7]


def present(value):
    if isinstance(value, str):
        return value.strip().lower() not in ('', 'none', 'null', 'undefined', 'n/a', 'unknown', '0', '0.0')
    return bool(value)


def complete(meta):
    """Pictures alone do not make a complete title."""
    from .home_art import clean
    return (isinstance(meta, dict)
            and all(any(present(meta.get(k)) for k in group) for group in CORE[:5])
            and all(any(clean(meta.get(k)) for k in group) for group in CORE[5:]))


def exact_ids(meta):
    from .art import extract_ids
    ids = extract_ids(meta or {})
    # Strip season/episode suffixes from a show's canonical identity. Never
    # interpret an episode number or an untyped numeric id as a TMDb id.
    raw = str((meta or {}).get('id') or '')
    for key, pattern in (('imdb_id', r'(?:imdb:)?(tt\d+)(?::\d+:\d+)?'),
                         ('tmdb_id', r'tmdb:(?:(?:movie|series|tv):)?(\d+)(?::\d+:\d+)?'),
                         ('tvdb_id', r'tvdb:(\d+)(?::\d+:\d+)?')):
        match = re.fullmatch(pattern, raw, re.I)
        if match:
            ids[key] = match.group(1)
    return {key: str(ids.get(key) or '').lower() for key, pattern in
            (('imdb_id', r'tt\d+'), ('tmdb_id', r'\d+'), ('tvdb_id', r'\d+'))
            if re.fullmatch(pattern, str(ids.get(key) or ''), re.I)}


def _kind(value):
    value = str(value or '').lower()
    return 'series' if value in ('series', 'tv', 'tvshow', 'show', 'anime') else value


def merge_missing(base, supplement, media_type=''):
    """Reject conflicting identities; keep ids/routes/videos and existing facts."""
    out = dict(base or {})
    if not isinstance(supplement, dict):
        return out
    before, after = exact_ids(out), exact_ids(supplement)
    if any(before[k] != after[k] for k in before.keys() & after.keys()):
        return out
    expected, actual = _kind(media_type or out.get('type')), _kind(supplement.get('type'))
    if actual and expected and actual != expected:
        return out
    from .home_art import clean
    for group in GROUPS:
        usable = clean if group in GROUPS[5:8] else present
        if any(usable(out.get(k)) for k in group):
            continue
        # Copy the source's units and keys (duration seconds / runtime minutes).
        for key in group:
            if usable(supplement.get(key)):
                out[key] = supplement[key]
    for key, value in after.items():
        if not before.get(key):
            out[key] = value
    return out


def enabled(target=''):
    from .meta_source import get_meta_source_for
    return get_meta_source_for(target) in ('', 'auto')


def enrich(meta, media_type, cache_only=False, language='', provider_id='', provider_network=True):
    from . import meta_source as S, profile_scope
    profile_scope.require_current()
    out = dict(meta or {})
    ids = exact_ids(out)
    if not ids or _kind(media_type) not in ('movie', 'series'):
        return out

    def accept(found):
        nonlocal out, ids
        profile_scope.require_current()
        out = merge_missing(out, found, media_type)
        ids = exact_ids(out)

    # Blank name/year prevent Helper's title search from matching a remake.
    accept(S._fetch_from_tmdb_helper(ids, {}, media_type, cache_only=True))
    sources = [s['id'] for s in S.list_available_meta_sources() if s.get('kind') == 'stremio']
    if provider_id in sources:
        sources.remove(provider_id)
        sources.insert(0, provider_id)
    for source in sources:
        if complete(out) and present(out.get('clearlogo') or out.get('logo')):
            break
        accept(S._fetch_from_stremio(source, media_type, ids, cache_only=True))

    from . import tmdb_direct as T
    query = dict(tmdb_id=ids.get('tmdb_id', ''), imdb_id=ids.get('imdb_id', ''),
                 media_type=media_type, title='', year='', language_override=language)
    if query['tmdb_id'] or query['imdb_id']:
        found = T.meta_for_cached_only(**query)
        if not cache_only and (not complete(out) or not (out.get('clearlogo') or out.get('logo'))):
            found = T.meta_for(**query, timeout=4, max_attempts=1)
        accept(found)
    # Only a focused/background lookup may use providers over the network.
    # At most two providers and one exact candidate per provider, never a
    # name search or an unbounded crawl of the user's installed sources.
    if not cache_only and provider_network and not complete(out):
        for source in sources[:2]:
            accept(S._fetch_from_stremio(source, media_type, ids, cache_only=False, max_candidates=1))
            if complete(out):
                break
    return out


def warm(metas, media_type, provider_id=''):
    """One bounded prefetch for a catalog the user opened, never a widget."""
    from . import profile_scope
    from .dexhub.client import run_parallel
    deadline = time.monotonic() + 4.0
    targets = [m for m in metas if not complete(m) and exact_ids(m)][:24]

    def one(meta):
        if time.monotonic() >= deadline:
            return None
        return enrich(meta, media_type, provider_id=provider_id, provider_network=False)

    if targets:
        run_parallel(profile_scope.capture(one), targets, workers=4, timeout=4.0, lane='browse')


def enrich_tile(tile, language='', cache_only=False, canonical='', provider_id='', force=False):
    """Bridge full metadata to Home/Spotlight/details, retaining episode facts."""
    params = dict(parse_qsl(urlsplit(str(tile.get('path') or '')).query))
    provider_id = provider_id or params.get('source_provider_id') or params.get('provider_id') or ''
    # v5.10.286: force=True fills only absent fields for the title page in
    # "Like Nuvio" mode (GitHub #7/#17); filled fields are never replaced
    if not force and not enabled(provider_id):
        return False
    episode = tile.get('media_type') == 'episode'
    media = 'series' if episode or _kind(tile.get('media_type')) == 'series' else 'movie'
    seed = dict(tile, id=canonical or params.get('canonical_id') or '', type=media,
                background=tile.get('fanart') or '')
    if episode:
        # A show's overview/runtime/year/rating must never become the episode's.
        for group in GROUPS[:5]:
            for key in group:
                seed.pop(key, None)
    found = enrich(seed, media, cache_only=cache_only, language=language, provider_id=provider_id)
    before = dict(tile)
    if not cache_only and exact_ids(seed) and not complete(found):
        tile['_auto_retry_at'] = time.time() + 180
    else:
        tile.pop('_auto_retry_at', None)
    fields = {'fanart': ('background', 'fanart', 'backdrop'), 'poster': ('poster',),
              'clearlogo': ('clearlogo', 'logo'), 'imdb_id': ('imdb_id',), 'tmdb_id': ('tmdb_id',),
              'seasons_count': ('number_of_seasons',), 'tv_status': ('status',),
              '_studio_rows': ('studio_logos',)}
    if not episode:
        fields.update({'plot': ('description', 'overview', 'plot'), 'genres': ('genres', 'genre'),
                       'year': ('year', 'releaseInfo', 'released', 'premiered'),
                       'rating': ('rating', 'imdbRating', 'voteAverage', 'score'), 'tagline': ('tagline',)})
    for target, aliases in fields.items():
        from .home_art import clean
        usable = clean if target in ('fanart', 'poster', 'clearlogo') else present
        if usable(tile.get(target)):
            continue
        value = next((found[k] for k in aliases if usable(found.get(k))), None)
        if value is None:
            continue
        if target == 'year':
            value = str(value)[:4]
        elif target == 'genres':
            value = value if isinstance(value, list) else [str(value)]
        elif target == 'rating':
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
        elif target == 'clearlogo' and 'image.tmdb.org' in str(value) and str(value).lower().endswith('.svg'):
            value = value[:-4] + '.png'
        tile[target] = value
    if not episode and not present(tile.get('duration')):
        try:
            runtime = re.match(r'^\s*(\d+)', str(found.get('runtime') or ''))
            tile['duration'] = int(found.get('duration') or 0) or (int(runtime.group(1)) * 60 if runtime else 0)
        except (TypeError, ValueError):
            pass
    return tile != before
