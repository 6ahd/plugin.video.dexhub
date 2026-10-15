# -*- coding: utf-8 -*-
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()
API = 'https://api.mdblist.com'
_URL_RE = re.compile(r'^/lists/(?P<username>[^/]+)/(?P<slug>[^/?#]+)')
_CACHE = {}
_CACHE_TTL = 300


class MDBListError(Exception):
    """Typed API failure so the UI never mistakes an error for an empty list."""

    def __init__(self, message, status=0, code='', details=''):
        super().__init__(str(message or 'MDBList request failed'))
        self.status = int(status or 0)
        self.code = str(code or '')
        self.details = str(details or '')


def _error_message(status=0, details=''):
    status = int(status or 0)
    detail = str(details or '').strip()
    if status == 401:
        return 'مفتاح MDBList غير صحيح أو منتهي الصلاحية.'
    if status == 403:
        return 'MDBList رفض صلاحية الوصول إلى بيانات هذا الحساب.'
    if status == 429:
        return 'تم بلوغ حد طلبات MDBList. انتظر قليلًا ثم حدّث الصفحة.'
    if status >= 500:
        return 'خدمة MDBList غير متاحة مؤقتًا (HTTP %d).' % status
    if status:
        return 'فشل طلب MDBList (HTTP %d)%s' % (
            status, (': %s' % detail[:160]) if detail else '.')
    return detail or 'تعذّر الاتصال بخدمة MDBList.'


def _response_error_detail(body):
    body = str(body or '').strip()
    if not body:
        return ''
    try:
        data = json.loads(body)
    except Exception:
        return body[:240]
    if isinstance(data, dict):
        return str(data.get('message') or data.get('error') or data.get('detail') or '')[:240]
    return body[:240]


def clear_cache():
    _CACHE.clear()


def _setting(key, default=''):
    try:
        return ADDON.getSetting(key) or default
    except Exception:
        return default


def _headers():
    return {
        'Accept': 'application/json',
        'User-Agent': 'DexHub/%s (Kodi)' % (ADDON.getAddonInfo('version') or '3.8.9'),
    }


def _request(path, params=None, timeout=20):
    import time as _time
    params = dict(params or {})
    api_key = (_setting('mdblist_api_key', '') or '').strip()
    if api_key and 'apikey' not in params:
        params['apikey'] = api_key
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    cache_key = url
    cached = _CACHE.get(cache_key)
    if cached and (_time.time() - cached[0]) <= _CACHE_TTL:
        return cached[1], cached[2]
    req = urllib.request.Request(url, headers=_headers(), method='GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', 'ignore')
            try:
                data = json.loads(body) if body else {}
            except Exception as exc:
                raise MDBListError('استجابة MDBList ليست JSON صالحًا.',
                                   status=getattr(resp, 'status', 0),
                                   code='invalid_json', details=str(exc))
            has_more = str(resp.headers.get('X-Has-More') or '').strip().lower() == 'true'
            next_cursor = str(resp.headers.get('X-Next-Cursor') or '').strip()
            # MDBList's cursor can arrive in a header even when the JSON body
            # uses the legacy top-level shape. Preserve it without changing
            # the public (data, has_more) return contract used by callers.
            if isinstance(data, dict) and (next_cursor or has_more):
                data = dict(data)
                data['_dexhub_meta'] = {
                    'next_cursor': next_cursor,
                    'has_more': has_more,
                }
            _CACHE[cache_key] = (_time.time(), data, has_more)
            return data, has_more
    except MDBListError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode('utf-8', 'ignore')
        except Exception:
            body = ''
        detail = _response_error_detail(body)
        raise MDBListError(_error_message(exc.code, detail), status=exc.code,
                           code='http_error', details=detail)
    except urllib.error.URLError as exc:
        detail = str(getattr(exc, 'reason', '') or exc)
        raise MDBListError(_error_message(0, 'تعذّر الاتصال بـ MDBList: %s' % detail),
                           code='network_error', details=detail)
    except Exception as exc:
        raise MDBListError(_error_message(0, 'تعذّر الاتصال بـ MDBList: %s' % exc),
                           code='network_error', details=str(exc))


def api_key():
    return (_setting('mdblist_api_key', '') or '').strip()


