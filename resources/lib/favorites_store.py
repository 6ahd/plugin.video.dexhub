# -*- coding: utf-8 -*-
"""Local favorites store.

Lightweight SQLite-backed favourites/watchlist for users who don't have Trakt
linked, AND a stable mirror cache for users who do (so the home-screen
"المفضلة" row stays instant even when Trakt is slow/offline).

Schema mirrors the Continue Watching shape so the same row-rendering code in
plugin.py can consume both.
"""
from . import profile_scope as _scope
import os
import sqlite3
import threading
import time

import xbmc

from .dexhub.common import profile_path

DB_PATH = _scope.ScopedPath('favorites.db')
_DB_READY = set()
_DB_LOCK = threading.Lock()

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS favorites (
    media_type TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    title TEXT,
    poster TEXT,
    background TEXT,
    clearlogo TEXT,
    year INTEGER,
    plot TEXT,
    source TEXT NOT NULL DEFAULT 'local',
    added_at INTEGER,
    PRIMARY KEY (media_type, canonical_id, source)
)
"""


def _connect():
    # v5.10.117: the file stays open (dbkeep): a close is no disk sync
    from .dexhub import dbkeep
    return dbkeep.connect(os.fspath(DB_PATH), timeout=2.0)


def _table_sql(conn, name):
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row[0] if row and row[0] else ''
    except Exception:
        return ''


def _needs_migration(conn):
    sql = (_table_sql(conn, 'favorites') or '').lower().replace('\n', ' ')
    if not sql:
        return False
    return 'primary key (media_type, canonical_id, source)' not in sql


def _migrate_schema(conn):
    if not _needs_migration(conn):
        return
    conn.execute('ALTER TABLE favorites RENAME TO favorites_legacy')
    conn.execute(CREATE_SQL)
    conn.execute(
        """
        INSERT OR REPLACE INTO favorites (
            media_type, canonical_id, title, poster, background,
            clearlogo, year, plot, source, added_at
        )
        SELECT
            media_type,
            canonical_id,
            title,
            poster,
            background,
            clearlogo,
            year,
            plot,
            COALESCE(NULLIF(source, ''), 'local') AS source,
            added_at
        FROM favorites_legacy
        """
    )
    conn.execute('DROP TABLE favorites_legacy')


def _ensure_db():
    global _DB_READY
    if os.fspath(DB_PATH) in _DB_READY:
        return
    with _DB_LOCK:
        if os.fspath(DB_PATH) in _DB_READY:
            return
        conn = _connect()
        try:
            if _table_sql(conn, 'favorites'):
                _migrate_schema(conn)
            else:
                conn.execute(CREATE_SQL)
            conn.execute('CREATE INDEX IF NOT EXISTS idx_fav_added ON favorites(added_at DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_fav_source ON favorites(source)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_fav_identity ON favorites(media_type, canonical_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_fav_source_page ON favorites(source, added_at DESC, canonical_id)')
            # Older merged artwork enrichment accidentally created membership
            # under "mixed". Move its cached art to the actual owners; keep
            # an otherwise orphaned saved title in the local list.
            # v5.10.140: only when such rows exist; every process that opened
            # the favourites ran these writes (and their disk sync) before.
            if conn.execute("SELECT 1 FROM favorites WHERE source='mixed' LIMIT 1").fetchone() is not None:
                for field in ('poster', 'background', 'clearlogo', 'plot'):
                    conn.execute("""UPDATE favorites SET %s=(SELECT m.%s FROM favorites m
                        WHERE m.source='mixed' AND m.media_type=favorites.media_type
                        AND m.canonical_id=favorites.canonical_id)
                        WHERE source!='mixed' AND COALESCE(%s,'')=''
                        AND EXISTS (SELECT 1 FROM favorites m WHERE m.source='mixed'
                        AND m.media_type=favorites.media_type AND m.canonical_id=favorites.canonical_id
                        AND COALESCE(m.%s,'')!='')""" % (field, field, field, field))
                conn.execute("""DELETE FROM favorites WHERE source='mixed' AND EXISTS
                    (SELECT 1 FROM favorites other WHERE other.source!='mixed'
                     AND other.media_type=favorites.media_type AND other.canonical_id=favorites.canonical_id)""")
                conn.execute("UPDATE favorites SET source='local' WHERE source='mixed'")
            conn.commit()
        finally:
            conn.close()
        _DB_READY.add(os.fspath(DB_PATH))


def _mark_sync_dirty():
    """v4.8.2: tell the service a local change is waiting.

    The continuous sync loop wakes every 20s and syncs immediately when
    this flag is set, so a favourite added here reaches Nuvio in seconds
    instead of waiting for the next scheduled pull.
    """
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty('dexhub.sync_dirty', '1')
    except Exception:
        pass


@_scope.bound
def add(media_type, canonical_id, title, poster='', background='', clearlogo='',
        year=0, plot='', source='local'):
    _ensure_db()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO favorites (media_type, canonical_id, title, poster, background,
                                   clearlogo, year, plot, source, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_type, canonical_id, source) DO UPDATE SET
                title=excluded.title,
                poster=CASE WHEN excluded.poster != '' THEN excluded.poster ELSE favorites.poster END,
                background=CASE WHEN excluded.background != '' THEN excluded.background ELSE favorites.background END,
                clearlogo=CASE WHEN excluded.clearlogo != '' THEN excluded.clearlogo ELSE favorites.clearlogo END,
                year=CASE WHEN excluded.year > 0 THEN excluded.year ELSE favorites.year END,
                plot=CASE WHEN excluded.plot != '' THEN excluded.plot ELSE favorites.plot END,
                added_at=excluded.added_at
            """,
            (media_type or 'movie', canonical_id or '', title or canonical_id or '',
             poster or '', background or '', clearlogo or '', int(year or 0),
             plot or '', source or 'local', int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()
    if (source or 'local') != 'nuvio':      # never echo a pulled row back
        _mark_sync_dirty()


@_scope.bound
def set_title(media_type, canonical_id, title):
    """v5.10.103: a name found for a row saved without one; the order stays."""
    title = str(title or '').strip()
    if not title:
        return
    _ensure_db()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE favorites SET title=? WHERE media_type=? AND canonical_id=? "
            "AND (title IS NULL OR title='' OR title=canonical_id OR title='Untitled')",
            (title, media_type or 'movie', canonical_id or ''))
        conn.commit()
    finally:
        conn.close()


