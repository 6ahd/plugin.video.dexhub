# -*- coding: utf-8 -*-
"""Back goes one step back in the Menu (v5.10.358).

The Menu (control centre) and the settings cards are dialogs that close
before what they open shows: a page (ActivateWindow ... return) or another
run of the add-on. Back from that page landed on the Home. Here the run that
opened it waits until it closed and the Home is on top again with nothing
over it, then opens the step it came from (the settings cards, or the Menu).

It gives up when a video starts, Kodi stops, another window stays on top
past the time limit, or the user opens the Menu again in between.
"""
import time

import xbmc
import xbmcgui

CLEAR = 'Window.IsActive(home) + !System.HasActiveModalDialog + !System.HasVisibleModalDialog'
TOKEN = 'dexhub.menu.return'


def _clear():
    return xbmc.getCondVisibility(CLEAR)


def back_to(url, started=4.0, limit=1800.0):
    """Wait here for what was just opened to close, then RunPlugin(url)."""
    home = xbmcgui.Window(10000)
    token = '%.3f' % time.time()
    home.setProperty(TOKEN, token)
    monitor = xbmc.Monitor()
    player = xbmc.Player()
    begin = time.time()
    # 1. what was opened shows (a page, a dialog); an action that only shows
    #    a note never leaves the Home: the step comes back after a moment
    left = False
    while time.time() - begin < started:
        if monitor.waitForAbort(0.15):
            return False
        if not _clear():
            left = True
            break
    # 2. it closes: the Home on top again with no dialog over it (twice,
    #    as one dialog often opens the next)
    while left:
        if monitor.waitForAbort(0.25) or time.time() - begin > limit:
            return False
        if home.getProperty(TOKEN) != token:
            return False                # another step took over
        try:
            if player.isPlayingVideo():
                return False
        except Exception:
            pass
        if _clear():
            if monitor.waitForAbort(0.4):
                return False
            if _clear():
                break
    if home.getProperty(TOKEN) != token:
        return False
    home.clearProperty(TOKEN)
    xbmc.executebuiltin('RunPlugin(%s)' % url)
    return True


def forget():
    """A step opened by hand: an older wait must not open anything."""
    xbmcgui.Window(10000).clearProperty(TOKEN)
