from resources.lib.tvos_assets import resolve_assets as _tvos_assets
# -*- coding: utf-8 -*-
"""The tracking panel and the rating prompt (v5.10.153).

TrackPanel (dexhub_track.xml): a sheet at the right of the title page with
the title's status, a 1 to 10 rating and the lists it is in. It opens with
what is known already (tracking_state.json) and reads Floppy afterwards;
every change is saved off the UI thread and the line at the bottom says
which services took it.

RatePrompt (dexhub_rate.xml): a small card after a movie ends (setting
tracking_rate_prompt: movies, all, off). It closes by itself.
"""
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

from . import floppy_api
from . import tracking
from .i18n import is_english

ADDON = xbmcaddon.Addon('plugin.video.dexhub')
MEDIA = _tvos_assets('special://home/addons/plugin.video.dexhub/resources/media/track/')
_TONES = {'Planning': '478B7CF8', 'In progress': '478B7CF8', 'Completed': '472BC48A',
          'Paused': '47F5B428', 'Dropped': '47E5484D', '': '47E5484D'}
_ICONS = {'Planning': 'plan', 'In progress': 'watching', 'Completed': 'completed',
          'Paused': 'paused', 'Dropped': 'dropped', '': 'remove'}
ACTION_BACK = (9, 10, 13, 92, 110)


def _t(ar, en):
    return en if is_english() else ar


def _path():
    return ADDON.getAddonInfo('path')


def _joined(names):
    return u' · '.join(names)


