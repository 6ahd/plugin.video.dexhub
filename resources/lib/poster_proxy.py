# -*- coding: utf-8 -*-
"""Local HTTP image proxy for poster fallback (v3.9.24).

Inspired by the Plexio (plexio routers/merged_addon.py /proxy/{token}
endpoint) approach: instead of asking Kodi to download a poster from
a remote decoration service (RPDB / BetterPosters / TOP Posters), we
hand Kodi a `http://127.0.0.1:<port>/poster?d=…&c=…` URL that points
to this local server. The server:

  1. Tries the decorated URL upstream (3s budget). If it returns a
     valid image, stream it through to Kodi → user sees badges.
  2. On any failure (timeout, non-2xx, connection error), falls back
     transparently to the clean metahub.space URL → user sees a
     plain poster, but NEVER an empty card.

Why this matters: a chunk of Kodi skins (default Estuary, Confluence,
many community skins) do NOT walk the `poster → thumb → icon` fallback
chain when the primary URL fails. Putting decorated on `poster` and
clean on `thumb` only helps the skins that do walk that chain.
A single-URL local proxy works on every skin because Kodi only ever
sees one URL, which always returns a valid image.

Lifecycle:
  - Started by service.py via `start()` at addon startup.
  - Binds to 127.0.0.1 on a random free port (no external exposure).
  - Port published via Window property `dexhub.poster_proxy.port`.
  - plugin.py reads that property and constructs proxy URLs in
    `_apply_poster_reliability` when the auto mode is active.
  - `stop()` called from service.py when Kodi shuts the addon down.

Failure handling: if the proxy can't start (port conflict, firewall,
sandboxed environment that blocks bind), the property is never set
and plugin.py silently falls back to the v3.9.23 HEAD-probe approach.
There is no scenario in which adding the proxy makes things worse.
"""
import base64
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

import xbmc
import xbmcgui

# ── constants ─────────────────────────────────────────────────────────
PROXY_HOST = '127.0.0.1'
WINDOW_ID  = 10000
PROP_PORT  = 'dexhub.poster_proxy.port'

_CHUNK              = 64 * 1024     # streaming chunk size
_DECORATED_TIMEOUT  = 3.0           # decoration services are slow; allow more
_CLEAN_TIMEOUT      = 4.0           # metahub.space rarely slow but be safe

# ── module-level server state ─────────────────────────────────────────
_server          = None
_server_thread   = None


# ──────────────────────────────────────────────────────────────────────
# request handler
# ──────────────────────────────────────────────────────────────────────
class _ImageProxyHandler(BaseHTTPRequestHandler):
    """Handle GET /poster?d=<b64>&c=<b64>."""

    # Silence default request logging — Kodi captures stdout and the
    # default access-log spam clutters the log.
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != '/poster':
            self.send_error(404, 'not-found')
            return
        qs = parse_qs(parsed.query)
        decorated = self._decode((qs.get('d') or [''])[0])
        clean     = self._decode((qs.get('c') or [''])[0])

        # 1) Try decorated upstream — preserves badges/ratings if it works.
        if decorated and self._try_stream(decorated, _DECORATED_TIMEOUT):
            return

        # 2) Fall back to clean metahub URL — always works (Stremio CDN).
        if clean and self._try_stream(clean, _CLEAN_TIMEOUT):
            return

        # 3) Both upstreams failed — return 404 so Kodi can pick its own
        #    blank placeholder. No silent infinite spinner.
        self.send_error(404, 'upstream-unreachable')

    # ── helpers ───────────────────────────────────────────────────────
    def _decode(self, b64):
        if not b64:
            return ''
        try:
            # URL-safe base64; add padding back if it was stripped at build time.
            return base64.urlsafe_b64decode(b64 + '==' * (-len(b64) % 4)).decode('utf-8')
        except Exception:
            return ''

    def _try_stream(self, url, timeout):
        """Open `url` upstream and stream the bytes back to the client.

        Returns True if the response was successfully streamed; False on
        any error (so the caller can try the fallback URL).
        """
        upstream = None
        try:
            req = Request(url, headers={
                'User-Agent': 'DexHub-poster-proxy/1.0',
                # Identify ourselves so a sysadmin auditing logs can see
                # what's hitting their poster proxy.
            })
            upstream = urlopen(req, timeout=timeout)
            code = upstream.getcode()
            if not (200 <= code < 400):
                return False
            content_type   = upstream.headers.get('Content-Type') or 'image/jpeg'
            content_length = upstream.headers.get('Content-Length')

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            if content_length:
                self.send_header('Content-Length', content_length)
            # Aggressive cache so Kodi's image cache also caches our proxy
            # response — repeated views of the same poster don't re-hit
            # the upstream decoration service.
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()

            while True:
                chunk = upstream.read(_CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)
            return True
        except Exception as exc:
            # Log at debug — these are normal during proxy outages and
            # would otherwise flood the log on every poster.
            xbmc.log('[DexHub] poster-proxy upstream %s failed: %s' %
                     (url[:80], exc), xbmc.LOGDEBUG)
            return False
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except Exception:
                    pass


