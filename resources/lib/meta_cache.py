# -*- coding: utf-8 -*-
"""SQLite metadata cache, POV-style.

Replaces the per-request HTTP cache for catalog rows. Every meta dict the
addon resolves for a movie/series/episode goes into this DB keyed by
(media_type, canonical_id). Subsequent renders read from disk in <5ms
instead of round-tripping to the Stremio addon.

Smart TTL:
  * Old movie (released > 2 years ago) → 180 days (rarely changes)
  * Recent movie (within 2 years) → 7 days (rating + tagline updates)
  * Finished show (status='Ended'/'Canceled') → 180 days
  * Ongoing show with known next-air date → expires AT that air date
  * Ongoing show without next-air info → 4 days

A two-tier read path matches POV's design:
  1. In-memory dict (per Python invoker, cleared between processes)
  2. SQLite disk fallback (survives Kodi restarts)

Both are written together. SQLite uses WAL + NORMAL synchronous for fast
writes that don't block reads.
"""
import json
import os
import sqlite3
import time
import threading

from .log import log
from .dexhub.common import profile_path

DB_PATH = os.path.join(profile_path(), 'meta_cache.db')

# Per-process LRU. Bounded to avoid unbounded growth under
# reuselanguageinvoker. Eviction is naive (oldest insertion order).
_MEM = {}
_MEM_MAX = 800
_MEM_LOCK = threading.Lock()

