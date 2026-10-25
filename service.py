# -*- coding: utf-8 -*-
import json
import os
import sys
import threading
import time
import xbmc

# Claim before expensive startup imports. A playback can wake the service
# safely when Kodi did not start it; exactly one interpreter owns callbacks.
if __name__ == '__main__':
    from resources.lib.service_lease import claim
    if not claim():
        sys.exit(0)
addon_path = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from resources.lib.tvos_compat import install as install_tvos_compat
install_tvos_compat()

from resources.lib.portable_restore import startup as restore_startup
restore_startup()

from resources.lib.profile_scope import initialize as initialize_profiles
initialize_profiles()

# v5.10.301: one settings snapshot for the service's watchers (they built a
# fresh xbmcaddon.Addon every second or two; on Apple TV each one reads and
# parses the whole settings file)
from resources.lib.settings_cache import install_shared as _install_shared_settings
_install_shared_settings(60.0)

# v5.10.301: the shipped default metadata source becomes TMDb in one language
try:
    from resources.lib.meta_mode import migrate_to_tmdb as _migrate_to_tmdb
    _migrate_to_tmdb()
except Exception:
    pass

import xbmcgui


# Trakt/Simkl and Nuvio/Stremio touch the same local playback state.  They
# must never run concurrently: apart from extra pressure, overlapping pulls
# can race over progress rows.  A non-blocking lock lets the later cycle defer
# cleanly instead of creating another waiting worker.
_SYNC_CYCLE_LOCK = threading.Lock()
# --- dexhub-402-patch ---
try:
    from resources.lib.i18n import tr as tr
except Exception:
    try:
        from i18n import tr as tr
    except Exception:
        def tr(_s):
            return _s



def _purge_http_cache():
    """Delete cached HTTP responses older than twice their max TTL. Keeps the
    cache dir bounded on long-running installs.

    Also purges:
      * special://temp/dexhub_subs/  → subtitle files older than 24h
      * addon_data/subtitle_cache/    → switched subtitle copies older than 7d
      * meta_cache.db expired rows
      * fanarttv_cache.db expired rows
    """
    import os as _os, time as _t
    try:
        from resources.lib.dexhub.client import HTTP_CACHE_DIR, catalog_ttl, meta_ttl
        max_ttl = max(catalog_ttl(), meta_ttl(), 3600) * 2
        if _os.path.isdir(HTTP_CACHE_DIR):
            now = _t.time()
            removed = 0
            for name in _os.listdir(HTTP_CACHE_DIR):
                path = _os.path.join(HTTP_CACHE_DIR, name)
                try:
                    if _os.path.isfile(path) and (now - _os.path.getmtime(path)) > max_ttl:
                        _os.remove(path)
                        removed += 1
                except Exception:
                    continue
            if removed:
                xbmc.log('[DexHub] purged %d stale http-cache files' % removed, xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] http-cache purge failed: %s' % exc, xbmc.LOGWARNING)

    # Subtitle files dir — wasn't being touched in earlier versions.
    try:
        import xbmcvfs
        subs_dir = xbmcvfs.translatePath('special://temp/dexhub_subs/')
        if _os.path.isdir(subs_dir):
            now = _t.time()
            cutoff = now - 86400  # 24h
            removed = 0
            for root, dirs, files in _os.walk(subs_dir):
                for fn in files:
                    fp = _os.path.join(root, fn)
                    try:
                        if _os.path.getmtime(fp) < cutoff:
                            _os.remove(fp)
                            removed += 1
                    except Exception:
                        continue
                # Drop empty subdirs left behind
                try:
                    if root != subs_dir and not _os.listdir(root):
                        _os.rmdir(root)
                except Exception:
                    pass
            if removed:
                xbmc.log('[DexHub] purged %d stale subtitle files' % removed, xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] subs purge failed: %s' % exc, xbmc.LOGWARNING)

    # Stable copies used only to preserve external subtitles across source
    # switches. Keep them for a week, then remove them so the cache stays
    # bounded even on devices that are rarely restarted.
    try:
        import xbmcvfs
        switch_subs_dir = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/subtitle_cache/')
        if _os.path.isdir(switch_subs_dir):
            cutoff = _t.time() - (7 * 86400)
            removed = 0
            for fn in _os.listdir(switch_subs_dir):
                fp = _os.path.join(switch_subs_dir, fn)
                try:
                    if _os.path.isfile(fp) and _os.path.getmtime(fp) < cutoff:
                        _os.remove(fp)
                        removed += 1
                except Exception:
                    continue
            if removed:
                xbmc.log('[DexHub] purged %d stale switched-subtitle files' % removed, xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] switched-subs purge failed: %s' % exc, xbmc.LOGWARNING)

    # SQLite caches — drop expired rows
    try:
        from resources.lib import meta_cache as _mc
        n = _mc.purge_expired()
        if n:
            xbmc.log('[DexHub] purged %d expired meta_cache rows' % n, xbmc.LOGINFO)
    except Exception:
        pass
    try:
        from resources.lib import fanarttv as _ft
        n = _ft.purge_expired()
        if n:
            xbmc.log('[DexHub] purged %d expired fanarttv rows' % n, xbmc.LOGINFO)
    except Exception:
        pass


_WIN = []


def _win():
    # v5.10.140: one Home window object for the service (each new one takes
    # Kodi's GUI lock to be built; see skinui.common.home)
    try:
        if not _WIN:
            _WIN.append(xbmcgui.Window(10000))
        return _WIN[0]
    except Exception:
        return None


def _interactive_busy(max_age=180.0, include_playback=True):
    """True while playback or a source/search interaction has priority."""
    try:
        if include_playback and xbmc.Player().isPlayingVideo():
            return True
    except Exception:
        pass
    try:
        win = _win()
        raw = win.getProperty('dexhub.interactive_busy') if win else ''
        return bool(raw and (time.time() - float(raw)) < float(max_age))
    except Exception:
        return False



def _setting(key, default=''):
    try:
        import xbmcaddon
        return xbmcaddon.Addon().getSetting(key) or default
    except Exception:
        return default



