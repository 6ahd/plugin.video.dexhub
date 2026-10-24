# -*- coding: utf-8 -*-
import json
import os
import time

import xbmc
from . import profile_scope as _scope
from .nextup_state import Snapshot, coordinate, show_keys, watched, history, visible

NEXTUP_CACHE_KEY = 'dexhub.nextup_cache'
NEXTUP_CACHE_TS_KEY = 'dexhub.nextup_cache_ts'
SERIES_CACHE_TTL = 3600
NEXTUP_CACHE_TTL = 600  # fresh for 10 min
NEXTUP_STALE_TTL = 21600  # serve stale cache up to 6h while refreshing in background


def _cache_file_path():
    try:
        from .dexhub.common import profile_path
        return os.path.join(profile_path(), 'nextup_cache.json')
    except Exception:
        return ''


def _read_file_cache():
    path = _cache_file_path()
    if not path:
        return None, 0
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        items = data.get('items') if isinstance(data, dict) else None
        ts = int(data.get('ts') or 0) if isinstance(data, dict) else 0
        if isinstance(items, list) and ts:
            return items, ts
    except Exception:
        pass
    return None, 0


def _write_file_cache(items):
    path = _cache_file_path()
    if not path:
        return
    try:
        # v3.9.17: atomic write — minor reliability win since this cache is
        # rebuildable, but consistency means a corrupt next-up cache no
        # longer blocks the home screen on reload.
        from .dexhub.safe_io import write_json as _safe_write_json
        _safe_write_json(path, {'ts': int(time.time()), 'items': items or []})
    except Exception:
        pass


def _delete_file_cache():
    path = _cache_file_path()
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def invalidate_nextup_cache(window):
    for key in (NEXTUP_CACHE_KEY, NEXTUP_CACHE_TS_KEY):
        try:
            window.clearProperty(key)
        except Exception:
            pass
    _delete_file_cache()


def series_videos_cached(canonical_id, *, window, prop_prefix, provider_order_for_id, fetch_meta, fast_timeout_value,
                         item_fallback_art, resolve_meta_art, provider_prefers_native_art,
                         media_type='series', title=''):
    canonical_id = str(canonical_id or '').strip()
    if not canonical_id:
        return []
    cache_key = prop_prefix + canonical_id
    try:
        cached_ts = int(window.getProperty(cache_key + '.ts') or '0')
    except Exception:
        cached_ts = 0
    raw = window.getProperty(cache_key) or ''
    if raw and cached_ts and (time.time() - cached_ts) <= SERIES_CACHE_TTL:
        try:
            videos = json.loads(raw)
            if isinstance(videos, list) and videos:
                return videos
        except Exception:
            pass
    providers = provider_order_for_id(media_type or 'series', canonical_id)
    picked_meta = None
    for provider in providers:
        try:
            data = fetch_meta(provider, media_type or 'series', canonical_id, timeout_override=fast_timeout_value)
            meta = (data or {}).get('meta') or (data or {}).get('metas')
            if isinstance(meta, list):
                meta = meta[0] if meta else None
            if meta and (meta.get('videos') or []):
                picked_meta = meta
                provider_fb = item_fallback_art(provider, media_type or 'series')
                try:
                    series_art = resolve_meta_art(provider, media_type or 'series', meta, fallback_art=provider_fb)
                    window.setProperty(cache_key + '.art', json.dumps(series_art))
                    window.setProperty(cache_key + '.native_art', '1' if provider_prefers_native_art(provider) else '0')
                except Exception:
                    pass
                break
        except Exception:
            continue
    if not picked_meta:
        return []
    videos = picked_meta.get('videos') or []
    try:
        window.setProperty(cache_key, json.dumps(videos))
        window.setProperty(cache_key + '.ts', str(int(time.time())))
        window.setProperty(cache_key + '.title', title or picked_meta.get('name') or canonical_id)
        window.setProperty(cache_key + '.media_type', media_type or 'series')
    except Exception:
        pass
    return videos


