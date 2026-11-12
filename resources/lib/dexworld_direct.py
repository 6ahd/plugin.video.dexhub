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


_LIVE_PATH = re.compile(r'^/(?:live/)?([^/]+)/([^/]+)/\d+(?:\.\w{1,5})?$')
_CHANNEL_ID = re.compile(r'^dex_channel_(\d+)_(ts|m3u8)~(s\d+)$')
_HOME_PROP = 'dexhub.dw.acct.%s'


def _remembered(slot):
    """The account kept in Kodi's memory (a Home property: shared by every
    add-on call of this Kodi session, never written to disk)."""
    import hashlib
    import json
    import xbmcgui
    name = _HOME_PROP % hashlib.sha1(slot.encode('utf-8')).hexdigest()[:12]
    try:
        data = json.loads(xbmcgui.Window(10000).getProperty(name) or '{}')
    except ValueError:
        data = {}
    return name, data


def _source_from_stream(base, key, sid):
    """v5.10.560: the account of source ``sid`` from DexWorld's own stream
    answer, while /dexhub/sources.json is not on the server. /stream/ builds
    a channel's address without asking the provider (it answers when the
    lists get 403), and that address is the provider's own
    {server}/live/{user}/{password}/{id}.ts the device plays anyway. Kept in
    Kodi's memory only (6 hours; 10 minutes when not found), never logged or
    written. The server took 25 to 80 s for it on 12 Nov 2026."""
    import json
    import xbmcgui
    from urllib.parse import unquote
    slot = '%s/%s|%s' % (base, key, sid)
    name, kept = _remembered(slot)
    now = time.time()
    if kept and now - float(kept.get('t') or 0) < (_SOURCES_TTL if kept.get('server') else _MISSING_TTL):
        return dict((k, kept[k]) for k in ('server', 'username', 'password')) if kept.get('server') else None
    found = None
    try:
        from .dexhub import client
        data = client.get_json('%s/%s/stream/channel/dex_channel_1_ts~%s.json' % (base, key, sid), ttl_seconds=0,
                               memory_only=True, timeout_override=90, retry=False)
        for row in (data or {}).get('streams') or [] if isinstance(data, dict) else []:
            parts = urlsplit(str((row or {}).get('url') or '').split('|', 1)[0])
            match = _LIVE_PATH.match(parts.path or '')
            if parts.scheme in ('http', 'https') and parts.netloc and match:
                found = {'server': '%s://%s' % (parts.scheme, parts.netloc),
                         'username': unquote(match.group(1)), 'password': unquote(match.group(2))}
                break
    except Exception as exc:
        _log('the stream answer was not read: %s' % type(exc).__name__, xbmc.LOGDEBUG)
    xbmcgui.Window(10000).setProperty(name, json.dumps(dict(found or {}, t=now)))
    _log('source %s: %s' % (sid, 'account found in its stream answer' if found else 'no account in the stream answer'))
    return found


def account(base, key, sid, ask=True):
    """The provider account of DexWorld source ``sid``: sources.json, else
    its stream answer (``ask`` False: only one already known)."""
    # the one this Kodi session already found first: DexWorld took 21 s even
    # to say sources.json is not there (12 Nov 2026), on every add-on call
    _name, kept = _remembered('%s/%s|%s' % (base, key, sid))
    if kept.get('server') and time.time() - float(kept.get('t') or 0) < _SOURCES_TTL:
        return dict((k, kept[k]) for k in ('server', 'username', 'password'))
    if not ask:
        return None
    source = _sources(base, key).get(sid)
    if source:
        return source
    return _source_from_stream(base, key, sid)


def channel_address(base, key, channel_id):
    """v5.10.560: the provider's own address of a DexWorld channel
    (dex_channel_<id>_ts~sN), as DexWorld's /stream/ builds it, when its
    account is known; '' otherwise (the caller asks DexWorld)."""
    from urllib.parse import quote
    match = _CHANNEL_ID.match(str(channel_id or ''))
    if not match:
        return ''
    stream_id, ext, sid = match.groups()
    source = account(base, key, sid, ask=False)
    if not source:
        return ''
    return '%s/live/%s/%s/%s.%s' % (source['server'].rstrip('/'), quote(source['username'], safe=''),
                                    quote(source['password'], safe=''), stream_id, ext)


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


