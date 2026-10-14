# -*- coding: utf-8 -*-
"""The focused title of a Dex Hub grid in skin.dexhub (v5.10.114).

The add-on's own grid gives the focused title its plot, logo and backdrop
from TMDb (in the user's language) as the focus reaches it; skin.dexhub's
grids are Kodi's Videos window, listed once, so the service does it here:
it follows the focus while a Dex Hub grid is on show and publishes the
focused title's data (Home properties dhs.gh.*, the skin's grid hero shows
them while dhs.gh.id names the focused card). What it finds is kept
(grids/enriched.json): the next listing of a grid starts with it. A grid's
titles not yet looked at are looked at in the background afterwards.
"""
import json
import threading
import time

import xbmc

from . import common as C

KEEP = 600              # titles remembered
_FILE = 'enriched.json'
_WANT = 'want.json'
_PROPS = ('id', 'logo', 'plot', 'fanart', 'meta', 'rating', 'title')
_LOCK = threading.Lock()


def _path(name):
    return C.folder('grids') + '/' + name


def load():
    data = C.read_json(_path(_FILE), {}) or {}
    return data if isinstance(data, dict) else {}


def _save(data):
    if len(data) > KEEP:
        ordered = sorted(data.items(), key=lambda kv: kv[1].get('t', 0))
        data = dict(ordered[-KEEP:])
    C.write_json(_path(_FILE), data)


def gid_of(src):
    return C.short_id('%s|%s|%s|%s' % (src.get('media_type') or '', src.get('imdb_id') or '',
                                       src.get('tmdb_id') or '', src.get('path') or src.get('title') or ''))


def prepare(items, language_key):
    """A grid listing's items: each title card names itself (dhs.gid) and
    what the service needs to look it up (dhs.src); what is already known
    of it is applied now. Returns the titles still to look up."""
    known = load()
    todo = []
    for item in items:
        props = item.get('props') or {}
        src = item.get('src') or {}
        if props.get('kind') != 'work' or not src:
            continue
        gid = gid_of(src)
        props['dhs.gid'] = gid
        info = item.get('info') or {}
        art = item.get('art') or {}
        payload = dict(src)
        payload.update({'plot': info.get('plot') or '', 'genres': list(info.get('genres') or []),
                        'duration': info.get('duration') or 0, 'rating': info.get('rating') or 0,
                        'mpaa': info.get('mpaa') or '', 'poster': art.get('poster') or '',
                        'fanart': art.get('fanart') or '', 'landscape': art.get('landscape') or '',
                        'clearlogo': art.get('clearlogo') or ''})
        props['dhs.src'] = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        item['props'] = props
        found = known.get(gid)
        if found and found.get('lang') == language_key:
            apply(item, found)
        else:
            todo.append(payload)
    return todo


def apply(item, found):
    info = item.setdefault('info', {})
    art = item.setdefault('art', {})
    props = item.setdefault('props', {})
    if found.get('plot'):
        info['plot'] = found['plot']
    if found.get('logo'):
        art['clearlogo'] = found['logo']
    if found.get('fanart'):
        art['fanart'] = found['fanart']
        if not art.get('landscape'):
            art['landscape'] = found['fanart']
    if found.get('poster') and not art.get('poster'):
        art['poster'] = found['poster']
        art['thumb'] = art.get('thumb') or found['poster']
    if found.get('meta'):
        props['dhs.meta'] = found['meta']
    if found.get('rating'):
        props['dhs.rating'] = found['rating']


def want(todo):
    """Titles of a grid just listed, looked up in the background."""
    if not todo:
        return
    C.write_json(_path(_WANT), {'t': time.time(), 'items': todo[:40]})
    C.set_prop('dhs.gh.want', '%.3f' % time.time())


