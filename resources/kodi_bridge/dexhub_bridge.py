# -*- coding: utf-8 -*-
"""Dex Hub bridge 3 (plugin.video.dexhub, resources/lib/kodi_sources.py).

Copied into a Kodi source add-on when the user switches it on in Dex Hub's
"Add-ons searched for sources", taken out again when it is switched off.
The add-on's router gets one fenced line that hands Dex Hub's two requests
here; nothing else of the add-on is edited: the bridge extends the add-on's
own source class from outside and stops it where it would show its window.

  dexhub_list  search with the add-on's scrapers and debrid accounts, no
               window, and save the results (plus a plain summary of each
               for Dex Hub) to addon_data/<add-on>/dexhub/<request>.json
  dexhub_play  play result N of that file with the add-on's own resolver
               and player

Families, told apart by Dex Hub from the add-on's code:
  fen       Fen, Red Light, POV, Gears and the like (modules/sources.py, Sources)
  umbrella  Umbrella and the like (resources/lib/modules/sources.py, Sources.play)
  otaku     Otaku (resources/lib/MetaBrowser.get_sources, anime by MAL id)

Bridge 3: while it searches or resolves for Dex Hub, the add-on cannot close
every dialog (see _keep_dialogs): one that did took Dex Hub's title page with
it. And an add-on that asks TorBox about every link in one request (POV)
asks 100 at a time while the bridge searches (see _chunked_torbox). And
dexhub_play with resolve_only=1 (fen family): the add-on resolves the pick
with its own resolver and debrid account, showing nothing, and the bridge
writes the link to <request>.resolved.json for Dex Hub to play with its own
waiting screen and player (see _resolve_only).
"""
import json
import os
import sys
import time

import xbmc
import xbmcaddon
import xbmcplugin
import xbmcvfs

BRIDGE_VERSION = 3
_KEEP_SECONDS = 900


def _log(msg):
    xbmc.log('[Dex Hub bridge] %s' % msg, xbmc.LOGINFO)


class Job(object):
    """One Dex Hub request: what to do, where its list lives."""

    def __init__(self, kind, params):
        self.kind = kind                      # 'list' or 'play'
        self.params = dict(params or {})
        self.request = str(self.params.get('request_id') or int(time.time() * 1000))
        try:
            self.index = int(self.params.get('selected_index') or 0)
        except ValueError:
            self.index = 0
        self.saved = False
        self.resolving = self.kind == 'play' and str(self.params.get('resolve_only') or '') == '1'
        aid = xbmcaddon.Addon().getAddonInfo('id')
        folder = xbmcvfs.translatePath('special://profile/addon_data/%s/dexhub/' % aid)
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError:
                pass
        self._tidy(folder)
        self.path = os.path.join(folder, '%s.json' % self.request)

    @staticmethod
    def _tidy(folder):
        now = time.time()
        try:
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if now - os.path.getmtime(path) > _KEEP_SECONDS:
                    os.remove(path)
        except OSError:
            pass

    @property
    def listing(self):
        return self.kind == 'list'

    def save(self, results, rows, extra=None):
        """The first answer wins: an add-on may report 'nothing' after it
        already handed its list over."""
        if self.saved:
            return
        self.saved = True
        data = {'bridge': BRIDGE_VERSION, 'rows': rows, 'results': results}
        data.update(extra or {})
        try:
            with open(self.path, 'w') as handle:
                json.dump(data, handle, default=str)
        except Exception as exc:
            _log('could not save: %s' % exc)
        _log('%d results saved for %s' % (len(rows), self.request))

    def answer_resolve(self, url, played=False):
        """The link the add-on resolved ('' for none), or played=True when
        it played the pick itself (no player of its own to stop)."""
        try:
            with open(self.path[:-len('.json')] + '.resolved.json', 'w') as handle:
                json.dump({'bridge': BRIDGE_VERSION, 'url': url or '', 'played': bool(played)}, handle)
        except Exception as exc:
            _log('could not save the link: %s' % exc)
        _log('link %s for %s' % ('played by the add-on' if played else ('resolved' if url else 'not found'),
                                  self.request))

    def load(self):
        try:
            with open(self.path) as handle:
                data = json.load(handle)
            results = data.get('results') or []
        except Exception:
            data, results = {}, []
        if not results:
            _log('nothing saved for %s (older than 15 minutes?)' % self.request)
            return data, None, None
        index = self.index if 0 <= self.index < len(results) else 0
        return data, results, results[index]


