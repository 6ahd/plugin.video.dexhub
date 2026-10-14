# -*- coding: utf-8 -*-
"""Per-library sort preference (Plex + Emby).

Before v3.9.232 the sort was a pure URL parameter.  Each context-menu entry did
a `Container.Update` with `sort=...` baked into the URL and nothing was ever
stored, so:

  * the library TILE always opened with `sort=''` (Plex) / `'SortName'` (Emby),
  * walking back into a library reset the order,
  * the user had to re-pick the sort every single time.

This stores the chosen sort per (backend, server, library) so a library reopens
the way the user left it.  A tiny JSON file is enough — there are only ever a
handful of libraries, and it keeps the choice readable/removable by hand.

`reuselanguageinvoker=true` NOTE
    Dex Hub runs with a WARM Python VM: module-level state survives across
    plugin invocations.  A naive `_MEM = None` module cache would be populated
    once and never refreshed, so an invocation that cached an EMPTY store would
    keep returning the default even after the chooser wrote a sort — i.e. the
    exact bug this module exists to fix would come back through the cache.

    So the cache is keyed on the file's (mtime, size): a warm VM picks up a
    choice written by any other invocation, while still avoiding a disk read on
    every call.  `profile_path()` is likewise resolved lazily rather than frozen
    at import time.
"""
from . import profile_scope as _scope
import os
import threading

from .dexhub.common import profile_path
from .dexhub.safe_io import read_json, write_json

_LOCK = threading.RLock()
_MEM = None
_STAMP = None   # (mtime, size) of the file the cache was built from


def store_path():
    """Resolve lazily: must not be frozen at import time in a warm VM."""
    return _scope.path('sort_prefs.json')


def _stamp(path):
    try:
        st = os.stat(path)
        return (_scope.key(), st.st_mtime, st.st_size)
    except Exception:
        return (_scope.key(), None)


def _load():
    """Return the store, re-reading whenever the file changed on disk."""
    global _MEM, _STAMP
    with _LOCK:
        path = store_path()
        stamp = _stamp(path)
        if _MEM is not None and stamp == _STAMP:
            return _MEM
        try:
            data = read_json(path, {})
        except Exception:
            data = {}
        _MEM = data if isinstance(data, dict) else {}
        _STAMP = stamp
        return _MEM


def _key(backend, server_id, library_key):
    return '%s|%s|%s' % (backend or '', server_id or '', library_key or '')


def get_sort(backend, server_id, library_key, default=''):
    """Return the remembered sort for this library, or `default`."""
    try:
        value = _load().get(_key(backend, server_id, library_key))
    except Exception:
        value = None
    return value if isinstance(value, str) and value else default


def set_sort(backend, server_id, library_key, sort_value):
    """Remember `sort_value` for this library. An empty value clears it."""
    global _MEM, _STAMP
    with _LOCK:
        path = store_path()
        data = dict(_load())
        key = _key(backend, server_id, library_key)
        if sort_value:
            data[key] = sort_value
        else:
            data.pop(key, None)
        try:
            write_json(path, data)
        except Exception:
            pass
        _MEM = data
        # Re-stamp AFTER the write so this warm VM does not needlessly re-read,
        # while any other invocation still sees the change via mtime/size.
        _STAMP = _stamp(path)
    # v5.10.104: a menu the skin shows as a widget carries the saved sort in
    # its links; its recording would reopen the library the old way
    try:
        from . import widget_cache
        widget_cache.clear()
    except Exception:
        pass
    return sort_value


def clear_all():
    global _MEM, _STAMP
    with _LOCK:
        path = store_path()
        try:
            write_json(path, {})
        except Exception:
            pass
        _MEM = {}
        _STAMP = _stamp(path)


# v5.10.198: a server library opens on what was added last, each server its
# own way: (movies, series)
NEWEST = {
    'plex': ('addedAt:desc', 'episode.addedAt:desc'),
    'emby': ('DateCreated:desc', 'DateLastContentAdded:desc'),
    'jellyfin': ('DateCreated:desc', 'DateLastContentAdded:desc'),
    'silo': ('added_at:desc', 'latest_episode_added:desc'),
}
_SERIES = ('show', 'shows', 'series', 'tv', 'tvshow', 'tvshows')


def newest_first(backend, library_type=''):
    """The server's own "newest added first" for a library of this type."""
    movies, series = NEWEST.get(str(backend or '').lower(), ('', ''))
    return series if str(library_type or '').strip().lower() in _SERIES else movies
