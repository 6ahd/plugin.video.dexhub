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

5.10.564: laid out as the YouTube TV app (make_youtube_tv.py): the side rail
(Search, Home, Shorts, Music, Subscriptions, Library, Live, Gaming, Sports,
News, then Trending and the Arabic pages, Settings) in place of the chips;
moving on it changes the page. Linked, each page is the TV app's own,
shelf by shelf (youtube_feed.tv_page), with the Shorts as tall cards.

5.10.564: the music player (MusicWindow, make_youtube_music.py), in the
manner of SoundCloud: what plays from the Music page (or "Play in the music
player" on any card's menu) plays there, the video unseen, with its queue;
Back leaves it playing, and Now playing at the top of the rail opens it again.
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
CHIPS = 120                # 5.10.564: the side rail (youtube_feed.rail()), as the YouTube TV app's
SEARCH = 130               # (gone in 5.10.564: Search is on the rail)
WIDE = 700
ROUND = 800
TALL = 600                 # 5.10.564: Shorts
RAIL_MEDIA = 'resources/media/homeui/yt_rail_%s.png'
MUSIC_PROP = 'dexhub.yt.music'      # 5.10.564: the music player's queue plays
_MUSIC = {}                          # its queue: ids, known, where (this interpreter keeps it)
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
    if item['kind'] == 'short':
        return _item(item.get('title') or '', item.get('channel') or '', {'thumb': item.get('thumb') or ''})
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
        self.state['opened'] = False      # 5.10.564: each time the page opens, the shelves take the focus
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
        self._root = ''
        self._move_timer = None

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('dw.t.title', 'YouTube')
        try:
            import os
            import xbmcaddon
            self._root = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
            self._logo = os.path.join(self._root, LOGO)
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
            # v5.10.515: Apple TV says where the account is
            text = '' if Y.addon_installable() else _t('التشغيل في تطبيق YouTube', 'Plays in the YouTube app')
        elif Y.linked():
            info = Y.account_info()
            text = info.get('name') or _t('YouTube مربوط ✓', 'YouTube linked ✓')
        else:
            text = _t('غير مربوط · القائمة ← ربط YouTube', 'Not linked · Menu → Link YouTube')
        self.setProperty('dw.t.account', text)
        self.setProperty('dw.t.me', info.get('photo') or '')
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
        from .app import join_briefly
        join_briefly(self._threads)

    # ---------------------------------------------------------- drawing
    def _view(self):
        return self.state['stack'][-1]

    def _search(self):
        query = xbmcgui.Dialog().input(_t('ابحث في يوتيوب', 'Search YouTube'))
        if not query:
            return
        self.state['stack'] = [self.state['stack'][0], ('search', query)]
        self._render()

    def _rail(self):
        """youtube_feed.rail(), with Now playing first while the music player's queue plays."""
        rail = Y.rail()
        if _music_playing():
            rail = [('nowplaying', _t('يُشغَّل الآن', 'Now playing'), 'music', False)] + rail
        return rail

    def _chips(self):
        """The side rail: an icon and a name each, the page shown white, Music red."""
        import os
        active = self.state['stack'][0][1]
        rail = self._rail()
        items = []
        for key, label, icon, sep in rail:
            on = key == active
            red = key in ('music', 'nowplaying')
            tint = 'FFFF0033' if red else ('FFFFFFFF' if on else '8CFFFFFF')
            items.append(_item(label, '', None, sel='1' if on else '', key=key, sep='1' if sep else '',
                               red='1' if red else '', tint=tint,
                               icon=os.path.join(self._root, RAIL_MEDIA % icon)))
        control = self.getControl(CHIPS)
        control.reset()
        control.addItems(items)
        keys = [k for k, _l, _i, _s in rail]
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
        if self._number_of(self.getFocusId()):
            # the rows are about to change: the first one that fills takes the focus
            self.setFocusId(CHIPS)
        self.setProperty('dw.t.avatar', '')
        self.setProperty('dw.t.logo', self._logo)
        self.setProperty('yt.head', '' if what == 'chip' else '1')
        self.setProperty('yt.loading', _t('جاري التحميل…', 'Loading…'))
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
        if gen == self._gen and not self._closing:
            self.setProperty('yt.loading', '' if any(v[1] for v in self.sections.values()) else
                             _t('ما فيه شي هنا الحين', 'Nothing here right now'))

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
            base = {'round': ROUND, 'tall': TALL}.get(shape, WIDE)
            if items:
                self.setProperty('yt.loading', '')
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
        # the page as the app opens: the shelves with the focus, the rail closed;
        # a page picked on the rail keeps the focus there
        if first and (self._view()[0] != 'chip' or not self.state.get('opened')):
            self._focused = True
            self.state['opened'] = True
            take_focus(self, base + n)

    # ---------------------------------------------------------- moving
    def _number_of(self, cid):
        for base in (WIDE, ROUND, TALL):
            if base < cid <= base + SLOTS:
                return cid - base
        return 0

    def _list_of(self, n):
        return {'round': ROUND, 'tall': TALL}.get(self.sections[n][3], WIDE) + n

    def _wire(self):
        try:
            chain = [self._list_of(n) for n in sorted(self.sections) if self.sections[n][1]]
            self.getControl(CHIPS).controlRight(self.getControl(chain[0] if chain else CHIPS))
            previous = chain[0] if chain else CHIPS
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
            elif focus != CHIPS:
                take_focus(self, CHIPS)          # as the app: Back opens the rail first
            else:
                self.close_window()
            return
        if focus == CHIPS and aid in (A_UP, A_DOWN):
            self._moved_on_rail()
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

    def _moved_on_rail(self):
        """Moving on the rail shows its page, as the app does, once the remote rests a moment."""
        if self._move_timer is not None:
            self._move_timer.cancel()
        self._move_timer = threading.Timer(0.35, lambda: (not self._closing and self.getFocusId() == CHIPS
                                                          and self._pick_chip(False)))
        self._move_timer.daemon = True
        self._move_timer.start()

    def _pick_chip(self, clicked):
        try:
            key = self._rail()[self.getControl(CHIPS).getSelectedPosition()][0]
        except (RuntimeError, IndexError):
            return
        if key == 'nowplaying':
            if clicked:
                self.next_play = 'nowplaying'
                self.close_window()
            return
        if key == 'settings':
            if clicked:
                self._settings()
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
            if clicked:
                self._to_shelves()
            return
        self.state['stack'] = [('chip', key)]
        self._chips()
        self._render()
        if clicked:
            self.state['opened'] = False        # OK on the rail: the page's first shelf takes the focus

    def _to_shelves(self):
        chain = [self._list_of(n) for n in sorted(self.sections) if self.sections[n][1]]
        if chain:
            take_focus(self, chain[0])

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
            if item['kind'] == 'short':
                # 5.10.564: the Shorts player, up and down through the shelf from this one
                row = [i for i in self.sections[n][1][pos:] if i['kind'] == 'short']
                self._play_now([i['id'] for i in row[:50]], 0, items=row[:50], music='shorts')
                return
            row = [i for i in self.sections[n][1][pos:] if i['kind'] in ('video', 'short')]
            row = [row[0]] + [i for i in row[1:] if not i.get('live')] if not item.get('live') else [item]
            self._play_now([i['id'] for i in row[:25]], 0, items=row[:25], resume=item.get('pos') or 0,
                           where=self.sections[n][0] or '')

    def _open_channel(self, cid, title='', thumb=''):
        if not cid:
            return
        if self._view()[0] == 'channel' and self._view()[1][0] == cid:
            return
        self.state['stack'].append(('channel', (cid, title, thumb)))
        self._render()

    def _play_now(self, ids, start, items=None, resume=0, music=None, where=''):
        """Apple TV and Android: the device's YouTube app plays it (the page stays).
        Elsewhere: close the page and play through the official YouTube add-on,
        once it is there (the page stays otherwise). music (5.10.564): in the
        music player; by default what plays from the Music page."""
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
                if i['kind'] in ('video', 'short'):
                    known.setdefault(i['id'], i)
        if music is None:
            music = self.state['stack'][0] == ('chip', 'music')
        self.next_play = (ids, start, known, resume, music,
                          where or _t('YouTube · موسيقى', 'YouTube · Music'))
        if not music:          # (the music and Shorts players show it themselves)
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
        country = _t('الدولة: %s', 'Country: %s') % (dict(countries).get(region) or region or
                                                   _t('تلقائي (%s)', 'Automatic (%s)') % Y.R.country())
        if not Y.addon_ready(install=False) and not Y.addon_installable():
            # v5.10.515: Apple TV, where the add-on cannot be installed: the
            # YouTube app plays the videos and holds the account, its quality and subtitles
            pick = xbmcgui.Dialog().select('YouTube', [
                _t('الفيديو يشتغل في تطبيق YouTube، والحساب والجودة والترجمة من هناك',
                   'Videos play in the YouTube app; your account, quality and subtitles are set there'),
                country])
            pick = 3 if pick == 1 else -1
        else:
            linked = Y.linked()
            options = [_t('فك ربط YouTube', 'Unlink YouTube') if linked else _t('ربط YouTube (باركود)', 'Link YouTube (QR code)'),
                       _t('الجودة: %s', 'Quality: %s') % Y.quality(),
                       _t('الترجمة: %s', 'Subtitles: %s') % Y.subtitle_label(),
                       country]
            pick = xbmcgui.Dialog().select('YouTube', options)
        if pick == 0:
            if not linked:
                self._link()
            elif xbmcgui.Dialog().yesno('YouTube', _t('فك ربط حساب YouTube من هذا الجهاز؟', 'Unlink the YouTube account from this device?')):
                Y.sign_out()
                from .app import page_pause
                self._later(lambda: page_pause(self, 4) or self._account())
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
                if self.state['stack'][0] == ('chip', 'music'):
                    choices.append(('as_video', _t('شغّل كفيديو', 'Play as a video')))
                else:
                    choices.append(('as_music', _t('شغّل بمشغل الموسيقى', 'Play in the music player')))
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
        elif key in ('as_music', 'as_video'):
            row = [i for i in self.sections[n][1][_pos:] if i['kind'] in ('video', 'short') and not i.get('live')] or [item]
            self._play_now([i['id'] for i in row[:25]], 0, items=row[:25], music=key == 'as_music',
                           where=self.sections[n][0] or '')
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
            state = _t('ما قدرت أربط الحساب الحين، جرّب بعد شوي', 'Could not link the account now; try again in a moment')
            if Y.LINK_ERROR[0]:
                # v5.10.513: the reason on the page (a box's log is hard to reach)
                state += '[CR][COLOR FF8A8A8A]%s[/COLOR]' % Y.LINK_ERROR[0]
            self.setProperty('yt.l.state', state)
            xbmc.Monitor().waitForAbort(12 if Y.LINK_ERROR[0] else 4)
        self.close()

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
            self._stop = True
            self.close()


def _play(ids, start, known=None, resume=0, windowed=False):
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
    xbmc.Player().play(playlist, windowed=windowed)


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
            if player.isPlayingVideo() and not xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)'):
                # 5.10.564: the player comes up under the loading page, not after it:
                # Kodi 22 left the video behind its Home, which showed in between
                xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
            if player.isPlaying() and player.getTime() > 0.2:
                break
        except RuntimeError:
            pass
        if monitor.waitForAbort(0.5):
            break
    _to_player(player, monitor)
    if loading is not None:
        try:
            loading.close()
        except RuntimeError:
            pass
        del loading
    seen = {}                          # id -> (position, total)
    current = ''
    subtitles_due = 0
    was_full, left = False, 0
    while _still_playing(player, monitor):
        # 5.10.564: Back from the player is Back to YouTube, as the app does: the
        # video stopped (its place kept) and the page again, not Kodi's Home
        # with the video playing on behind it
        try:
            full = xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)')
            window = xbmcgui.getCurrentWindowId()
        except RuntimeError:
            full, window = True, 0
        if full:
            was_full, left = True, 0
        elif was_full and (window == 10000 or 13000 <= window < 13100) and player.isPlayingVideo():
            left += 1
            if left >= 2:
                xbmc.log('[DexHub] youtube: Back from the player: the video stops, the page comes back', xbmc.LOGINFO)
                video_id = _playing_id(player)
                try:
                    position, total = player.getTime(), player.getTotalTime()
                except RuntimeError:
                    position, total = 0, 0
                if video_id and total:
                    seen[video_id] = (position, total)
                    current = video_id
                player.stop()
                break
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


