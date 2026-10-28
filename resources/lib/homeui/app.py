# -*- coding: utf-8 -*-
"""Controller for the Dex Hub Home windows (v5.10.96).

The app owns the shared services every window uses: a small daemon worker
pool, a debounce scheduler, the trailer resolver and director, the local
focus-art cache and the lazily imported Dex Hub router. Windows are opened
modally on top of each other; closing one returns to the one below it.
"""
from .. import profile_scope as _scope
import heapq
import itertools
import os
import sys
import threading
import time
from .. import lazy_tb as traceback

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import ui_size

ADDON_ID = 'plugin.video.dexhub'
HOME_WINDOW = 10000
# The running Home window publishes its id and a heartbeat on Kodi's Home
# window, so a second launch brings it back instead of stacking a copy.
PROP_WINDOW = 'dexhub.homeui.window'
PROP_TOP = 'dexhub.homeui.top'
PROP_ALIVE = 'dexhub.homeui.alive'
PROP_WINDOWS = 'dexhub.homeui.windows'      # v5.10.131: theme_sync waits while any is open
# a job for the running Home from another invocation (Settings): 'layout'
PROP_REQUEST = 'dexhub.homeui.request'


def _log(msg, level=None):
    try:
        xbmc.log('[DexHub] homeui: %s' % msg, xbmc.LOGINFO if level is None else level)
    except Exception:
        pass


class Settings(object):
    """Fresh reads with typed defaults; values are read once per window open."""

    def __init__(self):
        try:
            self._addon = xbmcaddon.Addon(ADDON_ID)
        except Exception:
            self._addon = xbmcaddon.Addon()

    def text(self, key, default=''):
        try:
            value = self._addon.getSetting(key)
        except Exception:
            value = ''
        return value if value not in (None, '') else default

    def flag(self, key, default=False):
        raw = str(self.text(key, 'true' if default else 'false')).strip().lower()
        return raw in ('true', '1', 'yes', 'on')

    def number(self, key, default=0, lo=None, hi=None):
        try:
            value = float(self.text(key, default))
        except Exception:
            value = float(default)
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        return value

    def write(self, key, value):
        """Save one setting through a fresh handle and read through it from now on.

        A fresh handle carries what other invocations saved since this one was
        made, so the write cannot put an older copy of the other settings back.
        """
        try:
            addon = xbmcaddon.Addon(ADDON_ID)
        except Exception:
            addon = xbmcaddon.Addon()
        addon.setSetting(key, str(value))
        self._addon = addon

    @property
    def arabic(self):
        return str(self.text('ui_language', 'English')).strip().lower().startswith('ar')


class Scheduler(object):
    """Keyed one-shot timers on one daemon thread; re-scheduling a key replaces it."""

    def __init__(self):
        self._heap = []
        self._keys = {}
        self._cv = threading.Condition()
        self._seq = itertools.count()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name='DexHub-homeui-timer')
        self._thread.daemon = True
        self._thread.start()

    def call_later(self, key, delay, fn):
        fn = _scope.capture(fn)
        with self._cv:
            seq = next(self._seq)
            self._keys[key] = seq
            heapq.heappush(self._heap, (time.monotonic() + max(0.0, delay), seq, key, fn))
            self._cv.notify()

    def cancel(self, key):
        with self._cv:
            self._keys.pop(key, None)

    def cancel_prefix(self, prefix):
        with self._cv:
            for key in [k for k in self._keys if str(k).startswith(prefix)]:
                self._keys.pop(key, None)

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify()

    def _loop(self):
        while True:
            with self._cv:
                while not self._stop:
                    if self._heap:
                        due = self._heap[0][0] - time.monotonic()
                        if due <= 0:
                            break
                        self._cv.wait(min(due, 1.0))
                    else:
                        self._cv.wait(1.0)
                if self._stop:
                    return
                _due, seq, key, fn = heapq.heappop(self._heap)
                if self._keys.get(key) != seq:
                    continue
                self._keys.pop(key, None)
            try:
                fn()
            except Exception:
                _log('timer %s failed:\n%s' % (key, traceback.format_exc()), xbmc.LOGWARNING)


