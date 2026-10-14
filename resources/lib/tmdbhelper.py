# -*- coding: utf-8 -*-
import json
import re
import sqlite3
from functools import lru_cache

import xbmcvfs

# Bug fix: previously every image (poster/fanart/logo) was fetched at
# /t/p/original — full-resolution 2000+px files (often 500KB-2MB each)
# downloaded just to be displayed in a 320x480 poster slot. Use TMDb's
# CDN-served sized variants to drop bandwidth ~95% with no visible
# quality loss for Kodi display sizes.
_TMDB_IMAGE_BASE_POSTER = 'https://image.tmdb.org/t/p/w500'
_TMDB_IMAGE_BASE_BACKDROP = 'https://image.tmdb.org/t/p/w1280'
_TMDB_IMAGE_BASE_LOGO = 'https://image.tmdb.org/t/p/w500'
# Backwards-compatible alias for any external caller that imports the old name.
_TMDB_IMAGE_BASE = _TMDB_IMAGE_BASE_POSTER
_DB_PATHS = [
    'special://userdata/addon_data/plugin.video.themoviedb.helper/database_10/ItemDetails.db',
    'special://userdata/addon_data/plugin.video.themoviedb.helper/database_09/ItemDetails.db',
    'special://userdata/addon_data/plugin.video.themoviedb.helper/database_08/ItemDetails.db',
    'special://userdata/addon_data/plugin.video.themoviedb.helper/database_07/ItemDetails.db',
    'special://userdata/addon_data/plugin.video.themoviedb.helper/database_06/ItemDetails.db',
    'special://userdata/addon_data/plugin.video.themoviedb.helper/database_05/ItemDetails.db',
]
_CACHE = {}
_CACHE_ORDER = []
_META_AT = {}
_CACHE_MAX = 1024


@lru_cache(maxsize=2)
def _helper_languages(path):
    # Read the installed Helper's mapping without importing its runtime.
    import ast
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'LANGUAGES' for t in node.targets):
            return ast.literal_eval(node.value)
    return ()


_LANG_CACHE = {'stamp': 0.0, 'value': None}
_LANG_CACHE_TTL = 5.0


def helper_language():
    """TMDb Helper's configured language (e.g. 'ar-SA').

    v5.10.68: memoised for a few seconds. This used to create a fresh
    xbmcaddon handle and read a setting for EVERY item on a page (the meta
    bundle and the art bundle each call it), which is pure Python->C++
    round-trip cost on a 60-item render.
    """
    import time as _t
    now = _t.monotonic()
    cached = _LANG_CACHE.get('value')
    if cached is not None and (now - float(_LANG_CACHE.get('stamp') or 0.0)) < _LANG_CACHE_TTL:
        return cached
    value = _helper_language_uncached()
    _LANG_CACHE['value'] = value
    _LANG_CACHE['stamp'] = now
    return value


def _helper_language_uncached():
    try:
        import xbmc
        if not xbmc.getCondVisibility('System.HasAddon(plugin.video.themoviedb.helper)'):
            return ''
        import xbmcaddon
        addon = xbmcaddon.Addon('plugin.video.themoviedb.helper')
        index = int(addon.getSetting('language') or 0)
        if not index:
            return 'en-US'
        path = xbmcvfs.translatePath(addon.getAddonInfo('path'))
        import os
        return _helper_languages(os.path.join(path, 'resources/tmdbhelper/lib/addon/consts.py'))[index]
    except Exception:
        return ''


def _translated_row(conn, row, language):
    out = dict(row)
    if not language or not row.get('id'):
        return out
    lang, _, country = language.partition('-')
    try:
        translated = conn.execute('SELECT title,plot,tagline FROM translation WHERE parent_id=? AND iso_language=? ORDER BY CASE WHEN iso_country=? THEN 0 ELSE 1 END LIMIT 1', (row['id'], lang, country)).fetchone()
        if translated:
            for key, value in zip(('title', 'plot', 'tagline'), translated):
                if value:
                    out[key] = value
    except sqlite3.Error:
        pass
    return out


def _cache_put(key, value):
    # Descriptive reads share the bounded, locked memo with ID reads.
    _remember(key, value, miss=not any(value.values()))


def _meta_memo(key):
    hit, value = _memo(key)
    if not hit:
        return False, None
    ttl = 300.0 if any(value.values()) else 30.0
    with _MEMO_LOCK:
        if _time.monotonic() - _META_AT.get(key, 0.0) >= ttl:
            _forget_locked(key)
            return False, None
    return True, value



_DB_FOLDERS = ('database_10', 'database_09', 'database_08', 'database_07',
               'database_06', 'database_05')
_DB_PATH_STATE = {'path': None, 'at': 0.0}
_DB_PATH_RETRY = 60.0


def _db_candidates():
    """TMDb Helper's database files, newest folder first.

    v5.10.105: TMDb Helper can keep its cache outside addon_data (its
    "cache_location" setting); that folder is read first.
    """
    out = []
    try:
        import xbmc
        if not xbmc.getCondVisibility('System.HasAddon(plugin.video.themoviedb.helper)'):
            return list(_DB_PATHS)
        import xbmcaddon
        base = (xbmcaddon.Addon('plugin.video.themoviedb.helper').getSetting('cache_location') or '').strip()
    except Exception:
        base = ''
    if base:
        base = base if base.endswith(('/', '\\')) else base + '/'
        out.extend('%s%s/ItemDetails.db' % (base, folder) for folder in _DB_FOLDERS)
    out.extend(_DB_PATHS)
    return out


def _active_db_path():
    """The first existing TMDb Helper database, remembered for the session.

    v5.10.105: a miss is remembered for a minute only, so a TMDb Helper that
    creates its database after Dex Hub started is used without a restart.
    """
    import time as _t
    now = _t.monotonic()
    known = _DB_PATH_STATE.get('path')
    if known or (known is not None and now - _DB_PATH_STATE['at'] < _DB_PATH_RETRY):
        return known
    found = ''
    for db in _db_candidates():
        try:
            path = xbmcvfs.translatePath(db)
            if path and xbmcvfs.exists(path):
                found = path
                break
        except Exception:
            continue
    _DB_PATH_STATE['path'] = found
    _DB_PATH_STATE['at'] = now
    return found


def _active_db_path_clear():
    _DB_PATH_STATE['path'] = None
    _DB_PATH_STATE['at'] = 0.0


_active_db_path.cache_clear = _active_db_path_clear     # older callers (meta_mode)


def _normalize_media_type(media_type):
    if str(media_type or '').strip().lower() in ('tvshow', 'episode', 'season', 'series', 'show', 'tv', 'anime'):
        return 'tv'
    return 'movie'


def _to_image_url(icon, kind='poster'):
    """Build a sized TMDb URL. `kind` ∈ {'poster','backdrop','logo'}."""
    icon = str(icon or '').strip()
    if icon and '](' in icon:
        icon = icon.split('](')[-1].replace(')', '')
    if not icon:
        return ''
    if icon.startswith('http'):
        return icon
    if kind == 'backdrop':
        return _TMDB_IMAGE_BASE_BACKDROP + icon
    if kind == 'logo':
        return _TMDB_IMAGE_BASE_LOGO + icon
    return _TMDB_IMAGE_BASE_POSTER + icon


# Map TMDb Helper db `art.type` values to our size kind.
_ART_TYPE_KIND = {
    'posters': 'poster', 'poster': 'poster', 'thumb': 'poster',
    'backdrops': 'backdrop', 'fanarts': 'backdrop', 'landscape': 'backdrop', 'stills': 'backdrop',
    'logos': 'logo', 'clearlogo': 'logo',
}


def _query_first_icon(conn, table, parent_id, art_type, order_sql=''):
    """Read one icon from TMDb Helper artwork tables defensively.

    TMDb Helper 5.x stores normal TMDb images in `art` using types like
    posters/backdrops/logos, while older DexHub code only queried fanarts/
    landscape. Some cached rows also live in `default_art`, `user_art`, or
    `fanart_tv`, so check them too.
    """
    try:
        cols = _table_columns(conn, table)
        if not cols or 'icon' not in cols or 'parent_id' not in cols or 'type' not in cols:
            return ''
        sql = "SELECT icon FROM %s WHERE type=? AND parent_id=?" % table
        if order_sql:
            sql += " ORDER BY " + order_sql
        sql += " LIMIT 1"
        cur = conn.cursor()
        cur.execute(sql, (art_type, parent_id))
        row = cur.fetchone()
        return str(row[0]).strip() if row and row[0] not in (None, '') else ''
    except Exception:
        return ''


