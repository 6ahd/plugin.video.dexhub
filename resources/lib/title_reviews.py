# -*- coding: utf-8 -*-
"""v5.10.365: reviews, ratings and episode ratings for the title page.

* Reviews: Trakt's comments (the most liked first, full reviews before short
  ones) and TMDb's reviews, in one list. OK on one reads it whole.
* Episode ratings: TMDb's rating of each episode (★ 8.4) on the episode cards.

Both title pages use it: skin.dexhub's (skinui/actions.py) and the add-on's
own under other skins (homeui/details.py).
"""
import re
import time

import xbmc
import xbmcgui

REVIEWS_TTL = 12 * 3600
VOTES_TTL = 24 * 3600
MIN_VOTES = 3
EXCERPT = 140


def _log(text, level=xbmc.LOGDEBUG):
    xbmc.log('[DexHub] reviews: %s' % text, level)


def _clean(text):
    text = re.sub(r'\[/?spoiler\]', '', str(text or ''), flags=re.I)
    text = re.sub(r'[\*_#>`]+', '', text)          # markdown marks
    text = re.sub(r'\r', '', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _short(text):
    one = re.sub(r'\s+', ' ', text).strip()
    return one if len(one) <= EXCERPT else one[:EXCERPT].rsplit(' ', 1)[0] + '…'


def _trakt_reviews(media, imdb, tmdb):
    try:
        from . import trakt
    except Exception:
        return []
    kind = 'shows' if media == 'series' else 'movies'
    ident = imdb if str(imdb).startswith('tt') else ''
    if not ident and tmdb:
        try:
            found = trakt._cached_public_request('/search/tmdb/%s?type=%s' % (tmdb, 'show' if media == 'series' else 'movie')) or []
            ids = ((found[0] or {}).get('show' if media == 'series' else 'movie') or {}).get('ids') or {} if found else {}
            ident = str(ids.get('slug') or ids.get('trakt') or '')
        except Exception:
            ident = ''
    if not ident:
        return []
    try:
        rows = trakt._cached_public_request('/%s/%s/comments/likes?limit=30' % (kind, ident)) or []
    except Exception as exc:
        _log('trakt: %s' % exc)
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        text = _clean(row.get('comment'))
        if len(text) < 40:
            continue
        rating = (row.get('user_stats') or {}).get('rating') or row.get('user_rating')
        out.append({'src': 'Trakt', 'author': ((row.get('user') or {}).get('username') or 'Trakt'),
                    'rating': int(rating) if rating else None, 'text': text, 'spoiler': bool(row.get('spoiler')),
                    'review': bool(row.get('review')), 'likes': int(row.get('likes') or 0),
                    'date': str(row.get('created_at') or '')[:10]})
    out.sort(key=lambda r: (not r['review'], -r['likes']))
    return out[:15]


def _tmdb_reviews(media, tmdb):
    if not str(tmdb).isdigit():
        return []
    try:
        from . import tmdb_direct as T
        data = T._request('/%s/%s/reviews' % ('tv' if media == 'series' else 'movie', tmdb), {'language': 'en-US'}) or {}
    except Exception as exc:
        _log('tmdb: %s' % exc)
        return []
    out = []
    for row in data.get('results') or []:
        text = _clean(row.get('content'))
        if len(text) < 40:
            continue
        rating = (row.get('author_details') or {}).get('rating')
        out.append({'src': 'TMDb', 'author': row.get('author') or 'TMDb',
                    'rating': int(round(float(rating))) if rating else None, 'text': text, 'spoiler': False,
                    'review': True, 'likes': 0, 'date': str(row.get('created_at') or '')[:10]})
    return out[:10]


def reviews(media, imdb='', tmdb=''):
    """[{'src', 'author', 'rating', 'text', 'spoiler', 'date'}], cached half a day."""
    from . import tmdb_direct as T
    key = 'dh-reviews:%s:%s:%s' % (media, imdb, tmdb)
    cached = T._cache_get(key)
    if isinstance(cached, dict) and isinstance(cached.get('rows'), list):
        return cached['rows']
    started = time.monotonic()
    rows = _trakt_reviews(media, imdb, tmdb) + _tmdb_reviews(media, tmdb)
    T._cache_set(key, {'rows': rows}, REVIEWS_TTL if rows else 3600)
    _log('%d review(s) in %.0f ms' % (len(rows), (time.monotonic() - started) * 1000))
    return rows


def show(media, imdb, tmdb, title, tr):
    """The reviews as a list; OK reads one whole; Back closes."""
    from .homeui.options import choose
    busy = xbmcgui.DialogProgressBG()
    busy.create('Dex Hub', tr('جاري جلب المراجعات…'))
    try:
        rows = reviews(media, imdb, tmdb)
    finally:
        busy.close()
    if not rows:
        xbmcgui.Dialog().notification('Dex Hub', tr('ما فيه مراجعات لهذا العمل'), sound=False)
        return
    options = []
    for row in rows:
        head = '%s  ·  %s' % (row['author'], row['src'])
        if row.get('rating'):
            head = '★ %d/10  ·  %s' % (row['rating'], head)
        hint = tr('فيها حرق للأحداث') if row.get('spoiler') else _short(row['text'])
        options.append((head, hint))
    pick = 0
    while True:
        pick = choose(title, options, preselect=pick, subtitle=tr('المراجعات'))
        if pick < 0:
            return
        row = rows[pick]
        if row.get('spoiler') and not xbmcgui.Dialog().yesno('Dex Hub', tr('المراجعة فيها حرق للأحداث، تبي تقراها؟')):
            continue
        lines = ['[B]%s[/B]  ·  %s%s' % (row['author'], row['src'], ('  ·  ' + row['date']) if row.get('date') else '')]
        if row.get('rating'):
            lines.append('★ %d/10' % row['rating'])
        xbmcgui.Dialog().textviewer(title, '\n'.join(lines) + '\n\n' + row['text'])


def season_votes(tmdb, season):
    """{episode number: TMDb rating} for one season (episodes with a few votes)."""
    if not str(tmdb).isdigit():
        return {}
    from . import tmdb_direct as T
    key = 'dh-season-votes:%s:%s' % (tmdb, season)
    data = T._cache_get(key)
    if data is None:
        try:
            raw = T._request('/tv/%s/season/%s' % (tmdb, season), {'language': 'en-US'}) or {}
            data = {}
            for ep in raw.get('episodes') or []:
                if int(ep.get('vote_count') or 0) >= MIN_VOTES and ep.get('vote_average'):
                    data[str(int(ep.get('episode_number') or 0))] = round(float(ep['vote_average']), 1)
        except Exception as exc:
            _log('season votes: %s' % exc)
            data = {}
        T._cache_set(key, data, VOTES_TTL if data else 3600)
    out = {}
    for k, v in (data or {}).items():
        try:
            out[int(k)] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def season_imdb(imdb, season):
    """{episode number: IMDb rating} for one season, from OMDb (TMDb
    Helper's OMDb key: one request a season, kept a day). {} without a key."""
    imdb = str(imdb or '').strip()
    if not imdb.startswith('tt'):
        return {}
    from .homeui.ratings import _helper_key
    key_value = _helper_key('omdb_apikey')
    if not key_value:
        return {}
    from . import tmdb_direct as T
    key = 'dh-season-imdb:%s:%s' % (imdb, season)
    data = T._cache_get(key)
    if data is None:
        data = {}
        try:
            import json
            import urllib.parse
            import urllib.request
            url = 'https://www.omdbapi.com/?' + urllib.parse.urlencode({'i': imdb, 'Season': season, 'apikey': key_value})
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'DexHub'}), timeout=6) as handle:
                raw = json.loads(handle.read().decode('utf-8', 'replace'))
            for ep in raw.get('Episodes') or []:
                value = str(ep.get('imdbRating') or '')
                if value and value != 'N/A':
                    data[str(int(ep.get('Episode') or 0))] = float(value)
        except Exception as exc:
            _log('season imdb: %s' % exc)
            data = {}
        T._cache_set(key, data, VOTES_TTL if data else 3600)
    out = {}
    for k, v in (data or {}).items():
        try:
            out[int(k)] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def episode_source():
    """v5.10.595: the episodes' rating follows the ratings the user shows
    (Control Centre's ratings choice): IMDb when it is shown, else TMDb when
    it is, else none."""
    try:
        from .homeui.ratings import chosen
        keys = chosen()
    except Exception:
        keys = ['imdb', 'tmdb']
    if 'imdb' in keys:
        return 'imdb'
    if 'tmdb' in keys:
        return 'tmdb'
    return ''


def with_votes(tmdb, season, rows, imdb=''):
    """The episode rows with their rating ('rating') and its source
    ('rating_src': imdb or tmdb). IMDb (OMDb) when it is the chosen one and
    known for the episode, TMDb otherwise when TMDb is shown too."""
    if not rows:
        return rows
    source = episode_source()
    if not source:
        return rows
    imdb_votes = {}
    if source == 'imdb':
        try:
            imdb_votes = season_imdb(imdb, season)
        except Exception:
            imdb_votes = {}
    # TMDb's for the episodes IMDb has no rating for, when TMDb is shown too
    try:
        from .homeui.ratings import chosen
        tmdb_ok = source == 'tmdb' or 'tmdb' in chosen()
    except Exception:
        tmdb_ok = True
    try:
        votes = season_votes(tmdb, season) if tmdb_ok else {}
    except Exception:
        votes = {}
    for row in rows:
        try:
            number = int(row.get('episode') or 0)
        except (TypeError, ValueError):
            continue
        if imdb_votes.get(number):
            row['rating'], row['rating_src'] = imdb_votes[number], 'imdb'
        elif votes.get(number) and tmdb_ok:
            row['rating'], row['rating_src'] = votes[number], 'tmdb'
    return rows
