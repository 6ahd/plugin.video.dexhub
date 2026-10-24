"""Optional skin-owned loading artwork; no modal Python windows or player calls.

The service receives Player.OnAVStart (first frame available) and only releases
artwork for the selected source. The plugin publishes properties and returns.
"""
import hashlib
import json
import time
import uuid
import xbmc
import xbmcgui

PREFIX = 'dhs.loading.'
TTL = 60.0
FADE = 0.45                 # skin fades in 350ms; retain images until it finishes


def _home():
    return xbmcgui.Window(10000)


def _state():
    try:
        value = json.loads(_home().getProperty(PREFIX + 'state') or '{}')
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _identity(path):
    # Header pipes are transport options, not part of Kodi's playing filename.
    return hashlib.sha256(str(path or '').partition('|')[0].encode()).hexdigest()


def _playing_source(player):
    try:
        path = player.getPlayingFile()
        return _identity(path) if path else ''
    except Exception:
        return ''


def cancel(owner):
    """Old failures/timers must never clear a newly selected work's artwork."""
    if not owner or _state().get('owner') != owner:
        return
    home = _home()
    home.clearProperty(PREFIX + 'owner')
    for key in ('phase', 'fanart', 'logo', 'title', 'state'):
        home.clearProperty(PREFIX + key)


def prepare(ctx, art, stream_url, switching=False):
    from . import ui_preferences, profile_scope
    import xbmcvfs
    current = _state()
    # A replacement stream belongs to the open player. Never cover the video
    # with the initial-start artwork, even if Kodi has already stopped the
    # old decoder or the switch marker was consumed by resume handling.
    if switching or ctx.get('source_switch_pending') or xbmc.Player().isPlayingVideo():
        cancel(current.get('owner'))
        return ''
    # v5.10.405: shown when the source is chosen, then asked again at the
    # hand-off: the same source keeps its artwork (no flash, same timeout)
    if (current.get('owner') and current.get('fading') is None and
            current.get('source') == _identity(stream_url) and
            _home().getProperty(PREFIX + 'owner') == current.get('owner')):
        return current['owner']
    cancel(current.get('owner'))
    if not ui_preferences.enabled('player_loading_art'):
        return ''
    if xbmc.getSkinDir() not in ('skin.dexhub', 'skin.dexhub.tvos'):
        return ''
    home = _home()
    if not home.getProperty(PREFIX + 'service') or not xbmcvfs.exists(
            'special://skin/xml/Custom_1161_PlaybackLoading.xml'):
        return ''
    art = art if isinstance(art, dict) else {}
    fanart = str(art.get('fanart') or art.get('landscape') or '')
    logo = str(art.get('clearlogo') or art.get('tvshow.clearlogo') or art.get('logo') or '')
    # No new metadata/network request on the playback handoff.
    if not fanart:
        return ''
    owner = uuid.uuid4().hex
    state = {'owner':owner, 'source':_identity(stream_url),
             'previous':_playing_source(xbmc.Player()), 'created':time.monotonic(),
             'scope':profile_scope.key(), 'engaged':False}
    home.setProperty(PREFIX + 'fanart', fanart)
    home.setProperty(PREFIX + 'logo', logo)
    home.setProperty(PREFIX + 'title', str(ctx.get('show_title') or ctx.get('title') or ''))
    home.setProperty(PREFIX + 'state', json.dumps(state))
    home.setProperty(PREFIX + 'phase', 'loading')
    home.setProperty(PREFIX + 'owner', owner)  # publish last; no empty-image flash
    return owner


class Presentation:
    def __init__(self):
        import threading
        self.lock = threading.RLock()
        self.player = xbmc.Player()
        self.service = uuid.uuid4().hex
        presentation = self
        class Events(xbmc.Monitor):
            def onNotification(self, sender, method, data):
                try:
                    presentation.event(method)
                except Exception:
                    pass  # cosmetic overlay can never break playback
        self.events = Events()
        # Clean a previous stopped service's transient overlay, never artwork.
        cancel(_state().get('owner'))
        _home().setProperty(PREFIX + 'service', self.service)

    def _matches(self, state):
        return _playing_source(self.player) == state.get('source')

    def _new_source(self, state):
        current = _playing_source(self.player)
        # Redirects/input streams may expose a different playing filename.
        # A first-frame event for any replacement must release the artwork;
        # an event from the still-closing trailer must leave it in place.
        return bool(current and (current == state.get('source') or
                                 current != state.get('previous')))

    def event(self, method):
        if method not in ('Player.OnAVStart','Player.OnPlay','Player.OnStop','Player.OnError'):
            return
        with self.lock:
            state = _state()
            if not state.get('owner') or state.get('fading') is not None:
                return
            matches = self._matches(state)
            if method == 'Player.OnAVStart':
                if self._new_source(state) and self.player.isPlayingVideo():
                    state['fading'] = time.monotonic()
                    if _state().get('owner') == state['owner']:
                        _home().setProperty(PREFIX + 'state', json.dumps(state))
                        _home().setProperty(PREFIX + 'phase', 'ready')
                # Ignore a late AV event from the trailer/previous source.
            elif method == 'Player.OnPlay':
                if matches:
                    state['engaged'] = True
                    if _state().get('owner') == state['owner']:
                        _home().setProperty(PREFIX + 'state', json.dumps(state))
            elif matches or (not self.player.isPlayingVideo() and
                             (state.get('engaged') or time.monotonic()-state['created'] > .75)):
                cancel(state['owner'])

    def tick(self):
        with self.lock:
            state = _state()
            if not state.get('owner'):
                return
            from . import profile_scope, ui_preferences
            now = time.monotonic()
            if (now-state['created'] >= TTL or now < state['created'] or
                    profile_scope.key() != state.get('scope') or
                    not ui_preferences.enabled('player_loading_art') or
                    xbmc.getSkinDir() not in ('skin.dexhub', 'skin.dexhub.tvos') or
                    (state.get('fading') is not None and now-state['fading'] >= FADE)):
                cancel(state['owner'])

    def close(self):
        with self.lock:
            if _home().getProperty(PREFIX + 'service') == self.service:
                cancel(_state().get('owner'))
                _home().clearProperty(PREFIX + 'service')
