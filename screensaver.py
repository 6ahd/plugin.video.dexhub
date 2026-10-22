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

if sys.argv and str(sys.argv[0]).startswith('plugin://'):
    # v5.10.378: Kodi 22 beta1 runs this file for a plugin:// address it
    # resolves for xbmc.Player().play() (TMDb Helper's player from an Arctic
    # Fuse 3 widget): the screensaver opened instead of the source search and
    # the playback failed. Such a call is the add-on's, so default.py takes it.
    # (run in this __main__: Kodi finds the add-on's id there; runpy's own
    # __main__ made xbmcaddon.Addon() fail)
    import xbmc
    xbmc.log('[DexHub] Kodi ran the screensaver entry for %s%s: handed to default.py'
             % (sys.argv[0], sys.argv[2] if len(sys.argv) > 2 else ''), xbmc.LOGINFO)
    _entry = os.path.join(addon_path, 'default.py')
    with open(_entry, 'rb') as _handle:
        _code = compile(_handle.read(), _entry, 'exec')
    exec(_code, globals())
else:
    from resources.lib.homeui.screensaver import run

    run()
