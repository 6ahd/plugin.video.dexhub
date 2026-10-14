# -*- coding: utf-8 -*-
"""M3U playlists and Xtream Codes accounts kept by Dex Hub itself (v5.10.104).

These live in Dex Hub, not in Kodi's PVR: nothing has to be installed, and
each one is a tab of the Live TV page.

M3U: the playlist is downloaded, read once (#EXTINF attributes, #EXTVLCOPT
user agent and referrer, #EXTGRP) and kept as JSON in the Home's profile
folder (live_cache), then read again every 12 hours in the background. Its
groups are the rows. Film and series entries of Xtream playlists (/movie/,
/series/) and DRM protected channels are left out: this is live TV. An XMLTV
guide (the address given with the playlist, or the playlist's own url-tvg)
is read in the background too, streamed, keeping only the playlist's
channels and the hours around now, so a large guide costs little memory.

Xtream Codes: read through the account's own API (player_api.php): the live
categories are the rows, a category's channels are asked for when its row
first shows, and the short guide of the channels on screen gives now and
next. A channel plays from {server}/live/{user}/{password}/{id}.ts.

Both carry the account in their addresses, so no address is ever logged or
shown (see live_sources).
"""
import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import xbmc
import xbmcgui

from . import live
from . import live_sources as LS

M3U_TTL = 12 * 3600
EPG_TTL = 12 * 3600
XT_CATS_TTL = 6 * 3600
XT_STREAMS_TTL = 3600
XT_EPG_TTL = 900
EPG_BEFORE = 2 * 3600
EPG_AFTER = 30 * 3600
EPG_OPEN_BEFORE = 24 * 3600  # how far back a programme without a stop time may start
EPG_OPEN_LAST = 3600        # the length given to the last programme without a stop time
EPG_PER_CHANNEL = 48
EPG_BUDGET = 120.0          # reading a guide, once it is downloaded
EPG_RETRY = 3600            # a guide cut short is read again after an hour
DOWNLOAD_TIMEOUT = 20       # seconds without a byte before a download fails
FAILED_RETRY = 600          # a playlist or guide that failed is not asked for again sooner
M3U_LIMIT = 150 * 1024 * 1024
EPG_LIMIT = 400 * 1024 * 1024
# v5.10.110: a channel row has three more fields (tvg-name, #KODIPROP
# properties, other headers); a playlist kept by 5.10.109 is still read,
# and read again in the background
M3U_VERSION = 2
_M3U_READ = (1, 2)

# v5.10.110: values quoted with " or ', or bare; _HEAD ends at the comma that
# starts the title (one outside a quoted value)
_ATTR = re.compile(r'''([A-Za-z0-9_-]+)=(?:"([^"]*)"|'([^']*)'|([^\s"',]+))''')
_HEAD = re.compile(r'''(?:="[^"]*"|='[^']*'|[^,])*''')
# v5.10.110: an Xtream film or episode (/movie/<user>/<pass>/<id>.<ext>); any
# path holding /movies/ also dropped live channels (a 24/7 channel, a user
# named 'series')
_VOD = re.compile(r'/(?:movie|movies|series)/[^/]+/[^/]+/[^/]+$', re.I)
_SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
_PLAYABLE = ('http', 'https', 'rtmp', 'rtmps', 'rtsp', 'udp', 'rtp', 'smb', 'nfs', 'special', 'file',
             'plugin')

_mem = {}               # cache file -> (mtime, data)
_mem_lock = threading.Lock()
_jobs = {}              # background job key -> thread
_jobs_lock = threading.Lock()
_locks = {}             # 'm3u:<id>' / 'epg:<id>' -> the lock of that download
_failed = {}            # 'm3u:<id>' / 'epg:<id>' -> not asked for again before this time
# v5.10.104: set when the Home closes. A playlist or a guide still being
# downloaded or read stops at its next piece: Kodi waits for every thread of
# a finished script, and stops the interpreter when that takes too long.
_STOP = threading.Event()
_xt_epg = {}            # (source id, stream id) -> (time, programmes)
_xt_epg_lock = threading.Lock()


class BadGuide(LS.SourceError):
    """A programme guide that could not be read at all (v5.10.110)."""


class Refused(LS.SourceError):
    """The Xtream server refused the account (v5.10.110)."""


def _blocked(job):
    return time.time() < _failed.get(job, 0)


def _failed_now(job, exc):
    # v5.10.110: a guide too large or damaged was downloaded again (up to
    # 400 MB) every ten minutes; it now waits as long as a fresh guide would
    if isinstance(exc, LS.TooLarge):
        wait = EPG_TTL
    elif isinstance(exc, BadGuide):
        wait = EPG_RETRY
    else:
        wait = FAILED_RETRY
    _failed[job] = time.time() + wait


# ---------------------------------------------------------------- common
def source(conf, app):
    kind = conf.get('kind')
    host = urlsplit(conf.get('url') or conf.get('server') or '').hostname or ''
    label = live.clean(conf.get('name')) or host or ('M3U' if kind == 'm3u' else 'Xtream')
    return {'key': ('m3u:%s' if kind == 'm3u' else 'xt:%s') % conf['id'], 'kind': kind,
            'label': label, 'hint': 'M3U' if kind == 'm3u' else 'Xtream',
            'icon': app.media_path('tab_%s.png' % kind), 'card': 'live_%s.jpg' % kind,
            'conf': conf}


def _conf(app, src):
    conf = LS.get_saved(app.profile, str(src).split(':', 1)[-1])
    if not conf:
        raise LS.SourceError('المصدر غير موجود')
    return conf


def _cache_file(profile, name):
    return os.path.join(LS.cache_dir(profile), name)


def _load(path):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _mem_lock:
        hit = _mem.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    data = LS.read_json(path)
    if not isinstance(data, dict):
        return None
    with _mem_lock:
        if len(_mem) > 40:
            _mem.clear()
        _mem[path] = (mtime, data)
    return data


def _store(path, data):
    LS.write_json(path, data)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return
    with _mem_lock:
        _mem[path] = (mtime, data)


def stop_jobs(wait=2.0):
    """The Home is closing: downloads and guide reads stop; waited for briefly."""
    _STOP.set()
    until = time.monotonic() + max(0.0, float(wait))
    with _jobs_lock:
        threads = [t for t in _jobs.values() if t is not None and t.is_alive()]
    for thread in threads:
        left = until - time.monotonic()
        if left <= 0:
            break
        thread.join(left)


def resume_jobs():
    """A Home opens (this interpreter may have closed one before)."""
    _STOP.clear()


def stopping():
    return _STOP.is_set()


def _stoppable(progress=None):
    """A download's progress check that also ends it when the Home closes."""
    def check(size):
        if _STOP.is_set():
            return False
        return progress(size) if progress is not None else True
    return check


