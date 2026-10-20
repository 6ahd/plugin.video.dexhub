from .. import profile_scope as _scope
# -*- coding: utf-8 -*-
import os
import sqlite3
import threading
import time

from .common import profile_path

DB_PATH = _scope.ScopedPath('playback.db')
_DB_READY = set()
_DB_LOCK = threading.RLock()


_ID_COLUMNS = (
    ('tmdb_id', 'TEXT'),
    ('imdb_id', 'TEXT'),
    ('tvdb_id', 'TEXT'),
    # Episode rows normally carry the series TMDb id.  Keep it separately so
    # a TMDb Helper episode handoff can match even when a provider uses an
    # opaque id for both canonical_id and video_id.
    ('show_tmdb_id', 'TEXT'),
    # Native server identity lets Continue Watching reopen the exact Plex or
    # Emby object even when a translated show title has no usable external ID.
    ('native_server_id', 'TEXT'),
    ('native_item_id', 'TEXT'),
    # v5.10.103: the show's name for episode rows, found once for rows that
    # arrive without one (Nuvio's watch progress carries ids only)
    ('show_title', 'TEXT'),
)


def _connect():
    # v5.10.117: the file stays open (dbkeep): a close is no disk sync
    from . import dbkeep
    return dbkeep.connect(os.fspath(DB_PATH), timeout=10)


