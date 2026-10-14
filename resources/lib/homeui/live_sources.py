# -*- coding: utf-8 -*-
"""The sources of the Live TV page (v5.10.104).

The Live TV page shows one source at a time, chosen in its tab bar:

  pvr       Kodi's own PVR (IPTV Simple or any PVR client), as in 5.10.103
  st:<id>   a Stremio add-on installed in Dex Hub whose catalogs hold TV
            channels (catalog type tv, channel, channels, live or iptv): its
            catalogs and their genres are the rows (live_stremio)
  dw        the DexWorld subscription whose key is saved in Dex Hub's
            settings, when the DexWorld IPTV add-on is not installed as a
            source already: the same catalogs, with the programme guide
  m3u:<id>  an M3U playlist kept by Dex Hub itself: its groups are the rows,
            and an XMLTV guide gives now and next (live_iptv)
  xt:<id>   an Xtream Codes account read through its own API: its live
            categories are the rows, its short guide gives now and next
  setup     adding, refreshing and removing sources

Every source makes the same channel tiles (channel_tile). A channel that is
not a PVR channel has a stream address of its own, looked up when the cursor
rests on it (resolve), and the preview director plays it like a trailer that
behaves like a channel (see director.py).

Nothing here touches the GUI except the setup dialogs, which the page calls
on its own thread. IPTV addresses carry the account (Xtream puts the password
in the path, DexWorld the key), so no address is ever logged or shown:
failures are described by SourceError texts that hold no address.
"""
import codecs
import errno
import gzip
import io
import json
import os
import re
import threading
import time
from .. import lazy_tb as traceback
import uuid
import zlib
from urllib.parse import parse_qsl, urlencode, urlsplit

import xbmc

from . import live

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
LIVE_TYPES = ('tv', 'channel', 'channels', 'live', 'iptv')
ROW_LIMIT = live.ROW_LIMIT
PAGE_SIZE = live.PAGE_SIZE
_SAVED = 'live_sources.json'
_TABS = 'page_tabs.json'
_lock = threading.RLock()


class SourceError(Exception):
    """A failure whose text is safe to log and to show: it holds no address."""


class TooLarge(SourceError):
    """A file or a reply larger than allowed (v5.10.110): asking again soon
    would only download it again."""


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] live: %s' % msg, level)


# ------------------------------------------------------------------ files
def cache_dir(profile):
    path = os.path.join(profile, 'live_cache')
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return default


def write_json(path, data, private=True):
    # v5.10.110: a temporary name of its own for each write (two threads
    # storing the same cache moved each other's file away), private before it
    # shows, and never left behind when the write fails
    tmp = '%s.%s.tmp' % (path, uuid.uuid4().hex[:8])
    try:
        try:
            _dump(tmp, data, False)
        except UnicodeEncodeError:
            # a lone surrogate (a name cut inside an emoji) is no UTF-8:
            # escaped, the file is still written
            _dump(tmp, data, True)
        if private:
            try:
                os.chmod(tmp, 0o600)
            except Exception:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _dump(path, data, ascii_only):
    text = json.dumps(data, ensure_ascii=ascii_only, separators=(',', ':'))
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)


_SWEPT = [0.0]
_LEFTOVERS = ('.download', '.part', '.tmp')


def sweep(profile, age=3600.0):
    """Remove what a download or a write left behind when Kodi stopped or
    crashed meanwhile (v5.10.110; a guide's part file can hold 400 MB).

    At most once an hour, and only files older than ``age``: a file being
    written now is younger.
    """
    now = time.time()
    if now - _SWEPT[0] < age:
        return
    _SWEPT[0] = now
    own = (_SAVED, _TABS, 'live_recent.json')
    for folder, names_of in ((cache_dir(profile), None), (profile, own)):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.endswith(_LEFTOVERS) or (names_of and not name.startswith(names_of)):
                continue
            path = os.path.join(folder, name)
            try:
                if now - os.path.getmtime(path) > age:
                    os.remove(path)
            except OSError:
                pass


# ------------------------------------------------------------------- http
def _pool():
    try:
        from ..dexhub import client
        return client._ensure_pool()
    except Exception:
        return None


