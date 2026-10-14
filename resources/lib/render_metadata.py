"""Final, cache-only policy shared by all standard Kodi media listings."""
from urllib.parse import parse_qsl, urlsplit
from . import meta_source, tmdbhelper
from .art import extract_ids

FIELDS = ('plot', 'plotoutline', 'tagline', 'genre', 'studio', 'country', 'cast',
          'director', 'writer', 'rating', 'votes', 'year', 'premiered', 'mpaa', 'originaltitle')
ALIASES = {'plot': ('plot', 'description', 'overview'), 'genre': ('genre', 'genres'),
           'studio': ('studio', 'studios', 'network'), 'rating': ('rating', 'imdbRating'),
           'mpaa': ('mpaa', 'certification')}


def cached(source, ids, kind):
    if source == 'tmdb_helper':
        return tmdbhelper.get_meta_bundle_from_db(tmdb_id=ids.get('tmdb_id') or '',
            imdb_id=ids.get('imdb_id') or '', media_type=kind) or {}
    data = meta_source._fetch_from_stremio(source, kind, ids, cache_only=True) or {}
    if not data:
        try:
            from . import metadata_warm
            metadata_warm.enqueue(source, ids, kind)
        except Exception:
            pass
    return data


def apply(info, art, ids=None, path=''):
    info, art = dict(info or {}), dict(art or {})
    addon = meta_source.ADDON
    source = addon.getSetting('global_meta_source_id') or ''
    unified = addon.getSetting('global_meta_source_all') == 'true'
    poster_source = addon.getSetting('global_poster_source_id') or 'same'
    if not (source and unified) and poster_source == 'same':
        return info, art
    params = dict(parse_qsl(urlsplit(str(path or '')).query))
    media = info.get('mediatype') or params.get('media_type') or ''
    if media not in ('movie', 'movies', 'tvshow', 'series', 'tv', 'show', 'anime', 'episode', 'season'):
        return info, art
    resolved = dict(ids or {})
    for key in ('tmdb_id', 'imdb_id', 'tvdb_id'):
        if not resolved.get(key) and params.get(key):
            resolved[key] = params[key]
    for key, value in extract_ids({'id': params.get('canonical_id') or ''}).items():
        if value and not resolved.get(key):
            resolved[key] = value
    if not any(resolved.get(k) for k in ('tmdb_id', 'imdb_id', 'tvdb_id')):
        return info, art  # Do not guess by title or borrow focused-item IDs.
    kind = 'movie' if media in ('movie', 'movies') else 'series'
    # Poster services commonly require IMDb, whereas Nuvio/server rows may
    # supply only TMDb. Resolve from the typed Helper cache, never by title.
    try:
        mapped = tmdbhelper.get_external_ids_from_db(
            tmdb_id=resolved.get('tmdb_id') or '', imdb_id=resolved.get('imdb_id') or '',
            tvdb_id=resolved.get('tvdb_id') or '', media_type=kind) or {}
        for key in ('tmdb_id', 'imdb_id', 'tvdb_id'):
            if not resolved.get(key) and mapped.get(key):
                resolved[key] = mapped[key]
    except Exception:
        pass
    original_art = dict(art)
    backup = None
    def fallback():
        nonlocal backup
        if backup is None:
            backup = cached('tmdb_helper', resolved, kind)
        return backup
    replacement = {}
    if unified and source not in ('', 'auto', 'native'):
        replacement = cached(source, resolved, kind)
        if not replacement:
            replacement = fallback()
        details = dict(replacement)
        if source == 'tmdb_helper' and media == 'episode':
            episode_details = tmdbhelper.get_episode_details_from_db(
                tmdb_id=resolved.get('tmdb_id') or '', imdb_id=resolved.get('imdb_id') or '',
                season=info.get('season', params.get('season', '')),
                episode=info.get('episode', params.get('episode', '')))
            details.update(episode_details)
            if episode_details.get('title'):
                info['title'] = episode_details['title']
            if replacement.get('title') or replacement.get('name'):
                info['tvshowtitle'] = replacement.get('title') or replacement['name']
        for field in FIELDS:
            if details:
                info.pop(field, None)
            for alias in ALIASES.get(field, (field,)):
                if details.get(alias) not in (None, '', [], {}):
                    info[field] = details[alias]
                    break
        info['_dexhub_policy_applied'] = True
        # Never turn a season/episode into its parent's title or runtime.
        if media not in ('episode', 'season') and (details.get('name') or details.get('title')):
            info['title'] = details.get('name') or details['title']
        if source == 'tmdb_helper' and kind == 'series' and replacement.get('network'):
            info['studio'] = replacement['network']
        for key in ('poster', 'tvshow.poster', 'season.poster', 'thumb', 'thumbnail',
                    'icon', 'image', 'cover', 'banner', 'clearart', 'tvshow.thumb',
                    'fanart', 'landscape', 'clearlogo', 'logo', 'tvshow.clearlogo'):
            art.pop(key, None)
        poster = replacement.get('poster') or fallback().get('poster') or original_art.get('poster') or original_art.get('thumb') or ''
        background = replacement.get('background') or replacement.get('fanart') or fallback().get('background') or fallback().get('fanart') or original_art.get('fanart') or ''
        logo = replacement.get('clearlogo') or replacement.get('logo') or fallback().get('clearlogo') or original_art.get('clearlogo') or ''
        art.update(poster=poster, thumb=poster, fanart=background, landscape=background,
                   clearlogo=logo, logo=logo)
    if poster_source not in ('', 'same'):
        info['_dexhub_poster_applied'] = True
        if poster_source == 'betterposters':
            from . import better_posters
            decorated = better_posters.apply(art, resolved.get('imdb_id') or '', kind, addon)
            poster = decorated.get('poster') or art.get('poster') or ''
        else:
            data = replacement if poster_source == source and replacement else cached(poster_source, resolved, kind)
            poster = data.get('poster') or fallback().get('poster') or art.get('poster') or original_art.get('poster') or ''
        for key in ('poster', 'tvshow.poster', 'season.poster', 'thumb', 'thumbnail'):
            art[key] = poster
    # Skin layouts use different portrait aliases for movie/show/episode
    # folders. Publish the same selected poster to all, not only `poster`.
    if art.get('poster') and (info.get('_dexhub_policy_applied') or info.get('_dexhub_poster_applied')):
        for key in ('tvshow.poster', 'season.poster', 'thumb', 'thumbnail', 'cover', 'image', 'icon', 'tvshow.thumb'):
            art[key] = art['poster']
    if info.get('_dexhub_policy_applied') or info.get('_dexhub_poster_applied'):
        # v5.10.288: the listing's own poster, before the choice replaced it:
        # a Home row that is re-dressed later (another poster source, or
        # back to the source's own) starts from it (skinui.rows._poster_pass)
        info['_dexhub_source_poster'] = original_art.get('poster') or original_art.get('thumb') or ''
    return info, art