class TrackPanel(xbmcgui.WindowXMLDialog):

    def __init__(self, *args, **kwargs):
        super(TrackPanel, self).__init__(*args)
        self.work = kwargs.get('work')
        self.state = {'status': '', 'score': None, 'lists': [], 'favorite': False}
        self.floppy_lists = []
        self.extra = {}
        self._busy = threading.Lock()
        self.changed = False

    # ------------------------------------------------------------ building
    def onInit(self):
        work = self.work
        old = tracking.remembered(work.key)
        self.state.update(status=old.get('status') or '', score=old.get('score'))
        try:
            from . import favorites_store
            self.state['favorite'] = bool(favorites_store.is_favorite(work.media, work.canonical))
        except Exception:
            pass
        floppy = floppy_api.configured()
        self.setProperty('kicker', _t('تتبّع في Floppy', 'Track on Floppy') if floppy else _t('تتبّع', 'Track'))
        self.setProperty('kicker_icon', _tvos_assets('special://home/addons/plugin.video.dexhub/resources/media/floppy.png') if floppy else '')
        self.setProperty('title', work.title or work.canonical)
        kind = _t('مسلسل', 'Series') if work.media == 'series' else _t('فيلم', 'Movie')
        self.setProperty('sub', u'%s%s' % (kind, (u' · %d' % work.year) if work.year else ''))
        self.setProperty('h_status', _t('الحالة', 'STATUS'))
        self.setProperty('h_rating', _t('تقييمك', 'YOUR RATING'))
        self.setProperty('h_lists', _t('القوائم', 'LISTS'))
        services = tracking.linked_services()
        if services:
            self._note(_t('ينحفظ في: %s', 'Saved to: %s') % _joined(services), 'info')
        else:
            self._note(_t('ينحفظ في جهازك. اربط Floppy أو Trakt عشان ينحفظ في حسابك.',
                          'Saved on this device. Link Floppy or Trakt to keep it in your account.'), 'info')
        self._fill_status()
        self._fill_rating()
        self._fill_lists()
        # v5.10.167: the status buttons have the focus as the panel opens (the
        # list was empty when Kodi tried its default control, so the first
        # key only gave the focus)
        try:
            self.setFocusId(100)
        except Exception:
            pass
        threading.Thread(target=self._read_remote, name='dexhub-track-read').start()

    def _note(self, text, kind='info'):
        self.setProperty('note', text)
        self.setProperty('note_kind', kind)

    def _fill_status(self):
        ctl = self.getControl(100)
        pos = ctl.getSelectedPosition() if ctl.size() else 0
        ctl.reset()
        items = []
        for status in tracking.STATUSES + ('',):
            label = tracking.status_label(status) if status else _t('إزالة', 'Remove')
            li = xbmcgui.ListItem(label)
            li.setArt({'icon': MEDIA + _ICONS[status] + '.png'})
            li.setProperty('status', status or 'remove')
            li.setProperty('tone', _TONES[status])
            if status and status == self.state.get('status'):
                li.setProperty('on', '1')
            items.append(li)
        ctl.addItems(items)
        ctl.selectItem(max(0, pos))

    def _fill_rating(self):
        ctl = self.getControl(200)
        pos = ctl.getSelectedPosition() if ctl.size() else None
        ctl.reset()
        score = self.state.get('score')
        try:
            score = int(round(float(score))) if score not in (None, '') else 0
        except (TypeError, ValueError):
            score = 0
        items = []
        for value in range(1, 11):
            li = xbmcgui.ListItem(str(value))
            li.setProperty('value', str(value))
            fill = 'sel' if value == score else ('on' if value < score else '')
            li.setProperty('fill', fill)
            li.setProperty('ink', 'FFFFD27A' if fill == 'on' else 'FFA3ABB9')
            items.append(li)
        ctl.addItems(items)
        ctl.selectItem(pos if pos is not None else max(0, score - 1))

    def _rows(self):
        rows = [('fav', _t('مفضلتي', 'My Favorites'), 'Dex Hub', bool(self.state.get('favorite')))]
        member = set(self.state.get('lists') or [])
        for row in self.floppy_lists:
            rows.append(('floppy:%s' % row.get('id'), row.get('name') or '?',
                         u'Floppy · %s' % (row.get('items_count') or 0), int(row['id']) in member))
        for name, label, on in self._service_lists():
            rows.append((name, label, '', on))
        if floppy_api.configured():
            rows.append(('new', _t('قائمة جديدة', 'New list'), 'Floppy', False))
        return rows

    def _service_lists(self):
        out = []
        try:
            from . import favorites_store
            key = (self.work.media, self.work.canonical)
            if tracking._trakt_ready():
                out.append(('trakt', _t('قائمة المشاهدة في Trakt', 'Trakt watchlist'),
                            self.extra.get('trakt', key in favorites_store.favorite_keys(source='trakt'))))
            if tracking._mdblist_ready():
                out.append(('mdblist', _t('قائمة المشاهدة في MDBList', 'MDBList watchlist'),
                            self.extra.get('mdblist', key in favorites_store.favorite_keys(source='mdblist'))))
        except Exception:
            pass
        return out

    def _fill_lists(self):
        ctl = self.getControl(300)
        pos = ctl.getSelectedPosition() if ctl.size() else 0
        ctl.reset()
        items = []
        for name, label, label2, on in self._rows():
            li = xbmcgui.ListItem(label, label2)
            li.setProperty('name', name)
            li.setProperty('kind', 'new' if name == 'new' else 'list')
            if on:
                li.setProperty('on', '1')
            items.append(li)
        ctl.addItems(items)
        ctl.selectItem(max(0, min(pos, len(items) - 1)))

    def _read_remote(self):
        try:
            state = tracking.read_state(self.work, fresh=True)
            self.state.update(state)
            if floppy_api.configured():
                self.floppy_lists = floppy_api.lists()
            self._fill_status()
            self._fill_rating()
            self._fill_lists()
        except Exception as exc:
            xbmc.log('[DexHub] track panel read: %s' % exc.__class__.__name__, xbmc.LOGINFO)

    # ------------------------------------------------------------ actions
    def onAction(self, action):
        if action.getId() in ACTION_BACK:
            self.close()

    def onClick(self, control_id):
        if control_id == 100:
            item = self.getControl(100).getSelectedItem()
            status = item.getProperty('status') if item else ''
            status = '' if status == 'remove' else status
            if status == self.state.get('status'):
                return
            self._background(lambda: self._set_status(status))
        elif control_id == 200:
            item = self.getControl(200).getSelectedItem()
            if item:
                value = int(item.getProperty('value') or 0)
                self._background(lambda: self._set_score(value))
        elif control_id == 300:
            item = self.getControl(300).getSelectedItem()
            if item:
                name = item.getProperty('name')
                self._background(lambda: self._toggle(name, not item.getProperty('on')))

    def _background(self, job):
        if not self._busy.acquire(False):
            return

        def run():
            try:
                self._note(_t('يحفظ…', 'Saving…'), 'info')
                job()
                self.changed = True
            except Exception as exc:
                xbmc.log('[DexHub] track panel: %s' % exc.__class__.__name__, xbmc.LOGWARNING)
                self._note(_t('ما قدرت أحفظ التغيير', 'Could not save the change'), 'error')
            finally:
                self._busy.release()
        threading.Thread(target=run, name='dexhub-track-save').start()

    def _report(self, done, failed):
        if done and not failed:
            self._note(_t('انحفظ في: %s', 'Saved to: %s') % _joined(done), 'ok')
        elif done:
            self._note(_t('انحفظ في: %s. ما وصل: %s', 'Saved to: %s. Not saved: %s') % (_joined(done), _joined(failed)), 'ok')
        elif failed:
            self._note(_t('ما وصل لـ: %s', 'Not saved to: %s') % _joined(failed), 'error')
        else:
            self._note(_t('انحفظ في جهازك', 'Saved on this device'), 'ok')

    def _set_status(self, status):
        previous = self.state.get('status') or ''
        self.state['status'] = status
        self._fill_status()
        done, failed = tracking.set_status(self.work, status, previous)
        self._report(done, failed)

    def _set_score(self, value):
        self.state['score'] = value
        self._fill_rating()
        done, failed = tracking.set_score(self.work, value)
        self._report(done, failed)

    def _toggle(self, name, member):
        work = self.work
        if name == 'fav':
            tracking.set_favorite(work, member)
            self.state['favorite'] = member
            self._report(['Dex Hub'], [])
        elif name.startswith('floppy:'):
            list_id = int(name.split(':', 1)[1])
            ok = tracking.set_floppy_list(work, list_id, member)
            lists = set(self.state.get('lists') or [])
            if ok:
                (lists.add if member else lists.discard)(list_id)
            self.state['lists'] = sorted(lists)
            self._report(['Floppy'] if ok else [], [] if ok else ['Floppy'])
        elif name in ('trakt', 'mdblist'):
            work.ensure_tmdb()
            ids = work.ids()
            bucket = tracking._bucket(work)
            if name == 'trakt':
                tracking._trakt('/sync/watchlist' if member else '/sync/watchlist/remove', {bucket: [{'ids': ids}]})
            else:
                tracking._mdblist('/watchlist/items/%s' % ('add' if member else 'remove'), {bucket: [ids]})
            self.extra[name] = member
            self._report(['Trakt' if name == 'trakt' else 'MDBList'], [])
            tracking._after_change()
        elif name == 'new':
            title = xbmcgui.Dialog().input(_t('اسم القائمة', 'List name'))
            title = (title or '').strip()
            if not title:
                self._note('', 'info')
                return
            code, data = floppy_api.request('POST', 'lists/', {'name': title})
            floppy_api.forget('lists')
            if code in (200, 201) and isinstance(data, dict) and data.get('id') is not None:
                tracking.set_floppy_list(work, int(data['id']), True)
                lists = set(self.state.get('lists') or [])
                lists.add(int(data['id']))
                self.state['lists'] = sorted(lists)
                self.floppy_lists = floppy_api.lists()
                self._report(['Floppy'], [])
            else:
                self._report([], ['Floppy'])
        self._fill_lists()


