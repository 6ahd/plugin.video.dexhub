# -*- coding: utf-8 -*-
"""Allow urllib imports on Apple Kodi builds without the native _scproxy.

Some tvOS builds report Darwin to Python but omit the macOS extension used
by urllib.request to discover system proxies. Install only its two small
proxy-discovery helpers when the extension itself is missing. urllib still
reads environment proxies and explicit ProxyHandler settings as usual.
No network library is imported here and a working native module is kept.
"""
import sys


def ensure_scproxy():
    """Install a missing Apple proxy helper before importing urllib.request."""
    if sys.platform not in ('darwin', 'ios', 'tvos'):
        return False
    try:
        import _scproxy
    except ModuleNotFoundError as exc:
        # Do not hide a broken dependency of an otherwise present extension.
        if exc.name != '_scproxy':
            raise
    else:
        return False

    from types import ModuleType
    compat = ModuleType('_scproxy', 'Dex Hub fallback for absent Apple system proxy discovery.')

    def _get_proxy_settings():
        return {'exclude_simple': False, 'exceptions': []}

    def _get_proxies():
        return {}

    compat._get_proxy_settings = _get_proxy_settings
    compat._get_proxies = _get_proxies
    # Do not replace an extension or another fallback loaded in the meantime.
    return sys.modules.setdefault('_scproxy', compat) is compat