def _year(item):
    match = re.search(r'(19|20)\d{2}', str(item.get('year') or item.get('releaseDate') or item.get('release_date')
                                           or item.get('releasedate') or ''))
    if not match:
        match = re.search(r'[\(\[]((?:19|20)\d{2})[\)\]]\s*$', str(item.get('name') or ''))
        return match.group(1) if match else ''
    return match.group(0)


def _tmdb(item):
    value = str(item.get('tmdb') or item.get('tmdb_id') or '').strip()
    return value if value.isdigit() and value != '0' else ''


def _film(item, sid):
    """v5.10.560: a film of the provider, with Dex Hub's own id: its details
    and its file come from the provider too (local_meta, local_streams)."""
    stream_id = str(item.get('stream_id') or '').strip()
    if not stream_id.isdigit() or _separator(str(item.get('name') or '')):
        return None
    ext = re.sub(r'[^A-Za-z0-9]', '', str(item.get('container_extension') or 'mp4'))[:8] or 'mp4'
    meta = {'id': 'dexhd_movie_%s_%s~%s' % (stream_id, ext, sid), 'type': 'movie',
            'name': str(item.get('name') or '').strip() or stream_id, 'posterShape': 'poster',
            'poster': str(item.get('stream_icon') or '').strip()}
    if _tmdb(item):
        meta['tmdb'] = _tmdb(item)
    if _year(item):
        meta['year'] = meta['releaseInfo'] = _year(item)
    try:
        if float(item.get('rating') or 0):
            meta['imdbRating'] = str(round(float(item['rating']), 1))
    except (TypeError, ValueError):
        pass
    return meta


def _show(item, sid):
    series_id = str(item.get('series_id') or '').strip()
    if not series_id.isdigit() or _separator(str(item.get('name') or '')):
        return None
    meta = {'id': 'dexhd_series_%s~%s' % (series_id, sid), 'type': 'series',
            'name': str(item.get('name') or '').strip() or series_id, 'posterShape': 'poster',
            'poster': str(item.get('cover') or '').strip(),
            'description': str(item.get('plot') or '').strip()}
    backdrop = item.get('backdrop_path') or ''
    if isinstance(backdrop, list):
        backdrop = backdrop[0] if backdrop else ''
    if backdrop:
        meta['background'] = str(backdrop)
    if _tmdb(item):
        meta['tmdb'] = _tmdb(item)
    if _year(item):
        meta['year'] = meta['releaseInfo'] = _year(item)
    return meta


_BUILDERS = {'get_live_streams': _channel, 'get_vod_streams': _film, 'get_series': _show}
_LOCAL_ID = re.compile(r'^dexhd_(movie|series|ep)_(\d+)(?:_([A-Za-z0-9]{1,8}))?~(s\d+)$')
# DexWorld's own series ids (its favourites and recent: dex_series_<id>_mp<n>~sN)
_SERVER_SERIES = re.compile(r'^dex_series_(\d+)(?:_[A-Za-z0-9]+)?~(s\d+)$')


def _resource(url):
    """(base, key, resource) of a DexWorld meta/stream address, or None."""
    parts = urlsplit(str(url or ''))
    if 'dexworld' not in (parts.hostname or '').lower():
        return None
    segments = [s for s in parts.path.split('/') if s]
    if len(segments) < 2 or segments[1] not in ('meta', 'stream'):
        return None
    return '%s://%s' % (parts.scheme or 'https', parts.netloc), segments[0], segments[1]


