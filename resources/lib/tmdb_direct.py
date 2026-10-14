# -*- coding: utf-8 -*-
import json
import os
import sqlite3
import time
import urllib.parse
# v5.4.7: urllib.request is imported inside _request(). This module is
# pulled eagerly via art.py on every render, and the top-level import
# dragged http.client onto every invoker cold start.

import xbmc
import xbmcaddon

from .dexhub.common import profile_path

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()
API_BASE = 'https://api.themoviedb.org/3'
# Bug fix: previously every image (poster, fanart, clearlogo) was fetched at
# /t/p/original, returning 2000+px / 500KB-2MB files for thumbnail-sized
# display slots. The user reported: "tmdb posters downloaded with Plex full
# resolution — please make smaller". TMDb's CDN serves correctly-sized
# variants for free, so we now request appropriate widths per image kind.
# Reference sizes per TMDb docs: posters w185/w342/w500/w780, backdrops
# w300/w780/w1280, logos w92/w154/w300/w500.
IMAGE_BASE_POSTER = 'https://image.tmdb.org/t/p/w500'      # ~70 KB per item
IMAGE_BASE_BACKDROP = 'https://image.tmdb.org/t/p/w1280'   # ~200 KB per item
IMAGE_BASE_LOGO = 'https://image.tmdb.org/t/p/w500'        # logos are small
# Kept for backwards compatibility — used only if a caller passes raw paths
# without specifying a kind.
IMAGE_BASE = IMAGE_BASE_POSTER
CACHE_DB = os.path.join(profile_path(), 'tmdb_direct_cache.sqlite')
POSITIVE_TTL = 30 * 24 * 60 * 60
NEGATIVE_TTL = 6 * 60 * 60
DEFAULT_API_KEY = 'd0667e1fd4d2611331f3d9feca0df2b5'

# In-memory cache (per Python invocation) so a single catalog render doesn't
# reopen sqlite N times for the same items. Bounded to 512 entries with a
# simple FIFO eviction.
_MEM_CACHE = {}
_MEM_CACHE_ORDER = []
_MEM_CACHE_MAX = 512


def _mem_get(key):
    return _MEM_CACHE.get(key)


def _mem_put(key, value):
    if key in _MEM_CACHE:
        return
    _MEM_CACHE[key] = value
    _MEM_CACHE_ORDER.append(key)
    if len(_MEM_CACHE_ORDER) > _MEM_CACHE_MAX:
        old = _MEM_CACHE_ORDER.pop(0)
        _MEM_CACHE.pop(old, None)


def _setting(key, default=''):
    # v5.4.36: TMDb-backed Nuvio Collections must see a key immediately after
    # the user saves it. Under reuselanguageinvoker the cached Addon handle can
    # lag the native settings dialog, so prefer the profile/live reader.
    try:
        from .live_settings import live_setting
        return live_setting(key, default=default)
    except Exception:
        try:
            return ADDON.getSetting(key) or default
        except Exception:
            return default


def _api_key():
    return (_setting('tmdb_api_key', '').strip() or DEFAULT_API_KEY).strip()


def _timeout():
    try:
        return max(4, int(_setting('timeout', '20') or '20'))
    except Exception:
        return 12


def _normalize_media_type(media_type):
    value = str(media_type or 'movie').strip().lower()
    if value in ('tvshow', 'episode', 'season', 'series', 'show', 'tv', 'anime'):
        return 'tv'
    return 'movie'


def _preferred_image_languages(language_override=''):
    override = str(language_override or '').strip().lower().replace('_', '-')
    raw = (_setting('preferred_subtitle_langs', 'ar,en') or 'ar,en').strip()
    langs = [x.strip().lower().replace('_', '-') for x in raw.split(',') if x.strip()]
    out = []
    # Nuvio catalogs must use Nuvio's own TMDb language preference first.
    # Outside Nuvio mode, retain Dex Hub's historical English-first policy.
    ordered = ([override, override.split('-')[0], 'en'] if override else ['en']) + langs + ['null', '']
    for lang in ordered:
        if lang not in out:
            out.append(lang)
    return out or ['en', 'null', '']


# v5.4.43: Mirror NuvioTV's current TMDb locale/artwork selection for
# Nuvio-owned catalog rows. This is intentionally separate from Dex Hub's
# normal TMDb art ranking: Nuvio uses the localized details poster/backdrop
# paths directly and only language-ranks logos from the images response.
_NUVIO_LANGUAGE_DEFAULT_REGION = {
    'ar': 'SA', 'bg': 'BG', 'bs': 'BA', 'cs': 'CZ', 'da': 'DK',
    'de': 'DE', 'el': 'GR', 'es': 'ES', 'et': 'EE', 'fi': 'FI',
    'fr': 'FR', 'he': 'IL', 'hi': 'IN', 'hr': 'HR', 'hu': 'HU',
    'id': 'ID', 'it': 'IT', 'ja': 'JP', 'ko': 'KR', 'lt': 'LT',
    'lv': 'LV', 'nl': 'NL', 'no': 'NO', 'pl': 'PL', 'pt': 'PT',
    'ro': 'RO', 'ru': 'RU', 'sk': 'SK', 'sl': 'SI', 'sr': 'RS',
    'sv': 'SE', 'th': 'TH', 'tr': 'TR', 'uk': 'UA', 'vi': 'VN',
    'zh': 'CN',
}


def _normalize_nuvio_tmdb_language(language):
    raw = str(language or '').strip().replace('_', '-')
    if not raw:
        return 'en'
    parts = raw.split('-')
    if len(parts) == 2:
        normalized = '%s-%s' % (parts[0].lower(), parts[1].upper())
    else:
        normalized = raw.lower()
    # Nuvio maps the Latin-America aggregate locale to TMDb's closest
    # supported locale.
    return 'es-MX' if normalized == 'es-419' else normalized


def _nuvio_logo_image(rows, normalized_language):
    """Select a logo with NuvioTV's current locale ordering.

    Order: exact language+region, same language/no region, same language any
    region, English, language-neutral. Python's sort is stable, matching
    Kotlin sortedWith when scores tie.
    """
    rows = list(rows or [])
    if not rows:
        return {}
    language_code = str(normalized_language or 'en').split('-', 1)[0].lower()
    region = ''
    if '-' in str(normalized_language or ''):
        candidate = str(normalized_language).split('-', 1)[1].upper()
        if len(candidate) == 2:
            region = candidate
    if not region:
        region = _NUVIO_LANGUAGE_DEFAULT_REGION.get(language_code, '')

    def _score(row):
        row = row or {}
        lang = str(row.get('iso_639_1') or '').strip().lower()
        raw_region = row.get('iso_3166_1')
        img_region = str(raw_region or '').strip().upper() if raw_region is not None else ''
        has_region = bool(raw_region)
        return (
            1 if (lang == language_code and img_region == region and bool(region)) else 0,
            1 if (lang == language_code and not has_region) else 0,
            1 if lang == language_code else 0,
            1 if lang == 'en' else 0,
            1 if not lang else 0,
        )

    best = max(enumerate(rows), key=lambda pair: (_score(pair[1]), -pair[0]))[1]
    return best or {}


def _nuvio_localized_title(detail, media_type, normalized_language, fallback_title=''):
    """Return Nuvio's localized title, or empty when it would keep addon text."""
    detail = detail or {}
    if media_type == 'tv':
        raw = str(detail.get('name') or '').strip()
        original = str(detail.get('original_name') or '').strip()
    else:
        raw = str(detail.get('title') or '').strip()
        original = str(detail.get('original_title') or '').strip()
    original_language = str(detail.get('original_language') or '').strip().lower()
    normalized = str(normalized_language or 'en')
    # Nuvio: if TMDb merely echoed the original title because no requested
    # translation exists, don't replace the addon-provided title.
    if (raw and original and raw == original and not normalized.lower().startswith('en')
            and original_language and not normalized.lower().startswith(original_language)):
        return ''
    return raw or str(fallback_title or '').strip()


_DB_READY = []