@_scope.bound
def remove(media_type, canonical_id, source=None):
    _ensure_db()
    conn = _connect()
    try:
        if source:
            conn.execute(
                "DELETE FROM favorites WHERE media_type=? AND canonical_id=? AND source=?",
                (media_type or '', canonical_id or '', source or ''),
            )
        else:
            conn.execute(
                "DELETE FROM favorites WHERE media_type=? AND canonical_id=?",
                (media_type or '', canonical_id or ''),
            )
        conn.commit()
    finally:
        conn.close()
    _mark_sync_dirty()


@_scope.bound
def is_favorite(media_type, canonical_id):
    _ensure_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE media_type=? AND canonical_id=? LIMIT 1",
            (media_type or '', canonical_id or ''),
        ).fetchone()
    finally:
        conn.close()
    return bool(row)




@_scope.bound
def favorite_keys(source=None):
    _ensure_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT media_type, canonical_id FROM favorites" +
            (" WHERE source=?" if source else "") +
            " GROUP BY media_type, canonical_id", (source,) if source else (),
        ).fetchall()
    finally:
        conn.close()
    return set((str(r[0] or ''), str(r[1] or '')) for r in rows or [])

@_scope.bound
def list_favorites(limit=200, source=None, offset=0):
    _ensure_db()
    conn = _connect()
    try:
        if source:
            rows = conn.execute(
                """
                SELECT media_type, canonical_id, title, poster, background, clearlogo,
                       year, plot, source, added_at
                FROM favorites WHERE source=?
                ORDER BY added_at DESC, canonical_id ASC LIMIT ? OFFSET ?
                """,
                (source, int(limit) if limit else -1, max(0, int(offset or 0))),
            ).fetchall()
            return [{
                'media_type': row[0], 'canonical_id': row[1], 'title': row[2],
                'poster': row[3], 'background': row[4], 'clearlogo': row[5],
                'year': row[6], 'plot': row[7], 'source': row[8],
                'sources': [row[8]], 'added_at': row[9],
            } for row in rows]

        rows = conn.execute(
            """
            SELECT media_type, canonical_id, title, poster, background, clearlogo,
                   year, plot, source, added_at
            FROM favorites
            ORDER BY added_at DESC, CASE WHEN source='local' THEN 0 ELSE 1 END
            """
        ).fetchall()
    finally:
        conn.close()

    out = []
    seen = {}
    for row in rows:
        media_type, canonical_id = row[0], row[1]
        key = (media_type, canonical_id)
        source_name = row[8] or 'local'
        payload = {
            'media_type': media_type,
            'canonical_id': canonical_id,
            'title': row[2],
            'poster': row[3],
            'background': row[4],
            'clearlogo': row[5],
            'year': row[6],
            'plot': row[7],
            'source': source_name,
            'sources': [source_name],
            'added_at': row[9],
        }
        existing = seen.get(key)
        if not existing:
            seen[key] = payload
            out.append(payload)
            continue
        sources = set(existing.get('sources') or [])
        sources.add(source_name)
        existing['sources'] = sorted(sources)
        if source_name != existing.get('source'):
            existing['source'] = 'mixed'
        for field in ('title', 'poster', 'background', 'clearlogo', 'year', 'plot'):
            if not existing.get(field) and payload.get(field):
                existing[field] = payload.get(field)
        existing['added_at'] = max(int(existing.get('added_at') or 0), int(payload.get('added_at') or 0))

    out.sort(key=lambda row: int(row.get('added_at') or 0), reverse=True)
    if limit:
        return out[max(0, int(offset or 0)): max(0, int(offset or 0)) + max(0, int(limit or 0))]
    return out[max(0, int(offset or 0)):]


