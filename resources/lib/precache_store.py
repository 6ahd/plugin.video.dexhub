# -*- coding: utf-8 -*-

"""
DexHub pre-cache store — holds silently-scraped stream entries for the NEXT
episode so the source picker is instant when the user finishes the current one.

Architecture:
  - In-memory dict keyed by (canonical_id, season, episode) -> (expires_at, entries)
  - Cache lives for the Kodi process; clears on restart
  - 30-minute TTL is long enough for a full episode binge, short enough that a
    user returning hours later still gets a fresh scrape
  - Thread-safe via a single lock; `consume()` removes after read so a single
    pre-cache slot is never reused for a different play session

NO UI, NO Kodi calls — this module is pure data. The producer (background
thread in plugin.py) writes results here; the consumer (`episode_streams` at
the top of its function body) reads them and skips network scraping when hot.
"""

import threading
import time

_DEFAULT_TTL_SECONDS = 30 * 60  # 30 minutes

_lock = threading.Lock()
_store = {}  # {(canonical_id, season, episode): (expires_at, entries_list, meta_dict)}
_inflight = set()  # keys currently being pre-scraped (avoid double-scrape)


def _now():
    return time.time()


def _make_key(canonical_id, season, episode):
    try:
        return (str(canonical_id or ''), int(season or 0), int(episode or 0))
    except Exception:
        return (str(canonical_id or ''), 0, 0)


# --------------------------------------------------------------------------- #
#  Producer side — called from the background pre-scrape thread               #
# --------------------------------------------------------------------------- #

def put(canonical_id, season, episode, entries, meta=None, ttl=_DEFAULT_TTL_SECONDS):
    """Store the pre-scraped entries for an episode."""
    if not entries:
        return False
    key = _make_key(canonical_id, season, episode)
    expires_at = _now() + ttl
    with _lock:
        _store[key] = (expires_at, list(entries), dict(meta or {}))
        _inflight.discard(key)
    return True


def mark_inflight(canonical_id, season, episode):
    """Reserve a slot — call before starting a background scrape so a second
    trigger for the same episode doesn't spawn a duplicate scrape thread.
    Returns True if the caller got the slot, False if it was already taken."""
    key = _make_key(canonical_id, season, episode)
    with _lock:
        if key in _inflight:
            return False
        if key in _store:
            entry = _store[key]
            if entry[0] >= _now():
                return False  # fresh cache already exists
        _inflight.add(key)
    return True


def release_inflight(canonical_id, season, episode):
    """Drop the in-flight reservation (call from a finally block on the worker
    so a crash doesn't leave the slot locked forever)."""
    key = _make_key(canonical_id, season, episode)
    with _lock:
        _inflight.discard(key)


# --------------------------------------------------------------------------- #
#  Consumer side — called from episode_streams()                              #
# --------------------------------------------------------------------------- #

def get(canonical_id, season, episode):
    """Peek without removing. Returns (entries, meta) or None."""
    key = _make_key(canonical_id, season, episode)
    with _lock:
        entry = _store.get(key)
        if not entry:
            return None
        expires_at, entries, meta = entry
        if expires_at < _now():
            _store.pop(key, None)
            return None
        return (list(entries), dict(meta))


def consume(canonical_id, season, episode):
    """Read AND remove. Use this from episode_streams so a stale entry from a
    previous session never bleeds into a fresh play attempt."""
    key = _make_key(canonical_id, season, episode)
    with _lock:
        entry = _store.pop(key, None)
        _inflight.discard(key)
        if not entry:
            return None
        expires_at, entries, meta = entry
        if expires_at < _now():
            return None
        return (list(entries), dict(meta))



def clear():
    """Drop everything (e.g. settings changed, addon home reopened)."""
    with _lock:
        _store.clear()
        _inflight.clear()


def stats():
    """Diagnostics — count of stored vs. in-flight keys."""
    with _lock:
        return {'cached': len(_store), 'inflight': len(_inflight)}
