"""Native intro/recap buttons and lazy seek previews for Dex Hub playback only."""
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

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
            if 0 <= a <= position < b - 0.25 and b - a <= 15 * 60 and b < duration - 1:
                return kind, a, b
    return None


def seek_target(label, fallback, duration):
    from .seek_previews import seconds
    try:
        value = seconds(label) if label else float(fallback)
        if not math.isfinite(value):
            raise ValueError()
        return max(0.0, min(duration - 0.001, value))
    except (TypeError, ValueError):
        return max(0.0, min(duration - 0.001, fallback))


class SkipDialog(xbmcgui.WindowXMLDialog):
    def __new__(cls, xml_file, addon_path, skin='Default', res='1080i', **kwargs):
        # Kodi's native constructor accepts its XML arguments only. Keep our
        # playback token/segment out of the native keyword-argument parser.
        return super(SkipDialog, cls).__new__(cls, xml_file, addon_path, skin, res)

    def __init__(self, *args, **kwargs):
        self.token = kwargs.pop('token')
        self.segment = kwargs.pop('segment')
        self.closed = False
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
            finally:
                self.close()

    def onAction(self, action):
        aid = action.getId()
        if aid in (10, 92, 216):
            self.close()
        elif aid in (1, 2, 3, 4, 12):
            self.close()
            xbmc.executebuiltin('Action(%s)' % {1: 'Left', 2: 'Right', 3: 'Up', 4: 'Down', 12: 'Pause'}[aid])


def _load_track(ctx, playing, duration, api_key):
    from . import seek_previews as P
    try:
        track = P.server_track(ctx, duration)
        if track:
            return track
    except Exception:
        pass
    try:
        return P.seekr_track(playing, duration, api_key)
    except Exception:
        return None


def _clear(home):
    for key in ('visible', 'time', 'hint'):
        if home.getProperty(PREFIX + key):
            home.clearProperty(PREFIX + key)