def _build_nextup_items(limit, trakt_mod, playback_store_mod, series_videos_loader, log_prefix='[DexHub]'):
    items = []
    seen = set()
    seen_shows = set()
    trakt_rows = []

    try:
        trakt_progress_enabled = (not hasattr(trakt_mod, 'sync_enabled') or
                                  bool(trakt_mod.sync_enabled()))
        trakt_rows = (trakt_mod.fetch_next_up(limit=limit) or []) if trakt_progress_enabled else []
    except Exception as exc:
        xbmc.log('%s nextup trakt fetch failed: %s' % (log_prefix, exc), xbmc.LOGDEBUG)

    try:
        # Lower than the previous 300. We only need enough recent shows to fill
        # the visible NextUp screen; the large scan was the main delay on open.
        activity_rows = playback_store_mod.list_recent_items(limit=max(40, min(120, limit * 2)), media_types=['series', 'anime', 'tv', 'show', 'episode'], include_watched=True) or []
    except Exception:
        activity_rows = []

    state = Snapshot(history(playback_store_mod, activity_rows + trakt_rows))
    hidden_reader = getattr(playback_store_mod, 'hidden_nextup_keys', None)
    hidden = hidden_reader() if callable(hidden_reader) else set()
    for row in trakt_rows:
        keys = show_keys(row)
        point = coordinate(row)
        if not point or point[0] <= 0 or state.watched(row) or keys & (hidden | seen_shows):continue
        seen.add((row.get('canonical_id') or '',)+point)
        seen_shows.update(keys);items.append(row)

    latest_by_show = {}
    for row in activity_rows:
        try:
            canonical = row.get('canonical_id') or ''
            if not canonical or canonical in latest_by_show:
                continue
            latest_by_show[canonical] = row
            if len(latest_by_show) >= max(40, min(80, limit)):
                break
        except Exception:
            continue

    # v5.10.239: the shows' episode lists are read a few at a time (one by
    # one, nineteen shows took 70 s on a CoreELEC box), a batch after another
    # until the row is full
    wanted = []
    for canonical, row in latest_by_show.items():
        try:
            keys = show_keys(row)
            if keys & (hidden | seen_shows):continue
            percent = float(row.get('percent') or 0.0)
            event_type = str(row.get('event_type') or '').lower()
            threshold = 80.0 if 'simkl' in str(row.get('provider_name') or '').lower() else 90.0
            if percent < threshold and event_type not in ('watched', 'finished'):
                continue
            point = coordinate(row)
            if not point or point[0] <= 0:
                continue
            wanted.append((canonical, row))
        except Exception:
            continue
    lists = {}

    def _read(pair):
        canonical, row = pair
        mt = (row.get('media_type') or '').lower() or 'series'
        if mt in ('episode','tv','show'):mt = 'series'
        try:
            return canonical, series_videos_loader(canonical, media_type=mt, title=row.get('title') or canonical) or []
        except Exception:
            return canonical, []

    def _lists_for(batch):
        missing = [pair for pair in batch if pair[0] not in lists]
        if not missing:
            return
        if len(missing) == 1:
            key, videos = _read(missing[0])
            lists[key] = videos
            return
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(missing))) as pool:
                for result in pool.map(_scope.capture(_read), missing):
                    if result is not None:
                        key, videos = result
                        lists[key] = videos
        except RuntimeError:
            for pair in missing:
                key, videos = _read(pair)
                lists[key] = videos

    for index, (canonical, row) in enumerate(wanted):
        if index % 8 == 0:
            _lists_for(wanted[index:index + 8])
        try:
            mt = (row.get('media_type') or '').lower() or 'series'
            anchor = max(coordinate(row) or (0,0), state.furthest(row))
            videos = sorted((v for v in lists.get(canonical) or [] if coordinate(v) and coordinate(v)[0] > 0),
                            key=coordinate)
            picked = next((v for v in videos if coordinate(v) > anchor and
                           not state.watched(dict(row, season=coordinate(v)[0], episode=coordinate(v)[1]))), None)
            if not picked:continue
            next_s, next_e = coordinate(picked)
            key = (canonical, next_s, next_e)
            if key in seen or show_keys(row) & seen_shows:
                continue
            seen.add(key)
            seen_shows.update(show_keys(row))
            items.append({
                'media_type': mt or 'series',
                'canonical_id': canonical,
                'video_id': picked.get('id') or canonical,
                'title': row.get('title') or canonical,
                'episode_title': picked.get('title') or '',
                # v5.10.374: when it comes out (the Next episode row shows it)
                'aired': picked.get('released') or picked.get('firstAired') or '',
                'show_tmdb_id': str(row.get('show_tmdb_id') or ''),
                'imdb_id': str(row.get('imdb_id') or ''),
                'season': next_s,
                'episode': next_e,
                'plot': picked.get('overview') or '',
                'poster': row.get('poster') or '',
                'background': row.get('background') or '',
                'clearlogo': row.get('clearlogo') or '',
                'provider_name': row.get('provider_name') or '',
                'last_watched_at': int(row.get('updated_at') or 0),
            })
            if len(items) >= limit:
                break
        except Exception:
            continue

    return sorted(items, key=lambda r: int(r.get('last_watched_at') or 0), reverse=True)[:limit]


