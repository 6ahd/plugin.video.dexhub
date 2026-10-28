# -*- coding: utf-8 -*-
"""Shared pieces of the skin.dexhub support: cheap to import (no router).

v5.10.133: a Home row's call (serve.row) imports this and little else, in a
fresh Python each time: json, hashlib, threading and urllib.parse are
imported where they are used (json alone brings re, enum, functools and
collections: about half of a row call's import time), and a row's cache is
also kept in marshal form, which needs no import at all (read_row)."""
from .. import profile_scope as _scope
import _thread
import os
import time

import xbmc
import xbmcgui

ADDON_ID = 'plugin.video.dexhub'
BASE = 'plugin://%s/' % ADDON_ID
SKIN_ID = 'skin.dexhub'
TABS = ('all', 'movie', 'series', 'library', 'servers')   # v5.10.153: the Library tab; v5.10.157: Servers
ROWS = 20                   # row slots of the skin's Home
HOME = 10000

# how old a row's cache may be before it is read again (seconds); an older
# copy is still shown at once while the service reads the new one
TTL_CATALOG = 45 * 60.0
TTL_PROGRESS = 3 * 60.0
TTL_COLLECTION = 6 * 3600.0
TTL_TITLE = 6 * 3600.0

# the layout of a cached row; a copy written by another version of the
# add-on is still shown, and read again at once
FORMAT = 16

PROP_READY = 'dhs.ready'
PROP_REPUBLISH = 'dhs.republish'    # set by the add-on when the rows changed
PROP_BUSY = 'dhs.busy'              # rows the service is reading now
PROP_PUBLISHING = 'dhs.publishing'  # a publish runs now (the add-on or the service)
PROP_PUBLISHED = 'dhs.published'    # when the rows were last published

_lock = _thread.allocate_lock()
_paths = {}


def log(msg, level=xbmc.LOGINFO):
    try:
        xbmc.log('[DexHub] skin: %s' % msg, level)
    except Exception:
        pass


class Layout(object):
    """Where the skin keeps its lists, by skin generation.

    skin.dexhub 1.x (Estuary based) has one Home window with tabs; 2.x is
    Arctic Fuse 3 with Dex Hub inside: a hub window per tab (the Home hub is
    "all", custom hubs 1101 and 1102 films and series), rows 501..520 and a
    spotlight list 301; the title page is AF3's info dialog. 3.x is Estuary
    again (one Home with tabs, rows 5001..5020, the title page 8xxx) with a
    spotlight list 4000 at the top of each tab and trailers behind it.

    hubs: a hub window per tab (2.x); trailers: the service plays trailers
    behind the spotlight (2.x and 3.x)."""

    def __init__(self, major):
        self.major = major
        self.hubs = major == 2
        self.trailers = major >= 2
        if self.hubs:
            self.row_base = 500
            self.spotlight = 301
            self.windows = {'all': ('Home', 10000), 'movie': ('1101', 11101), 'series': ('1102', 11102)}
            self.tp = {'seasons': 5081, 'episodes': 5082, 'cast': 5083, 'related': 5084}
        else:
            self.row_base = 5000
            self.spotlight = 4000 if major >= 3 else 0
            self.windows = {'all': ('Home', 10000), 'movie': ('Home', 10000), 'series': ('Home', 10000)}
            self.tp = {'seasons': 8200, 'episodes': 8300, 'cast': 8400, 'related': 8500}
        self.hero_buttons = 4010 if major >= 3 else 0

    def row_list(self, slot):
        return self.row_base + slot

    def tab_window(self, tab):
        """(name for builtins and conditions, window id) of a tab's hub."""
        return self.windows.get(tab, ('Home', 10000))

    def tab_on_show(self):
        """The Dex Hub tab whose hub is the active window (or None)."""
        try:
            active = xbmcgui.getCurrentWindowId()
        except Exception:
            return None
        if not self.hubs:
            if active != HOME:
                return None
            # the skin's own pages (the add-ons page, v3.2.0) are not Dex Hub tabs
            tab = prop('dhs.tab') or 'all'
            return tab if tab in TABS else None
        for tab, (name, wid) in self.windows.items():
            if active == wid and xbmc.getCondVisibility('Skin.HasSetting(DexHub.Hub.%s)' % name):
                return tab
        return None


