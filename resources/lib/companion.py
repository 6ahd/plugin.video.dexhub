# -*- coding: utf-8 -*-
import json
import urllib.error
import urllib.parse
import urllib.request
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .session_store import clear_session, load_session, save_session
from . import trakt, simkl, playback_store
from .i18n import tr

_PG_SHOWN = {'key': '', 'at': 0.0}


def _parents_guide_corner():
    """v5.10.322 (GitHub #21): IMDb's Parents Guide in the player's top
    left corner for a few seconds when a video starts, as Nuvio shows it.
    Once per title and file (a track change calls onAVStarted again)."""
    try:
        from .ui_preferences import value
        if str(value('parents_guide') or 'true').lower() == 'false':
            return
        ctx = load_session() or {}
        imdb = ''
        for raw in (ctx.get('show_imdb_id'), ctx.get('imdb_id'), ctx.get('canonical_id')):
            raw = str(raw or '')
            if raw.startswith('tt'):
                imdb = raw.split(':')[0]
                break
        if not imdb:
            return
        try:
            key = '%s|%s' % (imdb, xbmc.Player().getPlayingFile())
        except Exception:
            key = imdb
        now = time.time()
        if _PG_SHOWN['key'] == key and now - _PG_SHOWN['at'] < 6 * 3600:
            return
        _PG_SHOWN.update(key=key, at=now)
    except Exception:
        return

    def run():
        home = xbmcgui.Window(10000)
        try:
            from . import parents_guide as PG
            rows = PG.guide(imdb, timeout=5)
            if not rows:
                return
            player = xbmc.Player()
            if not player.isPlayingVideo():
                return
            home.setProperty('dhs.pg.overlay', PG.lines(rows))
            home.setProperty('dhs.pg.title', 'IMDb ' + ('Parents Guide' if PG._english() else 'دليل الوالدين'))
            monitor = xbmc.Monitor()
            end = time.time() + 12
            while time.time() < end and not monitor.abortRequested():
                if monitor.waitForAbort(0.5) or not player.isPlayingVideo():
                    break
        except Exception as exc:
            xbmc.log('[DexHub] parents guide corner: %s' % exc, xbmc.LOGDEBUG)
        finally:
            home.clearProperty('dhs.pg.overlay')
            home.clearProperty('dhs.pg.title')

    threading.Thread(target=run, name='DexHubParentsGuideCorner', daemon=True).start()

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()

_SOURCE_SWITCH_HANDOFF_PROP = 'dexhub.source_switch_handoff.v2'

# v5.4.11: files that are NEVER DexHub playback, no matter what a (possibly
# stale) session file says. TMDb Helper resolves every play through a local
# dummy clip whose 100ms start/stop double mode-switches the AML display
# pipeline — the companion must be completely silent for it.
_FOREIGN_FILE_MARKERS = (
    'plugin.video.themoviedb.helper',
    'dummy.mp4',
)


_PLAYERS = []


def stop_all():
    """v5.10.241: the service stops: the playback heartbeat leaves at once
    (it waited up to 30 s on its event, past Kodi's 5 s)."""
    for player in list(_PLAYERS):
        try:
            player._progress_heartbeat_stop.set()
        except Exception:
            pass


def _widgets_changed():
    """Playback ended: the skin's continue and next up widgets render fresh."""
    try:
        from . import widget_cache
        widget_cache.mark_changed()
    except Exception:
        pass


def _source_switch_handoff_read():
    try:
        raw = xbmcgui.Window(10000).getProperty(_SOURCE_SWITCH_HANDOFF_PROP) or ''
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            return {}
        expires = float(data.get('expires_at') or 0.0)
        if expires and time.time() > expires:
            xbmcgui.Window(10000).clearProperty(_SOURCE_SWITCH_HANDOFF_PROP)
            return {}
        return data
    except Exception:
        return {}


def _source_switch_handoff_ack(data):
    try:
        payload = dict(data or {})
        if not payload.get('token'):
            return False
        payload['ack'] = True
        payload['ack_at'] = time.time()
        xbmcgui.Window(10000).setProperty(
            _SOURCE_SWITCH_HANDOFF_PROP,
            json.dumps(payload, separators=(',', ':')),
        )
        return True
    except Exception:
        return False


def _monitor_path():
    try:
        import xbmcvfs
        return xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/playback_monitor.log')
    except Exception:
        try:
            return xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/playback_monitor.log')
        except Exception:
            return ''


def _monitor(event, level=xbmc.LOGINFO, **payload):
    record = {'event': str(event or ''), 'ts': int(time.time())}
    for key, value in dict(payload or {}).items():
        if value in (None, '', [], {}, ()): 
            continue
        try:
            record[key] = value if isinstance(value, (dict, list)) else str(value)
        except Exception:
            continue
    try:
        # v5.10.321: tokens in a played URL (X-Plex-Token, api_key) stay out of the log
        from .log import _redact as _hide
        xbmc.log('[DexHubMonitor] %s' % _hide(json.dumps(record, ensure_ascii=False, sort_keys=True)), level)
    except Exception:
        pass
    try:
        path = _monitor_path()
        if path:
            import os
            folder = os.path.dirname(path)
            if folder and not os.path.exists(folder):
                os.makedirs(folder, exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > 262144:
                with open(path, 'rb') as fh:
                    data = fh.read()[-131072:]
                with open(path, 'wb') as fh:
                    fh.write(data)
            with open(path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')
    except Exception:
        pass


def _timeout():
    try:
        return max(3, int(ADDON.getSetting('timeout') or '20'))
    except Exception:
        return 20


# v5.10.81: resume coordinator bounds. READY_POLLS x 200ms is the longest a
# stream may take to really start (45s); SEEK_SETTLE is how long one seek may
# take to land on a remote file before the next attempt.
_RESUME_READY_POLLS = 225
_RESUME_SEEK_SETTLE = 6.0
# v5.10.77: poll interval while nothing is playing. See ProgressLoop.run.
_PROGRESS_IDLE_POLL = 15


def _interval_seconds():
    try:
        return max(2, int(ADDON.getSetting('companion_interval') or '5'))
    except Exception:
        return 5


def companion_enabled():
    try:
        return (ADDON.getSetting('enable_companion_sync') or 'true').lower() == 'true'
    except Exception:
        return True


def _json_post(url, payload, headers=None):
    data = json.dumps(payload or {}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}, **{'Content-Type': 'application/json', 'Accept': 'application/json'}), method='POST')
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:
        return resp.read().decode('utf-8', 'ignore')


def _json_get(url, headers=None):
    req = urllib.request.Request(url, headers=dict(headers or {}, **{'Accept': 'application/json'}), method='GET')
    with urllib.request.urlopen(req, timeout=_timeout()) as resp:
        return json.loads(resp.read().decode('utf-8', 'ignore'))


def _absolute_url(base, maybe_url):
    value = str(maybe_url or '').strip()
    if not value:
        return ''
    if value.startswith('http://') or value.startswith('https://'):
        return value
    if value.startswith('/') and base:
        parsed = urllib.parse.urlparse(base)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{value}"
    return value


def _session_owned_by(ctx):
    """True when the CURRENT stored session belongs to this playback.

    v3.9.152: onPlayBackStopped/Ended used to clear_session()
    unconditionally. When the user switches sources, the OLD stream's stop
    event can arrive AFTER the NEW playback already saved its session — the
    late clear then wipes the new session and switch_source reports
    "nothing to switch" while a video is clearly playing.  Identity is
    compared via stream_key (persisted since v3.9.152) with stream_url as
    fallback; when neither side is comparable we keep the old behaviour.
    """
    try:
        sess = load_session() or {}
    except Exception:
        return True
    mine_uid = (ctx or {}).get('playback_uid') or ''
    cur_uid = sess.get('playback_uid') or ''
    if mine_uid and cur_uid:
        return cur_uid == mine_uid
    mine = (ctx or {}).get('stream_key') or ''
    cur = sess.get('stream_key') or ''
    if not (mine and cur):
        mine = (ctx or {}).get('stream_url') or ''
        cur = sess.get('stream_url') or ''
        if not (mine and cur):
            return True
    return cur == mine


def _clear_tidb_next_episode(ctx):
    """Remove only the TIDB action owned by this playback UID."""
    try:
        from . import next_episode_bridge
        return next_episode_bridge.clear(
            playback_uid=(ctx or {}).get('playback_uid') or '')
    except Exception:
        return False


class BaseReporter(object):
    def paused_tick(self, ctx, position_ms):
        # Pause callbacks already send a scrobble. A stationary periodic tick
        # must not repeat cloud pause requests or synthesize a resume.
        return None

    def started(self, ctx, position_ms=0):
        raise NotImplementedError
    def progress(self, ctx, position_ms):
        raise NotImplementedError
    def paused(self, ctx, position_ms):
        raise NotImplementedError
    def stopped(self, ctx, position_ms):
        raise NotImplementedError
    def ended(self, ctx, position_ms=0):
        raise NotImplementedError


class UrlCompanionReporter(BaseReporter):
    def _headers(self, ctx):
        headers = {}
        client_id = ctx.get('clientIdentifier') or ctx.get('client_id')
        if client_id:
            headers['X-Plex-Client-Identifier'] = client_id
        product = ctx.get('product') or 'Dex Hub'
        headers['X-Plex-Product'] = product
        headers['X-Plex-Platform'] = 'Kodi'
        headers['X-Plex-Device'] = 'Kodi'
        headers['X-Plex-Device-Name'] = ctx.get('deviceName') or 'Kodi'
        if ctx.get('token'):
            headers['X-Plex-Token'] = ctx.get('token')
        return headers

    def _bootstrap(self, ctx):
        if ctx.get('_bootstrapped'):
            return
        url = ctx.get('bootstrapUrl')
        if not url and ctx.get('companionUrl'):
            url = str(ctx['companionUrl']).replace('/companion/timeline/', '/companion/bootstrap/')
        if not url:
            ctx['_bootstrapped'] = True
            return
        try:
            data = _json_get(url, headers=self._headers(ctx))
            if isinstance(data, dict):
                for src, dst in [('timelineUrl','companionUrl'),('scrobbleUrl','companionScrobbleUrl'),('unscrobbleUrl','companionUnscrobbleUrl'),('sessionIdentifier','sessionIdentifier'),('playbackSessionId','playbackSessionId'),('clientIdentifier','clientIdentifier')]:
                    val = data.get(src)
                    val = _absolute_url(url, val)
                    if val:
                        ctx[dst] = val
                interval = data.get('intervalMs')
                if interval and not ctx.get('intervalSeconds'):
                    try:
                        ctx['intervalSeconds'] = max(2, int(float(interval) / 1000.0))
                    except Exception:
                        pass
                if data.get('duration') and not ctx.get('duration_ms'):
                    try:
                        ctx['duration_ms'] = int(data.get('duration'))
                    except Exception:
                        pass
        except Exception as exc:
            xbmc.log('[DexHub] companion bootstrap error: %s' % exc, xbmc.LOGWARNING)
        ctx['_bootstrapped'] = True

    def _payload(self, ctx, position_ms, state, mark_watched=None):
        payload = {
            'state': state,
            'time': int(position_ms or 0),
            'duration': int(ctx.get('duration_ms') or 0),
            'playbackTime': int(position_ms or 0),
            'sessionIdentifier': ctx.get('sessionIdentifier') or '',
            'playbackSessionId': ctx.get('playbackSessionId') or ctx.get('sessionIdentifier') or '',
            'deviceName': ctx.get('deviceName') or 'Kodi',
            'device': 'Kodi',
            'platform': 'Kodi',
            'product': ctx.get('product') or 'Dex Hub',
        }
        if mark_watched is not None:
            payload['markWatched'] = bool(mark_watched)
        return payload

    def _post(self, ctx, key, position_ms, state, mark_watched=None):
        self._bootstrap(ctx)
        url = ctx.get(key)
        if not url:
            return None
        return _json_post(url, self._payload(ctx, position_ms, state, mark_watched), headers=self._headers(ctx))

    def started(self, ctx, position_ms=0):
        return self._post(ctx, 'companionUrl', position_ms, 'playing')

    def progress(self, ctx, position_ms):
        return self._post(ctx, 'companionUrl', position_ms, 'playing')

    def paused(self, ctx, position_ms):
        return self._post(ctx, 'companionUrl', position_ms, 'paused')

    def stopped(self, ctx, position_ms):
        return self._post(ctx, 'companionUrl', position_ms, 'stopped')

    def ended(self, ctx, position_ms=0):
        if ctx.get('companionScrobbleUrl'):
            return self._post(ctx, 'companionScrobbleUrl', position_ms or int(ctx.get('duration_ms') or 0), 'stopped', mark_watched=True)
        return self._post(ctx, 'companionUrl', position_ms or int(ctx.get('duration_ms') or 0), 'stopped', mark_watched=True)