# v5.10.110: "HTTP 404" or "HTTP Error 404", never a host name such as
# http808.example
_HTTP_CODE = re.compile(r'\bhttp\s+(?:error\s+)?(\d{3})\b', re.I)


def _describe(exc):
    """A short reason for a network failure, without the address it had."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    code = _HTTP_CODE.search(text)
    if code:
        return _status_text(int(code.group(1)))
    if 'timeout' in name or 'timed out' in text or 'timeout' in text:
        return 'انتهت مهلة الاتصال بالسيرفر'
    if 'ssl' in name or 'certificate' in text or 'ssl' in text:
        return 'مشكلة في شهادة أمان السيرفر'
    if ('name or service' in text or 'nodename' in text or 'getaddrinfo' in text
            or 'resolve' in text or 'no address associated' in text):
        return 'اسم السيرفر غير معروف'
    if 'name resolution' in text:
        # v5.10.110: the name could not be looked up at all (no DNS answer),
        # which says nothing about the name itself
        return 'تعذر البحث عن اسم السيرفر، تأكد من اتصال الإنترنت'
    if 'refused' in text:
        return 'السيرفر رفض الاتصال'
    if 'incompleteread' in text or 'connection broken' in text or 'ended prematurely' in text:
        return 'انقطع التحميل قبل نهايته'
    return 'تعذر الاتصال بالسيرفر'


def _status_text(status):
    if status in (401, 403):
        return 'السيرفر رفض الحساب (HTTP %d)' % status
    if status == 404:
        return 'العنوان غير موجود على السيرفر (HTTP 404)'
    if status == 429:
        return 'السيرفر مشغول، حاول بعد قليل (HTTP 429)'
    return 'رد السيرفر بخطأ (HTTP %d)' % status


# Kodi's own options after '|' that are no request headers
_KODI_OPTIONS = ('seekable', 'verifypeer', 'noshout', 'failonerror', 'customrequest', 'postdata',
                 'redirect-limit', 'active-remote', 'auth', 'sslcipherlist', 'connection-timeout',
                 'acceptencoding', 'encoding', 'httpproxy', 'cookies')
_HEADER_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9-]*$')


def set_header(headers, name, value):
    """Put a header in ``headers``, in place of one of the same name in any case."""
    for key in [k for k in headers if k.lower() == name.lower()]:
        del headers[key]
    headers[name] = value


def split_options(url):
    """(address, request headers) of an address in Kodi's form 'url|Name=value&...'.

    v5.10.110: a playlist or guide address given that way (as Kodi and IPTV
    Simple take it) was requested with the options as part of its path.
    """
    plain, _sep, given = str(url or '').partition('|')
    headers = {}
    for name, value in parse_qsl(given, keep_blank_values=False):
        name = name.strip()
        if _HEADER_NAME.match(name) and name.lower() not in _KODI_OPTIONS:
            set_header(headers, name, value)
    return plain.strip(), headers


def _expected(headers):
    """The body length a reply announces, when its bytes arrive as they are sent."""
    try:
        if (headers.get('Content-Encoding') or 'identity').strip().lower() != 'identity':
            return None
        value = headers.get('Content-Length')
        return int(value) if value not in (None, '') else None
    except Exception:
        return None


def _chunks(url, headers=None, timeout=15, method='GET', body=None):
    """Yield the body of ``url`` in pieces (gzip undone); raise SourceError."""
    url, given = split_options(url)
    merged = {'User-Agent': UA, 'Accept': '*/*'}
    for name, value in list((headers or {}).items()) + list(given.items()):
        if name and value:
            set_header(merged, str(name), str(value))
    pool = _pool()
    if pool is not None:
        try:
            import urllib3
            from urllib3.util.retry import Retry
            sent = dict(merged)
            set_header(sent, 'Accept-Encoding', 'gzip')
            response = pool.request(
                method, url, body=body, headers=sent,
                timeout=urllib3.Timeout(connect=min(10.0, float(timeout)), read=float(timeout)),
                retries=Retry(total=5, connect=1, read=0, redirect=5, status=0,
                              raise_on_redirect=False, raise_on_status=False),
                redirect=True, preload_content=False, decode_content=True)
        except Exception as exc:
            raise SourceError(_describe(exc)) from None
        try:
            if int(response.status or 0) >= 400:
                raise SourceError(_status_text(int(response.status)))
            length, got = _expected(response.headers), 0
            try:
                for piece in response.stream(65536, decode_content=True):
                    if piece:
                        got += len(piece)
                        yield piece
            except SourceError:
                raise
            except Exception as exc:
                raise SourceError(_describe(exc)) from None
            if length is not None and got < length:
                # v5.10.110: the connection dropped before the announced end
                # (urllib3 1.x does not check it): half a playlist is no playlist
                raise SourceError('انقطع التحميل قبل نهايته')
        finally:
            try:
                response.release_conn()
            except Exception:
                pass
        return
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    try:
        handle = urlopen(Request(url, data=body, headers=merged, method=method), timeout=timeout)
    except HTTPError as exc:
        raise SourceError(_status_text(int(exc.code))) from None
    except Exception as exc:
        raise SourceError(_describe(exc)) from None
    try:
        length, got = _expected(handle.headers), 0
        while True:
            try:
                piece = handle.read(65536)
            except Exception as exc:
                raise SourceError(_describe(exc)) from None
            if not piece:
                break
            got += len(piece)
            yield piece
        if length is not None and got < length:
            # v5.10.110: http.client ends a cut body quietly
            raise SourceError('انقطع التحميل قبل نهايته')
    finally:
        try:
            handle.close()
        except Exception:
            pass


def http_bytes(url, headers=None, timeout=15, method='GET', body=None, limit=25 * 1024 * 1024):
    out = io.BytesIO()
    for piece in _chunks(url, headers, timeout, method, body):
        out.write(piece)
        if out.tell() > limit:
            raise TooLarge('الرد أكبر من المسموح')
    data = out.getvalue()
    if data[:2] == b'\x1f\x8b':
        try:
            data = gzip.decompress(data)
        except Exception:
            try:
                data = zlib.decompress(data, 47)
            except Exception:
                pass
    return data


def http_json(url, headers=None, timeout=15, method='GET', payload=None):
    body = None
    hdrs = {'Accept': 'application/json'}
    hdrs.update(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
        hdrs['Content-Type'] = 'application/json'
    raw = http_bytes(url, hdrs, timeout, method, body, limit=40 * 1024 * 1024)
    try:
        # v5.10.110: utf-8-sig: PHP panels often send a BOM before the JSON
        return json.loads(raw.decode('utf-8-sig', 'replace') or 'null')
    except Exception:
        raise SourceError('رد السيرفر غير مفهوم') from None


def download(url, dest, headers=None, timeout=30, limit=200 * 1024 * 1024, progress=None):
    """Save ``url`` to ``dest`` (a playlist or a guide); the byte count."""
    size = 0
    tmp = dest + '.part'
    try:
        with open(tmp, 'wb') as handle:
            for piece in _chunks(url, headers, timeout):
                handle.write(piece)
                size += len(piece)
                if size > limit:
                    raise TooLarge('الملف أكبر من المسموح')
                if progress is not None and progress(size) is False:
                    raise SourceError('أُلغي')
        os.replace(tmp, dest)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return size


def open_text(path):
    """A downloaded file as text lines (a gzip file is opened through gzip).

    v5.10.110: UTF-16 is known by its BOM. Otherwise each line is UTF-8 (BOM
    or not), and a line that is not UTF-8 is read as Windows Arabic (cp1256),
    the encoding of playlists saved on Windows in Arabic: its names used to
    become U+FFFD.
    """
    with open_binary(path) as probe:
        head = probe.read(2)
    if head in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return io.TextIOWrapper(open_binary(path), encoding='utf-16', errors='replace')
    return _TextLines(open_binary(path))


class _TextLines(object):
    """The lines of a binary file as text, decoded one by one (see open_text)."""

    def __init__(self, handle):
        self._handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        try:
            self._handle.close()
        except Exception:
            pass

    def __iter__(self):
        first = True
        for raw in self._handle:
            if first:
                first = False
                if raw.startswith(codecs.BOM_UTF8):
                    raw = raw[3:]
            # a bare CR ends a line too (files from old Macs)
            for part in (raw.split(b'\r') if b'\r' in raw else (raw,)):
                try:
                    yield part.decode('utf-8')
                except UnicodeDecodeError:
                    yield part.decode('cp1256', 'replace')


def open_binary(path):
    with open(path, 'rb') as probe:
        magic = probe.read(2)
    return gzip.open(path, 'rb') if magic == b'\x1f\x8b' else open(path, 'rb')


# ------------------------------------------------------------ the streams
def _mime(plain):
    low = plain.lower().split('?', 1)[0].split('#', 1)[0]
    if '.m3u8' in low or low.endswith('.m3u'):
        return 'application/vnd.apple.mpegurl'
    if low.endswith('.mpd'):
        return 'application/dash+xml'
    if low.endswith('.ts') or '/live/' in low:
        return 'video/mp2t'
    if low.endswith('.mp4') or low.endswith('.m4v'):
        return 'video/mp4'
    if low.endswith('.mkv'):
        return 'video/x-matroska'
    return ''       # Kodi looks at the stream itself


def _origin(parts):
    """scheme://host[:port] of an address, without the account it may carry.

    v5.10.110: built from netloc it sent 'user:pass@' in Referer and Origin.
    """
    host = parts.hostname or ''
    if not host:
        return ''
    if ':' in host:
        host = '[%s]' % host            # IPv6
    try:
        port = parts.port
    except ValueError:
        port = None
    return '%s://%s%s' % (parts.scheme, host, ':%d' % port if port else '')


def _own_props(props):
    """The channel's own #KODIPROP properties (v5.10.110); an inputstream that
    is not installed is left out, and Kodi opens the stream itself."""
    own = {}
    for name, value in (props.items() if isinstance(props, dict) else ()):
        name = str(name or '').strip()
        if name and value not in (None, ''):
            own[name] = str(value)
    addon = own.get('inputstream') or own.get('inputstreamaddon') or ''
    if addon and not xbmc.getCondVisibility('System.HasAddon(%s)' % addon):
        own = dict((k, v) for k, v in own.items() if not k.startswith('inputstream'))
    return own


def stream_for(url, headers=None, key='', logo='', props=None):
    """The director's stream for a live address (headers after '|', as Kodi takes them).

    Many IPTV servers refuse Kodi's own user agent: a browser's goes along
    unless the source names its own headers. ``props``: the channel's own
    #KODIPROP lines (an inputstream with its options, or a mimetype).
    """
    url = str(url or '').strip()
    plain, _sep, given = url.partition('|')
    parts = urlsplit(plain)
    own = _own_props(props)
    mime = own.pop('mimetype', '') or _mime(plain)
    if parts.scheme not in ('http', 'https'):
        # a local file, a share or another add-on: Kodi opens it as it is
        return {'url': url, 'mime': mime if parts.scheme not in ('plugin',) else '',
                'live': True, 'key': key, 'logo': logo or '', 'props': own}
    merged = {'User-Agent': UA}
    origin = _origin(parts)
    if origin:
        merged['Referer'] = origin + '/'
        merged['Origin'] = origin
    # v5.10.110: a name in any case replaces the default ('referer' used to
    # go along with the stream's own 'Referer')
    for name, value in (headers or {}).items():
        if name and value:
            set_header(merged, str(name), str(value))
    for name, value in parse_qsl(given, keep_blank_values=False):
        set_header(merged, name, value)
    if mime == 'application/dash+xml' and not own.get('inputstream'):
        if not xbmc.getCondVisibility('System.HasAddon(inputstream.adaptive)'):
            return None
        own.update({'inputstream': 'inputstream.adaptive',
                    'inputstream.adaptive.manifest_type': 'mpd'})
    return {'url': plain + '|' + urlencode(merged), 'mime': mime, 'live': True,
            'key': key, 'logo': logo or '', 'props': own}


# ------------------------------------------------------------------ tiles
def channel_tile(src, uid, name, card, logo='', group='', number='', stream=None,
                 desc='', fanart='', epg=None, genre=''):
    """A channel of any source other than the PVR (same shape as live.channel_tile)."""
    name = live.clean(name) or '…'
    logo = str(logo or '')
    if logo.lower().split('?', 1)[0].endswith('.svg'):
        logo = ''       # Kodi cannot draw SVG
    tile = {
        'kind': 'channel', 'shape': 'landscape', 'title': name, 'label': name,
        'channelid': 0, 'src': src, 'number': str(number or ''), 'group': group or '',
        'path': 'live://%s/%s' % (src, uid), 'folder': False,
        'logo': logo, 'clearlogo': logo, 'poster': card, 'landscape': card,
        'fanart': str(fanart or ''), 'stream': dict(stream or {}), 'desc': live.clean(desc),
        'genre': genre or '', 'epg': dict(epg or {}), 'now': {}, 'next': {}, 'locked': False,
    }
    live.update_times(tile)
    return tile


def programme(title, start, end, plot='', genre='', episode='', thumb=''):
    title = live.clean(title)
    if not title:
        return {}
    try:
        start, end = int(float(start or 0)), int(float(end or 0))
    except Exception:
        start = end = 0
    return {'title': title, 'plot': live.clean(plot), 'start': start, 'end': end,
            'genre': live.clean(genre), 'episode': live.clean(episode), 'thumb': str(thumb or '')}


def apply_guide(tile, items, now=None):
    """now/next of ``tile`` from its guide (programmes sorted by start)."""
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        live.update_times(tile)
        return False
    now = time.time() if now is None else now
    cur = nxt = {}
    for item in items or []:
        if not item:
            continue
        if item['start'] <= now < item['end']:
            cur = item
        elif item['start'] > now:
            nxt = item
            break
    if cur or nxt:
        tile['now'], tile['next'] = cur, nxt
    live.update_times(tile, now)
    return bool(cur or nxt)


_MINIMAL = ('title', 'logo', 'path', 'stream', 'number', 'group', 'fanart', 'desc', 'genre', 'epg')


def remember(profile, tile):
    """A channel the user watched with OK (recently watched row of its source)."""
    src = tile.get('src') or 'pvr'
    if src == 'pvr':
        live.remember(profile, tile.get('channelid'))
        return
    live.remember_entry(profile, {'src': src, 'tile': dict((k, tile.get(k)) for k in _MINIMAL)})


def recent_tiles(profile, src, card):
    tiles = []
    for entry in live.recent_entries(profile):
        if entry.get('src') != src:
            continue
        saved = entry.get('tile') or {}
        path = str(saved.get('path') or '')
        if not path.startswith('live://%s/' % src):
            continue
        tile = channel_tile(src, path.split('/', 3)[-1], saved.get('title'), card,
                            logo=saved.get('logo'), group=saved.get('group'),
                            number=saved.get('number'), stream=saved.get('stream'),
                            desc=saved.get('desc'), fanart=saved.get('fanart'),
                            epg=saved.get('epg'), genre=saved.get('genre'))
        tile['path'] = path
        tiles.append(tile)
        if len(tiles) >= live.RECENT_LIMIT:
            break
    return tiles


# --------------------------------------------------------- saved sources
def saved(profile):
    """The M3U playlists and Xtream accounts the user added in Dex Hub."""
    data = read_json(os.path.join(profile, _SAVED), {}) or {}
    rows = data.get('sources') if isinstance(data, dict) else None
    return [dict(r) for r in rows or []
            if isinstance(r, dict) and r.get('id') and r.get('kind') in ('m3u', 'xtream')]


def save_source(profile, conf):
    with _lock:
        rows = saved(profile)
        # v5.10.110: a source saved again (renamed) keeps its place in the
        # tab bar; it used to move to the end
        for index, row in enumerate(rows):
            if row.get('id') == conf.get('id'):
                rows[index] = dict(conf)
                break
        else:
            rows.append(dict(conf))
        write_json(os.path.join(profile, _SAVED), {'sources': rows})


def remove_source(profile, sid):
    if not sid:
        return
    with _lock:
        rows = [r for r in saved(profile) if r.get('id') != sid]
        write_json(os.path.join(profile, _SAVED), {'sources': rows})
    drop_cache(profile, sid)
    # v5.10.110: its recently watched channels go too (an M3U channel keeps
    # its own address, which may carry the account)
    live.forget_sources(profile, ('m3u:%s' % sid, 'xt:%s' % sid))


def drop_cache(profile, sid):
    """The cache files of one source (a removed one, or one that was not added)."""
    if not sid:
        return
    folder = cache_dir(profile)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if ('_%s.' % sid) in name or ('_%s_' % sid) in name:
            try:
                os.remove(os.path.join(folder, name))
            except Exception:
                pass


def get_saved(profile, sid):
    for conf in saved(profile):
        if conf.get('id') == sid:
            return conf
    return None


# -------------------------------------------------------------- the tabs
def remembered_tab(profile, page):
    data = read_json(os.path.join(profile, _TABS), {}) or {}
    return str(data.get(page) or '') if isinstance(data, dict) else ''


def remember_tab(profile, page, key):
    with _lock:
        data = read_json(os.path.join(profile, _TABS), {}) or {}
        if not isinstance(data, dict):
            data = {}
        if data.get(page) == key:
            return
        data[page] = key
        try:
            write_json(os.path.join(profile, _TABS), data, private=False)
        except Exception:
            pass


# ----------------------------------------------------------- the sources
def sources(app):
    """Every live source, in tab order (the setup tab last)."""
    from ..ui_preferences import iptv_available
    if not iptv_available():
        return [{'key': 'setup', 'kind': 'setup', 'label': app.tr('مصادر البث'), 'hint': '', 'icon': app.media_path('tab_setup.png'), 'card': 'live_stremio.jpg'}]
    from . import live_iptv, live_stremio
    sweep(app.profile)
    out = []
    if iptv_available('channels') and live.has_client():
        out.append({'key': 'pvr', 'kind': 'pvr', 'label': app.tr('قنوات Kodi'), 'hint': 'Kodi PVR',
                    'icon': app.media_path('tab_pvr.png'), 'card': 'live_pvr.jpg'})
    try:
        out.extend(live_stremio.sources(app))
    except Exception as exc:
        log('Stremio channel sources not read: %s' % type(exc).__name__, xbmc.LOGWARNING)
    for conf in saved(app.profile):
        out.append(live_iptv.source(conf, app))
    if not iptv_available('channels'):
        from . import vod
        out = [s for s in out if vod.available(s)]
    out.append({'key': 'setup', 'kind': 'setup', 'label': app.tr('مصادر البث'),
                'hint': app.tr('أضف مصدراً'), 'icon': app.media_path('tab_setup.png'),
                'card': 'live_stremio.jpg'})
    return out


def _module(src):
    src = str(src or '')
    if src.startswith('st:') or src == 'dw':
        from . import live_stremio
        return live_stremio
    if src.startswith('m3u:') or src.startswith('xt:'):
        from . import live_iptv
        return live_iptv
    return None


def rows(source, app, changed=None):
    """Row specs of a source: [{'key', 'title', 'subtitle', 'params', 'loader'}]."""
    from ..ui_preferences import iptv_available
    if not iptv_available('channels'):
        return []
    module = _module(source.get('key'))
    return module.rows(source, app, changed) if module is not None else []


def page_tiles(params, app):
    """A page of the "See all" grid of a live row (params with 'live_src')."""
    if params.get('vod_type'):
        from . import vod
        return vod.page_tiles(params, app)
    from ..ui_preferences import iptv_available
    if not iptv_available('channels'):
        return [], None
    module = _module(params.get('live_src'))
    if module is None:
        return [], None
    return module.page_tiles(params, app)


def resolve(tile, app):
    """The director's stream for a channel tile of a source other than the PVR."""
    module = _module(tile.get('src'))
    if module is None:
        return None
    return module.resolve(tile, app)