def _still_playing(player, monitor):
    """5.10.564: playing, or between two videos of the queue. Kodi stops for a
    moment before the next one starts: the page came back then and sat over
    the next video, which played on behind it (heard, not seen)."""
    if monitor.abortRequested():
        return False
    if player.isPlaying():
        return True
    playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
    for _wait in range(40):                       # up to 20 s for the next one to open
        try:
            if playlist.getposition() < 0 or playlist.getposition() >= playlist.size() - 1:
                return player.isPlaying()
        except RuntimeError:
            return False
        if monitor.waitForAbort(0.5):
            return False
        if player.isPlaying():
            _to_player(player, monitor)
            return True
    return False


def _to_player(player, monitor):
    """v5.10.563: the video in Kodi's full-screen player. Kodi 22 left it
    behind the page at times (heard, not seen): Dex Hub's own playback has
    the companion's guard for that, a YouTube video had none. Only from the
    Home or a Dex Hub page; a window the user opened meanwhile is left alone."""
    cond = xbmc.getCondVisibility
    for _attempt in range(6):
        try:
            if not player.isPlayingVideo() or cond('Window.IsActive(fullscreenvideo)'):
                return
            window = xbmcgui.getCurrentWindowId()
            if window not in (10000, 10025) and not 13000 <= window < 13100:
                return
            for name in ('busydialog', 'busydialognocancel'):
                if cond('Window.IsActive(%s)' % name):
                    xbmc.executebuiltin('Dialog.Close(%s,true)' % name)
            if _attempt == 0:
                xbmc.log('[DexHub] youtube: the video started behind window %s; opening the player' % window,
                         xbmc.LOGINFO)
            xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
        except RuntimeError:
            return
        if monitor.waitForAbort(0.5):
            return


