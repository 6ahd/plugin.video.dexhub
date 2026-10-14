# -*- coding: utf-8 -*-
"""One place to track a title in every linked service (v5.10.153).

Status (plan to watch, watching, completed, paused, dropped), a 1 to 10
rating and list membership are saved in Floppy when the Floppy service
add-on is set up, and passed on to the other linked services the way each
one understands them:

  Trakt     plan to watch = watchlist; rating = Trakt rating
  Simkl     status = the Simkl list (plantowatch, watching, completed,
            hold, dropped); rating = Simkl rating
  MDBList   plan to watch = MDBList watchlist
  Dex Hub   My Favorites (local) is one of the lists in the panel

Each service can be turned off (settings tracking_sync_trakt, _simkl,
_mdblist). A service that fails never stops the others; the panel says
which ones took the change.

The last known state of a title is kept in tracking_state.json so the
title page can label its button at once (dhs.tp.track).
"""
from . import profile_scope as _scope
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon

from . import floppy_api
from .i18n import is_english

STATUSES = floppy_api.STATUSES
_LABELS = {
    'Planning': ('أبي أشوفه', 'Plan to watch'),
    'In progress': ('أشوفه', 'Watching'),
    'Completed': ('خلصته', 'Completed'),
    'Paused': ('موقّف', 'Paused'),
    'Dropped': ('تركته', 'Dropped'),
}
_SIMKL = {'Planning': 'plantowatch', 'In progress': 'watching', 'Completed': 'completed',
          'Paused': 'hold', 'Dropped': 'dropped'}
_LOCK = threading.Lock()


def _t(ar, en):
    return en if is_english() else ar


def status_label(status):
    pair = _LABELS.get(status)
    return (pair[1] if is_english() else pair[0]) if pair else ''


def _flag(key, default='true'):
    try:
        from .settings_cache import cached_addon
        return (cached_addon().getSetting(key) or default).strip().lower() == 'true'
    except Exception:
        return default == 'true'


# ---------------------------------------------------------------- the work
class Work(object):
    """The title being tracked: media (movie or series) and its ids."""

    def __init__(self, media='movie', canonical='', tmdb='', imdb='', title='', year=0,
                 poster='', fanart='', clearlogo='', plot=''):
        self.media = 'series' if str(media).lower() in ('series', 'tv', 'show', 'anime', 'tvshow', 'episode') else 'movie'
        self.canonical = canonical or ''
        self.tmdb = str(tmdb or '')
        self.imdb = str(imdb or '')
        if not self.imdb and self.canonical.startswith('tt'):
            self.imdb = self.canonical.split(':')[0]
        if not self.tmdb and self.canonical.startswith('tmdb:'):
            self.tmdb = self.canonical.split(':')[1]
        self.title = title or ''
        try:
            self.year = int(year or 0)
        except (TypeError, ValueError):
            self.year = 0
        self.poster, self.fanart, self.clearlogo, self.plot = poster or '', fanart or '', clearlogo or '', plot or ''
        if not self.canonical:
            self.canonical = self.imdb or ('tmdb:%s' % self.tmdb if self.tmdb else '')

    @property
    def key(self):
        return '%s|%s' % (self.media, self.canonical)

    def ensure_tmdb(self):
        if self.tmdb or not self.imdb:
            return self.tmdb
        try:
            from . import tmdb_direct
            found = tmdb_direct._resolve_tmdb_from_imdb(self.imdb, 'series' if self.media == 'series' else 'movie')
            self.tmdb = str(found or '')
        except Exception:
            self.tmdb = ''
        return self.tmdb

    def ids(self):
        out = {}
        if self.imdb:
            out['imdb'] = self.imdb
        if self.tmdb:
            try:
                out['tmdb'] = int(self.tmdb)
            except ValueError:
                pass
        return out

    @classmethod
    def from_params(cls, params):
        return cls(media=params.get('media') or params.get('m') or 'movie',
                   canonical=params.get('canonical') or params.get('id') or '',
                   tmdb=params.get('tmdb') or '', imdb=params.get('imdb') or '',
                   title=params.get('title') or '', year=params.get('year') or 0,
                   poster=params.get('poster') or '', fanart=params.get('fanart') or '',
                   clearlogo=params.get('clearlogo') or '', plot=params.get('plot') or '')

    def params(self):
        return {'media': self.media, 'canonical': self.canonical, 'tmdb': self.tmdb, 'imdb': self.imdb,
                'title': self.title, 'year': str(self.year or ''), 'poster': self.poster,
                'fanart': self.fanart, 'clearlogo': self.clearlogo}


