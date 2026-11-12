# -*- coding: utf-8 -*-
"""The Usenet engine beside Kodi (v5.10.558; inside the app since v5.10.559).

Dex Hub can play a Usenet release straight from the viewer's own NNTP provider: a separate
program (the engine) reads the release's articles and serves the film on this device as an
ordinary address that Kodi plays and seeks in. Measured on a Mac with a 14 GB release: opened
in 2 s, picture in Kodi after 3 s, a seek back to playing in about a second.

The engine is NOT part of Dex Hub and is not in its zip. It is the Usenet engine of NuvioTV
(https://github.com/NuvioMedia/NuvioTV, pull request 4426, GPL-3.0), built unchanged as its own
program. It comes as a Kodi add-on of its own, "Usenet engine (for Dex Hub)"
(script.dexhub.usenet, GPL-3.0, with its licences and source statement), installed from a zip
or a repository like any add-on; its programs are in that add-on's bin/ folder as
  nuvio-usenet-<system>-<processor>
A program placed by hand in addon_data/plugin.video.dexhub/usenet/ under the same name is used
too. Dex Hub only starts it and talks to it over 127.0.0.1, as any program could; none of
NuvioTV's code is in Dex Hub.

Two sides:
- the service owns the process (service_loop): the engine stops when its parent's pipe closes,
  so a plugin call, which ends in seconds, cannot own it. A plugin call asks by setting a Home
  property; the service starts the engine and publishes its port and token the same way.
- a plugin call uses it (open, close): one release becomes one session with a stream address.
The engine listens on 127.0.0.1 only, on a random port, and its control calls need the token.

Inside the app (v5.10.559): Apple TV lets no app start a second program, so there the engine
is a library in the app and the engine add-on (script.dexhub.usenet, GPL-3.0) loads it in its
own service. That add-on says it is there with the Home property dexhub.usenet.inapp; Dex Hub
asks it to start and stop through Home properties (_AppEngine) and talks to the engine over
127.0.0.1 exactly as elsewhere. Dex Hub itself never loads the library.
"""
import json
import os
import platform
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import xbmc
import xbmcgui
import xbmcvfs

FOLDER = 'special://profile/addon_data/plugin.video.dexhub/usenet/'
ENGINE_ADDON = 'script.dexhub.usenet'
WANT, PORT, TOKEN, SESSION, STATE = ('dexhub.usenet.want', 'dexhub.usenet.port', 'dexhub.usenet.token',
                                     'dexhub.usenet.session', 'dexhub.usenet.state')
INAPP, APP_START, APP_STOP, APP_PORT, APP_STATE = ('dexhub.usenet.inapp', 'dexhub.usenet.app.start',
                                                   'dexhub.usenet.app.stop', 'dexhub.usenet.app.port',
                                                   'dexhub.usenet.app.state')
_IDLE_STOP = 600            # the engine stops ten minutes after its last session ended
_SESSION_IDLE = 45          # a session whose film is not playing any more is closed


class UsenetError(Exception):
    """A release that did not open; kind is the engine's own word for it ('provider', ...)."""

    def __init__(self, message, kind=''):
        Exception.__init__(self, message)
        self.kind = kind


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] Usenet engine: %s' % msg, level)


def _home():
    return xbmcgui.Window(10000)


def binary_name():
    """nuvio-usenet-darwin-arm64, nuvio-usenet-linux-arm64, ... ('' where it cannot run)."""
    if xbmc.getCondVisibility('System.Platform.Android') or xbmc.getCondVisibility('System.Platform.IOS') \
            or xbmc.getCondVisibility('System.Platform.TVOS'):
        return ''           # these systems do not let an add-on start a program of its own
    system = 'darwin' if sys.platform == 'darwin' else 'windows' if sys.platform.startswith('win') else 'linux'
    machine = platform.machine().lower()
    arch = 'arm64' if machine in ('arm64', 'aarch64') else 'amd64' if machine in ('x86_64', 'amd64') else machine
    return 'nuvio-usenet-%s-%s%s' % (system, arch, '.exe' if system == 'windows' else '')


def binary():
    """The engine's path on this device, '' when it is not there."""
    name = binary_name()
    if not name:
        return ''
    # the engine add-on first (when it is installed and switched on), then a program put by hand
    places = [os.path.join(xbmcvfs.translatePath(FOLDER), name)]
    if xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % ENGINE_ADDON):
        places.insert(0, os.path.join(xbmcvfs.translatePath('special://home/addons/%s/bin/' % ENGINE_ADDON), name))
    for path in places:
        if os.path.isfile(path):
            return path
    return ''