# v5.10.288: the poster layer on its own, for cards already made (a Home
# row's saved copy, a tile of the add-on's Home): the same choice apply()
# makes, from the title's IDs and the listing's own poster. ``network``:
# a title the chosen source has not answered for yet is asked (a
# background pass only, never a listing); apply() reads caches only.
_NET_MISSES = {}        # source -> (misses in a row, monotonic time to wait until)


def _source_meta(source, ids, kind, network):
    """(meta dict, asked the network)."""
    if source == 'tmdb_helper':
        return cached('tmdb_helper', ids, kind), False
    data = meta_source._fetch_from_stremio(source, kind, ids, cache_only=True) or {}
    if data or not network:
        return data, False
    import time
    misses, until = _NET_MISSES.get(source, (0, 0.0))
    if until and time.monotonic() < until:
        return {}, False        # the source keeps failing: no more asks for a while
    data = meta_source._fetch_from_stremio(source, kind, ids, cache_only=False, max_candidates=2) or {}
    if data:
        _NET_MISSES.pop(source, None)
    else:
        misses += 1
        _NET_MISSES[source] = (misses, time.monotonic() + 120.0 if misses >= 4 else 0.0)
    return data, True


def poster_choice_active():
    """v5.10.288: a poster source or a unified metadata source replaces posters."""
    addon = meta_source.ADDON
    source = addon.getSetting('global_meta_source_id') or ''
    unified = addon.getSetting('global_meta_source_all') == 'true'
    poster_source = addon.getSetting('global_poster_source_id') or 'same'
    return poster_source not in ('', 'same') or (unified and source not in ('', 'auto', 'native'))


def poster_for(ids, media_type, source_poster='', network=False):
    """v5.10.288: (poster, asked the network) for one title under the choice
    in the settings now; ``source_poster`` is the listing's own poster."""
    addon = meta_source.ADDON
    source = addon.getSetting('global_meta_source_id') or ''
    unified = addon.getSetting('global_meta_source_all') == 'true'
    poster_source = addon.getSetting('global_poster_source_id') or 'same'
    use_meta = unified and source not in ('', 'auto', 'native')
    if poster_source in ('', 'same') and not use_meta:
        return source_poster or '', False
    kind = 'movie' if str(media_type or '') in ('movie', 'movies') else 'series'
    resolved = {}
    for key in ('tmdb_id', 'imdb_id', 'tvdb_id'):
        value = str((ids or {}).get(key) or '').strip()
        if value:
            resolved[key] = value
    if not any(resolved.values()):
        return source_poster or '', False
    try:
        mapped = tmdbhelper.get_external_ids_from_db(
            tmdb_id=resolved.get('tmdb_id') or '', imdb_id=resolved.get('imdb_id') or '',
            tvdb_id=resolved.get('tvdb_id') or '', media_type=kind) or {}
        for key in ('tmdb_id', 'imdb_id', 'tvdb_id'):
            if not resolved.get(key) and mapped.get(key):
                resolved[key] = mapped[key]
    except Exception:
        pass
    asked = False
    backup = {}

    def fallback():
        if 'v' not in backup:
            backup['v'] = cached('tmdb_helper', resolved, kind) or {}
        return backup['v']
    base, replacement = source_poster or '', {}
    if use_meta:
        replacement, net = _source_meta(source, resolved, kind, network)
        asked = asked or net
        base = replacement.get('poster') or fallback().get('poster') or base
    if poster_source in ('', 'same'):
        return base, asked
    if poster_source == 'betterposters':
        from . import better_posters
        decorated = better_posters.apply({'poster': base}, resolved.get('imdb_id') or '', kind, addon) or {}
        return decorated.get('poster') or base, asked
    if poster_source == source and replacement:
        data = replacement
    else:
        data, net = _source_meta(poster_source, resolved, kind, network)
        asked = asked or net
    return data.get('poster') or fallback().get('poster') or base, asked