def _source_lock(key):
    with _jobs_lock:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def _temp_name(prefix, conf):
    # its own name for each download: two readers never share a half file
    return '%s_%s_%s.download' % (prefix, conf['id'], uuid.uuid4().hex[:8])


def _background(key, fn):
    """Run ``fn`` on its own thread unless the same job still runs."""
    if _STOP.is_set():
        return False
    with _jobs_lock:
        running = _jobs.get(key)
        if running is not None and running.is_alive():
            return False
        thread = threading.Thread(target=fn, name='DexHub-live-%s' % key.split(':', 1)[0])
        thread.daemon = True
        _jobs[key] = thread
        thread.start()
        return True


def rows(source, app, changed=None):
    if source['kind'] == 'xtream':
        return _xt_rows(source, app, changed)
    return _m3u_rows(source, app, changed)


def page_tiles(params, app):
    card = app.media_path('channel_card.jpg')
    if str(params.get('live_src')).startswith('xt:'):
        return xt_tiles(app, params, card, count=LS.PAGE_SIZE)
    return m3u_tiles(app, params, card, count=LS.PAGE_SIZE)


def resolve(tile, app):
    src = tile.get('src') or ''
    info = tile.get('stream') or {}
    if src.startswith('xt:'):
        conf = _conf(app, src)
        url = '%s/live/%s/%s/%s.%s' % (_xt_base(conf), quote(conf['username'], safe=''),
                                       quote(conf['password'], safe=''), quote(str(info.get('id')), safe=''),
                                       conf.get('fmt') or 'ts')
        return LS.stream_for(url, {}, key=tile.get('path') or '', logo=tile.get('logo') or '')
    if not info.get('url'):
        return None
    return LS.stream_for(info['url'], info.get('headers') or {}, key=tile.get('path') or '',
                         logo=tile.get('logo') or '', props=info.get('props'))


def guide(tile, app):
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    src = tile.get('src') or ''
    conf = _conf(app, src)
    now = time.time()
    if src.startswith('xt:'):
        items = xt_short_epg(conf, (tile.get('epg') or {}).get('xt'), limit=40)
    else:
        items = _m3u_guide_items(_epg_data(app.profile, conf), tile.get('epg') or {})
    return [p for p in items if p.get('end', 0) > now][:60]


def fill_epg(tiles, app):
    """Now and next of channels that have none yet: an M3U channel from its
    playlist's saved XMLTV guide (a recently watched one is made without it),
    an Xtream channel from the account's short guide."""
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    changed = []
    guides = {}
    for tile in tiles:
        src = str(tile.get('src') or '')
        if not src.startswith('m3u:') or tile.get('now'):
            continue
        if src not in guides:
            try:
                guides[src] = _epg_data(app.profile, _conf(app, src))
            except Exception:
                guides[src] = None
        tile['_epg_done'] = True
        if guides[src] and LS.apply_guide(tile, _m3u_guide_items(guides[src], tile.get('epg') or {})):
            changed.append(tile)
    todo = []
    for tile in tiles:
        sid = (tile.get('epg') or {}).get('xt')
        if sid and not tile.get('now') and str(tile.get('src', '')).startswith('xt:'):
            todo.append(tile)
    if not todo:
        return changed
    confs = {}

    def one(tile):
        src = tile['src']
        conf = confs.get(src)
        if conf is None:
            conf = confs[src] = _conf(app, src)
        items = xt_short_epg(conf, tile['epg']['xt'])
        tile['_epg_done'] = True
        if items and LS.apply_guide(tile, items):
            return tile
        return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(_quiet(one), todo[:12]):
            if result is not None:
                changed.append(result)
    return changed


def _quiet(fn):
    def run(arg):
        try:
            return fn(arg)
        except Exception:
            return None
    return run


# ------------------------------------------------------------------- M3U
def _attrs(text):
    out = {}
    for match in _ATTR.finditer(text):
        # the value is the one alternative that matched (quoted ", quoted ', bare)
        out[match.group(1).lower()] = (match.group(match.lastindex) or '').strip()
    return out


def _extinf(line):
    body = line[8:] if line.startswith('#EXTINF:') else line[7:]
    # v5.10.110: only what comes before the title's comma holds attributes,
    # so a title such as 'Sky Sports=HD' is never read as one
    head = _HEAD.match(body).end()
    attrs = _attrs(body[:head])
    name = body[head + 1:].strip() if head < len(body) else ''
    return {'name': name or attrs.get('tvg-name') or '',
            'logo': attrs.get('tvg-logo') or attrs.get('logo') or '',
            'group': attrs.get('group-title') or '',
            'tvg': attrs.get('tvg-id') or '',
            'tvg_name': attrs.get('tvg-name') or '',
            'chno': attrs.get('tvg-chno') or attrs.get('channel-number') or attrs.get('tvg-num') or ''}


def _joined(base, address):
    """An address of the playlist read against the playlist's own (v5.10.110).

    A relative entry ('streams/one.m3u8', '//cdn/x.ts') used to be dropped; an
    absolute path ('/storage/...') stays a local file, as before.
    """
    if not base or _SCHEME.match(address) or re.match(r'^[A-Za-z]:[\\/]', address):
        return address
    if address.startswith('/') and not address.startswith('//'):
        return address
    return urljoin(base, address)


