# -*- coding: utf-8 -*-
"""Token-bucket rate limiter to keep DexHub a polite citizen.

Some Stremio addons (Cinemeta, Torrentio variants, AIOStreams) enforce
per-IP rate limits and start returning 429 Too Many Requests when several
widgets fire simultaneously on a busy home screen. This module gives the
HTTP layer a simple bucket per host with sane defaults that the user can
override in settings.

Usage:
    from .ratelimit import limiter
    limiter.acquire('api.themoviedb.org')   # blocks up to N seconds if needed
    response = requests.get(url)

Buckets are created on demand; each holds N tokens that refill at N/sec.
"""
import threading
import time

from .log import log

# Per-host policies. Conservative defaults; tweak via set_policy() if a host
# is known to be more permissive (or stricter).
_DEFAULT_POLICY = (10, 5.0)  # max 10 in flight, 5/sec sustained refill

_HOST_POLICIES = {
    # TMDb v3 free tier: 50 requests / second per IP — we stay well under
    # Nuvio-backed Collections can fan out into many TMDb calls. Keep a
    # deliberately smaller burst so Kodi widgets do not trip upstream guards.
    'api.themoviedb.org':       (12, 6.0),
    # Nuvio is a shared Supabase/PostgREST backend; short bursts from Home +
    # Collections + progress sync are much more expensive than normal catalog
    # reads. Serialize Nuvio cloud requests; persistent cooldown handles backend throttling.
    'api.nuvio.tv':             (1, 0.5),
    'api.strem.io':             (4, 2.0),
    # Trakt: 1000/5min for unauthenticated, less for auth. Be polite.
    'api.trakt.tv':             (12, 4.0),
    # Cinemeta: friendly but heavily concurrent home screens hit it
    'v3-cinemeta.strem.io':     (20, 10.0),
    # Fanart.tv: officially asks for "be reasonable"
    'webservice.fanart.tv':     (8, 3.0),
    # Plex.tv (discover): per-token rate-limited
    'discover.provider.plex.tv': (6, 2.0),
}


class _Bucket:
    __slots__ = ('capacity', 'rate', 'tokens', 'last', 'lock')

    def __init__(self, capacity, rate):
        self.capacity = float(capacity)
        self.rate = float(rate)
        self.tokens = float(capacity)
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, max_wait=3.0):
        """Block up to max_wait seconds for a token. Returns True on success."""
        deadline = time.monotonic() + max_wait
        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.last
                if elapsed > 0:
                    self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                    self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                # How long until at least 1 token is available?
                wait_for = (1.0 - self.tokens) / self.rate
            if time.monotonic() + wait_for > deadline:
                # Soft timeout — let the caller proceed anyway, the upstream
                # will tell us if it's overloaded.
                return False
            time.sleep(min(wait_for, 0.25))


class _Limiter:
    def __init__(self):
        self._buckets = {}
        self._lock = threading.Lock()

    def _bucket_for(self, host):
        host = (host or '').lower()
        with self._lock:
            b = self._buckets.get(host)
            if b is None:
                policy = _HOST_POLICIES.get(host) or _DEFAULT_POLICY
                b = _Bucket(*policy)
                self._buckets[host] = b
            return b

    def acquire(self, host, max_wait=3.0):
        if not host:
            return True
        ok = self._bucket_for(host).acquire(max_wait)
        if not ok:
            log.warn('RATELIMIT', 'soft timeout for %s', host)
        return ok

    def set_policy(self, host, capacity, rate):
        """Override a host's policy at runtime (e.g. when TMDb returns 429)."""
        with self._lock:
            self._buckets[host.lower()] = _Bucket(capacity, rate)
            _HOST_POLICIES[host.lower()] = (capacity, rate)


limiter = _Limiter()


def host_of(url):
    """Cheap urlparse wrapper. Returns lowercased host or ''."""
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or '').lower()
    except Exception:
        return ''
