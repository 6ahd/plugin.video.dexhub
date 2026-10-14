# -*- coding: utf-8 -*-
"""DexHub Index Render — folder rendering from the local index.

When the user opens a home folder in Fast Index Mode, this module is
called INSTEAD OF the live aggregator (_hub_section_direct_content).
It does NOT make any HTTP requests — only a SQLite query.

Output shape matches what the live aggregator produced: a list of meta
dicts that the existing rendering code (_hub_render_media_rows) can
consume unchanged. This is intentional: it lets us swap in the index
behind a setting without rewriting the rendering loop.

When the index is empty for a bucket (first run, or just-pinned
catalogs), we return `None` so the caller can fall back to the live
aggregator. This guarantees the user never sees an empty folder just
because the index hasn't synced yet.
"""
from __future__ import annotations

import json

import xbmc

from .dexhub import index as index_mod


# Module-level singleton — opened lazily on first call.
_DB = None
_DB_LOCK = None


def _get_db():
    global _DB, _DB_LOCK
    if _DB is None:
        import threading
        if _DB_LOCK is None:
            _DB_LOCK = threading.Lock()
        with _DB_LOCK:
            if _DB is None:
                from .dexhub.common import profile_path
                import os
                db_path = os.path.join(profile_path(), 'library.sqlite3')
                _DB = index_mod.IndexDB(db_path)
    return _DB


def get_db():
    """Public accessor for the IndexDB. Used by sync_engine + diagnostics."""
    return _get_db()


def query_bucket_metas(bucket, sort='release_date', media_type=None,
                       genre=None, year=None, language=None,
                       limit=80, offset=0):
    """Return a list of Stremio-shaped meta dicts for the given bucket.

    Returns None when the index has no data for this bucket — the caller
    is expected to fall back to the live aggregator in that case.
    """
    try:
        db = _get_db()
        rows = db.query_bucket(
            bucket, sort=sort, media_type=media_type,
            genre=genre, year=year, language=language,
            limit=limit, offset=offset,
        )
    except Exception as exc:
        xbmc.log('[DexHub] index query failed: %s' % exc, xbmc.LOGWARNING)
        return None

    if not rows:
        return None

    metas = []
    for row in rows:
        try:
            meta = json.loads(row['meta_blob']) if row['meta_blob'] else {}
        except Exception:
            meta = {}
        # Ensure the basic fields are always present even if the blob is
        # corrupt — the renderer expects id/name/poster etc.
        meta.setdefault('id', row['id'])
        meta.setdefault('imdb_id', row.get('imdb_id') or row['id'])
        meta.setdefault('type', row['type'])
        meta.setdefault('name', row['title'])
        if row.get('poster') and not meta.get('poster'):
            meta['poster'] = row['poster']
        if row.get('background') and not meta.get('background'):
            meta['background'] = row['background']
        if row.get('description') and not meta.get('description'):
            meta['description'] = row['description']
        if row.get('rating') is not None and not meta.get('imdbRating'):
            meta['imdbRating'] = row['rating']
        if row.get('year') and not meta.get('year'):
            meta['year'] = row['year']
        # v3.9.45: propagate the real source attribution from the index
        # query to the renderer. The play path reads these to send the
        # user's click to the provider that actually has this item,
        # rather than defaulting to "first installed provider" as it
        # did before. Falls back gracefully to empty strings when the
        # query couldn't determine attribution (very rare; happens only
        # if item_sources rows were orphaned).
        meta['_dexhub_indexed'] = True
        meta['_dexhub_provider_id'] = row.get('best_provider_id') or ''
        meta['_dexhub_catalog_id']  = row.get('best_catalog_id')  or ''
        metas.append(meta)
    return metas


def search_metas(query, media_type=None, limit=80):
    """FTS5 search over the index. Empty result = caller may fall back
    to live search to catch items not yet indexed."""
    if not query or not query.strip():
        return []
    try:
        db = _get_db()
        rows = db.search(query, media_type=media_type, limit=limit)
    except Exception as exc:
        xbmc.log('[DexHub] index search failed: %s' % exc, xbmc.LOGWARNING)
        return []
    metas = []
    for row in rows:
        try:
            meta = json.loads(row['meta_blob']) if row['meta_blob'] else {}
        except Exception:
            meta = {}
        meta.setdefault('id', row['id'])
        meta.setdefault('imdb_id', row.get('imdb_id') or row['id'])
        meta.setdefault('type', row['type'])
        meta.setdefault('name', row['title'])
        if row.get('poster') and not meta.get('poster'):
            meta['poster'] = row['poster']
        meta['_dexhub_indexed'] = True
        metas.append(meta)
    return metas


def bucket_has_data(bucket):
    """Cheap check used by the dispatcher to know if the index is
    populated for a bucket before falling back."""
    try:
        return _get_db().count_bucket(bucket) > 0
    except Exception:
        return False


def stats():
    """Diagnostics — used by the status screen and the welcome wizard."""
    try:
        return _get_db().stats()
    except Exception:
        return {'items': 0, 'sources': 0, 'catalogs': 0,
                'last_sync': 0, 'db_size': 0}


