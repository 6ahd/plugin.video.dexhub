# -*- coding: utf-8 -*-
"""Floppy's API for the Library and the tracking panel (v5.10.153).

The Floppy service add-on (service.dexhub.floppy) keeps the server and the
token (floppy_progress.credentials reads them); this module makes the calls
the Library tab, the title page and the tracking panel need:

  status, rating, lists     GET/PATCH/POST/DELETE media/<type>/tmdb/<id>/
  list membership           PUT/DELETE media/<type>/tmdb/<id>/lists/<list>/
  the user's lists          GET lists/
  titles by status          GET media/?status=..&media_type=..
  calendar                  GET calendar/?start_date&end_date
  month statistics          GET statistics/overview/?start_date&end_date

Floppy speaks TMDb ids (source tmdb, media_type movie or tv).

Nothing here logs the token or puts it in a URL Kodi could show. Reads are
kept a short while in memory and in a small file (the plugin is a fresh
Python on every call).
"""
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import xbmc

from . import floppy_progress

TIMEOUT = 8
STATUSES = ('Planning', 'In progress', 'Completed', 'Paused', 'Dropped')
_MEM = {}


def configured():
    return floppy_progress.configured()


def _cache_path():
    import xbmcvfs
    return os.path.join(xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/'),
                        'floppy_cache.json')


_DISK = {'stamp': None, 'data': {}}


def _disk():
    """The file cache (a copy of its top level: callers add and drop keys).

    v5.10.188: read again only when the file changed. Every Library publish
    read and parsed the whole file two or three times (the lists, this
    month's numbers), its entries hold whole lists of titles."""
    path = _cache_path()
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if stamp != _DISK['stamp']:
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except Exception:
            data = {}
        _DISK['stamp'], _DISK['data'] = stamp, (data if isinstance(data, dict) else {})
    return dict(_DISK['data'])


def _disk_put(key, value):
    from .skinui import common as C
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with C.file_lock(path):
        data = _disk()
        data[key] = {'at': time.time(), 'v': value}
        if len(data) > 60:
            for old in sorted(data, key=lambda k: data[k].get('at', 0))[:-60]:
                data.pop(old, None)
        C.write_json(path, data)


def forget(prefix=''):
    """Invalidate this account's cache without overwriting a concurrent write."""
    from .skinui import common as C
    prefix = _scoped(prefix)
    for key in [k for k in _MEM if k.startswith(prefix)]:
        _MEM.pop(key, None)
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with C.file_lock(path):
        data = _disk()
        kept = {k: v for k, v in data.items() if not k.startswith(prefix)}
        if kept != data:
            C.write_json(path, kept)


def request(method, path, body=None, params=None, timeout=TIMEOUT):
    """(http status, parsed body). Status 0: no answer (or not set up)."""
    server, token = floppy_progress.credentials()
    if not (server and token):
        return 0, None
    url = server + '/api/v1/' + path.lstrip('/')
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Accept': 'application/json', 'Authorization': 'Bearer ' + token,
               'User-Agent': 'DexHub Kodi'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        context = ssl.create_default_context()
    except Exception:
        context = None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            raw = resp.read().decode('utf-8') or 'null'
            try:
                return resp.getcode(), json.loads(raw)
            except ValueError:
                return resp.getcode(), None
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode('utf-8') or 'null')
        except Exception:
            detail = None
        return exc.code, detail
    except Exception as exc:
        xbmc.log('[DexHub] floppy %s %s: %s' % (method, path.split('?')[0], exc.__class__.__name__), xbmc.LOGINFO)
        return 0, None


def account_key():
    import hashlib
    server, token = floppy_progress.credentials()
    return hashlib.sha256((server + '\0' + token).encode('utf-8')).hexdigest()


def _scoped(key):
    return account_key() + ':' + key


def is_fresh(key, ttl):
    key = _scoped(key)
    hit = _MEM.get(key)
    if hit and time.time() - hit[0] < ttl:
        return True
    disk = _disk().get(key) or {}
    return bool(disk and time.time() - float(disk.get('at') or 0) < ttl)


def cached_get(key, path, params=None, ttl=600.0, stale_ok=True, cache_only=False):
    """GET with a memory and file cache; the last good answer when Floppy is away."""
    owner = account_key()
    key = owner + ':' + key
    now = time.time()
    hit = _MEM.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    disk = _disk().get(key)
    if disk and now - float(disk.get('at') or 0) < ttl:
        _MEM[key] = (float(disk['at']), disk.get('v'))
        return disk.get('v')
    if cache_only:
        return hit[1] if hit else (disk.get('v') if disk else None)
    status, data = request('GET', path, params=params)
    if account_key() != owner:
        return None
    if status == 200 and data is not None:
        _MEM[key] = (now, data)
        _disk_put(key, data)
        return data
    if stale_ok and disk:
        return disk.get('v')
    return None


