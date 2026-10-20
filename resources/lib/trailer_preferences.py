"""Trailer preferences shared by native settings, phone setup and playback."""
import math


def volume_level(value):
    try:
        number = float(value)
        if not math.isfinite(number):
            return 30
        return max(0, min(100, int(number)))
    except (TypeError, ValueError, OverflowError):
        return 30


MODES = ('background', 'card')


def display_mode(value=None):
    """v5.10.326: background (the whole screen) or card (inside the focused
    Spotlight card of a row, skin.dexhub 3.28.70); anything else is background."""
    value = str(value or '').strip().casefold()
    if value in ('card', 'inside card', 'inside the card', 'داخل البطاقة', 'داخل البوستر'):
        return 'card'
    return 'background'


# v5.10.334: the skin's trailer thread published these five properties four
# times a second, each time building a Window and three Addon objects (every
# one a wait on Kodi's graphics lock, so a pause in the frame while the remote
# moves the cursor). A value is written now when it changes (and again after
# half a minute, in case something else cleared it), the settings are read
# again after two seconds, and one Window serves every call.
_WRITTEN = {}
_HOME = []
_READ = {'t': -1e9}


def _home():
    import xbmcgui
    if not _HOME:
        _HOME.append(xbmcgui.Window(10000))
    return _HOME[0]


def _put(key, value):
    import time
    now = time.monotonic()
    last = _WRITTEN.get(key)
    if last is not None and last[0] == value and now - last[1] < 30.0:
        return
    _WRITTEN[key] = (value, now)
    _home().setProperty(key, value)


def _settings():
    import time
    now = time.monotonic()
    if now - _READ['t'] > 2.0:
        import xbmcaddon
        addon = xbmcaddon.Addon('plugin.video.dexhub')
        _READ.update(t=now, mode=addon.getSetting('homeui_trailer_mode'),
                     on=addon.getSetting('homeui_trailers'), lang=addon.getSetting('ui_language').lower())
    return _READ


def invalidate():
    """Read the settings again at the next publish (a setting changed)."""
    _READ['t'] = -1e9


def publish_presentation(mode=None):
    """Publish layout without starting/importing the video service or resolver."""
    from . import profile_scope
    if not profile_scope.is_current():
        return
    if mode is None:
        _READ['t'] = -1e9               # a direct call (a setting changed) reads them again
    found = _settings()
    mode = display_mode(found['mode'])
    _put('dhs.trailer.mode', mode)
    _put('dhs.trailer.enabled', '0' if found['on'] == 'false' else '1')
    _put('dhs.trailer.config.scope', profile_scope.key())
    return mode


def publish_status(state):
    from . import profile_scope
    if not profile_scope.is_current():
        return
    labels = {
        'disabled': ('التريلرات متوقفة — شغّلها من القائمة ← الرئيسية', 'Trailers are off — enable them in Menu > Home'),
        'waiting': ('توقف على عمل لبدء التريلر', 'Select a title to start its trailer'),
        'loading': ('جار تحميل التريلر…', 'Loading trailer…'),
        'unavailable': ('لا يتوفر تريلر قابل للتشغيل لهذا العمل', 'No playable trailer is available for this title'),
        'error': ('تعذر بدء خدمة التريلر — أعد تشغيل Kodi', 'Trailer service could not start — restart Kodi'),
        'playing': ('', ''),
    }
    lang = _settings()['lang']
    _put('dhs.trailer.status', state)
    _put('dhs.trailer.notice', labels.get(state, labels['waiting'])[1 if lang == 'english' else 0])


def write_setting(key, value):
    """Do not claim success if Kodi rejects a value against its setting schema."""
    import xbmcaddon
    from . import profile_scope
    profile_scope.require_current()
    value = str(value)
    addon = xbmcaddon.Addon('plugin.video.dexhub')
    if addon.setSetting(key, value) is False:
        raise RuntimeError('Kodi rejected the trailer setting; update the add-on and restart Kodi')
    # A fresh handle verifies the value other invocations will read.
    saved = xbmcaddon.Addon('plugin.video.dexhub').getSetting(key)
    if saved != value:
        raise RuntimeError('Trailer setting was not saved; update the add-on and restart Kodi')
    if key in ('homeui_trailer_mode', 'homeui_trailers'):
        publish_presentation()
    if key == 'homeui_trailer_mode':
        # the poster rows change shape: the Home's rows are published again
        try:
            from .homeui.layout import _skin_follows
            _skin_follows()
        except Exception:
            pass
    return saved
