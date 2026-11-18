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
import io
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

# v5.10.581: a disc image's small files (the menus' Java, sounds and pictures, the clip and
# playlist files) are read once and kept: a disc menu asks for them one after another, and
# each was a trip to the image's server (a 4K disc from Emby: its menu waited minutes). The
# first time Kodi asks for a disc, its small files are read in the background, several at once.
SMALL_FILE = 4 * 1024 * 1024
SMALL_TOTAL = 160 * 1024 * 1024
WARM_WORKERS = 6             # v5.10.581: six at once, on kept connections
WARM_BUSY = 2                # ... and two once the server answers it is busy (an Emby server gave 500, 16 Nov 2026)
RETRY_CODES = (429, 500, 502, 503, 504)
_small = {}                  # (image link, file name) -> bytes
_small_order = []
_small_size = [0]
_warmed = set()
_inflight = {}               # (disc, file) -> Event while the warm-up reads it
SPAN_MAX = 4 * 1024 * 1024  # one read of side-by-side small files
SPAN_GAP = 128 * 1024       # bytes between two files still read across


def _small_get(image, name):
    with _lock:
        return _small.get((image, name))


def _small_put(image, name, data):
    with _lock:
        if (image, name) in _small or len(data) > SMALL_FILE:
            return
        _small[(image, name)] = data
        _small_order.append((image, name))
        _small_size[0] += len(data)
        while _small_size[0] > SMALL_TOTAL and _small_order:
            old = _small_order.pop(0)
            _small_size[0] -= len(_small.pop(old, b''))


def _ranged_retry(image, offset, length, seconds=60):
    """One range of the image, asked again (up to three times, a little later each time) when
    the server answers it is busy: an Emby server answered 500 while it served other ranges."""
    import urllib.error
    for wait in (0.0, 0.7, 2.0):
        if wait:
            time.sleep(wait)
        try:
            with _ranged(image, offset, length, seconds) as answer:
                return answer.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_CODES or wait == 2.0:
                raise


STREAM_CHUNK = 4 * 1024 * 1024    # one read of the film from the link
STREAM_FIRST = 512 * 1024         # the first read is small: Kodi gets its first bytes sooner
STREAM_WORKERS = 4                # reads at once, ahead of Kodi (the Usenet engine's way)


def _stream_range(image, offset, length, out):
    """One range of the image written to `out`, read over STREAM_WORKERS connections at once
    in STREAM_CHUNK pieces, in order: a link that gives each connection only so much is read
    several times as fast (v5.10.581). A small range is read as one."""
    if length <= STREAM_FIRST * 2:
        return _stream_one(image, offset, length, out)
    import concurrent.futures
    pieces, at = [], 0
    while at < length:
        size = min(STREAM_FIRST if not pieces else STREAM_CHUNK, length - at)
        pieces.append((offset + at, size))
        at += size
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=STREAM_WORKERS, thread_name_prefix='DexHub-disc-read')
    try:
        waiting = {}
        ahead = STREAM_WORKERS * 2
        for index, (where, size) in enumerate(pieces[:ahead]):
            waiting[index] = pool.submit(_ranged_retry, image, where, size, 60)
        for index in range(len(pieces)):
            data = waiting.pop(index).result()
            if len(data) != pieces[index][1]:
                raise IOError('the server gave %d of %d bytes' % (len(data), pieces[index][1]))
            nxt = index + ahead
            if nxt < len(pieces):
                waiting[nxt] = pool.submit(_ranged_retry, image, pieces[nxt][0], pieces[nxt][1], 60)
            out.write(data)                     # Kodi gone: BrokenPipe goes to the caller
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _stream_one(image, offset, length, out):
    """One range of the image written to `out` as it comes. v5.10.581: a busy answer (500)
    or a cut connection from the image's server is asked again from where it stopped, up to
    four times: Kodi closed The Batman from Emby when one read of its stream answered 500."""
    import http.client
    import urllib.error

    class _KodiGone(Exception):
        pass

    done, tries = 0, 0
    while done < length:
        try:
            with _ranged(image, offset + done, length - done, 60) as answer:
                while done < length:
                    block = answer.read(min(256 * 1024, length - done))
                    if not block:
                        break
                    try:
                        out.write(block)
                    except (BrokenPipeError, ConnectionResetError) as gone:
                        raise _KodiGone(gone)
                    done += len(block)
            if done < length:
                raise IOError('the server ended the range early')
        except _KodiGone as gone:
            raise gone.args[0]                 # Kodi moved on (a seek): the caller's
        except (urllib.error.URLError, IOError, OSError, http.client.HTTPException) as exc:
            code = getattr(exc, 'code', None)
            if (code is not None and code not in RETRY_CODES) or tries >= 4:
                raise
            tries += 1
            accounts.log('disc image read: %s, asked again (%d) at %d of %d' % (exc, tries, done, length))
            time.sleep(0.5 * tries)


