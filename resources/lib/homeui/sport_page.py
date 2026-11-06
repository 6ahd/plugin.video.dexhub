# -*- coding: utf-8 -*-
"""The Sport page of Live TV (v5.10.453).

WindowXMLDialog on dexhub_sport*.xml (gen_calendar_sport_xml.py):

  101-104  Today, My games, Tables, Follow
  110      the days around today (Today)
  120      the leagues; on Today the first chip is All
  130      the games; OK opens the game's channels and reminder
  400      a league's table
  500      a league's teams; OK follows one

Games and tables come from ESPN, the channels from the guide of the user's own
sources (sport_feed). While a game is live the page reads again every minute.
"""
import datetime
import threading
import time

import xbmc
import xbmcgui

from .. import sport_feed as F
from .. import lazy_tb as traceback

VIEWS = {101: 'today', 102: 'mine', 103: 'tables', 104: 'follow'}
DAYS, LEAGUES, GAMES, TABLE, TEAMS = 110, 120, 130, 400, 500
DAY_BACK, DAY_AHEAD = 2, 6
A_LEFT, A_RIGHT = 1, 2
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
LIVE_EVERY = 60.0


def _t(ar, en):
    return F._t(ar, en)


def _item(label, label2='', art=None, **props):
    item = xbmcgui.ListItem(label, label2, offscreen=True)
    if art:
        item.setArt(art)
    for key, value in props.items():
        item.setProperty(key, '' if value is None else str(value))
    return item


def _clock(epoch):
    return time.strftime('%H:%M', time.localtime(int(epoch)))


def _day_word(epoch):
    from .. import calendar_feed as C
    return C.day_title(datetime.date.fromtimestamp(int(epoch)))


def game_state(game):
    """(the line on the card, its kind: live, done, soon)."""
    if game.get('state') == 'in':
        return game.get('clock') or game.get('status') or _t('مباشر', 'Live'), 'live'
    if game.get('state') == 'post':
        return _t('انتهت', 'Full time'), 'done'
    return _clock(game['start']), 'soon'


def take_focus(window, target):
    """A view's group shows some frames after its property: a hidden control takes no focus."""
    for _attempt in range(12):
        xbmc.sleep(50)
        window.setFocusId(target)
        if window.getFocusId() == target:
            return


class SportWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(SportWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.view = 'today'
        self.days = [datetime.date.today() + datetime.timedelta(days=n) for n in range(-DAY_BACK, DAY_AHEAD + 1)]
        self.day = datetime.date.today()
        self.league = ''
        self.games = []
        self.table_rows = []
        self.team_rows = []
        self.guide = []
        self.next_tile = None
        self._days_pos = DAY_BACK
        self._league_pos = -1
        self._leagues = []
        self._gen = 0
        self._lock = threading.Lock()
        self._inited = False
        self._closing = False
        self._live_at = 0.0
        self._threads = []

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        for key, text in (('title', _t('رياضة', 'Sport')), ('today', _t('المباريات', 'Games')),
                          ('mine', _t('مبارياتي', 'My games')), ('tables', _t('الترتيب', 'Tables')),
                          ('follow', _t('المتابعة', 'Follow')), ('rank', '#'), ('team', _t('الفريق', 'Team')),
                          ('played', _t('لعب', 'P')), ('won', _t('فاز', 'W')), ('drawn', _t('تعادل', 'D')),
                          ('lost', _t('خسر', 'L')), ('diff', _t('الفارق', 'GD')), ('points', _t('نقاط', 'Pts')),
                          ('source', _t('النتائج من ESPN · القنوات من دليل قنواتك', 'Scores from ESPN · channels from your own guide'))):
            self.setProperty('sw.t.%s' % key, text)
        self.setProperty('tone', '402F6DE0')
        self._fill_days()
        self._show_view('today', focus=True)
        self.guide = F.guide_read().get('channels') or []
        self._later(self._read_guide)
        self._later(self._ticker)

    def _later(self, job):
        from .. import profile_scope
        thread = threading.Thread(target=profile_scope.capture(job), name='DexHub-sport')
        thread.daemon = True
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def _read_guide(self):
        if not F.guide_stale():
            return

        def progress(done, total):
            self.setProperty('sw.busy', _t('يقرأ دليل القنوات %d/%d', 'Reading the guide %d/%d') % (done, total))
        try:
            data = F.guide_refresh(self.app, progress=progress) or {}
            self.guide = data.get('channels') or []
        except Exception:
            xbmc.log('[DexHub] sport guide: %s' % traceback.format_exc(), xbmc.LOGWARNING)
        self.setProperty('sw.busy', '')
        if not self._closing and self.view in ('today', 'mine'):
            self._paint_games()

    def _ticker(self):
        monitor = xbmc.Monitor()
        while not self._closing and not monitor.abortRequested():
            if monitor.waitForAbort(0.5):
                return
            if self._closing:
                return
            if self.view in ('today', 'mine') and time.monotonic() - self._live_at > LIVE_EVERY \
                    and any(game.get('state') == 'in' for game in self.games):
                self._live_at = time.monotonic()
                self._load_games(keep=True)

    def close_window(self):
        self._closing = True
        self.close()

    def on_cover(self):
        pass

    def on_uncover(self):
        pass

    def on_close(self):
        self._closing = True
        # no thread of this page outlives it: Python ends with the script
        for thread in self._threads:
            thread.join(3.0)

    # ---------------------------------------------------------- filling
    def _show_view(self, view, focus):
        self.view = view
        self.setProperty('sw.view', view)
        self._fill_leagues()
        if view in ('today', 'mine'):
            self._load_games()
            target = DAYS if view == 'today' else GAMES
        elif view == 'tables':
            self._load_table()
            target = LEAGUES
        else:
            self._load_teams()
            target = LEAGUES
        if focus:
            take_focus(self, target)

    def _fill_days(self):
        from .. import calendar_feed as C
        today = datetime.date.today()
        items = [_item(C.day_short(day), str(day.day), today='1' if day == today else '', sel='1' if day == self.day else '')
                 for day in self.days]
        control = self.getControl(DAYS)
        control.reset()
        control.addItems(items)
        control.selectItem(self._days_pos)

    def _fill_leagues(self):
        data = F.load()
        if self.view == 'today':
            keys = [''] + list(data['leagues'])
        elif self.view == 'mine':
            keys = []
        else:
            keys = [row[0] for row in F.LEAGUES if row[0] not in ('f1', 'ufc')]
            if self.league not in keys:
                self.league = keys[0]
        if self.view == 'today' and self.league not in keys:
            self.league = ''
        self._leagues = keys
        items = []
        for key in keys:
            name = F.league_name(key) if key else _t('الكل', 'All')
            items.append(_item(name, sel='1' if key == self.league else '',
                               on='1' if self.view == 'follow' and key in data['leagues'] else ''))
        control = self.getControl(LEAGUES)
        control.reset()
        control.addItems(items)
        self._league_pos = keys.index(self.league) if self.league in keys else 0
        if items:
            control.selectItem(self._league_pos)

    def _load_games(self, keep=False):
        self._gen += 1
        gen = self._gen
        if not keep:
            self.setProperty('sw.loading', '1')
            self.setProperty('sw.empty', '')

        def job():
            data = F.load()
            if self.view == 'mine':
                games = F.mine()
            else:
                keys = [self.league] if self.league else (data['leagues'] or list(F.DEFAULT_LEAGUES))
                games = F.games_between(self.day, self.day, keys)
            if gen != self._gen or self._closing:
                return
            self.games = games
            self.setProperty('sw.loading', '')
            self._paint_games()
        self._later(job)

    def _paint_games(self):
        with self._lock:
            data = F.load()
            items = []
            for game in self.games:
                line, kind = game_state(game)
                home, away = game.get('home') or {}, game.get('away') or {}
                channels = F.channels_for(game, self.guide)
                channel = channels[0]['name'] if channels else ''
                when = line if self.view == 'today' else ('%s  ·  %s' % (_day_word(game['start']), line) if kind == 'soon' else line)
                items.append(_item(F.league_name(game['league']), game.get('venue') or '', None, when=when, statekind=kind,
                                   home=F.team_name(home.get('name') or '') or game.get('title') or '',
                                   away=F.team_name(away.get('name') or ''), homelogo=home.get('logo') or '',
                                   awaylogo=away.get('logo') or '', homescore=home.get('score') if kind != 'soon' else '',
                                   awayscore=away.get('score') if kind != 'soon' else '', channel=channel,
                                   sure='1' if channels and channels[0]['score'] == F.SURE else '',
                                   bell='1' if game['id'] in data['reminders'] else '',
                                   mine='1' if F.is_mine(game, data) else ''))
            try:
                control = self.getControl(GAMES)
            except RuntimeError:
                return
            pos = control.getSelectedPosition()
            control.reset()
            control.addItems(items)
            if 0 <= pos < len(items):
                control.selectItem(pos)
            if self.view == 'mine' and not data['teams']:
                empty = _t('تابع فريقك من «المتابعة» وتطلع مبارياته هنا', 'Follow a team under Follow and its games show here')
            elif self.view == 'mine':
                empty = _t('ما فيه مباريات لفرقك في الأسبوعين الجايين', 'No games for your teams in the next two weeks')
            else:
                empty = _t('ما فيه مباريات في هذا اليوم', 'No games on this day')
            self.setProperty('sw.empty', '' if items else empty)
            live = sum(1 for game in self.games if game.get('state') == 'in')
            self.setProperty('sw.count', _t('%d مباشر', '%d live') % live if live else '')

    def _load_table(self):
        self._gen += 1
        gen = self._gen
        key = self.league
        self.setProperty('sw.loading', '1')
        self.setProperty('sw.empty', '')

        def job():
            rows = F.table(key)
            if gen != self._gen or self._closing:
                return
            self.table_rows = rows
            items = []
            for row in rows:
                items.append(_item(F.team_name(row['name']), row['rank'], {'thumb': row['logo']}, played=row['played'],
                                   won=row['won'], drawn=row['drawn'], lost=row['lost'], diff=row['diff'], points=row['points']))
            control = self.getControl(TABLE)
            control.reset()
            control.addItems(items)
            self.setProperty('sw.loading', '')
            self.setProperty('sw.empty', '' if items else _t('ما فيه جدول لهذه البطولة', 'No table for this competition'))
        self._later(job)

    def _load_teams(self):
        self._gen += 1
        gen = self._gen
        key = self.league
        self.setProperty('sw.loading', '1')
        self.setProperty('sw.empty', '')

        def job():
            rows = F.teams(key)
            if gen != self._gen or self._closing:
                return
            self.team_rows = rows
            self._paint_teams()
            self.setProperty('sw.loading', '')
            self.setProperty('sw.empty', '' if rows else _t('ما فيه فرق لهذه البطولة', 'No teams for this competition'))
        self._later(job)

    def _paint_teams(self):
        data = F.load()
        items = [_item(F.team_name(row['name']), '', {'thumb': row['logo']}, on='1' if row['id'] in data['teams'] else '')
                 for row in self.team_rows]
        control = self.getControl(TEAMS)
        pos = control.getSelectedPosition()
        control.reset()
        control.addItems(items)
        if 0 <= pos < len(items):
            control.selectItem(pos)

    # ---------------------------------------------------------- moving
    def _days_moved(self):
        control = self.getControl(DAYS)
        pos = control.getSelectedPosition()
        if pos == self._days_pos or not 0 <= pos < len(self.days):
            return
        self._days_pos = pos
        self.day = self.days[pos]
        for index in range(control.size()):
            control.getListItem(index).setProperty('sel', '1' if index == pos else '')
        self._load_games()

    def _leagues_moved(self):
        control = self.getControl(LEAGUES)
        pos = control.getSelectedPosition()
        if pos == self._league_pos or not 0 <= pos < len(self._leagues):
            return
        self._league_pos = pos
        self.league = self._leagues[pos]
        for index in range(control.size()):
            control.getListItem(index).setProperty('sel', '1' if index == pos else '')
        if self.view == 'today':
            self._load_games()
        elif self.view == 'tables':
            self._load_table()
        elif self.view == 'follow':
            self._load_teams()

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self.close_window()
            return
        if aid in (A_LEFT, A_RIGHT):
            focus = self.getFocusId()
            if VIEWS.get(focus) not in (None, self.view):
                self._show_view(VIEWS[focus], focus=False)
            elif focus == DAYS:
                self._days_moved()
            elif focus == LEAGUES:
                self._leagues_moved()

    def onClick(self, cid):
        if cid in VIEWS:
            self._show_view(VIEWS[cid], focus=True)
        elif cid == DAYS:
            self.setFocusId(GAMES)
        elif cid == LEAGUES:
            if self.view == 'follow':
                self._toggle_league()
            elif self.view == 'today':
                self.setFocusId(GAMES)
        elif cid == GAMES:
            self._game_menu()
        elif cid == TEAMS:
            self._toggle_team()

    # ---------------------------------------------------------- doing
    def _toggle_league(self):
        key = self.league
        state = F.toggle_league(key)
        xbmcgui.Dialog().notification('Dex Hub', (_t('تظهر مبارياتها في «المباريات»', 'Shown under Games') if state
                                                  else _t('ما عادت تظهر في «الكل»', 'No longer under All')),
                                      time=2000, sound=False)
        self._fill_leagues()
        self.setFocusId(LEAGUES)

    def _toggle_team(self):
        pos = self.getControl(TEAMS).getSelectedPosition()
        if 0 <= pos < len(self.team_rows):
            F.toggle_team(self.team_rows[pos], self.league)
            self._paint_teams()

    def _game_menu(self):
        from .options import choose
        pos = self.getControl(GAMES).getSelectedPosition()
        if not 0 <= pos < len(self.games):
            return
        game = self.games[pos]
        data = F.load()
        channels = F.channels_for(game, self.guide)
        words = {F.SURE: _t('مؤكد', 'Sure'), F.LIKELY: _t('غالبًا', 'Likely'), F.MAYBE: _t('ممكن', 'Maybe')}
        choices = [('tile', row['tile'], (row['name'], '%s  ·  %s' % (words[row['score']], row['programme'])))
                   for row in channels]
        if game.get('state') != 'post':
            shown = set(row['name'] for row in channels)
            for row in self.guide[:30]:
                name = row['tile'].get('title') or ''
                if name not in shown:
                    choices.append(('tile', row['tile'], (name, _t('قناة رياضية', 'Sport channel'))))
        if game.get('state') == 'pre':
            text = _t('ألغِ التذكير', 'Cancel the reminder') if game['id'] in data['reminders'] else _t('ذكّرني قبلها بخمس دقايق', 'Remind me 5 minutes before')
            choices.insert(0, ('bell', None, (text, '')))
        # v5.10.535: the game's own page: statistics and what happened
        choices.insert(0, ('game', None, (_t('تفاصيل المباراة', 'Game details'), _t('الإحصائيات والأحداث', 'Statistics and events'))))
        subtitle = '%s  ·  %s' % (F.league_name(game['league']), _day_word(game['start']))
        pick = choose(F.game_line(game), [row[2] for row in choices], subtitle=subtitle)
        if pick < 0:
            return
        kind, tile = choices[pick][0], choices[pick][1]
        if kind == 'game':
            from .sport_game import open_game
            open_game(self.app, game)
        elif kind == 'bell':
            F.toggle_reminder(game)
            self._paint_games()
        else:
            self.next_tile = tile
            self.close_window()


def _play_tile(app, tile):
    """Start a channel of any source, full screen."""
    from . import live_sources
    stream = live_sources.resolve(tile, app)
    if not stream:
        return False
    item = xbmcgui.ListItem(label=tile.get('title') or '', path=stream['url'])
    if stream.get('mime'):
        item.setMimeType(stream['mime'])
        item.setContentLookup(False)
    for name, value in (stream.get('props') or {}).items():
        item.setProperty(name, value)
    if tile.get('logo'):
        item.setArt({'icon': tile['logo'], 'thumb': tile['logo']})
    item.getVideoInfoTag().setTitle(tile.get('title') or '')
    from .player_handoff import play_video
    play_video(stream['url'], item)
    live_sources.remember(app.profile, tile)
    app.live_watching = {'url': stream['url'].split('|', 1)[0], 'tile': tile}
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if xbmc.Player().isPlayingVideo():
            xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
            return True
        xbmc.sleep(150)
    return False


def open_sport(app):
    from . import ui_size
    window = SportWindow(ui_size.xml('dexhub_sport.xml'), app.addon_path, 'Default', '1080i', app=app)
    app._show(window)
    tile = window.next_tile
    del window
    if tile and not _play_tile(app, tile):
        xbmcgui.Dialog().notification('Dex Hub', _t('تعذر تشغيل القناة', 'Could not start the channel'), time=3000, sound=False)
