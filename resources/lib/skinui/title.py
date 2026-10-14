# -*- coding: utf-8 -*-
"""The skin's title page (DialogVideoInfo.xml): its seasons, episodes, cast
and related titles, and the live labels of its buttons.

The page opens at once with the item's own data (Action(Info) on a Dex Hub
item); these listings fill it after. The data is the Python title page's
(homeui/details.py, run here without its window): the episode list from the
title's metadata add-on, TMDb for the cast, the related titles and the text
in the user's language, Dex Hub's playback store for progress. One read of
a title is kept for a few hours (titles/<id>.json); progress is read fresh.

Live labels for the page (Home window properties, tied to dhs.tp.id):
  dhs.tp.play       Play / Resume / Play S1E3 / Resume S1E3
  dhs.tp.status     what is left of the title in progress
  dhs.tp.season     the season to show first (the one being watched)
  dhs.tp.season_idx its place among the seasons; dhs.tp.ep_idx the episode's
  dhs.tp.fav        '1' when the title is a favourite
  dhs.tp.plot       the plot in the user's language
  dhs.tp.meta       the meta line with TMDb's genres and runtime
"""
import time
from .. import lazy_tb as traceback
from urllib.parse import parse_qsl, urlencode

import xbmc

from . import common as C
from . import rows as RW
from ..homeui import rows as R

SERIES_TYPES = ('series', 'episode', 'tv', 'show', 'anime', 'tvshow')
_MEM = {}


_MEM_TITLES = 12             # titles kept in memory (the reused interpreter lives long)
# v5.10.120: a title read without its cast and related titles (TMDb did not
# answer in time, or its id was not found) is read again after a few
# minutes, not kept for TTL_TITLE: on a box such a read kept the page's
# cast and "More like this" away for hours
THIN_TTL = 180.0
# the page's lists ask at the same moment; the ones waiting for the reader
# wait this long (a read took 10 to 11 s on a box, and with 10 s every
# list read the title again, three reads at once making each slower)
READ_WAIT = 45.0


def _fresh(data):
    data = data or {}
    thin = data.get('thin')
    if thin is None:
        # a file written before 5.10.120
        thin = not (data.get('cast') or data.get('related'))
    from ..auto_metadata import enabled, complete
    from ..meta_policy import source_appearance
    # v5.10.286 (GitHub #7/#17): in "Like Nuvio" too, a read whose hero
    # lacks plot or art (one saved while the page dropped TMDb's facts) is
    # read again after THIN_TTL, not kept for hours
    if enabled() or source_appearance():
        thin = thin or not complete(data.get('hero') or {})
    return time.time() - float(data.get('t') or 0) < (THIN_TTL if thin else C.TTL_TITLE)


def _remember(key, data):
    _MEM.pop(key, None)
    _MEM[key] = data
    while len(_MEM) > _MEM_TITLES:
        _MEM.pop(next(iter(_MEM)))


def _int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


TMDB_SEASON_POSTER = 'https://image.tmdb.org/t/p/w342'


def page_art(title_id):
    """The art of the title page on show (its own item), while it is this
    title's (v5.10.110): the seasons and the episodes fall back to it.

    Art stays on list items; it never goes into an add-on address.
    """
    out = {}
    try:
        if not title_id or xbmc.getInfoLabel('ListItem.Property(dhs.id)') != title_id:
            return out
        for key in ('poster', 'fanart', 'landscape', 'thumb'):
            value = xbmc.getInfoLabel('ListItem.Art(%s)' % key)
            if value:
                out[key] = value
    except Exception:
        pass
    return out


