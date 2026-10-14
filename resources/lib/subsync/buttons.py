# -*- coding: utf-8 -*-
"""The player's subtitle buttons (v5.10.290, skin.dexhub 3.28.41).

  ?action=subtitle_sync   the best synced subtitle: the playing one synced, or
                          the subtitle that matches the video best
  ?action=subtitle_next   the next subtitle stream, then Off
  ?action=subtitle_pick   a list: the player's subtitles and the search results

subtitle_sync and subtitle_pick go to DexSubtitles when it is installed and
enabled with its player script (osd.py, DexSubtitles 5.7.6 and later): its
own server search and the same engine. Otherwise Dex Hub runs the same flow
itself (osdflow.py, the owner's code vendored from DexSubtitles 5.7.6), with
the candidates of Dex Hub's subtitle broker. subtitle_next is always Dex
Hub's own (the same code as DexSubtitles' next, without starting a script).
"""
import os

import xbmc

OTHER = 'service.subtitles.dexworld'
_COMMANDS = {'subtitle_sync': 'best', 'subtitle_next': 'next', 'subtitle_pick': 'pick'}


def dexsubtitles_script():
    """DexSubtitles is enabled and has its player script (osd.py)."""
    try:
        if not xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % OTHER):
            return False
        import xbmcaddon
        import xbmcvfs
        path = xbmcvfs.translatePath(xbmcaddon.Addon(OTHER).getAddonInfo('path'))
        return os.path.isfile(os.path.join(path, 'osd.py'))
    except Exception:
        return False


def run(action):
    command = _COMMANDS.get(str(action or ''))
    if not command:
        return False
    if command in ('best', 'pick') and dexsubtitles_script():
        xbmc.log('[DexHub] subtitle buttons: %s handed to DexSubtitles' % command, xbmc.LOGINFO)
        xbmc.executebuiltin('RunScript(%s,%s)' % (OTHER, command))
        return True
    from . import osdflow
    osdflow.run([command])
    return True