def _text(value):
    return str(value or '').strip()


def _gigabytes(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- fen family

def _fen_row(item):
    get = item.get
    service = _text(get('cache_provider') or get('debrid'))
    site = _text(get('source') if get('scrape_provider') == 'external' else get('scrape_provider') or get('source'))
    return {'name': _text(get('display_name') or get('name')), 'quality': _text(get('quality')).upper(),
            'size': _text(get('size_label')), 'size_gb': _gigabytes(get('size')),
            'info': [x.strip() for x in _text(get('extraInfo')).split('|') if x.strip()],
            'service': service.replace('Uncached', '').strip(), 'site': site,
            'cached': 'uncached' not in service.lower()}


def _chunked_torbox():
    """POV posts every hash of a search to TorBox's checkcached at once. A
    popular film has a few thousand; TorBox fails that request, POV passes
    over the error, counts every link as not cached and hides them all (0
    results where Red Light, with the same account, finds hundreds). Its
    check asks 100 at a time instead, as Red Light's does. Add-ons whose
    TorBox module is not POV's are left as they are."""
    try:
        from indexers import torbox_api
    except Exception:
        return
    api = getattr(torbox_api, 'TorBoxAPI', None)
    check = getattr(api, 'check_cache', None)
    if check is None or getattr(check, '_dexhub_chunked', False):
        return

    def check_cache(self, hashes):
        found = []
        for start in range(0, len(hashes or []), 100):
            try:
                part = check(self, hashes[start:start + 100])
            except Exception:
                continue
            if isinstance(part, list):
                found.extend(part)
        return found

    check_cache._dexhub_chunked = True
    api.check_cache = check_cache


# players whose run() ends the add-on's play_file once it has the link (POV's):
# those add-ons can resolve for Dex Hub. Red Light's play_file waits on its
# player and goes on to the next link, so it keeps playing by itself.
_RESOLVE_PLAYERS = ('POVPlayer',)


def _can_resolve(module):
    return any(isinstance(getattr(module, name, None), type) for name in _RESOLVE_PLAYERS)


class _Quiet(object):
    """A progress window that shows nothing: Dex Hub's waiting screen stays."""
    full_screen = True

    def __init__(self, *args, **kwargs):
        pass

    def _nothing(self, *args, **kwargs):
        return None

    make = update = kill = close = create = _nothing

    def iscanceled(self):
        return False


def _resolve_only(module, source, results, item, job):
    """The add-on's own play_file, with its player classes swapped for one
    that keeps the link, and no progress window."""
    caught = {}

    class Catch(object):
        def __init__(self, *args, **kwargs):
            pass

        def run(self, link, *args, **kwargs):
            caught['url'] = link

    swapped = {}
    for name in _RESOLVE_PLAYERS:
        value = getattr(module, name, None)
        if isinstance(value, type):
            swapped[name] = value
            setattr(module, name, Catch)
    source.progress_dialog = _Quiet()
    source.background = True                  # no "no results" notice of its own
    try:
        source.play_file(results, item)
    finally:
        for name, value in swapped.items():   # the module stays loaded for the next call
            setattr(module, name, value)
    job.answer_resolve(caught.get('url') or '', played=not swapped)
    return None


def _fen(job):
    from modules import sources as module      # the add-on's own module
    _chunked_torbox()
    base = module.Sources

    class DexHubSources(base):
        def get_sources(self):
            if not job.listing:
                _data, results, item = job.load()
                if results is None:
                    if job.resolving:
                        job.answer_resolve('')
                    return None
                if job.resolving and _can_resolve(module):
                    return _resolve_only(module, self, results, item, job)
                if job.resolving:
                    job.answer_resolve('', played=True)   # it plays the pick itself, below
                # POV's resolver writes to the progress object a search would have made
                if getattr(self, 'progress_dialog', None) is None and hasattr(module, 'DialogProgress'):
                    try:
                        self.progress_dialog = module.DialogProgress(getattr(self, 'full_screen', False))
                    except Exception:
                        pass
                return self.play_file(results, item)
            self.autoscrape = False           # never chain on to a next episode
            found = base.get_sources(self)
            if isinstance(found, list):       # POV hands its list back
                found = [i for i in found if isinstance(i, dict)]
                job.save(found, [_fen_row(i) for i in found], {'resolve': _can_resolve(module)})
            return found

        def play_source(self, results):
            if job.listing:
                results = [i for i in results or [] if isinstance(i, dict)]
                job.save(results, [_fen_row(i) for i in results], {'resolve': _can_resolve(module)})
            return None

        def _process_post_results(self):
            if job.listing:
                job.save([], [])
            return None

        def autoscrape_nextep_handler(self):
            return self.get_sources()

    params = dict(job.params)
    for key in ('mode', 'request_id', 'selected_index'):
        params.pop(key, None)
    params.update({'autoplay': 'false', 'prescrape': 'false'})
    if job.listing:
        params['background'] = 'true'         # no progress window over Dex Hub
    src = DexHubSources()
    start = getattr(src, 'playback_prep', None) or src.source_select
    return start(params)


# ----------------------------------------------------------- umbrella family

def _umbrella_row(item):
    get = item.get
    size = _gigabytes(get('size'))
    return {'name': _text(get('name')), 'quality': _text(get('quality')).upper(),
            'size': '%.2f GB' % size if size else '', 'size_gb': size,
            'info': [x.strip() for x in _text(get('info')).split('|') if x.strip()],
            'service': _text(get('debrid')), 'site': _text(get('provider')),
            'cached': not _text(get('source')).lower().startswith('uncached')}


class _Silent(object):
    def create(self, *a, **k):
        pass

    def update(self, *a, **k):
        pass

    def close(self, *a, **k):
        pass

    def iscanceled(self):
        return False

    def isFinished(self):
        return False


def _umbrella(job):
    from resources.lib.modules import sources as module
    base = module.Sources

    class DexHubSources(base):
        def sourceSelect(self, title, items, uncached_items, meta):
            if job.listing:
                found = [i for i in items or [] if isinstance(i, dict)]
                job.save(found, [_umbrella_row(i) for i in found])
                return None
            _data, results, item = job.load()
            if results is None:
                return None
            return self.playItem(title, json.dumps(results), json.dumps([item]), json.dumps(meta or {}))

        def errorForSources(self, *a, **k):
            if job.listing:
                job.save([], [])
                return None
            return base.errorForSources(self, *a, **k)

    # while listing: Umbrella's search window, prompts and questions stay closed
    swapped = []
    if job.listing:
        setting = module.getSetting

        def quiet_setting(key, *a, **k):
            if key == 'progress.dialog':
                return 'dexhub'               # not a window style: the quiet branch
            if key == 'dialogs.useumbrelladialog':
                return 'false'
            return setting(key, *a, **k)
        swapped = [(module, 'getSetting', setting),
                   (module.control, 'progressDialogBG', module.control.progressDialogBG),
                   (module.control, 'yesnoDialog', module.control.yesnoDialog)]
        module.getSetting = quiet_setting
        module.control.progressDialogBG = _Silent()
        module.control.yesnoDialog = lambda *a, **k: False
    try:
        src = DexHubSources()
        src.custom_query = 'false'
        p = job.params
        none = lambda key: None if p.get(key) in (None, '', 'None') else p.get(key)
        return src.play(none('title'), none('year'), none('imdb'), none('tmdb'), none('tvdb'), none('season'),
                        none('episode'), none('tvshowtitle'), none('premiered'), None, '0')
    finally:
        for owner, name, value in swapped:
            setattr(owner, name, value)


# ------------------------------------------------------------------- otaku

_OTAKU_QUALITY = {4: '4K', 3: '1080P', 2: '720P', 1: 'SD'}


def _otaku_row(item):
    get = item.get
    info = get('info') or []
    return {'name': _text(get('release_title')), 'quality': _OTAKU_QUALITY.get(get('quality'), 'SD'),
            'size': '' if _text(get('size')).upper() in ('NA', 'N/A') else _text(get('size')), 'size_gb': 0.0,
            'info': [str(x) for x in info] if isinstance(info, (list, tuple)) else [_text(info)],
            'service': _text(get('debrid_provider')), 'site': _text(get('provider')), 'cached': True,
            'lang': _text(get('lang'))}


def _otaku_entry(params):
    """The MAL entry for Dex Hub's ids, from Otaku's own mapping table: the
    TVDB season for an episode, the TMDb id for a movie."""
    from resources.lib.ui import control
    from resources.lib.ui.database import SQL
    tvdb, tmdb = _text(params.get('tvdb_id')), _text(params.get('tmdb_id'))
    season = _text(params.get('season'))
    with SQL(control.mappingDB) as cur:
        rows = []
        if season and tvdb:
            cur.execute('SELECT * FROM anime WHERE thetvdb_id=?', (tvdb,))
            rows = [r for r in cur.fetchall() if _text(r.get('thetvdb_season')) == season]
        if not rows and tmdb:
            cur.execute('SELECT * FROM anime WHERE themoviedb_id=?', (tmdb,))
            rows = cur.fetchall()
    return _text(rows[0].get('mal_id')) if rows else ''


def otaku_answer(payload, params):
    """Otaku's route dexhub_sources/<list|play>."""
    from resources.lib import MetaBrowser, Main
    from resources.lib.ui import control
    kind = 'list' if payload.startswith('list') else 'play'
    job = Job(kind, params)
    if job.listing:
        try:
            mal_id = _otaku_entry(params)
            movie = not _text(params.get('season'))
            episode = 1 if movie else int(params.get('episode') or 1)
            found = MetaBrowser.get_sources(mal_id, episode, 'movie' if movie else 'show', silent=True) if mal_id else []
            found = [i for i in (found or []) if isinstance(i, dict)]
            job.save(found, [_otaku_row(i) for i in found],
                     {'mal_id': mal_id, 'episode': episode, 'movie': movie})
        except Exception as exc:
            _log('Otaku list failed: %s' % exc)
            job.save([], [])
        finally:
            _end_listing()
        return
    data, results, item = job.load()
    if results is None:
        return
    # Otaku's own play route, handed the picked result first (its resolver
    # falls through the rest), straight to the resolver instead of its picker
    ordered = [item] + [r for r in results if r is not item]
    get_sources, get_bool, get_int = MetaBrowser.get_sources, control.getBool, control.getInt
    MetaBrowser.get_sources = lambda *a, **k: ordered
    control.getBool = lambda key, *a: False if key == 'general.smartplay' else get_bool(key, *a)
    control.getInt = lambda key, *a: 0 if key.startswith('general.playstyle') else get_int(key, *a)
    try:
        if data.get('movie'):
            return Main.PLAY_MOVIE('%s/0' % data.get('mal_id'), {})
        return Main.PLAY('%s/%s' % (data.get('mal_id'), data.get('episode')), {})
    finally:
        MetaBrowser.get_sources, control.getBool, control.getInt = get_sources, get_bool, get_int


# ------------------------------------------------------------- the router

def _end_listing():
    try:
        handle = int(sys.argv[1])
    except (IndexError, ValueError):
        return
    if handle > 0:
        try:
            xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)
        except Exception:
            pass