def local_meta(url, item_id):
    """v5.10.560: the details of a DexWorld series (Dex Hub's own
    dexhd_series_<id>~sN, or DexWorld's dex_series_<id>_mp<n>~sN of its
    favourites), from the provider's get_series_info: its seasons
    and episodes as a Stremio meta. None for any other id."""
    match = _LOCAL_ID.match(str(item_id or ''))
    where = _resource(url)
    if match and match.group(1) == 'series':
        series_id, sid = match.group(2), match.group(4)
    else:
        found = _SERVER_SERIES.match(str(item_id or ''))
        if not found:
            return None
        series_id, sid = found.groups()
    if not where:
        return None
    source = account(where[0], where[1], sid)
    if not source:
        return None
    from .homeui import live_iptv
    conf = dict(source, id='dw_%s' % sid)
    try:
        data = live_iptv.xt_api(conf, timeout=25, action='get_series_info', series_id=series_id) or {}
    except Exception as exc:
        _log('the provider did not answer get_series_info: %s' % type(exc).__name__, xbmc.LOGWARNING)
        return None
    info = data.get('info') if isinstance(data, dict) else None
    info = info if isinstance(info, dict) else {}
    videos = []
    for season, episodes in ((data.get('episodes') or {}) if isinstance(data, dict) else {}).items():
        for episode in episodes or []:
            if not isinstance(episode, dict) or not str(episode.get('id') or '').isdigit():
                continue
            extra = episode.get('info') if isinstance(episode.get('info'), dict) else {}
            ext = re.sub(r'[^A-Za-z0-9]', '', str(episode.get('container_extension') or 'mp4'))[:8] or 'mp4'
            videos.append({'id': 'dexhd_ep_%s_%s~%s' % (episode['id'], ext, sid),
                           'season': int(season) if str(season).isdigit() else 1,
                           'episode': int(episode.get('episode_num') or 0),
                           'title': '', 'thumbnail': str(extra.get('movie_image') or ''),
                           'overview': str(extra.get('plot') or '')})
    backdrop = info.get('backdrop_path') or ''
    if isinstance(backdrop, list):
        backdrop = backdrop[0] if backdrop else ''
    meta = {'id': item_id, 'type': 'series', 'name': str(info.get('name') or ''),
            'poster': str(info.get('cover') or ''), 'background': str(backdrop or ''),
            'description': str(info.get('plot') or ''), 'videos': videos}
    if _tmdb(info):
        meta['tmdb'] = _tmdb(info)
    _log('series of %s: %d episodes from the provider' % (sid, len(videos)))
    return {'meta': meta}


def local_streams(url, item_id):
    """v5.10.560: the provider's own file of a film or episode listed by Dex
    Hub itself (dexhd_movie_/dexhd_ep_<id>_<ext>~sN). None for any other id."""
    from urllib.parse import quote
    match = _LOCAL_ID.match(str(item_id or ''))
    where = _resource(url)
    if not (match and where and match.group(1) in ('movie', 'ep')):
        return None
    source = account(where[0], where[1], match.group(4))
    if not source:
        return {'streams': []}
    kind = 'movie' if match.group(1) == 'movie' else 'series'
    address = '%s/%s/%s/%s/%s.%s' % (source['server'].rstrip('/'), kind, quote(source['username'], safe=''),
                                     quote(source['password'], safe=''), match.group(2), match.group(3) or 'mp4')
    return {'streams': [{'name': 'DexWorld', 'title': 'IPTV · %s' % (match.group(3) or 'mp4').upper(),
                         'url': address, 'behaviorHints': {'notWebReady': True}}]}


# v5.10.560: a source the provider turns DexWorld's server away from is not
# asked again for an hour: its categories open from the provider at once
# (each server answer took 20-80 s on 12 Nov 2026). The provider category of
# each DexWorld category is learnt from the server's 403 answers (kept on
# disk, without the key), else found by its name.
_BLOCK_TTL = 3600
_CAT_ACTIONS = (('dex_live', 'get_live_streams', 'get_live_categories'),
                ('dex_vod', 'get_vod_streams', 'get_vod_categories'),
                ('dex_series', 'get_series', 'get_series_categories'))


def _home():
    import xbmcgui
    return xbmcgui.Window(10000)


def _slot_hash(text):
    import hashlib
    return hashlib.sha1(str(text).encode('utf-8')).hexdigest()[:16]


def _blocked_prop(base, key, sid):
    return 'dexhub.dw.blocked.%s' % _slot_hash('%s/%s|%s' % (base, key, sid))


def _map_file():
    try:
        from . import profile_scope
        import os
        return os.path.join(profile_scope.path('homeui'), 'dw_categories.json')
    except Exception:
        return ''


