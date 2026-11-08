# -*- coding: utf-8 -*-
"""Search catalogs that keep failing are left out for a while (v5.10.547).

A CoreELEC box's searches all waited on the same catalogs: DexTube answered
HTTP 404 to every search, and Plex servers that were off held workers until
the 6 s limit. Like Plex's own rest in source searches, such a catalog sits
out the next searches: 30 min after a refusal (HTTP 4xx), 10 min after it ran
the whole time limit without answering. The list lives in a Home window
property, so every Dex Hub process sees it, and it is gone when Kodi restarts.
"""
import json
import time

_PROP = 'dexhub.search.rest'
REFUSED_MINUTES = 30
SLOW_MINUTES = 10


def _home():
    import xbmcgui
    return xbmcgui.Window(10000)


def _read():
    try:
        data = json.loads(_home().getProperty(_PROP) or '{}')
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write(data):
    try:
        _home().setProperty(_PROP, json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def resting():
    """{catalog name: reason} for those still sitting out."""
    now = time.time()
    out = {}
    for name, value in _read().items():
        try:
            until, reason = float(value[0]), str(value[1])
        except Exception:
            continue
        if until > now:
            out[name] = reason
    return out


def refused(error):
    """True for an answer that will not change on the next search: HTTP 4xx
    other than 408 / 429."""
    text = str(error or '')
    for code in ('400', '401', '403', '404', '405', '410', '451'):
        if 'HTTP %s' % code in text or 'HTTP Error %s' % code in text:
            return True
    return False


def mark(name, minutes, reason):
    if not name:
        return
    now = time.time()
    data = dict((k, v) for k, v in _read().items()
                if isinstance(v, list) and v and _number(v[0]) > now)
    data[name] = [now + minutes * 60.0, reason]
    _write(data)


def clear(names):
    names = [n for n in (names or ()) if n]
    if not names:
        return
    data = _read()
    if any(n in data for n in names):
        for n in names:
            data.pop(n, None)
        _write(data)


def _number(value):
    try:
        return float(value)
    except Exception:
        return 0.0
