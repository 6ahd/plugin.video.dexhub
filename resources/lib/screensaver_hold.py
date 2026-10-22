# -*- coding: utf-8 -*-
"""v5.10.377: no screensaver over a source search.

Played from another skin's widget (Arctic Fuse 3 through TMDb Helper, a
RunPlugin), Kodi does not hold its own wait over the search as it does for a
playable item, so its screensaver clock kept running: with a short screensaver
time the screensaver (Dex Hub's, when chosen in Kodi) came up over the search,
and the keys pressed to end it reached the search window.

While the search window is open Kodi's screensaver is held
(InhibitScreensaver), which also restarts its clock: after the search the full
screensaver time counts again. A Home property records the hold; the service
lets go of one left behind by a search that never ended (STALE).
"""
import time

import xbmc
import xbmcgui

PROP = 'dexhub.ss_hold'
STALE = 15 * 60


def _home():
    return xbmcgui.Window(10000)


def hold():
    try:
        xbmc.executebuiltin('InhibitScreensaver(true)')
        _home().setProperty(PROP, '%.0f' % time.time())
    except Exception:
        pass


def release():
    try:
        if _home().getProperty(PROP):
            _home().clearProperty(PROP)
            xbmc.executebuiltin('InhibitScreensaver(false)')
    except Exception:
        pass


def tidy():
    """The service: a hold older than STALE is let go."""
    try:
        since = float(_home().getProperty(PROP) or 0)
    except ValueError:
        since = 1.0
    if since and time.time() - since > STALE:
        xbmc.log('[DexHub] screensaver hold left by a search let go', xbmc.LOGINFO)
        release()
