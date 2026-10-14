# -*- coding: utf-8 -*-
"""AutoSync in Dex Hub's service, only while DexSubtitles is not enabled (v5.10.117).

DexSubtitles' own service runs the same queue and subtitle watcher: with
both add-ons enabled that is the one that runs, and Dex Hub waits; turning
DexSubtitles off (or removing it) hands the work to Dex Hub within seconds.
Nothing is imported while it is not Dex Hub's turn or AutoSync is off.
"""
import xbmc

OTHER = 'service.subtitles.dexworld'


def other_enabled():
    """DexSubtitles is installed and enabled, and its service is there: it syncs.

    v5.10.240: ABUKARIM TOOLS (3.1.15 and later) deletes DexSubtitles 5.7.3's
    service_auto.py at every start (it takes it for its own retired
    auto-download service): DexSubtitles was enabled but nothing synced, and
    Dex Hub waited for it. Without its service file Dex Hub syncs."""
    try:
        if not xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % OTHER):
            return False
    except Exception:
        return False
    try:
        import os
        import xbmcaddon
        import xbmcvfs
        path = xbmcvfs.translatePath(xbmcaddon.Addon(OTHER).getAddonInfo('path'))
        return any(os.path.isfile(os.path.join(path, name))
                   for name in ('service_autosync.py', 'service_auto.py'))
    except Exception:
        return True


def enabled():
    try:
        import xbmcaddon
        value = (xbmcaddon.Addon('plugin.video.dexhub').getSetting('autosync_enabled') or 'true').lower()
        return value not in ('false', '0', 'no', 'off')
    except Exception:
        return True


def run(monitor):
    player = None
    watcher = None
    autosync = None
    mine = None
    checked = 0.0
    import time
    while not monitor.abortRequested():
        now = time.time()
        if mine is None or now - checked > 10.0:
            checked = now
            turn = enabled() and not other_enabled()
            if turn != mine:
                mine = turn
                xbmc.log('[DexHub] subtitle AutoSync: %s' % (
                    'runs in Dex Hub' if mine else
                    ('left to DexSubtitles' if other_enabled() else 'off')), xbmc.LOGINFO)
        if not mine:
            if monitor.waitForAbort(10):
                break
            continue
        try:
            if autosync is None:
                from . import autosync as _autosync
                from . import dexwatch as _dexwatch
                autosync = _autosync
                player = xbmc.Player()
                watcher = _dexwatch.Watcher(autosync, autosync._setting_bool, autosync.TEMP_DIR)
            autosync.process_queue_once(player)
            watcher.tick(player)
            autosync.flush_notice()
        except Exception as exc:
            xbmc.log('[DexHub] subtitle AutoSync: %s' % exc, xbmc.LOGWARNING)
        if monitor.waitForAbort(1):
            break
