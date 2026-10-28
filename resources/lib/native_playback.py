"""Native intro/recap buttons for Dex Hub playback only."""
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcaddon
import xbmcgui

PREFIX = 'dhs.seek.'


def setting(key, default=''):
    return xbmcaddon.Addon('plugin.video.dexhub').getSetting(key) or default


def skip_mode():
    value = setting('native_skip_mode', 'Manual').lower()
    return value if value in ('manual', 'automatic', 'off') else 'manual'


def text(ar, en):
    return ar if setting('ui_language', 'English').lower() == 'arabic' else en


def source_key(value):
    return str(value or '').split('|', 1)[0]


_PROFILE = {'at': 0, 'key': ''}


def context(player, fresh=False):
    from . import profile_scope
    now = time.monotonic()
    if fresh or now - _PROFILE['at'] > 1:
        _PROFILE.update(at=now, key=profile_scope.active_key())
    # Active sessions are already in RAM. Avoid disk/session fallback on every
    # remote-input tick (and never read a stale session after playback stops).
    raw = xbmcgui.Window(10000).getProperty('dexhub.session_v1')
    ctx = json.loads(raw) if raw else {}
    url = player.getPlayingFile()
    if not isinstance(ctx, dict) or ctx.get('profile_scope') != _PROFILE['key']:
        return None
    if not ctx.get('playback_uid') or not ctx.get('stream_url') or source_key(ctx['stream_url']) != source_key(url):
        return None
    if xbmc.getCondVisibility('VideoPlayer.Content(LiveTV)') or ctx.get('is_trailer'):
        return None
    return (str(ctx['playback_uid']), url, _PROFILE['key']), ctx


def playing_identity(ctx):
    from .introdb_bridge import playback_identity
    safe = dict(ctx)
    if str(ctx.get('server_type') or '').lower() in ('emby', 'jellyfin', 'plex', 'silo'):
        # A server episode's generic IDs identify the episode, not its show.
        if ctx.get('season') is not None or str(ctx.get('media_type')).lower() in ('series', 'episode'):
            external = ctx.get('external_ids') if isinstance(ctx.get('external_ids'), dict) else {}
            safe['external_ids'] = {k: v for k, v in external.items()
                                    if k.startswith('show_') or k in ('tmdbshow', 'tmdb_show', 'imdbshow', 'imdb_show')}
            for field in ('tmdb_id', 'imdb_id', 'tvdb_id'):
                safe[field] = ctx.get('show_' + field) or external.get('show_' + field) or ''
    identity = playback_identity(safe)
    return {'series': identity['is_episode'], 'season': identity['season'],
            'episode': identity['episode'], 'episode_order': ctx.get('episode_order') or ctx.get('displayorder'),
            'q': {'tmdb': identity['tmdb_id'], 'imdb': identity['imdb_id'], 'tvdb': identity['tvdb_id']}}


def active_segment(data, position, duration):
    for kind in ('recap', 'intro'):
        for a, b in data.get(kind, []):
            # Never offer a seek past the video or for an implausible full-film segment.
            if 0 <= a <= position < b and b - a <= 15 * 60 and b < duration - 1:
                return kind, a, b
    return None


class SkipDialog(xbmcgui.WindowXMLDialog):
    def __new__(cls, xml_file, addon_path, skin='Default', res='1080i', **kwargs):
        # Kodi's native constructor accepts its XML arguments only. Keep our
        # playback token/segment out of the native keyword-argument parser.
        return super(SkipDialog, cls).__new__(cls, xml_file, addon_path, skin, res)

    def __init__(self, *args, **kwargs):
        self.token = kwargs.pop('token')
        self.segment = kwargs.pop('segment')
        self.closed = False
        self.dismissed = False
        super(SkipDialog, self).__init__(*args, **kwargs)

    def onInit(self):
        from .tvos_assets import publish_asset_root
        publish_asset_root()
        self.getControl(100).setLabel(text('تخطي المقدمة', 'Skip intro') if self.segment[0] == 'intro'
                                      else text('تخطي الملخص', 'Skip recap'))
        self.setFocusId(100)

    def close(self):
        self.closed = True
        super(SkipDialog, self).close()

    def onClick(self, control_id):
        if control_id == 100:
            try:
                player = xbmc.Player()
                active = context(player, fresh=True) if player.isPlayingVideo() else None
                a, b = self.segment[1:]
                if active and active[0] == self.token and a <= player.getTime() < b and b < player.getTotalTime():
                    player.seekTime(b)
                    self.dismissed = True
            finally:
                self.close()

    def onAction(self, action):
        aid = action.getId()
        if aid in (10, 92, 216):
            self.dismissed = True
            self.close()
        elif aid in (1, 2, 3, 4):
            # Give navigation back to Kodi without declining the segment.
            self.close()
            xbmc.executebuiltin('Action(%s)' % {1: 'Left', 2: 'Right', 3: 'Up', 4: 'Down'}[aid])
        elif aid == 12:
            # Pausing must not permanently dismiss the skip button.
            xbmc.executebuiltin('Action(Pause)')


