"""IPTV/VOD handoff: never inherit an old playlist index on Kodi 22."""
import re
import threading

import xbmc

_LOCK = threading.RLock()


def play_video(url, item, player=None, windowed=False):
    player = player if player is not None else xbmc.Player()
    major = 0
    try:
        match = re.match(r'(\d+)', xbmc.getInfoLabel('System.BuildVersion') or '')
        major = int(match.group(1)) if match else 0
    except Exception:
        pass
    safe = major >= 22
    if safe:
        try:
            from ..settings_cache import cached_addon
            safe = (cached_addon().getSetting('safe_playback_handoff') or 'true').lower() \
                in ('1', 'true', 'yes', 'on')
        except Exception:
            pass
    if not safe:
        return player.play(url, item, windowed=windowed)

    # Direct IPTV/VOD playback previously bypassed the normal playback
    # engine's one-item playlist. A background video may leave a playlist
    # index which is invalid for the new item. Explicitly select entry zero.
    # Serialize clear/add/play in this Home interpreter (preview and click
    # workers share it); native failures do not fall back to the old route.
    with _LOCK:
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        playlist.clear()
        playlist.add(url, item)
        xbmc.log('[DexHub] IPTV/VOD handoff: one item, startpos=0', xbmc.LOGINFO)
        return player.play(playlist, windowed=windowed, startpos=0)
