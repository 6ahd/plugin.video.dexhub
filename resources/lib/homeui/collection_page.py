# -*- coding: utf-8 -*-
"""A collection as one page (v5.10.459), built like Discover.

WindowXMLDialog on dexhub_collection*.xml (tools/s459/make_collection.py):
the collection's name (or logo) over All, Movies, Series (101-103), then one
section per catalog, each in its own card shape: the first a big feature card
with the story, then tall posters, posters, wide pictures in turn; a catalog
called "Top ..." gets big numbers (list 46N instead of 40N). With more than 12
catalogs a last section (413) has every catalog as a card that opens its grid.

The catalogs are read as the old page read them (skinui/rows.py, tab "fold":
the same cached copies), three side by side, each section showing as it
comes; one that comes back empty is hidden. skin.dexhub's collection cards
open this page (skinui/folderpage.request_open).
"""
import re
import threading

import xbmc
import xbmcgui

from .. import discover_feed as F
from .. import lazy_tb as traceback
from .discover_page import KINDS, A_BACK, A_LEFT, A_RIGHT, A_UP, A_DOWN, A_CONTEXT, A_MENU, A_PLAY, _t, _item, _meta
from .sport_page import take_focus

SLOTS = 12
ALL = 13
SHAPES = ('feature', 'tall', 'posters', 'wide', 'posters', 'tall', 'wide', 'posters', 'tall', 'posters', 'wide', 'posters')
TOP = re.compile(r'\btop\s*\d*\b|الأعلى|الأفضل', re.I)
TINTS = F.TINTS
READERS = 4           # v5.10.476 (3 before)
PER_ROW = 20


def _row_of(item):
    """A cached row item (skinui/rows) as a section's row."""
    info, art, ids = item.get('info') or {}, item.get('art') or {}, item.get('ids') or {}
    kind = 'series' if (info.get('mediatype') or '') in ('tvshow', 'season', 'episode') else 'movie'
    tmdb = str(ids.get('tmdb') or '')
    year = str(info.get('year') or '')
    try:
        rating = round(float(info.get('rating') or 0), 1)
    except (TypeError, ValueError):
        rating = 0
    try:
        runtime = int(float(info.get('duration') or 0)) // 60
    except (TypeError, ValueError):
        runtime = 0     # v5.10.524: 'N/A' and the like took the whole section with it
    return {'kind': kind, 'tmdb': tmdb, 'imdb': ids.get('imdb') or '', 'title': item.get('label') or info.get('title') or '',
            'poster': art.get('poster') or art.get('thumb') or '', 'fanart': art.get('fanart') or art.get('landscape') or '',
            'year': year if year not in ('0', '-1') else '', 'rating': rating or '', 'plot': info.get('plot') or '',
            'runtime': runtime, 'genres': list(info.get('genres') or []),
            'key': ('movie|%s' if kind == 'movie' else 'series|%s') % tmdb, 'path': item.get('path') or '',
            'work': (item.get('props') or {}).get('kind') in ('work', None, '') and bool(tmdb or ids.get('imdb'))}


class CollectionWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(CollectionWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.params = dict(kwargs.get('params') or {})
        self.state = kwargs.get('state') if isinstance(kwargs.get('state'), dict) else {}
        self.media = self.state.get('media') or 'all'
        self.ref = dict((k, self.params.get(k) or '') for k in ('set_id', 'group_id', 'folder_id', 'media_filter'))
        self.specs = []
        self.data = {}          # section number -> all its rows (before All/Movies/Series)
        self.shown = {}         # section number -> the rows on screen
        self.ranked = set()
        self.watched = set()
        self.next_title = None
        self.next_action = None
        self.next_grid = None
        self._threads = []
        self._lock = threading.Lock()
        self._inited = False
        self._closing = False
        self._moved = False
        self._resolved = set()  # sections read (with titles or empty): the focus waits for the ones above

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        for key, text in (('all', _t('الكل', 'All')), ('movie', _t('أفلام', 'Movies')), ('series', _t('مسلسلات', 'Series')),
                          ('title', _t('كوليكشن', 'Collection')), ('hello', self.params.get('t') or '')):
            self.setProperty('dw.t.%s' % key, text)
        self.setProperty('dw.media', self.media)
        for n in range(1, ALL + 1):
            self.setProperty('dw.s%d.off' % n, '1')
        from ..art_tone import Tone
        self.tone = Tone(self)
        self._later(self._read_watched)
        self._later(self._load)
        self._later(self._focus_late)

    def _later(self, job):
        from .. import profile_scope
        thread = threading.Thread(target=profile_scope.capture(job), name='DexHub-collection')
        thread.daemon = True
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def _read_watched(self):
        from .. import calendar_feed
        self.watched = set(calendar_feed.watched_keys())

    def close_window(self):
        self._closing = True
        self.close()

    def on_cover(self):
        pass

    def on_uncover(self):
        pass

    def on_close(self):
        self._closing = True
        try:
            self.tone.stop()
        except Exception:
            pass
        from .app import join_briefly
        join_briefly(self._threads)

    # ---------------------------------------------------------- reading
    def _load(self):
        from ..skinui import folderpage, rows
        from . import window as W
        if self.params.get('dw'):
            # v5.10.507: a list of a DexWorld collection: it first, then the
            # collection's other lists, its titles' posters as the page's wall
            from .. import dexworld as DW
            self.ref = {'set_id': 'dexworld', 'group_id': self.params['dw'], 'folder_id': self.params.get('dwm') or '',
                        'media_filter': ''}
            try:
                sources, members = DW.fold_sources(self.params['dw'], self.params.get('dwm') or '')
            except Exception:
                xbmc.log('[DexHub] collection page: %s' % traceback.format_exc(), xbmc.LOGWARNING)
                sources, members = [], []
            if self._closing:
                return      # v5.10.524: closed while its lists were read (nothing more is written to it)
            first = members[0] if members else {}
            folder = {'name': self.params.get('t') or first.get('title') or ''}
            network = DW.NETWORK_ID.match(str(first.get('id') or ''))
            if network:
                # v5.10.508: a network's clear logo in place of its name
                folder['clearlogo'] = DW.network_logo(network.group(1), fetch=True)
            wall = DW._posters(first) if first else []
            if not wall and first and not self._closing:
                try:
                    DW.row_page(first['id'], 1)
                    wall = DW._posters(first)
                except Exception:
                    wall = []
            with self._lock:
                if self._closing:
                    return
                for n in range(8):
                    self.setProperty('dw.wall.p%d' % (n + 1), wall[n % len(wall)] if wall else '')
            owner = DW.rows_by_id(DW.page()).get(self.params['dw']) or {}
            self.params.setdefault('group_name', owner.get('title') or '')
        else:
            try:
                home = rows.HeadlessHome(rows.app(), 'all')
                folder, sources = W.BrowseWindow.__dict__['_folder_sources'](home, self.ref)
            except Exception:
                xbmc.log('[DexHub] collection page: %s' % traceback.format_exc(), xbmc.LOGWARNING)
                folder, sources = None, []
        if self._closing:
            return
        folder = folder or {}
        self.specs = folderpage._specs(self.ref, sources)
        wrap = getattr(rows.api(), '_wrap_tokenized_url', None) or (lambda u: u)
        title = folder.get('name') or self.params.get('t') or ''
        logo = str(folder.get('clearlogo') or '') or (str(folder.get('poster') or '') if folder.get('hide_title') else '')
        count = len(self.specs)
        self.setProperty('dw.t.hello', title)
        self.setProperty('dw.t.logo', wrap(logo) if logo else '')
        self.setProperty('dw.t.title', self._group_name() or _t('كوليكشن', 'Collection'))
        # the collection's own picture is the backdrop until a title has the focus
        backdrop = str(folder.get('background') or folder.get('poster') or '')
        if backdrop and not self.getProperty('dw.f.fanart'):
            self.setProperty('dw.f.fanart', wrap(backdrop))
        self.setProperty('dw.t.sub', (_t('%d كتالوج', '%d catalogs') % count) if count != 1 else _t('كتالوج واحد', 'One catalog'))
        if not self.specs:
            self.setProperty('dw.t.sub', _t('ما فيه كتالوجات في هالكوليكشن', 'No catalogs in this collection'))
            return
        for n, spec in enumerate(self.specs[:SLOTS], 1):
            if TOP.search(spec.get('title') or ''):
                self.ranked.add(n)
                self.setProperty('dw.s%d.rank' % n, '1')
            self.setProperty('dw.s%d.heading' % n, spec.get('title') or '')
            self.setProperty('dw.s%d.eyebrow' % n, spec.get('sub') or (_t('كتالوج %d من %d', 'Catalog %d of %d') % (n, count)))
            # v5.10.525: a section shows once its titles are in (_show), not
            # empty before: Kodi gives an empty list no focus, so the first
            # section in was the page's last focusable one, and Kodi scrolls
            # the page to its end for that one (DexWorld's Netflix opened on
            # Pixar and A24, the cursor out of sight at the top)
        if count > SLOTS:
            self.setProperty('dw.s%d.eyebrow' % ALL, _t('%d كتالوج', '%d catalogs') % count)
            self.setProperty('dw.s%d.heading' % ALL, _t('كل الكتالوجات', 'Every catalog'))
            self.setProperty('dw.s%d.off' % ALL, '')
            self._show_catalogs()
        # cached copies first, at once; then what is old or missing, three at a time
        todo = []
        for n, spec in enumerate(self.specs[:SLOTS], 1):
            data = rows.C.read_json(rows.C.row_file(folderpage.TAB, spec['id']))
            if data and data.get('items'):
                self._take(n, data)
            if not data or not rows.C.fresh_copy(data, rows.ttl_of(spec)):
                todo.append((n, spec))
        # v5.10.476: "All" (every catalog together, the slowest) after the single ones
        todo.sort(key=lambda job: 1 if (job[1].get('params') or {}).get('action') == 'collection_nuvio_sources_open' else 0)
        from concurrent.futures import ThreadPoolExecutor

        def read(job):
            n, spec = job
            if self._closing:
                return
            try:
                data = rows.fetch(folderpage.TAB, spec['id'], spec, wait_other=False)
            except Exception:
                data = None
            if data is not None and not self._closing:
                self._take(n, data)
            elif n not in self.data and not self._closing:
                self._show(n, [])
        # the workers end with the block: nothing outlives the page (Kodi 22)
        with ThreadPoolExecutor(max_workers=READERS, thread_name_prefix='DexHub-collection-read') as pool:
            list(pool.map(read, todo))
        if count > SLOTS and not self._closing:
            self._show_catalogs()

    def _group_name(self):
        """The collection row the card was in ("Streaming Services")."""
        if self.params.get('group_name'):
            return self.params['group_name']
        try:
            from .. import collection_sets
            for sets in collection_sets.active_sets():
                for entry in sets.get('entries') or []:
                    if str(entry.get('id') or '') == self.ref['group_id']:
                        return entry.get('name') or entry.get('title') or ''
        except Exception:
            pass
        return ''

    def _take(self, n, data):
        found = [_row_of(item) for item in (data.get('items') or [])[:PER_ROW]]
        found = [row for row in found if row['title'] and (row['poster'] or row['fanart'])]
        if n == 1:
            self._fill_story(found[:8])
        self.data[n] = found
        self._show(n, found)

    def _fill_story(self, found):
        """The feature cards tell the story: what the cached copy lacks is read from TMDb."""
        missing = [row for row in found if row['tmdb'] and not (row['plot'] and row['fanart'])]
        for row, full in zip(missing, F._details_many([(row['kind'], row['tmdb']) for row in missing])):
            for key in ('plot', 'fanart', 'year', 'rating', 'runtime', 'genres'):
                if full.get(key) and not row.get(key):
                    row[key] = full[key]

    def _show(self, n, found):
        rows_ = [row for row in found if self.media == 'all' or row['kind'] == self.media]
        ranked = n in self.ranked
        items = []
        for index, row in enumerate(rows_):
            art = {'thumb': row['poster'] or row['fanart'], 'fanart': row['fanart'] or row['poster']}
            watched = '1' if row['key'] in self.watched else ''
            if ranked:
                items.append(_item(row['title'], row['year'], art, watched=watched,
                                   rank='%s/resources/media/homeui/rank/rank_side_%d.png' % (self.app.addon_path, min(index + 1, 20))))
            elif SHAPES[n - 1] in ('posters',):
                items.append(_item(row['title'], row['year'], art, watched=watched))
            else:
                items.append(_item(row['title'], _meta(row), art, watched=watched, plot=row['plot']))
        with self._lock:
            if self._closing:
                return
            self.shown[n] = rows_
            control = self.getControl((460 if ranked else 400) + n)
            pos = control.getSelectedPosition()
            control.reset()
            control.addItems(items)
            if 0 <= pos < len(items):
                control.selectItem(pos)
            self.setProperty('dw.s%d.off' % n, '' if rows_ else '1')
            self.setProperty('dw.s%d.src' % n, (_t('%d عنوان', '%d titles') % len(rows_)) if rows_ else '')
            self._wire()
            self._resolved.add(n)
            # v5.10.519: back from a title opened here, the cursor is on it again
            back = self.state.get('back_to')
            again = None
            if back and back[0] == n and not self._moved:
                self.state.pop('back_to', None)
                again = next((i for i, row in enumerate(rows_) if row['key'] == back[1]), None)
                if again is not None:
                    control.selectItem(again)
                    self._moved = True
            target = self._first_ready(waited=getattr(self, '_late', False))
            first = (target and not self._moved and not self.state.get('back_to')
                     and self.getFocusId() in KINDS)
            if first:
                self._moved = True
        if again is not None:
            self._focus_section(self._list_of(n))
            self._describe(self._list_of(n))
        elif first:
            # v5.10.510: the first section in order, not the first to arrive (a
            # network's rows come side by side: a lower one took the focus)
            self._focus_section(self._list_of(target))
            self._describe(self._list_of(target))

    def _focus_section(self, cid):
        """v5.10.523: the focus on a section, the page scrolled to it. Kodi
        scrolls its list of sections to a focused one only when its group
        shows, and the group shows a frame after its property: the focus
        came in while it was hidden, the page stayed where it was (at the
        bottom after a Down, the Netflix page on its last rows). The focus
        is set again once the group shows, and the page scrolls to it."""
        take_focus(self, cid)
        xbmc.sleep(150)
        if not self._closing and self.getFocusId() == cid:
            self.setFocusId(cid)

    def _first_ready(self, waited=False):
        """The first section with titles once every section above it is read
        (``waited``: the first with titles, whatever is still on its way)."""
        for k in range(1, min(len(self.specs), SLOTS) + 1):
            if self.shown.get(k):
                return k
            if k not in self._resolved and not waited:
                return 0
        return 0

    FOCUS_LATE = 8      # v5.10.523: after this, Down and the focus take the first section ready
                        # (20 in 5.10.520: the focus sat on All while a slow first section came)

    def _focus_late(self):
        """A section still on its way after FOCUS_LATE seconds: the focus goes
        to the first one ready, and Down from All/Movies/Series too (_wire)."""
        from .app import page_pause
        if page_pause(self, self.FOCUS_LATE):
            return
        with self._lock:
            self.state.pop('back_to', None)     # its section did not come: the first one ready
            self._late = True
            self._wire()
            target = self._first_ready(waited=True)
            go = target and not self._moved and self.getFocusId() in KINDS
            if go:
                self._moved = True
        if go:
            self._focus_section(self._list_of(target))
            self._describe(self._list_of(target))

    def _show_catalogs(self):
        cards = []
        for index, spec in enumerate(self.specs):
            posters = [row['poster'] for row in (self.data.get(index + 1) or []) if row['poster']][:3]
            cards.append(_item(spec.get('title') or '', spec.get('sub') or '', {'thumb': ''},
                               tint=TINTS[index % len(TINTS)], solo='',
                               **dict(('p%d' % k, posters[k] if k < len(posters) else '') for k in range(3))))
        with self._lock:
            if self._closing:
                return
            self.shown[ALL] = list(self.specs)
            control = self.getControl(400 + ALL)
            pos = control.getSelectedPosition()
            control.reset()
            control.addItems(cards)
            if 0 <= pos < len(cards):
                control.selectItem(pos)
            self._wire()

    # ---------------------------------------------------------- moving
    def _list_of(self, n):
        return (460 if n in self.ranked else 400) + n

    def _number_of(self, cid):
        if 461 <= cid <= 460 + SLOTS:
            return cid - 460
        if 401 <= cid <= 400 + ALL:
            return cid - 400
        return 0

    def _wire(self):
        try:
            chain = [self._list_of(n) for n in range(1, ALL + 1) if self.shown.get(n)]
            media_btn = [cid for cid, kind in KINDS.items() if kind == self.media][0]
            # v5.10.523: Down from All/Movies/Series goes to the first section in
            # order once it is in, and waits there while one above is on its
            # way. It went to the first list that had arrived: a lower section,
            # or the catalogs list at the bottom (in before any section), so a
            # first open's Down landed on the last row.
            target = self._first_ready(waited=getattr(self, '_late', False))
            if target:
                first = self._list_of(target)
            elif chain and len(self._resolved) >= min(len(self.specs), SLOTS):
                first = chain[0]        # every section read, none with titles
            else:
                first = media_btn
            for cid in KINDS:
                self.getControl(cid).controlDown(self.getControl(first))
            previous = media_btn
            for index, cid in enumerate(chain):
                control = self.getControl(cid)
                control.controlUp(self.getControl(previous))
                control.controlDown(self.getControl(chain[index + 1] if index + 1 < len(chain) else cid))
                previous = cid
        except (RuntimeError, IndexError, TypeError):
            pass

    def _row_at(self, cid):
        n = self._number_of(cid)
        rows_ = self.shown.get(n) or []
        try:
            pos = self.getControl(cid).getSelectedPosition()
        except RuntimeError:
            return 0, None
        return (n, rows_[pos]) if 0 <= pos < len(rows_) else (n, None)

    def _describe(self, cid):
        n, row = self._row_at(cid)
        if not row or n == ALL:
            return
        self.setProperty('dw.f.fanart', row.get('fanart') or row.get('poster') or '')
        self.tone.follow(row.get('poster') or '')

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self.close_window()
            return
        focus = self.getFocusId()
        if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
            # v5.10.523: Down on All/Movies/Series before the first section is
            # in goes nowhere (_wire), and the focus still goes to it when it comes
            if not (aid == A_DOWN and focus in KINDS and not self._first_ready(waited=getattr(self, '_late', False))):
                self._moved = True
        if aid in (A_LEFT, A_RIGHT) and KINDS.get(focus) not in (None, self.media):
            self._set_media(KINDS[focus])
        elif self._number_of(focus):
            if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
                self._describe(focus)
            elif aid in (A_CONTEXT, A_MENU, A_PLAY):
                n, row = self._row_at(focus)
                if row and n != ALL:
                    self._play(row)

    def onFocus(self, cid):
        if self._number_of(cid):
            self._describe(cid)

    def _set_media(self, media):
        self.media = media
        self.state['media'] = media
        self.setProperty('dw.media', media)
        for n, found in list(self.data.items()):
            self._show(n, found)

    def onClick(self, cid):
        if cid in KINDS:
            if KINDS[cid] != self.media:
                self._set_media(KINDS[cid])
            return
        n, row = self._row_at(cid)
        if row is None:
            return
        if n == ALL:
            self.next_grid = (dict(row.get('params') or {}), row.get('title') or '')
            self.close_window()
        elif row.get('work'):
            query = {'m': row['kind'], 'title': row['title'], 'tmdb': row['tmdb'], 'imdb': row['imdb']}
            # v5.10.519: over this page, which stays (it closed and Back went to the Home)
            from ..skinui.items import title_over_window
            if title_over_window(self, query):
                return
            self.next_title = query
            self.state['back_to'] = (n, row['key'])
            self.close_window()
        elif row.get('path'):
            self.next_action = row['path']
            self.close_window()

    def _play(self, row):
        from .discover_page import DiscoverWindow
        DiscoverWindow._play(self, row)


def open_collection(app, params):
    """The page; a title or a catalog opened from it shows over the Home and the page comes back after it."""
    from . import ui_size
    from .app import _details_tile
    state = {}
    while True:
        window = CollectionWindow(ui_size.xml('dexhub_collection.xml'), app.addon_path, 'Default', '1080i',
                                  app=app, params=params, state=state)
        app._show(window)
        query, action, grid = window.next_title, window.next_action, window.next_grid
        del window
        if grid:
            route, title = grid
            try:
                app.open_grid(route, title=title)
            except Exception:
                xbmc.log('[DexHub] collection: grid failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
                return
            continue
        if action:
            xbmc.executebuiltin('RunPlugin("%s")' % action)
            return
        if not query:
            return
        try:
            from ..skinui.items import title_here, title_target
            skin_page = title_here()
        except Exception:
            skin_page, title_target = False, None
        if skin_page and title_target is not None:
            xbmc.executebuiltin('RunPlugin("%s")' % title_target(query))
            return
        try:
            app.open_details(_details_tile(query))
        except Exception:
            xbmc.log('[DexHub] collection: title page failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
