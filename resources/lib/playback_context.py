"""Shared playback_context. No imports or side effects until called.

The caller supplies Kodi and compatibility adapters; Home/catalog code does not
belong here. Relative feature imports remain lazy and retain package semantics.
"""

def playback_art_from_ctx(api, ctx):
    ctx = ctx or {}
    poster = ctx.get('poster') or ctx.get('thumb') or ''
    fanart = ctx.get('background') or ctx.get('fanart') or poster or api.neutral_fanart()
    clearlogo = ctx.get('clearlogo') or ctx.get('logo') or ''
    landscape = ctx.get('landscape') or fanart
    clearart = ctx.get('clearart') or ''
    banner = ctx.get('banner') or ''

    # Kodi 22/CoreELEC safe mode: keep only standard artwork keys on the
    # playback item. The source picker can keep richer tvshow/season aliases,
    # but those aliases have caused native skin/player reloads on Piers alpha
    # when handed directly to VideoPlayer.
    if api._safe_playback_handoff_enabled():
        art = {
            'thumb': poster,
            'poster': poster,
            'icon': poster,
            'thumbnail': poster,
            'fanart': fanart,
            'fanart_image': fanart,
            'landscape': landscape,
            'clearlogo': clearlogo,
            'logo': clearlogo,
            'clearart': clearart,
            'banner': banner,
        }
        return {k: v for k, v in art.items() if v}

    art = {
        'thumb': poster, 'poster': poster, 'icon': poster, 'thumbnail': poster,
        'fanart': fanart, 'fanart_image': fanart, 'landscape': landscape,
        'clearlogo': clearlogo, 'logo': clearlogo, 'clearart': clearart, 'banner': banner,
        # TV-show aliases are important for Arctic Fuse/AZ skins and for
        # TMDb Helper-style player monitors that prefer tvshow.* art while
        # playing episodes.
        'tvshow.clearlogo': clearlogo, 'tvshow.clearart': clearart,
        'tvshow.poster': poster, 'tvshow.thumb': poster,
        'tvshow.fanart': fanart, 'tvshow.landscape': landscape,
        'season.poster': poster, 'season.thumb': poster,
    }
    return {k: v for k, v in art.items() if v}


def lookup_local_clearlogo_for_playback(api, ctx):
    """Fast local clearlogo repair for the player handoff.

    DPlex resolves the player clearlogo from TMDb Helper's local database right
    before building the playback ListItem.  Do the same here, but keep it
    strictly local/read-only so selecting a source never waits on fanart.tv or
    other network calls.  This fixes the common Kodi 22/Arctic Fuse case where
    the source window has enough IDs but the cached stream payload has an empty
    clearlogo, so the fullscreen player shows only poster/fanart.
    """
    ctx = ctx or {}
    if ctx.get('clearlogo') or ctx.get('logo'):
        return ctx.get('clearlogo') or ctx.get('logo') or ''
    try:
        ids = api._ids_from_playback_ctx(ctx)
    except Exception:
        ids = {}
    tmdb_id = str(ids.get('tmdb_id') or ctx.get('tmdb_id') or '').strip()
    imdb_id = str(ids.get('imdb_id') or ctx.get('imdb_id') or '').strip()
    if not (tmdb_id or imdb_id):
        return ''
    media_type = 'tv' if api._tmdb_type_from_ctx(ctx) == 'tv' else 'movie'
    try:
        from .tmdbhelper import get_clearlogo_from_db
        return get_clearlogo_from_db(tmdb_id=tmdb_id, media_type=media_type, imdb_id=imdb_id) or ''
    except Exception:
        return ''


def ensure_playback_art_context(api, ctx):
    """Complete only missing player artwork without changing the chosen source.

    We intentionally repair clearlogo only.  Posters/fanart can be decorated
    proxy URLs from ERDB/Plexio and should not be swapped at playback time.
    """
    out = dict(ctx or {})
    logo = out.get('clearlogo') or out.get('logo') or api._lookup_local_clearlogo_for_playback(out)
    if logo:
        out['clearlogo'] = logo
        out['logo'] = logo
    return out


def normalise_imdb_id_value(api, value):
    value = str(value or '').strip()
    if not value:
        return ''
    if value.lower().startswith('imdb:'):
        value = value.split(':', 1)[1].strip()
    if value.isdigit():
        value = 'tt%s' % value
    return value if api.re.match(r'^tt\d{5,10}$', value, api.re.I) else value


