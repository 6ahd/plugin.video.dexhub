# -*- coding: utf-8 -*-
"""The service side of skin.dexhub: publishes the Home's rows and keeps their
caches fresh, so the skin's rows always come from a cache (instant) and are
read again in the background.

Started by service.py; it waits while another skin is in use. Its work:

  * at start (and on dhs.republish): the rows of every tab are worked out and
    published, then the rows on show are read ahead, the current tab first;
  * want/: rows a skin_row call served from an old copy are read again;
  * after playback, the Continue Watching and Next Up rows are read again;
  * every few minutes the rows are worked out again (a Nuvio sync or a new
    catalog changes them) and stale rows are read again;
  * the TMDb pass gives the titles their plot and logo in the user's
    language (homeui's enrich), after a row is read.

A row whose content changed gets a new path (rows.bump), so Kodi reads it
again from its fresh cache; a row whose content did not change is left alone.
Nothing is read while a video plays, except the progress rows after it.
"""
import bisect
import os
import time
from .. import lazy_tb as traceback

import xbmc

from .. import guistate
from . import common as C

_HEARTBEAT = 'dhs.worker'
_CHECK_EVERY = 5 * 60.0         # work the rows out again (Nuvio syncs, new catalogs)
_PROGRESS_GAP = 12.0            # the least time between two reads of the progress rows


def _quiet():
    """Nobody is looking at the Home: screensaver, a quarter hour idle, or
    a video playing."""
    try:
        return bool(xbmc.getCondVisibility('System.ScreenSaverActive')
                    or xbmc.getCondVisibility('System.IdleTime(900)')
                    or xbmc.getCondVisibility('Player.HasVideo'))
    except Exception:
        return False
_EMPTY_EVERY = 30 * 60.0        # read rows that came back empty again
_PROGRESS_AFTER = 4.0           # seconds after playback stops (progress is saved first)
_REAL_PLAY = 20.0               # a video played this long changes the progress rows
# the title page's lists in Arctic Fuse 3 with Dex Hub's pages (as skin.dexhub 3's)
_TP_LISTS = {'seasons': 8200, 'episodes': 8300, 'cast': 8400, 'related': 8500}


# rows below the cursor the skin shows from their saved copies (v5.10.140)
SHOW_AHEAD = 3


def _settings_snapshot():
    import xml.etree.ElementTree as ET
    import xbmcvfs
    try:
        path = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/settings.xml')
        return {s.get('id'): s.text or '' for s in ET.parse(path).getroot().iter('setting') if s.get('id')}
    except Exception:
        return {}


def _bookkeeping(key):
    return key in ('welcome_seen', 'wizard_completed', 'metadata_generation', 'last_account_sync_at',
                   'plex_client_identifier', 'plex_servers_cache_json', 'ui_lang_autodetected',
                   'theme_preset', 'switch_source_keymap_data') or key.startswith(('dexhub_defaults_',
                   'dexhub_v', 'perf_defaults_', 'subtitle_defaults_', 'trailer_sound_default_'))



def run(monitor):
    """Service thread: serve the skin while it is in use (skin.dexhub, or the
    Arctic Fuse 3 Dex Hub's rows were installed in, v5.10.111)."""
    while not monitor.abortRequested():
        if C.served():
            try:
                Worker(monitor).loop()
            except Exception:
                C.log('worker stopped:\n%s' % traceback.format_exc(), xbmc.LOGERROR)
                if monitor.waitForAbort(30):
                    break
        if monitor.waitForAbort(5):
            break
    C.set_prop(_HEARTBEAT, '')


class _Events(xbmc.Monitor):
    """Kodi's library and player notifications: after one, Kodi reloads every
    list of video items on show at once, and a reload that starts in the same
    instant as another can fail (the list comes back empty): the lists are
    checked a moment later and a failed one is loaded again."""

    _METHODS = ('VideoLibrary.OnUpdate', 'VideoLibrary.OnScanFinished', 'VideoLibrary.OnCleanFinished',
                'VideoLibrary.OnRemove', 'Player.OnStop')

    def __init__(self):
        super(_Events, self).__init__()
        self.due = []
        self.settings_changed = False
        self.trailer = False

    def onSettingsChanged(self):
        self.settings_changed = True

    def onNotification(self, sender, method, data):
        if method == 'System.OnWake':
            # v5.10.314: servers asked in the next minute and a half wait
            # for the network (net_wake.py)
            from ..net_wake import mark_woke
            mark_woke()
            return
        if method in ('Player.OnPlay', 'Player.OnAVStart'):
            # v5.10.239: was it a Home trailer? (its end changes no list)
            self.trailer = bool(C.prop('dexhub.trailer.active') or C.prop('dhs.trailer'))
            return
        if method in self._METHODS:
            if method == 'Player.OnStop' and (getattr(self, 'trailer', False) or C.prop('dexhub.trailer.active')):
                self.trailer = False
                return
            now = time.time()
            self.due = [now + 2.0, now + 5.0, now + 10.0]


