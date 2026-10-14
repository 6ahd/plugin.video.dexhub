# -*- coding: utf-8 -*-
"""Where you are in a title, sent to the linked services at once (v5.10.206).

Trakt and the media servers already heard of a pause or a stop as it
happened, but Nuvio only on the next cloud sync (deferred while video plays,
then a 45 s debounce, often the 30 minute cycle) and Simkl only learnt that
a title was finished. Each playback event now queues a small push per
service; a worker thread sends it, so playback never waits on a network
call, and a failed push is tried again a few times (Nuvio's own sync still
carries the saved row later, so nothing is lost).
"""
import queue
import threading
import time

import xbmc

PROGRESS_EVERY = 120.0          # seconds between pushes while the video runs
_RETRY_DELAYS = (10.0, 30.0, 90.0, 270.0)
_queue = queue.Queue()
_worker_lock = threading.Lock()
_worker = [None]
_simkl_scrobble_off = [False]    # Simkl refused its scrobble API: history only


def _log(msg, level=xbmc.LOGINFO):
    try:
        xbmc.log('[DexHub] push: %s' % msg, level)
    except Exception:
        pass


def _run():
    while True:
        job = _queue.get()
        if job is None:
            return          # v5.10.241: the service stops (stop())
        name, fn, attempt = job
        try:
            fn()
        except Exception as exc:
            if attempt < len(_RETRY_DELAYS):
                delay = _RETRY_DELAYS[attempt]
                _log('%s failed (%s), again in %ds' % (name, exc.__class__.__name__, delay), xbmc.LOGINFO)
                timer = threading.Timer(delay, _queue.put, args=((name, fn, attempt + 1),))
                timer.daemon = True
                timer.start()
            else:
                _log('%s gave up: %s' % (name, exc), xbmc.LOGWARNING)


def stop():
    """v5.10.241: the worker leaves at once when the service stops (it
    waited on its queue for ever: Kodi killed the service after 5 s on
    every update)."""
    with _worker_lock:
        thread = _worker[0]
    if thread is not None and thread.is_alive():
        _queue.put(None)


def submit(name, fn):
    """Queue ``fn`` (no arguments) to run on the push worker."""
    with _worker_lock:
        if _worker[0] is None or not _worker[0].is_alive():
            thread = threading.Thread(target=_run, name='DexHub-push')
            thread.daemon = True
            thread.start()
            _worker[0] = thread
    _queue.put((name, fn, 0))


def _ms(value):
    try:
        return max(0, int(float(value or 0)))
    except Exception:
        return 0


def _is_episode(ctx):
    media = str((ctx or {}).get('media_type') or '').lower()
    return media in ('series', 'show', 'tv', 'anime', 'episode') or bool(
        (ctx or {}).get('season') not in (None, '') and (ctx or {}).get('episode') not in (None, ''))


# ------------------------------------------------------------------ Nuvio
def nuvio_ready():
    """Nuvio is linked, synced and allowed to receive progress."""
    try:
        from .dexhub import nuvio_stremio_sync as S
        if 'nuvio' not in S.enabled_targets():
            return False
        if S._direction_for('nuvio') == 'pull':
            return False
        return bool(S._sections_for('nuvio').get('progress', True))
    except Exception:
        return False


_SERVER_IDS = ('plex:', 'emby:', 'jellyfin:', 'silo:')


def nuvio_row(ctx, position_ms, finished=False):
    ctx = ctx or {}
    # v5.10.255: a title played straight from Plex, Emby or Silo carries
    # the server's own item number as its video id (Nuvio got "movie 929929"
    # for Spider-Man, "series 783161" for an episode): the title's own ids
    # instead, the show's with S:E for an episode (safe_helpers._search_ctx)
    try:
        from .safe_helpers import _search_ctx
        ctx = _search_ctx(ctx)
    except Exception:
        pass
    episode = _is_episode(ctx)
    ext = ctx.get('external_ids') if isinstance(ctx.get('external_ids'), dict) else {}
    imdb = str(ctx.get('imdb_id') or ext.get('imdb_id') or ext.get('imdb') or '').strip()
    tmdb = str(ctx.get('tmdb_id') or ext.get('tmdb_id') or ext.get('tmdb') or '').strip()
    canonical = str(ctx.get('canonical_id') or imdb or tmdb or '').strip()
    server_only = canonical.startswith(_SERVER_IDS) and not imdb and not tmdb
    if episode and ':' in canonical:
        canonical = canonical.split(':', 1)[0]
    if episode and canonical.startswith('tt') and imdb and imdb != canonical:
        imdb = canonical        # the show's, not the episode's own
    row = {
        'imdb_id': imdb,
        'tmdb_id': tmdb,
        'canonical_id': canonical,
        'media_type': 'series' if episode else 'movie',
        'video_id': str(ctx.get('video_id') or canonical),
        'position': _ms(position_ms) / 1000.0,
        'duration': _ms(ctx.get('duration_ms')) / 1000.0,
        'updated_at': int(time.time()),
        'finished': bool(finished),
    }
    if server_only:
        row['server_only'] = True
    if episode:
        try:
            row['season'] = int(ctx.get('season'))
            row['episode'] = int(ctx.get('episode'))
        except Exception:
            pass
        if row['video_id'] == canonical and row.get('season') is not None and row.get('episode') is not None:
            row['video_id'] = '%s:%s:%s' % (canonical, row['season'], row['episode'])
    return row


def push_nuvio(ctx, position_ms, finished=False):
    row = nuvio_row(ctx, position_ms, finished)
    if not row.get('canonical_id') and not row.get('imdb_id') and not row.get('tmdb_id'):
        return False
    if row.pop('server_only', False):
        # a server title with no IMDb or TMDb id: Nuvio could only show a
        # number it cannot open
        _log('nuvio: skipped %s (no title ids)' % row.get('canonical_id'))
        return False

    def send():
        from .dexhub import nuvio_stremio_sync as S
        S.Nuvio.sync_progress([row], direction='push')
        _log('nuvio %s %s at %ds%s' % (row['media_type'], row['video_id'], int(row['position']),
                                       ' (finished)' if finished else ''))
    submit('nuvio', send)
    return True


# ------------------------------------------------------------------ Simkl
def simkl_scrobble(action, ctx, position_ms):
    """Simkl's live scrobble (start / pause / stop), the shape Trakt uses."""
    if _simkl_scrobble_off[0]:
        return False
    from . import simkl
    ids = simkl._ids_from_ctx(ctx)
    if not ids:
        return False
    duration = _ms((ctx or {}).get('duration_ms'))
    progress = round(min(100.0, _ms(position_ms) * 100.0 / duration), 2) if duration else 0.0
    if _is_episode(ctx):
        try:
            season, number = int(ctx.get('season')), int(ctx.get('episode'))
        except Exception:
            return False
        body = {'progress': progress,
                'show': {'title': ctx.get('show_title') or ctx.get('title') or '', 'ids': ids},
                'episode': {'season': season, 'number': number}}
    else:
        body = {'progress': progress, 'movie': {'title': ctx.get('title') or '', 'ids': ids}}

    def send():
        try:
            simkl._request('/scrobble/%s' % action, payload=body, method='POST', auth=True, timeout=12)
        except Exception as exc:
            code = getattr(exc, 'code', 0)
            if code in (404, 405, 501):
                _simkl_scrobble_off[0] = True       # not offered: history only
                _log('simkl scrobble not offered (HTTP %s), history only' % code)
                return
            if code == 409:
                return                               # already scrobbled
            raise
        _log('simkl %s at %.0f%%' % (action, progress))
    submit('simkl', send)
    return True