def ratings_key():
    """v5.10.301: for ratings only, the key TMDb Helper already has when
    Dex Hub has none (the sources window's ratings came from it, the Home's
    did not). Lists and scrobbling keep Dex Hub's own key."""
    own = api_key()
    if own:
        return own
    try:
        return (xbmcaddon.Addon('plugin.video.themoviedb.helper').getSetting('mdblist_apikey') or '').strip()
    except Exception:
        return ''


def configured():
    return bool(api_key())


def authorization_status():
    return 'connected' if configured() else 'needs_api'


def fetch_user_info(strict=False):
    """GET /user — account limits/profile. Empty dict when the key is bad."""
    if not configured():
        return {}
    try:
        data, _ = _request('/user')
    except Exception:
        if strict:
            raise
        return {}
    return data if isinstance(data, dict) else {}


def fetch_my_lists():
    """GET /lists/user — the account's own lists.

    Normalized rows: {'id', 'name', 'slug', 'user_name', 'items',
    'mediatype'} — enough for the my-lists folder and the authenticated
    numeric-id item route.
    """
    if not configured():
        return []

    def _list_payload(value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in ('lists', 'results', 'data'):
                candidate = value.get(key)
                if isinstance(candidate, list):
                    return candidate
                if isinstance(candidate, dict):
                    nested = candidate.get('lists') or candidate.get('results')
                    if isinstance(nested, list):
                        return nested
        return []

    # Current MDBList contract: /lists/user is a top-level array. Do NOT pass
    # unified=true here: MDBList then replaces the required numeric `id` with
    # an `ids` array for movie/show twins. The menu opens each private list by
    # numeric id, so non-unified rows are the correct foundation.
    data, _ = _request('/lists/user', params={'sort': 'ranked'})
    list_rows = _list_payload(data)

    # A few deployed API revisions returned an empty /lists/user payload while
    # the account-scoped path still contained the lists. Only pay for this
    # compatibility fallback when the primary response is genuinely empty.
    if not list_rows:
        info = fetch_user_info(strict=True) or {}
        account = info.get('user') if isinstance(info.get('user'), dict) else info
        identities = []
        for key in ('id', 'user_id', 'userid', 'username', 'user_name', 'name'):
            value = str((account or {}).get(key) or '').strip()
            if value and value not in identities:
                identities.append(value)
        for identity in identities[:2]:
            try:
                fallback, _ = _request(
                    '/lists/user/%s' % urllib.parse.quote(identity),
                    params={'sort': 'ranked'})
            except MDBListError as exc:
                if exc.status in (400, 404, 405):
                    continue
                raise
            list_rows = _list_payload(fallback)
            if list_rows:
                break

    data = list_rows
    if isinstance(data, dict):
        data = data.get('lists') or data.get('results') or data.get('data') or []
    rows = []
    for row in (data if isinstance(data, list) else []):
        if not isinstance(row, dict):
            continue
        try:
            item_count = int(row.get('items') or row.get('item_count') or row.get('count') or 0)
        except Exception:
            item_count = 0
        rows.append({
            'id': str(row.get('id') or ''),
            'name': row.get('name') or row.get('slug') or '',
            'slug': str(row.get('slug') or ''),
            'user_name': str(row.get('user_name') or row.get('username') or ''),
            'items': item_count,
            'mediatype': str(row.get('mediatype') or ''),
            'description': row.get('description') or '',
            'private': bool(row.get('private') or row.get('is_private')),
            'dynamic': bool(row.get('dynamic') or row.get('is_dynamic')),
            'updated_at': str(row.get('last_updated_at') or row.get('updated_at') or
                              row.get('updated') or ''),
        })
    return rows


def _offset_cursor(value):
    value = str(value or '').strip()
    if not value.startswith('offset:'):
        return None
    try:
        return max(0, int(value.split(':', 1)[1]))
    except Exception:
        return None


def _apply_page_cursor(params, cursor):
    offset = _offset_cursor(cursor)
    if offset is not None:
        params['offset'] = offset
    elif cursor:
        params['cursor'] = cursor


def _normalize_items_response(data, has_more, mf, current_cursor='', limit=100):
    """Shared shape for list-items and watchlist-items responses."""
    def _true(value):
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)

    rows = []
    next_cursor = ''
    pagination = {}
    if isinstance(data, dict):
        pagination = data.get('pagination') if isinstance(data.get('pagination'), dict) else {}
        response_meta = data.get('_dexhub_meta') if isinstance(data.get('_dexhub_meta'), dict) else {}
        next_cursor = str(
            pagination.get('next_cursor') or data.get('next_cursor') or
            response_meta.get('next_cursor') or ''
        ).strip()
        has_more = any(_true(value) for value in (
            has_more, pagination.get('has_more'), response_meta.get('has_more')))
        for row in (data.get('movies') or []):
            if isinstance(row, dict):
                item = dict(row)
                item.setdefault('_type', 'movie')
                rows.append(item)
        for row in (data.get('shows') or []):
            if isinstance(row, dict):
                item = dict(row)
                item.setdefault('_type', 'show')
                rows.append(item)
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                item = dict(row)
                raw_type = str(
                    item.get('_type') or item.get('mediatype') or
                    item.get('media_type') or item.get('type') or '').lower()
                if raw_type in ('movie', 'movies'):
                    item['_type'] = 'movie'
                elif raw_type in ('show', 'shows', 'series', 'tv', 'tvshow', 'anime'):
                    item['_type'] = 'show'
                else:
                    item.setdefault('_type', 'movie' if mf in ('movie', 'movies') else 'show')
                rows.append(item)
    raw_count = len(rows)
    if not has_more and isinstance(pagination, dict):
        try:
            total = int(pagination.get('total') or pagination.get('total_items') or 0)
            page_offset = int(pagination.get('offset') or _offset_cursor(current_cursor) or 0)
            has_more = bool(total and (page_offset + raw_count) < total)
        except Exception:
            pass
    if not next_cursor and has_more:
        # List-items endpoints historically page with offset + X-Has-More,
        # while newer MDBList endpoints expose next_cursor. Encode the offset
        # as an opaque route cursor so the Kodi UI can support both contracts.
        base_offset = _offset_cursor(current_cursor)
        if base_offset is None:
            try:
                base_offset = int(pagination.get('offset') or 0) if isinstance(data, dict) else 0
            except Exception:
                base_offset = 0
        # Advance by the unfiltered response count. If a mixed page contains
        # 70 movies and 30 shows, filtering to shows must still move offset 100.
        step = raw_count or max(1, int(limit or 100))
        next_cursor = 'offset:%d' % (base_offset + step)

    # v4.4.1: some MDBList responses (unified mode, future revisions) nest
    # identifiers under `ids` as a dict or even an ARRAY of dicts. Flatten
    # them into the imdb_id/tvdb_id/id fields the renderer reads, so a shape
    # change can never again render a whole list empty.
    for item in rows:
        nested = item.get('ids')
        candidates = []
        if isinstance(nested, dict):
            candidates = [nested]
        elif isinstance(nested, list):
            candidates = [c for c in nested if isinstance(c, dict)]
        for cand in candidates:
            if not item.get('imdb_id') and cand.get('imdb'):
                item['imdb_id'] = cand.get('imdb')
            if not item.get('tvdb_id') and cand.get('tvdb'):
                item['tvdb_id'] = cand.get('tvdb')
            if not item.get('id') and cand.get('tmdb'):
                item['id'] = cand.get('tmdb')
        if candidates and not isinstance(nested, dict):
            item.pop('ids', None)

    mf = str(mf or 'auto').strip().lower()
    if mf in ('movie', 'movies'):
        rows = [row for row in rows if row.get('_type') == 'movie']
    elif mf in ('show', 'shows', 'series', 'tv', 'tvshow', 'anime'):
        rows = [row for row in rows if row.get('_type') == 'show']
    if not rows and data:
        try:
            shape = list(data.keys())[:6] if isinstance(data, dict) else 'list[%d]' % len(data)
            first = data
            if isinstance(data, dict):
                for key in ('movies', 'shows'):
                    if data.get(key):
                        first = data[key]
                        break
            first_keys = list(first[0].keys())[:10] if isinstance(first, list) and first and isinstance(first[0], dict) else '-'
            xbmc.log('[DexHub] MDBList items normalized to 0 rows (mf=%s) shape=%r first_row_keys=%r'
                     % (mf, shape, first_keys), xbmc.LOGWARNING)
        except Exception:
            pass
    return rows, next_cursor