def _read_map():
    import json
    path = _map_file()
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _learn(catalog_url, action, category, sid, blocked=''):
    import json
    path = _map_file()
    if not path:
        return
    data = _read_map()
    key = _slot_hash(_strip_paging(catalog_url))
    if data.get(key) == [action, category, sid] and (not blocked or
                                                      time.time() - float(data.get(blocked) or 0) < 600):
        return
    data[key] = [action, category, sid]
    if blocked:
        data[blocked] = int(time.time())
    if len(data) > 2000:
        data = dict(list(data.items())[-1500:])
    try:
        with open(path + '.tmp', 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        import os
        os.replace(path + '.tmp', path)
    except Exception:
        pass


def _strip_paging(catalog_url):
    """The catalog address without its page (skip): one category, one entry."""
    return re.sub(r'([/&])skip=\d+', r'\1', str(catalog_url or '')).replace('/.json', '.json').replace('&.json', '.json')


def _parts(catalog_url):
    """(catalog id, {extra}) of a DexWorld catalog address."""
    from urllib.parse import parse_qsl, unquote
    path = urlsplit(str(catalog_url or '')).path
    segments = [s for s in path.split('/') if s]
    if len(segments) < 4 or segments[1] != 'catalog':
        return '', {}
    if len(segments) >= 5:
        cid = unquote(segments[3])
        extra = dict(parse_qsl(unquote(segments[4])[:-5] if segments[4].endswith('.json') else unquote(segments[4])))
    else:
        cid = unquote(segments[3][:-5] if segments[3].endswith('.json') else segments[3])
        extra = {}
    return cid, extra


def _norm(text):
    return re.sub(r'[^0-9a-z\u0600-\u06ff]+', '', str(text or '').lower())


def _by_name(source, sid, list_action, genre):
    """The provider category named like a DexWorld genre option, or ''."""
    from .homeui import live_iptv
    want = _norm(genre)
    if not want:
        return ''
    key = (source['server'], source['username'], list_action, '')
    now = time.time()
    with _LOCK:
        hit = _LISTS.get(key)
    if hit and now - hit[0] < _LISTS_TTL:
        cats = hit[1]
    else:
        conf = dict(source, id='dw_%s' % sid)
        reply = live_iptv.xt_api(conf, timeout=25, action=list_action)
        cats = [c for c in (reply if isinstance(reply, list) else []) if isinstance(c, dict)]
        with _LOCK:
            _LISTS[key] = (now, cats)
    for cat in cats:
        if _norm(cat.get('category_name')) == want:
            return str(cat.get('category_id') or '')
    return ''


def before_catalog(catalog_url, skip=0):
    """The catalog built from the provider at once when its source is known
    to be blocked for DexWorld's server (an hour after its last 403), else
    None and the server is asked as before."""
    origin = _origin(catalog_url)
    if not origin:
        return None
    cid, extra = _parts(catalog_url)
    match = re.match(r'^(dex_(?:live|vod|series))_[a-z]+~(s\d+)$', cid)
    if not match or extra.get('search'):
        return None
    prefix, sid = match.groups()
    prop = _blocked_prop(origin[0], origin[1], sid)
    try:
        since = float(_home().getProperty(prop) or 0)
    except ValueError:
        since = 0
    if not since:
        # kept on disk too, so a restarted Kodi does not wait for the server again
        try:
            since = float(_read_map().get(prop) or 0)
        except (TypeError, ValueError):
            since = 0
    if not since or time.time() - since > _BLOCK_TTL:
        return None
    action, list_action = next((a, l) for p, a, l in _CAT_ACTIONS if p == prefix)
    known = _read_map().get(_slot_hash(_strip_paging(catalog_url)))
    category = known[1] if known and known[0] == action and known[2] == sid else ''
    source = account(origin[0], origin[1], sid, ask=False)
    if not source:
        return None
    if not category and extra.get('genre'):
        try:
            category = _by_name(source, sid, list_action, extra['genre'])
        except Exception:
            category = ''
    if not category:
        return None
    return catalog(catalog_url, {'code': 'IPTV_UPSTREAM', 'action': action, 'category': category,
                                 'source': sid, '_known': True}, skip)


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
    if not failure.get('_known'):
        # the server's own 403: remember it, and which provider category this is
        _home().setProperty(_blocked_prop(origin[0], origin[1], sid), '%d' % time.time())
        _learn(catalog_url, action, category, sid, blocked=_blocked_prop(origin[0], origin[1], sid))
    source = account(origin[0], origin[1], sid)
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