def parse_m3u(lines, base=''):
    """(guide address, groups, channels) of an M3U playlist.

    A channel is [name, logo, group index, address, tvg id, number, user
    agent, referrer, tvg name, properties, headers] (v5.10.110: the last
    three; '' when there are none). ``base``: the playlist's own address.
    """
    base = str(base or '').split('|', 1)[0].strip()
    if not base.lower().startswith(('http://', 'https://')):
        base = ''
    header_epg = ''
    groups, index, channels = [], {}, []
    # v5.10.110: #EXTVLCOPT, #KODIPROP, #EXTHTTP and #EXTGRP lines given
    # before #EXTINF belong to the same channel: all wait for its address
    info, opts, props, headers, drm, grp = None, {}, {}, {}, False, ''
    for count, raw in enumerate(lines):
        if not count % 4096 and _STOP.is_set():
            # the Home closed: a large playlist is not read to its end
            raise LS.SourceError('أُلغي')
        line = raw.strip().lstrip('﻿')
        if not line:
            continue
        if line.startswith('#EXTM3U'):
            attrs = _attrs(line)
            found = attrs.get('url-tvg') or attrs.get('x-tvg-url') or ''
            # several guides may be listed, after commas or spaces: the first
            found = next((part for part in re.split(r'[,\s]+', found) if part), '')
            header_epg = urljoin(base, found) if found and base and '://' not in found else found
            continue
        if line.startswith('#EXTINF'):
            info = _extinf(line)
            continue
        if line.startswith('#EXTVLCOPT:'):
            name, _sep, value = line[11:].partition('=')
            name, value = name.strip().lower(), value.strip()
            if name == 'http-user-agent':
                opts['User-Agent'] = value
            elif name in ('http-referrer', 'http-referer'):
                opts['Referer'] = value
            continue
        if line.startswith('#EXTHTTP:'):
            # TiviMate's and IPTV Simple's headers: #EXTHTTP:{"User-Agent": ...}
            try:
                given = json.loads(line[9:])
            except ValueError:
                given = None
            for name, value in (given.items() if isinstance(given, dict) else ()):
                if name and value not in (None, ''):
                    LS.set_header(headers, str(name).strip(), str(value))
            continue
        if line.startswith('#EXTGRP:'):
            grp = line[8:].strip()
            continue
        if line.startswith('#KODIPROP:'):
            name, _sep, value = line[10:].partition('=')
            name, value = name.strip(), value.strip()
            low = name.lower()
            if 'license' in low or low.endswith(('.drm', '.drm_legacy')):
                drm = True
            elif name and value:
                props[name] = value
            continue
        if line.startswith('#'):
            continue
        url = _joined(base, line)
        info = info or {'name': ''}
        scheme = url.split(':', 1)[0].lower() if '://' in url else ('file' if url.startswith('/') else '')
        if (scheme in _PLAYABLE and not drm
                and not _VOD.search(url.split('|', 1)[0].split('?', 1)[0])):
            group = live.clean(info.get('group') or grp)
            gi = index.get(group)
            if gi is None:
                gi = index[group] = len(groups)
                groups.append(group)
            name = live.clean(info.get('name')) or url.split('|', 1)[0].rstrip('/').rsplit('/', 1)[-1]
            channels.append([name, info.get('logo') or '', gi, url, info.get('tvg') or '',
                             str(info.get('chno') or ''), opts.get('User-Agent') or '',
                             opts.get('Referer') or '', live.clean(info.get('tvg_name')),
                             props or '', headers or ''])
        info, opts, props, headers, drm, grp = None, {}, {}, {}, False, ''
    return header_epg, groups, channels


def _m3u_path(profile, conf):
    return _cache_file(profile, 'm3u_%s.json' % conf['id'])


def playlist(profile, conf, refresh=False, progress=None):
    """The parsed playlist: the saved copy, or downloaded when there is none."""
    path = _m3u_path(profile, conf)
    if not refresh:
        data = _load(path)
        if data is not None and data.get('v') in _M3U_READ:
            return data
    asked = time.time()
    # a source being added is not saved yet; a saved one may be removed meanwhile
    existed = LS.get_saved(profile, conf['id']) is not None
    # one download of a playlist at a time: a second caller waits and uses it
    lock = _source_lock('m3u:%s' % conf['id'])
    check = _stoppable(progress)
    while not lock.acquire(timeout=0.25):
        # v5.10.110: "Refresh now" behind a background read of the same
        # playlist waited for all of it with Cancel unseen
        if check(0) is False:
            raise LS.SourceError('أُلغي')
    try:
        data = _load(path)
        if (data is not None and data.get('v') in _M3U_READ
                and (not refresh or float(data.get('ts') or 0) >= asked)):
            return data
        tmp = _cache_file(profile, _temp_name('m3u', conf))
        try:
            LS.download(conf['url'], tmp, timeout=DOWNLOAD_TIMEOUT, limit=M3U_LIMIT, progress=check)
            with LS.open_text(tmp) as handle:
                header_epg, groups, channels = parse_m3u(handle, conf['url'])
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass
        if _STOP.is_set():
            raise LS.SourceError('أُلغي')
        if not channels:
            raise LS.SourceError('لا توجد قنوات مباشرة في هذه القائمة')
        # a source being added (asked for with refresh, not saved yet) is
        # kept by its caller; any other must still be saved to be stored
        if LS.get_saved(profile, conf['id']) is None and not (refresh and not existed):
            raise LS.SourceError('المصدر غير موجود')     # removed meanwhile
        data = {'v': M3U_VERSION, 'ts': time.time(), 'epg': header_epg, 'groups': groups,
                'channels': channels, 'mark': _channels_mark(channels)}
        _store(path, data)
    finally:
        lock.release()
    LS.log('playlist read: %d channels in %d groups' % (len(channels), len(groups)))
    return data


def _channels_mark(channels):
    """A mark of what a guide is matched with: the channels' ids and names (v5.10.110).

    A guide keeps only the channels of the playlist it was read for; a
    playlist read again with other channels has it read again too.
    """
    keys = set()
    for channel in channels:
        keys.add('%s\t%s\t%s' % (channel[4], channel[0], channel[8] if len(channel) > 8 else ''))
    return hashlib.sha1('\n'.join(sorted(keys)).encode('utf-8', 'replace')).hexdigest()[:12]


def _members(data):
    members = data.get('_members')
    if members is None or len(members) != len(data.get('groups') or []):
        members = [[] for _group in data.get('groups') or []]
        for idx, channel in enumerate(data.get('channels') or []):
            try:
                members[channel[2]].append(idx)
            except Exception:
                continue
        data['_members'] = members
    return members


def _m3u_rows(source, app, changed):
    conf = source['conf']
    data = playlist(app.profile, conf)
    if data.get('v') != M3U_VERSION or time.time() - float(data.get('ts') or 0) > M3U_TTL:
        _refresh_playlist(app, conf, source['key'], changed)
    guide_url = conf.get('epg') or data.get('epg') or ''
    if guide_url:
        _refresh_guide(app, conf, data, guide_url, source['key'], changed)
    card = app.media_path('channel_card.jpg')
    members = _members(data)
    out = []
    named = [(gi, group) for gi, group in enumerate(data['groups']) if members[gi]]
    for gi, group in named:
        title = live.clean(group) or app.tr('بدون مجموعة')
        params = {'live_src': source['key'], 'group': gi, 'start': 0, 'label': title}
        out.append({'key': 'live:%s:g:%d' % (source['key'], gi), 'title': title,
                    'subtitle': app.tr('عدد القنوات: %d') % len(members[gi]), 'params': params,
                    'loader': _m3u_loader(app, params, card)})
    if len(named) > 1:
        title = app.tr('كل القنوات')
        params = {'live_src': source['key'], 'group': -1, 'start': 0, 'label': title}
        out.append({'key': 'live:%s:all' % source['key'], 'title': title,
                    'subtitle': app.tr('عدد القنوات: %d') % len(data['channels']), 'params': params,
                    'loader': _m3u_loader(app, params, card)})
    return out


def _m3u_loader(app, params, card):
    def load(patience=0.0):
        return m3u_tiles(app, params, card)
    return load