def _items_path_suffix(mf):
    if mf in ('movie', 'movies'):
        return '/items/movie', False
    if mf in ('show', 'shows', 'series', 'tv', 'anime'):
        return '/items/show', False
    # v4.4.1: auto no longer requests unified (see fetch_items_by_id).
    return '/items', False


def fetch_items_by_id(list_id, media_filter='auto', cursor='', limit=100):
    """Items of one of the account's lists by numeric id (GET /lists/{id}/items)."""
    list_id = str(list_id or '').strip()
    if not list_id:
        return [], ''
    mf = str(media_filter or 'auto').strip().lower()
    # Numeric/private list endpoint has one path only: /lists/{id}/items.
    # Media-specific suffixes belong to the deprecated username/slug routes.
    # v4.4.1: NO unified=true here. Unified responses replace the flat
    # imdb_id/tvdb_id fields with an `ids` ARRAY for movie/show twins —
    # the renderer's id extraction gets nothing and every private list
    # opened empty ('القائمة فارغة'). Non-unified keeps the classic
    # movies/shows buckets with flat ids: exactly what the renderer eats.
    params = {'limit': int(limit or 100)}
    offset = _offset_cursor(cursor)
    if offset is None and str(cursor or '').isdigit():
        offset = int(cursor)
    if offset is not None:
        params['offset'] = offset
    data, has_more = _request(
        '/lists/%s/items' % urllib.parse.quote(list_id), params=params)
    return _normalize_items_response(data, has_more, mf, current_cursor=cursor, limit=limit)