class PlexReporter(BaseReporter):
    def _headers(self, ctx):
        return {
            'X-Plex-Product': ctx.get('product', 'Dex Hub'),
            'X-Plex-Version': ctx.get('product_version', '2.6.0'),
            'X-Plex-Client-Identifier': ctx['client_id'],
            'X-Plex-Provides': 'player',
            'X-Plex-Device-Name': ctx.get('device_name', 'Kodi'),
            'X-Plex-Platform': 'Kodi',
            'X-Plex-Model': 'Kodi',
            'X-Plex-Device': ctx.get('device_name', 'Kodi'),
        }

    def _get(self, ctx, path, params):
        params = dict(params)
        params['X-Plex-Token'] = ctx['token']
        url = ctx['server_url'].rstrip('/') + path + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(ctx), method='GET')
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            return resp.read()

    def _timeline(self, ctx, position_ms, state):
        rating_key = ctx['rating_key']
        duration_ms = int(ctx.get('duration_ms', 0))
        params = {
            'duration': duration_ms,
            'guid': 'com.plexapp.plugins.library',
            'key': f'/library/metadata/{rating_key}',
            'containerKey': f'/library/metadata/{rating_key}',
            'ratingKey': str(rating_key),
            'state': state,
            'time': int(position_ms),
        }
        return self._get(ctx, '/:/timeline', params)

    def _scrobble(self, ctx):
        return self._get(ctx, '/:/scrobble', {'key': str(ctx['rating_key']), 'identifier': 'com.plexapp.plugins.library'})

    def started(self, ctx, position_ms=0):
        return self._timeline(ctx, position_ms, 'playing')
    def progress(self, ctx, position_ms):
        return self._timeline(ctx, position_ms, 'playing')
    def paused(self, ctx, position_ms):
        return self._timeline(ctx, position_ms, 'paused')
    def stopped(self, ctx, position_ms):
        return self._timeline(ctx, position_ms, 'stopped')
    def ended(self, ctx, position_ms=0):
        self._timeline(ctx, position_ms or int(ctx.get('duration_ms', 0)), 'stopped')
        return self._scrobble(ctx)


class EmbyReporter(BaseReporter):
    def _headers(self, ctx):
        return {
            'Content-Type': 'application/json',
            'X-Emby-Token': ctx['token'],
            'X-Emby-Authorization': (
                'MediaBrowser Client="Kodi", Device="Kodi", DeviceId="%s", Version="1.0.0"'
                % (ctx.get('device_id') or 'dexhub')
            ),
        }

    def _post(self, ctx, path, payload):
        url = ctx['server_url'].rstrip('/') + path
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=self._headers(ctx), method='POST')
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            return resp.read()

    def _payload(self, ctx, position_ms, paused=False):
        payload = {
            'ItemId': ctx['item_id'],
            'PositionTicks': int(position_ms) * 10000,
            'IsPaused': paused,
            'PlayMethod': 'DirectStream',
            'CanSeek': True,
            'SessionId': ctx.get('session_id') or ctx.get('device_id') or 'dexhub',
        }
        # v3.9.241: Emby rejected /Sessions/Playing with HTTP 400 (53x in one
        # session log) — it ties reports to the PlaybackInfo session and wants
        # PlaySessionId (+ MediaSourceId) when the stream came from PlaybackInfo.
        if ctx.get('play_session_id'):
            payload['PlaySessionId'] = ctx['play_session_id']
        if ctx.get('media_source_id'):
            payload['MediaSourceId'] = ctx['media_source_id']
        return payload

    def started(self, ctx, position_ms=0):
        return self._post(ctx, '/Sessions/Playing', self._payload(ctx, position_ms, paused=False))
    def progress(self, ctx, position_ms):
        payload = self._payload(ctx, position_ms, paused=False); payload['EventName']='TimeUpdate'; return self._post(ctx, '/Sessions/Playing/Progress', payload)
    def paused(self, ctx, position_ms):
        payload = self._payload(ctx, position_ms, paused=True); payload['EventName']='Pause'; return self._post(ctx, '/Sessions/Playing/Progress', payload)
    def stopped(self, ctx, position_ms):
        payload = self._payload(ctx, position_ms, paused=False); payload['EventName']='Stop'; return self._post(ctx, '/Sessions/Playing/Stopped', payload)
    def ended(self, ctx, position_ms=0):
        payload = self._payload(ctx, position_ms or int(ctx.get('duration_ms', 0)), paused=False); payload['EventName']='Stop'; return self._post(ctx, '/Sessions/Playing/Stopped', payload)


from .providers.silo_playback import NativeReporter as SiloReporter




class TraktReporter(BaseReporter):
    # v5.10.103: progress reaches this reporter from the service's progress
    # loop (every few seconds) as well as from the heartbeat, and each one was
    # a Trakt scrobble/start call. Trakt only needs start on playback start and
    # on resume; while the video runs, one refresh every ten minutes keeps a
    # long session current.
    # v5.10.206: every two minutes (was ten), so another device resumes
    # close to where this one stopped
    PROGRESS_EVERY = 120.0

    def __init__(self):
        self._sent_at = 0.0
        self._paused = False

    def _start(self, ctx, position_ms):
        self._sent_at = time.monotonic()
        self._paused = False
        return trakt.scrobble('start', ctx, position_ms)

    def started(self, ctx, position_ms=0):
        return self._start(ctx, position_ms)

    def progress(self, ctx, position_ms):
        # v5.10.206: watched the moment 90 % is crossed (a stop scrobble);
        # Trakt answers a second stop for the same title with 409, so the
        # stop at the real end is harmless
        if _push_watched_once(self, 'trakt', ctx, position_ms,
                              lambda: trakt.scrobble('stop', ctx, position_ms)):
            return None
        if getattr(self, '_finished_sent', False):
            return None
        if self._paused or time.monotonic() - self._sent_at >= self.PROGRESS_EVERY:
            return self._start(ctx, position_ms)     # a resume, or the periodic refresh
        return None

    def paused(self, ctx, position_ms):
        self._paused = True
        return trakt.scrobble('pause', ctx, position_ms)

    def stopped(self, ctx, position_ms):
        if getattr(self, '_finished_sent', False):
            return None          # already scrobbled as watched at 90 %
        return trakt.scrobble('pause', ctx, position_ms)

    def ended(self, ctx, position_ms=0):
        return trakt.scrobble('stop', ctx, position_ms)


class SimklReporter(BaseReporter):
    """v5.10.206: Simkl hears where you are as you watch (its scrobble API:
    start, pause, every two minutes), not only at the end. The finished
    title still goes to its history once (mark_watched_from_ctx: threshold
    and a per-session dedupe), now as soon as the threshold is crossed."""

    def __init__(self):
        self._sent_at = 0.0
        self._paused = False

    def _start(self, ctx, position_ms):
        self._sent_at = time.monotonic()
        self._paused = False
        from . import progress_push
        return progress_push.simkl_scrobble('start', ctx, position_ms)

    def started(self, ctx, position_ms=0):
        return self._start(ctx, position_ms)

    def progress(self, ctx, position_ms):
        def finish():
            from . import progress_push
            progress_push.simkl_scrobble('stop', ctx, position_ms)
            simkl.mark_watched_from_ctx(ctx, position_ms)
        if _push_watched_once(self, 'simkl', ctx, position_ms, finish):
            return None
        if getattr(self, '_finished_sent', False):
            return None
        if self._paused or time.monotonic() - self._sent_at >= 120.0:
            return self._start(ctx, position_ms)
        return None

    def paused(self, ctx, position_ms):
        self._paused = True
        from . import progress_push
        return progress_push.simkl_scrobble('pause', ctx, position_ms)

    def stopped(self, ctx, position_ms):
        if not getattr(self, '_finished_sent', False):
            from . import progress_push
            progress_push.simkl_scrobble('pause', ctx, position_ms)
        return simkl.mark_watched_from_ctx(ctx, position_ms)

    def ended(self, ctx, position_ms=0):
        from . import progress_push
        progress_push.simkl_scrobble('stop', ctx, position_ms)
        # Natural completion is authoritative even if Kodi closed before the
        # duration or final position could be read.
        return simkl.mark_watched_from_ctx(ctx, position_ms, force=True)


class NuvioReporter(BaseReporter):
    """v5.10.206: Nuvio hears where you are at once: on pause, stop and end,
    and every two minutes while the video runs. Before, the row waited for
    the next cloud sync, which never runs during playback."""

    def __init__(self):
        self._sent_at = 0.0

    def _push(self, ctx, position_ms, finished=False):
        from . import progress_push
        self._sent_at = time.monotonic()
        return progress_push.push_nuvio(ctx, position_ms, finished=finished)

    def started(self, ctx, position_ms=0):
        self._sent_at = time.monotonic()      # nothing worth saying at 0:00
        return None

    def progress(self, ctx, position_ms):
        if _push_watched_once(self, 'nuvio', ctx, position_ms,
                              lambda: self._push(ctx, position_ms, finished=True)):
            return None
        if getattr(self, '_finished_sent', False):
            return None
        if position_ms >= 30000 and time.monotonic() - self._sent_at >= 120.0:
            return self._push(ctx, position_ms)
        return None

    def paused(self, ctx, position_ms):
        if position_ms >= 30000 and not getattr(self, '_finished_sent', False):
            return self._push(ctx, position_ms)
        return None

    def stopped(self, ctx, position_ms):
        finished = should_mark_watched(position_ms, (ctx or {}).get('duration_ms') or 0)
        if position_ms < 30000 and not finished:
            return None
        return self._push(ctx, position_ms, finished=finished)

    def ended(self, ctx, position_ms=0):
        return self._push(ctx, position_ms or (ctx or {}).get('duration_ms') or 0, finished=True)


def _push_watched_once(reporter, name, ctx, position_ms, send):
    """Send ``send()`` once, the moment playback crosses the watched mark
    (90 %), instead of waiting for the player to close."""
    if getattr(reporter, '_finished_sent', False):
        return False
    if not should_mark_watched(position_ms, (ctx or {}).get('duration_ms') or 0):
        return False
    reporter._finished_sent = True
    try:
        send()
    except Exception as exc:
        xbmc.log('[DexHub] %s watched at 90%%: %s' % (name, exc), xbmc.LOGINFO)
    return True


class MultiReporter(BaseReporter):
    def __init__(self, reporters):
        self.reporters = [r for r in (reporters or []) if r]

    def _call(self, method_name, ctx, position_ms=0):
        results = []
        for reporter in self.reporters:
            try:
                results.append(getattr(reporter, method_name)(ctx, position_ms))
            except TypeError:
                results.append(getattr(reporter, method_name)(ctx))
            except Exception as exc:
                # v3.9.241: one line per playback, not 53 — the log showed the
                # same Emby 400 repeated every heartbeat for a whole session.
                if not getattr(self, '_report_err_logged', False):
                    self._report_err_logged = True
                    xbmc.log('[DexHub] reporter %s error (repeats suppressed): %s' % (method_name, exc), xbmc.LOGWARNING)
        return results

    def started(self, ctx, position_ms=0):
        return self._call('started', ctx, position_ms)

    def progress(self, ctx, position_ms):
        return self._call('progress', ctx, position_ms)

    def paused_tick(self, ctx, position_ms):
        return self._call('paused_tick', ctx, position_ms)

    def paused(self, ctx, position_ms):
        return self._call('paused', ctx, position_ms)

    def stopped(self, ctx, position_ms):
        return self._call('stopped', ctx, position_ms)

    def ended(self, ctx, position_ms=0):
        return self._call('ended', ctx, position_ms)


