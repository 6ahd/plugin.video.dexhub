# -*- coding: utf-8 -*-
"""DexHub Library Index — SQLite-backed local mirror of Stremio catalogs.

Architectural shift from v3.9.26 → v3.9.27:
  Before: every folder open = N parallel HTTP requests to N federated
          Stremio catalogs. Even with SWR + prefetch + 600s cache, the
          first open of a folder is bounded by network latency (1-4s).
  After:  folder open = SQLite query (≈ 5ms). Catalogs are synced into
          a local index by a background worker. Sources only consulted
          at Play time (for streams).

This module is the LOW-LEVEL data layer. It knows nothing about how
catalogs are fetched (that's sync_engine.py) or how folders are
rendered (that's index_render.py). It just exposes a clean upsert/query
API over the SQLite database.

Schema invariants:
  - Items deduped globally by `id` (canonical imdb_id where possible,
    composite otherwise). Same movie from 3 catalogs = 1 row in `items`,
    3 rows in `item_sources`.
  - Sources tracked per (item, provider, catalog, bucket) so we know
    which catalog vouched for which item, and so we can resolve streams
    from those same sources at Play time.
  - FTS5 index on title/description/genres maintained by triggers — no
    manual sync needed. Unicode + diacritics-folded so "naruto" matches
    "Naruto" and "حُروب" matches "حروب".

Safe to call concurrently: SQLite WAL mode + a per-connection lock means
the sync engine writing in one thread doesn't block the read thread
serving the home folder.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

# ─── Schema ───────────────────────────────────────────────────────────
_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -8000;  -- ~8MB page cache
PRAGMA foreign_keys = ON;

-- ─── items: canonical metadata, deduped globally ───────────────────
CREATE TABLE IF NOT EXISTS items (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,            -- movie | series | anime
    title           TEXT NOT NULL,
    title_norm      TEXT NOT NULL,            -- lowercased for sort/search
    year            INTEGER,
    release_date    TEXT,                     -- ISO-8601 or empty
    added_date      INTEGER NOT NULL,         -- unix ts first inserted
    updated_date    INTEGER NOT NULL,         -- unix ts last upsert
    imdb_id         TEXT,
    tmdb_id         TEXT,
    poster          TEXT,
    background      TEXT,
    description     TEXT,
    rating          REAL,                     -- imdbRating from catalog
    genres          TEXT,                     -- JSON array
    runtime         INTEGER,                  -- minutes
    language        TEXT,
    meta_blob       TEXT NOT NULL             -- full meta JSON for render
);

CREATE INDEX IF NOT EXISTS idx_items_type_release ON items(type, release_date DESC);
CREATE INDEX IF NOT EXISTS idx_items_type_added   ON items(type, added_date   DESC);
CREATE INDEX IF NOT EXISTS idx_items_type_rating  ON items(type, rating       DESC);
CREATE INDEX IF NOT EXISTS idx_items_type_year    ON items(type, year         DESC);
CREATE INDEX IF NOT EXISTS idx_items_type_title   ON items(type, title_norm   ASC);
CREATE INDEX IF NOT EXISTS idx_items_imdb         ON items(imdb_id) WHERE imdb_id IS NOT NULL;

-- ─── item_sources: which (provider, catalog, bucket) gave us each item ─
CREATE TABLE IF NOT EXISTS item_sources (
    item_id      TEXT NOT NULL,
    provider_id  TEXT NOT NULL,
    catalog_id   TEXT NOT NULL,
    bucket       TEXT NOT NULL,
    priority     INTEGER NOT NULL DEFAULT 0,  -- position in catalog
    last_seen    INTEGER NOT NULL,
    PRIMARY KEY (item_id, provider_id, catalog_id, bucket),
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sources_bucket_seen
    ON item_sources(bucket, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_sources_bucket_prio
    ON item_sources(bucket, priority ASC);
CREATE INDEX IF NOT EXISTS idx_sources_item
    ON item_sources(item_id);

-- ─── catalog_sync: per-catalog sync state for diagnostics + scheduling ─
CREATE TABLE IF NOT EXISTS catalog_sync (
    provider_id    TEXT NOT NULL,
    catalog_id     TEXT NOT NULL,
    bucket         TEXT NOT NULL,
    last_sync      INTEGER,                   -- unix ts of last attempt
    item_count     INTEGER DEFAULT 0,         -- items synced last run
    sync_duration  REAL,                      -- seconds taken
    status         TEXT,                      -- ok / error / partial
    error_message  TEXT,
    PRIMARY KEY (provider_id, catalog_id, bucket)
);

CREATE INDEX IF NOT EXISTS idx_catalog_sync_last
    ON catalog_sync(last_sync ASC);

-- ─── FTS5 virtual table for instant search ─────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title,
    description,
    genres,
    content='items',
    content_rowid='rowid',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS items_fts_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, description, genres)
    VALUES (new.rowid, new.title,
            COALESCE(new.description,''), COALESCE(new.genres,''));
END;

CREATE TRIGGER IF NOT EXISTS items_fts_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, description, genres)
    VALUES('delete', old.rowid, old.title,
           COALESCE(old.description,''), COALESCE(old.genres,''));
END;

CREATE TRIGGER IF NOT EXISTS items_fts_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, description, genres)
    VALUES('delete', old.rowid, old.title,
           COALESCE(old.description,''), COALESCE(old.genres,''));
    INSERT INTO items_fts(rowid, title, description, genres)
    VALUES (new.rowid, new.title,
            COALESCE(new.description,''), COALESCE(new.genres,''));
END;
"""