def fetch_watchlist_items(media_filter='auto', cursor='', limit=100):
    """The account watchlist (GET /watchlist/items). Same row shape as lists."""
    if not configured():
        return [], ''
    mf = str(media_filter or 'auto').strip().lower()
    suffix, unified = _items_path_suffix(mf)
    params = {'limit': int(limit or 100)}
    _apply_page_cursor(params, cursor)
    if unified:
        params['unified'] = 'true'
    data, has_more = _request('/watchlist%s' % suffix, params=params)
    return _normalize_items_response(data, has_more, mf, current_cursor=cursor, limit=limit)


def parse_list_url(url):
    url = (url or '').strip()
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    host = (parsed.netloc or '').lower()
    if 'mdblist.com' not in host:
        return None
    match = _URL_RE.match(parsed.path or '')
    if not match:
        return None
    username = urllib.parse.unquote(match.group('username') or '').strip()
    slug = urllib.parse.unquote(match.group('slug') or '').strip()
    if not username or not slug:
        return None
    return {
        'username': username,
        'slug': slug,
        'url': 'https://mdblist.com/lists/%s/%s' % (
            urllib.parse.quote(username), urllib.parse.quote(slug)
        ),
    }



def fetch_items(username, slug, media_filter='auto', cursor='', limit=100):
    username = str(username or '').strip()
    slug = str(slug or '').strip()
    mf = str(media_filter or 'auto').strip().lower()
    params = {'limit': int(limit or 100)}
    _apply_page_cursor(params, cursor)
    if mf in ('movie', 'movies'):
        path = '/lists/%s/%s/items/movie' % (urllib.parse.quote(username), urllib.parse.quote(slug))
    elif mf in ('show', 'shows', 'series', 'tv', 'anime'):
        path = '/lists/%s/%s/items/show' % (urllib.parse.quote(username), urllib.parse.quote(slug))
    else:
        path = '/lists/%s/%s/items' % (urllib.parse.quote(username), urllib.parse.quote(slug))
        params['unified'] = 'true'
    data, has_more = _request(path, params=params)
    return _normalize_items_response(data, has_more, mf, current_cursor=cursor, limit=limit)


