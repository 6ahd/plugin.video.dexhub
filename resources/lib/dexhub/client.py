# -*- coding: utf-8 -*-
import gzip
import hashlib
import io
import json
import os
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor, as_completed, TimeoutError,
    wait as _future_wait, FIRST_COMPLETED,
)
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlunparse
# v5.4.7: urllib.request is imported lazily at the call sites below.
# It drags in http.client/email (~35ms x86, ~120-150ms ARM32) and was
# paid on EVERY invoker cold start before the first byte of UI rendered.


# ─────────────────────────────────────────────────────────────────────
# v3.9.29: HTTP connection pool via urllib3 (if available).
#
# Python's urllib.request opens a fresh TCP+TLS connection for every
# urlopen() call. TLS handshake costs ~150-300ms per request, repeated
# for every Cinemeta/Torrentio/whatever fetch. urllib3's PoolManager
# keeps connections alive per host, eliminating that overhead on
# subsequent requests.
#
# Try-import with graceful fallback: if urllib3 isn't bundled with the
# user's Kodi Python (older installs), we silently use urllib and lose
# only the keep-alive bonus. No functional regression.
# ─────────────────────────────────────────────────────────────────────
# v5.4.7: pool construction is deferred to the first real HTTP call.
# Importing urllib3 at module scope cost ~45ms x86 / ~150-200ms ARM32 on
# every invoker cold start — including renders served entirely from
# meta_cache that never touch the network. _HAS_URLLIB3 is tri-state:
# None = not attempted yet, True/False = probe result (frozen after).
_POOL_MGR = None
_HAS_URLLIB3 = None
_URLLIB3 = None
_POOL_INIT_LOCK = threading.Lock()


def _pool_enabled():
    """Hidden kill switch for the keep-alive pool (v5.10.89).

    urllib3 became importable in 5.10.86 and this pool ran on the device for
    the first time; 5.10.88 routes Emby and Silo through it as well. If a
    device misbehaves only since then, http_keepalive_pool=false restores the
    plain urllib path everywhere without touching any other setting.
    """
    try:
        import xbmcaddon
        return (xbmcaddon.Addon().getSetting('http_keepalive_pool') or 'true').strip().lower() != 'false'
    except Exception:
        return True


def _ensure_pool():
    if not _pool_enabled():
        return None
    """Import urllib3 and build the PoolManager on first use. Idempotent,
    thread-safe, never raises. Returns the pool or None (urllib fallback)."""
    global _POOL_MGR, _HAS_URLLIB3, _URLLIB3
    if _HAS_URLLIB3 is not None:
        return _POOL_MGR
    with _POOL_INIT_LOCK:
        if _HAS_URLLIB3 is not None:
            return _POOL_MGR
        try:
            import urllib3
            # Suppress the InsecureRequestWarning if SSL verification ever
            # fails; we still verify by default, just don't spam the log.
            try:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
            pool = urllib3.PoolManager(
                num_pools=20,        # one pool per host
                maxsize=4,           # connections kept per pool
                retries=False,       # we have our own retry logic in _retry_open
                timeout=urllib3.Timeout(connect=10, read=30),
            )
            _URLLIB3 = urllib3
            _POOL_MGR = pool
            _HAS_URLLIB3 = True
        except Exception:
            _POOL_MGR = None
            _HAS_URLLIB3 = False
    return _POOL_MGR

from .common import addon, profile_path

# v3.9.17: use orjson when available — 2-5× faster than stdlib `json` on the
# big catalog responses (200KB+ Stremio catalog payloads). Falls back to
# `json` automatically when orjson isn't installed; the code path is
# identical from the caller's perspective. Most Kodi installs ship CPython
# without third-party packages, so the fallback is the common case; users
# who *do* have orjson (Linux desktop, side-loaded Android, …) get a free
# speedup with no config.
try:
    import orjson as _orjson  # type: ignore[import-not-found]

    def _json_loads_fast(text):
        # orjson is strict about BOMs and whitespace; Stremio addons
        # occasionally emit either, so we strip up front. orjson accepts
        # bytes faster than str.
        if isinstance(text, bytes):
            return _orjson.loads(text.lstrip(b'\xef\xbb\xbf').strip())
        return _orjson.loads(text.lstrip('\ufeff').strip().encode('utf-8'))

    _JSON_BACKEND = 'orjson'
except Exception:
    def _json_loads_fast(text):
        try:
            return json.loads(text)
        except Exception:
            # Stremio addons sometimes emit a UTF-8 BOM or trailing
            # whitespace; recover from those once.
            if isinstance(text, bytes):
                text = text.decode('utf-8', 'replace')
            return json.loads(text.strip().lstrip('\ufeff'))

    _JSON_BACKEND = 'stdlib'

HTTP_CACHE_DIR = os.path.join(profile_path(), 'http_cache')
# Ensure the cache directory exists exactly once at module import time.
# Previously _cache_file() ran os.makedirs on every read/write — a syscall
# per cache hit. With 50-item catalog renders that's 50+ wasted stat calls
# per page. (3.8.12)
try:
    os.makedirs(HTTP_CACHE_DIR, exist_ok=True)
except Exception:
    pass


def _get_setting(key, default):
    try:
        raw = addon().getSetting(key) or ''
        return raw if raw != '' else default
    except Exception:
        return default


# v3.9.17: per-invocation settings cache.
# A single folder render calls _get_int_setting('parallel_workers') etc. dozens
# of times. Each call constructs xbmcaddon.Addon() (Python↔C++ boundary) and
# reads from Kodi's settings store. With reuselanguageinvoker=true the module
# stays warm across invocations, so we *cannot* cache forever; setting changes
# would never propagate. A short TTL (2s) is the right balance: well shorter
# than any human action that follows a settings change, but long enough that
# every read inside one render is O(1) after the first.
_SETTINGS_CACHE = {}
_SETTINGS_CACHE_TS = 0.0
_SETTINGS_CACHE_TTL = 2.0


def _get_setting_cached(key, default):
    global _SETTINGS_CACHE, _SETTINGS_CACHE_TS
    now = time.time()
    if (now - _SETTINGS_CACHE_TS) > _SETTINGS_CACHE_TTL:
        _SETTINGS_CACHE = {}
        _SETTINGS_CACHE_TS = now
    if key in _SETTINGS_CACHE:
        cached = _SETTINGS_CACHE[key]
        return cached if cached != '' else default
    raw = _get_setting(key, '')
    _SETTINGS_CACHE[key] = raw or ''
    return raw if raw not in ('', None) else default


def _get_int_setting_cached(key, default):
    try:
        return max(0, int(float(_get_setting_cached(key, str(default)))))
    except Exception:
        return default


def _get_int_setting(key, default):
    try:
        return max(0, int(float(_get_setting(key, str(default)))))
    except Exception:
        return default


def timeout_seconds():
    return max(5, _get_int_setting_cached('timeout', 20))


def catalog_ttl():
    # v3.9.26: default raised from 120s → 600s. Stremio catalogs are
    # mostly stable (Top Movies, Popular, Trending refresh hourly at most),
    # so 10-minute cache hits cost very little staleness and give 5×
    # more cache hits → noticeably snappier folder navigation.
    return _get_int_setting_cached('catalog_cache_ttl', 600)


def meta_ttl():
    return _get_int_setting_cached('meta_cache_ttl', 3600)


def stream_ttl():
    # v3.9.29: ceiling raised from 15s → 60s. The user backing out of
    # the source picker and re-opening it (very common when trying
    # several streams) hits the cache for a full minute now instead of
    # making 5-10 fresh HTTP requests to every stream addon. Memory-
    # only cache, so it costs nothing on disk.
    return min(60, max(4, _get_int_setting_cached('catalog_cache_ttl', 120)))


