# -*- coding: utf-8 -*-
"""The Dex Hub title page (v5.10.103): a movie or a series inside the Home UI.

After the Details screen of the Nuvio Hub community interface, in the
Home's own design: the title's backdrop (and its trailer) behind the Home's
hero, the actions under it, then for a series its seasons as tabs and the
season's episodes with pictures, plots and progress, then the cast and more
titles like it.

Where the data comes from:
  * the tile the page opens from: title, art, plot and ids, shown at once;
  * the title's metadata add-on (AIOMetadata, Cinemeta, ...) for the episode
    list, read exactly as the classic season listing reads it, so episode
    ids and playback paths are the classic ones (play_item through
    plugin._content_click_path with force_dexhub);
  * TMDb, with the key Dex Hub already uses, for the cast with photos, the
    recommendations, the text in the user's TMDb language and, when no
    add-on has the episode list, the episodes themselves;
  * Dex Hub's playback store for progress and watched episodes.

Layout: dexhub_details.xml (gen_xml.py details_xml).
"""
import json
import re
import time
from .. import lazy_tb as traceback
from urllib.parse import urlencode

import xbmc
import xbmcgui

from . import rows as R
from . import window as W

BUTTONS, SEASONS, EPISODES, CAST, RELATED = 100, 300, 310, 400, 500
ZONES = {BUTTONS: 'buttons', SEASONS: 'seasons', EPISODES: 'episodes', CAST: 'cast', RELATED: 'related'}
SERIES_TYPES = ('series', 'episode', 'tv', 'show', 'anime', 'tvshow')
TMDB_IMG = 'https://image.tmdb.org/t/p/'
_DETAIL_TTL = 24 * 3600
_SEASON_TTL = 12 * 3600
# v5.10.194: a server's movie or show (Plex, Emby, Jellyfin, Silo) opens on
# the title page too; Play keeps the server's own route (direct from the
# server when Library playback says so), Sources searches as for any title
SERVER_PLAY = frozenset(('plex_play', 'emby_play', 'jellyfin_play', 'silo_play'))
_SERVER_MAPS = {}           # route -> (time, {(season, episode): play path})
_SERVER_TTL = 15 * 60


_GENERIC_SEASON = re.compile(r'^\s*(season|series|staffel|saison|temporada|stagione|seizoen|sezon|'
                             r'الموسم|موسم|الجزء)\s*[0-9\u0660-\u0669]+\s*$', re.I)


def season_label(tr, number, name='', show=''):
    """v5.10.208: a season's name on the title pages: "Season 1: Murder
    House" when TMDb gives the season a name of its own (American Horror
    Story, Fargo...), "Season 1" otherwise. Settings, Look: Season names."""
    if number == 0:
        return tr('حلقات خاصة')
    plain = tr('الموسم %s') % number
    name = str(name or '').strip()
    if not name or _GENERIC_SEASON.match(name) or name.lower() in ('specials', 'extras'):
        return plain
    try:
        from ..settings_cache import cached_addon
        if (cached_addon().getSetting('season_names') or 'true').lower() == 'false':
            return plain
    except Exception:
        pass
    show = str(show or '').strip()
    if show and name.lower().startswith(show.lower()):
        name = name[len(show):].lstrip(' :-\u2013\u2014') or name
    if name == str(number):
        return plain
    return '%s: %s' % (plain, name)


def _now():
    return time.monotonic()


def _int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def _img(path, size):
    path = str(path or '')
    if not path:
        return ''
    return path if path.startswith('http') else TMDB_IMG + size + path


def server_route(tile):
    """The server route of a server movie or show tile (v5.10.194), or None:
    its play route (a movie), or its seasons page (a show)."""
    tile = tile or {}
    path = tile.get('path') or ''
    if tile.get('kind') != 'work' or tile.get('cw') or tile.get('season_card') or not R.is_dexhub_path(path):
        return None
    params = R.params_of(path)
    media = tile.get('media_type') or ''
    if media == 'movie' and params.get('action') in SERVER_PLAY:
        return params
    if media == 'series' and params.get('action') in R.SERVER_FOLDER_ACTIONS and R.is_show_page(params):
        return params
    return None


def supported(tile):
    """Movies and series from catalogs, search and favourites open here."""
    tile = tile or {}
    if tile.get('kind') != 'work' or tile.get('cw'):
        return False
    media = tile.get('media_type') or ''
    if media not in ('movie', 'series', 'episode'):
        return False
    path = tile.get('path') or ''
    if path and R.is_dexhub_path(path):
        params = R.params_of(path)
        if params.get('action', '') not in W._HELPER_ACTIONS:
            # a server's episode plays from its server; its movies and shows
            # open here (v5.10.194; v5.10.195 also without ids: server_canonical)
            return bool(server_route(tile))
        return bool(params.get('canonical_id') or tile.get('imdb_id') or tile.get('tmdb_id'))
    return bool(tile.get('imdb_id') or tile.get('tmdb_id'))


def server_canonical(tile):
    """The page's id of a server movie or show its server gives no IMDb or
    TMDb id for (v5.10.195): its route, so two such shows never share a page."""
    route = server_route(tile)
    if not route:
        return ''
    import hashlib
    return 'srv:' + hashlib.sha1(_route_key(route).encode('utf-8')).hexdigest()[:16]


def _route_key(params):
    return urlencode(sorted((k, v) for k, v in (params or {}).items() if k != 'ui_seed_key'))


