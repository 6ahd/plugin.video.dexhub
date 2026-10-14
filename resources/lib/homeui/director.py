# -*- coding: utf-8 -*-
"""Background trailer playback for the Dex Hub Home windows.

One trailer at a time, played windowed so the hero's video layer shows it
behind the artwork gradients. The director owns every side effect of a
trailer so nothing leaks into real playback:

  * ``dexhub.trailer.active`` / ``dexhub.trailer.url`` on the Home window
    tell Dex Hub's playback companion (and any subtitle service that wants
    to check) that this playback is only a preview;
  * the ``dexhub.sub.*`` bridge properties from the last real playback are
    cleared before a trailer starts, so a subtitle service never searches
    for the previous title while a trailer runs;
  * the trailer item carries no ids and the generic ``video`` media type, so
    scrobblers ignore it;
  * previews apply their selected level with the SetVolume builtin
    (Kodi's mute would flash the volume dialog on every trailer) and
    restore the user's level once the last preview has really stopped; a
    marker file holding that level lets the Dex Hub service restore it after
    a crash.

Two Kodi behaviours shape the threading here:

  * ``xbmc.Player().stop()`` waits until Kodi has closed the player, which
    can take seconds on some boxes. The window's callbacks run on a single
    thread, so a stop made there would freeze the remote. Stops requested by
    the UI therefore only flip the state and hand the real stop to a short
    background thread.
  * Kodi opens its modal busy dialog on every playback start, which would
    swallow key presses while a preview buffers; a guard thread cancels that
    dialog as soon as it appears (the preview keeps loading).

Moving from one title to the next parks the running preview (v5.10.103):
it is hidden and paused, not stopped, and the next title's preview takes over
the open player. Closing Kodi's player runs on Kodi's own interface thread
and can hold every screen for a moment on some boxes, so stopping a preview
on each move made browsing stutter; switching the file of an open player
does not. A parked preview that nothing takes over within a few seconds (the
cursor rests on a title with no trailer) is stopped for real, and leaving the
page or starting real playback stops it at once as before.

Kodi delivers player callbacks late and sometimes for the file that was just
replaced, so the director never trusts a single callback: a stop that arrives
while the next trailer is still starting is ignored, and a watchdog tick
(driven by the app) settles any state the callbacks missed. Window callbacks
are always made after the director's lock is released.

Live TV channels (v5.10.103) are previews too, opened through Kodi's PVR by
channel id. Kodi then reports the channel's stream address as the playing
file, never the address the preview was started with, so a channel preview
is recognised by the channel Kodi says is playing, and that stream address is
remembered as the preview's own from then on. A parked channel is silenced,
not paused: a live stream carries on, and coming back to it shows it live.
OK on a previewed channel hands it over as real playback (adopt).

Channels of the other live sources (v5.10.104: Stremio add-ons, the user's
subscription, M3U playlists and Xtream accounts) have a stream address of
their own. They open like a trailer, and live like a PVR channel: parking
silences them, coming back needs no resume, and OK adopts them. Each live
preview carries a key (the tile's path), so a page can tell which channel
the running preview is.
"""
import collections
import json
import os
import threading
import time

import xbmc
import xbmcgui

HOME = 10000
# v5.10.189: set while a preview in a window opens: skin.dexhub 3.27.3 draws
# nothing in Kodi's busy dialog then (Kodi opens it with every playback start
# and Dex Hub closes it at once: its shade and spinner flashed before each
# trailer). Cleared a moment after the preview shows or goes.
OPENING_PROP = 'dhs.preview.opening'
_OPENING_LINGER = 0.8
_SUB_PROPS = ('dexhub.sub.imdb_id', 'dexhub.sub.tmdb_id', 'dexhub.sub.tvdb_id',
              'dexhub.sub.season', 'dexhub.sub.episode', 'dexhub.sub.title',
              'dexhub.sub.mediatype')
_START_TIMEOUT = 12.0
_STOP_WAIT = 6.0


def _rpc(method, params=None):
    try:
        raw = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                                              'params': params or {}}))
        return (json.loads(raw) or {}).get('result')
    except Exception:
        return None


def audio_state():
    """(volume 0-100 or None, muted) of Kodi's own volume control."""
    result = _rpc('Application.GetProperties', {'properties': ['volume', 'muted']}) or {}
    try:
        volume = int(result.get('volume'))
    except Exception:
        volume = None
    return volume, bool(result.get('muted'))