def fast_timeout(kind='generic'):
    base = timeout_seconds()
    if kind == 'stream':
        # v5.10.90: provider lifetime is 12s again. The picker foreground
        # window is controlled separately, so late providers can arrive via
        # live updates without making the UI wait for the full timeout.
        return max(4, min(base, 12))
    if kind == 'meta':
        return max(4, min(base, 8))
    if kind == 'search':
        return max(3, min(base, 6))
    if kind == 'catalog':
        # v3.9.17: catalog fetches happen on every folder-open click, so cap
        # them aggressively. A slow Stremio catalog used to block folder
        # rendering for the full base timeout (10s+). Now if a single catalog
        # exceeds 4s the parallel batch budget skips it and we render whatever
        # the faster catalogs returned.
        #
        # v3.9.31: tightened 4s → 2.5s ceiling. The whole stack now has
        # multiple safety nets for slow catalogs (SWR returns stale instantly,
        # Index Engine pre-mirrors content, background prefetch warms the
        # cache). Foreground render no longer needs to be patient — anything
        # past 2.5s misses this folder open and the user sees the result on
        # next entry (cache is now warm). Folder-open p95 drops from 4s to ~2s
        # in the worst case without affecting catch-up of slow catalogs.
        #
        # v5.10.97: the Dex Hub Home window retries a row whose server missed
        # this budget once, in the background, with more patience (a slow
        # Emby-backed Continue Watching answers in 3-4 s every time).
        patient = _capture_patience()
        if patient > 0:
            return max(3, min(max(base, patient), 30))
        return max(2, min(base, 3))
    return base


def _capture_patience():
    try:
        from .. import capture as _capture
    except Exception:
        return 0.0
    try:
        return float(_capture.patience() or 0.0)
    except Exception:
        return 0.0


def parallel_workers():
    # v3.9.87: keep the global worker budget lower on CoreELEC/Android.
    # High fan-out across many simultaneous plugin invokers caused
    # RuntimeError("can't start new thread") and then native Kodi aborts.
    return max(2, min(4, _get_int_setting_cached('parallel_workers', 4)))


def gzip_enabled():
    raw = (_get_setting_cached('http_gzip', 'true') or 'true').lower()
    return raw not in ('false', '0', 'no')



def _rate_limit_url(url, max_wait=2.0):
    """Apply the host token-bucket limiter, regardless of how this module
    was imported (as dexhub.client through the compatibility shim, or as
    resources.lib.dexhub.client inside Kodi). Older builds used a single
    relative import that failed silently in the shim path, disabling the
    limiter and allowing bursty parallel requests to trigger 429s/stalls.
    """
    try:
        try:
            from resources.lib.ratelimit import limiter, host_of
        except Exception:
            try:
                from ratelimit import limiter, host_of
            except Exception:
                from ..ratelimit import limiter, host_of
        host = host_of(url)
        if host:
            limiter.acquire(host, max_wait=max_wait)
    except Exception:
        pass


def _tighten_rate_limit(url, capacity=4, rate=1.0):
    try:
        try:
            from resources.lib.ratelimit import limiter, host_of
        except Exception:
            try:
                from ratelimit import limiter, host_of
            except Exception:
                from ..ratelimit import limiter, host_of
        host = host_of(url)
        if host:
            limiter.set_policy(host, capacity, rate)
    except Exception:
        pass


def _cache_file(url):
    # Directory existence is ensured once at module init (see HTTP_CACHE_DIR
    # above). No per-call makedirs. (3.8.12)
    return os.path.join(HTTP_CACHE_DIR, hashlib.sha1(url.encode('utf-8')).hexdigest() + '.json')


# In-memory HTTP cache layer — avoids disk I/O for hot URLs within a single
# Python invocation (e.g. catalog opens, then back, then opens again).
# Bounded to 256 entries, FIFO eviction.
_HTTP_MEM = {}
_HTTP_MEM_ORDER = []
_HTTP_MEM_MAX = 256
_HTTP_MEM_LOCK = threading.RLock()


def _mem_cache_get(url, ttl_seconds):
    with _HTTP_MEM_LOCK:
        entry = _HTTP_MEM.get(url)
        if not entry:
            return None
        saved_at, data = entry
        if (time.time() - saved_at) > ttl_seconds:
            _HTTP_MEM.pop(url, None)
            try:
                _HTTP_MEM_ORDER.remove(url)
            except Exception:
                pass
            return None
        return data


def _mem_cache_put(url, data, saved_at=None):
    saved_at = time.time() if saved_at is None else saved_at
    with _HTTP_MEM_LOCK:
        if url in _HTTP_MEM:
            _HTTP_MEM[url] = (saved_at, data)
            return
        _HTTP_MEM[url] = (saved_at, data)
        _HTTP_MEM_ORDER.append(url)
        if len(_HTTP_MEM_ORDER) > _HTTP_MEM_MAX:
            old = _HTTP_MEM_ORDER.pop(0)
            _HTTP_MEM.pop(old, None)


def purge_meta_cache():
    """Drop ALL cached responses for /meta/ URLs.

    Called after the user changes a meta-source mapping so the next
    catalog/details page actually re-fetches and applies the new source
    instead of serving yesterday's cached art and description.

    Cheap: meta URLs typically number in the tens, not thousands.
    Disk + memory layers both purged.
    """
    # In-memory layer
    with _HTTP_MEM_LOCK:
        drop_keys = [k for k in list(_HTTP_MEM.keys()) if '/meta/' in k]
        for k in drop_keys:
            _HTTP_MEM.pop(k, None)
        try:
            _HTTP_MEM_ORDER[:] = [u for u in _HTTP_MEM_ORDER if u not in set(drop_keys)]
        except Exception:
            pass
    # Disk layer — we hash URLs into filenames, so we can't tell from the
    # filename alone whether it's a meta URL. Just nuke the directory; the
    # next request rebuilds. The cost is one extra HTTP roundtrip per
    # (catalog/stream/manifest) URL the user happens to revisit, which is
    # acceptable for the rarity of the meta-picker change action.
    try:
        if os.path.isdir(HTTP_CACHE_DIR):
            for name in os.listdir(HTTP_CACHE_DIR):
                try:
                    os.remove(os.path.join(HTTP_CACHE_DIR, name))
                except Exception:
                    continue
    except Exception:
        pass


def _read_cache(url, ttl_seconds, memory_only=False):
    if ttl_seconds <= 0:
        return None
    # In-memory first (no disk hit)
    cached = _mem_cache_get(url, ttl_seconds)
    if cached is not None:
        return cached
    if memory_only:
        return None
    path = _cache_file(url)
    try:
        saved_at = os.path.getmtime(path) if os.path.exists(path) else 0
        if saved_at and (time.time() - saved_at <= ttl_seconds):
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            _mem_cache_put(url, data, saved_at=saved_at)
            return data
    except Exception:
        return None
    return None


# ─────────────────────────────────────────────────────────────────────
# v3.9.26: stale-while-revalidate (SWR) cache reader.
#
# Returns cached data even when it's past the configured TTL, as long
# as it's within `stale_window_seconds` (typically 2-3× the fresh TTL).
# Callers that get a stale hit are expected to fire a background refresh
# via `_revalidate_async()` so the next access sees fresh data.
#
# Why: the user's perceived speed is dominated by the FIRST visit to a
# folder after the cache expires. SWR removes that "I waited 4s" felt
# every 10 minutes by returning the slightly-stale data instantly while
# we refresh underneath. The data quality cost is minimal because most
# Stremio catalogs (Top, Popular, Trending) move slowly.
# ─────────────────────────────────────────────────────────────────────
def _read_stale_cache(url, stale_window_seconds):
    """Return (data, age_seconds) if cache exists within stale_window_seconds.
    Returns (None, 0) when no acceptable stale data is available."""
    if stale_window_seconds <= 0:
        return None, 0
    # Memory layer doesn't track age beyond TTL — only check disk.
    path = _cache_file(url)
    try:
        if not os.path.exists(path):
            return None, 0
        age = time.time() - os.path.getmtime(path)
        if age > stale_window_seconds:
            return None, 0
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh), age
    except Exception:
        return None, 0


