# -*- coding: utf-8 -*-
"""One idle connection held open to each of Dex Hub's SQLite files.

v5.10.117. Every store here opens a connection for a call and closes it.
Closing the LAST connection to a WAL database checkpoints it, syncs the disk
and deletes the WAL file: strace counted 408 disk syncs for 100 cached
items, 13 with one connection held open. On a box's eMMC a sync costs 5 to
50 ms, more while Kodi writes its own databases, and a Continue Watching
row wrote two cache rows per title: the 2 to 17 seconds kodi.log measured
for that row on a Ugoos. With one connection held for the life of the
interpreter, every other close is a plain close.

The held connection runs no statement after its first read, so it never
keeps a snapshot open and the WAL still checkpoints as it fills.
"""
import sqlite3
import threading

_KEPT = {}
_LOCK = threading.Lock()


def keep(path):
    """Hold one idle connection to ``path`` (once per interpreter)."""
    if not path or path in _KEPT:
        return
    with _LOCK:
        if path in _KEPT:
            return
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=1.0, check_same_thread=False)
            # a read opens the WAL index: from then on this connection
            # counts as open and no other close is the last one
            conn.execute('PRAGMA schema_version').fetchall()
        except Exception:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
        _KEPT[path] = conn


def connect(path, timeout=10.0, wal=True):
    """A connection to ``path`` with Dex Hub's pragmas, the file held open."""
    conn = sqlite3.connect(path, timeout=timeout)
    if wal:
        try:
            conn.execute('PRAGMA journal_mode=WAL').fetchall()
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA temp_store=MEMORY')
        except Exception:
            pass
    keep(path)
    return conn
