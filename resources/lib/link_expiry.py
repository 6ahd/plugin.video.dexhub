# -*- coding: utf-8 -*-
"""When a source's signed link stops working (v5.10.595).

Some Stremio add-ons hand out signed links that live only minutes. ToastFlix
(Nov 2026) answers with an HLS link through its own proxy whose real address
rides base64 in the `d` parameter and carries `e=<epoch ms>` three minutes
ahead. Dex Hub kept that answer for five minutes in the source results cache
and thirty in the next-episode pre-cache, so a play from either one opened a
dead link (403). The caches use `earliest()` to drop such an answer before its
links expire.

Pure functions, no Kodi calls.
"""
import base64
import re
import time
from urllib.parse import parse_qsl, unquote, urlsplit

# Query names that carry an absolute expiry time on common CDNs and proxies.
_NAMES = ('e', 'exp', 'expires', 'expiry', 'expire', 'expires_at', 'expiresat',
          'expiration', 'deadline', 'validto', 'valid_to')
_DIGITS = re.compile(r'^\d{10}(\d{3})?$')
_B64 = re.compile(r'^[A-Za-z0-9_\-+/]{24,}={0,2}$')
_MAX_AHEAD = 30 * 86400.0


def _as_epoch(value, now):
    value = str(value or '').strip()
    if not _DIGITS.match(value):
        return 0.0
    stamp = float(value) / (1000.0 if len(value) == 13 else 1.0)
    # an absolute time near now, not an id or a duration
    if now - 86400.0 <= stamp <= now + _MAX_AHEAD:
        return stamp
    return 0.0


def _inner_url(value):
    value = str(value or '').strip()
    low = value.lower()
    if low.startswith(('http://', 'https://')):
        return value
    if low.startswith(('http%3a', 'https%3a')):
        return unquote(value)
    if not _B64.match(value):
        return ''
    try:
        text = value.replace('-', '+').replace('_', '/')
        text += '=' * (-len(text) % 4)
        text = base64.b64decode(text).decode('utf-8')
    except Exception:
        return ''
    return text if text.lower().startswith(('http://', 'https://')) else ''


def expiry(url, now=None, _depth=0):
    """Earliest absolute expiry in `url` (epoch seconds), or 0 when none."""
    now = time.time() if now is None else now
    url = str(url or '').split('|', 1)[0].strip()
    if not url or _depth > 2:
        return 0.0
    try:
        query = urlsplit(url).query
    except Exception:
        return 0.0
    found = []
    for name, value in parse_qsl(query, keep_blank_values=False):
        if name.lower() in _NAMES:
            stamp = _as_epoch(value, now)
            if stamp:
                found.append(stamp)
                continue
        inner = _inner_url(value)
        if inner:
            stamp = expiry(inner, now, _depth + 1)
            if stamp:
                found.append(stamp)
    return min(found) if found else 0.0


def earliest(rows, now=None, keys=('url', 'stream_url', 'externalUrl')):
    """Earliest expiry over the links of stream rows or Dex Hub entries."""
    now = time.time() if now is None else now
    found = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in keys:
            stamp = expiry(row.get(key), now)
            if stamp:
                found.append(stamp)
    return min(found) if found else 0.0