# ── v4.6.3: aggregated ratings — contract verified against TMDb Helper ───
# Source of truth: jurialmunkey/plugin.video.themoviedb.helper @ HEAD
#   lib/api/mdblist/api.py            → get_details(provider='tmdb', …)
#   lib/…/concrete_classes/ratings.py → get_ratings(trakt_type, tmdb_id)
# Verified facts (the old VERIFY-LIVE doubt on the show path is resolved):
#   * Path shape is /{provider}/{movie|show}/{id}. TMDb Helper queries by
#     TMDB id ('tmdb' provider); 'show' IS the correct TV segment. We prefer
#     the tmdb path too (v4.5.0 defers IMDb resolution, so tmdb_id is the id
#     we reliably hold when the picker opens) and keep /imdb as fallback.
#   * Raw scales in the response (per TMDb Helper's normalizers):
#       imdb /10 · trakt /100 · tomatoes|tomatoesaudience|metacritic /100 ·
#       letterboxd /5 (5-star) · myanimelist /10 ·
#       MDBList's own score = TOP-LEVEL 'score' (/100), NOT an array row.
_RATINGS_CACHE = {}
_RATINGS_TTL = 6 * 3600

_RATING_SOURCE_MAP = {
    'imdb': 'imdb',
    'metacritic': 'metacritic',
    'trakt': 'trakt',
    'tomatoes': 'rt_crit',
    'tomatoesaudience': 'rt_aud',
    'letterboxd': 'letterboxd',
    'myanimelist': 'mal',
    'score_average': 'mdblist',
}


def _ratings_kind(media_type):
    return 'show' if str(media_type or '').lower() in (
        'series', 'show', 'tv', 'anime', 'tvshow') else 'movie'


def _ratings_cache_keys(kind, imdb_id='', tmdb_id=''):
    """One title may be warmed by tmdb id and read back by imdb id (or the
    reverse), so every store/read uses BOTH keys."""
    keys = []
    tmdb_id = str(tmdb_id or '').strip()
    imdb_id = str(imdb_id or '').strip()
    if tmdb_id:
        keys.append('%s:tmdb:%s' % (kind, tmdb_id))
    if imdb_id:
        if not imdb_id.startswith('tt'):
            imdb_id = 'tt%s' % imdb_id
        keys.append('%s:%s' % (kind, imdb_id))
    return keys


def cached_ratings(imdb_id='', media_type='movie', tmdb_id=''):
    """Return a fresh in-process ratings hit without making an API request."""
    kind = _ratings_kind(media_type)
    import time as _time
    for cache_key in _ratings_cache_keys(kind, imdb_id=imdb_id, tmdb_id=tmdb_id):
        hit = _RATINGS_CACHE.get(cache_key)
        if hit and (_time.time() - hit[0]) <= _RATINGS_TTL:
            return dict(hit[1])
    return {}


def fetch_ratings(imdb_id='', media_type='movie', timeout=6, tmdb_id=''):
    """Aggregated ratings for one title, normalized to display strings.

    Display scales match what TMDb Helper itself renders, so an MDBList
    overlay value is indistinguishable from a TMDb Helper-provided one:
      imdb/trakt/mal → 0-10 one-decimal · letterboxd → 0-5 one-decimal ·
      rt/metacritic/mdblist → 0-100 integer.
    Returns {} when unconfigured or on any failure — callers overlay these on
    top of whatever TMDb Helper already published, never the other way round.
    """
    kind = _ratings_kind(media_type)
    tmdb_id = str(tmdb_id or '').strip()
    imdb_id = str(imdb_id or '').strip()
    if imdb_id and not imdb_id.startswith('tt'):
        imdb_id = 'tt%s' % imdb_id
    key = ratings_key()
    if not key or not (tmdb_id or imdb_id):
        return {}
    hit = cached_ratings(imdb_id=imdb_id, media_type=media_type, tmdb_id=tmdb_id)
    if hit:
        return hit
    import time as _time
    out = {}
    try:
        if tmdb_id:
            path = '/tmdb/%s/%s' % (kind, tmdb_id)      # TMDb Helper's own path
        else:
            path = '/imdb/%s/%s' % (kind, imdb_id)
        data, _ = _request(path, params={'apikey': key}, timeout=timeout)
        data = data or {}
        for row in data.get('ratings') or []:
            source = str(row.get('source') or '').strip().lower()
            key = _RATING_SOURCE_MAP.get(source)
            value = row.get('value')
            if not key or value in (None, '', 0):
                continue
            if key in ('rt_crit', 'rt_aud', 'metacritic', 'mdblist'):
                out[key] = str(int(float(value)))       # raw 0-100
            elif key == 'trakt':
                # v4.6.3: MDBList serves trakt on /100 (TMDb Helper stores it
                # unconverted in its /100 store) — "75.0" was the old render.
                out[key] = '%.1f' % (float(value) / 10.0)
            else:
                out[key] = '%.1f' % float(value)        # imdb/mal /10, letterboxd /5
        # MDBList's own score lives at the TOP LEVEL of the payload (the
        # array row was a lucky accident on some titles — chip often missing).
        for score_key in ('score', 'score_average'):
            value = data.get(score_key)
            if value not in (None, '', 0):
                out['mdblist'] = str(int(float(value)))
                break
    except Exception as exc:
        xbmc.log('[DexHub] mdblist fetch_ratings(%s/%s) failed: %s'
                 % (tmdb_id or imdb_id, kind, exc), xbmc.LOGDEBUG)
        out = {}
    stamp = (_time.time(), dict(out))
    for cache_key in _ratings_cache_keys(kind, imdb_id=imdb_id, tmdb_id=tmdb_id):
        _RATINGS_CACHE[cache_key] = stamp
    return out