def _publish(home, frame):
    """Change the existing seek bar's image; never create a focus-taking window.

    The skin's grouplist clips the sprite sheet to a fixed 320 x 180 viewport.
    Only native Kodi texture rendering is used (including on tvOS).
    """
    if not frame:
        _clear(home)
        return False
    try:
        path, (x, y, w, h), target = frame
        image = xbmcgui.Window(10115).getControl(9891)
        home.clearProperty(PREFIX + 'visible')
        image.setPosition(x, y)
        image.setWidth(w)
        image.setHeight(h)
        image.setImage(path, False)
        n = int(target)
        label = '%d:%02d:%02d' % (n // 3600, (n // 60) % 60, n % 60)
        home.setProperty(PREFIX + 'time', label)
        home.setProperty(PREFIX + 'visible', 'true')
        home.setProperty(PREFIX + 'state', 'ready')
        return True
    except Exception:
        home.setProperty(PREFIX + 'state', 'display_error')
        _clear(home)
        return False


def watch(monitor):
    from . import introdb_client, introdb_bridge, profile_scope
    player, home = xbmc.Player(), xbmcgui.Window(10000)
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='DexHub-previews')
    token, ctx, started = None, {}, 0
    intro_job = track_job = frame_job = None
    data, playing, track = {}, {}, None
    intro_diagnostics = {}
    intro_requested = track_requested = False
    dialog, dismissed = None, set()
    last_request, last_cue, pending_frame, pending_url = 0, None, None, None
    seeking_before = False
    failures = {}

    def close_dialog():
        nonlocal dialog
        if dialog:
            dialog.close()
            dialog = None

    try:
        while not monitor.abortRequested():
            try:
                active = context(player) if player.isPlayingVideo() else None
                duration = float(player.getTotalTime()) if active else 0
                if not active or not math.isfinite(duration) or duration <= 0:
                    if token:
                        close_dialog()
                        _clear(home)
                        home.clearProperty(PREFIX + 'state')
                    token = None
                    introdb_bridge.restore_native_guard(grace=15)
                    if monitor.waitForAbort(0.5):
                        break
                    continue
                current, current_ctx = active
                if current != token:
                    close_dialog()
                    _clear(home)
                    # Cancel queued old-video work; running bounded HTTP calls finish silently.
                    for job in (intro_job, track_job, frame_job):
                        if job:
                            job.cancel()
                    token, ctx, started = current, current_ctx, time.monotonic()
                    playing = playing_identity(ctx)
                    data, track, dismissed, failures = {}, None, set(), {}
                    intro_diagnostics = {'state': 'waiting', 'api': 'v4'}
                    home.setProperty('dhs.skip.report', json.dumps(intro_diagnostics))
                    intro_job = track_job = frame_job = None
                    intro_requested = track_requested = seeking_before = False
                    last_cue = pending_frame = None
                    last_request = 0
                    home.clearProperty(PREFIX + 'state')
                mode = skip_mode()
                if mode != 'off':
                    introdb_bridge.suspend_for_native()
                else:
                    introdb_bridge.restore_native_guard()
                    close_dialog()
                position = float(player.getTime())
                enabled = setting('seek_previews', 'true').lower() == 'true'
                # Read the small track metadata ahead of the first seek, away
                # from the AV handoff. Images still load only while scrubbing.
                if enabled and not track_requested and time.monotonic() - started >= 2:
                    track_requested = True
                    home.setProperty(PREFIX + 'state', 'loading')
                    track_job = pool.submit(_load_track, ctx, playing, duration, setting('seekr_api_key'))
                if track_job and track_job.done():
                    try:
                        track = track_job.result()
                    except Exception:
                        track = None
                    track_job = None
                    home.setProperty(PREFIX + 'state', 'ready' if track else 'unavailable')
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
                if dialog and (dialog.closed or segment != dialog.segment or
                               xbmc.getCondVisibility('Window.IsActive(videoosd)')):
                    dismissed.add(dialog.segment)
                    close_dialog()
                full = xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)')
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
                seeking = enabled and xbmc.getCondVisibility(
                    '[Player.Seeking | Player.HasPerformedSeek(2)] + Window.IsVisible(seekbar)')
                if not seeking:
                    if seeking_before:
                        _clear(home)
                    seeking_before = False
                    pending_frame = None
                    # A previous scrub may have completed while idle. Never show it later.
                    if frame_job and frame_job.done():
                        frame_job = None
                    if monitor.waitForAbort(0.25):
                        break
                    continue
                if not seeking_before:
                    last_cue = None
                seeking_before = True
                target = seek_target(xbmc.getInfoLabel('Player.SeekTime(hh:mm:ss)'), position, duration)
                if not track_requested:
                    track_requested = True
                    track_job = pool.submit(_load_track, ctx, playing, duration, setting('seekr_api_key'))
                cue = track.at(target) if track else None
                cue_key = cue[:2] if cue else None
                # Missing/loading previews stay silent in the client player.
                if home.getProperty(PREFIX + 'hint'):
                    home.clearProperty(PREFIX + 'hint')
                # Hide the old thumbnail as soon as the scrub crosses a cue boundary.
                if cue_key != last_cue and home.getProperty(PREFIX + 'visible'):
                    _clear(home)
                if frame_job and frame_job.done():
                    try:
                        frame = frame_job.result()
                    except Exception:
                        frame = None
                    frame_job = None
                    if pending_frame == cue_key and frame:
                        frame = (frame[0], frame[1], target)
                        if _publish(home, frame):
                            last_cue = cue_key
                        else:
                            failures[pending_url] = time.monotonic() + 5
                    elif not frame:
                        failures[pending_url] = time.monotonic() + 5
                    pending_frame = None
                elif cue_key and cue_key == last_cue:
                    n = int(target)
                    label = '%d:%02d:%02d' % (n // 3600, (n // 60) % 60, n % 60)
                    if home.getProperty(PREFIX + 'time') != label:
                        home.setProperty(PREFIX + 'time', label)
                if cue_key and cue_key != last_cue and time.monotonic() >= failures.get(cue[2], 0) and not frame_job and time.monotonic() - last_request >= 0.2:
                    directory = os.path.join(xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile')),
                                             'seek_previews', profile_scope.key())
                    pending_frame, pending_url, last_request = cue_key, cue[2], time.monotonic()
                    frame_job = pool.submit(track.frame, target, directory)
                if monitor.waitForAbort(0.25):
                    break
            except Exception:
                close_dialog()
                _clear(home)
                if monitor.waitForAbort(1):
                    break
    finally:
        close_dialog()
        _clear(home)
        introdb_bridge.restore_native_guard()
        home.clearProperty(PREFIX + 'state')
        pool.shutdown(wait=False)
