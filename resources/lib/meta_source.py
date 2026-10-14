# -*- coding: utf-8 -*-
"""Per-provider metadata source.

Each registered provider (Plex, Emby, Plexio, Jellyfin, Cinemeta, Torrentio,
etc.) can have its own metadata override target. The mapping is stored as a
single JSON blob in `provider_meta_map` setting:

    {
      "plexio_abc123":  "cinemeta_xyz",    # provider_id → meta source id
      "plex_def456":    "tmdb_helper",
      "anime_provider": "kitsu_mno"
    }

Values can be:
  - A provider_id of another registered Stremio meta-capable addon
  - "tmdb_helper" — TMDb Helper local database only (no Dex Hub TMDb API key required)
  - "auto"   — fill missing facts/art by exact identity across cached sources,
             then TMDb and bounded metadata-provider fallbacks when permitted.
  - "native" — explicitly keep the provider's own metadata, no override.
               When set, the override layer is a no-op for both individual and
               batch calls; the consumer is expected to also respect it when
               picking artwork (see _resolve_meta_art in plugin.py).

`list_available_meta_sources()` populates the picker UI. We auto-register
EVERY addon that declares `meta` as a resource — the previous narrow whitelist
("only addons whose name contains cinemeta/tmdb/...") missed addons like
AIOMetadata, Anime Catalogs, MAL, anidb, etc.
"""
import json
import time

import xbmc
import xbmcaddon

from .dexhub import store as _store
from .dexhub.client import fetch_meta, fetch_meta_cached_only
from .art import extract_ids

# v5.10.67: selected-source cache window + page warm-up. The meta source the
# user picks (AIOMetadata etc.) is authoritative and caches server-side, so
# Dex Hub keeps its /meta/ answers for days instead of the 1-4h generic
# window, and fills a page's cache misses in one bounded parallel pass
# instead of one item at a time on selection.
_SOURCE_TTL_DEFAULT_DAYS = 7
_WARM_MAX_ITEMS = 48
_WARM_BUDGET_SECONDS = 4.0
_WARM_ITEM_TIMEOUT = 3
_WARM_NEGATIVE_TTL = 3600
_WARM_NEGATIVE_MAX = 2000
# Own-server sources answer /meta/ in tens of milliseconds; the default
# polite bucket (10 burst, 5/s) would stretch a 48-item warm-up past the
# budget. Applied only to the host of the SELECTED source.
_SOURCE_HOST_POLICY = (12, 8.0)
_WARM_NEGATIVE = {}
_WARM_POLICY_HOSTS = set()

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()


# Module-level cache of the provider_meta_map setting.
# Was being re-parsed (getSetting + json.loads) on every meta lookup —
# 60-item catalogs paid for it 60 times. Now parsed once per invocation,
# invalidated whenever _save_map() runs (user changed the picker).
# (3.8.12)
_MAP_CACHE = None


def _invalidate_map_cache():
    global _MAP_CACHE
    _MAP_CACHE = None


def _load_map():
    global _MAP_CACHE
    if _MAP_CACHE is not None:
        return _MAP_CACHE
    try:
        raw = ADDON.getSetting('provider_meta_map') or '{}'
        data = json.loads(raw)
        _MAP_CACHE = data if isinstance(data, dict) else {}
    except Exception:
        _MAP_CACHE = {}
    return _MAP_CACHE


def _save_map(mapping):
    global _MAP_CACHE
    try:
        ADDON.setSetting('provider_meta_map', json.dumps(mapping or {}, ensure_ascii=False))
        _MAP_CACHE = dict(mapping or {})
    except Exception:
        _invalidate_map_cache()


def _default_source_id_from_setting():
    """Read the user's default meta source setting and translate the
    labelenum index into the internal id ('auto', 'tmdb_helper', 'native')."""
    try:
        selected = (ADDON.getSetting('global_meta_source_id') or '').strip()
        if selected:
            return selected
        raw = (ADDON.getSetting('default_meta_source') or '').strip()
    except Exception:
        raw = ''
    # Kodi labelenum returns the visible label by default; map it.
    low = raw.lower()
    if 'tmdb helper' in low or 'tmdb_helper' in low or low == '1':
        return 'tmdb_helper'
    if 'native' in low or 'الأصلي' in low or 'اصلي' in low or low == '2':
        return 'native'
    return 'auto'


def get_explicit_meta_source_for(provider_id):
    """Unified selection (including Auto), otherwise a per-target choice.

    Without a unified selection, Nuvio keeps its own profile preferences.
    """
    try:
        if ADDON.getSetting('global_meta_source_all') == 'true':
            return _default_source_id_from_setting()
        return str((_load_map().get(provider_id or '') or '')).strip()
    except Exception:
        return ''


