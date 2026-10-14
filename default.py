# -*- coding: utf-8 -*-
import os
import sys
addon_path = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from resources.lib.tvos_compat import install as install_tvos_compat
install_tvos_compat()

from resources.lib.profile_scope import initialize as initialize_profiles
initialize_profiles()

from resources.lib.tvos_assets import publish_asset_root
publish_asset_root()

from resources.lib.bootstrap import run

if __name__ == '__main__':
    run()
