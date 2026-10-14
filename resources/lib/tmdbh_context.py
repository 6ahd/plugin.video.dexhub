# -*- coding: utf-8 -*-
"""
TMDb Helper live-context bridge.

When DexHub is launched from a skin that uses TMDb Helper (Arctic Fuse 3,
Arctic Horizon, etc.), TMDb Helper's monitor service publishes the *currently
focused* item's metadata onto the Home window as
``TMDbHelper.ListItem.*`` properties.

This module reads that live context — gated by a matching TMDb id so we never
pick up a stale neighbour — and:

  1. Back-fills any artwork DexHub is still missing (clearlogo / fanart).
  2. Extracts the aggregate rating set (IMDb / TMDb / Trakt / Rotten Tomatoes
     critics + audience) and republishes it under the ``dexhub.rating.*``
     namespace so the sources dialog can show the same ratings row the skin's
     info screen shows.

Everything is best-effort: missing properties simply produce empty strings and
the dialog hides the corresponding chip.
"""

import xbmc
import xbmcgui


HOME = xbmcgui.Window(10000)
_NS = 'TMDbHelper.ListItem.'


def _prop(name):
    try:
        return (HOME.getProperty(_NS + name) or '').strip()
    except Exception:  # pylint: disable=broad-except
        return ''


def _first(names):
    """Return the first non-empty property among several candidate names.

    TMDb Helper's exact rating property names vary a little between versions,
    so we probe a few aliases for each rating source.
    """
    for n in names:
        v = _prop(n)
        if v:
            return v
    return ''


# Candidate property names per rating source (version-tolerant).
_RATING_ALIASES = {
    'imdb':    ['IMDb_Rating', 'imdb_rating', 'Rating.IMDb'],
    'tmdb':    ['Rating', 'TMDb_Rating', 'themoviedb_rating'],
    'trakt':   ['Trakt_Rating', 'trakt_rating'],
    'rt_crit': ['RottenTomatoes_Rating', 'Tomatometer', 'rottentomatoes_rating'],
    'rt_aud':  ['RottenTomatoes_AudienceRating', 'Audience_Rating',
                'rottentomatoes_audiencerating'],
    'metacritic': ['Metacritic_Rating', 'metacritic_rating'],
}

# Artwork properties TMDb Helper may expose on the Home window.
_ART_ALIASES = {
    'clearlogo': ['Art(clearlogo)', 'Art(tvshow.clearlogo)', 'clearlogo'],
    'fanart':    ['Art(fanart)', 'fanart'],
    'poster':    ['Art(poster)', 'poster'],
}


def _normalise_rating(value, scale10=True):
    """Clean a rating string. Returns '' if not a usable number.

    scale10=True formats to one decimal on a 0-10 scale (IMDb/TMDb/Trakt).
    For RT (percentages) we keep the integer and append nothing here — the
    dialog adds the % glyph.
    """
    if not value:
        return ''
    v = value.strip().replace('%', '')
    try:
        f = float(v)
    except ValueError:
        return ''
    if f <= 0:
        return ''
    if scale10:
        # Some sources give 0-100; bring to 0-10.
        if f > 10.0:
            f = f / 10.0
        return '%.1f' % f
    # percentage style
    if f <= 1.0:
        f *= 100.0
    return str(int(round(f)))


