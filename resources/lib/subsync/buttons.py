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
_COMMANDS = {'subtitle_sync': 'best', 'subtitle_next': 'next', 'subtitle_pick': 'pick',
             'subtitle_translate': 'translate'}     # 5.10.588: Dex Hub's own, never DexSubtitles'


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


def _disc_playing():
    try:
        from .runner import disc_playing
        return disc_playing()
    except Exception:
        return False


def run(action):
    command = _COMMANDS.get(str(action or ''))
    if not command:
        return False
    # v5.10.486: on a Silo source the server syncs to the audio and translates, as in its apps
    if command == 'best':
        try:
            from .. import silo_subsync
            if silo_subsync.handle_button():
                return True
        except Exception as exc:
            xbmc.log('[DexHub] silo subtitles: %s' % exc, xbmc.LOGWARNING)
    # v5.10.588: a disc stays with Dex Hub: DexSubtitles cannot time it (no MKV index), Dex Hub
    # times it from a REMUX of the same disc in the sources list
    if command in ('best', 'pick') and dexsubtitles_script() and not _disc_playing():
        xbmc.log('[DexHub] subtitle buttons: %s handed to DexSubtitles' % command, xbmc.LOGINFO)
        xbmc.executebuiltin('RunScript(%s,%s)' % (OTHER, command))
        return True
    from . import osdflow
    osdflow.run([command])
    return True
