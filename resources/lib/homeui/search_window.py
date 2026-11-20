# -*- coding: utf-8 -*-
"""The search page (v5.10.595), after the mock-up the user chose over Arctic
Fuse 3's search: the query field and a glass keyboard (English, Arabic and
numbers) on the left, the results on the right as they come, and the focused
result's backdrop behind the page.

  - typing searches by itself after a short pause (no OK, no keyboard window):
    TMDb, the Stremio add-ons, the media servers and the IPTV subscription's
    films and series together (plugin.hub_search_collect), and the live
    channels of every Live TV source (guide.search_channels)
  - rows: Top results (wide cards with where each title is), AI picks, your
    servers, Movies, Series, Anime, IPTV films and series, IPTV channels,
    People; the filters on top count each kind and show one as a grid
  - the recent searches as chips; a search is kept in them once a result is
    opened (a word half typed is not)

WindowXMLDialog on resources/skins/Default/1080i/dexhub_search.xml
(tools/make_search_window.py).
"""
import re
import threading
import time

import xbmc
import xbmcgui

KEYS, MODES, RECENT, FILTERS, SORT, TOP, GRID, ROWS = 100, 101, 110, 200, 210, 300, 400, 9000
MEDIA = 'special://home/addons/plugin.video.dexhub/resources/media/'
PAGE_TONE = 'FF07080D'      # the page before a title gives it its colour
SERVER_LOGOS = {'plex': 'provider_plex.png', 'emby': 'provider_emby.png', 'jellyfin': 'provider_jellyfin.png',
                'silo': 'provider_silo.png'}


def _server_kind(line):
    """('plex', 'ARPX') from a server copy's line, ('', '') when it names no
    server: the part that is a server's kind, and the part after it."""
    parts = [re.sub(r'\[/?[A-Za-z]+[^\]]*\]', '', part).strip() for part in str(line or '').split(u'\u2022')]
    for index, part in enumerate(parts):
        kind = part.lower()
        if kind in SERVER_LOGOS:
            name = parts[index + 1] if index + 1 < len(parts) else ''
            if name.lower() == kind or len(name) > 18:
                name = ''
            return kind, name
    return '', ''
ROW_IDS = {'ai': 301, 'lib': 302, 'movie': 303, 'series': 304, 'anime': 305, 'iptv': 306,
           'channel': 307, 'person': 308, 'collection': 309}
# v5.10.595: the order of the results, as Silo's search sorts them
SORTS = (('relevance', 'الصلة'), ('title', 'العنوان'), ('newest', 'الأحدث'), ('oldest', 'الأقدم'),
         ('rating', 'التقييم'))
ROW_TITLES = (('top', 'أفضل النتائج'), ('collection', 'سلاسل أفلام'), ('ai', 'اقتراحات الذكاء الاصطناعي'), ('lib', 'من سيرفراتك'),
              ('movie', 'أفلام'), ('series', 'مسلسلات'), ('anime', 'أنمي'), ('iptv', 'IPTV · أفلام ومسلسلات'),
              ('channel', 'IPTV · قنوات'), ('person', 'أشخاص'))
FILTER_KEYS = (('all', 'الكل'), ('movie', 'أفلام'), ('series', 'مسلسلات'), ('collection', 'سلاسل'), ('channel', 'قنوات'),
               ('iptv', 'IPTV'), ('lib', 'السيرفرات'), ('person', 'أشخاص'))
SPACE, DELETE, CLEAR = u'␣', u'⌫', u'✕'
LAYOUTS = {
    'en': list('abcdefghijklmnopqrstuvwxyz') + [SPACE, DELETE, CLEAR, '\'', '-', '&'],
    'ar': list(u'ابتثجحخدذرزسشصضطظعغفقكلمنهوية') + [u'ى', u'ء', u'أ', u'إ', u'آ', u'ؤ', u'ئ', SPACE, DELETE, CLEAR],
    'num': list('1234567890') + [':', '.', ',', '!', '?', '(', ')', '+', SPACE, DELETE, CLEAR],
}
MODE_LABELS = (('en', 'EN'), ('ar', u'عربي'), ('num', '123'))
PAUSE = 0.7             # seconds without a key before the search starts
MIN_CHARS = 2
ROW_LIMIT = 40
A_LEFT, A_RIGHT, A_UP, A_DOWN = 1, 2, 3, 4
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}


def _arabic(text):
    return any(u'؀' <= ch <= u'ۿ' for ch in text or '')


class SearchWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(SearchWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.query = str(kwargs.get('query') or '')
        self.mode = 'ar' if _arabic(self.query) or self._interface_arabic() else 'en'
        self.filter = 'all'
        self.sort = 'relevance'
        self.collection = None   # (title, [tile]) while a film series is open
        self.groups = {}
        self.tiles = {}          # control id -> [tile]
        self.next = None         # (kind, payload) to open once the window is closed
        self._gen = 0
        self._timer = None
        self._lock = threading.Lock()
        self._inited = False
        self._closing = False
        self._spot_tile = None

    # ------------------------------------------------------------ helpers
    def tr(self, text):
        return self.app.tr(text) if self.app is not None else text

    @staticmethod
    def _interface_arabic():
        try:
            return xbmc.getInfoLabel('System.Language').lower().startswith('arab')
        except Exception:
            return False

    def _log(self, msg, level=xbmc.LOGINFO):
        xbmc.log('[DexHub] search page: %s' % msg, level)

    def _ctrl(self, cid):
        try:
            return self.getControl(cid)
        except Exception:
            return None

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('tone', PAGE_TONE)
        self.setProperty('t.search', self.tr('البحث'))
        self.setProperty('t.hint', self.tr('اكتب اسم فيلم أو مسلسل أو قناة أو شخص'))
        self.setProperty('t.recent', self.tr('عمليات بحث سابقة'))
        for key, title in ROW_TITLES:
            self.setProperty('row.%s' % key, self.tr(title))
        self.setProperty('filter', 'all')
        self._show_sort()
        self._fill_modes()
        self._fill_keys()
        self._fill_recent()
        self._fill_filters()
        self._show_query()
        self.setFocusId(KEYS)
        if len(self.query.strip()) >= MIN_CHARS:
            self._search_now()
        else:
            self._empty_state()

    def close(self):
        self._closing = True
        self._gen += 1
        if self._timer is not None:
            self._timer.cancel()
        super(SearchWindow, self).close()

    # ------------------------------------------------------------ keyboard
    def _fill_modes(self):
        ctrl = self._ctrl(MODES)
        if ctrl is None:
            return
        items = []
        for key, text in MODE_LABELS:
            li = xbmcgui.ListItem(text)
            li.setProperty('key', key)
            li.setProperty('on', '1' if key == self.mode else '')
            items.append(li)
        ctrl.reset()
        ctrl.addItems(items)
        ctrl.selectItem([k for k, _t in MODE_LABELS].index(self.mode))

    def _fill_keys(self):
        ctrl = self._ctrl(KEYS)
        if ctrl is None:
            return
        items = []
        for key in LAYOUTS[self.mode]:
            li = xbmcgui.ListItem(key)
            li.setProperty('key', key)
            items.append(li)
        ctrl.reset()
        ctrl.addItems(items)

    def _press(self, key):
        if key == SPACE:
            if self.query and not self.query.endswith(' '):
                self.query += ' '
        elif key == DELETE:
            self.query = self.query[:-1]
        elif key == CLEAR:
            self.query = ''
        else:
            self.query += key
        self._show_query()
        self._schedule()

    def _show_query(self):
        self.setProperty('q', self.query)

    def _schedule(self):
        if self._timer is not None:
            self._timer.cancel()
        self._gen += 1
        if len(self.query.strip()) < MIN_CHARS:
            self._empty_state()
            return
        self.setProperty('status', self.tr('…'))
        self._timer = threading.Timer(PAUSE, self._search_now)
        self._timer.daemon = True
        self._timer.start()

    # ------------------------------------------------------------- recent
    def _fill_recent(self):
        ctrl = self._ctrl(RECENT)
        if ctrl is None:
            return
        try:
            from .. import search_history
            recent = search_history.recent(limit=12) or []
        except Exception:
            recent = []
        items = []
        for entry in recent:
            query = str((entry or {}).get('query') or '').strip()
            if query:
                li = xbmcgui.ListItem(query)
                li.setProperty('query', query)
                items.append(li)
        ctrl.reset()
        ctrl.addItems(items[:9])

    # ------------------------------------------------------------- filters
    def _fill_filters(self, counts=None):
        ctrl = self._ctrl(FILTERS)
        if ctrl is None:
            return
        counts = counts or {}
        items = []
        for key, title in FILTER_KEYS:
            if key != 'all' and counts and not counts.get(key):
                continue
            li = xbmcgui.ListItem(self.tr(title))
            n = counts.get(key) if counts else None
            li.setLabel2(str(n) if n else '')
            li.setProperty('key', key)
            li.setProperty('on', '1' if key == self.filter else '')
            items.append(li)
        pos = ctrl.getSelectedPosition() if ctrl.size() else 0
        self.setProperty('fw', str(len(items) * 204 + 4))     # the bar's glass track
        ctrl.reset()
        ctrl.addItems(items)
        if items:
            ctrl.selectItem(min(max(pos, 0), len(items) - 1))

    def _set_filter(self, key):
        self.filter = key
        self.setProperty('filter', key)
        self._fill_filters(self._counts())
        if key != 'all':
            tiles = self.groups.get(key) or []
            self._fill(GRID, tiles, 'poster')

    # ---------------------------------------------------------------- sort
    def _show_sort(self):
        self.setProperty('sortlabel', u'\u21c5  ' + self.tr(dict(SORTS)[self.sort]))

    def _choose_sort(self):
        from .options import choose
        keys = [k for k, _t in SORTS]
        index = choose(self.tr('ترتيب النتائج'), [self.tr(t) for _k, t in SORTS], preselect=keys.index(self.sort))
        if index < 0 or keys[index] == self.sort:
            return
        self.sort = keys[index]
        self._show_sort()
        for key, cid in ROW_IDS.items():
            tiles = self.groups.get(key) or []
            self.groups[key] = self._sorted(tiles, key)
            self._fill(cid, self.groups[key], 'channel' if key == 'channel' else 'person' if key == 'person' else 'poster')
        if self.filter != 'all':
            self._fill(GRID, self.groups.get(self.filter) or [], 'poster')

    def _sorted(self, tiles, key=''):
        tiles = list(tiles or [])
        for index, tile in enumerate(tiles):
            tile.setdefault('_order', index)
        if self.sort == 'title':
            return sorted(tiles, key=lambda t: (t.get('title') or '').lower())
        if self.sort in ('newest', 'oldest'):
            dated = [t for t in tiles if str(t.get('year') or '').isdigit()]
            rest = [t for t in tiles if not str(t.get('year') or '').isdigit()]
            dated.sort(key=lambda t: int(t['year']), reverse=self.sort == 'newest')
            return dated + rest
        if self.sort == 'rating':
            return sorted(tiles, key=lambda t: -(float(t.get('rating') or 0) if str(t.get('rating') or '0').replace('.', '', 1).isdigit() else 0))
        return sorted(tiles, key=lambda t: t.get('_order', 0))

    # ---------------------------------------------------------- collections
    def _open_collection(self, tile):
        """A film series' films, in order, as this page's grid; Back is the results."""
        def run():
            from .. import tmdb_direct as T
            try:
                rows = T.nuvio_source_items({'tmdbSourceType': 'COLLECTION', 'tmdbId': tile.get('collection_id'),
                                             'mediaType': 'movie'}, limit=40) or []
            except Exception:
                rows = []
            films = []
            for row in rows:
                films.append({'kind': 'work', 'media_type': 'movie', 'title': row.get('title') or '',
                              'year': row.get('year') or '', 'tmdb_id': row.get('tmdb_id') or '', 'imdb_id': '',
                              'poster': row.get('poster') or '', 'fanart': row.get('backdrop') or '',
                              'rating': row.get('rating') or 0, 'plot': row.get('overview') or '', 'path': ''})
            films.sort(key=lambda t: t.get('year') or '9999')
            if self._closing:
                return
            self.collection = (tile.get('title') or '', films)
            self.setProperty('filter', 'col')
            self.setProperty('coltitle', tile.get('title') or '')
            self._fill(GRID, films, 'poster')
            if films:
                self.setFocusId(GRID)
        threading.Thread(target=run, name='DexHubSearchCollection', daemon=True).start()

    def _close_collection(self):
        self.collection = None
        self.setProperty('filter', self.filter)
        self.setProperty('coltitle', '')
        if self.filter != 'all':
            self._fill(GRID, self.groups.get(self.filter) or [], 'poster')
        self.setFocusId(ROWS if self.filter == 'all' else GRID)

    def _counts(self):
        counts = dict((key, len(tiles)) for key, tiles in self.groups.items() if key != 'top')
        counts['all'] = sum(v for k, v in counts.items() if k != 'all')
        return counts

    # -------------------------------------------------------------- search
    def _empty_state(self):
        self._gen += 1
        self.groups = {}
        self.setProperty('has', '')
        self.setProperty('status', '')
        self.setProperty('msg', self.tr('ابحث في كل مصادرك'))
        self.setProperty('msg2', self.tr('TMDb وإضافاتك وسيرفراتك واشتراك IPTV وقنواته معاً، والنتائج تطلع وأنت تكتب.'))
        self._fill_filters()

    def _search_now(self):
        query = self.query.strip()
        if self._closing or len(query) < MIN_CHARS:
            return
        with self._lock:
            self._gen += 1
            gen = self._gen
        self.setProperty('status', self.tr('جاري البحث…'))
        self.groups = {}
        found = {'rows': [], 'channels': [], 'collections': []}

        def stop():
            return self._closing or gen != self._gen

        def publish():
            if stop():
                return
            self._show_results(query, found['rows'], found['channels'], gen, found['collections'])

        def channels():
            try:
                found['channels'] = search_channels(self.app, query, stop)
            except Exception as exc:
                self._log('channels: %s' % exc, xbmc.LOGWARNING)
            publish()

        threading.Thread(target=channels, name='DexHubSearchChannels', daemon=True).start()

        def collections():
            try:
                found['collections'] = search_collections(query)
            except Exception as exc:
                self._log('collections: %s' % exc, xbmc.LOGWARNING)
            publish()

        threading.Thread(target=collections, name='DexHubSearchCollections', daemon=True).start()

        def progress(done, total, results):
            if not stop():
                self.setProperty('status', self.tr('%d من %d مصدر') % (done, total))

        def partial(rows):
            found['rows'] = list(rows or [])
            publish()

        def collect():
            try:
                return self.app.api().hub_search_collect(query, progress=progress, should_stop=stop,
                                                         partial=partial, record=False)
            except TypeError:
                return self.app.api().hub_search_collect(query, progress=progress, should_stop=stop,
                                                         partial=partial)
            except Exception as exc:
                self._log('failed: %s' % exc, xbmc.LOGWARNING)
                return [], 'error'

        def run():
            started = time.monotonic()
            rows, status = collect()
            if not rows and status == 'empty' and not stop():
                # the first search of a page often finds every source cold
                # (all of them timed out on the box once); asked again, they answer
                self._log('"%s": nothing on the first try, asking again' % query)
                rows, status = collect()
            if stop():
                return
            found['rows'] = list(rows or [])
            publish()
            total = sum(len(t) for k, t in self.groups.items() if k != 'top')
            self.setProperty('status', self.tr('%d نتيجة') % total if total else '')
            self._log('"%s": %s, %d result(s), %.0f ms' % (query, status, total, (time.monotonic() - started) * 1000))
            if not total:
                self.setProperty('msg', self.tr('لا توجد نتائج لـ "%s"') % query)
                self.setProperty('msg2', self.tr('جرّب كلمة أخرى، أو اسم العمل بالإنجليزية.'))

        threading.Thread(target=run, name='DexHubSearch', daemon=True).start()

    def _show_results(self, query, rows, channels, gen, collections=None):
        from . import rows as R
        groups = {}
        best = {}
        seen = {}
        for entry in rows or []:
            tile = R.search_tile(entry)
            if tile is None:
                continue
            if entry.get('ai'):
                key = 'ai'
            elif entry.get('server'):
                key = 'lib'
                # a short clean badge: the server's name when it is one, never
                # the copy's technical line ("[1080p] • [H.264] …")
                name = entry.get('server') if isinstance(entry.get('server'), str) else ''
                name = name if name and len(name) <= 14 and not any(c in name for c in '[]•|') else ''
                tile['badge'] = name or self.tr('على سيرفرك')
                # the server's own logo instead of the words (the user, 20 Nov
                # 2026): the copy's line names its kind, then the server
                # ("1080p • H.264 • MKV • [COLOR ..]PLEX[/COLOR] • ARPX")
                kind, server = _server_kind(entry.get('source_label') or entry.get('provider_name') or '')
                if kind:
                    tile['srv'] = MEDIA + SERVER_LOGOS[kind]
                    tile['srvname'] = server
            elif entry.get('iptv'):
                key = 'iptv'
                tile['badge'] = 'IPTV'
            else:
                media = str(entry.get('media_type') or '').lower()
                if media in ('person', 'people') or entry.get('is_person'):
                    key = 'person'
                elif entry.get('is_anime') or media == 'anime':
                    key = 'anime'
                elif media == 'movie':
                    key = 'movie'
                elif media in ('series', 'show', 'tvshow'):
                    key = 'series'
                else:
                    key = 'channel' if media in ('tv', 'channel', 'live') else 'series'
            # the same title from several sources once in its row (TMDb and
            # the add-ons answer with the same film); the best ranked first
            ident = (('tmdb', tile['tmdb_id']) if tile.get('tmdb_id') else ('imdb', tile['imdb_id'])
                     if tile.get('imdb_id') else ('t', (tile.get('title') or '').strip().lower(), str(tile.get('year') or '')))
            marks = seen.setdefault(key, set())
            if ident in marks:
                continue
            marks.add(ident)
            bucket = groups.setdefault(key, [])
            if len(bucket) < ROW_LIMIT:
                bucket.append(tile)
            best[key] = max(best.get(key, 0.0), tile.get('_score') or 0.0)
        if channels:
            groups.setdefault('channel', [])
            groups['channel'] = (channels + groups['channel'])[:ROW_LIMIT]
        if collections:
            groups['collection'] = list(collections)[:ROW_LIMIT]
        for key in list(groups):
            groups[key] = self._sorted(groups[key], key)
        picks = [t for key in ('lib', 'movie', 'series', 'anime', 'iptv') for t in groups.get(key, [])
                 if t.get('fanart') and t.get('fanart') != t.get('poster')]
        picks.sort(key=lambda t: -(t.get('_score') or 0.0))
        if picks:
            groups['top'] = picks[:6]
        if gen != self._gen or self._closing:
            return
        self.groups = groups
        self.setProperty('row.top.n', '')
        for key, cid in ROW_IDS.items():
            tiles = groups.get(key) or []
            self.setProperty('row.%s.n' % key, str(len(tiles)) if tiles else '')
            self._fill(cid, tiles, 'channel' if key == 'channel' else 'person' if key == 'person' else 'poster')
        self._fill(TOP, groups.get('top') or [], 'top')
        self._enrich_top(gen, groups.get('top') or [])
        has = any(groups.get(k) for k in groups)
        self.setProperty('has', '1' if has else '')
        if has:
            self.setProperty('msg', '')
        self._fill_filters(self._counts())
        if self.filter != 'all':
            self._fill(GRID, groups.get(self.filter) or [], 'poster')
        self._focus_info()

    def _enrich_top(self, gen, tiles):
        """The top results' own logos (and a textless backdrop when a card has
        none), from the titles' TMDb images (cached a week), behind the page."""
        wanted = [t for t in tiles if t.get('tmdb_id') and not t.get('_enriched')]
        if not wanted:
            return

        def run():
            from .. import art_choice, tmdb_direct as T
            lang = (T._card_language() or 'en')
            changed = False
            for tile in wanted:
                if gen != self._gen or self._closing:
                    return
                tile['_enriched'] = True
                try:
                    data = art_choice._images(tile['tmdb_id'], tile.get('media_type') or 'movie') or {}
                except Exception:
                    continue
                if not tile.get('clearlogo'):
                    logo = T._nuvio_logo_image(data.get('logos') or [], lang) or {}
                    if logo.get('file_path'):
                        tile['clearlogo'] = T.IMAGE_BASE_LOGO + logo['file_path']
                        changed = True
                if not tile.get('fanart'):
                    plain = [b for b in data.get('backdrops') or [] if not b.get('iso_639_1')]
                    if plain:
                        tile['fanart'] = T.IMAGE_BASE_BACKDROP + plain[0]['file_path']
                        changed = True
            if changed and gen == self._gen and not self._closing:
                self._fill(TOP, tiles, 'top')
                self._focus_info()

        threading.Thread(target=run, name='DexHubSearchLogos', daemon=True).start()

    # -------------------------------------------------------------- items
    def _fill(self, cid, tiles, shape):
        ctrl = self._ctrl(cid)
        if ctrl is None:
            return
        keep = ctrl.getSelectedPosition() if self.getFocusId() == cid else 0
        items = [self._item(tile, shape) for tile in tiles]
        self.tiles[cid] = list(tiles)
        ctrl.reset()
        if items:
            ctrl.addItems(items)
            ctrl.selectItem(min(max(keep, 0), len(items) - 1))

    def _item(self, tile, shape):
        li = xbmcgui.ListItem(tile.get('title') or tile.get('label') or '')
        if shape == 'channel':
            li.setLabel2(tile.get('group') or (tile.get('now') or {}).get('title') or '')
            logo = tile.get('logo') or tile.get('clearlogo') or ''
            li.setArt({'clearlogo': logo, 'thumb': logo, 'poster': tile.get('poster') or '',
                       'fanart': tile.get('fanart') or ''})
        elif shape == 'person':
            li.setLabel2(tile.get('plot') if len(tile.get('plot') or '') < 30 else '')
            words = [w for w in (tile.get('title') or '').split() if w]
            li.setProperty('initials', ''.join(w[0] for w in words[:2]).upper())
            li.setArt({'poster': tile.get('poster') or '', 'thumb': tile.get('poster') or ''})
        elif tile.get('kind') == 'collection':
            li.setLabel2(self.tr('سلسلة أفلام'))
            li.setArt({'poster': tile.get('poster') or '', 'fanart': tile.get('fanart') or '',
                       'thumb': tile.get('poster') or ''})
        else:
            li.setLabel2(str(tile.get('year') or ''))
            if tile.get('tmdb_id') and tile.get('media_type') in ('movie', 'series') and not tile.get('_clean'):
                # TMDb's own poster where its images are known: the add-ons'
                # posters often carry printed ratings (art_rotate's pick)
                try:
                    from ..art_rotate import pick
                    clean = pick(tile['tmdb_id'], tile['media_type'])
                except Exception:
                    clean = {}
                if clean.get('poster'):
                    tile['poster'] = clean['poster']
                if clean.get('fanart') and not tile.get('fanart'):
                    tile['fanart'] = clean['fanart']
                tile['_clean'] = bool(clean)
            li.setArt({'poster': tile.get('poster') or tile.get('landscape') or '',
                       'fanart': tile.get('fanart') or tile.get('landscape') or '',
                       'clearlogo': tile.get('clearlogo') or '', 'thumb': tile.get('poster') or ''})
        li.setProperty('badge', tile.get('badge') or '')
        li.setProperty('srv', tile.get('srv') or '')
        li.setProperty('meta', self._meta(tile))
        return li

    def _meta(self, tile):
        parts = []
        media = tile.get('media_type')
        if media == 'movie':
            parts.append(self.tr('فيلم'))
        elif media == 'series':
            parts.append(self.tr('مسلسل'))
        if tile.get('year'):
            parts.append(str(tile['year']))
        genres = tile.get('genres') or []
        if genres:
            parts.append(' / '.join(genres[:2]))
        if tile.get('rating'):
            try:
                parts.append('IMDb %.1f' % float(tile['rating']))
            except (TypeError, ValueError):
                pass
        return u'  ·  '.join(parts)

    def _focused_tile(self):
        cid = self.getFocusId()
        tiles = self.tiles.get(cid)
        if not tiles:
            return None, cid
        ctrl = self._ctrl(cid)
        pos = ctrl.getSelectedPosition() if ctrl is not None else -1
        if 0 <= pos < len(tiles):
            return tiles[pos], cid
        return None, cid

    def _focus_info(self):
        tile, _cid = self._focused_tile()
        if tile is None:
            # the first result's picture stays behind the page until one is focused
            first = (self.groups.get('top') or [None])[0]
            if first is not None and not self.getProperty('bg'):
                self.setProperty('bg', first.get('fanart') or '')
            return
        art = tile.get('fanart') or tile.get('landscape') or ''
        if art:
            self.setProperty('bg', art)
        self._spot(tile)

    # ------------------------------------------------------------- spotlight
    def _spot(self, tile):
        """The focused result in full at the top of the page while browsing:
        its logo (or whole name), the year and facts, and the story, as the
        Home's spotlight; the cards keep no names under them, which a small
        poster cut short (the user, 20 Nov 2026). What a result lacks comes
        from TMDb behind the page (cached), a moment after the focus stops."""
        self._spot_tile = tile
        kind = tile.get('kind')
        if kind == 'collection':
            meta = self.tr('سلسلة أفلام')
        elif tile.get('media_type') in ('movie', 'series'):
            meta = self._spot_meta(tile)
        else:
            meta = tile.get('group') or (tile.get('now') or {}).get('title') or tile.get('plot') or ''
            if len(meta) > 80:
                meta = ''
        plot = tile.get('overview') or tile.get('plot') or ''
        if kind not in ('collection',) and tile.get('media_type') not in ('movie', 'series'):
            plot = ''
        self.setProperty('spot.title', tile.get('title') or tile.get('label') or '')
        self.setProperty('spot.logo', tile.get('clearlogo') or '')
        if tile.get('srv'):
            # the logo stands before the line; the server's own name after it
            if tile.get('srvname'):
                meta = (meta + u'  \u00b7  ' if meta else '') + tile['srvname']
        elif tile.get('badge'):
            meta = (meta + u'    ' if meta else '') + u'[COLOR FF5FD3A6]\u25cf[/COLOR] ' + tile['badge']
        self.setProperty('spot.srv', tile.get('srv') or '')
        self.setProperty('spot.meta', meta)
        self.setProperty('spot.plot', plot)
        if (tile.get('tmdb_id') and tile.get('media_type') in ('movie', 'series') and not tile.get('_spot')):
            self._spot_fetch(tile)
        self._tint(tile)

    def _tint(self, tile):
        """The whole page in the colour of the focused title's picture, as
        the Home and Silo's app (the user, 20 Nov 2026): art_colour's dark
        tone for the page and its glow over it. A known picture tints at
        once; another a moment after the focus stops, so a fast scroll does
        not flash colours. Nothing found keeps the colour there is."""
        url = tile.get('fanart') or tile.get('landscape') or tile.get('poster') or ''
        if not url:
            return
        from .. import art_colour
        found = art_colour.cached(url)
        if found is not False:
            self._tone(found)
            return
        gen = self._gen

        def run():
            if xbmc.Monitor().waitForAbort(0.3) or self._closing or gen != self._gen or self._spot_tile is not tile:
                return
            found = art_colour.colour(url)
            if not self._closing and gen == self._gen and self._spot_tile is tile:
                self._tone(found)

        threading.Thread(target=run, name='DexHubSearchTint', daemon=True).start()

    def _tone(self, found):
        if found and found.get('tone') and found.get('glow'):
            self.setProperty('tone', found['tone'])
            self.setProperty('glow', found['glow'])

    def _spot_meta(self, tile):
        parts = [self.tr('فيلم') if tile.get('media_type') == 'movie' else self.tr('مسلسل')]
        if tile.get('year'):
            parts.append(str(tile['year']))
        if tile.get('_seasons'):
            parts.append(self.tr('%d مواسم') % tile['_seasons'] if tile['_seasons'] > 1 else self.tr('موسم واحد'))
        elif tile.get('_runtime'):
            hours, minutes = divmod(int(tile['_runtime']), 60)
            parts.append(('%dh %02dm' % (hours, minutes)) if hours else ('%dm' % minutes))
        if tile.get('_cert'):
            parts.append(tile['_cert'])
        genres = tile.get('genres') or []
        if genres:
            parts.append(' / '.join(genres[:2]))
        if tile.get('rating'):
            try:
                parts.append('IMDb %.1f' % float(tile['rating']))
            except (TypeError, ValueError):
                pass
        return u'  ·  '.join(parts)

    def _spot_fetch(self, tile):
        gen = self._gen

        def run():
            # only the title the focus rests on: a fast scroll asks nothing
            if xbmc.Monitor().waitForAbort(0.35) or self._closing or gen != self._gen or self._spot_tile is not tile:
                return
            tile['_spot'] = True
            try:
                from .. import tmdb_direct as T
                meta = T.meta_for(tmdb_id=tile['tmdb_id'], media_type=tile['media_type'], timeout=6, max_attempts=1) or {}
            except Exception:
                meta = {}
            if not tile.get('clearlogo') and meta.get('clearlogo'):
                tile['clearlogo'] = meta['clearlogo']
            if not (tile.get('overview') or tile.get('plot')) and meta.get('overview'):
                tile['overview'] = meta['overview']
            if not tile.get('genres') and meta.get('genres'):
                tile['genres'] = list(meta['genres'])
            if not tile.get('rating') and meta.get('rating'):
                tile['rating'] = meta['rating']
            if not tile.get('year') and meta.get('year'):
                tile['year'] = meta['year']
            tile['_runtime'] = meta.get('runtime') or 0
            tile['_seasons'] = meta.get('number_of_seasons') or 0
            tile['_cert'] = meta.get('certification') or ''
            if not self._closing and gen == self._gen and self._spot_tile is tile:
                self._spot(tile)

        threading.Thread(target=run, name='DexHubSearchSpot', daemon=True).start()

    # ------------------------------------------------------------- events
    def onClick(self, cid):
        if cid == KEYS:
            ctrl = self._ctrl(KEYS)
            item = ctrl.getSelectedItem() if ctrl is not None else None
            if item is not None:
                self._press(item.getProperty('key'))
            return
        if cid == MODES:
            ctrl = self._ctrl(MODES)
            item = ctrl.getSelectedItem() if ctrl is not None else None
            if item is not None and item.getProperty('key') != self.mode:
                self.mode = item.getProperty('key')
                self._fill_modes()
                self._fill_keys()
            return
        if cid == RECENT:
            ctrl = self._ctrl(RECENT)
            item = ctrl.getSelectedItem() if ctrl is not None else None
            if item is not None:
                self.query = item.getProperty('query')
                if _arabic(self.query) and self.mode != 'ar':
                    self.mode = 'ar'
                    self._fill_modes()
                    self._fill_keys()
                self._show_query()
                self._search_now()
            return
        if cid == SORT:
            self._choose_sort()
            return
        if cid == FILTERS:
            ctrl = self._ctrl(FILTERS)
            item = ctrl.getSelectedItem() if ctrl is not None else None
            if item is not None:
                self._set_filter(item.getProperty('key'))
            return
        tile, _cid = self._focused_tile()
        if tile is None:
            return
        if tile.get('kind') == 'collection':
            self._remember()
            self._open_collection(tile)
            return
        if 'people_filmography' in (tile.get('path') or ''):
            # v5.10.595: a person opens their page over the search (Back: here)
            from . import rows as R
            params = R.params_of(tile['path'])
            if str(params.get('person_id') or '').isdigit():
                self._remember()
                from .person_page import open_person
                open_person(self.app, params)
                return
        self._remember()
        # a film or a series: the skin's title page over this page, which
        # stays under it (Back is the search again, on the same result)
        if tile.get('kind') == 'work' and tile.get('media_type') in ('movie', 'series') \
                and not tile.get('badge') == 'IPTV':
            q = {'m': tile['media_type'], 'title': tile.get('title') or '',
                 'tmdb': tile.get('tmdb_id') or '', 'imdb': tile.get('imdb_id') or ''}
            if q['tmdb'] or q['imdb']:
                try:
                    from ..skinui.items import title_over_window
                    if title_over_window(self, q):
                        return
                except Exception as exc:
                    self._log('title page: %s' % exc, xbmc.LOGWARNING)
        self.next = tile
        self.close()

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            if self.collection is not None:
                self._close_collection()
                return
            if self.getFocusId() != KEYS and self.getFocusId() not in (MODES, RECENT):
                # the keyboard is hidden while the results have the focus (the
                # spotlight is there): the filters first, then the keys once
                # the field is back on screen
                if self.getFocusId() not in (FILTERS, SORT):
                    self.setFocusId(FILTERS)
                    xbmc.sleep(180)
                self.setFocusId(KEYS)
                return
            self.close()
            return
        if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
            self._focus_info()

    def onFocus(self, cid):
        self._focus_info()

    def _remember(self):
        try:
            from .. import search_history
            search_history.add(self.query.strip(), media_type='all')
        except Exception:
            pass


# --------------------------------------------------------------- collections
def search_collections(query, limit=12):
    """TMDb's film series whose name holds ``query`` (Silo's "Collections"
    row): one request, cached as TMDb's calls are."""
    from .. import tmdb_direct as T
    lang = (T._card_language() or 'en')
    data = T._request('/search/collection', {'query': query, 'language': lang}, timeout=6, max_attempts=1) or {}
    out = []
    for row in (data.get('results') or [])[:limit]:
        if not isinstance(row, dict) or not row.get('id') or not row.get('poster_path'):
            continue
        out.append({'kind': 'collection', 'collection_id': str(row['id']), 'title': row.get('name') or '',
                    'poster': T.IMAGE_BASE_POSTER + row['poster_path'],
                    'fanart': (T.IMAGE_BASE_BACKDROP + row['backdrop_path']) if row.get('backdrop_path') else ''})
    return out


# ------------------------------------------------------------------ channels
def search_channels(app, query, stop=None, limit=40):
    """Live channels whose name holds ``query``, from every Live TV source
    (the PVR, the Stremio sources with channels, the M3U and Xtream lists)."""
    from ..ui_preferences import iptv_available
    if not iptv_available('channels'):
        return []
    from . import live_sources as LS
    from .guide import search_source_channels
    found = []
    for source in LS.sources(app):
        if source.get('kind') == 'setup':
            continue
        if stop is not None and stop():
            break
        try:
            tiles = search_source_channels(app, source, query, limit=limit - len(found))
        except Exception as exc:
            LS.log('channel search in %s: %s' % (source.get('key'), LS.failure(exc)), xbmc.LOGWARNING)
            tiles = []
        for tile in tiles:
            tile['badge'] = source.get('label') or ''
        found.extend(tiles)
        if len(found) >= limit:
            break
    return found[:limit]


# -------------------------------------------------------------------- opening
def open_search(app, query=''):
    """The page; a result opens once the page has closed, and the page comes
    back with the same query when the result is done (Back)."""
    from . import ui_size
    while True:
        window = SearchWindow(ui_size.xml('dexhub_search.xml'), app.addon_path, 'Default', '1080i',
                              app=app, query=query)
        app._show(window)
        tile, query = window.next, window.query
        del window
        if not tile:
            return
        if not _open_tile(app, tile):
            return          # it went to the player or to Kodi's own lists


def _open_tile(app, tile):
    """Open a result; True when it was a page of the Home UI that returns
    here (the search page then opens again)."""
    from . import rows as R
    if tile.get('kind') == 'channel':
        _play_channel(app, tile)
        return False
    path = tile.get('path') or ''
    params = R.params_of(path) if R.is_dexhub_path(path) else {}
    action = params.get('action', '')
    if action in ('home_vod_list', 'home_vod_series'):
        grid = dict(params, action='home_vod_list')
        app.open_grid(grid, title=tile.get('title') or '', shape='landscape' if grid.get('season') else 'poster')
        return True
    media = tile.get('media_type')
    if media in ('movie', 'series') and (tile.get('tmdb_id') or tile.get('imdb_id')) and tile.get('kind') == 'work':
        from . import details
        if details.supported(tile):
            app.open_details(tile)
            return True
    if not path:
        return False
    if not R.is_dexhub_path(path):
        builtin = 'ActivateWindow(Videos,"%s",return)' if tile.get('folder') else 'PlayMedia("%s")'
        xbmc.executebuiltin(builtin % path)
        return False
    if action in R.PLAYER_ACTIONS or not tile.get('folder'):
        xbmc.executebuiltin('RunPlugin("%s")' % path)
        return False
    if action in R.WORK_FOLDER_ACTIONS or tile.get('kind') == 'work':
        xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % path)
        return False
    app.open_grid(params, title=tile.get('title') or '')
    return True


def _play_channel(app, tile):
    """A channel full screen: the PVR's by its channel id, the others by
    their stream (as the Live TV page plays them)."""
    import json
    if tile.get('channelid'):
        xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Player.Open',
                                        'params': {'item': {'channelid': int(tile['channelid'])}}}))
        return
    from . import live_sources as LS
    stream = LS.resolve(tile, app)
    if not stream or not stream.get('url'):
        xbmcgui.Dialog().notification('Dex Hub', app.tr('تعذر تشغيل القناة'), xbmcgui.NOTIFICATION_WARNING, 3000)
        return
    li = xbmcgui.ListItem(tile.get('title') or '', path=stream['url'])
    li.setArt({'icon': tile.get('logo') or '', 'thumb': tile.get('logo') or ''})
    if stream.get('mime'):
        li.setMimeType(stream['mime'])
        li.setContentLookup(False)
    for key, value in (stream.get('props') or {}).items():
        li.setProperty(key, str(value))
    li.setProperty('IsPlayable', 'true')
    xbmc.Player().play(stream['url'], li)