def in_app():
    """True where the engine is a library inside the app and its add-on's service is up."""
    return _home().getProperty(INAPP) == '1'


def available():
    return bool(binary()) or in_app()


def state_note():
    """What the engine add-on said about its last start inside the app ('' when nothing)."""
    return _home().getProperty(APP_STATE)


# ------------------------------------------------------------ the service's side

class _Engine(object):
    def __init__(self):
        self.proc = None
        self.port = 0
        self.token = ''
        self.idle_since = time.time()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        path = binary()
        if not path:
            return False
        try:
            if not os.access(path, os.X_OK):
                os.chmod(path, 0o755)
        except OSError:
            pass
        roots = []
        try:
            with open(xbmcvfs.translatePath('special://xbmc/system/certs/cacert.pem'), 'r', encoding='utf-8',
                      errors='replace') as handle:
                roots = [handle.read()]         # Kodi's own trust roots: a box may have none of its own
        except Exception:
            pass
        cache = os.path.join(xbmcvfs.translatePath(FOLDER), 'nzb-cache')
        self.token = secrets.token_hex(32)
        env = dict(os.environ, GODEBUG='madvdontneed=1')
        try:
            self.proc = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, env=env)
            self.proc.stdin.write(json.dumps({'token': self.token, 'certificates': roots, 'memoryMiB': 256,
                                              'nzbCacheDir': cache}).encode('utf-8') + b'\n')
            self.proc.stdin.flush()             # the pipe stays open: its closing stops the engine
            ready = {}
            reader = threading.Thread(target=lambda: ready.update(json.loads(self.proc.stdout.readline() or b'{}')),
                                      daemon=True)
            reader.start()
            reader.join(10)
            self.port = int(ready.get('port') or 0)
        except Exception as exc:
            _log('did not start: %s' % exc, xbmc.LOGWARNING)
            self.stop()
            return False
        if not self.port:
            _log('gave no port', xbmc.LOGWARNING)
            self.stop()
            return False
        self.idle_since = time.time()
        _log('started on port %d (protocol %s)' % (self.port, ready.get('protocol')))
        return True

    def stop(self):
        proc, self.proc, self.port = self.proc, None, 0
        if proc is None:
            return
        try:
            proc.stdin.close()                  # its own way to stop
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class _AppEngine(object):
    """The engine inside the app: its add-on owns the library; start and stop are asked for
    through Home properties and answered the same way."""

    proc = None

    def __init__(self):
        self.port = 0
        self.token = ''
        self.idle_since = time.time()

    def running(self):
        return bool(self.port) and _home().getProperty(APP_PORT) == str(self.port)

    def start(self):
        home = _home()
        self.token = secrets.token_hex(32)
        home.clearProperty(APP_PORT)
        home.clearProperty(APP_STOP)
        home.setProperty(APP_STATE, '')
        home.setProperty(APP_START, self.token)
        started = time.time()
        while time.time() - started < 20:
            port = home.getProperty(APP_PORT)
            if port.isdigit() and int(port) > 0:
                self.port = int(port)
                self.idle_since = time.time()
                _log('started inside the app on port %d' % self.port)
                return True
            if home.getProperty(APP_STATE).startswith('failed'):
                break
            xbmc.sleep(100)
        home.clearProperty(APP_START)
        _log('did not start inside the app: %s' % (home.getProperty(APP_STATE) or 'no answer'), xbmc.LOGWARNING)
        return False

    def stop(self):
        if self.port:
            _home().setProperty(APP_STOP, '1')
        self.port = 0


