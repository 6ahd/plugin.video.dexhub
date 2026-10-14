# -*- coding: utf-8 -*-
"""Local copies of Nuvio collection focus art.

Kodi animates a GIF only when it loads the file itself from local disk; a
remote GIF goes through the texture cache, which keeps the first frame. Many
Nuvio "hover" files also carry a .gif name while being JPEG or PNG, and Kodi
picks the decoder from the extension, so those fail as remote URLs. Each file
is therefore downloaded once, its real type is read from the first bytes, and
it is stored under the matching extension. The folder is trimmed to a size
budget, oldest first.
"""
import hashlib
import os
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_MAX_FILE = 16 * 1024 * 1024
_UA = 'Mozilla/5.0 (Kodi; Dex Hub) AppleWebKit/537.36'


def sniff_extension(head):
    head = bytes(head or b'')[:16]
    if head.startswith(b'GIF87a') or head.startswith(b'GIF89a'):
        return '.gif'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if head[:3] == b'\xff\xd8\xff':
        return '.jpg'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    return ''


# Logo answers are kept for the whole Kodi session, across Home openings
# (v5.10.103): the add-on's Python stays loaded between them. A logo that
# could not be reached (timeout, no network) is tried again after two minutes;
# one that answered without an image is not.
_LOGO_STATE = {}        # url -> (ok, monotonic expiry or 0 for the session)
_LOGO_LOCK = threading.Lock()
_LOGO_RETRY = 120.0
_LOGO_MAX = 6000


class LogoCheck(object):
    """Is a title logo URL a real image? Remembered per URL for the session.

    A logo that fails to load leaves the hero with neither the logo nor the
    title text, so a logo from a host that often has none (MetaHub answers
    404 for many titles) is shown only once it has answered with an image.
    The big artwork CDNs are trusted without a request.
    """
    TRUSTED = ('image.tmdb.org', 'fanart.tv', 'artworks.thetvdb.com', 'thetvdb.com/banners',
               'm.media-amazon.com', 'images.plex.tv')

    def __init__(self):
        self._state = _LOGO_STATE
        self._lock = _LOGO_LOCK

    def state(self, url):
        """True / False when known, None when it still has to be checked."""
        url = str(url or '')
        if not url:
            return False
        if not url.startswith(('http://', 'https://')):
            return True     # local file, special:// or image:// wrapped url
        low = url.lower()
        if low.split('?', 1)[0].endswith('.svg'):
            return False    # Kodi cannot draw SVG
        if any(host in low for host in self.TRUSTED):
            return True
        with self._lock:
            hit = self._state.get(url)
        if hit is None:
            return None
        ok, until = hit
        if until and until < time.monotonic():
            return None
        return ok

    def check(self, url, timeout=4.0):
        known = self.state(url)
        if known is not None:
            return known
        ok = False
        until = 0.0
        try:
            req = Request(url, headers={'User-Agent': _UA, 'Range': 'bytes=0-15'})
            with urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, 'status', 200) or 200
                head = resp.read(16)
            ok = 200 <= int(status) < 300 and bool(sniff_extension(head))
        except HTTPError:
            ok = False                      # the host answered: no logo there
        except Exception:
            ok = False
            until = time.monotonic() + _LOGO_RETRY
        with self._lock:
            if len(self._state) > _LOGO_MAX:
                self._state.clear()
            self._state[url] = (ok, until)
        return ok


class MediaCache(object):
    def __init__(self, folder, budget_mb=160, log=None):
        self._folder = folder
        self._budget = max(20, int(budget_mb or 160)) * 1024 * 1024
        self._log = log or (lambda msg: None)
        self._lock = threading.Lock()
        self._inflight = {}
        self._index = {}
        self._failed = {}
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass

    def _stem(self, url):
        return hashlib.sha1(str(url).encode('utf-8')).hexdigest()[:20]

    def local(self, url):
        """Path of an already cached copy, or ''. Cheap; safe on the GUI path."""
        if not url:
            return ''
        with self._lock:
            path = self._index.get(url)
        if path and os.path.exists(path):
            return path
        stem = self._stem(url)
        for ext in ('.gif', '.png', '.jpg', '.webp'):
            path = os.path.join(self._folder, stem + ext)
            if os.path.exists(path):
                with self._lock:
                    self._index[url] = path
                return path
        return ''


    def fetch(self, url, timeout=15.0):
        """Download (once) and return the local path, or '' on failure."""
        if not url or not str(url).startswith(('http://', 'https://')):
            return ''
        cached = self.local(url)
        if cached:
            return cached
        with self._lock:
            failed_at = self._failed.get(url)
            if failed_at and time.time() - failed_at < 900:
                return ''
            event = self._inflight.get(url)
            owner = event is None
            if owner:
                event = threading.Event()
                self._inflight[url] = event
        if not owner:
            event.wait(timeout + 2)
            return self.local(url)
        path = ''
        try:
            req = Request(url, headers={'User-Agent': _UA})
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read(_MAX_FILE + 1)
            if len(data) > _MAX_FILE:
                raise ValueError('file too large')
            ext = sniff_extension(data[:16])
            if not ext:
                raise ValueError('not an image')
            path = os.path.join(self._folder, self._stem(url) + ext)
            tmp = path + '.part'
            with open(tmp, 'wb') as handle:
                handle.write(data)
            os.replace(tmp, path)
            with self._lock:
                self._index[url] = path
        except Exception as exc:
            path = ''
            with self._lock:
                self._failed[url] = time.time()
            self._log('focus art download failed: %s' % exc)
        finally:
            with self._lock:
                self._inflight.pop(url, None)
            event.set()
        if path:
            self._trim()
        return path

    def _trim(self):
        try:
            files = []
            total = 0
            for name in os.listdir(self._folder):
                full = os.path.join(self._folder, name)
                if not os.path.isfile(full) or name.endswith('.part'):
                    continue
                st = os.stat(full)
                files.append((st.st_mtime, st.st_size, full))
                total += st.st_size
            if total <= self._budget:
                return
            for _mtime, size, full in sorted(files):
                try:
                    os.remove(full)
                    total -= size
                except Exception:
                    pass
                if total <= self._budget * 0.8:
                    break
            with self._lock:
                self._index = {k: v for k, v in self._index.items() if os.path.exists(v)}
        except Exception:
            pass