# ---------------------------------------------------------------- one title
def kind(media):
    return 'tv' if str(media or '').lower() in ('series', 'tv', 'show', 'anime', 'tvshow', 'episode') else 'movie'


def _detail_path(media, tmdb):
    return 'media/%s/tmdb/%s/' % (kind(media), urllib.parse.quote(str(tmdb)))


def entry(media, tmdb, fresh=False):
    """{'tracked', 'status', 'score', 'lists': [list ids]} or None (no answer)."""
    if not tmdb:
        return None
    key = 'detail:%s:%s' % (kind(media), tmdb)
    if fresh:
        forget(key)
    data = cached_get(key, _detail_path(media, tmdb), ttl=300.0)
    if not isinstance(data, dict):
        return None
    consumptions = [c for c in (data.get('consumptions') or []) if isinstance(c, dict)]
    first = consumptions[0] if consumptions else {}
    status = first.get('status')
    if isinstance(status, int):
        status = STATUSES[status] if 0 <= status < len(STATUSES) else ''
    score = first.get('score')
    lists = []
    for row in data.get('lists') or []:
        if isinstance(row, dict) and row.get('list_id') is not None:
            lists.append(int(row['list_id']))
    return {'tracked': bool(data.get('tracked')), 'status': status or '',
            'score': float(score) if score not in (None, '') else None, 'lists': lists,
            'title': data.get('title') or ''}


def set_status(media, tmdb, status, title='', image=''):
    """Track the title with this status (adds it when Floppy has no row yet)."""
    if status not in STATUSES:
        return False
    current = entry(media, tmdb)
    if current and current.get('tracked'):
        code, _data = request('PATCH', _detail_path(media, tmdb), {'status': status})
    else:
        body = {'source': 'tmdb', 'media_id': str(tmdb), 'status': status}
        if title:
            body['title'] = title
        if image:
            body['image_url'] = image
        code, _data = request('POST', 'media/%s/' % kind(media), body)
        if code == 409:
            code, _data = request('PATCH', _detail_path(media, tmdb), {'status': status})
    forget('detail:%s:%s' % (kind(media), tmdb))
    forget('status:')
    return code in (200, 201, 204)


def set_score(media, tmdb, score, title=''):
    current = entry(media, tmdb)
    if current and current.get('tracked'):
        code, _data = request('PATCH', _detail_path(media, tmdb), {'score': score})
    else:
        body = {'source': 'tmdb', 'media_id': str(tmdb), 'status': 'Completed', 'score': score}
        if title:
            body['title'] = title
        code, _data = request('POST', 'media/%s/' % kind(media), body)
    forget('detail:%s:%s' % (kind(media), tmdb))
    return code in (200, 201, 204)


def untrack(media, tmdb):
    code, _data = request('DELETE', _detail_path(media, tmdb))
    forget('detail:%s:%s' % (kind(media), tmdb))
    forget('status:')
    return code in (200, 204, 404)


def set_list(media, tmdb, list_id, member):
    path = _detail_path(media, tmdb) + 'lists/%d/' % int(list_id)
    code, _data = request('PUT' if member else 'DELETE', path, {} if member else None)
    forget('detail:%s:%s' % (kind(media), tmdb))
    forget('lists')
    return code in (200, 201, 204, 404 if not member else 200)


# ---------------------------------------------------------------- collections
def lists(cache_only=False):
    data = cached_get('lists', 'lists/', params={'limit': 100}, ttl=900.0, cache_only=cache_only)
    rows = (data or {}).get('results') if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get('id') is not None]


def list_items(list_id, limit=100):
    data = cached_get('list:%s' % list_id, 'lists/%d/items/' % int(list_id), params={'limit': limit}, ttl=900.0)
    return [r for r in ((data or {}).get('results') or []) if isinstance(r, dict)] if isinstance(data, dict) else []


def by_status(status, media='', limit=100, sort='updated'):
    params = {'status': status, 'limit': limit, 'sort': sort, 'direction': 'desc'}
    if media:
        params['media_type'] = kind(media)
    data = cached_get('status:%s:%s' % (status, media or 'all'), 'media/', params=params, ttl=600.0)
    return [r for r in ((data or {}).get('results') or []) if isinstance(r, dict)] if isinstance(data, dict) else []


