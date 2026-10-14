# -*- coding: utf-8 -*-
"""Faster "Best quality automatically" playback (v5.10.295).

Only used when the playback mode is "Best quality automatically". The source
picker never goes through here.

1. collect_early: stop waiting for slow add-ons. The first (highest priority)
   add-on is always awaited; after that, once a playable row exists, the
   others get a short grace period instead of their full timeout.
2. verify_pick: the top few rows are checked together with a tiny ranged
   request, and the first one that answers, in ranking order, is played.
   A faster lower-ranked row never wins over a working higher-ranked one.
3. Show affinity: the stream family (bingeGroup / add-on / service) that
   autoplay used for a series is remembered per show, so the next episode
   prefers the same release family.
"""
import json
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, wait

GRACE_SECONDS = 2.0      # extra wait for other add-ons after the first playable row
HARD_SECONDS = 9.0       # never wait longer than this once any row exists
VERIFY_TOP = 3           # rows checked in parallel
VERIFY_TIMEOUT = 4.0
CHECKABLE_TYPES = ('debrid', 'direct')
_AFFINITY_MAX = 300
_AFFINITY_LOCK = threading.Lock()


def _log(xbmc, message, level=None):
    try:
        xbmc.log('[DexHub] autopick: %s' % message, xbmc.LOGINFO if level is None else level)
    except Exception:
        pass


def collect_early(targets, fetch, on_data, pool, xbmc=None, grace=GRACE_SECONDS,
                  hard=HARD_SECONDS, has_rows=None):
    """Run fetch(target) for all targets and feed results to on_data(target, data).

    Returns once every target finished, or once the first target answered and
    a playable row has been available for `grace` seconds, or once rows have
    existed for `hard` seconds. Late results are ignored (their threads end on
    their own request timeout).
    """
    targets = list(targets or [])
    if not targets:
        return 0
    started = time.monotonic()
    futures = {}
    for index, target in enumerate(targets):
        try:
            futures[pool.submit(fetch, target)] = index
        except RuntimeError:
            # Thread quota exhausted: finish the rest inline.
            for rest in targets[index:]:
                try:
                    data = fetch(rest)
                except Exception as exc:
                    data = exc
                if data and not isinstance(data, Exception):
                    on_data(rest, data)
            break
    pending = set(futures)
    first_done = False
    rows_since = None
    while pending:
        now = time.monotonic()
        if rows_since is not None:
            if first_done and now - rows_since >= grace:
                break
            if now - rows_since >= hard:
                break
            limit = max(0.05, min(rows_since + (grace if first_done else hard) - now, 1.0))
        else:
            limit = 1.0
        done, pending = wait(pending, timeout=limit, return_when=FIRST_COMPLETED)
        for future in done:
            index = futures[future]
            if index == 0:
                first_done = True
            try:
                data = future.result(timeout=0)
            except Exception as exc:
                data = exc
            if data and not isinstance(data, Exception):
                try:
                    on_data(targets[index], data)
                except Exception as exc:
                    _log(xbmc, 'append failed: %s' % exc)
        if rows_since is None and (has_rows() if has_rows else False):
            rows_since = time.monotonic()
    if pending:
        _log(xbmc, 'started after %.1fs without %d slow add-on(s) of %d'
             % (time.monotonic() - started, len(pending), len(targets)))
        for future in pending:
            try:
                future.cancel()
            except Exception:
                pass
    return len(targets) - len(pending)


def _headers(raw):
    pairs = []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        pairs = [tuple(p) for p in raw if isinstance(p, (list, tuple)) and len(p) == 2]
    return {str(k): str(v) for k, v in pairs if k and v not in (None, '')}