def _query_art_for_parent(conn, parent_id, art_type):
    kind = _ART_TYPE_KIND.get(art_type, 'poster')
    language = helper_language().split('-')[0]
    language = language if re.fullmatch('[a-z]{2}', language) else 'en'
    order = "CASE WHEN iso_language='%s' THEN 0 WHEN iso_language IS NULL THEN 1 ELSE 2 END, rating DESC" % language
    candidates = []

    # Primary TMDb Helper art table. Use the real TMDb Helper names.
    type_aliases = [art_type]
    if art_type in ('fanarts', 'landscape'):
        type_aliases.insert(0, 'backdrops')
    elif art_type == 'thumb':
        type_aliases.insert(0, 'posters')
    elif art_type == 'clearlogo':
        type_aliases.insert(0, 'logos')

    # Honor Helper's selected artwork before generic ranked images. Stop at
    # the first hit instead of executing every fallback query for each item.
    for table in ('user_art', 'default_art'):
        for t in type_aliases + [{'posters': 'poster', 'backdrops': 'fanart', 'logos': 'clearlogo'}.get(art_type, art_type)]:
            icon = _query_first_icon(conn, table, parent_id, t)
            if icon:
                return _to_image_url(icon, kind=kind)
    for t in type_aliases:
        icon = _query_first_icon(conn, 'art', parent_id, t, order)
        if icon:
            return _to_image_url(icon, kind=kind)

    # Default/user art rows are commonly present even when the full `art`
    # table has not been populated by browsing TMDb Helper directly.
    for t in type_aliases:
        candidates.append(_query_first_icon(conn, 'user_art', parent_id, t))
        candidates.append(_query_first_icon(conn, 'default_art', parent_id, t))

    # Fanart.tv cache stores logos/backdrops separately.
    if art_type in ('logos', 'clearlogo'):
        candidates.append(_query_first_icon(conn, 'fanart_tv', parent_id, 'logos', "CASE WHEN iso_language='en' THEN 0 WHEN iso_language IS NULL THEN 1 ELSE 2 END, likes DESC"))
    elif art_type in ('fanarts', 'landscape', 'backdrops'):
        candidates.append(_query_first_icon(conn, 'fanart_tv', parent_id, 'backdrops', "CASE WHEN iso_language='en' THEN 0 WHEN iso_language IS NULL THEN 1 ELSE 2 END, likes DESC"))

    for icon in candidates:
        if icon:
            return _to_image_url(icon, kind=kind)
    return ''




# v5.10.28: per-thread read-only connection reuse.
#
# Every art/id/title lookup used to open its own sqlite connection, so a
# 60-row folder paid 60 opens (plus PRAGMA discovery on each) before the
# first item drew. Connections are now kept per thread for a short idle
# window and reused across the rows of one render; an idle or errored
# connection is closed and reopened transparently. Read-only + thread-local
# keeps TMDb Helper's own writer and Kodi's threads out of each other's way.
import threading as _threading
import time as _time

_RO_POOL = _threading.local()
_RO_IDLE_SECONDS = 45.0


def _ro_connect(path):
    pool = getattr(_RO_POOL, 'conns', None)
    if pool is None:
        pool = _RO_POOL.conns = {}
    entry = pool.get(path)
    now = _time.monotonic()
    if entry is not None:
        conn, last_used = entry
        if (now - last_used) < _RO_IDLE_SECONDS:
            try:
                conn.execute('SELECT 1')
                return conn
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass
        pool.pop(path, None)
    conn = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=2)
    pool[path] = (conn, now)
    return conn


def _ro_release(conn):
    """Mark the connection idle instead of closing it; it is reused by the
    next lookup on this thread and closed after the idle window."""
    pool = getattr(_RO_POOL, 'conns', None) or {}
    now = _time.monotonic()
    for path, (pooled, _last) in list(pool.items()):
        if pooled is conn:
            pool[path] = (pooled, now)
            return
    try:
        conn.close()
    except Exception:
        pass


_COLS_CACHE = {}
_COLS_CACHE_LOCK = _threading.Lock()


def _table_columns(conn, table):
    """Column names for a table, memoised per (database file, table).

    v5.10.28: the PRAGMA ran on every lookup for every candidate table; the
    schema of TMDb Helper's database does not change between rows.
    """
    try:
        key = (str(_active_db_path() or ''), str(table))
    except Exception:
        key = ('', str(table))
    with _COLS_CACHE_LOCK:
        cached = _COLS_CACHE.get(key)
    if cached is not None:
        return list(cached)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(%s)" % table)
        cols = [row[1] for row in cur.fetchall()]
    except Exception:
        return []
    with _COLS_CACHE_LOCK:
        if len(_COLS_CACHE) > 512:
            _COLS_CACHE.clear()
        _COLS_CACHE[key] = tuple(cols)
    return cols


def _find_title_columns(cols):
    candidates = ['title', 'name', 'label', 'originaltitle', 'originalname', 'showname']
    return [c for c in candidates if c in cols]


def _find_tmdb_column(cols):
    for key in ('tmdb_id', 'tmdb'):
        if key in cols:
            return key
    return ''


def _find_media_type_column(cols):
    for key in ('media_type', 'mediatype', 'type'):
        if key in cols:
            return key
    return ''


def _find_year_column(cols):
    for key in ('year', 'release_year'):
        if key in cols:
            return key
    return ''


def _resolve_tmdb_from_imdb(conn, imdb_id, media_type):
    if not imdb_id:
        return ''
    mt = _normalize_media_type(media_type)
    if _has_v6_ids(conn):
        # v5.10.105: one map of TMDb Helper's ids instead of a table scan per
        # title (unique_id.value has no index)
        return _reverse_lookup(conn, mt, 'imdb', str(imdb_id).strip())
    table = 'tvshow' if mt == 'tv' else 'movie'
    queries = [
        # Old helper/legacy schemas
        ("SELECT tmdb_id FROM unique_ids WHERE imdb_id=? LIMIT 1", (imdb_id,)),
        ("SELECT tmdb FROM unique_ids WHERE imdb=? LIMIT 1", (imdb_id,)),
        ("SELECT tmdb_id FROM item_ids WHERE imdb_id=? LIMIT 1", (imdb_id,)),
        ("SELECT tmdb FROM item_ids WHERE imdb=? LIMIT 1", (imdb_id,)),
        ("SELECT tmdb_id FROM items WHERE imdb_id=? AND media_type=? LIMIT 1", (imdb_id, mt)),
        ("SELECT tmdb_id FROM items WHERE imdb_id=? LIMIT 1", (imdb_id,)),
        # Current TMDb Helper schema: unique_id(key,value,parent_id) + movie/tvshow(tmdb_id)
        ("SELECT %s.tmdb_id FROM unique_id JOIN %s ON %s.id=unique_id.parent_id WHERE unique_id.key='imdb_id' AND unique_id.value=? LIMIT 1" % (table, table, table), (imdb_id,)),
        ("SELECT %s.tmdb_id FROM unique_id JOIN %s ON %s.id=unique_id.parent_id WHERE unique_id.key='imdb' AND unique_id.value=? LIMIT 1" % (table, table, table), (imdb_id,)),
    ]
    for sql, params in queries:
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            value = str(row[0]) if row and row[0] not in (None, '') else ''
            if value and value.isdigit():
                return value
        except Exception:
            continue
    return ''


def _resolve_tmdb_from_title(conn, title, media_type, year=''):
    title = str(title or '').strip()
    if not title:
        return ''
    mt = _normalize_media_type(media_type)
    norm = re.sub(r'\s+', ' ', title).strip().lower()
    # Current TMDb Helper uses movie/tvshow tables directly. Keep old tables
    # for compatibility with older helper builds.
    tables = ['tvshow' if mt == 'tv' else 'movie', 'items', 'item_ids', 'unique_ids']
    for table in tables:
        cols = _table_columns(conn, table)
        if not cols:
            continue
        title_cols = _find_title_columns(cols)
        tmdb_col = _find_tmdb_column(cols)
        if not title_cols or not tmdb_col:
            continue
        mt_col = _find_media_type_column(cols)
        year_col = _find_year_column(cols)
        for tc in title_cols:
            sql = "SELECT %s FROM %s WHERE lower(%s)=?" % (tmdb_col, table, tc)
            params = [norm]
            if mt_col:
                sql += " AND %s=?" % mt_col
                params.append('tvshow' if mt == 'tv' and table == 'tvshow' else mt)
            if year_col and str(year or '').isdigit():
                sql += " AND %s=?" % year_col
                params.append(int(year))
            sql += " LIMIT 1"
            try:
                cur = conn.cursor()
                cur.execute(sql, tuple(params))
                row = cur.fetchone()
                value = str(row[0]) if row and row[0] not in (None, '') else ''
                if value and value.isdigit():
                    return value
            except Exception:
                continue
    return ''