@_scope.bound
def replace_source_mirror(source, rows):
    """Atomically replace one successful provider snapshot; retain cached art.

    Callers must propagate request failures instead of passing an empty list.
    A genuine empty snapshot clears only this provider's membership.
    """
    if source not in ('trakt', 'simkl', 'mdblist', 'nuvio', 'stremio', 'floppy'):
        raise ValueError('Unknown favorites source')
    if not isinstance(rows, list):
        raise ValueError('Invalid favorites snapshot')
    previous = {(r['media_type'], r['canonical_id']): r
                for r in list_favorites(limit=0, source=source)}
    values = []
    seen = set()
    for row in rows:
        mt, cid = row.get('media_type') or 'movie', row.get('canonical_id') or ''
        if not cid or (mt, cid) in seen:
            continue
        seen.add((mt, cid))
        old = previous.get((mt, cid), {})
        values.append((mt, cid, row.get('title') or old.get('title') or cid,
                       row.get('poster') or old.get('poster') or '',
                       row.get('background') or old.get('background') or '',
                       row.get('clearlogo') or old.get('clearlogo') or '',
                       int(row.get('year') or old.get('year') or 0),
                       row.get('plot') or old.get('plot') or '', source,
                       int(row.get('added_at') or old.get('added_at') or time.time())))
    _ensure_db()
    conn = _connect()
    try:
        conn.execute('DELETE FROM favorites WHERE source=?', (source,))
        conn.executemany("""INSERT INTO favorites (media_type, canonical_id, title,
            poster, background, clearlogo, year, plot, source, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



@_scope.bound
def source_counts():
    """Navigation-only counts, with no account or metadata requests."""
    _ensure_db()
    conn = _connect()
    try:
        # v5.10.168: titles, as count() counts them (a title saved twice in one
        # source is one), so a chip never says more than its row holds
        return dict(conn.execute('SELECT source, COUNT(*) FROM (SELECT DISTINCT source, media_type, canonical_id '
                                 'FROM favorites) GROUP BY source').fetchall())
    finally:
        conn.close()


@_scope.bound
def source_covers():
    """{source: (poster, backdrop)} of each source's newest favourite with a
    poster: the cover of its card on the favourites page (v5.10.140)."""
    _ensure_db()
    conn = _connect()
    try:
        out = {}
        # SQLite: with MAX() the bare columns come from the row holding it
        for source, poster, background, _added in conn.execute(
                "SELECT source, poster, background, MAX(added_at) FROM favorites "
                "WHERE COALESCE(poster,'')!='' GROUP BY source"):
            out[source] = (poster or '', background or '')
        return out
    finally:
        conn.close()


@_scope.bound
def count(source=None):
    _ensure_db()
    conn = _connect()
    try:
        if source:
            n = conn.execute(
                'SELECT COUNT(*) FROM (SELECT 1 FROM favorites WHERE source=? GROUP BY media_type, canonical_id)',
                (source,),
            ).fetchone()[0]
        else:
            n = conn.execute(
                'SELECT COUNT(*) FROM (SELECT 1 FROM favorites GROUP BY media_type, canonical_id)'
            ).fetchone()[0]
    finally:
        conn.close()
    return int(n or 0)


@_scope.bound
def refresh_external_mirror(include_trakt=True, others=True):
    """Independent snapshots: never relabel another service as Trakt.

    Only successful fetches replace membership. A timeout/403 keeps that
    service's last-good list, while other services can still update.
    """
    import xbmcaddon

    def flag(key):
        return (xbmcaddon.Addon().getSetting(key) or 'true').strip().lower() == 'true'

    total = 0
    jobs = []
    trakt_mod = None
    if include_trakt:
        from . import trakt
        trakt_mod = trakt
        # v5.10.208: Trakt's watchlist only when it changed there (smart sync)
        if trakt.enabled() and not trakt.unchanged('watchlist'):
            jobs.append(('trakt', lambda: trakt.fetch_watchlist(limit=1000, strict=True, fetch_art=False)))
    from . import simkl, mdblist
    if others and simkl.enabled() and simkl.authorized() and flag('watchlist_merge_simkl'):
        jobs.append(('simkl', lambda: simkl.watchlist_mirror_rows(limit=1000, strict=True)))
    if others and mdblist.configured() and flag('watchlist_merge_mdblist'):
        jobs.append(('mdblist', lambda: mdblist.watchlist_mirror_rows(limit=1000, strict=True)))
    # v5.10.153: Floppy's "Planning" titles are its watchlist
    from . import floppy_api
    if others and floppy_api.configured() and flag('watchlist_merge_floppy'):
        jobs.append(('floppy', floppy_api.watchlist_mirror_rows))
    for source, fetch in jobs:
        try:
            rows = fetch()
            replace_source_mirror(source, rows)
            total += len(rows)
            if source == 'trakt' and trakt_mod is not None:
                trakt_mod.mark_synced('watchlist')
        except Exception as exc:
            xbmc.log('[DexHub] watchlist mirror (%s) retained: %s' % (source, exc), xbmc.LOGDEBUG)
    return total


@_scope.bound
def update_art(media_type, canonical_id, source, art):
    """Enrich cached artwork without changing list membership or added order."""
    _ensure_db()
    conn = _connect()
    try:
        conn.execute("""UPDATE favorites SET
            poster=CASE WHEN ?!='' THEN ? ELSE poster END,
            background=CASE WHEN ?!='' THEN ? ELSE background END,
            clearlogo=CASE WHEN ?!='' THEN ? ELSE clearlogo END
            WHERE media_type=? AND canonical_id=? AND (?='mixed' OR source=?)""",
            (art.get('poster') or '', art.get('poster') or '',
             art.get('fanart') or '', art.get('fanart') or '',
             art.get('clearlogo') or '', art.get('clearlogo') or '',
             media_type, canonical_id, source, source))
        conn.commit()
    finally:
        conn.close()
