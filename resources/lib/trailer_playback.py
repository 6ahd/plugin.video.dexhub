"""An explicit Trailer button owns a fullscreen video, not a background preview."""
import time
import xbmc
import xbmcgui
from . import profile_scope


def play_fullscreen(url, item):
    profile_scope.require_current()
    player = xbmc.Player()
    player.play(url, item, windowed=False)
    # v5.10.286 (issue #14): the old loop gave up after 6 s and at the first
    # look at another file. A preview or a trailer left behind the GUI (Back)
    # is still the playing file for a moment after play(), so the trailer
    # then opened behind the GUI (sound, no picture); a slow start did the same.
    return ensure_fullscreen(url, player=player, alive=profile_scope.is_current)


def ensure_fullscreen(url, timeout=20.0, player=None, alive=None):
    """v5.10.286 (issue #14): an explicit trailer ends up full screen once it
    plays. Kodi refuses the switch while a modal dialog is up (its busy
    spinner, a title page still closing), so it is asked again until the
    full-screen player shows; never for a different file (a late start must
    not foreground another movie), and never again once it showed (Back
    stays the user's)."""
    player = player or xbmc.Player()
    monitor = xbmc.Monitor()
    deadline = time.monotonic() + float(timeout)
    asked = 0.0
    seen = False
    while time.monotonic() < deadline:
        if monitor.abortRequested() or (alive is not None and not alive()):
            return False
        try:
            current = player.getPlayingFile() if player.isPlayingVideo() else ''
        except RuntimeError:
            current = ''  # Player callbacks can precede AV startup.
        if current and same_url(current, url):
            seen = True
            if xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)'):
                return True
            now = time.monotonic()
            if now - asked >= 0.5:
                asked = now
                xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
        elif seen and current:
            return False  # something else took the player (a reopen passes)
        if monitor.waitForAbort(0.1):
            return False
    return False


def foreground_if_playing(url):
    """v5.10.286 (issue #14): this trailer already plays behind the GUI (Back
    from it): brought to the front instead of being opened again."""
    player = xbmc.Player()
    try:
        if not (player.isPlayingVideo() and same_url(player.getPlayingFile(), url)):
            return False
    except RuntimeError:
        return False
    if xbmc.getCondVisibility('Player.Paused'):
        xbmc.executebuiltin('PlayerControl(Play)')
    return ensure_fullscreen(url, timeout=4.0, player=player, alive=profile_scope.is_current)


def same_url(left, right):
    from urllib.parse import urlsplit, parse_qsl, unquote
    def key(value):
        parsed = urlsplit(str(value or '').split('|', 1)[0].strip())
        return (parsed.scheme.lower(), parsed.netloc.lower(), unquote(parsed.path),
                sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return bool(left and right) and key(left) == key(right)


def request_takeover():
    """Let the preview service finish its stop jobs before manual playback starts."""
    profile_scope.require_current()
    home = xbmcgui.Window(10000)
    token = str(time.monotonic_ns())
    home.setProperty('dhs.trailer.takeover', token)
    home.setProperty('dhs.trailer.takeover_at', str(time.time()))
    monitor = xbmc.Monitor()
    # The acknowledgement also joins a delayed stop of the same trailer URL.
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if not profile_scope.is_current() or monitor.waitForAbort(0.05):
            return False
        if home.getProperty('dhs.trailer.takeover_ready') == token:
            return True
        if not home.getProperty('dexhub.trailer.active') and not xbmc.Player().isPlaying():
            # No preview service is required when no video is running. Give
            # the service one tick to acknowledge any queued stop first.
            if time.monotonic() + 4 < deadline:
                continue
            return True
    # v5.10.286 (issue #14): no acknowledgement and nothing plays: the marks
    # are left from an interrupted preview; cleared, the trailer goes ahead
    try:
        if profile_scope.is_current() and not xbmc.Player().isPlaying():
            for key in ('dexhub.trailer.active', 'dexhub.trailer.url', 'dhs.preview.opening'):
                home.clearProperty(key)
            return True
    except Exception:
        pass
    return False


def finish_takeover():
    home = xbmcgui.Window(10000)
    for key in ('takeover', 'takeover_at', 'takeover_ready'):
        home.clearProperty('dhs.trailer.' + key)