def _keep(known, video_id, position, total):
    item = dict((known or {}).get(video_id) or {'id': video_id, 'title': '', 'thumb': 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % video_id})
    if item.get('live'):
        position, total = 0, 0          # a stream has no place to come back to
    try:
        Y.remember(item, position, total)
    except Exception:
        xbmc.log('[DexHub] youtube: history not kept:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)


def _music_playing():
    try:
        return bool(_MUSIC) and xbmcgui.Window(10000).getProperty(MUSIC_PROP) == '1' and xbmc.Player().isPlaying()
    except RuntimeError:
        return False


def _music(app, queue=None):
    """The music player over the queue (a new one, or the one playing)."""
    from . import ui_size
    home = xbmcgui.Window(10000)
    if queue:
        import json
        video = _MUSIC.get('video')            # Audio | Video stays as it was chosen
        _MUSIC.clear()
        _MUSIC.update(queue)
        _MUSIC['video'] = video
        home.setProperty(MUSIC_PROP, '1')
        known = queue.get('known') or {}
        home.setProperty(Y.MUSIC_QUEUE, json.dumps(dict(
            (i, [(known.get(i) or {}).get('title') or '', (known.get(i) or {}).get('channel') or '',
                 (known.get(i) or {}).get('thumb') or '']) for i in queue.get('ids') or [])))
    if not _MUSIC:
        return
    while True:
        window = MusicWindow(ui_size.xml('dexhub_youtube_music.xml'), app.addon_path, 'Default', '1080i', **_MUSIC)
        window.doModal()
        result = window.result
        del window
        home.clearProperty('dexhub.yt.music.open')
        if result != 'video' or not _music_video():
            return


def _music_video():
    """The song as a video, in Kodi's player; Back from it is the music player
    again (True), the queue ended is not (False)."""
    player, monitor = xbmc.Player(), xbmc.Monitor()
    cond = xbmc.getCondVisibility
    if not player.isPlayingVideo():
        return False
    xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
    seen, left, asked = False, 0, time.time()
    while _still_playing(player, monitor):
        if cond('Window.IsActive(fullscreenvideo)'):
            seen, left = True, 0
        elif seen:
            left += 1
            if left >= 2:
                return True
        elif time.time() - asked > 3:
            xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
            asked = time.time()
        if monitor.waitForAbort(0.5):
            return False
    return False


class MusicWindow(xbmcgui.WindowXMLDialog):
    """YouTube's music player (dexhub_youtube_music*.xml): the cover, the controls,
    the waveform, the queue. Back leaves the music playing."""
    def __init__(self, *args, **kwargs):
        super(MusicWindow, self).__init__(*args)
        kwargs.pop('video', None)
        self.ids = list(kwargs.get('ids') or [])
        self.known = kwargs.get('known') or {}
        self.where = kwargs.get('where') or ''
        self._items = []
        self._index = -1
        self._stop = False
        self._inited = False
        self.tone = None
        self.result = ''

    def _close(self):
        self._stop = True
        if self.tone:
            self.tone.stop()
        xbmcgui.Window(10000).clearProperty('dexhub.yt.music.open')
        self.close()

    def onInit(self):
        if self._inited:
            return
        self._inited = True
        xbmcgui.Window(10000).setProperty('dexhub.yt.music.open', '1')
        try:
            from ..art_tone import Tone
            self.tone = Tone(self)
        except Exception:
            self.tone = None
        self.setProperty('np.accent', 'FFFF5500')
        self.setProperty('np.glow', '59FF5500')
        self.setProperty('np.next', _t('التالي', 'Up next'))
        self.setProperty('np.audio_word', _t('صوت', 'Audio'))
        self.setProperty('np.video_word', _t('فيديو', 'Video'))
        self.setProperty('np.video', '1' if _MUSIC.get('video') else '')
        self.setProperty('np.from', self.where)
        self.setProperty('np.keys', _t('رجوع: تبقى الموسيقى شغّالة · «يُشغَّل الآن» في القائمة الجانبية يرجّعك هنا',
                                       'Back: the music keeps playing · Now playing on the side rail brings you here'))
        for video_id in self.ids:
            info = self.known.get(video_id) or {}
            self._items.append(_item(info.get('title') or video_id, info.get('channel') or '',
                                     {'thumb': info.get('thumb') or ''}))
        control = self.getControl(50)
        control.reset()
        control.addItems(self._items)
        self.setProperty('np.count', _t('%d أغنية', '%d songs') % len(self._items))
        thread = threading.Thread(target=self._run, name='DexHub-youtube-music')
        thread.daemon = True
        thread.start()

    def _show(self, pos):
        previous, self._index = self._index, pos
        info = self.known.get(self.ids[pos]) or {}
        self.setProperty('np.title', info.get('title') or '')
        self.setProperty('np.artist', info.get('channel') or '')
        self.setProperty('np.art', info.get('big') or info.get('thumb') or '')
        if self.tone:
            self.tone.follow(info.get('thumb') or '')
        if 0 <= previous < len(self._items):
            self._items[previous].setProperty('now', '')
        self._items[pos].setProperty('now', '1')
        try:
            if self.getFocusId() != 50:
                self.getControl(50).selectItem(pos)
        except RuntimeError:
            pass

    def _run(self):
        player, monitor = xbmc.Player(), xbmc.Monitor()
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        started, quiet = time.time(), 0
        while not self._stop and not monitor.abortRequested():
            try:
                pos = playlist.getposition()
                playing = player.isPlaying()
            except RuntimeError:
                pos, playing = -1, False
            if 0 <= pos < len(self.ids) and pos != self._index:
                self._show(pos)
            tone = self.getProperty('tone')
            if len(tone) == 8:
                self.setProperty('np.accent', 'FF' + tone[2:])
                self.setProperty('np.glow', '80' + tone[2:])
            if playing:
                quiet = 0
                self.setProperty('np.state', '')
            elif time.time() - started > 8:
                quiet += 1
                if quiet == 3:
                    self.setProperty('np.state', _t('جاري التحميل…', 'Loading…'))
                if quiet > 50 and (pos < 0 or pos >= len(self.ids) - 1):
                    self.setProperty('np.state', _t('انتهت القائمة', 'The queue has ended'))
                    xbmcgui.Window(10000).clearProperty(MUSIC_PROP)
            else:
                self.setProperty('np.state', _t('جاري التشغيل…', 'Starting…'))
            monitor.waitForAbort(0.4)

    def onClick(self, cid):
        if cid == 50:
            try:
                self._go(self.getControl(50).getSelectedPosition())
            except RuntimeError:
                pass
            return
        if cid == 306:
            self.result = 'video'
            self._close()
            xbmcgui.Dialog().notification(_t('فيديو', 'Video'), _t('رجوع يرجّعك للصوت', 'Back returns to the sound'),
                                          xbmcgui.NOTIFICATION_INFO, 4000, False)
            return
        if cid == 307:
            # Audio | Video: the cover, or the video playing in its place (kept for next time)
            _MUSIC['video'] = not _MUSIC.get('video')
            self.setProperty('np.video', '1' if _MUSIC['video'] else '')
            return
        if cid in (302, 304):
            self._step(1 if cid == 304 else -1)
            return
        command = {301: 'PlayerControl(Random)', 303: 'PlayerControl(Play)', 305: 'PlayerControl(Repeat)'}.get(cid)
        if cid == 303 and not xbmc.Player().isPlaying() and self._index >= 0:
            xbmc.Player().play(xbmc.PlayList(xbmc.PLAYLIST_VIDEO), windowed=True, startpos=self._index)
            return
        if command:
            xbmc.executebuiltin(command)

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            if _MUSIC.get('video'):
                # the video in the cover's place: Back is the sound only again, before leaving
                _MUSIC['video'] = False
                self.setProperty('np.video', '')
                return
            self._close()
        elif aid in (12, 79, 229):           # the remote's play/pause keys
            xbmc.executebuiltin('PlayerControl(Play)')
        elif aid in (14, 15):                # next / previous keys
            self._step(1 if aid == 14 else -1)

    def _step(self, by):
        """The next or previous song, straight to it: Kodi's own Next skipped two (the
        song cut short counts as ended, and the queue moved on once more). Previous
        past the first seconds starts the song again, as music players do."""
        player = xbmc.Player()
        try:
            if by < 0 and player.isPlaying() and player.getTime() > 5:
                player.seekTime(0)
                return
            pos = xbmc.PlayList(xbmc.PLAYLIST_VIDEO).getposition()
        except RuntimeError:
            return
        self._go(pos + by)

    def _go(self, target):
        """Straight to a song of the queue (Kodi's playselected did nothing here)."""
        if 0 <= target < len(self.ids):
            import json
            xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GoTo',
                                            'params': {'playerid': 1, 'to': target}}))