_DB_LOCK = threading.Lock()
_DB_READY = False

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    media_type TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT 'auto',
    payload TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (media_type, canonical_id, source_id)
)
"""

CREATE_IDX_SQL = "CREATE INDEX IF NOT EXISTS idx_meta_expires ON meta(expires_at)"


def _connect():
    # v5.10.117: the file stays open (dbkeep): a close is no disk sync
    from .dexhub import dbkeep
    return dbkeep.connect(DB_PATH, timeout=2.0)


def _ensure_db():
    global _DB_READY
    if _DB_READY:
        return
    with _DB_LOCK:
        if _DB_READY:
            return
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        except Exception:
            pass
        try:
            conn = _connect()
            try:
                conn.execute(CREATE_SQL)
                conn.execute(CREATE_IDX_SQL)
                conn.commit()
            finally:
                conn.close()
            _DB_READY = True
        except Exception as exc:
            log.error('META', 'meta_cache init failed: %s', exc, exc_info=True)


def _mem_put(key, payload, expires_at):
    with _MEM_LOCK:
        _MEM[key] = (expires_at, payload)
        # Cheap LRU eviction: when over capacity, drop ~10% of oldest entries.
        if len(_MEM) > _MEM_MAX:
            try:
                victims = list(_MEM.keys())[:int(_MEM_MAX * 0.1)]
                for k in victims:
                    _MEM.pop(k, None)
            except Exception:
                pass


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


def _key(media_type, canonical_id, source_id='auto'):
    return '%s|%s|%s' % (
        (media_type or '').lower(),
        canonical_id or '',
        source_id or 'auto',
    )


def _smart_expiry(media_type, payload, now):
    """Compute an expiration timestamp tailored to the type of content.

    Returns absolute epoch seconds (NOT a delta).
    """
    DAY = 86400
    media_type = (media_type or '').lower()
    is_series = media_type in ('series', 'show', 'tv', 'tvshow', 'anime')

    if is_series:
        # Try to expire at the next-air-date so the meta refreshes once a
        # new episode actually airs. Otherwise treat by status.
        try:
            status = str((payload.get('status') or '')).strip().lower()
            if status in ('ended', 'canceled', 'cancelled'):
                return now + 180 * DAY
            # Cinemeta / Trakt sometimes provide last_air_date or
            # next_episode_to_air; we expire to whichever is sooner.
            extra = payload.get('extra_info') or {}
            for key in ('next_episode_to_air', 'next_air', 'next_aired', 'next_episode_air'):
                v = extra.get(key) if isinstance(extra, dict) else None
                if not v and key in payload:
                    v = payload.get(key)
                if v:
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(str(v)[:10], '%Y-%m-%d')
                        return max(now + DAY, int(dt.timestamp()) + DAY)
                    except Exception:
                        continue
        except Exception:
            pass
        return now + 4 * DAY  # default for unknown ongoing series

    # Movies
    try:
        year = int(payload.get('year') or 0)
        if year and (time.gmtime(now).tm_year - year) >= 2:
            return now + 180 * DAY  # truly old movie
    except Exception:
        pass
    return now + 7 * DAY  # recent movie


def get(media_type, canonical_id, source_id='auto'):
    """Fetch a cached meta dict, or None if not found / expired.

    Always tries memory first, then SQLite. Population is automatic via put().
    """
    if not (media_type and canonical_id):
        return None
    now = int(time.time())
    key = _key(media_type, canonical_id, source_id)
    mem_hit = _mem_get(key, now)
    if mem_hit is not None:
        return dict(mem_hit)
    _ensure_db()
    if not _DB_READY:
        return None
    try:
        conn = _connect()
        try:
            row = conn.execute(
                'SELECT payload, expires_at FROM meta WHERE media_type=? AND canonical_id=? AND source_id=?',
                (media_type.lower(), canonical_id, source_id),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        if row[1] < now:
            # The service already purges expired rows in one batch. Spawning a
            # thread per stale hit caused thread storms on large catalog pages.
            return None
        try:
            payload = json.loads(row[0])
        except Exception:
            return None
        _mem_put(key, payload, row[1])
        return dict(payload)
    except Exception as exc:
        log.warn('META', 'meta_cache get(%s) failed: %s', key, exc)
        return None


def get_many(media_type, canonical_ids, source_id='auto'):
    """Batch counterpart of get() — fetch many ids in a single SQLite query.

    Returns ``{canonical_id: payload, ...}``. Misses are simply absent from
    the dict; callers do their own miss handling.

    v3.9.17: introduced to fix a hot-loop perf issue where rendering an
    80-item catalog meant 80 separate SQLite connection-open / PRAGMA / SELECT
    / close cycles. With get_many() it becomes one connection, one SELECT
    using ``WHERE canonical_id IN (...)``, and the memory cache is populated
    in bulk for downstream renders.

    SQLite's default parameter limit is 999, so very large id lists are
    chunked transparently.
    """
    if not (media_type and canonical_ids):
        return {}

    now = int(time.time())
    media_type_lower = media_type.lower()
    out = {}

    # Memory cache: O(1) per id, no I/O. Populates `out` for hits, accumulates
    # misses for the SQLite path.
    misses = []
    seen = set()
    for cid in canonical_ids:
        if not cid or cid in seen:
            continue
        seen.add(cid)
        key = _key(media_type_lower, cid, source_id)
        mem_hit = _mem_get(key, now)
        if mem_hit is not None:
            out[cid] = dict(mem_hit)
        else:
            misses.append(cid)

    if not misses:
        return out

    _ensure_db()
    if not _DB_READY:
        return out

    try:
        conn = _connect()
        try:
            # Chunk IN-clause so we never hit the 999-parameter SQLite limit.
            CHUNK = 800
            for start in range(0, len(misses), CHUNK):
                chunk = misses[start:start + CHUNK]
                placeholders = ','.join('?' * len(chunk))
                rows = conn.execute(
                    'SELECT canonical_id, payload, expires_at '
                    'FROM meta '
                    'WHERE media_type=? AND source_id=? AND canonical_id IN (%s)' % placeholders,
                    (media_type_lower, source_id) + tuple(chunk),
                ).fetchall()
                for cid, payload_blob, expires_at in rows:
                    if expires_at < now:
                        continue
                    try:
                        payload = json.loads(payload_blob)
                    except Exception:
                        continue
                    _mem_put(_key(media_type_lower, cid, source_id), payload, expires_at)
                    out[cid] = dict(payload)
        finally:
            conn.close()

        return out
    except Exception as exc:
        log.warn('META', 'meta_cache get_many(n=%d) failed: %s', len(misses), exc)
        return out


def put(media_type, canonical_id, payload, source_id='auto', expires_at=None):
    """Store a meta dict. expires_at is computed automatically when not given."""
    if not (media_type and canonical_id and isinstance(payload, dict)):
        return False
    now = int(time.time())
    if expires_at is None:
        expires_at = _smart_expiry(media_type, payload, now)
    key = _key(media_type, canonical_id, source_id)
    _mem_put(key, payload, expires_at)
    _ensure_db()
    if not _DB_READY:
        return False
    try:
        conn = _connect()
        try:
            conn.execute(
                'INSERT OR REPLACE INTO meta (media_type, canonical_id, source_id, payload, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)',
                (media_type.lower(), canonical_id, source_id,
                 json.dumps(payload, ensure_ascii=False), now, expires_at),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        log.warn('META', 'meta_cache put(%s) failed: %s', key, exc)
        return False



def purge_expired():
    """Drop expired rows. Call periodically from the service."""
    _ensure_db()
    if not _DB_READY:
        return 0
    now = int(time.time())
    try:
        conn = _connect()
        try:
            cur = conn.execute('DELETE FROM meta WHERE expires_at < ?', (now,))
            deleted = cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return deleted
    except Exception as exc:
        log.warn('META', 'meta_cache purge failed: %s', exc)
        return 0


def clear_all():
    """Wipe both the disk DB and the in-memory cache."""
    with _MEM_LOCK:
        _MEM.clear()
    _ensure_db()
    if not _DB_READY:
        return False
    try:
        conn = _connect()
        try:
            conn.execute('DELETE FROM meta')
            # v5.10.80: DELETE opens an implicit transaction and VACUUM cannot
            # run inside one, so this raised, the DELETE was rolled back on
            # close, and "clear metadata cache" never cleared the disk cache.
            conn.commit()
            conn.execute('VACUUM')
        finally:
            conn.close()
        return True
    except Exception as exc:
        log.warn('META', 'meta_cache clear failed: %s', exc)
        return False


def stats():
    """Return {entries, db_bytes} for diagnostics."""
    _ensure_db()
    if not _DB_READY:
        return {'entries': 0, 'db_bytes': 0, 'mem_entries': len(_MEM)}
    try:
        conn = _connect()
        try:
            entries = conn.execute('SELECT COUNT(*) FROM meta').fetchone()[0]
        finally:
            conn.close()
        db_bytes = 0
        try:
            db_bytes = os.path.getsize(DB_PATH)
        except Exception:
            pass
        return {'entries': int(entries or 0),
                'db_bytes': int(db_bytes),
                'mem_entries': len(_MEM)}
    except Exception:
        return {'entries': 0, 'db_bytes': 0, 'mem_entries': len(_MEM)}
