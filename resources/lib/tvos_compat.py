# SPDX-License-Identifier: MIT
"""Bundled tvOS proxy bootstrap; independent of optional Kodi addon modules."""
import importlib.util
import sys
import types


def install():
    import xbmc
    if not xbmc.getCondVisibility('System.Platform.TVOS'):
        return False
    if '_scproxy' in sys.modules or importlib.util.find_spec('_scproxy') is not None:
        return False
    module = types.ModuleType('_scproxy')
    module.__spec__ = importlib.util.spec_from_loader('_scproxy', loader=None)
    module._get_proxy_settings = lambda: {'exclude_simple': False, 'exceptions': []}
    module._get_proxies = lambda: {}
    sys.modules['_scproxy'] = module
    return True