def m3u_tiles(app, params, card, count=LS.ROW_LIMIT):
    src = params['live_src']
    conf = _conf(app, src)
    data = playlist(app.profile, conf)
    members = _members(data)
    gi = int(params.get('group', -1))
    ids = members[gi] if 0 <= gi < len(members) else range(len(data['channels']))
    start = int(params.get('start') or 0)
    guide_data = _epg_data(app.profile, conf)
    groups = data.get('groups') or []
    tiles = []
    for idx in ids[start:start + count]:
        channel = (list(data['channels'][idx]) + [''] * 11)[:11]
        group = groups[channel[2]] if isinstance(channel[2], int) and channel[2] < len(groups) else ''
        tiles.append(_m3u_tile(src, channel, card, group or params.get('label') or '', guide_data))
    more = dict(params, start=start + count) if start + count < len(ids) else None
    return tiles, more


def _m3u_tile(src, channel, card, group, guide_data):
    name, logo, _gi, url, tvg, number, agent, referrer, alias, props, extra = channel
    uid = hashlib.sha1(str(url).encode('utf-8', 'replace')).hexdigest()[:16]
    headers = dict(extra) if isinstance(extra, dict) else {}
    if agent:
        LS.set_header(headers, 'User-Agent', agent)
    if referrer:
        LS.set_header(headers, 'Referer', referrer)
    stream = {'url': url, 'headers': headers}
    if isinstance(props, dict) and props:
        stream['props'] = dict(props)
    epg = {'tvg': tvg, 'name': name}
    if alias:
        epg['tvg_name'] = alias
    tile = LS.channel_tile(src, uid, name, card, logo=logo, group=group, number=number,
                           stream=stream, epg=epg)
    if str(url).lower().startswith('plugin://'):
        # another add-on's entry: Kodi only knows it by the address that
        # add-on resolves it to, so it plays at once, full screen
        tile['nopreview'] = True
    if guide_data:
        LS.apply_guide(tile, _m3u_guide_items(guide_data, tile['epg']))
    return tile


def _refresh_playlist(app, conf, key, changed):
    job = 'm3u:%s' % conf['id']
    if _blocked(job):
        return

    def run():
        try:
            playlist(app.profile, conf, refresh=True)
            _failed.pop(job, None)
        except Exception as exc:
            if not _STOP.is_set():
                _failed_now(job, exc)
            LS.log('playlist not refreshed: %s' % LS.failure(exc), xbmc.LOGWARNING)
            return
        if changed is not None and not _STOP.is_set():
            changed(key)
    _background(job, run)


# ----------------------------------------------------------------- XMLTV
def _epg_path(profile, conf):
    return _cache_file(profile, 'epg_%s.json' % conf['id'])


def _epg_data(profile, conf):
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return None
    data = _load(_epg_path(profile, conf))
    return data if data and data.get('v') == 1 else None


def _url_mark(url):
    return hashlib.sha1(str(url).encode('utf-8', 'replace')).hexdigest()[:12]


def _refresh_guide(app, conf, data, url, key, changed, force=False):
    """Read the guide again in the background when it is old, of another
    address or of other channels; ``force`` ("Refresh now"): read it anyway,
    an earlier failure too (v5.10.110)."""
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return
    job = 'epg:%s' % conf['id']
    if not force:
        current = _epg_data(app.profile, conf)
        # v5.10.110: the guide keeps only the channels of the playlist it was
        # read for: a playlist with other channels has it read again
        if (current and current.get('src') == _url_mark(url)
                and current.get('pl') == data.get('mark')
                and time.time() - float(current.get('ts') or 0) < EPG_TTL):
            return
        if _blocked(job):
            return

    def run():
        try:
            guide = read_xmltv(app.profile, conf, data, url)
            _failed.pop(job, None)
        except Exception as exc:
            if not _STOP.is_set():
                _failed_now(job, exc)
            LS.log('programme guide of a playlist not read: %s' % LS.failure(exc), xbmc.LOGWARNING)
            return
        if guide is not None and changed is not None and not _STOP.is_set():
            changed(key)
    _background(job, run)


_XMLTV_TIME = re.compile(r'^(\d{12})(\d{2})?\s*(?:([+-])(\d{2}):?(\d{2}))?')


