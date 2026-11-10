"""DexWorld lists fetched from the subscriber's own connection (v5.10.553).

On 8 Nov 2026 the IPTV provider behind a subscriber's source s1 blocked
DexWorld's server address in its nginx. Every list the server asked for got 403
(IPTV_UPSTREAM in the catalog answer), while the channels still played from the
subscriber's home: the server builds stream addresses without asking the
provider. Other subscribers' providers were turning the same address away too,
because one server fetched every subscriber's lists.

So when DexWorld answers a catalog with IPTV_UPSTREAM, Dex Hub asks the
provider itself, from the subscriber's own connection, with the account
DexWorld gives it (GET /<key>/dexhub/sources.json: the subscriber's own
sources, the same details every stream address carries). It then builds the
metas DexWorld would have built, so the rows, favourites, guide and playback
(still through DexWorld's /stream/) work as before.

Only live channels are built here: dex_channel_<stream_id>_ts~<source>, the
form DexWorld's favourites carry. VOD and series wait for the server's exact
format. The account is kept in memory only, never logged or written to disk.
"""
import re
import threading
import time
from urllib.parse import urlsplit

import xbmc

_SOURCES = {}           # 'https://dexworld.cc/<key>' -> (time, {sid: source} or None)
_SOURCES_TTL = 6 * 3600
_MISSING_TTL = 600      # an endpoint that is not there yet is asked again after 10 minutes
_LISTS = {}             # (server, user, action, category) -> (time, [items])
_LISTS_TTL = 600
_LOCK = threading.Lock()


def _log(text, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] dexworld direct: %s' % text, level)


def _origin(catalog_url):
    """('https://dexworld.cc', key) of a DexWorld catalog address, or None."""
    parts = urlsplit(str(catalog_url or ''))
    if 'dexworld' not in (parts.hostname or '').lower():
        return None
    segments = [s for s in parts.path.split('/') if s]
    if len(segments) < 2 or segments[1] != 'catalog':
        return None
    return '%s://%s' % (parts.scheme or 'https', parts.netloc), segments[0]


def _sources(base, key):
    """{source id: {'type', 'server', 'username', 'password'}} of this
    subscriber, from DexWorld; {} when DexWorld cannot say (yet)."""
    slot = '%s/%s' % (base, key)
    now = time.time()
    with _LOCK:
        hit = _SOURCES.get(slot)
    if hit and now - hit[0] < (_SOURCES_TTL if hit[1] else _MISSING_TTL):
        return hit[1] or {}
    found = None
    try:
        from .dexhub import client
        data = client.get_json('%s/dexhub/sources.json' % slot, ttl_seconds=0, memory_only=True,
                               timeout_override=10, retry=False)
        rows = (data or {}).get('sources') if isinstance(data, dict) else None
        if isinstance(rows, list):
            found = {}
            for row in rows:
                if isinstance(row, dict) and row.get('id') and row.get('type') == 'xtream' \
                        and row.get('server') and row.get('username'):
                    found[str(row['id'])] = {'server': str(row['server']).rstrip('/'),
                                             'username': str(row['username']),
                                             'password': str(row.get('password') or '')}
    except Exception as exc:
        _log('the sources were not read: %s' % type(exc).__name__, xbmc.LOGDEBUG)
    with _LOCK:
        _SOURCES[slot] = (now, found)
    return found or {}


def _items(source, sid, action, category):
    key = (source['server'], source['username'], action, category)
    now = time.time()
    with _LOCK:
        hit = _LISTS.get(key)
    if hit and now - hit[0] < _LISTS_TTL:
        return hit[1]
    from .homeui import live_iptv
    conf = dict(source, id='dw_%s' % sid)
    reply = live_iptv.xt_api(conf, timeout=25, action=action, category_id=category)
    items = [i for i in (reply if isinstance(reply, list) else []) if isinstance(i, dict)]
    with _LOCK:
        if len(_LISTS) > 40:
            _LISTS.clear()
        _LISTS[key] = (now, items)
    return items


def _separator(name):
    """Panels put headings in the list as channels ('###### NEWS TV ######')."""
    bare = re.sub(r'[\s#=*\-_|~•▶◀►◄]+', '', name)
    return not bare or (name.strip()[:3] in ('###', '===', '***', '---') and name.strip()[-3:] == name.strip()[:3])


def _channel(item, sid):
    stream_id = str(item.get('stream_id') or '').strip()
    if not stream_id or _separator(str(item.get('name') or '')):
        return None
    meta = {'id': 'dex_channel_%s_ts~%s' % (stream_id, sid), 'type': 'channel',
            'name': str(item.get('name') or '').strip() or stream_id, 'posterShape': 'square'}
    icon = str(item.get('stream_icon') or '').strip()
    if icon:
        meta['poster'] = icon
    return meta


_BUILDERS = {'get_live_streams': _channel}


def catalog(catalog_url, failure, skip=0):
    """{'metas': [...], 'dexhubDirect': True} built from the provider for the
    DexWorld catalog that failed with ``failure`` (its dexworldError), or None."""
    if not isinstance(failure, dict) or failure.get('code') != 'IPTV_UPSTREAM':
        return None
    action = str(failure.get('action') or '')
    build = _BUILDERS.get(action)
    category = str(failure.get('category') or '').strip()
    sid = str(failure.get('source') or '').strip()
    origin = _origin(catalog_url)
    if not (build and category and sid and origin):
        return None
    source = _sources(*origin).get(sid)
    if not source:
        return None
    started = time.time()
    try:
        items = _items(source, sid, action, category)
    except Exception as exc:
        _log('the provider of %s did not answer %s either: %s' % (sid, action, str(exc)[:120]), xbmc.LOGWARNING)
        return None
    metas = [m for m in (build(i, sid) for i in items) if m]
    _log('%s %s of %s: %d from the provider in %.0f ms' % (action, category, sid, len(metas),
                                                           (time.time() - started) * 1000))
    return {'metas': metas[max(0, int(skip or 0)):], 'dexhubDirect': True}
