# -*- coding: utf-8 -*-
"""Whether library items play from their own server or go through a search.

v5.10.83. library_playback_mode is a labelenum with localized labels, and a
labelenum stores the VISIBLE label: choosing "Direct from library source" in
Kodi's settings page saved "Direct from library source" (or its Arabic text),
never the value "native". Every check compared against "native", so direct
play could only be switched on from Dex Hub's own dialog, which writes the raw
value; chosen from the settings page it silently kept searching. The same
pitfall is already handled for the metadata source in meta_source.py.
"""


def library_direct(addon=None):
    try:
        if addon is None:
            import xbmcaddon
            addon = xbmcaddon.Addon()
        raw = str(addon.getSetting('library_playback_mode') or '').strip().lower()
    except Exception:
        return False
    if not raw:
        return False
    if raw in ('native', '0', 'direct'):
        return True
    if raw in ('dexhub', '1') or 'dex hub' in raw or 'dexhub' in raw:
        return False
    return 'direct' in raw or u'\u0645\u0628\u0627\u0634\u0631' in raw  # "مباشر"