def _image_bytes(image, entry):
    """A whole (small) file of a disc image, read from its ranges."""
    if entry.get('data') is not None:
        return bytes.fromhex(entry['data'])
    return b''.join(_ranged_retry(image, offset, length) for offset, length in entry.get('x') or [])


def _ident(disc):
    """What the kept files of a disc image go by: its size and its first file's place, not
    its link, which an Emby server gives anew for each playback (the Disc menu button opens
    the same disc again with a new link, 16 Nov 2026)."""
    files = disc.get('files') or {}
    first = files.get('BDMV/index.bdmv') or files.get('BDMV/INDEX.BDMV') or {}
    return 'disc:%s:%s' % (disc.get('size') or 0, (first.get('x') or [[0]])[0][0] if first.get('x') else '')


def _twin(files, name):
    """v5.10.581: a BDMV/BACKUP file is a copy of the one beside it: that one's name when it is
    there and the same size (Kodi reads every playlist's backup as a disc opens: 1917, hundreds
    of them, each a read of the link), else ''."""
    low = str(name or '').replace('\\', '/')
    if not low.lower().startswith('bdmv/backup/'):
        return ''
    main = 'BDMV/' + low[len('BDMV/BACKUP/'):]
    lower = {k.lower(): k for k in files}
    found = lower.get(main.lower())
    if found and int((files.get(found) or {}).get('size') or -1) == int((files.get(name) or {}).get('size') or -2):
        return found
    return ''


def _small_file(image, name, entry, ident=''):
    """The bytes of a small file, from the kept ones or read now (and kept)."""
    data = _small_get(ident or image, name)
    if data is None:
        # being read by the warm-up in a stretch with its neighbours: wait for it, not read it twice
        with _lock:
            event = _inflight.get((ident, name)) if ident else None
        if event is not None:
            event.wait(30)
            data = _small_get(ident or image, name)
    if data is None:
        data = _image_bytes(image, entry)
        _small_put(ident or image, name, data)
    return data