def _db_conn():
    # v5.10.117: WAL with the file held open (dbkeep). This cache ran in
    # SQLite's default rollback mode: every TMDb answer cached during a
    # listing was a journal file written, synced and deleted.
    from .dexhub import dbkeep
    if not _DB_READY:
        os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    conn = dbkeep.connect(CACHE_DB, timeout=5.0)
    if not _DB_READY:
        conn.execute(
            'CREATE TABLE IF NOT EXISTS art_cache ('
            ' cache_key TEXT PRIMARY KEY,'
            ' expires_at INTEGER NOT NULL,'
            ' payload TEXT NOT NULL'
            ')'
        )
        conn.commit()
        _DB_READY.append(True)
    return conn


def _cache_get(cache_key):
    # In-memory first (fast path; avoids opening sqlite for repeated items in
    # the same render).
    cached = _mem_get(cache_key)
    if cached is not None:
        return cached
    try:
        conn = _db_conn()
        row = conn.execute('SELECT expires_at, payload FROM art_cache WHERE cache_key=?', (cache_key,)).fetchone()
        if not row:
            conn.close()
            return None
        expires_at, payload = row
        if int(expires_at or 0) < int(time.time()):
            conn.execute('DELETE FROM art_cache WHERE cache_key=?', (cache_key,))
            conn.commit()
            conn.close()
            return None
        conn.close()
        result = json.loads(payload or '{}')
        _mem_put(cache_key, result)
        return result
    except Exception:
        return None


