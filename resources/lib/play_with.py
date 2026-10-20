# -*- coding: utf-8 -*-
"""Explicit per-title player choice. No provider/metadata work before choice."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit
import xbmc
import xbmcaddon
import xbmcgui
from .context import build_url
from .i18n import tr

SERIES = ('series', 'anime', 'show', 'tv', 'tvshow', 'episode')
FIELDS = ('media_type', 'canonical_id', 'title', 'tmdb_id', 'imdb_id', 'tvdb_id',
          'season', 'episode', 'video_id', 'source_provider_id', 'native_path')


def episode_coords(params):
    try:
        season, episode = int(params.get('season', '')), int(params.get('episode', ''))
        return (season, episode) if season >= 0 and episode > 0 else None
    except (TypeError, ValueError):
        return None


def from_tile(tile):
    params = dict(parse_qsl(urlsplit(tile.get('path') or '').query))
    for key in FIELDS:
        value = tile.get(key)
        if value is not None and value != '': params[key] = value
    if str(params.get('media_type', '')).lower() == 'episode':
        params['title'] = tile.get('show') or params.get('title', '')
    if not params.get('canonical_id'):
        params['canonical_id'] = params.get('imdb_id') or ('tmdb:' + str(params['tmdb_id']) if params.get('tmdb_id') else '')
    if _native_path(tile.get('path') or ''):
        params['native_path'] = tile['path']
    params['action'] = 'choose_player'
    return {key: value for key, value in params.items() if key in FIELDS or key == 'action'}


def command(params):
    return 'RunPlugin(%s)' % build_url(**dict(params, action='choose_player'))


def _identity(params):
    for key in ('tmdb_id', 'imdb_id', 'tvdb_id'):
        if params.get(key): return {key: str(params[key])}
    canonical = str(params.get('canonical_id') or '')
    imdb = re.fullmatch(r'(tt[0-9]+)(?::[0-9]+:[0-9]+)?', canonical)
    if imdb: return {'imdb_id': imdb.group(1)}
    match = re.fullmatch(r'(tmdb|tvdb)[:_]([0-9]+)', canonical, re.I)
    if match: return {match.group(1).lower() + '_id': match.group(2)}
    title = str(params.get('title') or '').strip()
    return {'query': title} if title else {}


def _arg(value):
    return '"%s"' % str(value).replace('\\', '\\\\').replace('"', '\\"')


def _native_path(path):
    url = urlsplit(path)
    action = dict(parse_qsl(url.query)).get('action', '')
    if url.netloc not in ('plugin.video.dexhub', 'plugin.video.dexhub.tvos'):
        return ''
    return action if action in ('plex_play', 'emby_play', 'jellyfin_play', 'silo_play',
        'plex_children', 'emby_children', 'jellyfin_children', 'silo_series', 'silo_episodes') else ''


def dex_command(params):
    native = params.get('native_path') or ''
    native_action = _native_path(native)
    if native_action:
        if native_action.endswith('_play'):
            return 'RunPlugin(%s)' % _arg(native)
        return 'ActivateWindow(Videos,%s,return)' % _arg(native)
    series = str(params.get('media_type') or 'movie').lower() in SERIES
    coords = episode_coords(params) if series else None
    target = {key: params[key] for key in FIELDS if key != 'native_path' and params.get(key) is not None and params.get(key) != ''}
    target['media_type'] = 'series' if series else 'movie'
    if series and not coords:
        target['action'] = 'series_meta'
        return 'ActivateWindow(Videos,%s,return)' % _arg(build_url(**target))
    target.update(action='episode_streams' if coords else 'streams', force_picker='1')
    if coords:
        target.update(season=str(coords[0]), episode=str(coords[1]))
        target['video_id'] = params.get('video_id') or params.get('canonical_id', '')
    return 'RunPlugin(%s)' % build_url(**target)


def helper_command(params):
    identity = _identity(params)
    if not identity: return ''
    series = str(params.get('media_type') or 'movie').lower() in SERIES
    coords = episode_coords(params) if series else None
    if series and not coords:
        target = dict(identity, info='details', type='tv')
        url = 'plugin://plugin.video.themoviedb.helper/?' + urlencode(target)
        return 'ActivateWindow(Videos,%s,return)' % _arg(url)
    # Official TMDb Helper Player-Function API. Explicit Play with always
    # shows compatible installed players, even when Dex Hub is the default.
    target = dict(identity, play='tv' if series else 'movie', ignore_default='true')
    if coords: target.update(season=str(coords[0]), episode=str(coords[1]))
    return 'RunScript(plugin.video.themoviedb.helper,%s)' % ','.join(_arg('%s=%s' % pair) for pair in target.items())


def run(params):
    from .homeui.options import choose
    helper = helper_command(params)
    installed = bool(xbmc.getCondVisibility('System.HasAddon(plugin.video.themoviedb.helper)'))
    options = [(tr('مصادر Dex Hub'), tr('اختيار المصدر لهذا العمل'))]
    if helper:
        options.append((tr('إضافة أخرى عبر TMDb Helper'), tr('اختيار الإضافة والمشغّل') if installed else tr('يلزم تثبيت TMDb Helper')))
    choice = choose(params.get('title') or tr('التشغيل باستخدام…'), options, subtitle=tr('التشغيل باستخدام…'))
    if choice == 0:
        xbmc.executebuiltin(dex_command(params))
    elif choice == 1 and helper:
        if installed:
            xbmc.executebuiltin(helper)
        else:
            # Open its add-on page; installation remains an explicit Kodi action.
            xbmc.executebuiltin('Addon.OpenInfo(plugin.video.themoviedb.helper)')
