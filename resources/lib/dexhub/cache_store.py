# -*- coding: utf-8 -*-
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid

from .common import profile_path

DB_PATH = os.path.join(profile_path(), 'cache.db')

_MEM = {}
_MEM_ORDER = []
_MEM_MAX = 256
_LOCK = threading.RLock()
_DB_READY = False
# v4.8.6: on Android the addon-data directory can be read-only (external
# storage permissions, a profile restored from backup, a DB left owned by
# another install). SQLite then raises "attempt to write a readonly
# database" — and that used to escape into the source-scan worker threads,
# killing one per stream row: the crash, the stutter and the failed
# playback in the user's log all traced back here. When the disk refuses
# writes we say so ONCE and run entirely from the in-memory cache, which is
# what every read already consults first.
_DISK_DISABLED = False

# v5.10.117: one connection for the life of the interpreter (every use holds
# _LOCK). A connection per call meant four disk syncs per cached item: the
# close of the last connection to a WAL database checkpoints it. The source
# list writes one item per stream, a catalog page one per title with a
# studio, Continue Watching two per title: seconds of eMMC syncs per page.
_CONN = [None]
# the age prune runs once in a while, not with every item
_PRUNE_EVERY = 300.0
_PRUNED = [0.0]
# an item written here lately (same key, same content) is not written again
_WRITTEN = {}
_REWRITE_AFTER = 1800.0


def _disable_disk(exc):
    global _DISK_DISABLED
    _drop_conn()
    if _DISK_DISABLED:
        return
    _DISK_DISABLED = True
    try:
        import xbmc
        xbmc.log('[DexHub] cache store: disk cache disabled for this session '
                 '(%s) — running from memory' % exc, xbmc.LOGWARNING)
    except Exception:
        pass


def _mem_put(kind, cache_key, payload):
    key = '%s:%s' % (kind or '', cache_key or '')
    with _LOCK:
        if key not in _MEM:
            _MEM_ORDER.append(key)
        _MEM[key] = payload
        while len(_MEM_ORDER) > _MEM_MAX:
            old = _MEM_ORDER.pop(0)
            _MEM.pop(old, None)


def _mem_get(kind, cache_key):
    with _LOCK:
        return _MEM.get('%s:%s' % (kind or '', cache_key or ''))


