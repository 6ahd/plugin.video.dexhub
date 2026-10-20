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
# v5.10.334: the names in the picker of the ratings shown
NAMES = {'imdb': 'IMDb', 'tmdb': 'TMDb', 'rt_crit': 'Rotten Tomatoes', 'rt_aud': 'Popcornmeter',
         'metacritic': 'Metacritic', 'trakt': 'Trakt', 'letterboxd': 'Letterboxd', 'mdblist': 'MDBList',
         'mal': 'MyAnimeList'}


def chosen(saved=None):
    """v5.10.334: the ratings the user shows, in ORDER (all of them when the
    setting is empty or names none we know)."""
    if saved is None:
        try:
            from ..settings_cache import cached_addon
            saved = cached_addon().getSetting('ratings_shown')
        except Exception:
            saved = ''
    keys = [k for k in ORDER if k in str(saved or '').split(',')]
    return keys or list(ORDER)


def pick(tr=None):
    """The picker (the Control Centre): any of the nine, at least one."""
    import xbmcgui
    tr = tr or (lambda text: text)
    current = chosen()
    picked = xbmcgui.Dialog().multiselect(tr('التقييمات الظاهرة'), [NAMES[k] for k in ORDER],
                                          preselect=[i for i, k in enumerate(ORDER) if k in current])
    if picked is None:
        return False
    keys = [ORDER[i] for i in picked]
    value = '' if not keys or len(keys) == len(ORDER) else ','.join(keys)
    import xbmcaddon
    from ..settings_cache import invalidate
    xbmcaddon.Addon().setSetting('ratings_shown', value)
    invalidate()
    return True
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
        if not extra and network and len(found) < 3 and mdblist.ratings_key():
            extra = mdblist.fetch_ratings(imdb_id=imdb_id, media_type=media_type,
                                          tmdb_id=tmdb_id, timeout=6) or {}
        for key, value in extra.items():
            if value and not found.get(key):
                found[key] = value
    except Exception:
        pass
    if network and len([k for k in ORDER if found.get(k)]) < 4:
        try:
            for key, value in free_ratings(tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=media_type,
                                           have=found).items():
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
    allowed = chosen()     # v5.10.334: the ratings the user chose
    shown = [key for key in ORDER if key in allowed and ratings.get(key)
             and _number(ratings[key]) is not None][:limit]
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


# v5.10.301: ratings without an MDBList key of Dex Hub's own. The Home hero
# showed none for most titles: its only sources were TMDb Helper's local DB
# (titles TMDb Helper had looked up) and MDBList (a key few set). Now, in this
# order, until the row is full: TMDb's own score, IMDb's from Cinemeta (no
# key), and Rotten Tomatoes and Metacritic from OMDb or MDBList with the key
# TMDb Helper already has.
_FREE_CACHE = {}
_FREE_MAX = 500


def _json(url, timeout=4):
    import json as _j
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': 'DexHub (Kodi)', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return _j.loads(response.read(512 * 1024).decode('utf-8', 'replace'))


def _helper_key(name):
    try:
        import xbmcaddon
        return (xbmcaddon.Addon('plugin.video.themoviedb.helper').getSetting(name) or '').strip()
    except Exception:
        return ''


def free_ratings(tmdb_id='', imdb_id='', media_type='movie', have=None):
    """{key: value} from the keyless sources (and TMDb Helper's own keys)."""
    have = dict(have or {})
    series = str(media_type or '').lower() in ('series', 'tv', 'tvshow', 'show')
    key = '%s|%s|%s' % ('tv' if series else 'movie', tmdb_id, imdb_id)
    if key in _FREE_CACHE:
        return dict(_FREE_CACHE[key])
    out = {}
    if not imdb_id and tmdb_id:
        try:
            from .. import tmdb_direct
            imdb_id = tmdb_direct.imdb_id_for(tmdb_id, 'tv' if series else 'movie', timeout=4) or ''
        except Exception:
            imdb_id = ''
    if not have.get('tmdb') and tmdb_id:
        try:
            from .. import tmdb_direct
            data = tmdb_direct._request('/%s/%s' % ('tv' if series else 'movie', tmdb_id), timeout=4,
                                        max_attempts=1) or {}
            score = float(data.get('vote_average') or 0)
            if score > 0 and int(data.get('vote_count') or 0) >= 5:
                out['tmdb'] = '%.1f' % score
        except Exception:
            pass
    if not have.get('imdb') and str(imdb_id).startswith('tt'):
        try:
            meta = (_json('https://v3-cinemeta.strem.io/meta/%s/%s.json'
                          % ('series' if series else 'movie', imdb_id)) or {}).get('meta') or {}
            score = float(str(meta.get('imdbRating') or 0).replace(',', '.') or 0)
            if score > 0:
                out['imdb'] = '%.1f' % score
        except Exception:
            pass
    wants = [k for k in ('rt_crit', 'metacritic', 'imdb') if not have.get(k) and not out.get(k)]
    omdb = _helper_key('omdb_apikey') if wants and str(imdb_id).startswith('tt') else ''
    if omdb:
        try:
            import urllib.parse
            data = _json('https://www.omdbapi.com/?' + urllib.parse.urlencode({'i': imdb_id, 'apikey': omdb})) or {}
            for row in data.get('Ratings') or []:
                source, value = str(row.get('Source') or ''), str(row.get('Value') or '')
                if source == 'Rotten Tomatoes' and value.endswith('%'):
                    out.setdefault('rt_crit', value.rstrip('%'))
                elif source == 'Metacritic' and '/' in value:
                    out.setdefault('metacritic', value.split('/')[0])
            imdb_score = str(data.get('imdbRating') or '')
            if imdb_score and imdb_score != 'N/A':
                out.setdefault('imdb', imdb_score)
        except Exception:
            pass
    if len(_FREE_CACHE) > _FREE_MAX:
        _FREE_CACHE.clear()
    _FREE_CACHE[key] = dict(out)
    return out
