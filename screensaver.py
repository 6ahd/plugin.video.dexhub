# -*- coding: utf-8 -*-
"""Kodi's entry for the Dex Hub screensaver (extension point xbmc.ui.screensaver).

See resources/lib/homeui/screensaver.py.
"""
import os
import sys

addon_path = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from resources.lib.homeui.screensaver import run

run()
