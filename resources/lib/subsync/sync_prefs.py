# -*- coding: utf-8 -*-
"""One switch for automatic subtitle sync, whatever the subtitle (v5.10.491).

On: a subtitle put on screen is synced to the video on its own: a Silo
subtitle by the Silo server (to the audio), any other by the local sync
(Dex Hub's, or DexSubtitles' when it runs it). Off: neither does, and the
sync button still syncs on demand. The player's subtitle settings page and
the Silo button menu show it; DexSubtitles' own copy of the setting follows,
so the two add-ons never disagree.
"""
import xbmc
import xbmcaddon

OTHER = 'service.subtitles.dexworld'
KEYS = ('autosync_enabled', 'autosync_any')


def auto_on():
    """On only when both keys are (v5.10.494: the sync worker needs
    autosync_any too, so a switch reading only the first showed "On" while
    other subtitles were not synced; set_auto writes both)."""
    try:
        addon = xbmcaddon.Addon('plugin.video.dexhub')
        values = [addon.getSetting(key) or 'true' for key in KEYS]
    except Exception:
        return True
    return all(value.lower() not in ('false', '0', 'no', 'off') for value in values)


def set_auto(on):
    value = 'true' if on else 'false'
    addons = [xbmcaddon.Addon('plugin.video.dexhub')]
    try:
        if xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % OTHER):
            addons.append(xbmcaddon.Addon(OTHER))
    except Exception:
        pass
    for addon in addons:
        for key in KEYS:
            try:
                addon.setSetting(key, value)
            except Exception:
                pass
    try:
        from ..settings_cache import invalidate
        invalidate()
    except Exception:
        pass
    xbmc.log('[DexHub] subtitle auto sync %s (all subtitles)' % ('on' if on else 'off'), xbmc.LOGINFO)
