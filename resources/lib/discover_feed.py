# -*- coding: utf-8 -*-
"""The Discover page's data (v5.10.454): one short list per page, nothing more.

Tonight, Your time and Your mood ask TMDb (12 titles a page, kept 6 hours);
Collections are the active set's folders with the posters of their first
catalog (kept a day in homeui/discover.json); Calendar is the calendar's own
list. Only the page on screen is read.
"""
import datetime
import json
import os
import time

from . import profile_scope as _scope

PAGE = 12
TTL = 6 * 3600
MAX_FOLDERS = 8
FOLDER_KEEP = 24 * 3600.0
THIS_FAR_BACK, THIS_FAR_AHEAD = 14, 45

TIMES = {'short': {'with_runtime.lte': 95}, 'mid': {'with_runtime.gte': 96, 'with_runtime.lte': 135},
         'long': {'with_runtime.gte': 150}}
MOODS = {'feel': '35|10751', 'tense': '53|28', 'mind': '878|9648'}
TV_MOODS = {'feel': '35|10751', 'tense': '80|10759', 'mind': '10765|9648'}
SORTS = ('popular', 'rating', 'new')
RATINGS = (0, 7, 8)
DECADES = ('', '2020', '2010', '2000', '1990')
LANGUAGES = ('', 'ar', 'en', 'ko')
TINTS = ('FF4A3418', 'FF123F57', 'FF2C2455', 'FF4F1E22', 'FF12443A', 'FF3D2A4F', 'FF4A2F18', 'FF1B3358')


def english():
    try:
        from .i18n import is_english
        return bool(is_english())
    except Exception:
        return False


def empty_filter():
    return {'rating': 0, 'decade': '', 'language': '', 'unwatched': False}


def filter_count(chosen):
    return sum(1 for key in ('rating', 'decade', 'language', 'unwatched') if chosen.get(key))


# ---------------------------------------------------------------- TMDb's questions (no Kodi in here)
def discover_params(media, sort='popular', chosen=None, today=None):
    """The filters and the order as TMDb's Discover takes them."""
    chosen = chosen or {}
    today = today or datetime.date.today()
    dated = 'first_air_date' if media == 'series' else 'primary_release_date'
    params = {'include_adult': 'false', 'vote_count.gte': 100}
    if sort == 'rating':
        params.update({'sort_by': 'vote_average.desc', 'vote_count.gte': 1000})
    elif sort == 'new':
        params.update({'sort_by': '%s.desc' % dated, '%s.lte' % dated: today.isoformat(), 'vote_count.gte': 30})
    else:
        params['sort_by'] = 'popularity.desc'
    if chosen.get('rating'):
        params['vote_average.gte'] = chosen['rating']
    if chosen.get('decade'):
        start = int(chosen['decade'])
        params['%s.gte' % dated] = '%d-01-01' % start
        params.setdefault('%s.lte' % dated, '%d-12-31' % (start + 9))
    if chosen.get('language'):
        params['with_original_language'] = chosen['language']
    return params


def time_params(chip, media, sort='popular', chosen=None):
    params = discover_params(media, sort, chosen)
    params.update(TIMES.get(chip) or TIMES['mid'])
    return params


def mood_params(chip, media, sort='popular', chosen=None):
    params = discover_params(media, sort, chosen)
    params['with_genres'] = (TV_MOODS if media == 'series' else MOODS).get(chip) or MOODS['tense']
    return params


def apply_local(rows, sort='popular', chosen=None, watched=()):
    """The same filter on a list TMDb has already sent (Tonight)."""
    chosen = chosen or {}
    out = []
    for row in rows:
        if chosen.get('rating') and float(row.get('rating') or 0) < chosen['rating']:
            continue
        if chosen.get('decade'):
            year = str(row.get('year') or '')
            if not (year.isdigit() and int(chosen['decade']) <= int(year) < int(chosen['decade']) + 10):
                continue
        if chosen.get('language') and row.get('language') != chosen['language']:
            continue
        if chosen.get('unwatched') and row.get('key') in watched:
            continue
        out.append(row)
    if sort == 'rating':
        out.sort(key=lambda r: -float(r.get('rating') or 0))
    elif sort == 'new':
        out.sort(key=lambda r: str(r.get('date') or ''), reverse=True)
    return out


def _row(raw, media):
    from . import tmdb_direct as T
    kind = media
    if raw.get('media_type') in ('movie', 'tv'):
        kind = 'series' if raw['media_type'] == 'tv' else 'movie'
    tmdb = str(raw.get('id') or '')
    date = str(raw.get('release_date') or raw.get('first_air_date') or '')
    if not tmdb or not raw.get('poster_path'):
        return None
    return {'kind': kind, 'tmdb': tmdb, 'key': ('movie|%s' if kind == 'movie' else 'series|%s') % tmdb,
            'title': raw.get('title') or raw.get('name') or '', 'date': date, 'year': date[:4],
            'rating': round(float(raw.get('vote_average') or 0), 1), 'plot': raw.get('overview') or '',
            'language': raw.get('original_language') or '', 'poster': T._image_url(raw['poster_path'], kind='poster'),
            'fanart': T._image_url(raw['backdrop_path'], kind='backdrop') if raw.get('backdrop_path') else ''}


