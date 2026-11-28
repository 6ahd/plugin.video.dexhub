"""v5.10.620: Dex Hub TV's external button: the link it was playing, played
as it is (player_entry sends play_item calls that carry direct_url here)."""
import sys

import xbmc
import xbmcgui
import xbmcplugin

def play(params):
    """v5.10.620: play a link handed over by Dex Hub TV as it is: the same
    stream (with its request headers), every subtitle it had, the one on its
    screen turned on, from the same moment, tagged with the title's ids so
    Trakt, the subtitle service and Next up know what it is."""
    url = str(params.get('direct_url') or '').strip()
    if not url:
        return
    headers = str(params.get('headers') or '').strip()
    play_path = url + ('|' + headers if headers else '')
    title = str(params.get('title') or '').strip()
    media_type = str(params.get('media_type') or 'movie').strip().lower()
    season = str(params.get('season') or '').strip()
    episode = str(params.get('episode') or '').strip()
    imdb_id = str(params.get('imdb_id') or '').strip()
    tmdb_id = str(params.get('tmdb_id') or '').strip()
    subs = [s for s in str(params.get('subs') or '').split('\n') if s.strip()]
    selected_sub = str(params.get('selected_sub') or '').strip()
    try:
        resume = max(0.0, float(params.get('resume_seconds') or 0))
    except Exception:
        resume = 0.0
    li = xbmcgui.ListItem(label=title or url, path=play_path, offscreen=True)
    try:
        tag = li.getVideoInfoTag()
        if title:
            tag.setTitle(title)
        if media_type in ('series', 'tv', 'episode'):
            tag.setMediaType('episode')
            if season.isdigit():
                tag.setSeason(int(season))
            if episode.isdigit():
                tag.setEpisode(int(episode))
        else:
            tag.setMediaType('movie')
        ids = {}
        if imdb_id:
            ids['imdb'] = imdb_id
            tag.setIMDBNumber(imdb_id)
        if tmdb_id:
            ids['tmdb'] = tmdb_id
        if ids:
            tag.setUniqueIDs(ids, 'imdb' if imdb_id else 'tmdb')
    except Exception:
        pass
    if subs:
        li.setSubtitles([s for s in subs if s != selected_sub])
    if resume > 0:
        li.setProperty('StartOffset', '%.3f' % resume)
    xbmc.log('[DexHub] direct link from Dex Hub TV: subs=%d resume=%ds imdb=%s' % (len(subs), int(resume), imdb_id or '-'), xbmc.LOGINFO)
    try:
        handle = int(sys.argv[1])
    except Exception:
        handle = -1
    if handle >= 0:
        xbmcplugin.setResolvedUrl(handle, True, li)
    else:
        xbmc.Player().play(play_path, li)
    # once it plays: the subtitle that was on Dex Hub TV's screen, and the
    # moment again where StartOffset was not honoured
    player = xbmc.Player()
    monitor = xbmc.Monitor()
    for _ in range(120):
        if monitor.waitForAbort(0.25):
            return
        try:
            if player.isPlayingVideo() and player.getTotalTime() > 0:
                break
        except Exception:
            pass
    else:
        return
    try:
        if selected_sub:
            player.setSubtitles(selected_sub)
            player.showSubtitles(True)
        if resume > 10 and player.getTime() < resume - 10:
            player.seekTime(resume)
    except Exception as e:
        xbmc.log('[DexHub] direct link: after start: %s' % e, xbmc.LOGWARNING)