# Dedup in-flight background revalidates so a busy home folder doesn't
# fire 10 concurrent refreshes for the same URL.
_REVALIDATE_INFLIGHT = set()
_REVALIDATE_LOCK = threading.Lock()
_REVALIDATE_MAX_INFLIGHT = 16


def _revalidate_async(url, headers, timeout):
    """Use a bounded, invocation-owned pool to refresh cached data.

    Used by the SWR path in `get_json()` — caller already returned stale
    data to the user; this background task refreshes the cache for the
    next access. Deduped by URL: if a revalidation is already in flight
    for this URL, we skip.
    """
    with _REVALIDATE_LOCK:
        if url in _REVALIDATE_INFLIGHT:
            return
        if len(_REVALIDATE_INFLIGHT) >= _REVALIDATE_MAX_INFLIGHT:
            return
        _REVALIDATE_INFLIGHT.add(url)

    def _worker():
        try:
            # v5.10.106: a refresh nobody waits for gives way to the pages the
            # user is opening (they share each host's rate limit)
            waited = 0.0
            while _FOREGROUND[0] > 0 and waited < 8.0:
                time.sleep(0.25)
                waited += 0.25
            from urllib.request import Request
            req = Request(url, headers=headers)
            with _retry_open(req, timeout=timeout) as response:
                text = _decode_response(response)
            data = _json_loads_fast(text)
            _write_cache(url, data)
        except Exception:
            pass
        finally:
            with _REVALIDATE_LOCK:
                _REVALIDATE_INFLIGHT.discard(url)

    try:
        future = _get_pool(2, lane='revalidate').submit(_worker)
        def _cancelled(done):
            if done.cancelled():
                with _REVALIDATE_LOCK:
                    _REVALIDATE_INFLIGHT.discard(url)
        future.add_done_callback(_cancelled)
    except Exception:
        # Cache revalidation is optional.  If Kodi cannot submit to the stable
        # pool (most notably ``can't start new thread`` on small CoreELEC
        # boxes), never create an emergency ad-hoc thread: return the stale
        # value already served to the caller and allow the next foreground
        # request to refresh it normally.
        with _REVALIDATE_LOCK:
            _REVALIDATE_INFLIGHT.discard(url)


def _write_cache(url, data, memory_only=False):
    if isinstance(data, dict) and data.get('dexworldError'):
        return  # A rejected IPTV request is not a successful empty catalog.
    _mem_cache_put(url, data)
    if memory_only:
        return
    try:
        # v5.10.117: one write of the whole text (json.dump writes piece by
        # piece: thousands of small writes for a catalog page)
        text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        with open(_cache_file(url), 'w', encoding='utf-8') as fh:
            fh.write(text)
    except Exception:
        pass


def _decode_response(response):
    body = response.read()
    encoding = (response.headers.get('Content-Encoding') or '').lower()
    if 'gzip' in encoding:
        try:
            body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
        except Exception:
            pass
    return body.decode('utf-8', 'replace')


def _retry_open(req, timeout, retry=True, rate_wait=2.0):
    """urlopen with a single retry on transient errors.

    Retries once on:
      - URLError / socket.timeout      (DNS hiccup, connection reset, RST)
      - HTTPError with status >= 500   (502/503/504 from upstream)
    Does NOT retry on:
      - HTTPError 4xx                  (client bug — won't change on retry)
      - any other exception            (programming errors)
    Backoff is fixed at 250ms — long enough to clear most blips, short
    enough to keep total latency under 1.5x of a normal failure.
    Added in 3.8.12 to fix "catalogs render half-empty after a single 502".

    v3.9.0: rate-limited via the per-host token bucket so concurrent
    widget renders don't tip TMDb / Trakt / Cinemeta into 429 territory.
    """
    # Per-host throttle (works in both shim and package import modes).
    url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
    _rate_limit_url(url, max_wait=max(0.0, float(rate_wait or 0.0)))

    from urllib.request import urlopen  # lazy: keeps http.client off import path

    last_exc = None
    attempts = (0, 1) if retry else (0,)
    for attempt in attempts:
        try:
            return urlopen(req, timeout=timeout)
        except HTTPError as exc:
            last_exc = exc
            if exc.code == 429:
                # Tighten the bucket — upstream is telling us to slow down.
                _tighten_rate_limit(req.full_url if hasattr(req, 'full_url') else req.get_full_url(), 4, 1.0)
            if exc.code < 500:
                raise
        except (URLError, socket.timeout) as exc:
            last_exc = exc
        if retry and attempt == 0:
            time.sleep(0.25)
    raise last_exc


# v5.10.106 (after Nuvio Hub's browse cache): a catalog page stays usable as a
# stale copy for a week. It is shown at once and read again in the
# background, so a catalog opened hours or days later never waits for its
# add-on; the next visit has the fresh page. One request per address runs at
# a time (a second caller waits for it), and background refreshes give way
# to requests the user is waiting for.
CATALOG_STALE_SECONDS = 7 * 24 * 3600
_FOREGROUND = [0]
_FOREGROUND_LOCK = threading.Lock()
_FLIGHTS = {}
_FLIGHTS_LOCK = threading.Lock()
_FRESH = threading.local()


class fresh_reads(object):
    """Within this block (one thread), an expired copy is not served stale:
    the Home reading its rows again after showing their saved copy wants the
    add-on's current page, not the copy it already shows."""

    def __enter__(self):
        self.previous = getattr(_FRESH, 'on', False)
        _FRESH.on = True
        return self

    def __exit__(self, *exc):
        _FRESH.on = self.previous
        return False


def _foreground(step):
    with _FOREGROUND_LOCK:
        _FOREGROUND[0] = max(0, _FOREGROUND[0] + step)


_PRUNED = [False]


def _cache_keep_seconds():
    """The longest any saved answer is still used: a catalog's stale week, or
    the selected metadata source's own window (meta_source_cache_days)."""
    try:
        days = int(float(addon().getSetting('meta_source_cache_days') or 7))
    except Exception:
        days = 7
    return max(CATALOG_STALE_SECONDS, max(1, min(days, 60)) * 86400) + 86400


def _prune_http_cache_soon():
    """Once per interpreter, in the background: saved answers older than any
    window that could still use them are removed (the folder never shrank).

    v5.10.120: only in the service. In a listing's or a skin row's call the
    thread (20 s asleep first) held the call open: Kodi waits for every
    thread of a finished call ("waiting on thread", "threads still running
    as it ends: DexHub-cache-prune" after every title page and row)."""
    if _PRUNED[0]:
        return
    _PRUNED[0] = True
    try:
        import sys
        if str((sys.argv or [''])[0]).startswith('plugin://'):
            return
    except Exception:
        return

    def run():
        try:
            time.sleep(20.0)
            keep = _cache_keep_seconds()
            now = time.time()
            for name in os.listdir(HTTP_CACHE_DIR):
                path = os.path.join(HTTP_CACHE_DIR, name)
                try:
                    if now - os.path.getmtime(path) > keep:
                        os.remove(path)
                except OSError:
                    continue
        except Exception:
            pass
    try:
        thread = threading.Thread(target=run, name='DexHub-cache-prune')
        thread.daemon = True
        thread.start()
    except Exception:
        pass