def _ask_rating(ctx, finale):
    """v5.10.153: "How was it?" after a movie (tracking_rate_prompt)."""
    try:
        from . import tracking_ui
        tracking_ui.maybe_rate(dict(ctx or {}), finale=finale)
    except Exception as exc:
        xbmc.log('[DexHub] rating prompt: %s' % exc.__class__.__name__, xbmc.LOGINFO)


def get_reporter(ctx):
    if not ctx or ctx.get('iptv'):
        # v5.10.239: an IPTV play is kept on this device only
        return None
    reporters = []
    server_reporter = None
    if ctx.get('companionUrl') or ctx.get('companionScrobbleUrl'):
        server_reporter = UrlCompanionReporter()
    elif ctx.get('server_type') == 'plex' and ctx.get('server_url') and ctx.get('token') and ctx.get('rating_key'):
        server_reporter = PlexReporter()
    elif ctx.get('server_type') in ('emby', 'jellyfin') and ctx.get('server_url') and ctx.get('token') and ctx.get('item_id'):
        server_reporter = EmbyReporter()
    elif ctx.get('server_type') == 'silo' and ctx.get('server_url') and ctx.get('token') and ctx.get('item_id'):
        server_reporter = SiloReporter()
    if server_reporter:
        reporters.append(server_reporter)
    ids_present = any(ctx.get(k) for k in ('imdb_id', 'tmdb_id', 'tvdb_id'))
    if trakt.enabled() and trakt.scrobble_enabled():
        if ids_present:
            reporters.append(TraktReporter())
    # v4.1.0: Simkl watched-history reporter — fires only at stop/ended and
    # only when the account is linked, so it costs nothing during playback.
    if simkl.enabled() and simkl.authorized():
        if ids_present:
            reporters.append(SimklReporter())
    # v5.10.206: Nuvio's progress goes at once, not with the next cloud sync
    try:
        from . import progress_push
        if progress_push.nuvio_ready():
            reporters.append(NuvioReporter())
    except Exception:
        pass
    if not reporters:
        return None
    if len(reporters) == 1:
        return reporters[0]
    return MultiReporter(reporters)


def should_mark_watched(position_ms, duration_ms, threshold=0.9):
    try:
        return duration_ms > 0 and (float(position_ms) / float(duration_ms)) >= threshold
    except Exception:
        return False


def _safe_int(value, default=0):
    try:
        return int(value or 0)
    except Exception:
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value or 0.0)
    except Exception:
        return default


def _local_percent(position_ms, duration_ms):
    try:
        if duration_ms and duration_ms > 0:
            return max(0.0, min(100.0, (float(position_ms) / float(duration_ms)) * 100.0))
    except Exception:
        pass
    return 0.0


def _save_local_progress(ctx, position_ms=0, duration_ms=0, finished=False):
    if not isinstance(ctx, dict) or not ctx:
        return
    if ctx.get('iptv'):
        # v5.10.239: an IPTV film or episode keeps its place apart (its ids
        # are the subscription's: iptv_progress)
        try:
            from . import iptv_progress
            iptv_progress.save(ctx, _safe_float(position_ms) / 1000.0, _safe_float(duration_ms) / 1000.0,
                               finished=finished)
        except Exception as exc:
            xbmc.log('[DexHub] IPTV progress save failed: %s' % exc, xbmc.LOGWARNING)
        else:
            if finished or position_ms >= 30000:
                _refresh_cw_containers()
        return
    media_type = (ctx.get('media_type') or '').lower()
    canonical_id = ctx.get('canonical_id') or ''
    if not canonical_id:
        return
    is_episode = media_type in ('series', 'anime', 'show', 'tv') or (ctx.get('season') not in (None, '', 0, '0') and ctx.get('episode') not in (None, '', 0, '0'))
    if not media_type:
        media_type = 'series' if is_episode else 'movie'
    video_id = ctx.get('video_id') or canonical_id
    season = _safe_int(ctx.get('season'), 0) if is_episode else None
    episode = _safe_int(ctx.get('episode'), 0) if is_episode else None
    title = ctx.get('show_title') or ctx.get('title') or canonical_id
    provider_name = ctx.get('provider_name') or 'Dex Hub'
    poster = ctx.get('poster') or ''
    background = ctx.get('background') or ''
    clearlogo = ctx.get('clearlogo') or ''
    duration_sec = _safe_float(duration_ms, 0.0) / 1000.0
    position_sec = _safe_float(position_ms, 0.0) / 1000.0
    percent = 100.0 if finished else _local_percent(position_ms, duration_ms)
    event_type = 'watched' if finished else 'progress'
    # Persist standard ids alongside Dex Hub's provider-specific ids.  The
    # latter are often opaque (for example a Stremio addon id), whereas TMDb
    # Helper launches with IMDb/TMDb/TVDb ids.  Keeping both makes a future
    # handoff resume the same row instead of starting from 0:00.
    external_ids = ctx.get('external_ids') or {}
    if not isinstance(external_ids, dict):
        external_ids = {}
    tmdb_id = str(ctx.get('tmdb_id') or external_ids.get('tmdb_id') or external_ids.get('tmdb') or '').strip()
    imdb_id = str(ctx.get('imdb_id') or external_ids.get('imdb_id') or external_ids.get('imdb') or '').strip()
    tvdb_id = str(ctx.get('tvdb_id') or external_ids.get('tvdb_id') or external_ids.get('tvdb') or '').strip()
    show_tmdb_id = str(ctx.get('show_tmdb_id') or (tmdb_id if is_episode else '')).strip()
    try:
        playback_store.upsert_entry(
            media_type,
            canonical_id,
            video_id,
            title,
            provider_name,
            poster,
            background,
            clearlogo,
            season,
            episode,
            position_sec,
            duration_sec,
            percent,
            ctx.get('stream_url') or '',
            event_type,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
            show_tmdb_id=show_tmdb_id,
            native_server_id=str(ctx.get('server_id') or '').strip(),
            native_item_id=str(ctx.get('rating_key') or ctx.get('item_id') or '').strip(),
        )
    except Exception as exc:
        xbmc.log('[DexHub] local progress save failed: %s' % exc, xbmc.LOGWARNING)
    else:
        _invalidate_nextup_cache()
        # Wake up the home / CW UI so the row reflects this save without
        # the user having to navigate away and back. Skipped when we're
        # mid-stream and only saving an interim progress tick — those
        # don't change the visible row order or membership.
        if finished or position_ms >= 30000:
            _refresh_cw_containers()


def _invalidate_nextup_cache():
    try:
        win = xbmcgui.Window(10000)
        for key in ('dexhub.nextup_cache', 'dexhub.nextup_cache_ts'):
            try:
                win.clearProperty(key)
            except Exception:
                pass
    except Exception:
        pass


# Paths whose visible content is driven by playback_store / favorites and
# therefore needs a Container.Refresh after each progress save. Matched as
# substrings against Container.FolderPath.
_CW_REFRESH_PATHS = (
    'plugin.video.dexhub/?',     # root home page (CW row + nextup row)
    'plugin.video.dexhub/',      # any DexHub container
    'action=continue',           # full Continue Watching list
    'action=favorites',          # favorites screen
    'action=nextup',             # next-up list
    'action=home',               # explicit home action
)


def _refresh_cw_containers():
    """Trigger Container.Refresh on the current container if it's CW-related,
    AND set a stale-flag on Window(10000) so that the next time the user
    opens a CW-displaying screen it rebuilds from fresh data even if Kodi
    served its directory cache.

    Why both:
      - Container.Refresh handles the "user is staring at CW right now"
        case (e.g. episode ended → Kodi auto-returned to the previous
        container which was the CW list).
      - The stale-flag handles "user finished playback then navigated
        elsewhere then came back to CW", where Container.Refresh isn't
        applicable because no DexHub container was visible.
    """
    # Always set the stale flag so the next CW-screen open rebuilds.
    try:
        win = xbmcgui.Window(10000)
        win.setProperty('dexhub.cw_dirty', '1')
        win.setProperty('dexhub.cw_dirty_ts', str(int(time.time())))
    except Exception:
        pass

    # If a CW-affected DexHub container is the current container, refresh it.
    # Never re-enter a directory while Kodi is playing or waiting for a
    # plugin result. The dirty flag above still preserves the next refresh.
    try:
        if xbmc.Player().isPlaying() or xbmc.getCondVisibility(
                'Window.IsActive(busydialog) | Window.IsActive(busydialognocancel) | Window.IsActive(selectdialog)'):
            return
    except Exception:
        return
    try:
        current = xbmc.getInfoLabel('Container.FolderPath') or ''
    except Exception:
        return
    if not current or 'plugin.video.dexhub' not in current:
        return
    try:
        from urllib.parse import urlsplit, parse_qs
        route = parse_qs(urlsplit(current).query).get('action', [''])[0]
    except Exception:
        return
    if route not in ('', 'continue', 'favorites', 'nextup', 'home'):
        return
    try:
        xbmc.executebuiltin('Container.Refresh')
    except Exception:
        pass


def _clear_tmdbh_handoff_flag():
    try:
        for win_id in (10000,):
            try:
                win = xbmcgui.Window(win_id)
            except Exception:
                continue
            for key in ('dexhub.invoked_by_tmdbh', 'dexhub.tmdbh_seed_ids', 'dexhub.tmdbh_seed_for', 'dexhub.tmdbh_handoff_until'):
                try:
                    win.clearProperty(key)
                except Exception:
                    pass
    except Exception:
        pass


def _same_address(current, url):
    """Is Kodi's playing file ``current`` the stream ``url``? Kodi may report
    it with or without the request headers after '|' (homeui director)."""
    if not current or not url:
        return False
    if current == url:
        return True
    from urllib.parse import unquote
    current, url = unquote(str(current).split('|', 1)[0]), unquote(str(url).split('|', 1)[0])
    return current == url or current.split('?', 1)[0] == url.split('?', 1)[0]



def _publish_playback_artwork(ctx):
    if not isinstance(ctx, dict) or not ctx:
        return
    try:
        logo = ctx.get('clearlogo') or ''
        poster = ctx.get('poster') or ''
        fanart = ctx.get('background') or poster or ''
        title = ctx.get('title') or ''
        plot = ctx.get('plot') or ''
        year = str(ctx.get('year') or '')
        for win_id in (10000,):
            try:
                win = xbmcgui.Window(win_id)
            except Exception:
                continue
            for key, value in (
                ('dexhub.source.clearlogo', logo),
                ('dexhub.source.poster', poster),
                ('dexhub.source.thumb', poster),
                ('dexhub.source.fanart', fanart),
                ('dexhub.source.title', title),
                ('dexhub.source.plot', plot),
                ('dexhub.source.year', year),
                ('clearlogo', logo),
                ('logo', logo),
                ('tvshow.clearlogo', logo),
                ('poster', poster),
                ('thumb', poster),
                ('fanart', fanart),
                ('fanart_image', fanart),
            ):
                try:
                    win.setProperty(key, value)
                except Exception:
                    pass
        # v5.10.298: the player bar in the title's colour (when chosen)
        try:
            from . import osd_look
            osd_look.follow_playing(fanart or None)
        except Exception:
            pass
    except Exception:
        pass


def _is_internal_navigation_path(path):
    value = str(path or '').strip()
    if not value.startswith('plugin://plugin.video.dexhub/'):
        return False
    markers = (
        'action=streams', 'action=episode_streams', 'action=item_open',
        'action=series_meta', 'action=play_item', 'action=cw_resume',
        'action=cw_play_from_start', 'action=play'
    )
    return any(marker in value for marker in markers)