def read_live_context(expected_tmdb_id=None, expected_imdb_id=None,
                      fetch_mdblist_ratings=False):
    """Return a dict of live context, or {} if it doesn't match our item.

    Keys: monitor_tmdb_id, monitor_type, art{clearlogo,fanart,poster},
    ratings{imdb,tmdb,trakt,rt_crit,rt_aud,metacritic}
    """
    mon_id = _prop('Monitor.TMDb_ID')
    mon_type = _prop('Monitor.TMDb_Type')

    # Gate on TMDb id match when we know what we expect. If TMDb Helper has no
    # monitor id at all, we still allow a soft match via imdb id below.
    if not (expected_tmdb_id and mon_id and str(mon_id) == str(expected_tmdb_id)):
        # Try IMDb fallback before giving up
        live_imdb = _prop('IMDb')
        if not (expected_imdb_id and live_imdb
                and str(live_imdb) == str(expected_imdb_id)):
            return {}

    art = {}
    for key, aliases in _ART_ALIASES.items():
        val = _first(aliases)
        if val:
            art[key] = val

    ratings = {}
    for key, aliases in _RATING_ALIASES.items():
        raw = _first(aliases)
        # v4.3.1: Metacritic is a 0-100 score like RT — dividing it to 8.2
        # made the chip read as a 0-10 rating (the screenshot bug).
        is_pct = key in ('rt_crit', 'rt_aud', 'metacritic')
        norm = _normalise_rating(raw, scale10=not is_pct)
        if norm:
            ratings[key] = norm

    # Never make an auxiliary ratings request while the source picker opens.
    # Reuse a warm MDBList result when another route already fetched it; an
    # explicit caller may opt into a network lookup outside the hot path.
    # v4.6.3: keyed by tmdb id first (TMDb Helper's own MDBList path) — the
    # old imdb-only gate skipped the overlay for every title whose IMDb
    # resolution is deferred (v4.5.0 defers it until the item opens).
    try:
        imdb_for_ratings = str(expected_imdb_id or _prop('IMDb') or '').strip()
        tmdb_for_ratings = str(expected_tmdb_id or mon_id or '').strip()
        if imdb_for_ratings or tmdb_for_ratings:
            from . import mdblist as _mdblist
            if fetch_mdblist_ratings:
                extra = _mdblist.fetch_ratings(
                    imdb_id=imdb_for_ratings, media_type=(mon_type or 'movie'),
                    tmdb_id=tmdb_for_ratings) or {}
            else:
                extra = _mdblist.cached_ratings(
                    imdb_id=imdb_for_ratings, media_type=(mon_type or 'movie'),
                    tmdb_id=tmdb_for_ratings) or {}
            for key, val in extra.items():
                if val and not ratings.get(key):
                    ratings[key] = val
    except Exception:
        pass

    return {
        'monitor_tmdb_id': mon_id,
        'monitor_type': mon_type,
        'art': art,
        'ratings': ratings,
    }



_RESUME_ALIASES = {
    'position': ['ResumeTime', 'Resume.Time', 'Player.ResumeTime', 'Progress.Time', 'Resume'],
    'duration': ['TotalTime', 'DurationSeconds', 'Player.Duration', 'Duration'],
    'percent': ['PercentPlayed', 'Progress', 'ResumePercent', 'Player.Progress'],
    'updated_at': ['LastPlayed', 'LastPlayedAt', 'PausedAt'],
}

def _as_float(value):
    try:
        text = str(value or '').strip().replace('%', '')
        if not text:
            return 0.0
        # Kodi can expose HH:MM:SS / MM:SS rather than seconds.
        if ':' in text:
            parts = [float(x or 0) for x in text.split(':')]
            total = 0.0
            for part in parts:
                total = total * 60.0 + part
            return total
        return float(text)
    except Exception:
        return 0.0

def _info_label_first(names):
    for name in names:
        try:
            value = (xbmc.getInfoLabel(name) or '').strip()
        except Exception:
            value = ''
        if value:
            return value
    return ''

