# -*- coding: utf-8 -*-
"""Context-menu shortcut: play the focused title in the opposite mode.

v5.10.78. Long-press any movie or episode (TMDb Helper widgets, the Kodi
library, Dex Hub lists) and one Dex Hub entry appears: "Search sources" when
the play setting is direct, "Play directly" when it is the Dex Hub search.
Each label states exactly what that entry does; the service only decides
which of the two is visible, through the dexhub.play_direct Home property.

This module imports nothing but the standard library and xbmc, so the menu
reacts at once. Playback itself goes through the normal tmdb_player route
via RunPlugin: no ResolvePath wait, no busy dialog, and no nested resolve if
the user presses something else while sources load.
"""
import sys
from urllib.parse import urlencode

import xbmc

ADDON_ID = 'plugin.video.dexhub'
PROPERTY = 'dexhub.play_direct'
MODES = ('search', 'direct')


def _first(*values):
    for value in values:
        text = str(value if value is not None else '').strip()
        if text and text not in ('0', '-1', 'None'):
            return text
    return ''


def _uid(tag, key):
    try:
        return tag.getUniqueID(key) or ''
    except Exception:
        return ''


def _prop(item, key):
    try:
        return item.getProperty(key) or ''
    except Exception:
        return ''


def build_params(item, mode):
    """tmdb_player arguments for the focused item, or {} if it cannot play.

    Episodes need the SHOW's ids, exactly as the TMDb Helper player template
    sends them. An episode's own tmdb uniqueid is a different number that
    would resolve to the wrong title, so it is never used; when no show id is
    present, tmdb_player's existing fallback (TMDb Helper's focused-item
    properties, then a title lookup) takes over.
    """
    tag = item.getVideoInfoTag()
    media = str(tag.getMediaType() or '').strip().lower()
    params = {'action': 'tmdb_player', 'play_mode': mode}
    if media == 'episode':
        season, episode = tag.getSeason(), tag.getEpisode()
        if season is None or episode is None or season < 0 or episode < 0:
            return {}
        params.update({
            'video_type': 'episode',
            'tmdb_id': _first(_uid(tag, 'tvshow.tmdb'), _prop(item, 'tvshow.tmdb_id'),
                              _prop(item, 'tvshow.tmdb')),
            'imdb_id': _first(_uid(tag, 'tvshow.imdb'), _prop(item, 'tvshow.imdb_id')),
            'tvdb_id': _first(_uid(tag, 'tvshow.tvdb'), _prop(item, 'tvshow.tvdb_id')),
            'showname': tag.getTVShowTitle() or '',
            'title': tag.getTitle() or '',
            'season': str(season), 'episode': str(episode),
        })
    elif media == 'movie':
        imdb = _first(_uid(tag, 'imdb'))
        if not imdb:
            number = str(tag.getIMDBNumber() or '')
            imdb = number if number.startswith('tt') else ''
        year = tag.getYear()
        params.update({
            'video_type': 'movie',
            'tmdb_id': _first(_uid(tag, 'tmdb'), _prop(item, 'tmdb_id')),
            'imdb_id': imdb,
            'title': tag.getTitle() or item.getLabel() or '',
            'year': str(year) if year and year > 0 else '',
        })
    else:
        return {}
    try:
        poster = item.getArt('poster') or item.getArt('thumb') or ''
        fanart = item.getArt('fanart') or ''
    except Exception:
        poster = fanart = ''
    if poster:
        params['poster'] = poster
    if fanart:
        params['fanart'] = fanart
    return {key: value for key, value in params.items() if value != ''}


def run(mode):
    if mode not in MODES:
        return
    item = getattr(sys, 'listitem', None)
    if item is None:
        return
    params = build_params(item, mode)
    if not params:
        xbmc.log('[DexHub] context play: item is not a movie or episode', xbmc.LOGINFO)
        return
    xbmc.log('[DexHub] context play: mode=%s %s' % (mode, params.get('video_type')),
             xbmc.LOGINFO)
    xbmc.executebuiltin('RunPlugin(plugin://%s/?%s)' % (ADDON_ID, urlencode(params)))


def publish(addon=None):
    """Mirror tmdbh_auto_play_first into the Home property the menu reads."""
    try:
        import xbmcaddon
        import xbmcgui
        addon = addon or xbmcaddon.Addon(ADDON_ID)
        direct = (addon.getSetting('tmdbh_auto_play_first') or 'false').lower() == 'true'
        xbmcgui.Window(10000).setProperty(PROPERTY, 'true' if direct else 'false')
    except Exception:
        pass
