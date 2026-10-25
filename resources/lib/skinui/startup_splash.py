# -*- coding: utf-8 -*-
"""v5.10.424: the Home comes whole, not row by row (skin.dexhub 3.28.139).

As Arctic Fuse 3 waits for its widgets on a splash before it shows its Home:
when Kodi starts, the skin covers the Home (Custom_1198_DexStartup.xml, shown
while the Home property dhs.splash is set) and this service thread watches
the rows load underneath: the spotlight and the first three rows of the tab.
Once they have their items and have stood still a moment (their artwork too),
or after 15 seconds whatever happens, it takes the cover away and the Home
is there, all of it at once.

The rows below the first four come one after another (each waits for the
one above; skin 3.28.139 loads rows 1-4 together), and an older skin stages
them all, so between two rows there can be a moment where nothing loads;
a skin condition alone would see it and lift the cover too soon. Here a row
whose path is published counts as loaded only once it has items, or after it
has stayed empty and idle for EMPTY_FOR seconds (a row that is empty).

Only at Kodi's start (not after a skin reload or coming back to the Home);
off with homeui_startup_splash. The skin raises the cover itself in
Startup.xml, the first window Kodi opens, since the Home can show before this
service runs (and lifts it after 20 s if the service never comes); the
setting reaches it as the skin setting DexHub.NoStartupSplash.
"""
import time

import xbmc
import xbmcgui

PROP, SEEN, SKIN_OFF = 'dhs.splash', 'dhs.splash.seen', 'DexHub.NoStartupSplash'
SPOT, FIRST_ROW, ROWS = 4000, 5001, 3
LIMIT, EMPTY_FOR, SETTLE, TICK = 15.0, 2.0, 0.6, 0.25


def _cond(expr):
    try:
        return bool(xbmc.getCondVisibility(expr))
    except Exception:
        return False


def _items(cid):
    try:
        return int(xbmc.getInfoLabel('Container(%d).NumItems' % cid) or 0)
    except ValueError:
        return 0


def _loaded(cid, empty_since, now):
    """The list has its items, or has stayed idle and empty long enough."""
    if _cond('Container(%d).IsUpdating' % cid):
        empty_since.pop(cid, None)
        return False
    if _items(cid) > 0:
        return True
    first = empty_since.setdefault(cid, now)
    return now - first >= EMPTY_FOR


def follow_setting(on):
    """The skin raises the cover before any Python runs: it reads this."""
    xbmc.executebuiltin('Skin.%s(%s)' % ('Reset' if on else 'SetBool', SKIN_OFF))


def watch(monitor):
    """Kodi's start: keep the Home covered until its first rows are in."""
    home = xbmcgui.Window(10000)
    try:
        if xbmc.getSkinDir() not in ('skin.dexhub', 'skin.dexhub.tvos'):
            return
        from ..ui_preferences import value
        on = value('homeui_startup_splash') != 'false'
        if on == _cond('Skin.HasSetting(%s)' % SKIN_OFF):
            follow_setting(on)
        if not on:
            home.clearProperty(PROP)
            return
    except Exception:
        return
    if not home.getProperty(SEEN):
        # this service came before the skin's Startup window (or the skin is older)
        home.setProperty(SEEN, '1')
        home.setProperty(PROP, '1')
    if not home.getProperty(PROP):
        return                              # the Home is already uncovered
    started = time.time()
    empty_since = {}
    settled_at = None
    why = 'timed out'
    try:
        while time.time() - started < LIMIT:
            if monitor.waitForAbort(TICK):
                return
            if not home.getProperty(PROP):
                why = 'taken away'          # the skin's own (its onunload)
                break
            if not _cond('Window.IsVisible(Home)') or not home.getProperty('dhs.ready'):
                settled_at = None
                continue
            now = time.time()
            tab = home.getProperty('dhs.tab') or 'all'
            waiting = []
            if _cond('Control.IsVisible(%d)' % SPOT) and not _loaded(SPOT, empty_since, now):
                waiting.append(SPOT)
            for n in range(ROWS):
                if home.getProperty('dhs.%s.%d.path' % (tab, n + 1)) and not _loaded(FIRST_ROW + n, empty_since, now):
                    waiting.append(FIRST_ROW + n)
            if waiting:
                settled_at = None
                continue
            settled_at = settled_at or now
            if now - settled_at >= SETTLE:       # a moment for the artwork
                why = 'rows in'
                break
    finally:
        home.clearProperty(PROP)
        xbmc.log('[DexHub] startup splash: %s after %.1f s' % (why, time.time() - started), xbmc.LOGINFO)