# v5.10.241: catalogs of an add-on whose host keeps timing out (the log:
# 30 timeouts of 3 s a night on one host's Emby rows) are not asked for a
# while (5 min, doubling to 30), and a catalog answering an empty body is
# not asked for 30 min. Kept as Home window properties: every plugin call is
# a new interpreter. Streams, meta and manifests are never held.
_HOLD_EMPTY = 1800.0


def _host_of(url):
    try:
        parts = urlparse(url)
        base = url.split('/catalog/', 1)[0]
        return '%s|%s' % (parts.netloc, hashlib.sha1(base.encode('utf-8')).hexdigest()[:8])
    except Exception:
        return ''


def _hold_key(url, kind):
    target = _host_of(url) if kind == 'down' else hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]
    return 'dexhub.hold.%s.%s' % (kind, hashlib.sha1(target.encode('utf-8')).hexdigest()[:12])


def _window():
    try:
        import xbmcgui
        return xbmcgui.Window(10000)
    except Exception:
        return None


def _held(url):
    win = _window()
    if win is None:
        return ''
    now = time.time()
    for kind in ('empty', 'down'):
        try:
            raw = win.getProperty(_hold_key(url, kind))
            until = float(raw.split('|', 1)[0]) if raw else 0.0
        except Exception:
            until = 0.0
        if until > now:
            return kind
    return ''


def _hold(url, kind):
    win = _window()
    if win is None:
        return
    key = _hold_key(url, kind)
    if kind == 'empty':
        win.setProperty(key, '%.0f|1' % (time.time() + _HOLD_EMPTY))
        return
    try:
        count = int((win.getProperty(key) or '0|0').split('|', 1)[1])
    except Exception:
        count = 0
    pause = min(1800.0, 300.0 * (2 ** min(count, 3)))
    win.setProperty(key, '%.0f|%d' % (time.time() + pause, count + 1))
    try:
        import xbmc
        xbmc.log('[DexHub] %s timed out: its catalogs wait %.0f min' % (_host_of(url).split('|')[0], pause / 60),
                 xbmc.LOGINFO)
    except Exception:
        pass


def _hold_clear(url, kind):
    win = _window()
    if win is not None:
        try:
            if win.getProperty(_hold_key(url, kind)):
                win.clearProperty(_hold_key(url, kind))
        except Exception:
            pass


def _is_timeout(exc):
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    text = ('%s %s' % (exc.__class__.__name__, exc)).lower()
    return 'timed out' in text or 'timeout' in text


def get_json(url, ttl_seconds=0, timeout_override=None, memory_only=False,
             retry=True, rate_wait=2.0, stale_seconds=None, extra_headers=None):
    cached = _read_cache(url, ttl_seconds, memory_only=memory_only)
    if memory_only and isinstance(cached, dict) and 'streams' in cached and not cached['streams']:
        cached = _read_cache(url, min(ttl_seconds, 10), memory_only=True)
    if cached is not None:
        return cached

    # Prepare headers/timeout for either the SWR background refresh or the
    # foreground fetch below.
    headers = {
        'Accept': 'application/json',
        'User-Agent': 'DexHub/%s (Kodi)' % (addon().getAddonInfo('version') or '3.8.9'),
    }
    if 'dexworld.cc/' in url:
        # v5.10.185: DexWorld's server knows its Kodi clients by this header
        # (the Dex IPTV add-on sends it on every request)
        headers['X-DexWorld-Client'] = 'kodi'
        # v5.10.241: and Dex Hub by this one (the server then lists its
        # search catalogs for Dex Hub's search: DEXHUB_SEARCH_V1)
        headers['X-DexWorld-App'] = 'dexhub'
    if gzip_enabled():
        headers['Accept-Encoding'] = 'gzip'
    if extra_headers:
        headers.update(extra_headers)
    timeout = timeout_seconds() if timeout_override in (None, '') else max(1, int(float(timeout_override)))

    # v3.9.26: stale-while-revalidate. If cache is expired but recent
    # enough (within 2× the fresh TTL window), serve the stale copy
    # IMMEDIATELY and refresh the disk cache in a background thread. The
    # user gets a zero-latency render; the next access sees fresh data.
    # Disabled for memory_only callers because the SWR cache lives on
    # disk (the mem layer doesn't track age past TTL).
    if ttl_seconds > 0 and not memory_only and not getattr(_FRESH, 'on', False):
        window = ttl_seconds * 2 if stale_seconds is None else max(0, int(stale_seconds))
        stale, age = _read_stale_cache(url, window)
        if stale is not None:
            _revalidate_async(url, headers, timeout)
            # kept in memory with its real age, so it never counts as fresh
            _mem_cache_put(url, stale, saved_at=time.time() - age)
            return stale

    # Cache miss or beyond stale window → real foreground HTTP fetch.
    # v3.9.29: route through urllib3 PoolManager when available — gains
    # us TLS keep-alive and connection reuse across calls (saves ~150-300ms
    # per request after the first to each host). urllib fallback path is
    # functionally identical, just without the keep-alive bonus.
    # A paused request must not claim an in-flight slot: it returns before
    # the HTTP finally block, leaving later callers waiting on an orphan.
    catalog = '/catalog/' in url
    if catalog:
        hold = _held(url)
        if hold == 'empty':
            return {'metas': []}
        if hold == 'down':
            raise socket.timeout('paused after timeouts (%s)' % _host_of(url))
    flight = None
    if ttl_seconds > 0:
        _prune_http_cache_soon()
        with _FLIGHTS_LOCK:
            running = _FLIGHTS.get(url)
            if running is None:
                flight = _FLIGHTS[url] = threading.Event()
        if running is not None:
            # the same address is being read: its answer serves this caller
            running.wait(max(2.0, float(timeout) + 2.0))
            cached = _read_cache(url, ttl_seconds, memory_only=memory_only)
            if cached is not None:
                return cached
    _foreground(1)
    try:
        try:
            text = _http_get(url, headers, timeout, retry=retry, rate_wait=rate_wait)
        except Exception as exc:
            if catalog and _is_timeout(exc):
                _hold(url, 'down')
            raise
        if catalog:
            _hold_clear(url, 'down')
        try:
            data = _json_loads_fast(text)
        except Exception:
            if catalog and not (text or '').strip():
                # v5.10.241: an add-on answering a catalog with nothing (the
                # log: one catalog 34 times a night) is not asked again for
                # half an hour
                _hold(url, 'empty')
                return {'metas': []}
            raise
        if ttl_seconds > 0:
            _write_cache(url, data, memory_only=memory_only)
        return data
    finally:
        _foreground(-1)
        if flight is not None:
            with _FLIGHTS_LOCK:
                if _FLIGHTS.get(url) is flight:
                    _FLIGHTS.pop(url, None)
            flight.set()