def calendar(days=7, back=1):
    import datetime
    today = datetime.date.today()
    start = today - datetime.timedelta(days=back)
    end = today + datetime.timedelta(days=days)
    data = cached_get('calendar:%s' % today.isoformat(), 'calendar/',
                      params={'start_date': start.isoformat(), 'end_date': end.isoformat(), 'limit': 200},
                      ttl=1800.0)
    return [r for r in ((data or {}).get('results') or []) if isinstance(r, dict)] if isinstance(data, dict) else []


def _find(data, key):
    """The first value of ``key`` anywhere in nested dicts and lists."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if key in node and not isinstance(node[key], (dict, list)):
                return node[key]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def month_stats(cache_only=False):
    """{'hours', 'episodes', 'movies', 'streak'} for this month, or None."""
    import datetime
    today = datetime.date.today()
    data = cached_get('stats:%s' % today.strftime('%Y-%m'), 'statistics/overview/',
                      params={'start_date': today.replace(day=1).isoformat(), 'end_date': today.isoformat()},
                      ttl=3600.0, cache_only=cache_only)
    st = (data or {}).get('statistics') if isinstance(data, dict) else None
    if not isinstance(st, dict):
        return None
    hours = 0.0
    for block in ('movie_consumption', 'tv_consumption', 'anime_consumption'):
        hours += _number(((st.get(block) or {}).get('hours') or {}).get('total'))
    if not hours:
        hours = sum(_number(v) for v in (st.get('hours_per_media_type') or {}).values()
                    if not isinstance(v, (dict, list)))
    movies = _number(((st.get('movie_consumption') or {}).get('plays') or {}).get('total'))
    episodes = _number(((st.get('tv_consumption') or {}).get('plays') or {}).get('total')) + \
        _number(((st.get('anime_consumption') or {}).get('plays') or {}).get('total'))
    return {'hours': hours, 'episodes': int(episodes), 'movies': int(movies),
            'streak': int(_number(_find(st, 'current_streak')))}


# ---------------------------------------------------------------- rows
def parts(entry_row):
    """A Floppy media row as {'media', 'tmdb', 'title', 'poster', 'fanart', 'year', 'plot',
    'season', 'episode', 'when'} (media: movie or series)."""
    item = entry_row.get('item') if isinstance(entry_row.get('item'), dict) else entry_row
    media_type = item.get('media_type') or entry_row.get('media_type') or ''
    show = entry_row.get('show') if isinstance(entry_row.get('show'), dict) else {}
    tmdb = ''
    if item.get('source') == 'tmdb' and item.get('media_id'):
        tmdb = str(item['media_id'])
    ids = entry_row.get('ids') or item.get('ids') or {}
    if not tmdb and ids.get('tmdb'):
        tmdb = str(ids['tmdb'])
    released = str(item.get('release_datetime') or item.get('release_date') or '')
    year = 0
    if released[:4].isdigit():
        year = int(released[:4])
    return {
        'media': 'series' if media_type in ('tv', 'season', 'episode', 'anime') else ('movie' if media_type == 'movie' else ''),
        'tmdb': tmdb, 'imdb': str(ids.get('imdb') or ''),
        'title': item.get('title') or show.get('title') or entry_row.get('title') or '',
        'poster': item.get('image') or entry_row.get('image') or '',
        'fanart': item.get('backdrop') or entry_row.get('backdrop') or '',
        'plot': item.get('synopsis') or '', 'year': year,
        'season': item.get('season_number'), 'episode': item.get('episode_number'),
        'when': entry_row.get('datetime') or '',
        'score': entry_row.get('score'),
    }


def watchlist_mirror_rows():
    """Floppy's "Planning" titles as favourites rows (source floppy).

    Raises when Floppy does not answer, so the last good mirror stays.
    """
    forget('status:Planning')
    status, data = request('GET', 'media/', params={'status': 'Planning', 'limit': 500,
                                                    'sort': 'date_added', 'direction': 'desc'})
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError('floppy watchlist: %s' % (status or 'offline'))
    rows, now = [], int(time.time())
    for index, entry_row in enumerate(data.get('results') or []):
        if not isinstance(entry_row, dict):
            continue
        p = parts(entry_row)
        if p['media'] not in ('movie', 'series') or not (p.get('tmdb') or p.get('imdb')):
            continue
        rows.append({'media_type': p['media'], 'canonical_id': p.get('imdb') or 'tmdb:%s' % p['tmdb'],
                     'title': p.get('title') or '', 'poster': p.get('poster') or '',
                     'background': p.get('fanart') or '', 'year': p.get('year') or 0,
                     'plot': p.get('plot') or '', 'added_at': now - index})
    return rows
