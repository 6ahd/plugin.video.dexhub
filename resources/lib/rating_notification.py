# -*- coding: utf-8 -*-
"""Use Kodi's own notification on skins without the Dex Hub rating overlay.

No window is opened, no focus is taken and no skin file is patched. Kodi
owns the notification's drawing and fonts, including across skin changes.
"""
import threading
_LOCK = threading.Lock()
_RUNNING = set()
PREFIX = 'dhs.cap.'
NATIVE = ('skin.dexhub', 'skin.dexhub.tvos')


def _notify(home):
    import xbmcgui
    from .i18n import is_english
    from .ui_preferences import value
    certificate = home.getProperty(PREFIX + 'card.cert')
    words = home.getProperty(PREFIX + 'card.words')
    info = home.getProperty(PREFIX + 'card.info')
    if not (certificate or words or info):
        return
    heading = ('Content rating' if is_english() else 'التصنيف الرقابي') if certificate or words else ('Playback info' if is_english() else 'معلومات التشغيل')
    if certificate:
        heading += ' · ' + certificate
    message = '  ·  '.join(part for part in (words, info) if part)
    try:
        seconds = max(1, min(15, float(value('player_capsule_time') or 6)))
    except (ValueError, TypeError):
        seconds = 6
    xbmcgui.Dialog().notification(heading, message or certificate,
                                 time=int(seconds * 1000), sound=False)


def _watch(token, valid):
    import xbmc, xbmcgui
    monitor = xbmc.Monitor()
    home = xbmcgui.Window(10000)
    start_shown = False
    last_osd = False
    try:
        while valid() and not monitor.abortRequested() and xbmc.Player().isPlayingVideo():
            if xbmc.getSkinDir() in NATIVE:
                return
            ready = bool(home.getProperty(PREFIX + 'ready'))
            start = ready and bool(home.getProperty(PREFIX + 'on'))
            osd = (ready and bool(home.getProperty(PREFIX + 'osd')) and
                   xbmc.getCondVisibility('Window.IsVisible(videoosd)'))
            if ((start and not start_shown) or (osd and not last_osd)) and valid():
                _notify(home)
            start_shown = start_shown or start
            last_osd = osd
            if monitor.waitForAbort(0.2):
                return
    except Exception as exc:
        xbmc.log('[DexHub] rating notification: %s' % exc, xbmc.LOGWARNING)
    finally:
        with _LOCK:
            _RUNNING.discard(token)


def ensure(token, valid):
    with _LOCK:
        if token in _RUNNING:
            return
        _RUNNING.add(token)
    try:
        threading.Thread(target=_watch, args=(token, valid),
                         name='DexHubRatingNotification', daemon=True).start()
    except RuntimeError:
        with _LOCK:
            _RUNNING.discard(token)
