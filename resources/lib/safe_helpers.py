# -*- coding: utf-8 -*-
"""Pure, self-contained routing helpers for plugin.video.dexhub.

First module of the "safe helpers" tier from the v3.9.145 refactor map:
functions whose entire dependency closure is ``context.build_url`` plus each
other.  Moved verbatim from ``plugin.py`` (v3.9.147); behaviour is verified
identical via a 1700+-case before/after output snapshot.

Rules for adding functions here:
  * no Kodi UI calls (dialogs, ListItems, window properties),
  * no module-level state beyond what ``context`` provides,
  * no imports from ``plugin`` (would create a cycle).
"""

from .context import build_url


def _is_series_media(media_type=''):
    return str(media_type or '').strip().lower() in ('series', 'anime', 'show', 'tv')


def _source_picker_url(media_type='movie', canonical_id='', title='', season='', episode='', video_id='', resume_seconds='', preferred_provider_name='', source_provider_id='', source_mode='', resume_percent=''):
    media_type = str(media_type or 'movie').strip().lower()
    if season not in (None, '', 0, '0') and episode not in (None, '', 0, '0'):
        args = {
            'action': 'episode_streams',
            'force_picker': '1',
            'canonical_id': canonical_id,
            'video_id': video_id or canonical_id,
            'season': str(season),
            'episode': str(episode),
            'title': title or canonical_id,
            'media_type': 'series' if _is_series_media(media_type) else media_type,
        }
        if resume_seconds not in (None, '', 0, '0'):
            args['resume_seconds'] = str(resume_seconds)
        if resume_percent not in (None, '', 0, '0'):
            args['resume_percent'] = str(resume_percent)
        if preferred_provider_name:
            args['preferred_provider_name'] = preferred_provider_name
        if source_provider_id:
            args['source_provider_id'] = source_provider_id
        if source_mode:
            args['source_mode'] = source_mode
        return build_url(**args)
    if _is_series_media(media_type):
        return build_url(action='series_meta', media_type='series', canonical_id=canonical_id, title=title or canonical_id, source_provider_id=source_provider_id or '')
    args = {'action': 'streams', 'force_picker': '1', 'media_type': media_type or 'movie', 'canonical_id': canonical_id, 'title': title or canonical_id}
    if resume_seconds not in (None, '', 0, '0'):
        args['resume_seconds'] = str(resume_seconds)
    if resume_percent not in (None, '', 0, '0'):
        args['resume_percent'] = str(resume_percent)
    if preferred_provider_name:
        args['preferred_provider_name'] = preferred_provider_name
    if source_provider_id:
        args['source_provider_id'] = source_provider_id
    if source_mode:
        args['source_mode'] = source_mode
    return build_url(**args)


_SERVER_KINDS = ('plex', 'emby', 'jellyfin', 'silo')


def _search_ctx(ctx):
    """v5.10.250: a title played straight from a server (Plex, Emby,
    Jellyfin, Silo) keeps the server's own item as its id (video_id is the
    rating key, and a Plex or Emby episode's canonical id is the episode's
    own IMDb id). The source search needs the title's ids: the show's IMDb
    or TMDb id and S:E for an episode, the movie's for a movie, and no
    server as the only source."""
    ctx = dict(ctx or {})
    kind = str(ctx.get('server_type') or '').lower()
    provider = str(ctx.get('provider_id') or '')
    if kind not in _SERVER_KINDS and not provider.endswith('_native'):
        return ctx

    def _id(imdb, tmdb):
        imdb = str(imdb or '').strip()
        if imdb.startswith('tt'):
            return imdb
        tmdb = str(tmdb or '').strip()
        return ('tmdb:%s' % tmdb) if tmdb and tmdb not in ('0', 'None') else ''

    def _real(value):
        value = str(value or '')
        return value if value and value.split(':', 1)[0] not in _SERVER_KINDS else ''

    season, episode = ctx.get('season'), ctx.get('episode')
    is_ep = (_is_series_media(ctx.get('media_type'))
             and season not in (None, '', 0, '0') and episode not in (None, '', 0, '0'))
    if is_ep:
        base = _id(ctx.get('show_imdb_id'), ctx.get('show_tmdb_id'))
        if not base:
            canonical = _real(ctx.get('canonical_id'))
            # an Emby/Silo episode already carries its show's id
            if canonical and canonical not in (ctx.get('imdb_id'), 'tmdb:%s' % ctx.get('tmdb_id')):
                base = canonical
        if base:
            ctx['canonical_id'] = base
            ctx['video_id'] = '%s:%s:%s' % (base, int(season), int(episode))
            ctx['media_type'] = 'series'
            ctx['title'] = ctx.get('show_title') or ctx.get('title') or ''
    else:
        base = _id(ctx.get('imdb_id'), ctx.get('tmdb_id')) or _real(ctx.get('canonical_id'))
        if base:
            ctx['canonical_id'] = base
            ctx['video_id'] = base
    ctx['source_provider_id'] = ''
    ctx['meta_target_key'] = ''
    ctx['source_mode'] = ''
    ctx['provider_name'] = ''
    return ctx


def _source_picker_url_from_ctx(ctx, resume_seconds='', resume_percent=''):
    ctx = _search_ctx(ctx)
    try:
        rs = resume_seconds if resume_seconds not in (None, '') else ctx.get('resume_seconds', '')
        rp = resume_percent if resume_percent not in (None, '') else ctx.get('resume_percent', '')
        return _source_picker_url(
            media_type=ctx.get('media_type') or 'movie',
            canonical_id=ctx.get('canonical_id') or '',
            title=ctx.get('title') or ctx.get('show_title') or ctx.get('canonical_id') or '',
            season=ctx.get('season') or '',
            episode=ctx.get('episode') or '',
            video_id=ctx.get('video_id') or ctx.get('canonical_id') or '',
            resume_seconds=rs,
            resume_percent=rp,
            preferred_provider_name=ctx.get('provider_name') or '',
            source_provider_id=ctx.get('source_provider_id') or ctx.get('meta_target_key') or '',
            source_mode=ctx.get('source_mode') or '',
        )
    except Exception:
        return ''