class Workers(object):
    """A few daemon threads draining a priority queue (lower runs first)."""

    def __init__(self, count=3, name='row'):
        self._heap = []
        self._cv = threading.Condition()
        self._seq = itertools.count()
        self._pending = set()
        self._stop = False
        self._threads = []
        for index in range(max(1, count)):
            thread = threading.Thread(target=self._loop, name='DexHub-homeui-%s%d' % (name, index))
            thread.daemon = True
            thread.start()
            self._threads.append(thread)

    def submit(self, fn, priority=5, key=None):
        fn = _scope.capture(fn)
        with self._cv:
            if self._stop:
                return False
            if key is not None:
                if key in self._pending:
                    return False
                self._pending.add(key)
            heapq.heappush(self._heap, (priority, next(self._seq), key, fn))
            self._cv.notify()
            return True

    def stop(self):
        with self._cv:
            self._stop = True
            self._heap = []
            self._cv.notify_all()

    def cancel_where(self, predicate):
        """Remove queued work only. Running jobs keep their pending key."""
        with self._cv:
            kept, removed = [], []
            for entry in self._heap:
                if entry[2] is not None and predicate(entry[2]):
                    removed.append(entry[2])
                    self._pending.discard(entry[2])
                else:
                    kept.append(entry)
            self._heap = kept
            heapq.heapify(self._heap)
            return removed

    def prioritize(self, key, priority):
        with self._cv:
            changed = False
            entries = []
            for old, seq, queued_key, fn in self._heap:
                if queued_key == key:
                    old = min(old, priority)
                    changed = True
                entries.append((old, seq, queued_key, fn))
            if changed:
                self._heap = entries
                heapq.heapify(self._heap)
                self._cv.notify()
            return changed

    def _loop(self):
        while True:
            with self._cv:
                while not self._heap and not self._stop:
                    self._cv.wait(1.0)
                if self._stop:
                    return
                _prio, _seq, key, fn = heapq.heappop(self._heap)
            try:
                fn()
            except Exception:
                _log('job failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            finally:
                if key is not None:
                    with self._cv:
                        self._pending.discard(key)


class App(object):
    def __init__(self):
        self.settings = Settings()
        addon = self.settings._addon
        self.addon_path = addon.getAddonInfo('path')
        self.profile = _scope.ScopedPath('homeui')
        try:
            os.makedirs(self.profile, exist_ok=True)
        except Exception:
            pass
        self.stop_event = threading.Event()
        self.scheduler = Scheduler()
        # Rows load on their own lane: a slow catalog server holds a row
        # worker for seconds, and the focused title's trailer, logo and
        # focus art must never wait behind that (v5.10.97).
        self.workers = Workers(3, 'row')
        self.fast = Workers(2, 'fast')
        # prefetching and second tries of slow rows: never in the way of either
        self.bg = Workers(1, 'bg')
        # the focused title's trailer lookup, made while the start delay runs
        # (v5.10.103): its own lane, so a slow lookup holds up nothing else
        self.pre = Workers(1, 'pre')
        # working out a page's rows (v5.10.104): the Servers and Live TV pages
        # rebuild when their tab changes, and must not wait behind rows of a
        # slow server that hold every row worker
        self.pages = Workers(2, 'page')
        # the programmes of the channels on the Live TV guide (v5.10.131): a
        # lane of their own, never in the way of a page or a preview
        self.epg = Workers(2, 'epg')
        # the live channel (with its own address) the user watches from the
        # Live TV page, for the now-playing card
        self.live_watching = None
        from .trailers import TrailerResolver
        from .media_cache import MediaCache, LogoCheck
        from .director import TrailerDirector
        quality = {'1080p': 1080, '720p': 720, '480p': 480}.get(
            self.settings.text('homeui_trailer_quality', '720p'), 720)
        self.resolver = TrailerResolver(self.profile, quality=quality, log=self.log)
        self.media = MediaCache(os.path.join(self.profile, 'focusart'), log=self.log)
        self.logos = LogoCheck()
        self.director = TrailerDirector(self)
        from .layout import Layout
        self.layout = Layout(self.profile)
        self.request = ''
        # v5.10.110: a new interface size closes the Home and opens it
        # again on its new canvas (the media page it was on)
        self.reopen = ''
        # v5.10.167: a tab picked on My Library, My Servers or Add-ons: the
        # Home under the page opens it once the page has closed
        self.nav_next = ''
        self._api = None
        self._api_lock = threading.Lock()
        self._windows = []
        self._watch = threading.Thread(target=self._watch_loop, name='DexHub-homeui-watch')
        self._watch.daemon = True
        self._watch.start()

    # ---------------------------------------------------------------- misc
    def log(self, msg, level=None):
        _log(msg, level)

    def media_path(self, name):
        return os.path.join(self.addon_path, 'resources', 'media', 'homeui', name)

    def api(self):
        """The Dex Hub router, imported once, on first need, from any thread."""
        if self._api is not None:
            return self._api
        with self._api_lock:
            if self._api is None:
                started = time.monotonic()
                try:
                    from ..i18n import reset_language_cache
                    reset_language_cache()
                except Exception:
                    pass
                from .. import plugin as _plugin
                try:
                    _plugin._reset_invocation_state()
                except Exception:
                    try:
                        from ..runtime_binding import bind
                        bind(_plugin)
                    except Exception:
                        pass
                self._api = _plugin
                self.log('router ready in %.0f ms' % ((time.monotonic() - started) * 1000))
        return self._api

    def tr(self, text):
        try:
            from ..i18n import tr
            return tr(text)
        except Exception:
            return text

    def touch_interactive(self):
        try:
            xbmcgui.Window(10000).setProperty('dexhub.interactive_busy', '%.3f' % time.time())
        except Exception:
            pass

    # ------------------------------------------------------------ windows
    def push(self, window):
        self._windows.append(window)
        self._publish_windows()

    def pop(self, window):
        try:
            self._windows.remove(window)
        except ValueError:
            pass
        self._publish_windows()

    def _publish_windows(self):
        """How many Dex Hub windows are open (v5.10.131): skin.dexhub's colour
        theme waits for none (Kodi's skin reload closes script windows)."""
        try:
            count = len(self._windows)
            xbmcgui.Window(HOME_WINDOW).setProperty(PROP_WINDOWS, str(count) if count else '')
        except Exception:
            pass

    def top(self):
        return self._windows[-1] if self._windows else None

    def after_skin_reload(self):
        """v5.10.195: Kodi reloaded the skin under the open pages (their
        controls are gone and cannot be fetched again): every page closes and
        the Home opens anew (run's reopen loop); a page opened on its own over
        skin.dexhub's Home just closes."""
        root = self._windows[0] if self._windows else None
        if root is not None and not getattr(self, 'skin_page', False) and getattr(root, 'mode', '') == 'home':
            self.reopen = getattr(root, 'media', '') or 'all'
        for window in reversed(list(self._windows)):
            try:
                window.close_window()
            except Exception:
                try:
                    window.close()
                except Exception:
                    pass

    def close_all(self):
        """Leave Dex Hub Home entirely (whatever plays keeps playing)."""
        for window in reversed(list(self._windows)):
            try:
                window.close_window()
            except Exception:
                pass

    def _pending_request(self):
        if self.request:
            return self.request
        try:
            return xbmcgui.Window(HOME_WINDOW).getProperty(PROP_REQUEST) or ''
        except Exception:
            return ''

    def _serve_request(self):
        """Open what Settings (or the launch) asked for, over the Home itself.

        Pages opened above the Home are closed first, one per tick; the job
        then reaches the Home's GUI thread through a click on its focus sink,
        because windows may only be opened from that thread.
        """
        request = self._pending_request()
        if not request:
            return
        root, top = self._root(), self.top()
        if root is None or getattr(root, 'mode', '') != 'home' or not getattr(root, '_inited', False):
            return
        if top is not root:
            if not getattr(top, '_closing', False):
                try:
                    top.close_window()
                except Exception:
                    pass
            return
        if not root.is_active() or root.dialog_open():
            return
        self.request = ''
        try:
            xbmcgui.Window(HOME_WINDOW).clearProperty(PROP_REQUEST)
        except Exception:
            pass
        root.post_request(request)

    def _watch_loop(self):
        class SettingsMonitor(xbmc.Monitor):
            dirty = False
            def onSettingsChanged(self):
                self.dirty = True

            def onNotification(self, sender, method, data):
                # v5.10.195: a skin reload frees every control of the open
                # windows (see window.skin_event)
                if method.endswith('OnSkinUnloading') or method.endswith('OnSkinLoaded'):
                    from .window import skin_event
                    skin_event(method.endswith('OnSkinUnloading'))
        monitor = SettingsMonitor()
        beat = 0.0
        try:
            from .. import server_accounts
            self._accounts_sig = server_accounts.signature()
        except Exception:
            self._accounts_sig = None
        while not self.stop_event.is_set():
            if monitor.waitForAbort(self._watch_pause()):
                self.stop_event.set()
                break
            if monitor.dirty:
                monitor.dirty = False
                self._watch_accounts()
                self.settings = Settings()
                self._tmdb_settings = None
                try:
                    from ..i18n import reset_language_cache
                    reset_language_cache()
                    for page in list(self._windows):
                        page.reload_preferences()
                except Exception:
                    self.log('settings refresh failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            try:
                self.director.tick()
            except Exception:
                pass
            window = self.top()
            if window is not None:
                try:
                    window.watch_tick()
                except Exception:
                    pass
            now = time.time()
            if now - beat >= 1.0:
                beat = now
                self._heartbeat(now)
            try:
                self._check_left_behind()
            except Exception:
                pass
            try:
                self._serve_request()
            except Exception:
                pass

    def _root(self):
        return self._windows[0] if self._windows else None

    def _watch_pause(self):
        """v5.10.198: the watch thread's rest: a quarter second, or a second
        once nothing was pressed for a while and no preview is playing (an
        idle Home wakes Python four times less often)."""
        try:
            if getattr(self.director, '_state', 'idle') != 'idle' or xbmc.getGlobalIdleTime() < 10:
                return 0.25
        except Exception:
            return 0.25
        return 1.0

    def _heartbeat(self, now):
        root = self._root()
        if root is None or not getattr(root, '_window_id', 0) or getattr(root, 'mode', '') != 'home':
            return
        top = self.top()
        try:
            home = xbmcgui.Window(HOME_WINDOW)
            home.setProperty(PROP_WINDOW, str(root._window_id))
            home.setProperty(PROP_TOP, str(getattr(top, '_window_id', 0) or root._window_id))
            home.setProperty(PROP_ALIVE, '%.1f' % now)
        except Exception:
            pass

    def _watch_accounts(self):
        """v5.10.198: a server account linked or removed in another invocation
        (Accounts and sources, Settings, a server's own menu; the settings
        changed): an open Servers page reads its servers again at once
        (server_accounts)."""
        from .. import server_accounts
        sig = server_accounts.signature()
        last = getattr(self, '_accounts_sig', None)
        self._accounts_sig = sig
        if last is None or sig == last:
            return
        for window in list(self._windows):
            if getattr(window, 'mode', '') == 'servers' and not getattr(window, '_closing', False):
                self.log('a server account changed: the Servers page reads its servers again')
                try:
                    window._start_build()
                except Exception:
                    self.log('servers page refresh failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    def _check_left_behind(self):
        """Close quietly when Kodi dropped these windows from its history.

        The Home key (or anything that activates Kodi's Home) removes every
        window above Home from the history without closing Python windows;
        their modal loops would keep running unseen. Being on Kodi's Home
        with none of these windows active and no dialog open means exactly
        that, and closing then only returns to Home, where the user already is.
        """
        windows = list(self._windows)
        if not windows or any(getattr(w, '_closing', False) for w in windows):
            self._left_ticks = 0
            return
        if not all(getattr(w, '_window_id', 0) for w in windows):
            return
        try:
            current = xbmcgui.getCurrentWindowId()
            dialog = xbmcgui.getCurrentWindowDialogId()
        except Exception:
            return
        ours = set(w._window_id for w in windows)
        if current in ours or current != HOME_WINDOW or dialog not in (9999, 0):
            self._left_ticks = 0
            return
        self._left_ticks = getattr(self, '_left_ticks', 0) + 1
        if self._left_ticks < 3:
            return
        self.log('Kodi left Dex Hub Home (window history reset); closing it')
        for window in reversed(windows):
            try:
                window.close_window()
            except Exception:
                pass

    def shutdown(self):
        root_id = str(getattr(self._root(), '_window_id', '') or '')
        self._windows = []
        self._publish_windows()
        try:
            home = xbmcgui.Window(HOME_WINDOW)
            if not root_id or home.getProperty(PROP_WINDOW) in ('', root_id):
                home.clearProperty(PROP_WINDOW)
                home.clearProperty(PROP_TOP)
                home.clearProperty(PROP_ALIVE)
        except Exception:
            pass
        self.stop_event.set()
        try:
            self.director.stop(wait=True)
        except Exception:
            pass
        try:
            from .live import release_windowed
            release_windowed(self.profile)
        except Exception:
            pass
        self.scheduler.stop()
        self.workers.stop()
        self.fast.stop()
        self.bg.stop()
        self.pre.stop()
        self.pages.stop()
        self.epg.stop()
        try:
            self.resolver.flush()
        except Exception:
            pass

    # ----------------------------------------------------------- entries
    def open_home(self, media='all'):
        from .window import BrowseWindow
        window = BrowseWindow(ui_size.xml('dexhub_browse.xml'), self.addon_path, 'Default', '1080i',
                              app=self, mode='home', media=media)
        self._show(window)

    def open_folder(self, set_id, group_id, folder_id, title='', media_filter=''):
        from .window import BrowseWindow
        window = BrowseWindow(ui_size.xml('dexhub_browse.xml'), self.addon_path, 'Default', '1080i',
                              app=self, mode='folder', folder_ref={
                                  'set_id': set_id, 'group_id': group_id,
                                  'folder_id': folder_id, 'media_filter': media_filter}, title=title)
        self._show(window)

    def open_grid(self, params, title='', shape='poster', page_tile=None):
        from .window import GridWindow
        if shape in ('wide', 'wide_ranked', 'feature', 'landscape_info'):
            shape = 'landscape'     # the grid has no big banner cards
        elif shape == 'circle':
            shape = 'square'
        elif shape in ('ranked', 'tall', 'tall_ranked', 'collage', 'mosaic', 'stack',
                       'poster_large', 'collection_spotlight', 'collection_compact', 'collection_ranking'):
            shape = 'poster'
        window = GridWindow(ui_size.xml('dexhub_grid.xml'), self.addon_path, 'Default', '1080i',
                            app=self, params=params, title=title, shape=shape,
                            page_tile=page_tile)
        self._show(window)

    def open_search(self, query=''):
        """The search page (v5.10.102): results as rows, in the Home's own design."""
        from .window import BrowseWindow
        window = BrowseWindow(ui_size.xml('dexhub_browse.xml'), self.addon_path, 'Default', '1080i',
                              app=self, mode='folder', search=str(query or ''),
                              title=self.tr('البحث'))
        self._show(window)

    def open_library(self):
        from .window import BrowseWindow
        window = BrowseWindow(ui_size.xml('dexhub_browse.xml'), self.addon_path, 'Default', '1080i',
                              app=self, mode='library', title=self.tr('مكتبتي'))
        self._show(window)

    def open_addons(self):
        """The Add-ons page (v5.10.167): Kodi's programs and video add-ons, as
        skin.dexhub's Add-ons tab shows them."""
        from .window import BrowseWindow
        window = BrowseWindow(ui_size.xml('dexhub_browse.xml'), self.addon_path, 'Default', '1080i',
                              app=self, mode='addons', title=self.tr('الإضافات'))
        self._show(window)

    def open_servers(self):
        """The Servers page (v5.10.103): Plex, Emby, Jellyfin and Silo, in the Home's design."""
        from .window import BrowseWindow
        window = BrowseWindow(ui_size.xml('dexhub_browse.xml'), self.addon_path, 'Default', '1080i',
                              app=self, mode='servers', title=self.tr('السيرفرات'))
        self._show(window)

    def open_follow(self, media=''):
        """My watching (v5.10.207): Up next, the watchlists, what is coming
        and picks from the last title, on one page (follow.py)."""
        from .follow import open_follow
        open_follow(self, media=media)

    def open_details(self, tile):
        """The title page (v5.10.103): a movie or series inside the Home UI."""
        from .details import DetailsWindow
        window = DetailsWindow(ui_size.xml('dexhub_details.xml'), self.addon_path, 'Default', '1080i',
                               app=self, tile=tile)
        self._show(window)

    def open_live(self):
        """Live TV (v5.10.131, drawn anew in v5.10.193): the guide, every
        category of a source with its channels' programmes on a timeline
        (guide.py)."""
        from .guide import GuideWindow
        window = GuideWindow(ui_size.xml('dexhub_live.xml'), self.addon_path, 'Default', '1080i', app=self)
        self._show(window)

    def _show(self, window):
        below = self.top()
        if below is not None:
            try:
                below.on_cover()
            except Exception:
                pass
        self.push(window)
        try:
            window.doModal()
        finally:
            try:
                window.on_close()
            except Exception:
                pass
            self.pop(window)
            del window
            top = self.top()
            if top is not None:
                try:
                    top.on_uncover()
                except Exception:
                    pass


def _running_windows():
    """(root id, top id) of a live Dex Hub Home in another invocation, or None."""
    try:
        home = xbmcgui.Window(HOME_WINDOW)
        root = int(home.getProperty(PROP_WINDOW) or 0)
        top = int(home.getProperty(PROP_TOP) or 0) or root
        alive = float(home.getProperty(PROP_ALIVE) or 0)
    except Exception:
        return None
    if root and time.time() - alive < 5.0:
        return root, top
    return None


def _busy():
    """Kodi refuses window switches while any modal dialog (busy spinner included) is open."""
    from .. import guistate     # v5.10.140: no getCondVisibility (guistate.py)
    return guistate.dialog_up()


def _leave_media_window():
    """Put Kodi's Home right under the Dex Hub Home window.

    Launched from inside Kodi's Videos window (the add-on browser, a skin menu
    entry or the classic Dex Hub menu) the window has to be left first: a
    window above Videos in the history is dropped the moment it opens any
    Videos listing, and closing the Home would land back in Videos instead of
    Kodi's Home. Right after the root listing was declined Kodi is still
    busy falling back to another folder, and it refuses to switch windows
    while its busy dialog is up, so the switch waits for it and is verified
    (v5.10.97; before, a refused switch left Videos under the Home window).
    """
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        try:
            if not xbmc.getCondVisibility('Window.IsActive(videos)'):
                return
        except Exception:
            return
        if not _busy():
            xbmc.executebuiltin('ActivateWindow(Home)', True)
            try:
                if not xbmc.getCondVisibility('Window.IsActive(videos)'):
                    return
            except Exception:
                return
        xbmc.sleep(60)
    _log('Videos window did not close before the Home opened', xbmc.LOGWARNING)


def _bring_back(running):
    """Activate the running Home's top window once Kodi accepts window switches."""
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            current = xbmcgui.getCurrentWindowId()
        except Exception:
            current = 0
        if current in running:
            return
        if not _busy():
            xbmc.executebuiltin('ActivateWindow(%d)' % running[1], True)
            try:
                if xbmcgui.getCurrentWindowId() in running:
                    return
            except Exception:
                return
        xbmc.sleep(60)


def _skin_native():
    """skin.dexhub draws the Home itself (v5.10.109)."""
    try:
        return xbmc.getSkinDir() == 'skin.dexhub'
    except Exception:
        return False


# v5.10.109: the pages the skin's own Home opens from its menu bar; with
# skin.dexhub they open on their own over the skin's Home
_SKIN_PAGES = {'home_servers': 'servers', 'home_live': 'live', 'home_search': 'search', 'home_library': 'library'}


def run(params):
    params = dict(params or {})
    action = params.get('action') or 'home_ui'
    if action == 'home_live':
        from ..ui_preferences import iptv_available
        if not iptv_available():
            xbmcgui.Dialog().notification('Dex Hub', 'IPTV / VOD: الأقسام متوقفة في الإعدادات', time=3000, sound=False)
            return None
    if action == 'home_vod_play':
        from . import vod
        try:
            return vod.play(params)
        except Exception:
            _log('VOD playback failed', xbmc.LOGWARNING)
            xbmcgui.Dialog().notification('Dex Hub', 'تعذر تشغيل VOD من المصدر', xbmcgui.NOTIFICATION_WARNING, 4000)
            return None
    if action == 'home_vod_series':
        # v5.10.239: an IPTV series' season (Continue Watching's menu), over any page
        return _run_page('vod', params)
    request = ''
    if action == 'home_follow':
        # v5.10.207: My watching, on its own over any Home
        return _run_page('follow', params)
    if action == 'home_details':
        # v5.10.114: a title's page on its own, over any skin's Home (a Dex
        # Hub title clicked in Arctic Fuse 3's widgets, the player's Info)
        return _run_page('details', params)
    if _skin_native():
        if action == 'home_ui':
            # the skin's Home is Dex Hub's Home
            from . import setup
            if setup.should_show():
                if setup.open_setup(first_run=True):
                    from ..skinui import common as _skin_common
                    _skin_common.ask_republish('accounts')
            xbmc.executebuiltin('ActivateWindow(Home)')
            return None
        if action == 'home_layout':
            from ..skinui import actions as _skin_actions
            _skin_actions.layout_editor()
            return None
        if action == 'home_community':
            from ..skinui import actions as _skin_actions
            _skin_actions.community()
            return None
        if action == 'home_menu':
            # v5.10.198: the control centre (a keymap or a shortcut can open it)
            from ..skinui import actions as _skin_actions
            _skin_actions.menu({}, -1)
            return None
        if action in _SKIN_PAGES:
            return _run_page(_SKIN_PAGES[action], params)
    if action in ('home_servers', 'home_search', 'home_library'):
        # (another skin: these open over the Dex Hub Home)
        action, request = 'home_ui', {'home_servers': 'servers', 'home_search': 'search', 'home_library': 'library'}[action]
    if action == 'home_screensaver':
        # v5.10.103: the screensaver choices, from Settings
        from . import screensaver
        screensaver.setup_menu()
        return None
    if action == 'home_layout':
        # v5.10.98: the Home layout editor, from Settings. It opens over the
        # Home (it arranges the Home's own rows), started if need be.
        action, request = 'home_ui', 'layout'
    elif action == 'home_community':
        # v5.10.195: Dex Community, over the Home (it adds rows to it)
        action, request = 'home_ui', 'community'
    elif action == 'home_menu':
        # v5.10.198: the control centre, over the Home
        action, request = 'home_ui', 'menu'
    elif action == 'home_live':
        # v5.10.103: Live TV (a skin shortcut can open it), over the Home
        action, request = 'home_ui', 'live'
    elif action == 'home_setup':
        # v5.10.102: Accounts and sources, from Settings or the classic menu.
        # Over the open Home, which rebuilds its rows after it; otherwise on
        # its own, over the page it was opened from.
        running = _running_windows()
        if running:
            try:
                xbmcgui.Window(HOME_WINDOW).setProperty(PROP_REQUEST, 'setup')
            except Exception:
                pass
            _bring_back(running)
            return None
        from . import setup
        if setup.open_setup(first_run=params.get('first') == '1'):
            if _skin_native():
                from ..skinui import common as _skin_common
                _skin_common.ask_republish('accounts')
            xbmc.executebuiltin('Container.Refresh')
        return None
    if action == 'home_ui':
        running = _running_windows()
        if running:
            # Bring the open Home back instead of stacking a second copy. Its
            # top window is activated, which only drops Kodi windows the user
            # opened from it; its own windows stay in Kodi's history.
            if request:
                # served by the running Home's own watch loop (_serve_request)
                try:
                    xbmcgui.Window(HOME_WINDOW).setProperty(PROP_REQUEST, request)
                except Exception:
                    pass
            _bring_back(running)
            return None
    _leave_media_window()
    if action == 'home_ui' and not request:
        # v5.10.102: the first run links an account or sources before the
        # Home is built, so the Home opens with their rows.
        try:
            from . import setup
            if setup.should_show():
                _log('first run: showing Accounts and sources')
                setup.open_setup(first_run=True)
        except Exception:
            _log('setup screen failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    app = App()
    app.request = request
    _live_jobs(stop=False)
    from . import keymap
    keymap.hold('home')
    try:
        if action == 'home_folder':
            app.open_folder(params.get('set_id', ''), params.get('group_id', ''),
                            params.get('folder_id', ''), title=params.get('title', ''),
                            media_filter=params.get('media_filter', ''))
        elif action == 'home_grid':
            from urllib.parse import parse_qsl
            route = dict(parse_qsl(params.get('route', ''), keep_blank_values=True))
            app.open_grid(route, title=params.get('title', ''))
        else:
            app.open_home(media=params.get('media', 'all') or 'all')
        monitor = xbmc.Monitor()
        while app.reopen and not app.stop_event.is_set() and not monitor.abortRequested():
            media, app.reopen = app.reopen, ''
            app.open_home(media=media)
    except Exception:
        _log('window failed:\n%s' % traceback.format_exc(), xbmc.LOGERROR)
        try:
            xbmcgui.Dialog().notification('Dex Hub', 'Dex Hub Home: %s' % 'error',
                                          xbmcgui.NOTIFICATION_ERROR, 3000)
        except Exception:
            pass
    finally:
        keymap.release('home')
        app.shutdown()
        # v5.10.104: everything this Home started ends before the script
        # does: playlist and guide downloads stop at their next piece, and the
        # shared network lanes (DexHub-browse and the others) are shut down
        # now instead of after the wait below, which they used to outlive.
        _live_jobs(stop=True)
        _shutdown_pools()
        _await_threads(timeout=4.0)
    return None


def _details_tile(params):
    """The tile of a title page opened by a query (skin.dexhub's dhs.q:
    m, id, imdb, tmdb, title, year, src)."""
    from urllib.parse import urlencode
    media = 'series' if str(params.get('m') or '') in ('series', 'anime', 'tvshow') else 'movie'
    canonical = params.get('id') or params.get('imdb') or ''
    if not canonical and params.get('tmdb'):
        canonical = 'tmdb:%s' % params['tmdb']
    route = [('action', 'item_open'), ('media_type', params.get('m') or media), ('canonical_id', canonical),
             ('title', params.get('title') or ''), ('tmdb_id', params.get('tmdb') or ''),
             ('imdb_id', params.get('imdb') or ''), ('source_provider_id', params.get('src') or '')]
    if params.get('srv'):
        from urllib.parse import parse_qsl
        route=list(parse_qsl(params['srv']))
    return {'kind': 'work', 'media_type': media, 'title': params.get('title') or '',
            'year': params.get('year') or '', 'imdb_id': params.get('imdb') or '',
            'tmdb_id': params.get('tmdb') or '', 'folder': media == 'series',
            'path': 'plugin://plugin.video.dexhub/?' + urlencode([(k, v) for k, v in route if v])}


def _run_page(page, params):
    """One page of the Dex Hub Home (Servers, Live TV, Search) on its own,
    over skin.dexhub's Home (v5.10.109)."""
    app = App()
    # the page sits over the skin's Home: Back returns there (no "Leave Dex Hub?")
    app.skin_page = True
    _live_jobs(stop=False)
    from . import keymap
    keymap.hold('home')
    try:
        if page == 'library':
            app.open_library()
        elif page == 'servers':
            app.open_servers()
        elif page == 'live':
            app.open_live()
        elif page == 'details':
            app.open_details(_details_tile(params))
        elif page == 'follow':
            app.open_follow(params.get('media') or '')
        elif page == 'vod':
            grid = dict((k, v) for k, v in params.items() if k != 'action')
            grid['action'] = 'home_vod_list'
            app.open_grid(grid, title=params.get('title') or '',
                          shape='landscape' if grid.get('season') else 'poster')
        else:
            app.open_search(params.get('query', '') or '')
        if page in ('library', 'servers') and app.nav_next:
            # v5.10.167: a tab picked on the page: skin.dexhub's own tab
            tab = {'home': 'all'}.get(app.nav_next, app.nav_next)
            xbmc.executebuiltin('SetProperty(dhs.tab,%s,home)' % tab)
            xbmc.executebuiltin('SetProperty(dhs.fr,0,home)')
            xbmc.executebuiltin('ActivateWindow(Home)')
    except Exception:
        _log('page failed:\n%s' % traceback.format_exc(), xbmc.LOGERROR)
    finally:
        keymap.release('home')
        app.shutdown()
        _live_jobs(stop=True)
        _shutdown_pools()
        _await_threads(timeout=4.0)
    return None


def _live_jobs(stop):
    """Stop (or allow again) the IPTV background jobs, when loaded."""
    module = sys.modules.get(__package__ + '.live_iptv')
    if module is None:
        return
    try:
        if stop:
            module.stop_jobs(wait=2.0)
        else:
            module.resume_jobs()
    except Exception:
        pass


def _shutdown_pools():
    try:
        from .. import runtime_cleanup
        runtime_cleanup.shutdown_loaded_pools()
    except Exception:
        pass


def _await_threads(timeout=1.5):
    """Let this invocation's threads end before the script does (v5.10.98).

    Kodi waits for every thread of a finished script, and when it has to stop
    the interpreter instead (an add-on update or disable, Kodi's exit) while
    one still runs, CPython can crash tearing it down. The Home's own threads
    stop on its stop event; anything still alive after a short wait is named
    in kodi.log so it can be found.
    """
    deadline = time.monotonic() + timeout
    current = threading.current_thread()
    while True:
        alive = [t for t in threading.enumerate()
                 if t is not current and t is not threading.main_thread() and t.is_alive()]
        if not alive or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    if alive:
        _log('threads still running after the Home closed: %s' % ', '.join(
            sorted(t.name for t in alive)), xbmc.LOGINFO)
