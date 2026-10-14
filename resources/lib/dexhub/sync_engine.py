# -*- coding: utf-8 -*-
"""DexHub Sync Engine — background catalog → index pipeline.

This is the WORKER that fills the IndexDB. It knows about:
  * Stremio catalogs (via client.fetch_catalog)
  * The user's pinned-catalogs configuration (provided by caller)
  * The IndexDB schema (via index.IndexDB)

It does NOT know about:
  * How folders are rendered (that's index_render)
  * How the user triggers a sync (that's plugin.py actions)
  * UI / progress display (caller subscribes via on_progress callback)

Lifecycle:
  - Started by service.py at addon boot, after a delay.
  - Periodic sync every N hours (configurable, default 4h).
  - Manual sync via plugin actions (index_refresh_all, index_refresh_bucket).
  - Pauses if Kodi requests shutdown.

Concurrency:
  - At most 2 catalogs sync in parallel — gentle on user bandwidth.
  - Per-catalog 8s timeout (longer than folder open's 4s because this
    is background work, not user-facing).
  - Sync runs are mutex'd so a manual refresh during a scheduled run
    doesn't trigger duplicate work.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import xbmc

from . import index as index_mod
from .client import fetch_catalog


SYNC_WORKERS         = 2          # concurrent catalogs
SYNC_TIMEOUT_PER_CAT = 8          # seconds per catalog fetch
SYNC_LIMIT_DEFAULT   = 100        # items per catalog per sync (caps payload)
SYNC_INTERVAL_HOURS  = 4          # default scheduled sync interval


class SyncEngine:
    """Orchestrates background syncs of pinned catalogs into IndexDB."""

    def __init__(self, db: index_mod.IndexDB,
                 pinned_entries_provider=None,
                 on_progress=None):
        """
        db: IndexDB instance.
        pinned_entries_provider: callable() → list of (provider_dict,
            catalog_dict, bucket_str, pin_dict) tuples. We call this on
            every sync because the user can pin/unpin between runs. The
            real impl plumbs to plugin._hub_catalog_entries / similar.
        on_progress: optional callback(stage, info_dict). Called with
            stage='start'|'catalog'|'done'|'error' so UI can display.
        """
        self._db = db
        self._get_pinned = pinned_entries_provider or (lambda: [])
        self._on_progress = on_progress or (lambda *a, **kw: None)
        self._mutex = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()


    def stop(self):
        self._stop_event.set()

    def sync_all(self):
        """Sync every pinned catalog into the index. Blocking. Safe to
        call from a background thread."""
        if not self._mutex.acquire(blocking=False):
            xbmc.log('[DexHub] sync_all: another sync is running, skipping',
                     xbmc.LOGINFO)
            return {'skipped': True}
        try:
            self._running = True
            self._stop_event.clear()
            entries = list(self._get_pinned() or [])
            return self._run_entries(entries, label='full')
        finally:
            self._running = False
            self._mutex.release()

    def sync_bucket(self, bucket):
        """Sync just the catalogs pinned to one home bucket."""
        if not self._mutex.acquire(blocking=False):
            xbmc.log('[DexHub] sync_bucket: another sync is running, skipping',
                     xbmc.LOGINFO)
            return {'skipped': True}
        try:
            self._running = True
            self._stop_event.clear()
            entries = [
                e for e in (self._get_pinned() or [])
                if (e[2] if len(e) > 2 else '') == bucket
            ]
            return self._run_entries(entries, label='bucket:%s' % bucket)
        finally:
            self._running = False
            self._mutex.release()


    # ─── Internals ───────────────────────────────────────────────────
    def _run_entries(self, entries, label):
        if not entries:
            self._on_progress('done', {
                'label': label, 'total': 0,
                'message': 'no pinned catalogs to sync',
            })
            return {'total': 0, 'ok': 0, 'errors': 0}

        started_at = time.time()
        self._on_progress('start', {
            'label': label, 'total': len(entries),
        })

        ok = 0
        errors = 0

        # Bounded parallelism — these are background fetches, we don't
        # need to drown the network.
        with ThreadPoolExecutor(max_workers=SYNC_WORKERS,
                                 thread_name_prefix='DexHub-sync') as pool:
            futures = {}
            for idx, entry in enumerate(entries):
                if self._stop_event.is_set():
                    break
                fut = pool.submit(self._sync_one, entry, idx, len(entries))
                futures[fut] = entry

            for fut in as_completed(futures):
                if self._stop_event.is_set():
                    break
                try:
                    result = fut.result()
                    if result and result.get('status') == 'ok':
                        ok += 1
                    else:
                        errors += 1
                except Exception as exc:
                    errors += 1
                    xbmc.log('[DexHub] sync future failed: %s' % exc,
                             xbmc.LOGWARNING)

        duration = time.time() - started_at
        self._on_progress('done', {
            'label': label,
            'total': len(entries),
            'ok': ok,
            'errors': errors,
            'duration': duration,
        })
        xbmc.log(
            '[DexHub] sync %s done: %d catalogs in %.1fs (%d ok / %d errors)' %
            (label, len(entries), duration, ok, errors),
            xbmc.LOGINFO,
        )
        return {'total': len(entries), 'ok': ok, 'errors': errors,
                'duration': duration}

    def _sync_one(self, entry, idx, total):
        """Fetch one catalog and upsert into the index."""
        # v3.9.28: defensive about entry shape. _hub_catalog_entries
        # returns 3-tuples (provider, catalog, bucket); other callers
        # may produce 4-tuples or even shorter shapes. Skip silently
        # rather than crash the whole sync run.
        if not isinstance(entry, (tuple, list)) or len(entry) < 2:
            xbmc.log('[DexHub] sync skipping malformed entry: %r' % (entry,),
                     xbmc.LOGDEBUG)
            return {'status': 'error', 'error': 'malformed entry'}

        provider = entry[0] or {}
        catalog  = entry[1] or {}
        bucket   = entry[2] if len(entry) > 2 else ''
        if not isinstance(provider, dict) or not isinstance(catalog, dict):
            return {'status': 'error', 'error': 'non-dict entry'}
        provider_id = (provider or {}).get('id') or ''
        catalog_id  = (catalog or {}).get('id') or ''
        media_type  = (catalog or {}).get('type') or 'movie'

        self._on_progress('catalog', {
            'index': idx + 1, 'total': total,
            'provider': provider.get('name') or provider_id,
            'catalog':  catalog.get('name') or catalog_id,
            'bucket':   bucket,
        })

        t0 = time.time()
        try:
            # Fetch with limit so first sync isn't 10K items per catalog.
            data = fetch_catalog(provider, media_type, catalog_id,
                                  extra={'limit': str(SYNC_LIMIT_DEFAULT)},
                                  timeout_override=SYNC_TIMEOUT_PER_CAT)
            metas = (data or {}).get('metas') or []
            if not isinstance(metas, list):
                metas = []

            inserted, updated, sources = self._db.upsert_items_from_catalog(
                metas, provider_id, catalog_id, bucket)

            duration = time.time() - t0
            self._db.record_sync(
                provider_id, catalog_id, bucket,
                item_count=inserted + updated,
                duration=duration, status='ok',
            )
            return {'status': 'ok',
                    'inserted': inserted, 'updated': updated,
                    'duration': duration}
        except Exception as exc:
            duration = time.time() - t0
            err = str(exc)[:300]
            try:
                self._db.record_sync(
                    provider_id, catalog_id, bucket,
                    item_count=0, duration=duration,
                    status='error', error_message=err,
                )
            except Exception:
                pass
            xbmc.log(
                '[DexHub] sync %s/%s failed: %s' % (provider_id, catalog_id, err),
                xbmc.LOGWARNING,
            )
            return {'status': 'error', 'error': err}


# ─── Scheduler loop (called from service.py) ──────────────────────────
def run_scheduler(engine: SyncEngine, monitor, get_interval_hours=None):
    """Periodic-sync loop. Runs forever in a daemon thread.

    `monitor` is xbmc.Monitor() — used for abort/wait.
    `get_interval_hours` is callable returning the interval at each tick
    so settings changes take effect without restart.
    """
    # First-run safety: don't sync the second we boot — wait so the user
    # can interact with home first.
    monitor.waitForAbort(30)

    while not monitor.abortRequested():
        try:
            engine.sync_all()
        except Exception as exc:
            xbmc.log('[DexHub] scheduler error: %s' % exc, xbmc.LOGWARNING)
        # Sleep until next run. Re-read interval every loop so the user
        # can shorten/lengthen without restarting Kodi.
        try:
            hours = int(get_interval_hours() if get_interval_hours else SYNC_INTERVAL_HOURS)
        except Exception:
            hours = SYNC_INTERVAL_HOURS
        hours = max(1, min(48, hours))
        if monitor.waitForAbort(hours * 3600):
            break