@_scope.bound
def nextup_items_cached(limit=40, *, window, trakt_mod, playback_store_mod, series_videos_loader, log_prefix='[DexHub]', cached_only=False):
    try:
        cached_ts = int(window.getProperty(NEXTUP_CACHE_TS_KEY) or '0')
    except Exception:
        cached_ts = 0
    cached_raw = window.getProperty(NEXTUP_CACHE_KEY) or ''
    cached = None
    if cached_raw and cached_ts:
        try:
            parsed = json.loads(cached_raw)
            if isinstance(parsed, list):
                cached = parsed
        except Exception:
            cached = None
    # Cross-process/cache-on-disk path: Kodi starts a new plugin process for
    # many skin widget opens, so Window properties alone are not enough after a
    # restart or service reset.
    if cached is None:
        file_cached, file_ts = _read_file_cache()
        if file_cached is not None and file_ts:
            cached = file_cached
            cached_ts = file_ts
            try:
                window.setProperty(NEXTUP_CACHE_KEY, json.dumps(file_cached))
                window.setProperty(NEXTUP_CACHE_TS_KEY, str(file_ts))
            except Exception:
                pass

    age = time.time() - cached_ts if cached_ts else 999999
    if cached is not None and age <= NEXTUP_CACHE_TTL:
        return visible(cached, playback_store_mod)[:limit]

    def _refresh_cache():
        try:
            fresh = _build_nextup_items(limit, trakt_mod, playback_store_mod, series_videos_loader, log_prefix=log_prefix)
            _scope.require_current()
            now = int(time.time())
            window.setProperty(NEXTUP_CACHE_KEY, json.dumps(fresh))
            window.setProperty(NEXTUP_CACHE_TS_KEY, str(now))
            _write_file_cache(fresh)
            # Refresh only progress shelves; never reload Home or its hero.
            from .skinui import common as C, board
            for tab in C.TABS:
                for spec in board.load_specs(tab):
                    if spec.get('progress'):
                        C.request_refresh(tab, spec['id'])
        except Exception as exc:
            xbmc.log('%s nextup refresh failed: %s' % (log_prefix, exc), xbmc.LOGDEBUG)

    # Fast path for UI: use stale cache while a background refresh warms the
    # next open. This keeps NextUp snappy on skins/widgets that reopen it often.
    if cached_only or (cached is not None and age <= NEXTUP_STALE_TTL):
        try:
            from .runtime_tasks import submit_optional
            submit_optional(_scope.capture(_refresh_cache), key='nextup-cache-refresh:' + _scope.key())
        except Exception:
            pass
        return visible(cached or [], playback_store_mod)[:limit]

    items = _build_nextup_items(limit, trakt_mod, playback_store_mod, series_videos_loader, log_prefix=log_prefix)
    _scope.require_current()
    try:
        now = int(time.time())
        window.setProperty(NEXTUP_CACHE_KEY, json.dumps(items))
        window.setProperty(NEXTUP_CACHE_TS_KEY, str(now))
        _write_file_cache(items)
    except Exception:
        pass
    return visible(items, playback_store_mod)[:limit]
