# -*- coding: utf-8 -*-
"""The calendar page (v5.10.453): Week, List and Month.

WindowXMLDialog on dexhub_calendar*.xml (gen_calendar_sport_xml.py):

  101-103  Week, List, Month
  121-123  All, Series, Movies
  110      the week's days; Left on the first day and Right on the last turn the week
  140      the chosen day's cards (Week)
  200      every title from a week back on (List)
  300      the month's days (Month), 310 the focused day's titles
  311 312  the month before and after
  131-133  open, play, bell (Week); 231-233 the same (List)

The page draws what was kept and reads again behind it (calendar_feed).
"""
import datetime
import threading

import xbmc
import xbmcgui

from .. import calendar_feed as F
from .. import lazy_tb as traceback

VIEWS = {101: 'week', 102: 'list', 103: 'month'}
KINDS = {121: 'all', 122: 'ep', 123: 'movie'}
DAYS, CARDS, LIST, GRID, DAY_LIST = 110, 140, 200, 300, 310
MONTH_PREV, MONTH_NEXT = 311, 312
OPEN, PLAY, BELL = (131, 231), (132, 232), (133, 233)
A_LEFT, A_RIGHT, A_UP, A_DOWN = 1, 2, 3, 4
A_CONTEXT, A_MENU = 117, 163
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
LIST_BACK = 7
VIEW_PROP = 'dexhub.calendar.view'


def _t(ar, en):
    return F._t(ar, en)


def _item(label, label2='', art=None, **props):
    item = xbmcgui.ListItem(label, label2, offscreen=True)
    if art:
        item.setArt(art)
    for key, value in props.items():
        item.setProperty(key, '' if value is None else str(value))
    return item


def _meta(ev):
    parts = [ev.get('network') or '', ev.get('badge') or '']
    if ev.get('runtime'):
        parts.append(_t('%d د', '%d min') % ev['runtime'])
    return '  ·  '.join(part for part in parts if part)


def _line(ev):
    if ev.get('kind') == 'ep':
        return '  ·  '.join(part for part in (ev.get('code'), ev.get('sub')) if part)
    return ev.get('sub') or ''


def _when(ev):
    day = datetime.date.fromisoformat(ev['day'])
    text = F.day_title(day)
    return '%s  ·  %s' % (text, ev['time']) if ev.get('time') else text


def is_out(ev, now=None):
    now = now or datetime.datetime.now()
    today = now.date().isoformat()
    if ev.get('day') != today:
        return (ev.get('day') or '') < today
    return not ev.get('time') or ev['time'] <= now.strftime('%H:%M')


class CalendarWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(CalendarWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.state = kwargs.get('state') if isinstance(kwargs.get('state'), dict) else {}
        today = datetime.date.today()
        self.view = self.state.get('view') or xbmcgui.Window(10000).getProperty(VIEW_PROP) or 'week'
        self.kind = self.state.get('kind') or 'all'
        self.day = self.state.get('day') or today
        self.month = self.state.get('month') or today.replace(day=1)
        self.events = []
        self.watched = set()
        self.paused = []
        self.next_title = None
        self.next_play = None
        self._days_pos = -1
        self._cards = []
        self._rows = []
        self._grid = []
        self._day_rows = []
        self._inited = False
        self._closing = False
        self._thread = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        for key, text in (('title', _t('التقويم', 'Calendar')), ('week', _t('أسبوع', 'Week')),
                          ('list', _t('قائمة', 'List')), ('month', _t('شهر', 'Month')),
                          ('all', _t('الكل', 'All')), ('ep', _t('مسلسلات', 'Series')),
                          ('movie', _t('أفلام', 'Movies')), ('open', _t('صفحة العمل', 'Open title')),
                          ('play', _t('شغّل', 'Play')),
                          ('empty', _t('ما فيه شي تتابعه بعد', 'Nothing to show yet')),
                          ('emptyhelp', _t('التقويم يقرأ قوائمك في Trakt والمفضلة وتابع المشاهدة. أضف عمل لقائمتك ويطلع هنا لحاله.',
                                           'The calendar reads your Trakt lists, favourites and Continue Watching. Add a title to a list and it shows up here.')),
                          ('none', _t('ما فيه شي في هذا اليوم', 'Nothing on this day')),
                          ('source', _t('البيانات من TMDb وTVmaze وحساباتك', 'Data from TMDb, TVmaze and your accounts'))):
            self.setProperty('cw.t.%s' % key, text)
        from ..art_tone import Tone
        self.tone = Tone(self)
        self._apply(F.read())
        self._show_view(self.view, focus=True)
        self._tone_now()
        self._thread = threading.Thread(target=self._read_again, name='DexHub-calendar')
        self._thread.daemon = True
        self._thread.start()

    def _read_again(self):
        from .. import profile_scope
        data = F.read()
        if not F.stale(data):
            self._watched_later()
            return
        self.setProperty('cw.busy', _t('يحدّث…', 'Updating…'))
        try:
            data = profile_scope.capture(F.refresh)() or data
        except Exception:
            xbmc.log('[DexHub] calendar: %s' % traceback.format_exc(), xbmc.LOGWARNING)
        self.setProperty('cw.busy', '')
        if self._closing:
            return
        with self._lock:
            self._apply(data)
            self._show_view(self.view, focus=False)

    def _watched_later(self):
        keys = F.watched_keys()
        if keys != self.watched and not self._closing:
            with self._lock:
                self.watched = keys
                self._show_view(self.view, focus=False)

    def _apply(self, data):
        self.events = list((data or {}).get('events') or [])
        self.setProperty('cw.empty', '' if self.events else '1')
        try:
            from .. import watch_sources as W
            self.paused = W.config().get('paused') or []
        except Exception:
            self.paused = []

    def close_window(self):
        self._closing = True
        xbmcgui.Window(10000).setProperty(VIEW_PROP, self.view)
        self.state.update({'view': self.view, 'kind': self.kind, 'day': self.day, 'month': self.month})
        self.close()

    def on_cover(self):
        pass

    def on_uncover(self):
        pass

    def on_close(self):
        self._closing = True
        self.tone.stop()
        # no thread of this page outlives it: Python ends with the script
        if self._thread is not None:
            self._thread.join(10.0)
        for thread in self.__dict__.get('_logo_threads', []):
            thread.join(8.0)

    # ---------------------------------------------------------- views
    def _shown(self):
        return F.filtered(self.events, self.kind)

    def _show_view(self, view, focus):
        self.view = view
        self.setProperty('cw.view', view)
        self.setProperty('cw.kind', self.kind)
        if view == 'week':
            self._fill_week()
            target = DAYS
        elif view == 'list':
            self._fill_list()
            target = LIST
        else:
            self._fill_month()
            target = GRID
        if focus:
            from .sport_page import take_focus
            take_focus(self, 102 if self.getProperty('cw.empty') else target)

    def _fill_week(self):
        shown = F.by_day(self._shown())
        start = F.week_start(self.day)
        items = []
        today = datetime.date.today()
        for n in range(7):
            day = start + datetime.timedelta(days=n)
            count = len(shown.get(day.isoformat()) or [])
            items.append(_item(F.day_short(day), str(day.day), dots='●' * min(count, 3),
                               today='1' if day == today else '', sel='1' if day == self.day else ''))
        days = self.getControl(DAYS)
        days.reset()
        days.addItems(items)
        self._days_pos = (self.day - start).days
        days.selectItem(self._days_pos)
        self._fill_cards()

    def _fill_cards(self):
        events = F.by_day(self._shown()).get(self.day.isoformat()) or []
        self._cards = events
        cards = self.getControl(CARDS)
        cards.reset()
        cards.addItems([self._event_item(ev) for ev in events])
        self.setProperty('cw.daytitle', F.day_title(self.day))
        self.setProperty('cw.dayempty', '' if events else '1')

    def _event_item(self, ev):
        bell = F.bell_on(ev, self.paused)
        art = {'thumb': ev.get('still') or ev.get('poster') or '', 'poster': ev.get('poster') or '',
               'fanart': ev.get('fanart') or ''}
        return _item(ev.get('title') or '', _line(ev), art, time=ev.get('time') or '', code=ev.get('code') or '',
                     when=_when(ev), meta=_meta(ev), plot=ev.get('plot') or '', badge=ev.get('badge') or '',
                     bell='1' if bell else '', watched='1' if ev.get('key') in self.watched else '',
                     belltext=self._bell_text(bell), canplay='1' if is_out(ev) and ev.get('imdb') else '',
                     daycol='%s %d' % (F.day_short(datetime.date.fromisoformat(ev['day'])),
                                       datetime.date.fromisoformat(ev['day']).day))

    @staticmethod
    def _bell_text(state):
        if state is None:
            return ''
        return _t('التنبيه شغّال', 'Alert on') if state else _t('التنبيه طافي', 'Alert off')

    def _fill_list(self):
        lo = (datetime.date.today() - datetime.timedelta(days=LIST_BACK)).isoformat()
        rows = [ev for ev in self._shown() if ev.get('day', '') >= lo]
        self._rows = rows
        today = datetime.date.today().isoformat()
        items, last, first = [], '', -1
        for index, ev in enumerate(rows):
            item = self._event_item(ev)
            if ev['day'] != last:
                day = datetime.date.fromisoformat(ev['day'])
                item.setProperty('dayname', _t('اليوم', 'Today') if ev['day'] == today else F.day_short(day))
                item.setProperty('daynum', str(day.day))
                item.setProperty('istoday', '1' if ev['day'] == today else '')
                last = ev['day']
            if first < 0 and ev['day'] >= today:
                first = index
            items.append(item)
        control = self.getControl(LIST)
        control.reset()
        control.addItems(items)
        if items:
            control.selectItem(max(first, 0))

    def _fill_month(self):
        shown = F.by_day(self._shown())
        self.setProperty('cw.monthtitle', '%s %d' % (F.month_name(self.month), self.month.year))
        first = F.week_first_day()
        for n in range(7):
            sample = datetime.date(2024, 1, 1) + datetime.timedelta(days=(first + n) % 7)
            self.setProperty('cw.wd%d' % n, F.day_short(sample))
        self._grid = F.month_cells(self.month)
        today = datetime.date.today()
        items, pick = [], -1
        for index, day in enumerate(self._grid):
            events = shown.get(day.isoformat()) or []
            items.append(_item(str(day.day), '', None, inmonth='1' if day.month == self.month.month else '',
                               today='1' if day == today else '', dots='●' * min(len(events), 3)))
            if day == today or (pick < 0 and day == self.month):
                pick = index
        control = self.getControl(GRID)
        control.reset()
        control.addItems(items)
        control.selectItem(max(pick, 0))
        self._fill_day_list(self._grid[max(pick, 0)])

    def _fill_day_list(self, day):
        self._day_rows = F.by_day(self._shown()).get(day.isoformat()) or []
        control = self.getControl(DAY_LIST)
        control.reset()
        control.addItems([self._event_item(ev) for ev in self._day_rows])
        self.setProperty('cw.griddate', F.day_title(day))
        self.setProperty('cw.griddempty', '' if self._day_rows else '1')

    # ---------------------------------------------------------- moving
    def _days_moved(self, action_id):
        control = self.getControl(DAYS)
        pos = control.getSelectedPosition()
        if pos == self._days_pos:
            if action_id == A_LEFT and pos == 0:
                self._turn_week(-1)
            elif action_id == A_RIGHT and pos == 6:
                self._turn_week(1)
            return
        self._days_pos = pos
        self.day = F.week_start(self.day) + datetime.timedelta(days=pos)
        for index in range(control.size()):
            control.getListItem(index).setProperty('sel', '1' if index == pos else '')
        self._fill_cards()

    def _turn_week(self, step):
        self.day = self.day + datetime.timedelta(days=7 * step)
        self._fill_week()

    def _turn_month(self, step):
        self.month = F.add_months(self.month, step)
        self._fill_month()

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            if self.view != 'week' and self.getFocusId() in (LIST, GRID, DAY_LIST):
                self.setFocusId(next(k for k, v in VIEWS.items() if v == self.view))
                return
            self.close_window()
            return
        focus = self.getFocusId()
        if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
            self._tone_now()
            if aid in (A_LEFT, A_RIGHT) and VIEWS.get(focus) not in (None, self.view):
                # v5.10.456: the views and the kinds change as the cursor rests on them
                self._show_view(VIEWS[focus], focus=False)
            elif aid in (A_LEFT, A_RIGHT) and KINDS.get(focus) not in (None, self.kind):
                self.kind = KINDS[focus]
                self._show_view(self.view, focus=False)
            elif focus == DAYS and aid in (A_LEFT, A_RIGHT):
                self._days_moved(aid)
            elif focus == GRID:
                pos = self.getControl(GRID).getSelectedPosition()
                if 0 <= pos < len(self._grid):
                    self._fill_day_list(self._grid[pos])
        elif aid in (A_CONTEXT, A_MENU):
            ev = self._focused()
            if ev:
                self._options(ev)

    def onClick(self, cid):
        if cid in VIEWS:
            self._show_view(VIEWS[cid], focus=True)
        elif cid in KINDS:
            self.kind = KINDS[cid]
            self._show_view(self.view, focus=False)
        elif cid == DAYS:
            self.setFocusId(CARDS if self._cards else DAYS)
        elif cid == MONTH_PREV:
            self._turn_month(-1)
        elif cid == MONTH_NEXT:
            self._turn_month(1)
        elif cid == GRID:
            if self._day_rows:
                self.setFocusId(DAY_LIST)
        elif cid in (CARDS, LIST, DAY_LIST) or cid in OPEN:
            ev = self._focused(cid)
            if ev:
                self._open(ev)
        elif cid in PLAY:
            ev = self._focused(cid)
            if ev:
                self._play(ev)
        elif cid in BELL:
            ev = self._focused(cid)
            if ev:
                self._bell(ev)

    def _tone_now(self):
        cid = {'week': CARDS, 'list': LIST}.get(self.view)
        ev = self._focused(cid) if cid else None
        self.tone.follow((ev or {}).get('poster') or '')
        self._want_logo(ev)

    def _want_logo(self, ev):
        """v5.10.463: the focused title's clear logo in place of its name
        (cw.logo), read once from TMDb (kept there a month) and remembered."""
        ev = ev or {}
        key = str(ev.get('tmdb') or ev.get('imdb') or '')
        if not key:
            self.setProperty('cw.logo', '')
            return
        logos = self.__dict__.setdefault('_logos', {})
        if key in logos:
            self.setProperty('cw.logo', logos[key])
            return
        self.setProperty('cw.logo', '')
        self._logo_key = key

        def job():
            from .. import tmdb_direct as T
            try:
                art = T.art_for(tmdb_id=str(ev.get('tmdb') or ''), imdb_id=str(ev.get('imdb') or ''),
                                media_type='tv' if ev.get('kind') == 'ep' else 'movie', title=ev.get('title') or '')
                url = (art or {}).get('clearlogo') or ''
            except Exception:
                url = ''
            logos[key] = url
            if not self._closing and getattr(self, '_logo_key', '') == key:
                self.setProperty('cw.logo', url)
        thread = threading.Thread(target=job, name='DexHub-calendar-logo')
        thread.daemon = True
        self.__dict__.setdefault('_logo_threads', []).append(thread)
        thread.start()

    def _focused(self, cid=None):
        cid = cid if cid is not None else self.getFocusId()
        if cid == DAY_LIST:
            rows, source = self._day_rows, DAY_LIST
        elif cid == LIST or cid in (OPEN[1], PLAY[1], BELL[1]):
            rows, source = self._rows, LIST
        elif cid == CARDS or cid in (OPEN[0], PLAY[0], BELL[0]):
            rows, source = self._cards, CARDS
        else:
            return None
        pos = self.getControl(source).getSelectedPosition()
        return rows[pos] if 0 <= pos < len(rows) else None

    # ---------------------------------------------------------- doing
    def _options(self, ev):
        from .options import choose
        choices = [('open', _t('صفحة العمل', 'Open title'))]
        if is_out(ev) and ev.get('imdb'):
            choices.append(('play', _t('شغّل', 'Play')))
        state = F.bell_on(ev, self.paused)
        if state is not None:
            choices.append(('bell', _t('طفّ التنبيه', 'Turn the alert off') if state else _t('شغّل التنبيه', 'Turn the alert on')))
        pick = choose(ev.get('title') or '', [text for _key, text in choices], subtitle=_line(ev))
        if pick < 0:
            return
        key = choices[pick][0]
        if key == 'open':
            self._open(ev)
        elif key == 'play':
            self._play(ev)
        else:
            self._bell(ev)

    def _open(self, ev):
        self.next_title = {'m': 'series' if ev.get('kind') == 'ep' else 'movie', 'title': ev.get('title') or '',
                           'tmdb': ev.get('tmdb') or '', 'imdb': ev.get('imdb') or ''}
        self.close_window()

    def _play(self, ev):
        if not (is_out(ev) and ev.get('imdb')):
            xbmcgui.Dialog().notification('Dex Hub', _t('لسا ما نزل', 'Not out yet'), time=2000, sound=False)
            return
        from .. import reminders
        self.next_play = reminders._opener(ev, play=True)
        self.close_window()

    def _bell(self, ev):
        state = F.toggle_bell(ev)
        if state is None:
            xbmcgui.Dialog().notification('Dex Hub', _t('العمل ما هو في مصادر المراقبة', 'This title is not in your monitored sources'),
                                          time=2500, sound=False)
            return
        try:
            from .. import watch_sources as W
            self.paused = W.config().get('paused') or []
        except Exception:
            pass
        focus = self.getFocusId()
        positions = {}
        for cid in (LIST, CARDS, DAY_LIST):
            try:
                positions[cid] = self.getControl(cid).getSelectedPosition()
            except Exception:
                pass
        self._show_view(self.view, focus=False)
        for cid, pos in positions.items():
            try:
                if pos >= 0:
                    self.getControl(cid).selectItem(pos)
            except Exception:
                pass
        if focus:
            self.setFocusId(focus)


def open_calendar(app):
    """The page; a title opened from it shows over the Home and the page
    comes back after it."""
    from . import ui_size
    from .app import _details_tile
    state = {}
    while True:
        window = CalendarWindow(ui_size.xml('dexhub_calendar.xml'), app.addon_path, 'Default', '1080i', app=app, state=state)
        app._show(window)
        query, target = window.next_title, window.next_play
        del window
        if target:
            xbmc.executebuiltin('RunPlugin("%s")' % target)
            return
        if not query:
            return True         # v5.10.537: Back: a page under this one comes back
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
            xbmc.log('[DexHub] calendar: title page failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
