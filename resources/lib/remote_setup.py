# -*- coding: utf-8 -*-
"""v5.10.374: this TV's setup page from anywhere (the hub's relay).

The page the phone opens on the home network (phone_setup: accounts, source
add-ons, the Home's rows with their pictures, collections, the look,
subtitles, the player) also opens on the hub's site, for the account this TV
is linked to, at <hub>/tv/<device>/: no QR, no same network. The hub keeps
nothing; it hands each request of the page to this TV and the answer back.

Here: a thread of the service asks the hub for work (a long-poll, held up to
25 s, in a thread of its own so Kodi's exit never waits on it) while the TV
is linked, and answers each request as the home network page does, with the
same Session. Changes reach the Home a few seconds after the last one, as when
the phone page closes.

Never from afar (the hub refuses them too): a full backup file, a restore,
uploads and Kodi's file manager. They stay on the home network page.
"""
import base64
import json
import threading
import time

import xbmc

GET = ('', 'api/state')
POST = {'api/profile': 'profile', 'api/set': 'set', 'api/addon': 'addon', 'api/login': 'login', 'api/code': 'code',
        'api/tv': 'tv', 'api/logout': 'logout', 'api/home': 'home', 'api/done': 'finish', 'api/silo': 'silo',
        'api/pref': 'pref', 'api/collection': 'collection', 'api/covers': 'covers'}
# v5.10.432: collections and covers from the DexCloud site (the hub lets them
# through since 2.12; without them here the site said "Update Dex Hub").
IDLE = 30 * 60          # a page not used for this long starts a new session
SETTLE = 4.0            # seconds after the last change: the Home reads it
_STATE = {'thread': None}


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] remote setup: %s' % msg, level)


