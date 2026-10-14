# -*- coding: utf-8 -*-
"""Trailers behind the Dex Hub hubs of skin.dexhub 2 (Arctic Fuse 3 inside).

The add-on's own Home plays the trailer of the title in its hero once the
cursor has rested on it for a few seconds. On the skin's Dex Hub hubs the
service does the same for the title the hub shows: the spotlight's pick while
the spotlight leads, the focused title of a row while a row has the focus.
homeui's TrailerDirector plays it (windowed, silenced or not, parked between
titles so the player stays open) and the skin draws it inside the artwork's
frame while dexhub.trailer.active is set; the spotlight stops moving on while
a trailer plays and carries on once it ends.

It follows the add-on's Home settings: homeui_trailers, homeui_trailer_delay,
homeui_trailer_sound and homeui_trailer_quality.
"""
import os
import threading
import time
from .. import lazy_tb as traceback

import xbmc

from .. import guistate
from . import common as C

_POLL = 0.25
# v5.10.239: no new trailer after ten minutes without a key press (a Home
# left on all night played 29 trailers an hour, reloaded the lists after
# each and kept the screensaver away); the next key press brings them back
_IDLE_STOP = 600
_SETTINGS_EVERY = 2.0
_SPOTLIGHT = 301
_FOLD_WINDOW = 1190     # a collection folder's page of rows (folderpage.py)
_FOLD_BASE = 7000       # its row n is list 7000 + n


# v5.10.140: what stops a trailer, asked of Kodi in one call per look (each
# getCondVisibility is a pause in Kodi's frame, see guistate.py; this look
# runs every 0.25 s and asked eight of them)
_COVERED = ('System.ScreenSaverActive | Window.IsVisible(DialogVideoInfo.xml) | '
            'Window.IsVisible(DialogContextMenu.xml) | Window.IsVisible(DialogSelect.xml) | '
            'Window.IsVisible(1170) | Window.IsVisible(1181) | Window.IsVisible(fullscreenvideo)')
_COVERED_AF3 = ('System.ScreenSaverActive | System.HasActiveModalDialog | '
                'Window.IsVisible(DialogVideoInfo.xml) | Window.IsVisible(fullscreenvideo)')
_COVER_DIALOGS = (12003, 10106, 12000)    # title page, context menu, select
_COVER_CACHE = {}
_PLAYER = []


def _covered(expr):
    """Something stops the trailer: the dialogs that do, by id at once; the
    screensaver and the skin's own dialogs by the condition, asked at most
    once a second."""
    if guistate.top_dialog() in _COVER_DIALOGS or guistate.active() == guistate.FULLSCREEN_VIDEO:
        return True
    now = time.monotonic()
    seen = _COVER_CACHE.get(expr)
    if seen is None or now - seen[0] >= 1.0:
        seen = _COVER_CACHE[expr] = (now, bool(xbmc.getCondVisibility(expr)))
    return seen[1]


def _video_plays():
    """Player.HasVideo without the GUI lock."""
    if not _PLAYER:
        _PLAYER.append(xbmc.Player())
    try:
        return _PLAYER[0].isPlayingVideo()
    except Exception:
        return False


def _focused(container):
    """Control.HasFocus(container) without the GUI lock."""
    try:
        return int(xbmc.getInfoLabel('System.CurrentControlId') or 0) == container
    except ValueError:
        return False


def _busy_now():
    """Kodi's busy spinner is up now (the dialog id asked afresh)."""
    try:
        import xbmcgui
        return (xbmcgui.getCurrentWindowDialogId() & 0xffff) in guistate.BUSY
    except Exception:
        return False


def _has_items(container):
    try:
        return int(xbmc.getInfoLabel('Container(%d).NumItems' % container) or 0) > 0
    except ValueError:
        return False


class _Host(object):
    """What the director needs of an app: a profile folder, a stop event, a
    log, a timer thread and the trailer resolver."""

    def __init__(self, profile):
        from ..homeui.app import Scheduler
        self.profile = profile
        self.stop_event = threading.Event()
        self.scheduler = Scheduler()
        self.resolver = None

    @staticmethod
    def log(msg, level=None):
        C.log('trailer: %s' % msg, xbmc.LOGDEBUG)