def get_meta_source_for(provider_id):
    """Return the configured meta source id for a given provider.

    Resolution order:
      1) per-bucket override for the currently-rendering bucket
         (set via the home bucket context menu in plugin.py)
      2) explicit per-provider mapping in `provider_meta_map`
      3) global default from the `default_meta_source` setting
      4) 'auto' if neither is set
    """
    # A unified selection masks overrides without deleting them. Turning the
    # switch off restores per-section choices, with this source as default.
    if ADDON.getSetting('global_meta_source_all') == 'true':
        return _default_source_id_from_setting()
    # v3.9.48: consult per-bucket override first. This is read via a
    # callback into plugin.py to avoid an import cycle. The callback
    # returns '' when no bucket is currently active or no override is
    # set, in which case we fall through to the existing logic.
    try:
        from .runtime_binding import current
        _plugin = current(required=False)
        bucket = _plugin._get_active_bucket() if _plugin is not None else ''
        if bucket:
            bucket_override = _plugin.get_bucket_meta_source(bucket)
            if bucket_override and bucket_override != 'auto':
                return bucket_override
    except Exception:
        pass
    explicit = (_load_map().get(provider_id or '') or '').strip()
    if explicit and explicit != 'auto':
        return explicit
    return _default_source_id_from_setting()


def set_meta_source_for(provider_id, source_id):
    if not provider_id:
        return
    mapping = _load_map()
    if not source_id or source_id == 'auto':
        mapping.pop(provider_id, None)
    else:
        mapping[provider_id] = source_id
    _save_map(mapping)
    # Drop the on-disk + in-memory HTTP cache for /meta/ URLs so the very
    # next catalog/detail render actually reflects the new mapping. Without
    # this, the user would have to wait for the meta TTL to expire (often
    # 6h+) before the change took visible effect — what users perceived as
    # "the meta source picker doesn't work".
    try:
        from .dexhub.client import purge_meta_cache as _purge
        _purge()
    except Exception:
        pass


def clear_meta_map():
    _save_map({})
    try:
        from .dexhub.client import purge_meta_cache as _purge
        _purge()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# v3.9.30: per-bucket art priority.
#
# Sits next to the existing provider_meta_map (per-provider meta source).
# Where provider_meta_map answers "for items from Plex, fetch metadata
# from Cinemeta", this answers "for items in the Anime bucket, prefer
# poster art from Kitsu, then AniList, then fall back to default".
#
# Stored shape (in setting `bucket_art_map`):
#   {
#     "anime":  {"poster": ["kitsu_xxx", "anilist_yyy"],
#                "background": ["fanart_zzz"]},
#     "movies": {"poster": ["cinemeta_aaa"]}
#   }
#
# Art types supported: poster, background, clearlogo. Anything missing
# from the map → caller uses the existing _resolve_meta_art strategy.
# Empty list for an art type → also fall through.
#
# Why JSON-in-setting rather than a separate file: matches the rest of
# DexHub's hidden-state pattern (providers_json, catalog_state_json,
# provider_meta_map). Settings round-trip is reliable across Kodi
# restarts and survives addon updates.
# ─────────────────────────────────────────────────────────────────────

_BUCKET_ART_CACHE = None

ALLOWED_ART_TYPES = ('poster', 'background', 'clearlogo', 'landscape', 'banner', 'clearart')


def art_configuration_mode():
    """Return unified or custom artwork mode."""
    try:
        raw = (ADDON.getSetting('art_configuration_mode') or 'Unified').strip().lower()
    except Exception:
        raw = 'unified'
    return 'custom' if raw in ('custom', 'تخصيص') else 'unified'




# Global artwork source priorities. These apply to every item in Dex Hub,
# then a per-folder override may replace them. Stored separately from the
# per-folder map so exporting/importing settings remains predictable.
_GLOBAL_ART_CACHE = None

def _invalidate_global_art_cache():
    global _GLOBAL_ART_CACHE
    _GLOBAL_ART_CACHE = None

def _load_global_art_map():
    global _GLOBAL_ART_CACHE
    if _GLOBAL_ART_CACHE is not None:
        return _GLOBAL_ART_CACHE
    try:
        raw = ADDON.getSetting('global_art_map') or '{}'
        data = json.loads(raw)
        clean = {}
        if isinstance(data, dict):
            for art_type, pids in data.items():
                if art_type in ALLOWED_ART_TYPES and isinstance(pids, list):
                    clean[art_type] = [str(p) for p in pids if p]
        _GLOBAL_ART_CACHE = clean
    except Exception:
        _GLOBAL_ART_CACHE = {}
    return _GLOBAL_ART_CACHE

def _save_global_art_map(mapping):
    global _GLOBAL_ART_CACHE
    try:
        clean = {}
        for art_type, pids in (mapping or {}).items():
            if art_type in ALLOWED_ART_TYPES and isinstance(pids, list) and pids:
                clean[art_type] = [str(p) for p in pids if p]
        ADDON.setSetting('global_art_map', json.dumps(clean, ensure_ascii=False))
        _GLOBAL_ART_CACHE = clean
    except Exception:
        _invalidate_global_art_cache()