class CompanionPlayer(xbmc.Player):
    def onAVStarted(self):
        if not self._foreign_active:
            try:
                from . import nowplaying   # 5.10.302: the Home keeps the playing title's logo
                nowplaying.from_session(load_session() or {})
            except Exception:
                pass
        try:
            from . import osd_look      # v5.10.298: the bar follows the playing title's colours
            osd_look.follow_playing()
        except Exception:
            pass
        # v5.10.177: real playback (a channel, a film) and a trailer with
        # sound never start at a volume a silent preview left at zero
        try:
            import xbmcaddon
            import xbmcvfs
            from .homeui.director import ensure_audible
            ensure_audible(xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile')))
        except Exception:
            pass
        # Warm the optional custom OSD from actual stream labels and local
        # JSON once. No HTTP, polling, subtitle changes or skin reloads.
        # v5.10.141: the folder style too (the user's images, the skin's
        # where the folder has none)
        try:
            if (xbmc.getSkinDir() == 'skin.dexhub' and
                    xbmcgui.Window(10000).getProperty('dhs.osd.badge.style') in ('custom', 'folder')):
                from . import player_badges
                player_badges.publish()
        except Exception:
            pass

        if not self._foreign_active:
            _parents_guide_corner()

    def onAVChange(self):
        self.onAVStarted()

    def __init__(self):
        super().__init__()
        _PLAYERS.append(self)
        self.ctx = {}
        self.reporter = None
        # Playback health tracking for fallback.
        self._play_start_time = 0.0
        self._had_error = False
        self._progress_seen_ms = 0
        self._fallback_chain = []
        self._next_episode = None
        self._upnext_sent = False
        self._started_reported = False
        self._started_probe_pending = False
        self._resume_applied = False
        self._resume_in_progress = False
        self._resume_generation = 0
        self._subtitle_restore_generation = 0
        self._subtitle_restored = False
        self._progress_heartbeat_token = 0
        self._progress_heartbeat_stop = threading.Event()
        self._active_playback_uid = ''
        self._last_started_file = ''
        self._last_started_at = 0.0
        self._last_position_ms = 0
        self._last_duration_ms = 0
        # Flag we set when we're the ones triggering the next play (avoid loops).
        self._own_trigger = False
        # v5.4.11: True while the CURRENT playback does not belong to DexHub
        # (TMDb Helper dummy, other addons, local files). While set, every
        # Player callback is a hard no-op: no Window-property writes, no
        # probe/heartbeat threads, no Player polling, no session or monitor
        # traffic. Kodi 22 on AML resets the display pipeline around every
        # playback start/stop, and running the companion machinery inside
        # that window (for a clip that was never ours) is what wedged the
        # box on stop → instant replay.
        self._foreign_active = False
        # v5.4.11: last playback_uid whose artwork was published to the home
        # window. Pause/resume used to republish the exact same 15 values.
        self._artwork_published_uid = ''
        # Delayed fallback timer for false-positive start/stop handoffs.
        self._fallback_pending = False
        self._started_reported = False
        self._started_probe_pending = False

    def _refresh_context(self):
        self.ctx = load_session() or {}
        base = self.ctx.get('provider_base_url') or self.ctx.get('server_url') or ''
        for key in ('bootstrapUrl', 'companionUrl', 'companionScrobbleUrl', 'companionUnscrobbleUrl'):
            if self.ctx.get(key):
                self.ctx[key] = _absolute_url(base, self.ctx.get(key))
        self.reporter = get_reporter(self.ctx)
        # v5.3: source choice is always user-owned.  Ignore and remove any
        # fallback chain left by an older session so a playback error cannot
        # silently start a different URL.
        self._fallback_chain = []
        self.ctx.pop('fallback_stream_keys', None)
        self._next_episode = self.ctx.get('next_episode') or None
        self._upnext_sent = False
        self._had_error = False
        self._progress_seen_ms = 0
        self._own_trigger = False
        self._fallback_pending = False
        self._started_reported = False
        self._started_probe_pending = False
        self._resume_applied = False
        self._resume_in_progress = False
        self._resume_generation += 1
        self._subtitle_restore_generation += 1
        self._subtitle_restored = False
        self._progress_heartbeat_token += 1
        try:
            self._progress_heartbeat_stop.set()
        except Exception:
            pass
        self._active_playback_uid = str(self.ctx.get('playback_uid') or '')
        self._last_started_file = self._current_playing_file()
        import time as _t
        self._last_started_at = _t.time()
        self._play_start_time = self._last_started_at

    def _enter_foreign_playback(self, reason, playing_file=''):
        """Mark the current playback as not-ours and go fully idle.

        v5.4.11 root-cause fix for the hard AML freeze on stop → replay:
        the companion previously ran its FULL machinery for every playback
        in Kodi, including TMDb Helper's 100ms dummy.mp4 — ~17 Window(10000)
        property operations (each a GuiLock → LockFrameMoveGuard handshake
        with the app thread), Player polling at 4Hz from probe threads, and
        session/monitor traffic — synchronously on the announce pump, at
        the exact moment CVideoPlayer::CloseFile tears down the Amlogic
        codec and the windowing system re-inits GLES for the mode switch
        (kodi.log: 'GLES: Maximum texture width' between the dummy stop and
        the next tmdb_player resolution). Foreign playback now executes
        zero DexHub work inside that window."""
        self._foreign_active = True
        self._stop_progress_heartbeat()
        try:
            xbmc.log('[DexHub] companion idle: foreign playback (%s)%s'
                     % (reason, (' file=%s' % playing_file) if playing_file else ''),
                     xbmc.LOGDEBUG)
        except Exception:
            pass

    def _publish_artwork_async(self, ctx):
        """Publish playback artwork OFF the announce/callback path, once per
        playback_uid.

        _publish_playback_artwork performs ~15 Window(10000) property writes
        and each goes through Kodi's GuiLock, i.e. LockFrameMoveGuard — a
        synchronisation handshake with the app thread between frames. Doing
        that inline inside Player callbacks stalls the announce pump exactly
        while AML resets the display pipeline around playback start/stop.
        A short worker publishes instead, and identical republishes from
        pause/resume are coalesced away."""
        if not isinstance(ctx, dict) or not ctx:
            return
        uid = str(ctx.get('playback_uid') or '')
        if not uid:
            uid = 'file:%s' % (ctx.get('url') or ctx.get('title') or '')
        if uid == self._artwork_published_uid:
            return
        self._artwork_published_uid = uid
        snapshot = dict(ctx)

        def _job():
            if self._active_playback_uid != str(snapshot.get('playback_uid') or ''):
                return
            _publish_playback_artwork(snapshot)
            try:
                from . import player_presentation
                extras = player_presentation.cached_extras(snapshot)
                if self._active_playback_uid == str(snapshot.get('playback_uid') or ''):
                    player_presentation.publish(extras)
            except Exception:
                pass

        try:
            threading.Thread(target=_job, name='DexHubArtPublish',
                             daemon=True).start()
        except RuntimeError:
            # Thread quota exhausted — publish inline rather than lose art.
            _publish_playback_artwork(snapshot)

    def _apply_introdb_identity_after_start(self):
        """Finish TheIntroDB metadata after Kodi 22's safe handoff.

        TheIntroDB reads ``Player.GetItem`` and this player's
        ``VideoInfoTag`` after ``onAVStarted``. Its service loop waits one
        second, so applying the small identity here avoids mutating the
        crash-sensitive handoff ListItem while still preceding its lookup.
        """
        if not self.ctx.get('introdb_post_start'):
            return False
        try:
            ready = bool(self.isPlayingVideo())
        except Exception:
            ready = False
        if not ready:
            # v5.10.103: onPlayBackStarted comes before the video is open, and
            # asking Kodi for the playing item's tag then only fails with an
            # EXCEPTION line in kodi.log. Write it once the video is open.
            ctx = self.ctx

            def _when_open():
                monitor = xbmc.Monitor()
                for _ in range(100):
                    if monitor.waitForAbort(0.1) or self.ctx is not ctx:
                        return
                    try:
                        if self.isPlayingVideo():
                            break
                    except Exception:
                        return
                else:
                    return
                self._write_introdb_identity()
            try:
                threading.Thread(target=_when_open, name='DexHubIntroDBIdentity',
                                 daemon=True).start()
            except RuntimeError:
                pass
            return False
        return self._write_introdb_identity()

    def _write_introdb_identity(self):
        class _ActivePlayerItem(object):
            def __init__(self, player):
                self.player = player

            def setProperty(self, _key, _value):
                # ListItem properties were already written during handoff.
                return None

            def getVideoInfoTag(self, *args, **kwargs):
                return self.player.getVideoInfoTag()

        try:
            from . import introdb_bridge
            applied = introdb_bridge.apply_playback_identity(
                _ActivePlayerItem(self), self.ctx,
                apply_video_tag=True)
            if applied:
                xbmc.log('[DexHub][TheIntroDB] Kodi 22 post-start identity applied',
                         xbmc.LOGINFO)
            return applied
        except Exception as exc:
            xbmc.log('[DexHub][TheIntroDB] post-start identity skipped: %s' % exc,
                     xbmc.LOGWARNING)
            return False

    def _position_ms(self):
        try:
            if self._player_looks_active_for_clock():
                offset = float(((self.ctx.get('silo_session') or {}).get('timeline') or {}).get(
                    'timeline_offset_seconds') or 0)
                value = int((self.getTime() + offset) * 1000)
                if value >= 0:
                    self._last_position_ms = value
                    return value
        except Exception:
            pass
        return int(self._last_position_ms or 0)

    def _duration_ms(self):
        source_duration = (self.ctx.get('silo_session') or {}).get('source_duration')
        if source_duration:
            return int(float(source_duration) * 1000)
        try:
            if self._player_looks_active_for_clock():
                total = int(self.getTotalTime() * 1000)
                if total > 0:
                    self._last_duration_ms = total
                    return total
        except Exception:
            pass
        return int(self._last_duration_ms or self.ctx.get('duration_ms', 0) or 0)

    def _player_looks_active_for_clock(self):
        try:
            return bool(self.isPlaying() or self.isPlayingVideo())
        except Exception:
            return False

    def _current_playing_file(self):
        try:
            return self.getPlayingFile() or ''
        except Exception:
            return ''

    def _player_looks_active(self, require_real_media=False):
        playing_file = self._current_playing_file()
        if playing_file and _is_internal_navigation_path(playing_file):
            return False
        try:
            if self.isPlayingVideo():
                return True
        except Exception:
            pass
        if playing_file:
            return True
        try:
            if xbmc.getCondVisibility('Player.HasVideo'):
                if not require_real_media:
                    return True
                return self._position_ms() > 0
        except Exception:
            pass
        return False

    def _restore_switched_subtitle_async(self):
        """Restore the exact external subtitle after a source replacement.

        Kodi treats a replacement URL as a new video. Subtitle-service dialogs
        can therefore rebuild the subtitle list *after* AV start. Restoring too
        early makes the saved track disappear when the user closes that dialog.
        Wait for the player, resume seek, and modal dialogs to settle, then
        verify the selected external path and retry a small bounded number of
        times.
        """
        if self._subtitle_restored:
            return
        path = str(self.ctx.get('switch_subtitle_path') or '').strip()
        if not path:
            self._subtitle_restored = True
            return
        enabled = bool(self.ctx.get('switch_subtitle_enabled', True))
        uid = str(self._active_playback_uid or self.ctx.get('playback_uid') or '')
        self._subtitle_restore_generation += 1
        generation = self._subtitle_restore_generation

        def _same_path(left, right):
            try:
                l = str(xbmcvfs.translatePath(left) or left).replace('\\', '/').lower()
                r = str(xbmcvfs.translatePath(right) or right).replace('\\', '/').lower()
                return bool(l and r and l == r)
            except Exception:
                return str(left or '') == str(right or '')

        def _dialog_active():
            # getCurrentWindowDialogId is available on Kodi 19+ and catches
            # subtitle-service pickers even when their skin XML name differs.
            try:
                return int(xbmcgui.getCurrentWindowDialogId() or 0) > 0
            except Exception:
                pass
            for cond in ('Window.IsActive(subtitlesearch)',
                         'Window.IsActive(subtitles)',
                         'Dialog.IsActive(subtitlesearch)'):
                try:
                    if xbmc.getCondVisibility(cond):
                        return True
                except Exception:
                    pass
            return False

        def _runner():
            try:
                # Wait up to 90s: the user may keep a subtitle picker open.
                # Require a quiet dialog-free window for ~1.25s before attach.
                quiet_ticks = 0
                for _ in range(360):
                    if generation != self._subtitle_restore_generation:
                        return
                    if uid and uid != str(self._active_playback_uid or ''):
                        return
                    ready = self._player_looks_active(require_real_media=True) and not self._resume_in_progress
                    if ready and not _dialog_active():
                        quiet_ticks += 1
                        if quiet_ticks >= 5:
                            break
                    else:
                        quiet_ticks = 0
                    xbmc.sleep(250)
                else:
                    return

                if not path.startswith(('http://', 'https://')):
                    try:
                        if not xbmcvfs.exists(path):
                            _monitor('subtitle-restore-missing', level=xbmc.LOGWARNING, path=path, playback_uid=uid)
                            return
                    except Exception:
                        pass

                # A subtitle service can mutate the list just after its dialog
                # closes. Re-attach at most three times, never an unbounded loop.
                restored = False
                for attempt in range(3):
                    if generation != self._subtitle_restore_generation:
                        return
                    if uid and uid != str(self._active_playback_uid or ''):
                        return
                    if _dialog_active():
                        xbmc.sleep(500)
                        continue
                    try:
                        self.setSubtitles(path)
                        xbmc.sleep(450)
                        self.showSubtitles(bool(enabled))
                    except Exception as exc:
                        _monitor('subtitle-restore-failed', level=xbmc.LOGWARNING, error=exc, path=path, playback_uid=uid, attempt=attempt + 1)
                        xbmc.sleep(600)
                        continue
                    try:
                        current = str(self.getSubtitles() or '').strip()
                    except Exception:
                        current = ''
                    if not current or _same_path(current, path):
                        restored = True
                        break
                    xbmc.sleep(700)

                if not restored:
                    _monitor('subtitle-restore-unverified', level=xbmc.LOGWARNING, path=path, playback_uid=uid)
                    return
                self._subtitle_restored = True
                self.ctx['switch_subtitle_restored'] = True
                self.ctx['source_switch_reuse_subtitle'] = False
                try:
                    save_session(self.ctx)
                except Exception:
                    pass
                _monitor('subtitle-restored-after-switch', path=path, enabled=enabled, playback_uid=uid)
            except Exception as exc:
                _monitor('subtitle-restore-error', level=xbmc.LOGWARNING, error=exc, playback_uid=uid)

        try:
            threading.Thread(target=_runner, name='DexHubSubtitleRestore', daemon=True).start()
        except Exception as exc:
            xbmc.log('[DexHub] subtitle restore thread failed: %s' % exc, xbmc.LOGWARNING)

    def _back_keymap_async(self, playing):
        """v5.10.100: Back in the player keeps a Dex Hub video playing.

        Holds Dex Hub's player keymap (homeui/keymap.py) while its own video
        plays, so a Back that Kodi or another add-on maps to Stop (Stremio for
        Kodi's map, a remote's B button, a long press) returns to the page
        below with the video going on. Applied two seconds after the change,
        away from the display mode switch at start and stop; only the latest
        change is applied.
        """
        token = object()
        self._keymap_token = token

        def run():
            if xbmc.Monitor().waitForAbort(2.0) or getattr(self, '_keymap_token', None) is not token:
                return
            try:
                from .homeui import keymap
                if playing:
                    keymap.hold('play')
                else:
                    keymap.release('play')
            except Exception as exc:
                xbmc.log('[DexHub] player keymap %s failed: %s' % (
                    'hold' if playing else 'release', exc), xbmc.LOGWARNING)
        thread = threading.Thread(target=run, name='DexHub-player-keymap')
        thread.daemon = True
        thread.start()

    def _fullscreen_guard_async(self, started_at):
        """v5.10.115: Dex Hub's video opens in the full-screen player.

        Kodi switches to its player once the streams are ready, unless the
        start was windowed. A start that took over the player of a preview
        (a trailer behind the Home, a hub or a title page) could stay behind
        the page on some boxes (CoreELEC, Kodi 22) until Back was pressed.
        Once the video really plays, the player is brought up; only once, so
        Back from the player still leaves the video playing behind the page.

        v5.10.166: a key pressed while the stream was still starting no
        longer leaves the video behind the title page (the user's report:
        the video plays unseen until Back). The start of a debrid or Usenet
        stream takes seconds, and any key in that time (or a remote that
        sends its own events) counted as "the user is elsewhere". Now only a
        page the user opened since the start keeps the video windowed: an
        other window, or a title page that was not the one the play came
        from. The title page the play came from is closed even when Kodi
        already switched to the player under it (Kodi closes it only when it
        is the top dialog, and the busy spinner was often above it). The
        wait for the first frames is 150 s instead of 30.
        """
        token = object()
        self._fullscreen_token = token
        cond = xbmc.getCondVisibility
        try:
            start_window = xbmcgui.getCurrentWindowId()
            start_title_page = bool(cond('Window.IsVisible(movieinformation)'))
        except Exception:
            start_window, start_title_page = 0, False

        def elsewhere(window):
            """A window the user opened since the start (Settings, another section).
            The Home, the player and add-on pages (Dex Hub's own) are not."""
            return window not in (start_window, 10000, 12005) and not 13000 <= window < 13100

        def run():
            monitor = xbmc.Monitor()
            deadline = time.time() + 150.0
            seen_player = False
            while time.time() < deadline and not monitor.abortRequested():
                if monitor.waitForAbort(0.25) or getattr(self, '_fullscreen_token', None) is not token:
                    return
                try:
                    player = xbmc.Player()
                    if not player.isPlayingVideo():
                        continue
                    if cond('Window.IsActive(fullscreenvideo)'):
                        # Kodi's own switch: Back from it is the user's choice.
                        # Kodi closes the title page only when it is the top
                        # dialog; with the busy spinner above it, the player
                        # came up under it: closed at once here
                        seen_player = True
                        if start_title_page and cond('Window.IsVisible(movieinformation)'):
                            xbmc.executebuiltin('Dialog.Close(movieinformation,true)')
                            xbmc.log('[DexHub] player: closed the title page left over the player', xbmc.LOGINFO)
                    if float(player.getTime() or 0.0) <= 0.0:
                        continue
                except Exception:
                    continue
                # Kodi's own switch comes right after the first frames
                if monitor.waitForAbort(0.5) or getattr(self, '_fullscreen_token', None) is not token:
                    return
                try:
                    if cond('Window.IsActive(visualisation)') or not cond('Player.HasVideo'):
                        return
                    if xbmcgui.Window(10000).getProperty('dexhub.trailer.active') == '1':
                        return          # a preview, windowed on purpose
                    full = bool(cond('Window.IsActive(fullscreenvideo)'))
                    if seen_player and not full:
                        xbmc.log('[DexHub] player: left windowed (Back from the player)', xbmc.LOGINFO)
                        return
                except Exception:
                    return
                announced = False
                for _attempt in range(12):
                    if getattr(self, '_fullscreen_token', None) is not token:
                        return
                    try:
                        if not cond('Player.HasVideo'):
                            return
                        window = xbmcgui.getCurrentWindowId()
                        if elsewhere(window):
                            xbmc.log('[DexHub] player: left windowed (window %s was opened since the start)'
                                     % window, xbmc.LOGINFO)
                            return
                        title_page = bool(cond('Window.IsVisible(movieinformation)'))
                        if title_page and not start_title_page:
                            xbmc.log('[DexHub] player: left windowed (a title page was opened since the start)',
                                     xbmc.LOGINFO)
                            return
                        if title_page:
                            # the title page the video was started from
                            xbmc.executebuiltin('Dialog.Close(movieinformation,true)')
                            xbmc.log('[DexHub] player: closed the title page left over the video', xbmc.LOGINFO)
                        if cond('Window.IsActive(busydialog) | Window.IsActive(busydialognocancel)'):
                            # Kodi refuses a window while a modal dialog is up:
                            # the busy spinner of the start can still sit over the video
                            for name in ('busydialog', 'busydialognocancel'):
                                xbmc.executebuiltin('Dialog.Close(%s,true)' % name)
                        if title_page and monitor.waitForAbort(0.3):
                            return
                        if cond('Window.IsActive(fullscreenvideo)'):
                            if not cond('Window.IsVisible(movieinformation)'):
                                return
                            continue
                        if not announced:
                            xbmc.log('[DexHub] player: the video started behind window %s; opening the player'
                                     % window, xbmc.LOGINFO)
                            announced = True
                        xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
                    except Exception:
                        return
                    if monitor.waitForAbort(0.5):
                        return
                try:
                    xbmc.log('[DexHub] player: the player did not come up (window %s, dialog %s)' % (
                        xbmcgui.getCurrentWindowId(), xbmcgui.getCurrentWindowDialogId()), xbmc.LOGWARNING)
                except Exception:
                    pass
                return
        thread = threading.Thread(target=run, name='DexHub-player-fullscreen')
        thread.daemon = True
        thread.start()

    def _player_ui_check_async(self):
        """v5.10.98: one look at the player once the video really plays.

        Reports what sits on top of the playing video (window, dialog, modal)
        so a report of "OK shows no player controls" has an answer in
        kodi.log, and closes a busy spinner still up over a video that has
        been playing for two seconds: skins hide that spinner during playback,
        but as a modal dialog it keeps taking the remote's keys, the OK that
        should open the player controls included.
        """
        token = object()
        self._ui_check_token = token

        def run():
            monitor = xbmc.Monitor()
            deadline = time.time() + 45.0
            first = None
            while time.time() < deadline and not monitor.abortRequested():
                if monitor.waitForAbort(0.5) or getattr(self, '_ui_check_token', None) is not token:
                    return
                try:
                    player = xbmc.Player()
                    if not player.isPlayingVideo():
                        continue
                    position = float(player.getTime() or 0.0)
                except Exception:
                    continue
                if first is None:
                    first = position
                    continue
                if position - first < 2.0:
                    continue
                try:
                    window = xbmcgui.getCurrentWindowId()
                    dialog = xbmcgui.getCurrentWindowDialogId()
                    modal = xbmc.getCondVisibility('System.HasModalDialog')
                    busy = xbmc.getCondVisibility('Window.IsActive(busydialog) | '
                                                  'Window.IsActive(busydialognocancel)')
                except Exception:
                    return
                xbmc.log('[DexHub] player ui: window=%s dialog=%s modal=%s busy=%s' % (
                    window, dialog, modal, busy), xbmc.LOGINFO)
                if busy:
                    for name in ('busydialog', 'busydialognocancel'):
                        try:
                            xbmc.executebuiltin('Dialog.Close(%s,true)' % name)
                        except Exception:
                            pass
                    xbmc.log('[DexHub] player ui: closed a busy spinner left over the playing video',
                             xbmc.LOGINFO)
                return
        thread = threading.Thread(target=run, name='DexHub-player-ui')
        thread.daemon = True
        thread.start()

    def _confirm_started_async(self):
        """Ensure the shared playback lifecycle worker is running.

        v5.2 spawned a short start-probe thread *and* a separate long-lived
        progress heartbeat for every playback.  The heartbeat now owns the
        bounded start probe too, so duplicate Kodi Started callbacks do not
        consume another native thread.
        """
        if self._started_probe_pending or self._started_reported:
            return
        self._start_progress_heartbeat()

    def _schedule_fallback(self, reason='', delay_ms=3200):
        """Automatic source fallback is intentionally disabled in v5.3."""
        self._fallback_pending = False
        return False

    def _report(self, method_name, position_ms=None):
        if not self.reporter:
            return
        try:
            from .silo_lifecycle import send_report
            return send_report(self.reporter, self.ctx, method_name,
                               self._position_ms() if position_ms is None else position_ms,
                               companion_enabled())
        except TypeError:
            getattr(self.reporter, method_name)(self.ctx)
        except urllib.error.HTTPError as exc:
            xbmc.log('[DexHub] companion %s HTTP %s' % (method_name, exc.code), xbmc.LOGWARNING)
        except Exception as exc:
            xbmc.log('[DexHub] companion %s error: %s' % (method_name, exc), xbmc.LOGWARNING)

    def _apply_resume_async(self):
        """Apply one coordinated resume seek for the current playback session.

        Older builds had two independent seek loops (plugin post-start chores
        and CompanionPlayer), each retrying several times.  That produced
        repeated Seeking overlays, stalls, and occasional jumps back to zero.
        CompanionPlayer is now the sole resume coordinator.  A generation
        token invalidates stale callbacks as soon as another item/source starts.
        """
        if self._resume_applied or self._resume_in_progress:
            return
        try:
            target = float(self.ctx.get('resume_seconds') or 0.0)
            timeline = (self.ctx.get('silo_session') or {}).get('timeline') or {}
            if 'player_start_seconds' in timeline:
                target = float(timeline['player_start_seconds'])
        except Exception:
            target = 0.0
        try:
            resume_pct = float(self.ctx.get('resume_percent') or 0.0)
        except Exception:
            resume_pct = 0.0
        has_silo_timeline = 'player_start_seconds' in ((self.ctx.get('silo_session') or {}).get('timeline') or {})
        if has_silo_timeline:
            # A remux can start at source 1200s and player 0s. Deriving a
            # percentage seek here would apply the source resume twice.
            resume_pct = 0.0
        # A 30s floor is right for an ORDINARY resume (ignore trivial offsets),
        # but WRONG for an explicit source switch: switch_source captures any
        # position > 1.0s, so switching early in a title (e.g. at 00:20) was
        # silently dropped here and the new source restarted from 00:00.
        # When a switch is pending, honour whatever was captured.
        switching = bool(self.ctx.get('source_switch_pending'))
        min_resume = 1.0 if switching or has_silo_timeline else 30.0

        if target < min_resume and not (1.0 < resume_pct < 95.0):
            self._resume_applied = True
            return

        self._resume_in_progress = True
        self._resume_generation += 1
        generation = self._resume_generation

        def _runner():
            success = False
            try:
                stable_total = 0.0
                stable_count = 0
                # Wait for real media and a stable duration.  Do not seek while
                # Kodi is still opening/replacing the stream during source switch.
                # v5.10.81: wait for playback to really run, not for a fixed 10s.
                # kodi.log: a Plex stream via plex.dexworld.cc reported
                # playback-started at 05:58:11 but only played from 05:58:28,
                # and an AIOStreams stream was similar. The old loop gave up
                # after 10s, seeked into a stream that was still opening, Kodi
                # ignored the seek, and the title restarted from 00:00 (the
                # user stopped it at 4.7s). A stream is ready once its duration
                # is stable AND its clock is advancing; the ceiling only bounds
                # a stream that never starts.
                last_clock = -1.0
                advancing = False
                for _ in range(_RESUME_READY_POLLS):
                    if generation != self._resume_generation:
                        return
                    xbmc.sleep(200)
                    if not self._player_looks_active(require_real_media=True):
                        continue
                    try:
                        total = float(self.getTotalTime() or 0.0)
                    except Exception:
                        total = 0.0
                    try:
                        clock = float(self.getTime() or 0.0)
                    except Exception:
                        clock = 0.0
                    if last_clock >= 0.0 and clock > last_clock and clock > 0.3:
                        advancing = True
                    last_clock = clock
                    if total > 60.0:
                        if abs(total - stable_total) < 1.0:
                            stable_count += 1
                        else:
                            stable_total = total
                            stable_count = 1
                        if stable_count >= 2 and advancing:
                            break

                if generation != self._resume_generation or not self._player_looks_active(require_real_media=True):
                    return
                total = stable_total
                seek_to = target
                # Only derive from percent when there is no usable absolute
                # position; during a switch the captured seconds are authoritative.
                if seek_to < min_resume and 1.0 < resume_pct < 95.0 and total > 60.0:
                    seek_to = total * resume_pct / 100.0
                if total > 0.0:
                    seek_to = min(seek_to, max(0.0, total - 90.0))
                if seek_to < min_resume:
                    success = True
                    return

                try:
                    current = float(self.getTime() or 0.0)
                except Exception:
                    current = 0.0
                # Kodi/native player may already have resumed. Never seek again
                # when already close to or beyond the desired point.
                # The floor must not exceed the target: with a small switch
                # target (e.g. 20s) a hardcoded 15s floor could read the freshly
                # opened stream as "already resumed" and skip the seek entirely.
                already_at = max(min(15.0, seek_to * 0.5), seek_to - 8.0)
                if current >= already_at:
                    success = True
                    _monitor('resume-already-applied', title=self.ctx.get('title') or '', target_seconds='%.1f' % seek_to, current_seconds='%.1f' % current)
                    return

                # One normal attempt plus one recovery attempt only.  Repeated
                # loops are intentionally avoided because each call shows Kodi's
                # Seeking overlay and can reset adaptive streams.
                for attempt in range(2):
                    if generation != self._resume_generation:
                        return
                    try:
                        self.seekTime(seek_to)
                    except Exception:
                        continue
                    # v5.10.81: a seek on a remote file is a new ranged request and
                    # can take several seconds to land. One reading after 1.2s
                    # called slow-but-successful seeks failures; poll until it
                    # lands or the settle window closes.
                    settle_until = time.monotonic() + _RESUME_SEEK_SETTLE + (2.0 if attempt else 0.0)
                    current = 0.0
                    while True:
                        xbmc.sleep(400)
                        if generation != self._resume_generation:
                            return
                        try:
                            current = float(self.getTime() or 0.0)
                        except Exception:
                            current = 0.0
                        if current >= already_at or time.monotonic() >= settle_until:
                            break
                    if current >= already_at:
                        success = True
                        _monitor('resume-applied', title=self.ctx.get('title') or '', target_seconds='%.1f' % seek_to, current_seconds='%.1f' % current, attempt=attempt + 1)
                        return
                _monitor('resume-failed', level=xbmc.LOGWARNING, title=self.ctx.get('title') or '', target_seconds='%.1f' % seek_to, resume_percent='%.2f' % resume_pct)
            except Exception as exc:
                _monitor('resume-error', level=xbmc.LOGWARNING, error=exc, title=self.ctx.get('title') or '')
            finally:
                if generation == self._resume_generation:
                    self._resume_in_progress = False
                    self._resume_applied = True

        try:
            threading.Thread(target=_runner, name='DexHubResumeCoordinator', daemon=True).start()
        except Exception:
            self._resume_in_progress = False
            self._resume_applied = True

    def _start_progress_heartbeat(self):
        """Persist and sync progress while playback is active.

        Kodi does not emit a general progress callback. Previously a crash,
        power loss, or force-close could lose everything since the last pause.
        A single daemon heartbeat saves locally every 30 seconds and reports to
        connected companions/Trakt every 60 seconds. A generation token makes
        old playback threads exit immediately when a new item starts.
        """
        try:
            self._progress_heartbeat_stop.set()
        except Exception:
            pass
        self._progress_heartbeat_token += 1
        token = self._progress_heartbeat_token
        stop_event = threading.Event()
        self._progress_heartbeat_stop = stop_event
        self._started_probe_pending = True
        probe_uid = str(self._active_playback_uid or
                        self.ctx.get('playback_uid') or '')

        def _runner():
            try:
                started = False
                for _ in range(32):
                    if stop_event.is_set() or token != self._progress_heartbeat_token:
                        return
                    if probe_uid and probe_uid != str(self._active_playback_uid or ''):
                        return
                    if self._player_looks_active(require_real_media=True):
                        started = True
                        if not self._started_reported:
                            self._started_reported = True
                            _monitor(
                                'playback-started',
                                title=self.ctx.get('title') or '',
                                canonical_id=self.ctx.get('canonical_id') or '',
                                provider=(self.ctx.get('provider_name') or
                                          self.ctx.get('provider_id') or ''),
                                playing_file=self._current_playing_file(),
                            )
                            self._report('started')
                            try:
                                from . import playback_choices
                                playback_choices.save_started(dict(self.ctx))
                            except Exception as exc:
                                xbmc.log('[DexHub] remember played source failed: %s' % type(exc).__name__, xbmc.LOGDEBUG)
                        break
                    if stop_event.wait(0.25):
                        return
                if not started:
                    _monitor(
                        'playback-start-timeout', level=xbmc.LOGWARNING,
                        title=self.ctx.get('title') or '',
                        canonical_id=self.ctx.get('canonical_id') or '',
                        provider=(self.ctx.get('provider_name') or
                                  self.ctx.get('provider_id') or ''),
                        playing_file=self._current_playing_file(),
                    )
                    return

                ticks = 0
                while (token == self._progress_heartbeat_token and
                       not stop_event.wait(30.0)):
                    if not self._player_looks_active(require_real_media=True):
                        return
                    if self._resume_in_progress:
                        continue
                    pos = self._position_ms()
                    dur = self._duration_ms()
                    if pos < 15000:
                        continue
                    self._progress_seen_ms = max(
                        self._progress_seen_ms, int(pos or 0))
                    if dur > 0:
                        self.ctx['duration_ms'] = int(dur)
                    _save_local_progress(self.ctx, pos, dur, finished=False)
                    ticks += 1
                    if ticks % 2 == 0:
                        self._report('paused_tick' if xbmc.getCondVisibility('Player.Paused') else 'progress', position_ms=pos)
                    # Hand the next episode to service.upnext once, early. The
                    # Up Next add-on owns the near-end popup + countdown timing
                    # from the runtime we supply (same pattern DPlex/Umbrella
                    # use). DexHub's own end-of-episode prompt stays as the
                    # fallback when service.upnext is absent.
                    if not self._upnext_sent:
                        try:
                            self._maybe_notify_upnext()
                        except Exception:
                            pass
            finally:
                if token == self._progress_heartbeat_token:
                    self._started_probe_pending = False

        try:
            threading.Thread(target=_runner, name='DexHubPlaybackLifecycle',
                             daemon=True).start()
        except Exception as exc:
            self._started_probe_pending = False
            xbmc.log('[DexHub] progress heartbeat failed: %s' % exc, xbmc.LOGWARNING)

    def _stop_progress_heartbeat(self):
        self._progress_heartbeat_token += 1
        self._started_probe_pending = False
        try:
            self._progress_heartbeat_stop.set()
        except Exception:
            pass

    def _run_post_start_async(self):
        """v5.4.11: run the plugin-published post-start subtitle job.

        The play dispatch used to spawn two threads inside ITS OWN invoker
        (subtitle lifecycle + post-start chores).  They kept the invoker in
        Kodi's waiting-on-thread loop long after the script ended, which
        broke reuselanguageinvoker and piled up zombie sub-interpreters —
        the second half of the stop → replay AML freeze.  The dispatch now
        only publishes a job property; this long-lived interpreter owns the
        actual work, keyed to the active playback_uid so a stale job can
        never touch the wrong stream."""
        uid = str(self._active_playback_uid or self.ctx.get('playback_uid') or '')
        if not uid:
            return
        try:
            from .playback import post_start
            job = post_start.take_job(uid)
        except Exception as exc:
            try:
                xbmc.log('[DexHub] post-start job pickup failed: %s' % exc,
                         xbmc.LOGWARNING)
            except Exception:
                pass
            return
        if not job:
            return

        def _runner():
            try:
                post_start.run_job(job, monitor=_monitor)
            except Exception as exc:
                _monitor('play-post-start-error', level=xbmc.LOGWARNING,
                         error=exc, title=self.ctx.get('title') or '')

        try:
            threading.Thread(target=_runner, name='DexHubPostStart',
                             daemon=True).start()
        except RuntimeError as exc:
            try:
                xbmc.log('[DexHub] post-start worker unavailable: %s' % exc,
                         xbmc.LOGWARNING)
            except Exception:
                pass

    def onPlayBackStarted(self):
        # Adaptive/HLS players may emit Started more than once for the same
        # media item (manifest open, decoder re-open, quality change). Do not
        # reset the resume coordinator or spawn another heartbeat for those
        # duplicate callbacks.
        try:
            pending = load_session() or {}
        except Exception:
            pending = {}
        pending_uid = str(pending.get('playback_uid') or '')
        # v5.10.96: Dex Hub Home background trailers set this flag before they
        # start. They are previews, never a session to resume, scrobble or
        # attach subtitles to.
        try:
            home = xbmcgui.Window(10000)
            if home.getProperty('dexhub.trailer.active') == '1':
                trailer_url = home.getProperty('dexhub.trailer.url')
                # v5.10.109: skin.dexhub's title page marks its trailer too
                # (dhs.trailer); real playback that replaced that trailer
                # before the mark was cleared is not a trailer.
                # v5.10.115: the same for any preview (the Home's, a hub's,
                # a parked one): what plays now is not the preview's address
                replaced = False
                if trailer_url and not trailer_url.startswith('pvr://'):
                    # (a live channel's preview plays its own stream address)
                    playing_now = self._current_playing_file()
                    differs = bool(playing_now) and not _same_address(playing_now, trailer_url)
                    replaced = differs and bool(home.getProperty('dhs.trailer') or pending_uid)
                if not replaced:
                    self._enter_foreign_playback('dexhub-home-trailer')
                    return
                for key in ('dexhub.trailer.active', 'dexhub.trailer.url', 'dhs.trailer', 'dhs.trailer.seen'):
                    home.clearProperty(key)
        except Exception:
            pass
        # v5.4.11 ownership gate — decide from the session file alone, BEFORE
        # touching the Player or any window property. No DexHub session and
        # no armed uid means this start is not ours (TMDb Helper dummy, other
        # addons, local files): go idle without a single Kodi API call.
        if not pending_uid and not self._active_playback_uid:
            self._enter_foreign_playback('no-session')
            return
        current_file = self._current_playing_file()
        # v5.10.218: a trailer by its fingerprint or its address, whatever
        # marks were lost on the way: never a session to save or scrobble
        try:
            from . import trailer_mark
            if trailer_mark.playing_is_trailer() or trailer_mark.is_trailer_url(current_file, picked=bool(pending_uid)):
                self._enter_foreign_playback('trailer-fingerprint', current_file)
                return
        except Exception:
            pass
        _cf_low = (current_file or '').lower()
        if _cf_low and all(m in _cf_low for m in _FOREIGN_FILE_MARKERS):
            # A stale session must never let the TMDbH dummy arm the
            # companion — the real playback of that session comes next.
            self._enter_foreign_playback('tmdbh-dummy', current_file)
            return
        self._foreign_active = False
        import time as _t
        now = _t.time()
        same_uid = bool(pending_uid and pending_uid == self._active_playback_uid)
        same_file = bool(current_file and current_file == self._last_started_file)
        # A new source can resolve to the exact same URL/file. When both sides
        # have UIDs, identity is UID-only; using same_file here suppressed a
        # genuine replacement and left the old context/threads active.
        duplicate_identity = same_uid if (pending_uid and self._active_playback_uid) else same_file
        if duplicate_identity and (now - float(self._last_started_at or 0.0)) < 12.0:
            _monitor('playback-started-duplicate-suppressed', title=self.ctx.get('title') or pending.get('title') or '', playback_uid=pending_uid, playing_file=current_file)
            self._publish_artwork_async(self.ctx or pending)
            self._confirm_started_async()
            return
        self._refresh_context()
        _clear_tmdbh_handoff_flag()
        self._apply_introdb_identity_after_start()
        self._publish_artwork_async(self.ctx)
        self._apply_resume_async()
        self._fullscreen_guard_async(now)
        self._player_ui_check_async()
        self._back_keymap_async(True)
        self._restore_switched_subtitle_async()
        self._run_post_start_async()
        self._start_progress_heartbeat()
        # v3.9.104: schedule pre-cache for the next episode while this one
        # plays. It is strictly opt-in and remains OFF by default so a small
        # CoreELEC box never pays for speculative network/thread work.
        try:
            self._schedule_next_episode_precache()
        except Exception as _pc_exc:
            try:
                xbmc.log('[DexHub] precache schedule failed: %s' % _pc_exc, xbmc.LOGDEBUG)
            except Exception:
                pass

    def _schedule_next_episode_precache(self):
        """Fire-and-forget: read next_episode hint from session ctx and ask
        the addon to pre-scrape it in the background. Silently no-ops if the
        feature is disabled, if there's no next episode, or if the current
        item isn't a TV episode."""
        nxt = self._next_episode or {}
        if not nxt:
            return
        # Explicit opt-in only. A missing setting must behave as OFF too.
        try:
            from xbmcaddon import Addon as _Addon
            raw = (_Addon().getSetting('pre_cache_next_episode') or 'false').strip().lower()
            if raw not in ('true', '1', 'yes', 'on'):
                return
        except Exception:
            pass
        try:
            canonical_id = nxt.get('canonical_id') or nxt.get('canonical') or ''
            video_id = nxt.get('video_id') or ''
            season = nxt.get('season')
            episode = nxt.get('episode')
            title = nxt.get('title') or nxt.get('show_title') or ''
            media_type = nxt.get('media_type') or 'series'
        except Exception:
            return
        if not canonical_id or not season or not episode:
            return
        # Precache uses the player-only worker, not the browsing router.
        try:
            from . import player_runtime as _plg
            _plg._precache_episode_async(
                canonical_id=canonical_id,
                video_id=video_id,
                season=season,
                episode=episode,
                title=title,
                media_type=media_type,
                delay_seconds=60,
            )
        except Exception as exc:
            try:
                xbmc.log('[DexHub] precache dispatch failed: %s' % exc, xbmc.LOGWARNING)
            except Exception:
                pass

    def onPlayBackPaused(self):
        if self._foreign_active or not self.ctx:
            return
        if self._resume_in_progress:
            _monitor('playback-pause-during-resume-suppressed', title=self.ctx.get('title') or '')
            return
        self._publish_artwork_async(self.ctx)
        _monitor('playback-paused', title=self.ctx.get('title') or '', position_ms=self._position_ms())
        pos = self._position_ms()
        self._progress_seen_ms = max(self._progress_seen_ms, int(pos or 0))
        dur = self._duration_ms()
        _save_local_progress(self.ctx, pos, dur, finished=False)
        self._report('paused', position_ms=pos)

    def onPlayBackResumed(self):
        if self._foreign_active or not self.ctx:
            return
        if self._resume_in_progress:
            _monitor('playback-resume-during-resume-suppressed', title=self.ctx.get('title') or '')
            return
        self._publish_artwork_async(self.ctx)
        _monitor('playback-resumed', title=self.ctx.get('title') or '', position_ms=self._position_ms())
        pos = self._position_ms()
        self._progress_seen_ms = max(self._progress_seen_ms, int(pos or 0))
        _save_local_progress(self.ctx, pos, self._duration_ms(), finished=False)
        self._report('progress', position_ms=pos)

    def onPlayBackError(self):
        """Kodi 20+ fires this on format/codec/network failures."""
        if self._foreign_active:
            return
        self._back_keymap_async(False)
        _clear_tmdbh_handoff_flag()
        self._had_error = True
        _monitor('playback-error', level=xbmc.LOGWARNING, title=self.ctx.get('title') or '', canonical_id=self.ctx.get('canonical_id') or '', provider=self.ctx.get('provider_name') or self.ctx.get('provider_id') or '')
        if self._fallback_chain:
            xbmc.log('[DexHub] onPlayBackError — scheduling fallback grace', xbmc.LOGWARNING)
            self._schedule_fallback('playback-error', delay_ms=3200)
        else:
            xbmc.log('[DexHub] onPlayBackError — no fallback configured', xbmc.LOGWARNING)


    def onPlayBackStopped(self):
        # v5.10.239: a Home trailer's end (foreign) changes no watch progress:
        # the widgets are not marked stale for it (30 trailers an hour each
        # rebuilt Continue Watching)
        if not self._foreign_active:
            _widgets_changed()
        self._stop_progress_heartbeat()
        self._resume_generation += 1
        self._resume_in_progress = False
        if self._foreign_active:
            # Foreign playback ended (e.g. the TMDbH dummy): reset the flag
            # and touch NOTHING else. No Window-property clears, no session
            # reads/writes, no monitor events — this callback lands inside
            # the AML display-mode-switch window. This also removes the
            # garbage 'playback-stopped' rows that reported the previous
            # movie's position for a 100ms dummy clip.
            self._foreign_active = False
            return
        try:
            from . import nowplaying
            nowplaying.clear()
        except Exception:
            pass
        self._back_keymap_async(False)
        _clear_tmdbh_handoff_flag()
        sess_for_reopen = load_session() or {}
        # Kodi has already detached the player by the time this callback runs
        # on several Kodi 22 builds. Never call getTime/getTotalTime after Stop;
        # use the last heartbeat or the exact source-switch snapshot instead.
        handoff = _source_switch_handoff_read()
        my_uid = str(self.ctx.get('playback_uid') or '')
        handoff_old_uid = str(handoff.get('old_uid') or '')
        handoff_matches = bool(handoff.get('token') and (not handoff_old_uid or handoff_old_uid == my_uid))
        switching = bool(sess_for_reopen.get('source_switch_pending'))
        same_uid = bool(my_uid and (my_uid == str(sess_for_reopen.get('playback_uid') or '')))
        if handoff_matches or (switching and same_uid):
            try:
                pos = int(float(sess_for_reopen.get('resume_seconds') or 0.0) * 1000)
            except Exception:
                pos = int(self._last_position_ms or self._progress_seen_ms or 0)
            try:
                dur = int(float(sess_for_reopen.get('duration') or 0.0) * 1000)
            except Exception:
                dur = int(self._last_duration_ms or self.ctx.get('duration_ms', 0) or 0)
            self._progress_seen_ms = max(self._progress_seen_ms, pos)
            if handoff_matches:
                _source_switch_handoff_ack(handoff)
            _monitor('source-switch-old-stop-suppressed', title=self.ctx.get('title') or '', playback_uid=self.ctx.get('playback_uid') or '', position_ms=pos, duration_ms=dur)
            # Do not report stopped/unscrobble, clear the session, or trigger
            # fallback. The replacement invocation owns the handoff now.
            _clear_tidb_next_episode(self.ctx)
            self.ctx = {}
            self.reporter = None
            self._fallback_chain = []
            self._next_episode = None
            return
        pos = int(self._last_position_ms or self._progress_seen_ms or 0)
        dur = int(self._last_duration_ms or self.ctx.get('duration_ms', 0) or 0)
        self._progress_seen_ms = max(self._progress_seen_ms, int(pos or 0))
        watched = should_mark_watched(pos, dur)
        # False-positive fallback fix: some hosts briefly stop/reopen the
        # player while the real stream continues. Only auto-fallback when
        # playback died almost immediately AND we never reached meaningful
        # progress, or Kodi explicitly reported a playback error.
        import time as _t
        elapsed = _t.time() - (self._play_start_time or 0)
        played_little = (pos < 3000)
        no_real_progress = (self._progress_seen_ms < 3000)
        # Do not auto-play another source when the user backs out or stops
        # early. Kodi reports those cases the same way as an early stop, so the
        # old no-progress fallback could re-enter the source search and start
        # the movie again.
        #
        # Tightened in 3.8.11: Kodi 20/21 fire onPlayBackError as a
        # false-positive on transient HLS hiccups, codec changes, and
        # Stop-after-Pause scenarios. `_had_error` alone is not a reliable
        # signal. Require ALL of:
        #   - had_error                  (Kodi-reported error)
        #   - elapsed < 60s wall-clock   (user couldn't have watched much)
        #   - progress_seen_ms < 30000   (player never advanced past 30s)
        # Anything else is a user-initiated stop — no auto-fallback.
        probably_failed = (
            bool(self._had_error)
            and elapsed < 60.0
            and self._progress_seen_ms < 30000
        )
        if probably_failed and self._fallback_chain:
            _monitor('stopped-probably-failed', level=xbmc.LOGWARNING, title=self.ctx.get('title') or '', elapsed='%.2f' % elapsed, position_ms=pos, duration_ms=dur, progress_seen_ms=self._progress_seen_ms, had_error=self._had_error, fallback_count=len(self._fallback_chain or []))
            self._schedule_fallback('stopped-early', delay_ms=3200)
            # Don't clear session yet — the fallback or recovered playback will reload it.
            return
        try:
            # v5.10.185: a title stopped can be played again at once (the
            # double press guard of playback_engine holds it for a while)
            xbmcgui.Window(10000).clearProperty('dexhub.play.handoff')
        except Exception:
            pass
        if self._had_error and not probably_failed:
            _monitor('stopped-error-but-user-progress', title=self.ctx.get('title') or '',
                     elapsed='%.2f' % elapsed, progress_seen_ms=self._progress_seen_ms,
                     position_ms=pos, note='auto-fallback suppressed')
        if watched:
            _monitor('playback-ended', title=self.ctx.get('title') or '', position_ms=dur or pos, duration_ms=dur, watched=True)
            _save_local_progress(self.ctx, dur or pos, dur or pos, finished=True)
            _ask_rating(self.ctx, self._next_episode is None)
        elif pos >= 15000 or _local_percent(pos, dur) >= 1.0:
            _monitor('playback-stopped', title=self.ctx.get('title') or '', position_ms=pos, duration_ms=dur, watched=False)
            _save_local_progress(self.ctx, pos, dur, finished=False)
        if self.reporter:
            if watched:
                self._report('ended', position_ms=dur or pos)
            else:
                self._report('stopped', position_ms=pos)
                if self.ctx.get('companionUnscrobbleUrl'):
                    try:
                        self.reporter._post(self.ctx, 'companionUnscrobbleUrl', pos, 'stopped', mark_watched=False)
                    except Exception as exc:
                        xbmc.log('[DexHub] companion unscrobble error: %s' % exc, xbmc.LOGWARNING)
        # User request: after Back/Stop, stay where Kodi returns naturally.
        # Do not reopen the source picker or trigger a fresh source search.
        try:
            _monitor('stop-reopen-sources-suppressed', title=self.ctx.get('title') or '', position_seconds='%.1f' % (float(pos or 0) / 1000.0))
        except Exception:
            pass
        # v5.10.220: Kodi 22 often reports an episode that ran to its end as
        # stopped (always while the Home is in front of it). With Up Next no
        # longer told (the end of episode panel owns the end), nothing then
        # offered the next episode: the last heartbeat within 20 s of the end
        # is the end.
        next_ep = self._next_episode
        natural_end = bool(dur and pos and pos >= dur - 20000)
        if _session_owned_by(self.ctx):
            clear_session()
        _clear_tidb_next_episode(self.ctx)
        self.ctx = {}
        self.reporter = None
        self._fallback_chain = []
        self._next_episode = None
        self._active_playback_uid = ''
        self._fallback_pending = False
        if natural_end and next_ep:
            _monitor('stopped-at-end-next-episode', position_ms=pos, duration_ms=dur)
            self._maybe_play_next(next_ep)

    def onPlayBackEnded(self):
        if not self._foreign_active and self.ctx.get('server_type') == 'silo':
            from .providers.silo_playback import early_end
            position, duration = self._position_ms(), self._duration_ms()
            if early_end(position, duration):
                _monitor('silo-early-eof', position_ms=position, duration_ms=duration)
                # Keep the source and the measured resume point. Do not
                # manufacture a completion or auto-play the following episode.
                self._fallback_chain = []
                return self.onPlayBackStopped()
        if not self._foreign_active:
            _widgets_changed()
        self._stop_progress_heartbeat()
        self._resume_generation += 1
        self._active_playback_uid = ''
        self._fallback_pending = False
        self._resume_in_progress = False
        if self._foreign_active:
            # See onPlayBackStopped: foreign playback must end silently.
            self._foreign_active = False
            return
        try:
            from . import nowplaying
            nowplaying.clear()
        except Exception:
            pass
        self._back_keymap_async(False)
        _clear_tmdbh_handoff_flag()
        dur = self._duration_ms()
        _save_local_progress(self.ctx, dur or self._position_ms(), dur, finished=True)
        self._report('ended', position_ms=dur)
        next_ep = self._next_episode
        _ask_rating(self.ctx, next_ep is None)
        _clear_tidb_next_episode(self.ctx)
        if _session_owned_by(self.ctx):
            clear_session()
        self.ctx = {}
        self.reporter = None
        self._fallback_chain = []
        self._next_episode = None
        self._started_reported = False
        self._started_probe_pending = False
        # Auto-next episode prompt (if enabled and available).
        if next_ep:
            self._maybe_play_next(next_ep)

    def _maybe_notify_upnext(self):
        """Fire the standard service.upnext signal once, near the end.

        Only used when service.upnext is installed and the user enabled it.
        Otherwise DexHub falls back to its own end-of-episode prompt in
        onPlayBackEnded -> _maybe_play_next.
        """
        if self._upnext_sent:
            return
        next_ep = self._next_episode
        if not next_ep:
            return
        try:
            use_upnext = (ADDON.getSetting('use_service_upnext') or 'true').lower() == 'true'
        except Exception:
            use_upnext = True
        if not use_upnext:
            return
        # v5.10.218: Dex Hub's end of episode panel owns the end when it is on
        try:
            from . import credits_panel
            if credits_panel.owns_end():
                return
        except Exception:
            pass
        try:
            from . import upnext_service
        except Exception:
            return
        if not upnext_service.upnext_installed():
            return
        try:
            same_source = ((ADDON.getSetting('next_episode_same_source') or 'true').lower() == 'true')
        except Exception:
            same_source = True
        try:
            encoding = (ADDON.getSetting('upnext_data_encoding') or 'hex').strip().lower() or 'hex'
        except Exception:
            encoding = 'hex'
        sent = upnext_service.notify(next_ep, self.ctx, same_source=same_source, encoding=encoding)
        if sent:
            # service.upnext now owns the popup + countdown + autoplay, so
            # suppress DexHub's own end-of-episode prompt for this item.
            self._upnext_sent = True

    def _maybe_play_next(self, next_ep):
        # When service.upnext already handled this episode's Up Next popup,
        # do not also show DexHub's built-in prompt.
        if getattr(self, '_upnext_sent', False):
            return
        # v5.10.217: the end-of-episode panel already started the next one
        try:
            home = xbmcgui.Window(10000)
            done = float(home.getProperty('dexhub.credits.done') or 0)
            if done and time.time() - done < 180:
                home.clearProperty('dexhub.credits.done')
                return
        except Exception:
            pass
        try:
            auto = (ADDON.getSetting('auto_next_episode') or 'true').lower() == 'true'
        except Exception:
            auto = True
        if not auto:
            return
        try:
            countdown = max(3, int(ADDON.getSetting('auto_next_prompt_seconds') or '20'))
        except Exception:
            countdown = 20
        title = next_ep.get('title') or ''
        s = int(next_ep.get('season') or 0)
        e = int(next_ep.get('episode') or 0)
        dlg = xbmcgui.DialogProgress()
        try:
            dlg.create('Dex Hub', tr('الحلقة التالية: %s — S%02dE%02d') % (title, s, e))
            import time as _t
            start = _t.time()
            while not dlg.iscanceled() and (_t.time() - start) < countdown:
                remaining = int(countdown - (_t.time() - start))
                pct = int(max(0, min(100, ((_t.time() - start) / float(countdown)) * 100)))
                dlg.update(pct, tr('الحلقة التالية: %s — S%02dE%02d\nالبدء خلال: %ss') % (title, s, e, remaining))
                xbmc.sleep(250)
        finally:
            try:
                dlg.close()
            except Exception:
                pass
        if dlg.iscanceled():
            return
        try:
            same_source = ((ADDON.getSetting('next_episode_same_source') or
                            'true').lower() == 'true')
        except Exception:
            same_source = True
        same_source = bool(same_source and next_ep.get('source_provider_id'))
        # The normal route respects the user's picker/autoplay preference.
        # Binge continuation is different: when source affinity is available,
        # use the selected provider/server only and prefer its bingeGroup or
        # internal addon/indexer identity. Never fall back silently.
        params = {
            'action': ('play_next_same_source' if same_source else 'play_item'),
            'media_type': 'series',
            'canonical_id': next_ep.get('canonical_id') or '',
            'video_id': next_ep.get('video_id') or '',
            'season': str(s),
            'episode': str(e),
            'title': next_ep.get('title') or '',
        }
        if same_source:
            for source_key, route_key in (
                    ('source_provider_id', 'source_provider_id'),
                    ('server_id', 'source_server_id'),
                    ('provider_name', 'preferred_provider_name'),
                    ('binge_group', 'preferred_binge_group'),
                    ('source_addon', 'preferred_source_addon'),
                    ('source_indexer', 'preferred_source_indexer'),
                    ('source_service', 'preferred_source_service'),
                    ('source_type', 'preferred_source_type')):
                if next_ep.get(source_key):
                    params[route_key] = next_ep.get(source_key)
            try:
                xbmc.log('[DexHub] next episode locked to source=%s server=%s binge=%s' % (
                    next_ep.get('source_provider_id') or '-',
                    next_ep.get('server_id') or '-',
                    next_ep.get('binge_group') or '-'), xbmc.LOGINFO)
            except Exception:
                pass
        url = 'plugin://plugin.video.dexhub/?' + urllib.parse.urlencode(params)
        xbmc.executebuiltin('RunPlugin(%s)' % url)


class ProgressLoop(object):
    def __init__(self, player):
        self.player = player
        self._back_reopen_until = 0.0
        self._back_seen_for = 0.0


    def _maybe_stop_and_reopen_sources(self):
        # Keep playback alive when the user backs out of fullscreen. Kodi can
        # continue the video in the background / mini-player; the source picker
        # should only reopen after the user actually stops/closes playback.
        self._back_seen_for = 0.0
        return


    def run(self):
        """Report progress while a video plays; stay nearly asleep otherwise.

        v5.10.77: this woke every 5 seconds for as long as Kodi ran — 17,280
        times a day — reading two settings and polling the player even with the
        box idle on the home screen. On an ARM TV box that is a steady stream of
        wakeups that keeps the service interpreter busy for nothing.

        While nothing plays it now checks every IDLE_POLL seconds. That delay
        only postpones the first periodic tick: playback start itself is
        reported by the Player callbacks, not by this loop, so no progress
        point is lost.
        """
        monitor = xbmc.Monitor()
        while not monitor.abortRequested():
            try:
                from .service_lease import pulse
                pulse()
            except Exception:
                pass
            playing = False
            try:
                playing = bool(self.player.isPlayingVideo())
            except Exception:
                playing = False
            if playing:
                interval = _interval_seconds()
                try:
                    from .silo_lifecycle import maintain
                    maintain(self.player)
                except Exception as exc:
                    xbmc.log('[DexHub] Silo lifecycle: %s' % type(exc).__name__, xbmc.LOGWARNING)
                if str((getattr(self.player, 'ctx', None) or {}).get('server_type') or '') == 'silo':
                    interval = min(interval, 10)
                if self.player.reporter:
                    # Paused video is still isPlayingVideo(). Do not undo the
                    # explicit pause heartbeat with is_paused=false every tick.
                    self.player._report('paused_tick' if xbmc.getCondVisibility('Player.Paused') else 'progress')
            else:
                interval = _PROGRESS_IDLE_POLL
            # Unchanged from before: a plain field reset, kept on every pass.
            self._maybe_stop_and_reopen_sources()
            if monitor.waitForAbort(interval):
                break