def url_answers(url, headers=None, timeout=VERIFY_TIMEOUT):
    """True when the URL serves media: a 1 byte ranged GET, redirects followed."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    request_headers = {'User-Agent': 'Kodi/22.0', 'Range': 'bytes=0-0', 'Accept': '*/*'}
    request_headers.update(_headers(headers))
    try:
        response = urlopen(Request(url, headers=request_headers), timeout=timeout)
    except HTTPError as exc:
        return exc.code in (416,)  # range not satisfiable still means the file exists
    except Exception:
        return False
    try:
        status = getattr(response, 'status', None) or response.getcode()
        kind = (response.headers.get('Content-Type') or '').lower()
        return 200 <= int(status) < 300 and not kind.startswith(('text/html', 'application/json'))
    except Exception:
        return False
    finally:
        try:
            response.close()
        except Exception:
            pass


def _checkable(entry):
    url = str(entry.get('stream_url') or '')
    return (url.startswith(('http://', 'https://')) and
            str(entry.get('source_type') or '').lower() in CHECKABLE_TYPES)


def verify_pick(entries, pool, headers_for=None, xbmc=None, top=VERIFY_TOP,
                timeout=VERIFY_TIMEOUT):
    """Index of the first row, in ranking order, that answers.

    Rows that cannot be checked (local servers, torrents, usenet) are trusted
    as before. If nothing answers, 0 is returned so behaviour never gets worse.
    """
    rows = list(entries or [])[:max(1, top)]
    if not rows or not _checkable(rows[0]):
        return 0
    started = time.monotonic()
    futures = {}
    for index, entry in enumerate(rows):
        if not _checkable(entry):
            break  # trusted row: nothing below it needs checking
        try:
            headers = headers_for(entry) if headers_for else None
        except Exception:
            headers = None
        try:
            futures[index] = pool.submit(url_answers, entry['stream_url'], headers, timeout)
        except RuntimeError:
            break
    results = {}
    for index in sorted(futures):
        try:
            results[index] = bool(futures[index].result(timeout=timeout + 1.0))
        except Exception:
            results[index] = False
        if results[index]:
            break  # a higher-ranked row works; lower ones do not matter
    for index in range(len(rows)):
        if index not in futures:
            chosen = index  # first trusted row after failed checks
            break
        if results.get(index):
            chosen = index
            break
    else:
        chosen = 0
    if chosen:
        _log(xbmc, 'row 1 did not answer; playing row %d (checked in %.1fs)'
             % (chosen + 1, time.monotonic() - started))
    return chosen


# ---------------------------------------------------------------- affinity

def _affinity_path(profile_dir):
    return os.path.join(profile_dir, 'autopick_affinity.json')


def _load(profile_dir):
    try:
        with open(_affinity_path(profile_dir), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def show_affinity(profile_dir, show_key):
    if not show_key:
        return {}
    row = _load(profile_dir).get(str(show_key)) or {}
    return row if isinstance(row, dict) else {}


def remember_show(profile_dir, show_key, entry):
    if not show_key or not isinstance(entry, dict):
        return
    row = {
        'binge_group': str(entry.get('binge_group') or ''),
        'source_addon': str(entry.get('source_addon') or ''),
        'source_indexer': str(entry.get('source_indexer') or ''),
        'source_service': str(entry.get('source_service') or ''),
        'source_type': str(entry.get('source_type') or ''),
        'provider_name': str(entry.get('provider_name_raw') or ''),
        'saved_at': int(time.time()),
    }
    if not any(row[key] for key in ('binge_group', 'source_addon', 'provider_name')):
        return
    with _AFFINITY_LOCK:
        data = _load(profile_dir)
        data[str(show_key)] = row
        if len(data) > _AFFINITY_MAX:
            ordered = sorted(data.items(), key=lambda kv: int((kv[1] or {}).get('saved_at') or 0),
                             reverse=True)
            data = dict(ordered[:_AFFINITY_MAX])
        try:
            os.makedirs(profile_dir, exist_ok=True)
            temp = _affinity_path(profile_dir) + '.tmp'
            with open(temp, 'w', encoding='utf-8') as handle:
                json.dump(data, handle)
            os.replace(temp, _affinity_path(profile_dir))
        except Exception:
            pass