def get_global_art_priority(art_type):
    if art_type not in ALLOWED_ART_TYPES:
        return []
    return list(_load_global_art_map().get(art_type) or [])

def set_global_art_priority(art_type, provider_ids):
    if art_type not in ALLOWED_ART_TYPES:
        return
    mapping = _load_global_art_map()
    if provider_ids:
        mapping[art_type] = [str(p) for p in provider_ids if p]
    else:
        mapping.pop(art_type, None)
    _save_global_art_map(mapping)
    try:
        from .dexhub.client import purge_meta_cache as _purge
        _purge()
    except Exception:
        pass

def clear_global_art():
    _save_global_art_map({})
    try:
        from .dexhub.client import purge_meta_cache as _purge
        _purge()
    except Exception:
        pass


def _invalidate_bucket_art_cache():
    global _BUCKET_ART_CACHE
    _BUCKET_ART_CACHE = None


def _load_bucket_art_map():
    global _BUCKET_ART_CACHE
    if _BUCKET_ART_CACHE is not None:
        return _BUCKET_ART_CACHE
    try:
        raw = ADDON.getSetting('bucket_art_map') or '{}'
        data = json.loads(raw)
        # Defensive: validate shape so a corrupted setting doesn't crash
        # callers downstream. Coerce non-list priorities into [] and drop
        # unknown art types.
        clean = {}
        if isinstance(data, dict):
            for bucket, types in data.items():
                if not isinstance(types, dict):
                    continue
                clean_types = {}
                for art_type, pids in types.items():
                    if art_type not in ALLOWED_ART_TYPES:
                        continue
                    if isinstance(pids, list):
                        clean_types[art_type] = [str(p) for p in pids if p]
                if clean_types:
                    clean[str(bucket)] = clean_types
        _BUCKET_ART_CACHE = clean
    except Exception:
        _BUCKET_ART_CACHE = {}
    return _BUCKET_ART_CACHE


def _save_bucket_art_map(mapping):
    global _BUCKET_ART_CACHE
    try:
        ADDON.setSetting('bucket_art_map',
                          json.dumps(mapping or {}, ensure_ascii=False))
        _BUCKET_ART_CACHE = dict(mapping or {})
    except Exception:
        _invalidate_bucket_art_cache()


def get_bucket_art_priority(bucket, art_type):
    """Return the ordered provider_id priority list for (bucket, art_type),
    or [] if no priority is configured. Empty list = caller should fall
    through to the default art-resolution strategy."""
    if not bucket or art_type not in ALLOWED_ART_TYPES:
        return []
    bucket_cfg = _load_bucket_art_map().get(str(bucket)) or {}
    return list(bucket_cfg.get(art_type) or [])


def set_bucket_art_priority(bucket, art_type, provider_ids):
    """Persist a new priority list for (bucket, art_type). An empty list
    clears the entry. Passing provider_ids=None also clears."""
    if not bucket or art_type not in ALLOWED_ART_TYPES:
        return
    mapping = _load_bucket_art_map()
    bucket_key = str(bucket)
    bucket_cfg = mapping.get(bucket_key) or {}
    if not provider_ids:
        bucket_cfg.pop(art_type, None)
        if not bucket_cfg:
            mapping.pop(bucket_key, None)
        else:
            mapping[bucket_key] = bucket_cfg
    else:
        bucket_cfg[art_type] = [str(p) for p in provider_ids if p]
        mapping[bucket_key] = bucket_cfg
    _save_bucket_art_map(mapping)
    # Purge meta cache so the next render uses the new priority.
    try:
        from .dexhub.client import purge_meta_cache as _purge
        _purge()
    except Exception:
        pass


def clear_bucket_art_for(bucket):
    """Remove all priorities for one bucket (poster + background + clearlogo)."""
    if not bucket:
        return
    mapping = _load_bucket_art_map()
    if str(bucket) in mapping:
        mapping.pop(str(bucket), None)
        _save_bucket_art_map(mapping)
        try:
            from .dexhub.client import purge_meta_cache as _purge
            _purge()
        except Exception:
            pass


def _provider_supports_meta(provider):
    manifest = (provider or {}).get('manifest') or {}
    for resource in manifest.get('resources', []):
        if resource == 'meta':
            return True
        if isinstance(resource, dict) and resource.get('name') == 'meta':
            return True
    return False


# --- Stream-only detection (Plex/Emby/Plexio/Jellyfin/DexBridge) --------
# These providers have private-token poster URLs that Kodi can't reliably
# load, so 'auto' meta-source mode auto-replaces them with remote art.
_STREAM_ONLY_HINTS = ('plex', 'plexio', 'emby', 'jellyfin', 'dexbridge')