def server_episodes(route, api):
    """{(season, episode): episode tile} of a server show, read from its
    own pages (the seasons, then each season's episodes); kept a few minutes."""
    key = _route_key(route)
    hit = _SERVER_MAPS.get(key)
    if hit and time.time() - hit[0] < _SERVER_TTL:
        return hit[1]
    found = {}

    def take(tiles):
        for tile in tiles:
            if tile.get('media_type') == 'episode' and tile.get('path'):
                number, episode = _int(tile.get('season'), -1), _int(tile.get('episode'), -1)
                if number >= 0 and episode >= 0:
                    found.setdefault((number, episode), {
                        'path': tile['path'], 'title': tile.get('episode_name') or tile.get('title') or '',
                        'plot': tile.get('plot') or '', 'thumb': tile.get('landscape') or tile.get('poster') or ''})
    try:
        tiles, _more = R.capture_tiles(dict(route), api, limit=400)
        take(tiles)
        for tile in tiles:
            path = tile.get('path') or ''
            if not tile.get('season_card') or not R.is_dexhub_path(path):
                continue
            params = R.params_of(path)
            if params.get('action') not in R.SERVER_FOLDER_ACTIONS:
                continue
            try:
                take(R.capture_tiles(params, api, limit=400)[0])
            except Exception:
                xbmc.log('[DexHub] server season read failed:\n%s' % traceback.format_exc(), xbmc.LOGDEBUG)
    except Exception:
        xbmc.log('[DexHub] server show read failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    _SERVER_MAPS[key] = (time.time(), found)
    xbmc.log('[DexHub] server show %s: %d episodes on the server' % (route.get('action'), len(found)), xbmc.LOGINFO)
    return found


class DetailsWindow(W._BaseWindow):
    def __init__(self, *args, **kwargs):
        super(DetailsWindow, self).__init__(*args, **kwargs)
        tile = dict(kwargs.get('tile') or {})
        tile.pop('_src_li', None)
        params = R.params_of(tile.get('path')) if R.is_dexhub_path(tile.get('path') or '') else {}
        media = str(tile.get('media_type') or params.get('media_type') or 'movie').lower()
        self.series = media in SERIES_TYPES
        self.media_type = (params.get('media_type') if params.get('media_type') in ('series', 'anime')
                           else ('series' if self.series else 'movie'))
        self.source_provider_id = params.get('source_provider_id') or ''
        self.server = server_route(tile)
        canonical = params.get('canonical_id') or tile.get('imdb_id') or ''
        if not canonical and tile.get('tmdb_id'):
            canonical = 'tmdb:%s' % tile['tmdb_id']
        if self.series:
            match = re.fullmatch(r'(tt\d+|tmdb:\d+):\d+:\d+', str(canonical))
            if match:
                canonical = match.group(1)
        if not canonical and self.server:
            canonical = server_canonical(tile)
        self.canonical = canonical
        hero = dict(tile)
        hero['kind'] = 'work'
        hero['media_type'] = 'series' if self.series else 'movie'
        if media == 'episode' and tile.get('show'):
            hero['title'] = tile['show']
            hero['show'] = ''
            hero['plot'] = ''
        hero.pop('hint', None)
        hero.pop('label2', None)
        if self.server:
            hero['_server'] = True      # v5.10.198: its server's metadata stays (meta_policy)
        self.hero = hero
        self.want_season = _int(tile.get('season')) if media == 'episode' else -1
        self.want_episode = _int(tile.get('episode')) if media == 'episode' else 0
        self.tmdb = {}
        self.tmdb_id = str(tile.get('tmdb_id') or '')
        self.seasons = []           # [(number, label)]
        self.episodes = {}          # number -> [episode] (None: not loaded yet)
        self.season_at = 0
        self.cast = []
        self.related = []
        self.progress = {}          # (season, episode) -> playback row; (0, 0) the movie
        self.favorite = False
        self.zone = 'buttons'
        self._loaded = False
        self._season_gen = 0
        self._shown_season = None
        self._tmdb_seasons = {}
        self._play_target = None
        # the page keeps its actions on screen while a trailer plays
        self.immersive_on = False

    # ------------------------------------------------------------ lifecycle
    def onInit(self):
        if self._fresh_controls():
            return
        try:
            self._window_id = xbmcgui.getCurrentWindowId()
        except Exception:
            self._window_id = 0
        if self._inited:
            self._mark_shown_again()
            self._reactivated()
            return
        self._inited = True
        self._warm_controls((BUTTONS, SEASONS, EPISODES, CAST, RELATED, W.HERO_PROGRESS, W.SINK, W.NP))
        self._theme()
        self._setup_np_buttons()
        self.prop('dh.kenburns', '1' if self.s.flag('homeui_kenburns', True) else '')
        import os
        self.prop('dh.brand.fanart', os.path.join(self.app.addon_path, 'resources', 'media', 'fanart.jpg'))
        self.prop('dd.series', '1' if self.series else '')
        self.prop('dd.loading', '1')
        self.prop('dd.cast.title', self.tr('طاقم العمل'))
        self.prop('dd.related.title', self.tr('أعمال مشابهة'))
        self.set_zone('buttons')
        self.show_hero(self.hero, animate=False)
        self._fill_buttons()
        self.focus(BUTTONS)
        # Kodi fills a list on its next frame: focus again once it has items
        self.app.scheduler.call_later(self.key + ':focus', 0.15, self._first_focus)
        self.app.fast.submit(self._load, priority=0, key=('details', self.key))
        self.schedule_trailer(self.hero)

    def _first_focus(self):
        if not self._closing and self.focus_id() in (0, W.SINK, BUTTONS):
            self.focus(BUTTONS)

    def _reactivated(self):
        """Back on the page (after playback or another page): progress may have moved."""
        self._covered = False
        self._theme()
        self.app.fast.submit(self._refresh_progress, priority=1, key=('details-progress', self.key))
        self.schedule_trailer(self.hero)

    def on_uncover(self):
        super(DetailsWindow, self).on_uncover()
        self.app.fast.submit(self._refresh_progress, priority=1, key=('details-progress', self.key))

    def on_playback_returned(self):
        self.app.fast.submit(self._refresh_progress, priority=1, key=('details-progress', self.key))

    def default_focus(self):
        return BUTTONS

    def set_zone(self, zone):
        self.zone = zone
        self.prop('dd.zone', zone)

    # --------------------------------------------------------------- loading
    def _load(self):
        started = _now()
        try:
            self._apply_helper_cache()
        except Exception:
            self.app.log('details helper cache failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        try:
            self._load_tmdb()
        except Exception:
            self.app.log('details tmdb failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        self._apply_tmdb()
        if self.series:
            try:
                self._load_episodes()
            except Exception:
                self.app.log('details episodes failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        self._load_progress()
        self._load_favorite()
        self._loaded = True
        if self._closing:
            return
        self._fill_people()
        self._fill_related()
        if self.series:
            self._fill_seasons()
        self._fill_buttons()
        self.prop('dd.loading', '')
        self.app.log('title page ready: %s in %.0f ms' % (self.hero.get('title') or self.canonical,
                                                          (_now() - started) * 1000))
        # an MDbList request may wait its whole timeout: off the fast lane
        self.app.bg.submit(self._load_ratings, priority=3, key=('details-ratings', self.key))

    def _tmdb_lang(self):
        language = self._tmdb_language() or ('ar' if self.s.arabic else 'en')
        try:
            # v5.10.302: Like Nuvio: the page in the profile's TMDb language
            from ..meta_policy import nuvio_details_prefs
            prefs = nuvio_details_prefs()
            if prefs:
                language = str(prefs.get('language') or 'en')
        except Exception:
            pass
        try:
            from .. import tmdb_direct as T
            return T._normalize_nuvio_tmdb_language(language)
        except Exception:
            return 'ar-SA' if language.startswith('ar') else 'en-US'

    def _resolve_tmdb_id(self):
        if str(self.tmdb_id).isdigit():
            return self.tmdb_id
        m = re.match(r'^tmdb[:_](\d+)', self.canonical or '', re.I)
        if m:
            return m.group(1)
        from .. import tmdb_direct as T
        kind = 'tv' if self.series else 'movie'
        imdb = self.hero.get('imdb_id') or ''
        if not imdb:
            m = re.match(r'^(tt\d{5,10})', self.canonical or '')
            imdb = m.group(1) if m else ''
        try:
            from ..auto_metadata import enabled as auto_enabled
            found = T._resolve_tmdb_id(tmdb_id='', imdb_id=imdb, media_type=kind,
                                       title='' if auto_enabled(self.source_provider_id) else self.hero.get('title') or '',
                                       year=self.hero.get('year') or '')
        except Exception:
            found = ''
        return str(found or '')

    def _apply_helper_cache(self):
        from ..meta_policy import source_appearance
        if source_appearance():
            # v5.10.286 (GitHub #7/#17): a thin card (Spotlight, a Nuvio-synced
            # Continue Watching title) fills its absent facts from the caches
            # at once; what the source gave stays
            try:
                from ..auto_metadata import enrich_tile as fill_tile
                fill_tile(self.hero, self._tmdb_lang(), cache_only=True, canonical=self.canonical,
                          provider_id=self.source_provider_id, force=True)
            except Exception:
                pass
            self._supplement_badges(self.hero)
            return
        from ..auto_metadata import enabled as auto_enabled, enrich_tile
        auto = auto_enabled(self.source_provider_id)
        if auto:
            enrich_tile(self.hero, self._tmdb_lang(), cache_only=True,
                        canonical=self.canonical, provider_id=self.source_provider_id)
        """The page's text at once from TMDb Helper's cache (v5.10.105).

        A title TMDb Helper holds in the page's language fills the hero
        (plot, genres, year, rating, runtime, ids) and its studio logo before
        the TMDb request returns; the request then adds cast and related
        titles. A title TMDb Helper does not hold changes nothing.
        """
        from .. import tmdbhelper
        kind = 'tv' if self.series else 'movie'
        hero = self.hero
        imdb = hero.get('imdb_id') or ''
        if not imdb:
            m = re.match(r'^(tt\d{5,10})', self.canonical or '')
            imdb = m.group(1) if m else ''
        tmdb = self.tmdb_id if str(self.tmdb_id).isdigit() else ''
        if not tmdb:
            m = re.match(r'^tmdb[:_](\d+)', self.canonical or '', re.I)
            tmdb = m.group(1) if m else ''
        if not (tmdb or imdb):
            return
        local = tmdbhelper.get_localized_from_db(tmdb_id=tmdb, media_type=kind, imdb_id=imdb,
                                                 language=self._tmdb_lang()) or {}
        if not local:
            return
        if local.get('tmdb_id') and not str(self.tmdb_id).isdigit():
            self.tmdb_id = str(local['tmdb_id'])
            hero['tmdb_id'] = hero.get('tmdb_id') or self.tmdb_id
        if str(local.get('imdb_id') or '').startswith('tt') and not hero.get('imdb_id'):
            hero['imdb_id'] = local['imdb_id']
        from ..meta_policy import keep_server_meta
        keep = auto or keep_server_meta(hero)
        if auto or local.get('complete'):
            if local.get('plot') and (not hero.get('plot') or (self._tmdb_language() and not keep)):
                hero['plot'] = local['plot']
            if local.get('tagline') and not hero.get('tagline'):
                hero['tagline'] = local['tagline']
            if local.get('genres') and not (keep and hero.get('genres')):
                hero['genres'] = list(local['genres'])[:3]
        if not hero.get('year') and local.get('year'):
            hero['year'] = str(local['year'])
        if not hero.get('rating') and local.get('rating'):
            hero['rating'] = local['rating']
        if not hero.get('duration') and local.get('runtime'):
            hero['duration'] = int(local['runtime']) * 60
        if not hero.get('studio_logo'):
            rows = tmdbhelper.get_studio_logos_from_db(tmdb_id=self.tmdb_id if str(self.tmdb_id).isdigit() else '',
                                                       media_type=kind, imdb_id=hero.get('imdb_id') or '',
                                                       limit=3) or []
            self._set_studio(rows)
        self._local_ratings()
        hero.pop('_enriched', None)
        if not self._closing:
            self.show_hero(hero, animate=False)

    def _local_ratings(self, network=False):
        """Ratings with their logos (v5.10.105, ratings.py): TMDb Helper's cache
        and Dex Hub's MDbList memory, and with network=True one MDbList
        request when an MDbList key is set."""
        from ..ui_preferences import enabled
        if not enabled('metadata_badges'):
            return
        from . import ratings as RB
        found = dict(self.hero.get('ratings') or {})
        if network or not found:
            more = RB.collect(tmdb_id=self.tmdb_id if str(self.tmdb_id).isdigit() else '',
                              imdb_id=self.hero.get('imdb_id') or '',
                              media_type='tv' if self.series else 'movie', network=network)
            for key, value in more.items():
                found.setdefault(key, value)
        self.hero['ratings'] = dict((key, found[key]) for key in RB.ORDER if found.get(key))

    def _load_ratings(self):
        """After the page is up: ask MDbList for what the caches lacked."""
        if self._closing:
            return
        before = dict(self.hero.get('ratings') or {})
        try:
            self._local_ratings(network=True)
        except Exception:
            return
        if (self.hero.get('ratings') or {}) != before and not self._closing:
            self.show_hero(self.hero, animate=False)

    def _set_studio(self, rows):
        from ..ui_preferences import enabled
        if not enabled('metadata_badges'):
            return
        try:
            from .. import studio_art
            picked = studio_art.pick(rows, [], limit=1)
        except Exception:
            picked = []
        if picked:
            self.hero['studio_logo'] = picked[0][0]
            self.hero['studio_logo_chip'] = bool(picked[0][1])

    def _load_tmdb(self):
        from .. import tmdb_direct as T
        tmdb = self._resolve_tmdb_id()
        if not tmdb:
            return
        self.tmdb_id = tmdb
        self.hero['tmdb_id'] = self.hero.get('tmdb_id') or tmdb
        kind = 'tv' if self.series else 'movie'
        lang = self._tmdb_lang()
        key = 'dh-details-v3:%s:%s:%s' % (kind, tmdb, lang)   # v5.10.322: with the age rating
        data = T._cache_get(key)
        if data is None:
            data = T._request('/%s/%s' % (kind, tmdb), {
                'language': lang,
                'append_to_response': 'credits,recommendations,external_ids,translations,%s' % (
                    'content_ratings' if kind == 'tv' else 'release_dates'),
            }, timeout=8, max_attempts=2) or {}
            # v5.10.120: an empty answer (a timeout) is asked again after
            # two minutes, not ten: the page's cast and related titles
            T._cache_set(key, data, _DETAIL_TTL if data else 120)
        self.tmdb = data if isinstance(data, dict) else {}

    def _apply_tmdb(self):
        from ..tmdb_direct import certification as T_cert
        data = self.tmdb or {}
        hero = self.hero
        from ..meta_policy import source_appearance
        from ..auto_metadata import enabled as auto_enabled, enrich_tile
        auto = auto_enabled(self.source_provider_id)
        source_hero = dict(hero) if source_appearance() else None
        if data:
            # v5.10.198: a server title keeps its server's plot and genres
            from ..meta_policy import keep_server_meta
            keep = auto or keep_server_meta(hero)
            imdb = str((data.get('external_ids') or {}).get('imdb_id') or '')
            if imdb.startswith('tt') and not hero.get('imdb_id'):
                hero['imdb_id'] = imdb
            overview = str(data.get('overview') or '').strip()
            if auto and not overview:
                from ..tmdb_direct import _localized_overview
                overview = _localized_overview(data, [self._tmdb_lang(), 'en'])
            if overview and (not hero.get('plot') or (self._tmdb_language() and not keep)):
                hero['plot'] = overview
            if data.get('tagline') and not hero.get('tagline'):
                hero['tagline'] = str(data.get('tagline'))
            genres = [str(g.get('name')) for g in (data.get('genres') or []) if g.get('name')]
            if genres and not (keep and hero.get('genres')):
                hero['genres'] = genres[:3]
            if not hero.get('year'):
                date = str(data.get('release_date') or data.get('first_air_date') or '')
                hero['year'] = date[:4]
            # v5.10.225: how many seasons, and whether the show goes on
            if data.get('number_of_seasons'):
                hero['seasons_count'] = _int(data.get('number_of_seasons'))
            if data.get('status') and (data.get('number_of_seasons') or data.get('first_air_date')):
                hero['tv_status'] = str(data.get('status'))
            if not hero.get('mpaa'):
                # v5.10.322 (GitHub #20): PG-13, TV-MA on the meta line
                hero['mpaa'] = T_cert(data, 'tv' if self.series else 'movie')
            if not hero.get('rating') and data.get('vote_average'):
                hero['rating'] = round(float(data.get('vote_average') or 0), 1)
            if not hero.get('duration'):
                runtime = data.get('runtime') or ((data.get('episode_run_time') or [0]) or [0])[0]
                hero['duration'] = _int(runtime) * 60
            if not hero.get('fanart') and data.get('backdrop_path'):
                hero['fanart'] = _img(data.get('backdrop_path'), 'w1280')
            if not hero.get('poster') and data.get('poster_path'):
                hero['poster'] = _img(data.get('poster_path'), 'w500')
            if not hero.get('studio_logo'):
                # v5.10.105: networks first for a series, then the studios
                rows = []
                for company in list(data.get('networks') or []) + list(data.get('production_companies') or []):
                    logo = _img((company or {}).get('logo_path'), 'w300')
                    if logo.lower().endswith('.svg'):
                        logo = logo[:-4] + '.png'
                    rows.append({'name': str((company or {}).get('name') or ''), 'logo': logo})
                self._set_studio(rows)
            try:
                self._local_ratings()
                if not (hero.get('ratings') or {}).get('tmdb') and data.get('vote_average'):
                    from . import ratings as RB
                    found = dict(hero.get('ratings') or {})
                    found['tmdb'] = '%.1f' % float(data.get('vote_average') or 0)
                    hero['ratings'] = dict((key, found[key]) for key in RB.ORDER if found.get(key))
            except Exception:
                pass
            cast = []
            for member in ((data.get('credits') or {}).get('cast') or [])[:24]:
                name = str(member.get('name') or '').strip()
                if not name:
                    continue
                cast.append({'name': name, 'role': str(member.get('character') or '').strip(),
                             'thumb': _img(member.get('profile_path'), 'w185'),
                             'id': str(member.get('id') or '')})
            self.cast = cast
            related = []
            for row in ((data.get('recommendations') or {}).get('results') or [])[:24]:
                tid = str(row.get('id') or '')
                title = str(row.get('title') or row.get('name') or '').strip()
                if not tid or not title:
                    continue
                rmedia = 'series' if (row.get('media_type') == 'tv' or (self.series and row.get('media_type') != 'movie')) else 'movie'
                date = str(row.get('release_date') or row.get('first_air_date') or '')
                related.append(self._tmdb_tile(tid, rmedia, title, row, date))
            self.related = related
        if auto:
            enrich_tile(hero, self._tmdb_lang(), canonical=self.canonical,
                        provider_id=self.source_provider_id)
        # the hero shows what the page knows now (TMDb text, genres, runtime)
        hero.pop('_enriched', None)
        if source_hero is not None and self._nuvio_parity_hero(source_hero):
            self.show_hero(hero, animate=False)
            return
        if source_hero is not None:
            # v5.10.286 (GitHub #7/#17): the source's own text and art stay,
            # but a field the card lacks keeps what TMDb found (it was
            # dropped, so a thin card's page showed no plot, genres or art)
            for field in ('title', 'plot', 'tagline', 'genres', 'year', 'rating', 'duration',
                          'poster', 'fanart', 'landscape', 'clearlogo'):
                if source_hero.get(field):
                    hero[field] = source_hero[field]
            # then the metadata add-ons (AIOMetadata, ...) and TMDb fill what
            # is still absent, by exact id, fill-only
            from ..auto_metadata import complete as auto_complete
            if not auto_complete(hero) or not hero.get('clearlogo'):
                try:
                    enrich_tile(hero, self._tmdb_lang(), canonical=self.canonical,
                                provider_id=self.source_provider_id, force=True)
                except Exception:
                    pass
        self.show_hero(hero, animate=False)

    def _nuvio_parity_hero(self, source_hero):
        """v5.10.302: Like Nuvio: the title page as Nuvio shows it for the
        signed-in profile. The source's own text and art first; with the
        profile's TMDb on, TMDb's backdrop and logo (Artwork), its name, plot
        and genres (Basic info) in the profile's language replace them, the
        poster stays the source's. Returns False without Like Nuvio."""
        from ..meta_policy import nuvio_parity, nuvio_details_prefs
        if not nuvio_parity():
            return False
        hero = self.hero
        for field in ('title', 'plot', 'tagline', 'genres', 'year', 'rating', 'duration',
                      'poster', 'fanart', 'landscape', 'clearlogo'):
            if source_hero.get(field):
                hero[field] = source_hero[field]
        prefs = nuvio_details_prefs()
        if not prefs:
            return True
        try:
            from .. import tmdb_direct as T
            query = dict(tmdb_id=str(self.tmdb_id or hero.get('tmdb_id') or ''),
                         imdb_id=str(hero.get('imdb_id') or ''),
                         media_type='series' if self.series else 'movie',
                         title='', year='', language_override=str(prefs.get('language') or 'en'),
                         nuvio_exact=True)
            found = T.meta_for_cached_only(**query) or T.meta_for(**query) or {}
        except Exception:
            found = {}
        if not found:
            return True
        if prefs.get('use_basic_info', True):
            name = str(found.get('title') or found.get('name') or '').strip()
            if name:
                hero['title'] = name
            overview = str(found.get('overview') or found.get('description') or '').strip()
            if overview:
                hero['plot'] = overview
            genres = [str(g) for g in (found.get('genres') or []) if g]
            if genres:
                hero['genres'] = genres[:3]
        if prefs.get('use_artwork', True):
            fanart = str(found.get('fanart') or '').strip()
            if fanart:
                hero['fanart'] = fanart
                hero['landscape'] = fanart
            logo = str(found.get('clearlogo') or '').strip()
            if logo.lower().endswith('.svg'):
                logo = logo[:-4] + '.png'
            if logo:
                hero['clearlogo'] = logo
        if prefs.get('use_details', True):
            try:
                runtime = int(found.get('runtime') or 0)
            except Exception:
                runtime = 0
            if runtime and not self.series:
                hero['duration'] = runtime * 60
        return True

    def _tmdb_tile(self, tmdb_id, media, title, row, date):
        canonical = 'tmdb:%s' % tmdb_id
        path = 'plugin://plugin.video.dexhub/?' + urlencode({
            'action': 'item_open', 'media_type': media, 'canonical_id': canonical,
            'title': title, 'tmdb_id': tmdb_id})
        return {
            'kind': 'work', 'title': title, 'label': title, 'media_type': media,
            'path': path, 'action': 'item_open', 'folder': media == 'series',
            'poster': _img(row.get('poster_path'), 'w342'),
            'fanart': _img(row.get('backdrop_path'), 'w1280'),
            'landscape': _img(row.get('backdrop_path'), 'w780'),
            'plot': str(row.get('overview') or ''), 'year': date[:4],
            'rating': round(float(row.get('vote_average') or 0), 1),
            'tmdb_id': tmdb_id, 'imdb_id': '', 'genres': [], 'shape': 'poster',
        }

    # ------------------------------------------------------------- episodes
    def _addon_meta(self, api):
        """Keep the chosen source first; probe fallbacks together with a budget."""
        from .episode_meta import lookup
        meta, provider = lookup(api, self.media_type, self.canonical, self.source_provider_id)
        if not meta:
            return {}, None
        try:
            from .. import meta_source
            enriched = meta_source.override_meta(provider, self.media_type, meta, cache_only=True)
            # Artwork policy never replaces the authoritative episode IDs.
            if isinstance(enriched, dict):
                meta = dict(enriched, videos=meta['videos'])
        except Exception:
            pass
        return meta, provider

    def _load_episodes(self):
        api = self.app.api()
        if not self.canonical:
            return
        if str(self.canonical).startswith('srv:'):
            meta, provider = {}, None       # only the server knows this show
        else:
            meta, provider = self._addon_meta(api)
        videos = [v for v in (meta.get('videos') or []) if isinstance(v, dict)]
        by_season = {}
        for video in videos:
            try:
                number = int(api._video_season_num(video))
            except Exception:
                number = _int(video.get('season'))
            by_season.setdefault(number, []).append(video)
        if by_season:
            from .episode_meta import unique_episodes
            by_season = {number: unique_episodes(rows, season=number) for number, rows in by_season.items()}
            videos = [video for rows in by_season.values() for video in rows]
            self._remember_series(api, meta, videos, provider)
            ids = self._ids(api)
            for number, rows in by_season.items():
                rows.sort(key=lambda v: _int(v.get('episode') or v.get('number')))
                self.episodes[number] = [self._episode(api, number, v, ids) for v in rows]
        else:
            # no add-on has the episode list: TMDb's seasons, loaded one by one
            for season in (self.tmdb or {}).get('seasons') or []:
                number = _int(season.get('season_number'), -1)
                if number < 0 or not _int(season.get('episode_count')):
                    continue
                self.episodes[number] = None
        route = getattr(self, 'server', None)
        if not self.episodes and route and route.get('action') not in SERVER_PLAY:
            # v5.10.194: neither knows the show: the server's own episodes
            ids = self._ids(api)
            found = server_episodes(route, api)
            for (number, episode) in sorted(found):
                row = found[(number, episode)]
                self.episodes.setdefault(number, []).append(self._episode_row(
                    api, number, episode, '%s:%d:%d' % (self.canonical, number, episode), row.get('title') or '',
                    row.get('plot') or '', row.get('thumb') or '', '', ids))
        numbers = sorted(n for n in self.episodes if n > 0) + sorted(n for n in self.episodes if n <= 0)
        names = {}
        for season in (self.tmdb or {}).get('seasons') or []:
            names[_int(season.get('season_number'), -1)] = str(season.get('name') or '')
        self.seasons = [(number, season_label(self.tr, number, names.get(number), self.hero.get('title')))
                        for number in numbers]

    def _remember_series(self, api, meta, videos, provider):
        """What the classic series page leaves for the next-episode features."""
        try:
            home = xbmcgui.Window(api.WINDOW_ID)
            prefix = api.SERIES_PROP_PREFIX + self.canonical
            home.setProperty(prefix, json.dumps(videos))
            home.setProperty(prefix + '.title', meta.get('name') or self.hero.get('title') or self.canonical)
            home.setProperty(prefix + '.media_type', self.media_type)
            if not home.getProperty(prefix + '.source_provider_id'):
                home.setProperty(prefix + '.source_provider_id',
                                 self.source_provider_id or ((provider or {}).get('id') or ''))
        except Exception:
            pass

    def _ids(self, api):
        try:
            ids = api._merge_seed_ids(api.extract_ids({'id': self.canonical}),
                                      api._get_tmdbh_seed_ids(self.canonical))
        except Exception:
            ids = {}
        ids = dict(ids or {})
        for key in ('tmdb_id', 'imdb_id', 'tvdb_id'):
            if not ids.get(key) and self.hero.get(key):
                ids[key] = self.hero.get(key)
        if not ids.get('tmdb_id') and self.tmdb_id:
            ids['tmdb_id'] = self.tmdb_id
        return ids

    def _episode(self, api, season, video, ids):
        number = _int(video.get('episode') or video.get('number'))
        video_id = str(video.get('id') or ('%s:%s:%s' % (self.canonical, season, number)))
        title = str(video.get('title') or video.get('name') or '').strip()
        return self._episode_row(api, season, number, video_id, title,
                                 str(video.get('overview') or video.get('description') or ''),
                                 str(video.get('thumbnail') or ''),
                                 str(video.get('released') or video.get('firstAired') or '')[:10], ids)

    def _server_path(self, season, episode):
        """The server's own play route of an episode, when the page opened
        from a server show (v5.10.194), or ''."""
        route = getattr(self, 'server', None)
        if not route or route.get('action') in SERVER_PLAY:
            return ''
        try:
            return (server_episodes(route, self.app.api()).get((_int(season), _int(episode))) or {}).get('path', '')
        except Exception:
            return ''

    def _episode_row(self, api, season, number, video_id, title, plot, thumb, aired, ids):
        source = self.source_provider_id
        path = self._server_path(season, number)
        try:
            if not path:
                path, _folder = api._content_click_path(
                    media_type=self.media_type, canonical_id=self.canonical, video_id=video_id,
                    season=season, episode=number, title=title or self.hero.get('title') or '',
                    tmdb_id=ids.get('tmdb_id') or '', imdb_id=ids.get('imdb_id') or '',
                    tvdb_id=ids.get('tvdb_id') or '', source_provider_id=source, force_dexhub=True)
        except Exception:
            path = ''
        try:
            menu = api._build_source_picker_menu(
                media_type=self.media_type, canonical_id=self.canonical, title=title, season=season,
                episode=number, video_id=video_id, source_provider_id=source)
        except Exception:
            menu = []
        return {'season': season, 'episode': number, 'title': title, 'plot': plot, 'thumb': thumb,
                'aired': aired, 'video_id': video_id, 'path': path, 'menu': [list(m) for m in menu]}

    def _tmdb_season(self, number):
        """TMDb's season (names, plots, pictures in the user's language), cached."""
        if number in self._tmdb_seasons:
            return self._tmdb_seasons[number]
        out = self._helper_season(number) if self.tmdb_id else {}
        if out:
            self._tmdb_seasons[number] = out
            return out
        if self.tmdb_id:
            from .. import tmdb_direct as T
            lang = self._tmdb_lang()
            key = 'dh-season:%s:%s:%s' % (self.tmdb_id, number, lang)
            data = T._cache_get(key)
            if data is None:
                try:
                    data = T._request('/tv/%s/season/%s' % (self.tmdb_id, number),
                                      {'language': lang}, timeout=8, max_attempts=2) or {}
                except Exception:
                    data = {}
                T._cache_set(key, data, _SEASON_TTL if data else 600)
            for row in (data or {}).get('episodes') or []:
                out[_int(row.get('episode_number'))] = row
        self._tmdb_seasons[number] = out
        return out

    def _helper_season(self, number):
        """The season from TMDb Helper's cache when it holds every episode
        the page needs, in the page's language (v5.10.105): no TMDb request."""
        try:
            from .. import tmdbhelper
            local = tmdbhelper.get_season_episodes_from_db(self.tmdb_id, number, self._tmdb_lang()) or {}
        except Exception:
            return {}
        if not local:
            return {}
        rows = self.episodes.get(number)
        if rows:
            if not {row.get('episode') for row in rows}.issubset(set(local)):
                return {}
        else:
            count = 0
            for season in (self.tmdb or {}).get('seasons') or []:
                if _int(season.get('season_number'), -1) == number:
                    count = _int(season.get('episode_count'))
            if not count or len(local) < count:
                return {}
        return local

    def _season_rows(self, number):
        """The season's episodes, TMDb's language and pictures merged in."""
        rows = self.episodes.get(number)
        if rows is not None:
            from .episode_meta import unique_episodes
            rows = self.episodes[number] = unique_episodes(rows, season=number)
        from .. import episode_titles as ET
        # v5.10.214: TMDb also names the episodes a source only numbers
        unnamed = rows is not None and ET.mode() != 'off' and any(ET.generic(r.get('title')) for r in rows)
        # v5.10.323: an episode without its own picture (none, or the show's
        # backdrop or one picture for the whole season) takes TMDb's still
        stale = set()
        if rows:
            hero = self.hero or {}
            show_art = set(v for v in (hero.get('fanart'), hero.get('landscape'), hero.get('poster'),
                                       hero.get('clearlogo')) if v)
            seen = {}
            for r in rows:
                if r.get('thumb'):
                    seen[r['thumb']] = seen.get(r['thumb'], 0) + 1
            stale = set(t for t, n in seen.items() if n > 1 or t in show_art)
        bare = rows is not None and any((not r.get('thumb')) or r.get('thumb') in stale for r in rows)
        tmdb = self._tmdb_season(number) if (self.tmdb_id and (rows is None or unnamed or bare or self._tmdb_language())) else {}
        if rows is None:
            api = self.app.api()
            ids = self._ids(api)
            rows = []
            for ep in sorted(tmdb):
                row = tmdb[ep]
                rows.append(self._episode_row(
                    api, number, ep, '%s:%s:%s' % (self.canonical, number, ep),
                    str(row.get('name') or ''), str(row.get('overview') or ''),
                    _img(row.get('still_path'), 'w780'), str(row.get('air_date') or ''), ids))
            self.episodes[number] = rows
            return rows
        for row in rows:
            extra = tmdb.get(row['episode'])
            if not extra:
                continue
            if extra.get('name') and not ET.generic(extra['name']):
                row['title'] = str(extra['name'])
            if extra.get('overview'):
                row['plot'] = str(extra['overview'])
            if extra.get('still_path') and (not row.get('thumb') or row.get('thumb') in stale):
                row['thumb'] = _img(extra['still_path'], 'w780')
            if extra.get('runtime') and not row.get('runtime'):
                row['runtime'] = _int(extra['runtime'])
            if not row.get('aired'):
                row['aired'] = str(extra.get('air_date') or '')
        return rows

    # -------------------------------------------------------------- progress
    def _load_progress(self):
        out = {}
        try:
            from ..dexhub import playback_store as P
            rows = P.list_recent_items(limit=2000, include_watched=True)
        except Exception:
            rows = []
        imdb = str(self.hero.get('imdb_id') or '')
        tmdb = str(self.tmdb_id or self.hero.get('tmdb_id') or '')
        for row in rows:
            same = (row.get('canonical_id') == self.canonical
                    or (imdb and str(row.get('imdb_id') or '') == imdb)
                    or (tmdb and tmdb in (str(row.get('tmdb_id') or ''), str(row.get('show_tmdb_id') or ''))))
            if not same:
                continue
            if self.series:
                if row.get('media_type') not in ('series', 'episode', 'anime', 'tv', 'show'):
                    continue
                key = (_int(row.get('season')), _int(row.get('episode')))
            else:
                if row.get('media_type') not in ('movie', 'movies', ''):
                    continue
                key = (0, 0)
            if key not in out:          # newest first
                out[key] = row
        if self.series:
            # v5.10.208: episodes watched anywhere Trakt heard of are ticked too
            try:
                from .. import trakt
                canonical = str(self.canonical or '')
                for season, episode in trakt.watched_episodes(
                        imdb_id=imdb or (canonical if canonical.startswith('tt') else ''), tmdb_id=tmdb):
                    if (season, episode) not in out or not self._watched(out[(season, episode)]):
                        if (season, episode) in out and self._percent(out[(season, episode)]) > 0:
                            continue        # watched again here and stopped half way: the resume wins
                        out[(season, episode)] = {'media_type': 'series', 'season': season, 'episode': episode,
                                                  'percent': 100.0, 'event_type': 'watched',
                                                  'provider_name': 'Trakt', 'canonical_id': self.canonical,
                                                  'video_id': '%s:%s:%s' % (self.canonical, season, episode),
                                                  # the latest episode leads among these; anything
                                                  # played here (a real time) leads over all of them
                                                  'updated_at': season * 1000 + episode, '_remote': True}
            except Exception:
                pass
        self.progress = out

    def _refresh_progress(self):
        if not self._loaded or self._closing:
            return
        self._load_progress()
        self._load_favorite()
        self._fill_buttons()
        if self.series and self._shown_season is not None:
            self._show_season(self.season_at, force=True)

    def _load_favorite(self):
        try:
            from .. import favorites_store as F
            self.favorite = bool(F.is_favorite(self.media_type if self.series else 'movie', self.canonical))
        except Exception:
            self.favorite = False

    @staticmethod
    def _percent(row):
        try:
            return float((row or {}).get('percent') or 0)
        except Exception:
            return 0.0

    def _watched(self, row):
        return bool(row) and (self._percent(row) >= 90 or (row.get('event_type') == 'watched'))

    def _next_episode(self):
        """(season, episode row) to play: the one in progress, else the one after the last watched."""
        if not self.seasons:
            return None, None
        best, best_at = None, -1
        for (season, episode), row in self.progress.items():
            stamp = _int(row.get('updated_at'))
            if stamp > best_at:
                best, best_at = (season, episode, row), stamp
        order = [n for n, _label in self.seasons if n > 0] or [n for n, _label in self.seasons]
        if best is not None:
            season, episode, row = best
            if not self._watched(row) and self._percent(row) > 0:
                return season, {'season': season, 'episode': episode}
            # the episode after the last watched one
            return self._after(season, episode, order)
        if not order:
            return None, None
        rows = self.episodes.get(order[0])
        return order[0], (rows[0] if rows else {'season': order[0], 'episode': 1})

    def _after(self, season, episode, order):
        rows = self.episodes.get(season)
        if rows:
            for row in rows:
                if row['episode'] > episode:
                    return season, row
        later = [n for n in order if n > season]
        if later:
            return later[0], None
        return season, {'season': season, 'episode': episode}

    # ------------------------------------------------------------- rendering
    def _fill_buttons(self):
        items, keys = [], []

        def add(key, label, icon, icon_on='', on=False):
            li = xbmcgui.ListItem(label=label)
            li.setProperty('primary', '1' if key == 'play' else '')
            li.setProperty('icon', self.app.media_path(icon))
            li.setProperty('icon_on', self.app.media_path(icon_on or icon))
            if on:
                li.setProperty('on', '1')
            items.append(li)
            keys.append(key)
        play_label, status = self.tr('تشغيل'), ''
        if self.series:
            season, row = self._next_episode() if self._loaded else (None, None)
            if season is not None and row:
                progress = self.progress.get((season, row.get('episode')))
                code = 'S%dE%d' % (season, _int(row.get('episode')))
                if progress and not self._watched(progress) and self._percent(progress) > 0:
                    play_label = self.tr('استئناف %s') % code
                    status = self._left_line(progress, code)
                else:
                    play_label = self.tr('تشغيل %s') % code
                self._play_target = (season, row.get('episode'))
            else:
                self._play_target = None
        else:
            progress = self.progress.get((0, 0))
            if progress and not self._watched(progress) and self._percent(progress) > 0:
                play_label = self.tr('استئناف')
                status = self._left_line(progress, '')
            elif not progress and int(self.hero.get('progress') or 0) > 0:
                play_label = self.tr('استئناف')     # the tile's own progress (the hero shows what is left)
        add('play', play_label, 'btn_play.png')
        # v5.10.167: skin.dexhub's buttons (Sources short, Track before More)
        add('sources', 'المصادر' if self.s.arabic else self.tr('مصادر التشغيل'), 'btn_sources.png')
        if self.trailers_on or True:
            add('trailer', self.tr('التريلر'), 'btn_trailer.png')
        # a filled star once saved (the label stays short)
        add('favorite', self.tr('المفضلة'), 'btn_fav.png', 'btn_fav_on.png', on=self.favorite)
        add('track', self.tr('تتبّع'), 'btn_track.png')
        add('more', self.tr('المزيد'), 'btn_more.png')
        with self._lock:
            ctrl = self._control(BUTTONS)
            try:
                pos = int(ctrl.getSelectedPosition())
            except Exception:
                pos = 0
            self._set_items(BUTTONS, items, keys)
            if 0 < pos < len(items):
                try:
                    ctrl.selectItem(pos)
                except Exception:
                    pass
        self.prop('dd.status', status)

    def _left_line(self, row, code):
        try:
            left = int((float(row.get('duration') or 0) - float(row.get('position') or 0)) // 60)
        except Exception:
            left = 0
        parts = [code] if code else []
        if left > 0:
            parts.append(self.tr('متبقي %d دقيقة') % left)
        return '  •  '.join(parts)

    def _fill_seasons(self):
        items, keys = [], []
        for number, label in self.seasons:
            li = xbmcgui.ListItem(label=label)
            items.append(li)
            keys.append(str(number))
        with self._lock:
            self._set_items(SEASONS, items, keys)
        if not self.seasons:
            self.prop('dd.eps.status', self.tr('لا توجد حلقات لهذا المسلسل في مصادر البيانات'))
            return
        numbers = [n for n, _l in self.seasons]
        start = 0
        if self.want_season in numbers:
            start = numbers.index(self.want_season)
        else:
            season, _row = self._next_episode()
            if season in numbers:
                start = numbers.index(season)
        try:
            self._control(SEASONS).selectItem(start)
        except Exception:
            pass
        self._show_season(start)

    def _show_season(self, index, force=False):
        if not self.seasons:
            return
        index = max(0, min(index, len(self.seasons) - 1))
        number = self.seasons[index][0]
        if number == self._shown_season and not force:
            return
        self.season_at = index
        self._shown_season = number
        self._season_gen += 1
        gen = self._season_gen
        cached = self.episodes.get(number)
        if cached is None or (self.tmdb_id and self._tmdb_language() and number not in self._tmdb_seasons):
            self.prop('dd.eps.status', self.tr('جاري تحميل الحلقات…'))
            self.clear_list(EPISODES)

            def job():
                rows = self._season_rows(number)
                if gen == self._season_gen and not self._closing:
                    self._render_episodes(number, rows)
            self.app.fast.submit(job, priority=0, key=('season', self.key, number))
            return
        self._render_episodes(number, self._season_rows(number))

    def _render_episodes(self, number, rows):
        from .. import episode_titles as ET
        items = []
        select = 0
        target = None
        if self.want_season == number and self.want_episode:
            target = self.want_episode
        else:
            season, row = self._next_episode()
            if season == number and row:
                target = row.get('episode')
        for index, row in enumerate(rows or []):
            progress = self.progress.get((number, row['episode']))
            label = ET.list_label(self.tr, row['episode'], row.get('title'),
                                  started=bool(progress and (self._watched(progress) or self._percent(progress) > 0)))
            li = xbmcgui.ListItem(label=label)
            thumb = row.get('thumb') or self.hero.get('landscape') or self.hero.get('fanart') or ''
            li.setArt({'thumb': thumb, 'landscape': thumb})
            sub = []
            if row.get('aired'):
                sub.append(row['aired'])
            if row.get('runtime'):
                sub.append(self.tr('%d دقيقة') % row['runtime'])
            li.setProperty('sub', '  •  '.join(sub))
            li.setProperty('plot', row.get('plot') or '')
            progress = self.progress.get((number, row['episode']))
            if self._watched(progress):
                li.setProperty('watched', '1')
            elif progress and self._percent(progress) > 0:
                try:
                    li.getVideoInfoTag().setResumePoint(self._percent(progress), 100.0)
                except Exception:
                    pass
            if target and row['episode'] == target:
                select = index
            items.append(li)
        with self._lock:
            self._set_items(EPISODES, items, [str(r['episode']) for r in rows or []])
            if items and select:
                try:
                    self._control(EPISODES).selectItem(select)
                except Exception:
                    pass
        self.prop('dd.eps.status', '' if items else self.tr('لا توجد حلقات في هذا الموسم'))

    def _fill_people(self):
        items = []
        for person in self.cast:
            li = xbmcgui.ListItem(label=person['name'], label2=person.get('role') or '')
            if person.get('thumb'):
                li.setArt({'thumb': person['thumb']})
            items.append(li)
        with self._lock:
            self._set_items(CAST, items, [p.get('id') or '' for p in self.cast])

    def _fill_related(self):
        items = []
        for tile in self.related:
            li = xbmcgui.ListItem(label=tile['title'])
            li.setArt({'poster': tile.get('poster') or '', 'thumb': tile.get('poster') or ''})
            items.append(li)
        with self._lock:
            self._set_items(RELATED, items, [t.get('tmdb_id') or '' for t in self.related])

    # --------------------------------------------------------------- actions
    def _selected_episode(self):
        if self._shown_season is None:
            return None
        rows = self.episodes.get(self._shown_season) or []
        pos = self.selected_pos(EPISODES, 0)
        return rows[pos] if 0 <= pos < len(rows) else None

    def _episode_for(self, season, episode):
        for row in self.episodes.get(season) or []:
            if row['episode'] == episode:
                return row
        return None

    def _play_episode(self, row, choose=False):
        if not row:
            return
        if choose:
            command = next((m[1] for m in row.get('menu') or [] if m and len(m) > 1), '')
            if command:
                self.run_builtin(command)
                return
        if not row.get('path'):
            self.notify(self.tr('تعذر تشغيل هذه الحلقة'))
            return
        tile = dict(self.hero)
        tile.update({'path': row['path'], 'folder': False, 'media_type': 'episode',
                     'season': str(row['season']), 'episode': str(row['episode'])})
        self.open_path(tile, helper=False)

    def _play(self):
        if not self.series:
            tile = dict(self.hero)
            tile['folder'] = False
            params = R.params_of(tile.get('path'))
            if params.get('action') == 'item_open' or not tile.get('path'):
                try:
                    api = self.app.api()
                    tile['path'], _folder = api._content_click_path(
                        media_type='movie', canonical_id=self.canonical, title=self.hero.get('title') or '',
                        tmdb_id=self.hero.get('tmdb_id') or '', imdb_id=self.hero.get('imdb_id') or '',
                        source_provider_id=self.source_provider_id, force_dexhub=True)
                except Exception:
                    pass
            self.open_path(tile, helper=False)
            return
        if not self._loaded:
            self.notify(self.tr('لحظة، جاري تحميل الحلقات…'))
            return
        target = getattr(self, '_play_target', None)
        row = self._episode_for(*target) if target else None
        if row is None and target:
            rows = self._season_rows(target[0])
            row = next((r for r in rows if r['episode'] == target[1]), None) or (rows[0] if rows else None)
        if row is None:
            row = self._selected_episode()
        self._play_episode(row)

    def _sources(self):
        if self.series:
            row = self._selected_episode()
            if row is None and getattr(self, '_play_target', None):
                row = self._episode_for(*self._play_target)
            self._play_episode(row, choose=True)
            return
        try:
            api = self.app.api()
            menu = api._build_source_picker_menu(media_type='movie', canonical_id=self.canonical,
                                                 title=self.hero.get('title') or '',
                                                 source_provider_id=self.source_provider_id)
        except Exception:
            menu = []
        command = next((m[1] for m in menu if m and len(m) > 1), '')
        if command:
            self.run_builtin(command)
        else:
            self._play()

    def _track(self):
        """The tracking panel (status, rating, lists) for this title, as
        skin.dexhub's Track button opens it (v5.10.167)."""
        from .. import tracking, tracking_ui
        hero = self.hero or {}
        work = tracking.Work('series' if self.series else 'movie', canonical=self.canonical,
                             tmdb=self.tmdb_id or hero.get('tmdb_id') or '', imdb=hero.get('imdb_id') or '',
                             title=hero.get('title') or '', year=hero.get('year') or 0,
                             poster=hero.get('poster') or '', fanart=hero.get('fanart') or '',
                             clearlogo=hero.get('clearlogo') or '', plot=hero.get('plot') or '')
        try:
            tracking_ui.open_panel(work)
        except Exception:
            self.app.log('track panel failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    def _toggle_favorite(self):
        from .. import favorites_store as F
        media = self.media_type if self.series else 'movie'
        try:
            if self.favorite:
                F.remove(media, self.canonical)
                self.favorite = False
                self.notify(self.tr('حُذف من المفضلة'))
            else:
                F.add(media, self.canonical, self.hero.get('title') or '', poster=self.hero.get('poster') or '',
                      background=self.hero.get('fanart') or '', clearlogo=self.hero.get('clearlogo') or '',
                      year=_int(self.hero.get('year')), plot=self.hero.get('plot') or '')
                self.favorite = True
                self.notify(self.tr('أُضيف للمفضلة'))
        except Exception:
            self.app.log('favorite failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        self._fill_buttons()

    def _set_watched(self, row, watched):
        """Mark a movie or an episode watched (or not) in Dex Hub's own progress."""
        from ..dexhub import playback_store as P
        if self.series:
            if not row:
                return
            key = (row['season'], row['episode'])
            video_id, season, episode, title = row['video_id'], row['season'], row['episode'], row.get('title') or ''
            media = 'series'
        else:
            key = (0, 0)
            video_id, season, episode, title = self.canonical, 0, 0, self.hero.get('title') or ''
            media = 'movie'
        existing = self.progress.get(key)
        try:
            if watched:
                P.upsert_entry(media, self.canonical, (existing or {}).get('video_id') or video_id,
                               title if not self.series else (self.hero.get('title') or title), '',
                               self.hero.get('poster') or '', self.hero.get('fanart') or '',
                               self.hero.get('clearlogo') or '', season, episode,
                               float((existing or {}).get('duration') or 0), float((existing or {}).get('duration') or 0),
                               100.0, '', 'watched', tmdb_id='' if self.series else (self.tmdb_id or ''),
                               imdb_id='' if self.series else (self.hero.get('imdb_id') or ''),
                               show_tmdb_id=self.tmdb_id if self.series else '')
                from .. import progress_events
                progress_events.changed()
                # v5.10.171: Floppy hears it too (playback reaches it through
                # the Floppy service add-on; a mark by hand never played)
                try:
                    from .. import floppy_watched
                    canon = str(self.canonical or '')
                    floppy_watched.push_watched(
                        media, title=(self.hero.get('title') or title) if not self.series else title,
                        imdb=self.hero.get('imdb_id') or (canon.split(':')[0] if canon.startswith('tt') else ''),
                        tmdb=self.tmdb_id or '', series_title=(self.hero.get('title') or '') if self.series else '',
                        season=season, episode=episode,
                        duration=float((existing or {}).get('duration') or 0))
                except Exception:
                    pass
            elif existing:
                P.delete_entry(existing.get('media_type') or media, existing.get('canonical_id') or self.canonical,
                               existing.get('video_id') or video_id)
                from .. import progress_events
                progress_events.changed()
        except Exception:
            self.app.log('watched failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        self._load_progress()
        self._fill_buttons()
        if self.series:
            self._show_season(self.season_at, force=True)

    def _more(self):
        from .options import choose
        entries = []
        row = self._selected_episode() if self.series else None
        if self.series and row is None and getattr(self, '_play_target', None):
            row = self._episode_for(*self._play_target)
        key = (row['season'], row['episode']) if row else (0, 0)
        watched = self._watched(self.progress.get(key))
        if self.series and row:
            code = 'S%dE%d' % (row['season'], row['episode'])
            entries.append((self.tr('تعليم %s كغير مشاهدة') % code if watched else self.tr('تعليم %s كمشاهدة') % code,
                            'unwatch' if watched else 'watch'))
        elif not self.series:
            entries.append((self.tr('تعليم كغير مشاهد') if watched else self.tr('تعليم كمشاهد'),
                            'unwatch' if watched else 'watch'))
        from ..play_with import from_tile, command as player_command
        play_tile = dict(self.hero, canonical_id=self.canonical, source_provider_id=self.source_provider_id)
        if row:
            play_tile.update(path=row.get('path') or '', season=row['season'], episode=row['episode'], media_type='episode', show=self.hero.get('title') or '')
        entries.insert(0, (self.tr('التشغيل باستخدام…'), 'play_with'))
        if self.series:
            entries.append((self.tr('الصفحة الكلاسيكية'), 'classic'))
        choice = choose(self.hero.get('title') or '', [e[0] for e in entries], subtitle=self.tr('المزيد'))
        if choice < 0:
            return
        key = entries[choice][1]
        if key == 'play_with':
            self._launch(player_command(from_tile(play_tile)))
        elif key in ('watch', 'unwatch'):
            self._set_watched(row, key == 'watch')
        elif key == 'classic':
            tile = dict(self.hero)
            if self.series:
                try:
                    api = self.app.api()
                    tile['path'], _folder = api._content_click_path(
                        media_type=self.media_type, canonical_id=self.canonical, title=self.hero.get('title') or '',
                        source_provider_id=self.source_provider_id, force_dexhub=True)
                    tile['folder'] = True
                except Exception:
                    pass
            self.open_path(tile, helper=False)

    def _person(self):
        pos = self.selected_pos(CAST, -1)
        if not 0 <= pos < len(self.cast):
            return
        person = self.cast[pos]
        if not person.get('id'):
            return
        self.app.open_grid({'action': 'people_filmography', 'person_id': person['id'],
                            'name': person['name']}, title=person['name'], shape='poster')

    def _open_related(self):
        pos = self.selected_pos(RELATED, -1)
        if 0 <= pos < len(self.related):
            self.app.open_details(self.related[pos])

    def _episode_menu(self):
        from .options import choose
        row = self._selected_episode()
        if not row:
            return
        code = 'S%dE%d' % (row['season'], row['episode'])
        watched = self._watched(self.progress.get((row['season'], row['episode'])))
        entries = [(self.tr('تشغيل'), 'play'), (self.tr('التشغيل باستخدام…'), 'play_with'), (self.tr('اختيار المصدر'), 'sources'),
                   (self.tr('تعليم كغير مشاهدة') if watched else self.tr('تعليم كمشاهدة'),
                    'unwatch' if watched else 'watch')]
        title = '%s  •  %s' % (self.hero.get('title') or '', code)
        choice = choose(title, [e[0] for e in entries], subtitle=row.get('title') or '')
        if choice < 0:
            return
        key = entries[choice][1]
        if key == 'play':
            self._play_episode(row)
        elif key == 'play_with':
            from ..play_with import from_tile, command as player_command
            tile = dict(self.hero, canonical_id=self.canonical, source_provider_id=self.source_provider_id, path=row.get('path') or '', media_type='episode', season=row['season'], episode=row['episode'], show=self.hero.get('title') or '')
            self._launch(player_command(from_tile(tile)))
        elif key == 'sources':
            self._play_episode(row, choose=True)
        else:
            self._set_watched(row, key == 'watch')

    # ---------------------------------------------------------------- events
    def onFocus(self, control_id):
        zone = ZONES.get(control_id)
        if zone and zone != self.zone:
            self.set_zone(zone)

    def onClick(self, control_id):
        if control_id == BUTTONS:
            key = self._selected_key(BUTTONS)
            if key == 'play':
                self._play()
            elif key == 'sources':
                self._sources()
            elif key == 'trailer':
                self.play_trailer_fullscreen(self.hero)
            elif key == 'favorite':
                self._toggle_favorite()
            elif key == 'track':
                self._track()
            elif key == 'more':
                self._more()
        elif control_id == SEASONS:
            self._show_season(self.selected_pos(SEASONS, 0))
            self.focus(EPISODES)
        elif control_id == EPISODES:
            self._play_episode(self._selected_episode())
        elif control_id == CAST:
            self._person()
        elif control_id == RELATED:
            self._open_related()
        elif control_id == W.NP:
            key = self._selected_key(W.NP)
            if key:
                self.player_action(key)

    def onAction(self, action):
        aid = action.getId()
        if aid in W.A_IGNORE:
            return
        if self._back_bounce(aid):
            self.app.log('Back right after the page showed: taken as the same press')
            return
        was_immersive = self._immersive
        self.input_seen()
        if was_immersive:
            self.exit_immersive()
            return
        focus = self.focus_id()
        if aid in W.A_BACK:
            if focus in (CAST, RELATED, EPISODES, SEASONS):
                self.focus(BUTTONS)
                return
            self.close_window()
            return
        if aid in W.A_PLAY and self._real_playing and focus != W.NP:
            return
        if aid in (W.A_CONTEXT, W.A_MENU) and focus == BUTTONS and self._selected_key(BUTTONS) == 'play':
            import xbmc, xbmcgui, xbmcaddon
            from ..tvos_players import choose_once
            if xbmc.getCondVisibility('System.Platform.TVOS'):
                if choose_once(xbmc, xbmcgui, xbmcaddon):
                    self._play()
                return
        if aid in (W.A_CONTEXT, W.A_MENU):
            if focus == EPISODES:
                self._episode_menu()
            elif focus == RELATED:
                pos = self.selected_pos(RELATED, -1)
                if 0 <= pos < len(self.related):
                    self.context_menu(self.related[pos])
            else:
                self._more()
            return
        if focus == SEASONS and aid in (W.A_LEFT, W.A_RIGHT):
            # the tabs move first; the episodes follow a moment later
            self.app.scheduler.call_later(self.key + ':season', 0.25,
                                          lambda: self._show_season(self.selected_pos(SEASONS, 0)))
        if focus in (0, W.SINK):
            self.focus(BUTTONS)
