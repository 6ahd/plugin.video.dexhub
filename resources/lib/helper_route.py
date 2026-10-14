"""Shared helper_route. No imports or side effects until called.

The caller supplies Kodi and compatibility adapters; Home/catalog code does not
belong here. Relative feature imports remain lazy and retain package semantics.
"""

def resume_from_continue(api, media_type='movie', canonical_id='', tmdb_id='', imdb_id='', tvdb_id='', season='', episode='', title=''):
    """Best-effort resume bridge for TMDb Helper -> DexHub playback.

    When a skin/widget launches playback through TMDb Helper, the player URL
    does not carry DexHub's local Continue Watching position. DexHub's own CW
    screen resumes correctly because it calls cw_resume with resume_seconds,
    but the TMDbH handoff only gives IDs.  Look up the matching row in our
    playback database and inject resume_seconds/resume_percent into the source
    picker/autoplay path.  This keeps TMDbH widgets from starting local DexHub
    progress at 0:00.
    """
    def _norm_imdb(v):
        v = str(v or '').strip()
        if v.lower().startswith('imdb:'):
            v = v.split(':', 1)[1].strip()
        if v.isdigit():
            v = 'tt%s' % v
        return v.lower()

    wanted = {
        'canonical': str(canonical_id or '').strip().lower(),
        'tmdb_id': str(tmdb_id or '').strip(),
        'imdb_id': _norm_imdb(imdb_id),
        'tvdb_id': str(tvdb_id or '').strip(),
    }

    def _norm_title(value):
        # Last-resort legacy matching only.  Keep Arabic/Latin letters and
        # digits, collapse punctuation/spacing, and require a UNIQUE row
        # below.  This recovers opaque provider ids from old databases without
        # guessing between remakes or identically named shows.
        value = str(value or '').strip().casefold()
        value = api.re.sub(r'[^\w]+', ' ', value, flags=api.re.UNICODE)
        return ' '.join(value.split())

    wanted_title = _norm_title(title)
    try:
        wanted['season'] = int(season or 0)
    except Exception:
        wanted['season'] = 0
    try:
        wanted['episode'] = int(episode or 0)
    except Exception:
        wanted['episode'] = 0

    is_episode = wanted['season'] > 0 and wanted['episode'] > 0

    def _resume_payload(row):
        """Normalize a stored row into the playback arguments we need."""
        try:
            pos = float(row.get('position') or 0.0)
        except Exception:
            pos = 0.0
        try:
            pct = float(row.get('percent') or 0.0)
        except Exception:
            pct = 0.0
        if pos <= 1.0 and not (1.0 < pct < 95.0):
            return {}
        return {
            'resume_seconds': pos if pos > 1.0 else 0,
            'resume_percent': ('%.2f' % pct) if (pos <= 1.0 and 1.0 < pct < 95.0) else '',
            'provider_name': row.get('provider_name') or '',
            'poster': row.get('poster') or '',
            'fanart': row.get('background') or '',
            'clearlogo': row.get('clearlogo') or '',
            'updated_at': int(row.get('updated_at') or 0),
            'source': 'dexhub',
        }

    # v3.9.157: New rows have independent standard ids and are found via
    # indexed lookups.  This is both reliable for provider-private ids and
    # avoids loading/scanning 500 Continue Watching rows on every TMDbH play.
    try:
        direct_row = api.playback_store.find_resume_entry(
            media_type='series' if is_episode else 'movie',
            canonical_id=canonical_id,
            tmdb_id=wanted['tmdb_id'], imdb_id=wanted['imdb_id'], tvdb_id=wanted['tvdb_id'],
            season=wanted['season'], episode=wanted['episode'],
        )
        direct_payload = _resume_payload(direct_row or {})
        if direct_payload:
            api.xbmc.log('[DexHub] TMDbH resume bridge matched indexed playback row', api.xbmc.LOGINFO)
            return direct_payload
    except Exception as exc:
        try:
            api.xbmc.log('[DexHub] TMDbH indexed resume lookup failed: %s' % exc, api.xbmc.LOGWARNING)
        except Exception:
            pass

    # Compatibility path for progress recorded before the schema migration.
    # Old rows do not have dedicated id columns, so retain the conservative
    # parser below until users naturally re-save their playback positions.
    try:
        rows = api.playback_store.list_continue_items(limit=500) or []
    except Exception:
        return {}
    title_candidates = []
    for row in rows:
        try:
            row_mt = str(row.get('media_type') or '').strip().lower()
            row_is_episode = api._continue_row_is_episode(row)
            if is_episode != bool(row_is_episode):
                continue
            if is_episode:
                if int(row.get('season') or 0) != wanted['season'] or int(row.get('episode') or 0) != wanted['episode']:
                    continue
            elif row_mt not in ('movie', 'movies', ''):
                # Avoid matching a show-level row against movie playback.
                continue

            row_canonical = str(row.get('canonical_id') or '').strip()
            row_video = str(row.get('video_id') or '').strip()
            if wanted_title and _norm_title(row.get('title') or '') == wanted_title:
                title_candidates.append(row)
            row_ids = api._merge_seed_ids(api.extract_ids({'id': row_canonical}), api.extract_ids({'id': row_video}))
            # Some older rows were saved with plain tmdb/imdb ids embedded in
            # canonical/video only, while TMDb Helper calls us with explicit
            # route ids. Keep the matching permissive but still require
            # season/episode equality for episodes (checked above).
            row_tmdb = str(row_ids.get('tmdb_id') or '').strip()
            row_imdb = _norm_imdb(row_ids.get('imdb_id') or '')
            row_tvdb = str(row_ids.get('tvdb_id') or '').strip()

            matched = False
            _row_keys = set([row_canonical.lower(), row_video.lower()])
            if row_tmdb:
                _row_keys.update(['tmdb:%s' % row_tmdb, 'tmdb:movie:%s' % row_tmdb, 'tmdb:tv:%s' % row_tmdb, 'tmdb:series:%s' % row_tmdb])
            if row_imdb:
                _row_keys.update([row_imdb, 'imdb:%s' % row_imdb])
            if row_tvdb:
                _row_keys.update(['tvdb:%s' % row_tvdb])
            if wanted['canonical'] and wanted['canonical'] in _row_keys:
                matched = True
            if not matched and wanted['tmdb_id'] and row_tmdb and wanted['tmdb_id'] == row_tmdb:
                matched = True
            if not matched and wanted['imdb_id'] and row_imdb and wanted['imdb_id'] == row_imdb:
                matched = True
            if not matched and wanted['tvdb_id'] and row_tvdb and wanted['tvdb_id'] == row_tvdb:
                matched = True
            if not matched:
                continue

            out = _resume_payload(row)
            if not out:
                continue
            try:
                api.xbmc.log('[DexHub] TMDbH resume bridge matched legacy CW row', api.xbmc.LOGINFO)
            except Exception:
                pass
            return out
        except Exception:
            continue
    # Old provider rows may contain only an opaque provider id.  When there is
    # exactly one unfinished row with the same normalized title (and the same
    # season/episode was already required above), it is safe enough to bridge
    # it once.  More than one candidate deliberately returns no resume.
    if len(title_candidates) == 1:
        out = _resume_payload(title_candidates[0])
        if out:
            try:
                api.xbmc.log('[DexHub] TMDbH resume bridge matched unique legacy title', api.xbmc.LOGINFO)
            except Exception:
                pass
            return out
    return {}