def _drop_conn():
    conn, _CONN[0] = _CONN[0], None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _conn():
    """The store's connection (callers hold _LOCK)."""
    global _DB_READY
    conn = _CONN[0]
    if conn is not None:
        return conn
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    except Exception:
        pass
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    try:
        conn.execute('PRAGMA journal_mode=WAL').fetchall()
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA busy_timeout=10000')
        conn.execute('PRAGMA temp_store=MEMORY')
    except Exception:
        pass
    if not _DB_READY:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_items (
                cache_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_kind_created ON cache_items(kind, created_at DESC)")
        # Every put prunes by age alone. The kind-first index cannot serve
        # that query, causing a full cache scan for every result/UI seed.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_created ON cache_items(created_at)")
        conn.commit()
        _DB_READY = True
    _CONN[0] = conn
    return conn


def _prune(conn, cutoff):
    now = time.time()
    if now - _PRUNED[0] < _PRUNE_EVERY:
        return
    _PRUNED[0] = now
    # A last successful copy is a playback choice, beyond the short-lived
    # stream/catalog cache. Signed URL freshness is checked by the player;
    # native file identity still helps after a day or a restart.
    conn.execute("DELETE FROM cache_items WHERE created_at < ? AND kind != 'last_played'", (cutoff,))
    conn.execute("DELETE FROM cache_items WHERE kind = 'last_played' AND created_at < ?",
                 (int(now) - 90 * 86400,))
    for key, at in list(_WRITTEN.items()):
        if now - at > _REWRITE_AFTER:
            _WRITTEN.pop(key, None)


def put(kind, payload, ttl_hours=24):
    """Store a payload and return its key.

    The key is generated here and the in-memory copy is always written, so
    a disk failure can never cost the caller its handle to the data — the
    row still plays, it just is not remembered across restarts.

    v5.10.117: the key follows the content (kind and payload), so a page
    listed again carries the same keys in its addresses and writes nothing
    new.
    """
    now = int(time.time())
    cutoff = now - int(ttl_hours * 3600)
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha1(('%s\0%s' % (kind, encoded)).encode('utf-8')).hexdigest()[:32]
    except Exception:
        encoded, key = None, uuid.uuid4().hex
    with _LOCK:
        _mem_put(kind, key, payload)          # first: this must never fail
        if _DISK_DISABLED or encoded is None:
            return key
        if now - _WRITTEN.get(key, 0) < _REWRITE_AFTER:
            return key
        try:
            conn = _conn()
            conn.execute(
                "INSERT OR REPLACE INTO cache_items(cache_key, kind, payload, created_at) VALUES(?,?,?,?)",
                (key, kind, encoded, now),
            )
            _prune(conn, cutoff)
            conn.commit()
            _WRITTEN[key] = now
        except sqlite3.Error as exc:
            # readonly / locked / disk-full / corrupt — all the same answer
            _disable_disk(exc)
        except Exception as exc:
            _disable_disk(exc)
    return key


def put_many(kind, payloads, ttl_hours=24):
    """Store several payloads under the caller's own keys in one transaction
    (v5.10.109: skin.dexhub's rows keep one key per title's art, so a row read
    again writes the same keys and one commit, not one per title)."""
    payloads = dict(payloads or {})
    if not payloads:
        return
    now = int(time.time())
    cutoff = now - int(ttl_hours * 3600)
    with _LOCK:
        for key, payload in payloads.items():
            _mem_put(kind, key, payload)
        if _DISK_DISABLED:
            return
        try:
            rows = [(key, kind, json.dumps(payload, ensure_ascii=False), now) for key, payload in payloads.items()]
            conn = _conn()
            conn.executemany(
                "INSERT OR REPLACE INTO cache_items(cache_key, kind, payload, created_at) VALUES(?,?,?,?)", rows)
            _prune(conn, cutoff)
            conn.commit()
        except sqlite3.Error as exc:
            _disable_disk(exc)
        except Exception as exc:
            _disable_disk(exc)


def get(kind, cache_key):
    cached = _mem_get(kind, cache_key)
    if cached is not None:
        return cached
    if _DISK_DISABLED:
        return None
    row = None
    with _LOCK:
        try:
            conn = _conn()
            row = conn.execute("SELECT payload FROM cache_items WHERE kind=? AND cache_key=?", (kind, cache_key)).fetchone()
        except Exception as exc:
            # a DB that cannot even be opened must not break a lookup
            _disable_disk(exc)
    if not row:
        return None
    try:
        payload = json.loads(row[0])
        _mem_put(kind, cache_key, payload)
        return payload
    except Exception:
        return None


def update(kind, cache_key, payload):
    if not cache_key:
        return None
    now = int(time.time())
    with _LOCK:
        _mem_put(kind, cache_key, payload)     # memory first, always
        _WRITTEN.pop(cache_key, None)
        if _DISK_DISABLED:
            return cache_key
        try:
            encoded = json.dumps(payload, ensure_ascii=False)
            conn = _conn()
            conn.execute(
                "INSERT OR REPLACE INTO cache_items(cache_key, kind, payload, created_at) VALUES(?,?,?,?)",
                (cache_key, kind, encoded, now),
            )
            conn.commit()
        except Exception as exc:
            _disable_disk(exc)
    return cache_key


def clear_all(kind=None):
    with _LOCK:
        _MEM.clear()
        del _MEM_ORDER[:]
        _WRITTEN.clear()
        if _DISK_DISABLED:
            return
        try:
            conn = _conn()
            if kind:
                conn.execute("DELETE FROM cache_items WHERE kind=?", (kind,))
            else:
                conn.execute("DELETE FROM cache_items")
            conn.commit()
        except Exception as exc:
            _disable_disk(exc)