def guide(tile, app):
    """The programmes of a channel from now on (as live.guide)."""
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    module = _module(tile.get('src'))
    if module is None:
        return []
    try:
        return module.guide(tile, app) or []
    except Exception as exc:
        log('guide not read: %s' % failure(exc), xbmc.LOGWARNING)
        return []


def fill_epg(tiles, app, limit=24):
    """Now and next for tiles that have none yet; the tiles that changed."""
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    by_src = {}
    for tile in tiles or []:
        if tile.get('kind') == 'channel' and tile.get('src') and tile.get('epg'):
            by_src.setdefault(tile['src'], []).append(tile)
    changed = []
    for src, group in by_src.items():
        module = _module(src)
        if module is None or not hasattr(module, 'fill_epg'):
            continue
        try:
            # the module marks '_epg_done' on the tiles it really looked up;
            # the others are asked for when the cursor reaches them
            changed.extend(module.fill_epg(group[:limit], app) or [])
        except Exception as exc:
            log('guide not read: %s' % failure(exc), xbmc.LOGWARNING)
    return changed


_NET_ERRNOS = frozenset(getattr(errno, name) for name in (
    'ECONNREFUSED', 'ECONNRESET', 'ECONNABORTED', 'ETIMEDOUT', 'EHOSTUNREACH', 'ENETUNREACH',
    'ENETDOWN', 'EHOSTDOWN', 'EPIPE') if hasattr(errno, name))