def set_volume(percent):
    # the SetVolume builtin, unlike mute, does not open the volume dialog
    xbmc.executebuiltin('SetVolume(%d)' % max(0, min(100, int(percent))), True)


SILENCED_PROP = 'dexhub.preview.silenced'     # preview owns a temporary Kodi volume level
MARKER = 'muted_by_trailer'


def _marker_path(profile_dir):
    """The level saved before a preview silenced Kodi (the add-on's profile
    folder or its homeui folder: both callers are accepted)."""
    profile_dir = str(profile_dir or '')
    if os.path.basename(profile_dir.rstrip('/\\')) != 'homeui':
        profile_dir = os.path.join(profile_dir, 'homeui')
    return os.path.join(profile_dir, MARKER)


def _volume_marker(marker):
    try:
        with open(marker, 'r') as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {'saved': int(data), 'applied': 0}  # older silent previews
        saved, applied = int(data['saved']), int(data['applied'])
        if 0 <= saved <= 100 and 0 <= applied <= 100:
            return saved, applied
    except Exception:
        pass
    return None


def _restore_volume_marker(marker):
    record = _volume_marker(marker)
    if record is None:
        return False
    saved, applied = record
    volume, _muted = audio_state()
    if volume is None:
        return False
    restored = False
    if volume == applied:
        set_volume(saved)
        volume, _muted = audio_state()
        restored = volume == saved
        if not restored:
            return False  # keep recovery data if Kodi did not apply the change
    # A remote-control volume change wins over the saved level, including zero.
    try:
        os.remove(marker)
    except OSError:
        pass
    return restored


def ensure_audible(profile_dir, why=''):
    """Restore the pre-preview level after silence or a reduced-volume trailer."""
    try:
        if xbmcgui.Window(HOME).getProperty(SILENCED_PROP):
            return False
    except Exception:
        pass
    return _restore_volume_marker(_marker_path(profile_dir))


def restore_mute_marker(profile_dir):
    """Service startup: undo a silenced volume left behind by an interrupted session."""
    try:
        xbmcgui.Window(HOME).clearProperty(SILENCED_PROP)
    except Exception:
        pass
    try:
        ensure_audible(profile_dir, 'start')
    except Exception:
        pass
    try:
        home = xbmcgui.Window(HOME)
        home.clearProperty('dexhub.trailer.active')
        home.clearProperty('dexhub.trailer.url')
    except Exception:
        pass
    try:
        from .. import refresh_guard
        refresh_guard.restore()
    except Exception:
        pass


def _plain(url):
    """An address without the headers Kodi takes after a '|'."""
    return str(url or '').split('|', 1)[0]


def _same_url(current, url):
    if not current or not url:
        return False
    if current == url:
        return True
    # a live stream carries its request headers after '|'; Kodi may report
    # the address with or without them
    current, url = _plain(current), _plain(url)
    base = url.split('?', 1)[0]
    return current == url or current.split('?', 1)[0] == base or base in current


def _same_alias(current, alias):
    """A channel's stream address, compared exactly (without its query)."""
    if not current or not alias:
        return False
    return current == alias or current.split('?', 1)[0] == alias.split('?', 1)[0]


def _playing_channel():
    try:
        from .live import playing_channel
        return playing_channel()
    except Exception:
        return 0


class _Player(xbmc.Player):
    def __init__(self, director):
        super(_Player, self).__init__()
        self._director = director

    def onAVStarted(self):
        self._director._on_av_started()

    def onPlayBackEnded(self):
        self._director._on_finished(True)

    def onPlayBackStopped(self):
        self._director._on_finished(False)

    def onPlayBackError(self):
        self._director._on_finished(False, error=True)