class Spotlight(threading.Thread):
    def __init__(self, monitor):
        super(Spotlight, self).__init__(name='dexhub-skin-trailers')
        self.daemon = True
        self.monitor = monitor
        self.halt = threading.Event()
        self.director = None
        self.resolver = None
        self.host = None
        self._settings = None
        self._settings_at = 0.0
        self.key = None             # the title on show (its dhs.id)
        self.since = 0.0            # when it came on show
        self.tried = None           # the title whose trailer was asked for last
        self.visible_since = 0.0
        self.immersive = False
        self.why = ''
        self.last = None            # the title target() found last
        self._resolve_thread = None
        self._prefetched = None     # v5.10.185: the title page read ahead last
        self._prefetch_thread = None
        self._revealed = False      # v5.10.186: the artwork has made way for the picture
        self._playing_title = ''

    # ------------------------------------------------------------- setup
    def _setup(self):
        import xbmcaddon
        import xbmcvfs
        from ..homeui.director import TrailerDirector, restore_mute_marker
        from ..homeui.trailers import TrailerResolver
        addon = xbmcaddon.Addon(C.ADDON_ID)
        profile = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo('profile')), 'homeui')
        os.makedirs(profile, exist_ok=True)
        try:
            restore_mute_marker(profile)
        except Exception:
            pass
        self.host = _Host(profile)
        settings = self.settings()
        self.resolver = TrailerResolver(profile, quality=settings['quality'], log=self.host.log)
        self.host.resolver = self.resolver
        self.director = TrailerDirector(self.host)

    def settings(self):
        now = time.monotonic()
        from .. import profile_scope
        profile = profile_scope.key()
        if (self._settings is None or now - self._settings_at > _SETTINGS_EVERY
                or profile != getattr(self, '_settings_profile', None)):
            try:
                import xbmcaddon
                addon = xbmcaddon.Addon(C.ADDON_ID)

                def flag(key, default):
                    value = (addon.getSetting(key) or '').strip().lower()
                    return default if value == '' else value == 'true'

                try:
                    delay = float(addon.getSetting('homeui_trailer_delay') or 3)
                except ValueError:
                    delay = 3.0
                quality = (addon.getSetting('homeui_trailer_quality') or '720p').lower().rstrip('p')
                from ..trailer_preferences import volume_level
                self._settings = {
                    'layout': addon.getSetting('homeui_layout_mode'),
                    'volume': volume_level(addon.getSetting('homeui_trailer_volume')),
                    'mode': 'background',
                    'immersive': flag('homeui_immersive', True),
                    'on': flag('homeui_trailers', True),
                    'delay': max(0.6, min(20.0, delay)),
                    'sound': flag('homeui_trailer_sound', True),
                    'quality': int(quality) if quality.isdigit() else 720,
                }
            except Exception:
                self._settings = {'on': True, 'delay': 3.0, 'sound': True, 'quality': 720, 'immersive': True, 'volume': 30, 'mode': 'background'}
            self._settings_at = now
            self._settings_profile = profile
        return self._settings

    # -------------------------------------------------------------- loop
    def run(self):
        from ..trailer_preferences import publish_presentation, publish_status
        publish_presentation()
        while not self.halt.is_set() and not self.monitor.abortRequested():
            try:
                self._setup()
                break
            except Exception:
                publish_status('error')
                C.log('trailers setup failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
                if self.host is not None:
                    self.host.scheduler.stop()
                if self.monitor.waitForAbort(10.0):
                    return
        else:
            return
        while not self.halt.is_set() and not self.monitor.abortRequested():
            try:
                self.tick()
            except Exception:
                publish_status('error')
                C.log('trailer tick failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
                if self.monitor.waitForAbort(2.0):
                    break
            if self.monitor.waitForAbort(C.poll_wait(_POLL)):
                break
        self.stop_trailer(wait=True)
        try:
            self.resolver.flush()
        except Exception:
            pass

    def stop(self):
        self.halt.set()
        if self.director is not None:
            self.director.next_token()
        if self.host is not None:
            self.host.stop_event.set()
            try:
                self.host.scheduler.stop()
            except Exception:
                pass

    def ours(self):
        return self.director is not None and self.director.active and self.director.owner() is self

    def tick(self):
        pending = C.prop('dhs.trailer.takeover')
        if pending:
            try:
                stale = time.time() - float(C.prop('dhs.trailer.takeover_at') or 0) > 15
            except ValueError:
                stale = True
            if stale:
                from ..trailer_playback import finish_takeover
                finish_takeover()
            else:
                if C.prop('dhs.trailer.takeover_ready') != pending:
                    self.director.next_token()
                    self.director.stop(wait=True)
                    self.stop_trailer()
                    self.key, self.tried = None, None
                    C.set_prop('dhs.trailer.takeover_ready', pending)
                return
        # A Trailer button now owns playback. No preview may adopt/stop it.
        if C.prop('dhs.trailer'):
            return
        self.director.tick()
        settings = self.settings()
        from ..trailer_preferences import publish_presentation, publish_status
        publish_presentation(settings.get('mode', 'background'))
        self.director.update_preview_audio(self, not settings['sound'], settings['volume'])
        target = self.target()
        if target is not None and xbmc.getGlobalIdleTime() >= _IDLE_STOP:
            self.why = 'idle'
            target = None
        if target is not None and not settings['on']:
            # trailers off: the title the cursor rests on is still read ahead
            now = time.monotonic()
            if target['key'] != getattr(self, '_pf_key', None):
                self._pf_key, self._pf_since = target['key'], now
            self._prefetch_title(target, now, self._pf_since)
            target = None
        if target is None:
            publish_status('disabled' if not settings['on'] else 'unavailable' if self.why.startswith('item ') else 'waiting')
            if self.key is not None:
                self.director.next_token()  # cancel a lookup whose page was left
            if self.ours():
                # v5.10.187: a trailer stopped before it showed says why (INFO)
                C.log('trailer: no target (%s)' % self.why,
                      xbmc.LOGINFO if self.director.opening else xbmc.LOGDEBUG)
                self.stop_trailer()
            else:
                if self.key is not None:
                    C.log('trailer: title gone (%s)' % self.why, xbmc.LOGDEBUG)
                # v5.10.187: real playback took the player from a card trailer
                # (Play pressed on it): the card flags must not outlive it, or
                # the film showed inside that card back on the Home
                self._drop_flags()
            self.key, self.tried = None, None
            return
        now = time.monotonic()
        if target['key'] != self.key:
            publish_status('waiting')
            # the cursor moved to another title: the preview waits, hidden,
            # for the next one to take the open player over
            self.director.next_token()
            self.key, self.since, self.tried = target['key'], now, None
            self.immersive = False
            C.set_prop('dhs.immersive', '')
            if self.ours():
                self.visible_since = 0.0
                self._revealed = False
                C.set_prop('dhs.trailer.visible', '')
                self.director.park(max(3.0, settings['delay'] + 3.0))
            return
        self.update_immersive(settings)
        self._prefetch_title(target, now, self.since)
        if self.tried == target['key']:
            return              # asked for already (it plays, ended, or has none)
        if now - self.since < settings['delay']:
            return
        if self.director.foreign_playback():
            return
        self.begin(target, settings)

    def target(self):
        """The title the hub on show presents now, or None (no hub, a dialog,
        a real video, the screensaver...)."""
        self.why = 'skin'
        target, self.last = self.last, None      # the title before this look
        layout = C.layout()
        if not layout.trailers or not C.skin_active():
            return None
        self.why = 'no hub'
        # a collection folder's page of rows (skin.dexhub 3.2, folderpage.py)
        fold = layout.major >= 3 and guistate.is_active(_FOLD_WINDOW)
        tab = None if fold else layout.tab_on_show()
        if tab is None and not fold:
            return None
        self.why = 'dialog'

        info = xbmc.getInfoLabel
        if guistate.busy_dialog():
            # Kodi's busy dialog opens with every playback start (the
            # preview's own too) and hides the hub's lists from info labels
            # meanwhile: the title stays what it was
            self.why = 'busy'
            self.last = target
            return target
        if _covered(_COVERED):
            return None
        self.why = 'video'
        if _video_plays() and not self.ours():
            return None         # real playback (or a paused film behind the hub)
        self.why = 'container'

        if fold:
            try:
                container = _FOLD_BASE + int(C.prop('dhs.ffr') or 1)
            except ValueError:
                container = _FOLD_BASE + 1
            if not _focused(container):
                return None
        elif layout.hubs:
            _name, window_id = layout.tab_window(tab)
            try:
                container = int(info('Window(%d).Property(TMDbHelper.WidgetContainer)' % window_id) or 0)
            except ValueError:
                container = 0
        else:
            # skin.dexhub 3: dhs.fr is 0 while the spotlight leads (the nav or
            # the hero's buttons have the focus), else the row with the focus
            fr = C.prop('dhs.fr') or '0'
            try:
                container = layout.spotlight if fr == '0' else layout.row_list(int(fr))
            except ValueError:
                container = layout.spotlight
        if fold:
            pass
        elif container == layout.spotlight:
            if not _has_items(container):
                return None
        elif not (layout.row_list(1) <= container <= layout.row_list(C.ROWS)
                  and _focused(container)):
            # v5.10.187: Kodi's busy spinner opens with a trailer and takes
            # the focus before the cached dialog id knows it: a box stopped
            # three trailers 0.2 s after they began (two blinks, then it played)
            if target is not None and self.ours() and (self.director.opening or _busy_now()):
                self.why = 'focus away while it opens'
                self.last = target
                return target
            return None
        self.why = 'item %d' % container
        prefix = 'Container(%d).ListItem.' % container
        video = info(prefix + 'Property(dhs.hero_video)')
        if info(prefix + 'Property(kind)') == 'folder' and video:
            if not C.prop('dhs.opt.kenburns'):
                return None
            self.last = {'key': 'banner:' + C.short_id(video + info(prefix + 'Label')),
                         'video': video, 'imdb': '', 'title': info(prefix + 'Label')}
            return self.last
        title_id = info(prefix + 'Property(dhs.id)')
        imdb = info(prefix + 'UniqueID(imdb)')
        if not title_id or not imdb.startswith('tt'):
            return None
        self.last = {'key': title_id, 'imdb': imdb, 'title': info(prefix + 'Label'),
                     'q': info(prefix + 'Property(dhs.q)')}
        return self.last

    PREFETCH_DWELL = 1.2

    def _prefetch_title(self, target, now, since):
        """v5.10.185: the title page of the card the cursor rests on is read
        ahead (its seasons, cast, related titles and TMDb text), one title at
        a time, so OK opens a complete page at once (it read TMDb on open:
        0.7 s on a Mac, several seconds on a box)."""
        q = target.get('q') or ''
        if not q or self._prefetched == target['key'] or now - since < self.PREFETCH_DWELL:
            return
        if self._prefetch_thread is not None and self._prefetch_thread.is_alive():
            return
        self._prefetched = target['key']

        def job():
            try:
                from urllib.parse import parse_qsl
                from .title import HeadlessTitle
                started = time.monotonic()
                HeadlessTitle(dict(parse_qsl(q))).load()
                spent = (time.monotonic() - started) * 1000
                if spent > 50:
                    C.log('title page of "%s" read ahead in %.0f ms' % (target.get('title') or '', spent))
            except Exception as exc:
                C.log('title read ahead failed: %s' % exc, xbmc.LOGDEBUG)
        self._prefetch_thread = threading.Thread(target=job, name='dexhub-skin-title-ahead', daemon=True)
        self._prefetch_thread.start()

    def begin(self, target, settings):
        if self._resolve_thread is not None and self._resolve_thread.is_alive():
            return  # one network resolver at a time while browsing quickly
        C.log('trailer for "%s" (%s)' % (target['title'], target['imdb']), xbmc.LOGDEBUG)
        self.tried = target['key']
        from ..trailer_preferences import publish_status
        publish_status('loading')
        token = self.director.next_token()
        director = self.director

        def job():
            # the test harness's own video (a local file only)
            test = C.prop('dhs.test.trailer')
            if test and not os.path.isfile(test):
                test = ''
            try:
                stream = ({'url': test, 'mime': 'video/mp4', 'title': 'Trailer'} if test
                          else {'url': target['video'], 'mime': 'video/mp4'} if target.get('video')
                          else self._resolver_for(settings).resolve(target['imdb']))
            except Exception:
                stream = None
            if (not stream or self.halt.is_set() or self.monitor.abortRequested()
                    or not director.is_current(token) or self.key != target['key']):
                C.log('trailer for "%s" dropped (stream %s, current %s, key %s)' % (
                    target['title'], bool(stream), director.is_current(token), self.key == target['key']),
                    xbmc.LOGDEBUG)
                if director.is_current(token) and self.key == target['key'] and not self.halt.is_set():
                    publish_status('unavailable')
                return
            self._playing_title = target['title']
            ok = director.play(self, token, stream, title=target['title'], muted=not settings['sound'], volume=settings['volume'])
            if not ok and director.is_current(token):
                publish_status('unavailable')
            C.log('trailer for "%s" started: %s' % (target['title'], ok), xbmc.LOGDEBUG)
        self._resolve_thread = threading.Thread(target=job, name='dexhub-skin-trailer', daemon=True)
        self._resolve_thread.start()

    def _resolver_for(self, settings):
        return self.resolver

    def update_immersive(self, settings):
        """After a few seconds of a trailer with no key pressed the trailer
        fills the screen and the rows step aside (the add-on's immersive
        view); a key brings them back."""
        showing = bool(self.ours() and self.director.visible and self.visible_since)
        idle = xbmc.getGlobalIdleTime()
        compact = settings.get('layout') == 'two_rows' and guistate.is_active(10000)
        want = bool(not compact and settings.get('immersive') and showing
                    and time.monotonic() - self.visible_since > 16.0 and idle >= 16)
        if want != self.immersive:
            self.immersive = want
            C.set_prop('dhs.immersive', '1' if want else '')
        # v5.10.176: first the big stage (the hero's text and the rows slide
        # down, the trailer takes most of the screen), as Apple TV does
        stage = bool(not compact and showing and not want and time.monotonic() - self.visible_since > 1.5 and idle >= 3)
        if stage != getattr(self, '_stage', False):
            self._stage = stage
            C.set_prop('dhs.stage', '1' if stage else '')

    def _place_line(self, settings):
        """Where the trailer shows, for the log (one line per trailer)."""
        fr = C.prop('dhs.fr') or '0'
        return 'stage (the hero)' if fr == '0' else 'stage (row %s)' % fr

    def _drop_flags(self):
        if self._revealed or getattr(self, '_stage', False) or self.immersive:
            self._revealed = False
            self.visible_since = 0.0
            C.set_prop('dhs.trailer.visible', '')
            self.immersive = False
            C.set_prop('dhs.immersive', '')
            self._stage = False
            C.set_prop('dhs.stage', '')

    def stop_trailer(self, wait=False):
        if self.ours():
            C.log('trailer stopped (the hub left the title)', xbmc.LOGDEBUG)
            self.director.stop(wait=wait)
        self._revealed = False
        self.visible_since = 0.0
        C.set_prop('dhs.trailer.visible', '')
        self.immersive = False
        C.set_prop('dhs.immersive', '')
        self._stage = False
        C.set_prop('dhs.stage', '')

    # --------------------------------------------------- director owner
    def on_trailer_visible(self):
        if self.ours():
            self.visible_since = time.monotonic()
            self._revealed = False
            # AVStarted can precede the first rendered video frame.
            # Keep the artwork covering it for one short renderer settle.
            key, stamp, token = self.key, self.visible_since, self.director.current_token()
            title = getattr(self, '_playing_title', '') or ''
            def reveal():
                if (self.key != key or self.visible_since != stamp or not self.ours()
                        or not self.director.is_current(token) or not self.director.visible):
                    return
                if not self.director.ready_to_reveal(settled=time.monotonic() - stamp >= 1.5):
                    self.host.scheduler.call_later('skin:trailer-reveal', 0.1, reveal)
                    return
                self._revealed = True
                settings = self.settings()
                C.set_prop('dhs.trailer.visible', '1')
                from ..trailer_preferences import publish_status
                publish_status('playing')
                # v5.10.186: one line per trailer, so a log shows where it went
                try:
                    C.log('trailer of "%s" on screen %.1f s after it started: %s' % (
                        title, time.monotonic() - stamp, self._place_line(settings)))
                except Exception:
                    pass
            self.host.scheduler.call_later('skin:trailer-reveal', 0.25, reveal)

    def on_trailer_finished(self, natural):
        C.log('trailer finished (natural %s)' % natural, xbmc.LOGDEBUG)
        self.visible_since = 0.0
        self._revealed = False
        self.host.scheduler.cancel('skin:trailer-reveal')
        C.set_prop('dhs.trailer.visible', '')
        self.immersive = False
        C.set_prop('dhs.immersive', '')
        self._stage = False
        C.set_prop('dhs.stage', '')


# Arctic Fuse 3's hub windows: (name in conditions, Python window id)
_AF3_HUBS = (('Home', 10000), ('1101', 11101), ('1102', 11102), ('1103', 11103), ('1104', 11104),
             # Dex Hub's own sections (af3hubs.py, v5.10.120)
             ('1982', 11982), ('1983', 11983), ('1984', 11984))


class AF3Spotlight(Spotlight):
    """Trailers behind Dex Hub's titles in the regular Arctic Fuse 3, once
    its trailer patch is in (af3patch.py, v5.10.112): the spotlight's Dex
    Hub pick, or the focused Dex Hub title of a row, on the Home or a hub.
    The skin draws it in its artwork's video frame; there is no immersive
    view there (the skin's rows stay as they are).

    v5.10.119: with Dex Hub's pages in (af3pages.py) a collection folder's
    page plays the focused title's trailer behind its rows, as skin.dexhub's
    does, immersive view included. ``hubs``: the trailer patch is in;
    ``fold``: the pages are in (af3.Service keeps both up to date)."""

    def __init__(self, monitor, hubs=True, fold=False):
        super(AF3Spotlight, self).__init__(monitor)
        self.name = 'dexhub-af3-trailers'
        self.hubs = hubs
        self.fold = fold
        self.fold_window = 0

    def on_fold(self):
        if not self.fold:
            return False
        if not self.fold_window:
            from . import af3pages
            self.fold_window = af3pages.fold_window()
        return guistate.is_active(self.fold_window)

    def settings(self):
        values = dict(super(AF3Spotlight, self).settings())
        values['immersive'] = bool(values.get('immersive')) and self.on_fold()
        return values

    def _place_line(self, settings):
        return "the skin's artwork frame"

    def target(self):
        self.why = 'skin'
        target, self.last = self.last, None
        if C.served() != 'af3':
            return None
        info = xbmc.getInfoLabel
        if guistate.busy_dialog():
            self.why = 'busy'
            self.last = target
            return target
        self.why = 'dialog'
        if guistate.dialog_up() or _covered(_COVERED_AF3):
            return None
        self.why = 'video'
        if _video_plays() and not self.ours():
            return None
        if self.on_fold():
            # a collection folder's page of rows: the focused row's title
            try:
                container = _FOLD_BASE + int(C.prop('dhs.ffr') or 1)
            except ValueError:
                container = _FOLD_BASE + 1
            self.why = 'folder page'
            if not _focused(container):
                return None
            return self._item(container)
        self.why = 'no hub'
        if not self.hubs:
            return None
        name = ''
        active = guistate.active()
        for hub, wid in _AF3_HUBS:
            if active == guistate.window_id(wid):
                name = hub
                break
        if not name:
            return None
        self.why = 'container'
        try:
            container = int(info('Window(%s).Property(TMDbHelper.WidgetContainer)' % name) or 0)
        except ValueError:
            container = 0
        if container == _SPOTLIGHT:
            if not _has_items(_SPOTLIGHT):
                return None
        elif container <= 0 or not _focused(container):
            return None
        return self._item(container)

    def _item(self, container):
        info = xbmc.getInfoLabel
        prefix = 'Container(%d).ListItem.' % container
        video = info(prefix + 'Property(dhs.hero_video)')
        if info(prefix + 'Property(kind)') == 'folder' and video:
            if not C.prop('dhs.opt.kenburns'):
                return None
            self.last = {'key': 'banner:' + C.short_id(video + info(prefix + 'Label')),
                         'video': video, 'imdb': '', 'title': info(prefix + 'Label')}
            return self.last
        title_id = info(prefix + 'Property(dhs.id)')
        if not title_id:
            return None             # not a Dex Hub title: the skin's own
        imdb = info(prefix + 'UniqueID(imdb)')
        if not imdb.startswith('tt'):
            return None
        self.last = {'key': title_id, 'imdb': imdb, 'title': info(prefix + 'Label')}
        return self.last