# ---------------------------------------------------------------- saved state
def _state_path():
    import xbmcvfs
    return os.path.join(_scope.path(),
                        'tracking_state.json')


def _states():
    try:
        with open(_state_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def remembered(work_key):
    return _states().get(work_key) or {}


def remember(work_key, **fields):
    with _LOCK:
        data = _states()
        row = dict(data.get(work_key) or {})
        row.update(fields)
        row['at'] = time.time()
        data[work_key] = row
        if len(data) > 800:
            for old in sorted(data, key=lambda k: data[k].get('at', 0))[:-800]:
                data.pop(old, None)
        try:
            tmp = _state_path() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(data, handle)
            os.replace(tmp, _state_path())
        except Exception:
            pass
    return row


def button_label(work_key):
    """What the title page's Track button says for this title."""
    row = remembered(work_key)
    label = status_label(row.get('status') or '')
    score = row.get('score')
    if score:
        star = u'★ %d' % int(round(float(score)))
        label = '%s  %s' % (label, star) if label else star
    return label


def read_state(work, fresh=False):
    """{'status', 'score', 'lists', 'favorite', 'floppy'} for the panel."""
    state = {'status': '', 'score': None, 'lists': [], 'floppy': False}
    try:
        from . import favorites_store
        state['favorite'] = bool(favorites_store.is_favorite(work.media, work.canonical))
    except Exception:
        state['favorite'] = False
    if floppy_api.configured() and work.ensure_tmdb():
        found = floppy_api.entry(work.media, work.tmdb, fresh=fresh)
        if found is not None:
            state.update(status=found.get('status') or '', score=found.get('score'),
                         lists=found.get('lists') or [], floppy=True)
    if not state['floppy']:
        old = remembered(work.key)
        state['status'] = old.get('status') or ''
        state['score'] = old.get('score')
    remember(work.key, status=state['status'], score=state['score'])
    return state


# ---------------------------------------------------------------- the services
def _trakt_ready():
    try:
        from . import trakt
        return _flag('tracking_sync_trakt') and trakt.enabled() and trakt.authorized()
    except Exception:
        return False


def _simkl_ready():
    try:
        from . import simkl
        return _flag('tracking_sync_simkl') and simkl.enabled() and simkl.authorized()
    except Exception:
        return False


def _mdblist_ready():
    try:
        from . import mdblist
        return _flag('tracking_sync_mdblist') and mdblist.configured()
    except Exception:
        return False


def linked_services():
    out = []
    if floppy_api.configured():
        out.append('Floppy')
    if _trakt_ready():
        out.append('Trakt')
    if _simkl_ready():
        out.append('Simkl')
    if _mdblist_ready():
        out.append('MDBList')
    return out


def _bucket(work):
    return 'shows' if work.media == 'series' else 'movies'


def _trakt(path, payload):
    from . import trakt
    trakt._ensure_auth()
    return trakt._request(path, payload=payload, method='POST', auth=True, timeout=12)


def _simkl(path, payload):
    from . import simkl
    return simkl._request(path, payload=payload, method='POST', auth=True, timeout=12)


def _mdblist(path, payload):
    from . import mdblist
    key = mdblist.api_key()
    url = 'https://api.mdblist.com' + path + '?' + urllib.parse.urlencode({'apikey': key})
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                          'User-Agent': 'DexHub Kodi'})
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.getcode()