def _network(exc):
    """Is ``exc`` a failure of the network (not of a file, a parser or the code)?"""
    import socket
    import ssl
    from http.client import HTTPException
    from urllib.error import URLError
    if isinstance(exc, (socket.timeout, socket.gaierror, socket.herror, ssl.SSLError, ConnectionError,
                        TimeoutError, URLError, HTTPException)):
        return True
    if isinstance(exc, OSError) and getattr(exc, 'errno', None) in _NET_ERRNOS:
        return True
    # Dex Hub's own client raises a bare Exception for a refused request
    # ('HTTP 401 for ...', 'upstream 502'); urllib3 and requests their own
    return type(exc) is Exception or (type(exc).__module__ or '').split('.', 1)[0] in ('urllib3', 'requests')


def safe(exc):
    """The text of a failure for the log and the screen (no address in it)."""
    if isinstance(exc, SourceError):
        return str(exc)
    if _network(exc):
        return _describe(exc)
    if isinstance(exc, (ValueError, KeyError)):
        text = str(exc)
        return text if text and '://' not in text and '/' not in text else type(exc).__name__
    # v5.10.110: a damaged file, a full disk or a bug used to read "the server
    # could not be reached"; its kind is named (failure() logs its traceback)
    return 'خطأ غير متوقع (%s)' % type(exc).__name__


