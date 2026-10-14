# -*- coding: utf-8 -*-
"""Grids and title lists worked out by the service (v5.10.132).

A plugin call starts a fresh Python (reuselanguageinvoker is off: Kodi's
reuse races when calls overlap, rows came back empty in the test harness)
and a grid had to import the whole router first: about 0.45 s on a PC before
any network, several times that on an Amlogic box, on every page opened.
The service has all of it loaded already. While skin.dexhub (or Arctic Fuse 3
with Dex Hub's pages) is in use, the skin worker runs a small server on
127.0.0.1; a grid or title list call hands its parameters to it, the service
runs the very same route with the listing captured (items.capture), and the
call only turns the items into Kodi's list (as skin_row does from its
cache). Anything wrong (no service, an error, a timeout) and the call works
it out itself, as before.

Server address: Home window property dhs.listing = "<port>:<token>" (the
token keeps other programs on the box out).

v5.10.133: request and answer are marshal data behind a 4 byte length (both
ends are Kodi's own Python): the call no longer imports json, which brings
re, enum, functools and collections along (a good part of a call's imports).
"""
import _socket
import marshal
import os
import time

import xbmc

from . import common as C

PROP = 'dhs.listing'
# the listings the service works out (serve.run hands them over)
ROUTES = ('skin_list', 'skin_folder', 'skin_seasons', 'skin_episodes', 'skin_cast', 'skin_related')
_CONNECT = 0.6          # seconds to reach the service
_ANSWER = 90.0          # a slow server's catalog can take this long
_MAX_REQUEST = 256 * 1024
_MAX_ANSWER = 64 * 1024 * 1024
_WORKERS = 3


# ----------------------------------------------------------- the call side
def _address():
    raw = C.prop(PROP)
    if not raw or ':' not in raw:
        return None, ''
    port, _, token = raw.partition(':')
    try:
        return int(port), token
    except ValueError:
        return None, ''


def _send(conn, value):
    body = marshal.dumps(value)
    conn.sendall(len(body).to_bytes(4, 'big') + body)


def _receive(conn, limit):
    """One length-prefixed marshal value from ``conn`` (ValueError when cut
    short or too long)."""
    head = b''
    while len(head) < 4:
        data = conn.recv(4 - len(head))
        if not data:
            raise ValueError('closed')
        head += data
    size = int.from_bytes(head, 'big')
    if size > limit:
        raise ValueError('too long')
    chunks, got = [], 0
    while got < size:
        data = conn.recv(min(262144, size - got))
        if not data:
            raise ValueError('cut short')
        chunks.append(data)
        got += len(data)
    return marshal.loads(b''.join(chunks))