def _cache_set(cache_key, payload, ttl):
    _mem_put(cache_key, payload or {})
    try:
        conn = _db_conn()
        conn.execute(
            'INSERT OR REPLACE INTO art_cache(cache_key, expires_at, payload) VALUES (?, ?, ?)',
            (cache_key, int(time.time() + max(60, int(ttl or NEGATIVE_TTL))), json.dumps(payload or {}, ensure_ascii=False))
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _request(path, params=None, timeout=None, max_attempts=3,
             rate_wait=5.0):
    """TMDb JSON request with a shared limiter + bounded transient backoff.

    v5.4.37: Nuvio Collections can contain many TMDb LIST/DISCOVER sources.
    This function used raw urllib and therefore bypassed Dex Hub's host
    limiter entirely, allowing a single Kodi render to create a burst of
    lookups. 429 and throttle-style 503 responses now respect Retry-After and
    back off without turning one failed item into an immediate retry storm.
    """
    import urllib.request  # lazy: keeps http.client off the render import path
    import urllib.error
    api_key = _api_key()
    if not api_key:
        return {}
    query = dict(params or {})
    query['api_key'] = api_key
    url = API_BASE + path + ('?' + urllib.parse.urlencode(query) if query else '')
    req = urllib.request.Request(url, headers={
        'Accept': 'application/json',
        'User-Agent': 'DexHub/%s (Kodi)' % (ADDON.getAddonInfo('version') or '5.4.37'),
    })
    request_timeout = _timeout() if timeout is None else max(1.0, float(timeout))

    try:
        from .ratelimit import limiter, host_of
    except Exception:
        limiter = None
        host_of = None
    # v5.10.117: Dex Hub's connection pool (TLS kept alive). A new connection
    # per request cost a TLS handshake each time: on a box two or three times
    # the request itself, for every title a listing looks up.
    try:
        from .dexhub import client as _client
        pool = _client._ensure_pool()
        urllib3 = _client._URLLIB3
    except Exception:
        pool, urllib3 = None, None

    try:
        attempts = max(1, min(3, int(max_attempts or 1)))
    except Exception:
        attempts = 1
    last_exc = None
    for attempt in range(attempts):
        try:
            if limiter is not None and host_of is not None:
                limiter.acquire(
                    host_of(url), max_wait=max(0.0, float(rate_wait or 0.0)))
            if pool is not None and urllib3 is not None:
                try:
                    resp = pool.request(
                        'GET', url, headers=dict(req.headers),
                        timeout=urllib3.Timeout(connect=min(10.0, request_timeout), read=request_timeout),
                        redirect=True, retries=False, decode_content=True, preload_content=True)
                except Exception as exc:
                    # urllib3 names the address (and with it the API key)
                    raise urllib.error.URLError(type(exc).__name__) from None
                if resp.status >= 400:
                    raise urllib.error.HTTPError(API_BASE + path, resp.status, 'HTTP %d' % resp.status,
                                                 resp.headers, None)
                body = resp.data.decode('utf-8', 'ignore')
                return json.loads(body) if body else {}
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                body = resp.read().decode('utf-8', 'ignore')
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            last_exc = exc
            code = int(getattr(exc, 'code', 0) or 0)
            if (code not in (429, 502, 503, 504)
                    or attempt >= attempts - 1):
                raise
            # Retry-After may be seconds or absent. Cap it so Kodi stays usable.
            try:
                retry_after = float(exc.headers.get('Retry-After') or 0)
            except Exception:
                retry_after = 0.0
            wait = retry_after if retry_after > 0 else (0.8 * (2 ** attempt))
            time.sleep(min(4.0, max(0.5, wait)))
        except Exception as exc:
            last_exc = exc
            # Network hiccups get one small retry only; do not amplify outages.
            if attempt >= min(1, attempts - 1):
                raise
            time.sleep(0.6)
    if last_exc:
        raise last_exc
    return {}


def _image_url(file_path, kind='poster'):
    """Build a TMDb image URL with the appropriate size for `kind`.

    `kind` is one of: 'poster', 'backdrop', 'logo'. We pick a size that's
    large enough for any reasonable display slot in Kodi while keeping
    bandwidth (and the user's Plex download path through Kodi's image
    cache) modest.
    """
    file_path = str(file_path or '').strip()
    if not file_path:
        return ''
    if file_path.startswith('http://') or file_path.startswith('https://'):
        return file_path
    if kind == 'backdrop':
        return IMAGE_BASE_BACKDROP + file_path
    if kind == 'logo':
        # v5.10.97: Kodi cannot draw SVG; TMDb serves every logo as PNG too
        if file_path.lower().endswith('.svg'):
            file_path = file_path[:-4] + '.png'
        return IMAGE_BASE_LOGO + file_path
    return IMAGE_BASE_POSTER + file_path


def _best_image(rows, langs):
    rows = rows or []
    langs = langs or ['en', 'null', '']

    def _lang_rank(row):
        iso = str((row or {}).get('iso_639_1') or '').strip().lower()
        iso_short = iso.split('-')[0] if iso else ''
        for idx, lang in enumerate(langs):
            target = str(lang or '').strip().lower()
            target_short = target.split('-')[0] if target else ''
            if iso == target or (iso_short and target_short and iso_short == target_short):
                return idx
        if not iso:
            return len(langs) + 1
        return len(langs) + 10

    ranked = sorted(
        rows,
        key=lambda row: (
            _lang_rank(row),
            -float((row or {}).get('vote_average') or 0.0),
            -int((row or {}).get('width') or 0),
            -int((row or {}).get('height') or 0),
            -float((row or {}).get('vote_count') or 0.0),
        )
    )
    return ranked[0] if ranked else {}


def _best_backdrop(rows, langs):
    """v5.10.204: a backdrop without words first. A backdrop in a language
    carries the title painted on it, and the cards also draw the clear logo
    over it: the name showed twice, one over the other (a Reddit report)."""
    plain = [row for row in (rows or []) if not str((row or {}).get('iso_639_1') or '').strip()]
    return _best_image(plain, langs) if plain else _best_image(rows, langs)


def _helper_ids(tmdb_id='', imdb_id='', media_type='movie'):
    """v5.10.105: ids TMDb Helper already cached (its database, no network)."""
    try:
        from . import tmdbhelper
        return tmdbhelper.get_external_ids_from_db(
            tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=media_type) or {}
    except Exception:
        return {}


def _resolve_tmdb_from_imdb(imdb_id, media_type, timeout=None, max_attempts=3):
    imdb_id = str(imdb_id or '').strip()
    if not imdb_id:
        return ''
    local = str(_helper_ids(imdb_id=imdb_id, media_type=media_type).get('tmdb_id') or '')
    if local.isdigit():
        return local
    try:
        data = _request('/find/%s' % urllib.parse.quote(imdb_id), {'external_source': 'imdb_id'},
                        timeout=timeout, max_attempts=max_attempts) or {}
    except Exception as exc:
        xbmc.log('[DexHub] tmdb_direct imdb lookup failed: %s' % exc, xbmc.LOGDEBUG)
        return ''
    mt = _normalize_media_type(media_type)
    bucket = 'tv_results' if mt == 'tv' else 'movie_results'
    rows = data.get(bucket) or []
    if rows:
        return str(rows[0].get('id') or '')
    return ''


def show_tmdb_for_episode_imdb(imdb_id, timeout=4):
    """v5.10.229: the SHOW's TMDb id when ``imdb_id`` is an episode's own
    IMDb id ('' for a show's or a film's id). Cached a week."""
    imdb_id = str(imdb_id or '').strip()
    if not imdb_id.startswith('tt') or not _api_key():
        return ''
    key = 'ep_show:%s' % imdb_id
    cached = _cache_get(key)
    if cached is not None:
        return str(cached or '')
    show = ''
    try:
        data = _request('/find/%s' % urllib.parse.quote(imdb_id), {'external_source': 'imdb_id'},
                        timeout=timeout, max_attempts=1, rate_wait=1.0) or {}
        if not data.get('tv_results') and not data.get('movie_results'):
            rows = data.get('tv_episode_results') or []
            if rows:
                show = str(rows[0].get('show_id') or '')
    except Exception:
        return ''
    _cache_set(key, show, ttl=7 * 86400)
    return show


def episode_for_external(tvdb_id='', timeout=4):
    """v5.10.243: {'show_tmdb_id','season','episode'} when ``tvdb_id`` is an
    EPISODE's TVDb id (TMDb's find answers it in tv_episode_results), else
    {}. A title handed over as a movie that carries its episode's TVDb id is
    still that episode for the subtitle add-ons. Cached 30 days."""
    tvdb_id = str(tvdb_id or '').strip().lower()
    if tvdb_id.startswith('tvdb:'):
        tvdb_id = tvdb_id[5:]
    tvdb_id = tvdb_id.split(':', 1)[0]
    if not tvdb_id.isdigit() or not _api_key():
        return {}
    key = 'ep_tvdb:%s' % tvdb_id
    cached = _cache_get(key)
    if cached is not None:
        return dict(cached) if isinstance(cached, dict) else {}
    found = {}
    try:
        data = _request('/find/%s' % tvdb_id, {'external_source': 'tvdb_id'},
                        timeout=timeout, max_attempts=1, rate_wait=1.0) or {}
    except Exception:
        return {}
    rows = data.get('tv_episode_results') or []
    if rows and not data.get('movie_results') and not data.get('tv_results'):
        row = rows[0]
        try:
            season, episode = int(row.get('season_number')), int(row.get('episode_number'))
        except (TypeError, ValueError):
            season = episode = -1
        if row.get('show_id') and season >= 0 and episode > 0:
            found = {'show_tmdb_id': str(row.get('show_id')), 'season': season, 'episode': episode}
    _cache_set(key, found, ttl=30 * 86400)
    return found


def _resolve_tmdb_from_search(title, year, media_type):
    title = str(title or '').strip()
    if not title:
        return ''
    mt = _normalize_media_type(media_type)
    params = {'query': title}
    year = str(year or '').strip()
    if year.isdigit():
        if mt == 'movie':
            params['year'] = year
        else:
            params['first_air_date_year'] = year
    try:
        data = _request('/search/%s' % mt, params) or {}
    except Exception as exc:
        xbmc.log('[DexHub] tmdb_direct search failed: %s' % exc, xbmc.LOGDEBUG)
        return ''
    rows = data.get('results') or []
    if not rows:
        return ''
    return str(rows[0].get('id') or '')


def _resolve_tmdb_id(tmdb_id='', imdb_id='', media_type='movie', title='', year=''):
    tmdb_id = str(tmdb_id or '').strip()
    if tmdb_id.isdigit():
        return tmdb_id
    imdb_id = str(imdb_id or '').strip()
    if imdb_id:
        resolved = _resolve_tmdb_from_imdb(imdb_id, media_type)
        if resolved:
            return resolved
    return _resolve_tmdb_from_search(title, year, media_type)


# v3.9.48: public search function returning full meta records for the
# in-app TMDb search experience. Uses /search/multi so a single query
# returns movies, TV shows, and people interleaved by relevance — same
# as TMDb Helper's behaviour, but rendered inline within DexHub rather
# than requiring a redirect.
def search_multi(query, limit=40):
    """Search TMDb across movies, TV and people simultaneously. Returns a list
    of normalised result dicts ready for rendering, each containing
    media_type, tmdb_id, title, year, poster, backdrop, overview, and
    rating."""
    query = str(query or '').strip()
    if not query or not _api_key():
        return []
    language = 'ar-SA' if any('\u0600' <= ch <= '\u06ff' for ch in query) else 'en-US'
    cache_key = 'search_multi:%s:%s' % (language, query.casefold())
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached or []
    try:
        data = _request('/search/multi', {
            'query': query, 'include_adult': 'false', 'language': language,
        }, timeout=min(float(_timeout()), 6.0)) or {}
    except Exception as exc:
        xbmc.log('[DexHub] tmdb_direct search_multi failed: %s' % exc, xbmc.LOGDEBUG)
        return []
    raw_results = data.get('results') or []
    results = []
    for row in raw_results[:max(1, int(limit))]:
        mt = row.get('media_type') or ''
        if mt not in ('movie', 'tv', 'person'):
            continue
        if mt == 'person':
            profile_path = row.get('profile_path')
            known_for = [item for item in (row.get('known_for') or [])
                         if isinstance(item, dict)][:4]
            known_titles = [item.get('title') or item.get('name') or ''
                            for item in known_for]
            known_titles = [value for value in known_titles if value]
            # People share the same poster panel: profile photos are already
            # portrait artwork and need no additional detail request.
            results.append({
                'media_type': 'person',
                'tmdb_id': str(row.get('id') or ''),
                'title': row.get('name') or '',
                'original_title': row.get('original_name') or '',
                'year': '',
                'overview': ('%s: %s' % (
                    row.get('known_for_department') or 'Known for',
                    ', '.join(known_titles))) if known_titles else
                    (row.get('known_for_department') or ''),
                'poster': _image_url(profile_path, kind='poster') if profile_path else '',
                'backdrop': '',
                'rating': 0.0,
                'department': row.get('known_for_department') or '',
            })
            continue
        title = row.get('title') if mt == 'movie' else row.get('name')
        release_raw = row.get('release_date') if mt == 'movie' else row.get('first_air_date')
        year = ''
        if release_raw and len(release_raw) >= 4 and release_raw[:4].isdigit():
            year = release_raw[:4]
        poster_path = row.get('poster_path')
        backdrop_path = row.get('backdrop_path')
        results.append({
            'media_type': 'series' if mt == 'tv' else 'movie',
            'tmdb_id': str(row.get('id') or ''),
            'title': title or '',
            'original_title': (row.get('original_title') if mt == 'movie'
                               else row.get('original_name')) or '',
            'year': year,
            'overview': row.get('overview') or '',
            'poster': _image_url(poster_path, kind='poster') if poster_path else '',
            'backdrop': _image_url(backdrop_path, kind='backdrop') if backdrop_path else '',
            'rating': row.get('vote_average') or 0.0,
        })
    _cache_set(cache_key, results, ttl=NEGATIVE_TTL if not results else POSITIVE_TTL // 4)
    return results


def search_metas(query, limit=40):
    """Return lightweight Stremio-shaped metas for the unified search."""
    metas = []
    for row in search_multi(query, limit=limit) or []:
        tmdb_id = str(row.get('tmdb_id') or '').strip()
        if not tmdb_id:
            continue
        media_kind = row.get('media_type') or 'movie'
        backdrop = row.get('backdrop') or ''
        if media_kind == 'person':
            metas.append({
                'id': 'person:%s' % tmdb_id, 'type': 'person',
                'name': row.get('title') or '', 'person_id': tmdb_id,
                'description': row.get('overview') or '',
                'poster': row.get('poster') or '',
                'known_for_department': row.get('department') or '',
                '_backend': 'tmdb',
            })
            continue
        metas.append({
            'id': 'tmdb:%s' % tmdb_id, 'type': media_kind,
            'name': row.get('title') or '',
            'original_title': row.get('original_title') or '',
            'moviedb_id': tmdb_id, 'year': row.get('year') or '',
            'releaseInfo': row.get('year') or '',
            'description': row.get('overview') or '',
            'poster': row.get('poster') or '', 'background': backdrop,
            'fanart': backdrop, 'imdbRating': row.get('rating') or 0.0,
            '_backend': 'tmdb',
        })
    return metas


# ── v4.7.9: ready-made collection sets ───────────────────────────────────
# TMDb-native endpoints only, so every row already carries a TMDb id and a
# poster: no id conversion, no per-item lookup, and the existing click path
# opens them like any other catalog row.
READY_SETS = (
    ('trending_movie', 'رائج الآن • أفلام',      '/trending/movie/week',   'movie'),
    ('trending_tv',    'رائج الآن • مسلسلات',   '/trending/tv/week',      'series'),
    ('popular_movie',  'الأكثر شعبية • أفلام',   '/movie/popular',         'movie'),
    ('popular_tv',     'الأكثر شعبية • مسلسلات', '/tv/popular',           'series'),
    ('top_movie',      'الأعلى تقييماً • أفلام',  '/movie/top_rated',      'movie'),
    ('top_tv',         'الأعلى تقييماً • مسلسلات', '/tv/top_rated',        'series'),
    ('now_playing',    'في السينما الآن',        '/movie/now_playing',     'movie'),
    ('upcoming',       'قريباً',                 '/movie/upcoming',        'movie'),
    ('airing_today',   'يُعرض اليوم',            '/tv/airing_today',       'series'),
)


def ready_set_def(set_id):
    for row in READY_SETS:
        if row[0] == str(set_id or ''):
            return row
    return None


def _row_from_tmdb(row, media_type):
    title = row.get('title') or row.get('name') or ''
    release_raw = row.get('release_date') or row.get('first_air_date') or ''
    year = release_raw[:4] if len(str(release_raw)) >= 4 and str(release_raw)[:4].isdigit() else ''
    poster_path = row.get('poster_path')
    backdrop_path = row.get('backdrop_path')
    return {
        'media_type': media_type,
        'tmdb_id': str(row.get('id') or ''),
        'title': title,
        'year': year,
        'released': str(release_raw or ''),
        'releaseInfo': str(release_raw or year or ''),
        'overview': row.get('overview') or '',
        'poster': _image_url(poster_path, kind='poster') if poster_path else '',
        'backdrop': _image_url(backdrop_path, kind='backdrop') if backdrop_path else '',
        'rating': row.get('vote_average') or 0.0,
    }


def ready_set_items(set_id, page=1, limit=40):
    """Fetch one ready-made set. Cached like every other TMDb call."""
    row_def = ready_set_def(set_id)
    if not row_def or not _api_key():
        return []
    _sid, _label, path, media_type = row_def
    page = max(1, int(page or 1))
    cache_key = 'ready_set:%s:%d' % (set_id, page)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached or []
    try:
        data = _request(path, {'page': str(page)}) or {}
    except Exception as exc:
        xbmc.log('[DexHub] tmdb ready_set(%s) failed: %s' % (set_id, exc), xbmc.LOGWARNING)
        return []
    out = []
    for raw in (data.get('results') or [])[:max(1, int(limit))]:
        item = _row_from_tmdb(raw, media_type)
        if item['tmdb_id'] and item['title']:
            out.append(item)
    _cache_set(cache_key, out, ttl=NEGATIVE_TTL if not out else POSITIVE_TTL // 8)
    return out




_NUVIO_FILTER_MAP = {
    'withGenres': 'with_genres', 'with_genres': 'with_genres',
    'withoutGenres': 'without_genres', 'without_genres': 'without_genres',
    'withOriginalLanguage': 'with_original_language', 'with_original_language': 'with_original_language',
    'voteCountGte': 'vote_count.gte', 'vote_count.gte': 'vote_count.gte',
    'voteAverageGte': 'vote_average.gte', 'vote_average.gte': 'vote_average.gte',
    'voteAverageLte': 'vote_average.lte', 'vote_average.lte': 'vote_average.lte',
    'withWatchProviders': 'with_watch_providers', 'with_watch_providers': 'with_watch_providers',
    'watchRegion': 'watch_region', 'watch_region': 'watch_region',
    'withNetworks': 'with_networks', 'with_networks': 'with_networks',
    'withKeywords': 'with_keywords', 'with_keywords': 'with_keywords',
    'withoutKeywords': 'without_keywords', 'without_keywords': 'without_keywords',
    'includeAdult': 'include_adult', 'include_adult': 'include_adult',
    'includeVideo': 'include_video', 'include_video': 'include_video',
    'withRuntimeGte': 'with_runtime.gte', 'with_runtime.gte': 'with_runtime.gte',
    'withRuntimeLte': 'with_runtime.lte', 'with_runtime.lte': 'with_runtime.lte',
}


def _nuvio_discover_params(source, media_type, page=1):
    source = source if isinstance(source, dict) else {}
    filters = source.get('filters') if isinstance(source.get('filters'), dict) else {}
    params = {}

    # v5.4.23: AIO Metadata exports preserve the complete TMDb Discover
    # query under tmdbParams.  Keep those parameters verbatim so self-hosted
    # Kaptain collections resolve to the same real works as the AIO catalog.
    # This covers provider/network/person/status/release-type filters that the
    # compact Nuvio schema does not have first-class keys for.
    raw_params = source.get('tmdbParams') if isinstance(source.get('tmdbParams'), dict) else {}
    for key, value in raw_params.items():
        if value in (None, '', [], {}):
            continue
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        params[str(key)] = str(value)

    for key, value in filters.items():
        if value in (None, '', [], {}):
            continue
        target = _NUVIO_FILTER_MAP.get(str(key))
        if not target:
            continue
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        params[target] = str(value)

    sort_by = str(source.get('sortBy') or filters.get('sortBy') or filters.get('sort_by') or '').strip()
    if sort_by:
        params['sort_by'] = sort_by

    # Kaptain/Nuvio use a generic releaseDateGte/Lte key. TMDb splits movie
    # and TV date fields, so translate based on the source media type.
    date_prefix = 'first_air_date' if _normalize_media_type(media_type) == 'tv' else 'primary_release_date'
    for src_key, suffix in (('releaseDateGte', 'gte'), ('release_date_gte', 'gte'),
                            ('releaseDateLte', 'lte'), ('release_date_lte', 'lte')):
        value = filters.get(src_key)
        if value not in (None, ''):
            params['%s.%s' % (date_prefix, suffix)] = str(value)

    year = filters.get('year')
    if year not in (None, ''):
        params['first_air_date_year' if _normalize_media_type(media_type) == 'tv' else 'primary_release_year'] = str(year)
    # v5.10.107: the choice made on the Home's grid (Sort, Genre, Filter)
    # goes over the source's own values; an empty value drops the source's
    # (its fixed year when a decade is chosen)
    overrides = source.get('dexOverrides') if isinstance(source.get('dexOverrides'), dict) else {}
    special = {}
    for key, value in overrides.items():
        key = str(key)
        if key.startswith('dex_'):
            special[key] = str(value or '')
        elif value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = str(value)
    genre = special.get('dex_genre') or ''
    if genre:
        # narrows the source: added to its own genres (a list of AND genres);
        # a source of alternatives (28|12) takes the chosen genre instead
        own = str(params.get('with_genres') or '')
        if own and '|' not in own:
            parts = [g for g in own.split(',') if g]
            if genre not in parts:
                parts.append(genre)
            params['with_genres'] = ','.join(parts)
        else:
            params['with_genres'] = genre

    def _floor(key, value):
        try:
            if float(value) > float(params.get(key) or 0):
                params[key] = str(value)
        except Exception:
            params[key] = str(value)
    if special.get('dex_min_votes'):
        _floor('vote_count.gte', special['dex_min_votes'])
    if special.get('dex_min_rating'):
        _floor('vote_average.gte', special['dex_min_rating'])
    if special.get('dex_until_today'):
        prefix = 'first_air_date' if _normalize_media_type(media_type) == 'tv' else 'primary_release_date'
        today = time.strftime('%Y-%m-%d')
        if not params.get(prefix + '.lte') or params[prefix + '.lte'] > today:
            params[prefix + '.lte'] = today
    # Page always belongs to the caller, never to a saved export.
    params['page'] = str(max(1, int(page or 1)))
    return params


def genre_list(media_type='movie', language=''):
    """[(id, name)] of TMDb's movie or TV genres in a language (v5.10.107),
    for the Genre button of a collection's TMDb source. Cached a week."""
    mt = _normalize_media_type(media_type)
    lang = _normalize_nuvio_tmdb_language(language or 'en')
    key = 'genre_list:%s:%s' % (mt, lang)
    cached = _cache_get(key)
    if cached is not None:
        return [(str(g[0]), str(g[1])) for g in (cached.get('genres') or []) if len(g) == 2]
    if not _api_key():
        return []
    try:
        data = _request('/genre/%s/list' % mt, {'language': lang}, timeout=6, max_attempts=2,
                        rate_wait=1.0) or {}
    except Exception as exc:
        xbmc.log('[DexHub] tmdb genre list failed: %s' % exc, xbmc.LOGWARNING)
        return []
    out = [(str(g.get('id')), str(g.get('name') or '').strip()) for g in (data.get('genres') or [])
           if isinstance(g, dict) and g.get('id') and str(g.get('name') or '').strip()]
    _cache_set(key, {'genres': out}, 7 * 24 * 3600 if out else NEGATIVE_TTL)
    return out


def nuvio_source_items(source, page=1, limit=40):
    """Fetch a Nuvio/Kaptain native TMDb source.

    Supports DISCOVER, LIST and COLLECTION. Results use the same lightweight
    row shape as ``ready_set_items`` so Dex Hub's existing click/source-picker
    path can render them without per-item conversion work.
    """
    source = source if isinstance(source, dict) else {}
    if not _api_key():
        return []
    media_type = 'series' if _normalize_media_type(source.get('mediaType') or source.get('type') or 'movie') == 'tv' else 'movie'
    source_type = str(source.get('tmdbSourceType') or 'DISCOVER').strip().upper()
    tmdb_id = str(source.get('tmdbId') or '').strip()
    try:
        page = max(1, int(page or 1))
    except Exception:
        page = 1
    try:
        limit = max(1, int(limit or 40))
    except Exception:
        limit = 40

    cache_blob = json.dumps(source, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    import hashlib as _hashlib
    cache_key = 'nuvio_tmdb:%s:%d:%s' % (source_type, page, _hashlib.sha1(cache_blob.encode('utf-8')).hexdigest()[:16])
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached or []

    try:
        if source_type == 'LIST' and tmdb_id:
            data = _request(
                '/list/%s' % urllib.parse.quote(tmdb_id),
                {'page': str(page)}, timeout=3, max_attempts=1,
                rate_wait=0.5) or {}
            raws = data.get('items') or []
            mixed = True
        elif source_type == 'COLLECTION' and tmdb_id:
            # TMDb collection endpoint is not paginated. Keep page>1 empty so
            # Kodi never shows duplicate "more" pages.
            if page > 1:
                return []
            data = _request(
                '/collection/%s' % urllib.parse.quote(tmdb_id),
                timeout=3, max_attempts=1, rate_wait=0.5) or {}
            raws = data.get('parts') or []
            mixed = False
        else:
            mt = _normalize_media_type(media_type)
            data = _request(
                '/discover/%s' % mt,
                _nuvio_discover_params(source, media_type, page=page),
                timeout=3, max_attempts=1, rate_wait=0.5) or {}
            raws = data.get('results') or []
            mixed = False
    except Exception as exc:
        xbmc.log('[DexHub] Nuvio TMDb source failed: %s' % exc, xbmc.LOGWARNING)
        return []

    out = []
    for raw in raws[:limit]:
        if not isinstance(raw, dict):
            continue
        raw_mt = media_type
        if mixed:
            mt_token = str(raw.get('media_type') or '').lower()
            if mt_token == 'tv' or (not mt_token and raw.get('name') and not raw.get('title')):
                raw_mt = 'series'
            else:
                raw_mt = 'movie'
        item = _row_from_tmdb(raw, raw_mt)
        if item.get('tmdb_id') and item.get('title'):
            out.append(item)
    _cache_set(cache_key, out, ttl=NEGATIVE_TTL if not out else POSITIVE_TTL // 8)
    return out

def collection_art(tmdb_id):
    """Poster/backdrop of a TMDb film collection (e.g. "Die Hard Collection").

    v5.4.26 — Kaptain "Film Collections" folders are native COLLECTION
    sources; their selector tiles now show the official collection poster
    instead of a repeated generic section icon. Cached on disk so a folder
    of 28 collections only pays the lookups on its very first open.
    """
    tmdb_id = str(tmdb_id or '').strip()
    if not tmdb_id.isdigit() or not _api_key():
        return {}
    cache_key = 'collection_art:%s' % tmdb_id
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached or {})
    try:
        data = _request('/collection/%s' % tmdb_id) or {}
    except Exception as exc:
        xbmc.log('[DexHub] collection art failed %s: %s' % (tmdb_id, exc), xbmc.LOGDEBUG)
        data = {}
    out = {}
    poster = data.get('poster_path')
    backdrop = data.get('backdrop_path')
    if poster:
        out['poster'] = _image_url(poster, kind='poster')
    if backdrop:
        out['fanart'] = _image_url(backdrop, kind='backdrop')
    _cache_set(cache_key, out, ttl=POSITIVE_TTL if out else NEGATIVE_TTL)
    return out


def imdb_id_for(tmdb_id, media_type='movie', timeout=None):
    """Fetch the IMDb id for a given TMDb id when DexHub's playback path
    needs to feed an imdb identifier to a Stremio provider. Cached."""
    tmdb_id = str(tmdb_id or '').strip()
    if not tmdb_id.isdigit():
        return ''
    mt = _normalize_media_type(media_type)
    # v5.10.105: TMDb Helper's cache answers most titles at once (it keeps
    # every title it showed with its external ids).
    local = str(_helper_ids(tmdb_id=tmdb_id, media_type=mt).get('imdb_id') or '')
    if local.startswith('tt'):
        return local
    if not _api_key():
        return ''
    cache_key = 'imdb_for:%s:%s' % (mt, tmdb_id)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached or ''
    try:
        data = _request('/%s/%s/external_ids' % (mt, tmdb_id),
                        timeout=timeout) or {}
        imdb = (data.get('imdb_id') or '').strip()
    except Exception:
        imdb = ''
    _cache_set(cache_key, imdb, ttl=POSITIVE_TTL if imdb else NEGATIVE_TTL)
    return imdb


def english_titles_for(tmdb_id, media_type='movie'):
    """The ENGLISH and ORIGINAL titles of an item — cached.

    v3.9.215 — the reason Plex kept answering "no match".

    Dex Hub searched the user's Plex libraries with the title it had, which for
    an Arabic user is the ARABIC one ("برشامة") — while the library catalogues
    the film under its English name ("Cheat Sheet"). For episodes it was worse:
    the title passed was literally "الحلقة 16" (Episode 16), so Plex was asked
    for a show by that name and, truthfully, found nothing.
    """
    tmdb_id = str(tmdb_id or '').strip()
    mt = _normalize_media_type(media_type)
    if not tmdb_id.isdigit():
        return []
    try:
        from . import tmdbhelper
        local = tmdbhelper.get_english_titles_from_db(tmdb_id=tmdb_id, media_type=mt) or []
    except Exception:
        local = []
    if local:
        return list(local)      # v5.10.105: TMDb Helper's cache, no network
    if not _api_key():
        return []
    cache_key = 'en_titles:%s:%s' % (mt, tmdb_id)
    cached = _cache_get(cache_key)
    if cached is not None:
        return list(cached or [])
    titles = []
    try:
        # v5.10.26: this runs on the source-scan hot path before the loading
        # window opens; one short attempt, never the 3x global-timeout ladder.
        data = _request('/%s/%s' % (mt, tmdb_id), params={'language': 'en-US'},
                        timeout=4, max_attempts=1, rate_wait=1.0) or {}
        for key in ('title', 'name', 'original_title', 'original_name'):
            value = str(data.get(key) or '').strip()
            if value and value.casefold() not in [t.casefold() for t in titles]:
                titles.append(value)
    except Exception:
        titles = []
    _cache_set(cache_key, titles, ttl=POSITIVE_TTL if titles else NEGATIVE_TTL)
    return list(titles)


def titles_for_imdb(imdb_id, media_type='movie'):
    """English + original titles for an IMDb id — one call, cached.

    v3.9.219 — the last gap, straight from the log:

        plex lookup: ids=imdb_id=tt33076347 titles=1 -> 0 item(s)

    Only ONE title (the Arabic one) was ever sent to Plex, because the English
    title was fetched from /movie/<tmdb_id> — and this item has NO tmdb_id.
    TMDb's /find endpoint resolves an IMDb id directly, and returns the item in
    English, so Plex can finally be asked using the name its library actually
    uses.
    """
    imdb_id = str(imdb_id or '').strip()
    if not imdb_id.startswith('tt'):
        return []
    mt = _normalize_media_type(media_type)
    try:
        from . import tmdbhelper
        local = tmdbhelper.get_english_titles_from_db(imdb_id=imdb_id, media_type=mt) or []
    except Exception:
        local = []
    if local:
        return list(local)      # v5.10.105: TMDb Helper's cache, no network
    if not _api_key():
        return []
    cache_key = 'find_titles:%s:%s' % (mt, imdb_id)
    cached = _cache_get(cache_key)
    if cached is not None:
        return list(cached or [])

    titles = []
    try:
        data = _request('/find/%s' % imdb_id,
                        params={'external_source': 'imdb_id',
                                'language': 'en-US'},
                        timeout=4, max_attempts=1, rate_wait=1.0) or {}
        bucket = ('tv_results' if mt == 'tv' else 'movie_results')
        rows = list(data.get(bucket) or [])
        if not rows:
            # An episode id resolves to tv_episode_results; the SHOW name is
            # what Plex needs, and it rides along there.
            rows = list(data.get('tv_episode_results') or []) + \
                   list(data.get('tv_results') or []) + \
                   list(data.get('movie_results') or [])
        for row in rows[:2]:
            for key in ('title', 'name', 'original_title', 'original_name',
                        'show_name'):
                value = str(row.get(key) or '').strip()
                if value and value.casefold() not in [t.casefold() for t in titles]:
                    titles.append(value)
    except Exception:
        titles = []
    _cache_set(cache_key, titles, ttl=POSITIVE_TTL if titles else NEGATIVE_TTL)
    return list(titles)


def art_cached_only(tmdb_id='', imdb_id='', media_type='movie', title='', year=''):
    """Return TMDb art ONLY if it is already cached — never touches the network.

    Used on the Plex/Emby render path: a listing must never wait on TMDb. On a
    miss the caller keeps the server art for this pass and warms TMDb off-thread
    so the next visit is an instant cache hit.
    """
    mt = _normalize_media_type(media_type)
    cache_key = 'art:%s:%s:%s:%s:%s' % (mt, tmdb_id or '', imdb_id or '', title or '', year or '')
    cached = _cache_get(cache_key)
    if not cached and (title or year) and (tmdb_id or imdb_id):
        # v5.10.215: the same title looked up by its ids alone (Continue
        # Watching's warm-up asks without a name)
        cached = _cache_get('art:%s:%s:%s::' % (mt, tmdb_id or '', imdb_id or ''))
    return cached if isinstance(cached, dict) else {}


def _season_show(tmdb_id='', imdb_id='', cached_only=False):
    """The show's TMDb id for a season lookup ('' when unknown)."""
    show = str(tmdb_id or '').strip()
    if show.isdigit():
        return show
    imdb_id = str(imdb_id or '').split(':')[0]
    if not imdb_id.startswith('tt'):
        return ''
    # v5.10.214: the IMDb to TMDb answer is kept, so a listing that reads
    # cached only finds the season too (and a failed lookup is not repeated)
    known = _cache_get('show_of_imdb:%s' % imdb_id)
    if known is not None:
        return str(known) if str(known).isdigit() else None
    if cached_only:
        return ''
    show = str(_resolve_tmdb_id(imdb_id=imdb_id, media_type='series') or '')
    show = show if show.isdigit() else ''
    _cache_set('show_of_imdb:%s' % imdb_id, show, POSITIVE_TTL if show else NEGATIVE_TTL)
    return show


def season_episodes(tmdb_id='', imdb_id='', season=0, cached_only=False, language=''):
    """v5.10.214: a season's episodes as TMDb names them, {'3': {'name', 'still'}}.

    One request per season and language, kept a month (a failure six hours),
    shared by the episode pictures and the episode names of the cards. None
    when it is not known yet and ``cached_only`` forbids asking."""
    try:
        season = int(season or 0)
    except (TypeError, ValueError):
        return {}
    show = _season_show(tmdb_id, imdb_id, cached_only)
    if show is None:
        return {}           # TMDb does not know this IMDb id (asked lately)
    if not show:
        return None if cached_only else {}
    language = str(language or '').strip()
    cache_key = 'season_eps:%s:%d:%s' % (show, season, language or 'en')
    out = _cache_get(cache_key)
    if out is not None:
        return out
    if cached_only or not _api_key():
        return None if cached_only else {}
    try:
        params = {'language': language} if language else None
        data = _request('/tv/%s/season/%d' % (show, season), params, timeout=6, max_attempts=1) or {}
    except Exception:
        data = {}
    out = {}
    for row in data.get('episodes') or []:
        if row.get('episode_number') is None:
            continue
        out[str(row['episode_number'])] = {
            'name': str(row.get('name') or '').strip(),
            'still': _image_url(row['still_path'], kind='backdrop') if row.get('still_path') else '',
            'overview': str(row.get('overview') or '').strip()[:600],
        }
    _cache_set(cache_key, out, POSITIVE_TTL if out else NEGATIVE_TTL)
    return out


def _card_language():
    try:
        from .continue_names import _language
        return _language()
    except Exception:
        return ''


def episode_still(tmdb_id='', imdb_id='', season=0, episode=0, cached_only=False):
    """v5.10.208: an episode's own picture (TMDb's still), for Continue
    Watching and Next Up when the user prefers it to the show's art. One
    request per season (shared with the episode names), kept a month; ''
    when there is none."""
    try:
        episode = int(episode or 0)
    except (TypeError, ValueError):
        return ''
    if episode <= 0:
        return ''
    rows = season_episodes(tmdb_id, imdb_id, season, cached_only, _card_language()) or {}
    return (rows.get(str(episode)) or {}).get('still') or ''


def episode_name(tmdb_id='', imdb_id='', season=0, episode=0, cached_only=False):
    """v5.10.214: an episode's own name from TMDb, in the cards' language and
    in English when that language only has "Episode 3" for it; '' if none."""
    try:
        episode = int(episode or 0)
    except (TypeError, ValueError):
        return ''
    if episode <= 0:
        return ''
    from .episode_titles import generic
    language = _card_language()
    rows = season_episodes(tmdb_id, imdb_id, season, cached_only, language) or {}
    name = (rows.get(str(episode)) or {}).get('name') or ''
    if name and not generic(name):
        return name
    if language and not language.lower().startswith('en') and rows:
        rows = season_episodes(tmdb_id, imdb_id, season, cached_only, 'en-US') or {}
        name = (rows.get(str(episode)) or {}).get('name') or ''
        if name and not generic(name):
            return name
    return ''


def art_for(tmdb_id='', imdb_id='', media_type='movie', title='', year=''):
    mt = _normalize_media_type(media_type)
    cache_key = 'art:%s:%s:%s:%s:%s' % (mt, tmdb_id or '', imdb_id or '', title or '', year or '')
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not _api_key():
        return {}

    resolved_tmdb = _resolve_tmdb_id(tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=mt, title=title, year=year)
    if not resolved_tmdb:
        out = {}
        _cache_set(cache_key, out, NEGATIVE_TTL)
        return out

    langs = _preferred_image_languages()
    image_langs = ','.join([x for x in langs if x and x != 'null'] + ['null'])
    # v5.10.117: one request for the images and the ids (append_to_response)
    # instead of two
    images = ext = None
    try:
        detail = _request('/%s/%s' % (mt, resolved_tmdb), {
            'append_to_response': 'images,external_ids',
            'include_image_language': image_langs,
        }) or {}
        if isinstance(detail.get('images'), dict):
            images = detail['images']
        if isinstance(detail.get('external_ids'), dict):
            ext = detail['external_ids']
    except Exception as exc:
        xbmc.log('[DexHub] tmdb_direct details failed: %s' % exc, xbmc.LOGDEBUG)
    if images is None:
        try:
            images = _request('/%s/%s/images' % (mt, resolved_tmdb), {
                'include_image_language': image_langs
            }) or {}
        except Exception as exc:
            xbmc.log('[DexHub] tmdb_direct images failed: %s' % exc, xbmc.LOGDEBUG)
            images = {}
    if ext is None:
        ext = {}
        if not imdb_id:
            try:
                ext = _request('/%s/%s/external_ids' % (mt, resolved_tmdb)) or {}
            except Exception:
                ext = {}

    poster = _best_image(images.get('posters') or [], langs)
    backdrop = _best_backdrop(images.get('backdrops') or [], langs)
    logo = _best_image(images.get('logos') or [], langs)
    poster_path = (poster or {}).get('file_path') or ''
    backdrop_path = (backdrop or {}).get('file_path') or ''
    out = {
        'tmdb_id': resolved_tmdb,
        'imdb_id': str(ext.get('imdb_id') or imdb_id or '').strip(),
        'poster': _image_url(poster_path, kind='poster'),
        'fanart': _image_url(backdrop_path, kind='backdrop'),
        'landscape': _image_url(backdrop_path, kind='backdrop'),
        'clearlogo': _image_url((logo or {}).get('file_path') or '', kind='logo'),
    }
    ttl = POSITIVE_TTL if any(out.get(k) for k in ('poster', 'fanart', 'clearlogo')) else NEGATIVE_TTL
    _cache_set(cache_key, out, ttl)
    return out


def _localized_overview(detail, langs):
    """Keep the requested-language overview; translations only fill its absence."""
    base = str((detail or {}).get('overview') or '').strip()
    if base:
        return base
    translations = ((detail or {}).get('translations') or {}).get('translations') or []
    by_lang = {}
    for tr_row in translations:
        iso = str(tr_row.get('iso_639_1') or '').strip().lower()
        data = tr_row.get('data') or {}
        text = str(data.get('overview') or '').strip()
        if iso and text and iso not in by_lang:
            by_lang[iso] = text
    for lang in (langs or []):
        short = str(lang or '').strip().lower().split('-')[0]
        if short and short in by_lang:
            return by_lang[short]
    return by_lang.get('en', '')


def _meta_for_cache_key(tmdb_id='', imdb_id='', media_type='movie', title='',
                        year='', language_override='', nuvio_exact=False):
    """Build the exact key shared by network and cache-only metadata reads."""
    mt = _normalize_media_type(media_type)
    # v5.10.227: a show's entry carries its seasons and status from now on
    if mt == 'tv':
        mt = 'tv2'
    return 'meta-v2:%s:%s:%s:%s:%s:%s:%s' % (
        mt, tmdb_id or '', imdb_id or '', title or '', year or '',
        str(language_override or '').lower(),
        'nuvio1to1' if nuvio_exact else 'dex')


def meta_for_cached_only(tmdb_id='', imdb_id='', media_type='movie', title='',
                         year='', language_override='', nuvio_exact=False):
    """Return an already-cached metadata payload without touching the network.

    Nuvio/AF3 home widgets use this path while Kodi is building a directory.
    A cache miss intentionally returns immediately so seven simultaneous
    widgets cannot turn into hundreds of serialized TMDb detail requests.
    """
    cached = _cache_get(_meta_for_cache_key(
        tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=media_type,
        title=title, year=year, language_override=language_override,
        nuvio_exact=nuvio_exact))
    return dict(cached) if isinstance(cached, dict) else {}


def metas_for_cached_only(requests):
    """Batch cache-only lookup using at most one SQLite connection.

    Kodi can start several AF3 widgets together. Reading a whole catalog page
    in one transaction avoids repeatedly opening the database on slower flash
    storage while retaining the hard guarantee that no network call occurs.
    """
    rows = list(requests or [])
    if not rows:
        return []
    keys = [_meta_for_cache_key(**dict(row or {})) for row in rows]
    out = [None] * len(keys)
    missing = {}
    for idx, key in enumerate(keys):
        cached = _mem_get(key)
        if cached is not None:
            out[idx] = dict(cached) if isinstance(cached, dict) else {}
        else:
            missing.setdefault(key, []).append(idx)

    if missing:
        conn = None
        try:
            conn = _db_conn()
            placeholders = ','.join('?' for _key in missing)
            query = ('SELECT cache_key, expires_at, payload FROM art_cache '
                     'WHERE cache_key IN (%s)') % placeholders
            expired = []
            now = int(time.time())
            for cache_key, expires_at, payload in conn.execute(
                    query, tuple(missing.keys())).fetchall():
                if int(expires_at or 0) < now:
                    expired.append(cache_key)
                    continue
                try:
                    value = json.loads(payload or '{}')
                except Exception:
                    value = {}
                _mem_put(cache_key, value)
                for idx in missing.get(cache_key, ()):
                    out[idx] = dict(value) if isinstance(value, dict) else {}
            if expired:
                conn.executemany(
                    'DELETE FROM art_cache WHERE cache_key=?',
                    [(key,) for key in expired])
                conn.commit()
        except Exception:
            pass
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    return [value if isinstance(value, dict) else {} for value in out]


def meta_for(tmdb_id='', imdb_id='', media_type='movie', title='', year='', language_override='', nuvio_exact=False,
             timeout=None, max_attempts=3):
    """Full TMDb metadata in ONE network round-trip.

    v3.9.144: extends the TMDb-first art policy to TEXT. Appends
    credits + images + external_ids + translations to the detail call so a
    single request returns plot, year, genres, rating, runtime, studios,
    cast AND artwork. Normalized to DexHub's existing meta keys. Returns {}
    when no API key / resolution / HTTP fails (callers keep addon meta).

    When ``nuvio_exact`` is true, artwork/title selection mirrors NuvioTV:
    localized detail poster_path/backdrop_path are authoritative and logos use
    Nuvio's language/region ranking. This avoids Dex Hub picking a different
    poster from TMDb's image gallery even with the same language settings.
    """
    mt = _normalize_media_type(media_type)
    cache_key = _meta_for_cache_key(
        tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=mt, title=title,
        year=year, language_override=language_override,
        nuvio_exact=nuvio_exact)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not _api_key():
        return {}

    if not title and not str(tmdb_id or '').isdigit():
        resolved_tmdb = _resolve_tmdb_from_imdb(imdb_id, mt, timeout=timeout, max_attempts=max_attempts) if imdb_id else ''
    else:
        resolved_tmdb = _resolve_tmdb_id(tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=mt, title=title, year=year)
    if not resolved_tmdb:
        out = {}
        _cache_set(cache_key, out, 180)
        return out

    langs = _preferred_image_languages(language_override=language_override)
    if nuvio_exact:
        detail_lang = _normalize_nuvio_tmdb_language(language_override or 'en')
        language_code = detail_lang.split('-', 1)[0]
        include_image_language = ','.join([language_code, detail_lang, 'en', 'null'])
    else:
        detail_lang = next((x for x in langs if x and x != 'null'), 'en')
        include_image_language = ','.join([x for x in langs if x and x != 'null'] + ['null'])
    try:
        detail = _request('/%s/%s' % (mt, resolved_tmdb), {
            'language': detail_lang,
            'append_to_response': 'credits,images,external_ids,translations',
            'include_image_language': include_image_language,
        }, timeout=timeout, max_attempts=max_attempts) or {}
    except Exception as exc:
        xbmc.log('[DexHub] tmdb_direct meta_for failed: %s' % exc, xbmc.LOGDEBUG)
        out = {}
        _cache_set(cache_key, out, 180)
        return out

    if not detail:
        out = {}
        _cache_set(cache_key, out, 180)
        return out

    images = detail.get('images') or {}
    if nuvio_exact:
        # NuvioTV does NOT rank poster/backdrop gallery rows here. It uses the
        # localized details response directly; only the logo is chosen from
        # the images response. This is the key to 1:1 poster parity.
        poster_path = str(detail.get('poster_path') or '').strip()
        backdrop_path = str(detail.get('backdrop_path') or '').strip()
        logo = _nuvio_logo_image(images.get('logos') or [], detail_lang)
    else:
        poster = _best_image(images.get('posters') or [], langs)
        backdrop = _best_backdrop(images.get('backdrops') or [], langs)
        poster_path = (poster or {}).get('file_path') or detail.get('poster_path') or ''
        backdrop_path = (backdrop or {}).get('file_path') or detail.get('backdrop_path') or ''
        logo = _best_image(images.get('logos') or [], langs)
    ext = detail.get('external_ids') or {}

    if mt == 'tv':
        name = (_nuvio_localized_title(detail, mt, detail_lang, title) if nuvio_exact
                else str(detail.get('name') or detail.get('original_name') or title or '').strip())
        date = str(detail.get('first_air_date') or '').strip()
    else:
        name = (_nuvio_localized_title(detail, mt, detail_lang, title) if nuvio_exact
                else str(detail.get('title') or detail.get('original_title') or title or '').strip())
        date = str(detail.get('release_date') or '').strip()
    year_val = date[:4] if date[:4].isdigit() else (str(year or '').strip())

    genres = [str(g.get('name')).strip() for g in (detail.get('genres') or []) if g.get('name')]

    try:
        rating = round(float(detail.get('vote_average') or 0.0), 1)
    except Exception:
        rating = 0.0
    try:
        votes = int(detail.get('vote_count') or 0)
    except Exception:
        votes = 0

    runtime_min = 0
    if mt == 'tv':
        ert = detail.get('episode_run_time') or []
        if isinstance(ert, list) and ert:
            try:
                runtime_min = int(ert[0] or 0)
            except Exception:
                runtime_min = 0
    else:
        try:
            runtime_min = int(detail.get('runtime') or 0)
        except Exception:
            runtime_min = 0

    if mt == 'tv':
        studios = [str(s.get('name')).strip() for s in (detail.get('networks') or []) if s.get('name')]
    else:
        studios = [str(s.get('name')).strip() for s in (detail.get('production_companies') or []) if s.get('name')]
    # v5.10.105: the logos come in the same answer (networks first for a series)
    studio_logos = []
    for company in (list(detail.get('networks') or []) + list(detail.get('production_companies') or [])):
        company_name = str((company or {}).get('name') or '').strip()
        if not company_name or company_name.casefold() in [x['name'].casefold() for x in studio_logos]:
            continue
        company_logo = str((company or {}).get('logo_path') or '').strip()
        if company_logo:
            company_logo = 'https://image.tmdb.org/t/p/w300' + company_logo
            if company_logo.lower().endswith('.svg'):
                company_logo = company_logo[:-4] + '.png'
        studio_logos.append({'name': company_name, 'logo': company_logo})
        if len(studio_logos) >= 3:
            break

    cast = []
    for member in ((detail.get('credits') or {}).get('cast') or [])[:15]:
        nm = str(member.get('name') or '').strip()
        if not nm:
            continue
        cast.append({
            'name': nm,
            'role': str(member.get('character') or '').strip(),
            'thumbnail': _image_url(member.get('profile_path') or '', kind='logo'),
        })

    director = ''
    writers = []
    for member in ((detail.get('credits') or {}).get('crew') or []):
        job = str(member.get('job') or '').strip()
        nm = str(member.get('name') or '').strip()
        if not nm:
            continue
        if job == 'Director' and not director:
            director = nm
        elif job in ('Writer', 'Screenplay', 'Author'):
            if nm not in writers:
                writers.append(nm)

    # Nuvio keeps the overview from the localized details response. If TMDb
    # has no overview in that locale, the enrichment stays empty and the
    # original addon description survives the merge; it does not silently
    # replace it with an English translation.
    overview = (str(detail.get('overview') or '').strip() if nuvio_exact
                else _localized_overview(detail, langs))
    country = ''
    countries = detail.get('production_countries') or detail.get('origin_country') or []
    if isinstance(countries, list) and countries:
        first = countries[0]
        country = str(first.get('name') if isinstance(first, dict) else first or '').strip()

    out = {
        'tmdb_id': str(resolved_tmdb),
        'imdb_id': str(ext.get('imdb_id') or imdb_id or '').strip(),
        'tvdb_id': str(ext.get('tvdb_id') or '').strip(),
        'name': name,
        'title': name,
        'year': year_val,
        'description': overview,
        'overview': overview,
        'genres': genres,
        'imdbRating': ('%.1f' % rating) if rating else '',
        'rating': rating,
        'vote_count': votes,
        'runtime': runtime_min,
        'studio': studios,
        'studio_logos': studio_logos,
        # v5.10.227: a show's seasons and status (the Home's hero chips)
        'number_of_seasons': int(detail.get('number_of_seasons') or 0) if mt == 'tv' else 0,
        'country': country,
        'director': director,
        'writer': writers,
        'cast': cast,
        'tagline': str(detail.get('tagline') or '').strip(),
        'status': str(detail.get('status') or '').strip(),
        'poster': _image_url(poster_path, kind='poster'),
        'fanart': _image_url(backdrop_path, kind='backdrop'),
        'landscape': _image_url(backdrop_path, kind='backdrop'),
        'clearlogo': _image_url((logo or {}).get('file_path') or '', kind='logo'),
        '_nuvio_exact_art': '1' if nuvio_exact else '',
        '_nuvio_tmdb_language': detail_lang if nuvio_exact else '',
    }
    has_payload = bool(out.get('description') or out.get('poster') or out.get('genres') or out.get('cast'))
    # A poster-only result must not suppress richer data for thirty days.
    from .auto_metadata import complete
    _cache_set(cache_key, out, POSITIVE_TTL if complete(out) else (3600 if has_payload else 180))
    return out


def merge_into_meta(addon_meta, tmdb_meta, prefer_tmdb_text=False):
    """Fill gaps in addon_meta from tmdb_meta without destroying good data.

    Mirrors the poster policy: TMDb fills/corrects, addon stays the base.
      - Text: addon wins when present; TMDb fills blanks (prefer_tmdb_text
        flips this for streaming-only providers with junk descriptions).
      - Lists (genres/cast/studio/writer): whichever is non-empty, addon first.
      - IDs: only fill blanks, never overwrite.
    """
    if not isinstance(addon_meta, dict):
        addon_meta = {}
    if not isinstance(tmdb_meta, dict) or not tmdb_meta:
        return addon_meta
    out = dict(addon_meta)

    text_keys = ('description', 'overview', 'name', 'title', 'year',
                 'imdbRating', 'runtime', 'tagline', 'director', 'country', 'status')
    for key in text_keys:
        tval = tmdb_meta.get(key)
        if not tval:
            continue
        aval = out.get(key)
        if prefer_tmdb_text or not aval:
            out[key] = tval

    for key in ('genres', 'cast', 'studio', 'writer'):
        tval = tmdb_meta.get(key) or []
        aval = out.get(key) or []
        if prefer_tmdb_text:
            out[key] = tval or aval
        else:
            out[key] = aval or tval

    for key in ('imdb_id', 'tmdb_id', 'tvdb_id'):
        if not out.get(key) and tmdb_meta.get(key):
            out[key] = tmdb_meta.get(key)

    return out
