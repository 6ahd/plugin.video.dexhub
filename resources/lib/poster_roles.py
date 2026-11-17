"""Keep known landscape artwork out of portrait slots, using Kodi's local cache.

No image downloads and no writes to Kodi's texture database. Unknown images are
preserved. Exact show metadata can then repair missing posters asynchronously.
"""
from contextlib import closing
import glob
import os
import re
import sqlite3
import threading
import time
from urllib.parse import unquote, parse_qs, urlsplit

_cache = {}
_lock = threading.Lock()

def _url(value):
    value = str(value or '').strip()
    if value.startswith('image://'):
        value = unquote(value[8:].rstrip('/'))
    return value

def _database():
    try:
        import xbmcvfs
        folder = xbmcvfs.translatePath('special://database/')
        files = glob.glob(os.path.join(folder, 'Textures*.db'))
        return max(files, key=lambda p: int(re.search(r'Textures(\d+)', p).group(1))) if files else ''
    except Exception:
        return ''

def dimensions(value):
    url = _url(value)
    if not url:
        return None
    now = time.monotonic()
    with _lock:
        prior = _cache.get(url)
        if prior and now - prior[0] < (3600 if prior[1] else 30):
            return prior[1]
    size = None
    db = _database()
    if db:
        try:
            with closing(sqlite3.connect('file:' + db + '?mode=ro', uri=True, timeout=0.02)) as conn:
                row = conn.execute('SELECT s.width,s.height FROM texture t JOIN sizes s '
                                   'ON s.idtexture=t.id WHERE t.url=? '
                                   'ORDER BY s.width*s.height DESC LIMIT 1', (url,)).fetchone()
                if row and row[0] > 0 and row[1] > 0:
                    size = tuple(row)
        except (sqlite3.Error, OSError):
            pass
    with _lock:
        if len(_cache) >= 2048:
            _cache.clear()
        _cache[url] = (now, size)
    return size

def portrait(value, landscape=()):
    """Return candidate unless its role/aspect is known to be landscape."""
    value = str(value or '').strip()
    url = _url(value)
    if not url:
        return ''
    # Explicit episode still keys must never be promoted to show posters.
    if any(url == _url(other) for other in landscape if other):
        return ''
    path = urlsplit(url.split('|', 1)[0]).path.lower()
    if '/images/thumb' in path or '/images/backdrop' in path or '/still/' in path:
        return ''
    size = dimensions(value)
    return '' if size and size[0] > size[1] * 1.1 else value