def _plain(value):
    """What marshal can carry, else None (a ListItem kept by a capture)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return dict((str(k), _plain(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return None


def ask(action, params):
    """The captured listing of ``action`` from the service, or None."""
    port, token = _address()
    if not port:
        return None
    started = time.monotonic()
    # the socket module itself is not imported (it brings enum and selectors)
    conn = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        conn.settimeout(_CONNECT)
        conn.connect(('127.0.0.1', port))
    except OSError:
        conn.close()
        return None
    try:
        conn.settimeout(_ANSWER)
        _send(conn, {'t': token, 'a': action, 'p': dict((str(k), str(v)) for k, v in params.items())})
        reply = _receive(conn, _MAX_ANSWER)
        if not isinstance(reply, dict):
            raise ValueError('not a listing')
    except (OSError, ValueError, EOFError, TypeError) as exc:
        C.log('%s: the service did not answer (%s), worked out here' % (action, exc), xbmc.LOGWARNING)
        return None
    finally:
        try:
            conn.close()
        except OSError:
            pass
    if not reply.get('ok'):
        C.log('%s: the service could not (%s), worked out here' % (action, reply.get('e') or '?'), xbmc.LOGWARNING)
        return None
    box = reply.get('d') or {}
    C.log('%s: %d item(s) from the service in %.0f ms' % (
        action, len(box.get('items') or []), (time.monotonic() - started) * 1000))
    return box


def serve_call(action, params, handle):
    """serve.run's way in: True when the service's listing went to Kodi."""
    if handle < 0 or action not in ROUTES or params.get('dh_here'):
        return False
    box = ask(action, params)
    if box is None or 'items' not in box:
        return False
    from . import items as I
    kw = box.get('kw') or {}
    I.directory(handle, box.get('items') or [], tab=kw.get('tab') or '', content=kw.get('content') or 'videos',
                props=kw.get('props') or None, row=kw.get('row') or '', kodi_clicks=kw.get('kodi_clicks') or False)
    return True


# ------------------------------------------------------------ the service
def _work(action, params):
    """Run the route with its listing captured (in the service)."""
    from .. import settings_cache
    from . import items as I
    settings_cache.invalidate()
    with I.capture() as box:
        if action in ('skin_seasons', 'skin_episodes', 'skin_cast', 'skin_related'):
            from . import title
            title.listing(action, params, I.CAPTURE_HANDLE)
        else:
            from . import actions
            actions.run(action, params, I.CAPTURE_HANDLE)
    if not box.get('done'):
        raise RuntimeError('no listing')
    if box.get('failed'):
        raise RuntimeError('the route failed')
    return {'items': box.get('items') or [], 'kw': box.get('kw') or {}}


def _warm(monitor):
    """v5.10.133: the modules a grid or a title list needs are loaded once
    the Home has settled, so the first grid opened is as quick as the next
    ones (they were imported by the first call: about 0.2 s on a PC)."""
    if monitor.waitForAbort(6.0):
        return
    started = time.monotonic()
    try:
        import importlib
        for name in ('.actions', '.items', '.title', '..capture', '..homeui.grid_filters', '..homeui.rows'):
            importlib.import_module(name, __package__)
        C.log('listing modules ready in %.0f ms' % ((time.monotonic() - started) * 1000), xbmc.LOGDEBUG)
    except Exception as exc:
        C.log('listing modules not loaded ahead: %s' % exc, xbmc.LOGDEBUG)


_SERVER = []


def Server(monitor):
    """The service's listing server (skinui/worker.py starts and stops it);
    its class is made on first use, so a call never imports threading or socket."""
    if not _SERVER:
        _SERVER.append(_server_class())
    return _SERVER[0](monitor)


def _server_class():
    import socket
    import threading

    class _Server(threading.Thread):

        def __init__(self, monitor):
            super(_Server, self).__init__(name='dexhub-skin-list')
            self.daemon = True
            self.monitor = monitor
            self._halt = threading.Event()
            self._slots = threading.Semaphore(_WORKERS)
            self.token = os.urandom(12).hex()
            self.sock = None

        def _clear(self):
            if C.prop(PROP).endswith(':' + self.token):
                C.set_prop(PROP, '')

        def stop(self):
            self._halt.set()
            self._clear()
            try:
                if self.sock is not None:
                    self.sock.close()
            except OSError:
                pass

        def run(self):
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.bind(('127.0.0.1', 0))
                self.sock.listen(8)
                self.sock.settimeout(1.0)
            except OSError as exc:
                C.log('listing server not started: %s' % exc, xbmc.LOGWARNING)
                return
            C.set_prop(PROP, '%d:%s' % (self.sock.getsockname()[1], self.token))
            C.log('listing server on')
            threading.Thread(target=_warm, args=(self.monitor,), name='dexhub-skin-list-warm', daemon=True).start()
            while not self._halt.is_set() and not self.monitor.abortRequested():
                try:
                    conn, _addr = self.sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._serve, args=(conn,), name='dexhub-skin-list-call', daemon=True).start()
            self._clear()
            try:
                self.sock.close()
            except OSError:
                pass

        def _serve(self, conn):
            started = time.monotonic()
            action = '?'
            try:
                conn.settimeout(5.0)
                request = _receive(conn, _MAX_REQUEST)
                if not isinstance(request, dict) or request.get('t') != self.token:
                    return
                action = str(request.get('a') or '')
                if action not in ROUTES:
                    reply = {'ok': False, 'e': 'unknown listing'}
                else:
                    with self._slots:
                        try:
                            reply = {'ok': True, 'd': _work(action, request.get('p') or {})}
                        except Exception as exc:
                            import traceback
                            C.log('listing %s failed in the service:\n%s' % (action, traceback.format_exc()),
                                  xbmc.LOGWARNING)
                            reply = {'ok': False, 'e': str(exc) or exc.__class__.__name__}
                conn.settimeout(30.0)
                _send(conn, _plain(reply))
                C.log('listing %s in the service: %.0f ms' % (action, (time.monotonic() - started) * 1000),
                      xbmc.LOGDEBUG)
            except Exception as exc:
                C.log('listing %s: %s' % (action, exc), xbmc.LOGWARNING)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    return _Server