_LAYOUT = {}


def skin_major():
    try:
        import xbmcaddon
        version = xbmcaddon.Addon(SKIN_ID).getAddonInfo('version') or '1'
        return int(version.split('.')[0] or 1)
    except Exception:
        return 1


_SKIN_VERSION = {'t': -1e9, 'v': (0,)}


def skin_version():
    """skin.dexhub's version as a tuple, (3, 27, 1); read again each minute
    (v5.10.186: what a row style can do depends on it)."""
    now = time.monotonic()
    if now - _SKIN_VERSION['t'] > 60.0:
        _SKIN_VERSION['t'] = now
        try:
            import xbmcaddon
            text = xbmcaddon.Addon(SKIN_ID).getAddonInfo('version') or '0'
            _SKIN_VERSION['v'] = tuple(int(p) if p.isdigit() else 0 for p in text.split('.')[:3])
        except Exception:
            _SKIN_VERSION['v'] = (0,)
    return _SKIN_VERSION['v']


def layout():
    """The Layout of the skin.dexhub in use (its version is read again each
    minute: the skin may be updated while the service runs)."""
    current = _LAYOUT.get('l')
    if current is None or time.time() - _LAYOUT.get('t', 0) > 60.0:
        major = skin_major()
        if current is None or current.major != major:
            current = _LAYOUT['l'] = Layout(major)
        _LAYOUT['t'] = time.time()
    return current


_SKIN_ON = {'t': 0.0, 'dir': None, 'on': False}


def skin_active():
    """skin.dexhub is the skin in use (Kodi names the chosen skin even when it
    could not load it, so the add-on being enabled is checked too).

    v5.10.140: the enabled check is kept for five seconds (the service's
    threads asked it a few times a second, and getCondVisibility pauses
    Kodi's frame each time); a change of skin is seen at once."""
    try:
        skin = xbmc.getSkinDir()
        if skin != SKIN_ID:
            return False
        now = time.time()
        if _SKIN_ON['dir'] != skin or now - _SKIN_ON['t'] > 5.0:
            _SKIN_ON['on'] = bool(xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % SKIN_ID))
            _SKIN_ON['dir'], _SKIN_ON['t'] = skin, now
        return _SKIN_ON['on']
    except Exception:
        return False


_AF3 = {'t': 0.0, 'state': {}}
AF3_STATE = 'af3.json'


def af3_state(fresh=False):
    """What Dex Hub put into Arctic Fuse 3 (skinui/af3.py), read again every
    ten seconds: {'installed', 'skin', 'places', 'hubs', ...}."""
    if fresh or time.time() - _AF3['t'] > 10.0:
        _AF3['state'] = read_json(os.path.join(folder(), AF3_STATE), {}) or {}
        _AF3['t'] = time.time()
    return _AF3['state']


def served():
    """'dexhub' while skin.dexhub is in use; 'af3' while the skin in use is the
    one Dex Hub's rows were installed in (Arctic Fuse 3, v5.10.111): its
    widgets read the same row caches, which the service keeps fresh; ''
    otherwise."""
    if skin_active():
        return 'dexhub'
    state = af3_state()
    if not state.get('installed'):
        return ''
    try:
        return 'af3' if xbmc.getSkinDir() == state.get('skin') else ''
    except Exception:
        return ''


_HOME_WINDOW = []


def home():
    """Kodi's Home window: one object for the whole process (v5.10.140).

    Every property read built a new xbmcgui.Window, and building one takes
    Kodi's GUI lock (the one the screen is drawn under) before the read takes
    it again; the worker's row checks did that twenty times per request.
    Kodi 22 RC1 on CoreELEC crashed (SIGSEGV in a Python call of this
    service, while other threads of it were building and disposing Window
    objects) right after a collection page's rows were read.
    """
    if not _HOME_WINDOW:
        _HOME_WINDOW.append(xbmcgui.Window(HOME))
    return _HOME_WINDOW[0]


