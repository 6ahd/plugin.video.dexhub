"""Resume a known source using the lightweight player entry.

Progress services describe what was watched, not where a stream can play.
Do not autoplay an arbitrary provider for one of those account-only rows.
"""

_PROGRESS_SOURCES = frozenset(('trakt', 'simkl', 'floppy', 'nuvio', 'stremio',
                               'kodi', 'local', 'external', 'pov', 'mdblist'))


def enabled(api):
    return api.ADDON.getSetting('continue_known_source_direct') != 'false'


def known(row):
    if row.get('native_backend') in ('plex', 'emby', 'jellyfin', 'silo') and row.get('native_item_id'):
        return True
    sid = str(row.get('source_provider_id') or '').strip()
    if sid and not sid.startswith('virtual.'):
        return True
    name = str(row.get('provider_name') or '').strip().casefold()
    return bool(name and name not in _PROGRESS_SOURCES)


def picker(api, row):
    """A saved server disappeared: keep the resume point in source choice."""
    episodic = row.get('season') not in (None, '', '0', 0) and row.get('episode') not in (None, '', '0', 0)
    media = row.get('media_type') or ('series' if episodic else 'movie')
    common = dict(title=row.get('title') or row.get('canonical_id') or '',
                  resume_seconds=row.get('position') or '0',
                  resume_percent=row.get('percent') or '',
                  preferred_provider_name=row.get('provider_name') or '',
                  source_provider_id='virtual.continue.series' if episodic else 'virtual.continue.movie',
                  ui_seed={'poster': row.get('poster') or '',
                           'background': row.get('background') or '',
                           'clearlogo': row.get('clearlogo') or ''})
    if episodic:
        return api.episode_streams(row.get('canonical_id') or '', row.get('video_id') or '',
                                   row.get('season'), row.get('episode'), media_type=media, **common)
    return api.streams(media, row.get('canonical_id') or '', **common)


def resume(api, media_type='', canonical_id='', video_id='', season='', episode='',
           title='', position='0', resume_percent='', provider_name='', direct='0',
           source_provider_id='', ui_poster='', ui_fanart='', ui_clearlogo='', studio='',
           ui_seed_key=''):
    target = media_type or ('series' if season not in (None, '', '0', 0) and
                           episode not in (None, '', '0', 0) else 'movie')
    seed = api._ui_seed_from_route(ui_poster=ui_poster, ui_fanart=ui_fanart,
                                   ui_clearlogo=ui_clearlogo, ui_seed_key=ui_seed_key)
    if studio:
        seed['studio'] = studio
    direct_requested = str(direct or '').strip().lower() in ('1', 'true', 'yes', 'on')
    if direct_requested and enabled(api):
        from . import playback_choices
        played, result = playback_choices.try_saved(api, target, canonical_id, video_id,
            season, episode, resume_seconds=position or '0', resume_percent=resume_percent or '',
            provider_id=source_provider_id or '', provider_name=(provider_name if known({'provider_name':provider_name}) else ''))
        if played:
            return result
    if direct_requested and enabled(api) and known({'provider_name': provider_name,
                                                   'source_provider_id': source_provider_id}):
        common = dict(title=title or canonical_id, resume_seconds=position or '0',
                      resume_percent=resume_percent or '',
                      preferred_provider_name=provider_name or '',
                      source_provider_id=source_provider_id or '',
                      source_mode='quick_same', ui_seed=seed or None)
        if season not in (None, '', '0', 0) and episode not in (None, '', '0', 0):
            return api._auto_play_first_episode(canonical_id, video_id or canonical_id,
                                                season, episode, **common)
        if not api._is_series_media(target):
            return api._auto_play_first_movie(canonical_id, **common)
    # Existing quick_same resolver falls back to the picker when that provider
    # is removed or returns no links. Explicit source-choice routes stay intact.
    return api._dispatch_dexhub_play(media_type=target, canonical_id=canonical_id,
        title=title or canonical_id, video_id=video_id, season=season, episode=episode,
        resume_seconds=position or '0', resume_percent=resume_percent or '',
        preferred_provider_name=provider_name or '', source_provider_id=source_provider_id or '',
        ui_seed=seed or None)
