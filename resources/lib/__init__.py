# -*- coding: utf-8 -*-
# Prepare Apple Python before any Dex Hub module imports urllib.request.
# All Kodi entry points import this package in their own Python invoker.
# Keep other platforms' cold starts free of extra module imports.
import sys as _sys

if _sys.platform in ('darwin', 'ios', 'tvos'):
    from .apple_compat import ensure_scproxy as _ensure_scproxy
    _ensure_scproxy()
    del _ensure_scproxy

del _sys
