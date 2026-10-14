# -*- coding: utf-8 -*-
import json
import os
import time

import xbmc

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

    try:
        trakt_progress_enabled = (not hasattr(trakt_mod, 'sync_enabled') or
                                  bool(trakt_mod.sync_enabled()))
        trakt_rows = (trakt_mod.fetch_next_up(limit=limit) or []) if trakt_progress_enabled else []
        for row in trakt_rows:
            key = (row.get('canonical_id') or '', row.get('season') or 0, row.get('episode') or 0)
            if key in seen:
                continue
            seen.add(key)
            items.append(row)
    except Exception as exc:
        xbmc.log('%s nextup trakt fetch failed: %s' % (log_prefix, exc), xbmc.LOGDEBUG)

    try:
        # Lower than the previous 300. We only need enough recent shows to fill
        # the visible NextUp screen; the large scan was the main delay on open.
        activity_rows = playback_store_mod.list_recent_items(limit=max(40, min(120, limit * 2)), media_types=['series', 'anime', 'tv', 'show'], include_watched=True) or []
    except Exception:
        activity_rows = []

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
            percent = float(row.get('percent') or 0.0)
            event_type = str(row.get('event_type') or '').lower()
            if percent < 70.0 and event_type != 'watched':
                continue
            if int(row.get('season') or 0) <= 0 or int(row.get('episode') or 0) <= 0:
                continue
            wanted.append((canonical, row))
        except Exception:
            continue
    lists = {}

    def _read(pair):
        canonical, row = pair
        mt = (row.get('media_type') or '').lower() or 'series'
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
                for key, videos in pool.map(_read, missing):
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
            cur_season = int(row.get('season') or 0)
            cur_episode = int(row.get('episode') or 0)
            videos = lists.get(canonical) or []
            picked = None
            next_s, next_e = cur_season, cur_episode + 1
            for v in videos:
                try:
                    if int(v.get('season') or 0) == next_s and int(v.get('episode') or 0) == next_e:
                        picked = v
                        break
                except Exception:
                    continue
            if not picked:
                next_s, next_e = cur_season + 1, 1
                for v in videos:
                    try:
                        if int(v.get('season') or 0) == next_s and int(v.get('episode') or 0) == next_e:
                            picked = v
                            break
                    except Exception:
                        continue
            if not picked:
                continue
            key = (canonical, next_s, next_e)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                'media_type': mt or 'series',
                'canonical_id': canonical,
                'video_id': picked.get('id') or canonical,
                'title': row.get('title') or canonical,
                'episode_title': picked.get('title') or '',
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


def nextup_items_cached(limit=40, *, window, trakt_mod, playback_store_mod, series_videos_loader, log_prefix='[DexHub]'):
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
        return cached[:limit]

    def _refresh_cache():
        try:
            fresh = _build_nextup_items(limit, trakt_mod, playback_store_mod, series_videos_loader, log_prefix=log_prefix)
            now = int(time.time())
            window.setProperty(NEXTUP_CACHE_KEY, json.dumps(fresh))
            window.setProperty(NEXTUP_CACHE_TS_KEY, str(now))
            _write_file_cache(fresh)
        except Exception as exc:
            xbmc.log('%s nextup refresh failed: %s' % (log_prefix, exc), xbmc.LOGDEBUG)

    # Fast path for UI: use stale cache while a background refresh warms the
    # next open. This keeps NextUp snappy on skins/widgets that reopen it often.
    if cached is not None and age <= NEXTUP_STALE_TTL:
        try:
            from .runtime_tasks import submit_optional
            submit_optional(_refresh_cache, key='nextup-cache-refresh')
        except Exception:
            pass
        return cached[:limit]

    items = _build_nextup_items(limit, trakt_mod, playback_store_mod, series_videos_loader, log_prefix=log_prefix)
    try:
        now = int(time.time())
        window.setProperty(NEXTUP_CACHE_KEY, json.dumps(items))
        window.setProperty(NEXTUP_CACHE_TS_KEY, str(now))
        _write_file_cache(items)
    except Exception:
        pass
    return items[:limit]
