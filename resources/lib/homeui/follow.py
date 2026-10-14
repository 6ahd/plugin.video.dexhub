# -*- coding: utf-8 -*-
"""My watching (v5.10.207): what you follow, on one page.

WindowXMLDialog on dexhub_follow*.xml (gen_follow_xml.py):

  100  the sources: All and each linked service (Trakt, Simkl, Nuvio,
       MDBList, Floppy); a chip keeps that service's titles only
  110  All, Movies, Series
  201  Up next: what you left half way and the next episode of each show,
       with the service it came from and the part watched. OK plays.
  301  Watchlist: the linked services' watchlists together, each title
       with the services it is on. OK opens the title.
  401  Coming up: new episodes from yesterday on, by day (Floppy's
       calendar, else Trakt's)
  501  Because you watched: TMDb's picks for the last title you watched
  Menu on a card: open the title, the card's own actions (watched,
  remove...), track it.

The header holds three numbers: this month's watching (Floppy's, else
what Dex Hub played in the last 30 days). Every section reads on its own
thread and shows when it has something; a section with nothing is hidden.
"""
import datetime
import re
import threading
import time
from .. import lazy_tb as traceback
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcgui

CHIPS, KINDS = 100, 110
UP, WATCH, SOON, MORE = 201, 301, 401, 501
SECTIONS = (UP, WATCH, SOON, MORE)
# the sections' heights in dexhub_follow.xml (gen_follow_xml.py), where they start, the gap
HEIGHTS = {UP: 402, WATCH: 462, SOON: 371, MORE: 462}
TOP, GAP = 272, 22
A_UP, A_DOWN, A_PGUP, A_PGDN = 3, 4, 5, 6
A_CONTEXT, A_MENU = 117, 163
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
SERIES = ('series', 'show', 'tv', 'tvshow', 'anime', 'episode', 'season')
SERVICES = (('trakt', 'Trakt'), ('simkl', 'Simkl'), ('nuvio', 'Nuvio'), ('mdblist', 'MDBList'),
            ('floppy', 'Floppy'), ('stremio', 'Stremio'))
SRC_PROP = 'dexhub.follow.src'
KIND_PROP = 'dexhub.follow.media'


def _t(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] follow: %s' % msg, level)


def _int(value):
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _series(media):
    return str(media or '').lower() in SERIES


def _query(media, title, tmdb='', imdb='', canonical=''):
    """The title page's query (skin.dexhub's dhs.q)."""
    canonical = str(canonical or '')
    if not imdb and canonical.startswith('tt'):
        imdb = canonical.split(':', 1)[0]
    if not tmdb and canonical.lower().startswith('tmdb:'):
        tmdb = canonical.split(':')[1]
    q = {'m': 'series' if _series(media) else 'movie', 'title': title or ''}
    if tmdb:
        q['tmdb'] = str(tmdb)
    if imdb:
        q['imdb'] = str(imdb)
    return q if (tmdb or imdb) else {}


def _service(name):
    """A provider name as a chip key ('Trakt' -> 'trakt'), '' for others."""
    low = str(name or '').lower()
    for key, _label in SERVICES:
        if key in low:
            return key
    return ''


_CODE_RE = re.compile(r'^\s*S\d+\s*E\d+', re.I)


