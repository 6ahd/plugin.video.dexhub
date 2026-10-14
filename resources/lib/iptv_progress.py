# -*- coding: utf-8 -*-
"""Where an IPTV film or episode was stopped (v5.10.239).

A title played from an IPTV subscription (Xtream or DexWorld IPTV, the
Live TV page's Movies and Series) is kept here, not in playback.db: its id is
the subscription's own, so Next Up, the account syncs (Nuvio, Trakt, Simkl)
and the notices never see it. Continue Watching shows these rows (Settings:
IPTV films and series in Continue Watching) and OK plays them again from
where they stopped, from the same subscription.

<profile>/homeui/vod_progress.json: {"rows": [row, ...]}, newest first, at
most KEEP rows. A row has the fields of a playback.db row (media_type,
canonical_id, video_id, title, show_title, poster, background, season,
episode, position, duration, percent, updated_at, tmdb_id, imdb_id) and
"iptv": the play parameters (live_src, vod_type, id, ext, series, title,
poster).
"""
import json
import os
import threading
import time

KEEP = 120
FINISHED = 90.0         # percent: a title from here on counts as watched
PREFIX = 'iptv|'
_LOCK = threading.Lock()


def _path():
    import xbmcaddon
    import xbmcvfs
    profile = xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile'))
    return os.path.join(profile, 'homeui', 'vod_progress.json')


def _read(path=None):
    try:
        with open(path or _path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        rows = data.get('rows') if isinstance(data, dict) else None
        return [r for r in rows or [] if isinstance(r, dict) and r.get('video_id')]
    except Exception:
        return []


def _write(rows, path=None):
    path = path or _path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump({'rows': rows[:KEEP]}, handle, ensure_ascii=False)
        os.replace(tmp, path)
        try:
            from . import widget_cache
            widget_cache.mark_changed()
            import xbmcgui
            xbmcgui.Window(10000).setProperty('dhs.progress.changed', '%.6f' % time.time())
        except Exception:
            pass
        return True
    except Exception:
        return False


def is_iptv(row_or_id):
    value = row_or_id.get('canonical_id') if isinstance(row_or_id, dict) else row_or_id
    return str(value or '').startswith(PREFIX)


def ids_of(params):
    """(canonical_id, video_id) of a VOD play: the work (the film, or the
    series) and what plays (the film, or the episode)."""
    src = str(params.get('live_src') or '')
    media = 'series' if params.get('vod_type') == 'series' else 'movie'
    mid = str(params.get('id') or '')
    work = str(params.get('series') or '') if media == 'series' else mid
    canonical = '%s%s|%s|%s' % (PREFIX, src, media, work or mid)
    return canonical, '%s|%s' % (canonical, mid)


def find(video_id):
    for row in _read():
        if row.get('video_id') == video_id:
            return row
    return None


def save(ctx, position_sec, duration_sec, finished=False):
    """A progress tick of an IPTV play (the companion calls it)."""
    play = ctx.get('iptv') if isinstance(ctx.get('iptv'), dict) else {}
    canonical, video = ctx.get('canonical_id') or '', ctx.get('video_id') or ''
    if not (canonical and video and play):
        return False
    try:
        position, duration = float(position_sec or 0), float(duration_sec or 0)
    except (TypeError, ValueError):
        position, duration = 0.0, 0.0
    percent = 100.0 if finished else (max(0.0, min(100.0, position * 100.0 / duration)) if duration > 0 else 0.0)
    row = {
        'media_type': ctx.get('media_type') or 'movie', 'canonical_id': canonical, 'video_id': video,
        'title': ctx.get('title') or '', 'show_title': ctx.get('show_title') or '',
        'provider_name': ctx.get('provider_name') or 'IPTV',
        'poster': ctx.get('poster') or '', 'background': ctx.get('background') or '', 'clearlogo': '',
        'season': ctx.get('season') if ctx.get('season') not in ('', None) else None,
        'episode': ctx.get('episode') if ctx.get('episode') not in ('', None) else None,
        'position': position, 'duration': duration, 'percent': percent,
        'updated_at': int(time.time()), 'tmdb_id': str(ctx.get('tmdb_id') or ''),
        'imdb_id': str(ctx.get('imdb_id') or ''), 'tvdb_id': '',
        'show_tmdb_id': str(ctx.get('show_tmdb_id') or ''),
        'show_imdb_id': str(ctx.get('show_imdb_id') or ''),
        'native_server_id': '', 'native_item_id': '', 'iptv': dict(play),
    }
    with _LOCK:
        rows = [r for r in _read() if r.get('video_id') != video]
        if row['media_type'] == 'series':
            # one place per series: the episode played last
            rows = [r for r in rows if r.get('canonical_id') != canonical or r.get('percent', 0) >= FINISHED]
        rows.insert(0, row)
        return _write(rows)


def remove(video_id):
    with _LOCK:
        rows = _read()
        keep = [r for r in rows if r.get('video_id') != video_id]
        return _write(keep) if len(keep) != len(rows) else False


def mark_watched(video_id):
    with _LOCK:
        rows = _read()
        for row in rows:
            if row.get('video_id') == video_id:
                row['percent'] = 100.0
                row['position'] = row.get('duration') or row.get('position') or 0
        return _write(rows)


def continue_rows(limit=60):
    """The unfinished ones, newest first (Continue Watching), unless the
    setting leaves them out."""
    try:
        import xbmcaddon
        if (xbmcaddon.Addon('plugin.video.dexhub').getSetting('iptv_in_continue') or 'true').lower() == 'false':
            return []
    except Exception:
        pass
    out = []
    for row in _read():
        try:
            if float(row.get('percent') or 0) >= FINISHED or float(row.get('position') or 0) < 30:
                continue
        except (TypeError, ValueError):
            continue
        out.append(dict(row))
        if len(out) >= limit:
            break
    return out


def play_params(row, resume=True):
    """The home_vod_play parameters that play a row again."""
    play = dict(row.get('iptv') or {})
    params = {'live_src': play.get('live_src') or '', 'vod_type': play.get('vod_type') or 'movie',
              'id': play.get('id') or '', 'ext': play.get('ext') or 'mp4',
              'title': play.get('title') or row.get('title') or '', 'poster': play.get('poster') or row.get('poster') or '',
              'fanart': play.get('fanart') or row.get('background') or '',
              'series': play.get('series') or '', 'show_title': play.get('show_title') or row.get('show_title') or '',
              'tmdb_id': row.get('tmdb_id') or '', 'imdb_id': row.get('imdb_id') or '',
              'show_tmdb_id': row.get('show_tmdb_id') or '', 'show_imdb_id': row.get('show_imdb_id') or ''}
    if row.get('season') not in (None, ''):
        params['season'] = str(row.get('season'))
    if row.get('episode') not in (None, ''):
        params['episode'] = str(row.get('episode'))
    if resume:
        params['resume'] = '1'
    return params


def series_params(row):
    """The home_vod_list parameters of the series grid a row's episode is in."""
    play = dict(row.get('iptv') or {})
    if play.get('vod_type') != 'series' or not play.get('series'):
        return None
    params = {'live_src': play.get('live_src') or '', 'vod_type': 'series', 'id': play['series'],
              'vod_series': play['series'], 'title': play.get('show_title') or row.get('show_title') or '',
              'poster': play.get('poster') or ''}
    if row.get('season') not in (None, ''):
        params['season'] = str(row.get('season'))
    return params