def _warm(key, disc):
    """Reads a disc image's small files in the background (once per disc and Kodi session):
    the menu's files first (index, objects, Java, sounds), then the clip and playlist files."""
    image = disc.get('image')
    ident = _ident(disc)
    with _lock:
        if not image or ident in _warmed:
            return
        _warmed.add(ident)
    files = disc.get('files') or {}

    def rank(name):
        low = name.lower()
        order = ('bdmv/index.bdmv', 'bdmv/movieobject.bdmv', 'bdmv/jar/', 'bdmv/bdjo/', 'bdmv/auxdata/',
                 'bdmv/meta/', 'bdmv/playlist/', 'bdmv/clipinf/', 'bdmv/backup/')
        return next((i for i, p in enumerate(order) if low.startswith(p)), len(order))

    # v5.10.581: the small files lie side by side in the image (the playlists, the clip files,
    # the Java), so they are read as stretches of up to SPAN_MAX, each one read for many files:
    # 1917 from a link that takes a second or more per read had ~400 files read one by one.
    single, jobs = [], []
    for n, e in files.items():
        if int(e.get('size') or 0) > SMALL_FILE or n.lower().startswith('bdmv/stream/') or _twin(files, n) \
                or e.get('data') is not None:
            continue
        extents = e.get('x') or []
        if len(extents) == 1:
            single.append((int(extents[0][0]), int(extents[0][1]), n))
        elif extents:
            jobs.append((rank(n), 0, [n], None))
    single.sort()
    span = []
    for off, length, n in single:
        if span and (off - (span[-1][0] + span[-1][1]) > SPAN_GAP or off + length - span[0][0] > SPAN_MAX):
            jobs.append((min(rank(x[2]) for x in span), span[0][0], [x[2] for x in span], list(span)))
            span = []
        span.append((off, length, n))
    if span:
        jobs.append((min(rank(x[2]) for x in span), span[0][0], [x[2] for x in span], list(span)))
    jobs.sort(key=lambda job: (job[0], job[1]))
    queue = list(jobs)
    with _lock:
        for job in jobs:
            for n in job[2]:
                _inflight.setdefault((ident, n), threading.Event())
    started, done = time.time(), [0, 0, 0]

    busy, tries = [False], {}

    def finish(names):
        with _lock:
            for n in names:
                event = _inflight.pop((ident, n), None)
                if event is not None:
                    event.set()

    def work(number):
        import urllib.error
        while True:
            with _lock:
                if not queue or _small_size[0] >= SMALL_TOTAL or (busy[0] and number >= WARM_BUSY):
                    return
                job = queue.pop(0)
            _rank, _at, names, parts = job
            try:
                if parts is None:               # a file in pieces: read on its own
                    data = _small_file(image, names[0], files[names[0]], ident)
                    got = len(data)
                else:
                    first, last = parts[0][0], parts[-1][0] + parts[-1][1]
                    blob = _ranged_retry(image, first, last - first)
                    if len(blob) < last - first:
                        raise IOError('the server gave %d of %d bytes' % (len(blob), last - first))
                    got = 0
                    for off, length, n in parts:
                        _small_put(ident, n, blob[off - first:off - first + length])
                        got += length
                with _lock:
                    done[0] += len(names)
                    done[1] += got
                    done[2] += 1
                finish(names)
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_CODES and not busy[0]:
                    busy[0] = True
                    accounts.log('disc image: the server is busy (%s): %d reads at once from here' % (exc.code, WARM_BUSY))
                key = names[0]
                with _lock:
                    tries[key] = tries.get(key, 0) + 1
                    again = tries[key] < 3
                    if again:
                        queue.append(job)       # read again by one of the workers that stay
                if not again:
                    finish(names)
            except Exception:
                finish(names)

    def run():
        workers = [threading.Thread(target=work, args=(n,), name='DexHub-disc-warm', daemon=True) for n in range(WARM_WORKERS)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(600)
        with _lock:
            left = [n for job in queue for n in job[2]]
        finish(left)
        accounts.log('disc image: %d small files (%.1f MB) kept in %.1f s (%d reads)'
                     % (done[0], done[1] / 1048576.0, time.time() - started, done[2]))

    threading.Thread(target=run, name='DexHub-disc-warm-run', daemon=True).start()


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


# v5.10.584: the disc images are served by a process of their own (discproc), which a stopped
# disc cannot freeze; this server inside Kodi stays for the Debrid discs and as the fallback.
PROC_PROP = 'dexhub.disc.procport'
_PYTHONS = ('/usr/bin/python3', '/usr/bin/python')


def start_process():
    """Start discproc (the service, once per Kodi session); its port goes in a Window property."""
    import subprocess
    import xbmcgui
    exe = next((p for p in _PYTHONS if os.access(p, os.X_OK)), '')
    if not exe:
        accounts.log('disc images served inside Kodi (no Python of its own on this device)')
        return False
    folder = os.path.dirname(accounts.path('discs.json'))
    port_file = accounts.path('discproc.port')
    try:
        os.remove(port_file)
    except OSError:
        pass
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'discproc.py')
    try:
        with open(os.path.join(folder, 'discproc.err'), 'a') as errors:
            subprocess.Popen([exe, '-I', script, folder, port_file, str(os.getpid())],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
                             close_fds=True, start_new_session=True)
    except Exception as exc:
        accounts.log('disc process not started: %s' % exc, True)
        return False
    for _ in range(50):
        try:
            with open(port_file) as handle:
                number = int(handle.read().strip() or 0)
            if number:
                xbmcgui.Window(10000).setProperty(PROC_PROP, str(number))
                accounts.log('disc images served by a process of their own on port %d' % number)
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    accounts.log('disc process gave no port: disc images served inside Kodi', True)
    return False


