# -*- coding: utf-8 -*-
"""Post-start subtitle work, executed by the SERVICE — not the plugin.

v5.4.11. Until 5.4.10 the play dispatch spawned two threads that kept
running long after the plugin script returned:

  * ``DexHubSubtitleLifecycle`` — waited for AV, then ran the FULL broker
    discovery (15s budget) and downloaded every found subtitle file;
  * ``DexHubPlayPost``          — polled up to 25s for real media, then
    applied/selected the elected subtitle track.

Kodi's CPythonInvoker cannot finish while any thread of its interpreter is
alive (``CPythonInvoker(...): waiting on thread`` in the log — one line per
blocking thread, exactly what invokers 119 and 220 showed).  While it waits,
the invoker never reaches ``InvokerStateScriptDone``, so with
``reuselanguageinvoker=true`` the next dispatch cannot reuse it: Kodi
releases the stuck invoker asynchronously and builds a FRESH sub-interpreter
instead.  Every playback therefore left a zombie interpreter whose threads
kept calling Player APIs and downloading files — and stop → instant replay
put that churn exactly inside the Amlogic display-pipeline reset while the
new dispatch was being wired up.  That collision is the hard freeze.

The rule now: a plugin invocation exits with ZERO surviving threads.  The
dispatch only *publishes a job* (one Window-property write, done before
``Player.play``); the long-lived service companion consumes it on its own
``onPlayBackStarted`` and runs the exact same work on a service-interpreter
thread, where no invoker lifecycle exists to block.

The job travels over a Home-window property (same transport as the session
store): written by the plugin interpreter, read+cleared by the service.
"""
import json

import xbmc
import xbmcgui
import xbmcvfs

WINDOW_ID = 10000
PROP_JOB = 'dexhub.post_start_job_v1'

# Files that must never count as "our media has started": TMDb Helper opens
# a local dummy clip before every real play, and DexHub's own plugin:// nav
# URLs can flash through the player during handoff.
_FOREIGN_FILE_MARKERS = (
    'plugin.video.themoviedb.helper',
    'dummy.mp4',
)
_NAV_MARKERS = (
    'action=streams', 'action=episode_streams', 'action=item_open',
    'action=series_meta', 'action=play_item', 'action=cw_resume',
    'action=cw_play_from_start', 'action=play', 'action=tmdb_player',
    'action=kodi_route_browse',
)


def _window():
    return xbmcgui.Window(WINDOW_ID)


def publish_job(ctx, stream_key='play', subtitle_path='', subtitle_index=-1,
                force_subtitles=False, existing_paths=None, subtitle_rows=None,
                attach_rows=None, discovery=False):
    """Called by the play dispatch (plugin interpreter). Cheap and thread-free:
    serialises the post-start work and hands it to the service."""
    ctx = dict(ctx or {})
    uid = str(ctx.get('playback_uid') or '')
    existing_ids = []
    for row in (subtitle_rows or []):
        # v5.10.254: id and address both (a prepared row may carry the
        # file's address under a different id than the add-on gave it)
        # (a prepared row keeps the add-on's row under 'subtitle')
        for source in (row, (row.get('subtitle') if isinstance(row, dict) else None) or {}):
            for name in ('id', 'url', 'source_url', 'original_url'):
                try:
                    rid = str(source.get(name) or '')
                except Exception:
                    rid = ''
                if rid and rid not in existing_ids:
                    existing_ids.append(rid)
    apply_job = None
    try:
        idx = int(subtitle_index)
    except Exception:
        idx = -1
    if subtitle_path or idx >= 0 or force_subtitles:
        apply_job = {
            'path': str(subtitle_path or ''),
            'index': idx,
            'force': bool(force_subtitles),
        }
    job = {
        'v': 1,
        'uid': uid,
        'stream_key': str(stream_key or 'play'),
        'title': str(ctx.get('title') or ''),
        'provider': str(ctx.get('provider_name') or ctx.get('provider_id') or ''),
        'ctx': ctx,
        'apply': apply_job,
        'existing_paths': [str(p) for p in (existing_paths or []) if p],
        'existing_ids': existing_ids,
        'attach_rows': list(attach_rows or []),
        'discovery': bool(discovery),
    }
    _window().setProperty(PROP_JOB, json.dumps(job, ensure_ascii=False,
                                               default=str))
    return job


def clear_job():
    try:
        _window().clearProperty(PROP_JOB)
    except Exception:
        pass


def take_job(expected_uid):
    """Called by the service companion on playback start.  Returns the job
    for ``expected_uid`` and clears the property; a job left behind by an
    earlier playback (uid mismatch) is discarded so it can never run against
    the wrong stream."""
    raw = ''
    try:
        raw = _window().getProperty(PROP_JOB) or ''
    except Exception:
        raw = ''
    if not raw:
        return None
    clear_job()
    try:
        job = json.loads(raw)
    except Exception:
        return None
    if not isinstance(job, dict):
        return None
    uid = str(job.get('uid') or '')
    if not uid or uid != str(expected_uid or ''):
        return None
    if not (job.get('apply') or job.get('attach_rows') or job.get('discovery')):
        return None
    return job


def _player_has_real_media(player):
    """Mirror of the plugin-side check the old workers used: video is up AND
    the playing file is neither DexHub navigation nor the TMDbH dummy."""
    has_video = False
    try:
        has_video = bool(player.isPlayingVideo())
    except Exception:
        has_video = False
    try:
        has_video = has_video or bool(xbmc.getCondVisibility('Player.HasVideo'))
    except Exception:
        pass
    if not has_video:
        return False
    playing_file = ''
    try:
        playing_file = player.getPlayingFile() or ''
    except Exception:
        playing_file = ''
    low = str(playing_file or '').lower()
    if low:
        if all(marker in low for marker in _FOREIGN_FILE_MARKERS):
            return False
        if low.startswith('plugin://plugin.video.dexhub/') and any(
                marker in low for marker in _NAV_MARKERS):
            return False
    return True


def run_job(job, monitor=None):
    """Execute one published job on a service-interpreter thread.

    Behaviour is a verbatim port of the removed plugin workers:
      1. wait up to 25s (250ms steps) for real media, then let it stabilise;
      2. apply the elected subtitle (stream index first, sidecar fallback)
         and re-assert the preferred-language stream;
      3. attach the deferred sidecars (non-playlist-handoff path);
      4. run background broker discovery and attach every new track.
    Every stage is bounded and abort-aware; the worker dies with playback.
    """
    job = dict(job or {})
    stream_key = str(job.get('stream_key') or 'play')
    title = str(job.get('title') or '')
    provider = str(job.get('provider') or '')

    def _emit(event, level=None, **fields):
        if monitor is None:
            return
        try:
            if level is not None:
                monitor(event, level=level, **fields)
            else:
                monitor(event, **fields)
        except Exception:
            pass

    kodi_monitor = xbmc.Monitor()
    player = xbmc.Player()
    apply_job = job.get('apply') or None
    started = False
    try:
        # Stage 1 — wait for real media (the old DexHubPlayPost budget).
        for _ in range(100):
            if kodi_monitor.abortRequested():
                return
            xbmc.sleep(250)
            try:
                if _player_has_real_media(player):
                    started = True
                    break
            except Exception:
                continue
        if not started:
            if apply_job:
                _emit('play-start-timeout', level=xbmc.LOGWARNING,
                      stream_key=stream_key, title=title, provider=provider)
            return
        if apply_job:
            _emit('play-started', stream_key=stream_key, title=title,
                  provider=provider)
            # Plex/CDN streams need 1-3s past the first keyframe before a
            # seek/subtitle call sticks.
            xbmc.sleep(800)

        # Stage 2 — elected subtitle.
        if apply_job:
            _apply_selected_subtitle(player, apply_job, kodi_monitor)
            try:
                from .subtitle_files import _force_preferred_subtitle_on_player
                _force_preferred_subtitle_on_player(
                    player, show=bool(apply_job.get('force')),
                    attempts=4, delay_ms=400)
            except Exception:
                pass

        # Stage 3 — deferred sidecars (legacy non-playlist handoff only).
        # v5.10.254: the deferred sidecars and the discovery share one
        # attacher: your subtitle languages only, and never a switch to a
        # worse language (the remaining rows were all attached in a row
        # before, so the last one, often Chinese, ended up showing)
        attach, counts = _attacher(player, job, stream_key)
        attach_rows = job.get('attach_rows') or []
        if attach_rows and _player_has_real_media(player):
            attach(attach_rows)

        # Stage 4 — background broker discovery.
        if job.get('discovery') and not kodi_monitor.abortRequested() \
                and _player_has_real_media(player):
            _discover_and_attach(player, job, stream_key, attach=attach, counts=counts)
    except Exception as exc:
        _emit('play-post-start-error', level=xbmc.LOGWARNING, error=exc,
              stream_key=stream_key, title=title)
        try:
            xbmc.log('[DexHub] post-start chores failed: %s' % exc,
                     xbmc.LOGDEBUG)
        except Exception:
            pass


def _apply_selected_subtitle(player, apply_job, kodi_monitor):
    path = str(apply_job.get('path') or '')
    try:
        index = int(apply_job.get('index'))
    except Exception:
        index = -1
    if path:
        low = path.lower()
        if not low.startswith(('http://', 'https://', 'smb://', 'nfs://')):
            # DPlex-style deferred sidecars may still be copying when AV
            # starts — wait for the file instead of silently losing it.
            for _ in range(40):
                try:
                    if xbmcvfs.exists(path):
                        break
                except Exception:
                    break
                if kodi_monitor.abortRequested():
                    return
                xbmc.sleep(250)
            else:
                return
    try:
        player.showSubtitles(False)
    except Exception:
        pass
    applied = False
    if index >= 0:
        try:
            player.setSubtitleStream(index)
            player.showSubtitles(True)
            applied = True
        except Exception:
            applied = False
    if not applied and path:
        try:
            player.setSubtitles(path)
            player.showSubtitles(True)
        except Exception:
            pass


def _attach_remaining_rows(player, rows, existing_paths, stream_key):
    try:
        from ..subtitle_policy import automatic_rows
        from .subtitle_files import _prepare_subtitle_files
        safe_rows = automatic_rows(list(rows))
        if not safe_rows:
            return
        prepared = _prepare_subtitle_files(
            safe_rows, stream_key, prefer_local_copy=True)
        extras = [r.get('path') for r in prepared if r.get('path')]
        if not extras:
            return
        # setSubtitles replaces the list wholesale on some Kodi builds —
        # replay the already-attached tracks first so none are lost.
        combined = list(existing_paths) + [p for p in extras
                                           if p not in existing_paths]
        try:
            player.setSubtitles(combined[0])
            for extra in combined[1:]:
                try:
                    player.setSubtitles(extra)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass


def _subtitle_state():
    """(player id, index, enabled, language) of the player's subtitle now,
    by JSON-RPC (the Python player has no getter for them)."""
    try:
        players = json.loads(xbmc.executeJSONRPC(json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetActivePlayers'})) or '{}')
        pid = next((p.get('playerid') for p in players.get('result') or []
                    if p.get('type') == 'video'), None)
        if pid is None:
            return None
        props = json.loads(xbmc.executeJSONRPC(json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetProperties',
             'params': {'playerid': pid, 'properties': ['currentsubtitle', 'subtitleenabled']}})) or '{}')
        result = props.get('result') or {}
        current = result.get('currentsubtitle') or {}
        return (pid, current.get('index') if current else None, bool(result.get('subtitleenabled')),
                str(current.get('language') or '') if current else '')
    except Exception:
        return None


def _restore_subtitle_state(state):
    """Back to the subtitle the viewer had (setSubtitles selects and shows
    the file it adds)."""
    if not state:
        return
    pid, index, enabled = state[0], state[1], state[2]
    try:
        if index is not None and int(index) >= 0:
            xbmc.executeJSONRPC(json.dumps(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.SetSubtitle',
                 'params': {'playerid': pid, 'subtitle': int(index), 'enable': bool(enabled)}}))
            if not enabled:
                xbmc.executeJSONRPC(json.dumps(
                    {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.SetSubtitle',
                     'params': {'playerid': pid, 'subtitle': 'off'}}))
        else:
            xbmc.executeJSONRPC(json.dumps(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.SetSubtitle',
                 'params': {'playerid': pid, 'subtitle': 'off'}}))
    except Exception:
        pass


def _track_count(pid):
    try:
        props = json.loads(xbmc.executeJSONRPC(json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetProperties',
             'params': {'playerid': pid, 'properties': ['subtitles']}})) or '{}')
        return len((props.get('result') or {}).get('subtitles') or [])
    except Exception:
        return -1


def _wait_for_tracks(state, added, seconds=3.0):
    """Until the player lists the subtitles just added (or a few seconds)."""
    if not state or len(state) < 5 or state[4] < 0:
        xbmc.sleep(700)
        return
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _track_count(state[0]) >= state[4] + added:
            xbmc.sleep(250)
            return
        xbmc.sleep(150)


# the most subtitles one play adds to the player's list
_LIST_CAP = 12


def _attacher(player, job, stream_key):
    """(attach(rows), counts): adds rows to the player's subtitle list.

    v5.10.254: the same subtitles as Dex Hub's subtitle add-on in Kodi's
    subtitle dialog: only your subtitle languages (Arabic, then English by
    default), in its order, at most a dozen; a Chinese or other track no
    longer comes in. A newly added subtitle is switched on only when its
    language ranks above the one showing (Arabic replaces English, never
    the reverse) and only with Play with subtitles; otherwise the subtitle
    that was showing (or none) stays."""
    import threading
    from ..subtitle_policy import automatic_rows
    from ..subtitle_broker import _normalize_lang, _preferred_languages
    from .subtitle_files import _prepare_subtitle_files
    try:
        from ..subtitle_service import _sort_key
    except Exception:
        _sort_key = None
    apply_job = job.get('apply') or {}
    show = bool(apply_job.get('force'))
    prefs = [str(c).split('-')[0] for c in (_preferred_languages() or ['ar', 'en'])]

    def _rank(lang):
        code = str(_normalize_lang(lang) or '').split('-')[0]
        return prefs.index(code) if code in prefs else 99

    existing_ids = set(str(i) for i in (job.get('existing_ids') or []))
    existing_paths = set(str(p) for p in (job.get('existing_paths') or []))
    lock = threading.Lock()
    counts = {'added': 0}

    def attach(rows):
        with lock:
            fresh = []
            for row in (rows or []):
                if _rank(row.get('lang') or row.get('language')) >= 99:
                    continue        # not one of your subtitle languages
                keys = [str(row.get(n) or '') for n in ('id', 'url', 'path')]
                keys = [k for k in keys if k]
                if any(k in existing_ids for k in keys):
                    continue
                fresh.append(row)
                existing_ids.update(keys)
            # AI rows stay manual-only: automatic preparation would trigger
            # generation and consume the user's DexWorld token.
            fresh = automatic_rows(fresh)
            room = _LIST_CAP - counts['added']
            if not fresh or room <= 0 or not _player_has_real_media(player):
                return
            if _sort_key is not None:
                fresh = sorted(fresh, key=_sort_key)
            fresh = sorted(fresh, key=lambda r: _rank(r.get('lang') or r.get('language')))[:room]
            prepared = _prepare_subtitle_files(fresh, stream_key, prefer_local_copy=True)
            # a prepared row is {path, subtitle: the add-on's row, lang_key}
            for row in prepared:
                sub = row.get('subtitle') or {}
                row['_rank'] = _rank(sub.get('lang') or sub.get('language') or row.get('lang_key'))
            prepared = [r for r in prepared if r.get('path') and r.get('path') not in existing_paths]
            if not prepared:
                return
            state = _subtitle_state()
            if state:
                state = state + (_track_count(state[0]),)
            showing_rank = _rank(state[3]) if (state and state[2]) else 99
            best = min(r.get('_rank', 99) for r in prepared)
            switch_on = show and best < showing_rank
            if switch_on:
                # setSubtitles selects the file it adds: the best one last
                prepared = sorted(prepared, key=lambda r: -r.get('_rank', 99))
            added = 0
            for row in prepared:
                if not _player_has_real_media(player):
                    break
                try:
                    player.setSubtitles(row['path'])
                    existing_paths.add(row['path'])
                    added += 1
                except Exception:
                    pass
            if not added:
                return
            # Kodi adds a subtitle file a moment after setSubtitles returns,
            # and selects it then: restoring before that was undone by it
            _wait_for_tracks(state, added)
            if switch_on:
                try:
                    player.showSubtitles(True)
                except Exception:
                    pass
            else:
                _restore_subtitle_state(state)
                # Kodi may still switch to the new file after that: check
                # for a few seconds and put the viewer's choice back
                for _ in range(12):
                    xbmc.sleep(250)
                    now = _subtitle_state()
                    if now and state and (now[1] != state[1] or now[2] != state[2]):
                        _restore_subtitle_state(state)
            counts['added'] += added
            xbmc.log('[DexHub] subtitles: %d more in the player list%s'
                     % (added, ' (switched to the better language)' if switch_on else ''), xbmc.LOGINFO)

    return attach, counts


def _discover_and_attach(player, job, stream_key, attach=None, counts=None):
    """Every subtitle Dex Hub finds goes into Kodi's subtitle list while the
    video plays, each add-on's answer as soon as it comes (v5.10.253, like
    Nuvio), through the attacher's language filter (v5.10.254)."""
    try:
        from .subtitle_files import _collect_playback_subtitles
        if attach is None:
            attach, counts = _attacher(player, job, stream_key)
        ctx = dict(job.get('ctx') or {})
        ctx['subtitle_on_rows'] = attach
        try:
            discovered = _collect_playback_subtitles(
                ctx, manual=False, force_search=True, include_broker=True)
        except Exception as exc:
            xbmc.log('[DexHub] background subtitle discovery failed: %s' % exc,
                     xbmc.LOGWARNING)
            return
        attach(discovered)      # the rest: the cache, the stream's own, the servers'
        xbmc.log('[DexHub] background subtitle discovery attached %d track(s)'
                 % (counts or {}).get('added', 0), xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] background subtitle attach failed: %s' % exc,
                 xbmc.LOGWARNING)