def persist_resume_hint(api, hint, media_type, canonical_id, video_id, title='', season=None, episode=None, ids=None, poster='', fanart='', clearlogo=''):
    """Mirror an external/live progress hint into Dex Hub's unified DB.

    Local Dex Hub rows are already persisted by the player monitor. TMDb
    Helper/Kodi and Trakt hints are saved here so Continue Watching immediately
    shows the same position and subsequent launches no longer require a remote
    lookup. Existing settings and historical rows are never deleted.
    """
    hint = dict(hint or {})
    source = str(hint.get('source') or '').strip().lower()
    if not hint or source in ('', 'dexhub'):
        return False
    try:
        position = float(hint.get('resume_seconds') or 0.0)
        percent = float(hint.get('resume_percent') or 0.0)
        duration = float(hint.get('duration') or 0.0)
    except Exception:
        return False
    if duration <= 0 and position > 0 and percent > 1:
        duration = position * 100.0 / percent
    if percent <= 0 and duration > 0:
        percent = position * 100.0 / duration
    if position <= 30.0 or not (0.0 < percent < 95.0):
        return False
    ids = dict(ids or {})
    label = {'trakt': 'Trakt', 'tmdbhelper': 'TMDb Helper / Kodi', 'handoff': 'TMDb Helper'}.get(source, source.title())
    try:
        api.playback_store.upsert_entry(
            media_type, canonical_id, video_id, title or canonical_id, label,
            poster or '', fanart or '', clearlogo or '', season, episode,
            position, duration, percent, '', 'unified_import',
            ext_updated_at=(int(hint.get('updated_at') or 0) or None),
            tmdb_id=ids.get('tmdb_id') or '', imdb_id=ids.get('imdb_id') or '',
            tvdb_id=ids.get('tvdb_id') or '',
            show_tmdb_id=(ids.get('tmdb_id') or '') if media_type == 'series' else '',
        )
        return True
    except Exception as exc:
        api.xbmc.log('[DexHub] unified resume persist failed: %s' % exc, api.xbmc.LOGWARNING)
        return False


