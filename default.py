# -*- coding: utf-8 -*-
import os
import sys
addon_path = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

# v5.10.408: the screensaver's entry is this file too (xbmc.ui.screensaver,
# library default.py). Kodi 22 beta1 ran screensaver.py for the add-on's
# plugin:// calls, every widget and row of the Home, and the call then hung
# on "waiting on thread"; with one library there is no other file to run.
# A screensaver start has no plugin:// address.
if not (sys.argv and str(sys.argv[0]).startswith('plugin://')):
    # started as before from screensaver.py: nothing else loaded first
    from resources.lib.homeui.screensaver import run as _screensaver
    _screensaver()
    sys.exit(0)

from resources.lib.tvos_compat import install as install_tvos_compat
install_tvos_compat()

from resources.lib.profile_scope import initialize as initialize_profiles
initialize_profiles()

# v5.10.301: one settings snapshot per two seconds instead of a full settings
# read for every xbmcaddon.Addon() (the skin's rows built dozens a second)
from resources.lib.settings_cache import install_shared as _install_shared_settings
_install_shared_settings(2.0)

from resources.lib.tvos_assets import publish_asset_root
publish_asset_root()

from resources.lib.bootstrap import run

if __name__ == '__main__':
    run()