def seed_ids_from_values(api, tmdb_id='', imdb_id='', tvdb_id='', canonical_id=''):
    """Normalize explicit route IDs from URLs/context into DexHub's *_id dict."""
    ids = {
        'tmdb_id': str(tmdb_id or '').strip(),
        'imdb_id': api._normalise_imdb_id_value(imdb_id),
        'tvdb_id': str(tvdb_id or '').strip(),
    }
    ids = api._merge_seed_ids(ids, api.extract_ids({'id': canonical_id or ''}))
    return {k: v for k, v in ids.items() if v}


def ids_from_playback_ctx(api, ctx):
    ctx = ctx or {}
    ids = api._seed_ids_from_values(
        tmdb_id=ctx.get('tmdb_id') or '',
        imdb_id=ctx.get('imdb_id') or '',
        tvdb_id=ctx.get('tvdb_id') or '',
        canonical_id=ctx.get('canonical_id') or '',
    )
    ext = ctx.get('external_ids') or {}
    if isinstance(ext, dict):
        ids = api._merge_seed_ids(ids, {
            'tmdb_id': ext.get('tmdb_id') or ext.get('tmdb') or '',
            'imdb_id': api._normalise_imdb_id_value(ext.get('imdb_id') or ext.get('imdb') or ''),
            'tvdb_id': ext.get('tvdb_id') or ext.get('tvdb') or '',
        })
    # Some episode stream ids arrive as tt1234567:1:2. Keep the show IMDb id.
    for key in ('video_id', 'stream_url'):
        text = str(ctx.get(key) or '')
        if not ids.get('imdb_id'):
            m = api.re.search(r'(tt\d{5,10})', text, api.re.I)
            if m:
                ids['imdb_id'] = m.group(1)
    return ids


def ensure_playback_ids(api, ctx):
    """Best-effort final ID repair before Player.play/setResolvedUrl.

    a4kSubtitles can read IMDb from Kodi's player metadata and/or from the
    video file URL. Stream collection already tries to resolve IDs, but this
    final guard recovers IDs lost by older source-picker routes or quick-open
    paths. It is intentionally quiet and never blocks playback on failure.
    """
    out = dict(ctx or {})
    ids = api._ids_from_playback_ctx(out)
    if not ids.get('imdb_id') and (ids.get('tmdb_id') or out.get('title') or out.get('show_title')):
        try:
            probe_meta = {
                'id': out.get('canonical_id') or '',
                'name': out.get('show_title') or out.get('title') or '',
                'title': out.get('show_title') or out.get('title') or '',
                'type': 'series' if out.get('season') or out.get('episode') or api._is_series_media(out.get('media_type')) else 'movie',
            }
            ids = api._enrich_stream_ids(ids, probe_meta.get('type') or 'movie', meta=probe_meta, canonical_id=out.get('canonical_id') or '', title=probe_meta.get('title') or '', network=False)
        except Exception:
            pass
    for key in ('tmdb_id', 'imdb_id', 'tvdb_id'):
        if ids.get(key) and not out.get(key):
            out[key] = ids.get(key)
    is_episode = bool(
        out.get('season') not in (None, '', 0, '0') or
        out.get('episode') not in (None, '', 0, '0') or
        str(out.get('media_type') or '').lower() in ('episode', 'episodes'))
    if ids:
        ext = dict(out.get('external_ids') or {}) if isinstance(out.get('external_ids') or {}, dict) else {}
        if ids.get('imdb_id'):
            ext.setdefault('imdb', ids.get('imdb_id'))
            ext.setdefault('imdb_id', ids.get('imdb_id'))
        if ids.get('tmdb_id'):
            ext.setdefault('tmdb', ids.get('tmdb_id'))
            ext.setdefault('tmdb_id', ids.get('tmdb_id'))
        if ids.get('tvdb_id'):
            ext.setdefault('tvdb', ids.get('tvdb_id'))
            ext.setdefault('tvdb_id', ids.get('tvdb_id'))
        if is_episode:
            # Prefer explicit parent-show ids supplied by Plex/Emby. Older
            # Stremio/TMDb Helper routes historically put the show ids in the
            # generic fields, which remain a compatibility fallback.
            for key in ('imdb_id', 'tmdb_id', 'tvdb_id'):
                show_key = 'show_%s' % key
                show_value = (out.get(show_key) or ext.get(show_key) or
                              out.get(key) or ids.get(key) or '')
                if show_value:
                    out[show_key] = show_value
                    ext.setdefault(show_key, show_value)
            if out.get('show_tmdb_id'):
                ext.setdefault('tmdbshow', out.get('show_tmdb_id'))
                ext.setdefault('tmdb_show', out.get('show_tmdb_id'))
        out['external_ids'] = ext
    return out