def _run(name, job, done, failed):
    try:
        job()
        done.append(name)
    except Exception as exc:
        failed.append(name)
        xbmc.log('[DexHub] tracking %s: %s' % (name, exc.__class__.__name__), xbmc.LOGINFO)


def set_status(work, status, previous=''):
    """Save the status everywhere; (done services, failed services)."""
    done, failed = [], []
    remove = status == ''
    if floppy_api.configured() and work.ensure_tmdb():
        def floppy_job():
            ok = floppy_api.untrack(work.media, work.tmdb) if remove else \
                floppy_api.set_status(work.media, work.tmdb, status, work.title, work.poster)
            if not ok:
                raise RuntimeError('floppy refused')
        _run('Floppy', floppy_job, done, failed)
    work.ensure_tmdb()
    ids = work.ids()
    if ids:
        bucket = _bucket(work)
        if _trakt_ready() and (status == 'Planning' or previous == 'Planning' or remove):
            path = '/sync/watchlist' if status == 'Planning' else '/sync/watchlist/remove'
            _run('Trakt', lambda: _trakt(path, {bucket: [{'ids': ids}]}), done, failed)
        if _simkl_ready():
            if remove:
                _run('Simkl', lambda: _simkl('/sync/history/remove', {bucket: [{'ids': ids}]}), done, failed)
            else:
                to = _SIMKL.get(status)
                if bucket == 'movies' and to in ('watching', 'hold'):
                    to = 'plantowatch'
                _run('Simkl', lambda: _simkl('/sync/add-to-list', {bucket: [{'to': to, 'ids': ids}]}), done, failed)
        if _mdblist_ready() and (status == 'Planning' or previous == 'Planning' or remove):
            action = 'add' if status == 'Planning' else 'remove'
            _run('MDBList', lambda: _mdblist('/watchlist/items/%s' % action, {bucket: [ids]}), done, failed)
    remember(work.key, status=status)
    _after_change()
    return done, failed


def set_score(work, score):
    done, failed = [], []
    if floppy_api.configured() and work.ensure_tmdb():
        def floppy_job():
            if not floppy_api.set_score(work.media, work.tmdb, score, work.title):
                raise RuntimeError('floppy refused')
        _run('Floppy', floppy_job, done, failed)
    work.ensure_tmdb()
    ids = work.ids()
    if ids:
        bucket = _bucket(work)
        if _trakt_ready():
            _run('Trakt', lambda: _trakt('/sync/ratings', {bucket: [{'rating': int(score), 'ids': ids}]}),
                 done, failed)
        if _simkl_ready():
            _run('Simkl', lambda: _simkl('/sync/ratings', {bucket: [{'rating': int(score), 'ids': ids}]}),
                 done, failed)
    remember(work.key, score=score)
    return done, failed


def set_favorite(work, member):
    from . import favorites_store
    if member:
        favorites_store.add(work.media, work.canonical, work.title or work.canonical, poster=work.poster,
                            background=work.fanart, clearlogo=work.clearlogo, year=work.year,
                            plot=work.plot, source='local')
    else:
        favorites_store.remove(work.media, work.canonical, source='local')
    _after_change()


def set_floppy_list(work, list_id, member):
    if not (floppy_api.configured() and work.ensure_tmdb()):
        return False
    if member and not floppy_api.entry(work.media, work.tmdb):
        floppy_api.set_status(work.media, work.tmdb, 'Planning', work.title, work.poster)
    return floppy_api.set_list(work.media, work.tmdb, list_id, member)


def _after_change():
    """The Library's rows read again (their path changes) on the next look."""
    try:
        import xbmcgui
        win = xbmcgui.Window(10000)
        win.setProperty('dhs.lib.v', str(int(time.time())))
        win.setProperty('dexhub.fav_mirror_done', '')
    except Exception:
        pass
