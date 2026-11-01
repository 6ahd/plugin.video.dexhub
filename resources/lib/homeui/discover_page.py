# -*- coding: utf-8 -*-
"""The Discover page (v5.10.454; one page of sections since v5.10.458).

WindowXMLDialog on dexhub_discover*.xml (tools/s458/make_discover.py), after
the user's example pictures: a greeting with All, Movies, Series (101-103),
then sections in a vertical grouplist (900), each a small eyebrow over a bold
heading and its own card shape:

  401 Trending now (Trakt)        tall posters, the name on the picture
  402 For you (Trakt, linked)     big wide cards with the story
  403 Top 10 this week (Trakt)    posters with big numbers
  404 Weekend plan                an evening each: Thursday, Friday, Saturday
  405 Your time (TMDb)            chips 451 and posters
  406 Made for the big screen     TMDb's top rated, wide pictures
  407 Your mood (TMDb)            chips 452 and posters
  408 Anticipated (Trakt)         posters with their date
  409 Collections                 tinted cards with fanned posters
  410 New and coming              the calendar, a day over each card

Two workers read the sections (Trakt's first), each shows as it comes; one
that came back empty is hidden and the up/down links skip it. OK opens the
title, the context key plays a film.
"""
import datetime
import threading

import xbmc
import xbmcgui

from .. import discover_feed as F
from .. import lazy_tb as traceback
from .sport_page import take_focus

KINDS = {101: 'all', 102: 'movie', 103: 'series'}
SECTIONS = ((1, 'trending', 'tall'), (2, 'foryou', 'feature'), (3, 'top10', 'ranked'), (4, 'weekend', 'days'),
            (5, 'time', 'posters'), (6, 'toprated', 'wide'), (7, 'mood', 'posters'), (8, 'anticipated', 'posters'),
            (9, 'collections', 'fan'), (10, 'calendar', 'days'))
NUMBER = dict((key, n) for n, key, _shape in SECTIONS)
CHIPS = {451: 'time', 452: 'mood'}
CHIPS_OF = {'time': 451, 'mood': 452}
A_LEFT, A_RIGHT, A_UP, A_DOWN = 1, 2, 3, 4
A_CONTEXT, A_MENU, A_PLAY = 117, 163, 79
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
MONTHS_AR = ('يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر')
MONTHS_EN = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
DAYS_AR = ('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')
DAYS_EN = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def _t(ar, en):
    return en if F.english() else ar


def _item(label, label2='', art=None, **props):
    item = xbmcgui.ListItem(label, label2, offscreen=True)
    if art:
        item.setArt(art)
    for key, value in props.items():
        item.setProperty(key, '' if value is None else str(value))
    return item


def _date_words(day, weekday=True):
    english = F.english()
    text = '%d %s' % (day.day, (MONTHS_EN if english else MONTHS_AR)[day.month - 1])
    if weekday:
        text = '%s · %s' % ((DAYS_EN if english else DAYS_AR)[day.weekday()], text)
    return text


def _length(minutes):
    minutes = int(minutes or 0)
    if not minutes:
        return ''
    hours, rest = divmod(minutes, 60)
    if not hours:
        return _t('%d د', '%dm') % rest
    return (_t('%dس %dد', '%dh %dm') % (hours, rest)) if rest else (_t('%d ساعة', '%dh') % hours)


def _meta(row, with_kind=True):
    parts = [row.get('year') or '']
    if with_kind:
        parts.append(_t('مسلسل', 'Series') if row.get('kind') == 'series' else _t('فيلم', 'Movie'))
    if row.get('genres'):
        parts.append(row['genres'][0] or '')
    if row.get('rating'):
        parts.append('★ %s' % row['rating'])
    if row.get('kind') == 'movie':
        parts.append(_length(row.get('runtime')))
    return '  ·  '.join(part for part in parts if part)


class DiscoverWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(DiscoverWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.state = kwargs.get('state') if isinstance(kwargs.get('state'), dict) else {}
        self.media = self.state.get('media') or 'all'
        self.chips = self.state.get('chips') or {'time': 'mid', 'mood': 'tense'}
        self.rows = {}
        self.watched = set()
        self.next_title = None
        self.next_action = None
        self._gen = 0
        self._threads = []
        self._lock = threading.Lock()
        self._inited = False
        self._closing = False
        self._moved = False
        self._tiles = []

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        hour = datetime.datetime.now().hour
        hello = (_t('صباح الخير، وش نشوف؟', 'Good morning, what is next?') if 5 <= hour < 12 else
                 _t('هلا، وش نشوف الليلة؟', 'Hey there, what is next?'))
        for key, text in (('title', _t('اكتشف', 'Discover')), ('hello', hello),
                          ('sub', _t('رائج على Trakt، مختار لك، وروائع TMDb في صفحة وحدة',
                                     'Trending on Trakt, picked for you and TMDb classics on one page')),
                          ('all', _t('الكل', 'All')), ('movie', _t('أفلام', 'Movies')), ('series', _t('مسلسلات', 'Series'))):
            self.setProperty('dw.t.%s' % key, text)
        try:
            from .. import trakt as K
            linked = K.linked()
        except Exception:
            linked = False
        self.setProperty('dw.t.account', _t('Trakt مربوط ✓', 'Trakt linked ✓') if linked else
                         _t('اربط Trakt من الإعدادات لاقتراحات لك', 'Link Trakt in Settings for picks of your own'))
        self.setProperty('dw.media', self.media)
        from ..art_tone import Tone
        self.tone = Tone(self)
        self._words(personal=linked)
        for chip_id in CHIPS:
            self._fill_chips(chip_id)
        self._later(self._read_watched)
        self._load_all()

    def _later(self, job):
        from .. import profile_scope
        thread = threading.Thread(target=profile_scope.capture(job), name='DexHub-discover')
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
        # no thread of this page outlives it: Python ends with the script
        for thread in self._threads:
            thread.join(9.0)

    # ---------------------------------------------------------- words
    def _words(self, personal=False):
        words = {
            'trending': (_t('حديث الناس هالأسبوع', 'What everyone is watching'), _t('رائج الآن', 'Trending now')),
            'foryou': ((_t('مختار لك من Trakt', 'Picked for you on Trakt') if personal else
                        _t('على حسب آخر شي شفته', 'From what you watched last')), _t('مقترح لك', 'Your next obsession')),
            'top10': (_t('الأكثر مشاهدة هالأسبوع', 'Most watched this week'), 'Top 10'),
            'weekend': (_t('على قد سهراتك', 'Fits your evenings'), _t('خطة الويكند', 'Your weekend plan')),
            'time': (_t('مختار على وقتك', 'Picked to fit'), _t('كم عندك وقت الليلة؟', 'How much time tonight?')),
            'toprated': (_t('الأعلى تقييمًا على TMDb', 'Best rated on TMDb'), _t('صُنع للشاشة الكبيرة', 'Made for the big screen')),
            'mood': (_t('على مزاجك', 'Tuned to your taste'), _t('وش مزاجك الليلة؟', 'What is your mood?')),
            'anticipated': (_t('ينتظره الكل على Trakt', 'Everyone is waiting'), _t('المترقَّب', 'Most anticipated')),
            'collections': (_t('مجموعاتك', 'Your collections'), _t('الكوليكشن', 'Collections')),
            'calendar': (_t('من تقويمك', 'From your calendar'), _t('الجديد والقادم', 'New and coming')),
        }
        for n, key, _shape in SECTIONS:
            eyebrow, heading = words[key]
            self.setProperty('dw.s%d.eyebrow' % n, eyebrow)
            self.setProperty('dw.s%d.heading' % n, heading)

    def _chip_list(self, key):
        if key == 'time':
            return (('short', _t('أقل من 90 د', 'Under 90 min')), ('mid', _t('ساعتين تقريبًا', 'About 2 hours')),
                    ('long', _t('سهرة طويلة', 'A long night')))
        return (('feel', _t('يفرّح', 'Feel-good')), ('tense', _t('يحبس الأنفاس', 'Edge of your seat')),
                ('mind', _t('يشغّل العقل', 'Mind-bending')))

    def _fill_chips(self, chip_id):
        key = CHIPS[chip_id]
        control = self.getControl(chip_id)
        pos = control.getSelectedPosition()
        control.reset()
        now = self.chips.get(key)
        control.addItems([_item(text, sel='1' if value == now else '') for value, text in self._chip_list(key)])
        if pos > 0:
            control.selectItem(pos)

    # ---------------------------------------------------------- reading
    def _load_all(self):
        self._gen += 1
        gen = self._gen
        with self._lock:
            self.rows = {}
            for n, _key, _shape in SECTIONS:
                self.setProperty('dw.s%d.off' % n, '')
                self.setProperty('dw.s%d.src' % n, '')
                self.getControl(400 + n).reset()
            self._wire()
        media = self.media

        def trakt_side():
            trending, _src = self._section(gen, 'trending', lambda: F.trending(media))
            foryou, _personal = self._section(gen, 'foryou', lambda: F.for_you(media), src_of=lambda p: 'Trakt' if p else 'TMDb')
            self._section(gen, 'top10', lambda: F.most_watched(media))
            top, _src = self._section(gen, 'toprated', lambda: F.top_rated(media))
            self._section(gen, 'weekend', lambda: (F.weekend([foryou, trending, top]), ''))
            self._section(gen, 'anticipated', lambda: F.anticipated(media))

        def tmdb_side():
            self._section(gen, 'time', lambda: (F.by_time(self.chips['time'], media, 'popular', F.empty_filter(), (),
                                                          limit=F.PAGE), 'TMDb'))
            self._section(gen, 'mood', lambda: (F.by_mood(self.chips['mood'], media, 'popular', F.empty_filter(), (),
                                                          limit=F.PAGE), 'TMDb'))
            self._section(gen, 'calendar', lambda: (F.tickets(media, limit=F.PAGE), ''))
            self._tiles = F.folders()
            self._section(gen, 'collections', lambda: (F.cards(self._tiles), ''))
            gone = lambda: self._closing or gen != self._gen
            if self._tiles and F.read_posters(self._tiles, self.app.api(), cancelled=gone) and not gone():
                self._show(gen, 'collections', F.cards(self._tiles), '')
        self._later(trakt_side)
        self._later(tmdb_side)

    def _section(self, gen, key, read, src_of=None):
        """Read one section (a worker) and show it: (rows, what read returned second)."""
        if self._closing or gen != self._gen:
            return [], ''
        try:
            rows, second = read()
        except Exception:
            xbmc.log('[DexHub] discover %s: %s' % (key, traceback.format_exc()), xbmc.LOGWARNING)
            rows, second = [], ''
        self._show(gen, key, rows or [], src_of(second) if src_of else second)
        return rows or [], second

    def _show(self, gen, key, rows, src):
        n = NUMBER[key]
        items = [self._row_item(key, row, index) for index, row in enumerate(rows)]
        with self._lock:
            if self._closing or gen != self._gen:
                return
            self.rows[n] = rows
            control = self.getControl(400 + n)
            pos = control.getSelectedPosition()
            control.reset()
            control.addItems(items)
            if 0 <= pos < len(items):
                control.selectItem(pos)
            self.setProperty('dw.s%d.off' % n, '' if rows else '1')
            self.setProperty('dw.s%d.src' % n, (_t('من %s', 'from %s') % src) if src and rows else '')
            self._wire()
            first = not self._moved and rows and key == 'trending' and self.getFocusId() in KINDS
        if first:
            # the page opens on its first section once it has titles
            take_focus(self, 400 + n)
            self._describe(400 + n)

    def _row_item(self, key, row, index):
        day = None
        if key == 'weekend':
            day, row = row
        art = {'thumb': row.get('poster') or row.get('cover') or '', 'fanart': row.get('fanart') or row.get('poster') or ''}
        watched = '1' if row.get('key') in self.watched else ''
        if key == 'collections':
            posters = row.get('posters') or []
            props = dict(('p%d' % n, posters[n] if n < len(posters) else '') for n in range(3))
            count = row.get('count') or 0
            sub = (_t('%d كتالوج', '%d catalogs') % count) if count != 1 else _t('كتالوج واحد', '1 catalog')
            return _item(row['title'], sub, art, tint=row.get('tint'), solo='' if posters else '1', **props)
        if key == 'weekend':
            name = (DAYS_EN if F.english() else DAYS_AR)[day.weekday()]
            if day.weekday() == 3:
                name = _t('ليلة الخميس', 'Thursday night')
            return _item(row.get('title') or '', _meta(row, with_kind=False), art, watched=watched, day=name)
        if key == 'calendar':
            when = _date_words(datetime.date.fromisoformat(row['date'])) if row.get('date') else ''
            return _item(row.get('title') or '', '  ·  '.join(p for p in (row.get('code'), row.get('sub')) if p), art, day=when)
        if key == 'top10':
            return _item(row.get('title') or '', row.get('year') or '', art, watched=watched,
                         rank='%s/resources/media/homeui/rank/rank_side_%d.png' % (self.app.addon_path, index + 1))
        if key == 'anticipated':
            sub = row.get('year') or ''
            try:
                sub = '%s %s' % (_date_words(datetime.date.fromisoformat(row['date'][:10]), weekday=False), row['date'][:4])
            except (KeyError, ValueError, TypeError):
                pass
            return _item(row.get('title') or '', sub, art, watched=watched)
        if key in ('time', 'mood'):
            return _item(row.get('title') or '', row.get('year') or '', art, watched=watched)
        return _item(row.get('title') or '', _meta(row), art, watched=watched, plot=row.get('plot') or '')

    # ---------------------------------------------------------- moving
    def _focusables(self):
        """The ids down the page: each shown section (its chips first)."""
        out = []
        for n, key, _shape in SECTIONS:
            if not self.rows.get(n):
                continue
            if key in CHIPS_OF:
                out.append(CHIPS_OF[key])
            out.append(400 + n)
        return out

    def _wire(self):
        """Up and down skip the sections not shown (empty or still reading)."""
        try:
            chain = self._focusables()
            media_btn = [cid for cid, kind in KINDS.items() if kind == self.media][0]
            first = chain[0] if chain else media_btn
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
        n = cid - 400
        rows = self.rows.get(n) or []
        try:
            pos = self.getControl(cid).getSelectedPosition()
        except RuntimeError:
            return None, None
        if not 0 <= pos < len(rows):
            return None, None
        row = rows[pos]
        if SECTIONS[n - 1][1] == 'weekend':
            row = row[1]
        return SECTIONS[n - 1][1], row

    def _describe(self, cid):
        _key, row = self._row_at(cid)
        if not row:
            return
        self.setProperty('dw.f.fanart', row.get('fanart') or row.get('poster') or '')
        self.tone.follow(row.get('poster') or row.get('cover') or (row.get('posters') or [''])[0])

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self.close_window()
            return
        focus = self.getFocusId()
        if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
            self._moved = True
        if aid in (A_LEFT, A_RIGHT) and KINDS.get(focus) not in (None, self.media):
            self._set_media(KINDS[focus])
        elif aid in (A_LEFT, A_RIGHT) and focus in CHIPS:
            self._pick_chip(focus)
        elif 401 <= focus <= 410:
            if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
                self._describe(focus)
            elif aid in (A_CONTEXT, A_MENU, A_PLAY):
                _key, row = self._row_at(focus)
                if row:
                    self._play(row)

    def onFocus(self, cid):
        if 401 <= cid <= 410:
            self._describe(cid)

    def _set_media(self, media):
        self.media = media
        self.state['media'] = media
        self.setProperty('dw.media', media)
        self._load_all()

    def _pick_chip(self, chip_id):
        key = CHIPS[chip_id]
        chips = self._chip_list(key)
        pos = self.getControl(chip_id).getSelectedPosition()
        if not 0 <= pos < len(chips) or chips[pos][0] == self.chips.get(key):
            return
        value = chips[pos][0]
        self.chips[key] = value
        self.state['chips'] = self.chips
        self._fill_chips(chip_id)
        gen, media = self._gen, self.media
        read = F.by_time if key == 'time' else F.by_mood

        def job():
            rows = read(value, media, 'popular', F.empty_filter(), (), limit=F.PAGE)
            if self.chips.get(key) == value:
                self._show(gen, key, rows, 'TMDb')
        self._later(job)

    def onClick(self, cid):
        if cid in KINDS:
            if KINDS[cid] != self.media:
                self._set_media(KINDS[cid])
        elif cid in CHIPS:
            self._pick_chip(cid)
        elif 401 <= cid <= 410:
            _key, row = self._row_at(cid)
            if row:
                self._open(row)

    # ---------------------------------------------------------- doing
    def _open(self, row):
        if row.get('kind') == 'folder':
            from ..skinui import folderpage
            self.next_action = folderpage.page_url(row.get('ref') or {}, row.get('title') or '')
        else:
            self.next_title = {'m': row.get('kind'), 'title': row.get('title') or '', 'tmdb': row.get('tmdb') or '',
                               'imdb': row.get('imdb') or ''}
        self.close_window()

    def _play(self, row):
        if row.get('kind') == 'folder' or row.get('kind') != 'movie':
            self._open(row)
            return
        from .. import reminders, tmdb_direct as T
        imdb = row.get('imdb')
        if not imdb:
            try:
                imdb = (T._request('/movie/%s/external_ids' % row['tmdb'], {}, timeout=6, max_attempts=1) or {}).get('imdb_id')
            except Exception:
                imdb = ''
        if not imdb:
            self._open(row)
            return
        self.next_action = reminders._opener({'kind': 'movie', 'imdb': imdb, 'tmdb': row['tmdb'], 'title': row.get('title') or '',
                                              'poster': row.get('poster') or '', 'fanart': row.get('fanart') or ''}, play=True)
        self.close_window()


def page_is_calendar(row):
    return 'code' in row and 'out' in row


def open_discover(app):
    """The page; a title opened from it shows over the Home and the page comes back after it."""
    from . import ui_size
    from .app import _details_tile
    state = {}
    while True:
        window = DiscoverWindow(ui_size.xml('dexhub_discover.xml'), app.addon_path, 'Default', '1080i', app=app, state=state)
        app._show(window)
        query, action = window.next_title, window.next_action
        del window
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
            xbmc.log('[DexHub] discover: title page failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