def _http_get(url, headers, timeout, retry=True, rate_wait=2.0):
    """Single HTTP GET with retry, transparent gzip, returns decoded text.

    Uses urllib3's PoolManager (with TLS keep-alive) when available;
    falls back to the original urllib path with a manual retry loop.
    """
    pool = _ensure_pool()
    if pool is not None:
        # Per-host throttle still applies — keep the rate limiter active.
        _rate_limit_url(url, max_wait=max(0.0, float(rate_wait or 0.0)))
        last_exc = None
        attempts = (0, 1) if retry else (0,)
        for attempt in attempts:
            try:
                # decode_content=True lets urllib3 transparently gunzip.
                # We then read .data which is already decompressed bytes.
                resp = pool.request(
                    'GET', url,
                    headers=headers,
                    timeout=_URLLIB3.Timeout(connect=min(10, timeout), read=timeout),
                    redirect=True,
                    retries=False,
                    decode_content=True,
                    preload_content=True,
                )
                if 500 <= resp.status < 600 and retry and attempt == 0:
                    # Retry once on 5xx (matches the urllib path).
                    last_exc = Exception('upstream %d' % resp.status)
                    time.sleep(0.25)
                    continue
                if resp.status >= 400:
                    raise Exception('HTTP %d for %s' % (resp.status, url))
                return resp.data.decode('utf-8', 'replace')
            except Exception as exc:
                last_exc = exc
                if retry and attempt == 0:
                    time.sleep(0.25)
                    continue
                raise
        if last_exc:
            raise last_exc

    # Fallback: original urllib path.
    from urllib.request import Request
    req = Request(url, headers=headers)
    with _retry_open(req, timeout=timeout, retry=retry,
                     rate_wait=rate_wait) as response:
        return _decode_response(response)


def validate_manifest(manifest_url, force_refresh=False):
    data = get_json(manifest_url, ttl_seconds=0 if force_refresh else 300)
    if not isinstance(data, dict):
        raise ValueError('Invalid manifest: not a JSON object')
    # Stremio manifest spec requires id, version, resources, types.
    # Reject incomplete manifests up front instead of letting downstream
    # catalog/stream code crash on missing keys.
    missing = [k for k in ('id', 'version', 'resources', 'types') if k not in data]
    if missing:
        raise ValueError('Invalid manifest: missing %s' % ', '.join(missing))
    if not isinstance(data.get('resources'), list) or not data['resources']:
        raise ValueError('Invalid manifest: resources must be a non-empty list')
    if not isinstance(data.get('types'), list) or not data['types']:
        raise ValueError('Invalid manifest: types must be a non-empty list')
    return data


def _configured_base_and_query(provider):
    """Return the configured addon base URL and any manifest query string.

    Stremio clients derive resource URLs from the exact configured manifest
    URL.  Some addons encode credentials in the path
    (/stremio/<config>/manifest.json); others may put them in the query string
    (manifest.json?...).  Preserve both shapes so Dex Hub calls the same route
    Stremio does.
    """
    manifest_url = str((provider or {}).get('manifest_url') or '').strip()
    if manifest_url and '/manifest.json' in manifest_url:
        try:
            parsed = urlparse(manifest_url)
            path = parsed.path.rsplit('/manifest.json', 1)[0]
            base = urlunparse((parsed.scheme, parsed.netloc, path, '', '', '')).rstrip('/')
            return base, parsed.query or ''
        except Exception:
            return manifest_url.rsplit('/manifest.json', 1)[0].rstrip('/'), ''
    return str(provider.get('base_url') or '').rstrip('/'), ''


def build_resource_url(provider, resource, media_type, item_id, extra=None):
    base, manifest_query = _configured_base_and_query(provider)
    # Episode stream ids use colons: tt1234567:1:2. Keep them readable
    # for stream routes because AIOStreams/Torrentio-style proxies are
    # more reliable with the normal Stremio path shape than an over-encoded id.
    safe_chars = ':' if resource in ('stream', 'catalog') else ''
    encoded_id = quote(str(item_id), safe=safe_chars)
    url = '%s/%s/%s/%s' % (base, resource, media_type, encoded_id)
    if extra:
        parts = []
        for key, value in extra.items():
            if value is None or value == '':
                continue
            parts.append('%s=%s' % (quote(str(key), safe=''), quote(str(value), safe='')))
        if parts:
            url += '/' + '&'.join(parts)
    url += '.json'
    if manifest_query:
        url += '?' + manifest_query
    return url


def fetch_catalog(provider, media_type, catalog_id, extra=None,
                  timeout_override=None, retry=True, rate_wait=2.0, force_refresh=False):
    # a search answers what was typed now: never a stale copy of it
    search = bool(str((extra or {}).get('search') or '').strip())
    return get_json(
        build_resource_url(provider, 'catalog', media_type, catalog_id, extra=extra or {}),
        ttl_seconds=0 if force_refresh else catalog_ttl(),
        timeout_override=timeout_override,
        retry=retry,
        rate_wait=rate_wait,
        stale_seconds=None if search else CATALOG_STALE_SECONDS,
    )


def _meta_ttl_for(ttl_override=None):
    # v5.10.67: a metadata SOURCE the user selected (AIOMetadata, Cinemeta,
    # ...) keeps its own freshness server-side, so callers may ask for a much
    # longer local TTL than the generic catalog/meta window. ``None`` keeps
    # the historical behaviour for every other /meta/ consumer.
    if ttl_override in (None, ''):
        return meta_ttl()
    try:
        return max(0, int(float(ttl_override)))
    except Exception:
        return meta_ttl()


def fetch_meta(provider, media_type, item_id, timeout_override=None,
               retry=True, rate_wait=2.0, ttl_override=None):
    return get_json(
        build_resource_url(provider, 'meta', media_type, item_id),
        ttl_seconds=_meta_ttl_for(ttl_override),
        timeout_override=timeout_override,
        retry=retry,
        rate_wait=rate_wait,
    )


def fetch_meta_cached_only(provider, media_type, item_id, ttl_override=None):
    """Return a fresh cached Stremio meta response without network I/O."""
    url = build_resource_url(provider, 'meta', media_type, item_id)
    cached = _read_cache(url, _meta_ttl_for(ttl_override))
    return cached if isinstance(cached, dict) else {}


def _aiostreams_headers(provider):
    """v5.10.204: AIOStreams adds its own data to each stream (streamData:
    the service and whether it is cached, in your library, size, indexer)
    only for a client that names itself AIO… in User-Agent (the server's
    PROVIDE_STREAM_DATA default). The source list then knows a cached or
    library source without depending on the user's formatter."""
    provider = provider or {}
    manifest = provider.get('manifest') or {}
    blob = ' '.join(str(v or '') for v in (
        manifest.get('id'), manifest.get('name'), provider.get('name'),
        provider.get('manifest_url'), provider.get('base_url'))).lower()
    if 'aiostreams' not in blob:
        return None
    version = addon().getAddonInfo('version') or ''
    return {'User-Agent': 'AIOStreams-client DexHub/%s (Kodi)' % version}


def fetch_streams(provider, media_type, video_id, timeout_override=None):
    # v3.9.29: respect the provider-health blacklist. After repeated
    # failures, skip the call entirely and return an empty stream list
    # so the parallel race in plugin.streams doesn't wait on a known-
    # broken addon for the full timeout.
    pid = (provider or {}).get('id') or (provider or {}).get('base_url') or ''
    force = bool((provider or {}).get('_force_stream_refresh'))
    if pid and not force and _provider_health_should_skip(pid):
        # A cooldown must not hide usable results already cached for this
        # exact provider/title. This does not contact or un-blacklist it.
        cached = _read_cache(build_resource_url(provider, 'stream', media_type, video_id),
                             stream_ttl(), memory_only=True)
        if isinstance(cached, dict) and cached.get('streams'):
            return cached
        return {'streams': [], '_source_status': 'skipped'}
    try:
        result = get_json(
            build_resource_url(provider, 'stream', media_type, video_id),
            ttl_seconds=0 if force else stream_ttl(),
            timeout_override=timeout_override,
            memory_only=True,
            retry=False,
            rate_wait=0.25,
            extra_headers=_aiostreams_headers(provider),
        )
        # Empty stream lists are valid (no sources found) — only treat
        # exceptions as health failures.
        if pid:
            _provider_health_record_success(pid)
        return result
    except Exception:
        if pid:
            _provider_health_record_failure(pid)
        raise