class Remote(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.session = None
        self.used = 0.0
        self.published = 0       # the session's changes already sent to the Home
        self.page = None

    def _session(self):
        from . import phone_setup
        with self.lock:
            now = time.time()
            if self.session is None or self.session.done or now - self.used > IDLE:
                self._settle(force=True)
                self.session = phone_setup.Session()
                self.published = 0
            self.used = now
            return self.session

    def _json(self, status, data):
        return status, 'application/json; charset=utf-8', json.dumps(data, ensure_ascii=False).encode('utf-8')

    def handle(self, req):
        """(status, content type, bytes) for one request of the page."""
        from . import phone_setup
        from . import profile_scope as _scope
        method = str(req.get('method') or 'GET').upper()
        route = str(req.get('route') or '')
        if method == 'GET':
            if route == '':
                if self.page is None:
                    self.page = phone_setup._page()
                self._session()
                return 200, 'text/html; charset=utf-8', self.page
            if route == 'api/state':
                return self._json(200, self._session().state())
            if route.startswith('api/img/'):
                try:
                    data, ctype = phone_setup.IMAGES.read(route[len('api/img/'):])
                except Exception:
                    data, ctype = None, ''
                return (200, ctype, data) if data else self._json(404, {'ok': False})
            return self._json(404, {'ok': False})
        name = POST.get(route)
        if method != 'POST' or not name:
            return self._json(404, {'ok': False, 'error': 'This works on the home network page only.'})
        if str(req.get('profile') or '') != _scope.active_key():
            return self._json(409, {'ok': False, 'error': 'Profile changed. Refresh this page before saving.'})
        try:
            body = json.loads(req.get('body') or '{}')
        except ValueError:
            return self._json(400, {'ok': False, 'error': 'bad request'})
        session = self._session()
        result = getattr(session, name)(body if isinstance(body, dict) else {})
        with session.lock:
            session.changes += 1
        if name == 'finish':
            self._settle(force=True)
        return self._json(200, result)

    def _settle(self, force=False):
        """The Home reads the changes (a few seconds after the last one)."""
        session = self.session
        if session is None:
            return
        with session.lock:
            changes, seen, refresh = session.changes, session.seen, session.refresh_after
        if changes <= self.published or (not force and time.time() - max(seen, self.used) < SETTLE):
            return
        self.published = changes
        from . import phone_setup
        if refresh:
            try:
                from .routes.simple_entry import _refresh_continue
                _refresh_continue()
            except Exception:
                pass
        phone_setup._republish('phone')
        _log('%d change(s) from the account page' % changes)

    def tick(self):
        with self.lock:
            self._settle()


def _play(item):
    """v5.10.433: Play on Kodi from a Telegram message (the hub's /play page):
    the title's source search, as the context menu's Search sources starts it."""
    from urllib.parse import urlencode
    if not isinstance(item, dict):
        return
    tmdb = str(item.get('tmdb') or '')
    kind = 'episode' if item.get('type') == 'episode' else 'movie'
    title = str(item.get('title') or '')[:120]
    if not tmdb.isdigit() or len(tmdb) > 9:
        return
    params = {'action': 'tmdb_player', 'play_mode': 'search', 'video_type': kind, 'tmdb_id': tmdb}
    if kind == 'episode':
        season, episode = item.get('season'), item.get('episode')
        if not (isinstance(season, int) and isinstance(episode, int) and season >= 0 and episode > 0):
            return
        params.update({'showname': title, 'season': str(season), 'episode': str(episode)})
    else:
        params['title'] = title
        year = str(item.get('year') or '')
        if year.isdigit() and len(year) == 4:
            params['year'] = year
    if xbmc.getCondVisibility('System.ScreenSaverActive'):
        xbmc.executebuiltin('Action(noop)')      # wake the screen first
    _log('play from the hub: %s %s' % (kind, tmdb))
    xbmc.executebuiltin('RunPlugin(plugin://plugin.video.dexhub/?%s)' % urlencode(params))


def _serve(remote, req):
    from . import hub_client
    if req.get('method') == 'NOTICE':
        # v5.10.375: a word from the hub (device sync), nothing to answer
        notice = req.get('notice') or {}
        if isinstance(notice, dict) and notice.get('kind') == 'play':
            _play(notice.get('item'))
            return
        try:
            from . import cloud_profile
            cloud_profile.on_notice(notice)
        except Exception as exc:
            _log('notice: %s' % exc)
        return
    try:
        status, ctype, data = remote.handle(req)
    except Exception as exc:
        import traceback
        _log('%s %s: %s' % (req.get('method'), req.get('route'), traceback.format_exc()), xbmc.LOGWARNING)
        status, ctype, data = 200, 'application/json; charset=utf-8', json.dumps(
            {'ok': False, 'error': str(exc) or exc.__class__.__name__}).encode('utf-8')
    try:
        hub_client.request('POST', '/api/device/relay/reply', {
            'id': req.get('id'), 'status': status, 'ctype': ctype,
            'body': base64.b64encode(data or b'').decode('ascii')}, timeout=40)
    except Exception as exc:
        _log('reply not sent: %s' % exc)
    if req.get('method') == 'POST':
        timer = threading.Timer(SETTLE + 0.5, remote.tick)
        timer.daemon = True
        timer.start()


def _wait(monitor):
    """The hub's answer (requests), None when Kodi is closing."""
    from . import hub_client
    return hub_client.long_poll('/api/device/relay/wait', monitor, timeout=35)


def run(monitor):
    """While this TV is linked: answer the account page's requests."""
    from . import hub_client
    remote = Remote()
    failures = 0
    _log('ready')
    try:
        while not monitor.abortRequested() and hub_client.linked():
            try:
                answer = _wait(monitor)
                if answer is None:
                    break
                failures = 0
            except Exception as exc:
                failures += 1
                status = getattr(exc, 'status', 0)
                # an older hub (404) or a hiccup: ask again later
                pause = 600 if status == 404 else min(120, 5 * failures)
                if monitor.waitForAbort(pause):
                    break
                continue
            for req in (answer.get('requests') or [])[:16]:
                # a daemon thread each (a pool's threads are joined at exit:
                # Kodi's exit then waited on the service)
                threading.Thread(target=_serve, args=(remote, req), name='DexHub-relay-req', daemon=True).start()
            remote.tick()
    finally:
        try:
            remote.tick()
        except Exception:
            pass
        _STATE['thread'] = None
        _log('stopped')


def ensure(monitor):
    """The relay thread, started once while linked (hub_worker)."""
    th = _STATE.get('thread')
    if th is not None and th.is_alive():
        return
    th = threading.Thread(target=run, args=(monitor,), name='DexHub-relay', daemon=True)
    _STATE['thread'] = th
    th.start()