def xmltv_time(text):
    match = _XMLTV_TIME.match(str(text or '').strip())
    if not match:
        return 0
    stamp, seconds, sign, hours, minutes = match.groups()
    try:
        moment = datetime.strptime(stamp + (seconds or '00'), '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    except Exception:
        return 0
    offset = 0
    if sign:
        offset = (int(hours) * 60 + int(minutes)) * 60 * (1 if sign == '+' else -1)
    return int(moment.timestamp()) - offset


def read_xmltv(profile, conf, data, url, budget=EPG_BUDGET):
    """Read the guide of the playlist's channels (streamed; the hours around now).

    None when the Home closed meanwhile or the source was removed: nothing is
    saved then. A guide cut short by ``budget`` is saved, and read again
    after EPG_RETRY instead of EPG_TTL.
    """
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return None
    with _source_lock('epg:%s' % conf['id']):
        return _read_xmltv(profile, conf, data, url, budget)


def _aliases(tvg_name):
    """The names a channel's tvg-name matches a guide's display-name by (v5.10.110):
    as written, and with '_' for spaces as IPTV Simple writes them."""
    out = []
    for name in (live.clean(tvg_name).lower(), live.clean(str(tvg_name or '').replace('_', ' ')).lower()):
        if name and name not in out:
            out.append(name)
    return out


def _settle(items, low, high):
    """A channel's programmes in time order, each without a stop time ending
    where the next one starts (v5.10.110): stop is optional in XMLTV."""
    items.sort(key=lambda item: item[0])
    out = []
    for position, item in enumerate(items):
        start, end = item[0], item[1]
        if not end:
            later = [other[0] for other in items[position + 1:position + 4] if other[0] > start]
            end = later[0] if later else start + EPG_OPEN_LAST
            item = [start, end] + list(item[2:])
        if start < end and end > low and start < high:
            out.append(item)
    return out[:EPG_PER_CHANNEL]


def _read_xmltv(profile, conf, data, url, budget):
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return None
    want_ids = set()
    want_names = set()
    for channel in data.get('channels') or []:
        if channel[4]:
            want_ids.add(str(channel[4]).strip().lower())
        want_names.add(str(channel[0]).strip().lower())
        # v5.10.110: tvg-name too, as IPTV Simple matches a guide
        want_names.update(_aliases(channel[8] if len(channel) > 8 else ''))
    if LS.get_saved(profile, conf['id']) is None:
        return None             # a guide is only read for a saved source
    tmp = _cache_file(profile, _temp_name('epg', conf))
    now = time.time()
    low, high = now - EPG_BEFORE, now + EPG_AFTER
    keep, names_of, programmes = {}, {}, {}
    cut = broken = False
    started = time.monotonic()
    seen = 0
    try:
        LS.download(url, tmp, timeout=DOWNLOAD_TIMEOUT, limit=EPG_LIMIT, progress=_stoppable())
        # the budget is for reading: a slow download has its own timeout
        started = time.monotonic()
        try:
            with LS.open_binary(tmp) as handle:
                root = None
                for event, elem in ET.iterparse(handle, events=('start', 'end')):
                    if root is None:
                        root = elem
                        continue
                    if event != 'end':
                        continue
                    tag = elem.tag
                    if tag == 'channel':
                        xid = (elem.get('id') or '').strip().lower()
                        names = [live.clean(node.text).lower() for node in elem.findall('display-name')
                                 if node.text]
                        wanted = xid in want_ids or any(n in want_names for n in names)
                        keep[xid] = wanted
                        if wanted:
                            names_of[xid] = names
                        root.clear()
                    elif tag == 'programme':
                        xid = (elem.get('channel') or '').strip().lower()
                        if keep.get(xid, xid in want_ids):
                            start, end = xmltv_time(elem.get('start')), xmltv_time(elem.get('stop'))
                            # v5.10.110: one without a stop time is kept; its end
                            # is the next start (_settle)
                            if start and start < high and (end > low if end else start > low - EPG_OPEN_BEFORE):
                                items = programmes.setdefault(xid, [])
                                if len(items) < EPG_PER_CHANNEL * 3:
                                    items.append([start, end, (elem.findtext('title') or '')[:200],
                                                  (elem.findtext('desc') or '')[:700],
                                                  (elem.findtext('sub-title') or '')[:120]])
                        root.clear()
                    seen += 1
                    if seen % 256:
                        continue
                    if _STOP.is_set():
                        LS.log('programme guide left unread: the Home closed')
                        return None
                    if time.monotonic() - started > budget:
                        LS.log('programme guide cut short after %d s' % int(budget), xbmc.LOGWARNING)
                        cut = True
                        break
        except (ET.ParseError, EOFError, OSError, zlib.error) as exc:
            # v5.10.110: a guide damaged further on (a stray '&', a cut .gz)
            # keeps what was read before; all of it used to be thrown away
            # and the failure shown as a network one
            LS.log('programme guide damaged after %d elements: %s' % (seen, type(exc).__name__),
                   xbmc.LOGWARNING)
            broken = True
    except LS.SourceError:
        if _STOP.is_set():
            return None
        raise
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    if _STOP.is_set() or LS.get_saved(profile, conf['id']) is None:
        return None             # the Home closed, or the source was removed meanwhile
    count = 0
    for xid in list(programmes):
        items = _settle(programmes[xid], low, high)
        if items:
            programmes[xid] = items
            count += len(items)
        else:
            del programmes[xid]
    if broken and not count:
        raise BadGuide('دليل البرامج تالف أو غير مكتمل')
    cut = cut or broken
    name_map = {}
    for xid, names in names_of.items():
        for name in names:
            name_map.setdefault(name, xid)
    # a guide cut short is read again sooner (see _refresh_guide)
    stamp = now - EPG_TTL + EPG_RETRY if cut else now
    guide_data = {'v': 1, 'ts': stamp, 'src': _url_mark(url), 'pl': data.get('mark'),
                  'ch': programmes, 'names': name_map}
    if cut:
        guide_data['partial'] = True
    _store(_epg_path(profile, conf), guide_data)
    LS.log('programme guide read: %d programmes for %d channels in %.1f s'
           % (count, len(programmes), time.monotonic() - started))
    return guide_data


def _m3u_guide_items(guide_data, keys):
    if not guide_data:
        return []
    table = guide_data.get('ch') or {}
    xid = str(keys.get('tvg') or '').strip().lower()
    items = table.get(xid) if xid else None
    if items is None:
        names = guide_data.get('names') or {}
        # v5.10.110: by tvg-name first (as IPTV Simple), then by the title
        for name in _aliases(keys.get('tvg_name')) + [str(keys.get('name') or '').strip().lower()]:
            mapped = names.get(name)
            if mapped and table.get(mapped) is not None:
                items = table[mapped]
                break
    out = []
    for item in items or []:
        item = (list(item) + [''] * 5)[:5]
        programme = LS.programme(item[2], item[0], item[1], plot=item[3], episode=item[4])
        if programme:
            out.append(programme)
    return out


# ---------------------------------------------------------------- Xtream
def _xt_base(conf):
    return str(conf.get('server') or '').rstrip('/')


def xt_api(conf, timeout=15, **params):
    query = [('username', conf.get('username') or ''), ('password', conf.get('password') or '')]
    query.extend((k, v) for k, v in params.items() if v not in (None, ''))
    return LS.http_json('%s/player_api.php?%s' % (_xt_base(conf), urlencode(query)), timeout=timeout)


def _refused(user):
    return str(user.get('auth', 1)) in ('0', 'false', 'False')


def login(conf):
    """Check the account; keeps the stream format it may use in ``conf``."""
    reply = xt_api(conf, timeout=20)
    user = reply.get('user_info') if isinstance(reply, dict) else None
    if not isinstance(user, dict) or _refused(user):
        raise Refused('رفض السيرفر اسم المستخدم أو كلمة المرور')
    status = str(user.get('status') or '').strip().lower()
    if status and status != 'active':
        raise Refused('الاشتراك غير فعّال على السيرفر')
    formats = [str(f).lower() for f in user.get('allowed_output_formats') or []]
    conf['fmt'] = 'ts' if (not formats or 'ts' in formats) else ('m3u8' if 'm3u8' in formats else formats[0])
    return user


def _xt_list(reply):
    """The list an Xtream panel answered with; None for an empty answer (v5.10.110).

    An account refused since (expired, banned, password changed) answers
    with its user_info instead of a list: that is said, no longer kept for
    hours as "no channels". A list sent as an object ({"0": {...}}) is a list.
    """
    if isinstance(reply, list):
        return reply
    if not reply:
        return None
    if isinstance(reply, dict):
        user = reply.get('user_info')
        if isinstance(user, dict):
            if _refused(user):
                raise Refused('رفض السيرفر اسم المستخدم أو كلمة المرور')
            status = str(user.get('status') or '').strip().lower()
            if status and status != 'active':
                raise Refused('الاشتراك غير فعّال على السيرفر')
        elif all(isinstance(value, dict) for value in reply.values()):
            return list(reply.values())
    raise LS.SourceError('رد السيرفر غير مفهوم')


def categories(profile, conf, refresh=False):
    path = _cache_file(profile, 'xt_%s_cats.json' % conf['id'])
    data = None if refresh else _load(path)
    # One refresh upgrades caches whose names were erased by the old tag
    # cleaner. Keep the old copy available if the panel is temporarily down.
    if data and data.get('labels_version') == 2 and time.time() - float(data.get('ts') or 0) < XT_CATS_TTL:
        return data.get('cats') or []
    try:
        reply = _xt_list(xt_api(conf, action='get_live_categories'))
    except Refused:
        raise
    except Exception:
        if data:
            return data.get('cats') or []
        raise
    if reply is None:
        # an empty answer is not kept: the next build asks again
        return (data or {}).get('cats') or []
    cats = []
    for cat in reply:
        if isinstance(cat, dict) and cat.get('category_id') not in (None, ''):
            cats.append({'id': str(cat.get('category_id')), 'name': live.clean(cat.get('category_name'))})
    _store(path, {'ts': time.time(), 'cats': cats, 'labels_version': 2})
    return cats


# v5.10.117: an account's channels in one answer. kodi.log on a Ugoos had
# five category rows of one panel asked for at once, each answering nothing
# after 12 seconds; a panel takes one request at a time from an account
# (and some turn away a burst). The account's whole live list is asked for
# once an hour, one request, and kept per category: every category row of
# the account reads its own file. A panel too large for that (or one that
# fails it) is asked per category, one request at a time.
XT_FULL_LIMIT = 25 * 1024 * 1024
_xt_hosts = {}          # panel host -> the lock its requests take turns on
_xt_hosts_lock = threading.Lock()
_xt_full_tried = {}     # account id -> time its whole list was last asked for


def _xt_host_lock(conf):
    host = (urlsplit(_xt_base(conf)).netloc or str(conf.get('id') or '')).lower()
    with _xt_hosts_lock:
        lock = _xt_hosts.get(host)
        if lock is None:
            lock = _xt_hosts[host] = threading.Lock()
        return lock


def _xt_streams_path(profile, conf, category_id):
    safe_id = re.sub(r'[^A-Za-z0-9]', '_', str(category_id))[:40]
    return _cache_file(profile, 'xt_%s_s_%s.json' % (conf['id'], safe_id))


def _xt_row(item):
    """A channel as kept: [name, logo, id, number, guide id]; None for a
    film, an episode or a broken entry."""
    if not isinstance(item, dict) or item.get('stream_id') in (None, ''):
        return None
    if str(item.get('stream_type') or 'live').lower() not in ('live', 'created_live', 'radio_streams', ''):
        return None
    return [live.clean(item.get('name')), str(item.get('stream_icon') or ''),
            str(item.get('stream_id')), str(item.get('num') or ''),
            str(item.get('epg_channel_id') or '')]


def _xt_full(profile, conf):
    """Every live channel of the account in one request, written per
    category; True when it was. Asked for at most once an hour."""
    now = time.time()
    if now - _xt_full_tried.get(conf['id'], 0) < XT_STREAMS_TTL:
        return False
    _xt_full_tried[conf['id']] = now
    query = [('username', conf.get('username') or ''), ('password', conf.get('password') or ''),
             ('action', 'get_live_streams')]
    raw = LS.http_bytes('%s/player_api.php?%s' % (_xt_base(conf), urlencode(query)),
                        {'Accept': 'application/json'}, 30, limit=XT_FULL_LIMIT)
    try:
        reply = json.loads(raw.decode('utf-8-sig', 'replace') or 'null')
    except Exception:
        raise LS.SourceError('رد السيرفر غير مفهوم') from None
    del raw
    reply = _xt_list(reply)
    if not reply:
        return False
    by_cat = {}
    for item in reply:
        row = _xt_row(item)
        if row is None:
            continue
        cats = item.get('category_ids')
        if not isinstance(cats, list) or not cats:
            cats = [item.get('category_id')]
        for cid in cats:
            if cid not in (None, ''):
                by_cat.setdefault(str(cid), []).append(row)
    del reply
    if not by_cat:
        return False
    stamp = time.time()
    known = set(by_cat)
    try:
        known.update(str(cat.get('id')) for cat in categories(profile, conf) if cat.get('id'))
    except Exception:
        pass
    for cid in known:
        LS.write_json(_xt_streams_path(profile, conf, cid), {'ts': stamp, 'items': by_cat.get(cid) or []})
    return True


def _whole(profile, conf, path):
    """The category's channels out of the account's whole list (None when
    that list could not be had)."""
    try:
        if _xt_full(profile, conf):
            return (_load(path) or {}).get('items') or []
    except Refused:
        raise
    except Exception as exc:
        xbmc.log('[DexHub] live: the account\'s whole channel list failed (%s)' % LS.clean_trace(exc)[:200],
                 xbmc.LOGINFO)
    return None


def streams(profile, conf, category_id, whole_first=False):
    """A category's channels. v5.10.133: the category alone is asked for
    (the guide opens one category at a time: the account's whole list, tens
    of thousands of channels on a big panel, is no longer read to show one);
    the whole list only for the search across the account (whole_first) or
    when the panel will not list a category alone."""
    path = _xt_streams_path(profile, conf, category_id)
    data = _load(path)
    if data and time.time() - float(data.get('ts') or 0) < XT_STREAMS_TTL:
        return data.get('items') or []
    with _xt_host_lock(conf):
        # another row of the account may have read it while this one waited
        data = _load(path) or data
        if data and time.time() - float(data.get('ts') or 0) < XT_STREAMS_TTL:
            return data.get('items') or []
        if whole_first:
            items = _whole(profile, conf, path)
            if items is not None:
                return items
        try:
            reply = _xt_list(xt_api(conf, timeout=25, action='get_live_streams', category_id=category_id))
        except Refused:
            raise
        except Exception:
            if data:
                return data.get('items') or []
            if not whole_first:
                items = _whole(profile, conf, path)
                if items is not None:
                    return items
            raise
        if reply is None:
            return (data or {}).get('items') or []
        items = [row for row in (_xt_row(item) for item in reply) if row is not None]
        _store(path, {'ts': time.time(), 'items': items})
        return items


def _xt_rows(source, app, changed):
    conf = source['conf']
    card = app.media_path('channel_card.jpg')
    out = []
    for cat in categories(app.profile, conf):
        title = live.clean(cat.get('name')) or app.tr('قنوات')     # v5.10.199: a list kept before is made readable too
        params = {'live_src': source['key'], 'category': cat['id'], 'start': 0, 'label': title}
        out.append({'key': 'live:%s:c:%s' % (source['key'], cat['id']), 'title': title, 'subtitle': '',
                    'params': params, 'loader': _xt_loader(app, params, card), 'epg': True})
    return out


def _xt_loader(app, params, card):
    def load(patience=0.0):
        return xt_tiles(app, params, card)
    return load


def xt_tiles(app, params, card, count=LS.ROW_LIMIT):
    src = params['live_src']
    conf = _conf(app, src)
    items = streams(app.profile, conf, params['category'])
    start = int(params.get('start') or 0)
    tiles = []
    for item in items[start:start + count]:
        name, icon, sid, number, _epg_id = (list(item) + [''] * 5)[:5]
        tile = LS.channel_tile(src, sid, name, card, logo=icon, group=params.get('label') or '',
                               number=number, stream={'id': sid}, epg={'xt': sid})
        cached = _xt_cached(conf, sid)
        if cached:
            LS.apply_guide(tile, cached)
        tiles.append(tile)
    more = dict(params, start=start + count) if start + count < len(items) else None
    return tiles, more


def _b64(value):
    """The text of a base64 field; the field itself when it is not base64.

    v5.10.110: Xtream pads its base64 to whole groups of four, so a plain
    title such as 'TV' is no longer decoded (it gave 'M').
    """
    text = str(value or '')
    if not text:
        return ''
    if len(text) % 4:
        return text
    try:
        return base64.b64decode(text, validate=True).decode('utf-8')
    except Exception:
        return text


def _all_b64(values):
    """Do all these fields read as base64? (one answer is all coded, or none is)"""
    for value in values:
        text = str(value or '')
        if text and _b64(text) == text:
            return False
    return True


def _xt_cached(conf, sid):
    with _xt_epg_lock:
        hit = _xt_epg.get((conf.get('id'), str(sid)))
    if hit and time.time() - hit[0] < XT_EPG_TTL:
        return hit[1]
    return None


def xt_short_epg(conf, sid, limit=4):
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    if not sid:
        return []
    cached = _xt_cached(conf, sid) if limit <= 4 else None
    if cached is not None:
        return cached
    with _xt_host_lock(conf):
        # one request at a time to a panel (v5.10.117)
        reply = xt_api(conf, timeout=8, action='get_short_epg', stream_id=sid, limit=limit)
    listings = reply.get('epg_listings') if isinstance(reply, dict) else None
    listings = [entry for entry in listings or [] if isinstance(entry, dict)]
    # v5.10.110: a panel that sends plain titles sends them all plain: one
    # title that is not base64 tells for the whole answer
    titles = _all_b64(entry.get('title') for entry in listings)
    plots = _all_b64(entry.get('description') for entry in listings)
    items = []
    for entry in listings:
        try:
            # v5.10.110: '1700000000.0' is a time too
            start = int(float(entry.get('start_timestamp') or 0))
            end = int(float(entry.get('stop_timestamp') or entry.get('end_timestamp') or 0))
        except Exception:
            start = end = 0
        title, plot = entry.get('title'), entry.get('description')
        programme = LS.programme(_b64(title) if titles else title, start, end,
                                 plot=_b64(plot) if plots else plot)
        if programme and programme['end'] > programme['start']:
            items.append(programme)
    items.sort(key=lambda p: p['start'])
    if limit <= 4:
        with _xt_epg_lock:
            if len(_xt_epg) > 3000:
                _xt_epg.clear()
            _xt_epg[(conf.get('id'), str(sid))] = (time.time(), items)
    return items


# ----------------------------------------------------------------- setup
def _progress_call(tr, text, work):
    """Run ``work(progress)`` under Kodi's progress dialog (Cancel stops it)."""
    dialog = xbmcgui.DialogProgress()
    dialog.create('Dex Hub', tr(text))

    def progress(size):
        try:
            if size:
                dialog.update(0, '%s\n%.1f MB' % (tr(text), size / 1048576.0))
            return not dialog.iscanceled()
        except Exception:
            return True
    try:
        return work(progress)
    finally:
        try:
            dialog.close()
        except Exception:
            pass


def _host(url):
    try:
        return urlsplit(str(url or '').split('|', 1)[0]).hostname or ''
    except ValueError:
        return ''


def _ask(heading, default='', hidden=False, private=False):
    """What was typed in Kodi's keyboard; None when it was cancelled (v5.10.110).

    Dialog().input() answers '' both for Cancel and for an empty OK, so a
    cancelled step could not stop the setup: the keyboard tells them apart.
    ``private``: account data (an address, a username), for which the skin
    asks no keyboard suggestions (kb_private).
    """
    from .. import kb_private
    keyboard = xbmc.Keyboard(default or '', heading, bool(hidden))
    if private or hidden:
        with kb_private.private():
            keyboard.doModal()
    else:
        keyboard.doModal()
    if not keyboard.isConfirmed():
        return None
    text = keyboard.getText() or ''
    return text if hidden else text.strip()


# an address typed without its scheme: 'example.com/list.m3u', '10.0.0.2:8080'
_BARE_HOST = re.compile(r'^([A-Za-z0-9.-]+)(:\d+)?(?:[/?]|$)')
_FILE_NAME = re.compile(r'\.(?:xml|m3u8?|gz|xz|zip|txt|json|php|ts)$', re.I)


def _with_scheme(text):
    """``text`` with http:// before it when it is an address typed without one
    (v5.10.110); a file name ('guide.xml') or a word stays as it is."""
    match = _BARE_HOST.match(text) if '://' not in text else None
    if not match:
        return text
    host, port = match.group(1), match.group(2)
    if port or host.lower() == 'localhost' or ('.' in host and not _FILE_NAME.search(host)):
        return 'http://' + text
    return text


def _ask_address(tr, heading, optional=False):
    """An http(s) address typed in: None when cancelled (or a required one
    is left empty), '' when an optional one is left empty.

    v5.10.110: a mistyped address is asked for again with what was typed,
    and nothing typed before it is lost (a bad guide address used to end the
    whole setup).
    """
    text, prompt = '', heading
    while True:
        text = _ask(prompt, text, private=True)
        if text is None:
            return None
        if not text:
            return '' if optional else None
        try:
            # v5.10.110: an address typed without http:// is an http one
            return live.valid_url(_with_scheme(text))
        except ValueError:
            prompt = tr('رابط غير صالح، صححه أو اتركه فارغاً') if optional else tr('رابط غير صالح، صححه')


def _address_mark(address):
    """An address as compared for a duplicate: scheme, host, port, path and query."""
    plain = str(address or '').split('|', 1)[0].strip()
    try:
        parts = urlsplit(plain)
        port = parts.port or {'http': 80, 'https': 443}.get(parts.scheme)
    except ValueError:
        return plain.lower()
    return '%s://%s:%s%s?%s' % (parts.scheme, parts.hostname or '', port, parts.path.rstrip('/'),
                                parts.query)


def _same_source(profile, kind, address, username=''):
    """A source saved before with this playlist, or this server and user (v5.10.110)."""
    mark = _address_mark(address)
    for conf in LS.saved(profile):
        if conf.get('kind') != kind:
            continue
        if kind == 'm3u' and _address_mark(conf.get('url')) == mark:
            return conf
        if (kind == 'xtream' and _address_mark(conf.get('server')) == mark
                and str(conf.get('username') or '') == username):
            return conf
    return None


def _open_existing(app, dialog, conf):
    """The tab key of a source added before, when the user wants it; else ''."""
    label = live.clean(conf.get('name')) or _host(conf.get('url') or conf.get('server'))
    if dialog.yesno('Dex Hub', app.tr('هذا المصدر مضاف من قبل باسم "%s". تفتحه؟') % label):
        return ('m3u:%s' if conf.get('kind') == 'm3u' else 'xt:%s') % conf['id']
    return ''


def add_m3u(app):
    """Ask for a playlist (and its guide), read it, keep it: its source key."""
    tr, dialog = app.tr, xbmcgui.Dialog()
    # v5.10.110: Cancel at any step ends the setup (an empty name keeps the
    # one offered); the address sellers send for an Xtream account
    # (get.php?username=...) is a playlist as it is
    url = _ask_address(tr, tr('رابط قائمة M3U'))
    if not url:
        return ''
    same = _same_source(app.profile, 'm3u', url)
    if same is not None:
        return _open_existing(app, dialog, same)
    guide_url = _ask_address(tr, tr('رابط دليل البرامج XMLTV (اختياري)'), optional=True)
    if guide_url is None:
        return ''
    name = _ask(tr('اسم يظهر في شريط المصادر'), _host(url))
    if name is None:
        return ''
    conf = {'id': uuid.uuid4().hex[:10], 'kind': 'm3u', 'name': name or _host(url), 'url': url,
            'epg': guide_url, 'added': int(time.time())}
    try:
        data = _progress_call(tr, 'جاري تحميل القائمة…',
                              lambda progress: playlist(app.profile, conf, refresh=True, progress=progress))
    except Exception as exc:
        dialog.ok('Dex Hub', '%s\n%s' % (tr('تعذر تحميل القائمة'), tr(LS.safe(exc))))
        return ''
    LS.save_source(app.profile, conf)
    LS.log('M3U playlist added (%d channels)' % len(data.get('channels') or []))
    dialog.notification('Dex Hub', tr('أُضيفت القائمة: %d قناة') % len(data.get('channels') or []),
                        xbmcgui.NOTIFICATION_INFO, 3000, sound=False)
    return 'm3u:%s' % conf['id']


def _xt_server(text):
    """(server, username, password) from what was typed as the server (v5.10.110).

    The address sellers send, http://host:port/get.php?username=U&password=P&type=m3u_plus,
    brings the account with it; any other query is no server address.
    """
    text = str(text or '').strip()
    if '://' not in text:
        text = 'http://' + text
    if '|' in text:
        raise ValueError('url')
    parts = urlsplit(live.valid_url(text))
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if parts.fragment or (parts.query and 'username' not in query and 'password' not in query):
        raise ValueError('url')
    path = parts.path.rstrip('/')
    for suffix in ('/get.php', '/player_api.php', '/xmltv.php', '/panel_api.php'):
        if path.lower().endswith(suffix):
            path = path[:-len(suffix)]
            break
    server = urlunsplit((parts.scheme, parts.netloc, path.rstrip('/'), '', ''))
    return server, (query.get('username') or '').strip(), query.get('password') or ''


def add_xtream(app):
    """Ask for an Xtream Codes account, check it, keep it: its source key."""
    tr, dialog = app.tr, xbmcgui.Dialog()
    # v5.10.110: Cancel at any step ends the setup; a mistyped server is asked
    # for again with what was typed, and the seller's get.php address fills
    # the username and the password (only what is missing is asked for)
    text, prompt = '', tr('عنوان السيرفر مع المنفذ، مثل http://example.com:8080')
    while True:
        text = _ask(prompt, text, private=True)
        if not text:
            return ''
        try:
            server, username, password = _xt_server(text)
            break
        except ValueError:
            prompt = tr('رابط غير صالح، صححه')
    if not username:
        username = _ask(tr('اسم المستخدم'), private=True)
        if not username:
            return ''
    same = _same_source(app.profile, 'xtream', server, username)
    if same is not None:
        return _open_existing(app, dialog, same)
    if not password:
        password = _ask(tr('كلمة المرور'), hidden=True)
        if not password:
            return ''
    name = _ask(tr('اسم يظهر في شريط المصادر'), _host(server))
    if name is None:
        return ''
    conf = {'id': uuid.uuid4().hex[:10], 'kind': 'xtream', 'name': name or _host(server),
            'server': server, 'username': username, 'password': password, 'added': int(time.time())}

    def work(progress):
        login(conf)
        # v5.10.110: Cancel did nothing here; it is seen between the requests
        if progress(0) is False:
            raise LS.SourceError('أُلغي')
        categories(app.profile, conf, refresh=True)
    try:
        _progress_call(tr, 'جاري التحقق من الاشتراك…', work)
    except Exception as exc:
        dialog.ok('Dex Hub', '%s\n%s' % (tr('تعذر ربط الاشتراك'), tr(LS.safe(exc))))
        LS.drop_cache(app.profile, conf['id'])
        return ''
    LS.save_source(app.profile, conf)
    LS.log('Xtream account added')
    dialog.notification('Dex Hub', tr('أُضيف الاشتراك'), xbmcgui.NOTIFICATION_INFO, 2500, sound=False)
    return 'xt:%s' % conf['id']


def refresh(app, conf, changed=None):
    """Read a source again now (the playlist and its guide, or the categories)."""
    tr = app.tr
    if conf.get('kind') == 'xtream':
        _progress_call(tr, 'جاري تحديث القنوات…', lambda progress: categories(app.profile, conf, refresh=True))
        # v5.10.110: the channels of each category are dropped only now that
        # the server answered; a refresh while it was down emptied every row
        folder = LS.cache_dir(app.profile)
        for name in list(os.listdir(folder)):
            if name.startswith('xt_%s_s_' % conf['id']) and name.endswith('.json'):
                try:
                    os.remove(os.path.join(folder, name))
                except Exception:
                    pass
        with _xt_epg_lock:
            for key in [k for k in _xt_epg if k[0] == conf['id']]:
                _xt_epg.pop(key, None)
        return
    data = _progress_call(tr, 'جاري تحديث القائمة…',
                          lambda progress: playlist(app.profile, conf, refresh=True, progress=progress))
    # v5.10.110: the guide on hand stays until the new one is read (it used
    # to be deleted first), and a failure a few minutes ago no longer holds
    # this read back
    guide_url = conf.get('epg') or data.get('epg') or ''
    if guide_url:
        _refresh_guide(app, conf, data, guide_url, 'm3u:%s' % conf['id'], changed, force=True)