def open_panel(work):
    if not work or not work.canonical:
        return False
    panel = TrackPanel('dexhub_track.xml', _path(), 'Default', '1080i', work=work)
    panel.doModal()
    changed = panel.changed
    del panel
    return changed


# ---------------------------------------------------------------- rating prompt
class RatePrompt(xbmcgui.WindowXMLDialog):
    SECONDS = 12.0

    def __init__(self, *args, **kwargs):
        super(RatePrompt, self).__init__(*args)
        self.work = kwargs.get('work')
        self.done_text = kwargs.get('done_text') or ''
        self.picked = 0
        self._closing = False

    def onInit(self):
        self.setProperty('title', _t('وش رأيك في %s؟', 'How was %s?') % (self.work.title or ''))
        self.setProperty('done', self.done_text)
        self.setProperty('low', _t('ما عجبني', 'Not for me'))
        self.setProperty('high', _t('عجبني مرة', 'Loved it'))
        self.setProperty('skip', _t('مو الحين', 'Not now'))
        self.setProperty('closes', _t('يقفل بروحه', 'Closes by itself'))
        ctl = self.getControl(200)
        ctl.addItems([xbmcgui.ListItem(str(n)) for n in range(1, 11)])
        ctl.selectItem(7)
        self.setFocusId(200)
        threading.Thread(target=self._countdown, name='dexhub-rate-timer').start()

    def _countdown(self):
        started = time.time()
        last_input = [xbmc.getGlobalIdleTime()]
        monitor = xbmc.Monitor()
        while not self._closing and not monitor.abortRequested():
            idle = xbmc.getGlobalIdleTime()
            if idle < last_input[0]:
                started = time.time()       # a key press restarts the count
            last_input[0] = idle
            left = self.SECONDS - (time.time() - started)
            if left <= 0:
                self._closing = True
                self.close()
                return
            try:
                self.getControl(20).setPercent(100.0 * left / self.SECONDS)
                self.setProperty('left', '%d s' % int(left + 0.99))
            except Exception:
                return
            monitor.waitForAbort(0.1)

    def onAction(self, action):
        if action.getId() in ACTION_BACK:
            self._closing = True
            self.close()

    def onClick(self, control_id):
        if control_id == 200:
            item = self.getControl(200).getSelectedItem()
            self.picked = int(item.getLabel()) if item else 0
        self._closing = True
        self.close()


