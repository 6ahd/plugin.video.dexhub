"""Player-only entry selected before importing any browsing route."""
ACTIONS = frozenset(('tmdb_player', 'streams', 'episode_streams', 'play',
                     'play_item', 'play_next_same_source', 'plex_play', 'emby_play',
                     'jellyfin_play', 'silo_play', 'native_resume', 'cw_resume',
                     'switch_source'))


# v5.10.120: two plays started within a moment (OK pressed twice on a title
# page that was still reading, or Play and an episode both): two source
# searches ran at once, two result windows opened, and a box crashed ten
# seconds later. The second one is dropped.
_STARTING = 'dexhub.play.starting'
_DOUBLE = 2.5
# calls another add-on or Dex Hub itself makes on the way of a play
_CHAINED = frozenset(('tmdb_player', 'native_resume', 'play_next_same_source'))


def _claim():
    """(own mark, seconds since the other play started or 0.0)."""
    try:
        import time
        import xbmcgui
        win = xbmcgui.Window(10000)
        now = time.time()
        try:
            then = float(win.getProperty(_STARTING) or 0)
        except ValueError:
            then = 0.0
        if then and 0.0 <= now - then < _DOUBLE:
            return '', now - then
        mark = '%.3f' % now
        win.setProperty(_STARTING, mark)
        return mark, 0.0
    except Exception:
        return '', 0.0


def _release(mark):
    if not mark:
        return
    try:
        import xbmcgui
        win = xbmcgui.Window(10000)
        if win.getProperty(_STARTING) == mark:
            win.clearProperty(_STARTING)
    except Exception:
        pass


def _drop(age):
    import sys
    import xbmc
    xbmc.log('[DexHub] play: another play started %.1f s ago; this one is dropped' % age, xbmc.LOGINFO)
    try:
        handle = int(sys.argv[1])
    except Exception:
        handle = -1
    if handle >= 0:
        try:
            import xbmcgui
            import xbmcplugin
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        except Exception:
            pass


def _action():
    import sys
    try:
        from urllib.parse import parse_qsl
        return dict(parse_qsl((sys.argv[2] if len(sys.argv) > 2 else '').lstrip('?'))).get('action') or ''
    except Exception:
        return ''


def run():
    mark, age = ('', 0.0) if _action() in _CHAINED else _claim()
    if age:
        return _drop(age)
    try:
        return _run()
    finally:
        _release(mark)


def _run():
    import sys
    import time
    import xbmc
    started = time.monotonic()
    from .settings_cache import invalidate
    from .i18n import reset_language_cache
    invalidate()
    reset_language_cache()
    from . import player_runtime as runtime
    if runtime.ADDON_ID == 'plugin.video.dexhublite':
        from .lite_ui import SourcesLoadingDialog, open_sources_window
        runtime.SourcesLoadingDialog = SourcesLoadingDialog
        runtime.open_sources_window = open_sources_window
        runtime._default_click_uses_tmdbhelper = lambda: False
        runtime._maybe_redirect_default_player = lambda **kw: False
        runtime._source_resolution_mode = lambda: 'picker'
        runtime._source_window_meta_fallback = lambda meta: meta
    xbmc.log('[DexHub timing] stage=player-runtime-import ms=%.1f' %
             ((time.monotonic() - started) * 1000), xbmc.LOGINFO)
    try:
        return runtime.run()
    finally:
        from .runtime_cleanup import shutdown_loaded_pools
        shutdown_loaded_pools()