def prop(key):
    try:
        return home().getProperty(key) or ''
    except Exception:
        return ''


IDLE_AFTER = 10.0


def poll_wait(fast, slow=1.0, idle_after=IDLE_AFTER):
    """How long a watcher waits before it looks again (v5.10.198): `fast`
    while the remote is in use, `slow` once nothing was pressed for a while
    (an idle Home, a video playing), so Python wakes Kodi less often then.
    A key press is seen within `slow` seconds."""
    try:
        idle = xbmc.getGlobalIdleTime()
    except Exception:
        return fast
    if idle < idle_after:
        return fast
    return max(fast, slow * 2 if idle >= 60 else slow)     # a minute without a key: slower still


def set_prop(key, value):
    if not _scope.is_current():
        return
    try:
        window = home()
        text = '' if value in (None, '') else str(value)
        # Publishing unchanged paths/labels must not dirty the Home again.
        if window.getProperty(key) == text:
            return
        if not text:
            window.clearProperty(key)
        else:
            window.setProperty(key, text)
    except Exception:
        pass


def folder(name=''):
    """addon_data/plugin.video.dexhub/skin[/name], made on first use."""
    with _lock:
        cached = _paths.get((_scope.key(), name))
    if cached:
        return cached
    try:
        import xbmcvfs
        root = _scope.path('skin')
    except Exception:
        root = os.path.join(os.path.expanduser('~'), '.kodi', 'userdata', 'addon_data', ADDON_ID, 'skin')
    path = os.path.join(root, name) if name else root
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    with _lock:
        _paths[(_scope.key(), name)] = path
    return path


def short_id(text):
    import hashlib
    return hashlib.sha1(str(text or '').encode('utf-8')).hexdigest()[:10]


def read_json(path, default=None):
    import json
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return default


_ROWS_DIR = os.sep + 'rows' + os.sep


def _side(path):
    """A row cache's marshal copy (rows/<tab>_<id>.mar beside the .json)."""
    return path[:-5] + '.mar' if path.endswith('.json') and _ROWS_DIR in path else ''


def read_row(path, default=None):
    """A row's cache (v5.10.133): its marshal copy when that is not older
    than the JSON (marshal is built into Python: a row call imports no json),
    else the JSON."""
    side = _side(path)
    if side:
        try:
            if os.stat(side).st_mtime_ns >= os.stat(path).st_mtime_ns:
                import marshal
                with open(side, 'rb') as handle:
                    data = marshal.load(handle)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return read_json(path, default)


def _write_side(side, data):
    tmp = '%s.%d.%d.tmp' % (side, os.getpid(), _thread.get_ident())
    try:
        import marshal
        with open(tmp, 'wb') as handle:
            marshal.dump(data, handle)
        os.replace(tmp, side)
        return True
    except Exception:
        # no copy: read_row reads the JSON (an older copy must not stay)
        for name in (tmp, side):
            try:
                os.remove(name)
            except Exception:
                pass
        return False


def ensure_row_copies():
    """Row caches written before v5.10.133 get their marshal copy (the
    service, once at its start). Returns how many were made."""
    where = folder('rows')
    made = 0
    try:
        names = os.listdir(where)
    except Exception:
        return 0
    for name in names:
        if not name.endswith('.json'):
            continue
        path = os.path.join(where, name)
        side = _side(path)
        try:
            if os.path.exists(side) and os.stat(side).st_mtime_ns >= os.stat(path).st_mtime_ns:
                continue
        except Exception:
            continue
        data = read_json(path)
        if isinstance(data, dict) and _write_side(side, data):
            made += 1
    return made