def service_loop(monitor):
    """Run by the service: starts the engine when a plugin call asks, publishes where it is,
    closes a session whose film stopped, and stops the engine after ten idle minutes."""
    home, engine, player = _home(), _Engine(), xbmc.Player()
    for key in (WANT, PORT, TOKEN, SESSION, STATE):
        home.clearProperty(key)
    not_playing_since = 0.0
    while not monitor.abortRequested():
        try:
            if home.getProperty(WANT) and not engine.running():
                if not binary() and in_app():
                    if not isinstance(engine, _AppEngine):
                        engine = _AppEngine()
                elif isinstance(engine, _AppEngine):
                    engine = _Engine()
                home.setProperty(STATE, 'starting')
                if engine.start():
                    home.setProperty(TOKEN, engine.token)
                    home.setProperty(PORT, str(engine.port))
                    home.setProperty(STATE, 'ready')
                else:
                    home.setProperty(STATE, 'failed')
                home.clearProperty(WANT)
            elif home.getProperty(WANT):
                home.clearProperty(WANT)
            if engine.proc is not None and not engine.running():
                _log('stopped by itself (code %s)' % engine.proc.poll(), xbmc.LOGWARNING)
                engine.stop()
                for key in (PORT, TOKEN, SESSION, STATE):
                    home.clearProperty(key)
            if engine.running():
                session = home.getProperty(SESSION)
                if session:
                    engine.idle_since = time.time()
                    if player.isPlaying():
                        not_playing_since = 0.0
                    else:
                        not_playing_since = not_playing_since or time.time()
                        if time.time() - not_playing_since > _SESSION_IDLE:
                            _request(engine.port, engine.token, 'DELETE', '/sessions/' + session.split('|', 1)[0], seconds=5)
                            home.clearProperty(SESSION)
                            not_playing_since = 0.0
                            _log('the session of a film that stopped was closed')
                elif time.time() - engine.idle_since > _IDLE_STOP:
                    _log('idle for ten minutes: stopped')
                    engine.stop()
                    for key in (PORT, TOKEN, STATE):
                        home.clearProperty(key)
        except Exception as exc:
            _log('service loop: %s' % exc, xbmc.LOGWARNING)
        if monitor.waitForAbort(0.5):
            break
    engine.stop()


# ------------------------------------------------------------ a plugin call's side

def _request(port, token, method, path, body=None, seconds=120):
    request = urllib.request.Request('http://127.0.0.1:%d%s' % (int(port), path), method=method,
                                     data=json.dumps(body).encode('utf-8') if body is not None else None,
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=seconds) as answer:
            text = answer.read().decode('utf-8', 'replace')
            return answer.status, (json.loads(text) if text.strip().startswith('{') else text), {}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8', 'replace')[:300].strip(), dict(exc.headers)


def ensure(seconds=25):
    """(port, token) of the running engine, started by the service when needed; None when it
    is not on this device or did not start."""
    if not available():
        return None
    home = _home()
    started = time.time()
    asked = False
    while time.time() - started < seconds:
        port, token = home.getProperty(PORT), home.getProperty(TOKEN)
        if port and token:
            try:
                status, _answer, _headers = _request(port, token, 'GET', '/health', seconds=3)
                if status == 200:
                    return int(port), token
            except Exception:
                pass
            home.clearProperty(PORT)            # a port nobody answers on: ask again
        if home.getProperty(STATE) == 'failed' and asked:
            return None
        if not asked or not home.getProperty(WANT):
            home.setProperty(STATE, '')
            home.setProperty(WANT, '1')
            asked = True
        xbmc.sleep(250)
    return None


def open_release(nzb_url, servers, season=None, episode=None, headers=None, private=False):
    """One release as a stream: {'url', 'filename', 'size'}. Raises UsenetError."""
    where = ensure()
    if not where:
        raise UsenetError('the Usenet engine is not on this device or did not start', 'engine')
    port, token = where
    home = _home()
    body = {'nzbUrl': nzb_url, 'servers': list(servers), 'keepSessionId': '',
            'config': {'profile': 'balanced', 'fastMkvStartup': True, 'fastNzbFetch': True, 'cacheNzb': True,
                       'holeFilling': True, 'allowPrivateNetwork': bool(private)}}
    if headers:
        body['headers'] = dict(headers)
    if season is not None and episode is not None:
        body['season'], body['episode'] = int(season), int(episode)
    started = time.time()
    status, answer, got = 0, '', {}
    for _try in range(8):                       # 409: another release is still opening
        status, answer, got = _request(port, token, 'POST', '/sessions', body)
        if status != 409:
            break
        xbmc.sleep(500)
    if status != 200 or not isinstance(answer, dict):
        raise UsenetError(str(answer) or 'the release did not open (%s)' % status,
                          got.get('X-Usenet-Failure') or got.get('x-usenet-failure') or '')
    home.setProperty(SESSION, '%s|%d' % (answer.get('id'), int(time.time())))
    _log('%s opened in %.1f s (%.1f GB)' % (str(answer.get('filename'))[:70], time.time() - started,
                                            float(answer.get('size') or 0) / 1073741824.0))
    return {'url': 'http://127.0.0.1:%d%s' % (port, answer.get('path')), 'filename': answer.get('filename') or '',
            'size': int(answer.get('size') or 0), 'id': answer.get('id')}