def failure(exc):
    """safe(exc) for the log; an unexpected error adds its traceback, without addresses."""
    text = safe(exc)
    if isinstance(exc, SourceError) or _network(exc):
        return text
    trace = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return '%s\n%s' % (text, clean_trace(trace))


_SECRET = re.compile(r'(?i)\b(password|passwd|username|user|key|api_key|token)=[^&\s\'"]+')
# v5.10.110: the other schemes of a playlist too (an smb or udp address can
# carry an account as well), now that failure() logs tracebacks
_ADDRESS = re.compile(r'(?i)\b(https?|rtmp[a-z]*|rtsp|udp|rtp|smb|nfs|ftp|mms[a-z]*)://([^/\s\'"<>?#]+)[^\s\'"<>]*')
_PATH = re.compile(r'(?i)\b(url:\s*)/[^\s\'"<>]*')


def mask(text):
    return _SECRET.sub(r'\1=***', str(text or ''))


def clean_trace(text):
    """A traceback (or any text) without addresses: an IPTV address carries the
    account in its path or its query, so only the host is kept."""
    text = _ADDRESS.sub(lambda m: '%s://%s/...' % (m.group(1), m.group(2).rsplit('@', 1)[-1]), str(text or ''))
    text = _PATH.sub(r'\1...', text)
    return mask(text)
