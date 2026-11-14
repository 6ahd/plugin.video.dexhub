# -*- coding: utf-8 -*-
"""Whole Blu-ray discs from a Debrid account, played in their own menus (v5.10.571).

A torrent that is a whole disc (BDMV folders) is many files, each with its own
link at the service, while Kodi's Blu-ray reader needs a folder: index.bdmv,
MovieObject.bdmv, the playlists, the clip files, the streams. Dex Hub's
service serves that folder on this device only (127.0.0.1): a folder is a
plain index page, a file is a redirect to the service's link for it, asked
the first time Kodi wants that file. Kodi then opens the disc as
bluray://<folder>/menu: the intros, the disc's menu, its chapters and
languages, the film started from the menu.

  offer()   (the playing add-on) remembers the disc and gives the path
  start()   (service.py) runs the server, its port in a Window property

Tried on Kodi 22 (macOS) with a 114 GB UHD disc from TorBox: menu, then the
film in 4K HDR from the menu's Play. A TorBox Usenet download that is a disc
folder is served the same way (v5.10.572); a Usenet release played through the
engine is served by the engine itself (usenet_engine.disc_menu).

A disc image (.iso) behind any link (v5.10.572): Kodi on the Mac hangs reading
one through udf://, and plays none elsewhere but on CoreELEC/LibreELEC. Its
UDF file system is read over ranges of the link (udf.py) and the disc is
served the same way, each file as its ranges of the image (offer_image).
Tried with a 42 GB image from TorBox: 440 files named in 4 s, six reads.
"""
import hashlib
import html
import json
import os
import threading
import time
import urllib.parse

from . import accounts

PORT_PROP = 'dexhub.disc.port'
HOST = '127.0.0.1'
KEEP = 24 * 3600             # a disc is remembered this long after it was offered
LINK_KEEP = 3 * 3600         # a file's link is asked again after this long
_ENC_SAFE = '-._!()'         # what Kodi's URIUtils::URLEncode leaves alone

_server = None
_links = {}
_lock = threading.Lock()
_discs = {'mtime': 0, 'data': {}}


def _file():
    return accounts.path('discs.json')


def kodi_encode(text):
    """Kodi's URIUtils::URLEncode: how a path nested in bluray:// is written."""
    out = []
    for byte in text.encode('utf-8'):
        char = chr(byte)
        out.append(char if char.isascii() and (char.isalnum() or char in _ENC_SAFE) else '%%%02x' % byte)
    return ''.join(out)


_enc = kodi_encode