# ─────────────────────────────────────────────────────────────────────
# v3.9.29: provider health blacklist.
#
# When a stream addon consistently times out or errors, we skip it for
# a 5-minute window so it doesn't gate every Play attempt. Tracked
# per-provider-id; auto-recovers without manual intervention. Reset
# instantly on any successful call.
#
# Failure threshold: 3 consecutive failures within 60s → blacklist.
# Recovery window: 300s (5 minutes). After that the next call gets
# through and the counter is reset on success.
# ─────────────────────────────────────────────────────────────────────
_HEALTH_FAILURES = {}   # pid → list[float] (recent failure timestamps)
_HEALTH_BLACKLIST = {}  # pid → float (timestamp when blacklist expires)
_HEALTH_LOCK = threading.Lock()
_HEALTH_FAILURE_THRESHOLD = 3
_HEALTH_FAILURE_WINDOW    = 60.0
_HEALTH_BLACKLIST_PERIOD  = 300.0   # 5 minutes
_HEALTH_PAUSED = [0.0]              # accounting suspended until this time


def _provider_health_should_skip(pid):
    """Return True if the provider is currently blacklisted."""
    if not pid:
        return False
    with _HEALTH_LOCK:
        expiry = _HEALTH_BLACKLIST.get(pid)
        if not expiry:
            return False
        if time.time() >= expiry:
            # Lapsed — clear and let the next call probe upstream.
            _HEALTH_BLACKLIST.pop(pid, None)
            _HEALTH_FAILURES.pop(pid, None)
            return False
        return True


def _provider_health_record_success(pid):
    if not pid:
        return
    with _HEALTH_LOCK:
        _HEALTH_FAILURES.pop(pid, None)
        if pid in _HEALTH_BLACKLIST:
            # Recovered — drop the blacklist immediately.
            _HEALTH_BLACKLIST.pop(pid, None)


def provider_health_pause(seconds=0.0):
    """Suspend failure accounting while something else is hogging the scan.

    v3.9.225 — Stremio addons were being blacklisted, and it was our fault.

    The native Plex step could burn ~43 seconds per server on a source scan
    (fixed in 3.9.224). While it did, the Stremio providers' own requests ran
    out of time, each timeout counted as a provider failure, three failures in
    sixty seconds blacklisted the addon for FIVE MINUTES — and the user's
    Stremio sources, which had always worked, quietly stopped appearing.

    A provider must only be penalised for ITS OWN failures.
    """
    with _HEALTH_LOCK:
        _HEALTH_PAUSED[0] = time.time() + max(0.0, float(seconds or 0))


def _provider_health_record_failure(pid):
    if not pid:
        return
    with _HEALTH_LOCK:
        if time.time() < _HEALTH_PAUSED[0]:
            return          # the scan was starved — not this provider's doing
    now = time.time()
    with _HEALTH_LOCK:
        recent = _HEALTH_FAILURES.get(pid, [])
        # Discard failures outside the window.
        recent = [t for t in recent if (now - t) <= _HEALTH_FAILURE_WINDOW]
        recent.append(now)
        _HEALTH_FAILURES[pid] = recent
        if len(recent) >= _HEALTH_FAILURE_THRESHOLD:
            _HEALTH_BLACKLIST[pid] = now + _HEALTH_BLACKLIST_PERIOD
            try:
                import xbmc
                xbmc.log(
                    '[DexHub] provider %s blacklisted for %ds after %d failures'
                    % (pid, int(_HEALTH_BLACKLIST_PERIOD), len(recent)),
                    xbmc.LOGINFO,
                )
            except Exception:
                pass



def fetch_subtitles(provider, media_type, item_id, timeout_seconds=None):
    return get_json(
        build_resource_url(provider, 'subtitles', media_type, item_id),
        ttl_seconds=300,
        timeout_override=timeout_seconds,
        retry=False,
        rate_wait=0.25,
    )


# Persistent, lane-isolated thread pools.  Keep only three physical lanes:
# browse, streams and subtitles.  Older builds created a new persistent pool
# for every logical label (default/search/identity/streams/stream_refresh/
# subtitles).  A long Kodi session could therefore retain dozens of native
# threads even though only a handful were doing useful work.  The aliases
# below keep playback isolated from stale browsing work without letting the
# number of pools grow with features.
_POOL = None                  # legacy/default lane compatibility
_POOL_SIZE = 0
_POOL_LOCK = threading.Lock()
_LANE_POOLS = {}
_LANE_POOL_SIZES = {}
# v5.10.117: 'art' runs a listing's art lookups (plugin._warm_listing_art): a
# lane of its own, so a listing rendered on a browse worker never waits on
# its own lane
_LANE_CAPS = {'browse': 4, 'streams': 4, 'subtitles': 3, 'revalidate': 2, 'art': 4}


def _widen_lanes_for_desktop():
    """v5.10.28: 6 stream workers on x86/desktop builds.

    The 4-worker cap protects CoreELEC/ARM boxes (see the 5.9.6 notes); a
    desktop/x86 Kodi has the cores and memory for one more pair, so a slow
    aggregator no longer holds a quarter of the scan.
    """
    try:
        import platform as _platform
        machine = (_platform.machine() or '').lower()
    except Exception:
        return
    if machine and not any(tag in machine for tag in ('arm', 'aarch', 'mips', 'riscv')):
        _LANE_CAPS['streams'] = 6


_widen_lanes_for_desktop()


def _physical_lane(lane):
    raw = str(lane or 'default').strip().lower()[:32] or 'default'
    if raw == 'revalidate':
        return 'revalidate'
    if raw == 'art':
        return 'art'
    if 'stream' in raw:
        return 'streams'
    if 'subtitle' in raw:
        return 'subtitles'
    return 'browse'


def _get_pool(size, lane='default'):
    global _POOL, _POOL_SIZE
    lane = _physical_lane(lane)
    # Keep a stable executor per physical lane. ThreadPoolExecutor starts
    # workers lazily, so a two-item job still uses only two threads; using the
    # lane cap here prevents a later small identity job from tearing down the
    # browse executor while an earlier search still owns its futures.
    size = _LANE_CAPS.get(lane, 4)
    with _POOL_LOCK:
        pool = _LANE_POOLS.get(lane)
        old_size = int(_LANE_POOL_SIZES.get(lane) or 0)
        if pool is None or old_size != size:
            # Recreate the pool when the requested size changes. Earlier builds
            # only grew the shared pool, so once a source scan asked for 8
            # workers every later catalog/search call kept the larger pool even
            # if the user lowered the setting. On CoreELEC/Kodi 22 this keeps
            # the thread budget predictable.
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    try:
                        pool.shutdown(wait=False)
                    except Exception:
                        pass
                except Exception:
                    pass
            pool = ThreadPoolExecutor(
                max_workers=size,
                thread_name_prefix='DexHub-%s' % lane,
            )
            _LANE_POOLS[lane] = pool
            _LANE_POOL_SIZES[lane] = size
        if lane == 'browse':
            _POOL = pool
            _POOL_SIZE = size
        return pool