class TrailerDirector(object):
    def __init__(self, app):
        self.app = app
        self._player = _Player(self)
        self._lock = threading.RLock()
        self._token = 0
        self._url = ''
        self._owner = None
        self._state = 'idle'          # idle | starting | playing | parked
        self._started_at = 0.0
        self._fullscreen = False
        self._ours = collections.deque(maxlen=8)   # addresses of recent previews
        # live TV previews: the channel id, and the stream address Kodi
        # reports for it once it plays (recent ones kept like _ours)
        self._channel = 0
        self._alias = ''
        self._aliases = collections.deque(maxlen=8)
        self._unmute_on_show = False
        # a live channel with its own stream address (v5.10.104), and the key
        # of any live preview (the channel tile's path)
        self._live = False
        self._key = ''
        # addresses of recent live previews: two channels may differ only in
        # their query (play.php?id=1, a proxy's ?d=...), so these are
        # compared whole, never by their path alone
        self._live_urls = collections.deque(maxlen=16)
        self._park_id = 0
        self._park_on_start = False   # parked while still opening: pause it once it shows
        self._parked_at = 0.0         # v5.10.186: when the preview was parked
        self._mute_owned = False      # we silenced Kodi for previews and still owe the restore
        self._saved_volume = 0
        self._applied_volume = None
        self._stopper = None
        self._marker = os.path.join(app.profile, 'muted_by_trailer')
        self._opening_on = False
        self._opening_until = 0.0

    # -------------------------------------------------------------- query
    @property
    def active(self):
        return self._state != 'idle'

    @property
    def visible(self):
        return self._state == 'playing'

    def ready_to_reveal(self, settled=False):
        """AV belongs to this preview; its playback clock has begun moving."""
        with self._lock:
            if self._state != 'playing' or not self._is_ours():
                return False
            try:
                if not self._player.isPlayingVideo():
                    return False
                # Live previews can lack a clock. Some input streams also
                # report zero briefly after AVStarted: allow a longer settle.
                return self._live or self._player.getTime() > 0 or settled
            except Exception:
                return bool(settled)

    @property
    def opening(self):
        """A preview on its way to the screen (not one parked while it opened)."""
        return self._state == 'starting' and not self._park_on_start

    def owner(self):
        return self._owner

    def stopping(self):
        stopper = self._stopper
        return stopper is not None and stopper.is_alive()

    def foreign_playback(self):
        """True when something other than one of our trailers is playing.

        With a preview open (or parked) the playing file itself decides: real
        playback started elsewhere replaces the preview in the same player.
        """
        if self.stopping():
            return False
        try:
            player = xbmc.Player()
            if not player.isPlaying():
                return False
            if self._state == 'idle':
                return True
            current = player.getPlayingFile() or ''
        except Exception:
            return False
        return bool(current) and not self._ours_url(current)

    def _matches(self, current, url):
        """Is ``current`` (Kodi's playing file) the preview address ``url``?"""
        if not current or not url:
            return False
        if _plain(url) in self._live_urls:
            return _plain(current) == _plain(url)
        return _same_url(current, url)

    def _ours_url(self, current):
        urls = list(self._ours)
        if self._url:
            urls.append(self._url)
        if any(self._matches(current, url) for url in urls):
            return True
        if any(_same_alias(current, alias) for alias in list(self._aliases)):
            return True
        if self._channel and self._state != 'idle':
            with self._lock:
                return self._is_ours()
        return False


    def preview_key(self):
        """The key of the running (or parked) live preview (its tile's path), or ''."""
        return self._key if self._state != 'idle' else ''

    @property
    def live(self):
        """A live channel is the preview (PVR or a stream of its own)."""
        return self._state != 'idle' and bool(self._channel or self._live)

    def adopt(self):
        """Hand the running preview over as the user's own playback (live TV OK).

        Nothing is stopped or reopened: the director only lets go of it, gives
        the volume back and forgets its addresses, so the pages treat it as
        real playback from now on.
        """
        events = []
        with self._lock:
            self._token += 1
            if self._state == 'idle':
                return False
            self._park_id += 1
            url, alias = self._url, self._alias
            if self._state == 'parked' and not (self._channel or self._live):
                _rpc('Player.PlayPause', {'playerid': 1, 'play': True})
            self._release_locked(events, notify=False, restore_mute=True)
            for store, value in ((self._ours, url), (self._aliases, alias)):
                try:
                    while value and value in store:
                        store.remove(value)
                except Exception:
                    pass
        self._notify(events)
        return True

    # ------------------------------------------------------------ control
    def next_token(self):
        with self._lock:
            self._token += 1
            return self._token

    def is_current(self, token):
        return token == self._token

    def current_token(self):
        with self._lock:
            return self._token

    @staticmethod
    def _notify(events):
        for owner, kind, arg in events:
            try:
                if kind == 'visible':
                    owner.on_trailer_visible()
                else:
                    owner.on_trailer_finished(arg)
            except Exception:
                pass

    def play(self, owner, token, stream, title='', muted=True, fullscreen=False, volume=None):
        """Start ``stream`` ({'url','mime'}) for ``owner`` if ``token`` is current.

        Called from worker threads. A preview that is still being stopped in
        the background is allowed to finish first; a preview that is still
        running is simply replaced by the new one, which Kodi does without a
        separate stop.
        """
        stopper = self._stopper
        if stopper is not None and stopper.is_alive():
            stopper.join(_STOP_WAIT)
        events = []
        started = False
        resumed = False
        with self._lock:
            if token != self._token or not stream or not stream.get('url'):
                return False
            if self.foreign_playback():
                if self._state != 'idle':
                    # real playback replaced our preview: let go of it (and
                    # give the volume back) instead of taking the player over
                    self._release_locked(events, notify=True)
                foreign = True
            else:
                foreign = False
        if foreign:
            self._notify(events)
            return False
        with self._lock:
            if token != self._token:
                return False
            if stream.get('live'):
                # a live channel is known by its tile, whatever its address
                same = bool(stream.get('key')) and self._key == stream.get('key')
            else:
                same = _same_url(self._url, stream.get('url'))
            if self._state == 'parked' and not fullscreen and same and self._is_ours():
                # back on the title whose preview is parked: carry on from
                # where it paused instead of opening it again
                self._park_id += 1          # its stop timer no longer applies
                self._owner = owner
                self._state = 'playing'
                self._preview_audio_locked(muted, volume)
                if owner is not None:
                    events.append((owner, 'visible', None))
                resumed = started = True
            else:
                if self._state != 'idle':
                    # v5.10.186: one preview after another keeps refresh
                    # switching off (it was put back and taken off again: two
                    # writes of Kodi's settings for every title on a box)
                    self._release_locked(events, notify=False, restore_mute=False, keep_refresh=True)
                started = self._start_locked(owner, stream, title, muted, fullscreen, events, volume)
        if resumed:
            if not (stream.get('channelid') or stream.get('live')):
                _rpc('Player.PlayPause', {'playerid': 1, 'play': True})
            self._notify(events)
            return True
        self._notify(events)
        if started and not fullscreen:
            guard = threading.Thread(target=self._busy_guard, args=(token,),
                                     name='DexHub-homeui-busyguard')
            guard.daemon = True
            guard.start()
        if started and fullscreen and not (stream.get('channelid') or stream.get('live')):
            # v5.10.286 (issue #14): the Trailer button's video replaced a
            # windowed preview in the open player, or Kodi's busy dialog
            # refused the switch: it played behind the page. Full screen is
            # asked for until it shows, while this trailer is still ours.
            url = stream.get('url')

            def front():
                from ..trailer_playback import ensure_fullscreen
                ensure_fullscreen(url, alive=lambda: self._state != 'idle' and self._url == url)
            threading.Thread(target=front, name='DexHub-homeui-fullscreen', daemon=True).start()
        return started

    def _busy_guard(self, token):
        """Keep Kodi's busy spinner from blocking the remote while a preview opens.

        Kodi opens its modal busy dialog on every playback start and keeps it
        up until the first frame. For a background trailer that would swallow
        every key press while the stream buffers, so the dialog is cancelled
        as soon as it appears. Cancelling only closes the spinner; the trailer
        keeps loading.
        """
        deadline = time.monotonic() + _START_TIMEOUT
        while time.monotonic() < deadline and not self.app.stop_event.is_set():
            if token != self._token or self._state != 'starting':
                return
            # v5.10.140: the dialog's id, not getCondVisibility: that call
            # pauses Kodi's frame each time and this loop asked it every
            # 30 ms for as long as a channel or trailer took to open (the
            # guide's moves stuttered while a preview started)
            from .. import guistate
            if guistate.top_dialog() == guistate.BUSY[0]:
                xbmc.executebuiltin('Action(Back,busydialog)')
                time.sleep(0.12)
            else:
                time.sleep(0.05)

    def _start_locked(self, owner, stream, title, muted, fullscreen, events, volume=None):
        url = stream.get('url')
        home = xbmcgui.Window(HOME)
        for key in _SUB_PROPS:
            try:
                home.clearProperty(key)
            except Exception:
                pass
        home.setProperty('dexhub.trailer.active', '1')
        home.setProperty('dexhub.trailer.url', url)
        live = bool(stream.get('live'))
        if not (stream.get('channelid') or live):
            self._preview_audio_locked(muted and not fullscreen, None if fullscreen else volume)
        elif muted and not fullscreen:
            self._silence_locked()
        self._fullscreen = bool(fullscreen)
        self._park_on_start = False
        self._key = str(stream.get('key') or '')
        if stream.get('channelid'):
            return self._start_channel_locked(owner, stream, muted, events)
        item = xbmcgui.ListItem(label=title or 'Trailer', path=url)
        # v5.10.218: the trailer's fingerprint (trailer_mark.py)
        if not (stream.get('channelid') or live):
            item.setProperty('dexhub.trailer', '1')
            if xbmc.getCondVisibility('System.Platform.TVOS'):
                item.setProperty('dexhub.playercore', 'VideoPlayer')
        # a live stream of an unknown kind has no type: Kodi looks at it itself
        mime = stream.get('mime') if 'mime' in stream else 'video/mp4'
        try:
            if mime:
                item.setMimeType(mime)
                item.setContentLookup(False)
        except Exception:
            pass
        for name, value in (stream.get('props') or {}).items():
            try:
                item.setProperty(name, str(value))
            except Exception:
                pass
        try:
            tag = item.getVideoInfoTag()
            tag.setTitle(title or 'Trailer')
            tag.setMediaType('video')
        except Exception:
            pass
        if live:
            # a live channel of its own address: heard once it shows (the
            # parked channel it replaces stays silent until then)
            self._live = True
            self._live_urls.append(_plain(url))
            self._unmute_on_show = not muted
            try:
                if stream.get('logo'):
                    item.setArt({'icon': stream['logo'], 'thumb': stream['logo']})
            except Exception:
                pass
        else:
            item.setProperty('dexhub.trailer', '1')
            if xbmc.getCondVisibility('System.Platform.TVOS'):
                item.setProperty('dexhub.playercore', 'VideoPlayer')
        self._owner = owner
        self._url = url
        self._ours.append(url)
        self._state = 'starting'
        self._started_at = time.monotonic()
        if fullscreen:
            try:
                # v5.10.156 (issue #5): no refresh rate switch for a trailer or
                # preview. v5.10.188: full screen only; Kodi (CoreELEC too)
                # switches the rate only for a video opened full screen, and the
                # pair of settings writes (each saves guisettings.xml) came
                # with every preview on the Home
                from .. import refresh_guard
                refresh_guard.suspend()
            except Exception:
                pass
        if not fullscreen:
            self._opening(True)
        try:
            if live:
                from .player_handoff import play_video
                play_video(url, item, player=self._player, windowed=not fullscreen)
            else:
                self._player.play(url, item, windowed=not fullscreen)
        except Exception as exc:
            # the type only: a live address can carry an account
            self.app.log('trailer play failed: %s' % type(exc).__name__)
            self._release_locked(events, notify=True)
            return False
        return True

    def _opening(self, on):
        """The busy dialog's mark (OPENING_PROP): on now, off a moment later."""
        if on:
            self._opening_until = 0.0
            if not self._opening_on:
                self._opening_on = True
                try:
                    xbmcgui.Window(HOME).setProperty(OPENING_PROP, '1')
                except Exception:
                    pass
        elif self._opening_on and not self._opening_until:
            self._opening_until = time.monotonic() + _OPENING_LINGER

    def _opening_tick(self):
        if self._opening_on and self._opening_until and time.monotonic() >= self._opening_until:
            self._opening_on = False
            self._opening_until = 0.0
            try:
                xbmcgui.Window(HOME).clearProperty(OPENING_PROP)
            except Exception:
                pass

    def _start_channel_locked(self, owner, stream, muted, events):
        """A live TV preview: the channel opens through Kodi's PVR, windowed."""
        from . import live
        url = stream.get('url')
        self._channel = int(stream['channelid'])
        self._alias = ''
        # a channel with sound is heard only once it shows: the parked
        # channel it replaces stays silent until then
        self._unmute_on_show = not muted
        self._owner = owner
        self._url = url
        self._ours.append(url)
        self._state = 'starting'
        self._started_at = time.monotonic()
        if live.playing_channel() == self._channel:
            # already on air (a stop that has not happened yet): opening it
            # again would make Kodi switch to its full-screen player
            return True
        live.hold_windowed(self.app.profile)
        if not live.open_channel(self._channel):
            self._release_locked(events, notify=True)
            return False
        return True

    def stop(self, wait=False):
        """Invalidate pending requests and stop the running trailer.

        ``wait=False`` (the UI default) returns at once and stops the player
        on a background thread; ``wait=True`` returns only after Kodi's player
        has stopped, which callers use right before real playback starts.
        """
        events = []
        url = alias = ''
        channel = 0
        with self._lock:
            self._token += 1
            if self._state != 'idle':
                if wait:
                    self._halt_locked(events)
                else:
                    url, alias, channel = self._url, self._alias, self._channel
                    self._release_locked(events, notify=True, restore_mute=False)
        self._notify(events)
        if url:
            self._stop_in_background(url, alias, channel)
        elif wait:
            stopper = self._stopper
            if stopper is not None and stopper.is_alive():
                stopper.join(_STOP_WAIT)

    def _stop_in_background(self, url, alias='', channel=0):
        previous = self._stopper
        thread = threading.Thread(target=self._stop_worker, args=(url, previous, alias, channel),
                                  name='DexHub-homeui-stop')
        thread.daemon = True
        self._stopper = thread
        thread.start()

    def _stop_worker(self, url, previous, alias='', channel=0):
        if previous is not None and previous.is_alive():
            previous.join(_STOP_WAIT)
        try:
            player = xbmc.Player()
            deadline = time.monotonic() + 2.0
            # the play request may still be queued in Kodi; give it a moment
            while not player.isPlaying() and time.monotonic() < deadline:
                if self.app.stop_event.is_set():
                    break
                time.sleep(0.05)
            if player.isPlaying():
                try:
                    current = player.getPlayingFile() or ''
                except Exception:
                    current = ''
                with self._lock:
                    replaced = self._state != 'idle' and self._matches(self._url, url)
                mine = (not current or self._matches(current, url) or _same_alias(current, alias)
                        or (channel and _playing_channel() == channel))
                if mine and not replaced:
                    player.stop()
                    deadline = time.monotonic() + 3.0
                    while player.isPlaying() and time.monotonic() < deadline:
                        time.sleep(0.05)
        except Exception:
            pass
        with self._lock:
            if self._state == 'idle':
                self._restore_mute_locked()

    def _halt_locked(self, events):
        try:
            player = xbmc.Player()
            if self._state == 'starting' and not player.isPlaying():
                # the play request is already queued in Kodi: let it open and
                # stop it, rather than have it start after real playback begins
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and not player.isPlaying():
                    time.sleep(0.05)
            if player.isPlaying():
                try:
                    current = player.getPlayingFile() or ''
                except Exception:
                    current = ''
                # never stop playback that is not this preview
                if not current or self._is_ours():
                    player.stop()
                    deadline = time.monotonic() + 2.5
                    while time.monotonic() < deadline and player.isPlaying():
                        time.sleep(0.04)
        except Exception:
            pass
        self._release_locked(events, notify=True)

    @property
    def silenced(self):
        return self._mute_owned and self._applied_volume == 0

    def _preview_audio_locked(self, muted, volume):
        if muted:
            self._set_preview_volume_locked(0)
        elif volume is None:
            self._restore_mute_locked()
        else:
            self._set_preview_volume_locked(volume)

    def update_preview_audio(self, owner, muted, volume):
        """Apply a changed preference only while this owner still has the preview."""
        with self._lock:
            if (self._owner is owner and self._state == 'playing'
                    and not self.live and not self._fullscreen and self._is_ours()):
                self._preview_audio_locked(muted, volume)

    def _set_preview_volume_locked(self, level):
        try:
            level = max(0, min(100, int(float(level))))
        except (ValueError, TypeError, OverflowError):
            level = 30
        if self._mute_owned and self._applied_volume == level:
            return
        current, _muted = audio_state()
        if current is None:
            return  # never change audio unless its previous level can be saved
        record = _volume_marker(self._marker)
        if not self._mute_owned or current != self._applied_volume:
            self._saved_volume = record[0] if record and current == record[1] else current
        try:
            os.makedirs(os.path.dirname(self._marker), exist_ok=True)
            temp = self._marker + '.tmp'
            with open(temp, 'w') as handle:
                json.dump({'saved': self._saved_volume, 'applied': level}, handle)
            os.replace(temp, self._marker)
        except OSError:
            return
        self._mark_silenced(True)
        self._applied_volume = level
        self._mute_owned = True
        set_volume(level)

    def _silence_locked(self):
        self._set_preview_volume_locked(0)

    @staticmethod
    def _mark_silenced(on):
        try:
            home = xbmcgui.Window(HOME)
            if on:
                home.setProperty(SILENCED_PROP, '1')
            else:
                home.clearProperty(SILENCED_PROP)
        except Exception:
            pass

    # ------------------------------------------------------------ parking
    def park(self, grace):
        """Hide the running preview without closing Kodi's player (see above).

        Called on the interface thread when the cursor moves to another title.
        A preview still opening is parked as soon as it shows. Nothing to park
        (or a full-screen trailer) falls back to an ordinary stop.
        """
        events = []
        with self._lock:
            self._token += 1
            state = self._state
            if state == 'parked' or (state == 'starting' and self._park_on_start):
                pid = self._park_id          # still parked: only wait longer
                work = None
            elif state in ('playing', 'starting') and not self._fullscreen:
                self._park_id += 1
                pid = self._park_id
                if state == 'playing':
                    self._state = 'parked'
                    work = 'mute' if (self._channel or self._live) else 'pause'
                else:
                    self._park_on_start = True
                    work = 'silence'
                if self._owner is not None:
                    events.append((self._owner, 'finished', False))
                self._parked_at = time.monotonic()
            else:
                pid = None
                work = None
        if pid is None:
            self.stop(wait=False)
            return
        self._notify(events)
        if work:
            thread = threading.Thread(target=self._park_worker, args=(pid, work),
                                      name='DexHub-homeui-park')
            thread.daemon = True
            thread.start()
        self.app.scheduler.call_later('director:park', max(1.0, float(grace)),
                                      lambda: self._park_expired(pid))

    def _park_worker(self, pid, work):
        if work == 'mute':
            # a live channel carries on unheard (a live stream is not paused)
            with self._lock:
                if self._state == 'parked' and self._park_id == pid:
                    self._silence_locked()
            return
        if work == 'silence':
            # a preview with sound that is still opening must not be heard
            # for the moment between showing and being paused
            with self._lock:
                if self._park_on_start and self._park_id == pid and self._state == 'starting':
                    self._silence_locked()
            return
        with self._lock:
            if self._state != 'parked' or self._park_id != pid:
                return
            url = self._url
        _rpc('Player.PlayPause', {'playerid': 1, 'play': False})
        with self._lock:
            state = self._state
            still = state == 'parked' and self._park_id == pid
        if still or state not in ('playing', 'starting'):
            return
        # a preview took the player over while this pause was on its way (the
        # same one carrying on, or the next title's): the pause must not stay
        # on it. A switch Kodi has not made yet starts unpaused by itself.
        try:
            current = xbmc.Player().getPlayingFile() or ''
        except Exception:
            current = ''
        if state == 'playing' or (current and not self._matches(current, url)):
            _rpc('Player.PlayPause', {'playerid': 1, 'play': True})

    # v5.10.186: Kodi closes a video on its interface thread (a box stood
    # still 150 ms for it): a parked preview is closed once the keys rest for
    # a moment, never in the middle of a quick run through the titles
    _KEYS_REST = 1
    _CLOSE_LATEST = 12.0

    def _park_expired(self, pid):
        url = alias = ''
        channel = 0
        try:
            busy = xbmc.getGlobalIdleTime() < self._KEYS_REST
        except Exception:
            busy = False
        postpone = False
        with self._lock:
            parked = self._state == 'parked' or (self._state == 'starting' and self._park_on_start)
            if (busy and parked and self._park_id == pid
                    and time.monotonic() - self._parked_at < self._CLOSE_LATEST):
                postpone = True
            elif parked and self._park_id == pid:
                url, alias, channel = self._url, self._alias, self._channel
                self._release_locked([], notify=False, restore_mute=False)
        if postpone:
            self.app.scheduler.call_later('director:park', 0.5, lambda: self._park_expired(pid))
            return
        if url:
            self._stop_in_background(url, alias, channel)

    def _restore_mute_locked(self):
        if not self._mute_owned:
            return
        self._mute_owned = False
        self._applied_volume = None
        self._mark_silenced(False)
        _restore_volume_marker(self._marker)

    def _release_locked(self, events, notify=True, natural=False, restore_mute=True, keep_refresh=False):
        owner = self._owner
        was = self._state
        self._opening(False)
        parked = was == 'parked' or self._park_on_start
        self._state = 'idle'
        self._owner = None
        self._url = ''
        self._channel = 0
        self._alias = ''
        self._live = False
        self._key = ''
        self._unmute_on_show = False
        self._park_on_start = False
        if parked:
            notify = False      # its page was told when it was parked
        if restore_mute:
            self._restore_mute_locked()
        try:
            home = xbmcgui.Window(HOME)
            home.clearProperty('dexhub.trailer.active')
            home.clearProperty('dexhub.trailer.url')
        except Exception:
            pass
        if not keep_refresh:
            try:
                from .. import refresh_guard
                refresh_guard.restore()
            except Exception:
                pass
        if notify and owner is not None and was != 'idle':
            events.append((owner, 'finished', bool(natural)))

    def _forget_url(self, url):
        # a cached trailer address that would not open is looked up afresh
        try:
            self.app.resolver.forget_url(url)
        except Exception:
            pass

    # ---------------------------------------------------------- callbacks
    def _is_ours(self):
        if self._state == 'idle' or not self._url:
            return False
        try:
            current = xbmc.Player().getPlayingFile() or ''
        except Exception:
            current = ''
        if not current:
            return self._state == 'starting'
        if self._matches(current, self._url) or _same_alias(current, self._alias):
            return True
        if self._channel and not self._alias and _playing_channel() == self._channel:
            # the channel's stream address: this preview's own from now on
            self._alias = current
            self._aliases.append(current)
            return True
        return False

    def _shown_locked(self, events):
        self._state = 'playing'
        self._opening(False)
        if self._unmute_on_show:
            self._unmute_on_show = False
            self._restore_mute_locked()
        if self._owner is not None:
            events.append((self._owner, 'visible', None))

    def _on_av_started(self):
        events = []
        park = None
        with self._lock:
            if self._state == 'starting':
                if self._is_ours():
                    if self._park_on_start:
                        park = self._shown_parked_locked()
                    else:
                        self._shown_locked(events)
                else:
                    # Real playback took over the player (started elsewhere).
                    self._release_locked(events, notify=True)
            elif self._state in ('parked', 'playing') and not self._is_ours():
                # the same, over a preview that was showing or parked: give
                # the volume back now rather than at the next watchdog tick
                self._release_locked(events, notify=True)
        self._notify(events)
        self._start_park_worker(park)

    def _shown_parked_locked(self):
        self._park_on_start = False
        self._state = 'parked'
        self._opening(False)
        return self._park_id

    def _start_park_worker(self, pid):
        if pid is None:
            return
        thread = threading.Thread(target=self._park_worker,
                                  args=(pid, 'mute' if (self._channel or self._live) else 'pause'),
                                  name='DexHub-homeui-park')
        thread.daemon = True
        thread.start()

    def _on_finished(self, natural, error=False):
        events = []
        with self._lock:
            if self._state == 'idle':
                return
            elapsed = time.monotonic() - self._started_at
            if self._state == 'starting' and elapsed < 5.0 and not error:
                return          # a late stop for the trailer this one replaced
            if error:
                self._forget_url(self._url)
            if self._state == 'parked':
                try:
                    if xbmc.Player().isPlaying() and self._is_ours():
                        return  # stale callback; the parked preview is still open
                except Exception:
                    pass
                self._release_locked(events, notify=False)
                return
            try:
                if (self._state == 'playing' and xbmc.Player().isPlaying()
                        and self._is_ours()):
                    return      # stale callback; ours is still running
            except Exception:
                pass
            self._release_locked(events, notify=True, natural=natural)
        self._notify(events)

    def tick(self):
        """Watchdog, called by the app a few times a second."""
        events = []
        park = None
        with self._lock:
            self._opening_tick()
            if self._state == 'idle':
                return
            try:
                player = xbmc.Player()
                playing = bool(player.isPlaying())
            except Exception:
                player, playing = None, False
            elapsed = time.monotonic() - self._started_at
            if self._state == 'parked':
                if not playing or not self._is_ours():
                    # stopped, or real playback replaced it: give the volume back
                    self._release_locked(events, notify=False)
            elif self._state == 'starting':
                if not playing and elapsed > _START_TIMEOUT:
                    self._forget_url(self._url)
                    self._release_locked(events, notify=True)
                elif self._channel and elapsed > _START_TIMEOUT and not self._is_ours():
                    # the channel never came up; what still plays is an
                    # earlier preview nobody shows any more
                    try:
                        current = player.getPlayingFile() or ''
                    except Exception:
                        current = ''
                    stale = current if self._ours_url(current) else ''
                    self._release_locked(events, notify=True)
                    if stale:
                        self._stop_in_background(stale, stale)
                elif playing and elapsed > 1.0 and self._is_ours():
                    try:
                        if player.getTime() > 0.3:
                            if self._park_on_start:
                                park = self._shown_parked_locked()
                            else:
                                self._shown_locked(events)
                    except Exception:
                        pass
            elif not playing:
                self._release_locked(events, notify=True, natural=True)
            elif not self._is_ours():
                self._release_locked(events, notify=True)
        self._notify(events)
        self._start_park_worker(park)