def _read():
    try:
        with open(_file(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data):
    target = _file()
    try:
        folder = os.path.dirname(target)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(target + '.tmp', 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        os.replace(target + '.tmp', target)
    except Exception as exc:
        accounts.log('disc not remembered: %s' % exc, True)


def port():
    try:
        import xbmcgui
        return int(xbmcgui.Window(10000).getProperty(PORT_PROP) or 0)
    except Exception:
        return 0


def root_of(name):
    """The folder that holds a disc file's BDMV folder ('' when it is at the top), or None."""
    path = str(name or '').replace('\\', '/')
    low = path.lower()
    at = low.find('bdmv/')
    if at < 0 or (at and low[at - 1] != '/'):
        return None
    return path[:at]


def _remember(key, disc):
    data = {k: v for k, v in _read().items() if time.time() - float((v or {}).get('at') or 0) < KEEP}
    data[key] = dict(disc, at=int(time.time()))
    _write(data)


def _split_link(link):
    """A Kodi link 'url|Header=value&...' as (url, headers)."""
    url, _sep, extra = str(link or '').partition('|')
    headers = {'User-Agent': 'Kodi'}
    for pair in extra.split('&') if extra else []:
        name, _eq, value = pair.partition('=')
        if name:
            headers[urllib.parse.unquote(name)] = urllib.parse.unquote(value)
    return url, headers


def _ranged(link, start, length, seconds=30):
    import urllib.request
    url, headers = _split_link(link)
    headers['Range'] = 'bytes=%d-%d' % (start, start + length - 1)
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=seconds)


def image_size(link):
    """The size of the file behind a link (its Content-Range), 0 when it does not say."""
    try:
        with _ranged(link, 0, 1, 20) as answer:
            total = str(answer.headers.get('Content-Range') or '').rsplit('/', 1)[-1]
            return int(total) if total.isdigit() else 0
    except Exception as exc:
        accounts.log('disc image size: %s' % exc, True)
        return 0


def offer_image(link, size=0):
    """The bluray:// path that opens the Blu-ray image behind `link` in its menus, or ''
    (not an image Dex Hub can read: a DVD, a damaged one, a link that gives no ranges)."""
    if not accounts.option('disc_menus'):
        return ''
    number = port()
    if not number:
        return ''
    size = int(size or 0) or image_size(link)
    if size <= 0:
        return ''
    from . import udf

    def read(offset, length):
        with _ranged(link, offset, length) as answer:
            if answer.status != 206:
                raise udf.UDFError('the link gave no range')
            return answer.read()

    started = time.time()
    try:
        image = udf.Image(read, size).open()
        tree = image.disc_files()
    except Exception as exc:
        accounts.log('disc image not read (%s): it plays as it is' % exc)
        return ''
    key = hashlib.sha1(('image:%s' % str(link).split('|', 1)[0]).encode('utf-8')).hexdigest()[:12]
    _remember(key, {'image': link, 'size': size, 'files': tree})
    lower = {k.lower(): v for k, v in tree.items()}

    def small(rel):
        entry = lower.get(rel.lower()) or {}
        if entry.get('data') is not None:
            return bytes.fromhex(entry['data'])
        return b''.join(image.read(o, l) for o, l in entry.get('x') or [])[:1 << 20]

    from . import bdinfo
    try:
        index = small('BDMV/index.bdmv')
    except Exception:
        index = b''
    names = [k.rsplit('/', 1)[-1] for k in tree if k.lower().startswith('bdmv/playlist/')]
    # one reader at a time: the playlists sit together, in the chunks already read
    target, how = bdinfo.path(_enc('http://%s:%d/disc/%s/' % (HOST, number, key)), index, names,
                              lambda name: small('BDMV/PLAYLIST/' + name), workers=1)
    accounts.log('a disc image: %d files read in %.1f s (%d reads), opened on its %s'
                 % (len(tree), time.time() - started, image.reads, how))
    return target


def offer(service_id, item_id, pool, chosen, kind='torrents'):
    """The bluray:// path that opens the disc of `chosen` in its menus, or '' (the file plays as it is).

    pool: the torrent's (or Usenet download's) files, each {'name', 'size', 'id'}."""
    if not accounts.option('disc_menus'):
        return ''
    root = root_of((chosen or {}).get('name'))
    number = port()
    if root is None or not number:
        return ''
    tree = {}
    for item in pool or []:
        name = str(item.get('name') or '').replace('\\', '/')
        if not name.startswith(root):
            continue
        rest = name[len(root):]
        if rest.lower().startswith(('bdmv/', 'certificate/', 'aacs/')):
            tree[rest] = {'id': item.get('id'), 'size': item.get('size') or 0}
    if not any(k.lower() == 'bdmv/index.bdmv' for k in tree):
        return ''
    key = hashlib.sha1(('%s:%s' % (service_id, item_id)).encode('utf-8')).hexdigest()[:12]
    _remember(key, {'service': service_id, 'item': item_id, 'kind': kind, 'files': tree})
    lower = {k.lower(): v for k, v in tree.items()}
    from . import bdinfo, services
    service = services.service(service_id)

    def small(rel):
        import urllib.request
        entry = lower.get(rel.lower()) or {}
        link = service.file_link(item_id, entry.get('id'), kind) if service and entry else ''
        if not link:
            return b''
        url, headers = _split_link(link)
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as answer:
            return answer.read(1 << 20)

    try:
        index = small('BDMV/index.bdmv')
    except Exception as exc:
        accounts.log('%s: index.bdmv not read: %s' % (service_id, exc), True)
        index = b''
    names = [k.rsplit('/', 1)[-1] for k in tree if k.lower().startswith('bdmv/playlist/')]
    target, how = bdinfo.path(_enc('http://%s:%d/disc/%s/' % (HOST, number, key)), index, names,
                              lambda name: small('BDMV/PLAYLIST/' + name))
    accounts.log('%s: whole disc (%d files), opened on its %s' % (service_id, len(tree), how))
    return target


# ================================================================ the server (service.py)
def _disc(key):
    try:
        mtime = os.path.getmtime(_file())
    except OSError:
        mtime = 0
    with _lock:
        if mtime != _discs['mtime']:
            _discs['data'], _discs['mtime'] = _read(), mtime
        return _discs['data'].get(key)


def _link(key, disc, name):
    stamp = (key, name)
    with _lock:
        known = _links.get(stamp)
        if known and time.time() - known[1] < LINK_KEEP:
            return known[0]
    from . import services
    service = services.service(disc.get('service'))
    entry = (disc.get('files') or {}).get(name) or {}
    link = service.file_link(disc.get('item'), entry.get('id'), disc.get('kind') or 'torrents') if service else ''
    if link:
        with _lock:
            _links[stamp] = (link, time.time())
    return link


def _handler():
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_HEAD(self):
            self._answer(head=True)

        def do_GET(self):
            self._answer(head=False)

        def _send(self, code, body=b'', kind='text/html; charset=utf-8', location=''):
            self.send_response(code)
            if location:
                self.send_header('Location', location)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            return body

        def _image_file(self, disc, entry, head):
            """One file of a disc image: its bytes are ranges of the image's link."""
            size = int(entry.get('size') or 0)
            first, last, partial = 0, size - 1, False
            wanted = str(self.headers.get('Range') or '')
            if wanted.startswith('bytes=') and ',' not in wanted:
                lo, _dash, hi = wanted[6:].strip().partition('-')
                try:
                    if lo:
                        first, last = int(lo), (min(int(hi), size - 1) if hi else size - 1)
                    elif hi:
                        first, last = max(0, size - int(hi)), size - 1
                    partial = True
                except ValueError:
                    first, last, partial = 0, size - 1, False
            if size and (first > last or first >= size):
                self.send_response(416)
                self.send_header('Content-Range', 'bytes */%d' % size)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(max(0, last - first + 1)))
            if partial:
                self.send_header('Content-Range', 'bytes %d-%d/%d' % (first, last, size))
            self.end_headers()
            if head or size <= 0:
                return
            if entry.get('data') is not None:
                self.wfile.write(bytes.fromhex(entry['data'])[first:last + 1])
                return
            at = 0
            try:
                for offset, length in entry.get('x') or []:
                    lo, hi = max(first, at), min(last, at + length - 1)
                    if lo <= hi:
                        with _ranged(disc['image'], offset + lo - at, hi - lo + 1, 60) as answer:
                            while True:
                                block = answer.read(256 * 1024)
                                if not block:
                                    break
                                self.wfile.write(block)
                    at += length
                    if at > last:
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass                                    # Kodi moved on (a seek)
            except Exception as exc:
                accounts.log('disc image read: %s' % exc, True)
                self.close_connection = True

        def _answer(self, head):
            parts = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).lstrip('/').split('/', 2)
            if len(parts) < 2 or parts[0] != 'disc':
                body = self._send(404)
            else:
                disc = _disc(parts[1])
                name = parts[2] if len(parts) > 2 else ''
                files = (disc or {}).get('files') or {}
                if name and name not in files:
                    name = {n.lower(): n for n in files}.get(name.lower(), name)
                if disc is None:
                    body = self._send(404)
                elif name in files and disc.get('image'):
                    self._image_file(disc, files[name], head)
                    return
                elif name in files:
                    try:
                        link = _link(parts[1], disc, name)
                    except Exception as exc:
                        accounts.log('disc file %s: %s' % (name, exc), True)
                        link = ''
                    body = self._send(302, location=link) if link else self._send(502)
                else:
                    prefix = name if not name or name.endswith('/') else name + '/'
                    kids = sorted({n[len(prefix):].split('/', 1)[0] + ('/' if '/' in n[len(prefix):] else '')
                                   for n in files if n.startswith(prefix)})
                    if not kids:
                        body = self._send(404)
                    else:
                        page = ('<html><head><title>Index of /%s</title></head><body><pre>' % html.escape(prefix) +
                                ''.join('<a href="%s">%s</a>\n' % (urllib.parse.quote(k), html.escape(k)) for k in kids) +
                                '</pre></body></html>').encode('utf-8')
                        body = self._send(200, page)
            if not head and body:
                self.wfile.write(body)

    return Handler


def start():
    """Run the server (once per Kodi session); its port goes in a Window property."""
    global _server
    if _server is not None:
        return True
    try:
        from http.server import ThreadingHTTPServer
        import xbmcgui
        _server = ThreadingHTTPServer((HOST, 0), _handler())
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, name='DexHub-disc-server', daemon=True).start()
        xbmcgui.Window(10000).setProperty(PORT_PROP, str(_server.server_address[1]))
        return True
    except Exception as exc:
        accounts.log('disc server not started: %s' % exc, True)
        _server = None
        return False


def stop():
    global _server
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
        _server = None
