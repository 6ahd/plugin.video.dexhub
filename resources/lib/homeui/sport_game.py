# -*- coding: utf-8 -*-
"""One game's page (v5.10.535), over Sport: dexhub_sportgame*.xml
(tools/make_sport_game.py).

  top   both clubs in their colours, the score, the minute, the ground
  100   the tabs: Statistics, Events
  200   statistics side by side, a bar for each club's share
  210   goals, cards and substitutions, in order, on their club's side

A game on now is read again every 30 seconds.
"""
import threading

import xbmc
import xbmcgui

from .. import lazy_tb as traceback
from .. import sport_feed as F
from .discover_page import A_BACK, A_LEFT, A_RIGHT, _item

TABS, STATS, EVENTS = 100, 200, 210
REFRESH = 30.0
STRIPE = {'goal': 'FFFFFFFF', 'penalty': 'FFFFFFFF', 'own': 'FFB9C3D3', 'yellow': 'FFF4B740', 'red': 'FFF87171',
          'sub': 'FF4ADE80'}


def _t(ar, en):
    return en if F.english() else ar


def _colour(side, fallback):
    code = str((side or {}).get('color') or '').strip().lstrip('#')
    return 'B3' + code.upper() if len(code) == 6 else fallback


def _word(kind):
    return {'goal': _t('هدف', 'Goal'), 'penalty': _t('ركلة جزاء', 'Penalty'), 'own': _t('هدف عكسي', 'Own goal'),
            'yellow': _t('بطاقة صفراء', 'Yellow card'), 'red': _t('بطاقة حمراء', 'Red card'),
            'sub': _t('تبديل', 'Substitution')}.get(kind, '')


class GameWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(GameWindow, self).__init__(*args)
        self.game = kwargs.get('game') or {}
        self.tab = 'stats'
        self.closing = False
        self._inited = False
        self._threads = []
        self._lock = threading.Lock()

    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('sg.view', 'stats')
        self.setProperty('sg.league', F.league_name(self.game.get('league')))
        self.setProperty('sg.none', _t('ما فيه إحصائيات للحين', 'No statistics yet'))
        self._top({'home': self.game.get('home'), 'away': self.game.get('away'), 'status': self.game.get('status'),
                   'venue': self.game.get('venue')})
        self._tabs()
        self.setFocusId(TABS)
        thread = threading.Thread(target=self._loop, name='DexHub-sport-game')
        thread.daemon = True
        self._threads.append(thread)
        thread.start()

    def _loop(self):
        monitor = xbmc.Monitor()
        while not self.closing:
            state = self._read()
            if state != 'in':
                return
            for _tick in range(int(REFRESH * 4)):
                if self.closing or monitor.waitForAbort(0.25):
                    return

    def _read(self):
        try:
            data = F.game_detail(self.game)
        except Exception:
            xbmc.log('[DexHub] sport game: %s' % traceback.format_exc(), xbmc.LOGWARNING)
            return ''
        if not data:
            return ''
        with self._lock:
            if self.closing:
                return ''
            self._top(data)
            self._fill(data)
        return data.get('state')

    def _top(self, data):
        home, away = data.get('home') or {}, data.get('away') or {}
        self.setProperty('sg.home', F.team_name(home.get('name') or ''))
        self.setProperty('sg.away', F.team_name(away.get('name') or ''))
        self.setProperty('sg.home.logo', home.get('logo') or '')
        self.setProperty('sg.away.logo', away.get('logo') or '')
        self.setProperty('sg.home.tint', _colour(home, 'B31E3A5F'))
        self.setProperty('sg.away.tint', _colour(away, 'B34A2B20'))
        score = '%s - %s' % (home.get('score'), away.get('score')) if home.get('score') not in (None, '') else '-'
        self.setProperty('sg.score', score)
        self.setProperty('sg.status', data.get('status') or '')
        self.setProperty('sg.venue', data.get('venue') or '')

    def _tabs(self):
        items = []
        for key, name in (('stats', _t('الإحصائيات', 'Statistics')), ('events', _t('الأحداث', 'Events'))):
            items.append(_item(name, sel='1' if key == self.tab else ''))
        control = self.getControl(TABS)
        pos = control.getSelectedPosition()
        control.reset()
        control.addItems(items)
        if pos > 0:
            control.selectItem(pos)

    def _fill(self, data):
        stats = [_item(row['name'], '', None, a=row['home'], b=row['away'], sa=max(row['home_step'], 1), sb=max(row['away_step'], 1))
                 for row in data.get('stats') or []]
        events = [_item(row['text'], _word(row['kind']), None, minute=row['minute'], stripe=STRIPE.get(row['kind'], 'FFFFFFFF'),
                        side=row['side']) for row in data.get('events') or []]
        for cid, items in ((STATS, stats), (EVENTS, events)):
            control = self.getControl(cid)
            pos = control.getSelectedPosition()
            control.reset()
            control.addItems(items)
            if 0 < pos < len(items):
                control.selectItem(pos)
        self.setProperty('sg.empty', '' if stats else '1')
        self._wire()

    def _wire(self):
        try:
            tabs = self.getControl(TABS)
            target = self.getControl(STATS if self.tab == 'stats' else EVENTS)
            tabs.controlDown(target if target.size() else tabs)
            target.controlUp(tabs)
        except (RuntimeError, TypeError):
            pass

    def _pick(self):
        tab = ('stats', 'events')[min(1, max(0, self.getControl(TABS).getSelectedPosition()))]
        if tab == self.tab:
            return
        self.tab = tab
        with self._lock:
            self.setProperty('sg.view', tab)
            self._tabs()
            self._wire()

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self.close_window()
        elif aid in (A_LEFT, A_RIGHT) and self.getFocusId() == TABS:
            self._pick()

    def onClick(self, cid):
        if cid == TABS:
            self._pick()

    def close_window(self):
        self.closing = True
        self.close()

    def on_cover(self):
        pass

    def on_uncover(self):
        pass

    def on_close(self):
        self.closing = True
        from .app import join_briefly
        join_briefly(self._threads)


def open_game(app, game):
    from . import ui_size
    window = GameWindow(ui_size.xml('dexhub_sportgame.xml'), app.addon_path, 'Default', '1080i', game=game)
    app._show(window)
    del window
