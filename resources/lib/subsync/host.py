# -*- coding: utf-8 -*-
"""Which add-on carries this copy of the subtitle engine (v5.10.597).

The same files run in Dex Hub (resources/lib/subsync) and in DexSubtitles
(service.subtitles.dexworld/resources/lib/subsync, copied by
tools/sync_dexsubtitles.py): one engine, one set of fixes. This module is the
only place that knows the difference: the add-on's id (its settings), its
queue folder, and its messages' language (Dex Hub's English table; DexSubtitles
speaks Arabic).
"""
import os

DEXSUBTITLES = 'service.subtitles.dexworld'
DEXHUB = 'plugin.video.dexhub'
ADDON_ID = DEXSUBTITLES if DEXSUBTITLES in os.path.abspath(__file__) else DEXHUB
IS_DEXHUB = ADDON_ID == DEXHUB
# Dex Hub's own queue, not DexSubtitles' autosync_queue (v5.10.590: with both installed,
# one took the other's jobs and dropped them)
QUEUE_NAME = 'dexhub_queue' if IS_DEXHUB else 'autosync_queue'
NAME = 'Dex Hub' if IS_DEXHUB else 'DexSubtitles'
AGENT = 'DexHub-AutoSync/2 (Kodi)' if IS_DEXHUB else 'DexSubtitles-AutoSync/2 (Kodi)'


def log_prefix(part):
    return ('[DexHub] %s: ' % part) if IS_DEXHUB else ('[DexSubtitles.%s] ' % part.replace(' ', ''))
_ADDON = [None]


def addon():
    if _ADDON[0] is None:
        import xbmcaddon
        _ADDON[0] = xbmcaddon.Addon(ADDON_ID)
    return _ADDON[0]


def tr(text):
    if not IS_DEXHUB:
        return text
    try:
        from ..i18n import tr as dexhub_tr
        return dexhub_tr(text)
    except Exception:
        return text