class GridFocus(threading.Thread):
    """Service thread (skinui/worker.py, skin.dexhub only)."""

    POLL = 0.2
    SETTLE = 0.18

    def __init__(self, monitor, busy):
        super(GridFocus, self).__init__(name='dexhub-skin-grid')
        self.daemon = True
        self.monitor = monitor
        self.busy = busy                # a video plays: no background lookups
        self._halt = threading.Event()
        self.current = ''
        self.known = None
        self.ctx = None
        self.enricher = None
        self._want_token = ''

    def stop(self):
        self._halt.set()

    def done(self):
        return self._halt.is_set() or self.monitor.abortRequested()

    def run(self):
        try:
            while not self.done():
                if not self.on_grid():
                    if self.current:
                        self.current = ''
                    self.background()
                    self._halt.wait(C.poll_wait(1.0, slow=2.0))
                    continue
                gid = xbmc.getInfoLabel('ListItem.Property(dhs.gid)')
                if gid and gid != self.current:
                    self._halt.wait(self.SETTLE)
                    if xbmc.getInfoLabel('ListItem.Property(dhs.gid)') != gid:
                        continue
                    self.current = gid
                    self.focus(gid, xbmc.getInfoLabel('ListItem.Property(dhs.src)'))
                    continue
                if not gid:
                    self.background()
                self._halt.wait(C.poll_wait(self.POLL))
        except Exception:
            import traceback
            C.log('grid focus stopped:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    @staticmethod
    def on_grid():
        try:
            from .. import guistate     # v5.10.140: no getCondVisibility (guistate.py)
            return (guistate.active() == guistate.VIDEOS
                    and bool(xbmc.getInfoLabel('Container.Property(dhs.grid)')))
        except Exception:
            return False

    # ------------------------------------------------------------ lookups
    def _setup(self):
        if self.ctx is None:
            from . import rows
            self.ctx = rows.Context('all')
            self.enricher = rows.HeadlessEnricher(rows.app())
        return self.ctx.language or '-'

    def _known(self):
        if self.known is None:
            self.known = load()
        return self.known

    def focus(self, gid, src_text):
        found = self._known().get(gid)
        language = self._setup()
        if found and found.get('lang') == language:
            self.publish(gid, found)
            return
        try:
            src = json.loads(src_text or '{}')
        except Exception:
            src = {}
        if not src:
            return
        found = self.lookup(src, language)
        if found:
            self.publish(gid, found)

    def lookup(self, src, language):
        gid = gid_of(src)
        tile = {'kind': 'work', 'media_type': src.get('media_type') or '', 'title': src.get('title') or '',
                'show': src.get('show') or '', 'year': src.get('year') or '', 'path': src.get('path') or '',
                'imdb_id': src.get('imdb_id') or '', 'tmdb_id': src.get('tmdb_id') or '',
                'plot': src.get('plot') or '', 'genres': list(src.get('genres') or []),
                'duration': src.get('duration') or 0, 'rating': src.get('rating') or 0,
                'mpaa': src.get('mpaa') or '', 'poster': src.get('poster') or '',
                'fanart': src.get('fanart') or '', 'landscape': src.get('landscape') or '',
                'clearlogo': src.get('clearlogo') or ''}
        try:
            self.enricher._enrich_now(tile, '' if language == '-' else language)
        except Exception:
            return None
        merged = {'kind': 'work', 'year': tile.get('year') or '', 'rating': tile.get('rating') or 0,
                  'duration': tile.get('duration') or 0, 'genres': list(tile.get('genres') or [])[:3],
                  'mpaa': tile.get('mpaa') or ''}
        found = {'lang': language, 't': time.time(),
                 'plot': tile.get('plot') or '', 'logo': tile.get('clearlogo') or '',
                 'fanart': tile.get('fanart') or '', 'poster': tile.get('poster') or '',
                 'meta': self.ctx.meta(merged), 'rating': self.ctx.rating(merged)}
        with _LOCK:
            known = self._known()
            known[gid] = found
            _save(known)
        return found

    def publish(self, gid, found):
        home = C.home()
        home.setProperty('dhs.gh.plot', found.get('plot') or '')
        home.setProperty('dhs.gh.logo', found.get('logo') or '')
        home.setProperty('dhs.gh.fanart', found.get('fanart') or '')
        home.setProperty('dhs.gh.meta', found.get('meta') or '')
        home.setProperty('dhs.gh.rating', found.get('rating') or '')
        # last: the skin shows the rest once the id names the focused card
        home.setProperty('dhs.gh.id', gid)

    def background(self):
        """A grid's titles not looked up yet, one at a time between focus
        changes (none while a video plays)."""
        token = C.prop('dhs.gh.want')
        if not token or token == self._want_token:
            return
        try:
            if self.busy():
                return
        except Exception:
            return
        data = C.read_json(_path(_WANT), {}) or {}
        items = list(data.get('items') or [])
        language = self._setup()
        known = self._known()
        for src in items:
            if self.done() or (self.on_grid() and xbmc.getInfoLabel('ListItem.Property(dhs.gid)') != self.current):
                return          # the focus moved: it comes first; the rest later
            gid = gid_of(src)
            found = known.get(gid)
            if found and found.get('lang') == language:
                continue
            self.lookup(src, language)
            self._halt.wait(0.05)
        self._want_token = token