# ──────────────────────────────────────────────────────────────────────
# lifecycle
# ──────────────────────────────────────────────────────────────────────
def start():
    """Start the proxy server. Idempotent. Returns True on success."""
    global _server, _server_thread
    if _server is not None:
        return True
    try:
        # Pick a free ephemeral port. We bind to 0 so the OS picks; this
        # avoids hard-coded collisions with other Kodi addons.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((PROXY_HOST, 0))
            port = probe.getsockname()[1]

        _server = ThreadingHTTPServer((PROXY_HOST, port), _ImageProxyHandler)
        _server.daemon_threads = True
        _server_thread = threading.Thread(target=_server.serve_forever,
                                            name='DexHub-poster-proxy',
                                            daemon=True)
        _server_thread.start()

        # Publish port to a Window property so plugin.py can find us.
        try:
            xbmcgui.Window(WINDOW_ID).setProperty(PROP_PORT, str(port))
        except Exception:
            pass

        xbmc.log('[DexHub] poster-proxy started on 127.0.0.1:%d' % port,
                 xbmc.LOGINFO)
        return True
    except Exception as exc:
        xbmc.log('[DexHub] poster-proxy failed to start: %s' % exc,
                 xbmc.LOGWARNING)
        _server = None
        _server_thread = None
        return False


def stop():
    """Shut the server down cleanly."""
    global _server, _server_thread
    if _server is None:
        return
    try:
        _server.shutdown()
        _server.server_close()
    except Exception:
        pass
    _server = None
    _server_thread = None
    try:
        xbmcgui.Window(WINDOW_ID).clearProperty(PROP_PORT)
    except Exception:
        pass
    xbmc.log('[DexHub] poster-proxy stopped', xbmc.LOGINFO)


def get_port():
    """Return the active proxy port (int), or None if unavailable.

    Public helper for plugin.py — avoids the caller having to know about
    Window properties.
    """
    try:
        raw = xbmcgui.Window(WINDOW_ID).getProperty(PROP_PORT) or ''
        return int(raw) if raw.isdigit() else None
    except Exception:
        return None


def build_url(decorated_url, clean_url):
    """Build a `http://127.0.0.1:port/poster?d=…&c=…` URL or return ''
    if the proxy is unavailable. Caller chooses what to do on empty
    string (typically: fall back to the v3.9.23 HEAD-probe path).
    """
    if not (decorated_url and clean_url):
        return ''
    port = get_port()
    if not port:
        return ''
    try:
        d_b64 = base64.urlsafe_b64encode(decorated_url.encode('utf-8')).decode('ascii').rstrip('=')
        c_b64 = base64.urlsafe_b64encode(clean_url.encode('utf-8')).decode('ascii').rstrip('=')
    except Exception:
        return ''
    return 'http://%s:%d/poster?d=%s&c=%s' % (PROXY_HOST, port, d_b64, c_b64)