def read_resume_hint(expected_tmdb_id=None, expected_imdb_id=None, season=None, episode=None):
    """Read the live TMDb Helper/Kodi resume point before focus is lost.

    TMDb Helper player URLs do not reliably include resume seconds. Its monitor
    service and the currently focused Kodi ListItem often do. We gate the hint
    by IDs and episode numbers when those values are available, preventing a
    stale neighbouring widget item from leaking its progress into playback.
    """
    ctx = read_live_context(expected_tmdb_id=expected_tmdb_id, expected_imdb_id=expected_imdb_id)
    if not ctx and (expected_tmdb_id or expected_imdb_id):
        # A missing monitor id is common on some skins. Validate against the
        # focused ListItem unique IDs before allowing the InfoLabel fallback.
        live_tmdb = _info_label_first(['ListItem.UniqueID(tmdb)', 'ListItem.Property(tmdb_id)'])
        live_imdb = _info_label_first(['ListItem.UniqueID(imdb)', 'ListItem.IMDBNumber'])
        if expected_tmdb_id and live_tmdb and str(live_tmdb) != str(expected_tmdb_id):
            return {}
        if expected_imdb_id and live_imdb and str(live_imdb).lower() != str(expected_imdb_id).lower():
            return {}

    try:
        wanted_s, wanted_e = int(season or 0), int(episode or 0)
    except Exception:
        wanted_s, wanted_e = 0, 0
    live_s = int(_as_float(_info_label_first(['ListItem.Season'])))
    live_e = int(_as_float(_info_label_first(['ListItem.Episode'])))
    if wanted_s and live_s and wanted_s != live_s:
        return {}
    if wanted_e and live_e and wanted_e != live_e:
        return {}

    position = _as_float(_first(_RESUME_ALIASES['position']))
    duration = _as_float(_first(_RESUME_ALIASES['duration']))
    percent = _as_float(_first(_RESUME_ALIASES['percent']))
    if position <= 0:
        position = _as_float(_info_label_first([
            'ListItem.ResumeTime', 'ListItem.Property(ResumeTime)',
            'ListItem.Property(resumetime)',
        ]))
    if duration <= 0:
        duration = _as_float(_info_label_first([
            'ListItem.Duration', 'ListItem.Property(TotalTime)',
            'ListItem.Property(DurationSeconds)',
        ]))
    if percent <= 0:
        percent = _as_float(_info_label_first([
            'ListItem.PercentPlayed', 'ListItem.Property(PercentPlayed)',
            'ListItem.Property(progress)',
        ]))
    if percent <= 0 and position > 0 and duration > 0:
        percent = position * 100.0 / duration
    if position <= 0 and percent > 0 and duration > 0:
        position = duration * percent / 100.0
    if position <= 30.0 or percent >= 95.0:
        return {}
    return {
        'resume_seconds': position,
        'resume_percent': percent if 0 < percent < 95 else 0.0,
        'duration': duration,
        'updated_at': 0,
        'source': 'tmdbhelper',
    }


def apply_to_meta(meta, ctx):
    """Back-fill missing artwork in `meta` from live context. Mutates + returns."""
    if not ctx:
        return meta
    art = ctx.get('art') or {}
    if not meta.get('clearlogo') and art.get('clearlogo'):
        meta['clearlogo'] = art['clearlogo']
        meta['logo'] = art['clearlogo']
    if not (meta.get('fanart') or meta.get('background')) and art.get('fanart'):
        meta['fanart'] = art['fanart']
        meta['background'] = art['fanart']
    if not meta.get('poster') and art.get('poster'):
        meta['poster'] = art['poster']
    return meta


def publish_ratings(window, ctx):
    """Publish the rating set as dexhub.rating.* on the given window + Home.

    Also sets dexhub.rating.any = '1' when at least one rating exists, so the
    dialog can show/hide the whole ratings row with one condition.
    """
    ratings = (ctx or {}).get('ratings') or {}
    targets = [HOME]
    if window is not None and window is not HOME:
        targets.append(window)

    # v5.10.107: the same values and logo hints as the Home's hero (the
    # ring's percentage, fresh or rotten, the Metacritic box colour), every
    # source shown: the sources window has room for all of them
    try:
        from .homeui import ratings as _rb
        values = _rb.props(ratings, prefix='dexhub.rating.', limit=len(_rb.ORDER))
    except Exception:  # pylint: disable=broad-except
        values = dict(('dexhub.rating.%s' % k, str(ratings.get(k, '') or ''))
                      for k in ('imdb', 'tmdb', 'trakt', 'rt_crit', 'rt_aud', 'metacritic',
                                'letterboxd', 'mdblist', 'mal'))
        values['dexhub.rating.any'] = '1' if ratings else ''
    any_rating = values.get('dexhub.rating.any') == '1'
    for win in targets:
        try:
            for name, val in values.items():
                win.setProperty(name, val)
        except Exception:  # pylint: disable=broad-except
            pass
    return any_rating