def _ask(path, params, media):
    from . import tmdb_direct as T
    params = dict(params)
    params['language'] = 'en-US' if english() else 'ar-SA'
    key = 'discover1:%s:%s' % (path, json.dumps(params, sort_keys=True))
    known = T._cache_get(key)
    if known is not None:
        return known.get('rows') or []
    try:
        raw = T._request(path, params, timeout=8, max_attempts=2) or {}
    except Exception:
        return []
    rows = [row for row in (_row(item, media) for item in raw.get('results') or []) if row][:PAGE]
    T._cache_set(key, {'rows': rows}, TTL if rows else 1800)
    return rows


def _mixed(media, build, path_of=None):
    if media in ('movie', 'series'):
        return _ask('/discover/%s' % ('tv' if media == 'series' else 'movie'), build(media), media)
    movies, shows = _ask('/discover/movie', build('movie'), 'movie'), _ask('/discover/tv', build('series'), 'series')
    out = []
    for pair in zip(movies, shows):
        out.extend(pair)
    return (out + movies[len(out) // 2:] + shows[len(out) // 2:])[:PAGE]


def _last_watched():
    """(media, tmdb) of the title played here last, or None."""
    try:
        from .dexhub import playback_store
        from . import episode_titles
        for row in playback_store.list_recent_items(limit=20, include_watched=True):
            tmdb = episode_titles.show_ids(row)[0]
            if tmdb:
                return ('tv' if row.get('season') else 'movie'), tmdb
    except Exception:
        pass
    return None


def tonight(media='all', sort='popular', chosen=None, watched=()):
    last = _last_watched()
    rows = []
    if last:
        rows = _ask('/%s/%s/recommendations' % last, {}, 'series' if last[0] == 'tv' else 'movie')
    if len(rows) < 6:
        rows = _ask('/trending/all/week', {}, 'movie')
    if media in ('movie', 'series'):
        rows = [row for row in rows if row['kind'] == media] or \
            _ask('/trending/%s/week' % ('tv' if media == 'series' else 'movie'), {}, media)
    return apply_local(rows, sort, chosen, watched)[:9]


def by_time(chip, media='all', sort='popular', chosen=None, watched=(), limit=6):
    # a series' length says little: its episodes are asked for
    rows = _mixed('movie' if media == 'all' else media, lambda m: time_params(chip, m, sort, chosen))
    return apply_local(rows, '', {'unwatched': (chosen or {}).get('unwatched')}, watched)[:limit]


def by_mood(chip, media='all', sort='popular', chosen=None, watched=(), limit=6):
    rows = _mixed(media, lambda m: mood_params(chip, m, sort, chosen))
    return apply_local(rows, '', {'unwatched': (chosen or {}).get('unwatched')}, watched)[:limit]


# ---------------------------------------------------------------- the calendar
def tickets(media='all', today=None, limit=6):
    """What came out lately and what is coming, the closest first (the calendar's list)."""
    from . import calendar_feed as F
    today = today or datetime.date.today()
    lo = (today - datetime.timedelta(days=THIS_FAR_BACK)).isoformat()
    hi = (today + datetime.timedelta(days=THIS_FAR_AHEAD)).isoformat()
    now = today.isoformat()
    rows, seen = [], set()
    events = [ev for ev in (F.read().get('events') or []) if lo <= (ev.get('day') or '') <= hi]
    events.sort(key=lambda ev: abs((datetime.date.fromisoformat(ev['day']) - today).days))
    for ev in events:
        kind = 'series' if ev.get('kind') == 'ep' else 'movie'
        if (media in ('movie', 'series') and kind != media) or (kind, ev.get('tmdb')) in seen:
            continue
        seen.add((kind, ev.get('tmdb')))
        rows.append({'kind': kind, 'tmdb': ev.get('tmdb') or '', 'imdb': ev.get('imdb') or '', 'title': ev.get('title') or '',
                     'year': '', 'rating': 0, 'plot': ev.get('plot') or '', 'poster': ev.get('poster') or '',
                     'fanart': ev.get('fanart') or '', 'date': ev.get('day') or '', 'code': ev.get('code') or '',
                     'sub': ev.get('sub') or '', 'out': (ev.get('day') or '') <= now, 'key': ev.get('key') or ''})
    return rows[:limit]


# ---------------------------------------------------------------- collections
def _store_path():
    return _scope.path('homeui/discover.json')


def _read_store():
    try:
        with open(_store_path(), encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_store(data):
    _scope.require_current()
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, path)


def fold_key(ref):
    return '%s|%s|%s' % (ref.get('set_id') or '', ref.get('group_id') or '', ref.get('folder_id') or '')


def folders():
    """The active collections' first folders, without asking the network."""
    from . import collection_sets
    from .homeui import rows as R
    out = []
    for sets in collection_sets.active_sets():
        for entry in sets.get('entries') or []:
            for tile in R.folder_tiles(entry, sets.get('id') or '') or []:
                if tile.get('source_indexes') and tile.get('title'):
                    out.append(tile)
    picked, seen = [], set()
    for tile in out:
        if tile['title'].lower() not in seen:
            seen.add(tile['title'].lower())
            picked.append(tile)
    return picked[:MAX_FOLDERS]


def cards(tiles, store=None, now=None):
    """Tiles as the page's cards: the kept posters, else the folder's own cover, each with its tint."""
    store = _read_store() if store is None else store
    out = []
    for index, tile in enumerate(tiles):
        ref = tile.get('folder_ref') or {}
        kept = store.get(fold_key(ref)) or {}
        posters = list(kept.get('p') or [])[:3]
        out.append({'kind': 'folder', 'title': tile.get('title') or '', 'ref': ref, 'tint': TINTS[index % len(TINTS)],
                    'count': int(tile.get('source_count') or 0), 'posters': posters, 'cover': tile.get('poster') or '',
                    'fanart': kept.get('f') or tile.get('fanart') or '', 'group': tile.get('group_title') or ''})
    return out


def read_posters(tiles, api, cancelled=None):
    """Read what is missing, a folder at a time. Returns True when something new came."""
    from .homeui import rows as R
    store = _read_store()
    changed = False
    now = time.time()
    for tile in tiles:
        if cancelled and cancelled():
            break
        ref = tile.get('folder_ref') or {}
        key = fold_key(ref)
        kept = store.get(key)
        if kept and now - float(kept.get('t') or 0) < FOLDER_KEEP:
            continue
        indexes = tile.get('source_indexes') or []
        params = {'action': 'collection_nuvio_source_open', 'set_id': ref.get('set_id') or '',
                  'group_id': ref.get('group_id') or '', 'folder_id': ref.get('folder_id') or '',
                  'source_index': str(indexes[0]), 'page': '1'}
        posters, fanart = [], ''
        try:
            for found in (R.capture_tiles(params, api, limit=8)[0] or []):
                if found.get('poster') and found['poster'] not in posters:
                    posters.append(found['poster'])
                fanart = fanart or found.get('fanart') or ''
        except Exception:
            pass
        store[key] = {'t': now, 'p': posters[:3], 'f': fanart}
        changed = True
    if changed:
        _write_store(store)
    return changed


# ---------------------------------------------------------------- Trakt and TMDb lists (v5.10.458)
# The one-page Discover: Trakt's trending, most watched this week, anticipated
# and (with an account) its recommendations, TMDb's top rated. Trakt gives ids
# only: each title is read from TMDb (kept 6 hours). When Trakt does not
# answer, TMDb's nearest list stands in.
TRAKT_TTL = 3 * 3600
WORKERS = 6


def _log(msg):
    try:
        import xbmc
        xbmc.log('[DexHub] discover: %s' % msg, xbmc.LOGINFO)
    except Exception:
        pass


def _details(kind, tmdb):
    """A title's row from TMDb, with its length and genres."""
    from . import tmdb_direct as T
    lang = 'en-US' if english() else 'ar-SA'
    key = 'discover2:item:%s:%s:%s' % (kind, tmdb, lang)
    known = T._cache_get(key)
    if known is not None:
        return known.get('row')
    try:
        raw = T._request('/%s/%s' % ('tv' if kind == 'series' else 'movie', tmdb), {'language': lang},
                         timeout=8, max_attempts=2) or {}
    except Exception:
        return None
    row = _row(raw, kind)
    if row:
        runtime = raw.get('runtime') or ((raw.get('episode_run_time') or [0])[0])
        row['runtime'] = int(runtime or 0)
        row['genres'] = [g.get('name') or '' for g in raw.get('genres') or []][:2]
    T._cache_set(key, {'row': row}, TTL if row else 1800)
    return row


def _details_many(pairs):
    if not pairs:
        return []
    from concurrent.futures import ThreadPoolExecutor
    # the workers end with the block: nothing outlives the call (Kodi 22)
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='DexHub-discover-tmdb') as pool:
        rows = list(pool.map(lambda pair: _details(*pair), pairs))
    return [row for row in rows if row]


def _trakt(path, kind, auth=False, limit=PAGE):
    """A Trakt list as rows; None when Trakt did not answer."""
    from . import tmdb_direct as T, trakt as K
    who = ''
    if auth:
        import hashlib
        who = hashlib.sha1((K.token_data().get('access_token') or '').encode('utf-8')).hexdigest()[:10]
    key = 'discover2:trakt:%s:%s' % (who, path)
    known = T._cache_get(key)
    if known is None:
        try:
            data = K._authed(path, timeout=10) if auth else K._request(path, timeout=10)
        except Exception as exc:
            _log('trakt %s: %s' % (path.split('?')[0], exc.__class__.__name__))
            return None
        ids = []
        for obj in data if isinstance(data, list) else []:
            body = (obj or {}).get('movie') or (obj or {}).get('show') or obj or {}
            tmdb = (body.get('ids') or {}).get('tmdb')
            if tmdb and str(tmdb) not in ids:
                ids.append(str(tmdb))
        known = {'ids': ids}
        T._cache_set(key, known, TRAKT_TTL if ids else 600)
    return _details_many([(kind, tmdb) for tmdb in (known.get('ids') or [])[:limit]])


def _interleave(movies, shows, limit=PAGE):
    out = []
    for pair in zip(movies, shows):
        out.extend(pair)
    return (out + movies[len(out) // 2:] + shows[len(out) // 2:])[:limit]


def _both(movie_path, show_path, media, auth=False, limit=PAGE):
    if media == 'movie':
        return _trakt(movie_path, 'movie', auth, limit)
    if media == 'series':
        return _trakt(show_path, 'series', auth, limit)
    movies, shows = _trakt(movie_path, 'movie', auth, limit), _trakt(show_path, 'series', auth, limit)
    if movies is None and shows is None:
        return None
    return _interleave(movies or [], shows or [], limit)


def _tmdb_both(movie_path, tv_path, media, params=None, limit=PAGE):
    if media in ('movie', 'series'):
        return _ask(movie_path if media == 'movie' else tv_path, params or {}, media)[:limit]
    return _interleave(_ask(movie_path, params or {}, 'movie'), _ask(tv_path, params or {}, 'series'), limit)


def trakt_on():
    try:
        from . import trakt as K
        return K.enabled()
    except Exception:
        return False


def trending(media='all'):
    """(rows, source): what people watch now."""
    rows = _both('/movies/trending?limit=20', '/shows/trending?limit=20', media) if trakt_on() else None
    if rows:
        return rows, 'Trakt'
    return _tmdb_both('/trending/movie/day', '/trending/tv/day', media), 'TMDb'


def most_watched(media='all'):
    """(rows, source): the week's ten most watched."""
    rows = _both('/movies/watched/weekly?limit=12', '/shows/watched/weekly?limit=12', media, limit=10) if trakt_on() else None
    if rows:
        return rows[:10], 'Trakt'
    return _tmdb_both('/trending/movie/week', '/trending/tv/week', media, limit=10), 'TMDb'


def anticipated(media='all'):
    """(rows, source): the most awaited titles still to come."""
    rows = _both('/movies/anticipated?limit=20', '/shows/anticipated?limit=20', media) if trakt_on() else None
    if rows:
        today = datetime.date.today().isoformat()
        coming = [row for row in rows if (row.get('date') or '9') >= today]
        return (coming or rows), 'Trakt'
    return _tmdb_both('/movie/upcoming', '/tv/on_the_air', media), 'TMDb'


def for_you(media='all'):
    """(rows, personal): Trakt's recommendations for the linked account, else
    TMDb's from the last title watched here."""
    try:
        from . import trakt as K
        linked = K.linked()
    except Exception:
        linked = False
    if linked:
        rows = _both('/recommendations/movies?limit=20&ignore_collected=true',
                     '/recommendations/shows?limit=20&ignore_collected=true', media, auth=True)
        if rows:
            return rows, True
    return tonight(media), False


def top_rated(media='all'):
    """(rows, source): the best rated of all time, with a wide picture."""
    rows = _tmdb_both('/movie/top_rated', '/tv/top_rated', media)
    return [row for row in rows if row.get('fanart')] or rows, 'TMDb'


def weekend(pools, today=None):
    """Three evenings ahead, a film each that fits an evening (90 to 150
    minutes), from the rows already read: [(day, row)]."""
    today = today or datetime.date.today()
    seen, picks = set(), []
    for rows in pools:
        for row in rows or []:
            if row.get('kind') != 'movie' or row['key'] in seen:
                continue
            seen.add(row['key'])
            minutes = int(row.get('runtime') or 0)
            if 85 <= minutes <= 155 or not minutes:
                picks.append(row)
    # Thursday night, Friday, Saturday: the weekend here
    ahead = [(today + datetime.timedelta(days=n)) for n in range(0, 7)]
    days = [d for d in ahead if d.weekday() in (3, 4, 5)][:3]
    return list(zip(days, picks[:3]))