_CLOSERS = ('dialog.close(all', 'dialog.close(movieinformation', 'dialog.close(12003')


def _keep_dialogs():
    """Dex Hub's title page and sources list stay open while the add-on
    searches or resolves for them: its Dialog.Close(all) (Fen Light ends a
    search that found nothing with one, others close their progress window
    that way) is dropped. Returns the undo."""
    original = xbmc.executebuiltin

    def guarded(command, *args, **kwargs):
        if str(command or '').replace(' ', '').lower().startswith(_CLOSERS):
            _log('kept the open dialogs (the add-on asked for %s)' % command)
            return None
        return original(command, *args, **kwargs)

    swapped = [(xbmc, 'executebuiltin')]
    xbmc.executebuiltin = guarded
    # a reference the add-on took when it loaded (from xbmc import executebuiltin)
    for module in list(sys.modules.values()):
        try:
            for name, value in list(vars(module).items()):
                if value is original and module is not xbmc:
                    setattr(module, name, guarded)
                    swapped.append((module, name))
        except Exception:
            continue

    def undo():
        for module, name in swapped:
            try:
                setattr(module, name, original)
            except Exception:
                pass
    return undo


def answer(mode, params, family):
    """The fen and umbrella routers' line: dexhub_list or dexhub_play."""
    job = Job('list' if mode == 'dexhub_list' else 'play', params)
    undo = _keep_dialogs() if (job.listing or job.resolving) else None
    try:
        return (_umbrella if family == 'umbrella' else _fen)(job)
    except Exception as exc:
        import traceback
        _log('%s failed: %s | %s' % (mode, exc, traceback.format_exc().replace('\n', ' | ')))
        if job.listing:
            job.save([], [])
    finally:
        if undo:
            undo()
        if job.listing:
            _end_listing()