def _primary_player_mode():
    raw = str(_setting('catalog_click_mode', 'TMDb Helper') or 'TMDb Helper').strip().lower()
    compact = raw.replace(' ', '').replace('_', '').replace('-', '')
    if compact in ('tmdbhelper', 'helper', '1') or 'tmdb' in compact:
        return 'tmdbhelper'
    if compact in ('ask', 'askeverytime', '2') or raw in ('اسأل كل مرة', 'السؤال كل مرة'):
        return 'ask'
    return 'dexhub'



# v5.10.120: settings Dex Hub writes for itself (account and server caches,
# sync times, one time defaults). Each write wakes onSettingsChanged; a box's
# log had the skin's 40 rows worked out again (5 s each time) after every
# Plex server cache write and every account sync, a few times a minute.
_VOLATILE_SETTINGS = frozenset((
    'welcome_seen', 'wizard_completed', 'metadata_generation', 'last_account_sync_at',
    'plex_client_identifier', 'plex_servers_cache_json', 'ui_lang_autodetected',
    'dexhub_defaults_rev',
    'nuvio_token_keep', 'stremio_token_keep', 'trakt_token_keep',
    'switch_source_keymap_data',
    # v5.10.131: a theme (kept in step with skin.dexhub's colour theme)
    'theme_preset',
))


def _volatile_setting(key):
    return key in _VOLATILE_SETTINGS or key.endswith('_auth_json') or key.endswith('_token')


def _settings_snapshot():
    """{id: value} of the add-on's saved settings (its settings.xml)."""
    import re
    try:
        import xbmcvfs
        path = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/settings.xml')
        with open(path, 'r', encoding='utf-8') as handle:
            text = handle.read()
    except Exception:
        return {}
    out = {}
    for match in re.finditer(r'<setting id="([^"]+)"[^>]*?(?:/>|>(.*?)</setting>)', text, re.S):
        out[match.group(1)] = match.group(2) or ''
    return out


def _publish_badge_props():
    """v4.7.5: bridge the badge settings to window properties.

    Reused plugin interpreters cannot see settings saved by the main Kodi
    process, so they read the badges URL they started with. This service
    runs in a process Kodi notifies (onSettingsChanged) and republishes
    the live values; window properties are global and always fresh."""
    win = _win()
    if not win:
        return
    try:
        url = (_setting('elite_badges_json_url', '') or '').strip()
        win.setProperty('dexhub.badges.url', url or '-')
        # v5.4.3: this fallback said 'false' while the setting itself
        # defaults to 'true'. A profile that had never written the key
        # got 'false' PUBLISHED to the window property — which the
        # badge code trusts ahead of the setting — so ticking the box
        # in settings changed nothing until Kodi restarted.
        enabled = (_setting('elite_badges_enabled', 'true') or 'true').strip().lower()
        win.setProperty('dexhub.badges.enabled', enabled)
        from resources.lib import player_badges
        player_badges.publish_style()
        from resources.lib import ui_preferences
        ui_preferences.publish()
        ui_preferences.sync_skin_place()
    except Exception as exc:
        xbmc.log('[DexHub] badge props publish failed: %s' % exc, xbmc.LOGWARNING)
    try:
        from resources.lib import player_capsule      # v5.10.423: TinyPPI is its own again
        player_capsule.leftovers()
    except Exception as exc:
        xbmc.log('[DexHub] TinyPPI leftovers: %s' % exc, xbmc.LOGWARNING)


def _publish_core_props(last_sync=''):
    win = _win()
    if not win:
        return
    # Keep Kodi startup light: optional integrations are imported only when
    # this small status snapshot is actually published.
    try:
        from resources.lib import tmdbh_player
        tmdbh_available = '1' if tmdbh_player.has_tmdbhelper() else '0'
        tmdbh_installed = '1' if tmdbh_player.player_installed() else '0'
    except Exception:
        tmdbh_available = '0'
        tmdbh_installed = '0'
    tmdbh_primary = '1' if _primary_player_mode() == 'tmdbhelper' else '0'
    # v5.10.28: answer from settings instead of importing the Trakt module on
    # the service's main thread at boot; the module is only loaded once the
    # first background sync tick actually needs it.
    try:
        trakt_enabled = '1' if (_setting('enable_trakt', 'true') or 'true').strip().lower() == 'true' else '0'
        trakt_connected = '0'
        if trakt_enabled == '1':
            import json as _json
            import xbmcaddon as _xbmcaddon
            import xbmcvfs as _xbmcvfs
            _token_path = os.path.join(
                _xbmcvfs.translatePath(_xbmcaddon.Addon().getAddonInfo('profile')), 'trakt_token.json')
            if os.path.exists(_token_path):
                with open(_token_path, 'r', encoding='utf-8') as _fh:
                    _tok = _json.load(_fh) or {}
                trakt_connected = '1' if _tok.get('access_token') else '0'
    except Exception:
        trakt_enabled = '0'
        trakt_connected = '0'
    payload = {
        'dexhub.core.ready': '1',
        'dexhub.core.tmdbh.available': tmdbh_available,
        'dexhub.core.tmdbh.player_installed': tmdbh_installed,
        'dexhub.core.tmdbh.primary': tmdbh_primary,
        'dexhub.core.trakt.enabled': trakt_enabled,
        'dexhub.core.trakt.connected': trakt_connected,
        'dexhub.core.trakt.last_sync': str(last_sync or ''),
        'dexhub.core.formatter.enabled': '1' if ((_setting('enable_source_formatter', 'true') or 'true').lower() == 'true') else '0',
    }
    for k, v in payload.items():
        try:
            win.setProperty(k, v)
        except Exception:
            pass



def _invalidate_ui_caches():
    win = _win()
    if not win:
        return
    for key in ('dexhub.nextup_cache', 'dexhub.nextup_cache_ts', 'dexhub.fav_mirror_done'):
        try:
            win.clearProperty(key)
        except Exception:
            pass



