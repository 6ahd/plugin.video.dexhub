# -*- coding: utf-8 -*-
"""Cheap, stable ordering of Dex Hub menus. Never deduplicate by label."""
import re
from urllib.parse import urlsplit, parse_qsl


def route(command):
    match = re.search(r'plugin://[^)"\s]+', str(command or ''))
    if not match:
        return '', {}, ''
    url = urlsplit(re.sub(r',(?:return|replace)$', '', match.group(0), flags=re.I))
    return url.netloc, dict(parse_qsl(url.query, keep_blank_values=True)), url.path


def _rank(command, params):
    action = params.get('action', '')
    if command in ('__channel_watch__', '__dexhub_open__') or action in ('play_item', 'play_native', 'resume'): return 0
    if action == 'choose_player' or command == '__play_with__': return 10
    if action in ('streams', 'episode_streams'): return 20
    if command in ('__details__', '__info__') or action in ('skin_title', 'series_meta'): return 30
    if command == '__trailer__' or 'trailer' in action: return 40
    if action.startswith('fav_'): return 50
    if action in ('cw_mark_watched', 'iptv_cw_watched'): return 60
    if action in ('cw_remove', 'nextup_remove', 'iptv_cw_remove'): return 80
    if command.startswith('__row_'): return 100
    return 70


def clean(entries):
    """Preserve distinct targets, episode coordinates and destructive actions."""
    seen = set(); result = []
    for entry in entries or []:
        if not isinstance(entry, (tuple, list)) or len(entry) < 2:
            continue
        label, command = str(entry[0] or ''), str(entry[1] or '')
        if not label.strip() or not command.strip(): continue
        host, params, path = route(command)
        own = host in ('plugin.video.dexhub', 'plugin.video.dexhub.tvos')
        key = command.strip()
        if own:
            # Parameter order and launch wrapper vary between captured Home
            # cards and directory items; the same Dex Hub route is one action.
            key = (host, path, tuple(sorted(params.items())))
            if params.get('action') == 'choose_player':
                key = (host, 'choose_player', tuple(params.get(k, '') for k in
                       ('media_type', 'canonical_id', 'season', 'episode', 'video_id', 'source_provider_id')))
                label = 'التشغيل باستخدام…'
        if key in seen: continue
        seen.add(key)
        result.append((label, command, _rank(command, params) if own or command.startswith('__') else 70))
    result.sort(key=lambda entry: entry[2])
    return [(label, command) for label, command, _ in result]


def with_player(entries, path='', info=None, ids=None, props=None):
    """Fill missing Play with on real titles, including old cached cards.

    Navigation, source files and third-party directory items are untouched.
    The raw source menu order remains available to title-page source buttons.
    """
    entries = list(entries or [])
    if any(route(e[1])[1].get('action') == 'choose_player'
           for e in entries if isinstance(e, (list, tuple)) and len(e) > 1):
        return entries
    info, ids, props = info or {}, ids or {}, props or {}
    media = str(info.get('mediatype') or '').lower()
    if media not in ('movie', 'tvshow', 'episode'): return entries
    path = props.get('dhs.raw') or path
    host, params, _path = route(path)
    if host not in ('plugin.video.dexhub', 'plugin.video.dexhub.tvos'): return entries
    allowed = ('item_open', 'series_meta', 'season', 'seasons', 'streams', 'episode_streams', 'play_item',
               'plex_play', 'emby_play', 'jellyfin_play', 'silo_play', 'plex_children',
               'emby_children', 'jellyfin_children', 'silo_series', 'silo_episodes')
    if params.get('action') not in allowed: return entries
    from .play_with import from_tile, command
    tile = dict(path=path, media_type='series' if media == 'tvshow' else media,
                title=info.get('title') or params.get('title') or '', show=info.get('tvshowtitle') or '',
                season=info.get('season', ''), episode=info.get('episode', ''))
    for name in ('imdb', 'tmdb', 'tvdb'):
        tile[name + '_id'] = ids.get(name + '_id') or ids.get(name) or props.get(name + '_id') or ''
    target = from_tile(tile)
    if target.get('canonical_id') or target.get('native_path'):
        entries.append(('التشغيل باستخدام…', command(target)))
    return entries
