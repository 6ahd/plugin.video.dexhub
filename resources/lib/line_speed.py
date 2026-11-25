# -*- coding: utf-8 -*-
"""How fast the viewer's line brings a stream (v5.10.612).

The sources page marks the copies the line will not carry (a copy's bitrate is its size over the
title's running time), and asks before one of them plays.

The speed is measured when playback stops, never during it: the service's player keeps the
playing link (record), and on stop it reads a few seconds of the same file (sample). That is the
real path, the debrid or the server and the line together, and the video is not slowed by it.
Kept: the last samples in line_speed.json. The line is the fastest server seen (each server's
median, then the highest): a slow server is that server, not the line.
"""
import json
import os
import threading
import time

import xbmc

FILE = 'line_speed.json'
KEEP = 24                 # samples kept
FRESH_DAYS = 30           # older samples are not used
READ_SECONDS = 5.0        # how long one sample measures (after the warm-up)
WARM_SECONDS = 1.0        # the connection speeds up first: not measured
READ_BYTES = 48 * 1048576
MIN_BYTES = 3 * 1048576   # a sample shorter than this says nothing
MIN_FILE = 300 * 1048576  # a film or an episode, not a trailer or a live channel
_LOCK = threading.Lock()
_PLAYING = {'url': ''}


def _log(msg):
    xbmc.log('[DexHub] line speed: %s' % msg, xbmc.LOGINFO)


def _path():
    import xbmcaddon
    import xbmcvfs
    folder = xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile'))
    return os.path.join(folder, FILE)


def _read():
    try:
        with open(_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data):
    try:
        path = _path()
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except Exception as exc:
        _log('not saved: %s' % exc)


def _samples():
    cutoff = time.time() - FRESH_DAYS * 86400
    # v2: measured after the warm-up (the first ones, read from the first byte, came out far too low)
    return [s for s in (_read().get('samples') or []) if isinstance(s, dict) and s.get('t', 0) >= cutoff
            and s.get('mbps', 0) > 0 and s.get('v') == 2]


def _median(values):
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def estimate():
    """(Mbit/s, number of samples): the fastest server's median; (0, 0) before the first sample."""
    by_host = {}
    for sample_ in _samples():
        by_host.setdefault(sample_.get('host') or '?', []).append(float(sample_['mbps']))
    if not by_host:
        return 0.0, 0
    return max(_median(v) for v in by_host.values()), sum(len(v) for v in by_host.values())


def fit(need_mbps, line=None):
    """'ok' (plays easily), 'tight' (close to what the line brings) or 'heavy' (likely to stop and
    buffer); '' when either number is unknown. Video needs headroom: a stream at 70 % of the line
    already stutters when the line dips."""
    if line is None:
        line, _count = estimate()
    if not need_mbps or not line:
        return ''
    ratio = float(need_mbps) / float(line)
    if ratio <= 0.7:
        return 'ok'
    if ratio <= 1.0:
        return 'tight'
    return 'heavy'


# ------------------------------------------------------------------ measuring
def _split(url):
    """Kodi's 'url|Header=value&...' into (url, headers)."""
    headers = {}
    if '|' in url:
        url, _sep, extra = url.partition('|')
        try:
            from urllib.parse import parse_qsl
            headers = dict(parse_qsl(extra))
        except Exception:
            headers = {}
    return url, headers


def _remote(url):
    """Only a stream fetched over the internet says anything about the line."""
    try:
        from urllib.parse import urlparse
        import ipaddress
        parts = urlparse(url)
        if parts.scheme not in ('http', 'https'):
            return False
        host = (parts.hostname or '').lower()
        if not host or host == 'localhost' or host.endswith('.local') or host.endswith('.lan'):
            return False
        try:
            address = ipaddress.ip_address(host)
            return not (address.is_private or address.is_loopback or address.is_link_local)
        except ValueError:
            pass
        lowered = parts.path.lower()
        return not (lowered.endswith('.m3u8') or lowered.endswith('.mpd') or '/live/' in lowered)
    except Exception:
        return False


def sample(url):
    """Reads the start of the file for a few seconds; returns Mbit/s (0 when it says nothing)."""
    url, headers = _split(url)
    if not _remote(url):
        return 0.0
    import urllib.request
    headers.setdefault('User-Agent', 'Mozilla/5.0 (Kodi; Dex Hub)')
    headers['Range'] = 'bytes=0-%d' % (READ_BYTES - 1)
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=8)
    except Exception as exc:
        _log('not measured (%s)' % exc.__class__.__name__)
        return 0.0
    try:
        total = 0
        content_range = response.headers.get('Content-Range') or ''
        if '/' in content_range:
            try:
                total = int(content_range.rsplit('/', 1)[1])
            except ValueError:
                total = 0
        if not total:
            try:
                total = int(response.headers.get('Content-Length') or 0)
            except ValueError:
                total = 0
        if total and total < MIN_FILE:
            return 0.0
        response.read(65536)               # the first bytes carry the server's wait, not the line
        warm = time.monotonic()
        while time.monotonic() - warm < WARM_SECONDS:
            if not response.read(262144):
                return 0.0
        got, started = 0, time.monotonic()
        while time.monotonic() - started < READ_SECONDS:
            chunk = response.read(262144)
            if not chunk:
                break
            got += len(chunk)
            if got >= READ_BYTES:
                break
        seconds = time.monotonic() - started
    except Exception as exc:
        _log('reading stopped (%s)' % exc.__class__.__name__)
        return 0.0
    finally:
        try:
            response.close()
        except Exception:
            pass
    if got < MIN_BYTES or seconds <= 0.2:
        return 0.0
    return got * 8.0 / seconds / 1000000.0


def add(mbps, host=''):
    with _LOCK:
        data = _read()
        samples = [s for s in (data.get('samples') or []) if isinstance(s, dict)]
        samples.append({'t': int(time.time()), 'mbps': round(float(mbps), 1), 'host': host[:60], 'v': 2})
        data['samples'] = samples[-KEEP:]
        _write(data)


def record(url):
    """The player started a stream (the service's player, onAVStarted)."""
    _PLAYING['url'] = url if isinstance(url, str) and _remote(_split(url)[0]) else ''


def measure_after_stop():
    """The player stopped: the stream it played is measured in the background."""
    url, _PLAYING['url'] = _PLAYING['url'], ''
    if not url:
        return

    def run():
        monitor = xbmc.Monitor()
        if monitor.waitForAbort(2):        # Kodi lets go of the stream first
            return
        if xbmc.Player().isPlaying():      # something else started: it must not be slowed
            return
        mbps = sample(url)
        if mbps:
            try:
                from urllib.parse import urlparse
                host = urlparse(_split(url)[0]).hostname or ''
            except Exception:
                host = ''
            add(mbps, host)
            line, count = estimate()
            _log('%.1f Mbit/s from %s (the line: about %.0f Mbit/s over %d sample(s))' % (mbps, host, line, count))
    threading.Thread(target=run, name='DexHubLineSpeed', daemon=True).start()
