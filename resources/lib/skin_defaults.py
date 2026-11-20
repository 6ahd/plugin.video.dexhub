# -*- coding: utf-8 -*-
"""The look a fresh install starts with (v5.10.560).

The user, 12 Nov 2026: a new install's Home, title page and IPTV look like
theirs. Dex Hub's own choices are its settings' defaults; the skin's are
Skin strings, which a skin cannot default, so they are written here once,
on a fresh install only (temporary_skin.initialize_defaults marks it), as
soon as skin.dexhub is the skin in use. Every choice already made is kept.
"""
import xbmc

STRINGS = (('dh.titletabs', 'on'),        # the title page's sections as tabs
           ('dh.pagetone', 'artwork'),    # the title page takes its artwork's colour
           ('dh.titlelayout', 'cinematic'),   # v5.10.595: the cinematic title page
           ('dh.posterlogo', 'on'),       # the title's logo on its poster
           ('dh.glow', 'off'),
           ('dh.bar.sysinfo', 'on'))      # the clock and the device facts on the bar
SKIN = 'skin.dexhub'
PENDING = 'skin_defaults_pending'
# v5.10.595: the new look for everyone, once (the user, 20 Nov 2026: "default
# for all"): the cinematic title page, and the title page, the collection
# page and the Home in the colour of the title on show. Only the default
# choices move (navy, classic: an empty Skin string); a colour or a layout
# picked in the Control Centre stays. Going back there afterwards sticks.
ONCE = (('dh.pagetone', 'artwork'), ('dh.titlelayout', 'cinematic'))
ONCE_MARK = 'look_v595'


def pending():
    try:
        import xbmcaddon     # a fresh read: another add-on call sets it
        return xbmcaddon.Addon('plugin.video.dexhub').getSetting(PENDING) == 'true'
    except Exception:
        return False


def apply_now():
    """Write the defaults when skin.dexhub is in use; True when done."""
    if xbmc.getSkinDir() != SKIN:
        return False
    for name, value in STRINGS:
        if not xbmc.getInfoLabel('Skin.String(%s)' % name):
            xbmc.executebuiltin('Skin.SetString(%s,%s)' % (name, value))
    try:
        import xbmcaddon
        xbmcaddon.Addon('plugin.video.dexhub').setSetting(PENDING, 'false')
    except Exception:
        pass
    xbmc.log('[DexHub] fresh install: the skin starts with the default look', xbmc.LOGINFO)
    return True


def once_done():
    try:
        import xbmcaddon
        return xbmcaddon.Addon('plugin.video.dexhub').getSetting(ONCE_MARK) == 'true'
    except Exception:
        return True


def apply_once():
    """The new look on a device that had the old defaults; True when done."""
    if xbmc.getSkinDir() != SKIN:
        return False
    moved = []
    for name, value in ONCE:
        if not xbmc.getInfoLabel('Skin.String(%s)' % name):
            xbmc.executebuiltin('Skin.SetString(%s,%s)' % (name, value))
            moved.append(name)
    try:
        import xbmcaddon
        xbmcaddon.Addon('plugin.video.dexhub').setSetting(ONCE_MARK, 'true')
    except Exception:
        pass
    xbmc.log('[DexHub] the new look (5.10.595): %s' % (', '.join(moved) or 'already chosen, kept'),
             xbmc.LOGINFO)
    return True


def needed():
    return pending() or not once_done()


def watch(monitor, minutes=30):
    """From the service, the first half hour after Kodi starts: a fresh
    install is marked when Dex Hub first opens (after the service started),
    then switches to skin.dexhub; the defaults are written once both hold."""
    end = 60 * minutes
    waited = 0
    while waited < end and not monitor.abortRequested():
        if pending():
            apply_now()
        if not pending() and not once_done():
            apply_once()
        if not needed():
            return
        if monitor.waitForAbort(5):
            return
        waited += 5