def warm_ratings_async(imdb_id='', media_type='movie', tmdb_id=''):
    """v4.6.3: background cache warm-up.

    The v4.2.2 design ("reuse a warm MDBList result while the source picker
    opens; never fetch in the hot path") shipped without anything that ever
    WARMED the cache — fetch_ratings had zero network callers, so the
    Letterboxd / MDBList / MAL chips (which have no TMDb Helper alias) never
    rendered at all. The source scan now queues this once in Dex Hub's bounded
    optional-work lane while the providers are being queried anyway.
    """
    if not configured() or not (str(tmdb_id or '').strip() or str(imdb_id or '').strip()):
        return
    def _job():
        try:
            fetch_ratings(imdb_id=imdb_id, media_type=media_type, tmdb_id=tmdb_id)
        except Exception:
            pass
    try:
        from .runtime_tasks import submit_optional
        submit_optional(
            _job,
            key='mdblist-rating:%s:%s:%s' % (
                str(media_type or ''), str(tmdb_id or ''), str(imdb_id or '')),
        )
    except Exception as exc:
        xbmc.log('[DexHub] mdblist warm task failed: %s' % exc, xbmc.LOGDEBUG)


_METAHUB_POSTER = 'https://images.metahub.space/poster/large/%s/img'


def watchlist_mirror_rows(limit=200, strict=False):
    """Account watchlist shaped for favorites_store mirror rows (v4.2.0)."""
    out = []
    try:
        rows, cursor, seen = [], '', set()
        while len(rows) < limit:
            batch, next_cursor = fetch_watchlist_items(cursor=cursor, limit=min(100, limit - len(rows)))
            rows.extend(batch)
            if not next_cursor or next_cursor in seen or not batch:
                break
            seen.add(next_cursor)
            cursor = next_cursor
    except Exception as exc:
        xbmc.log('[DexHub] mdblist watchlist mirror failed: %s' % exc, xbmc.LOGDEBUG)
        if strict:
            raise
        return out
    for row in rows or []:
        ids = row.get('ids') or {}
        imdb = str(ids.get('imdb') or ids.get('imdb_id') or
                   row.get('imdb_id') or row.get('imdb') or '').strip()
        tmdb = str(ids.get('tmdb') or ids.get('tmdb_id') or
                   row.get('tmdb_id') or row.get('tmdb') or
                   row.get('id') or '').strip()
        canonical = (imdb if imdb.startswith('tt') else ('tt%s' % imdb)) if imdb else ('tmdb:%s' % tmdb if tmdb else '')
        if not canonical:
            continue
        out.append({
            'media_type': 'series' if str(row.get('_type') or '') == 'show' else 'movie',
            'canonical_id': canonical,
            'title': row.get('title') or '',
            'poster': (row.get('poster') or row.get('poster_url') or
                       ((_METAHUB_POSTER % canonical) if canonical.startswith('tt') else '')),
            'background': row.get('backdrop') or row.get('background') or '',
            'clearlogo': row.get('clearlogo') or '',
            'year': row.get('release_year') or row.get('year') or 0,
            'plot': row.get('overview') or '',
        })
    return out