def proc_port():
    """discproc's port when it answers, else 0."""
    try:
        import socket
        import xbmcgui
        number = int(xbmcgui.Window(10000).getProperty(PROC_PROP) or 0)
        if number:
            socket.create_connection((HOST, number), 0.5).close()
        return number
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


# v5.10.581: what makes the Usenet engine quick, for a disc image behind a link too. Its
# connections stay open and serve read after read, where urllib opened a new HTTPS connection
# (and its TLS handshake) for each of the hundreds of small reads a disc menu makes; and it
# fetches the film over several connections at once, ahead of Kodi (_stream_range).
_POOL = {}                   # (scheme, host, port) -> [idle http.client connections]
_POOL_LOCK = threading.Lock()
_POOL_KEEP = 8               # idle connections kept for one server


class _Answer(object):
    """A ranged answer read from a kept connection, handed back when read to its end."""

    def __init__(self, conn, response, where):
        self._conn, self._response, self._where = conn, response, where
        self.status = response.status
        self.headers = response.headers

    def read(self, amount=None):
        return self._response.read() if amount is None else self._response.read(amount)

    def close(self):
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            done = self._response.isclosed() or self._response.length == 0
        except Exception:
            done = False
        if done and not self._response.will_close:
            with _POOL_LOCK:
                idle = _POOL.setdefault(self._where, [])
                if len(idle) < _POOL_KEEP:
                    idle.append(conn)
                    return
        try:
            conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _connection(where, seconds, fresh=False):
    import http.client
    import ssl
    scheme, host, port = where
    if not fresh:
        with _POOL_LOCK:
            idle = _POOL.get(where) or []
            if idle:
                conn = idle.pop()
                try:
                    conn.sock.settimeout(seconds)
                except Exception:
                    pass
                return conn, True
    if scheme == 'https':
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE     # as urllib was used here: the link is the viewer's own
        return http.client.HTTPSConnection(host, port, timeout=seconds, context=context), False
    return http.client.HTTPConnection(host, port, timeout=seconds), False


def _ranged(link, start, length, seconds=30):
    """bytes start..start+length-1 of the link: an answer with status, headers and read(),
    used with `with`. An error status raises urllib.error.HTTPError, as urlopen did."""
    import http.client
    import urllib.error
    url, headers = _split_link(link)
    headers['Range'] = 'bytes=%d-%d' % (start, start + length - 1)
    for _hop in range(4):                       # redirects
        parts = urllib.parse.urlsplit(url)
        scheme = (parts.scheme or 'http').lower()
        where = (scheme, parts.hostname or '', parts.port or (443 if scheme == 'https' else 80))
        path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        for attempt in range(2):
            conn, reused = _connection(where, seconds, fresh=attempt > 0)
            try:
                conn.request('GET', path, headers=dict(headers, Host=parts.netloc))
                response = conn.getresponse()
                break
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
                    http.client.BadStatusLine) as exc:
                try:
                    conn.close()
                except Exception:
                    pass
                if reused and attempt == 0:
                    continue                    # a kept connection the server had closed
                raise urllib.error.URLError(exc)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                raise
        if response.status in (301, 302, 303, 307, 308) and response.getheader('Location'):
            target = urllib.parse.urljoin(url, response.getheader('Location'))
            response.read()
            _Answer(conn, response, where).close()
            url = target
            continue
        if response.status >= 400:
            body = response.read()
            _Answer(conn, response, where).close()
            raise urllib.error.HTTPError(url, response.status, response.reason, response.headers, io.BytesIO(body))
        return _Answer(conn, response, where)
    raise urllib.error.URLError('too many redirects')