def _ensure_db():
    global _DB_READY
    if os.fspath(DB_PATH) in _DB_READY:
        return
    # Kodi can keep the plugin interpreter alive and invoke it from more than
    # one thread.  Serialising the one-time migration prevents concurrent
    # ALTER TABLE calls on slower CoreELEC/Android storage.
    with _DB_LOCK:
        if os.fspath(DB_PATH) in _DB_READY:
            return
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS playback (
                    media_type TEXT NOT NULL,
                    canonical_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    title TEXT,
                    provider_name TEXT,
                    poster TEXT,
                    background TEXT,
                    clearlogo TEXT,
                    season INTEGER,
                    episode INTEGER,
                    position REAL,
                    duration REAL,
                    percent REAL,
                    stream_url TEXT,
                    event_type TEXT,
                    updated_at INTEGER,
                    tmdb_id TEXT,
                    imdb_id TEXT,
                    tvdb_id TEXT,
                    show_tmdb_id TEXT,
                    native_server_id TEXT,
                    native_item_id TEXT,
                    PRIMARY KEY (media_type, canonical_id, video_id)
                )
                """
            )
            # Existing installations already have the original table.  SQLite
            # migrations are additive, so no Continue Watching rows are lost.
            columns = set(row[1] for row in conn.execute('PRAGMA table_info(playback)').fetchall())
            for name, kind in _ID_COLUMNS:
                if name not in columns:
                    conn.execute('ALTER TABLE playback ADD COLUMN %s %s' % (name, kind))
            conn.execute('CREATE INDEX IF NOT EXISTS idx_playback_updated ON playback(updated_at DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_playback_percent ON playback(percent)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_playback_imdb ON playback(imdb_id, updated_at DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_playback_tmdb ON playback(tmdb_id, updated_at DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_playback_show_tmdb ON playback(show_tmdb_id, season, episode, updated_at DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_playback_tvdb ON playback(tvdb_id, updated_at DESC)')
            # Older cloud imports stored Unix milliseconds beside local Unix
            # seconds. The conflict guard then rejected every local update as
            # "older". Normalize only impossible seconds values, preserving
            # identity, progress and chronological ordering of existing rows.
            conn.execute('UPDATE playback SET updated_at=CAST(updated_at / 1000 AS INTEGER) '
                         'WHERE updated_at >= 100000000000')
            conn.commit()
            _DB_READY.add(os.fspath(DB_PATH))
        finally:
            conn.close()


_UPSERT_SQL = """
        INSERT INTO playback (
            media_type, canonical_id, video_id, title, provider_name, poster, background, clearlogo,
            season, episode, position, duration, percent, stream_url, event_type, updated_at,
            tmdb_id, imdb_id, tvdb_id, show_tmdb_id
            , native_server_id, native_item_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(media_type, canonical_id, video_id)
        DO UPDATE SET
            -- v5.10.103: a row pulled from an account without a name or art
            -- (Nuvio's watch progress) never erases the ones already known
            title=CASE WHEN excluded.title != '' THEN excluded.title ELSE playback.title END,
            provider_name=CASE WHEN excluded.provider_name != '' THEN excluded.provider_name ELSE playback.provider_name END,
            poster=CASE WHEN excluded.poster != '' THEN excluded.poster ELSE playback.poster END,
            background=CASE WHEN excluded.background != '' THEN excluded.background ELSE playback.background END,
            clearlogo=CASE WHEN excluded.clearlogo != '' THEN excluded.clearlogo ELSE playback.clearlogo END,
            season=excluded.season,
            episode=excluded.episode,
            position=excluded.position,
            duration=excluded.duration,
            percent=excluded.percent,
            stream_url=excluded.stream_url,
            event_type=excluded.event_type,
            updated_at=excluded.updated_at,
            tmdb_id=CASE WHEN excluded.tmdb_id != '' THEN excluded.tmdb_id ELSE playback.tmdb_id END,
            imdb_id=CASE WHEN excluded.imdb_id != '' THEN excluded.imdb_id ELSE playback.imdb_id END,
            tvdb_id=CASE WHEN excluded.tvdb_id != '' THEN excluded.tvdb_id ELSE playback.tvdb_id END,
            show_tmdb_id=CASE WHEN excluded.show_tmdb_id != '' THEN excluded.show_tmdb_id ELSE playback.show_tmdb_id END,
            native_server_id=CASE WHEN excluded.native_server_id != '' THEN excluded.native_server_id ELSE playback.native_server_id END,
            native_item_id=CASE WHEN excluded.native_item_id != '' THEN excluded.native_item_id ELSE playback.native_item_id END
        WHERE excluded.updated_at >= playback.updated_at
"""


def _mark_sync_dirty():
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty('dexhub.sync_dirty', '1')
    except Exception:
        pass


def _upsert_params(conn, row):
    media_type = row.get('media_type') or ''
    canonical_id = row.get('canonical_id') or ''
    video_id = row.get('video_id') or ''
    ext_updated_at = row.get('ext_updated_at')
    try:
        ts = int(ext_updated_at) if ext_updated_at else int(time.time())
        if ts >= 100000000000:
            ts //= 1000
    except Exception:
        ts = int(time.time())
    # Preserve time only for an actual no-op. A short seek, pause or watched
    # event is a real update even when the position moved less than 30s.
    existing = None
    if ext_updated_at is None:
        existing = conn.execute(
            "SELECT position, updated_at, event_type, percent, duration FROM playback "
            "WHERE media_type=? AND canonical_id=? AND video_id=?",
            (media_type, canonical_id, video_id),
        ).fetchone()
        # v5.10.334: where it was, for the watching-time log (watch_log.py)
        row['_old_position'] = existing[0] if existing else None
    if existing:
        try:
            if (abs(float(row.get('position') or 0.0) - float(existing[0] or 0.0)) < 0.5
                    and (row.get('event_type') or '') == (existing[2] or '')
                    and abs(float(row.get('percent') or 0.0) - float(existing[3] or 0.0)) < 0.01
                    and abs(float(row.get('duration') or 0.0) - float(existing[4] or 0.0)) < 0.5):
                ts = int(existing[1] or ts)
        except Exception:
            pass
    return (
        media_type, canonical_id, video_id, row.get('title') or '',
        row.get('provider_name') or '', row.get('poster') or '',
        row.get('background') or '', row.get('clearlogo') or '',
        row.get('season'), row.get('episode'), row.get('position') or 0.0,
        row.get('duration') or 0.0, row.get('percent') or 0.0,
        row.get('stream_url') or '', row.get('event_type') or '', ts,
        str(row.get('tmdb_id') or '').strip(),
        str(row.get('imdb_id') or '').strip().lower(),
        str(row.get('tvdb_id') or '').strip(),
        str(row.get('show_tmdb_id') or '').strip(),
        str(row.get('native_server_id') or '').strip(),
        str(row.get('native_item_id') or '').strip(),
    )


@_scope.bound
def upsert_entries(entries, mark_dirty=True):
    """Write many progress rows in one SQLite transaction.

    Remote account imports previously called :func:`upsert_entry` hundreds of
    times.  That meant one connection, WAL setup and commit per row and also
    raised ``sync_dirty`` for every row pulled from the cloud.  This bulk path
    performs one connection/commit and lets imports suppress the feedback
    flag while local playback writes retain event-driven sync.
    """
    rows = [dict(row) for row in (entries or []) if isinstance(row, dict)]
    # v5.10.218: a trailer is never watching (trailer_mark.py): its address,
    # or a local save of a film shorter than five minutes
    try:
        from ..trailer_mark import is_trailer_url
    except Exception:
        is_trailer_url = None

    def _trailer(row):
        if is_trailer_url and is_trailer_url(row.get('stream_url')):
            return True
        if mark_dirty and str(row.get('media_type') or '').lower() == 'movie':
            try:
                return 0 < float(row.get('duration') or 0) < 300
            except (TypeError, ValueError):
                return False
        return False
    rows = [row for row in rows if not _trailer(row)]
    if not rows:
        return 0
    _ensure_db()
    changed = 0
    with _DB_LOCK:
        conn = _connect()
        try:
            for row in rows:
                if not (row.get('canonical_id') or row.get('video_id')):
                    continue
                cursor = conn.execute(_UPSERT_SQL, _upsert_params(conn, row))
                try:
                    changed += max(0, int(cursor.rowcount or 0))
                except Exception:
                    changed += 1
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()
    if changed and mark_dirty:
        _mark_sync_dirty()
        try:
            from .. import watch_log    # v5.10.334: the island's week
            watch_log.record([row for row in rows if '_old_position' in row])
        except Exception:
            pass
    return changed


@_scope.bound
def upsert_entry(media_type, canonical_id, video_id, title, provider_name,
                 poster, background, clearlogo, season, episode, position,
                 duration, percent, stream_url, event_type,
                 ext_updated_at=None, tmdb_id='', imdb_id='', tvdb_id='',
                 show_tmdb_id='', native_server_id='', native_item_id='',
                 mark_dirty=True):
    """Insert/update one row; local playback remains dirty-event driven."""
    return upsert_entries([{
        'media_type': media_type, 'canonical_id': canonical_id,
        'video_id': video_id, 'title': title,
        'provider_name': provider_name, 'poster': poster,
        'background': background, 'clearlogo': clearlogo,
        'season': season, 'episode': episode, 'position': position,
        'duration': duration, 'percent': percent, 'stream_url': stream_url,
        'event_type': event_type, 'ext_updated_at': ext_updated_at,
        'tmdb_id': tmdb_id, 'imdb_id': imdb_id, 'tvdb_id': tvdb_id,
        'show_tmdb_id': show_tmdb_id,
        'native_server_id': native_server_id,
        'native_item_id': native_item_id,
    }], mark_dirty=mark_dirty)


@_scope.bound
def update_art(media_type, canonical_id, video_id, poster='', background='', clearlogo=''):
    """Update artwork only without touching updated_at so Continue Watching
    ordering stays stable until the user actually watches something new."""
    _ensure_db()
    conn = _connect()
    conn.execute(
        """
        UPDATE playback
        SET
            poster=CASE WHEN ? != '' THEN ? ELSE poster END,
            background=CASE WHEN ? != '' THEN ? ELSE background END,
            clearlogo=CASE WHEN ? != '' THEN ? ELSE clearlogo END
        WHERE media_type=? AND canonical_id=? AND video_id=?
        """,
        (
            poster or '', poster or '',
            background or '', background or '',
            clearlogo or '', clearlogo or '',
            media_type or '', canonical_id or '', video_id or '',
        ),
    )
    conn.commit()
    conn.close()


# v5.10.156 (issue #4): a title counts as watched where the Nuvio apps say so:
# from 90 % (80 % for Simkl, as Simkl does). It was 95 %: titles stopped in the
# credits stayed in Continue Watching.
WATCHED_SQL = "percent < (CASE WHEN LOWER(COALESCE(provider_name,'')) LIKE '%simkl%' THEN 80.0 ELSE 90.0 END)"


@_scope.bound
def list_continue_items(limit=50):
    _ensure_db()
    conn = _connect()
    rows = conn.execute(
        """
        SELECT media_type, canonical_id, video_id, title, provider_name, poster, background, clearlogo,
               season, episode, position, duration, percent, updated_at,
               tmdb_id, imdb_id, tvdb_id, show_tmdb_id, native_server_id, native_item_id,
               show_title, event_type, stream_url
        FROM playback
        WHERE percent < (CASE WHEN LOWER(COALESCE(provider_name,'')) LIKE '%simkl%' THEN 80.0 ELSE 90.0 END)
        ORDER BY updated_at DESC, title COLLATE NOCASE ASC, canonical_id ASC, video_id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        out.append({
            'media_type': row[0], 'canonical_id': row[1], 'video_id': row[2], 'title': row[3],
            'provider_name': row[4], 'poster': row[5], 'background': row[6], 'clearlogo': row[7],
            'season': row[8], 'episode': row[9], 'position': row[10], 'duration': row[11],
            'percent': row[12], 'updated_at': row[13],
            'tmdb_id': row[14], 'imdb_id': row[15], 'tvdb_id': row[16], 'show_tmdb_id': row[17],
            'native_server_id': row[18], 'native_item_id': row[19],
            'show_title': row[20] or '',
            'event_type': row[21] or '', 'stream_url': row[22] or '',
        })
    return out


@_scope.bound
def list_finished_items(limit=400):
    """v5.10.236: the titles finished lately (watched percent, or a watched
    event): Continue Watching leaves out an older half-way row of the same
    episode or film kept under another id or by another service."""
    _ensure_db()
    conn = _connect()
    rows = conn.execute(
        """
        SELECT media_type, canonical_id, video_id, season, episode, updated_at,
               tmdb_id, imdb_id, show_tmdb_id, title, show_title, native_server_id
        FROM playback
        WHERE NOT (""" + WATCHED_SQL + """) OR LOWER(COALESCE(event_type,'')) IN ('watched', 'finished')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (int(limit or 400),),
    ).fetchall()
    conn.close()
    return [{'media_type': r[0], 'canonical_id': r[1], 'video_id': r[2], 'season': r[3], 'episode': r[4],
             'updated_at': r[5], 'tmdb_id': r[6], 'imdb_id': r[7], 'show_tmdb_id': r[8],
             'title': r[9], 'show_title': r[10] or '', 'native_server_id': r[11]} for r in rows]


@_scope.bound
def set_names(items):
    """Save names found for rows that had none; a known name is never replaced.

    items: [(media_type, canonical_id, video_id, title, show_title)]. The
    listing order (updated_at) is left alone.
    """
    rows = [tuple(item) for item in (items or []) if item and len(item) >= 5]
    if not rows:
        return 0
    _ensure_db()
    changed = 0
    with _DB_LOCK:
        conn = _connect()
        try:
            for media_type, canonical_id, video_id, title, show_title in rows:
                cursor = conn.execute(
                    """
                    UPDATE playback SET
                        title=CASE WHEN ? != '' AND (title IS NULL OR title='' OR title=canonical_id)
                                   THEN ? ELSE title END,
                        show_title=CASE WHEN ? != '' AND (show_title IS NULL OR show_title='')
                                        THEN ? ELSE show_title END
                    WHERE media_type=? AND canonical_id=? AND video_id=?
                    """,
                    (title or '', title or '', show_title or '', show_title or '',
                     media_type or '', canonical_id or '', video_id or ''))
                try:
                    changed += max(0, int(cursor.rowcount or 0))
                except Exception:
                    pass
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()
    return changed


@_scope.bound
def list_recent_items(limit=200, media_types=None, include_watched=True):
    """Return recent playback rows without forcing the Continue Watching filter.

    Used by Next Up and diagnostics where fully watched episodes must remain
    visible as the base for finding the *next* episode.
    """
    _ensure_db()
    conn = _connect()
    where = []
    params = []
    if media_types:
        mts = [str(x or '').strip().lower() for x in media_types if str(x or '').strip()]
        if mts:
            where.append('media_type IN (%s)' % ','.join(['?'] * len(mts)))
            params.extend(mts)
    if not include_watched:
        where.append(WATCHED_SQL)
    sql = """
        SELECT media_type, canonical_id, video_id, title, provider_name, poster, background, clearlogo,
               season, episode, position, duration, percent, updated_at, event_type, stream_url,
               tmdb_id, imdb_id, tvdb_id, show_tmdb_id, native_server_id, native_item_id,
               show_title
        FROM playback
    """
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += " ORDER BY updated_at DESC, title COLLATE NOCASE ASC, canonical_id ASC, video_id ASC LIMIT ?"
    params.append(int(limit or 200))
    rows = conn.execute(sql, tuple(params)).fetchall()
    conn.close()
    out = []
    for row in rows:
        out.append({
            'media_type': row[0], 'canonical_id': row[1], 'video_id': row[2], 'title': row[3],
            'provider_name': row[4], 'poster': row[5], 'background': row[6], 'clearlogo': row[7],
            'season': row[8], 'episode': row[9], 'position': row[10], 'duration': row[11],
            'percent': row[12], 'updated_at': row[13], 'event_type': row[14], 'stream_url': row[15],
            'tmdb_id': row[16], 'imdb_id': row[17], 'tvdb_id': row[18], 'show_tmdb_id': row[19],
            'native_server_id': row[20], 'native_item_id': row[21],
            'show_title': row[22] or '',
        })
    return out


def _row_to_dict(row):
    return {
        'media_type': row[0], 'canonical_id': row[1], 'video_id': row[2], 'title': row[3],
        'provider_name': row[4], 'poster': row[5], 'background': row[6], 'clearlogo': row[7],
        'season': row[8], 'episode': row[9], 'position': row[10], 'duration': row[11],
        'percent': row[12], 'updated_at': row[13], 'tmdb_id': row[14], 'imdb_id': row[15],
        'tvdb_id': row[16], 'show_tmdb_id': row[17],
        'native_server_id': row[18], 'native_item_id': row[19],
    }


@_scope.bound
def find_resume_entry(media_type='movie', canonical_id='', tmdb_id='', imdb_id='', tvdb_id='', season=None, episode=None):
    """Return the best unfinished row for a TMDb Helper playback request.

    The priority is deliberate: IMDb is globally stable, then TMDb (the show
    id is also checked for episodes), then TVDb, then Dex Hub's legacy
    provider/canonical ids.  Each query is indexed and only asks SQLite for a
    single row, avoiding the old full scan of the latest 500 records.
    """
    _ensure_db()
    mt = str(media_type or 'movie').strip().lower()
    is_episode = mt in ('series', 'anime', 'show', 'tv') or (season not in (None, '', 0, '0') and episode not in (None, '', 0, '0'))
    try:
        season_i = int(season or 0)
        episode_i = int(episode or 0)
    except Exception:
        season_i, episode_i = 0, 0
    base = ['percent < 95.0', '(position > 30.0 OR percent > 1.0)']
    params = []
    if is_episode:
        base.extend(['season=?', 'episode=?'])
        params.extend([season_i, episode_i])
    else:
        base.append("media_type IN ('movie', 'movies', '')")
    select = """
        SELECT media_type, canonical_id, video_id, title, provider_name, poster, background, clearlogo,
               season, episode, position, duration, percent, updated_at,
               tmdb_id, imdb_id, tvdb_id, show_tmdb_id, native_server_id, native_item_id
        FROM playback WHERE %s AND %%s
        ORDER BY updated_at DESC LIMIT 1
    """ % ' AND '.join(base)
    matches = []
    imdb = str(imdb_id or '').strip().lower()
    if imdb.lower().startswith('imdb:'):
        imdb = imdb.split(':', 1)[1].strip()
    if imdb.isdigit():
        imdb = 'tt%s' % imdb
    if imdb:
        matches.append(('LOWER(imdb_id)=?', [imdb]))
    tmdb = str(tmdb_id or '').strip()
    if tmdb:
        matches.append(('(tmdb_id=? OR show_tmdb_id=?)', [tmdb, tmdb]))
    tvdb = str(tvdb_id or '').strip()
    if tvdb:
        matches.append(('tvdb_id=?', [tvdb]))
    canonical = str(canonical_id or '').strip()
    if canonical:
        matches.append(('(canonical_id=? OR video_id=?)', [canonical, canonical]))
    if not matches:
        return None
    conn = _connect()
    try:
        for clause, values in matches:
            row = conn.execute(select % clause, tuple(params + values)).fetchone()
            if row:
                return _row_to_dict(row)
    finally:
        conn.close()
    return None


@_scope.bound
def delete_entry(media_type, canonical_id, video_id):
    _ensure_db()
    conn = _connect()
    conn.execute(
        "DELETE FROM playback WHERE media_type=? AND canonical_id=? AND video_id=?",
        (media_type or '', canonical_id or '', video_id or ''),
    )
    conn.commit()
    conn.close()


@_scope.bound
def mark_watched(media_type, canonical_id, video_id):
    """Finish an existing row and schedule the usual local-progress sync."""
    _ensure_db()
    with _DB_LOCK:
        conn = _connect()
        try:
            cursor = conn.execute(
                "UPDATE playback SET percent=100.0, event_type='watched', "
                "position=CASE WHEN duration>0 THEN duration ELSE position END, updated_at=? "
                "WHERE media_type=? AND canonical_id=? AND video_id=?",
                (int(time.time()), media_type or '', canonical_id or '', video_id or ''),
            )
            changed = cursor.rowcount > 0
            conn.commit()
        finally:
            conn.close()
    if changed:
        _mark_sync_dirty()
    return changed


@_scope.bound
def reconcile_simkl_up_next(entries):
    """Replace only Simkl's synthetic zero-position suggestions atomically.

    Local playback and other providers retain ownership of their progress.
    Call only after both Simkl watching lists were fetched successfully.
    """
    rows = [dict(row) for row in entries if isinstance(row, dict)]
    keep = {(row['media_type'], row['canonical_id'], row['video_id']) for row in rows}
    _ensure_db()
    changed = 0
    with _DB_LOCK:
        conn = _connect()
        try:
            old = conn.execute(
                "SELECT media_type,canonical_id,video_id FROM playback "
                "WHERE provider_name='Simkl' AND event_type='progress' "
                "AND COALESCE(position,0)=0 AND COALESCE(duration,0)=0 "
                "AND COALESCE(percent,0)=0").fetchall()
            for row in rows:
                # A real pause point takes precedence over a next-episode
                # suggestion. A newer watched event must not be reset to 0.
                aliases, values = ['canonical_id=?'], [row['canonical_id']]
                for name in ('imdb_id', 'tmdb_id', 'tvdb_id'):
                    if row.get(name):
                        aliases.append('%s=?' % name)
                        values.append(str(row[name]))
                existing = conn.execute(
                    "SELECT 1 FROM playback WHERE media_type IN ('series','show','tv','anime','episode') "
                    'AND (' + ' OR '.join(aliases) + ') AND '
                    '(((COALESCE(position,0)>0 OR COALESCE(percent,0)>0) AND percent<95) '
                    'OR (percent>=95 AND season=? AND episode=?)) LIMIT 1',
                    values + [row['season'], row['episode']]).fetchone()
                if existing:
                    keep.discard((row['media_type'], row['canonical_id'], row['video_id']))
                    continue
                cursor = conn.execute(_UPSERT_SQL, _upsert_params(conn, row))
                changed += max(0, cursor.rowcount)
            for key in old:
                if tuple(key) not in keep:
                    cursor = conn.execute(
                        'DELETE FROM playback WHERE media_type=? AND canonical_id=? AND video_id=?', key)
                    changed += max(0, cursor.rowcount)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return changed


@_scope.bound
def reconcile_provider_rows(provider, entries):
    """v5.10.147: an account's resume positions (Floppy) in one transaction.

    A title already known here (played on this device, a server, another
    account) keeps its row and its name; it only takes the account's
    position when that one is newer. Titles new to Dex Hub get a row named
    after the account. The account's own rows it no longer lists (finished
    or cleared there) are removed; rows of anything else are never touched.
    """
    rows = [dict(row) for row in entries if isinstance(row, dict) and row.get('canonical_id')]
    keep = set()
    _ensure_db()
    changed = 0
    with _DB_LOCK:
        conn = _connect()
        try:
            for row in rows:
                key = (row['media_type'], row['canonical_id'], row['video_id'])
                keep.add(key)
                stamp = int(row.get('ext_updated_at') or time.time())
                found = conn.execute(
                    'SELECT provider_name, updated_at FROM playback WHERE media_type=? AND canonical_id=? AND video_id=?',
                    key).fetchone()
                if found is None:
                    cursor = conn.execute(_UPSERT_SQL, _upsert_params(conn, row))
                elif found[0] == provider:
                    cursor = conn.execute(_UPSERT_SQL, _upsert_params(conn, row))
                else:
                    cursor = conn.execute(
                        'UPDATE playback SET position=?, duration=?, percent=?, updated_at=? '
                        'WHERE media_type=? AND canonical_id=? AND video_id=? AND updated_at < ?',
                        (row.get('position') or 0.0, row.get('duration') or 0.0,
                         row.get('percent') or 0.0, stamp) + key + (stamp,))
                changed += max(0, cursor.rowcount)
            old = conn.execute('SELECT media_type, canonical_id, video_id FROM playback '
                               'WHERE provider_name=? AND COALESCE(percent,0)<95', (provider,)).fetchall()
            for key in old:
                if tuple(key) not in keep:
                    cursor = conn.execute(
                        'DELETE FROM playback WHERE media_type=? AND canonical_id=? AND video_id=?', tuple(key))
                    changed += max(0, cursor.rowcount)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return changed


@_scope.bound
def mark_remote_watched(entries):
    """Apply exact remote watch events in one transaction, preserving rewatches.

    IDs also match provider-specific rows. Remote timestamps determine the
    conflict outcome; importing an old watch never stamps it with 'now'.
    """
    _ensure_db()
    counts = [0, 0]
    with _DB_LOCK:
        conn = _connect()
        try:
            for row in entries:
                canonical = row.get('canonical_id') or ''
                aliases, values = ['canonical_id=?'], [canonical]
                for name in ('imdb_id', 'tmdb_id', 'tvdb_id'):
                    value = str(row.get(name) or '').strip()
                    if value:
                        aliases.append('%s=?' % name)
                        values.append(value)
                        if name == 'tmdb_id' and row.get('media_type') == 'series':
                            aliases.append('show_tmdb_id=?')
                            values.append(value)
                where = '(' + ' OR '.join(aliases) + ') AND COALESCE(percent,0)<100'
                series = row.get('media_type') == 'series'
                if series:
                    where += " AND media_type IN ('series','show','tv','anime','episode') AND season=? AND episode=?"
                    values.extend([row['season'], row['episode']])
                else:
                    where += " AND media_type IN ('movie','movies','')"
                stamp = int(row.get('watched_at') or 0)
                if stamp > 0:
                    where += ' AND updated_at<=?'
                    values.append(stamp)
                else:
                    # Unknown watch dates cannot prove that a later local
                    # pause is stale. Only clear old synthetic suggestions.
                    where += " AND provider_name='Simkl' AND COALESCE(position,0)=0 AND COALESCE(percent,0)=0"
                cursor = conn.execute(
                    'UPDATE playback SET percent=100.0, updated_at=CASE WHEN ?>0 THEN ? ELSE updated_at END WHERE ' + where,
                    [stamp, stamp] + values)
                counts[1 if series else 0] += max(0, cursor.rowcount)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return tuple(counts)