def _clear_legacy_preview(home):
    # Remove only stale RAM properties left by an older installed version.
    for key in ('visible', 'time', 'hint', 'buttons', 'state', 'report'):
        home.clearProperty('dhs.seek.' + key)


def watch(monitor):
    from . import introdb_client, introdb_bridge
    player, home = xbmc.Player(), xbmcgui.Window(10000)
    _clear_legacy_preview(home)
    presentation = None
    try:
        from .loading_art import Presentation
        presentation = Presentation()
    except Exception:
        pass
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='DexHub-intro')
    token, started, playing = None, 0, {}
    intro_job, intro_requested = None, False
    data, intro_diagnostics = {}, {}
    dialog, dismissed = None, set()

    def close_dialog():
        nonlocal dialog
        if dialog:
            dialog.close()
            dialog = None

    try:
        while not monitor.abortRequested():
            try:
                if presentation:
                    try:
                        presentation.tick()
                    except Exception:
                        presentation.close()
                active = context(player) if player.isPlayingVideo() else None
                duration = float(player.getTotalTime()) if active else 0
                if not active or not math.isfinite(duration) or duration <= 0:
                    if token:
                        close_dialog()
                        if intro_job:intro_job.cancel()
                    token = None
                    introdb_bridge.restore_native_guard(grace=15)
                    if monitor.waitForAbort(0.5):break
                    continue
                current, ctx = active
                if current != token:
                    close_dialog()
                    if intro_job:intro_job.cancel()
                    token, started = current, time.monotonic()
                    playing = playing_identity(ctx)
                    data, dismissed = {}, set()
                    intro_diagnostics = {'state': 'waiting', 'api': 'v4'}
                    home.setProperty('dhs.skip.report', json.dumps(intro_diagnostics))
                    intro_job, intro_requested = None, False
                mode = skip_mode()
                if mode != 'off':
                    introdb_bridge.suspend_for_native()
                else:
                    introdb_bridge.restore_native_guard()
                    close_dialog()
                position = float(player.getTime())
                if mode != 'off' and not intro_requested and time.monotonic() - started >= 2:
                    intro_requested = True
                    intro_diagnostics = {'state': 'loading', 'api': 'v4'}
                    home.setProperty('dhs.skip.report', json.dumps(intro_diagnostics))
                    intro_job = pool.submit(introdb_client.lookup, playing, duration, intro_diagnostics)
                if intro_job and intro_job.done():
                    try:
                        data = intro_job.result() or {}
                    except Exception:
                        data = {}
                    intro_job = None
                    home.setProperty('dhs.skip.report', json.dumps(intro_diagnostics))
                segment = active_segment(data, position, duration) if mode != 'off' else None
                # The button belongs to the detected media interval, not an
                # OSD timeout. Only Back/dismiss or a successful skip declines
                # this segment; controls, seeking and leaving fullscreen merely
                # suspend it, so it returns while the intro is still playing.
                blocked = xbmc.getCondVisibility(
                    'Window.IsActive(videoosd) | Player.Seeking | Player.HasPerformedSeek(2)')
                full = xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)')
                if dialog and (dialog.closed or segment != dialog.segment or blocked or not full):
                    if dialog.dismissed:
                        dismissed.add(dialog.segment)
                    close_dialog()
                full = full and not blocked
                if segment and segment not in dismissed and full:
                    if mode == 'automatic':
                        # Revalidate playback ownership immediately before changing position.
                        confirmed = context(player, fresh=True)
                        if confirmed and confirmed[0] == token:
                            player.seekTime(segment[2])
                            dismissed.add(segment)
                    elif dialog is None:
                        path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
                        dialog = SkipDialog('dexhub_skip.xml', path, 'Default', '1080i', token=token, segment=segment)
                        try:
                            dialog.show()
                        except Exception:
                            intro_diagnostics['state'] = 'display_error'
                            home.setProperty('dhs.skip.report', json.dumps(intro_diagnostics))
                            dismissed.add(segment)
                            close_dialog()
                if monitor.waitForAbort(0.25):break
            except Exception:
                close_dialog()
                if monitor.waitForAbort(1):break
    finally:
        if presentation:presentation.close()
        close_dialog()
        _clear_legacy_preview(home)
        introdb_bridge.restore_native_guard()
        if intro_job:intro_job.cancel()
        pool.shutdown(wait=False)