def enrich(window, meta, log=None, fetch_mdblist_ratings=False):
    """One-shot: read live context, back-fill art, publish ratings.

    Returns the (possibly mutated) meta dict.
    """
    try:
        tmdb_id = str((meta or {}).get('tmdb_id') or '').strip()
        imdb_id = str((meta or {}).get('imdb_id') or '').strip()
        # One local lookup at window construction, never on focus/selection.
        # Only descriptive fields cross this bridge: playback IDs, episode
        # coordinates, runtime and resume state remain owned by the caller.
        from . import tmdbhelper, meta_source
        source = (meta or {}).get('_dexhub_meta_source_id') or meta_source.get_meta_source_for((meta or {}).get('provider_id') or '')
        if source not in ('auto', 'tmdb_helper', ''):
            publish_ratings(window, {})
            return meta
        if source in ('auto', 'tmdb_helper', '') and (tmdb_id or imdb_id):
            cached = tmdbhelper.get_meta_bundle_from_db(
                tmdb_id=tmdb_id, imdb_id=imdb_id,
                media_type=(meta or {}).get('media_type') or (meta or {}).get('type') or 'movie')
            meta = dict(meta or {})
            for key in ('plot', 'description', 'overview', 'studio', 'studios', 'genre', 'genres', 'cast', 'director', 'writer', 'country', 'clearlogo', 'logo', 'poster', 'fanart', 'background'):
                if cached.get(key) and (source == 'tmdb_helper' or not meta.get(key)):
                    meta[key] = cached[key]
            if source == 'tmdb_helper':
                meta['_dexhub_helper_cache_only'] = True
        ctx = read_live_context(expected_tmdb_id=tmdb_id or None,
                                expected_imdb_id=imdb_id or None,
                                fetch_mdblist_ratings=fetch_mdblist_ratings)
        expected_type = (meta or {}).get('media_type') or (meta or {}).get('type')
        if ctx and expected_type and ctx.get('monitor_type'):
            if tmdbhelper._normalize_media_type(expected_type) != tmdbhelper._normalize_media_type(ctx['monitor_type']):
                ctx = {}
        # v5.10.105: the ratings TMDb Helper already fetched for this title
        # (MDbList, OMDb, Trakt) fill what the live context lacks, and stand
        # in for it when Dex Hub was not opened from TMDb Helper.
        if tmdb_id or imdb_id:
            try:
                stored = tmdbhelper.get_ratings_from_db(
                    tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=expected_type or 'movie') or {}
            except Exception:
                stored = {}
            if stored:
                ctx = dict(ctx or {'art': {}})
                ratings = dict(ctx.get('ratings') or {})
                for key, value in stored.items():
                    if value and not ratings.get(key):
                        ratings[key] = value
                ctx['ratings'] = ratings
        if not ctx:
            # Clear any stale ratings from a previous item.
            publish_ratings(window, {})
            return meta
        meta = apply_to_meta(meta, ctx)
        has = publish_ratings(window, ctx)
        if log:
            log('tmdbh_context: ratings=%s art=%s'
                % (list((ctx.get('ratings') or {}).keys()),
                   list((ctx.get('art') or {}).keys())))
        return meta
    except Exception as exc:  # pylint: disable=broad-except
        if log:
            try:
                log('tmdbh_context error: %s' % exc)
            except Exception:  # pylint: disable=broad-except
                pass
        return meta