# Negative hints for meta-source picker — these addons CAN report meta but
# the user almost never wants them as the *primary* metadata source for
# OTHER addons. They still get listed if the user hasn't disabled them.
_META_PICKER_BLOCKLIST = ('torrentio', 'comet', 'mediafusion', 'watchhub',
                          'opensubtitles', 'orion', 'debrid', 'real-debrid',
                          'realdebrid', 'rd:', 'aiostreams')


def _provider_haystack(provider):
    manifest = (provider or {}).get('manifest') or {}
    return ' '.join([
        str((provider or {}).get('name') or ''),
        str((provider or {}).get('id') or ''),
        str((provider or {}).get('base_url') or ''),
        str((provider or {}).get('manifest_url') or ''),
        str(manifest.get('name') or ''),
        str(manifest.get('id') or ''),
        str(manifest.get('description') or ''),
    ]).lower()


def _provider_is_plexio(provider):
    haystack = _provider_haystack(provider)
    return any(hint in haystack for hint in (
        'plexio', 'plexbridge', 'com.stremio.plexio', 'com.stremio.plexbridge',
        'plexio.dexworld.cc'
    ))


def _provider_looks_like_meta_source(provider):
    """Permissive check: any provider that DECLARES `meta` is eligible for the
    picker, except the well-known stream-only providers and known stream
    aggregators. This is the fix for: 'when adding any tool that supports
    metadata it should auto-appear in the metadata picker'.
    """
    if not _provider_supports_meta(provider):
        return False
    # Descriptions often mention supported stream services (e.g. debrid).
    # That must not hide an actual metadata addon from the picker.
    manifest = (provider or {}).get('manifest') or {}
    haystack = ' '.join(str(v or '') for v in (
        provider.get('name'), manifest.get('name'), manifest.get('id'))).lower()
    if any(hint in haystack for hint in _STREAM_ONLY_HINTS):
        return False
    if any(hint in haystack for hint in _META_PICKER_BLOCKLIST):
        return False
    return True


def provider_is_stream_only(provider):
    """True if provider is best known for streams/catalogs, unreliable meta."""
    return any(h in _provider_haystack(provider) for h in _STREAM_ONLY_HINTS)


def list_available_meta_sources():
    """Return the list of choosable meta sources for the picker."""
    sources = [
        {'id': 'auto', 'name': 'تلقائي (ذكي)', 'kind': 'special'},
        {'id': 'native', 'name': 'ميتاداتا الإضافة نفسها', 'kind': 'special'},
        {'id': 'tmdb_helper', 'name': 'TMDb Helper (بدون API)', 'kind': 'builtin'},
    ]
    seen = set()
    for row in _store.list_providers() or []:
        if not _provider_looks_like_meta_source(row):
            continue
        pid = row.get('id') or ''
        if not pid or pid in seen:
            continue
        seen.add(pid)
        sources.append({
            'id': pid,
            'name': row.get('name') or pid,
            'kind': 'stremio',
        })
    return sources


def source_display_name(source_id):
    """Human label for a source id, used in menus."""
    if not source_id or source_id == 'auto':
        return 'Automatic'
    if source_id == 'native':
        return 'Addon metadata'
    if source_id == 'tmdb_helper':
        return 'TMDb Helper'
    prov = _store.get_provider(source_id)
    if prov:
        return prov.get('name') or source_id
    return source_id


def _candidate_ids(ids):
    imdb = ids.get('imdb_id') or ''
    tmdb = ids.get('tmdb_id') or ''
    tvdb = ids.get('tvdb_id') or ''
    candidates = []
    if imdb:
        candidates.append(imdb if imdb.startswith('tt') else 'tt%s' % imdb)
    if tmdb:
        candidates.append('tmdb:%s' % tmdb)
    if tvdb:
        candidates.append('tvdb:%s' % tvdb)
    return candidates


def selected_source_ttl_seconds():
    """Local cache window for /meta/ answers of a SELECTED meta source.

    Setting `meta_source_cache_days` (1..60, default 7). The generic
    `meta_cache_ttl` (max 4h) still governs every other /meta/ consumer.
    """
    try:
        days = int(float(ADDON.getSetting('meta_source_cache_days') or _SOURCE_TTL_DEFAULT_DAYS))
    except Exception:
        days = _SOURCE_TTL_DEFAULT_DAYS
    return max(1, min(days, 60)) * 86400


def _stremio_media_type(media_type):
    return 'series' if media_type in ('series', 'anime', 'tv', 'show') else 'movie'


def source_is_provider_itself(provider, source_id):
    """True when the selected meta source IS the provider that produced the
    items. Its catalog/meta payload is already the selected metadata, so an
    override pass would only re-request /meta/ for every card."""
    pid = str((provider or {}).get('id') or '').strip()
    return bool(pid) and str(source_id or '').strip() == pid