class ShortsWindow(xbmcgui.WindowXMLDialog):
    """The Shorts player (dexhub_youtube_shorts*.xml): the Short standing in the middle,
    up and down for the one before and after, OK to pause, Back to the page."""
    def __init__(self, *args, **kwargs):
        super(ShortsWindow, self).__init__(*args)
        self.ids = list(kwargs.get('ids') or [])
        self.known = kwargs.get('known') or {}
        self._index = -1
        self._stop = False

    def onInit(self):
        self.setProperty('sh.keys', _t('السابق / التالي', 'Before / after'))
        if self.ids:
            self._show(0)
        thread = threading.Thread(target=self._run, name='DexHub-youtube-shorts')
        thread.daemon = True
        thread.start()

    def _show(self, pos):
        self._index = pos
        info = self.known.get(self.ids[pos]) or {}
        self.setProperty('sh.title', info.get('title') or '')
        self.setProperty('sh.channel', info.get('channel') or '')
        self.setProperty('sh.art', info.get('thumb') or '')
        self.setProperty('sh.count', '%d / %d' % (pos + 1, len(self.ids)))

    def _run(self):
        player, monitor = xbmc.Player(), xbmc.Monitor()
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        started, quiet = time.time(), 0
        while not self._stop and not monitor.abortRequested():
            try:
                pos = playlist.getposition()
                playing = player.isPlaying()
            except RuntimeError:
                pos, playing = -1, False
            if 0 <= pos < len(self.ids) and pos != self._index:
                self._show(pos)
            if playing:
                quiet = 0
                self.setProperty('sh.state', '')
            elif time.time() - started > 6:
                quiet += 1
                if quiet == 3:
                    self.setProperty('sh.state', _t('جاري التحميل…', 'Loading…'))
                if quiet > 50 and (pos < 0 or pos >= len(self.ids) - 1):
                    self._stop = True           # the last Short ended: the page again
                    self.close()
                    return
            monitor.waitForAbort(0.3)

    def _go(self, target):
        if 0 <= target < len(self.ids):
            import json
            self._show(target)
            xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GoTo',
                                            'params': {'playerid': 1, 'to': target}}))

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self._stop = True
            xbmc.Player().stop()
            self.close()
        elif aid == A_UP:
            self._go(max(self._index, 0) - 1)
        elif aid == A_DOWN:
            self._go(max(self._index, 0) + 1)
        elif aid in (7, 12, 79, 229):           # OK and the play/pause keys
            xbmc.executebuiltin('PlayerControl(Play)')
        elif aid in (A_LEFT, A_RIGHT):
            try:
                player = xbmc.Player()
                player.seekTime(max(0.0, player.getTime() + (-5 if aid == A_LEFT else 5)))
            except RuntimeError:
                pass


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
        if play == 'nowplaying':
            _music(app)
            continue
        ids, start, known, resume, music, where = play
        if music == 'shorts':
            try:
                _play(ids, start, known, 0, windowed=True)
            except Exception:
                xbmc.log('[DexHub] youtube: shorts failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
                return
            window = ShortsWindow(ui_size.xml('dexhub_youtube_shorts.xml'), app.addon_path, 'Default', '1080i',
                                  ids=list(ids[start:start + 50]), known=known)
            window.doModal()
            del window
            continue
        if music:
            try:
                _play(ids, start, known, 0, windowed=True)
            except Exception:
                xbmc.log('[DexHub] youtube: music failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
                return
            _music(app, {'ids': list(ids[start:start + 50]), 'known': known, 'where': where})
            continue
        xbmcgui.Window(10000).clearProperty(MUSIC_PROP)
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
