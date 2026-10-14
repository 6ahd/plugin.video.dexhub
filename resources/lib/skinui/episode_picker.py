# -*- coding: utf-8 -*-
"""The player's Episodes button (v5.10.219): the show's episodes over the
playing video, with the next one already chosen.

skin_osd_pick opens dexhub_episodes.xml (a dialog: the video plays on
behind it). The seasons are chips along the top, the episodes cards under
them; the focus starts on the episode after the playing one (the first of
the next season after a season's last). OK plays the chosen episode the way
the title page does; the playing one just goes back to the video.
"""
import threading
import time

import xbmc
import xbmcgui

from . import common as C

SEASONS, EPISODES = 200, 300


def _tr(text):
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


class Picker(xbmcgui.WindowXMLDialog):

    def __init__(self, *args, **kwargs):
        super(Picker, self).__init__(*args, **kwargs)
        self.playing = kwargs.get('playing') or {}
        self.page = None
        self.seasons = []
        self.season = -1
        self.rows = []
        self.chosen = None
        self._lock = threading.Lock()
        self._want = None
        self.next_code = None

    # ------------------------------------------------------------ loading
    def onInit(self):
        q = self.playing.get('q') or {}
        self.setProperty('ep.show', q.get('title') or xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or '')
        self.setProperty('ep.head', _tr('الحلقات'))
        self.setProperty('ep.loading', '1')
        threading.Thread(target=self._load, name='DexHub-episodes', daemon=True).start()

    def _load(self):
        try:
            from . import title as T
            page = T.HeadlessTitle(self.playing['q'])
            page.load()
            try:
                page.fresh_state()
            except Exception:
                pass
            self.page = page
            self.seasons = list(page.seasons or [])
        except Exception as exc:
            C.log('episodes picker: %s' % exc, xbmc.LOGWARNING)
            self.setProperty('ep.loading', '')
            self.setProperty('ep.empty', _tr('ما قدرت أجيب الحلقات'))
            return
        numbers = [n for n, _label in self.seasons]
        now_s, now_e = self.playing.get('season', -1), self.playing.get('episode', 0)
        season = now_s if now_s in numbers else (numbers[0] if numbers else -1)
        rows = self._rows(season)
        # the next episode: after the playing one, else the next season's first
        want = 0
        if season == now_s:
            later = [i for i, r in enumerate(rows) if int(r['props'].get('episode') or 0) > now_e]
            if later:
                want = later[0]
            elif season in numbers and numbers.index(season) + 1 < len(numbers):
                season = numbers[numbers.index(season) + 1]
                rows, want = self._rows(season), 0
            else:
                current = [i for i, r in enumerate(rows) if int(r['props'].get('episode') or 0) == now_e]
                want = current[0] if current else 0
        chips = []
        for number, label in self.seasons:
            li = xbmcgui.ListItem(label)
            li.setProperty('season', str(number))
            chips.append(li)
        try:
            ctl = self.getControl(SEASONS)
            ctl.reset()
            ctl.addItems(chips)
            if season in numbers:
                ctl.selectItem(numbers.index(season))
        except Exception:
            pass
        try:
            self.next_code = (season, int(rows[want]['props'].get('episode') or 0)) if rows else None
            if self.next_code == (now_s, now_e):
                self.next_code = None
        except Exception:
            self.next_code = None
        self._show(season, rows, want)
        self.setProperty('ep.loading', '')
        try:
            self.setFocusId(EPISODES if rows else SEASONS)
        except Exception:
            pass

    def _rows(self, season):
        if season < 0 or self.page is None:
            return []
        try:
            return self.page.episode_items(season) or []
        except Exception as exc:
            C.log('episodes picker season %s: %s' % (season, exc), xbmc.LOGWARNING)
            return []

    def _show(self, season, rows, want=0):
        now_s, now_e = self.playing.get('season', -1), self.playing.get('episode', 0)
        items = []
        next_marked = False
        for row in rows:
            info = row.get('info') or {}
            number = int(row['props'].get('episode') or 0)
            code = 'S%d:E%d' % (season, number)
            name = info.get('title') or row.get('label') or ''
            li = xbmcgui.ListItem('%s  ·  %s' % (code, name) if name and name != code else code)
            li.setLabel2(row['props'].get('sub') or '')
            li.setArt(row.get('art') or {})
            try:
                tag = li.getVideoInfoTag()
                tag.setPlot(info.get('plot') or '')
            except Exception:
                pass
            li.setProperty('plot', info.get('plot') or '')
            if row.get('progress'):
                li.setProperty('percent', str(int(row['progress'])))
            if row.get('watched'):
                li.setProperty('watched', '1')
            if season == now_s and number == now_e:
                li.setProperty('badge', _tr('يعرض الحين'))
                li.setProperty('now', '1')
            elif not next_marked and (season, number) == self.next_code:
                li.setProperty('badge', _tr('التالية'))
                next_marked = True
            items.append(li)
        with self._lock:
            self.season, self.rows = season, rows
        self.setProperty('ep.count', str(len(rows)))
        self.setProperty('ep.empty', '' if rows else _tr('ما فيه حلقات في هذا الموسم'))
        ctl = self.getControl(EPISODES)
        ctl.reset()
        ctl.addItems(items)
        if items:
            ctl.selectItem(max(0, min(want, len(items) - 1)))

    # ------------------------------------------------------------ seasons
    def _season_later(self):
        stamp = time.time()
        self._want = stamp
        xbmc.sleep(350)
        if self._want != stamp:
            return
        try:
            pos = self.getControl(SEASONS).getSelectedPosition()
            number = self.seasons[pos][0]
        except Exception:
            return
        if number == self.season:
            return
        self.setProperty('ep.loading', '1')
        rows = self._rows(number)
        if self._want == stamp:
            self._show(number, rows, 0)
        self.setProperty('ep.loading', '')

    # ------------------------------------------------------------ input
    def onAction(self, action):
        aid = action.getId()
        if aid in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU, 92, 10):
            self.close()
            return
        if self.getFocusId() == SEASONS and aid in (xbmcgui.ACTION_MOVE_LEFT, xbmcgui.ACTION_MOVE_RIGHT):
            threading.Thread(target=self._season_later, daemon=True).start()

    def onClick(self, control_id):
        if control_id == SEASONS:
            threading.Thread(target=self._season_later, daemon=True).start()
            self.setFocusId(EPISODES)
            return
        if control_id != EPISODES:
            return
        pos = self.getControl(EPISODES).getSelectedPosition()
        with self._lock:
            rows = list(self.rows)
        if not (0 <= pos < len(rows)):
            return
        item = self.getControl(EPISODES).getSelectedItem()
        if item is not None and item.getProperty('now') == '1':
            self.close()
            return
        self.chosen = rows[pos]
        self.close()


def run(params=None):
    """skin_osd_pick: open the picker over the playing episode."""
    from . import osd
    if not xbmc.Player().isPlayingVideo():
        return None
    playing = osd._playing() or {}
    if not playing.get('series') or not playing.get('q'):
        xbmcgui.Dialog().notification('Dex Hub', _tr('هذا مو مسلسل'), time=2500, sound=False)
        return None
    try:
        osd.close_osd()
    except Exception:
        pass
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    window = Picker('dexhub_episodes.xml', path, 'Default', '1080i', playing=playing)
    window.doModal()
    chosen = window.chosen
    del window
    if not chosen:
        return None
    target = (chosen.get('props') or {}).get('dhs.target') or chosen.get('path') or ''
    if not target:
        return None
    C.log('episodes picker: S%s:E%s' % ((chosen.get('props') or {}).get('season'),
                                        (chosen.get('props') or {}).get('episode')))
    xbmc.executebuiltin('RunPlugin("%s")' % target.replace('"', '%22'))
    return None