def _mark_own_source(meta, source_id, provider_id):
    # Strict: the source's own artwork must reach the skin untouched (no
    # BetterPosters swap, no metahub "clean poster" rewrite, no TMDb/Helper
    # enrichment), exactly like an item the source replaced explicitly.
    return _mark_source(meta, source_id=source_id, provider_id=provider_id, strict_art=True)


def _apply_source_host_policy(provider):
    """Relax the per-host limiter for the selected source's own host."""
    try:
        from .ratelimit import limiter, host_of
        host = host_of(str((provider or {}).get('manifest_url') or (provider or {}).get('base_url') or ''))
        if host and host not in _WARM_POLICY_HOSTS:
            limiter.set_policy(host, *_SOURCE_HOST_POLICY)
            _WARM_POLICY_HOSTS.add(host)
    except Exception:
        pass


def _warm_negative_get(key):
    try:
        until = _WARM_NEGATIVE.get(key)
        if until and until > time.time():
            return True
        if until:
            _WARM_NEGATIVE.pop(key, None)
    except Exception:
        pass
    return False


def _warm_negative_put(key):
    try:
        if len(_WARM_NEGATIVE) >= _WARM_NEGATIVE_MAX:
            _WARM_NEGATIVE.clear()
        _WARM_NEGATIVE[key] = time.time() + _WARM_NEGATIVE_TTL
    except Exception:
        pass


def warm_pages_enabled():
    try:
        raw = ADDON.getSetting('meta_source_warm_pages')
    except Exception:
        raw = ''
    if raw in (None, ''):
        return True
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _warm_allowed_here():
    """Warm only for a directory the user opened (Videos, Dex Hub's own
    windows), never while the Home window is active: its widgets render
    often, in the background, and must stay network-free."""
    if not warm_pages_enabled():
        return False
    try:
        import xbmcgui
        return int(xbmcgui.getCurrentWindowId()) != 10000
    except Exception:
        return False


def warm_source_metas(source_id, media_type, metas, budget_seconds=None, max_items=None):
    """Prefetch /meta/ from the selected Stremio meta source for the items of
    one page whose answer is not cached yet.

    Bounded (max_items, budget), parallel (browse lane), best-effort: a
    timeout simply leaves the remaining items for the next visit. Returns the
    number of items requested.
    """
    provider = _store.get_provider(source_id) if source_id else None
    if not provider or not metas:
        return 0
    stremio_mt = _stremio_media_type(media_type)
    ttl = selected_source_ttl_seconds()
    limit = int(max_items or _WARM_MAX_ITEMS)
    targets = []
    seen = set()
    for meta in metas:
        try:
            candidates = _candidate_ids(extract_ids(meta or {}))
        except Exception:
            candidates = []
        if not candidates:
            continue
        # Any cached answer for any candidate (even an empty meta) means the
        # cache-only override pass already has what it needs.
        if any(fetch_meta_cached_only(provider, stremio_mt, item_id, ttl_override=ttl)
               for item_id in candidates):
            continue
        item_id = candidates[0]
        key = '%s|%s|%s' % (provider.get('id') or '', stremio_mt, item_id)
        if item_id in seen or _warm_negative_get(key):
            continue
        seen.add(item_id)
        targets.append(item_id)
        if len(targets) >= limit:
            break
    if not targets:
        return 0
    _apply_source_host_policy(provider)

    def _one(item_id):
        key = '%s|%s|%s' % (provider.get('id') or '', stremio_mt, item_id)
        try:
            data = fetch_meta(provider, stremio_mt, item_id,
                              timeout_override=_WARM_ITEM_TIMEOUT, retry=False,
                              rate_wait=0.25, ttl_override=ttl)
            meta = data.get('meta') if isinstance(data, dict) else None
            if not meta:
                # Unknown to the source: do not ask again for an hour.
                _warm_negative_put(key)
            return bool(meta)
        except Exception as exc:
            _warm_negative_put(key)
            xbmc.log('[DexHub] meta warm-up %s:%s failed: %s' % (
                provider.get('name'), item_id, exc), xbmc.LOGDEBUG)
            return False

    try:
        from .dexhub.client import run_parallel
        started = time.time()
        run_parallel(_one, targets, workers=8,
                     timeout=float(budget_seconds or _WARM_BUDGET_SECONDS),
                     lane='browse')
        xbmc.log('[DexHub] meta warm-up: %d item(s) from %s in %.2fs' % (
            len(targets), provider.get('name') or source_id, time.time() - started), xbmc.LOGDEBUG)
    except Exception as exc:
        xbmc.log('[DexHub] meta warm-up aborted: %s' % exc, xbmc.LOGDEBUG)
    return len(targets)


