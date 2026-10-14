"""Bounded post-render warming; never called from focus handlers."""
import threading
import time
from . import meta_source
_lock = threading.Lock()
_pending = {}
_inflight = set()
_cooldown = {}
_MAX_PENDING = 6


def generation():
    return tuple(meta_source.ADDON.getSetting(k) or '' for k in (
        'global_meta_source_id', 'global_poster_source_id', 'metadata_generation'))


def enqueue(source, ids, kind):
    if source in ('', 'auto', 'native', 'same', 'tmdb_helper', 'betterposters'):
        return
    candidates = meta_source._candidate_ids(ids)
    if not candidates:
        return
    key = (source, kind, candidates[0])
    with _lock:
        if len(_pending) < _MAX_PENDING and key not in _inflight and time.monotonic() >= _cooldown.get(source, 0):
            _pending[key] = generation()


def flush():
    import xbmc
    from .dexhub import client, store
    with _lock:
        jobs = [(k, g) for k, g in _pending.items() if k not in _inflight][:max(0, _MAX_PENDING-len(_inflight))]
        _pending.clear()
        _inflight.update(k for k, _ in jobs)
    if not jobs:
        return
    folder = xbmc.getInfoLabel('Container.FolderPath')
    state = {'remaining': len(jobs), 'success': False}
    batch_gen = jobs[0][1]

    def done(key, success=False):
        with _lock:
            _inflight.discard(key)
            state['success'] = state['success'] or success
            state['remaining'] -= 1
            refresh = state['remaining'] == 0 and state['success']
        if refresh and generation() == batch_gen:
            # Do not rebuild a visible list or reload widgets while the user
            # is navigating. New cache data is consumed on the next opening.
            import xbmcgui
            xbmcgui.Window(10000).setProperty('dexhub.metadata.cache_ready', str(time.time()))

    def worker(key, gen):
        source, kind, item_id = key
        success = False
        try:
            with _lock:
                blocked = time.monotonic() < _cooldown.get(source, 0)
            if blocked or generation() != gen or xbmc.Monitor().abortRequested() or xbmc.Player().isPlaying():
                return
            if folder and xbmc.getInfoLabel('Container.FolderPath') != folder:
                return
            provider = store.get_provider(source)
            if not provider:
                return
            data = client.fetch_meta(provider, kind, item_id, timeout_override=4, retry=False, rate_wait=0)
            success = bool((data or {}).get('meta'))
            if not success:
                with _lock:
                    _cooldown[source] = time.monotonic() + 60
        except Exception as exc:
            with _lock:
                _cooldown[source] = time.monotonic() + (300 if getattr(exc, 'code', 0) in (401, 403, 429) else 60)
        finally:
            done(key, success)

    for key, gen in jobs:
        try:
            # The pool ignores requested size; use its actual two-worker
            # background lane, not the default four-worker browsing lane.
            future = client._get_pool(2, lane='revalidate').submit(worker, key, gen)
            future.add_done_callback(lambda f, k=key: done(k) if f.cancelled() else None)
        except Exception:
            done(key)