def write_json(path, data):
    """Write atomically: a reader never sees half a file. A row's cache gets
    its marshal copy too (read_row), written after the JSON."""
    import json
    tmp = '%s.%d.%d.tmp' % (path, os.getpid(), _thread.get_ident())
    try:
        # v5.10.188: dumps, not dump: json.dump writes through Python's own
        # encoder piece by piece; dumps uses the C one (a row of 40 titles:
        # 22 ms became 4 on a PC, the publish writes a dozen of them)
        text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        with open(tmp, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception as exc:
        log('could not write %s: %s' % (os.path.basename(path), exc), xbmc.LOGWARNING)
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False
    side = _side(path)
    if side:
        _write_side(side, data)
    return True


def row_file(tab, row_id):
    return os.path.join(folder('rows'), '%s_%s.json' % (tab, row_id))


def spec_file(tab):
    return os.path.join(folder(), 'spec_%s.json' % tab)


def title_file(key):
    return os.path.join(folder('titles'), '%s.json' % short_id(key))


class file_lock(object):
    """One holder at a time across Kodi's Python interpreters (v5.10.118).

    The title page asks for its seasons, its cast and its related titles at
    the same moment, each in an interpreter of its own: each read the whole
    title (kodi.log on a Ugoos: three reads of one title, 2.2 to 2.5 s each).
    flock() locks belong to an open file, so two interpreters of the one
    Kodi process exclude each other. Without fcntl (Windows) or past
    ``timeout`` the holder goes on unlocked: a lock never stops a page.
    """

    def __init__(self, path, timeout=10.0):
        self.path = path + '.lock'
        self.timeout = timeout
        self.handle = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:
            return self
        try:
            self.handle = open(self.path, 'a')
        except Exception:
            self.handle = None
            return self
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (BlockingIOError, PermissionError):
                if time.monotonic() > deadline:
                    self._close()
                    return self
                time.sleep(0.05)
            except Exception:
                self._close()
                return self

    def _close(self):
        handle, self.handle = self.handle, None
        if handle is not None:
            try:
                handle.close()          # closing the file lets the lock go
            except Exception:
                pass

    def __exit__(self, *_exc):
        self._close()
        return False


def url(action, **params):
    query = [('action', action)] + [(k, v) for k, v in params.items() if v not in (None, '')]
    from urllib.parse import urlencode
    return BASE + '?' + urlencode(query)


def content_hash(items, more=None):
    """A short fingerprint of what a row shows: a copy read again with the
    same titles, art and text has the same one, and Kodi is left alone."""
    import json
    keys = []
    for item in items or []:
        if isinstance(item, dict):
            keys.append(json.dumps(dict((k, v) for k, v in item.items() if k != 'src'),
                                   sort_keys=True, ensure_ascii=False))
    keys.append(json.dumps(more or {}, sort_keys=True))
    return short_id('\n'.join(keys))


def now():
    return time.time()


def fresh_copy(data, ttl):
    """A cached row young enough, in this version's layout."""
    if not data or data.get('f') != FORMAT:
        return False
    if (data.get('kind') == 'continue' or str(data.get('key') or '').startswith('continue')) and not data.get('cw_merge_rev'):
        return False
    return now() - float(data.get('t') or 0) < float(ttl or TTL_CATALOG)


def request_refresh(tab, row_id, enrich_only=False):
    """Ask the service to read a row again (it was shown from an old copy),
    or only to give it its TMDb pass (a fresh copy not localized yet).

    One empty file per request (want/<tab>_<id>_<r|e>): several add-on calls
    asking at once never overwrite each other's request."""
    kind = 'e' if enrich_only else 'r'
    path = os.path.join(folder('want'), '%s_%s_%s' % (tab, row_id, kind))
    if os.path.exists(path):
        return
    try:
        with open(path, 'a'):
            pass
    except Exception:
        pass


def take_requests():
    """[(tab, row_id, kind)] asked for since the last call; kind 'e' is the
    TMDb pass only, 'r' a new read (a read gives the TMDb pass too)."""
    where = folder('want')
    try:
        names = os.listdir(where)
    except Exception:
        return []
    wanted = {}
    for name in names:
        try:
            os.remove(os.path.join(where, name))
        except Exception:
            continue                # taken already, or not ours
        parts = name.split('_')
        if len(parts) != 3 or not parts[0] or not parts[1] or parts[2] not in ('r', 'e'):
            continue
        key = (parts[0], parts[1])
        if wanted.get(key) != 'r':
            wanted[key] = parts[2]
    return [(tab, row_id, kind) for (tab, row_id), kind in wanted.items()]


def ask_fresh():
    """The next reads skip the HTTP and page caches (the menu's Refresh)."""
    set_prop('dhs.fresh', '%.1f' % time.time())


def fresh_asked(window=180.0):
    try:
        return time.time() - float(prop('dhs.fresh') or 0) < window
    except ValueError:
        return False


def ask_republish(reason=''):
    """The rows of the Home changed (layout, accounts, sources): publish again."""
    set_prop(PROP_REPUBLISH, '%.3f %s' % (time.time(), reason))


def show_info(li):
    """Kodi's information page for a list item, once (v5.10.182).

    Two calls at the same moment (OK seen both as a key and as a click, or two
    scripts opening a page together) fill Kodi's one information dialog from
    two threads, and Kodi crashes. A second call within two seconds of the
    first is dropped."""
    import time as _time
    import xbmcgui as _gui
    home = _gui.Window(10000)
    now = _time.time()
    try:
        last = float(home.getProperty('dexhub.info.opening') or 0)
    except ValueError:
        last = 0.0
    if 0 <= now - last < 2.0:
        return False
    home.setProperty('dexhub.info.opening', '%.3f' % now)
    _gui.Dialog().info(li)
    return True


# v5.10.228: the TMDb pass also gives a show's seasons and status; a row
# passed before that (no 'ev') is passed again once
ENRICH_V = 6  # Auto now completes facts even when every artwork slot was filled.


def is_enriched(data):
    """The row's TMDb pass is done (and is the current one)."""
    data = data or {}
    # v5.10.288: and its posters follow the poster source chosen now (the
    # service publishes the choice's stamp; a row passed under another
    # choice gets the poster pass again, its catalog is not read again)
    stamp = prop(PROP_POSTER_GEN)
    if stamp and data.get('pg') != stamp:
        return False
    from ..ui_preferences import enabled
    metadata_due = (enabled('metadata_refresh') and data.get('kind') != 'collection' and
                    time.time() - float(data.get('metadata_at') or data.get('t') or 0) >= 1800)
    return (not metadata_due and bool(data.get('enriched')) and data.get('ev') == ENRICH_V
            and not (data.get('auto_retry_at') and time.time() >= data['auto_retry_at']))


# v5.10.288: the poster layer's stamp. Every row of every page (the Home
# tabs, a collection folder's page) carries the stamp its posters were
# made with ('pg'); a different stamp (another poster source, another
# unified metadata source, other Better Posters options) re-dresses its
# posters from the titles' IDs (rows._poster_pass)
PROP_POSTER_GEN = 'dhs.pgen'
_POSTER_GEN_KEYS = ('betterposters_language', 'betterposters_trend_tags', 'betterposters_quality_tags',
                    'betterposters_genre', 'betterposters_rating', 'betterposters_rating_source',
                    'betterposters_age_rating', 'betterposters_url_template',
                    'betterposters_fallback_template', 'betterposters_catalogs_enabled')


def _poster_parts(addon=None):
    if addon is None:
        import xbmcaddon
        addon = xbmcaddon.Addon(ADDON_ID)
    get = addon.getSetting
    poster = (get('global_poster_source_id') or 'same').strip() or 'same'
    source = (get('global_meta_source_id') or '').strip()
    unified = (get('global_meta_source_all') or '') == 'true'
    parts = [poster, source if unified and source not in ('auto', 'native') else '']
    if poster == 'betterposters':
        parts.extend(get(key) or '' for key in _POSTER_GEN_KEYS)
    return parts


def poster_gen(addon=None):
    """v5.10.288: the stamp of the poster choice in the settings now."""
    try:
        return short_id('|'.join(_poster_parts(addon)))
    except Exception:
        return ''


def poster_choice_active(addon=None):
    """v5.10.288: a poster source (or a unified metadata source) replaces the
    listings' own posters; without one a row read now needs no poster pass."""
    try:
        parts = _poster_parts(addon)
        return parts[0] != 'same' or bool(parts[1])
    except Exception:
        return True