def _find_column(cols, *names):
    for name in names:
        if name in cols:
            return name
    return ''


_IDS_MISS_TTL = 120.0
_IDS_MISS_AT = {}
_MEMO_LOCK = _threading.Lock()
_MISSING = object()


def _forget_locked(key):
    _CACHE.pop(key, None)
    _IDS_MISS_AT.pop(key, None)
    _META_AT.pop(key, None)
    try:
        _CACHE_ORDER.remove(key)
    except ValueError:
        pass


def _memo(key):
    """(hit, value) from the session memo; an empty answer is asked again
    after two minutes, as TMDb Helper keeps filling its cache (v5.10.105)."""
    with _MEMO_LOCK:
        value = _CACHE.get(key, _MISSING)
        if value is _MISSING:
            return False, None
        missed = _IDS_MISS_AT.get(key)
        if missed is not None and (_time.monotonic() - missed) >= _IDS_MISS_TTL:
            _forget_locked(key)
            return False, None
        return True, value


def _remember(key, value, miss=None):
    """Store an answer; miss (default: an empty value) expires (_memo)."""
    with _MEMO_LOCK:
        _forget_locked(key)
        _CACHE[key] = value
        _CACHE_ORDER.append(key)
        if key.startswith(('meta:', 'episode:', 'bundle:')):
            _META_AT[key] = _time.monotonic()
        while len(_CACHE_ORDER) > _CACHE_MAX:
            old = _CACHE_ORDER.pop(0)
            _CACHE.pop(old, None)
            _IDS_MISS_AT.pop(old, None)
            _META_AT.pop(old, None)
        if (not value) if miss is None else miss:
            _IDS_MISS_AT[key] = _time.monotonic()


# TMDb Helper 6 keeps an item's external ids as unique_id rows, without an
# index on their value: finding a film by its IMDb id reads every IMDb row.
# Each answer (and each miss, for two minutes) is remembered for the session
# and shared by every reader, so a title costs that read once (v5.10.105).
def _has_v6_ids(conn):
    cols = _table_columns(conn, 'unique_id')
    return bool(cols) and all(c in cols for c in ('key', 'value', 'parent_id'))


def _reverse_lookup(conn, mt, key, value):
    """TMDb id of the film ('movie') or show ('tv') whose `key` id is `value`;
    seasons and episodes ('tv.1399.1.1') keep their own ids and never match."""
    value = str(value or '').strip()
    if not value:
        return ''
    kind = 'tv' if mt == 'tv' else 'movie'
    memo_key = 'rev:%s:%s:%s' % (kind, key, value)
    hit, found = _memo(memo_key)
    if hit:
        return found or ''
    try:
        row = conn.execute(
            "SELECT parent_id FROM unique_id WHERE key=? AND value=? "
            "AND parent_id LIKE ? AND parent_id NOT LIKE ? LIMIT 1",
            (key, value, kind + '.%', kind + '.%.%')).fetchone()
    except Exception:
        row = None
    found = ''
    if row and row[0]:
        tmdb = str(row[0]).split('.', 1)[-1]
        if tmdb.isdigit():
            found = tmdb
    _remember(memo_key, found)
    return found


def _v6_unique_ids(conn, mt, out):
    """Fill out's ids from TMDb Helper 6's unique_id table (v5.10.105).

    TMDb Helper 6 keeps an item's external ids as rows (key 'imdb', 'tvdb',
    'tmdb'; the value) under its item id, 'movie.603' or 'tv.1399'; seasons
    and episodes ('tv.1399.1.1') keep their own and are never matched here.
    Returns True when the item was found.
    """
    cols = _table_columns(conn, 'unique_id')
    if not cols or not all(c in cols for c in ('key', 'value', 'parent_id')):
        return False
    prefix = 'tv' if mt == 'tv' else 'movie'
    parent = ''
    tmdb = str(out.get('tmdb_id') or '').strip()
    if tmdb.isdigit():
        parent = '%s.%s' % (prefix, tmdb)
    else:
        for key, value in (('imdb', out.get('imdb_id')), ('tvdb', out.get('tvdb_id'))):
            found = _reverse_lookup(conn, mt, key, value) if value else ''
            if found:
                out['tmdb_id'] = found
                parent = '%s.%s' % (prefix, found)
                break
    if not parent:
        return False
    try:
        rows = conn.execute('SELECT key, value FROM unique_id WHERE parent_id=?', (parent,)).fetchall()
    except Exception:
        rows = []
    for key, value in rows:
        value = str(value or '').strip()
        if not value or value.lower() in ('none', 'null', '0'):
            continue
        key = str(key or '').strip().lower()
        if key == 'imdb' and not out.get('imdb_id') and value.startswith('tt'):
            out['imdb_id'] = value
        elif key == 'tvdb' and not out.get('tvdb_id') and value.isdigit():
            out['tvdb_id'] = value
        elif key == 'tmdb' and not out.get('tmdb_id') and value.isdigit():
            out['tmdb_id'] = value
    return bool(rows)