def _fetch_from_stremio(meta_provider_id, media_type, ids, cache_only=False, max_candidates=None):
    provider = _store.get_provider(meta_provider_id) if meta_provider_id else None
    if not provider:
        return None
    stremio_mt = _stremio_media_type(media_type)
    ttl = selected_source_ttl_seconds()
    for item_id in _candidate_ids(ids)[:max_candidates]:
        try:
            if cache_only:
                data = fetch_meta_cached_only(
                    provider, stremio_mt, item_id, ttl_override=ttl)
            else:
                data = fetch_meta(
                    provider, stremio_mt, item_id,
                    timeout_override=4, retry=False, rate_wait=0.5,
                    ttl_override=ttl)
            meta = data.get('meta') or {}
            if meta and any(meta.get(k) for k in ('poster', 'background', 'description', 'overview',
                                                 'plot', 'genres', 'cast', 'rating', 'imdbRating', 'runtime')):
                return meta
        except Exception as exc:
            xbmc.log('[DexHub] meta fetch %s:%s failed: %s' % (
                provider.get('name'), item_id, exc), xbmc.LOGDEBUG)
            continue
    return None


def _fetch_from_tmdb_helper(ids, meta, media_type, cache_only=False):
    """Pull rich metadata from TMDb Helper.

    v3.9.122: choosing TMDb Helper as a collection/provider metadata source
    should make the *entire* item look like TMDb Helper, not just swap the
    poster. Read title/plot/year/genres/studios/cast/ratings/art from the
    helper local database when available. Explicit Helper stays cache-only;
    Auto owns the independent TMDb fallback.
    """
    is_tv = media_type in ('series', 'anime', 'show', 'tv')
    mt_norm = 'tv' if is_tv else 'movie'
    tmdb_id = str(ids.get('tmdb_id') or '').strip()
    imdb_id = str(ids.get('imdb_id') or '').strip()
    tvdb_id = str(ids.get('tvdb_id') or '').strip()
    title = str(meta.get('name') or meta.get('title') or '').strip()
    year = str(meta.get('releaseInfo') or meta.get('year') or '').strip()

    try:
        from . import tmdbhelper as _tmdbh
        db_meta = _tmdbh.get_meta_bundle_from_db(
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
            media_type=mt_norm,
            title=title,
            year=year,
        ) or {}
    except Exception:
        db_meta = {}

    if db_meta and any(db_meta.get(k) for k in ('poster', 'background', 'description', 'genres', 'cast', 'clearlogo', 'studio')):
        return db_meta

    # Helper means Helper's cache, never the independent TMDb API/language.
    return db_meta or None

def _meta_is_good_enough(meta):
    from .auto_metadata import complete
    return complete(meta)


_ART_OVERRIDE_KEYS = (
    'poster', 'posterUrl', 'posterURL', 'thumbnail', 'thumb', 'thumbUrl', 'thumbURL',
    'image', 'imageUrl', 'imageURL', 'background', 'fanart', 'landscape', 'banner',
    'backdrop', 'logo', 'clearlogo', 'clearart', 'plex_original_poster',
    'parentThumb', 'grandparentThumb'
)


def _drop_art_fields(meta):
    """Remove artwork from a base meta object before applying an explicit source.

    Without this, choosing a metadata source can still leak artwork from the
    previous/provider meta through nested `images` or `behaviorHints` fields.
    """
    out = dict(meta or {})
    for key in _ART_OVERRIDE_KEYS:
        out.pop(key, None)
    images = out.get('images')
    if isinstance(images, dict):
        new_images = dict(images)
        for key in list(new_images.keys()):
            if str(key).lower() in (
                'poster', 'thumb', 'thumbnail', 'cover', 'image', 'posterurl', 'poster_url',
                'background', 'fanart', 'backdrop', 'landscape', 'banner', 'art',
                'clearlogo', 'clearart', 'logo'
            ):
                new_images.pop(key, None)
        if new_images:
            out['images'] = new_images
        else:
            out.pop('images', None)
    behavior = out.get('behaviorHints')
    if isinstance(behavior, dict):
        new_behavior = dict(behavior)
        for key in _ART_OVERRIDE_KEYS:
            new_behavior.pop(key, None)
        if new_behavior:
            out['behaviorHints'] = new_behavior
        else:
            out.pop('behaviorHints', None)
    return out


def _mark_source(meta, source_id='', provider_id='', strict_art=False, virtual_key=''):
    """Stamp meta so downstream artwork code can respect the user's picker.

    Kodi listing/detail/playback flows may re-score or re-cache metadata after
    the catalog page. These private fields let plugin.py know that the artwork
    already came from a selected source and must not be enriched/fallen back to
    another source later.
    """
    out = dict(meta or {})
    if source_id:
        out['_dexhub_meta_source_id'] = str(source_id or '')
    if provider_id:
        out['_dexhub_meta_provider_id'] = str(provider_id or '')
    if virtual_key:
        out['_dexhub_meta_virtual_key'] = str(virtual_key or '')
    if strict_art:
        out['_dexhub_art_strict'] = '1'
    return out