class HeadlessTitle(object):
    """homeui DetailsWindow's data side for one title, without a window."""

    _bound = False

    def __init__(self, q):
        RW.app()
        self._bind()
        self.app = RW.app()
        self.s = self.app.settings
        media = str(q.get('m') or 'movie').lower()
        self.series = media in SERIES_TYPES
        self.media_type = media if media in ('series', 'anime') else ('series' if self.series else 'movie')
        self.source_provider_id = q.get('src') or ''
        # v5.10.194: a server show's route (its episodes play from the server)
        self.server = dict(parse_qsl(q['srv'])) if q.get('srv') else None
        canonical = q.get('id') or q.get('imdb') or ''
        if not canonical and q.get('tmdb'):
            canonical = 'tmdb:%s' % q['tmdb']
        self.canonical = canonical
        self.title_id = q.get('i') or C.short_id('%s|%s' % ('series' if self.series else 'movie', canonical))
        self.hero = {'kind': 'work', 'media_type': 'series' if self.series else 'movie',
                     'title': q.get('title') or '', 'year': q.get('year') or '',
                     'imdb_id': q.get('imdb') or '', 'tmdb_id': q.get('tmdb') or '', 'plot': '',
                     'path': ''}
        from .title_seed import seed_hero
        seed_hero(self.hero, q)
        self._seed_source_hero()
        self.want_season = -1
        self.want_episode = 0
        self.tmdb = {}
        self.tmdb_id = str(self.hero.get('tmdb_id') or q.get('tmdb') or '')
        self.seasons = []
        self.episodes = {}
        self.cast = []
        self.related = []
        self.progress = {}
        self.favorite = False
        self._tmdb_seasons = {}
        self._closing = False
        self._loaded = False
        self._play_target = None
        self._episodes_ready = False

    def _seed_source_hero(self):
        from ..meta_policy import source_appearance, server_first
        # v5.10.198: a server title shows its server's own text and art (the
        # card's), the chosen source fills the rest (meta_policy.server_first)
        server = bool(self.server) and server_first()
        if server:
            self.hero['_server'] = True
        if not source_appearance() and not server:
            return
        selected = xbmc.getInfoLabel('ListItem.Property(dhs.id)') or xbmc.getInfoLabel('ListItem.Property(dhs.gid)')
        if not selected or selected != self.title_id:
            return
        info = xbmc.getInfoLabel
        if info('ListItem.DBType') == 'episode' or info('ListItem.Episode'):
            # v5.10.140: an episode card (Continue Watching, Next Up) opens
            # its series' page: the episode's title, plot, still and rating
            # are not the series'. Only the series' name, logo and backdrop.
            value = info('ListItem.TVShowTitle')
            if value:
                self.hero['title'] = value
            for field in ('fanart', 'clearlogo'):
                value = info('ListItem.Art(%s)' % field)
                if value:
                    self.hero[field] = value
            return
        for field, label in (('title', 'Title'), ('plot', 'Plot'), ('year', 'Year'), ('tagline', 'Tagline')):
            value = info('ListItem.' + label)
            if value:
                self.hero[field] = value
        for field in ('poster', 'fanart', 'landscape', 'clearlogo'):
            value = info('ListItem.Art(%s)' % field)
            if value:
                self.hero[field] = value
        genre = info('ListItem.Genre')
        if genre:
            self.hero['genres'] = [value.strip() for value in genre.split(' / ') if value.strip()]
        studio = info('ListItem.Studio')
        if studio:
            self.hero['studio'] = [studio]
        for field, label in (('imdb_id', 'UniqueID(imdb)'), ('tmdb_id', 'UniqueID(tmdb)')):
            value = info('ListItem.' + label)
            if value and not self.hero.get(field):
                self.hero[field] = value
        try:
            score = float(info('ListItem.Rating') or 0)
            if score:
                self.hero['rating'] = score
        except ValueError:
            pass

    @classmethod
    def _bind(cls):
        if cls._bound:
            return
        from ..homeui import details as D
        from ..homeui import window as W
        for name in ('_tmdb_lang', '_resolve_tmdb_id', '_apply_helper_cache', '_local_ratings', '_set_studio',
                     '_load_tmdb', '_apply_tmdb', '_tmdb_tile', '_addon_meta', '_load_episodes',
                     '_remember_series', '_ids', '_episode', '_episode_row', '_server_path', '_tmdb_season', '_helper_season',
                     '_load_progress', '_load_favorite', '_percent', '_watched',
                     '_next_episode', '_left_line'):
            setattr(cls, name, D.DetailsWindow.__dict__[name])
        for name in ('tmdb_settings', '_tmdb_language', '_supplement_badges', '_studio_badge', '_rating_badges'):
            setattr(cls, name, W._BaseWindow.__dict__[name])
        cls._bound = True

    def tr(self, text):
        return self.app.tr(text)

    def show_hero(self, *_args, **_kwargs):
        pass

    # ------------------------------------------------------------ loading
    def _key(self):
        from .. import meta_source
        return 'staged-v3|%s|%s|%s|%s|%s|%s' % (
            self.media_type, self.canonical, self.source_provider_id, self._tmdb_lang(),
            meta_source.get_meta_source_for(self.source_provider_id),
            meta_source.ADDON.getSetting('metadata_generation') or '')

    def load(self, refresh=False):
        """The title's data: from memory, from its file, or read now."""
        key = self._key()
        if not refresh:
            hit = _MEM.get(key)
            if hit and _fresh(hit):
                self._restore(hit)
                return self
            if self._from_file(key):
                return self
        # v5.10.118: the page's lists ask at the same moment; one of them
        # reads the title, the others wait for its file
        asked = time.time()
        with C.file_lock(C.title_file(key), timeout=READ_WAIT):
            if self._from_file(key, since=asked if refresh else 0.0):
                return self
            return self._read(key)

    def _from_file(self, key, since=0.0):
        data = C.read_json(C.title_file(key))
        written = float((data or {}).get('t') or 0)
        if data and _fresh(data) and written >= since:
            _remember(key, data)
            self._restore(data)
            return True
        return False

    def _read(self, key):
        started = time.monotonic()
        steps = []

        def step(name, run):
            began = time.monotonic()
            try:
                run()
            except Exception:
                C.log('title %s failed:\n%s' % (name, traceback.format_exc()), xbmc.LOGWARNING)
            steps.append('%s %.0f' % (name, (time.monotonic() - began) * 1000))

        step('helper cache', self._apply_helper_cache)
        step('tmdb', self._load_tmdb)
        step('tmdb apply', self._apply_tmdb)
        if self.series:
            # Publish the season index without waiting for metadata providers.
            self._seed_seasons()
            if not self.seasons:
                step('episodes', self._load_episodes)
                self._episodes_ready = True
        self._loaded = True
        data = self._snapshot()
        _remember(key, data)
        C.write_json(C.title_file(key), data)
        C.log('title "%s" read in %.0f ms (%s ms; tmdb %s, cast %d, related %d, seasons %d)%s' % (
            self.hero.get('title') or self.canonical, (time.monotonic() - started) * 1000, ', '.join(steps),
            self.tmdb_id or 'none', len(self.cast), len(self.related), len(self.seasons),
            ', read again in %d min' % (THIN_TTL // 60) if data.get('thin') else ''))
        return self

    def _snapshot(self):
        return {
            't': time.time(),
            'episodes_ready': self._episodes_ready,
            'hero': dict((k, v) for k, v in self.hero.items() if not str(k).startswith('_')),
            'tmdb_id': self.tmdb_id,
            'tmdb_seasons': list((self.tmdb or {}).get('seasons') or []),
            'seasons': [list(s) for s in self.seasons],
            'episodes': dict((str(k), v) for k, v in self.episodes.items()),
            'tmdb_season_cache': dict((str(k), dict((str(e), r) for e, r in (v or {}).items()))
                                      for k, v in self._tmdb_seasons.items()),
            'cast': self.cast,
            'related': self.related,
            # read without TMDb's part: kept a few minutes only (THIN_TTL)
            'thin': not (self.cast or self.related),
        }

    def _restore(self, data):
        cached = {k: v for k, v in (data.get('hero') or {}).items()
                  if v not in (None, '', [], 0) or not self.hero.get(k)}
        if self.hero.get('_server'):
            # v5.10.198: a server title's own text and art, seeded from its
            # card, stay over what an older read of the page kept
            for field in ('title', 'plot', 'tagline', 'genres', 'poster', 'fanart', 'landscape', 'clearlogo'):
                if self.hero.get(field):
                    cached.pop(field, None)
        self.hero.update(cached)
        self.tmdb_id = str(data.get('tmdb_id') or self.tmdb_id or '')
        self.tmdb = {'seasons': list(data.get('tmdb_seasons') or [])}
        self.seasons = [tuple(s) for s in data.get('seasons') or []]
        self.episodes = {}
        for key, value in (data.get('episodes') or {}).items():
            self.episodes[_int(key)] = value
        self._tmdb_seasons = {}
        for key, value in (data.get('tmdb_season_cache') or {}).items():
            self._tmdb_seasons[_int(key)] = dict((_int(e), r) for e, r in (value or {}).items())
        self.cast = list(data.get('cast') or [])
        self.related = list(data.get('related') or [])
        self._episodes_ready = bool(data.get('episodes_ready'))
        self._loaded = True

    def _seed_seasons(self):
        for season in (self.tmdb or {}).get('seasons') or []:
            number = _int(season.get('season_number'), -1)
            if number < 0 or not _int(season.get('episode_count')):
                continue
            self.episodes[number] = None
        numbers = sorted(n for n in self.episodes if n > 0) + sorted(n for n in self.episodes if n <= 0)
        names = dict((_int(x.get('season_number'), -1), x.get('name') or '') for x in (self.tmdb or {}).get('seasons') or [])
        from ..homeui.details import season_label
        self.seasons = [(n, season_label(self.tr, n, names.get(n), self.hero.get('title'))) for n in numbers]

    def ensure_episodes(self):
        if not self.series or self._episodes_ready:
            return
        key = self._key()
        # A second episode widget can reuse the first reader's provider IDs.
        with C.file_lock(C.title_file(key) + '.episodes', timeout=READ_WAIT):
            self._from_file(key)
            if self._episodes_ready:
                return
            self._load_episodes()
            self._episodes_ready = True
            self.save()

    def _season_rows(self, number):
        # Keep source-specific video IDs: defer the provider read, never guess them.
        self.ensure_episodes()
        from ..homeui.details import DetailsWindow
        return DetailsWindow._season_rows(self, number)

    def _after(self, season, episode, order):
        if self.episodes.get(season) is None:
            for entry in (self.tmdb or {}).get('seasons') or []:
                if _int(entry.get('season_number'), -1) == season:
                    count = _int(entry.get('episode_count'))
                    if 0 < episode < count:
                        return season, {'season': season, 'episode': episode + 1}
        from ..homeui.details import DetailsWindow
        return DetailsWindow._after(self, season, episode, order)

    def save(self):
        data = self._snapshot()
        _remember(self._key(), data)
        C.write_json(C.title_file(self._key()), data)

    def fresh_state(self):
        """Progress and favourite: read every time (they change with playback)."""
        try:
            self._load_progress()
        except Exception:
            self.progress = {}
        try:
            self._load_favorite()
        except Exception:
            self.favorite = False
        return self

    # ------------------------------------------------------------ the page
    def play_choice(self):
        """(label, status, (season, episode) or None) of the Play button."""
        play_label, status = self.tr('تشغيل'), ''
        target = None
        if self.series:
            season, row = self._next_episode()
            if season is not None and row:
                progress = self.progress.get((season, row.get('episode')))
                code = 'S%dE%d' % (season, _int(row.get('episode')))
                if progress and not self._watched(progress) and self._percent(progress) > 0:
                    play_label = self.tr('استئناف %s') % code
                    status = self._left_line(progress, code)
                else:
                    play_label = self.tr('تشغيل %s') % code
                target = (season, row.get('episode'))
        else:
            progress = self.progress.get((0, 0))
            if progress and not self._watched(progress) and self._percent(progress) > 0:
                play_label = self.tr('استئناف')
                status = self._left_line(progress, '')
        self._play_target = target
        return play_label, status, target

    def publish(self):
        """The page's live labels (see the module's doc)."""
        label, status, target = self.play_choice()
        try:
            from .. import tracking
            track = tracking.button_label('%s|%s' % ('series' if self.series else 'movie', self.canonical))
        except Exception:
            track = ''
        chips = self._show_chips()
        props = {'dhs.tp.play': label, 'dhs.tp.status': status, 'dhs.tp.fav': '1' if self.favorite else '0',
                 'dhs.tp.track': track,
                 'dhs.tp.plot': self.hero.get('plot') or '', 'dhs.tp.meta': self._meta(),
                 'dhs.tp.seasons': chips[0], 'dhs.tp.tvstatus': chips[1], 'dhs.tp.tvstatus_bg': chips[2],
                 'dhs.tp.rating': RW.Context.rating(dict(self.hero, kind='work')),
                 'dhs.tp.clearlogo': self.hero.get('clearlogo') or '',
                 # v5.10.286 (GitHub #7): the page's backdrop and poster when
                 # the card opened it without any (skin DialogVideoInfo.xml)
                 'dhs.tp.fanart': self.hero.get('fanart') or self.hero.get('landscape') or '',
                 'dhs.tp.poster': self.hero.get('poster') or '',
                 'dhs.tp.studio_logo': self.hero.get('studio_logo') or '',
                 'dhs.tp.studio_chip': '1' if self.hero.get('studio_logo_chip') else ''}
        numbers = [n for n, _label in self.seasons]
        season_idx, ep_idx, season = 0, 0, ''
        if numbers:
            want = target[0] if target else numbers[0]
            if want in numbers:
                season_idx = numbers.index(want)
            season = str(numbers[season_idx])
            if target and target[0] == numbers[season_idx]:
                rows = self.episodes.get(numbers[season_idx]) or []
                for index, row in enumerate(rows):
                    if _int(row.get('episode')) == _int(target[1]):
                        ep_idx = index
                        break
        props.update({'dhs.tp.season': season, 'dhs.tp.season_idx': str(season_idx), 'dhs.tp.ep_idx': str(ep_idx)})
        from ..homeui import ratings as RB
        from ..ui_preferences import enabled
        props.update(RB.props(self.hero.get('ratings') if enabled('metadata_badges') else {}, prefix='dhs.tp.ratings.',
                              limit=len(RB.ORDER)))
        C.set_prop('dhs.tp.id', '')
        for key, value in props.items():
            C.set_prop(key, value)
        # the id last: the page takes the labels together, once they are all this title's
        C.set_prop('dhs.tp.id', self.title_id)

    def _meta(self):
        """The page's own meta line, only when it knows more than the card
        (genres or a runtime from TMDb); else the card's line stays."""
        hero = self.hero
        if not (hero.get('genres') or hero.get('duration')):
            return ''
        ctx = RW.Context('all')
        return ctx.meta({'kind': 'work', 'year': hero.get('year') or '', 'rating': hero.get('rating') or 0,
                         'duration': hero.get('duration') or 0, 'genres': hero.get('genres') or [],
                         'mpaa': hero.get('mpaa') or ''})

    def _show_chips(self):
        """v5.10.226: the show's seasons and status as chips beside its badge."""
        if not self.series:
            return '', '', ''
        hero = self.hero
        count = len([n for n, _label in self.seasons if _int(n) > 0]) or _int(hero.get('seasons_count'))
        return RW.show_chips(self.tr, count, hero.get('tv_status'))

    def episodes_label(self, count):
        """'10 episodes' in the user's language (Arabic counts its plurals)."""
        if count <= 0:
            return ''
        if count == 1:
            return self.tr('حلقة واحدة')
        if count == 2:
            return self.tr('حلقتان')
        if count <= 10:
            return self.tr('%d حلقات') % count
        return self.tr('%d حلقة') % count

    def season_items(self):
        """The seasons row: TMDb's season poster, else the title's own (v5.10.110)."""
        page = page_art(self.title_id)
        hero = self.hero
        show_poster = hero.get('poster') or page.get('poster') or ''
        fanart = hero.get('fanart') or page.get('fanart') or ''
        tmdb = {}
        for season in (self.tmdb or {}).get('seasons') or []:
            if isinstance(season, dict):
                tmdb[_int(season.get('season_number'), -1)] = season
        items = []
        for number, label in self.seasons:
            info = tmdb.get(number) or {}
            poster = (TMDB_SEASON_POSTER + info['poster_path']) if info.get('poster_path') else show_poster
            count = len(self.episodes.get(number) or []) or _int(info.get('episode_count'))
            items.append({'label': label, 'label2': self.episodes_label(count), 'path': '', 'folder': False,
                          'art': {'poster': poster, 'thumb': poster, 'fanart': fanart},
                          'info': {'title': label, 'mediatype': 'season', 'season': number,
                                   'tvshowtitle': hero.get('title') or ''},
                          'props': {'season': str(number), 'dhs.id': self.title_id, 'kind': 'season',
                                    'sub': self.episodes_label(count)}})
        return items

    def episode_items(self, number):
        from .. import episode_titles as ET
        rows = self._season_rows(number) or []
        ctx = RW.Context('all')
        hero = self.hero
        page = page_art(self.title_id)
        backdrop = (hero.get('landscape') or hero.get('fanart') or page.get('landscape')
                    or page.get('fanart') or '')
        items = []
        for row in rows:
            progress = self.progress.get((number, _int(row.get('episode'))))
            started = bool(progress and (self._watched(progress) or self._percent(progress) > 0))
            label = ET.list_label(self.tr, _int(row.get('episode')), row.get('title'), started=started)
            thumb = row.get('thumb') or backdrop
            sub = []
            if row.get('aired'):
                sub.append(row['aired'])
            if row.get('runtime'):
                sub.append(self.tr('%d دقيقة') % _int(row['runtime']))
            progress = self.progress.get((number, _int(row.get('episode'))))
            watched = self._watched(progress)
            percent = 0 if watched else int(self._percent(progress)) if progress else 0
            path = row.get('path') or ''
            target = ''
            if path:
                tile = dict(hero)
                tile.update({'path': path, 'media_type': 'episode', 'season': str(number),
                             'episode': str(row.get('episode'))})
                action = R.params_of(path).get('action', '')
                target = ctx.home_route(ctx.with_ui_art(path, action, tile))
            # v5.10.224: a long press on an episode plays it or lists its
            # sources. The page is a dialog: the menu's Container.Update
            # (the add-on's lists) did nothing here, RunPlugin opens them.
            menu = []
            if target or path:
                menu.append([self.tr('▶  تشغيل الحلقة'), 'RunPlugin(%s)' % (target or path)])
            # v5.10.236: Show sources second, always the sources page (the
            # remembered source and quick play never take it over), also for
            # rows read before this version
            picker = ''
            try:
                from ..safe_helpers import _source_picker_url
                picker = _source_picker_url(
                    media_type=getattr(self, 'media_type', '') or 'series', canonical_id=getattr(self, 'canonical', ''),
                    title=row.get('title') or hero.get('title') or '', season=number, episode=row.get('episode'),
                    video_id=row.get('video_id') or '',
                    source_provider_id=getattr(self, 'source_provider_id', '') or '')
            except Exception:
                picker = ''
            if picker and 'force_picker' in picker:
                menu.append([self.tr('عرض الروابط'), 'RunPlugin(%s)' % picker])
            for entry in row.get('menu') or []:
                if isinstance(entry, (list, tuple)) and len(entry) > 1:
                    command = str(entry[1])
                    if picker and 'force_picker' in command and 'action=episode_streams' in command \
                            and 'source_mode' not in command:
                        continue        # the same page as Show sources
                    if command.startswith('Container.Update('):
                        command = 'RunPlugin(' + command[len('Container.Update('):]
                    menu.append([entry[0], command])
            items.append({
                'label': label, 'path': target or path, 'folder': False,
                'art': {'thumb': thumb, 'landscape': thumb, 'fanart': hero.get('fanart') or page.get('fanart') or ''},
                'info': {'title': label.split('  ', 1)[-1] if label.startswith('E') else label, 'mediatype': 'episode', 'plot': row.get('plot') or '',
                         'season': number, 'episode': _int(row.get('episode')),
                         'tvshowtitle': hero.get('title') or ''},
                'progress': percent if 0 < percent < 100 else 0,
                'watched': watched,
                'props': {'sub': '  •  '.join(sub), 'watched': '1' if watched else '',
                          'season': str(number), 'episode': str(row.get('episode')),
                          'dhs.click': 'run' if target else '', 'dhs.target': target},
                'menu': menu,
            })
        return items


def query_of(params):
    return dict((k, v) for k, v in params.items() if k not in ('action', 'season'))


def listing(action, params, handle):
    from . import items as I
    q = query_of(params)
    title = HeadlessTitle(q)
    title.load()
    title.fresh_state()
    out, content = [], 'videos'
    if action == 'skin_seasons':
        content = 'seasons'
        out = title.season_items()
        title.publish()
    elif action == 'skin_episodes':
        content = 'episodes'
        number = _int(params.get('season'), -1)
        if number < 0 and title.seasons:
            number = title.seasons[0][0]
        if number >= 0:
            out = title.episode_items(number)
            title.save()        # a season read from TMDb now is kept
        if not title.seasons:
            title.publish()
    elif action == 'skin_cast':
        content = 'actors'
        for person in title.cast:
            person_id = person.get('id') or ''
            target = RW.list_url({'action': 'people_filmography', 'person_id': person_id,
                                  'name': person.get('name') or ''}, title=person.get('name') or '') if person_id else ''
            out.append({'label': person.get('name') or '', 'label2': person.get('role') or '', 'path': target,
                        'folder': bool(target), 'art': {'thumb': person.get('thumb') or '', 'icon': 'DefaultActor.png'},
                        'props': {'dhs.click': 'grid' if target else '', 'dhs.target': target}})
        if not title.series:
            title.publish()
    elif action == 'skin_related':
        content = 'movies'
        ctx = RW.Context('all')
        for tile in title.related:
            item = ctx.item(dict(tile), {'shape': 'poster'})
            props = item.get('props') or {}
            if props.get('dhs.click') == 'info':
                # another title's page: closing this one first (an info key
                # here would only close it); the page's own query finds the
                # title's data again
                q2 = props.get('dhs.q') or ''
                props['dhs.click'] = 'run'
                props['dhs.target'] = (C.BASE + '?' + urlencode([('action', 'skin_open')]) + '&' + q2 + '&'
                                       + urlencode([('page', urlencode(sorted(q.items())))]))
            item['props'] = props
            out.append(item)
    name = {'skin_seasons': 'seasons', 'skin_episodes': 'episodes', 'skin_cast': 'cast',
            'skin_related': 'related'}.get(action)
    if name and not out:
        # nothing to list: the page's next list loads without waiting for items
        C.set_prop('dhs.tp.none.%s' % name, title.title_id)
    I.directory(handle, out, content=content)
    return None
