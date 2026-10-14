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


def display_mode(value=None):
    # Embedded trailers have been retired. Old backups/profiles safely use background.
    return 'background'


def publish_presentation(mode=None):
    """Publish layout without starting/importing the video service or resolver."""
    import xbmcaddon
    import xbmcgui
    from . import profile_scope
    if not profile_scope.is_current():
        return
    addon = xbmcaddon.Addon('plugin.video.dexhub')
    home = xbmcgui.Window(10000)
    mode = 'background'
    if addon.getSetting('homeui_trailer_mode') not in ('', 'background'):
        addon.setSetting('homeui_trailer_mode', 'background')
    home.setProperty('dhs.trailer.mode', mode)
    home.setProperty('dhs.trailer.enabled', '0' if addon.getSetting('homeui_trailers') == 'false' else '1')
    home.setProperty('dhs.trailer.config.scope', profile_scope.key())
    return mode


def publish_status(state):
    import xbmcaddon
    import xbmcgui
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
    lang = xbmcaddon.Addon('plugin.video.dexhub').getSetting('ui_language').lower()
    home = xbmcgui.Window(10000)
    home.setProperty('dhs.trailer.status', state)
    home.setProperty('dhs.trailer.notice', labels.get(state, labels['waiting'])[1 if lang == 'english' else 0])


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
    return saved