def _merge_meta(base_meta, replacement, strict_art=False, source_id='', provider_id='', virtual_key=''):
    # Preserve usable artwork on a cache miss. The final renderer prefers the
    # selected source, then Helper cache, then this existing image.
    old_art = {key: value for key, value in (base_meta or {}).items()
               if key in ('poster', 'background', 'fanart', 'clearlogo', 'logo', 'thumb', 'thumbnail') and value}
    if not replacement:
        return _mark_source(base_meta, source_id=source_id, provider_id=provider_id,
                            strict_art=False, virtual_key=virtual_key)
    if strict_art:
        base_meta = dict(base_meta or {})
        for key in ('description', 'overview', 'plot', 'plotoutline', 'tagline',
                    'genres', 'genre', 'releaseInfo', 'released', 'premiered', 'year',
                    'imdbRating', 'rating', 'votes', 'cast', 'credits_cast', 'director',
                    'writer', 'country', 'certification', 'mpaa', 'studio', 'studios',
                    'network', 'networks', 'originaltitle', 'trailer'):
            base_meta.pop(key, None)
    if not replacement:
        merged = _drop_art_fields(base_meta) if strict_art else dict(base_meta or {})
        return _mark_source(merged, source_id=source_id, provider_id=provider_id, strict_art=strict_art, virtual_key=virtual_key)
    merged = _drop_art_fields(base_meta) if strict_art else dict(base_meta or {})
    for key in ('poster', 'posterUrl', 'posterURL', 'thumbnail', 'thumb', 'image', 'background', 'fanart', 'landscape', 'backdrop', 'banner',
                'logo', 'clearlogo', 'clearart', 'description', 'overview', 'plot', 'plotoutline', 'tagline',
                'genres', 'genre', 'releaseInfo', 'released', 'premiered', 'firstaired', 'date', 'year',
                'imdbRating', 'rating', 'voteAverage', 'score', 'imdb_votes', 'votes', 'userrating',
                'cast', 'credits_cast', 'director', 'writer', 'runtime', 'duration', 'country',
                'certification', 'mpaa', 'studio', 'studios', 'network', 'networks', 'production_companies',
                'originaltitle', 'trailer', 'status', 'type', 'tmdb_id', 'imdb_id', 'tvdb_id', 'id',
                'images', 'behaviorHints', 'name', 'title', 'videos', 'trailers', 'links'):
        val = replacement.get(key)
        if val not in (None, '', [], {}):
            # Clearlogo / logo: never overwrite a good existing value with
            # something empty — the user sees the clearlogo change every time
            # they switch meta sources, which feels broken. Only update it
            # if the replacement actually has a richer value.
            if key in ('clearlogo', 'logo') and merged.get(key) and not val:
                continue
            merged[key] = val
    for key, value in old_art.items():
        if not merged.get(key):
            merged[key] = value
    # v5.10.67: AIOMetadata/tmdb-addon carry their ids in private fields;
    # surface them as the standard keys so TMDb Helper handoff, Trakt and
    # source lookups keep working after the override.
    for src_key, dst_key in (('_tmdbId', 'tmdb_id'), ('moviedb_id', 'tmdb_id'),
                             ('_tvdbId', 'tvdb_id'), ('_imdbId', 'imdb_id')):
        val = replacement.get(src_key)
        if val not in (None, '', 0, '0') and not merged.get(dst_key):
            merged[dst_key] = str(val)
    return _mark_source(merged, source_id=source_id, provider_id=provider_id, strict_art=strict_art, virtual_key=virtual_key)



def override_virtual_meta(target_key, media_type, meta, cache_only=False):
    """Apply a metadata override for non-provider targets (Continue Watching,
    Trakt collection entries) where there is no concrete Stremio provider row
    to attach the mapping to."""
    if not target_key:
        return meta
    meta = dict(meta or {})
    ids = extract_ids(meta)
    if not any(ids.values()):
        return meta

    source_id = get_meta_source_for(target_key)
    if source_id == 'native':
        return _mark_source(meta, source_id='native', strict_art=True, virtual_key=target_key)

    replacement = None
    if source_id in ('', 'auto'):
        from .auto_metadata import enrich
        return _mark_source(enrich(meta, media_type, cache_only=cache_only),
                            source_id='auto', virtual_key=target_key)
    elif source_id == 'tmdb_helper':
        replacement = _fetch_from_tmdb_helper(
            ids, meta, media_type, cache_only=cache_only)
    else:
        # Explicit source means explicit source: do not silently fall back to
        # TMDb Helper/API when the selected meta add-on has no artwork.
        replacement = _fetch_from_stremio(
            source_id, media_type, ids, cache_only=cache_only)

    if cache_only and not replacement and source_id in ('', 'auto'):
        # A cold directory cache must never turn the source/Nuvio poster into
        # a blank tile. Keep the seed for this render; item details can warm
        # the explicitly selected source for the next visit.
        return _mark_source(
            meta, source_id=source_id, strict_art=False,
            virtual_key=target_key)
    explicit = source_id not in ('', 'auto')
    return _merge_meta(meta, replacement, strict_art=explicit, source_id=source_id, virtual_key=target_key)


