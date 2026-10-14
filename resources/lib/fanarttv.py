# -*- coding: utf-8 -*-
"""Fanart.tv enrichment.

Fetches the high-quality non-poster artwork that TMDb does not provide:
clearart, clearlogo (multi-language), banner, discart (movies only),
landscape, characterart (anime).

Modern skins (Estuary MOD V2, Arctic Fuse 2, Aura, AH3) read these to
render the rich item view that makes lists feel "TMDb Helper-grade".

Caching:
  * In-memory dict for the current Python invoker
  * SQLite table piggybacking on meta_cache.db (separate table)
  * Default TTL 14 days — fanart.tv updates are rare

Rate limiting:
  * Soft: 5 concurrent requests across the whole addon (semaphore)
  * Cache misses on a single canonical id within 6h are NOT retried
    (negative cache) so a movie with no fanart doesn't keep hitting the API
"""
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request

import xbmcaddon

from .log import log
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

# Public free key — fanart.tv allows registered "personal" keys too. The
# personal key (entered in settings) takes priority when set.
_DEFAULT_API_KEY = 'fcca59bee130b70db37ee43e63f8d6c1'  # well-known public addon key
_NEGATIVE_TTL = 6 * 3600  # don't re-hit a "no art" item for 6h
_DEFAULT_TTL = 14 * 86400

DB_PATH = os.path.join(profile_path(), 'fanarttv_cache.db')
_DB_LOCK = threading.Lock()
_DB_READY = False
_MEM = {}
_MEM_LOCK = threading.Lock()

# Concurrency throttle — fanart.tv asks "please be nice".
_SEMAPHORE = threading.Semaphore(5)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS fanart (
    media_type TEXT NOT NULL,
    art_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (media_type, art_id)
)
"""


def _connect():
    # v5.10.117: the file stays open (dbkeep): a close is no disk sync
    from .dexhub import dbkeep
    return dbkeep.connect(DB_PATH, timeout=10)


def _ensure_db():
    global _DB_READY
    if _DB_READY:
        return
    with _DB_LOCK:
        if _DB_READY:
            return
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = _connect()
            try:
                conn.execute(CREATE_SQL)
                conn.commit()
            finally:
                conn.close()
            _DB_READY = True
        except Exception as exc:
            log.warn('FANART', 'fanarttv_cache init failed: %s', exc)


def _api_key():
    try:
        user_key = (ADDON.getSetting('fanarttv_api_key') or '').strip()
    except Exception:
        user_key = ''
    return user_key or _DEFAULT_API_KEY


def _is_enabled():
    try:
        return ADDON.getSettingBool('fanarttv_enrich')
    except Exception:
        # Default ON — this is the visual upgrade users want.
        return True


def _ui_language():
    """Return 2-letter ISO for clearlogo language preference."""
    try:
        from .i18n import current_language
        return current_language() or 'en'
    except Exception:
        return 'en'


def _cache_key(media_type, art_id):
    return '%s|%s' % ((media_type or '').lower(), str(art_id))


def _mem_get(key, now):
    with _MEM_LOCK:
        row = _MEM.get(key)
        if not row:
            return None
        expires_at, payload = row
        if expires_at < now:
            _MEM.pop(key, None)
            return None
        return payload


def _mem_put(key, payload, expires_at):
    with _MEM_LOCK:
        _MEM[key] = (expires_at, payload)
        if len(_MEM) > 600:
            try:
                victims = list(_MEM.keys())[:60]
                for k in victims:
                    _MEM.pop(k, None)
            except Exception:
                pass


def _disk_get(media_type, art_id, now):
    _ensure_db()
    if not _DB_READY:
        return None
    try:
        conn = _connect()
        try:
            row = conn.execute(
                'SELECT payload, expires_at FROM fanart WHERE media_type=? AND art_id=?',
                (media_type.lower(), str(art_id)),
            ).fetchone()
        finally:
            conn.close()
        if not row or row[1] < now:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None
    except Exception:
        return None


def _disk_put(media_type, art_id, payload, expires_at):
    _ensure_db()
    if not _DB_READY:
        return
    try:
        conn = _connect()
        try:
            conn.execute(
                'INSERT OR REPLACE INTO fanart (media_type, art_id, payload, expires_at) VALUES (?, ?, ?, ?)',
                (media_type.lower(), str(art_id),
                 json.dumps(payload, ensure_ascii=False), expires_at),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.warn('FANART', 'fanarttv_cache put failed: %s', exc)


def _http_get(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'DexHub/3.9'})
    with _SEMAPHORE:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status >= 400:
                    return None
                return json.loads(resp.read().decode('utf-8', errors='replace'))
        except urllib.error.HTTPError as e:
            # 404 = no art for this id; treat as a clean negative
            if e.code == 404:
                return {}
            log.warn('FANART', 'http %s for %s', e.code, url)
            return None
        except Exception as exc:
            log.warn('FANART', 'fetch %s failed: %s', url, exc)
            return None


def _pick_best(entries, lang_preference):
    """Pick the highest-rated entry, preferring an entry whose language
    matches the user's UI language for clearlogo."""
    if not isinstance(entries, list) or not entries:
        return ''
    # Sort: matching language first, then by likes desc.
    def _score(e):
        lang_match = 0
        if lang_preference:
            try:
                if str(e.get('lang', '') or '').lower() == lang_preference:
                    lang_match = 1000  # huge boost
            except Exception:
                pass
            # English is a great fallback if user lang not available
            if lang_preference != 'en' and str(e.get('lang', '') or '').lower() == 'en':
                lang_match = 500
        try:
            likes = int(e.get('likes', 0) or 0)
        except Exception:
            likes = 0
        return lang_match + likes

    best = sorted(entries, key=_score, reverse=True)[0]
    return str(best.get('url', '') or '')


