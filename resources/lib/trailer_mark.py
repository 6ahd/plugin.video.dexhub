# -*- coding: utf-8 -*-
"""What a trailer looks like (v5.10.218), so it is never saved as watching.

Every trailer Dex Hub plays carries the ListItem property ``dexhub.trailer``
= 1 (its fingerprint), and the Home holds ``dexhub.trailer.active`` while it
plays. The addresses below are trailers whoever started them (IMDb's video
CDN, Apple's trailers, KinoCheck; YouTube and Vimeo when no source was
picked), so a trailer whose marks
were lost still never reaches Continue Watching, Trakt, Simkl or Nuvio.
"""
from urllib.parse import unquote

# only ever trailers
HOSTS = ('imdb-video.media-imdb.com', 'media-imdb.com/video', 'trailers.apple.com',
         'movietrailers.apple.com', 'kinocheck.com', 'kinocheck.de', 'dexhub_trailer=1')
# trailers too, unless a source the user picked plays from there (DexTube)
VIDEO_SITES = ('googlevideo.com', 'youtube.com/', 'youtu.be/', 'youtube-nocookie.com',
               'plugin.video.youtube', 'plugin.video.tubed', 'vimeo.com/', 'vimeocdn.com')
PROP = 'dexhub.trailer'


def is_trailer_url(url, picked=True):
    """``picked``: the user chose this source, so only the trailer-only
    addresses count; without a pick the video sites count too."""
    value = unquote(str(url or '')).split('|', 1)[0].lower()
    if not value:
        return False
    return any(host in value for host in HOSTS + (() if picked else VIDEO_SITES))


def playing_is_trailer(player=None):
    """True while what plays is a trailer (its fingerprint or its address)."""
    try:
        import xbmc
        player = player or xbmc.Player()
        if not player.isPlayingVideo():
            return False
        try:
            item = player.getPlayingItem()
            if item is not None and item.getProperty(PROP) == '1':
                return True
        except Exception:
            pass
        return is_trailer_url(player.getPlayingFile(), picked=True)
    except Exception:
        return False