def image_size(link):
    """The size of the file behind a link (its Content-Range), 0 when it does not say."""
    try:
        with _ranged(link, 0, 1, 20) as answer:
            total = str(answer.headers.get('Content-Range') or '').rsplit('/', 1)[-1]
            return int(total) if total.isdigit() else 0
    except Exception as exc:
        accounts.log('disc image size: %s' % exc, True)
        return 0


_PROXIED = {}                # key -> (link, time): addresses Kodi plays through this server
_PROXY_KEEP = 6 * 3600


def offer_link(link):
    """v5.10.581: a local address (http://127.0.0.1:port/link/<key>/video.mp4) for `link`, which
    this server reads for Kodi, ranges and all, or '' (the server is not running). Kodi then
    looks up no name of its own: on an iPhone hotspot its lookups of imdb-video.media-imdb.com
    failed five times in six while the device's own resolver answered at once (a Mac, 17 Nov
    2026), and every failed trailer said "Playback failed"."""
    number = port()
    if not number or not str(link or '').lower().startswith(('http://', 'https://')):
        return ''
    key = hashlib.sha1(str(link).encode('utf-8')).hexdigest()[:16]
    now = time.time()
    with _lock:
        _PROXIED[key] = (link, now)
        for old in [k for k, v in _PROXIED.items() if now - v[1] > _PROXY_KEEP]:
            _PROXIED.pop(old, None)
    return 'http://%s:%d/link/%s/video.mp4' % (HOST, number, key)


def link_answers(link, seconds=6):
    """The first bytes of `link` came (a trailer is tried before Kodi is given it, so one that
    does not answer never shows "Playback failed" nor touches the poster)."""
    try:
        with _ranged(link, 0, 65536, seconds) as answer:
            if answer.status not in (200, 206):
                return False
            return len(answer.read(4096)) > 0
    except Exception as exc:
        accounts.log('a link did not answer (%s)' % str(exc)[:120])
        return False


def offer_image(link, size=0):
    """The bluray:// path that opens the Blu-ray image behind `link` in its menus, or ''
    (not an image Dex Hub can read: a DVD, a damaged one, a link that gives no ranges)."""
    if not accounts.option('disc_menus'):
        return ''
    number = proc_port() or port()
    if not number:
        return ''
    size = int(size or 0) or image_size(link)
    if size <= 0:
        return ''
    from . import udf
    key = hashlib.sha1(('image:%s' % str(link).split('|', 1)[0]).encode('utf-8')).hexdigest()[:12]

    def read(offset, length):
        with _ranged(link, offset, length) as answer:
            if answer.status != 206:
                raise udf.UDFError('the link gave no range')
            return answer.read()

    started = time.time()
    image = udf.Image(read, size)
    # v5.10.581: the same image opened again (the Disc menu button, a replay for a subtitle)
    # keeps the file list read the first time: no reads of its file system
    known = (_read().get(key) or {}) if isinstance(_read(), dict) else {}
    if known.get('size') == size and known.get('files'):
        tree = known['files']
        _remember(key, dict(known, image=link))
    else:
        tree = None
        for attempt in range(2):
            try:
                image = udf.Image(read, size)
                image.open()
                tree = image.disc_files()
                break
            except Exception as exc:
                # v5.10.584: a link slow to answer (Dexstreams, its TLS handshake timed out after
                # its first answer took 27 s) is asked once more before the disc goes to Kodi's
                # own reader, whose menus wait on every file (CoreELEC box, 18 Nov 2026)
                if attempt == 0 and 'timed out' in str(exc).lower():
                    accounts.log('disc image read timed out (%s): asked once more' % exc)
                    continue
                accounts.log('disc image not read (%s): it plays as it is' % exc)
                return ''
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


