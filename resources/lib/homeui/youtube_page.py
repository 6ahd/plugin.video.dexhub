# -*- coding: utf-8 -*-
"""YouTube (v5.10.498): Dex Hub's own YouTube page.

Its own window (dexhub_youtube*.xml, make_youtube.py): the chips (120) are
the pages (Home, Library, Live, Trending, Music, Sport, News, then Syrian,
Turkish, Gulf and Kids for Arabic, Gaming otherwise, Search). A section is
video cards (list 70N) or round channel cards (list 80N), youtube_feed's
rows for the device's language and country. Home starts with what is the
viewer's own: continue watching, their channels and what is new on them,
then live now and trending. A channel opens as its page (live, latest,
playlists); the menu on a card follows its channel or puts it on my list. A series (a playlist) opens as its episodes, 20 to a
section; OK on an episode plays it and the next ones after it, through
the official YouTube add-on (installed on first use).
Back from a series returns to the page it came from; the page comes back
when the video ends.
"""
import threading
import time

import xbmc
import xbmcgui

from .. import youtube_feed as Y
from .. import lazy_tb as traceback
from .discover_page import A_BACK, A_LEFT, A_RIGHT, A_UP, A_DOWN, A_CONTEXT, A_MENU, _item
from .sport_page import take_focus

SLOTS = 28
CHIPS = 120
SEARCH = 130               # the search box beside the logo
WIDE = 700
ROUND = 800
PER_SECTION = 20
LOGO = 'resources/media/homeui/yt_logo.png'      # under the add-on's folder
TONE = '59FF0033'           # the page's soft light before a card gives its colour: YouTube red
READERS = 4


def _t(ar, en):
    return Y._t(ar, en)


def _line(item, in_channel=False):
    if item['kind'] == 'channel':
        return item.get('channel') or ''
    if item['kind'] == 'playlist':
        return item.get('channel') or ''
    if in_channel:
        return item.get('when') or ''
    # each part closed with the page's direction mark: an Arabic channel name
    # otherwise takes the view count after it into its own right-to-left run
    mark = '\u200e' if Y.R.language() == 'en' else '\u200f'
    return ' · '.join(mark + x + mark for x in (item.get('channel') or '', item.get('when') or item.get('views') or '') if x)


def _card(item, in_channel=False):
    """A card's list item: the picture, its title and line, and what the window draws on it."""
    if item['kind'] == 'channel':
        return _item(item.get('title') or '', _line(item), {'thumb': item.get('thumb') or ''},
                     live=_t('مباشر', 'LIVE') if item.get('live') else '')
    art = {'thumb': item.get('thumb') or '', 'fanart': item.get('thumb') or ''}
    li = _item(item.get('title') or '', _line(item, in_channel), art,
               len=item.get('length') or '', live=_t('مباشر', 'LIVE') if item.get('live') else '',
               avatar='' if in_channel else (item.get('avatar') or ''),
               count=(item.get('count') or '').split(' ')[0] if item['kind'] == 'playlist' else '',
               listword=_t('فيديو', 'videos') if item['kind'] == 'playlist' else '')
    if item.get('total') and item.get('pos'):
        li.setProperty('pct', '1')
        try:
            li.getVideoInfoTag().setResumePoint(float(item['pos']), float(item['total']))
        except Exception:
            pass
    return li


class YouTubeWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(YouTubeWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.state = kwargs.get('state') if isinstance(kwargs.get('state'), dict) else {}
        self.state.setdefault('stack', [('chip', 'home')])
        self.sections = {}        # n -> (title, [items])
        self.specs = {}           # n -> the row as a Home row (home_add), when it can be one
        self.next_play = None     # (video ids, start index)
        self.loading = None       # the loading page, open while the video starts
        self._gen = 0
        self._lock = threading.Lock()
        self._threads = []
        self.tone = None
        self._inited = False
        self._closing = False
        self._focused = False
        self._logo = ''

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('dw.t.title', 'YouTube')
        try:
            import os
            import xbmcaddon
            self._logo = os.path.join(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path'), LOGO)
        except Exception:
            pass
        self.setProperty('dw.t.logo', self._logo)
        self.setProperty('dw.b.off', '1')
        try:
            from ..art_tone import Tone
            self.tone = Tone(self)
            self.setProperty('tone', TONE)
        except Exception:
            self.tone = None
        for n in range(1, SLOTS + 1):
            self.setProperty('dw.s%d.off' % n, '1')
        self._chips()
        self._render()
        self._later(self._ready)

    def _ready(self):
        """The official add-on set up out of sight, and whether YouTube is linked.
        Not linked: asked once each time Kodi runs, since many never find the
        menu and miss the higher qualities and their own subscriptions."""
        Y.quiet_setup()
        self._account()
        if Y.addon_ready(install=False) and not Y.linked():
            home = xbmcgui.Window(10000)
            if not home.getProperty('dexhub.youtube.asked'):
                home.setProperty('dexhub.youtube.asked', '1')
                if xbmcgui.Dialog().yesno(
                        _t('اربط حسابك في YouTube', 'Link your YouTube account'),
                        _t('اربط حسابك عشان تطلع لك الجودات العالية (1080p وأعلى)، واشتراكاتك وسجلّك، '
                           'وتكمل اللي بدأته من جوالك أو أي جهاز ثاني. يأخذ دقيقة بالجوال.',
                           'Link your account for higher qualities (1080p and up), your subscriptions and history, '
                           'and to carry on what you started on your phone or any other device. It takes a minute with your phone.'),
                        nolabel=_t('لاحقاً', 'Later'), yeslabel=_t('اربط الآن', 'Link now')):
                    self._link()

    def _account(self):
        """The account at the top right; a Premium account's logo reads Premium, as on YouTube."""
        info = {}
        if not Y.addon_ready(install=False):
            text = ''
        elif Y.linked():
            info = Y.account_info()
            text = info.get('name') or _t('YouTube مربوط ✓', 'YouTube linked ✓')
        else:
            text = _t('غير مربوط · القائمة ← ربط YouTube', 'Not linked · Menu → Link YouTube')
        self.setProperty('dw.t.account', text)
        self._premium = bool(info.get('premium'))
        if self._view()[0] == 'chip':
            self.setProperty('dw.t.hello', self._brand())

    def _brand(self):
        return 'Premium' if getattr(self, '_premium', False) else 'YouTube'

    def _link(self):
        """Link YouTube on one page: the QR and the code together (LinkWindow)."""
        if not Y.addon_ready():
            return
        Y.quiet_setup()
        from . import ui_size
        window = LinkWindow(ui_size.xml('dexhub_youtube_link.xml'), self.app.addon_path, 'Default', '1080i')
        window.doModal()
        result = window.result
        del window
        self._account()
        if result == 'linked':
            with Y._lock:
                Y._mem.clear()
            self._render()

    def _later(self, job):
        from .. import profile_scope
        thread = threading.Thread(target=profile_scope.capture(job), name='DexHub-youtube')
        thread.daemon = True
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def close_window(self):
        self._closing = True
        if self.tone:
            self.tone.stop()
        self.close()

    def on_cover(self):
        pass

    def on_uncover(self):
        pass

    def on_close(self):
        self._closing = True
        for thread in self._threads:
            thread.join(9.0)

    # ---------------------------------------------------------- drawing
    def _view(self):
        return self.state['stack'][-1]

    def _search(self):
        query = xbmcgui.Dialog().input(_t('ابحث في يوتيوب', 'Search YouTube'))
        if not query:
            return
        self.state['stack'] = [self.state['stack'][0], ('search', query)]
        self._render()

    def _chips(self):
        active = self.state['stack'][0][1]
        items = [_item(label, '', None, sel='1' if key == active else '', key=key) for key, label in Y.chips()]
        control = self.getControl(CHIPS)
        control.reset()
        control.addItems(items)
        keys = [k for k, _l in Y.chips()]
        if active in keys:
            control.selectItem(keys.index(active))

    def _render(self, focus_row=None):
        self._gen += 1
        gen = self._gen
        self.sections = {}
        self.specs = {}
        for n in range(1, SLOTS + 1):
            self.setProperty('dw.s%d.off' % n, '1')
        what, key = self._view()
        self._focused = False
        self.state['focus_row'] = focus_row
        if what != 'chip' and self._number_of(self.getFocusId()):
            # the rows are about to change: the first one that fills takes the focus
            self.setFocusId(CHIPS)
        self.setProperty('dw.t.avatar', '')
        self.setProperty('dw.t.logo', self._logo)
        self.setProperty('yt.search', key if what == 'search' else _t('ابحث في YouTube', 'Search YouTube'))
        # a channel's or a series' page has its long name where the box would sit
        self.setProperty('yt.box.off', '' if what in ('chip', 'search') else '1')
        if what == 'chip':
            label = dict(Y.chips()).get(key, 'YouTube')
            # the logo already says YouTube: the small line over it names the page instead
            self.setProperty('dw.t.title', label if key != 'home' else _t('لك', 'For you'))
            self.setProperty('dw.t.hello', self._brand())
            self.setProperty('dw.t.sub', _t('من يوتيوب في بلدك · %s', 'From YouTube in your country · %s') % Y.R.country())
            self._later(lambda: self._load_rows(gen, Y.rows_of(key)))
        elif what == 'search':
            self.setProperty('dw.t.title', _t('نتائج البحث', 'Search results'))
            self.setProperty('dw.t.hello', self._brand())
            self.setProperty('dw.t.sub', _t('نتائج «%s»', 'Results for "%s"') % key)
            rows = [{'title': _t('فيديوهات', 'Videos'), 'kind': 'search', 'query': key, 'filters': Y.params('video')},
                    {'title': _t('قنوات', 'Channels'), 'kind': 'search', 'query': key, 'filters': Y.params('channel')},
                    {'title': _t('قوائم ومسلسلات', 'Playlists and series'), 'kind': 'search', 'query': key, 'filters': Y.params('playlist')},
                    {'title': _t('حلقات وأفلام طويلة', 'Episodes and long videos'), 'kind': 'search', 'query': key,
                     'filters': Y.params('video', length='long')}]
            self._later(lambda: self._load_rows(gen, rows))
        elif what == 'channel':
            cid, title, thumb = key
            self.setProperty('dw.t.title', 'YouTube  ›  ' + _t('قناة', 'Channel'))
            self.setProperty('dw.t.logo', '')
            self.setProperty('dw.t.avatar', thumb or '')
            self.setProperty('dw.t.hello', title)
            self._channel_sub(cid)
            self._later(lambda: self._load_channel(gen, cid))
        else:
            playlist_id, title = key
            self.setProperty('dw.t.title', 'YouTube  ›  ' + _t('مسلسل', 'Series'))
            self.setProperty('dw.t.hello', title)
            self.setProperty('dw.t.sub', _t('جاري تحميل الحلقات…', 'Loading the episodes…'))
            self._later(lambda: self._load_playlist(gen, playlist_id))

    def _channel_sub(self, cid):
        followed = Y.is_followed(cid)
        self.setProperty('dw.t.sub', (_t('تتابعها ✓ · القائمة لإلغاء المتابعة', 'Following ✓ · Menu to unfollow') if followed else
                                      _t('القائمة ← تابِع القناة', 'Menu → Follow this channel')))

    def _load_rows(self, gen, rows):
        """Each row gets its own run of sections (a topic: one a shelf), in the page's order."""
        from concurrent.futures import ThreadPoolExecutor
        jobs, n = [], 1
        for row in rows:
            slots = row.get('slots', 1)
            if n + slots - 1 > SLOTS:
                slots = SLOTS - n + 1
            if slots <= 0:
                break
            jobs.append((n, slots, row))
            n += slots

        def read(job):
            first, slots, row = job
            if self._closing or gen != self._gen:
                return
            try:
                answers = Y.fill(row)[:slots]
            except Exception:
                xbmc.log('[DexHub] youtube row %s:\n%s' % (row.get('kind'), traceback.format_exc()), xbmc.LOGWARNING)
                answers = []
            for k, (title, found, shape) in enumerate(answers):
                self.specs[first + k] = _home_spec(row, title)
                self._show(gen, first + k, title, found, shape=shape)
        with ThreadPoolExecutor(max_workers=READERS, thread_name_prefix='DexHub-youtube-read') as pool:
            list(pool.map(read, jobs))

    def _load_channel(self, gen, cid):
        info, rows = Y.channel_rows(cid)
        if self._closing or gen != self._gen:
            return
        if info.get('thumb'):
            self.setProperty('dw.t.avatar', info['thumb'])
        for n, (title, found, shape) in enumerate(rows[:SLOTS], 1):
            self.specs[n] = Y.row_spec('channel', cid, title=info.get('title') or title) if found and found[0]['kind'] == 'video' else None
            self._show(gen, n, title, found, shape=shape, in_channel=True)

    def _load_playlist(self, gen, playlist_id):
        episodes = Y.playlist(playlist_id)
        if self._closing or gen != self._gen:
            return
        self.setProperty('dw.t.sub', (_t('%d حلقة', '%d episodes') % len(episodes)) if episodes else
                         _t('ما لقيت حلقات', 'No episodes found'))
        self.state['episodes'] = [e['id'] for e in episodes]
        for n in range(1, SLOTS + 1):
            part = episodes[(n - 1) * PER_SECTION:n * PER_SECTION]
            if not part:
                break
            first = (n - 1) * PER_SECTION + 1
            self._show(gen, n, _t('الحلقات %d - %d', 'Episodes %d - %d') % (first, first + len(part) - 1), part, start=first - 1)

    def _show(self, gen, n, title, found, start=None, shape='wide', in_channel=False):
        items = [_card(item, in_channel) for item in found]
        with self._lock:
            if self._closing or gen != self._gen:
                return
            self.sections[n] = (title, found, start, shape)
            base = ROUND if shape == 'round' else WIDE
            control = self.getControl(base + n)
            control.reset()
            control.addItems(items)
            self.setProperty('dw.s%d.kind' % n, shape)
            self.setProperty('dw.s%d.heading' % n, title)
            self.setProperty('dw.s%d.eyebrow' % n, '')
            self.setProperty('dw.s%d.src' % n, '')
            self.setProperty('dw.s%d.off' % n, '' if items else '1')
            self._wire()
            first = not self._focused and items and self.getFocusId() in (0, CHIPS) and n == min(
                k for k, v in self.sections.items() if v[1])
        if first and self._view()[0] != 'chip':
            self._focused = True
            take_focus(self, base + n)

    # ---------------------------------------------------------- moving
    def _number_of(self, cid):
        for base in (WIDE, ROUND):
            if base < cid <= base + SLOTS:
                return cid - base
        return 0

    def _list_of(self, n):
        return (ROUND if self.sections[n][3] == 'round' else WIDE) + n

    def _wire(self):
        try:
            chain = [self._list_of(n) for n in sorted(self.sections) if self.sections[n][1]]
            self.getControl(CHIPS).controlDown(self.getControl(chain[0] if chain else CHIPS))
            previous = CHIPS
            for index, cid in enumerate(chain):
                control = self.getControl(cid)
                control.controlUp(self.getControl(previous))
                control.controlDown(self.getControl(chain[index + 1] if index + 1 < len(chain) else cid))
                previous = cid
        except (RuntimeError, IndexError, TypeError):
            pass

    def _card_at(self, cid):
        n = self._number_of(cid)
        if not n or n not in self.sections:
            return n, None, 0
        try:
            pos = self.getControl(cid).getSelectedPosition()
        except RuntimeError:
            return n, None, 0
        found = self.sections[n][1]
        return n, (found[pos] if 0 <= pos < len(found) else None), pos

    def _describe(self, cid):
        _n, item, _pos = self._card_at(cid)
        if item:
            self.setProperty('dw.f.fanart', item.get('big') or item.get('thumb') or '')
            if self.tone:
                self.tone.follow(item.get('thumb') or '')

    # ---------------------------------------------------------- keys
    def onAction(self, action):
        aid = action.getId()
        focus = self.getFocusId()
        if aid in A_BACK:
            if len(self.state['stack']) > 1:
                self.state['stack'].pop()
                self._render()
                take_focus(self, CHIPS)
            elif self.getFocusId() == SEARCH:
                take_focus(self, CHIPS)
            else:
                self.close_window()
            return
        if focus == CHIPS and aid in (A_LEFT, A_RIGHT):
            self._pick_chip(False)
            return
        if focus in (CHIPS, SEARCH) and aid in (A_CONTEXT, A_MENU):
            self._settings()
            return
        if self._number_of(focus):
            if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
                self._describe(focus)
            elif aid in (A_CONTEXT, A_MENU):
                self._menu(focus)

    def onFocus(self, cid):
        if self._number_of(cid):
            self._describe(cid)

    def _pick_chip(self, clicked):
        try:
            key = Y.chips()[self.getControl(CHIPS).getSelectedPosition()][0]
        except (RuntimeError, IndexError):
            return
        if key == 'search':
            if not clicked:
                return
            query = xbmcgui.Dialog().input(_t('ابحث في يوتيوب', 'Search YouTube'))
            if not query:
                return
            self.state['stack'] = [('chip', 'search'), ('search', query)]
            self._chips()
            self._render()
            return
        if self.state['stack'] == [('chip', key)]:
            return
        self.state['stack'] = [('chip', key)]
        self._chips()
        self._render()

    def onClick(self, cid):
        if cid == SEARCH:
            self._search()
            return
        if cid == CHIPS:
            self._pick_chip(True)
            return
        _n, item, pos = self._card_at(cid)
        if not item:
            return
        if item['kind'] == 'channel':
            self._open_channel(item['id'], item.get('title') or '', item.get('thumb') or '')
            return
        if item['kind'] == 'playlist':
            self.state['stack'].append(('playlist', (item['id'], item['title'])))
            self._render()
            return
        n = self._number_of(cid)
        start = self.sections[n][2]
        if start is not None and self.state.get('episodes'):
            # an episode of a series: it and the ones after it
            self._play_now(self.state['episodes'], start + pos)
        else:
            # a row's video and the ones after it in the row (not live ones)
            row = [i for i in self.sections[n][1][pos:] if i['kind'] == 'video']
            row = [row[0]] + [i for i in row[1:] if not i.get('live')] if not item.get('live') else [item]
            self._play_now([i['id'] for i in row[:25]], 0, items=row[:25], resume=item.get('pos') or 0)

    def _open_channel(self, cid, title='', thumb=''):
        if not cid:
            return
        if self._view()[0] == 'channel' and self._view()[1][0] == cid:
            return
        self.state['stack'].append(('channel', (cid, title, thumb)))
        self._render()

    def _play_now(self, ids, start, items=None, resume=0):
        """Apple TV and Android: the device's YouTube app plays it (the page stays).
        Elsewhere: close the page and play through the official YouTube add-on,
        once it is there (the page stays otherwise)."""
        if Y.open_in_app(ids[start]):
            return
        if not Y.addon_ready():
            xbmcgui.Dialog().notification('YouTube', _t('التشغيل يحتاج إضافة YouTube الرسمية', 'Playing needs the official YouTube add-on'),
                                          xbmcgui.NOTIFICATION_WARNING, 4000)
            return
        Y.quiet_setup()                      # a fresh install: no wizard before the video
        known = dict((i['id'], i) for i in items or [])
        for n, (_title, found, _start, _shape) in self.sections.items():
            for i in found:
                if i['kind'] == 'video':
                    known.setdefault(i['id'], i)
        self.next_play = (ids, start, known, resume)
        # the loading page goes up before this page closes: no glimpse of what is under it
        self.loading = _loading(self.app, known.get(ids[start]) or {'title': '', 'thumb': ''})
        self.close_window()

    def _settings(self):
        """Menu on the chips: link YouTube, the quality and the country (the add-on itself stays out of sight)."""
        import xbmcaddon
        addon = xbmcaddon.Addon('plugin.video.dexhub')
        countries = [('', _t('تلقائي (حسب الجهاز)', 'Automatic (this device)')), ('SA', _t('السعودية', 'Saudi Arabia')),
                     ('AE', _t('الإمارات', 'UAE')), ('KW', _t('الكويت', 'Kuwait')), ('QA', _t('قطر', 'Qatar')),
                     ('BH', _t('البحرين', 'Bahrain')), ('OM', _t('عُمان', 'Oman')), ('EG', _t('مصر', 'Egypt')),
                     ('JO', _t('الأردن', 'Jordan')), ('SY', _t('سوريا', 'Syria')), ('IQ', _t('العراق', 'Iraq')),
                     ('MA', _t('المغرب', 'Morocco')), ('TR', _t('تركيا', 'Turkey')), ('US', _t('أمريكا', 'United States')),
                     ('GB', _t('بريطانيا', 'United Kingdom'))]
        if self._view()[0] == 'channel':
            cid, title, thumb = self._view()[1]
            pick = xbmcgui.Dialog().select(title, [_t('إلغاء المتابعة', 'Unfollow') if Y.is_followed(cid) else _t('تابِع القناة', 'Follow'),
                                                   _t('إعدادات YouTube', 'YouTube settings')])
            if pick == 0:
                self._toggle_follow(cid, title, thumb)
            if pick != 1:
                return
        region = Y.R.chosen()
        linked = Y.linked()
        options = [_t('فك ربط YouTube', 'Unlink YouTube') if linked else _t('ربط YouTube (باركود)', 'Link YouTube (QR code)'),
                   _t('الجودة: %s', 'Quality: %s') % Y.quality(),
                   _t('الترجمة: %s', 'Subtitles: %s') % Y.subtitle_label(),
                   _t('الدولة: %s', 'Country: %s') % (dict(countries).get(region) or region or
                                                    _t('تلقائي (%s)', 'Automatic (%s)') % Y.R.country())]
        pick = xbmcgui.Dialog().select('YouTube', options)
        if pick == 0:
            if not linked:
                self._link()
            elif xbmcgui.Dialog().yesno('YouTube', _t('فك ربط حساب YouTube من هذا الجهاز؟', 'Unlink the YouTube account from this device?')):
                Y.sign_out()
                self._later(lambda: (xbmc.Monitor().waitForAbort(4), self._account()))
        elif pick == 1:
            if Y.addon_ready():
                n = xbmcgui.Dialog().select(_t('الجودة', 'Quality'), [label for _v, label in Y.QUALITIES])
                if n >= 0:
                    Y.set_quality(n)
        elif pick == 2:
            choices = Y.subtitle_choices()
            n = xbmcgui.Dialog().select(_t('ترجمة YouTube', 'YouTube subtitles'), [label for _k, label in choices])
            if n >= 0 and Y.addon_ready():
                Y.set_subtitles(choices[n][0])
        elif pick == 3:
            n = xbmcgui.Dialog().select(_t('الدولة', 'Country'), [label for _c, label in countries])
            if n >= 0:
                addon.setSetting('device_region', countries[n][0])
                with Y._lock:
                    Y._mem.clear()
                self._later(Y.quiet_setup)
                self._render()

    def _menu(self, cid):
        n, item, _pos = self._card_at(cid)
        if not item:
            return
        choices = []
        if item['kind'] == 'channel':
            choices.append(('channel', _t('افتح القناة', 'Open the channel')))
            choices.append(('follow', _t('إلغاء المتابعة', 'Unfollow') if Y.is_followed(item['id']) else _t('تابِع القناة', 'Follow')))
        else:
            if item['kind'] == 'playlist':
                choices.append(('play', _t('شغّل من البداية', 'Play from the start')))
                choices.append(('open', _t('افتح القائمة', 'Open the playlist')))
            else:
                if item.get('pos'):
                    choices.append(('resume', _t('أكمل المشاهدة', 'Resume')))
                choices.append(('play', _t('شغّل من البداية', 'Play from the start') if item.get('pos') else _t('شغّل', 'Play')))
            choices.append(('list', _t('أزل من قائمتي', 'Remove from my list') if Y.in_list(item['id']) else _t('أضف إلى قائمتي', 'Add to my list')))
            if item.get('cid'):
                choices.append(('channel', _t('افتح القناة: %s', 'Open the channel: %s') % (item.get('channel') or '')))
                choices.append(('follow', (_t('إلغاء متابعة %s', 'Unfollow %s') if Y.is_followed(item['cid']) else
                                           _t('تابِع %s', 'Follow %s')) % (item.get('channel') or _t('القناة', 'the channel'))))
            if any(h['id'] == item['id'] for h in Y.history()):
                choices.append(('forget', _t('احذف من السجل', 'Remove from history')))
        if item['kind'] == 'channel':
            choices.append(('home_channel', _t('أضف القناة للواجهة', 'Add the channel to the Home screen')))
        elif item['kind'] == 'playlist':
            choices.append(('home_playlist', _t('أضف القائمة للواجهة', 'Add the playlist to the Home screen')))
        if self.specs.get(n):
            choices.append(('home_row', _t('أضف هذا الصف للواجهة', 'Add this row to the Home screen')))
        pick = xbmcgui.Dialog().select(item.get('title') or '', [label for _k, label in choices])
        if pick < 0:
            return
        key = choices[pick][0]
        if key in ('home_row', 'home_channel', 'home_playlist'):
            from .. import home_add
            if key == 'home_row':
                spec = self.specs.get(n)
            elif key == 'home_channel':
                spec = Y.row_spec('channel', item['id'], title=item.get('title') or '')
            else:
                spec = Y.row_spec('playlist', item['id'], title=item.get('title') or '')
            home_add.add([spec], name_hint='YouTube', icon='youtube')
            return
        if key == 'open':
            self.onClick(cid)
        elif key == 'channel':
            if item['kind'] == 'channel':
                self.onClick(cid)
            else:
                self._open_channel(item['cid'], item.get('channel') or '', item.get('avatar') or '')
        elif key == 'follow':
            target = item['id'] if item['kind'] == 'channel' else item['cid']
            name = item.get('title') if item['kind'] == 'channel' else item.get('channel')
            self._toggle_follow(target, name or '', (item.get('thumb') if item['kind'] == 'channel' else item.get('avatar')) or '')
        elif key == 'list':
            on = Y.toggle_list(item)
            xbmcgui.Dialog().notification('YouTube', _t('أُضيف إلى قائمتي', 'Added to my list') if on else
                                          _t('أُزيل من قائمتي', 'Removed from my list'), xbmcgui.NOTIFICATION_INFO, 2500)
            self._refresh_own()
        elif key == 'forget':
            Y.forget(item['id'])
            self._refresh_own()
        elif key in ('play', 'resume') and item['kind'] == 'playlist':
            episodes = Y.playlist(item['id'])
            if episodes:
                self._play_now([e['id'] for e in episodes], 0, items=episodes)
        elif key in ('play', 'resume'):
            self._play_now([item['id']], 0, items=[item], resume=(item.get('pos') or 0) if key == 'resume' else 0)

    def _toggle_follow(self, cid, title='', thumb=''):
        if Y.is_followed(cid):
            Y.unfollow(cid)
            text = _t('ألغيت متابعة %s', 'Unfollowed %s') % title
        else:
            Y.follow(cid, title, thumb)
            text = _t('تتابع الآن %s', 'Now following %s') % title
        xbmcgui.Dialog().notification('YouTube', text, xbmcgui.NOTIFICATION_INFO, 2500)
        if self._view()[0] == 'channel':
            self._channel_sub(self._view()[1][0])
        else:
            self._refresh_own()

    def _refresh_own(self):
        """The page again when it shows the viewer's own rows (follows, list, history)."""
        if self._view() in (('chip', 'home'), ('chip', 'library')):
            self._render()

def _home_spec(row, title):
    """A row of the page as a Home row (youtube_feed.row_spec), or None for the ones that cannot be."""
    kind = row.get('kind')
    account = {'a_continue': 'continue', 'a_history': 'history', 'a_later': 'later', 'a_new': 'subscriptions', 'a_home': 'home'}
    if kind in account:
        return Y.row_spec('account', account[kind], title=title)
    if kind == 'live':
        return Y.row_spec('live', title=title)
    if kind == 'trending':
        return Y.row_spec('trending', title=title)
    if kind == 'topic':
        return Y.row_spec('topic', row.get('name') or '', title, title=title)
    if kind == 'search' and row.get('query'):
        return Y.row_spec('search', row['query'], title=title)
    return None


class LinkWindow(xbmcgui.WindowXMLDialog):
    """Link YouTube on one page (dexhub_youtube_link.xml), as the YouTube app does:
    the QR beside the address and the code; when YouTube asks for a second code
    it takes the first one's place. Closes itself once linked; Back stops."""

    def __init__(self, *args, **kwargs):
        super(LinkWindow, self).__init__(*args)
        self.result = ''
        self._stop = False
        self._started = False

    def onInit(self):
        if self._started:
            return
        self._started = True
        try:
            import os
            import xbmcaddon
            self.setProperty('yt.l.logo', os.path.join(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path'), LOGO))
        except Exception:
            pass
        for key, ar, en in (('title', 'سجّل الدخول إلى YouTube', 'Sign in to YouTube'),
                            ('intro', 'اربط حسابك لتشوف اشتراكاتك وسجلّك ومتابعة المشاهدة من أجهزتك الثانية',
                             'Link your account for your subscriptions, history and continue watching from your other devices'),
                            ('scan', 'صوّر الرمز بكاميرا جوالك', 'Scan with your phone camera'),
                            ('or', 'أو', 'or'),
                            ('open', 'على جوالك أو كمبيوترك افتح', 'On your phone or computer, go to'),
                            ('enter', 'وأدخل هذا الرمز', 'and enter this code'),
                            ('back', 'رجوع للإلغاء', 'Back to cancel')):
            self.setProperty('yt.l.' + key, _t(ar, en))
        self.setProperty('yt.l.url', 'youtube.com/activate')
        self.setProperty('yt.l.state', _t('جاري طلب رمز من YouTube…', 'Asking YouTube for a code…'))
        thread = threading.Thread(target=self._run, name='DexHub-youtube-link')
        thread.daemon = True
        thread.start()

    def _show_code(self, url, code, n):
        short = url.replace('https://', '').replace('http://', '').replace('www.', '')
        try:
            from ..plex_qr import qr_png
            # the code rides along, so the page opened by the camera has it filled in
            target = url + ('&' if '?' in url else '?') + 'user_code=' + code
            path = qr_png(target, box_size=10, border=2, family='youtube_link_qr') or ''
        except Exception:
            path = ''
        self.setProperty('yt.l.qr', path)
        self.setProperty('yt.l.url', short)
        self.setProperty('yt.l.code', code)
        self.setProperty('yt.l.step', '' if n == 1 else _t('الرمز %d · YouTube يطلب رمز ثاني', 'Code %d · YouTube asks for one more') % n)
        self.setProperty('yt.l.state', _t('بانتظار موافقتك على جوالك…', 'Waiting for you to allow it…'))

    def _show_state(self, state, n):
        if state == 'ok':
            self.setProperty('yt.l.state', _t('تم ✓ · لحظة…', 'Done ✓ · one moment…'))
        elif state == 'expired_token':
            self.setProperty('yt.l.state', _t('انتهت صلاحية الرمز، اطلع وادخل من جديد', 'The code expired; leave and try again'))
        elif state == 'access_denied':
            self.setProperty('yt.l.state', _t('رفضت الربط من جوالك', 'It was declined on your phone'))

    def _run(self):
        try:
            self.result = Y.link(self._show_code, self._show_state, lambda: self._stop)
        except Exception:
            xbmc.log('[DexHub] youtube link:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            self.result = 'failed'
        if self._stop:
            return
        if self.result == 'linked':
            self.setProperty('yt.l.done', _t('تم ربط حسابك في YouTube', 'Your YouTube account is linked'))
            xbmc.Monitor().waitForAbort(2.5)
        else:
            self.setProperty('yt.l.code', '')
            self.setProperty('yt.l.state', _t('ما قدرت أربط الحساب الحين، جرّب بعد شوي', 'Could not link the account now; try again in a moment'))
            xbmc.Monitor().waitForAbort(4)
        self.close()

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
            self._stop = True
            self.close()


def _play(ids, start, known=None, resume=0):
    """The chosen video and the ones after it (a series' next episodes), as Kodi's video playlist."""
    if not Y.addon_ready(install=False):
        xbmcgui.Dialog().notification('YouTube', _t('ثبّت إضافة YouTube الرسمية أولاً', 'Install the official YouTube add-on first'),
                                      xbmcgui.NOTIFICATION_WARNING, 4000)
        return
    playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
    playlist.clear()
    for video_id in ids[start:start + 50]:
        info = (known or {}).get(video_id) or {}
        url = Y.dexhub_url(video_id, info.get('title') or '')
        item = xbmcgui.ListItem(info.get('title') or video_id, offscreen=True)
        item.setProperty('IsPlayable', 'true')
        if info.get('thumb'):
            item.setArt({'thumb': info['thumb']})
        item.setPath(url)
        playlist.add(url, item)
    xbmc.Player().play(playlist)


def _playing_id(player):
    import re
    try:
        path = player.getPlayingFile() if player.isPlayingVideo() else ''
    except RuntimeError:
        path = ''
    path = path or xbmc.getInfoLabel('Player.FilenameAndPath') or ''
    found = re.search(r'[?&]v=([\w-]{11})', path) or re.search(r'file=([\w-]{11})\.mpd', path)
    return found.group(1) if found else ''


class LoadingWindow(xbmcgui.WindowXMLDialog):
    """Before the video plays (dexhub_youtube_loading.xml): its picture dimmed behind
    Dex Hub's and YouTube's logos, its title and channel, a spinner."""


def _loading(app, item):
    """The loading page shown (not waited on); None when it cannot open."""
    try:
        import os
        import xbmcaddon
        from . import ui_size
        root = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
        window = LoadingWindow(ui_size.xml('dexhub_youtube_loading.xml'), app.addon_path, 'Default', '1080i')
        window.setProperty('yt.p.art', item.get('big') or item.get('thumb') or '')
        window.setProperty('yt.p.dex', os.path.join(root, 'resources', 'media', 'dexhub_infinity.png'))
        window.setProperty('yt.p.yt', os.path.join(root, LOGO))
        window.setProperty('yt.p.title', item.get('title') or '')
        window.setProperty('yt.p.channel', item.get('channel') or '')
        window.setProperty('yt.p.state', _t('جاري التشغيل…', 'Starting…'))
        window.show()
        xbmc.log('[DexHub] youtube: loading page up', xbmc.LOGINFO)
        return window
    except Exception:
        xbmc.log('[DexHub] youtube loading page:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        return None


def _watch(known, resume, loading=None):
    """While it plays: jump to where it was left (once), and keep how far each video got
    in the history, its last position written when it changes or stops."""
    player, monitor = xbmc.Player(), xbmc.Monitor()
    for _wait in range(180):          # the first picture (up to 90 s): Kodi opens the
        try:                          # file long before its clock starts moving
            if player.isPlaying() and player.getTime() > 0.2:
                break
        except RuntimeError:
            pass
        if monitor.waitForAbort(0.5):
            break
    if loading is not None:
        try:
            loading.close()
        except RuntimeError:
            pass
        del loading
    seen = {}                          # id -> (position, total)
    current = ''
    subtitles_due = 0
    while player.isPlaying() and not monitor.abortRequested():
        video_id = _playing_id(player)
        try:
            position, total = player.getTime(), player.getTotalTime()
        except RuntimeError:
            position, total = 0, 0
        if video_id and video_id != current:
            if current in seen:
                _keep(known, current, *seen[current])
            current = video_id
            subtitles_due = time.time() + 15         # the player lists them a moment after it starts
            if resume and total and 5 < resume < total - 10 and position < resume - 5:
                try:
                    player.seekTime(float(resume))
                except RuntimeError:
                    pass
            resume = 0
        if subtitles_due and (Y.apply_subtitles(player) or time.time() > subtitles_due):
            subtitles_due = 0
        if video_id and total:
            seen[video_id] = (position, total)
        monitor.waitForAbort(1.0)
    if current in seen:
        _keep(known, current, *seen[current])


def _keep(known, video_id, position, total):
    item = dict((known or {}).get(video_id) or {'id': video_id, 'title': '', 'thumb': 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % video_id})
    if item.get('live'):
        position, total = 0, 0          # a stream has no place to come back to
    try:
        Y.remember(item, position, total)
    except Exception:
        xbmc.log('[DexHub] youtube: history not kept:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)


def open_youtube(app):
    """The page; after a video the page comes back where it was."""
    from . import ui_size
    state = {}
    while True:
        window = YouTubeWindow(ui_size.xml('dexhub_youtube.xml'), app.addon_path, 'Default', '1080i', app=app, state=state)
        app._show(window)
        play = window.next_play
        loading = window.loading
        del window
        if not play:
            return
        ids, start, known, resume = play
        try:
            _play(ids, start, known, resume)
        except Exception:
            xbmc.log('[DexHub] youtube: playback failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            if loading is not None:
                loading.close()
            return
        _watch(known, resume, loading)
        if xbmc.Monitor().abortRequested():
            return