def shutdown_lane_pools(cancel_pending=True):
    """Terminate every lane executor so the Python interpreter can finalize.

    v5.10.15: ThreadPoolExecutor workers are NOT daemon threads (CPython 3.9+)
    and they block forever on the work queue when idle. Nothing ever shut these
    down, so every plugin invocation leaked its whole set — browse(4) +
    streams(4) + subtitles(3) + bg(2).

    A macOS crash report from Kodi 22.0.beta1 (Python 3.14) shows exactly that:
    40 live DexHub worker threads in three complete generations, three
    CPythonInvoker threads parked in the 100ms "wait for the script's threads
    to exit" loop, and 5.4 GB of address space (708 MB of it thread stacks) on
    an 8 GB machine. With invokers unable to finalize while new ones start, a
    thread state was attached with a NULL interpreter and CPython segfaulted
    inside _PyThreadState_Attach.

    Called at the end of every invocation. Executors create their threads
    lazily, so the next run only pays for the workers it actually uses.
    """
    with _POOL_LOCK:
        pools = list(_LANE_POOLS.items())
        _LANE_POOLS.clear()
        _LANE_POOL_SIZES.clear()
        global _POOL, _POOL_SIZE
        _POOL = None
        _POOL_SIZE = 0
    for _lane, pool in pools:
        if pool is None:
            continue
        try:
            # wait=False: never block the invocation on a slow provider.
            # cancel_futures: queued-but-unstarted work belongs to a request
            # that has already finished, so running it would only keep the
            # worker — and the invoker — alive for nothing.
            pool.shutdown(wait=False, cancel_futures=bool(cancel_pending))
        except TypeError:
            try:
                pool.shutdown(wait=False)
            except Exception:
                pass
        except Exception:
            pass
    return len(pools)


_TASK_LOCAL = threading.local()


def task_cancelled():
    """True inside a ParallelRace task whose race has been cancelled.

    v5.10.84: closing the sources window cancelled only the tasks still
    queued; tasks already running (a Plex title scan, a second server) ran to
    the end with nobody waiting for them. Work that has not started yet checks
    this and stops.
    """
    race = getattr(_TASK_LOCAL, 'race', None)
    return bool(race is not None and getattr(race, '_cancelled', False))


def install_stop_signal(target, seconds):
    """Give a server dict a _should_stop() the scan code can poll.

    v5.10.85. The cold-server budget runs a native lookup in a daemon thread
    and stops WAITING after the budget; the thread itself kept scanning. On the
    CoreELEC box a Plex title scan ran 15 to 31 seconds per server, went on
    after the user had picked a source, and left Kodi waiting on the thread
    (209s of such waits in one afternoon, the longest 64s). Because the work
    runs in another thread, the signal travels inside the server dict that
    every Plex call receives, not in a thread-local. It fires when the race
    that started the lookup is cancelled or when the deadline passes.
    """
    race = getattr(_TASK_LOCAL, 'race', None)
    deadline = time.monotonic() + max(1.0, float(seconds or 0.0))

    # v5.10.91: remember WHY the scan stopped. A lookup cut by the deadline
    # says the server is slow, one cut because the user picked a source says
    # nothing about the server; neither may be recorded as "found nothing".
    def _mark(reason):
        try:
            if not target.get('_stop_reason'):
                target['_stop_reason'] = reason
        except Exception:
            pass

    def _should_stop():
        if race is not None and getattr(race, '_cancelled', False):
            _mark('cancelled')
            return True
        if time.monotonic() > deadline:
            _mark('deadline')
            return True
        return False
    try:
        target['_should_stop'] = _should_stop
        # A reused interpreter keeps the server dict between searches; each
        # lookup starts clean so its stop is logged and classified afresh.
        target['_stop_reason'] = ''
        target['_stop_logged'] = False
    except Exception:
        pass


class ParallelRace(object):
    """A reusable completion-order race with non-destructive polling.

    ``iter_parallel`` necessarily cancels whatever is pending when its caller
    stops iterating. Source discovery needs a different contract: consume the
    fast first results on Kodi's foreground thread, then hand the *same* live
    futures to the source-window refresh worker. This class provides that seam
    and also lets UI loops evaluate early-finish timers every 100ms instead of
    blocking inside ``as_completed`` until the next slow provider responds.
    """

    def __init__(self, func, items, workers=None, timeout=None, lane='default'):
        self.items = list(items or [])
        self.lane = str(lane or 'default').strip().lower()[:32] or 'default'
        self.deadline = (time.monotonic() + max(0.0, float(timeout))
                         if timeout not in (None, '') else None)
        self._futures = {}
        self._serial_index = 0
        self._serial = False
        self._cancelled = False
        self._func = func
        if not self.items:
            return
        size = workers if workers else parallel_workers()
        size = max(1, min(int(size or 1), len(self.items), 8))
        try:
            try:
                pool = _get_pool(size, self.lane)
            except TypeError:
                # Test doubles and older monkey-patches may still expose the
                # one-argument helper. Keep graceful degradation intact.
                pool = _get_pool(size)
            for idx, item in enumerate(self.items):
                self._futures[pool.submit(self._bound, func, item)] = idx
        except Exception as exc:
            for future in list(self._futures):
                try:
                    future.cancel()
                except Exception:
                    pass
            self._futures.clear()
            self._serial = True
            _reset_pool_for_thread_failure(self.lane)
            try:
                import xbmc
                xbmc.log('[DexHub] %s parallel race fell back to serial: %s'
                         % (self.lane, exc), xbmc.LOGWARNING)
            except Exception:
                pass

    def _time_left(self):
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    @property
    def expired(self):
        left = self._time_left()
        return left is not None and left <= 0.0

    @property
    def done(self):
        if self._cancelled or self.expired:
            return True
        if self._serial:
            return self._serial_index >= len(self.items)
        return not self._futures

    def pending_items(self):
        if self._serial:
            return list(self.items[self._serial_index:])
        indices = sorted(self._futures.values())
        return [self.items[idx] for idx in indices]

    def continue_in_background(self, seconds=25.0):
        """Give the existing futures a bounded background collection window.

        Never resubmit requests or revive an explicitly cancelled race.
        The foreground deadline otherwise makes an inherited iterator exit
        immediately even when providers have already completed successfully.
        """
        if self._cancelled:
            return False
        self.deadline = max(self.deadline or 0.0,
                            time.monotonic() + max(0.0, float(seconds)))
        return True

    def drain_ready(self):
        """Consume completed futures without waiting, even at the deadline."""
        if self._cancelled or self._serial:
            return []
        rows = []
        for future, idx in sorted(list(self._futures.items()), key=lambda pair: pair[1]):
            if not future.done():
                continue
            if self._futures.pop(future, None) is None:
                continue
            try:
                value = future.result(timeout=0)
            except Exception as exc:
                value = exc
            rows.append((self.items[idx], value))
        return rows

    def poll(self, max_wait=0.1):
        """Return every completion currently ready, waiting at most max_wait.

        A successful provider result is returned immediately; the configured
        timeout is only a ceiling. This is deliberately unlike a fixed sleep.
        """
        if self.done:
            return []
        wait_for = max(0.0, float(max_wait or 0.0))
        left = self._time_left()
        if left is not None:
            wait_for = min(wait_for, left)
        if self._serial:
            if self._serial_index >= len(self.items) or self.expired:
                return []
            idx = self._serial_index
            self._serial_index += 1
            item = self.items[idx]
            try:
                value = self._func(item)
            except Exception as exc:
                value = exc
            return [(item, value)]

        done, _pending = _future_wait(
            set(self._futures), timeout=wait_for,
            return_when=FIRST_COMPLETED,
        )
        rows = []
        for future in sorted(done, key=lambda f: self._futures.get(f, 0)):
            # UI cancellation may clear the future map while ``wait`` is
            # returning. Treat those futures as intentionally discarded
            # instead of raising KeyError in the refresh worker.
            idx = self._futures.pop(future, None)
            if idx is None:
                continue
            try:
                value = future.result(timeout=0)
            except Exception as exc:
                value = exc
            rows.append((self.items[idx], value))
        return rows

    def iter_completed(self, timeout=None, poll_interval=0.1):
        local_deadline = (time.monotonic() + max(0.0, float(timeout))
                          if timeout not in (None, '') else None)
        while not self.done:
            if local_deadline is not None:
                remaining = local_deadline - time.monotonic()
                if remaining <= 0:
                    yield from self.drain_ready()
                    return
                wait_for = min(max(0.01, float(poll_interval or 0.1)), remaining)
            else:
                wait_for = max(0.01, float(poll_interval or 0.1))
            rows = self.poll(wait_for)
            for row in rows:
                yield row
        yield from self.drain_ready()

    def _bound(self, func, item):
        # v5.10.84: let a running task see that its race was cancelled.
        _TASK_LOCAL.race = self
        try:
            return func(item)
        finally:
            _TASK_LOCAL.race = None

    def cancel(self):
        self._cancelled = True
        for future in list(self._futures):
            try:
                future.cancel()
            except Exception:
                pass
        self._futures.clear()


