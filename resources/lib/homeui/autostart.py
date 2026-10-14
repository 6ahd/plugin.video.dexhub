# -*- coding: utf-8 -*-
"""Open the Dex Hub Home when Kodi starts (setting homeui_autostart, v5.10.98).

The service starts this once per Kodi session. It waits until Kodi has
settled on its own Home screen (no startup window, profile login, dialog,
screensaver or playback) and has stayed there for a moment, so skins that
run their own startup sequence (Arctic Fuse's widgets, a startup window)
finish first, then opens the Home exactly like its Settings entry does.

A Home window property marks the session: when Kodi restarts the service
(an add-on update does that), the Home is not opened again in the middle of
whatever the user is doing.
"""
import time

import xbmc
import xbmcgui

HOME_WINDOW = 10000
PROP_SESSION = 'dexhub.homeui.autostart_session'
_READY = ('Window.IsActive(home) + !System.HasModalDialog + !Window.IsActive(busydialog) + '
          '!Window.IsActive(busydialognocancel) + !Player.HasMedia + !System.ScreenSaverActive + '
          '!Window.IsActive(startup) + !Window.IsActive(loginscreen)')


def _log(msg):
    xbmc.log('[DexHub] homeui: %s' % msg, xbmc.LOGINFO)


def first_run_this_session():
    """True the first time the service asks in this Kodi session."""
    try:
        home = xbmcgui.Window(HOME_WINDOW)
        if home.getProperty(PROP_SESSION):
            return False
        home.setProperty(PROP_SESSION, '%.0f' % time.time())
    except Exception:
        return False
    return True


def launch_when_ready(timeout=120.0, settle=2.0):
    monitor = xbmc.Monitor()
    started = time.monotonic()
    steady_since = None
    while not monitor.abortRequested() and time.monotonic() - started < timeout:
        if monitor.waitForAbort(0.4):
            return
        try:
            ready = xbmc.getCondVisibility(_READY)
        except Exception:
            ready = False
        if not ready:
            steady_since = None
            continue
        if steady_since is None:
            steady_since = time.monotonic()
            continue
        if time.monotonic() - steady_since < settle:
            continue
        try:
            alive = float(xbmcgui.Window(HOME_WINDOW).getProperty('dexhub.homeui.alive') or 0)
        except Exception:
            alive = 0.0
        if time.time() - alive < 5.0:
            return      # already open
        if xbmc.getSkinDir() == 'skin.dexhub':
            return      # v5.10.109: the skin's Home is the Dex Hub Home
        _log('opening Dex Hub Home at Kodi startup')
        xbmc.executebuiltin('RunPlugin(plugin://plugin.video.dexhub/?action=home_ui)')
        return
    _log('Home autostart skipped: Kodi did not settle on its Home screen')
