# -*- coding: utf-8 -*-
"""Local search history.

Saves the last N search queries the user typed so they can pick from a
dropdown instead of retyping. POV's search-history UX, simplified.

Storage: SQLite under the addon profile. We track count + last_used so a
"top searches" list works on top of plain history.
"""
import os
import sqlite3
import threading
import time

from .log import log
from .dexhub.common import profile_path

DB_PATH = os.path.join(profile_path(), 'search_history.db')

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS history (
    query       TEXT PRIMARY KEY,
    count       INTEGER NOT NULL DEFAULT 1,
    last_used   INTEGER NOT NULL,
    media_type  TEXT NOT NULL DEFAULT 'all'
)
"""

MAX_ENTRIES = 50  # ring-buffer cap

_DB_READY = False
_LOCK = threading.Lock()


def _connect():
    # Search history is non-critical; never freeze the UI behind a DB writer.
    # v5.10.117: the file stays open (dbkeep): a close is no disk sync
    from .dexhub import dbkeep
    return dbkeep.connect(DB_PATH, timeout=0.35)


def _ensure_db():
    global _DB_READY
    if _DB_READY:
        return
    with _LOCK:
        if _DB_READY:
            return
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = _connect()
            try:
                conn.execute(CREATE_SQL)
                conn.execute('CREATE INDEX IF NOT EXISTS idx_history_last ON history(last_used DESC)')
                conn.commit()
            finally:
                conn.close()
            _DB_READY = True
        except Exception as exc:
            log.warn('SEARCH', 'history init failed: %s', exc)


def add(query, media_type='all'):
    """Record a search. Bumps count + last_used; trims oldest if over cap."""
    query = (query or '').strip()
    if not query or len(query) > 200:
        return
    _ensure_db()
    if not _DB_READY:
        return
    now = int(time.time())
    try:
        conn = _connect()
        try:
            conn.execute("""
                INSERT INTO history (query, count, last_used, media_type)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(query) DO UPDATE SET
                    count = count + 1,
                    last_used = excluded.last_used,
                    media_type = excluded.media_type
            """, (query, now, media_type or 'all'))
            # Trim if over cap
            count = conn.execute('SELECT COUNT(*) FROM history').fetchone()[0]
            if count and count > MAX_ENTRIES:
                excess = count - MAX_ENTRIES
                conn.execute("""
                    DELETE FROM history WHERE query IN (
                        SELECT query FROM history ORDER BY last_used ASC LIMIT ?
                    )
                """, (excess,))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.warn('SEARCH', 'history add failed: %s', exc)


def remove(query):
    """Drop a single entry."""
    query = (query or '').strip()
    if not query:
        return
    _ensure_db()
    if not _DB_READY:
        return
    try:
        conn = _connect()
        try:
            conn.execute('DELETE FROM history WHERE query=?', (query,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def clear_all():
    _ensure_db()
    if not _DB_READY:
        return
    try:
        conn = _connect()
        try:
            conn.execute('DELETE FROM history')
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def recent(limit=20, media_type=None):
    """Return list of {query, count, last_used} sorted by last_used DESC."""
    _ensure_db()
    if not _DB_READY:
        return []
    try:
        conn = _connect()
        try:
            if media_type and media_type != 'all':
                cur = conn.execute(
                    'SELECT query, count, last_used FROM history WHERE media_type IN (?, "all") ORDER BY last_used DESC LIMIT ?',
                    (media_type, int(limit or 20)),
                )
            else:
                cur = conn.execute(
                    'SELECT query, count, last_used FROM history ORDER BY last_used DESC LIMIT ?',
                    (int(limit or 20),),
                )
            return [{'query': r[0], 'count': int(r[1] or 0), 'last_used': int(r[2] or 0)} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def top(limit=10, media_type=None):
    """Return list sorted by count DESC (most searched)."""
    _ensure_db()
    if not _DB_READY:
        return []
    try:
        conn = _connect()
        try:
            if media_type and media_type != 'all':
                cur = conn.execute(
                    'SELECT query, count, last_used FROM history WHERE media_type IN (?, "all") ORDER BY count DESC LIMIT ?',
                    (media_type, int(limit or 10)),
                )
            else:
                cur = conn.execute(
                    'SELECT query, count, last_used FROM history ORDER BY count DESC LIMIT ?',
                    (int(limit or 10),),
                )
            return [{'query': r[0], 'count': int(r[1] or 0), 'last_used': int(r[2] or 0)} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []
