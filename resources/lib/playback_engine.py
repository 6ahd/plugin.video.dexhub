"""Shared selected-source playback. No imports or side effects until called.

The caller supplies Kodi and compatibility adapters; Home/catalog code does not
belong here. Relative feature imports remain lazy and retain package semantics.
"""

_PLAY_PROP = 'dexhub.play.handoff'
_PLAY_GRACE = 25.0          # the same title asked again while it opens or plays
_PLAY_OPENING = 8.0         # nothing plays yet: still opening for this long


def _attach_before_mode(api):
    try:
        from .playback.subtitle_files import _subtitle_attach_before
        return bool(_subtitle_attach_before())
    except Exception:
        return False


def _configure_content_lookup(item, stream_url, switching=False):
    """Known HTTP media files need no MIME HEAD request on the GUI thread.

    On a source switch, an opaque endpoint is left for the input stream's
    content probe, without guessing a MIME type or issuing a GUI MIME HEAD.
    Initial playback of an unknown endpoint keeps Kodi's usual detection.
    """
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(str(stream_url).split('|', 1)[0])
        if parsed.scheme.lower() not in ('http', 'https'):
            return False
        extension = parsed.path.lower().rsplit('.', 1)[-1]
        mime = {'mkv': 'video/x-matroska', 'mp4': 'video/mp4',
                'm4v': 'video/mp4', 'webm': 'video/webm',
                'avi': 'video/x-msvideo', 'ts': 'video/mp2t',
                'm2ts': 'video/mp2t', 'm3u8': 'application/vnd.apple.mpegurl',
                'mpd': 'application/dash+xml'}.get(extension)
        if not mime and not switching:
            return False
        if mime:
            item.setMimeType(mime)
        item.setContentLookup(False)
        return True
    except Exception:
        return False


def _same_play_starting(api, ctx, stream_key):
    """v5.10.185: the same title (same source, same start point) asked to play
    again while its first request opens or has just started is not played a
    second time. A Mac log had Play pressed again 4 s after the first press
    (Kodi was still closing the trailer), and the second request restarted
    the film: 18 s to the picture instead of 9. A first try that failed (no
    video after the opening time) can be retried at once."""
    try:
        key = '%s|%s|%s' % (stream_key or ctx.get('stream_url') or '', ctx.get('resume_seconds') or '',
                            ctx.get('resume_percent') or '')
        if not key.strip('|'):
            return False
        home = api.xbmcgui.Window(10000)
        now = api.time.time()
        raw = home.getProperty(_PLAY_PROP) or ''
        last_key, _sep, stamp = raw.rpartition('#')
        try:
            age = now - float(stamp)
        except ValueError:
            age = 1e9
        if last_key == key and age < _PLAY_GRACE:
            playing = False
            try:
                playing = bool(api.xbmc.Player().isPlayingVideo())
            except Exception:
                pass
            if age < _PLAY_OPENING or playing:
                api.xbmc.log('[DexHub] play: the same title is already starting (%.1f s ago); not played twice' % age,
                             api.xbmc.LOGINFO)
                return True
        home.setProperty(_PLAY_PROP, '%s#%.3f' % (key, now))
    except Exception:
        pass
    return False


def play_with_context(api, ctx, stream_key='', fallback_keys=None, entries_meta=None, use_resolved_url=None):
    """Play a stream via xbmc.Player().play() — matches the original behavior
    that reliably starts playback without leaving an empty folder behind.

    ``fallback_keys`` remains in the signature for old plugin URLs only.  It is
    intentionally ignored: playback never switches sources silently.
    """
    # Safety net for direct/native routes that bypassed the normal source scan.
    # The standard movie/episode paths start this job before stream discovery;
    # this call reuses that same keyed job and never starts a duplicate.
    ctx = dict(ctx or {})
    fallback_keys = []
    if _same_play_starting(api, ctx, stream_key):
        return
    # v5.10.94: direct library routes (plex_play, emby_play, silo_play, the
    # TMDb Helper player) build their context without the source window, so
    # play_with_subtitles was never set and the subtitle mode setting was
    # silently ignored for every library item. An explicit window choice
    # still wins; only an absent key falls back to the setting.
    if 'play_with_subtitles' not in ctx:
        try:
            ctx['play_with_subtitles'] = (
                api._subtitle_pick_mode_setting() == 'play_with_subtitles')
        except Exception:
            ctx['play_with_subtitles'] = False
    try:
        from .work_ids import work_ids as _work_ids
    except Exception:
        _work_ids = lambda c: {'imdb_id': c.get('imdb_id') or '', 'tmdb_id': c.get('tmdb_id') or '', 'tvdb_id': c.get('tvdb_id') or ''}
    _wids = _work_ids(ctx)
    try:
        if (bool(ctx.get('play_with_subtitles')) and
                not ctx.get('source_switch_reuse_subtitle') and
                not ctx.get('selected_subtitle') and
                not (ctx.get('subtitles') or [])):
            api.start_subtitle_prefetch(
                ctx.get('media_type') or 'movie',
                ctx.get('canonical_id') or ctx.get('video_id') or '',
                ids=dict(_wids),
                title=ctx.get('title') or ctx.get('show_title') or '',
                season=ctx.get('season'), episode=ctx.get('episode'),
            )
    except Exception as exc:
        api.xbmc.log('[DexHub] fast subtitle prefetch start failed: %s' % exc,
                 api.xbmc.LOGDEBUG)

    # Every actual handoff gets a fresh identity. Kodi can emit duplicate
    # onPlayBackStarted callbacks for the same adaptive stream, and late Stop
    # callbacks from a replaced source. A unique id lets the service suppress
    # duplicate resume/heartbeat work and prevents an old callback from owning
    # or clearing the replacement session.
    ctx = api._refresh_native_playback_context(ctx)
    try:
        import uuid as _uuid
        ctx = dict(ctx or {})
        ctx['playback_uid'] = _uuid.uuid4().hex
        ctx['playback_created_at'] = int(api.time.time() * 1000)
    except Exception:
        ctx = dict(ctx or {})
        ctx['playback_uid'] = '%d-%s' % (int(api.time.time() * 1000), str(stream_key or 'stream'))
    ctx = api._ensure_playback_ids(ctx)
    ctx = api._ensure_playback_art_context(ctx)
    # Publish one lightweight action for TIDB's optional Next Episode button.
    # Dex Hub remains the playback/source owner; an empty hint clears stale UI.
    try:
        from . import next_episode_bridge as _next_episode_bridge
        _next_hint = api._compute_next_episode_hint(ctx)
        if _next_hint:
            ctx['next_episode'] = _next_hint
        _next_episode_bridge.publish(
            _next_hint,
            same_source=api._get_bool_setting('next_episode_same_source', True),
            playback_uid=ctx.get('playback_uid') or '')
    except Exception as _next_bridge_exc:
        try:
            api.xbmc.log('[DexHub][TIDB] next-episode bridge skipped: %s' %
                     _next_bridge_exc, api.xbmc.LOGWARNING)
        except Exception:
            pass
    # Kodi 22 safe mode keeps the normal (large) VideoInfoTag out of the
    # fragile ListItem handoff. TheIntroDB is different: it decides whether
    # the item is a TV episode in onAVStarted, so a post-start-only tag can be
    # one event too late. Its bridge writes a deliberately tiny identity tag
    # (media type, show ids, season and episode) before AV start, then the
    # companion repeats it after start as a belt-and-suspenders handoff.
    try:
        from . import introdb_bridge as _introdb_bridge
        if _introdb_bridge.is_available():
            if api._get_bool_setting('introdb_manual_buttons', True):
                _introdb_bridge.ensure_manual_skip_buttons()
            if api._kodi22_minimal_item_enabled():
                ctx['introdb_post_start'] = True
    except Exception:
        pass
    api.xbmcgui.Window(api.WINDOW_ID).setProperty(api.PROP, api.json.dumps(ctx))
    api._monitor_playback('play-request', stream_key=stream_key, title=ctx.get('title') or '', media_type=ctx.get('media_type') or '', canonical_id=ctx.get('canonical_id') or '', provider=ctx.get('provider_name') or ctx.get('provider_id') or '', video_id=ctx.get('video_id') or '', fallback_count=0)
    comp_ctx = api._companion_context_from_playback(ctx)
    if comp_ctx:
        # Companion sync active: enrich the companion context with the live
        # playback fields, then persist.  stream_url is included in the copied
        # key set here (companion may not have carried it).
        api._augment_session_ctx(comp_ctx, ctx, fallback_keys, include_stream_url=True)
        if stream_key:
            comp_ctx['stream_key'] = stream_key
        api.save_session(comp_ctx)
    else:
        # Still save the next-episode hint so service sync works even without
        # a companion provider. No automatic source fallback is persisted.
        # stream_url is seeded explicitly first, so it is excluded from the copied
        # key set to preserve the original ('' -> value) precedence behaviour.
        minimal = {'stream_url': ctx.get('stream_url') or ''}
        api._augment_session_ctx(minimal, ctx, fallback_keys, include_stream_url=False)
        if stream_key:
            minimal['stream_key'] = stream_key
        api.save_session(minimal)
    api._invalidate_nextup_cache()
    api._publish_playback_art_properties(ctx)
    # IMPORTANT: do NOT clear dexhub.invoked_by_tmdbh here.
    # Clearing it before the player actually starts allows internal click/
    # playback paths to think the TMDb Helper handoff is already over, which
    # can bounce back into TMDb Helper one more time just before playback.
    # The flag is now cleared on real playback lifecycle events instead.

    stream_url = api._append_a4k_ids_to_url(ctx.get('stream_url') or '', ctx)
    if stream_url != (ctx.get('stream_url') or ''):
        try:
            ctx = dict(ctx or {})
            ctx['stream_url'] = stream_url
        except Exception:
            pass
    if not stream_url:
        api._clear_tmdbh_transient()
        api._clear_source_transient_props(clear_global=True)
        api._monitor_playback('play-missing-url', level=api.xbmc.LOGERROR, stream_key=stream_key, title=ctx.get('title') or '', canonical_id=ctx.get('canonical_id') or '')
        # Signal Kodi that resolve failed — without this, the busy spinner
        # hangs indefinitely when a HANDLE-based invocation has no URL.
        if api.HANDLE >= 0:
            try:
                api.xbmcplugin.setResolvedUrl(api.HANDLE, False, api.xbmcgui.ListItem())
            except Exception:
                pass
        api.error(api.tr('لا يوجد رابط تشغيل'))
        return

    try:
        item = api.xbmcgui.ListItem(path=stream_url, offscreen=True)
    except TypeError:
        item = api.xbmcgui.ListItem(path=stream_url)
    _switch_lookup = False
    try:
        _switch_stamp = float(api.xbmcgui.Window(10000).getProperty('dexhub.switch.inplace') or 0)
        _switch_lookup = 0 <= api.time.time() - _switch_stamp < 15
    except Exception:
        pass
    if _configure_content_lookup(item, stream_url, switching=_switch_lookup):
        api.xbmc.log('[DexHub] playback: MIME lookup skipped', api.xbmc.LOGINFO)
    _is_ep = bool(ctx.get('season') or ctx.get('episode'))
    _sub_ctx, _sub_ep = ctx, _is_ep
    if not _is_ep:
        # v5.10.242: an episode handed over without its numbers (a server or
        # IPTV row) whose title or stream says S01E05 is still an episode for
        # the subtitle services; the playback itself is left as it is
        try:
            from .work_ids import as_episode_ctx as _as_ep
            _found = _as_ep(ctx, stream_url.split('|', 1)[0].split('?', 1)[0].rsplit('/', 1)[-1])
            if _found.get('episode'):
                _sub_ctx, _sub_ep = _found, True
        except Exception:
            pass
    info = api._playback_info_from_ctx(ctx)
    _playback_cast = info.pop('cast', None) if isinstance(info.get('cast'), list) else None
    # v3.9.71: route through the modern VideoInfoTag API on Kodi 20+
    # (catalog rendering already does this — see line 1731 — but the
    # playback handoff was still calling deprecated setInfo('video', dict)
    # unconditionally). On Kodi 22 (Piers) alpha, the deprecated API
    # path appears to native-crash instead of raising a catchable
    # Python exception, which matches the user's report of a hard
    # Kodi restart immediately before playback. Using the InfoTag
    # API on Kodi 20+ matches the catalog code path and removes the
    # crash vector. The bare setInfo call survives only as a fallback
    # for Kodi 19 (Matrix) where the InfoTag API isn't available.
    # v5.10.94: the playback item is tagged with the WORK ids (series ids
    # for episodes). Kodi's tvshow.* unique ids, TMDb Helper, subtitle
    # addons and service.subtitles.dexworld all read these; Silo/Plex/Emby
    # episode-level ids used to land here and made every lookup miss.
    _ids_for_tag = dict(_wids)
    _used_videoinfotag = False
    _minimal_item = api._kodi22_minimal_item_enabled()
    if _minimal_item:
        # Breadcrumb: if the box still dies on play, the LAST line in kodi.log
        # names the exact native call that did it.
        api.xbmc.log('[DexHub] playback item: minimal (Kodi 22 safe) — InfoTag skipped', api.xbmc.LOGINFO)
        try:
            item.setLabel(str(info.get('title') or ctx.get('title') or ''))
        except Exception:
            pass
        _used_videoinfotag = True      # nothing else may touch the tag
    if api._KODI_MAJOR >= 20 and not _minimal_item:
        try:
            api.xbmc.log('[DexHub] playback InfoTag: applying full tag', api.xbmc.LOGINFO)
            _used_videoinfotag = api._videoinfotag_apply(item, info, ids=_ids_for_tag, cast=_playback_cast)
            api.xbmc.log('[DexHub] playback InfoTag: full tag applied', api.xbmc.LOGINFO)
        except Exception as _vit_exc:
            try:
                api.xbmc.log('[DexHub] playback InfoTag apply failed, falling back: %s' % _vit_exc, api.xbmc.LOGWARNING)
            except Exception:
                pass
            _used_videoinfotag = False
    if not _used_videoinfotag and not _minimal_item:
        try:
            item.setInfo('video', info)
        except Exception:
            pass
    # _apply_unique_ids also writes setProperty('imdb_id') / setProperty('tmdb_id')
    # which several skin info bindings still read. Kept for both Kodi
    # versions because property writes are safe.
    api._apply_unique_ids(item, _ids_for_tag, info)
    try:
        # Extra compatibility for subtitle addons that still read legacy
        # ListItem properties instead of the modern InfoTag object.
        if _wids.get('imdb_id'):
            item.setProperty('imdb', str(_wids.get('imdb_id')))
            item.setProperty('imdbnumber', str(_wids.get('imdb_id')))
            item.setProperty('VideoPlayer.IMDBNumber', str(_wids.get('imdb_id')))
            item.setProperty('tvshow.imdb_id', str(_wids.get('imdb_id')))
        if _wids.get('tmdb_id'):
            item.setProperty('tmdb', str(_wids.get('tmdb_id')))
            item.setProperty('tmdb_id', str(_wids.get('tmdb_id')))
            item.setProperty('tvshow.tmdb_id', str(_wids.get('tmdb_id')))
        if _wids.get('tvdb_id'):
            item.setProperty('tvdb', str(_wids.get('tvdb_id')))
            item.setProperty('tvdb_id', str(_wids.get('tvdb_id')))
            item.setProperty('tvshow.tvdb_id', str(_wids.get('tvdb_id')))
        item.setProperty('tmdb_type', api._tmdb_type_from_ctx(ctx))
        if ctx.get('season') not in (None, ''):
            item.setProperty('season', str(ctx.get('season')))
        if ctx.get('episode') not in (None, ''):
            item.setProperty('episode', str(ctx.get('episode')))
    except Exception:
        pass
    api._publish_subtitle_bridge_properties(_sub_ctx, _sub_ep)
    item.setProperty('IsPlayable', 'true')
    art = api._playback_art_from_ctx(ctx)
    try:
        from . import image_policy as _image_policy
        art = _image_policy.bound_art(art)
    except Exception:
        pass
    # v5.10.83, as DPlex: Arctic Fuse 3 reads an episode's show logo from
    # tvshow.clearlogo. It was only written into the InfoTag, which the Kodi 22
    # safe path skipped, so the player overlay had no logo for episodes.
    if _is_ep and isinstance(art, dict) and art.get('clearlogo') and not art.get('tvshow.clearlogo'):
        art = dict(art)
        art['tvshow.clearlogo'] = art['clearlogo']
    try:
        item.setArt(art)
    except Exception:
        pass
    # v3.9.73: explicitly write art onto the VideoInfoTag on Kodi 20+.
    # The v3.9.72 fix switched the playback handoff to the InfoTag API to
    # stop a native Kodi 22 crash, but ListItem.setArt() does NOT
    # automatically populate the InfoTag's artwork dict on Kodi 22 — the
    # player overlay reads clearlogo via InfoTag.getArt('clearlogo'), and
    # without this explicit call the overlay shows no logo. Same fix
    # pattern as catalog rendering: write art via both ListItem and
    # InfoTag so every consumer finds it where it expects.
    if api._KODI_MAJOR >= 20:
        try:
            # Kodi 22/CoreELEC safe mode: do not write artwork into the
            # VideoInfoTag during the playback handoff. On Piers alpha this
            # native call can restart Kodi before the first frame. ListItem art
            # and lightweight Window properties remain available to skins, and
            # IDs are still applied below for subtitles/TMDb Helper.
            if api._kodi22_playlist_handoff_enabled():
                _tag_art = {}
            else:
                _art_tag = item.getVideoInfoTag(offscreen=True)
                if api._safe_playback_handoff_enabled():
                    _tag_art = {}
                    for _k in ('clearlogo', 'logo'):
                        if art.get(_k):
                            _tag_art[_k] = art.get(_k)
                    if _is_ep and art.get('clearlogo'):
                        _tag_art['tvshow.clearlogo'] = art.get('clearlogo')
                else:
                    _tag_art = {k: v for k, v in art.items() if v}
                if _tag_art and hasattr(_art_tag, 'setArtwork'):
                    _art_tag.setArtwork(_tag_art)
        except Exception as _art_exc:
            try:
                api.xbmc.log('[DexHub] playback InfoTag setArtwork failed: %s' % _art_exc, api.xbmc.LOGWARNING)
            except Exception:
                pass
    # v3.9.73: re-assert unique IDs directly on the InfoTag immediately
    # before playback. Some subtitle services (a.b.subtitles.opensubtitlescom
    # and friends) report "TMDb not known" because they read
    # VideoPlayer.UniqueID(tmdb) and we hadn't guaranteed that key was set
    # AFTER setArt — InfoTag commits can be order-sensitive on alpha builds.
    # Belt-and-suspenders re-application is cheap and removes the regression.
    if api._KODI_MAJOR >= 20 and not _minimal_item and (ctx.get('imdb_id') or ctx.get('tmdb_id') or ctx.get('tvdb_id')):
        try:
            api.xbmc.log('[DexHub] playback InfoTag: re-asserting unique IDs', api.xbmc.LOGINFO)
            _uid_tag = item.getVideoInfoTag(offscreen=True)
            _final_ids = {}
            if ctx.get('imdb_id'):
                _final_ids['imdb'] = str(ctx.get('imdb_id'))
            if ctx.get('tmdb_id'):
                _final_ids['tmdb'] = str(ctx.get('tmdb_id'))
            if ctx.get('tvdb_id'):
                _final_ids['tvdb'] = str(ctx.get('tvdb_id'))
            if _is_ep:
                if 'imdb' in _final_ids: _final_ids['tvshow.imdb'] = _final_ids['imdb']
                if 'tmdb' in _final_ids: _final_ids['tvshow.tmdb'] = _final_ids['tmdb']
                if 'tvdb' in _final_ids: _final_ids['tvshow.tvdb'] = _final_ids['tvdb']
            _default = 'imdb' if 'imdb' in _final_ids else ('tmdb' if 'tmdb' in _final_ids else 'tvdb')
            _uid_tag.setUniqueIDs(_final_ids, _default)
            if ctx.get('imdb_id'):
                try:
                    _uid_tag.setIMDBNumber(str(ctx.get('imdb_id')))
                except Exception:
                    pass
            try:
                api.xbmc.log('[DexHub] playback IDs → InfoTag: %s (default=%s), clearlogo=%s' % (
                    ','.join('%s=%s' % (k, v[:8]) for k, v in _final_ids.items()),
                    _default,
                    'yes' if (ctx.get('clearlogo') or '') else 'no',
                ), api.xbmc.LOGINFO)
            except Exception:
                pass
        except Exception as _uid_exc:
            try:
                api.xbmc.log('[DexHub] playback InfoTag setUniqueIDs re-assert failed: %s' % _uid_exc, api.xbmc.LOGWARNING)
            except Exception:
                pass
    # TheIntroDB is a standalone service and owns fetching/skip UI. Dex Hub's
    # only job is to expose an unambiguous final player identity. The bridge
    # auto-detects plugin.video.tidb and is otherwise a zero-cost no-op.
    try:
        from . import introdb_bridge as _introdb_bridge
        _tidb_applied = _introdb_bridge.apply_playback_identity(
            item, ctx, info=info, apply_video_tag=True)
        if _tidb_applied and _minimal_item:
            api.xbmc.log('[DexHub][TheIntroDB] minimal pre-AV identity applied',
                     api.xbmc.LOGINFO)
    except Exception as _tidb_exc:
        try:
            api.xbmc.log('[DexHub][TheIntroDB] optional bridge skipped: %s' % _tidb_exc,
                     api.xbmc.LOGWARNING)
        except Exception:
            pass
    try:
        item.setProperties(api._playback_skin_properties(ctx, art, info))
    except Exception:
        pass
    reusing_switched_subtitle = bool(ctx.get('source_switch_reuse_subtitle') and ctx.get('switch_subtitle_path'))
    force_subtitles = bool(ctx.get('play_with_subtitles')) and not reusing_switched_subtitle
    # Native Plex/Emby sidecars are already matched to the selected file. Treat
    # them like DPlex: materialize the preferred external track immediately so
    # it survives Kodi 22 playlist handoff, while the remaining tracks attach
    # after AV start. This avoids the old state where all native subtitles were
    # deferred and therefore never reached Kodi on the safe playlist path.
    # Native Plex/Emby sidecars are prepared immediately, but their presence
    # must not turn normal Play into the blocking cross-addon subtitle path.
    # Only an explicit Play with subtitles request may block for the broker.
    # Do not launch/prepare a second subtitle set while replacing a source.
    # The exact external track from the old source is restored by the
    # singleton CompanionPlayer after Kodi's subtitle dialog is gone.
    # Two intentionally separate modes:
    #   Play                  -> source/native subtitles now; broker after AVStarted.
    #   Play with subtitles   -> wait for the full broker result before playback.
    # This keeps first-frame fast without removing the explicit wait-and-play
    # behavior the user selected from the source window.
    # v5.10.30: "Play with subtitles" no longer waits the whole broker
    # budget (10-20s) before the first frame. It takes whatever the
    # source-scan prefetch already has (waiting at most 1.5s for a job that
    # is about to finish), starts playback, and the service's post-start
    # discovery attaches and enables the best preferred-language track.
    subtitle_rows = [] if reusing_switched_subtitle else api._collect_playback_subtitles(
        ctx, force_search=force_subtitles, include_broker=force_subtitles,
        # v5.10.254: Attach before playing waits for the whole search (the
        # first mode); Play with subtitles starts at once and the rest come
        # into the player list while it plays
        broker_wait=((None if _attach_before_mode(api) else 1.5) if force_subtitles else None))
    # v5.10.254: Play only (play without subtitles) searches nothing: the
    # subtitles that came with the source only
    _background_subtitle_discovery = bool(
        not reusing_switched_subtitle and
        (force_subtitles or api._subtitle_pick_mode_setting() == 'play_with_subtitles'))

    # POV-style fast path: only the SELECTED/forced subtitle is prepared
    # synchronously. The rest are downloaded in a background thread AFTER
    # playback has already started, so they don't delay first-frame.
    # User sees their chosen subtitle immediately, the others appear in the
    # subtitle picker within a few seconds while the movie is already playing.
    selected_sub = ctx.get('selected_subtitle') or ''
    # Round 4a: partition logic moved verbatim to subtitle_logic.py
    # v3.9.230: the Kodi subtitle-addon search is GONE, and it had to be.
    #
    #   a4kSubtitles: no subtitles found for Arabic
    #   [DexHub] subtitle prefetch ready: 12 row(s)
    #
    # Those two lines sat next to each other in the log. Kodi's subtitle addons
    # read the item from the player's InfoTag — and the Kodi 22 crash guard
    # (v3.9.192) deliberately ships a MINIMAL playback item with no InfoTag at
    # all, because populating it kills Kodi before the first frame on this
    # hardware. So they search for a film with no title, no year and no IMDb id
    # and, correctly, find nothing. They cannot work here, and the crash guard
    # is not negotiable. Dex Hub's own search found twelve subtitles for the
    # same item. Keep what works.

    selected_first, other_subs = api._partition_subtitle_rows(
        subtitle_rows, force_subtitles, selected_sub, ctx, api._pick_default_subtitle_index)

    # Silo's downloaded subtitles are session-authenticated DeliveryUrl files.
    # On normal Play the generic fast path deferred every native subtitle, so
    # Kodi 21/22 could snapshot a list of not-yet-existing files and show none.
    # Materialize one preferred Silo sidecar synchronously (without turning
    # subtitles on); the rest remain deferred to keep first-frame fast.
    # v5.10.237: Plex, Emby and Jellyfin sidecars the same way as Silo's. They
    # were all deferred, so Kodi got paths not on disk yet and showed no
    # external subtitle at all (Emby above all: its subtitles are sidecars)
    _silo_native_subs = bool(subtitle_rows and (str(ctx.get('server_type') or '').lower() == 'silo'
                                                 or api._is_plex_like_playback(ctx)))
    if _silo_native_subs and not selected_first and other_subs:
        try:
            _silo_pick = api._pick_default_subtitle_index(other_subs, ctx)
        except Exception:
            _silo_pick = 0
        if _silo_pick < 0 or _silo_pick >= len(other_subs):
            _silo_pick = 0
        selected_first = [other_subs[_silo_pick]]
        other_subs = [row for i, row in enumerate(other_subs) if i != _silo_pick]

    # Native Plex/Emby rows may carry many authenticated sidecars.  The old
    # implementation handed Kodi paths that did not exist yet, then started
    # every download in the background. Kodi 21/22 commonly snapshots the
    # subtitle list before those files appear, so Plex/Emby looked as if they
    # had no subtitles. Materialize only the elected/default track now; keep
    # every other sidecar deferred so first-frame latency stays bounded.
    _native_deferred_subs = bool(api._is_plex_like_playback(ctx) and subtitle_rows
                                 and not _silo_native_subs)
    if _silo_native_subs:
        # Only the elected track delays ordinary playback. The singleton
        # service materializes and attaches other tracks after AV start.
        # Explicit "attach before playing" retains the whole preparation set.
        _silo_rows = list(selected_first)
        _before = bool(force_subtitles and _attach_before_mode(api))
        if _before:
            _silo_rows += [r for r in other_subs if r not in selected_first]
        prepared_first, _silo_left = api._prepare_subtitle_files_bounded(
            _silo_rows, stream_key or 'play', budget_seconds=(3.5 if _before else 1.0))
        other_subs = list(_silo_left) + ([] if _before else list(other_subs))
    else:
        prepared_first = api._prepare_subtitle_files(
            selected_first,
            stream_key or 'play',
            # v3.9.230: ALWAYS download a local copy. Handing Kodi a remote URL
            # means the player fetches it itself, mid-handoff, with no retry and no
            # error the user can see — the subtitle just silently never appears.
            prefer_local_copy=True,
            defer_download=False,
        )
    if _native_deferred_subs:
        prepared_other = api._prepare_subtitle_files(
            other_subs, stream_key or 'play', prefer_local_copy=True,
            defer_download=True)
        prepared_first.extend(prepared_other)
        other_subs = []
    subs = [row.get('path') for row in prepared_first if row.get('path')]
    if force_subtitles and not subs and not other_subs:
        try:
            api.notify(api.tr('لم يتم العثور على ترجمة مطابقة لهذا المصدر'))
        except Exception:
            pass
    # v3.9.105: pick the BEST Arabic subtitle as the auto-selected one, not
    # just whatever sits at index 0. The user complaint was that Chinese /
    # Spanish entries were getting auto-selected when an Arabic subtitle
    # existed further down the list. We now scan `prepared_first` for the
    # first row whose lang_key matches the user's preferred languages (ar
    # first, then en, then any fallback) and elect that as the active one.
    # Round 4a: election logic (best-Arabic pick, v3.9.105) moved verbatim
    # to subtitle_logic.py.
    selected_subtitle_path, selected_subtitle_index = api._elect_default_subtitle(prepared_first, subs)
    if subs:
        try:
            item.setSubtitles(subs)
        except Exception:
            pass

    _attach_remaining = bool(
        other_subs and (_silo_native_subs or not api._kodi22_playlist_handoff_enabled()))
    # v5.4.11: the post-start subtitle work — applying the elected track,
    # attaching the deferred sidecars, and the background broker discovery —
    # no longer runs on threads owned by THIS plugin invocation.  Kodi's
    # invoker cannot finish while any of its interpreter's threads are alive
    # ("CPythonInvoker(...): waiting on thread" in the log), which broke
    # reuselanguageinvoker on every playback and left zombie interpreters
    # whose Player polling collided with the Amlogic display reset on
    # stop → instant replay (the remaining hard freeze).  The dispatch now
    # publishes ONE job property before Player.play; the long-lived service
    # companion executes it in resources/lib/playback/post_start.py.
    try:
        _subtitle_index_valid = (
            selected_subtitle_index is not None and
            int(selected_subtitle_index) >= 0
        )
    except Exception:
        _subtitle_index_valid = False
    if (selected_subtitle_path or _subtitle_index_valid or force_subtitles
            or _attach_remaining or _background_subtitle_discovery):
        try:
            api._publish_post_start_job(
                ctx,
                stream_key=stream_key or 'play',
                subtitle_path=selected_subtitle_path or '',
                subtitle_index=(int(selected_subtitle_index)
                                if _subtitle_index_valid else -1),
                force_subtitles=bool(force_subtitles),
                existing_paths=list(subs),
                # v5.10.254: only the subtitles already handed to Kodi; the
                # others found before the play (prefetch) still go through
                # the player-list filter after start
                subtitle_rows=prepared_first,
                attach_rows=(other_subs if _attach_remaining else []),
                discovery=bool(_background_subtitle_discovery),
            )
        except Exception as exc:
            api.xbmc.log('[DexHub] post-start job publish failed: %s' % exc,
                     api.xbmc.LOGWARNING)

    # Original, proven approach: xbmc.Player().play() starts playback on its
    # own thread. We previously sat here for 6 seconds polling isPlayingVideo
    # to apply the subtitle and resume seek — but that kept the Python
    # invoker alive, which in turn kept Kodi's busy spinner on screen even
    # when playback had already started. Symptom: "loading spinner stays
    # even when the movie plays".
    #
    # The fix: kick off Player.play synchronously (so Kodi knows we have
    # started), then hand off the post-start chores (subtitles, seek) to a
    # background thread, and return immediately. The plugin invoker
    # finishes, the busy dialog clears, and the post-start work runs while
    # playback already shows on screen.
    resume_seconds = 0.0
    try:
        resume_seconds = float(ctx.get('resume_seconds') or 0.0)
    except Exception:
        resume_seconds = 0.0
    resume_percent = 0.0
    try:
        resume_percent = float(ctx.get('resume_percent') or 0.0)
    except Exception:
        resume_percent = 0.0

    # Native resume metadata is helpful on stable Kodi releases.  On Kodi 22
    # alpha safe-playlist handoff it can invoke Kodi's own resume dialog while
    # the plugin is resolving, causing a native crash.  That route uses the
    # post-start seek below instead.
    if resume_seconds > 1.0 or (1.0 < resume_percent < 95.0):
        try:
            total_for_resume = 0.0
            for _candidate in (
                info.get('totaltime') if isinstance(info, dict) else None,
                info.get('duration') if isinstance(info, dict) else None,
                ctx.get('duration'), ctx.get('duration_s'),
                (float(ctx.get('duration_ms') or 0) / 1000.0) if ctx.get('duration_ms') else None,
            ):
                try:
                    if _candidate not in (None, '') and float(_candidate) > 0:
                        total_for_resume = float(_candidate)
                        break
                except Exception:
                    pass
            # If only a percent is available and we know duration, compute a
            # concrete ResumeTime now. The post-start thread still keeps the
            # percent fallback for sources where Kodi only reports duration
            # after opening.
            if resume_seconds <= 1.0 and 1.0 < resume_percent < 95.0 and total_for_resume > 60.0:
                resume_seconds = max(0.0, total_for_resume * resume_percent / 100.0)
            # v5.10.83, as DPlex: open the stream AT the resume point instead of
            # starting at 00:00 and seeking once it plays. StartOffset is used
            # rather than ResumeTime because it starts playback there directly,
            # with no resume dialog, which also keeps clear of the Kodi 22 alpha
            # crash in the resume-dialog path that led to this being disabled.
            # The companion coordinator still runs; it sees the position already
            # reached and only seeks if Kodi did not honour the offset.
            _switching = bool(ctx.get('source_switch_pending'))
            try:
                # v5.10.248: a switch from the player's Sources panel
                _mark = api.xbmcgui.Window(10000).getProperty('dexhub.switch.inplace')
                if _mark and 0 <= api.time.time() - float(_mark) < 600:
                    _switching = True
                    api.xbmcgui.Window(10000).clearProperty('dexhub.switch.inplace')
            except Exception:
                pass
            _usable = resume_seconds >= 30.0 or (_switching and resume_seconds > 1.0)
            if _usable and total_for_resume > 0.0 and resume_seconds > total_for_resume - 90.0:
                _usable = False
            if _usable and api._get_bool_setting('native_start_offset', True):
                item.setProperty('StartOffset', '%.3f' % resume_seconds)
                try:
                    api.xbmc.log('[DexHub] native resume: StartOffset=%.1fs (companion verifies, seeks only if needed)' % resume_seconds, api.xbmc.LOGINFO)
                except Exception:
                    pass
            elif resume_seconds > 1.0:
                try:
                    api.xbmc.log('[DexHub] native resume skipped; companion resume coordinator owns seek', api.xbmc.LOGINFO)
                except Exception:
                    pass
        except Exception:
            pass

    # Explicitly close any busy dialog Kodi opened on our behalf BEFORE we
    # start playback, so the user sees the player loading screen instead of
    # a spinner stuck on top of the previous window.
    for dlg in ('busydialog', 'busydialognocancel'):
        try:
            api.xbmc.executebuiltin('Dialog.Close(%s,true)' % dlg)
        except Exception:
            pass

    from .tvos_players import handoff as _tvos_handoff
    if _tvos_handoff(api, stream_url, prepared_first, selected_subtitle_path, resume_seconds, item, ctx):
        return

    # v5.4: hand off to Kodi directly.  The optional custom XML waiter added
    # another modal lifecycle exactly where CoreELEC/Kodi 22 is most fragile.
    # Keep this local sentinel so the guarded cleanup below remains harmless.
    _waiter = None
    try:
        try:
            current_action = dict(api.parse_qsl(api.sys.argv[2].lstrip('?'))).get('action', '')
        except Exception:
            current_action = ''
        if use_resolved_url is None:
            use_resolved_url = (api.HANDLE >= 0 and current_action in (
                'play_item', 'cw_resume', 'cw_play_from_start', 'play',
                # v3.9.172: plex rows are IsPlayable, so plex_play must ride
                # the same resolve/handoff machinery as play_item.
                'plex_play',
                'emby_play',
                # tmdb_player excluded: dexhub.json is is_resolvable=false,
                # so TMDb Helper fires the URL and exits with no open HANDLE
                # to resolve. DexHub drives playback via player.play() here.
                # (v3.9.156: reverted the 155 resolvable experiment — it
                # made Kodi expect a resolved URL even when we show the
                # source list, raising 'is not playable'.)
            ))
            try:
                if float(ctx.get('resume_seconds') or 0.0) > 1.0 or (1.0 < float(ctx.get('resume_percent') or 0.0) < 95.0):
                    use_resolved_url = False
            except Exception:
                pass
        if api._kodi22_playlist_handoff_enabled():
            # EmbyCon-style safe path for Kodi 22/CoreELEC: avoid
            # setResolvedUrl/direct URL handoff and use a one-item playlist.
            use_resolved_url = False
        elif api._KODI_MAJOR >= 22 and not api._safe_playback_handoff_enabled():
            # Optional legacy Kodi 22 direct-play path.
            use_resolved_url = False
        if str(stream_url or '').lower().startswith(('plugin://', 'magnet:')):
            # Torrent handoffs (Elementum/Quasar/Torrest) are plugins, not raw
            # media files. setResolvedUrl can leave Kodi trying to resolve the
            # plugin URL as a file; Player.play dispatches it correctly.
            use_resolved_url = False
        if use_resolved_url:
            api._monitor_playback('play-handoff', handoff='setResolvedUrl', handle=api.HANDLE, stream_key=stream_key, provider=ctx.get('provider_name') or ctx.get('provider_id') or '')
            api.xbmcplugin.setResolvedUrl(api.HANDLE, True, item)
        else:
            # v4.7.8: do NOT report a failed resolve here. 4.7.2 closed the
            # abandoned resolve context with setResolvedUrl(False) to silence
            # Kodi's "Error resolving item" log line — but that tells Kodi the
            # item is unplayable, and the user's log then showed
            # "Playlist Player: skipping unplayable item" right where playback
            # should continue. A cosmetic log line is not worth poisoning the
            # playlist; the handoff owns playback from here.
            if api._kodi22_playlist_handoff_enabled():
                api.xbmc.log('[DexHub] playback: entering Kodi22 playlist handoff (minimal_item=%s)' % api._kodi22_minimal_item_enabled(), api.xbmc.LOGINFO)
                api._monitor_playback('play-handoff', handoff='Kodi22Playlist.play', handle=api.HANDLE, stream_key=stream_key, provider=ctx.get('provider_name') or ctx.get('provider_id') or '')
                # v5.10.114: Kodi opened this route through a resolve (a
                # skin's widget or a Kodi list played the item: PlayMedia)
                # and waits for the answer. Unanswered, Kodi gave up when
                # this script ended ("Error resolving item ... is not
                # playable", "Playlist Player: skipping unplayable item: 0")
                # and stopped the video the hand-off had started (the user's
                # log, Arctic Fuse 3's widgets). Answered after the hand-off
                # the failure hit the new item (v4.7.2, v4.7.8). Answered
                # first, the failed resolve is over before the playlist is
                # replaced: Kodi 22 keeps playing (test harness).
                if api.HANDLE >= 0:
                    try:
                        api.xbmcplugin.setResolvedUrl(api.HANDLE, False, api.xbmcgui.ListItem(offscreen=True))
                        api.xbmc.log('[DexHub] playback: the waiting resolve answered before the playlist hand-off', api.xbmc.LOGINFO)
                        api.xbmc.sleep(450)
                    except Exception:
                        pass
                try:
                    playlist = api.xbmc.PlayList(api.xbmc.PLAYLIST_VIDEO)
                    playlist.clear()
                    playlist.add(stream_url, item)
                    api.xbmc.Player().play(playlist, startpos=0)
                except Exception:
                    # Last-resort direct play if playlist construction itself
                    # fails; do not alter the selected source URL.
                    api.xbmc.Player().play(stream_url, item)
            else:
                api._monitor_playback('play-handoff', handoff='Player.play', handle=api.HANDLE, stream_key=stream_key, provider=ctx.get('provider_name') or ctx.get('provider_id') or '')
                api.xbmc.Player().play(stream_url, item)
        api._guard_cancellable_selector_failure(ctx, stream_url)
        # Auto-close the wait window as soon as AV actually starts.
        if _waiter is not None:
            _waiter.close_on_av_start()
    except Exception as exc:
        api._monitor_playback('play-handoff-failed', level=api.xbmc.LOGERROR, error=exc, stream_key=stream_key, title=ctx.get('title') or '', provider=ctx.get('provider_name') or ctx.get('provider_id') or '')
        api.xbmc.log('[DexHub] playback handoff failed: %s' % exc, api.xbmc.LOGERROR)
        # Tear down the wait window on error so the user isn't stuck
        # staring at a spinner that will never resolve.
        try:
            if _waiter is not None:
                _waiter.close()
        except Exception:
            pass
        api._clear_tmdbh_transient()
        api._clear_source_transient_props(clear_global=True)
        return


def play_selected(api, stream_key, resume_seconds='', resume_percent='', fallback_keys=None, switch_subtitle_path='', switch_subtitle_enabled='', switch_subtitle_name=''):
    if api._dispatch_in_flight('play|%s' % (stream_key or ''), window_seconds=4):
        api.xbmc.log('[DexHub] play: duplicate dispatch suppressed (%s)' % stream_key, api.xbmc.LOGINFO)
        return
    ctx = api.cache_store.get('stream', stream_key)
    if not ctx:
        api.error(api.tr('انتهت بيانات التشغيل. أعد اختيار المصدر.'))
        return api.end_dir()
    # Backward-compatible parameter only.  v5.3 never follows it: the selected
    # source is authoritative and a failure returns control to the user.
    fallback = []
    try:
        rs = float(resume_seconds or 0.0)
    except Exception:
        rs = 0.0
    if rs > 1.0:
        ctx = dict(ctx or {})
        ctx['resume_seconds'] = rs
    try:
        rp = float(resume_percent or 0.0)
    except Exception:
        rp = 0.0
    if rp > 1.0 and rp < 95.0:
        ctx = dict(ctx or {})
        ctx['resume_percent'] = rp
    if switch_subtitle_path:
        ctx = dict(ctx or {})
        ctx['switch_subtitle_path'] = str(switch_subtitle_path)
        ctx['switch_subtitle_enabled'] = str(switch_subtitle_enabled or '1').strip().lower() not in ('0', 'false', 'no', 'off')
        ctx['switch_subtitle_name'] = str(switch_subtitle_name or '')
        # A source switch is the same viewing session, not a new subtitle
        # search.  Suppress Dex Hub's broker/auto-pick for the replacement;
        # CompanionPlayer restores the exact external file after any modal
        # subtitle dialog has closed.
        ctx['source_switch_reuse_subtitle'] = True
        ctx['play_with_subtitles'] = False
        ctx['selected_subtitle'] = ''
    api._play_with_context(ctx, stream_key, fallback_keys=fallback)
