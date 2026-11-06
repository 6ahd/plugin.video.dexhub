# -*- coding: utf-8 -*-
"""What may follow a redirect (v5.10.530), for both ways Dex Hub reads the web:
the urllib3 pool (dexhub.client.pool_get) and urllib (redirect_guard.py).

Origins are compared whole: scheme, host and port. Only the headers named here
reach another origin, so a new token header is safe without being listed. A
request carrying private headers never goes from https to http.
"""
from urllib.error import URLError
from urllib.parse import urlsplit

SAFE_HEADERS = frozenset(('accept', 'accept-encoding', 'accept-language', 'content-type',
                          'range', 'user-agent'))
# not sent on to another origin, but no reason to refuse https to http either
_NOT_SECRET = SAFE_HEADERS | frozenset(('origin', 'referer'))
_DEFAULT_PORTS = {'http': 80, 'https': 443}


class RedirectRefused(URLError):
    """A redirect that will not be followed. The text never names the address:
    it can carry a key or a token."""


def origin(url):
    """(scheme, host, port) with the port filled in, or RedirectRefused."""
    try:
        parts = urlsplit(url)
        scheme = (parts.scheme or '').lower()
        host = (parts.hostname or '').lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        raise RedirectRefused('redirect to a bad address')
    if scheme not in _DEFAULT_PORTS or not host:
        raise RedirectRefused('redirect to an unsupported address')
    return scheme, host, port


def next_headers(sent, source, target):
    """The headers for the next hop, or RedirectRefused."""
    if source == target:
        return sent
    private = any(k.lower() not in _NOT_SECRET for k in sent)
    if private and source[0] == 'https' and target[0] == 'http':
        raise RedirectRefused('refused a redirect from https to http')
    # http to https on the same host and standard ports is the same server
    if source[1] == target[1] and source[0] == 'http' and target[0] == 'https' \
            and source[2] == 80 and target[2] == 443:
        return sent
    return {k: v for k, v in sent.items() if k.lower() in SAFE_HEADERS}