def _rate_mode():
    value = (ADDON.getSetting('tracking_rate_prompt') or 'movies').strip().lower()
    return value if value in ('movies', 'all', 'off') else 'movies'


def maybe_rate(ctx, finale=False):
    """After a movie (or a season's last episode with the 'all' setting)."""
    try:
        mode = _rate_mode()
        media = str(ctx.get('media_type') or 'movie').lower()
        episode = media not in ('movie',)
        if mode == 'off' or (episode and (mode != 'all' or not finale)):
            return
        services = tracking.linked_services()
        if not services:
            return
        if episode:
            work = tracking.Work('series', canonical=str(ctx.get('canonical_id') or '').split(':')[0],
                                 tmdb=ctx.get('show_tmdb_id') or '', title=ctx.get('show_title') or ctx.get('title') or '')
        else:
            work = tracking.Work('movie', canonical=ctx.get('canonical_id') or '', tmdb=ctx.get('tmdb_id') or '',
                                 imdb=ctx.get('imdb_id') or '', title=ctx.get('title') or '',
                                 poster=ctx.get('poster') or '', year=ctx.get('year') or 0)
        if not work.canonical:
            return
        if tracking.remembered(work.key).get('score'):
            return                         # rated before: no need to ask again
    except Exception:
        return

    def show():
        monitor = xbmc.Monitor()
        if monitor.waitForAbort(1.6) or xbmc.Player().isPlaying():
            return
        done = _t('تسجّل كمشاهد في %s', 'Marked watched on %s') % _joined(services)
        prompt = RatePrompt('dexhub_rate.xml', _path(), 'Default', '1080i', work=work, done_text=done)
        prompt.doModal()
        picked = prompt.picked
        del prompt
        if picked:
            done_list, failed = tracking.set_score(work, picked)
            if done_list:
                xbmcgui.Dialog().notification('Dex Hub', _t('تم حفظ تقييمك %d في %s', 'Rated %d on %s') % (picked, _joined(done_list)),
                                              MEDIA + 'star.png', 3000, False)
    threading.Thread(target=show, name='dexhub-rate').start()
