# -*- coding: utf-8 -*-
import os
import sys
import xbmc

addon_path = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from resources.lib.companion import CompanionPlayer, ProgressLoop
from resources.lib import trakt
from resources.lib import tmdbh_player
from resources.lib import favorites_store
import xbmcgui


def _purge_http_cache():
    """Delete cached HTTP responses older than twice their max TTL. Keeps the
    cache dir bounded on long-running installs.

    Also purges:
      * special://temp/dexhub_subs/  → subtitle files older than 24h
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


def _win():
    try:
        return xbmcgui.Window(10000)
    except Exception:
        return None



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



def _publish_core_props(last_sync=''):
    win = _win()
    if not win:
        return
    tmdbh_available = '1' if tmdbh_player.has_tmdbhelper() else '0'
    tmdbh_installed = '1' if tmdbh_player.player_installed() else '0'
    tmdbh_primary = '1' if _primary_player_mode() == 'tmdbhelper' else '0'
    trakt_enabled = '1' if trakt.enabled() else '0'
    trakt_connected = '1' if trakt.authorized() else '0'
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
            trakt.import_progress(limit=100)
            did_work = True
    except Exception as exc:
        xbmc.log('[DexHub] trakt progress sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)

    try:
        if ((_setting('trakt_sync_watchlist', 'true') or 'true').lower() == 'true'):
            rows = trakt.fetch_watchlist(limit=300) or []
            favorites_store.replace_trakt_mirror(rows)
            did_work = True
    except Exception as exc:
        xbmc.log('[DexHub] trakt watchlist sync failed (%s): %s' % (reason, exc), xbmc.LOGWARNING)

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



def _sync_interval_ms():
    try:
        minutes = int(_setting('trakt_service_sync_interval', '10') or '10')
    except Exception:
        minutes = 10
    minutes = max(2, min(60, minutes))
    return minutes * 60 * 1000



def _background_sync_loop(monitor):
    xbmc.sleep(4000)
    _last_health = 0
    _last_purge = 0
    while not monitor.abortRequested():
        try:
            tmdbh_player.ensure_installed_once()
        except Exception as exc:
            xbmc.log('[DexHub] tmdbh keepalive failed: %s' % exc, xbmc.LOGWARNING)
        try:
            _sync_trakt_state(reason='service')
        except Exception as exc:
            xbmc.log('[DexHub] trakt background sync failed: %s' % exc, xbmc.LOGWARNING)

        # Health check Plex/Emby endpoints every 5 minutes
        import time as _t
        now = _t.time()
        if now - _last_health >= 300:
            try:
                from resources.lib import health_monitor
                checked = health_monitor.run_check_cycle()
                if checked:
                    xbmc.log('[DexHub] health checks: %d endpoints' % checked, xbmc.LOGDEBUG)
            except Exception as exc:
                xbmc.log('[DexHub] health check failed: %s' % exc, xbmc.LOGWARNING)
            _last_health = now

        # Cache cleanup every hour
        if now - _last_purge >= 3600:
            try:
                _purge_http_cache()
            except Exception:
                pass
            _last_purge = now

        _publish_core_props(_win().getProperty('dexhub.core.trakt.last_sync') if _win() else '')
        if monitor.waitForAbort(_sync_interval_ms() / 1000.0):
            break


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


if __name__ == '__main__':
    xbmc.log('[DexHub] companion service started', xbmc.LOGINFO)
    # v3.9.71: log Kodi version on startup so platform-specific issues
    # (e.g. deprecated API native crashes on Kodi 22 alpha) are easy to
    # correlate with bug reports.
    try:
        xbmc.log('[DexHub] platform: Kodi %s' % (xbmc.getInfoLabel('System.BuildVersion') or '?'),
                 xbmc.LOGINFO)
    except Exception:
        pass

    _autodetect_language_first_run()
    _purge_http_cache()

    try:
        tmdbh_player.ensure_installed_once()
    except Exception as exc:
        xbmc.log('[DexHub] tmdbh player auto-install failed: %s' % exc, xbmc.LOGWARNING)

    _publish_core_props('')

    # v3.9.24: launch the local poster proxy. Inspired by Plexio's
    # /proxy/{token} pattern, this gives us a single-URL handle to every
    # poster image that transparently falls back from decorated → clean
    # if the upstream decoration service is slow/dead. Critically it
    # works on skins that don't honour Kodi's poster→thumb fallback
    # chain (Estuary, Confluence, much of the community-skin field).
    try:
        from resources.lib import poster_proxy as _poster_proxy
        _poster_proxy.start()
    except Exception as exc:
        xbmc.log('[DexHub] poster-proxy not started: %s' % exc, xbmc.LOGWARNING)

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
    _lightweight = (_setting('lightweight_mode', 'false') or 'false').strip().lower() in ('true', '1', 'yes', 'on')
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

            # v3.9.29: toast progress so the user knows the initial 5-15min
            # library sync is actually doing something. Throttled — only
            # fires on start/done and every 5 catalogs in between, never
            # spamming. Stays silent during scheduled background syncs (the
            # 4-hour periodic run) so it doesn't interrupt watching.
            _sync_progress_state = {'last_toast_at': 0, 'total': 0,
                                     'started_at': 0}

            def _on_sync_progress(stage, info):
                try:
                    import xbmcgui as _xg
                    now = time.time()
                    if stage == 'start':
                        _sync_progress_state['total'] = info.get('total') or 0
                        _sync_progress_state['started_at'] = now
                        _sync_progress_state['last_toast_at'] = now
                        if (info.get('total') or 0) > 0:
                            _xg.Dialog().notification(
                                'Dex Hub',
                                'بدء مزامنة المكتبة (%d كتالوج)' % info['total'],
                                _xg.NOTIFICATION_INFO, 2500, sound=False,
                            )
                    elif stage == 'catalog':
                        idx = info.get('index') or 0
                        total = info.get('total') or 0
                        # Throttle: every 5 catalogs, OR at least 4s since
                        # last toast — whichever is less frequent.
                        if total > 0 and (idx % 5 == 0 or idx == total) \
                           and (now - _sync_progress_state['last_toast_at']) >= 4:
                            _sync_progress_state['last_toast_at'] = now
                            _xg.Dialog().notification(
                                'Dex Hub',
                                '%d / %d  •  %s' % (
                                    idx, total,
                                    info.get('catalog') or info.get('bucket') or ''),
                                _xg.NOTIFICATION_INFO, 2000, sound=False,
                            )
                    elif stage == 'done':
                        elapsed = info.get('duration') or 0
                        ok = info.get('ok') or 0
                        if (info.get('total') or 0) > 0:
                            _xg.Dialog().notification(
                                'Dex Hub',
                                'انتهت المزامنة: %d ناجح في %.0f ث' % (ok, elapsed),
                                _xg.NOTIFICATION_INFO, 3500, sound=False,
                            )
                except Exception as exc:
                    xbmc.log('[DexHub] sync progress toast failed: %s' % exc,
                             xbmc.LOGDEBUG)

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

    player = CompanionPlayer()
    monitor = xbmc.Monitor()

    def _startup_sync():
        try:
            xbmc.sleep(5000)
            _sync_trakt_state(reason='startup')
        except Exception as exc:
            xbmc.log('[DexHub] trakt startup sync failed: %s' % exc, xbmc.LOGWARNING)

    try:
        import threading as _thr
        _thr.Thread(target=_startup_sync, name='DexHubCoreBoot', daemon=True).start()
        _thr.Thread(target=_background_sync_loop, args=(monitor,), name='DexHubCoreLoop', daemon=True).start()
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