# ─── Connection management ────────────────────────────────────────────
class IndexDB:
    """Thread-safe SQLite wrapper for the library index.

    Uses a single connection per thread (via threading.local). WAL mode
    lets readers and writers proceed concurrently without blocking.
    """

    def __init__(self, db_path):
        self._path = db_path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        # Initialize schema once at construction; subsequent connections
        # are no-ops because of `IF NOT EXISTS`.
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._connect_internal() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect_internal(self):
        """Make a fresh connection. Used at init only."""
        conn = sqlite3.connect(self._path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def conn(self):
        """Per-thread cached connection."""
        c = getattr(self._local, 'conn', None)
        if c is None:
            c = sqlite3.connect(self._path, timeout=10.0, isolation_level=None)
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    @contextmanager
    def write(self):
        """Serialize writers via Python-level lock on top of WAL.

        SQLite WAL itself allows one writer at a time, but the lock here
        avoids `SQLITE_BUSY` retries from concurrent sync jobs hitting
        the same table at once.
        """
        with self._write_lock:
            yield self.conn

    # ─── Item upsert ─────────────────────────────────────────────────
    def upsert_items_from_catalog(self, metas, provider_id, catalog_id, bucket, batch_size=200):
        """Bulk insert/update items from a Stremio catalog response.

        `metas` is a list of dicts in Stremio meta-preview format.
        Returns (items_inserted, items_updated, sources_added).
        """
        if not metas:
            return (0, 0, 0)

        now = int(time.time())
        item_rows = []
        source_rows = []

        for priority, meta in enumerate(metas):
            if not isinstance(meta, dict):
                continue
            item = _meta_to_item_row(meta, now)
            if not item:
                continue
            item_rows.append(item)
            source_rows.append((
                item['id'], provider_id, catalog_id, bucket, priority, now,
            ))

        if not item_rows:
            return (0, 0, 0)

        inserted = updated = 0
        sources_added = 0

        with self.write() as conn:
            cur = conn.cursor()
            cur.execute('BEGIN')
            try:
                for chunk_start in range(0, len(item_rows), batch_size):
                    chunk = item_rows[chunk_start:chunk_start + batch_size]
                    for row in chunk:
                        existing = cur.execute(
                            'SELECT id FROM items WHERE id = ?', (row['id'],)
                        ).fetchone()
                        if existing:
                            cur.execute("""
                                UPDATE items SET
                                    type=?, title=?, title_norm=?, year=?,
                                    release_date=?, updated_date=?, imdb_id=?,
                                    tmdb_id=?, poster=?, background=?,
                                    description=?, rating=?, genres=?,
                                    runtime=?, language=?, meta_blob=?
                                WHERE id=?
                            """, (
                                row['type'], row['title'], row['title_norm'],
                                row['year'], row['release_date'],
                                row['updated_date'], row['imdb_id'],
                                row['tmdb_id'], row['poster'], row['background'],
                                row['description'], row['rating'], row['genres'],
                                row['runtime'], row['language'], row['meta_blob'],
                                row['id'],
                            ))
                            updated += 1
                        else:
                            cur.execute("""
                                INSERT INTO items
                                  (id, type, title, title_norm, year,
                                   release_date, added_date, updated_date,
                                   imdb_id, tmdb_id, poster, background,
                                   description, rating, genres, runtime,
                                   language, meta_blob)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """, (
                                row['id'], row['type'], row['title'],
                                row['title_norm'], row['year'],
                                row['release_date'], row['added_date'],
                                row['updated_date'], row['imdb_id'],
                                row['tmdb_id'], row['poster'], row['background'],
                                row['description'], row['rating'], row['genres'],
                                row['runtime'], row['language'], row['meta_blob'],
                            ))
                            inserted += 1

                # Upsert sources in a single executemany — INSERT OR REPLACE
                # lets us refresh last_seen + priority cheaply.
                cur.executemany("""
                    INSERT OR REPLACE INTO item_sources
                      (item_id, provider_id, catalog_id, bucket, priority, last_seen)
                    VALUES (?,?,?,?,?,?)
                """, source_rows)
                sources_added = len(source_rows)

                cur.execute('COMMIT')
            except Exception:
                cur.execute('ROLLBACK')
                raise
        return (inserted, updated, sources_added)

    # ─── Catalog sync state ──────────────────────────────────────────
    def record_sync(self, provider_id, catalog_id, bucket, item_count,
                    duration, status='ok', error_message=None):
        with self.write() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO catalog_sync
                  (provider_id, catalog_id, bucket, last_sync, item_count,
                   sync_duration, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                provider_id, catalog_id, bucket, int(time.time()),
                int(item_count), float(duration), status, error_message,
            ))

    def get_sync_state(self, provider_id=None, catalog_id=None, bucket=None):
        """Return list of catalog_sync rows, optionally filtered."""
        sql = 'SELECT * FROM catalog_sync WHERE 1=1'
        args = []
        if provider_id:
            sql += ' AND provider_id = ?'; args.append(provider_id)
        if catalog_id:
            sql += ' AND catalog_id = ?';  args.append(catalog_id)
        if bucket:
            sql += ' AND bucket = ?';      args.append(bucket)
        sql += ' ORDER BY last_sync DESC'
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ─── Query API for the read path ─────────────────────────────────
    def query_bucket(self, bucket, sort='release_date', media_type=None,
                     genre=None, year=None, language=None,
                     limit=80, offset=0):
        """Return items in a bucket, applying SQL-side sort + filters.

        sort options (matches what most catalogs offer):
            'release_date'  newest released first
            'added_date'    newest INDEXED first ("Last Added")
            'rating'        highest rated first
            'title'         alphabetical
            'year'          newest year first

        v3.9.45: each returned row now also carries best_provider_id and
        best_catalog_id, identifying the highest-priority source that
        produced this item within the requested bucket. Previously the
        renderer received only items with no attribution, and the play
        path defaulted to "first installed provider" — which broke the
        "play from same source" feature in v3.9.40 by sending the user's
        click to a provider that did not actually have the item. The
        attribution flows through to the rendered meta as
        `_dexhub_provider_id` and `_dexhub_catalog_id` for the play path.
        """
        sort_col, sort_dir = _normalize_sort(sort)

        where = ['1=1']
        args = [bucket]   # parameter for the inner subquery

        if media_type:
            where.append('i.type = ?'); args.append(media_type)
        if genre:
            where.append("i.genres LIKE ?"); args.append('%"' + genre + '"%')
        if year:
            try:
                where.append('i.year = ?'); args.append(int(year))
            except (TypeError, ValueError):
                pass
        if language:
            where.append('i.language = ?'); args.append(language)

        where_sql = ' AND '.join(where)
        sql = """
            SELECT i.*,
                   src.provider_id AS best_provider_id,
                   src.catalog_id  AS best_catalog_id,
                   src.priority    AS best_priority
              FROM items i
              JOIN (
                  SELECT item_id, provider_id, catalog_id, priority,
                         ROW_NUMBER() OVER (
                             PARTITION BY item_id
                             ORDER BY priority ASC, last_seen DESC
                         ) AS rn
                    FROM item_sources
                   WHERE bucket = ?
              ) src ON src.item_id = i.id AND src.rn = 1
             WHERE {where}
          ORDER BY {sort_col} {sort_dir}
             LIMIT ? OFFSET ?
        """.format(where=where_sql, sort_col=sort_col, sort_dir=sort_dir)
        args.extend([int(limit), int(offset)])
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def count_bucket(self, bucket, media_type=None):
        sql = """
            SELECT COUNT(DISTINCT i.id) AS n
              FROM items i
              JOIN item_sources s ON s.item_id = i.id
             WHERE s.bucket = ?
        """
        args = [bucket]
        if media_type:
            sql += ' AND i.type = ?'; args.append(media_type)
        row = self.conn.execute(sql, args).fetchone()
        return int(row['n']) if row else 0

    # ─── Search ──────────────────────────────────────────────────────
    def search(self, query, media_type=None, limit=80):
        """FTS5 full-text search across title/description/genres.

        Returns items ordered by FTS relevance (bm25). Falls back to a
        title LIKE search if the FTS query is degenerate (empty after
        sanitisation, or all-stopwords).
        """
        if not query or not query.strip():
            return []
        # FTS5 accepts column filters with `column:term`. We pass user
        # text raw but escape double-quotes so a stray `"` doesn't break
        # parsing.
        cleaned = query.strip().replace('"', '""')
        # Use prefix search — "matr" matches "matrix".
        fts_query = ' '.join(t + '*' for t in cleaned.split() if t)
        if not fts_query:
            return []

        sql = """
            SELECT i.*, bm25(items_fts) AS score
              FROM items_fts
              JOIN items i ON i.rowid = items_fts.rowid
             WHERE items_fts MATCH ?
        """
        args = [fts_query]
        if media_type:
            sql += ' AND i.type = ?'; args.append(media_type)
        sql += ' ORDER BY score LIMIT ?'
        args.append(int(limit))

        try:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]
        except sqlite3.OperationalError:
            # FTS5 may reject pathological queries. Fall back to LIKE.
            return self._search_like(query, media_type, limit)

    def _search_like(self, query, media_type, limit):
        like = '%' + query.lower() + '%'
        sql = """
            SELECT * FROM items
             WHERE title_norm LIKE ?
        """
        args = [like]
        if media_type:
            sql += ' AND type = ?'; args.append(media_type)
        sql += ' ORDER BY rating DESC LIMIT ?'; args.append(int(limit))
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    # ─── Diagnostics ─────────────────────────────────────────────────
    def stats(self):
        """Return high-level stats for the status screen."""
        n_items = self.conn.execute('SELECT COUNT(*) AS n FROM items').fetchone()['n']
        n_sources = self.conn.execute('SELECT COUNT(*) AS n FROM item_sources').fetchone()['n']
        n_catalogs = self.conn.execute(
            'SELECT COUNT(*) AS n FROM catalog_sync'
        ).fetchone()['n']
        last_sync = self.conn.execute(
            'SELECT MAX(last_sync) AS t FROM catalog_sync'
        ).fetchone()['t']
        size_bytes = os.path.getsize(self._path) if os.path.exists(self._path) else 0
        return {
            'items': int(n_items or 0),
            'sources': int(n_sources or 0),
            'catalogs': int(n_catalogs or 0),
            'last_sync': int(last_sync or 0),
            'db_size': size_bytes,
        }

    # ─── Maintenance ─────────────────────────────────────────────────
    def prune_stale_sources(self, older_than_seconds):
        """Drop source rows not seen recently (catalog likely removed)."""
        cutoff = int(time.time()) - int(older_than_seconds)
        with self.write() as conn:
            conn.execute('DELETE FROM item_sources WHERE last_seen < ?', (cutoff,))
            # Drop orphan items that have no remaining source.
            conn.execute("""
                DELETE FROM items
                 WHERE id NOT IN (SELECT DISTINCT item_id FROM item_sources)
            """)

    def purge_provider(self, provider_id):
        """v3.9.45: drop ALL index data attributed to one provider.
        Called from the store when a provider is removed, so the
        provider's items no longer surface in the Hybrid renderer.
        Without this, removed providers' items remained visible from
        the index until the next sync swept them via prune_stale_sources,
        which could be hours away."""
        provider_id = str(provider_id or '').strip()
        if not provider_id:
            return
        with self.write() as conn:
            conn.execute('DELETE FROM item_sources WHERE provider_id = ?', (provider_id,))
            conn.execute('DELETE FROM catalog_sync  WHERE provider_id = ?', (provider_id,))
            conn.execute("""
                DELETE FROM items
                 WHERE id NOT IN (SELECT DISTINCT item_id FROM item_sources)
            """)

    def purge_catalog(self, provider_id, catalog_id, bucket=None):
        """v3.9.45: drop index data for one (provider, catalog) pair.
        Used when a single catalog pin is removed from a bucket. If
        the bucket is specified the deletion is scoped to that bucket,
        otherwise it covers every bucket the pair appears in."""
        provider_id = str(provider_id or '').strip()
        catalog_id  = str(catalog_id  or '').strip()
        if not provider_id or not catalog_id:
            return
        with self.write() as conn:
            if bucket:
                conn.execute(
                    'DELETE FROM item_sources WHERE provider_id = ? AND catalog_id = ? AND bucket = ?',
                    (provider_id, catalog_id, str(bucket).strip()))
                conn.execute(
                    'DELETE FROM catalog_sync  WHERE provider_id = ? AND catalog_id = ? AND bucket = ?',
                    (provider_id, catalog_id, str(bucket).strip()))
            else:
                conn.execute('DELETE FROM item_sources WHERE provider_id = ? AND catalog_id = ?',
                             (provider_id, catalog_id))
                conn.execute('DELETE FROM catalog_sync  WHERE provider_id = ? AND catalog_id = ?',
                             (provider_id, catalog_id))
            conn.execute("""
                DELETE FROM items
                 WHERE id NOT IN (SELECT DISTINCT item_id FROM item_sources)
            """)

    def vacuum(self):
        """Compact the DB file. Safe to call periodically (cheap on WAL)."""
        try:
            self.conn.execute('VACUUM')
        except Exception:
            pass

    def clear_all(self):
        """Nuke everything. Used by 'Reset library' action."""
        with self.write() as conn:
            for tbl in ('item_sources', 'items', 'catalog_sync'):
                conn.execute('DELETE FROM ' + tbl)
        self.vacuum()


