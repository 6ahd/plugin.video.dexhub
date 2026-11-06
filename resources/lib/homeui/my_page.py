# -*- coding: utf-8 -*-
"""The My page (v5.10.468): the Library and the Calendar on one page.

WindowXMLDialog on dexhub_my*.xml (tools/s468/make_my.py), after the user's
pictures of a tracking app, in Discover's look:

  453/406  v5.10.534: pick up where you left, from every linked service (the
           same list as My watching): a chip per service; OK plays, Menu opens
           the title. The chips filter the watchlist too.
  451/401  this week: the days (today in red), the chosen day's episodes and
           films; the last chip opens the full calendar
  402      today, this month (against the last), the genres you watch
  403      the last 13 weeks as a heatmap, the shows you watched most this month
  404      favourites, watching, watchlist: a count over fanned posters
  405      the watchlist as tickets

The numbers come from Trakt's history kept by my_feed (shown at once from
the saved copy, read again in the background). Up from the days goes back
to the Home's bar, where the page opened from.
"""
import datetime
import threading

import xbmc
import xbmcgui

from .. import lazy_tb as traceback
from .. import my_feed as F
from .sport_page import take_focus

A_LEFT, A_RIGHT, A_UP, A_DOWN = 1, 2, 3, 4
A_CONTEXT, A_MENU = 117, 163
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
CHAIN = (453, 406, 451, 401, 407, 408, 402, 403, 404, 405)
SECTION_OF = {453: 6, 406: 6, 407: 8, 408: 9, 402: 2, 403: 3, 404: 4, 405: 5}
DAYS_AR = ('اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت', 'أحد')
DAYS_EN = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
MONTHS_AR = ('يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر')
MONTHS_EN = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
             'November', 'December')
TINTS = ('FF1E3A3B', 'FF4A2B20', 'FF22402F', 'FF372B4A', 'FF3B3320')
HEAT = {-1: '00000000', 0: '1FFFFFFF', 1: '596D6AF2', 2: '8C6D6AF2', 3: 'CC6D6AF2', 4: 'FF7C79FF'}
GENRES_AR = {'drama': 'دراما', 'comedy': 'كوميديا', 'crime': 'جريمة', 'action': 'أكشن', 'thriller': 'إثارة',
             'science-fiction': 'خيال علمي', 'fantasy': 'فانتازيا', 'mystery': 'غموض', 'horror': 'رعب',
             'romance': 'رومانسي', 'animation': 'رسوم متحركة', 'adventure': 'مغامرة', 'documentary': 'وثائقي',
             'family': 'عائلي', 'anime': 'أنمي', 'reality': 'واقع', 'war': 'حرب', 'history': 'تاريخ', 'other': 'غيره'}


def _t(ar, en):
    return en if F.english() else ar


def _item(label='', label2='', art=None, **props):
    item = xbmcgui.ListItem(label, label2, offscreen=True)
    if art:
        item.setArt(art)
    for key, value in props.items():
        item.setProperty(key, '' if value is None else str(value))
    return item


def _genre(name):
    if F.english():
        return 'Other' if name == 'other' else name.replace('-', ' ').title()
    return GENRES_AR.get(name, name)


def _ids(row):
    canonical = str(row.get('canonical_id') or '')
    tmdb = canonical.split(':', 1)[1] if canonical.startswith('tmdb:') else ''
    imdb = canonical if canonical.startswith('tt') else ''
    kind = 'series' if (row.get('media_type') or '') in ('series', 'anime', 'tv', 'show') else 'movie'
    return kind, tmdb, imdb


class MyWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(MyWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.state = kwargs.get('state') if isinstance(kwargs.get('state'), dict) else {}
        self.next_title = None
        self.next_action = None
        self.week = []
        self.day_rows = []
        self.tickets = []
        self.ticket_all = []
        self.up_all = []
        self.up_rows = []
        self.picks = []
        self.past = []
        self.chips = []
        self.src = 'all'
        self.names = {}
        self.lists = []
        self._threads = []
        self._lock = threading.Lock()
        self._inited = False
        self._closing = False
        self._moved = False
        self._ready = False
        self._day = None

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('my.t.title', _t('صفحتي', 'My'))
        self.setProperty('my.t.hello', _t('مشاهداتك', 'Your watching'))
        linked = F.linked()
        self.setProperty('my.t.account', _t('Trakt مربوط ✓', 'Trakt linked ✓') if linked else
                         _t('اربط Trakt من الإعدادات لإحصائيات أدق', 'Link Trakt in Settings for full numbers'))
        words = ((7, _t('سلسلة المشاهدة', 'Your streak'), '', ''),
                 (8, _t('مختار لك', 'Picked for you'), _t('مقترحات', 'Recommended'), ''),
                 (9, _t('اللي شفته', 'What you watched'), _t('السجل', 'History'), ''),
                 (6, _t('من كل خدماتك', 'From all your services'), _t('كمّل من حيث وقفت', 'Pick up where you left'), ''),
                 (1, _t('هالأسبوع', 'This week'), _t('الجاي من تقويمك', 'Coming up'), _t('من تقويمك', 'From your calendar')),
                 (2, _t('بالأرقام', 'In numbers'), _t('وقتك مع الشاشة', 'Your screen time'), 'Trakt' if linked else 'Dex Hub'),
                 (3, _t('آخر ١٣ أسبوع', 'The last 13 weeks'), _t('سجل المشاهدة', 'Viewing history'), ''),
                 (4, _t('قوائمك', 'Your lists'), _t('المكتبة', 'Library'), ''),
                 (5, _t('قائمة المشاهدة', 'Watchlist'), _t('تذاكرك القادمة', 'Your next tickets'), ''))
        for n, eyebrow, heading, src in words:
            self.setProperty('my.s%d.eyebrow' % n, eyebrow)
            self.setProperty('my.s%d.heading' % n, heading)
            self.setProperty('my.s%d.src' % n, src)
        for n in (6, 7, 8, 9):
            self.setProperty('my.s%d.off' % n, '1')
        self.setProperty('my.up.empty', _t('ما فيه شي من هالخدمة', 'Nothing from this service'))
        self.setProperty('my.day.empty', _t('ما فيه شي ينزل هاليوم', 'Nothing comes out that day'))
        from ..art_tone import Tone
        self.tone = Tone(self)
        self._fill_week()
        self._fill_numbers()
        self._later(self._fill_lists)
        self._later(self._fill_services)
        self._later(self._fill_more)
        self._later(self._refresh)
        take_focus(self, 451)
        back = self.state.pop('back_to', None)
        if back:
            # v5.10.537: back from the full calendar or My watching, on the same spot
            self._moved = True
            if back[0] == 451:
                self.getControl(451).selectItem(back[1])
            else:
                take_focus(self, back[0])
                self.getControl(back[0]).selectItem(back[1])
        # the list can come before the first focus: whichever is last moves it
        self._ready = True
        if self.up_all and not self._moved:
            take_focus(self, 406)
            self._describe(406)

    def _later(self, job):
        from .. import profile_scope
        thread = threading.Thread(target=profile_scope.capture(job), name='DexHub-my')
        thread.daemon = True
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def _refresh(self):
        try:
            if F.refresh() and not self._closing:
                self._fill_numbers()
                self._fill_lists()
                self._fill_more()       # a first read: the history was empty before it
        except Exception:
            xbmc.log('[DexHub] my: refresh failed\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

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

    # ---------------------------------------------------------- the week
    def _fill_week(self):
        try:
            self.week = F.week()
        except Exception:
            xbmc.log('[DexHub] my: week\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            self.week = []
        today = datetime.date.today()
        names = DAYS_EN if F.english() else DAYS_AR
        items, pick = [], 0
        for index, (day, events) in enumerate(self.week):
            if day == today:
                pick = index
            items.append(_item(str(day.day), names[day.weekday()], day=day.isoformat(),
                               today='1' if day == today else '', sel='1' if day == today else ''))
        items.append(_item('', _t('التقويم', 'Calendar'), day='cal'))
        control = self.getControl(451)
        control.reset()
        control.addItems(items)
        control.selectItem(pick)
        self._show_day(pick)
        self.setProperty('my.s1.off', '' if self.week else '1')

    def _show_day(self, index):
        if not 0 <= index < len(self.week) or index == self._day:
            return
        self._day = index
        day, events = self.week[index]
        control = self.getControl(451)
        for n in range(control.size()):
            control.getListItem(n).setProperty('sel', '1' if n == index else '')
        rows = sorted(events, key=lambda ev: (ev.get('time') or '99', ev.get('title') or ''))
        self.day_rows = rows
        items = []
        for ev in rows:
            sub = '  ·  '.join(p for p in (ev.get('code') if ev.get('kind') == 'ep' else _t('فيلم', 'Film'), ev.get('time')) if p)
            items.append(_item(ev.get('title') or '', sub, {'thumb': ev.get('poster') or ev.get('fanart') or '',
                                                            'fanart': ev.get('fanart') or ''}))
        target = self.getControl(401)
        target.reset()
        target.addItems(items)
        self._wire()

    # ---------------------------------------------------------- the numbers
    def _fill_numbers(self):
        try:
            buckets = F.days()
            today, month, genres = F.today(buckets), F.month(buckets), F.genres(buckets)
            levels = F.heat(buckets)
        except Exception:
            xbmc.log('[DexHub] my: numbers\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
        english = F.english()
        now = datetime.date.today()
        hours = _t('س', 'h')
        stats = F.all_time()
        if stats.get('minutes'):
            self.setProperty('my.t.sub', _t('%s ساعة مشاهدة  ·  %s حلقة  ·  %s فيلم', '%s hours watched  ·  %s episodes  ·  %s films') % (
                '{:,}'.format(stats['minutes'] // 60), '{:,}'.format(stats.get('episodes') or 0), '{:,}'.format(stats.get('movies') or 0)))
        else:
            self.setProperty('my.t.sub', _t('مكتبتك وتقويمك وأرقامك في مكان واحد', 'Your library, calendar and numbers in one place'))
        props = {'card': 'today', 'head': _t('اليوم', 'Today'),
                 'side': '%d %s %d' % (now.day, (MONTHS_EN if english else MONTHS_AR)[now.month - 1][:3 if english else None], now.year),
                 'v1': F.hours_text(today['minutes']), 'u1': hours,
                 'v2': today['films'], 'u2': _t('فيلم', 'movies'), 'v3': today['eps'], 'u3': _t('حلقة', 'episodes')}
        for n, value in enumerate(today['bars']):
            props['h%d' % n] = value
        cards = [_item(**props)]
        delta = month['delta']
        arrow = '↑' if delta >= 0 else '↓'
        props = {'card': 'month', 'head': _t('مشاهدة هالشهر', "This month's viewing"),
                 'side': (MONTHS_EN if english else MONTHS_AR)[now.month - 1],
                 'v1': F.hours_text(month['minutes']), 'u1': hours,
                 'delta': '%s %s %s %s' % (arrow, F.hours_text(abs(delta)), hours, _t('عن الشهر اللي قبله', 'vs last month')),
                 'up': '1' if delta >= 0 else '', 'l1': _t('مسلسلات', 'TV'), 'l2': _t('أفلام', 'Movies'),
                 'x1': '%d/1' % now.month, 'x2': '%d/15' % now.month, 'x3': '%d/%d' % (now.month, month['count'])}
        for n in range(31):
            if n < month['count']:
                props['a%d' % n] = month['all'][n]
                props['t%d' % n] = month['tv'][n]
        cards.append(_item(**props))
        props = {'card': 'genre', 'head': _t('أنواعك', 'Genre mix'), 'side': _t('آخر ٩٠ يوم', 'Last 90 days'),
                 'v1': F.hours_text(genres['minutes']), 'u1': hours,
                 'v2': _t('أفلام: %d  ·  مسلسلات: %d', 'Movies: %d  ·  Shows: %d') % (genres['films'], genres['shows'])}
        for k, (name, share) in enumerate(genres['top'][:4]):
            props['g%dn' % k] = _genre(name)
            props['g%dp' % k] = '%d%%' % share
            props['g%db' % k] = F.step(share, 100) if share else ''
        cards.append(_item(**props))
        props = {'card': 'heat', 'head': _t('سجل المشاهدة', 'Viewing history'), 'side': _t('كل مربع يوم', 'A square a day')}
        for n, level in enumerate(levels):
            props['c%d' % n] = HEAT.get(level, HEAT[0])
        history = [_item(**props)]
        props = {'card': 'top', 'head': _t('أكثر ما تابعت', 'Your top shows'), 'side': _t('آخر ٣٠ يوم', 'Last 30 days')}
        top = month['top']
        most = max([m for _n, m, _id in top] + [1])
        for k, (name, minutes, _tmdb) in enumerate(top):
            props['s%dn' % k] = name
            props['s%db' % k] = F.step(minutes, most)
            props['s%dv' % k] = '%s %s' % (F.hours_text(minutes), hours)
        if not top:
            props['empty'] = _t('ما شفت مسلسلات آخر ٣٠ يوم', 'No shows watched in the last 30 days')
        history.append(_item(**props))
        with self._lock:
            if self._closing:
                return
            for cid, items in ((402, cards), (403, history)):
                control = self.getControl(cid)
                pos = control.getSelectedPosition()
                control.reset()
                control.addItems(items)
                if 0 <= pos < len(items):
                    control.selectItem(pos)
            self._wire()

    # ---------------------------------------------------------- the lists
    def _fill_lists(self):
        try:
            favourites, watchlist = F.favourites(), F.watchlist()
            count, shows = F.watching()
            going = [r for r in self.up_all if r.get('media') == 'series']
            count = max(count, len(going))
        except Exception:
            xbmc.log('[DexHub] my: lists\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
        posters = []
        try:
            from .. import discover_feed as D
            rows = D._details_many([('series', tmdb) for _title, tmdb, _kind in shows[:3] if tmdb])
            posters = [r.get('poster') for r in rows if r and r.get('poster')]
        except Exception:
            posters = []
        posters = posters or [r.get('poster') for r in going]

        def card(key, number, head, side, pics):
            pics = [p for p in pics if p][:3]
            return _item(card='count', key=key, v1='{:,}'.format(number), head=head, side=side,
                         **dict(('p%d' % (n + 1), pics[n] if n < len(pics) else '') for n in range(3)))
        self.lists = ['favs', 'watching', 'watchlist']
        love = (_t('اللي حطيتها في مفضلتك', 'Titles you love') if favourites else
                _t('من خيارات أي عمل', 'From any title\'s menu'))
        cards = [card('favs', len(favourites), _t('المفضلة', 'Favorites'), love,
                      [r.get('poster') for r in favourites]),
                 card('watching', count, _t('أتابعها', 'Watching'), _t('مسلسلات بديت فيها', 'Shows you started'), posters),
                 card('watchlist', len(watchlist), _t('قائمة المشاهدة', 'Watchlist'), _t('ناوي تشوفها', 'Saved for later'),
                      [r.get('poster') for r in watchlist])]
        self._posters(watchlist)
        self.ticket_all = watchlist
        with self._lock:
            if self._closing:
                return
            self.getControl(404).reset()
            self.getControl(404).addItems(cards)
            self._show_tickets()
            self._wire()

    def _posters(self, rows):
        """v5.10.537: every ticket has its poster: a service often saves none."""
        missing = []
        for row in rows[:30]:
            if row.get('poster'):
                continue
            kind, tmdb, imdb = _ids(row)
            if imdb:
                row['poster'] = 'https://images.metahub.space/poster/medium/%s/img' % imdb
            elif tmdb:
                missing.append((row, kind, tmdb))
        if not missing:
            return
        try:
            from .. import discover_feed as D
            found = dict((str(r.get('tmdb')), r) for r in D._details_many([(kind, tmdb) for _row, kind, tmdb in missing]))
        except Exception:
            found = {}
        for row, _kind, tmdb in missing:
            row['poster'] = (found.get(str(tmdb)) or {}).get('poster') or ''

    def _sources(self, row):
        return set(str(s).lower() for s in (row.get('sources') or [row.get('source')]) if s) - {'local'}

    def _show_tickets(self):
        """The watchlist, of the chosen service; each ticket says where it is from."""
        self.tickets = [r for r in self.ticket_all if self.src == 'all' or self.src in self._sources(r)][:30]
        tickets = []
        for index, row in enumerate(self.tickets):
            year = str(row.get('year') or '')
            sub = (_t('سنة %s', 'Released %s') % year) if year else (_t('مسلسل', 'Series') if _ids(row)[0] == 'series' else _t('فيلم', 'Film'))
            where = ' + '.join(sorted(self.names.get(s, s.title()) for s in self._sources(row)))
            sub = '  ·  '.join(x for x in (sub, where) if x)
            tickets.append(_item(row.get('title') or '', sub, {'poster': row.get('poster') or '', 'fanart': row.get('background') or ''},
                                 tint=TINTS[index % len(TINTS)]))
        control = self.getControl(405)
        control.reset()
        control.addItems(tickets)
        self.setProperty('my.s5.off', '' if tickets else '1')

    # ---------------------------------------------------------- every service
    def _fill_services(self):
        """v5.10.534: the page read Trakt's history and the calendar only: what
        is on the go on the other services was left on My watching."""
        from . import follow
        try:
            services = follow.linked()
        except Exception:
            services = []
        self.names = dict(follow.SERVICES)
        if services:
            self.setProperty('my.t.account', '%s ✓' % '  ·  '.join(name for _key, name in services))
        try:
            rows = follow.up_next(self.app)
        except Exception:
            xbmc.log('[DexHub] my: up next\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            rows = []
        with self._lock:
            if self._closing:
                return
            self.up_all = rows
            self.chips = [('all', _t('الكل', 'All'))] + list(services)
            for row in rows:
                # the servers and add-ons on the go, linked or not
                for src in sorted(row.get('srcs') or ()):
                    if src not in [key for key, _name in self.chips]:
                        self.chips.append((src, row.get('place') or src.title()))
            self._show_chips()
            self._show_up()
            self._show_tickets()
            self.setProperty('my.s6.off', '' if rows else '1')
            self._wire()
            first = bool(rows) and self._ready and not self._moved and self.getFocusId() == 451
        if first:
            take_focus(self, 406)
            self._describe(406)
        if not F.linked():
            # no Trakt: the counts and the month come from the others
            self._fill_lists()
            try:
                numbers = follow.stats()
                if numbers:
                    self.setProperty('my.t.sub', '  ·  '.join('%s %s' % pair for pair in numbers))
            except Exception:
                pass

    def _fill_more(self):
        """v5.10.537: as on Trakt's home: the streak, picks for you, what you watched."""
        try:
            from .. import discover_feed as D
            count, week = F.streak(F.days())
            days = [_item(_t(DAYS_AR[d.weekday()], DAYS_EN[d.weekday()]), '', None, step=value) for d, value in week]
            word = (_t('%d يوم ورا بعض', '%d-day streak') % count) if count else _t('ابدأ سلسلة جديدة اليوم', 'Start a new streak today')
            picks = (D.for_you('all') or ([], False))[0][:20]
            past = F.history()
            pairs = list(dict.fromkeys(('series' if row.get('k') == 'e' else 'movie', row.get('tmdb')) for row in past))
            art = dict((str(row.get('tmdb')), row) for row in D._details_many(pairs))
        except Exception:
            xbmc.log('[DexHub] my: more\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
        pick_items = [_item(row.get('title') or '', str(row.get('year') or ''), {'thumb': row.get('poster') or '', 'fanart': row.get('fanart') or ''})
                      for row in picks]
        rows, past_items = [], []
        for row in past:
            detail = art.get(str(row.get('tmdb'))) or {}
            code = 'S%s · E%s' % (row.get('sn'), row.get('en')) if row.get('sn') is not None and row.get('en') else ''
            line = '  ·  '.join(x for x in (code, row.get('name')) if x) or (_t('فيلم', 'Movie') if row.get('k') != 'e' else '')
            rows.append(dict(row, poster=detail.get('poster') or '', fanart=detail.get('fanart') or ''))
            past_items.append(_item(row.get('title') or '', line, {'fanart': detail.get('fanart') or detail.get('poster') or ''}, day=F.when(row.get('at'))))
        with self._lock:
            if self._closing:
                return
            self.picks, self.past = picks, rows
            self.setProperty('my.streak', word)
            for cid, items, n in ((409, days, 7), (407, pick_items, 8), (408, past_items, 9)):
                control = self.getControl(cid)
                control.reset()
                control.addItems(items)
                self.setProperty('my.s%d.off' % n, '' if items else '1')
            self._wire()

    def _count(self, key):
        return sum(1 for r in self.up_all if key == 'all' or key in (r.get('srcs') or ()))

    def _show_chips(self):
        items = []
        for key, name in self.chips:
            count = self._count(key)
            items.append(_item('%s  %d' % (name, count) if count else name, key=key, sel='1' if key == self.src else ''))
        control = self.getControl(453)
        pos = control.getSelectedPosition()
        control.reset()
        control.addItems(items)
        if 0 < pos < len(items):
            control.selectItem(pos)

    def _show_up(self):
        self.up_rows = [r for r in self.up_all if self.src == 'all' or self.src in (r.get('srcs') or ())]
        items = []
        for row in self.up_rows:
            left = str(row.get('left') or '').split('·')[-1].strip()     # '34 min left', not the clock too
            line = '  ·  '.join(x for x in (row.get('line'), left) if x)
            props = {'badge': row.get('badge') or ''}
            if row.get('prog'):
                props['progstep'] = max(1, min(20, int(round(float(row['prog']) / 5.0))))
            items.append(_item(row.get('title') or '', line, {'thumb': row.get('thumb') or row.get('poster') or '',
                                                             'poster': row.get('poster') or ''}, **props))
        control = self.getControl(406)
        control.reset()
        control.addItems(items)

    def _pick_chip(self, pos):
        if not 0 <= pos < len(self.chips) or self.chips[pos][0] == self.src:
            return
        with self._lock:
            if self._closing:
                return
            self.src = self.chips[pos][0]
            self._show_chips()
            self._show_up()
            self._show_tickets()
            self._wire()

    # ---------------------------------------------------------- moving
    def _wire(self):
        """Up and down go through the sections shown, the days first."""
        try:
            chain = [cid for cid in CHAIN if self.getControl(cid).size() > 0
                     and not self.getProperty('my.s%d.off' % SECTION_OF.get(cid, 1))]
            for index, cid in enumerate(chain):
                control = self.getControl(cid)
                if index:
                    control.controlUp(self.getControl(chain[index - 1]))
                control.controlDown(self.getControl(chain[index + 1] if index + 1 < len(chain) else cid))
        except (RuntimeError, IndexError, TypeError):
            pass

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self.close_window()
            return
        focus = self.getFocusId()
        if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
            self._moved = True
        if focus == 451:
            if aid in (A_LEFT, A_RIGHT):
                self._show_day(self.getControl(451).getSelectedPosition())
        elif focus == 453:
            if aid in (A_LEFT, A_RIGHT):
                self._pick_chip(self.getControl(453).getSelectedPosition())
        elif focus == 406 and aid in (A_CONTEXT, A_MENU):
            pos = self.getControl(406).getSelectedPosition()
            if 0 <= pos < len(self.up_rows):
                from .follow import title_query
                query = title_query(self.up_rows[pos])
                if query:
                    self._open_title(query)
                else:
                    xbmcgui.Dialog().notification('Dex Hub', _t('ما قدرنا نحدد العمل', 'Could not tell which title this is'),
                                                  time=2000, sound=False)
        elif focus in (401, 405, 406, 407, 408):
            self._describe(focus)

    def onFocus(self, cid):
        if cid in (401, 405, 406, 407, 408):
            self._describe(cid)
        elif cid in (402, 403, 404, 453):
            self.setProperty('my.fanart', '')

    def _describe(self, cid):
        pos = self.getControl(cid).getSelectedPosition()
        rows = {401: self.day_rows, 405: self.tickets, 406: self.up_rows, 407: self.picks, 408: self.past}[cid]
        if not 0 <= pos < len(rows):
            return
        row = rows[pos]
        self.setProperty('my.fanart', row.get('fanart') or row.get('background') or row.get('thumb') or '')
        self.tone.follow(row.get('poster') or '')

    # ---------------------------------------------------------- doing
    def _open_title(self, query):
        """v5.10.534: over this page, which stays, as on Discover (it closed and
        Back went to the Home)."""
        from ..skinui.items import title_over_window
        if title_over_window(self, query):
            return
        self.next_title = query
        self.close_window()

    def onClick(self, cid):
        pos = self.getControl(cid).getSelectedPosition()
        if cid == 453:
            self._pick_chip(pos)
        elif cid == 407 and 0 <= pos < len(self.picks):
            row = self.picks[pos]
            self._open_title({'m': row.get('kind') or 'movie', 'title': row.get('title') or '', 'tmdb': row.get('tmdb') or '',
                              'imdb': row.get('imdb') or ''})
        elif cid == 408 and 0 <= pos < len(self.past):
            row = self.past[pos]
            self._open_title({'m': 'series' if row.get('k') == 'e' else 'movie', 'title': row.get('title') or '',
                              'tmdb': row.get('tmdb') or '', 'imdb': row.get('imdb') or ''})
        elif cid == 406 and 0 <= pos < len(self.up_rows):
            from .follow import play_entry
            play_entry(self, self.up_rows[pos])
        elif cid == 451:
            if pos >= len(self.week):
                self.next_action = 'calendar'
                self.state['back_to'] = (cid, pos)
                self.close_window()
            else:
                self._show_day(pos)
        elif cid == 401 and 0 <= pos < len(self.day_rows):
            ev = self.day_rows[pos]
            self._open_title({'m': 'series' if ev.get('kind') == 'ep' else 'movie', 'title': ev.get('title') or '',
                              'tmdb': ev.get('tmdb') or '', 'imdb': ev.get('imdb') or ''})
        elif cid == 405 and 0 <= pos < len(self.tickets):
            kind, tmdb, imdb = _ids(self.tickets[pos])
            self._open_title({'m': kind, 'title': self.tickets[pos].get('title') or '', 'tmdb': tmdb, 'imdb': imdb})
        elif cid == 404 and 0 <= pos < len(self.lists):
            key = self.lists[pos]
            if key == 'watching':
                self.next_action = 'follow'
                self.state['back_to'] = (cid, pos)
            else:
                from ..skinui.rows import list_url
                params = {'action': 'lib_favs'} if key == 'favs' else {'action': 'lib_watchlist'}
                title = _t('المفضلة', 'Favorites') if key == 'favs' else _t('قائمة المشاهدة', 'Watchlist')
                self.next_action = 'grid:' + list_url(params, title=title, shape='poster')
            self.close_window()


def open_my(app):
    """The page; a title opened from it shows over the Home and the page comes back after it."""
    from . import ui_size
    from .app import _details_tile
    state = {}
    while True:
        window = MyWindow(ui_size.xml('dexhub_my.xml'), app.addon_path, 'Default', '1080i', app=app, state=state)
        app._show(window)
        query, action = window.next_title, window.next_action
        del window
        if action in ('calendar', 'follow'):
            # v5.10.537: opened from here, so Back comes here (it was started
            # and forgotten, and Back went to the Home)
            if action == 'calendar':
                from .calendar_page import open_calendar
                came_back = open_calendar(app)
            else:
                from .follow import open_follow
                came_back = open_follow(app)
            if came_back:
                continue
            return
        if action:
            if action.startswith('grid:'):
                xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % action[5:])
            else:
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
            xbmc.log('[DexHub] my: title page failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