def _left(tile):
    """'34 min left' of a card half watched."""
    total, resume = float(tile.get('total') or 0), float(tile.get('resume') or 0)
    if total <= 0 or resume <= 0:
        return ''
    minutes = int(round((total - resume) / 60.0))
    if minutes <= 0:
        return ''
    if minutes >= 60:
        return _t('باقي %d س %d د', '%dh %dm left') % (minutes // 60, minutes % 60)
    return _t('باقي %d د', '%d min left') % minutes


def _code(season, episode):
    if _int(season) <= 0 and _int(episode) <= 0:
        return ''
    return 'S%d · E%02d' % (_int(season), _int(episode))


# ---------------------------------------------------------------- the data
def up_next(app):
    """Continue Watching and Next Up as one list, one card per title."""
    from . import rows as R
    api = app.api()
    out, seen = [], set()
    # the service each title came from, by its id (the routes' paths do not
    # always carry it)
    providers = {}
    try:
        from .. import playback_store
        for row in playback_store.list_recent_items(limit=400):
            for value in (row.get('canonical_id'), row.get('imdb_id'), row.get('tmdb_id'), row.get('show_tmdb_id')):
                value = str(value or '').split(':')[0].lower() if not str(value or '').lower().startswith('tmdb:') \
                    else str(value).lower()
                if value and row.get('provider_name') and value not in providers:
                    providers[value] = row['provider_name']
                if value and row.get('show_title') and ('show', value) not in providers:
                    providers[('show', value)] = row['show_title']
    except Exception:
        pass
    for action, nextup in (('continue', False), ('nextup', True)):
        try:
            tiles, _more = R.progress_tiles(action, api, patience=8.0, limit=40)
        except Exception:
            _log('%s failed:\n%s' % (action, traceback.format_exc()), xbmc.LOGWARNING)
            continue
        for tile in tiles:
            episode = bool(tile.get('episode')) or _series(tile.get('media_type'))
            name = (tile.get('show') if episode else '') or ('' if episode else tile.get('title') or tile.get('label')) or ''
            params = R.params_of(tile.get('path') or '') if R.is_dexhub_path(tile.get('path')) else {}
            if episode and not name:
                # the show's name from the playback store, else no card (an
                # episode name or "S01E02" alone does not say what it is)
                for value in (params.get('canonical_id'), tile.get('imdb_id'), tile.get('tmdb_id')):
                    value = str(value or '').split(':')[0].lower()
                    if value and providers.get(('show', value)):
                        name = providers[('show', value)]
                        break
            if name.lower().startswith('tt') or _CODE_RE.match(name):
                continue
            key = (tile.get('tmdb_id') or tile.get('imdb_id') or name).lower()
            if not name or key in seen:
                continue
            seen.add(key)
            provider = params.get('provider_name') or tile.get('provider') or ''
            if not _service(provider):
                for value in (params.get('canonical_id'), tile.get('imdb_id'), tile.get('tmdb_id')):
                    value = str(value or '').split(':')[0].lower()
                    if value and _service(providers.get(value)):
                        provider = providers[value]
                        break
            src = _service(provider)
            line = []
            code = _code(tile.get('season'), tile.get('episode')) if episode else ''
            if code:
                line.append(code)
            if episode and tile.get('episode_name'):
                line.append(tile['episode_name'])
            elif nextup:
                line.append(_t('الحلقة القادمة', 'Next episode'))
            elif not episode and tile.get('year'):
                line.append(tile['year'])
            badge = dict(SERVICES).get(src, '') or (_t('الحلقة القادمة', 'Up next') if nextup else '')
            out.append({
                'title': name, 'line': '  ·  '.join(line), 'thumb': R.card_landscape(tile),
                'poster': tile.get('poster') or '', 'logo': tile.get('clearlogo') or '',
                'media': 'series' if episode else 'movie', 'srcs': {src} if src else set(),
                'badge': badge, 'prog': tile.get('progress') or 0, 'left': _left(tile),
                'open': _query('series' if episode else 'movie', name, tile.get('tmdb_id'), tile.get('imdb_id')),
                'play': tile.get('path') or '', 'folder': bool(tile.get('folder')),
                'menu': [tuple(m) for m in (tile.get('menu') or []) if len(m) == 2],
                'order': len(out),
            })
    return out


def _setting(key, default=''):
    try:
        from ..settings_cache import cached_addon
        return cached_addon().getSetting(key) or default
    except Exception:
        try:
            import xbmcaddon
            return xbmcaddon.Addon('plugin.video.dexhub').getSetting(key) or default
        except Exception:
            return default


def watchlist():
    from .. import favorites_store
    names = dict(SERVICES)
    merge = _setting('follow_merge', 'true').lower() != 'false'
    order = _setting('library_sort', 'added').strip().lower()
    merged = {}
    for row in favorites_store.list_favorites(limit=0) or []:
        sources = set(str(s).lower() for s in (row.get('sources') or [row.get('source')]) if s) - {'local'}
        if not sources:
            continue
        key = '%s|%s' % ('series' if _series(row.get('media_type')) else 'movie', row.get('canonical_id'))
        if not merge:
            key += '|' + ','.join(sorted(sources))      # one card per service (Following and library)
        have = merged.get(key)
        if have:
            have['srcs'] |= sources
            continue
        canonical = row.get('canonical_id') or ''
        poster = row.get('poster') or (
            'https://images.metahub.space/poster/medium/%s/img' % canonical if canonical.startswith('tt') else '')
        merged[key] = {
            'title': row.get('title') or canonical, 'thumb': row.get('background') or '', 'poster': poster,
            'logo': row.get('clearlogo') or '', 'media': 'series' if _series(row.get('media_type')) else 'movie',
            'srcs': sources, 'year': str(row.get('year') or ''), 'canonical': canonical,
            'open': _query(row.get('media_type'), row.get('title'), canonical=canonical),
        }
    out = list(merged.values())
    if order == 'title':
        out.sort(key=lambda e: (e['title'] or '').lower())
    elif order == 'year':
        out.sort(key=lambda e: -_int(e.get('year')))
    out = out[:80]
    for entry in out:
        ordered = sorted(entry['srcs'])
        entry['badge'] = names.get(ordered[0], ordered[0].title()) + (' +%d' % (len(ordered) - 1)
                                                                       if len(ordered) > 1 else '')
        kind = _t('مسلسل', 'Series') if entry['media'] == 'series' else _t('فيلم', 'Movie')
        entry['line'] = '  ·  '.join(x for x in (entry['year'] if entry['year'] not in ('0',) else '', kind) if x)
    return out


def _day_label(day, today):
    delta = (day - today).days
    if delta == 0:
        return _t('اليوم', 'Today')
    if delta == 1:
        return _t('بكرة', 'Tomorrow')
    if delta == -1:
        return _t('أمس', 'Yesterday')
    from ..routes.library import _day_name
    return _day_name(day)


def known_show_art():
    """{show imdb id or name (lower): (backdrop, poster, logo)} from what Dex
    Hub already holds (favourites, playback), for calendar cards before
    TMDb answers (v5.10.208)."""
    known = {}
    try:
        from .. import favorites_store, playback_store
        for row in list(favorites_store.list_favorites(limit=0) or []) + list(playback_store.list_recent_items(limit=300)):
            if not _series(row.get('media_type')):
                continue
            art = (row.get('background') or '', row.get('poster') or '', row.get('clearlogo') or '')
            if not art[0]:
                continue
            for value in (str(row.get('canonical_id') or '').split(':')[0], row.get('show_title'),
                          row.get('title') if not row.get('season') else ''):
                value = str(value or '').strip().lower()
                if value and value not in known:
                    known[value] = art
    except Exception:
        pass
    return known


def coming_up():
    from ..routes import library
    from .. import floppy_api
    events = library._calendar_events()
    src = 'floppy' if floppy_api.configured() and events else 'trakt'
    known = known_show_art()
    today = datetime.date.today()
    out = []
    for ev in events:
        if ev['date'] < today - datetime.timedelta(days=1):
            continue
        code = 'S%02dE%02d' % (ev['season'], ev['episode'])
        canonical = ev.get('imdb') or ('tmdb:%s' % ev['tmdb'] if ev.get('tmdb') else '')
        art = known.get(str(ev.get('imdb') or '').lower()) or known.get(str(ev.get('show') or '').lower())
        if art and not ev.get('fanart'):
            ev = dict(ev, fanart=art[0], poster=ev.get('poster') or art[1], logo=art[2])
        out.append({
            'title': ev['show'], 'line': '  ·  '.join(x for x in (code, ev.get('title') or '') if x),
            'thumb': ev.get('fanart') or '', 'poster': ev.get('poster') or '', 'logo': ev.get('logo') or '',
            'media': 'series', 'srcs': {src}, 'badge': _day_label(ev['date'], today),
            'hot': ev['date'] == today, 'tmdb': ev.get('tmdb') or '', 'imdb': ev.get('imdb') or '',
            'open': _query('series', ev['show'], ev.get('tmdb'), ev.get('imdb'), canonical),
        })
    return out[:40]


def _tmdb_lang():
    try:
        from ..i18n import is_english
        return 'en-US' if is_english() else 'ar-SA'
    except Exception:
        return 'en-US'


def last_watched(media):
    """(title, media, tmdb id) of the last title played, of ``media`` ('all',
    'movie', 'series')."""
    from .. import playback_store
    from .. import tmdb_direct as T
    for row in playback_store.list_recent_items(limit=60):
        series = _series(row.get('media_type')) or bool(row.get('season'))
        if media == 'movie' and series or media == 'series' and not series:
            continue
        title = (row.get('show_title') if series else '') or ('' if series else row.get('title')) or ''
        if not title or title.startswith('tt'):
            continue
        tmdb = str(row.get('show_tmdb_id') if series else row.get('tmdb_id') or '').strip()
        canonical = str(row.get('canonical_id') or '')
        if not tmdb and canonical.lower().startswith('tmdb:'):
            tmdb = canonical.split(':')[1]
        if not tmdb:
            imdb = row.get('imdb_id') if not series else ''
            imdb = imdb or (canonical.split(':')[0] if canonical.startswith('tt') else '')
            if imdb:
                try:
                    tmdb = str(T._resolve_tmdb_id(imdb_id=imdb, media_type='series' if series else 'movie') or '')
                except Exception:
                    tmdb = ''
        if tmdb.isdigit():
            return title, 'series' if series else 'movie', tmdb
    return None


def last_title(media):
    """The name of the last film or show played (no network), for a row's title."""
    try:
        from .. import playback_store
        for row in playback_store.list_recent_items(limit=60):
            series = _series(row.get('media_type')) or bool(row.get('season'))
            if media == 'movie' and series or media == 'series' and not series:
                continue
            title = (row.get('show_title') if series else row.get('title')) or ''
            if title and not title.startswith('tt') and not _CODE_RE.match(title):
                return title
    except Exception:
        pass
    return ''


def because(media):
    seed = last_watched(media)
    if not seed:
        return '', []
    title, kind, tmdb = seed
    return title, recommendations(kind, tmdb)


def recommendations(kind, tmdb):
    """TMDb's picks for a title (kept a day): My watching's "Because you
    watched" and the end-of-film panel (v5.10.217)."""
    from .. import tmdb_direct as T
    path = '/%s/%s/recommendations' % ('tv' if kind == 'series' else 'movie', tmdb)
    lang = _tmdb_lang()
    cache_key = 'follow_recs:%s:%s' % (path, lang)
    data = None
    try:
        data = T._cache_get(cache_key)
    except Exception:
        data = None
    if not isinstance(data, dict):
        try:
            data = T._request(path, {'language': lang}, timeout=8, max_attempts=1) or {}
        except Exception:
            data = {}
        try:
            # a day: picks change slowly; nothing (or no answer): ask again in half an hour
            T._cache_set(cache_key, data if data.get('results') else {'results': []},
                         86400 if data.get('results') else 1800)
        except Exception:
            pass
    out = []
    for row in (data.get('results') or [])[:24]:
        item = T._row_from_tmdb(row, kind)
        if not item.get('tmdb_id') or not item.get('poster'):
            continue
        out.append({
            'title': item['title'], 'line': item.get('year') or '', 'poster': item['poster'],
            'thumb': item.get('backdrop') or '', 'logo': '', 'media': kind, 'srcs': set(), 'badge': '',
            'open': _query(kind, item['title'], item['tmdb_id']),
            'tmdb': str(item['tmdb_id']),
        })
    return out


def stats():
    """[(value, label)] x3: this month's watching."""
    from .. import floppy_api
    if floppy_api.configured():
        month = floppy_api.month_stats(cache_only=True)
        if month:
            hours = float(month.get('hours') or 0)
            return [(_t('%d س', '%dh') % int(round(hours)), _t('مشاهدة هالشهر', 'watched this month')),
                    (str(month.get('movies') or 0), _t('فيلم', 'movies')),
                    (str(month.get('episodes') or 0), _t('حلقة', 'episodes'))]
    from .. import playback_store
    since = time.time() - 30 * 86400
    seconds, movies, episodes = 0.0, 0, 0
    for row in playback_store.list_recent_items(limit=3000):
        if _int(row.get('updated_at')) < since:
            break
        position, duration = float(row.get('position') or 0), float(row.get('duration') or 0)
        percent = float(row.get('percent') or 0)
        seconds += min(position, duration) if duration > 0 else position
        if percent >= 80:
            if _series(row.get('media_type')) or row.get('season'):
                episodes += 1
            else:
                movies += 1
    if not seconds and not movies and not episodes:
        return []
    return [(_t('%d س', '%dh') % int(round(seconds / 3600.0)), _t('آخر 30 يوم', 'last 30 days')),
            (str(movies), _t('فيلم', 'movies')), (str(episodes), _t('حلقة', 'episodes'))]


def linked():
    """The chips: [(key, label)] of the services linked."""
    out = []
    try:
        from ..routes import library
        names = set(n.lower() for n in library.linked_sources())
    except Exception:
        names = set()
    for key, name in SERVICES:
        if key in names:
            out.append((key, name))
    return out


# ---------------------------------------------------------------- the window
class FollowWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(FollowWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        home = xbmcgui.Window(10000)
        self.src = home.getProperty(SRC_PROP) or 'all'
        self.media = kwargs.get('media') or home.getProperty(KIND_PROP) or 'all'
        if self.media not in ('all', 'movie', 'series'):
            self.media = 'all'
        self.state = kwargs.get('state') if isinstance(kwargs.get('state'), dict) else {}
        self.next_title = None      # a title page to open once this window is closed (open_follow)
        self.data = {}
        self.shown = {}
        self.chips = []
        self.seed = ''
        self._ctrls = {}
        self._inited = False
        self._closing = False
        self._pending = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4)
        self._because_gen = 0
        self._moved = False         # the user moved: the focus stays where they put it

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        for cid in (CHIPS, KINDS) + SECTIONS + tuple(c + 1000 for c in SECTIONS):
            try:
                self._ctrls[cid] = self.getControl(cid)
            except Exception:
                self._ctrls[cid] = None
        self._place(None)
        self.setProperty('fw.crumb', 'Dex Hub  ›  ' + _t('المكتبة', 'Library'))
        self.setProperty('fw.title', _t('متابعتي', 'My watching'))
        self.setProperty('fw.hint', _t('موافق: تشغيل أو فتح العمل   •   القائمة: خيارات العمل   •   رجوع: إغلاق',
                                       'OK plays or opens   •   Menu: title options   •   Back: close'))
        self.setProperty('fw.%d.title' % UP, _t('تابع من حيث وقفت', 'Pick up where you left off'))
        self.setProperty('fw.%d.title' % WATCH, _t('قائمة المشاهدة', 'Watchlist'))
        self.setProperty('fw.%d.title' % SOON, _t('القادم', 'Coming up'))
        self.setProperty('fw.msg', _t('يجهّز متابعتك…', 'Getting your watching ready…'))
        self.chips = [('all', _t('كل المصادر', 'All sources'))] + linked()
        if self.src not in [k for k, _l in self.chips]:
            self.src = 'all'
        self._fill_chips()
        self._fill_kinds()
        self.setProperty('fw.sub', self._sub())
        if self.state.get('data'):
            self._restore()
        for name, job in (('up', lambda: up_next(self.app)), ('watch', watchlist), ('soon', coming_up),
                          ('stats', stats)):
            self._start(name, job)
        self._start_because()

    def _restore(self):
        """Back from a title page: the sections as they were, then read again."""
        self.data = dict(self.state['data'])
        self.seed = self.state.get('seed') or ''
        if self.seed:
            self.setProperty('fw.%d.title' % MORE, _t('لأنك شاهدت %s', 'Because you watched %s') % self.seed)
        for cid in SECTIONS:
            if cid in self.data:
                self._paint(cid)
        focus, pos = self.state.get('focus'), self.state.get('pos', -1)
        if focus in SECTIONS and self.shown.get(focus):
            self._moved = True
            self._place(focus)
            try:
                if pos > 0:
                    self._ctrls[focus].selectItem(pos)
            except Exception:
                pass
            self._set_focus(focus)
        self.setProperty('fw.msg', '')
        self.state.pop('data', None)

    def on_cover(self):
        pass

    def on_uncover(self):
        pass

    def on_close(self):
        self._closing = True
        try:
            self._pool.shutdown(wait=False)
        except Exception:
            pass

    def close_window(self):
        self.on_close()
        self.close()

    def _sub(self):
        names = [label for key, label in self.chips if key != 'all']
        if not names:
            return _t('اربط Trakt أو Simkl أو Nuvio من الإعدادات لتظهر قوائمك هنا',
                      'Link Trakt, Simkl or Nuvio in Settings to see your lists here')
        return _t('من: %s', 'From: %s') % u'  ·  '.join(names)

    # ------------------------------------------------------------- loading
    def _start(self, name, job):
        with self._lock:
            self._pending.add(name)

        def run():
            try:
                result = job()
            except Exception:
                _log('%s failed:\n%s' % (name, traceback.format_exc()), xbmc.LOGWARNING)
                result = None
            if self._closing:
                return
            with self._lock:
                self._pending.discard(name)
            try:
                self._arrived(name, result)
            except Exception:
                _log('%s paint failed:\n%s' % (name, traceback.format_exc()), xbmc.LOGWARNING)
        try:
            self._pool.submit(run)
        except Exception:
            pass

    def _start_because(self):
        self._because_gen += 1
        gen, media = self._because_gen, self.media

        def job():
            title, items = because(media)
            return gen, title, items
        self._start('because', job)

    def _arrived(self, name, result):
        if name == 'stats':
            for n in range(3):
                value, text = (result or [])[n] if n < len(result or []) else ('', '')
                self.setProperty('fw.s%d' % (n + 1), value)
                self.setProperty('fw.s%dl' % (n + 1), text)
            return
        if name == 'because':
            gen, title, items = result or (0, '', [])
            if gen != self._because_gen:
                return
            self.seed = title
            self.setProperty('fw.%d.title' % MORE, (_t('لأنك شاهدت %s', 'Because you watched %s') % title)
                             if title else '')
            self.data[MORE] = items
            self._paint(MORE)
        else:
            cid = {'up': UP, 'watch': WATCH, 'soon': SOON}[name]
            self.data[cid] = result or []
            self._paint(cid)
            if name == 'soon':
                self._fill_soon_art(result or [])
        self._message()

    def _message(self):
        with self._lock:
            waiting = bool(self._pending)
        if any(self.shown.get(cid) for cid in SECTIONS):
            self.setProperty('fw.msg', '')
            focus = self.getFocusId()
            if not self._moved or (focus not in (CHIPS, KINDS) and not self.shown.get(focus)):
                self._focus_first()
        elif not waiting:
            self.setProperty('fw.msg', _t('ما فيه شي تتابعه هنا للحين', 'Nothing to follow here yet')
                             if self.src == 'all' and self.media == 'all' else
                             _t('ما فيه شي لهذا الاختيار', 'Nothing for this choice'))

    def _focus_first(self, tries=4):
        """The first section with cards (Kodi shows a section a frame after
        its cards arrive, and cannot focus it before: a few tries)."""
        want = next((cid for cid in SECTIONS if self.shown.get(cid)), None)
        if want is None or self._closing:
            return
        self._place(want)
        try:
            if self.getFocusId() != want:
                self.setFocusId(want)
        except Exception:
            pass
        if tries > 0:
            timer = threading.Timer(0.25, self._settle_focus, args=(want, tries - 1))
            timer.daemon = True
            timer.start()

    def _settle_focus(self, want, tries):
        if self._closing or (self._moved and self.getFocusId() in SECTIONS + (CHIPS, KINDS)):
            return
        try:
            if self.getFocusId() != want:
                self._focus_first(tries)
        except Exception:
            pass

    def _fill_soon_art(self, entries):
        """Trakt's calendar has no pictures: TMDb's, a few at a time."""
        from .. import tmdb_direct as T
        missing = [e for e in entries if not e.get('thumb') and (e.get('tmdb') or e.get('imdb'))][:16]

        def one(entry):
            if self._closing:
                return
            try:
                art = T.art_for(tmdb_id=entry.get('tmdb') or '', imdb_id=entry.get('imdb') or '',
                                media_type='series', title=entry.get('title') or '')
            except Exception:
                return
            entry['thumb'] = art.get('landscape') or art.get('fanart') or ''
            entry['poster'] = entry.get('poster') or art.get('poster') or ''
            entry['logo'] = art.get('clearlogo') or ''
            li = entry.get('_li')
            if li is not None and not self._closing:
                li.setArt({'thumb': entry['thumb'], 'poster': entry['poster'], 'clearlogo': entry['logo']})
        for entry in missing:
            try:
                self._pool.submit(one, entry)
            except Exception:
                return

    # ------------------------------------------------------------- placing
    def _visible(self):
        return [cid for cid in SECTIONS if self.shown.get(cid)]

    def _place(self, focus):
        """The sections with cards one under the other, ``focus`` (a section)
        right under the header and the ones above it hidden."""
        visible = self._visible()
        first = visible.index(focus) if focus in visible else 0
        y = TOP
        for cid in SECTIONS:
            group = self._ctrls.get(cid + 1000)
            if group is None:
                continue
            index = visible.index(cid) if cid in visible else -1
            if index < first:
                group.setVisible(False)
                continue
            group.setPosition(0, y)
            group.setVisible(True)
            y += HEIGHTS[cid] + GAP

    def _move(self, step):
        """Up or Down between the header and the sections with cards."""
        focus = self.getFocusId()
        visible = self._visible()
        if focus in (CHIPS, KINDS):
            if step < 0 or not visible:
                return
            target = visible[0]
        elif focus in visible:
            index = visible.index(focus) + step
            if index < 0:
                self._place(None)
                self._set_focus(CHIPS)
                return
            if index >= len(visible):
                return
            target = visible[index]
        else:
            return
        self._place(target)
        self._set_focus(target)

    def _set_focus(self, cid):
        try:
            self.setFocusId(cid)
        except Exception:
            pass

    # ------------------------------------------------------------ painting
    def _keep(self, entry):
        if self.media != 'all' and entry.get('media') != self.media:
            return False
        if self.src != 'all' and self.src not in (entry.get('srcs') or set()):
            return False
        return True

    def _paint(self, cid):
        ctrl = self._ctrls.get(cid)
        if ctrl is None:
            return
        entries = [e for e in self.data.get(cid) or [] if self._keep(e)]
        items = []
        for entry in entries:
            li = xbmcgui.ListItem(label=entry.get('title') or '', label2=entry.get('line') or '')
            li.setArt({'thumb': entry.get('thumb') or entry.get('poster') or '', 'poster': entry.get('poster') or '',
                       'clearlogo': entry.get('logo') or ''})
            if entry.get('badge'):
                li.setProperty('badge', entry['badge'])
            if entry.get('hot'):
                li.setProperty('hot', '1')
            if entry.get('prog'):
                li.setProperty('prog', str(int(entry['prog'])))
            if entry.get('left'):
                li.setProperty('left', entry['left'])
            entry['_li'] = li
            items.append(li)
        had = self.shown.get(cid)
        pos = -1
        try:
            pos = ctrl.getSelectedPosition() if had else -1
        except Exception:
            pass
        ctrl.reset()
        ctrl.addItems(items)
        self.shown[cid] = entries
        if 0 < pos < len(items):
            try:
                ctrl.selectItem(pos)
            except Exception:
                pass
        count = len(entries)
        self.setProperty('fw.%d.line' % cid, (_t('%d عنوان', '%d titles') % count) if count else '')
        try:
            focus = self.getFocusId()
        except Exception:
            focus = 0
        self._place(focus if focus in SECTIONS and self.shown.get(focus) else None)

    def _fill_chips(self):
        items = []
        for key, text in self.chips:
            li = xbmcgui.ListItem(label=text)
            if key == self.src:
                li.setProperty('on', '1')
            items.append(li)
        ctrl = self._ctrls.get(CHIPS)
        if ctrl is not None:
            ctrl.reset()
            ctrl.addItems(items)
            ctrl.selectItem([k for k, _l in self.chips].index(self.src))

    def _fill_kinds(self):
        kinds = (('all', _t('الكل', 'All')), ('movie', _t('أفلام', 'Movies')), ('series', _t('مسلسلات', 'Series')))
        items = []
        for key, text in kinds:
            li = xbmcgui.ListItem(label=text)
            if key == self.media:
                li.setProperty('on', '1')
            items.append(li)
        ctrl = self._ctrls.get(KINDS)
        if ctrl is not None:
            ctrl.reset()
            ctrl.addItems(items)
            ctrl.selectItem([k for k, _l in kinds].index(self.media))

    def _refilter(self, because=False):
        for cid in SECTIONS:
            if cid in self.data:
                self._paint(cid)
        if because:
            self.data.pop(MORE, None)
            self._paint(MORE)
            self._start_because()
        self._message()

    # --------------------------------------------------------------- input
    def _entry(self, cid):
        ctrl = self._ctrls.get(cid)
        try:
            pos = ctrl.getSelectedPosition()
        except Exception:
            return None
        entries = self.shown.get(cid) or []
        return entries[pos] if 0 <= pos < len(entries) else None

    def onClick(self, cid):
        if cid == CHIPS:
            pos = self._ctrls[CHIPS].getSelectedPosition()
            if 0 <= pos < len(self.chips) and self.chips[pos][0] != self.src:
                self.src = self.chips[pos][0]
                xbmcgui.Window(10000).setProperty(SRC_PROP, self.src)
                self._fill_chips()
                self._refilter()
            return
        if cid == KINDS:
            pos = self._ctrls[KINDS].getSelectedPosition()
            media = ('all', 'movie', 'series')[pos] if 0 <= pos < 3 else 'all'
            if media != self.media:
                self.media = media
                xbmcgui.Window(10000).setProperty(KIND_PROP, media)
                self._fill_kinds()
                self._refilter(because=True)
            return
        entry = self._entry(cid)
        if entry is None:
            return
        if cid == UP and entry.get('play'):
            self.play(entry)
        else:
            self.open_title(entry)

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self.close_window()
            return
        if aid in (1, 2, 3, 4, 5, 6, 7, 100, 107):
            self._moved = True
        if aid in (A_UP, A_DOWN, A_PGUP, A_PGDN):
            self._move(-1 if aid in (A_UP, A_PGUP) else 1)
            return
        if aid in (A_CONTEXT, A_MENU):
            cid = self.getFocusId()
            entry = self._entry(cid) if cid in SECTIONS else None
            if entry is not None:
                self.options(cid, entry)

    def options(self, cid, entry):
        from .options import choose
        choices = []
        if cid == UP and entry.get('play'):
            choices.append(('play', _t('شغّل', 'Play')))
        if entry.get('open'):
            choices.append(('open', _t('صفحة العمل', 'Open title page')))
        for text, command in entry.get('menu') or []:
            choices.append(('cmd:' + command, text))
        if entry.get('open') and cid != MORE:
            choices.append(('track', _t('تتبّع (الحالة والتقييم والقوائم)', 'Track (status, rating, lists)')))
        if not choices:
            return
        pick = choose(entry.get('title') or '', [text for _k, text in choices], subtitle=entry.get('line') or '')
        if pick < 0:
            return
        key = choices[pick][0]
        if key == 'play':
            self.play(entry)
        elif key == 'open':
            self.open_title(entry)
        elif key == 'track':
            self.track(entry)
        elif key.startswith('cmd:') and 'action=cw_remove' in key:
            # v5.10.286: Remove from Continue Watching in this process, the
            # section read again once the title is hidden (not on a timer)
            self._cw_remove(key[4:])
        elif key.startswith('cmd:'):
            xbmc.executebuiltin(key[4:])
            # a card marked watched or taken out: read the section again
            threading.Timer(2.5, self._reload_up).start()

    def _cw_remove(self, command):
        """v5.10.286: plugin.cw_remove (the title hidden from every source,
        Trakt and Nuvio told), then Continue Watching read again."""
        from urllib.parse import parse_qsl, urlsplit
        match = re.search(r'plugin://[^)"\s]+', str(command or ''))
        params = dict(parse_qsl(urlsplit(match.group(0)).query)) if match else {}

        def job():
            try:
                self.app.api().cw_remove(media_type=params.get('media_type', ''),
                                         canonical_id=params.get('canonical_id', ''),
                                         video_id=params.get('video_id', ''), refresh=False)
            except Exception as exc:
                xbmc.log('[DexHub] follow cw remove: %s' % exc, xbmc.LOGWARNING)
            self._reload_up()
        thread = threading.Thread(target=job, name='DexHub-follow-cw-remove')
        thread.daemon = True
        thread.start()

    def _reload_up(self):
        if self._closing:
            return
        try:
            from . import rows as R
            with R._PROGRESS_LOCK:
                R._PROGRESS_MEMO.clear()
        except Exception:
            pass
        self._start('up', lambda: up_next(self.app))

    def play(self, entry):
        from . import rows as R
        path = entry.get('play') or ''
        if not path:
            return
        self.close_window()
        if R.is_dexhub_path(path) and (entry.get('folder') and R.params_of(path).get('action') not in R.PLAYER_ACTIONS):
            xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % path)
        else:
            xbmc.executebuiltin('%s("%s")' % ('RunPlugin' if R.is_dexhub_path(path) else 'PlayMedia', path))

    def open_title(self, entry):
        query = entry.get('open') or {}
        if not query:
            xbmcgui.Dialog().notification('Dex Hub', _t('ما قدرنا نحدد العمل', 'Could not tell which title this is'),
                                          time=2000, sound=False)
            return
        try:
            from ..skinui.items import title_here, title_target
            skin_page = title_here()
        except Exception:
            skin_page, title_target = False, None
        if skin_page and title_target is not None:
            # the skin's own title page: under this window, so this one goes
            self.close_window()
            xbmc.executebuiltin('RunPlugin("%s")' % title_target(query))
            return
        if self.app is not None and hasattr(self.app, 'open_details'):
            # Dex Hub's own title page: Kodi opens no window from inside this
            # one's click, so this one closes, open_follow opens the page and
            # then this page again, as it was (Back comes back here)
            focus = self.getFocusId()
            pos = -1
            try:
                pos = self._ctrls[focus].getSelectedPosition() if focus in SECTIONS else -1
            except Exception:
                pass
            self.state.update({'data': dict((k, v) for k, v in self.data.items()), 'focus': focus, 'pos': pos,
                               'seed': self.seed})
            self.next_title = query
            self.close_window()
            return
        self.close_window()
        from urllib.parse import urlencode
        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.dexhub/?%s")'
                            % urlencode([('action', 'home_details')] + sorted(query.items())))

    def track(self, entry):
        query = entry.get('open') or {}
        from urllib.parse import urlencode
        params = [('action', 'track_panel'), ('media', query.get('m') or 'movie'),
                  ('canonical', query.get('imdb') or ('tmdb:%s' % query['tmdb'] if query.get('tmdb') else '')),
                  ('tmdb', query.get('tmdb') or ''), ('title', entry.get('title') or ''),
                  ('poster', entry.get('poster') or '')]
        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.dexhub/?%s")' % urlencode(params))


def open_follow(app, media=''):
    """The page; a title opened from it shows over the Home and the page
    comes back after it."""
    from . import ui_size
    from .app import _details_tile
    state = {}
    while True:
        window = FollowWindow(ui_size.xml('dexhub_follow.xml'), app.addon_path, 'Default', '1080i', app=app,
                              media=media, state=state)
        app._show(window)
        query, media = window.next_title, window.media
        del window
        if not query:
            return
        _log('title page %s %s' % (query.get('m'), query.get('tmdb') or query.get('imdb')))
        try:
            app.open_details(_details_tile(query))
        except Exception:
            _log('title page failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
