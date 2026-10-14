# -*- coding: utf-8 -*-
"""Additive player badges from local caches, outside playback callbacks."""


def cached_extras(ctx):
    from . import ui_preferences as prefs
    from .homeui import ratings
    extra = {}
    title = str(ctx.get('title') or ctx.get('show_title') or '')
    extra['dhs.osd.extra.title'] = title
    values, logo, chip = {}, '', False
    if prefs.enabled('metadata_badges'):
        ids = ctx.get('external_ids') or {}
        kind = str(ctx.get('media_type') or '').lower()
        series = kind in ('series', 'tv', 'episode') or (kind not in ('movie', 'movies') and bool(ctx.get('season')))
        imdb = str(ctx.get('imdb_id') or ids.get('imdb_id') or ids.get('imdb') or '')
        tmdb = str(ctx.get('show_tmdb_id') or ctx.get('tmdb_id') or ids.get('tmdb_id') or ids.get('tmdb') or '')
        media = 'series' if series else 'movie'
        if prefs.enabled('player_show_ratings'):
            values = ratings.collect(imdb_id=imdb, tmdb_id=tmdb, media_type=media, network=False)
        if prefs.enabled('player_show_studio'):
            try:
                # v5.10.140: a pack logo only (white first), drawn without a
                # plate in one corner of the overlay; a work whose studio has
                # no pack logo shows none rather than a dark logo on a chip
                from . import studio_art, tmdbhelper
                rows = tmdbhelper.get_studio_logos_from_db(tmdb_id=tmdb, imdb_id=imdb, media_type=media, limit=3) or []
                names = [row.get('name') for row in rows if isinstance(row, dict) and row.get('name')]
                given = ctx.get('studios') or ctx.get('studio') or []
                names += list(given) if isinstance(given, (list, tuple)) else [given]
                for name in names:
                    logo = studio_art.resolve_player(name)
                    if logo:
                        break
            except Exception:
                logo = ''
    extra.update(ratings.props(values, prefix='dhs.osd.rating.'))
    extra['dhs.osd.studio'] = logo
    extra['dhs.osd.studio.chip'] = '1' if chip else ''
    return extra


def publish(extras):
    import xbmcgui
    win = xbmcgui.Window(10000)
    for key, value in extras.items():
        if win.getProperty(key) != value:
            if value:
                win.setProperty(key, value)
            else:
                win.clearProperty(key)


def refresh_current():
    """Settings changed during playback: bounded cached-only background refresh."""
    import xbmc
    from .session_store import load_session
    from .runtime_tasks import submit_optional
    if not xbmc.Player().isPlayingVideo():
        return
    ctx = load_session() or {}
    url = str(ctx.get('url') or '').split('|', 1)[0]
    playing = (xbmc.getInfoLabel('Player.FileNameAndPath') or '').split('|', 1)[0]
    if not ctx or not url or playing != url:
        return
    uid = ctx.get('playback_uid')
    def run():
        extras = cached_extras(ctx)
        current = load_session() or {}
        if current.get('playback_uid') == uid and (xbmc.getInfoLabel('Player.FileNameAndPath') or '').split('|', 1)[0] == url:
            publish(extras)
    submit_optional(run, key='player-presentation-refresh')