class Worker(object):
    def __init__(self, monitor):
        self.monitor = monitor
        self.events = _Events()
        self._settings_snap = _settings_snapshot()
        # v5.10.288: the poster choice's stamp (rows saved under another one,
        # by an earlier version included, get the poster pass when shown)
        C.set_prop(C.PROP_POSTER_GEN, C.poster_gen())
        self._healed = {}
        self._missing = {}              # v5.10.188: rows seen without their titles, since when
        self.player = xbmc.Player()
        self._republish_token = C.prop(C.PROP_REPUBLISH)
        self._progress_token = C.prop('dhs.progress.changed')
        self._progress_action_token = C.prop('dhs.progress.action')
        self._playing = False
        self._real_since = 0.0
        self._progress_due = 0.0
        self._progress_read_at = 0.0
        self._was_quiet = False
        self._quiet = False
        self._quiet_at = 0.0
        self._last_check = 0.0
        self._last_empty = time.time()
        self._signature = None
        self._ahead_tab = None          # the Home tab whose rows read_rows_ahead lets in
        self._section_check_at = 0.0
        self._ahead_window = None
        self._pending_updates = {}
        self._pending_metadata = set()
        self._redraw_hashes = {}
        self._later = None              # v5.10.188: tabs published after the one on screen
        self._slow_tabs = set()         # tabs whose rows took long: profiled at the next check
        self._profiled = set()
        self._published_at = 0.0
        from ..homeui.loading import Budget
        self._load_budget = Budget()
        # 'dexhub' (skin.dexhub) or 'af3' (Arctic Fuse 3 with Dex Hub's rows
        # as its widgets: af3.Service loads them in turn and follows Dex Hub)
        self.mode = C.served()

    # ------------------------------------------------------------ helpers
    def beat(self):
        # v5.10.165: every five seconds is enough (the readers allow 15 and
        # 90); it was written twice a loop, four Home property writes a second
        now = time.time()
        if now - getattr(self, '_beat_at', 0.0) >= 5.0:
            self._beat_at = now
            C.set_prop(_HEARTBEAT, '%.1f' % now)

    def stopped(self):
        return self.monitor.abortRequested() or C.served() != self.mode

    def busy(self):
        """A video plays: no reading ahead (it competes with the stream).

        v5.10.165: the answer is kept a quarter of a second; one loop asked
        it six times (each a trailer property and a dialog id from Kodi)."""
        now = time.monotonic()
        cached = getattr(self, '_busy_cache', None)
        if cached is not None and now - cached[0] < 0.25:
            return cached[1]
        try:
            # v5.10.140: the dialog id, not getCondVisibility (guistate.py)
            value = bool(self.watching() or (not C.prop('dexhub.trailer.active') and guistate.busy_dialog()))
        except Exception:
            value = False
        self._busy_cache = (now, value)
        return value

    def _set_once(self, key, value):
        """A Home property written when it changes, and every half minute
        whatever happened to it meanwhile (v5.10.165)."""
        written = self.__dict__.setdefault('_written', {})
        now = time.monotonic()
        last = written.get(key)
        if last is None or last[0] != value or now - last[1] > 30.0:
            written[key] = (value, now)
            C.set_prop(key, value)

    def preview_transition(self):
        """Hold automatic row work during resolve/open/first-frame settle.

        A playing background preview is deliberately not busy(), but treating
        its *startup* as idle let enrichment rebind the neighboring lists just
        as Kodi opened the renderer. Rebinding replaces the list's layouts and
        its background-loaded covers briefly expose their text placeholders.
        The director keeps preview.opening set for 0.8 s after AVStarted.
        Never turn an abandoned loading status into a permanent refresh lock.
        """
        active = bool(C.prop('dhs.preview.opening')
                      or C.prop('dhs.trailer.status') == 'loading')
        if not active:
            self._preview_transition_at = None
            return False
        now = time.monotonic()
        since = getattr(self, '_preview_transition_at', None)
        if since is None:
            self._preview_transition_at = since = now
        return now - since < 30.0

    def rest(self, seconds):
        self.beat()
        return self.monitor.waitForAbort(seconds)

    # ------------------------------------------------------------ the loop
    def loop(self):
        from . import rows
        C.log('worker started (%s)' % self.mode)
        self.beat()
        if self.mode == 'dexhub':
            # the Nuvio profile's name and picture on the Home (v5.10.114)
            try:
                from . import profile
                profile.publish(fetch=True)
            except Exception:
                C.log('profile:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            # v5.10.117: the player's options as Arctic Fuse 3 had them (once)
            try:
                from . import player_options
                player_options.seed_from_af3()
            except Exception:
                C.log('player options:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        self.spotlight = None
        self.af3 = None
        if self.mode == 'af3':
            # Arctic Fuse 3 with Dex Hub inside (v5.10.112): its rows load one at
            # a time (from the menus saved last time, before anything else),
            # follow Dex Hub, and its trailers play when patched in
            try:
                from . import af3
                self.af3 = af3.Service(self.monitor)
                self.af3.start()
            except Exception:
                C.log('Arctic Fuse 3:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        try:
            # v5.10.133: the rows' marshal copies (a row call imports no json)
            made = C.ensure_row_copies()
            if made:
                C.log('row caches given their quick copy: %d' % made)
        except Exception:
            C.log('row copies:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        self.start()
        import threading
        threading.Thread(target=self.first_run, name='dexhub-skin-firstrun', daemon=True).start()
        layout = C.layout()
        if self.mode == 'dexhub' and layout.hubs:
            try:
                self.ensure_hubs()
            except Exception:
                C.log('hubs:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        if self.mode == 'dexhub' and layout.trailers:
            # skin.dexhub 2 and 3: trailers behind the spotlight
            try:
                from .spotlight import Spotlight
                self.spotlight = Spotlight(self.monitor)
                self.spotlight.start()
            except Exception:
                C.log('trailers off:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        self.grid = None
        if self.mode == 'dexhub':
            # the focused title of a Dex Hub grid, as the add-on's grid shows it (v5.10.114)
            try:
                from .gridfocus import GridFocus
                self.grid = GridFocus(self.monitor, self.watching)
                self.grid.start()
            except Exception:
                C.log('grid focus off:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        self.ratings = None
        if self.mode == 'dexhub':
            # the ratings of the title shown large, with their logos (v5.10.115)
            try:
                from .herofocus import HeroFocus
                self.ratings = HeroFocus(self.monitor, self.watching)
                self.ratings.start()
            except Exception:
                C.log('ratings off:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        # v5.10.132: grids and title lists worked out here for the plugin
        # calls (listing.py), skin.dexhub and Arctic Fuse 3 alike
        self.listing = None
        try:
            from .listing import Server
            self.listing = Server(self.monitor)
            self.listing.start()
        except Exception:
            C.log('listing server off:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        try:
            self._loop(rows)
        finally:
            if self.listing is not None:
                self.listing.stop()
            if self.ratings is not None:
                self.ratings.stop()
            if self.grid is not None:
                self.grid.stop()
            if self.spotlight is not None:
                self.spotlight.stop()
            if self.af3 is not None:
                self.af3.stop()

    def first_run(self):
        """v5.10.115: a first run with nothing linked opens Accounts and
        sources (homeui/setup.py) over the skin's Home, as the add-on's own
        Home does at its start: Nuvio, Stremio and the other services, and
        Kaptain's collection ticked."""
        try:
            from ..homeui import setup
            if not setup.should_show():
                return
        except Exception:
            C.log('first run:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
        deadline = time.time() + 60.0
        while time.time() < deadline and not self.stopped():
            if xbmc.getCondVisibility('Window.IsActive(home) + !System.HasModalDialog'):
                C.log('first run: Accounts and sources')
                xbmc.executebuiltin('RunPlugin(%s)' % C.url('home_setup', first='1'))
                return
            if self.rest(0.5):
                return

    def ensure_hubs(self):
        """skin.dexhub 2 turns its Dex Hub hubs on in its first run. Skin
        settings left by skin.dexhub 1 (same id) or a first run that never
        ran left the hubs as plain Arctic Fuse ones, with nothing of Dex Hub
        in them: they are turned on here, once (v5.10.110)."""
        cond = xbmc.getCondVisibility
        if cond('Skin.HasSetting(DexHub.HubsReady)'):
            return
        if not any(cond('Skin.HasSetting(DexHub.Hub.%s)' % w) for w in ('Home', '1101', '1102')):
            for builtin in ('Skin.SetBool(DexHub.Hub.Home)', 'Skin.SetBool(DexHub.Hub.1101)',
                            'Skin.SetBool(DexHub.Hub.1102)',
                            'Skin.SetString(HomeSwitcher.1101.Toggle,true)',
                            'Skin.SetString(HomeSwitcher.1102.Toggle,true)',
                            'Skin.SetString(HomeSwitcher.Home.Icon,special://skin/extras/icons/home.png)',
                            'Skin.SetString(HomeSwitcher.1101.Icon,special://skin/extras/icons/film.png)',
                            'Skin.SetString(HomeSwitcher.1102.Icon,special://skin/extras/icons/tv.png)'):
                xbmc.executebuiltin(builtin)
            C.log('Dex Hub hubs turned on (the skin had not)')
            try:
                from . import strings
                strings.publish(force=True)        # their names in Dex Hub's language
            except Exception:
                pass
            xbmc.executebuiltin('ReloadSkin()')
        xbmc.executebuiltin('Skin.SetBool(DexHub.HubsReady)')

    def _loop(self, rows):
        if self.mode == 'dexhub':
            try:
                from .. import theme_sync
                theme_sync.reset()
            except Exception:
                pass
        else:
            C.set_prop('dhs.load.window', '')  # AF3 keeps its own widget loading policy
        self.read_rows_ahead()
        self.read_ahead()
        while not self.stopped():
            self.beat()
            self.flush_updates()
            if self.republish_if_asked():
                self.read_ahead()
                continue
            self.dispatch_requests(rows)
            self.read_rows_ahead()
            self.focus_gif(rows)
            self.publish_later()
            if self.mode == 'dexhub':
                # v5.10.131: the skin's colour theme and Dex Hub's theme in step
                try:
                    from .. import theme_sync
                    theme_sync.tick()
                except Exception:
                    C.log('theme:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            self.watch_playback()
            now = time.time()
            self._check_progress_actions(now)
            if self._progress_due and now >= self._progress_due:
                self._progress_due = 0.0
                self._progress_read_at = now
                self.refresh_progress()
            token = C.prop('dhs.progress.changed')
            if token and token != self._progress_token:
                self._progress_token = token
                # v5.10.255: a stop writes progress several times in a few
                # seconds (the stop, the services' push, finished): one read
                # of the rows for them all, not three (an 80-card server
                # row took 1.5 to 2.5 s each on an AM8 Pro)
                if now - self._progress_read_at < _PROGRESS_GAP:
                    self._progress_due = max(self._progress_due, self._progress_read_at + _PROGRESS_GAP)
                else:
                    self._progress_read_at = now
                    self.refresh_progress()
            if self.events.due and now >= self.events.due[0]:
                self.events.due.pop(0)
                if self.heal():
                    self.events.due.insert(0, now + 1.5)   # one at a time: look again soon
            if now - self._quiet_at >= 10.0:      # three conditions, every 10 s only
                self._quiet_at = now
                self._quiet = _quiet()
            quiet = self._quiet
            if self._was_quiet and not quiet:
                self._last_check = 0.0      # back from the screensaver: look at once
            self._was_quiet = quiet
            if quiet:
                # v5.10.365: once per screensaver, the Home is made ready
                from . import preload
                preload.tick(self, now)
            if not self.busy() and not self.preview_transition() and now - self._last_check >= _CHECK_EVERY:
                self._last_check = now
                self.check(quiet=quiet)
                self.heal()
            from ..home_preferences import apply_pending_scale
            apply_pending_scale()
            if self.events.settings_changed:
                self.events.settings_changed = False
                self.apply_settings()
            if self.rest(self._pause()):
                return
        C.log('worker paused (the skin in use changed)')

    def _pause(self):
        """v5.10.198: the loop's rest: half a second, or a second and a half
        while nothing waits on the loop and nothing was pressed for a while
        (an idle Home, a video playing)."""
        if self._later is not None or self.events.due or self._progress_due:
            return 0.5
        return C.poll_wait(0.5, slow=1.5, idle_after=20.0)

    def apply_settings(self):
        from . import rows, strings
        from ..settings_cache import invalidate
        from ..ui_preferences import DISPLAY_ONLY
        from ..homeui.app import Settings
        invalidate()
        snap = _settings_snapshot()
        old, self._settings_snap = self._settings_snap, snap
        changed = {k for k in set(old) | set(snap) if old.get(k) != snap.get(k) and not _bookkeeping(k)}
        if not changed:
            return
        rows.app().settings = Settings()
        strings.publish(force=True)
        if self.spotlight is not None:
            self.spotlight._settings = None
        if 'homeui_hero_shuffle' in changed:
            # v5.10.364: the banner's titles in their new order
            try:
                from . import board
                for tab in C.TABS:
                    board.update_spot(tab)
            except Exception:
                C.log('hero order:\n%s' % traceback.format_exc(), xbmc.LOGDEBUG)
        if 'ratings_shown' in changed and getattr(self, 'ratings', None) is not None:
            self.ratings.shown = ''         # v5.10.334: the hero's ratings again, as chosen
        filters = changed & {'library_src', 'servers_src', 'show_servers'}
        if filters:
            if 'show_servers' in changed and snap.get('show_servers') == 'false' and C.prop('dhs.tab') == 'servers':
                C.set_prop('dhs.tab', 'all')
                C.set_prop('dhs.fr', '0')
                C.set_prop('dhs.rmax', '1')
                self._ahead_window = None
            tabs = tuple(t for t, keys in (('library', {'library_src'}),
                         ('servers', {'servers_src', 'show_servers'})) if changed & keys)
            rows.publish(tabs=tabs, reason='section preferences')
            self._signature = self.signature()
        if changed & {'plex_auth_json', 'emby_auth_json', 'jellyfin_auth_json', 'silo_auth_json'}:
            # v5.10.198: a server linked or removed in another invocation:
            # the Servers tab reads the servers again at once
            try:
                from ..routes import servers_tab
                if servers_tab.accounts_changed():
                    C.ask_republish('servers source')
            except Exception:
                C.log('servers watch:\n%s' % traceback.format_exc(), xbmc.LOGDEBUG)
        if 'server_metadata' in changed:
            # v5.10.198: the server rows' titles and art follow the new choice
            try:
                from ..meta_policy import refresh_server_rows
                refresh_server_rows()
            except Exception:
                C.log('server rows:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        # v5.10.288: another poster choice: every saved row gets the poster pass
        stamp = C.poster_gen()
        if stamp and stamp != C.prop(C.PROP_POSTER_GEN):
            self.posters_changed(stamp)
        if changed - DISPLAY_ONLY - {'library_src', 'servers_src'}:
            rows.reset_settings()
            self.check()

    def posters_changed(self, stamp):
        """v5.10.288: the poster source (or a unified metadata source, or Better
        Posters' options) changed. The new stamp marks every saved row of every
        page as not passed (common.is_enriched), so each row on show or shown
        later is re-dressed (rows._poster_pass) from its saved copy; no catalog
        is read again. The rows on the Home's tabs and on an open folder page
        are asked at once; the caches of finished listings go."""
        from . import rows
        C.set_prop(C.PROP_POSTER_GEN, stamp)
        try:
            from .. import better_posters
            better_posters.invalidate()     # its options are kept for the process
        except Exception:
            pass
        try:
            rows.R.forget_pages()           # pages read ahead, with the old posters
        except Exception:
            pass
        try:
            from .. import widget_cache
            widget_cache.clear()            # finished listings replayed to Kodi's widgets
        except Exception:
            pass
        self._ahead_window = None       # read_rows_ahead looks at the rows on show again
        try:
            from .folderpage import TAB as fold_tab
        except Exception:
            fold_tab = 'fold'
        active = C.prop('dhs.tab') or 'all'
        asked = 0
        for tab in [active] + [t for t in C.TABS if t != active] + [fold_tab]:
            for _slot, spec in rows.published(tab):
                if spec.get('id'):
                    C.request_refresh(tab, spec['id'], enrich_only=True)
                    asked += 1
        C.log('poster choice changed: %d row(s) get the poster pass' % asked)

    def read_rows_ahead(self):
        """Expose the focused row and its neighbors, without crawling the Home."""
        if self.mode != 'dexhub' or self.busy() or self.preview_transition():
            return
        layout = C.layout()
        if layout.hubs:
            return                          # skin.dexhub 2: its hubs load every row already
        view = self.visible_rows()
        if view is None:
            self._ahead_window = None
            return
        tab, center, depth = view
        if tab != self._ahead_tab:
            self._ahead_tab = tab
            self._section_check_at = time.monotonic()
            if tab in ('library', 'servers'):
                from . import rows
                rows.publish(tabs=(tab,), reason='section opened')
                self._signature = self.signature()
        elif tab in ('servers', 'library') and time.monotonic() - self._section_check_at >= 5.0:
            self._section_check_at = time.monotonic()
            # Retry discovery without parsing the section caches on every cursor tick.
            if tab == 'servers':
                from ..routes import servers_tab
                servers_tab._servers()
            else:
                from ..routes import library
                library.refresh_later()
        reach = min(C.ROWS, center + depth - 1)
        # v5.10.140: the skin shows rows down to SHOW_AHEAD below the cursor
        # from their saved copies (nothing leaves the device for them), so a
        # row has its titles when the cursor lands on it; only the rows
        # within the loading depth are refreshed from their source. With the
        # reach at depth alone (one row below the cursor in light mode) rows
        # 5 to 7 of a Home landed empty and filled 0.7 to 1.9 s later.
        shown = min(C.ROWS, max(reach, center + SHOW_AHEAD))
        # v5.10.165: written when they change (they were written twice a second)
        if tab != 'fold':
            self._set_once('dhs.load.window', '1')
            self._set_once('dhs.rreach', str(shown))
            self._set_once('dhs.rpre', str(shown))  # compatibility with earlier skins
        else:
            self._set_once('dhs.freach', str(shown))
        window = (tab, center, depth)
        if window == self._ahead_window:
            return
        self._ahead_window = window
        from . import rows
        for slot, spec in rows.published(tab):
            if max(1, center - 1) <= slot <= reach:
                data = C.read_row(C.row_file(tab, spec['id']))
                if not C.fresh_copy(data, rows.ttl_of(spec)):
                    C.request_refresh(tab, spec['id'])
                elif not rows.is_enriched(data):
                    C.request_refresh(tab, spec['id'], enrich_only=True)

    def visible_rows(self):
        """None off the browse page; (tab, focused slot, loading depth) on it."""
        if self.mode != 'dexhub' or C.layout().hubs:
            return None
        if guistate.is_active(1190):
            tab, prop = 'fold', 'dhs.ffr'
        else:
            tab, prop = C.layout().tab_on_show(), 'dhs.fr'
        if tab is None:
            return None
        try:
            center = max(1, min(C.ROWS, int(C.prop(prop) or 1)))
        except ValueError:
            center = 1
        from . import rows
        depth = self._load_budget.depth(light=rows.app().settings.flag('homeui_light_mode', True),
                                        busy=self.busy())
        return tab, center, depth

    def request_priority(self, tab, row_id, kind):
        if self.mode != 'dexhub' or C.layout().hubs:
            return (kind == 'e', tab != (C.prop('dhs.tab') or 'all'), 0)
        view = self.visible_rows()
        if view is None or tab != view[0]:
            return None
        _tab, center, depth = view
        slot = next((n for n in range(1, C.ROWS + 1)
                     if C.prop('dhs.%s.%d.id' % (tab, n)) == row_id), None)
        if slot is None or slot < max(1, center - 1):
            return None
        if slot >= center + depth:
            # v5.10.140: the skin shows rows down to SHOW_AHEAD below the
            # cursor. One with a saved copy shows it and waits; one never read
            # has nothing to show, so its first read goes ahead, after the
            # rows in the loading depth (else it stayed "loading" until the
            # cursor came close).
            if kind != 'r' or slot > center + SHOW_AHEAD or os.path.exists(C.row_file(tab, row_id)):
                return None
        return (kind == 'e', slot != center, abs(slot - center))

    def dispatch_requests(self, rows):
        if self.busy() or self.preview_transition():
            return
        requests = []
        for entry in C.take_requests():
            priority = self.request_priority(*entry)
            if priority is not None:
                requests.append((priority, entry))
        requests.sort(key=lambda entry: entry[0])
        used = set()
        for _priority, (tab, row_id, kind) in requests:
            if self.request_priority(tab, row_id, kind) is None:
                continue  # the user left this section while another request ran
            if kind in used or self.stopped() or self.busy() or self.preview_transition():
                C.request_refresh(tab, row_id, enrich_only=kind == 'e')
                continue
            used.add(kind)
            if kind == 'e':
                self.enrich(tab, row_id, rows.find_spec(tab, row_id))
            else:
                self.refresh(tab, row_id, enrich=False)
                C.request_refresh(tab, row_id, enrich_only=True)

    def start(self):
        """Publish the rows, unless the Home's own call just did (or does now)."""
        deadline = time.time() + 15.0
        while C.prop(C.PROP_PUBLISHING) and time.time() < deadline:
            if self.rest(0.2):
                return
        try:
            recent = time.time() - float(C.prop(C.PROP_PUBLISHED) or 0) < 60.0
        except ValueError:
            recent = False
        if C.prop(C.PROP_READY) and recent:
            self._signature = self.signature()
            self._last_check = time.time()
        else:
            self.publish('start')
        # a request made while starting is answered by this publish
        self._republish_token = C.prop(C.PROP_REPUBLISH)

    # ------------------------------------------------------------ healing
    def heal(self):
        """A Home row or a title page list whose load failed (empty, idle,
        though it has titles): loaded again, one per call. True when one was."""
        if self.mode != 'dexhub':
            # Arctic Fuse 3 loads its own widgets; Dex Hub's title page in it (v5.10.119)
            try:
                if guistate.video_info_up():
                    from . import af3pages
                    if af3pages.installed():
                        return self.heal_title(_TP_LISTS)
            except Exception:
                C.log('heal failed:\n%s' % traceback.format_exc(), xbmc.LOGDEBUG)
            return False
        try:
            if guistate.video_info_up():
                return self.heal_title()
            if C.layout().tab_on_show():
                return self.heal_home()
        except Exception:
            C.log('heal failed:\n%s' % traceback.format_exc(), xbmc.LOGDEBUG)
        return False

    def _may_heal(self, key):
        last = self._healed.get(key, 0.0)
        if time.time() - last < 20.0:
            return False
        self._healed[key] = time.time()
        return True

    def heal_home(self):
        from . import rows
        layout = C.layout()
        tab = layout.tab_on_show()
        if not tab:
            return False
        if guistate.dialog_up():
            # v5.10.189: Container(id) labels are read in the dialog on top
            # (a menu, the style gallery, Kodi's busy spinner), where the
            # Home's lists do not exist: every row looked empty and was
            # loaded again (a box: Continue Watching, "0 titles")
            return False
        info, cond = xbmc.getInfoLabel, xbmc.getCondVisibility
        if layout.hubs:
            reach = C.ROWS      # every row of a hub loads, one after another
        else:
            # v5.10.133: the rows skin.dexhub 3.14 lets in (three below the
            # cursor's furthest row, and the ones read ahead while resting)
            try:
                reach = max(2, int(C.prop('dhs.rmax') or 1), int(C.prop('dhs.rreach') or 0))
            except ValueError:
                reach = 4
        if layout.spotlight:
            if self.heal_spotlight(tab, layout):
                return True
            spot_key = C.prop('dhs.%s.spot.key' % tab)
            if (spot_key and C.prop('dhs.%s.spot.path' % tab)
                    and info('Container(%d).ListItemAbsolute(0).Property(dhs.row)' % layout.spotlight) != spot_key):
                return False    # the rows wait for the spotlight
        for slot, spec in rows.published(tab):
            if slot > reach:
                break
            cid = layout.row_list(slot)
            key = '%s:%s' % (tab, spec['id'])
            # v5.10.188: the list's first title, not the selected one
            holds = info('Container(%d).ListItemAbsolute(0).Property(dhs.row)' % cid)
            if holds == key:
                self._missing.pop(key, None)
                continue                    # it holds its titles
            if cond('Container(%d).IsUpdating' % cid):
                return False                # loading now (the rows below wait for it)
            if slot > 1 and info('Container(%d).ListItemAbsolute(0).Property(dhs.row)' % (cid - 1)) != \
                    C.prop('dhs.%s.%d.key' % (tab, slot - 1)):
                return False                # not its turn yet
            data = C.read_row(C.row_file(tab, spec['id']))
            if not (data and data.get('items')):
                return False
            # v5.10.188: missing twice, two seconds apart (a row between its
            # new path and Kodi's next frame looked failed: a box reloaded
            # Continue Watching at every five-minute check)
            now = time.monotonic()
            first = self._missing.get(key)
            if first is None or now - first > 30.0:
                self._missing[key] = now
                bisect.insort(self.events.due, time.time() + 2.2)     # look again then
                return False
            if now - first < 2.0:
                return False
            if not self._may_heal(key):
                return False
            self._missing.pop(key, None)
            C.log('row "%s" loaded again (its last load failed: %s titles, first of %s, path v%s)' % (
                spec.get('title'), info('Container(%d).NumItems' % cid) or '0', holds or 'none',
                rows._version(tab, spec['id'])))
            rows.bump(tab, spec['id'], spec)
            return True
        return False

    def heal_spotlight(self, tab, layout):
        """The hub's spotlight list, when its load failed (True when loaded again)."""
        from . import board
        key, path = C.prop('dhs.%s.spot.key' % tab), C.prop('dhs.%s.spot.path' % tab)
        if not key or not path:
            return False
        info, cond = xbmc.getInfoLabel, xbmc.getCondVisibility
        if info('Container(%d).ListItemAbsolute(0).Property(dhs.row)' % layout.spotlight) == key:
            return False
        if cond('Container(%d).IsUpdating' % layout.spotlight):
            return False
        data = C.read_row(C.row_file(tab, board.SPOT))
        if not (data and data.get('items')) or not self._may_heal(key):
            return False
        C.log('spotlight of %s loaded again (its last load failed)' % tab)
        C.set_prop('dhs.v.%s.%s' % (tab, board.SPOT), board._version(tab, board.SPOT) + 1)
        C.set_prop('dhs.%s.spot.path' % tab, board.row_path(tab, board.SPOT, board._version(tab, board.SPOT)))
        return True

    def heal_title(self, tp=None):
        info, cond = xbmc.getInfoLabel, xbmc.getCondVisibility
        title_id = C.prop('dhs.tp.id')
        if not title_id or info('ListItem.Property(dhs.id)') != title_id:
            return False                    # another item's page, or not filled yet
        series = cond('String.IsEqual(ListItem.Property(dhs.media),series)')
        tp = tp or C.layout().tp
        lists = [(tp['seasons'], 'seasons'), (tp['episodes'], 'episodes')] if series else []
        lists += [(tp['cast'], 'cast'), (tp['related'], 'related')]
        for index, (cid, name) in enumerate(lists):
            if cond('Integer.IsGreater(Container(%d).NumItems,0)' % cid):
                continue
            if C.prop('dhs.tp.none.%s' % name) == title_id:
                continue                    # it has nothing to list
            if cond('Container(%d).IsUpdating' % cid):
                return False
            if index:
                before_cid, before = lists[index - 1]
                ready = (cond('Integer.IsGreater(Container(%d).NumItems,0)' % before_cid)
                         or C.prop('dhs.tp.none.%s' % before) == title_id)
                if not ready:
                    return False            # not its turn yet
            if not self._may_heal('%s:%s' % (title_id, name)):
                return False
            C.log('title page %s loaded again (its last load failed)' % name)
            C.set_prop('dhs.tp.rev.%s' % name, '%d' % int(time.time() * 1000))
            return True
        return False

    # v5.10.188: a user's pick asks once (no other request follows it)
    _AT_ONCE = ('row style', 'library source', 'servers source')

    def republish_if_asked(self, settle=1.0):
        """Publish when asked; a request younger than ``settle`` seconds waits
        (rows read one after another can ask several times: one publish)."""
        token = C.prop(C.PROP_REPUBLISH)
        if token and token != self._republish_token:
            try:
                asked = float(token.partition(' ')[0])
            except ValueError:
                asked = 0.0
            reason = token.partition(' ')[2] or 'request'
            if reason not in self._AT_ONCE and self.preview_transition():
                return False
            wait = min(settle, 0.1) if reason in self._AT_ONCE else settle
            if wait and time.time() - asked < wait:
                return False
            self._republish_token = token
            if reason == 'home' and time.time() - self._published_at < 120.0:
                # v5.10.188: the Home opened again (a skin reloaded as Kodi
                # starts): it shows the rows just published (board.publish_saved),
                # working them all out again took 5 s on a box
                C.log('rows just published: the Home shows them as they are', xbmc.LOGDEBUG)
                return False
            self.publish(reason)
            return True
        return False

    def publish(self, reason, computed=None):
        from . import rows
        try:
            if reason == 'library source':
                # v5.10.155: a Library chip: only that tab's rows change
                rows.publish(tabs=('library',), reason=reason)
            elif reason == 'servers source':
                # v5.10.157: a Servers chip, or its servers read again
                rows.publish(tabs=('servers',), reason=reason)
            else:
                # v5.10.188: the tab on screen at once, the others one by one
                # from the loop a moment later (the Home answers meanwhile).
                # A box took 5 s for every tab at start, 4.5 s for a row style.
                first = rows.screen_tab()
                rows.publish(tabs=(first,), reason=reason, computed=computed)
                self._note_slow(rows.LAST)
                rest = [t for t in C.TABS if t != first]
                later = self._later
                if later is None:
                    self._later = {'tabs': rest, 'reason': reason, 'computed': dict(computed or {}),
                                   'at': time.monotonic() + 0.6, 'n': {}, 'ms': 0.0, 'tab_ms': {}}
                else:
                    # tabs still waiting keep their turn (rows that came back
                    # empty ask every two seconds while the Home loads: each
                    # ask put the other tabs off again)
                    later['tabs'] = rest
                    later['computed'] = dict(computed or {})
                    if reason not in later['reason'].split(', '):
                        later['reason'] = ('%s, %s' % (later['reason'], reason))[:80]
            self._signature = self.signature()
            self._last_check = time.time()
            self._published_at = time.time()
        except Exception:
            C.log('publish failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    def publish_later(self):
        """One of the tabs left by the last publish (v5.10.188). True when one was."""
        later = self._later
        if later is None or time.monotonic() < later['at'] or self.busy() or self.preview_transition():
            return False
        from . import rows
        screen = rows.screen_tab()      # a tab the user went to meanwhile goes next
        tab = later['tabs'].pop(later['tabs'].index(screen) if screen in later['tabs'] else 0)
        started = time.monotonic()
        try:
            later['n'][tab] = rows.publish(tabs=(tab,), reason=later['reason'], computed=later['computed'], quiet=True)
            self._note_slow(rows.LAST)
        except Exception:
            C.log('publish of %s failed:\n%s' % (tab, traceback.format_exc()), xbmc.LOGWARNING)
        spent = (time.monotonic() - started) * 1000
        later['ms'] += spent
        later['tab_ms'][tab] = later['tab_ms'].get(tab, 0.0) + spent
        later['at'] = time.monotonic() + 0.05
        if not later['tabs']:
            self._later = None
            self._signature = self.signature()
            self._published_at = time.time()
            C.log('rows of the other tabs published (%s): %d in %.0f ms (%s)' % (
                later['reason'], sum(later['n'].values()), later['ms'],
                ', '.join('%s %.0f' % (t, ms) for t, ms in later['tab_ms'].items())))
        return True

    def _note_slow(self, last):
        for tab, ms in (last.get('tab_ms') or {}).items():
            if ms >= 400 and tab not in self._profiled:
                self._slow_tabs.add(tab)

    def signature(self):
        """What the published rows are (keys, titles, shapes) on every tab."""
        from . import rows
        out = []
        for tab in C.TABS:
            out.append([(s.get('key'), s.get('title'), s.get('sub')) for s in rows.load_specs(tab)])
        return out

    def check(self, quiet=False):
        """Rows changed (a sync, a catalog added): publish again; stale rows: read again.

        v5.10.255 (quiet): with the screensaver on, nobody at the box for a
        quarter of an hour, or a video playing, the stale rows wait: their
        reads (a server's 80-card Continue watching every five minutes, 2 to
        5 s each on an AM8 Pro) ran all night for no one. They are read the
        moment the box is in use again."""
        from . import rows
        computed = {}
        try:
            fresh = []
            for tab in C.TABS:
                if tab in self._slow_tabs:
                    # v5.10.188: a tab whose rows took long: where the time went, once
                    self._slow_tabs.discard(tab)
                    self._profiled.add(tab)
                    computed[tab], why, spent = rows.profile_compute(tab)
                    # slow at start only (the router's import, the first read
                    # of every file): nothing to say when it is quick now
                    C.log('rows of %s worked out in %s' % (tab, why), xbmc.LOGINFO if spent >= 250 else xbmc.LOGDEBUG)
                else:
                    computed[tab] = rows.compute_rows(tab)
                fresh.append([(r.key, r.title, r.subtitle) for r in computed[tab]])
        except Exception:
            fresh = None
        if fresh is not None and fresh != self._signature:
            # v5.10.119: the rows just worked out are published as they are
            # (they were worked out a second time)
            self.publish('changed', computed=computed)
        elif fresh is not None:
            try:
                rows.refresh_collections(computed)
            except Exception:
                C.log('collections:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        if not quiet:
            self.read_ahead(only_stale=True)
        if not quiet and time.time() - self._last_empty >= _EMPTY_EVERY:
            self._last_empty = time.time()
            self.read_empty()
            try:
                rows.prune()
            except Exception:
                pass

    # ------------------------------------------------------------ reading
    def read_ahead(self, only_stale=False):
        """The rows on show, read where they have no cache (or an old one),
        the tab on show first, then their TMDb pass."""
        from . import rows
        if self.preview_transition():
            return
        tab = C.prop('dhs.tab') or 'all'
        if tab not in C.TABS:
            tab = 'all'
        order = [tab] + [t for t in C.TABS if t != tab]
        todo = []
        view = self.visible_rows()
        if self.mode == 'dexhub' and not C.layout().hubs and view is None:
            return
        for name in order:
            # Only the active tab is read ahead; other tabs load on demand.
            # The remaining rows are read when the cursor comes near them.
            depth = (self._load_budget.depth(light=rows.app().settings.flag('homeui_light_mode', True))
                     if name == tab else 0)
            for slot, spec in rows.published(name):
                if ((view and name == view[0] and max(1, view[1] - 1) <= slot < view[1] + view[2])
                        or (view is None and slot <= depth)):
                    todo.append((name, slot, spec))
        # the first rows of every tab before the rest
        todo.sort(key=lambda entry: (entry[1] > 4, order.index(entry[0]), entry[1]))
        for name, slot, spec in todo:
            if self.stopped() or self.preview_transition():
                return
            if self.busy():
                return
            if self.request_priority(name, spec['id'], 'r') is None:
                continue
            data = C.read_row(C.row_file(name, spec['id']))
            fresh_enough = C.fresh_copy(data, rows.ttl_of(spec))
            if fresh_enough or (only_stale and data is None):
                continue
            self.refresh(name, spec['id'], spec, enrich=False)
            self.republish_if_asked()
            if self.rest(0.05):
                return
        for name, slot, spec in todo:
            if self.stopped() or self.busy():
                return
            if self.request_priority(name, spec['id'], 'e') is None:
                continue
            if slot <= 2:
                self.enrich(name, spec['id'], spec)
            else:
                C.request_refresh(name, spec['id'], enrich_only=True)

    def refresh(self, tab, row_id, spec=None, enrich=True):
        from . import rows
        spec = spec or rows.find_spec(tab, row_id)
        if spec is None:
            return
        before = C.read_row(C.row_file(tab, row_id))
        started = time.monotonic()
        data = rows.fetch(tab, row_id, spec, fresh=before is not None)
        self._load_budget.record(time.monotonic() - started)
        if data is None:
            return
        self.changed(tab, row_id, spec, before, data)
        if enrich:
            self.enrich(tab, row_id, spec)

    def changed(self, tab, row_id, spec, before, data, metadata=False):
        served = C.prop('dhs.sv.%s.%s' % (tab, row_id))
        shape_changed = before is not None and before.get('shape') != data.get('shape')
        if (served and served != data.get('h')) or shape_changed:
            key = (tab, row_id)
            if metadata and not getattr(self, '_manual_progress_refresh', False):
                # Six-title enrichment batches used to reload the entire row
                # each time. Keep caching, then publish the completed pass once.
                self._pending_metadata.add(key)
                self._pending_updates[key] = spec
                return
            self._pending_metadata.discard(key)
            # Protect every visible neighbor, not just the focused row. A
            # result already in flight must wait too; holding new jobs alone
            # still allowed its bump to flash the covers during trailer open.
            transition = self.preview_transition()
            if not getattr(self, '_manual_progress_refresh', False) and (
                    self.busy() or transition or self.collection_preview(tab) or xbmc.getGlobalIdleTime() < 1):
                if transition and (tab, row_id) not in self._pending_updates:
                    C.log('row redraw held for trailer startup: %s/%s' % (tab, row_id))
                if shape_changed:
                    self.__dict__.setdefault('_pending_shapes', set()).add((tab, row_id))
                self._pending_updates[(tab, row_id)] = spec
            else:
                self._pending_updates.pop((tab, row_id), None)
                getattr(self, '_pending_shapes', set()).discard((tab, row_id))
                self._bump_row(tab, row_id, spec, data)

    def _bump_row(self, tab, row_id, spec, data):
        """Do not issue the same rebind twice while Kodi still serves it."""
        key = (tab, row_id)
        stamp = (data.get('h'), data.get('shape'))
        if self._redraw_hashes.get(key) == stamp:
            return False
        from . import rows
        rows.bump(tab, row_id, spec)
        self._redraw_hashes[key] = stamp
        return True

    def _metadata_focused(self, tab, row_id):
        view = self.visible_rows()
        if not view or view[0] != tab:
            return False
        slot = view[1]
        cid = 7000 + slot if tab == 'fold' else C.layout().row_list(slot)
        return (C.prop('dhs.%s.%d.id' % (tab, slot)) == row_id
                and xbmc.getCondVisibility('Control.HasFocus(%d)' % cid))

    def collection_preview(self, tab):
        # Keep the collection's current row layouts alive for the entire
        # preview, including its card expansion. Metadata may still cache.
        if tab != 'fold':
            return False
        view = self.visible_rows()
        return bool(view and view[0] == 'fold' and
                    (C.prop('dexhub.trailer.active') or C.prop('dhs.trailer.visible')))

    def flush_updates(self):
        """Coalesce results and apply at most one rebind per settled loop."""
        if not self._pending_updates:
            return
        if self.busy() or self.preview_transition() or xbmc.getGlobalIdleTime() < 1:
            return
        from . import rows
        for (tab, row_id), spec in list(self._pending_updates.items()):
            if self.collection_preview(tab):
                continue
            data = C.read_row(C.row_file(tab, row_id)) or {}
            key = (tab, row_id)
            if key in self._pending_metadata:
                if (not rows.is_enriched(data) or self._metadata_focused(tab, row_id)
                        or C.prop('dexhub.trailer.active') or C.prop('dhs.trailer.visible')):
                    continue
            self._pending_updates.pop(key, None)
            self._pending_metadata.discard(key)
            shapes = getattr(self, '_pending_shapes', set())
            shape_changed = (tab, row_id) in shapes
            shapes.discard((tab, row_id))
            if shape_changed or C.prop('dhs.sv.%s.%s' % (tab, row_id)) not in ('', data.get('h')):
                if self._bump_row(tab, row_id, spec, data):
                    break

    def enrich(self, tab, row_id, spec):
        from . import rows
        try:
            if rows.enrich(tab, row_id, stop=lambda: self.stopped() or self.busy() or self.preview_transition()
                           or self.request_priority(tab, row_id, 'e') is None):
                data = C.read_row(C.row_file(tab, row_id)) or {}
                if C.prop('dhs.sv.%s.%s' % (tab, row_id)) not in ('', data.get('h')):
                    self.changed(tab, row_id, spec, None, data, metadata=True)
        except Exception:
            C.log('TMDb pass failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        pending = C.read_row(C.row_file(tab, row_id)) or {}
        if pending and not rows.is_enriched(pending) and not self.stopped():
            C.request_refresh(tab, row_id, enrich_only=True)
        if (spec or {}).get('kind') == 'collection' and self.wants_gifs():
            try:
                # a Dex Hub trailer does not hold the covers back (it plays
                # behind the spotlight all the time): the user's video does
                if rows.fetch_gifs(tab, row_id, stop=lambda: self.stopped() or self.watching() or self.preview_transition()):
                    data = C.read_row(C.row_file(tab, row_id)) or {}
                    # A newly downloaded focus GIF must still become visible.
                    self.changed(tab, row_id, spec, None, data)
            except Exception:
                C.log('collection covers:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    def focus_gif(self, rows):
        if not self.wants_gifs() or self.watching():
            return
        target = rows.focused_gif_target()
        if not target:
            self._gif_focus = None
            return
        previous = getattr(self, '_gif_focus', None)
        now = time.monotonic()
        if previous is None or previous[0] != target:
            self._gif_focus = (target, now, False)
            return
        if previous[2] or now - previous[1] < 0.35:
            return
        self._gif_focus = (target, previous[1], True)
        C.request_refresh(target[0], target[1], enrich_only=True)

    def wants_gifs(self):
        """skin.dexhub 3 plays a collection card's animated cover (the add-on's
        focus GIF setting), and so does Arctic Fuse 3 with Dex Hub's rows once
        its patch is in (v5.10.114: af3patch shows the cover on the focused
        card of its widgets)."""
        if not C.prop('dhs.opt.gif'):
            return False
        if self.mode == 'af3':
            return True
        return self.mode == 'dexhub' and C.layout().major >= 3

    def watching(self):
        """The user's own video plays (a Dex Hub trailer is not one)."""
        try:
            return self.player.isPlayingVideo() and not C.prop('dexhub.trailer.active')
        except Exception:
            return False

    def read_empty(self):
        """Rows left off the Home because they came back empty: read again
        (only those: a tab's rows past the twenty on show are not read)."""
        from . import rows
        for tab in C.TABS:
            if self.mode == 'dexhub' and tab != (C.prop('dhs.tab') or 'all'):
                continue
            shown = set(spec['id'] for _slot, spec in rows.published(tab))
            for spec in rows.load_specs(tab):
                if self.stopped() or self.busy():
                    return
                if spec['id'] in shown or spec.get('own') or spec.get('kind') == 'collection':
                    continue
                data = C.read_row(C.row_file(tab, spec['id']))
                if data is None or data.get('items'):
                    continue
                rows.fetch(tab, spec['id'], spec, fresh=True)   # titles again: it asks to publish
                if self.rest(0.2):
                    return

    def _check_progress_actions(self, now):
        action_token = C.prop('dhs.progress.action')
        if not action_token or action_token == self._progress_action_token:
            return False
        # Manual edits bypass the player debounce once, even just after a refresh.
        self._progress_action_token = action_token
        self._progress_token = C.prop('dhs.progress.changed')
        self._progress_due = 0.0
        self._progress_read_at = now
        self._manual_progress_refresh = True
        try:
            self.refresh_progress()
        finally:
            self._manual_progress_refresh = False
        return True

    def refresh_progress(self):
        """Continue Watching, Next Up and the other progress rows, after
        playback (also when empty and off the Home: a first play brings them)."""
        from . import rows
        for tab in C.TABS:
            for spec in rows.load_specs(tab):
                if self.stopped():
                    return
                if spec.get('progress') or spec.get('own'):
                    if self.mode == 'dexhub' and self.request_priority(tab, spec['id'], 'r') is None:
                        # Keep the saved cards visible; ask their source only when shown.
                        path = C.row_file(tab, spec['id'])
                        with C.file_lock(path):
                            data = C.read_json(path)
                            if data and data.get('t'):
                                data['t'] = 0
                                C.write_json(path, data)
                        continue
                    self.refresh(tab, spec['id'], spec)

    def watch_playback(self):
        """After a video the user watched (not a trailer, not a start that
        failed at once) the progress rows are read again (v5.10.120: every
        trailer behind the Home or a hub, and every Arctic Fuse 3 click
        Kodi took for a playback, read every progress row of every tab,
        failing servers included)."""
        playing = self.busy()
        now = time.time()
        if playing and not C.prop('dexhub.trailer.active'):
            if not self._real_since:
                self._real_since = now
        elif not playing and self._playing:
            if self._real_since and now - self._real_since >= _REAL_PLAY:
                self._progress_due = now + _PROGRESS_AFTER
            self._real_since = 0.0
        if playing and C.prop('dexhub.trailer.active'):
            self._real_since = 0.0
        self._playing = playing
        self.watch_trailer(playing)

    def watch_trailer(self, playing):
        """The title page's trailer mark (companion.py plays nothing it marks
        as a session): cleared when the trailer ends, when something else
        plays, or when it never started."""
        mark = C.prop('dhs.trailer')
        if not mark:
            return
        try:
            since = float(mark)
        except ValueError:
            since = 0.0
        url = C.prop('dexhub.trailer.url')
        if playing:
            try:
                current = self.player.getPlayingFile() or ''
            except Exception:
                current = ''
            # v5.10.215 (GitHub #8): the same address as Kodi reports it
            # (headers after '|', the query re-encoded). A plain compare took
            # the trailer itself for "real playback took over" a moment after
            # it started, cleared its mark and put refresh switching back
            # mid-trailer: on an Amlogic box the screen re-synced, the sound
            # started over and the picture could stay away.
            if current and url and not _same_trailer(current, url):
                self.clear_trailer()            # real playback took over
            else:
                C.set_prop('dhs.trailer.seen', '1')
            return
        if C.prop('dhs.trailer.seen') or time.time() - since > 15.0:
            self.clear_trailer()                # it ended, or it never started

    @staticmethod
    def clear_trailer():
        for key in ('dhs.trailer', 'dhs.trailer.seen', 'dexhub.trailer.active', 'dexhub.trailer.url'):
            C.set_prop(key, '')
        from .. import refresh_guard
        refresh_guard.restore()


def _same_trailer(current, url):
    """Is Kodi's playing file the trailer at ``url``?"""
    from urllib.parse import unquote
    def plain(text):
        return unquote(str(text or '').split('|', 1)[0]).strip()
    current, url = plain(current), plain(url)
    if not current or not url:
        return False
    if current == url:
        return True
    return current.split('?', 1)[0] == url.split('?', 1)[0]