PLAY_MODE_DIRECT = 'direct'
PLAY_MODE_SEARCH = 'search'
PLAY_MODE_INVERT = 'invert'


def resolve_play_mode(play_mode, setting_direct):
    """Return (auto_first, force_search) for one invocation.

    v5.10.78: `tmdbh_auto_play_first` used to be the only switch, global for
    every play. A shortcut now asks for one specific behaviour on one item:

      direct  play the best source at once, whatever the setting says
      search  open the Dex Hub source search, whatever the setting says
      invert  do the opposite of the setting (the context-menu shortcut)

    Anything else, including an empty value, keeps the setting untouched, so
    every existing player URL and widget link behaves exactly as before.
    force_search also bypasses the optional poster-picker dialog: someone who
    explicitly asked for the Dex Hub search should get the full search.
    """
    mode = str(play_mode or '').strip().lower()
    if mode == PLAY_MODE_INVERT:
        mode = PLAY_MODE_SEARCH if setting_direct else PLAY_MODE_DIRECT
    if mode == PLAY_MODE_DIRECT:
        return True, False
    if mode == PLAY_MODE_SEARCH:
        return False, True
    return bool(setting_direct), False


def tmdb_player(api, video_type='movie', tmdb_id='', imdb_id='', tvdb_id='', title='', year='', showname='', season='', episode='', ar_title='', ar_showname='', poster='', fanart='', resume_seconds='', resume_percent='', resume_duration='', resume_updated_at='', play_mode=''):
    vtype = (video_type or 'movie').lower()
    # Suppress duplicate TMDb Helper handoffs. TMDb Helper sometimes
    # fires its play URL twice in quick succession (especially when
    # users tap "Play" before the dialog has fully rendered). The dedup
    # key includes season/episode so different episodes of the same
    # show don't suppress each other.
    # v5.10.78: the requested play mode is part of the key. Pressing Play and
    # then the opposite-mode shortcut on the same title within the window is
    # two different requests, not a double tap, and must not be swallowed.
    _tp_key = 'tmdb_player|%s|%s|%s|%s|%s|%s|%s' % (
        vtype, tmdb_id or '', imdb_id or '', tvdb_id or '',
        str(season or ''), str(episode or ''), str(play_mode or '').strip().lower(),
    )
    if api._dispatch_in_flight(_tp_key, window_seconds=12):
        api.xbmc.log('[DexHub] tmdb_player: duplicate dispatch suppressed (%s)' % _tp_key, api.xbmc.LOGINFO)
        return
    lookup_title = showname or title

    # Fallback: when the player JSON didn't pass any IDs (e.g. invoked from a
    # widget where TMDb Helper's service monitor populated home-window props
    # but the URL template was missing keys), try those props as last-resort
    # seed before declaring failure.
    if not (tmdb_id or imdb_id or tvdb_id):
        try:
            _mon_win = api.xbmcgui.Window(10000)
            tmdb_id = tmdb_id or (_mon_win.getProperty('TMDbHelper.ListItem.TMDb') or '').strip()
            imdb_id = imdb_id or (_mon_win.getProperty('TMDbHelper.ListItem.IMDb') or '').strip()
            tvdb_id = tvdb_id or (_mon_win.getProperty('TMDbHelper.ListItem.TVDb') or '').strip()
        except Exception:
            pass

    canonical_id = api._resolve_best_id(vtype, tmdb_id=tmdb_id, imdb_id=imdb_id, tvdb_id=tvdb_id, title=lookup_title)
    if not canonical_id:
        api.error('Missing IDs for TMDb Helper player')
        return api.end_dir()

    # Preserve the IDs TMDb Helper gave us so downstream code (subtitle broker,
    # scrobbler, companion sync) has real identifiers even when our meta
    # provider can't resolve them from the canonical id alone. Scope the stash
    # by canonical_id so a later unrelated stream doesn't wrongly inherit it.
    seed_ids = {
        'tmdb_id': str(tmdb_id or '').strip(),
        'imdb_id': str(imdb_id or '').strip() if str(imdb_id or '').startswith('tt') else ('tt%s' % imdb_id if imdb_id and str(imdb_id).isdigit() else str(imdb_id or '').strip()),
        'tvdb_id': str(tvdb_id or '').strip(),
    }
    _resume_media_type = 'series' if vtype in ('episode', 'show', 'series', 'tv') else 'movie'
    # Capture the focused item before the source window replaces Kodi's
    # ListItem context. This is what allows TMDb Helper/Kodi resume to work
    # even when Dex Hub has never played the item before.
    try:
        _live_resume = api.tmdbh_context.read_resume_hint(
            expected_tmdb_id=tmdb_id or None, expected_imdb_id=imdb_id or None,
            season=season, episode=episode) or {}
    except Exception:
        _live_resume = {}
    _explicit_resume = {}
    try:
        _epos, _epct, _edur = float(resume_seconds or 0), float(resume_percent or 0), float(resume_duration or 0)
        if _epos > 30 and _epct < 95:
            _explicit_resume = {'resume_seconds': _epos, 'resume_percent': _epct, 'duration': _edur, 'updated_at': int(float(resume_updated_at or 0)), 'source': 'handoff'}
    except Exception:
        _explicit_resume = {}
    # Explicit/live hints already win below. Avoid reading/scanning Continue
    # Watching rows whose result would be discarded in those cases.
    _local_resume = {}
    if not (_explicit_resume or _live_resume):
        _local_resume = api._tmdbh_resume_from_continue(
            media_type=_resume_media_type, canonical_id=canonical_id,
            tmdb_id=tmdb_id, imdb_id=imdb_id, tvdb_id=tvdb_id,
            season=season, episode=episode, title=lookup_title,
        )
    _trakt_resume = {}
    # v5.10.15: Trakt is a NETWORK call (/sync/playback/*, 12s timeout) and it
    # used to run here, on the dispatch thread, before the loading screen was
    # drawn. Measured on CoreELEC/ARM it cost ~1.1s on every single search and
    # the result was then discarded, because a local Dex Hub resume point
    # already existed and wins the comparison below. Only ask Trakt when there
    # is genuinely nothing local to compare against.
    if not (_explicit_resume or _live_resume or _local_resume):
        try:
            _trakt_resume = api.trakt.find_playback_progress(
                media_type=_resume_media_type, tmdb_id=tmdb_id, imdb_id=imdb_id,
                tvdb_id=tvdb_id, season=season, episode=episode) or {}
        except Exception:
            _trakt_resume = {}
    # Explicit/live context wins because it belongs to the item the user just
    # pressed. Otherwise choose the newest timestamp between Dex Hub and Trakt.
    if _explicit_resume:
        _tmdbh_resume_seed = _explicit_resume
    elif _live_resume:
        _tmdbh_resume_seed = _live_resume
    else:
        _local_ts = int((_local_resume or {}).get('updated_at') or 0)
        _trakt_ts = int((_trakt_resume or {}).get('updated_at') or 0)
        _tmdbh_resume_seed = _trakt_resume if (_trakt_resume and _trakt_ts > _local_ts) else (_local_resume or _trakt_resume or {})
    resume_seconds = _tmdbh_resume_seed.get('resume_seconds') or 0
    resume_percent = _tmdbh_resume_seed.get('resume_percent') or ''
    api.xbmc.log('[DexHub] unified resume source=%s pos=%.1f pct=%s' % (
        _tmdbh_resume_seed.get('source') or _tmdbh_resume_seed.get('provider_name') or 'none',
        float(resume_seconds or 0), str(resume_percent or '')), api.xbmc.LOGINFO)
    # v3.9.156: poster picker dialog is opt-in via settings; default keeps
    # the original search + full results page.
    _tmdbh_pick = (api.ADDON.getSetting('tmdbh_poster_picker') or 'false').strip().lower() in ('true', '1', 'yes', 'on')
    if not poster and _tmdbh_resume_seed.get('poster'):
        poster = _tmdbh_resume_seed.get('poster')
    if not fanart and _tmdbh_resume_seed.get('fanart'):
        fanart = _tmdbh_resume_seed.get('fanart')

    # Stash optional player-supplied art / Arabic-title hints so downstream
    # meta lookups can use them as a fast-path before hitting TMDb / Trakt.
    # These come from the v2 dexhub.json player keys: {ar_title}, {poster},
    # {fanart}. Older v1 player files don't pass them; the empty defaults
    # mean nothing breaks.
    try:
        if ar_title:
            seed_ids['ar_title'] = ar_title
        if ar_showname:
            seed_ids['ar_showname'] = ar_showname
        if poster:
            seed_ids['poster'] = poster
        if fanart:
            seed_ids['fanart'] = fanart
    except Exception:
        pass

    try:
        win = api.xbmcgui.Window(api.WINDOW_ID)
        _prefix = api.ADDON_ID.rsplit('.', 1)[-1]
        win.setProperty(_prefix + '.tmdbh_seed_ids', api.json.dumps(seed_ids))
        win.setProperty(_prefix + '.tmdbh_seed_for', canonical_id or '')
        win.setProperty(_prefix + '.invoked_by_tmdbh', '1')
        api._mark_tmdbh_handoff(60)
    except Exception:
        pass

    auto_first = False
    try:
        auto_first = (api.ADDON.getSetting('tmdbh_auto_play_first') or 'false').lower() == 'true'
    except Exception:
        auto_first = False
    auto_first, _force_search = resolve_play_mode(play_mode, auto_first)
    if _force_search:
        _tmdbh_pick = False
    if play_mode:
        api.xbmc.log('[DexHub] tmdb_player play_mode=%s -> %s' % (
            play_mode, 'direct' if auto_first else 'search'), api.xbmc.LOGINFO)

    if vtype in ('episode', 'show', 'series', 'tv'):
        if season and episode:
            from .search.episode_route import helper_episode_id
            _episode_started = api.time.monotonic()
            video_id = helper_episode_id(imdb_id, season, episode)
            resolved_title = ''
            if not video_id:
                video_id, resolved_title = api._find_episode_video_id(canonical_id, season, episode)
            api.xbmc.log('[DexHub timing] stage=helper-episode-id ms=%.1f' % (
                (api.time.monotonic() - _episode_started) * 1000), api.xbmc.LOGINFO)
            api._persist_unified_resume_hint(
                _tmdbh_resume_seed, 'series', canonical_id, video_id,
                title=title or resolved_title or showname or canonical_id,
                season=int(season or 0), episode=int(episode or 0), ids=seed_ids,
                poster=poster or _tmdbh_resume_seed.get('poster') or '',
                fanart=fanart or _tmdbh_resume_seed.get('fanart') or '',
                clearlogo=_tmdbh_resume_seed.get('clearlogo') or '')
            if auto_first or _tmdbh_pick:
                return api._auto_play_first_episode(canonical_id, video_id, season, episode, title=title or resolved_title or showname or canonical_id, seed_ids=seed_ids, resume_seconds=resume_seconds, resume_percent=resume_percent or '', preferred_provider_name=_tmdbh_resume_seed.get('provider_name') or '', pick_dialog=(_tmdbh_pick and not auto_first))
            return api.episode_streams(canonical_id, video_id, season, episode, title=title or resolved_title or showname or canonical_id, media_type='series', seed_ids=seed_ids, resume_seconds=resume_seconds, resume_percent=resume_percent or '', preferred_provider_name=_tmdbh_resume_seed.get('provider_name') or '', ui_seed={'ui_poster': poster or '', 'ui_fanart': fanart or '', 'ui_clearlogo': _tmdbh_resume_seed.get('clearlogo') or ''})
        return api.series_meta('series', canonical_id, title=showname or title or canonical_id)

    api._persist_unified_resume_hint(
        _tmdbh_resume_seed, 'movie', canonical_id, canonical_id,
        title=title or canonical_id, ids=seed_ids,
        poster=poster or _tmdbh_resume_seed.get('poster') or '',
        fanart=fanart or _tmdbh_resume_seed.get('fanart') or '',
        clearlogo=_tmdbh_resume_seed.get('clearlogo') or '')
    if auto_first or _tmdbh_pick:
        return api._auto_play_first_movie(canonical_id, title=title or canonical_id, seed_ids=seed_ids, resume_seconds=resume_seconds, resume_percent=resume_percent or '', preferred_provider_name=_tmdbh_resume_seed.get('provider_name') or '', pick_dialog=(_tmdbh_pick and not auto_first))
    return api.streams('movie', canonical_id, title=title or canonical_id, seed_ids=seed_ids, resume_seconds=resume_seconds, resume_percent=resume_percent or '', preferred_provider_name=_tmdbh_resume_seed.get('provider_name') or '', ui_seed={'ui_poster': poster or '', 'ui_fanart': fanart or '', 'ui_clearlogo': _tmdbh_resume_seed.get('clearlogo') or ''})