def _JAR_LINK(prefix):
    """A Java menu's own folder (BDMV/JAR/00001/...), whose listing names its files with a leading
    slash (see the listing below); never BDMV/PLAYLIST and the like, whose names libbluray checks."""
    up = str(prefix or '').upper()
    return up.startswith('BDMV/JAR/') and len(up) > len('BDMV/JAR/')


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

        def _image_file(self, disc, entry, head, name=''):
            """One file of a disc image: its bytes are ranges of the image's link (a small
            file is read whole once and kept, v5.10.581)."""
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
            if name and 0 < size <= SMALL_FILE:
                files = disc.get('files') or {}
                twin = _twin(files, name)
                if twin:                        # a backup: the file beside it, read once for both
                    name, entry = twin, files[twin]
                try:
                    self.wfile.write(_small_file(disc['image'], name, entry, _ident(disc))[first:last + 1])
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    accounts.log('disc image read: %s' % exc, True)
                    self.close_connection = True
                return
            at = 0
            try:
                for offset, length in entry.get('x') or []:
                    lo, hi = max(first, at), min(last, at + length - 1)
                    if lo <= hi:
                        _stream_range(disc['image'], offset + lo - at, hi - lo + 1, self.wfile)
                    at += length
                    if at > last:
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass                                    # Kodi moved on (a seek)
            except Exception as exc:
                accounts.log('disc image read: %s' % exc, True)
                self.close_connection = True

        def _proxy(self, key, head):
            """v5.10.581: a remembered link read for Kodi (offer_link), with its Range."""
            import http.client
            with _lock:
                known = _PROXIED.get(key)
            if not known:
                self.wfile.write(self._send(404)) if not head else self._send(404)
                return
            url, headers = _split_link(known[0])
            wanted = str(self.headers.get('Range') or '')
            if wanted:
                headers['Range'] = wanted
            try:
                for _hop in range(4):
                    parts = urllib.parse.urlsplit(url)
                    scheme = (parts.scheme or 'http').lower()
                    where = (scheme, parts.hostname or '', parts.port or (443 if scheme == 'https' else 80))
                    conn, _reused = _connection(where, 30, fresh=True)
                    conn.request('HEAD' if head else 'GET', (parts.path or '/') + ('?' + parts.query if parts.query else ''),
                                 headers=dict(headers, Host=parts.netloc))
                    answer = conn.getresponse()
                    if answer.status in (301, 302, 303, 307, 308) and answer.getheader('Location'):
                        url = urllib.parse.urljoin(url, answer.getheader('Location'))
                        answer.read(); conn.close()
                        continue
                    break
                self.send_response(answer.status)
                for name in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Last-Modified', 'ETag'):
                    value = answer.getheader(name)
                    if value:
                        self.send_header(name, value)
                self.end_headers()
                if not head:
                    while True:
                        block = answer.read(256 * 1024)
                        if not block:
                            break
                        self.wfile.write(block)
                conn.close()
            except (BrokenPipeError, ConnectionResetError):
                pass                                    # Kodi moved on (a seek)
            except Exception as exc:
                accounts.log('link read: %s' % str(exc)[:120], True)
                self.close_connection = True

        def _answer(self, head):
            parts = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).lstrip('/').split('/', 2)
            if len(parts) >= 2 and parts[0] == 'link':
                self._proxy(parts[1], head)
                return
            if len(parts) < 2 or parts[0] != 'disc':
                body = self._send(404)
            else:
                disc = _disc(parts[1])
                name = parts[2] if len(parts) > 2 else ''
                while '//' in name:              # v5.10.584: a Java menu folder's files (_JAR_LINK)
                    name = name.replace('//', '/')
                files = (disc or {}).get('files') or {}
                if name and name not in files:
                    name = {n.lower(): n for n in files}.get(name.lower(), name)
                if disc is None:
                    body = self._send(404)
                elif name in files and disc.get('image'):
                    _warm(parts[1], disc)
                    started = time.time()
                    self._image_file(disc, files[name], head, name)
                    took = time.time() - started
                    # v5.10.581: a menu file that keeps Kodi waiting is named in the log
                    if not head and took > 2.0 and '/stream/' not in name.lower():
                        accounts.log('disc served %s (%s) in %.1f s' % (name, self.headers.get('Range') or 'whole', took))
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
                        # v5.10.584: Kodi names a listed file by the folder's own address and the
                        # link run together (CHTTPDirectory: strBasePath + link), and libbluray asks
                        # for a folder with no slash at its end: every file of BDMV/JAR/00001 was
                        # kept as BDMV/JAR/00001<name>, and Kodi refused to open the real ones (not
                        # in the folder it had just listed). A Java menu with its pictures in a folder
                        # stayed black (The Uprising, CoreELEC box, 18 Nov 2026). Its links start
                        # with a slash Kodi keeps (libbluray asks for name//file, read as name/file).
                        # Each file is listed twice there: by its plain name, which libbluray copies
                        # into its own cache folder, and by the slashed one, which puts the right
                        # address in Kodi's listing so the plain name opens. The slashed name alone
                        # made libbluray's cache folder name end in a slash, which it could not create.
                        links = [(urllib.parse.quote(k), html.escape(k)) for k in kids]
                        if _JAR_LINK(prefix):
                            links += [('//' + q, '/' + n) for q, n in links]
                        page = ('<html><head><title>Index of /%s</title></head><body><pre>' % html.escape(prefix) +
                                ''.join('<a href="%s">%s</a>\n' % pair for pair in links) +
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
        class _Server(ThreadingHTTPServer):
            # v5.10.586: the open connections, so stop() can end the threads that wait on them
            def process_request(self, request, client_address):
                self.open_requests.add(request)
                ThreadingHTTPServer.process_request(self, request, client_address)

            def shutdown_request(self, request):
                self.open_requests.discard(request)
                ThreadingHTTPServer.shutdown_request(self, request)

            def handle_error(self, request, client_address):
                pass                    # Kodi dropping a connection is not an error here
        _server = _Server((HOST, 0), _handler())
        _server.open_requests = set()
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, name='DexHub-disc-server', daemon=True).start()
        xbmcgui.Window(10000).setProperty(PORT_PROP, str(_server.server_address[1]))
        return True
    except Exception as exc:
        accounts.log('disc server not started: %s' % exc, True)
        _server = None
        return False


def stop():
    """v5.10.586: called by the service as Kodi closes. Nothing stopped this server before: its
    thread outlived the service ("still running: DexHub-disc-server"), Kodi killed the service
    after 5 s and then hung after unloading the skin, for 8 minutes, until the box restarted
    (CoreELEC box, every exit since 16 Nov 2026). The connections Kodi keeps open are closed
    too, so their threads leave, and the disc process is told to go."""
    global _server
    server, _server = _server, None
    if server is not None:
        try:
            server.shutdown()           # serve_forever leaves within half a second
        except Exception:
            pass
        import socket
        for request in list(getattr(server, 'open_requests', ())):
            try:
                request.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                request.close()
            except Exception:
                pass
        try:
            server.server_close()
        except Exception:
            pass
    try:
        os.remove(accounts.path('discproc.port'))   # discproc leaves within 2 s once it is gone
    except Exception:
        pass