# ─── Internal helpers ─────────────────────────────────────────────────
def _normalize_sort(sort):
    mapping = {
        'release_date': ('i.release_date',           'DESC'),
        'release':      ('i.release_date',           'DESC'),
        'added_date':   ('s.last_seen',              'DESC'),  # 'Last added'
        'last_added':   ('s.last_seen',              'DESC'),
        'rating':       ('i.rating',                 'DESC'),
        'popular':      ('i.rating',                 'DESC'),
        'title':        ('i.title_norm',             'ASC'),
        'name':         ('i.title_norm',             'ASC'),
        'a_z':          ('i.title_norm',             'ASC'),
        'year':         ('i.year',                   'DESC'),
        'priority':     ('best_priority',            'ASC'),
    }
    return mapping.get((sort or '').lower(), mapping['release_date'])


def _meta_to_item_row(meta, now):
    """Project a Stremio meta-preview dict into our items schema row."""
    raw_id = (meta.get('imdb_id') or meta.get('id') or '').strip()
    if not raw_id:
        return None
    # Strip episode/season suffixes off series ids ('tt123:1:1' → 'tt123')
    canonical = raw_id.split(':', 1)[0] if raw_id.startswith('tt') else raw_id

    title = (meta.get('name') or meta.get('title') or '').strip()
    if not title:
        return None

    media_type = (meta.get('type') or 'movie').strip()
    year = _safe_int(meta.get('year') or meta.get('releaseInfo'))
    if year is None and meta.get('releaseInfo'):
        # releaseInfo may be like "2023-2024" — take first 4 chars
        try:
            year = int(str(meta['releaseInfo'])[:4])
        except (ValueError, TypeError):
            year = None

    genres = meta.get('genres') or meta.get('genre') or []
    if isinstance(genres, str):
        genres = [g.strip() for g in genres.split(',') if g.strip()]
    if not isinstance(genres, list):
        genres = []

    rating = _safe_float(meta.get('imdbRating') or meta.get('rating'))
    runtime = _safe_int_minutes(meta.get('runtime'))

    return {
        'id':           canonical,
        'type':         media_type,
        'title':        title,
        'title_norm':   title.lower(),
        'year':         year,
        'release_date': str(meta.get('released') or meta.get('release_date') or '')[:10],
        'added_date':   now,
        'updated_date': now,
        'imdb_id':      canonical if canonical.startswith('tt') else None,
        'tmdb_id':      str(meta.get('tmdb_id') or '') or None,
        'poster':       str(meta.get('poster') or ''),
        'background':   str(meta.get('background') or meta.get('fanart') or ''),
        'description':  str(meta.get('description') or meta.get('plot') or ''),
        'rating':       rating,
        'genres':       json.dumps(genres, ensure_ascii=False),
        'runtime':      runtime,
        'language':     str(meta.get('language') or '')[:8] or None,
        'meta_blob':    json.dumps(meta, ensure_ascii=False),
    }


def _safe_int(v):
    if v is None:
        return None
    try:
        return int(str(v).split('-', 1)[0])
    except (ValueError, TypeError):
        return None


def _safe_float(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int_minutes(v):
    """Stremio runtime may be '120 min', '2h 5min', integer minutes, etc."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).lower()
    # First try plain int
    try:
        return int(s.strip())
    except ValueError:
        pass
    # Parse '120 min'
    total = 0
    import re
    h = re.search(r'(\d+)\s*h', s)
    m = re.search(r'(\d+)\s*m', s)
    if h:
        total += int(h.group(1)) * 60
    if m:
        total += int(m.group(1))
    return total or None