def _sync_trakt_state(reason='manual'):
    # Trakt is optional and fairly heavy; do not import it during service
    # bootstrap on installations where it is never used.
    try:
        from resources.lib import trakt
    except Exception:
        _publish_core_props('')
        return False
    if not trakt.enabled():
        _publish_core_props('')
        return False
    try:
        if not trakt.authorized():
            _publish_core_props('')
            return False
    except Exception:
        _publish_core_props('')
        return False

    did_work = False
    try:
        if trakt.sync_enabled():
            # v5.10.208: smart sync: nothing new on Trakt, nothing to pull or redraw
            if reason == 'manual' or not trakt.unchanged('playback'):
                trakt.import_progress(limit=100, force=(reason == 'manual'))
                did_work = True
    except Exception as exc:
        xbmc.log('[DexHub] trakt progress sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)

    try:
        # v4.2.0: one merged snapshot (Trakt + Simkl + MDBList). Writing a
        # trakt-only snapshot here used to erase the other services' rows on
        # every service cycle.
        from resources.lib import favorites_store
        # v4.4.1: the merged mirror hits up to three external APIs; refreshing
        # it every service cycle is wasteful. 30-minute throttle via window
        # property (resets on Kodi restart) keeps sections fresh and light.
        import time as _t
        win = _win()
        last = 0.0
        try:
            last = float(win.getProperty('dexhub.mirror.last') or 0) if win else 0.0
        except Exception:
            last = 0.0
        include_trakt = (_setting('trakt_sync_watchlist', 'true') or 'true').lower() == 'true'
        if _t.time() - last >= 30 * 60:
            favorites_store.refresh_external_mirror(include_trakt=include_trakt)
            if win:
                win.setProperty('dexhub.mirror.last', str(int(_t.time())))
            did_work = True
        elif include_trakt and trakt.smart_sync() and not trakt.unchanged('watchlist'):
            # v5.10.208: a title added on Trakt shows here on the next cycle,
            # not up to 30 minutes later (one small request tells)
            favorites_store.refresh_external_mirror(include_trakt=True, others=False)
            did_work = True
    except Exception as exc:
        xbmc.log('[DexHub] watchlist mirror sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)

    if did_work:
        try:
            trakt.invalidate_cache('next_up_v1')
            trakt.invalidate_cache('/sync/playback/')
        except Exception:
            pass
        _invalidate_ui_caches()
        _publish_core_props(str(int(__import__('time').time())))
    else:
        _publish_core_props('')
    return did_work



def _sync_simkl_state(reason='manual'):
    """Apply activity-gated history before rebuilding next-episode suggestions.

    The persistent baseline survives Kodi restarts. Unchanged cycles do not
    download full histories or repeat the local watched import.
    """
    if (_setting('simkl_service_sync', 'true') or 'true').lower() != 'true':
        return False
    try:
        from resources.lib import simkl
    except Exception:
        return False
    try:
        if not (simkl.enabled() and simkl.authorized()):
            return False
    except Exception:
        return False
    did_work = False
    try:
        movies, episodes = simkl.import_watched()
        did_work = bool(movies or episodes)
    except Exception as exc:
        xbmc.log('[DexHub] simkl watched import failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)
    try:
        did_work = bool(simkl.sync_continue_watching(limit=60)) or did_work
    except Exception as exc:
        xbmc.log('[DexHub] simkl continue sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)
    if did_work:
        _invalidate_ui_caches()
    return did_work


def _sync_floppy_state(reason='manual'):
    """v5.10.147: Floppy's resume positions (when the Floppy add-on is set up
    and Continue Watching takes them)."""
    if (_setting('continue_floppy', 'true') or 'true').lower() == 'false':
        return False
    try:
        from resources.lib import floppy_progress
        changed = floppy_progress.sync(limit=60)
    except Exception as exc:
        xbmc.log('[DexHub] floppy progress sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)
        return False
    if changed:
        _invalidate_ui_caches()
    return bool(changed)


def _sync_interval_ms():
    try:
        minutes = int(_setting('trakt_service_sync_interval', '30') or '30')
    except Exception:
        minutes = 30
    # Keep the full set exposed by the simplified account screen.  Earlier
    # code silently collapsed the 120/240 minute choices back to 60 minutes.
    minutes = max(10, min(240, minutes))
    return minutes * 60 * 1000



def _background_sync_loop(monitor):
    # Do not race Kodi/TMDb Helper/database migrations during boot. Previous
    # builds launched this at 4s and a second Trakt startup sync at 5s, doing
    # the same account import twice while the home screen was still opening.
    if monitor.waitForAbort(60):
        return
    _last_health = time.time()
    _last_purge = time.time()
    while not monitor.abortRequested():
        try:
            from resources.lib import screensaver_hold as _ss_hold
            _ss_hold.tidy()          # v5.10.377: a hold left by a search that never ended
        except Exception:
            pass
        deferred = _interactive_busy()
        acquired = False
        if not deferred:
            acquired = _SYNC_CYCLE_LOCK.acquire(False)
            deferred = not acquired
        if acquired:
            try:
                try:
                    _sync_trakt_state(reason='service')
                except Exception as exc:
                    xbmc.log('[DexHub] trakt background sync failed: %s' % exc, xbmc.LOGWARNING)
                try:
                    _sync_simkl_state(reason='service')
                except Exception as exc:
                    xbmc.log('[DexHub] simkl background sync failed: %s' % exc, xbmc.LOGWARNING)
                try:
                    _sync_floppy_state(reason='service')
                except Exception as exc:
                    xbmc.log('[DexHub] floppy background sync failed: %s' % exc, xbmc.LOGWARNING)
            finally:
                _SYNC_CYCLE_LOCK.release()

        # Health check Plex/Emby endpoints every 5 minutes
        import time as _t
        now = _t.time()
        if not deferred and now - _last_health >= 300:
            try:
                from resources.lib import health_monitor
                checked = health_monitor.run_check_cycle()
                if checked:
                    xbmc.log('[DexHub] health checks: %d endpoints' % checked, xbmc.LOGDEBUG)
            except Exception as exc:
                xbmc.log('[DexHub] health check failed: %s' % exc, xbmc.LOGWARNING)
            _last_health = now

        # Cache cleanup every hour
        if not deferred and now - _last_purge >= 3600:
            try:
                _purge_http_cache()
            except Exception:
                pass
            _last_purge = now

        if not deferred:
            _publish_core_props(_win().getProperty('dexhub.core.trakt.last_sync') if _win() else '')
        # If the user is browsing/playing or the cloud cycle owns the lock,
        # retry gently in one minute. A completed cycle follows the normal
        # account interval (30 minutes by default).
        wait_seconds = 60.0 if deferred else (_sync_interval_ms() / 1000.0)
        if deferred:
            if monitor.waitForAbort(wait_seconds):
                break
            continue
        if _wait_with_trakt_checks(monitor, wait_seconds):
            break


_TRAKT_CHECK_EVERY = 300.0


def _wait_with_trakt_checks(monitor, seconds):
    """v5.10.208: the wait between cycles, with a look at Trakt every five
    minutes when smart sync is on: one small request (last_activities) says
    whether the playback list or the watchlist changed there, and only then
    is it pulled. True when Kodi is closing."""
    end = time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            return False
        if monitor.waitForAbort(min(left, _TRAKT_CHECK_EVERY)):
            return True
        if end - time.time() <= 1 or _interactive_busy():
            continue
        try:
            from resources.lib import trakt
            if not (trakt.enabled() and trakt.smart_sync() and trakt.authorized()):
                continue
            if trakt.unchanged('playback') and trakt.unchanged('watchlist'):
                continue
        except Exception:
            continue
        if _SYNC_CYCLE_LOCK.acquire(False):
            try:
                _sync_trakt_state(reason='service')
            except Exception as exc:
                xbmc.log('[DexHub] trakt smart sync failed: %s' % exc, xbmc.LOGWARNING)
            finally:
                _SYNC_CYCLE_LOCK.release()


def _autodetect_language_first_run():
    """If the user hasn't picked a UI language yet, infer one from Kodi's
    locale on the very first start. Saves new users from seeing English when
    their Kodi UI is already Arabic (or vice versa). Runs once and writes a
    sentinel setting so subsequent starts don't override the user's choice.
    """
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        if (addon.getSetting('ui_lang_autodetected') or '').strip() == 'true':
            return
        # Read Kodi's UI language. xbmc.getLanguage gives English name; the
        # ISO 639-1 form is the most reliable signal.
        kodi_lang = (xbmc.getLanguage(xbmc.ISO_639_1) or '').strip().lower()
        if kodi_lang.startswith('ar'):
            addon.setSetting('ui_language', 'Arabic')
            addon.setSetting('preferred_subtitle_langs', 'ar,en')
        else:
            addon.setSetting('ui_language', 'English')
        addon.setSetting('ui_lang_autodetected', 'true')
        xbmc.log('[DexHub] auto-detected UI language from Kodi locale=%s' % kodi_lang,
                 xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] language auto-detect failed: %s' % exc, xbmc.LOGWARNING)


def _migrate_silo_always_on():
    """v5.10.17: a linked Silo profile is always a playback source."""
    try:
        addon = xbmcaddon.Addon()
        raw = addon.getSetting('silo_auth_json') or '{}'
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {}
        if data.get('url') and data.get('token') and data.get('profile_id'):
            addon.setSetting('silo_in_sources', 'true')
    except Exception:
        pass


def _stop_threads(budget=2.0):
    """v5.10.130: Kodi lets the service go only once every thread of its
    interpreter has ended (daemon threads too): "waiting on thread", then
    "script didn't stop in 5 seconds - let's kill it", and in the test
    harness Kodi hung after that kill instead of quitting. The threads that
    never ended were idle pool workers (the rows' network lanes), which wait
    on their queue for ever. Every executor is told to stop (an idle worker
    wakes and leaves, a busy one leaves after its task) and the threads still
    alive get a short while to finish: the loops leave on Kodi's abort."""
    import threading
    import time
    started = time.monotonic()
    import sys
    # v5.10.241: the push worker and the playback heartbeat leave at once
    for name, call in (('resources.lib.progress_push', 'stop'), ('resources.lib.companion', 'stop_all')):
        module = sys.modules.get(name)
        if module is not None:
            try:
                getattr(module, call)()
            except Exception:
                pass
    try:
        from resources.lib.runtime_cleanup import shutdown_loaded_pools
        shutdown_loaded_pools()
    except Exception:
        pass
    try:
        # every other executor of this interpreter (continue watching,
        # subtitles, sync): what Python does at its own exit, without the
        # join that would wait for a slow server
        import concurrent.futures.thread as _cft
        _cft._shutdown = True
        lock = getattr(_cft, '_global_shutdown_lock', None)
        if lock is not None:
            with lock:
                queues = list(_cft._threads_queues.items())
        else:
            queues = list(_cft._threads_queues.items())
        for _thread, work_queue in queues:
            try:
                work_queue.put(None)
            except Exception:
                pass
    except Exception:
        pass
    deadline = started + budget
    me = threading.current_thread()
    # Kodi's own threads: the process main thread (Python names it
    # MainThread in every interpreter) and the foreign threads Python only
    # knows as dummies never end here, so waiting on them only costs time
    skip = {me, threading.main_thread()}
    dummy = getattr(threading, '_DummyThread', None)
    left_alive = []
    for thread in threading.enumerate():
        if thread in skip or (dummy is not None and isinstance(thread, dummy)):
            continue
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                thread.join(remaining)
            except Exception:
                pass
        if thread.is_alive():
            left_alive.append(thread.name)
    try:
        xbmc.log('[DexHub] service stopped in %.0f ms%s' % (
            (time.monotonic() - started) * 1000,
            ('; still running: %s' % ', '.join(left_alive[:8])) if left_alive else ''), xbmc.LOGINFO)
    except Exception:
        pass


def _retire_apple_tv_addon():
    """v5.10.305: Dex Hub (Apple TV) belongs to the Apple TV app. Installed
    here beside Dex Hub, its service ran a second Home worker, theme writer
    and keymap writer (the CoreELEC log: two 'companion service started', one
    at 5.10.278, two accent colours fighting, the player keymap read half
    written). Off the Apple TV it is turned off, once, and said in the log."""
    other = 'plugin.video.dexhub.tvos'
    try:
        if xbmc.getCondVisibility('System.Platform.Darwin | System.Platform.TVOS'):
            return
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % other):
            return
        if not xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % other):
            return
        import json as _json
        answer = xbmc.executeJSONRPC(_json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.SetAddonEnabled',
            'params': {'addonid': other, 'enabled': False}}))
        xbmc.log('[DexHub] Dex Hub (Apple TV) found on this device and turned off (it is for the '
                 'Apple TV app and ran a second service here): %s' % (answer or '')[:120], xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] Apple TV add-on check: %s' % exc, xbmc.LOGWARNING)


def _settings_version_check():
    """v5.10.378: settings saved by a newer Kodi, unreadable by this one."""
    try:
        from resources.lib import settings_version
        from resources.lib.profile_scope import ADDON_ID as _sv_id
        settings_version.check(_sv_id)
    except Exception as exc:
        xbmc.log('[DexHub] settings version check: %s' % exc, xbmc.LOGWARNING)


if __name__ == '__main__':
    _settings_version_check()          # v5.10.378: first, before anything sets a value
    _retire_apple_tv_addon()
    try:
        import xbmcaddon
        _service_addon = xbmcaddon.Addon()
        xbmc.log('[DexHub] companion service started: version=%s skin=%s' % (
            _service_addon.getAddonInfo('version') or '?',
            xbmc.getSkinDir() or '?'), xbmc.LOGINFO)
    except Exception:
        xbmc.log('[DexHub] companion service started', xbmc.LOGINFO)
    # v3.9.71: log Kodi version on startup so platform-specific issues
    # (e.g. deprecated API native crashes on Kodi 22 alpha) are easy to
    # correlate with bug reports.
    try:
        xbmc.log('[DexHub] platform: Kodi %s, Python %s' % (xbmc.getInfoLabel('System.BuildVersion') or '?', sys.version.split()[0]),
                 xbmc.LOGINFO)
    except Exception:
        pass

    # v5.10.140: first, before any other write rewrites the settings file,
    # list choices saved as their shown label become their value again.
    try:
        from resources.lib import settings_migrate as _settings_migrate
        _settings_migrate.run()
    except Exception:
        pass
    _autodetect_language_first_run()
    _migrate_silo_always_on()

    # Publish the skin-aware theme palette early so every DexHub dialog
    # (sources, loading, wait, select) inherits the active skin's accent
    # the moment it opens, regardless of open order.
    try:
        from resources.lib import skin_theme
        skin_theme.publish_theme(log=lambda m: xbmc.log('[DexHub] %s' % m, xbmc.LOGINFO))
    except Exception as exc:
        xbmc.log('[DexHub] skin_theme publish failed: %s' % exc, xbmc.LOGWARNING)

    # v5.10.96: a Dex Hub Home trailer that was muted when Kodi stopped (crash,
    # power loss) must not leave the whole system muted.
    try:
        import xbmcaddon as _xbmcaddon
        import xbmcvfs as _xbmcvfs
        from resources.lib.homeui.director import restore_mute_marker
        restore_mute_marker(_xbmcvfs.translatePath(
            _xbmcaddon.Addon().getAddonInfo('profile')))
    except Exception:
        pass
    # v5.10.177: once, a volume left at zero by an earlier version's silent
    # preview (its saved level was lost) comes back to full; Kodi's volume at
    # zero is otherwise only ever a preview's, the TV's own remote sets the level
    try:
        import xbmcaddon as _xbmcaddon
        _addon = _xbmcaddon.Addon()
        if not _addon.getSetting('volume_rescue_177'):
            from resources.lib.homeui.director import audio_state, set_volume
            _volume, _muted = audio_state()
            if _volume == 0:
                set_volume(100)
                xbmc.log('[DexHub] Kodi volume was at zero: set to 100 (once)', xbmc.LOGINFO)
            _addon.setSetting('volume_rescue_177', '1')
    except Exception:
        pass
    # v5.10.103: likewise Kodi's "switch to full screen" setting for TV
    # channels, held at "never" while a Live TV preview starts.
    try:
        import xbmcaddon as _xbmcaddon
        import xbmcvfs as _xbmcvfs
        from resources.lib.homeui.live import restore_marker as _live_restore
        _live_restore(_xbmcvfs.translatePath(_xbmcaddon.Addon().getAddonInfo('profile')))
    except Exception:
        pass

    # v5.10.98: open the Dex Hub Home when Kodi starts (setting homeui_autostart).
    # Only at Kodi's own start: a service restart (add-on update) in the same
    # session never opens it in the middle of whatever the user is doing.
    try:
        from resources.lib.homeui import autostart as _autostart
        _first_start = _autostart.first_run_this_session()
        if _first_start:
            # a player keymap the Home left behind when Kodi stopped with it open
            from resources.lib.homeui import keymap as _home_keymap
            _home_keymap.reset()
            # v5.10.245: Back in the player does what Settings, Player says
            # for every video (keeps it playing behind the Home, or stops it)
            _home_keymap.always()
        if _first_start and (_setting('homeui_autostart', 'false') or 'false').strip().lower() in (
                'true', '1', 'yes', 'on'):
            import threading as _thr_auto
            _thr_auto.Thread(target=_autostart.launch_when_ready,
                             name='DexHub-homeui-autostart', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] Home autostart not started: %s' % exc, xbmc.LOGWARNING)

    _publish_core_props('')
    _publish_badge_props()
    # v5.10.142: a pack's or the folder's badges need skin.dexhub 3.19 (an
    # older skin draws them on plates); said once, after the Home is up
    try:
        import threading as _thr_badges
        from resources.lib import player_badges as _player_badges
        _badge_check = _thr_badges.Timer(25.0, _player_badges.warn_old_skin)
        _badge_check.daemon = True
        _badge_check.start()
    except Exception:
        pass
    # v5.10.78: tell the context menu which play mode is the default, so it
    # can offer the opposite one.
    try:
        from resources.lib.context_play import publish as _publish_play_mode
        _publish_play_mode()
    except Exception:
        pass

    # v3.9.24: launch the local poster proxy. Inspired by Plexio's
    # /proxy/{token} pattern, this gives us a single-URL handle to every
    # poster image that transparently falls back from decorated → clean
    # if the upstream decoration service is slow/dead. Critically it
    # works on skins that don't honour Kodi's poster→thumb fallback
    # chain (Estuary, Confluence, much of the community-skin field).
    _lightweight = (_setting('lightweight_mode', 'true') or 'true').strip().lower() in ('true', '1', 'yes', 'on')
    if not _lightweight:
        try:
            from resources.lib import poster_proxy as _poster_proxy
            _poster_proxy.start()
        except Exception as exc:
            xbmc.log('[DexHub] poster-proxy not started: %s' % exc, xbmc.LOGWARNING)
    else:
        xbmc.log('[DexHub] Lightweight Mode — poster proxy skipped', xbmc.LOGINFO)

    # v3.9.27: launch the library-index sync scheduler. Runs the first
    # sync 30s after Kodi boot, then every `index_sync_interval_hours`.
    # Activated only when the user has enabled hybrid/fast mode — in
    # 'live' mode the sync still runs to keep the index warm in case
    # the user toggles modes later, but we skip the first-boot sync to
    # avoid wasting bandwidth on someone who isn't using the feature.
    #
    # v3.9.37: Lightweight Mode disables the index scheduler entirely.
    # The user opted out of aggregated buckets, so there is nothing to
    # index — running the scheduler would only waste CPU and bandwidth.
    if _lightweight:
        xbmc.log('[DexHub] Lightweight Mode enabled — index scheduler skipped', xbmc.LOGINFO)
    else:
        try:
            from resources.lib import index_render as _idx_render
            from resources.lib.dexhub import sync_engine as _sync_eng
            _idx_db = _idx_render.get_db()

            def _pinned_provider():
                # Late-import plugin (heavy module) only when actually needed.
                try:
                    from resources.lib import plugin as _plg
                    return _plg._hub_catalog_entries(bucket=None) or []
                except Exception as exc:
                    xbmc.log('[DexHub] sync pinned-provider failed: %s' % exc,
                             xbmc.LOGWARNING)
                    return []

            # v5.4.13: background sync is deliberately silent. Kodi notifications
            # interrupt playback and stack up on TV boxes; diagnostics belong in
            # kodi.log while explicit user-triggered actions still show feedback.
            def _on_sync_progress(stage, info):
                try:
                    if stage == 'start':
                        xbmc.log('[DexHub] library sync started (%s catalogs)' % (info.get('total') or 0), xbmc.LOGDEBUG)
                    elif stage == 'done':
                        xbmc.log('[DexHub] library sync done: %s ok in %.1fs' % (info.get('ok') or 0, float(info.get('duration') or 0)), xbmc.LOGDEBUG)
                except Exception:
                    pass

            _idx_engine = _sync_eng.SyncEngine(
                _idx_db,
                pinned_entries_provider=_pinned_provider,
                on_progress=_on_sync_progress,
            )

            def _interval_hours():
                try:
                    raw = _setting('index_sync_interval_hours', '4') or '4'
                    return int(float(raw))
                except Exception:
                    return 4

            import threading as _thr
            _thr.Thread(
                target=_sync_eng.run_scheduler,
                args=(_idx_engine, xbmc.Monitor()),
                kwargs={'get_interval_hours': _interval_hours},
                name='DexHub-index-scheduler', daemon=True,
            ).start()
            xbmc.log('[DexHub] library index scheduler started', xbmc.LOGINFO)
        except Exception as exc:
            xbmc.log('[DexHub] index scheduler not started: %s' % exc, xbmc.LOGWARNING)

    # Playback monitor is the largest service dependency; load it only after
    # lightweight startup work and optional schedulers are ready.
    from resources.lib.companion import CompanionPlayer, ProgressLoop
    player = CompanionPlayer()

    class _DexHubMonitor(xbmc.Monitor):
        """v4.7.3: closes the badges feedback loop at the SETTINGS DIALOG.
        'I changed badges.json and nothing changed' had no on-change hook —
        the new rules were only ever attempted on the next picker open, and
        a failure looked identical to success. Now the URL change triggers
        an immediate background fetch whose success/failure toast fires
        right away (the once-guards reset with the memo)."""

        def __init__(self):
            super().__init__()
            self._elite_url = _setting('elite_badges_json_url', '')
            self._settings_snap = _settings_snapshot()

        def _changed_settings(self):
            """The settings the user changed since the last call (the ones
            Dex Hub keeps for itself left out); None when unknown."""
            snap = _settings_snapshot()
            if not snap:
                return None
            old, self._settings_snap = self._settings_snap, snap
            if not old:
                return None
            keys = set(old) | set(snap)
            return sorted(k for k in keys if old.get(k) != snap.get(k) and not _volatile_setting(k))

        def onSettingsChanged(self):
            try:
                from resources.lib import settings_cache as _sc
                _sc.invalidate()
            except Exception:
                pass
            _publish_badge_props()
            try:
                from resources.lib.context_play import publish as _publish_play_mode
                _publish_play_mode()
            except Exception:
                pass
            changed = self._changed_settings()
            if changed == []:
                return          # only Dex Hub's own bookkeeping was written
            if changed and set(changed).intersection({'metadata_badges', 'player_show_ratings', 'player_show_studio'}):
                try:
                    from resources.lib import player_presentation
                    player_presentation.refresh_current()
                except Exception:
                    pass
            if changed and 'badges_home' in changed and xbmc.getSkinDir() == 'skin.dexhub':
                # v5.10.145: the servers page's line or badges are in its
                # rows: publish them again, the page shows it at once
                try:
                    from resources.lib.skinui import common as _skin_common
                    _skin_common.ask_republish('badges')
                except Exception:
                    pass
            try:
                # v5.10.366: a look choice goes to the cloud once it settled
                from resources.lib import cloud_look as _cloud_look
                _cloud_look.settings_changed(changed)
            except Exception:
                pass
            from resources.lib.ui_preferences import DISPLAY_ONLY
            if changed and set(changed).issubset(DISPLAY_ONLY):
                return          # display-only choices do not rebuild Home/widgets
            if changed:
                xbmc.log('[DexHub] settings changed: %s' % ', '.join(changed[:8]), xbmc.LOGDEBUG)
            try:
                from resources.lib import widget_cache as _widget_cache
                _widget_cache.clear()
            except Exception:
                pass
            try:
                if xbmc.getSkinDir() == 'skin.dexhub':
                    from resources.lib.skinui import common as _skin_common
                    _skin_common.ask_republish('settings')
            except Exception:
                pass
            new_url = (_setting('elite_badges_json_url', '') or '').strip()
            if new_url == self._elite_url:
                return
            self._elite_url = new_url
            xbmc.log('[DexHub] elite badges URL changed — reloading rules', xbmc.LOGINFO)

            def _job():
                try:
                    from resources.lib import source_browser as _sb
                    _sb._ELITE_BADGE_RULE_CACHE.clear()
                    _sb._elite_badge_rules()
                except Exception as exc:
                    xbmc.log('[DexHub] elite reload after settings change failed: %s'
                             % exc, xbmc.LOGWARNING)
            import threading as _thr
            _thr.Thread(target=_job, name='DexHubEliteReload', daemon=True).start()

    monitor = _DexHubMonitor()

    if xbmc.getCondVisibility('System.Platform.TVOS'):
        from resources.lib.infuse_progress import run as _infuse_returns
        import threading as _thr
        _thr.Thread(target=_infuse_returns, args=(monitor,), name='DexHub-Infuse-return', daemon=True).start()

    SYNC_DIRTY_PROP = 'dexhub.sync_dirty'

    def _hub_worker(mon):
        try:
            from resources.lib import hub_worker as _hub_worker_mod
            _hub_worker_mod.run(mon)
        except Exception as exc:
            xbmc.log('[DexHub] hub thread not started: %s' % exc, xbmc.LOGWARNING)

    def _cloud_sync_loop(mon):
        """Native Nuvio/Stremio sync.

        v5.1 adaptive mode: local writes are debounced, cloud pulls use a
        minimum 30-minute cadence, and all network/database work is deferred
        while playback or an interactive source/search screen is active.
        Pulled account rows use a bulk transaction and do not raise the dirty
        flag, eliminating the old sync feedback loop.
        """
        if mon.waitForAbort(30):
            return
        from resources.lib.dexhub import nuvio_stremio_sync as sync
        win = _win()
        last_pull = time.time()  # never force a full pull during Kodi boot
        dirty_since = None
        next_retry = sync.next_retry_at()
        cycle_backoff = 0.0

        while not mon.abortRequested():
            try:
                interval = int(float(_setting('cloud_sync_interval_min', '30') or '30'))
            except Exception:
                interval = 30

            if interval <= 0:                     # user switched sync off
                if mon.waitForAbort(60):
                    break
                continue

            dirty = False
            try:
                dirty = bool(win and win.getProperty(SYNC_DIRTY_PROP))
            except Exception:
                dirty = False
            now = time.time()
            if dirty and dirty_since is None:
                dirty_since = now
            elif not dirty:
                dirty_since = None
            # v5.4.1: continuous mode is honoured again. 5.4.0 kept the
            # sturdier loop (cycle lock, retry backoff, no sync while the
            # user is interacting) but hardcoded a 45s debounce and a 30
            # minute floor on pulls, so the cloud_sync_continuous setting
            # did nothing at all. Continuous debounces 8s and pulls on the
            # user's interval; periodic keeps the conservative numbers.
            continuous = (_setting('cloud_sync_continuous', 'false') or 'false'
                          ).strip().lower() in ('true', '1', 'yes', 'on')
            debounce = 8.0 if continuous else 45.0
            pull_floor = max(120, interval * 60) if continuous else max(1800, interval * 60)
            dirty_due = bool(dirty and dirty_since is not None and
                             (now - dirty_since) >= debounce)
            pull_due = (now - last_pull) >= pull_floor
            # v5.10.120: a profile switch whose data Nuvio did not give
            # (homeui/profile.pull) is read in full at the next chance
            want_full = False
            try:
                want_full = bool(win and win.getProperty('dexhub.sync.want_full'))
            except Exception:
                want_full = False
            pull_due = pull_due or want_full

            retry_due = bool(next_retry and now >= next_retry)
            # Playback already has a push-only reporter every 120s and on
            # pause/stop/end. Never add a full-history sync while video plays.
            if ((dirty_due or pull_due or retry_due) and now >= cycle_backoff and
                    not _interactive_busy() and sync.enabled_targets()):
                acquired = _SYNC_CYCLE_LOCK.acquire(False)
                if not acquired:
                    if mon.waitForAbort(30):
                        break
                    continue
                try:
                    if win:
                        win.clearProperty(SYNC_DIRTY_PROP)
                except Exception:
                    pass
                light = dirty_due and not pull_due
                retry_pass = retry_due and not (dirty_due or pull_due)
                try:
                    # Each service resolves its own direction and sections.
                    # v5.10.120: a local change (progress, the library) is
                    # sent on its own; the add-ons, collections and Home
                    # settings wait for the scheduled pull. A full sync after
                    # every playback asked Nuvio a dozen things each time,
                    # and Nuvio answered 429 (sync paused 10 minutes, the
                    # profile list unreadable meanwhile).
                    if retry_pass:
                        result = sync.run_sync(background=True, retry_only=True)
                    elif light:
                        result = sync.run_sync(only=('progress', 'library'), background=True)
                    else:
                        result = sync.run_sync(background=True)
                    # A partial failure has its own section retry schedule.
                    # The normal full pull still waits for its usual interval.
                    if not light and not retry_pass:
                        last_pull = time.time()
                        if want_full and win:
                            win.clearProperty('dexhub.sync.want_full')
                    dirty_since = None
                    next_retry = (result or {}).get('retry_at') or 0.0
                    cycle_backoff = 0.0
                except Exception as exc:
                    cycle_backoff = time.time() + 300.0
                    if dirty and win:
                        try:
                            win.setProperty(SYNC_DIRTY_PROP, '1')
                        except Exception:
                            pass
                    xbmc.log('[DexHub] cloud sync failed: %s' % exc, xbmc.LOGDEBUG)
                finally:
                    _SYNC_CYCLE_LOCK.release()

            # Continuous mode has to wake often enough for its 8s debounce
            # to be real; periodic keeps the cheap 30s tick.
            if mon.waitForAbort(12 if continuous else 30):
                break


    def _tmdbh_player_maintenance():
        # Player-file maintenance is not part of Kodi startup.  Delay it until
        # the user has had time to enter the skin and skip the pass entirely
        # while Dex Hub is actively browsing/searching.
        try:
            if monitor.waitForAbort(75):
                return
            if _interactive_busy(max_age=120.0):
                return
            from resources.lib import tmdbh_player
            tmdbh_player.ensure_installed_once()
        except Exception as exc:
            xbmc.log('[DexHub] tmdbh player maintenance skipped: %s' % exc, xbmc.LOGDEBUG)


    def _bytecode_warm_job():
        # v5.4.7: pre-compile the addon tree to __pycache__ so the user's
        # first click after install/update pays warm-import cost only
        # (~0.5s ARM32) instead of full byte-compilation (~4-6s ARM32).
        # 120s boot delay keeps us out of Kodi's startup and first-browse CPU window;
        # after the first pass this is a stat-only sweep in milliseconds.
        try:
            if monitor.waitForAbort(120):
                return
            if _interactive_busy(max_age=240.0):
                return
            from bytecode_warm import warm
            addon_root = os.path.dirname(os.path.abspath(__file__))
            checked, compiled = warm(addon_root, monitor=monitor)
            if compiled:
                xbmc.log('[DexHub] bytecode warm: compiled %d/%d files'
                         % (compiled, checked), xbmc.LOGDEBUG)
        except Exception as exc:
            xbmc.log('[DexHub] bytecode warm skipped: %s' % exc, xbmc.LOGDEBUG)

    # v5.10.156: a trailer cut off by a crash or a power cut left refresh
    # switching off; the user's value comes back at start
    try:
        from resources.lib import refresh_guard as _refresh_guard
        _refresh_guard.restore()
    except Exception:
        pass

    # v5.10.109: skin.dexhub's rows (published and kept fresh here while that
    # skin is in use; the thread waits quietly under any other skin)
    def _skin_worker():
        try:
            from resources.lib.skinui import worker as _skin_worker_mod
            _skin_worker_mod.run(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] skin worker not started: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_skin_worker, name='DexHub-skin', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] skin worker thread failed: %s' % exc, xbmc.LOGWARNING)

    # v5.10.117: subtitle AutoSync (DexSubtitles' engine), only while
    # DexSubtitles itself is not enabled: never two services on one subtitle
    def _subsync_worker():
        try:
            from resources.lib.subsync import runner as _subsync_runner
            _subsync_runner.run(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] subtitle AutoSync not started: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_subsync_worker, name='DexHub-subsync', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] subtitle AutoSync thread failed: %s' % exc, xbmc.LOGWARNING)

    # v5.10.217: a notification on the day a followed show has a new episode
    def _silo_notifications_worker():
        try:
            from resources.lib import silo_notifications
            silo_notifications.run(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] Silo inbox worker stopped: %s' % type(exc).__name__, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_silo_notifications_worker, name='DexHub-SiloInbox', daemon=True).start()
    except Exception:
        pass

    def _reminders_worker():
        try:
            from resources.lib import reminders as _reminders
            _reminders.run(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] episode reminders not started: %s' % exc, xbmc.LOGWARNING)

    def _collection_opener_worker():
        try:
            from resources.lib.skinui.folderpage import watch_open_requests
            watch_open_requests(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] collection opener stopped: %s' % exc, xbmc.LOGWARNING)

    threading.Thread(target=_collection_opener_worker, name='DexHub-collection-opener', daemon=True).start()

    def _native_playback_worker():
        try:
            from resources.lib.native_playback import watch
            watch(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] native playback helper stopped: %s' % exc, xbmc.LOGWARNING)

    threading.Thread(target=_native_playback_worker, name='DexHub-playback-assist', daemon=True).start()

    # v5.10.217: the end-of-episode panel (the next episode at the credits)
    def _credits_worker():
        try:
            from resources.lib import credits_panel as _credits
            _credits.watch(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] end-of-episode panel not started: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_credits_worker, name='DexHub-credits', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] end-of-episode panel thread failed: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_reminders_worker, name='DexHub-reminders', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] episode reminders thread failed: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_background_sync_loop, args=(monitor,), name='DexHubCoreLoop', daemon=True).start()
        # v5.10.366: DexCloud (cloud look, watch party); sleeps when not linked
        _thr.Thread(target=_hub_worker, args=(monitor,), name='DexHub-hub', daemon=True).start()
        _thr.Thread(target=_cloud_sync_loop, args=(monitor,), name='DexHubCloudLoop', daemon=True).start()
        _thr.Thread(target=_tmdbh_player_maintenance, name='DexHubTMDbHMaintenance', daemon=True).start()
        _thr.Thread(target=_bytecode_warm_job, name='DexHubBytecodeWarm', daemon=True).start()
    except Exception as exc:
        xbmc.log('[DexHub] could not spawn core sync thread: %s' % exc, xbmc.LOGWARNING)
    try:
        ProgressLoop(player).run()
    finally:
        # Clean shutdown of the poster proxy thread when Kodi tears the
        # service down. Errors here are non-fatal — daemon threads die
        # with the process anyway, but explicit cleanup is hygienic.
        try:
            from resources.lib import poster_proxy as _poster_proxy
            _poster_proxy.stop()
        except Exception:
            pass
        _stop_threads()
        from resources.lib.service_lease import release
        release()