def get_external_ids_from_db(tmdb_id='', media_type='movie', imdb_id='', tvdb_id='', title='', year=''):
    """Resolve IMDb/TMDb/TVDb ids from TMDb Helper's local database.

    This is intentionally read-only and API-free.  Stremio stream addons such
    as Torrentio/AIOStreams usually resolve best with IMDb `tt...` ids, while
    Kodi/TMDb Helper handoffs often start as `tmdb:123`.  Stremio itself sends
    the configured addon the canonical id that the Stremio catalogue has; Dex
    Hub must recreate that by enriching ids locally before calling /stream.
    """
    mt = _normalize_media_type(media_type)
    tmdb_id = str(tmdb_id or '').strip()
    imdb_id = str(imdb_id or '').strip()
    tvdb_id = str(tvdb_id or '').strip()
    cache_key = 'ids:%s:%s:%s:%s:%s:%s' % (mt, tmdb_id, imdb_id, tvdb_id, title or '', year or '')
    # v5.10.105: a miss is asked again after two minutes; TMDb Helper may
    # have cached the title since (it fills its database as you browse).
    hit, value = _memo(cache_key)
    if hit:
        return dict(value or {})
    out = {'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'tvdb_id': tvdb_id}
    asked = dict(out)
    path = _active_db_path()
    if not path:
        _remember(cache_key, dict(out), miss=True)
        return out
    try:
        # Read-only DB — TMDb Helper writes from its own service. mode=ro
        # avoids taking a write lock and skips journal bookkeeping. (3.8.12)
        conn = _ro_connect(path)
        try:
            # v5.10.105: TMDb Helper 6 (database_07): ids as unique_id rows.
            _v6_unique_ids(conn, mt, out)
            # Prefer direct id tables.  Table/column names vary across TMDb
            # Helper versions, so discover columns and query defensively.
            for table in ('unique_ids', 'item_ids', 'items'):
                if out.get('tmdb_id') and out.get('imdb_id'):
                    break
                cols = _table_columns(conn, table)
                if not cols:
                    continue
                tmdb_col = _find_column(cols, 'tmdb_id', 'tmdb')
                imdb_col = _find_column(cols, 'imdb_id', 'imdb')
                tvdb_col = _find_column(cols, 'tvdb_id', 'tvdb')
                mt_col = _find_media_type_column(cols)
                select_cols = []
                for col in (tmdb_col, imdb_col, tvdb_col):
                    if col and col not in select_cols:
                        select_cols.append(col)
                if not select_cols:
                    continue

                where = []
                params = []
                if out.get('tmdb_id') and tmdb_col:
                    where.append('%s=?' % tmdb_col)
                    params.append(out['tmdb_id'])
                if out.get('imdb_id') and imdb_col:
                    where.append('%s=?' % imdb_col)
                    params.append(out['imdb_id'])
                if out.get('tvdb_id') and tvdb_col:
                    where.append('%s=?' % tvdb_col)
                    params.append(out['tvdb_id'])
                if not where:
                    continue
                sql = 'SELECT %s FROM %s WHERE (%s)' % (', '.join(select_cols), table, ' OR '.join(where))
                if mt_col:
                    sql += ' AND %s=?' % mt_col
                    params.append(mt)
                sql += ' LIMIT 1'
                try:
                    cur = conn.cursor()
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
                except Exception:
                    row = None
                if not row:
                    continue
                values = dict(zip(select_cols, row))
                if tmdb_col and not out.get('tmdb_id'):
                    val = str(values.get(tmdb_col) or '').strip()
                    if val and val.isdigit():
                        out['tmdb_id'] = val
                if imdb_col and not out.get('imdb_id'):
                    val = str(values.get(imdb_col) or '').strip()
                    if val:
                        out['imdb_id'] = val if val.startswith('tt') else ('tt%s' % val if val.isdigit() else val)
                if tvdb_col and not out.get('tvdb_id'):
                    val = str(values.get(tvdb_col) or '').strip()
                    if val and val.isdigit():
                        out['tvdb_id'] = val
                if out.get('tmdb_id') and out.get('imdb_id'):
                    break

            # Title fallback for older databases that don't have populated id
            # columns in unique_ids/item_ids.
            if (not out.get('imdb_id') or not out.get('tmdb_id')) and title:
                norm = re.sub(r'\s+', ' ', str(title or '')).strip().lower()
                for table in ('items', 'item_ids', 'unique_ids'):
                    cols = _table_columns(conn, table)
                    if not cols:
                        continue
                    title_cols = _find_title_columns(cols)
                    if not title_cols:
                        continue
                    tmdb_col = _find_column(cols, 'tmdb_id', 'tmdb')
                    imdb_col = _find_column(cols, 'imdb_id', 'imdb')
                    tvdb_col = _find_column(cols, 'tvdb_id', 'tvdb')
                    mt_col = _find_media_type_column(cols)
                    year_col = _find_year_column(cols)
                    select_cols = [c for c in (tmdb_col, imdb_col, tvdb_col) if c]
                    if not select_cols:
                        continue
                    for tc in title_cols:
                        sql = 'SELECT %s FROM %s WHERE lower(%s)=?' % (', '.join(select_cols), table, tc)
                        params = [norm]
                        if mt_col:
                            sql += ' AND %s=?' % mt_col
                            params.append(mt)
                        if year_col and str(year or '').isdigit():
                            sql += ' AND %s=?' % year_col
                            params.append(int(year))
                        sql += ' LIMIT 1'
                        try:
                            cur = conn.cursor()
                            cur.execute(sql, tuple(params))
                            row = cur.fetchone()
                        except Exception:
                            row = None
                        if not row:
                            continue
                        values = dict(zip(select_cols, row))
                        if tmdb_col and not out.get('tmdb_id'):
                            val = str(values.get(tmdb_col) or '').strip()
                            if val and val.isdigit():
                                out['tmdb_id'] = val
                        if imdb_col and not out.get('imdb_id'):
                            val = str(values.get(imdb_col) or '').strip()
                            if val:
                                out['imdb_id'] = val if val.startswith('tt') else ('tt%s' % val if val.isdigit() else val)
                        if tvdb_col and not out.get('tvdb_id'):
                            val = str(values.get(tvdb_col) or '').strip()
                            if val and val.isdigit():
                                out['tvdb_id'] = val
                        if out.get('tmdb_id') and out.get('imdb_id'):
                            break
                    if out.get('tmdb_id') and out.get('imdb_id'):
                        break
        finally:
            _ro_release(conn)
    except Exception:
        pass
    _remember(cache_key, dict(out), miss=(out == asked))
    return out


def _db_scalar(value):
    if value in (None, ''):
        return ''
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8', 'ignore')
        except Exception:
            return ''
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return ''
    if (text[:1] in ('{', '[')):
        try:
            parsed = json.loads(text)
            return parsed
        except Exception:
            return text
    return text


def _as_clean_list(value):
    value = _db_scalar(value)
    out = []
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                name = entry.get('name') or entry.get('title') or entry.get('value') or entry.get('label') or ''
                if name:
                    out.append(str(name).strip())
            elif entry not in (None, ''):
                out.append(str(entry).strip())
    elif isinstance(value, dict):
        for key in ('name', 'title', 'value', 'label'):
            if value.get(key):
                out.append(str(value.get(key)).strip())
                break
    else:
        text = str(value or '').strip()
        if text:
            # TMDb Helper DB fields vary between JSON strings and simple comma /
            # slash separated values depending on version and skin helper cache.
            if ',' in text or ' / ' in text or '|' in text:
                parts = re.split(r'\s*(?:,|/|\|)\s*', text)
                out.extend([x.strip() for x in parts if x.strip()])
            else:
                out.append(text)
    seen = set()
    clean = []
    for item in out:
        item = str(item or '').strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            clean.append(item)
    return clean


def _row_to_dict(cols, row):
    if not cols or row is None:
        return {}
    return {cols[i]: row[i] for i in range(min(len(cols), len(row)))}


def _first_field(row, *names):
    lowered = {str(k).lower(): k for k in (row or {}).keys()}
    for name in names:
        key = name if name in row else lowered.get(str(name).lower())
        if not key:
            continue
        val = _db_scalar(row.get(key))
        if val not in (None, '', [], {}):
            return val
    return ''


def _first_numeric(row, *names):
    val = _first_field(row, *names)
    if val in (None, '', [], {}):
        return ''
    try:
        if isinstance(val, str):
            m = re.search(r'\d+(?:\.\d+)?', val.replace(',', ''))
            return float(m.group(0)) if m else ''
        return float(val)
    except Exception:
        return ''


def _find_item_row(conn, media_type, tmdb_id='', imdb_id='', title='', year=''):
    mt = _normalize_media_type(media_type)
    resolved_tmdb = str(tmdb_id or '').strip()
    if not resolved_tmdb and imdb_id:
        resolved_tmdb = _resolve_tmdb_from_imdb(conn, imdb_id, mt)
    if not resolved_tmdb and title:
        resolved_tmdb = _resolve_tmdb_from_title(conn, title, mt, year)

    preferred = ['tvshow' if mt == 'tv' else 'movie', 'items']
    for table in preferred:
        cols = _table_columns(conn, table)
        if not cols:
            continue
        tmdb_col = _find_tmdb_column(cols)
        mt_col = _find_media_type_column(cols)
        title_cols = _find_title_columns(cols)
        row = None
        if resolved_tmdb and tmdb_col:
            sql = 'SELECT * FROM %s WHERE %s=?' % (table, tmdb_col)
            params = [resolved_tmdb]
            if table == 'items' and mt_col:
                sql += ' AND %s=?' % mt_col
                params.append(mt)
            sql += ' LIMIT 1'
            try:
                cur = conn.cursor()
                cur.execute(sql, tuple(params))
                row = cur.fetchone()
            except Exception:
                row = None
        if not row and not resolved_tmdb and not imdb_id and title and title_cols:
            norm = re.sub(r'\s+', ' ', str(title or '')).strip().lower()
            for tc in title_cols:
                sql = 'SELECT * FROM %s WHERE lower(%s)=?' % (table, tc)
                params = [norm]
                if table == 'items' and mt_col:
                    sql += ' AND %s=?' % mt_col
                    params.append(mt)
                ycol = _find_year_column(cols)
                if ycol and str(year or '').isdigit():
                    sql += ' AND %s=?' % ycol
                    params.append(int(year))
                sql += ' LIMIT 1'
                try:
                    cur = conn.cursor()
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
                except Exception:
                    row = None
                if row:
                    break
        if row:
            data = _row_to_dict(cols, row)
            if not resolved_tmdb and tmdb_col:
                val = str(data.get(tmdb_col) or '').strip()
                if val and val.isdigit():
                    resolved_tmdb = val
            return data, resolved_tmdb
    return {}, resolved_tmdb


def _parent_candidates(media_type, tmdb_id='', imdb_id='', tvdb_id='', internal_id=''):
    mt = _normalize_media_type(media_type)
    out = []
    def add(v):
        v = str(v or '').strip()
        if v and v not in out:
            out.append(v)
    if internal_id not in (None, ''):
        add(internal_id)
    if tmdb_id:
        add('%s.%s' % (mt, tmdb_id))
        if mt == 'tv':
            add('tvshow.%s' % tmdb_id)
            add('tv.%s' % tmdb_id)
        else:
            add('movie.%s' % tmdb_id)
        add(tmdb_id)
    if imdb_id:
        add('%s.%s' % (mt, imdb_id))
        add('%s.imdb:%s' % (mt, imdb_id))
        add(imdb_id)
    if tvdb_id:
        add('%s.tvdb:%s' % (mt, tvdb_id))
        add(tvdb_id)
    return out


def _query_related_values(conn, table_names, parent_ids, role_filter=None, limit=30):
    values = []
    for table in table_names:
        cols = _table_columns(conn, table)
        if not cols:
            continue
        parent_col = _find_column(cols, 'parent_id', 'item_id', 'media_id', 'dbid')
        name_col = _find_column(cols, 'name', 'title', 'value', 'label')
        if not parent_col or not name_col:
            continue
        role_col = _find_column(cols, 'role', 'job', 'department', 'type')
        order_col = _find_column(cols, 'order', 'sort_order', 'sortorder', 'ordering')
        sql = 'SELECT * FROM %s WHERE %s=?' % (table, parent_col)
        if order_col:
            sql += ' ORDER BY %s ASC' % order_col
        sql += ' LIMIT %d' % int(limit or 30)
        for pid in parent_ids or []:
            try:
                cur = conn.cursor()
                cur.execute(sql, (pid,))
                rows = cur.fetchall() or []
            except Exception:
                rows = []
            for row in rows:
                data = _row_to_dict(cols, row)
                if role_filter and role_col:
                    role_text = str(data.get(role_col) or '').lower()
                    if not any(x in role_text for x in role_filter):
                        continue
                name = str(_db_scalar(data.get(name_col)) or '').strip()
                if name:
                    values.append(name)
    seen = set()
    out = []
    for value in values:
        key = str(value).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(str(value).strip())
    return out[:limit]


def _query_cast(conn, parent_ids, limit=25):
    cast = []
    for table in ('cast', 'actors', 'actor', 'credits_cast'):
        cols = _table_columns(conn, table)
        if not cols:
            continue
        parent_col = _find_column(cols, 'parent_id', 'item_id', 'media_id', 'dbid')
        name_col = _find_column(cols, 'name', 'title', 'value', 'label')
        if not parent_col or not name_col:
            continue
        role_col = _find_column(cols, 'character', 'role', 'castrole')
        thumb_col = _find_column(cols, 'thumbnail', 'thumb', 'profile_path', 'icon', 'image')
        order_col = _find_column(cols, 'order', 'sort_order', 'sortorder', 'ordering')
        sql = 'SELECT * FROM %s WHERE %s=?' % (table, parent_col)
        if order_col:
            sql += ' ORDER BY %s ASC' % order_col
        sql += ' LIMIT %d' % int(limit or 25)
        for pid in parent_ids or []:
            try:
                cur = conn.cursor()
                cur.execute(sql, (pid,))
                rows = cur.fetchall() or []
            except Exception:
                rows = []
            for row in rows:
                data = _row_to_dict(cols, row)
                name = str(_db_scalar(data.get(name_col)) or '').strip()
                if not name:
                    continue
                thumb = str(_db_scalar(data.get(thumb_col)) or '').strip() if thumb_col else ''
                # TMDb profile images are also stored as /abc.jpg paths in many helper DB builds.
                if thumb and thumb.startswith('/'):
                    thumb = _to_image_url(thumb, kind='poster')
                cast.append({
                    'name': name,
                    'role': str(_db_scalar(data.get(role_col)) or '').strip() if role_col else '',
                    'thumbnail': thumb,
                })
                if len(cast) >= limit:
                    return cast
    return cast


def _helper_relations(conn, parent_id):
    """Read normalized Helper tables locally; tolerate older schemas.

    All joins are keyed by the resolved typed parent, never the focused item.
    No playback state or progress is read or modified here.
    """
    out = {}
    queries = {
        'studio': 'SELECT c.name FROM studio s JOIN company c ON c.tmdb_id=s.tmdb_id WHERE s.parent_id=?',
        'network': 'SELECT c.name FROM network s JOIN broadcaster c ON c.tmdb_id=s.tmdb_id WHERE s.parent_id=?',
        'country': 'SELECT c.name FROM country s JOIN countries c ON c.iso_country=s.iso_country WHERE s.parent_id=?',
        'director': "SELECT p.name FROM crewmember c JOIN person p ON p.tmdb_id=c.tmdb_id WHERE c.parent_id=? AND lower(c.role)='director'",
        'writer': "SELECT p.name FROM crewmember c JOIN person p ON p.tmdb_id=c.tmdb_id WHERE c.parent_id=? AND lower(c.role) IN ('writer','screenplay','story')",
    }
    for key, sql in queries.items():
        try:
            out[key] = list(dict.fromkeys(str(r[0]) for r in conn.execute(sql + ' LIMIT 25', (parent_id,)) if r[0]))
        except sqlite3.Error:
            pass
    try:
        out['cast'] = [{'name': r[0], 'role': r[1] or ''} for r in conn.execute(
            'SELECT p.name,c.role FROM castmember c JOIN person p ON p.tmdb_id=c.tmdb_id WHERE c.parent_id=? ORDER BY c.ordering LIMIT 25', (parent_id,)) if r[0]]
    except sqlite3.Error:
        pass
    return out


def get_meta_bundle_from_db(tmdb_id='', media_type='movie', imdb_id='', tvdb_id='', title='', year=''):
    """Return TMDb Helper-style metadata from the helper local DB.

    This is intentionally schema-tolerant: TMDb Helper has changed table/column
    names across releases, and skins can populate different caches.  We inspect
    available columns and copy every field Kodi skins normally use so a Dex Hub
    collection item can look like it came directly from TMDb Helper.
    """
    media_type = _normalize_media_type(media_type)
    language = helper_language()
    cache_key = 'meta:%s:%s:%s:%s:%s:%s:%s' % (language, media_type, tmdb_id or '', imdb_id or '', tvdb_id or '', title or '', year or '')
    hit, cached = _meta_memo(cache_key)
    if hit:
        return dict(cached)

    out = {}
    path = _active_db_path()
    if path:
        try:
            conn = _ro_connect(path)
            try:
                row, resolved_tmdb = _find_item_row(conn, media_type, tmdb_id=tmdb_id, imdb_id=imdb_id, title=title, year=year)
                row = _translated_row(conn, row, language)
                tmdb_final = str(tmdb_id or resolved_tmdb or '').strip()
                ids = get_external_ids_from_db(tmdb_id=tmdb_final, media_type=media_type, imdb_id=imdb_id, tvdb_id=tvdb_id, title=title, year=year) or {}
                tmdb_final = str(ids.get('tmdb_id') or tmdb_final or '').strip()
                imdb_final = str(ids.get('imdb_id') or imdb_id or '').strip()
                tvdb_final = str(ids.get('tvdb_id') or tvdb_id or '').strip()
                internal_id = _first_field(row, 'id', 'dbid', 'parent_id')
                parents = _parent_candidates(media_type, tmdb_id=tmdb_final, imdb_id=imdb_final, tvdb_id=tvdb_final, internal_id=internal_id)

                name = _first_field(row, 'title', 'name', 'label', 'showtitle') or title
                original = _first_field(row, 'originaltitle', 'original_title', 'originalname', 'original_name')
                plot = _first_field(row, 'plot', 'overview', 'description')
                tagline = _first_field(row, 'tagline')
                released = _first_field(row, 'premiered', 'released', 'release_date', 'firstaired', 'first_air_date', 'air_date', 'date')
                row_year = _first_field(row, 'year', 'release_year') or year
                if not row_year and released:
                    m = re.search(r'(\d{4})', str(released))
                    row_year = m.group(1) if m else ''
                runtime = _first_field(row, 'runtime', 'duration')
                rating = _first_numeric(row, 'rating', 'vote_average', 'userrating', 'user_rating', 'imdb_rating', 'score')
                votes = _first_numeric(row, 'votes', 'vote_count', 'imdb_votes')
                certification = _first_field(row, 'certification', 'mpaa', 'contentrating', 'content_rating', 'rated')
                if not certification and internal_id:
                    try:
                        cert = conn.execute("SELECT name FROM certification WHERE parent_id=? ORDER BY CASE WHEN iso_country='US' THEN 0 ELSE 1 END LIMIT 1", (internal_id,)).fetchone()
                        certification = cert[0] if cert else ''
                    except sqlite3.Error:
                        pass
                trailer = _first_field(row, 'trailer')
                status = _first_field(row, 'status')

                genres = _as_clean_list(_first_field(row, 'genres', 'genre'))
                if not genres:
                    genres = _query_related_values(conn, ('genre', 'genres'), parents, limit=12)
                studios = _as_clean_list(_first_field(row, 'studio', 'studios', 'production_companies', 'productionCompanies', 'network', 'networks'))
                if not studios:
                    studios = _query_related_values(conn, ('studio', 'studios', 'production_company', 'production_companies', 'network', 'networks'), parents, limit=12)
                countries = _as_clean_list(_first_field(row, 'country', 'countries', 'origin_country'))
                if not countries:
                    countries = _query_related_values(conn, ('country', 'countries'), parents, limit=12)
                directors = _as_clean_list(_first_field(row, 'director', 'directors'))
                if not directors:
                    directors = _query_related_values(conn, ('director', 'directors', 'crew', 'credits_crew'), parents, role_filter=('director',), limit=12)
                writers = _as_clean_list(_first_field(row, 'writer', 'writers'))
                if not writers:
                    writers = _query_related_values(conn, ('writer', 'writers', 'crew', 'credits_crew'), parents, role_filter=('writer', 'screenplay'), limit=12)
                cast = _query_cast(conn, parents, limit=25)
                related = _helper_relations(conn, internal_id) if internal_id else {}
                studios = related.get('studio') or studios or related.get('network') or []
                countries = related.get('country') or countries
                directors = related.get('director') or directors
                writers = related.get('writer') or writers
                cast = related.get('cast') or cast
                if related.get('network'):
                    out['network'] = related['network']
                    out['networks'] = related['network']

                if name:
                    out['name'] = str(name)
                    out['title'] = str(name)
                if original:
                    out['originaltitle'] = str(original)
                if plot:
                    out['description'] = str(plot)
                    out['overview'] = str(plot)
                    out['plot'] = str(plot)
                if tagline:
                    out['tagline'] = str(tagline)
                if released:
                    out['released'] = str(released)
                    out['premiered'] = str(released)
                    out['releaseInfo'] = str(released)
                elif row_year:
                    out['releaseInfo'] = str(row_year)
                if row_year:
                    try:
                        out['year'] = int(float(row_year))
                    except Exception:
                        out['year'] = str(row_year)
                if runtime:
                    out['runtime'] = runtime
                if rating not in (None, ''):
                    out['imdbRating'] = rating
                    out['rating'] = rating
                if votes not in (None, ''):
                    try:
                        out['imdb_votes'] = int(votes)
                        out['votes'] = int(votes)
                    except Exception:
                        out['imdb_votes'] = votes
                        out['votes'] = votes
                if certification:
                    out['certification'] = str(certification)
                    out['mpaa'] = str(certification)
                if trailer:
                    out['trailer'] = str(trailer)
                if status:
                    out['status'] = str(status)
                if genres:
                    out['genres'] = genres
                    out['genre'] = genres
                if studios:
                    out['studios'] = studios
                    out['studio'] = studios
                if countries:
                    out['country'] = countries
                if directors:
                    out['director'] = directors
                if writers:
                    out['writer'] = writers
                if cast:
                    out['cast'] = cast

                if tmdb_final:
                    out['tmdb_id'] = tmdb_final
                    out.setdefault('id', 'tmdb:%s' % tmdb_final)
                if imdb_final:
                    out['imdb_id'] = imdb_final
                if tvdb_final:
                    out['tvdb_id'] = tvdb_final
                out['type'] = 'series' if media_type == 'tv' else 'movie'
            finally:
                _ro_release(conn)
        except Exception:
            out = {}

    # Always attach helper artwork through the existing well-tested resolver.
    art = get_art_bundle_from_db(tmdb_id=out.get('tmdb_id') or tmdb_id, media_type=media_type, imdb_id=out.get('imdb_id') or imdb_id, title=title, year=year) or {}
    poster = art.get('poster') or ''
    fanart = art.get('fanart') or art.get('landscape') or ''
    clearlogo = art.get('clearlogo') or ''
    if poster:
        out['poster'] = poster
        out['thumbnail'] = poster
        out['thumb'] = poster
    if fanart:
        out['background'] = fanart
        out['fanart'] = fanart
        out['landscape'] = art.get('landscape') or fanart
    if clearlogo:
        out['logo'] = clearlogo
        out['clearlogo'] = clearlogo
    if not out.get('landscape') and art.get('landscape'):
        out['landscape'] = art.get('landscape')

    _cache_put(cache_key, dict(out))
    return out

def get_episode_details_from_db(tmdb_id='', imdb_id='', season='', episode=''):
    """Episode-only descriptive fields; exact show/season/episode, no network."""
    language = helper_language()
    key = 'episode:%s:%s:%s:%s:%s' % (language, tmdb_id, imdb_id, season, episode)
    hit, cached = _meta_memo(key)
    if hit:
        return dict(cached)
    out = {}
    path = _active_db_path()
    if not path:
        return out
    conn = None
    try:
        conn = _ro_connect(path)
        show, _ = _find_item_row(conn, 'tv', tmdb_id=tmdb_id, imdb_id=imdb_id)
        if show.get('id') and str(season).isdigit() and str(episode).isdigit():
            cur = conn.execute('SELECT e.* FROM episode e JOIN season s ON s.id=e.season_id WHERE e.tvshow_id=? AND s.season=? AND e.episode=? LIMIT 1', (show['id'], int(season), int(episode)))
            row = cur.fetchone()
            if row:
                data = dict(zip((c[0] for c in cur.description), row))
                data = _translated_row(conn, data, language)
                for field in ('plot', 'title', 'originaltitle', 'premiered', 'year', 'rating', 'votes'):
                    if data.get(field) not in (None, ''):
                        out[field] = data[field]
    except (sqlite3.Error, ValueError, TypeError):
        pass
    finally:
        if conn is not None:
            _ro_release(conn)
    _cache_put(key, dict(out))
    return out


def get_art_bundle_from_db(tmdb_id='', media_type='movie', imdb_id='', title='', year=''):
    media_type = _normalize_media_type(media_type)
    cache_key = 'bundle:%s:%s:%s:%s:%s:%s' % (helper_language(), media_type, tmdb_id or '', imdb_id or '', title or '', year or '')
    hit, cached = _meta_memo(cache_key)
    if hit:
        return dict(cached)

    bundle = {'poster': '', 'fanart': '', 'landscape': '', 'clearlogo': ''}
    path = _active_db_path()
    if path:
        try:
            # Read-only DB — see note in get_external_ids_from_db. (3.8.12)
            conn = _ro_connect(path)
            try:
                resolved_tmdb = tmdb_id or _resolve_tmdb_from_imdb(conn, imdb_id, media_type) or _resolve_tmdb_from_title(conn, title, media_type, year)
                parent_ids = []
                if resolved_tmdb:
                    parent_ids.append('%s.%s' % (media_type, resolved_tmdb))
                    if media_type == 'tv':
                        parent_ids.append('tv.%s' % resolved_tmdb)
                        parent_ids.append('tvshow.%s' % resolved_tmdb)
                    else:
                        parent_ids.append('movie.%s' % resolved_tmdb)
                if imdb_id:
                    parent_ids.extend([
                        '%s.%s' % (media_type, imdb_id),
                        '%s.imdb:%s' % (media_type, imdb_id),
                        imdb_id,
                    ])
                for parent_id in parent_ids:
                    if not bundle['poster']:
                        bundle['poster'] = _query_art_for_parent(conn, parent_id, 'posters') or _query_art_for_parent(conn, parent_id, 'thumb')
                    if not bundle['fanart']:
                        bundle['fanart'] = _query_art_for_parent(conn, parent_id, 'backdrops') or _query_art_for_parent(conn, parent_id, 'fanarts')
                    if not bundle['landscape']:
                        bundle['landscape'] = _query_art_for_parent(conn, parent_id, 'landscape') or _query_art_for_parent(conn, parent_id, 'backdrops')
                    if not bundle['clearlogo']:
                        bundle['clearlogo'] = _query_art_for_parent(conn, parent_id, 'logos')
                    if bundle['poster'] and bundle['fanart'] and bundle['clearlogo']:
                        break
            finally:
                _ro_release(conn)
        except Exception:
            pass
    if not bundle['landscape']:
        bundle['landscape'] = bundle['fanart']
    _cache_put(cache_key, dict(bundle))
    return bundle


def get_clearlogo_from_db(tmdb_id='', media_type='movie', imdb_id=''):
    return (get_art_bundle_from_db(tmdb_id=tmdb_id, media_type=media_type, imdb_id=imdb_id) or {}).get('clearlogo', '')



# --------------------------------------------------------------------------
# v5.10.105: more of TMDb Helper's ready cache (its 6.x ItemDetails.db).
#
# Everything below is read only, keyed by TMDb Helper's own item id
# ('movie.603', 'tv.1399') and returns {} or [] when the title is not in the
# cache, so a caller simply falls back to what it did before.
# --------------------------------------------------------------------------
_TMDB_IMAGE_BASE_STUDIO = 'https://image.tmdb.org/t/p/w300'


def _v6_parent(conn, mt, tmdb_id='', imdb_id=''):
    """TMDb Helper 6 item id for a title, or ''."""
    tmdb = str(tmdb_id or '').strip()
    if not tmdb.isdigit():
        tmdb = _resolve_tmdb_from_imdb(conn, str(imdb_id or '').strip(), mt) if imdb_id else ''
        if not tmdb:
            return ''
    return '%s.%s' % ('tv' if mt == 'tv' else 'movie', tmdb)


def _with_db(fn, default):
    path = _active_db_path()
    if not path:
        return default
    try:
        conn = _ro_connect(path)
    except Exception:
        return default
    try:
        return fn(conn)
    except Exception:
        return default
    finally:
        _ro_release(conn)


def _studio_logo_url(path):
    path = str(path or '').strip()
    if not path:
        return ''
    if path.startswith('http'):
        url = path
    else:
        url = _TMDB_IMAGE_BASE_STUDIO + (path if path.startswith('/') else '/' + path)
    if url.lower().endswith('.svg'):
        url = url[:-4] + '.png'     # TMDb serves every SVG logo as PNG too; Kodi draws no SVG
    return url


def get_studio_logos_from_db(tmdb_id='', media_type='movie', imdb_id='', limit=3):
    """[{'name', 'logo'}] for a title's studios from TMDb Helper's cache.

    A series lists its networks first (HBO, Netflix), then its production
    companies; a film its production companies, in TMDb's order. 'logo' is
    the TMDb image URL ('' when TMDb has no logo for that company).
    """
    mt = _normalize_media_type(media_type)
    key = 'studios:%s:%s:%s:%s' % (mt, tmdb_id or '', imdb_id or '', limit)
    hit, value = _memo(key)
    if hit:
        return [dict(x) for x in (value or [])]

    def read(conn):
        parent = _v6_parent(conn, mt, tmdb_id, imdb_id)
        if not parent:
            return []
        rows = []
        tables = (('network', 'broadcaster'), ('studio', 'company')) if mt == 'tv' else (('studio', 'company'),)
        for link, names in tables:
            cols = _table_columns(conn, names)
            if 'logo' not in cols or 'name' not in cols:
                continue
            try:
                rows.extend(conn.execute(
                    'SELECT c.name, c.logo FROM %s s JOIN %s c ON c.tmdb_id=s.tmdb_id '
                    'WHERE s.parent_id=? ORDER BY s.rowid LIMIT 12' % (link, names), (parent,)).fetchall())
            except Exception:
                continue
        out, seen = [], set()
        for name, logo in rows:
            name = str(name or '').strip()
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            out.append({'name': name, 'logo': _studio_logo_url(logo)})
            if len(out) >= int(limit or 3):
                break
        return out

    out = _with_db(read, [])
    _remember(key, [dict(x) for x in out])
    return out


def _rating_text(value, divisor, decimals=True):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ''
    if number <= 0:
        return ''
    number = number / float(divisor)
    return ('%.1f' % number) if decimals else str(int(round(number)))


def get_ratings_from_db(tmdb_id='', media_type='movie', imdb_id=''):
    """The ratings TMDb Helper already fetched for a title (MDbList, OMDb,
    Trakt, TMDb), in Dex Hub's display keys and scales: imdb, tmdb, trakt
    and mal out of 10, letterboxd out of 5, rt_crit, rt_aud, metacritic and
    mdblist out of 100. TMDb Helper stores them all as 0 to 100 integers.
    """
    mt = _normalize_media_type(media_type)
    key = 'ratings:%s:%s:%s' % (mt, tmdb_id or '', imdb_id or '')
    hit, value = _memo(key)
    if hit:
        return dict(value or {})
    spec = (
        ('imdb', 'imdb_rating', 10, True), ('tmdb', 'tmdb_rating', 10, True),
        ('trakt', 'trakt_rating', 10, True), ('mal', 'myanimelist_rating', 10, True),
        ('letterboxd', 'letterboxd_rating', 20, True),
        ('rt_crit', 'rottentomatoes_rating', 1, False), ('rt_aud', 'rottentomatoes_usermeter', 1, False),
        ('metacritic', 'metacritic_rating', 1, False), ('mdblist', 'mdblist_rating', 1, False),
    )

    def read(conn):
        parent = _v6_parent(conn, mt, tmdb_id, imdb_id)
        if not parent:
            return {}
        out = {}
        cols = _table_columns(conn, 'ratings')
        wanted = [(name, col, div, dec) for name, col, div, dec in spec if col in cols]
        if wanted:
            row = conn.execute('SELECT %s FROM ratings WHERE id=? LIMIT 1'
                               % ', '.join(col for _n, col, _d, _x in wanted), (parent,)).fetchone()
            if row:
                for (name, _col, div, dec), value in zip(wanted, row):
                    text = _rating_text(value, div, dec)
                    if text:
                        out[name] = text
        if not out.get('tmdb'):
            # the TMDb average itself is on the title row (a float out of 10)
            table = 'tvshow' if mt == 'tv' else 'movie'
            row = conn.execute('SELECT rating FROM %s WHERE id=? LIMIT 1' % table, (parent,)).fetchone()
            text = _rating_text(row[0] if row else None, 1, True)
            if text:
                out['tmdb'] = text
        return out

    out = _with_db(read, {})
    _remember(key, dict(out))
    return out


def _ranked_icon(conn, parent, art_type, language, textless_first=False, with_language=False):
    """One TMDb image path from TMDb Helper's art table: the asked language,
    then English, then textless (textless first for backdrops), best rated.
    with_language: (path, its language) instead."""
    found = _ranked_icon_row(conn, parent, art_type, language, textless_first)
    return found if with_language else found[0]


def _ranked_icon_row(conn, parent, art_type, language, textless_first=False):
    language = language if re.fullmatch('[a-z]{2}', language or '') else 'en'
    cols = _table_columns(conn, 'art')
    if not all(c in cols for c in ('icon', 'type', 'parent_id', 'iso_language', 'rating')):
        return '', ''
    if textless_first:
        order = ("CASE WHEN iso_language IS NULL OR iso_language='' THEN 0 WHEN iso_language=? THEN 1 "
                 "WHEN iso_language='en' THEN 2 ELSE 3 END, rating DESC")
    else:
        order = ("CASE WHEN iso_language=? THEN 0 WHEN iso_language='en' THEN 1 "
                 "WHEN iso_language IS NULL OR iso_language='' THEN 2 ELSE 3 END, rating DESC")
    try:
        row = conn.execute('SELECT icon, iso_language FROM art WHERE parent_id=? AND type=? ORDER BY %s LIMIT 1'
                           % order, (parent, art_type, language)).fetchone()
    except Exception:
        row = None
    if not row or not row[0]:
        return '', ''
    return str(row[0]).strip(), str(row[1] or '').strip().lower()


def get_localized_from_db(tmdb_id='', media_type='movie', imdb_id='', language=''):
    """A title's text in `language` from TMDb Helper's cache, or {}.

    TMDb Helper keeps each title in its own language (baseitem.language) and,
    when it caches translations, others in `translation`. Title, plot and
    genres are returned only when they really are in the asked language, so
    a caller can skip its own TMDb request for them; the logo (that language,
    then English, then textless), backdrop, year, runtime, rating and ids
    come along. 'complete' says the text was found in that language.
    """
    mt = _normalize_media_type(media_type)
    want = str(language or '').strip().split('-')[0].lower()
    key = 'local:%s:%s:%s:%s' % (mt, tmdb_id or '', imdb_id or '', want)
    hit, value = _memo(key)
    if hit:
        return dict(value or {})

    def read(conn):
        ids = {'tmdb_id': str(tmdb_id or '').strip(), 'imdb_id': str(imdb_id or '').strip(), 'tvdb_id': ''}
        if not ids['tmdb_id'].isdigit():
            ids['tmdb_id'] = ''
        _v6_unique_ids(conn, mt, ids)
        if not ids.get('tmdb_id') and ids.get('imdb_id') and not _has_v6_ids(conn):
            ids['tmdb_id'] = _resolve_tmdb_from_imdb(conn, ids['imdb_id'], mt)
        if not ids.get('tmdb_id'):
            return {}
        table = 'tvshow' if mt == 'tv' else 'movie'
        parent = '%s.%s' % ('tv' if mt == 'tv' else 'movie', ids['tmdb_id'])
        cols = _table_columns(conn, table)
        if not all(c in cols for c in ('id', 'title', 'plot', 'tagline', 'year', 'duration', 'rating', 'originaltitle')):
            return {}
        language_sql = ('b.language' if 'language' in _table_columns(conn, 'baseitem') else "''")
        row = conn.execute(
            'SELECT t.title, t.plot, t.tagline, t.year, t.duration, t.rating, t.originaltitle, %s '
            'FROM %s t LEFT JOIN baseitem b ON b.id=t.id WHERE t.id=? LIMIT 1' % (language_sql, table),
            (parent,)).fetchone()
        if not row:
            return {}
        title, plot, tagline, year, duration, rating, original, item_language = row
        item_language = str(item_language or '').split('-')[0].lower()
        out = {'tmdb_id': ids['tmdb_id'], 'imdb_id': ids.get('imdb_id') or '',
               'tvdb_id': ids.get('tvdb_id') or ''}
        same = bool(want) and item_language == want
        if want and not same:
            try:
                translated = conn.execute(
                    'SELECT title, plot, tagline FROM translation WHERE parent_id=? AND iso_language=? LIMIT 1',
                    (parent, want)).fetchone()
            except Exception:
                translated = None
            if translated and (translated[0] or translated[1]):
                title, plot, tagline = translated
                same = True
        if same:
            if title:
                out['title'] = str(title)
            if plot:
                out['plot'] = str(plot)
            if tagline:
                out['tagline'] = str(tagline)
            if item_language == want:
                # genre names are stored in TMDb Helper's own language
                try:
                    out['genres'] = [str(r[0]) for r in conn.execute(
                        'SELECT name FROM genre WHERE parent_id=? ORDER BY rowid LIMIT 6', (parent,)) if r[0]]
                except Exception:
                    pass
        out['complete'] = bool(same and plot)
        if original:
            out['originaltitle'] = str(original)
        if year:
            out['year'] = str(year)
        try:
            if duration and int(duration) > 0:
                out['runtime'] = int(duration) // 60       # TMDb Helper keeps seconds
        except (TypeError, ValueError):
            pass
        try:
            if rating and float(rating) > 0:
                out['rating'] = round(float(rating), 1)
        except (TypeError, ValueError):
            pass
        logo, logo_language = _ranked_icon(conn, parent, 'logos', want or 'en', with_language=True)
        if logo:
            out['clearlogo'] = _studio_logo_url(logo).replace(_TMDB_IMAGE_BASE_STUDIO, _TMDB_IMAGE_BASE_LOGO)
            # TMDb Helper asks TMDb for its own language's images (plus
            # English and textless): only then, or with a logo in the asked
            # language at hand, is this the logo TMDb itself would choose
            out['clearlogo_exact'] = bool(want) and (item_language == want or logo_language == want)
        backdrop = _ranked_icon(conn, parent, 'backdrops', want or 'en', textless_first=True)
        if backdrop:
            out['fanart'] = _to_image_url(backdrop, kind='backdrop')
        return out

    out = _with_db(read, {})
    _remember(key, dict(out))
    return out


def get_english_titles_from_db(tmdb_id='', media_type='movie', imdb_id=''):
    """[English title, original title] from TMDb Helper's cache, or [] when
    it holds no English title for the item (v5.10.105).

    Plex and Emby catalogue a film under its English name; this spares the
    TMDb request the server search made for it.
    """
    mt = _normalize_media_type(media_type)
    key = 'en_titles:%s:%s:%s' % (mt, tmdb_id or '', imdb_id or '')
    hit, value = _memo(key)
    if hit:
        return list(value or [])

    def read(conn):
        parent = _v6_parent(conn, mt, tmdb_id, imdb_id)
        if not parent:
            return []
        table = 'tvshow' if mt == 'tv' else 'movie'
        language_sql = ('b.language' if 'language' in _table_columns(conn, 'baseitem') else "''")
        row = conn.execute(
            'SELECT t.title, t.originaltitle, %s FROM %s t LEFT JOIN baseitem b ON b.id=t.id '
            'WHERE t.id=? LIMIT 1' % (language_sql, table), (parent,)).fetchone()
        if not row:
            return []
        title, original, language = row
        english = str(title or '').strip() if str(language or '').lower().startswith('en') else ''
        if not english:
            try:
                translated = conn.execute(
                    "SELECT title FROM translation WHERE parent_id=? AND iso_language='en' "
                    "ORDER BY CASE WHEN iso_country='US' THEN 0 ELSE 1 END LIMIT 1", (parent,)).fetchone()
            except Exception:
                translated = None
            english = str(translated[0] or '').strip() if translated else ''
        if not english:
            return []
        titles = [english]
        original = str(original or '').strip()
        if original and original.casefold() != english.casefold():
            titles.append(original)
        return titles

    out = _with_db(read, [])
    _remember(key, list(out))
    return out


def get_season_episodes_from_db(tmdb_id='', season=0, language=''):
    """One season's episodes from TMDb Helper's cache (v5.10.105).

    {episode number: row} shaped like TMDb's season answer (name, overview,
    still_path, air_date, runtime in minutes), only when TMDb Helper keeps
    the season in the asked language; {} otherwise. The caller decides
    whether the episodes found are all it needs (TMDb Helper may hold only
    the last and next aired episode of a season).
    """
    tmdb = str(tmdb_id or '').strip()
    try:
        number = int(season)
    except (TypeError, ValueError):
        return {}
    if not tmdb.isdigit() or number < 0:
        return {}
    want = str(language or '').strip().split('-')[0].lower()
    key = 'season:%s:%s:%s' % (tmdb, number, want)
    hit, value = _memo(key)
    if hit:
        return {int(k): dict(v) for k, v in (value or {}).items()}

    def read(conn):
        cols = _table_columns(conn, 'episode')
        if not all(c in cols for c in ('id', 'episode', 'title', 'plot', 'premiered', 'duration', 'season_id')):
            return {}
        language_sql = ('b.language' if 'language' in _table_columns(conn, 'baseitem') else "''")
        rows = conn.execute(
            'SELECT e.id, e.episode, e.title, e.plot, e.premiered, e.duration, %s FROM episode e '
            'LEFT JOIN baseitem b ON b.id=e.id WHERE e.season_id=? ORDER BY e.episode' % language_sql,
            ('tv.%s.%s' % (tmdb, number),)).fetchall()
        out = {}
        ids = []
        for eid, episode, title, plot, premiered, duration, item_language in rows:
            if episode is None:
                continue
            item_language = str(item_language or '').split('-')[0].lower()
            if want and item_language and item_language != want:
                return {}       # kept in another language: TMDb's own answer is better
            try:
                runtime = int(duration or 0) // 60
            except (TypeError, ValueError):
                runtime = 0
            out[int(episode)] = {'episode_number': int(episode), 'name': str(title or ''),
                                 'overview': str(plot or ''), 'still_path': '',
                                 'air_date': str(premiered or '')[:10], 'runtime': runtime}
            ids.append((str(eid), int(episode)))
        if ids and all(c in _table_columns(conn, 'art') for c in ('icon', 'type', 'parent_id')):
            by_id = dict(ids)
            marks = ','.join('?' * len(ids))
            try:
                for parent, icon in conn.execute(
                        "SELECT parent_id, icon FROM art WHERE type='stills' AND parent_id IN (%s) "
                        "ORDER BY rating DESC" % marks, tuple(by_id)):
                    row = out.get(by_id.get(str(parent)))
                    if row is not None and icon and not row['still_path']:
                        row['still_path'] = str(icon)
            except Exception:
                pass
        return out

    out = _with_db(read, {})
    _remember(key, {str(k): dict(v) for k, v in out.items()})
    return out