def start_parallel_race(func, items, workers=None, timeout=None, lane='default'):
    return ParallelRace(func, items, workers=workers, timeout=timeout, lane=lane)


def run_parallel(func, items, workers=None, timeout=None, lane='default'):
    """Run func(item) for every item concurrently.

    Returns a list of (item, result_or_exception) tuples in original order.
    v3.9.87 also handles thread quota exhaustion gracefully by falling back to
    serial execution instead of raising RuntimeError into Kodi.
    """
    if not items:
        return []
    size = workers if workers else parallel_workers()
    size = max(1, min(int(size or 1), len(items), 8))
    results = [None] * len(items)
    try:
        pool = _get_pool(size, lane)
    except TypeError:
        pool = _get_pool(size)
    futures = {}
    try:
        for idx, item in enumerate(items):
            futures[pool.submit(func, item)] = idx
    except RuntimeError as exc:
        for future in list(futures.keys()):
            try:
                future.cancel()
            except Exception:
                pass
        try:
            _reset_pool_for_thread_failure(lane)
        except Exception:
            pass
        try:
            import xbmc
            xbmc.log('[DexHub] run_parallel fell back to serial scan: %s' % exc, xbmc.LOGWARNING)
        except Exception:
            pass
        for idx, item in enumerate(items):
            try:
                results[idx] = (item, func(item))
            except Exception as inner_exc:
                results[idx] = (item, inner_exc)
        return results
    except Exception as exc:
        for future in list(futures.keys()):
            try:
                future.cancel()
            except Exception:
                pass
        for idx, item in enumerate(items):
            try:
                results[idx] = (item, func(item))
            except Exception as inner_exc:
                results[idx] = (item, inner_exc)
        return results

    pending = set(futures.keys())
    try:
        iterator = as_completed(futures, timeout=timeout) if timeout else as_completed(futures)
        for future in iterator:
            pending.discard(future)
            idx = futures[future]
            try:
                results[idx] = (items[idx], future.result(timeout=0.1))
            except Exception as exc:
                results[idx] = (items[idx], exc)
    except TimeoutError as exc:
        for future in list(pending):
            future.cancel()
            idx = futures.get(future)
            if idx is not None and results[idx] is None:
                results[idx] = (items[idx], exc)
    except Exception as exc:
        for future in list(pending):
            future.cancel()
            idx = futures.get(future)
            if idx is not None and results[idx] is None:
                results[idx] = (items[idx], exc)
    for idx, row in enumerate(results):
        if row is None:
            results[idx] = (items[idx], TimeoutError('parallel job skipped'))
    return results


def supports_resource(provider, resource_name, media_type=None, item_id=None):
    manifest = provider.get('manifest') or {}
    for resource in manifest.get('resources', []):
        if resource == resource_name:
            return True
        if isinstance(resource, dict) and resource.get('name') == resource_name:
            types = resource.get('types') or []
            if media_type and types and media_type not in types:
                continue
            prefixes = resource.get('idPrefixes') or []
            if item_id and prefixes:
                lower_id = str(item_id).lower()
                ok = False
                for prefix in prefixes:
                    if lower_id.startswith(str(prefix).lower()):
                        ok = True
                        break
                if not ok:
                    continue
            return True
    return False


def iter_parallel(func, items, workers=None, timeout=None, lane='default'):
    """Yield (item, result_or_exception) tuples as each task finishes.

    v3.9.87: thread-safe/degraded mode. If Kodi/CoreELEC refuses to create a
    new worker thread (RuntimeError: can't start new thread), do not crash the
    addon dispatcher. Cancel whatever was queued and fall back to a serial scan
    for this call. It is slower for that one screen, but keeps playback/UI alive.
    """
    if not items:
        return
    size = workers if workers else parallel_workers()
    size = max(1, min(int(size or 1), len(items), 8))
    try:
        pool = _get_pool(size, lane)
    except TypeError:
        pool = _get_pool(size)
    futures = {}
    serial_deadline = time.monotonic() + float(timeout) if timeout else None

    def _serial_results():
        for item in items:
            if serial_deadline is not None and time.monotonic() >= serial_deadline:
                return
            try:
                yield (item, func(item))
            except Exception as inner_exc:
                yield (item, inner_exc)
    try:
        for idx, item in enumerate(items):
            futures[pool.submit(func, item)] = idx
    except RuntimeError as exc:
        # Thread quota exhausted. Cancel partial submissions, reset the pool,
        # then degrade gracefully to sequential work instead of bubbling the
        # RuntimeError to Kodi's dispatcher.
        for future in list(futures.keys()):
            try:
                future.cancel()
            except Exception:
                pass
        try:
            _reset_pool_for_thread_failure(lane)
        except Exception:
            pass
        try:
            import xbmc
            xbmc.log('[DexHub] iter_parallel fell back to serial scan: %s' % exc, xbmc.LOGWARNING)
        except Exception:
            pass
        for row in _serial_results():
            yield row
        return
    except Exception as exc:
        for future in list(futures.keys()):
            try:
                future.cancel()
            except Exception:
                pass
        for row in _serial_results():
            yield row
        return

    pending = set(futures.keys())
    try:
        iterator = as_completed(futures, timeout=timeout) if timeout else as_completed(futures)
        for future in iterator:
            pending.discard(future)
            idx = futures[future]
            try:
                yield (items[idx], future.result(timeout=0.1))
            except Exception as exc:
                yield (items[idx], exc)
    except TimeoutError:
        return
    except Exception:
        return
    finally:
        for future in list(pending):
            try:
                future.cancel()
            except Exception:
                pass
        pending.clear()


def _reset_pool_for_thread_failure(lane=None):
    global _POOL, _POOL_SIZE
    lane = _physical_lane(lane) if lane else ''
    with _POOL_LOCK:
        lanes = [lane] if lane else list(_LANE_POOLS.keys())
        for key in lanes:
            pool = _LANE_POOLS.pop(key, None)
            _LANE_POOL_SIZES.pop(key, None)
            if pool is None:
                continue
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                try:
                    pool.shutdown(wait=False)
                except Exception:
                    pass
            except Exception:
                pass
        if not lane or lane == 'browse':
            _POOL = None
            _POOL_SIZE = 0


def clear_http_cache():
    removed = 0
    with _HTTP_MEM_LOCK:
        _HTTP_MEM.clear()
        _HTTP_MEM_ORDER[:] = []
    try:
        if os.path.isdir(HTTP_CACHE_DIR):
            for name in os.listdir(HTTP_CACHE_DIR):
                path = os.path.join(HTTP_CACHE_DIR, name)
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                        removed += 1
                except Exception:
                    continue
    except Exception:
        return removed
    return removed
