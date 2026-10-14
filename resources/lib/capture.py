# -*- coding: utf-8 -*-
"""Run a Dex Hub listing route and keep its items instead of sending them to Kodi.

v5.10.96. The Dex Hub Home window (resources/lib/homeui) draws its own rows,
but every row is exactly what the matching native listing would show: the
same catalog request, the same artwork policy, the same click path and the
same context menu. Instead of a second copy of that logic, the window runs
the normal route with a capture sink active on the calling thread.
``ui_core.add_item`` then hands each finished item to the sink (the ListItem
plus the plain data it was built from) and ``ui_core.end_dir`` returns
without touching xbmcplugin.

The sink is thread-local, so several rows can load in parallel threads
without mixing their items, and a normal plugin invocation running in the
same interpreter is never affected.
"""
import threading
import time
from urllib.parse import parse_qsl, urlsplit

_LOCAL = threading.local()
# Captured routes running in this interpreter. The Dex Hub Home window runs
# in its own invocation, so while any row is loading, a notification raised
# by a helper thread of that route is still a row notification, not a user
# message.
_RUNNING = [0]
_RUNNING_LOCK = threading.Lock()


def sink():
    """The active capture list for this thread, or None."""
    return getattr(_LOCAL, 'items', None)


def active():
    return sink() is not None


def quiet():
    """True while toasts must stay off screen (a row is being captured)."""
    return active() or _RUNNING[0] > 0


def note(message, level='info'):
    """A route's toast during a capture: logged, and kept for the row.

    An error marks the row as failed; any other message is remembered as the
    reason an empty row can show ("the Nuvio source is not available").
    """
    text = str(message or '').strip()
    if active():
        if level == 'error' and not getattr(_LOCAL, 'error', ''):
            _LOCAL.error = text or 'error'
        elif level != 'error' and text and not getattr(_LOCAL, 'notice', ''):
            _LOCAL.notice = text
    try:
        import xbmc
        xbmc.log('[DexHub] homeui row %s: %s' % (level, text), xbmc.LOGINFO)
    except Exception:
        pass


def patience():
    """Seconds a captured catalog request may wait (0: the normal fast budget)."""
    return float(getattr(_LOCAL, 'patience', 0.0) or 0.0)


def last_error():
    """The exception text of this thread's last captured route, or ''."""
    return getattr(_LOCAL, 'error', '')


def begin_notes():
    """Forget this thread's earlier route message (a row may run several routes)."""
    _LOCAL.notice = ''


def last_notice():
    """The first route message on this thread since begin_notes(), or ''."""
    return getattr(_LOCAL, 'notice', '')


def record(path, is_folder, label, label2, info, art, ids, cast, props, menu, listitem):
    items = sink()
    if items is None:
        return False
    items.append({
        'path': str(path or ''),
        'folder': bool(is_folder),
        'label': label or '',
        'label2': label2 or '',
        'info': dict(info or {}),
        'art': dict(art or {}),
        'ids': dict(ids or {}),
        'props': dict(props or {}),
        'menu': [tuple(m) for m in (menu or [])],
        'cast': cast,
        'listitem': listitem,
    })
    return True


class _Capture(object):
    def __enter__(self):
        self._previous = getattr(_LOCAL, 'items', None)
        _LOCAL.items = []
        return _LOCAL.items

    def __exit__(self, exc_type, exc, tb):
        _LOCAL.items = self._previous
        return False


def capturing():
    return _Capture()


def params_from_url(url):
    """plugin://plugin.video.dexhub/?a=b -> {'a': 'b'}; plain dicts pass through."""
    if isinstance(url, dict):
        return dict(url)
    try:
        query = urlsplit(str(url or '')).query
    except Exception:
        query = ''
    return dict(parse_qsl(query, keep_blank_values=True))


def run_route(params, api=None, patience=0.0):
    """Dispatch one Dex Hub route with the capture sink active.

    Returns (items, elapsed_seconds). ``api`` is the loaded plugin module; it
    is imported here when omitted so callers in the Home window never pay for
    the router before the first row actually needs it. ``patience`` (seconds)
    lets a catalog request wait longer than the usual fast budget; the Home
    window uses it once for a row whose server was too slow the first time.
    """
    if api is None:
        from . import plugin as api
    params = params_from_url(params)
    started = time.monotonic()
    _LOCAL.error = ''
    _LOCAL.patience = float(patience or 0.0)
    with _RUNNING_LOCK:
        _RUNNING[0] += 1
    try:
        with capturing() as items:
            try:
                api._dispatch(params)
            except Exception as exc:
                _LOCAL.error = str(exc) or exc.__class__.__name__
                try:
                    import xbmc
                    xbmc.log('[DexHub] homeui capture failed for %s: %s' % (
                        params.get('action') or '?', exc), xbmc.LOGWARNING)
                except Exception:
                    pass
            result = list(items)
    finally:
        _LOCAL.patience = 0.0
        with _RUNNING_LOCK:
            _RUNNING[0] = max(0, _RUNNING[0] - 1)
    return result, time.monotonic() - started