def _normalize_movie_payload(data, lang):
    if not isinstance(data, dict):
        return {}
    return {
        'clearart':   _pick_best(data.get('hdmovieclearart') or data.get('movieart'), lang),
        'clearlogo':  _pick_best(data.get('hdmovielogo') or data.get('movielogo'), lang),
        'banner':     _pick_best(data.get('moviebanner'), lang),
        'discart':    _pick_best(data.get('moviedisc'), lang),
        'landscape':  _pick_best(data.get('moviethumb'), lang),
        'fanart':     _pick_best(data.get('moviebackground'), lang),
        'characterart': '',  # movies don't have this
    }


def _normalize_tv_payload(data, lang):
    if not isinstance(data, dict):
        return {}
    return {
        'clearart':   _pick_best(data.get('hdclearart') or data.get('clearart'), lang),
        'clearlogo':  _pick_best(data.get('hdtvlogo') or data.get('clearlogo'), lang),
        'banner':     _pick_best(data.get('tvbanner'), lang),
        'discart':    '',  # TV shows don't have discart
        'landscape':  _pick_best(data.get('tvthumb'), lang),
        'fanart':     _pick_best(data.get('showbackground'), lang),
        'characterart': _pick_best(data.get('characterart'), lang),
    }


def get_art(media_type, tmdb_id='', tvdb_id='', imdb_id=''):
    """Return a dict of fanart.tv artwork for a movie or TV show.

    Uses tvdb_id when provided (fanart.tv's primary key for TV).
    Movies are looked up by tmdb_id or imdb_id.

    Returns {} when fanart.tv has no entries (cached negative for 6h) or
    when enrichment is disabled in settings.
    """
    if not _is_enabled():
        return {}
    media_type = (media_type or '').lower()
    is_tv = media_type in ('series', 'show', 'tv', 'tvshow', 'anime')

    # fanart.tv key selection
    if is_tv:
        art_id = str(tvdb_id or tmdb_id or imdb_id or '').strip()
    else:
        art_id = str(tmdb_id or imdb_id or '').strip()
    if not art_id:
        return {}

    now = int(time.time())
    key = _cache_key('tv' if is_tv else 'movie', art_id)
    mem = _mem_get(key, now)
    if mem is not None:
        return dict(mem)
    disk = _disk_get('tv' if is_tv else 'movie', art_id, now)
    if disk is not None:
        _mem_put(key, disk, now + 3600)
        return dict(disk)

    # Network fetch
    api_key = _api_key()
    lang = _ui_language()
    if is_tv:
        url = 'https://webservice.fanart.tv/v3/tv/%s?api_key=%s' % (art_id, api_key)
    else:
        # fanart.tv movie endpoint accepts both tmdb id and imdb tt-id.
        url = 'https://webservice.fanart.tv/v3/movies/%s?api_key=%s' % (art_id, api_key)

    raw = _http_get(url)
    if raw is None:
        # Soft failure (network down) — cache nothing so we retry later.
        return {}
    if is_tv:
        normalized = _normalize_tv_payload(raw, lang)
    else:
        normalized = _normalize_movie_payload(raw, lang)

    # Drop empty keys to keep payload small
    normalized = {k: v for k, v in normalized.items() if v}

    expires_at = now + (_DEFAULT_TTL if normalized else _NEGATIVE_TTL)
    _disk_put('tv' if is_tv else 'movie', art_id, normalized, expires_at)
    _mem_put(key, normalized, min(now + 3600, expires_at))
    return dict(normalized)


def merge_into_meta(meta, media_type, tmdb_id='', tvdb_id='', imdb_id=''):
    """Convenience helper: enrich a meta dict in-place. Only adds fields
    that the meta is missing, so a Stremio-supplied clearlogo wins over
    fanart.tv (the user-curated source is usually higher quality)."""
    if not isinstance(meta, dict):
        return meta
    art = get_art(media_type, tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id)
    if not art:
        return meta
    for key, value in art.items():
        if value and not meta.get(key):
            meta[key] = value
    return meta


def purge_expired():
    """Drop expired rows. Call periodically from service.py."""
    _ensure_db()
    if not _DB_READY:
        return 0
    now = int(time.time())
    try:
        conn = _connect()
        try:
            cur = conn.execute('DELETE FROM fanart WHERE expires_at < ?', (now,))
            deleted = cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return deleted
    except Exception:
        return 0
