# -*- coding: utf-8 -*-
import os
import sys

addon_path = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from resources.lib.plugin import run
# Invalidate i18n cache on each invocation so language flips apply without restart.
try:
    from resources.lib.i18n import reset_language_cache as _reset_lang
    _reset_lang()
except Exception:
    pass

if __name__ == '__main__':
    run()
