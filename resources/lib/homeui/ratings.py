# -*- coding: utf-8 -*-
"""Ratings with their logos for the Home hero and the title page (v5.10.105).

Sources, cheapest first: TMDb Helper's cache (the MDbList, OMDb, Trakt and
TMDb ratings it already fetched for the title), Dex Hub's own MDbList memory,
and only when asked (the title under the cursor, or an opened title page) and
an MDbList key is set, one MDbList request. Values are display strings in
the same scales as the sources window: IMDb, TMDb, Trakt and MAL out of 10,
Letterboxd out of 5, Rotten Tomatoes, Metacritic and MDbList out of 100.

v5.10.107: each source shows its own logo (resources/media/ratings, drawn by
gen_ratings.py): TMDb's score the way TMDb draws it, a ring around the
percentage; Rotten Tomatoes a fresh tomato or a green splat and a full or a
spilled popcorn bucket, as Rotten Tomatoes does (60% and up is fresh);
Metacritic its score in a green, yellow or red box; Trakt as a percentage.
"""

ORDER = ('imdb', 'tmdb', 'rt_crit', 'rt_aud', 'metacritic', 'trakt', 'letterboxd', 'mdblist', 'mal')
LIMIT = 6
PERCENT = ('rt_crit', 'rt_aud')
FRESH = 60
# Metacritic's own bands: 61 and up green, 40 to 60 yellow, below 40 red
MC_COLORS = ((61, 'FF66CC33'), (40, 'FFFFCC33'), (0, 'FFFF3B30'))


def collect(tmdb_id='', imdb_id='', media_type='movie', network=False):
    """{key: value} for the title, in ORDER, from the sources above."""
    from ..ui_preferences import enabled
    if not enabled('metadata_badges'):
        return {}
    tmdb_id = str(tmdb_id or '').strip()
    imdb_id = str(imdb_id or '').strip()
    if not (tmdb_id or imdb_id):
        return {}
    found = {}
    try:
        from .. import tmdbhelper
        found.update(tmdbhelper.get_ratings_from_db(
            tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=media_type) or {})
    except Exception:
        pass
    try:
        from .. import mdblist
        extra = mdblist.cached_ratings(imdb_id=imdb_id, media_type=media_type, tmdb_id=tmdb_id) or {}
        if not extra and network and len(found) < 3 and mdblist.configured():
            extra = mdblist.fetch_ratings(imdb_id=imdb_id, media_type=media_type,
                                          tmdb_id=tmdb_id, timeout=6) or {}
        for key, value in extra.items():
            if value and not found.get(key):
                found[key] = value
    except Exception:
        pass
    return dict((key, str(found[key])) for key in ORDER if found.get(key))


def _number(text):
    try:
        return float(str(text or '').strip().rstrip('%').strip())
    except Exception:
        return None


def props(ratings, prefix='dh.rating.', limit=LIMIT):
    """Window properties: one per rating, the first ``limit`` of ORDER shown,
    and what their logos need (the ring's percentage, fresh or rotten, the
    Metacritic box colour)."""
    ratings = ratings or {}
    # a score the page cannot draw ("N/A") is not shown and takes no place
    shown = [key for key in ORDER if ratings.get(key) and _number(ratings[key]) is not None][:limit]
    out = {}
    for key in ORDER:
        value = str(ratings.get(key) or '').strip() if key in shown else ''
        number = _number(value)
        if value and key in PERCENT and not value.endswith('%'):
            value += '%'
        if value and key == 'trakt' and not value.endswith('%'):
            # Trakt shows its score as a percentage
            value = '%d%%' % int(round(number * 10 if number <= 10 else number))
        out[prefix + key] = value
    tmdb = _number(out[prefix + 'tmdb'])
    out[prefix + 'tmdb.pct'] = str(max(0, min(100, int(round(tmdb * 10))))) if tmdb is not None else ''
    for key, fresh_icon, rotten_icon in (('rt_crit', 'rt_fresh.png', 'rt_rotten.png'),
                                         ('rt_aud', 'rt_pop_fresh.png', 'rt_pop_rotten.png')):
        number = _number(out[prefix + key])
        fresh = number is not None and number >= FRESH
        out[prefix + key + '.fresh'] = '1' if fresh else ''
        out[prefix + key + '.icon'] = (fresh_icon if fresh else rotten_icon) if number is not None else ''
    number = _number(out[prefix + 'metacritic'])
    color = ''
    if number is not None:
        color = next((c for floor, c in MC_COLORS if number >= floor), MC_COLORS[-1][1])
        out[prefix + 'metacritic'] = str(int(round(number)))
    out[prefix + 'metacritic.color'] = color
    out[prefix + 'metacritic.dark'] = '1' if color == 'FFFFCC33' else ''
    out[prefix + 'any'] = '1' if shown else ''
    return out
