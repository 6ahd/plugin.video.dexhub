# -*- coding: utf-8 -*-
"""Lightweight Plex/Emby/Plexio health monitor.

Pings each user-configured Plex/Plexio/Emby/StreamBridge endpoint every
N minutes and stores the result on Window(10000) so skin widgets and
status indicators can reflect the live state without their own polling.

Status states:
    green  → responded in < 1500ms
    amber  → responded in 1500-4000ms (slow but usable)
    red    → no response or HTTP error in last cycle

Properties published:
    dexhub.health.<name>.status   → green | amber | red
    dexhub.health.<name>.latency  → '%d' ms (last successful)
    dexhub.health.<name>.checked  → unix timestamp of last check
    dexhub.health.<name>.url      → host of the endpoint (for tooltip)

The service.py loop calls run_check_cycle() once per CHECK_INTERVAL.
"""
import time
import urllib.error
import urllib.request

import xbmcgui

from .log import log

_W = 10000
CHECK_INTERVAL_SECONDS = 300  # 5 minutes
TIMEOUT_S = 4


def _ping(url):
    """Return (status, latency_ms). status is one of 'green','amber','red'."""
    if not url:
        return ('red', 0)
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DexHub-Health/3.9'})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if 200 <= resp.status < 400:
                if elapsed_ms < 1500:
                    return ('green', elapsed_ms)
                if elapsed_ms < 4000:
                    return ('amber', elapsed_ms)
                return ('amber', elapsed_ms)
            return ('red', elapsed_ms)
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # 401/403 still means the server IS up — just our auth is wrong;
        # treat as amber so the user knows it's reachable.
        if e.code in (401, 403):
            return ('amber', elapsed_ms)
        return ('red', elapsed_ms)
    except Exception:
        return ('red', 0)


def _publish(name, status, latency_ms, url):
    try:
        win = xbmcgui.Window(_W)
        slug = ''.join(c for c in name.lower() if c.isalnum())[:30] or 'unknown'
        win.setProperty('dexhub.health.%s.status' % slug, status)
        win.setProperty('dexhub.health.%s.latency' % slug, '%d' % int(latency_ms or 0))
        win.setProperty('dexhub.health.%s.checked' % slug, '%d' % int(time.time()))
        if url:
            try:
                from urllib.parse import urlparse
                host = urlparse(url).hostname or url
                win.setProperty('dexhub.health.%s.url' % slug, host)
            except Exception:
                pass
        win.setProperty('dexhub.health.%s.name' % slug, name)
    except Exception as exc:
        log.warn('HEALTH', 'publish %s failed: %s', name, exc)


def _collect_endpoints():
    """Walk the user's provider list and pick out Plex/Plexio/Emby/Streambridge
    base URLs. Returns [(name, ping_url), ...]."""
    endpoints = []
    try:
        from .dexhub import store
        providers = store.list_providers() or []
    except Exception:
        return endpoints

    for prov in providers:
        if not isinstance(prov, dict):
            continue
        name = (prov.get('name') or prov.get('id') or '')[:40]
        manifest = prov.get('manifest') or {}
        haystack = ' '.join([
            str(prov.get('name') or ''),
            str(prov.get('id') or ''),
            str(prov.get('base_url') or ''),
            str(prov.get('manifest_url') or ''),
            str(manifest.get('name') or ''),
            str(manifest.get('id') or ''),
        ]).lower()
        is_relevant = any(h in haystack for h in (
            'plex', 'plexio', 'plexbridge', 'emby', 'streambridge', 'jellyfin', 'dexbridge'
        ))
        if not is_relevant:
            continue
        # Best ping target is the manifest URL — guaranteed to exist for a
        # registered Stremio provider, doesn't need auth, fast.
        ping_url = (prov.get('manifest_url') or prov.get('base_url') or '').strip()
        if not ping_url:
            continue
        endpoints.append((name, ping_url))
    return endpoints


def run_check_cycle():
    """Single pass over all configured Plex/Emby endpoints.

    Designed to be called from service.py's main loop every
    CHECK_INTERVAL_SECONDS.
    """
    endpoints = _collect_endpoints()
    if not endpoints:
        return 0
    checked = 0
    for name, url in endpoints:
        status, latency = _ping(url)
        _publish(name, status, latency, url)
        log.debug('HEALTH', '%s → %s (%dms)', name, status, latency)
        checked += 1
    return checked