def override_virtual_metas_batch(target_key, media_type, metas, warm=False):
    """Apply a virtual override to a directory using local caches only.

    ``warm=True`` (catalog pages the user opened, never widgets) first fills
    the cache misses of this page from the selected Stremio source in one
    bounded parallel pass, so the first render already shows that source.
    """
    if warm and metas:
        source_id = get_meta_source_for(target_key)
        if source_id in ('', 'auto') and _warm_allowed_here():
            from .auto_metadata import warm as warm_auto
            warm_auto(metas, media_type)
        elif source_id not in ('', 'auto', 'native', 'tmdb_helper') and _warm_allowed_here():
            try:
                warm_source_metas(source_id, media_type, metas)
            except Exception:
                pass
    return [
        override_virtual_meta(
            target_key, media_type, meta, cache_only=True)
        for meta in (metas or [])
    ]


def override_meta(provider, media_type, meta, cache_only=False):
    """Apply per-provider metadata override.

    'native' is treated as a STRICT no-op — the provider's own meta is
    returned untouched, even if it looks sparse. This is what 'احترام
    اختيار المستخدم' means for users that explicitly prefer Plexio/DexBridge
    meta because their setup already has rich local artwork.
    """
    if not provider:
        return meta
    source_id = get_meta_source_for(provider.get('id'))
    if source_id == 'native':
        return _mark_source(meta, source_id='native', provider_id=(provider.get('id') or ''), strict_art=(source_id == 'native'))
    if source_is_provider_itself(provider, source_id):
        # The selected source produced this item: its payload IS the
        # selected metadata. No /meta/ round-trip, no art re-resolution.
        return _mark_own_source(meta, source_id, provider.get('id') or '')

    ids = extract_ids(meta)
    if not any(ids.values()):
        return meta

    if source_id in ('', 'auto'):
        from .auto_metadata import enrich
        return _mark_source(enrich(meta, media_type, cache_only=cache_only,
                                   provider_id=provider.get('id') or ''),
                            source_id='auto', provider_id=provider.get('id') or '')
    elif source_id == 'tmdb_helper':
        replacement = _fetch_from_tmdb_helper(
            ids, meta, media_type, cache_only=cache_only)
    else:
        # Explicit source means explicit source: do not silently fall back to
        # TMDb Helper/API when the selected meta add-on has no artwork.
        replacement = _fetch_from_stremio(
            source_id, media_type, ids, cache_only=cache_only)

    explicit = source_id not in ('', 'auto')
    if cache_only and not replacement and not explicit:
        return _mark_source(
            meta, source_id=source_id,
            provider_id=provider.get('id') or '', strict_art=False)
    if not replacement:
        # Explicit source means strict source: if the selected source has no
        # artwork/meta for this item, do not leak the provider's old poster.
        return _merge_meta(meta, None, strict_art=explicit, source_id=source_id, provider_id=provider.get('id') or '')
    return _merge_meta(meta, replacement, strict_art=explicit, source_id=source_id, provider_id=provider.get('id') or '')


def override_metas_batch(provider, media_type, metas, warm=False):
    if not provider or not metas:
        return metas
    source_id = get_meta_source_for(provider.get('id'))
    if source_id == 'native':
        return [_mark_source(m, source_id='native', provider_id=(provider.get('id') or ''), strict_art=(source_id == 'native')) for m in (metas or [])]
    if source_is_provider_itself(provider, source_id):
        # v5.10.67: the source's own catalogs already carry the selected
        # metadata and artwork — zero per-item work.
        pid = provider.get('id') or ''
        return [_mark_own_source(m, source_id, pid) for m in (metas or [])]
    if warm and source_id in ('', 'auto') and _warm_allowed_here():
        from .auto_metadata import warm as warm_auto
        warm_auto(metas, media_type, provider_id=provider.get('id') or '')
    # v5.10.67: for an explicit Stremio source, fill this page's cache misses
    # in one bounded parallel pass before the cache-only override below.
    if warm and source_id not in ('', 'auto', 'native', 'tmdb_helper') and _warm_allowed_here():
        try:
            warm_source_metas(source_id, media_type, metas)
        except Exception:
            pass

    def _one(m):
        try:
            return override_meta(
                provider, media_type, m, cache_only=True)
        except Exception:
            return m

    # Directory overrides are cache-only, so a small sequential pass is both
    # faster and lighter than allocating futures or contending on local DBs.
    return [_one(meta) for meta in (metas or [])]
