# -*- coding: utf-8 -*-
"""The Live TV guide (v5.10.131): channels and their programmes on a timeline.

Laid out like UHF. An icon rail on the left (back to Dex Hub, search, the
live sources as app icons, the source's films and series, adding a source),
the source's categories in a panel next to it, and on the right the focused
channel's preview and programme over a two hour grid of the category's
channels. The panel slides away when the cursor goes into the grid, and
comes back with Left at the start of the timeline (or Back).

Every category of a source is listed (its playlist groups, Xtream
categories, DexWorld genres, the PVR's groups), with Favorites and Recently
watched first; a category's channels load when the cursor rests on it.

The grid is drawn by Python on the fixed controls of dexhub_live.xml
(guide_layout.py holds their ids and positions): each row shows one channel
and rows are reused as the list scrolls (channel i always sits in row
i % ROWS), each block one programme of the two hours on show. Only what
changed is sent to Kodi, and the programmes of the channels on screen are
asked for in the background, the focused channel first.
"""
import re
import threading
import time
from .. import lazy_tb as traceback

import xbmc
import xbmcgui

from . import guide_layout as GL
from . import ui_size
from .window import (_BaseWindow, A_BACK, A_CONTEXT, A_DOWN, A_IGNORE, A_INFO, A_LEFT, A_MENU,
                     A_PGDN, A_PGUP, A_PLAY, A_RIGHT, A_UP, _now)

SLOT = GL.STEP_MIN * 60         # the timeline moves in half hours
WINDOW = GL.WINDOW_MIN * 60     # and shows two hours
AHEAD = 24 * 3600               # how far ahead the timeline goes
GUIDE_TTL = 1200                # a channel's programmes are asked for again after 20 minutes
CAT_SETTLE = 0.45               # the panel's cursor rests this long on a category before it opens
TICK = 30.0                     # the now line, progress and the info move on
SEARCH_LIMIT = 300
VOD_AHEAD = 18                  # posters from the end that bring the next page (two rows)

_guides = {}            # channel path -> (time asked, [programmes]), shared by the guide windows
_guides_lock = threading.Lock()


def _slot(moment):
    return int(moment // SLOT) * SLOT


def _clock(epoch):
    try:
        return time.strftime('%H:%M', time.localtime(int(epoch)))
    except Exception:
        return ''


def _argb(value, fallback):
    value = str(value or '').strip().lstrip('#')
    if len(value) == 6:
        value = 'FF' + value
    try:
        int(value, 16)
    except ValueError:
        return fallback
    return value.upper() if len(value) == 8 else fallback


def _mix(color, toward, amount):
    """``color`` (AARRGGBB) moved ``amount`` of the way to ``toward`` (RRGGBB ints)."""
    a, r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4, 6))
    tr, tg, tb = toward
    mix = lambda x, y: max(0, min(255, int(round(x + (y - x) * amount))))
    return '%02X%02X%02X%02X' % (a, mix(r, tr), mix(g, tg), mix(b, tb))


def _alpha(color, alpha):
    return '%02X%s' % (alpha, color[2:])


_AR_FOLD = (('أ', 'ا'), ('إ', 'ا'), ('آ', 'ا'), ('ٱ', 'ا'), ('ة', 'ه'), ('ى', 'ي'), ('ـ', ''))
_MARKS = re.compile('[ً-ْٰ]')


def fold(text):
    """A name as searched: lower case, Arabic letters in one form, no marks."""
    text = _MARKS.sub('', str(text or '').lower())
    for old, new in _AR_FOLD:
        text = text.replace(old, new)
    return ' '.join(text.split())


def cached_guide(path):
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    with _guides_lock:
        hit = _guides.get(path)
    if hit and time.time() - hit[0] < GUIDE_TTL:
        return hit[1]
    return None


def _store_guide(path, items):
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return
    with _guides_lock:
        if len(_guides) > 800:
            now = time.time()
            for key in [k for k, v in _guides.items() if now - v[0] > GUIDE_TTL] or list(_guides)[:400]:
                _guides.pop(key, None)
        _guides[path] = (time.time(), items)


def has_guide(tile):
    """Can this channel's programmes be asked for? (a PVR channel, or a source's guide id)"""
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return False
    return bool(tile.get('channelid') or tile.get('epg'))


class GuideWindow(_BaseWindow):
    """dexhub_live.xml: the rail, the categories panel, the preview and the grid."""
    WINDOW = WINDOW     # the timeline's length in seconds (a page drawn its own way may show less)

    def __init__(self, *args, **kwargs):
        super(GuideWindow, self).__init__(*args, **kwargs)
        self.mode = 'live'
        self.L = GL.for_size(ui_size.current())
        # the guide is the page itself: no immersive view over it
        self.immersive_on = False
        self._draw = threading.RLock()
        self._cs = {}               # what each control was last given: (id, what) -> value
        self.sources = []
        self.source = None
        self.cats = []
        self.cat = None
        self.channels = []
        self.more = None
        self._more_busy = False
        self.first = 0              # the channel in the top row
        self.ci = 0                 # the focused channel
        self.anchor = 0             # the time the cursor stands on
        self.prog = None            # the focused programme (None: a row without any)
        self.t0 = _slot(time.time())
        self.panel = True
        self.full = False
        self._gen = 0               # each category shown counts up; late answers of older ones are dropped
        self._asked = set()
        self._rows = {}             # slot -> channel index drawn there
        self._sig = {}              # slot -> what was drawn there (to know when it needs drawing again)
        self._blocks = {}           # slot -> [(programme, title, small line)] of its blocks
        self._lit = None            # (slot, block) drawn as the cursor
        self._cat_keys = []
        self._rail_keys = []
        self._btn_keys = []
        self._tool_keys = []
        self._search = None         # the search category, while it is listed
        self._loaded = {}           # category key -> its channels read so far (the search looks there too)
        self._tick_at = 0.0
        self._grid_on = False       # the grid has the focus (its cursor is lit)
        self._entered = False       # a key just moved the focus into the grid
        self._focus_set = False     # the guide itself moves the focus into the grid
        self._enter_after = None    # the category OK opened: the grid takes the focus once it is in
        # v5.10.133: the source's films or series in the guide ('' while channels show)
        self.vod = ''
        self.vod_tiles = []
        self.vod_more = None
        self._vod_busy = False
        self._cat_cursor = None
        self._colors()

    # ----------------------------------------------------------- lifecycle
    def onInit(self):
        if self._fresh_controls():
            return
        try:
            self._window_id = xbmcgui.getCurrentWindowId()
        except Exception:
            self._window_id = 0
        if self._inited:
            # Kodi gives the focus back to where it was (the grid, mostly): no
            # key brought it there
            self._entered = False
            self._mark_shown_again()
            self._covered = False
            self._last_input = _now()
            self._theme()
            if self._colors():
                self._restyle()
            self._after_return()
            return
        self._inited = True
        L = self.L
        ids = [GL.RAIL, GL.BTNS, GL.CATS, GL.GRID, GL.TOOLS, GL.BAR_FG, GL.NOW_LINE, GL.NOW_DOT]
        for slot in range(L.ROWS):
            rid = GL.row_id(slot)
            ids += [rid + k for k in (0, GL.R_TILE, GL.R_LOGO, GL.R_NUMBER, GL.R_NAME, GL.R_MARK)]
            for block in range(GL.BLOCKS):
                bid = GL.block_id(slot, block)
                ids += [bid + k for k in (0, GL.B_BG, GL.B_BAR, GL.B_SMALL, GL.B_TITLE)]
        self._warm_controls(ids)
        self._theme()
        self._colors()
        self.prop('g.live', self.tr('مباشر'))
        self.prop('g.msg', self.tr('جاري تحميل المصادر…'))
        with self._draw:
            for slot in range(L.ROWS):
                self._vis(GL.row_id(slot), False)
            self._vis(GL.NOW_LINE, False)
            self._vis(GL.NOW_DOT, False)
            self._header()
        self.prop('g.ready', '1')
        self._set_panel(True)
        self._fill_buttons()
        self._fill_tools()
        self.focus(GL.CATS)
        self.app.pages.submit(self._load_sources, priority=0, key=('guide-sources', self.key))

    def _after_return(self):
        """Back from the player or from a page opened over the guide."""
        from . import live_sources as LS
        from . import live_favorites as LF
        from ..ui_preferences import iptv_available
        if not iptv_available():
            self.leave()
            return
        active = self.vod or 'channels'
        if not iptv_available(active):
            self.vod = ''
            if self.source is not None:
                source = self.source
                self.app.pages.submit(lambda: self._open_source(source), priority=0, key=('guide-source', self.key))
                return
        self._favorite_keys = LF.keys(self.app.profile)
        wanted = LS.remembered_tab(self.app.profile, 'live')
        if self.source is not None and wanted and wanted != self.source['key']:
            # the films and series page moved to another source: the guide follows
            match = [s for s in self.sources if s['key'] == wanted]
            if match:
                self.app.pages.submit(lambda: self._open_source(match[0]), priority=0,
                                      key=('guide-source', self.key))
                return
        if self._hero_tile is not None and not self.vod:
            self.schedule_trailer(self._hero_tile)
        self._tick()

    def default_focus(self):
        if self.vod:
            return GL.VOD if self.vod_tiles and not self.panel else GL.CATS
        if self.channels and not self.panel:
            return GL.GRID
        return GL.CATS

    def enter_immersive(self):
        pass

    def tick_extra(self):
        # Kodi can move a list selection without delivering its key to onAction.
        # Watch the actual selected category; debounce requests and keep stale replies out.
        if not self._closing and self.focus_id() == GL.CATS:
            key = self._selected_key(GL.CATS)
            if key != self._cat_cursor:
                self._cat_cursor = key
                self.app.scheduler.call_later(self.key + ':cat', CAT_SETTLE, self._cat_settled)
        if _now() - self._tick_at >= TICK:
            self._tick()

    def on_close(self):
        self.app.scheduler.cancel_prefix(self.key)
        super(GuideWindow, self).on_close()

    # -------------------------------------------------------------- colours
    def _colors(self):
        """Read the theme's colours; True when they differ from the ones drawn."""
        home = xbmcgui.Window(10000)
        get = lambda name, fallback: _argb(home.getProperty('dexhub.tvos.theme.%s' % name), fallback)
        before = getattr(self, 'c_accent', None), getattr(self, 'c_on', None), getattr(self, 'c_card', None)
        self.c_accent = get('accent', 'FF9B6BFF')
        self.c_on = get('on_accent', 'FFFFFFFF')
        self.c_card = get('surface_card', 'FF161B30')
        self.c_text = get('text', 'FFF3F5FF')
        self.c_muted = get('muted', 'FF9AA3C4')
        # the programme on now stands out a little from the ones to come
        # (v5.10.193: lit by the accent), the ones gone by sit back
        accent = tuple(int(self.c_accent[i:i + 2], 16) for i in (2, 4, 6))
        self.c_airing = _mix(_mix(self.c_card, (255, 255, 255), 0.05), accent, 0.12)
        self.c_past = _alpha(self.c_card, 0x8C)
        self.c_on_soft = _alpha(self.c_on, 0xCC)
        return before != (self.c_accent, self.c_on, self.c_card)

    def _restyle(self):
        """Colours changed (a theme picked meanwhile): the drawn blocks take them."""
        with self._draw:
            for key in [k for k in self._cs if k[1] in ('diffuse', 'label')]:
                self._cs.pop(key, None)
            self._sig = {}
            self._lit = None
            self._layout()
            self._set_cursor()

    # ------------------------------------------------------- control cache
    def _ctl(self, cid):
        try:
            return self._control(cid)
        except Exception:
            return None

    def _send(self, cid, what, value, call):
        if self._cs.get((cid, what)) == value:
            return
        ctrl = self._ctl(cid)
        if ctrl is None:
            self.trace('no control %s' % cid)
            return
        try:
            call(ctrl)
        except Exception as exc:
            self.trace('control %s %s failed: %s' % (cid, what, exc))
            return
        self._cs[(cid, what)] = value

    def _vis(self, cid, on):
        on = bool(on)
        self._send(cid, 'vis', on, lambda c: c.setVisible(on))

    def _pos(self, cid, x, y):
        x, y = int(x), int(y)
        self._send(cid, 'pos', (x, y), lambda c: c.setPosition(x, y))

    def _width(self, cid, width):
        width = max(1, int(width))
        self._send(cid, 'width', width, lambda c: c.setWidth(width))

    def _label(self, cid, text):
        text = str(text or '')
        self._send(cid, 'label', text, lambda c: c.setLabel(text))

    def _diffuse(self, cid, color):
        self._send(cid, 'diffuse', color, lambda c: c.setColorDiffuse(color))

    def _image(self, cid, path):
        path = str(path or '')
        self._send(cid, 'image', path, lambda c: c.setImage(path, False))

    # ------------------------------------------------------------- sources
    def _load_sources(self):
        from . import live_sources as LS
        try:
            sources = LS.sources(self.app)
        except Exception:
            self.app.log('live sources not read:\n%s' % LS.clean_trace(traceback.format_exc()), xbmc.LOGWARNING)
            sources = []
        self.sources = [s for s in sources if s.get('kind') != 'setup']
        if not self.sources:
            self.source = None
            self._fill_rail()
            self._no_sources()
            return
        wanted = LS.remembered_tab(self.app.profile, 'live')
        source = ([s for s in self.sources if s['key'] == wanted] or self.sources)[0]
        self._open_source(source)

    def _no_sources(self):
        """No live source yet: the panel lists the ways to add one."""
        self.cats = []
        self._show_channels(None, [], None)
        self.prop('g.src.name', 'IPTV')
        self.prop('g.src.sub', self.tr('أضف مصدر قنوات'))
        entries = [('m3u', 'قائمة M3U'), ('xtream', 'اشتراك Xtream Codes'), ('dexworld', 'اشتراك DexWorld'),
                   ('stremio', 'إضافات Stremio'), ('pvr', 'عبر Kodi PVR (IPTV Simple)')]
        items, keys = [], []
        for key, label in entries:
            items.append(xbmcgui.ListItem(label=self.tr(label)))
            keys.append('add:' + key)
        self._cat_keys = keys
        try:
            self._set_items(GL.CATS, items, keys)
        except Exception:
            pass
        self.prop('g.msg', self.tr('لا توجد مصادر قنوات بعد. اختر طريقة لإضافة مصدر من القائمة.'))

    def _open_source(self, source):
        """Show a source: its categories in the panel, then its remembered category."""
        from . import live_sources as LS
        self.source = source
        self._search = None
        self._loaded = {}
        LS.remember_tab(self.app.profile, 'live', source['key'])
        self._fill_rail()
        self._fill_buttons()
        from ..ui_preferences import iptv_available
        if not iptv_available('channels'):
            from . import vod
            support = vod.available(source)
            if support:
                self._open_vod(support[0])
            else:
                self.cats = []
                self._fill_cats()
                self.prop('g.msg', self.tr('أقسام هذا المصدر متوقفة في الإعدادات'))
            return
        self.prop('g.src.name', source.get('label') or '')
        self.prop('g.src.sub', source.get('hint') or '')
        self.prop('g.msg', self.tr('جاري تحميل الأقسام…'))
        self._gen += 1
        gen = self._gen
        self._show_channels(None, [], None, gen=gen, message=self.tr('جاري تحميل الأقسام…'))
        try:
            cats = self._categories(source)
        except Exception as exc:
            self.app.log('live source %s not read: %s' % (source.get('kind'), LS.failure(exc)), xbmc.LOGWARNING)
            if gen == self._gen:
                self.cats = []
                self._fill_cats()
                self.prop('g.msg', self.tr(LS.safe(exc)))
            return
        if gen != self._gen:
            return
        self.cats = cats
        if not cats:
            self._fill_cats()
            self.prop('g.msg', self.tr('لا توجد قنوات في هذا المصدر'))
            return
        remembered = LS.remembered_tab(self.app.profile, 'live_cat:%s' % source['key'])
        pick = [c for c in cats if c['key'] == remembered]
        if not pick:
            # the first category of the source itself (Favorites and Recently
            # watched open only when chosen)
            pick = [c for c in cats if c['key'] not in ('fav', 'recent')] or cats
        self._fill_cats(select=pick[0])
        self._open_cat(pick[0], gen=gen)

    def _categories(self, source):
        from . import live, live_sources as LS, live_favorites as LF
        key = source['key']
        card = self.app.media_path('channel_card.jpg')
        out = []
        try:
            favorites = LF.tiles(self.app.profile, key, card)
        except Exception:
            favorites = []
        if favorites:
            out.append({'key': 'fav', 'title': self.tr('المفضلة'), 'tiles': favorites, 'count': len(favorites)})
        try:
            if source['kind'] == 'pvr':
                recent = live.recent_tiles(self.app.profile, card)
            else:
                recent = LS.recent_tiles(self.app.profile, key, card)
        except Exception:
            recent = []
        if recent:
            out.append({'key': 'recent', 'title': self.tr('شاهدتها مؤخراً'), 'tiles': recent,
                        'count': len(recent)})
        if source['kind'] == 'pvr':
            groups = live.groups()
            # the playlist's own groups first; every channel together last
            for group in [g for g in groups if not g['all']] + [g for g in groups if g['all']]:
                label = self.tr('كل القنوات') if group['all'] else (group['label'] or self.tr('قنوات'))
                out.append({'key': 'g:%d' % group['id'], 'title': label,
                            'load': self._pvr_loader(group['id'], label, card)})
            return out
        for spec in LS.rows(source, self.app, changed=self._source_changed):
            count = ''
            match = re.search(r'(\d+)', spec.get('subtitle') or '')
            if match:
                count = match.group(1)
            out.append({'key': spec['key'], 'title': spec['title'], 'params': spec.get('params') or {},
                        'load': spec['loader'], 'count': count})
        return out

    @staticmethod
    def _pvr_loader(group_id, label, card):
        def load(patience=0.0):
            from . import live
            tiles, more, _total = live.group_tiles(group_id, label, card, count=live.PAGE_SIZE)
            return tiles, more
        return load

    def _source_changed(self, key):
        """A source read again in the background (its playlist or guide)."""
        if self._closing or self.source is None or (key and key != self.source['key']):
            return
        self.app.scheduler.call_later(self.key + ':changed', 1.0, self.live_refresh)

    # ---------------------------------------------------------- categories
    def _fill_cats(self, select=None):
        from . import live, emoji_labels
        items, keys = [], []
        active = (self.cat or {}).get('key') if select is None else select.get('key')
        pos = 0
        for index, cat in enumerate(self.cats):
            # Sanitize at the presentation boundary too: cached provider
            # labels must not hide text or replace the skin's contrast.
            label, icons = emoji_labels.presentation(live.clean(cat.get('title')), self.tr('قنوات'))
            li = xbmcgui.ListItem(label=label, label2=str(cat.get('count') or ''))
            for slot, icon in enumerate(icons, 1):
                li.setProperty('emoji%d' % slot, icon)
            if cat['key'] == active:
                li.setProperty('active', '1')
                pos = index
            items.append(li)
            keys.append(cat['key'])
        self._cat_keys = keys
        try:
            ctrl = self._set_items(GL.CATS, items, keys)
            if pos:
                ctrl.selectItem(pos)
        except Exception:
            pass
        self._focus_cats_if_lost()

    def _focus_cats_if_lost(self):
        """The categories take the focus when nothing has it yet (the guide just opened)."""
        focus = self.focus_id()
        if (not self._user_moved and focus != GL.GRID) or focus in (0, GL.GRID) and not self.channels:
            self.focus(GL.CATS)

    def _mark_cats(self):
        active = (self.cat or {}).get('key')
        with self._lock:
            items = list(self._items.get(GL.CATS) or [])
            keys = list(self._cat_keys)
        for li, key in zip(items, keys):
            try:
                li.setProperty('active', '1' if key == active else '')
                cat = self._cat(key)
                if cat is not None:
                    li.setLabel2(str(cat.get('count') or ''))
            except Exception:
                pass

    def _cat(self, key):
        for cat in self.cats:
            if cat['key'] == key:
                return cat
        return None

    def _cat_settled(self):
        """The panel's cursor rested on a category: it opens (the panel stays)."""
        if self._closing or self.focus_id() != GL.CATS:
            return
        key = self._selected_key(GL.CATS)
        cat = self._cat(key)
        if cat is not None and cat is not self.cat:
            gen = self._gen = self._gen + 1
            self.app.pages.submit(lambda: self._open_cat(cat, gen=gen), priority=0,
                                  key=('guide-cat', self.key, gen))

    def _cat_click(self):
        key = self._selected_key(GL.CATS)
        if key.startswith('add:'):
            self.live_action(key[4:])
            return
        cat = self._cat(key)
        if cat is None:
            return
        self.app.scheduler.cancel(self.key + ':cat')
        if cat is self.cat:
            if self.vod:
                if self.vod_tiles:
                    self.focus(GL.VOD)
            elif self.channels:
                self._enter_grid()
            return
        gen = self._gen = self._gen + 1
        self._enter_after = gen
        self.app.pages.submit(lambda: self._open_cat(cat, gen=gen), priority=0, key=('guide-cat', self.key, gen))

    def _open_cat(self, cat, gen=None):
        """Load a category's channels (a worker) and show them."""
        from . import live_sources as LS
        if self.vod:
            return self._open_vod_cat(cat, gen=gen)
        if gen is None:
            gen = self._gen = self._gen + 1
        if gen != self._gen or self._closing:
            return
        self.cat = cat
        self._mark_cats()
        self._fill_buttons()
        if self.source is not None and cat['key'] not in ('search',):
            LS.remember_tab(self.app.profile, 'live_cat:%s' % self.source['key'], cat['key'])
        if cat.get('tiles') is not None:
            tiles, more = list(cat['tiles']), cat.get('more')
        else:
            self._show_channels(cat, [], None, gen=gen, message=self.tr('جاري تحميل القنوات…'))
            try:
                tiles, more = self._load_cat(cat)
            except Exception as exc:
                self.app.log('live category not read: %s' % LS.failure(exc), xbmc.LOGWARNING)
                if gen == self._gen:
                    self._show_channels(cat, [], None, gen=gen, message=self.tr(LS.safe(exc)))
                return
        if gen != self._gen or self._closing:
            return
        if cat['key'] != 'search':
            self._loaded[cat['key']] = list(tiles)
        if not cat.get('count') or cat.get('tiles') is None:
            cat['count'] = '%d%s' % (len(tiles), '+' if more else '')
        self._mark_cats()
        self._show_channels(cat, tiles, more, gen=gen,
                            message='' if tiles else self.tr('لا توجد قنوات في هذا القسم'))
        if getattr(self, '_enter_after', None) == gen:
            self._enter_after = None
            if tiles:
                self._enter_grid()

    def _load_cat(self, cat):
        from . import live_sources as LS
        params = cat.get('params') or {}
        if params.get('catalog'):
            from . import catalog_controls as CC
            (tiles, more), _summary = CC.load(params, self.app, lambda p: LS.page_tiles(p, self.app))
            return tiles, more
        return cat['load'](0.0)

    def _load_more(self):
        """The next page of the category's channels, when the cursor nears the end."""
        if self._more_busy or not self.more or self.cat is None:
            return
        self._more_busy = True
        gen, params, cat = self._gen, self.more, self.cat

        def job():
            from . import live, live_sources as LS
            try:
                if 'live_group' in params:
                    tiles, more = live.page_tiles(params, self.app.media_path('channel_card.jpg'))
                else:
                    tiles, more = LS.page_tiles(params, self.app)
            except Exception as exc:
                self.app.log('more channels not read: %s' % LS.failure(exc), xbmc.LOGWARNING)
                tiles, more = [], None
            finally:
                self._more_busy = False
            if gen != self._gen or self._closing:
                return
            with self._draw:
                known = set(t.get('path') for t in self.channels)
                self.channels.extend(t for t in tiles if t.get('path') not in known)
                self._loaded[cat['key']] = list(self.channels)
                self.more = more
                cat['count'] = '%d%s' % (len(self.channels), '+' if more else '')
                self._layout()
            self._mark_cats()
            self._want_guides()
        self.app.pages.submit(job, priority=1, key=('guide-more', self.key, gen))

    # ---------------------------------------------------------------- rail
    def _fill_rail(self):
        from . import vod
        items, keys = [], []

        def add(key, label, icon, app=False, active=False):
            li = xbmcgui.ListItem(label=label)
            li.setArt({'icon': icon})
            if app:
                li.setProperty('app', '1')
            if active:
                li.setProperty('active', '1')
            items.append(li)
            keys.append(key)
        add('home', self.tr('رجوع'), self.app.media_path('nav_home.png'))
        from ..ui_preferences import iptv_available
        if iptv_available('channels'):
            add('search', self.tr('بحث في القنوات'), self.app.media_path('nav_search.png'))
        current = (self.source or {}).get('key')
        for source in self.sources:
            add('src:' + source['key'], source.get('label') or '', source.get('icon') or
                self.app.media_path('nav_live.png'), app=bool(source.get('icon')),
                active=source['key'] == current and not self.vod)
        if self.source is not None:
            try:
                support = vod.available(self.source)
            except Exception:
                support = []
            if 'movie' in support:
                add('vod:movie', self.tr('الأفلام'), self.app.media_path('nav_movie.png'),
                    active=self.vod == 'movie')
            if 'series' in support:
                add('vod:series', self.tr('المسلسلات'), self.app.media_path('nav_series.png'),
                    active=self.vod == 'series')
        add('add', self.tr('أضف مصدر قنوات'), self.app.media_path('lay_add.png'))
        with self._lock:
            try:
                pos = int(self._control(GL.RAIL).getSelectedPosition())
            except Exception:
                pos = 0
        self._rail_keys = keys
        try:
            ctrl = self._set_items(GL.RAIL, items, keys)
            if 0 < pos < len(items):
                ctrl.selectItem(pos)
        except Exception:
            pass

    def _rail_click(self):
        key = self._selected_key(GL.RAIL)
        if key == 'home':
            self.leave()
        elif key == 'search':
            if self.vod and self.source is not None:
                source = self.source
                self._leave_vod()
                self.app.pages.submit(lambda: self._open_source(source), priority=0,
                                      key=('guide-source', self.key))
            self._ask_search()
        elif key == 'add':
            self.live_setup_menu()
        elif key.startswith('src:'):
            source = [s for s in self.sources if s['key'] == key[4:]]
            if not source:
                return
            self.focus(GL.CATS)
            if self.vod:
                # back to the channels (of this source or another one)
                self._leave_vod()
                self.app.pages.submit(lambda: self._open_source(source[0]), priority=0,
                                      key=('guide-source', self.key))
                return
            if self.source is not None and source[0]['key'] == self.source['key']:
                return
            self.stop_trailer(wait=False)
            self.app.pages.submit(lambda: self._open_source(source[0]), priority=0,
                                  key=('guide-source', self.key))
        elif key.startswith('vod:'):
            self._open_vod(key[4:])

    def _open_vod(self, section):
        """v5.10.133: the source's films or series in the guide itself, as
        its channels are: every category in the panel (only their names are
        read), the posters of the one opened on the right, a page at a time
        (the next page comes in as the cursor nears the end). Before, the
        rail opened the old Live TV page, which read the first category
        whole and listed the others as cards."""
        from . import live_sources as LS
        if self.source is None:
            return
        from ..ui_preferences import iptv_available
        if not iptv_available(section):
            return
        self.stop_trailer(wait=False)
        LS.remember_tab(self.app.profile, 'live', self.source['key'])
        self.vod = section
        self.prop('g.vod', section)
        self.vod_tiles, self.vod_more = [], None
        self.cat = None
        self._set_vod_items([])
        self.prop('g.vmsg', self.tr('جاري تحميل الأقسام…'))
        self._fill_rail()
        self._fill_buttons()
        self._set_panel(True)
        self.focus(GL.CATS)
        gen = self._gen = self._gen + 1
        self.app.pages.submit(lambda: self._load_vod_cats(section, gen), priority=0,
                              key=('guide-vod', self.key, gen))

    def _leave_vod(self):
        self.vod = ''
        self.prop('g.vod', '')
        self.prop('g.vmsg', '')
        self.vod_tiles, self.vod_more = [], None
        self._set_vod_items([])
        self._fill_rail()
        self._fill_buttons()

    def _load_vod_cats(self, section, gen):
        from . import live_sources as LS
        from . import vod
        source = self.source
        try:
            cats = vod.categories(source, self.app, section)
        except Exception as exc:
            self.app.log('films or series not read: %s' % LS.failure(exc), xbmc.LOGWARNING)
            if gen == self._gen and self.vod == section:
                self.cats = []
                self._fill_cats()
                self.prop('g.vmsg', self.tr(LS.safe(exc)))
            return
        if gen != self._gen or self._closing or self.vod != section:
            return
        self.cats = cats
        if not cats:
            self._fill_cats()
            self.prop('g.vmsg', self.tr('لا توجد أفلام في هذا المصدر' if section == 'movie'
                                        else 'لا توجد مسلسلات في هذا المصدر'))
            return
        remembered = LS.remembered_tab(self.app.profile, 'vod_cat:%s:%s' % (source['key'], section))
        pick = [c for c in cats if c['key'] == remembered] or cats
        self._fill_cats(select=pick[0])
        self._open_vod_cat(pick[0], gen=gen)

    def _open_vod_cat(self, cat, gen=None):
        """A category's first page of posters (a worker)."""
        from . import live_sources as LS
        from . import vod
        if gen is None:
            gen = self._gen = self._gen + 1
        if gen != self._gen or self._closing or not self.vod:
            return
        section = self.vod
        self.cat = cat
        self._mark_cats()
        self._fill_buttons()
        if self.source is not None:
            LS.remember_tab(self.app.profile, 'vod_cat:%s:%s' % (self.source['key'], section), cat['key'])
        self.vod_tiles, self.vod_more = [], None
        self._set_vod_items([])
        self.prop('g.vmsg', self.tr('جاري التحميل…'))
        try:
            from . import catalog_controls as CC
            route = dict(cat.get('params') or {})
            if getattr(self, '_vod_refresh', False):
                self._vod_refresh = False
                route['_refresh'] = True
            (tiles, more), _summary = CC.load(route, self.app, lambda p: vod.page_tiles(p, self.app))
            if more:
                more.pop('_refresh', None)
        except Exception as exc:
            self.app.log('films or series category not read: %s' % LS.failure(exc), xbmc.LOGWARNING)
            if gen == self._gen and self.vod == section:
                self.prop('g.vmsg', self.tr(LS.safe(exc)))
            return
        if gen != self._gen or self._closing or self.vod != section:
            return
        tiles = [t for t in tiles or [] if t.get('path')]
        self.vod_tiles, self.vod_more = list(tiles), more
        cat['count'] = '%d%s' % (len(tiles), '+' if more else '')
        self._mark_cats()
        self._set_vod_items(tiles)
        self.prop('g.vmsg', '' if tiles else self.tr('لا يوجد شيء في هذا القسم'))
        if getattr(self, '_enter_after', None) == gen:
            self._enter_after = None
            if tiles:
                self.focus(GL.VOD)

    def _vod_listitem(self, tile):
        li = self.make_listitem(tile)
        # a title with no backdrop shows its poster whole on top (not cropped)
        li.setArt({'fanart': tile.get('fanart') or ''})
        genres = tile.get('genres') or []
        facts = [str(tile.get('year') or ''), ' / '.join(genres[:2]) if isinstance(genres, list) else '']
        try:
            rating = float(tile.get('rating') or 0)
        except (TypeError, ValueError):
            rating = 0.0
        if rating > 0:
            facts.append('★ %.1f' % rating)
        li.setProperty('v.facts', '   •   '.join(f for f in facts if f))
        if tile.get('plot'):
            li.setProperty('v.plot', str(tile['plot']))
        return li

    def _set_vod_items(self, tiles, append=False):
        """The posters panel: its items replaced, or ``tiles`` added at its end."""
        items = [self._vod_listitem(t) for t in tiles or []]
        with self._lock:
            try:
                ctrl = self._control(GL.VOD)
            except Exception:
                return
            if append:
                if items:
                    ctrl.addItems(items)
                    self._items.setdefault(GL.VOD, []).extend(items)
                return
            ctrl.reset()
            if items:
                ctrl.addItems(items)
            self._items[GL.VOD] = list(items)

    def _vod_near_end(self):
        """The next page of posters once the cursor is two rows from the end."""
        if not self.vod or self._vod_busy or not self.vod_more:
            return
        try:
            pos = int(self._control(GL.VOD).getSelectedPosition())
        except Exception:
            return
        if pos < len(self.vod_tiles) - VOD_AHEAD:
            return
        self._vod_busy = True
        gen, params, cat = self._gen, dict(self.vod_more), self.cat

        def job():
            from . import live_sources as LS
            from . import vod
            try:
                from . import catalog_controls as CC
                (tiles, more), _summary = CC.load(params, self.app, lambda p: vod.page_tiles(p, self.app))
            except Exception as exc:
                self.app.log('next films or series not read: %s' % LS.failure(exc), xbmc.LOGWARNING)
                tiles, more = [], None
            finally:
                self._vod_busy = False
            if gen != self._gen or self._closing or not self.vod:
                return
            known = set(t.get('path') for t in self.vod_tiles)
            fresh = [t for t in tiles or [] if t.get('path') and t.get('path') not in known]
            self.vod_tiles.extend(fresh)
            self.vod_more = more
            if cat is not None:
                cat['count'] = '%d%s' % (len(self.vod_tiles), '+' if more else '')
                self._mark_cats()
            if fresh:
                self._set_vod_items(fresh, append=True)
            elif more:
                # a page with nothing new (a category ended): the next one at once
                self._vod_near_end()
        self.app.pages.submit(job, priority=1, key=('guide-vod-more', self.key, gen))

    def _vod_click(self):
        try:
            pos = int(self._control(GL.VOD).getSelectedPosition())
        except Exception:
            return
        if 0 <= pos < len(self.vod_tiles):
            self.stop_trailer(wait=False)
            self.open_path(self.vod_tiles[pos])

    # ------------------------------------------------------------- buttons
    def _fill_buttons(self):
        entries = [('refresh', 'تحديث المحتوى' if self.vod else 'تحديث القنوات', 'guide_refresh.png')]
        if self.cat is not None:
            from . import catalog_controls as CC
            if CC.describe(self.cat.get('params') or {}, self.app) is not None:
                entries += [('sort', 'ترتيب', 'grid_sort.png'),
                            ('filter', 'الفلاتر', 'grid_filter.png')]
        if self.source is not None:
            entries.append(('manage', 'خيارات المصدر', 'guide_manage.png'))
        items, keys = [], []
        for key, label, icon in entries:
            li = xbmcgui.ListItem(label=self.tr(label))
            li.setArt({'icon': self.app.media_path(icon)})
            items.append(li)
            keys.append(key)
        if keys == self._btn_keys:
            return
        self._btn_keys = keys
        try:
            self._set_items(GL.BTNS, items, keys)
        except Exception:
            pass

    def _button_click(self):
        key = self._selected_key(GL.BTNS)
        if key == 'refresh':
            self.live_refresh(force=True)
        elif key == 'manage' and self.source is not None:
            self.manage_live_source(self.source['key'])
        elif key in ('sort', 'filter') and self.cat is not None:
            from . import catalog_controls as CC
            cat = self.cat
            result = CC.choose(cat.get('params') or {}, self.app, cat.get('title') or '',
                               self.vod_tiles if self.vod else self.channels,
                               which='sort' if key == 'sort' else None)
            if result is not None:
                gen = self._gen = self._gen + 1
                self.app.pages.submit(lambda: self._open_cat(cat, gen=gen), priority=0,
                                      key=('guide-filter', self.key, gen))

    def _fill_tools(self):
        entries = [('now', 'الآن', 'guide_now.png'),
                   ('full', 'عرض المعاينة' if self.full else 'الدليل الكامل',
                    'guide_collapse.png' if self.full else 'guide_expand.png')]
        items, keys = [], []
        for key, label, icon in entries:
            li = xbmcgui.ListItem(label=self.tr(label))
            li.setArt({'icon': self.app.media_path(icon)})
            items.append(li)
            keys.append(key)
        self._tool_keys = keys
        try:
            with self._lock:
                try:
                    pos = int(self._control(GL.TOOLS).getSelectedPosition())
                except Exception:
                    pos = 0
            ctrl = self._set_items(GL.TOOLS, items, keys)
            if pos:
                ctrl.selectItem(pos)
        except Exception:
            pass

    def _tool_click(self):
        key = self._selected_key(GL.TOOLS)
        if key == 'now':
            self._jump_now()
            self._enter_grid()
        elif key == 'full':
            self._toggle_full()
            self.focus(GL.TOOLS)

    # ------------------------------------------------------------ channels
    def _show_channels(self, cat, tiles, more, gen=None, message=''):
        """A category's channels in the grid, the cursor on the first one, at now."""
        if gen is not None and gen != self._gen:
            return
        with self._draw:
            self.channels = list(tiles or [])
            self.more = more
            self.first = self.ci = 0
            self.t0 = _slot(time.time())
            self.anchor = time.time()
            self.prog = None
            self._lit = None
            self._rows = {}
            self._sig = {}
            self._blocks = {}
            self._asked = set()
            self._header()
            self._layout()
            self.prop('g.has', '1' if self.channels else '')
            self.prop('g.msg', message if not self.channels else '')
            from . import live, emoji_labels
            self.prop('g.cat', emoji_labels.presentation(live.clean((cat or {}).get('title') or ''), '')[0])
            if self.channels:
                self.prog = self._pick(self.channels[0], self.anchor)
                self._set_cursor()
            else:
                self._clear_info()
        if self.channels:
            self._want_guides()
            if self.focus_id() == GL.GRID:
                self._channel_changed()
        elif self.focus_id() == GL.GRID:
            self.focus(GL.CATS)

    def _tile(self, index=None):
        index = self.ci if index is None else index
        if 0 <= index < len(self.channels):
            return self.channels[index]
        return None

    def _visible(self):
        return self.L.visible(self.full)

    def _shown(self):
        return min(len(self.channels) - self.first, self._visible() + 1, self.L.ROWS)

    def _scroll_to(self, index):
        """Keep the cursor's row in sight; True when the rows moved."""
        visible = self._visible()
        first = self.first
        if index < first:
            first = index
        elif index >= first + visible:
            first = index - visible + 1
        first = max(0, min(first, max(0, len(self.channels) - visible)))
        if first == self.first:
            return False
        self.first = first
        return True

    # ------------------------------------------------------------- drawing
    def _progs(self, tile):
        """The programmes known for a channel, sorted, from now on."""
        if tile is None:
            return []
        items = cached_guide(tile.get('path'))
        if items is None:
            items = [p for p in (tile.get('now'), tile.get('next')) if p and p.get('start') and p.get('end')]
        return items

    def _state(self, tile):
        """'ok' (programmes known), 'wait' (asked for) or 'none' (this channel has no guide)."""
        if not has_guide(tile):
            return 'none'
        items = cached_guide(tile.get('path'))
        if items is not None:
            return 'ok' if items else 'none'
        return 'wait'

    def _layout(self):
        """Every row in its place, drawn for the channel it shows."""
        L = self.L
        shown = max(0, self._shown())
        wanted = {}
        for index in range(self.first, self.first + shown):
            wanted[index % L.ROWS] = index
        for slot in range(L.ROWS):
            rid = GL.row_id(slot)
            index = wanted.get(slot)
            if index is None:
                self._vis(rid, False)
                self._rows.pop(slot, None)
                continue
            self._pos(rid, 0, (index - self.first) * L.ROW_H)
            self._draw_row(slot, index)
            self._vis(rid, True)
        self._now_line()

    def _row_sig(self, index):
        tile = self.channels[index]
        items = cached_guide(tile.get('path'))
        known = id(items) if items is not None else (id(tile.get('now')), id(tile.get('next')))
        # the minute too: the progress of the programme on now, and a
        # programme that ended, are drawn again (only what changed is sent)
        return (tile.get('path'), self.t0, known, self._state(tile), int(time.time() // 60))

    def _draw_row(self, slot, index, force=False):
        sig = self._row_sig(index)
        if not force and self._rows.get(slot) == index and self._sig.get(slot) == sig:
            return
        if self._lit and self._lit[0] == slot:
            self._lit = None
        tile = self.channels[index]
        rid = GL.row_id(slot)
        logo = self._logo(tile)
        self._image(rid + GL.R_LOGO, logo)
        self._label(rid + GL.R_NAME, '' if logo else (tile.get('title') or ''))
        self._label(rid + GL.R_NUMBER, tile.get('number') or '')
        self._vis(rid + GL.R_MARK, False)
        t0, t1 = self.t0, self.t0 + self.WINDOW
        now = time.time()
        blocks = []
        for prog in self._progs(tile):
            if prog['end'] <= t0 or prog['end'] <= now:
                continue
            if prog['start'] >= t1:
                break
            blocks.append(prog)
            if len(blocks) == GL.BLOCKS:
                break
        drawn = []
        if not blocks:
            state = self._state(tile)
            text = {'none': self.tr('لا يوجد دليل برامج'), 'wait': self.tr('جاري تحميل الدليل…')}.get(state) or ''
            empty = {'title': tile.get('title') or '', 'start': t0, 'end': t1, '_empty': True, 'plot': ''}
            drawn.append((empty, empty['title'], text))
            self._draw_block(GL.block_id(slot, 0), empty, empty['title'], text, airing=False)
            first_free = 1
        else:
            for number, prog in enumerate(blocks):
                small = '%s - %s' % (_clock(prog['start']), _clock(prog['end']))
                airing = prog['start'] <= now < prog['end']
                drawn.append((prog, prog.get('title') or '', small))
                self._draw_block(GL.block_id(slot, number), prog, prog.get('title') or '', small, airing)
            first_free = len(blocks)
        for number in range(first_free, GL.BLOCKS):
            self._vis(GL.block_id(slot, number), False)
        self._rows[slot] = index
        self._sig[slot] = sig
        self._blocks[slot] = drawn

    def _logo(self, tile):
        """The channel's logo once it answered as an image ('' meanwhile: its name shows)."""
        logo = tile.get('logo') or ''
        if not logo:
            return ''
        state = self.app.logos.state(logo)
        if state is None:
            path = tile.get('path')

            def job():
                ok = self.app.logos.check(logo)
                if self._closing:
                    return
                with self._draw:
                    for slot, index in list(self._rows.items()):
                        if 0 <= index < len(self.channels) and self.channels[index].get('path') == path:
                            self._draw_row(slot, index, force=True)
                            if index == self.ci:
                                self._set_cursor()
                if ok and index_of(path) == self.ci:
                    self.prop('g.ch.logo', logo)
            index_of = lambda p: next((i for i, t in enumerate(self.channels) if t.get('path') == p), -1)
            self.app.epg.submit(job, priority=2, key=('logo', logo))
            return ''
        return logo if state else ''

    def _block_box(self, prog):
        """(x, width) of a programme's block in the row, its gap taken off."""
        L = self.L
        ppm = L.px_per_min()
        start = max(prog['start'], self.t0)
        end = min(prog['end'], self.t0 + self.WINDOW)
        x = L.PROG_X + (start - self.t0) / 60.0 * ppm
        width = (end - start) / 60.0 * ppm - L.GAP
        return int(round(x)), int(round(width))

    def _draw_block(self, bid, prog, title, small, airing, lit=False):
        x, width = self._block_box(prog)
        if width < 6:
            self._vis(bid, False)
            return
        pad = 16 if width > 90 else 8
        self._pos(bid, x, 0)
        self._width(bid + GL.B_BG, width)
        past = not lit and not airing and not prog.get('_empty') and prog['end'] <= time.time()
        self._diffuse(bid + GL.B_BG, self.c_accent if lit else (self.c_airing if airing else
                                                                (self.c_past if past else self.c_card)))
        inner = max(1, width - 2 * pad)
        on = self.c_on
        self._width(bid + GL.B_TITLE, inner)
        self._width(bid + GL.B_SMALL, inner)
        self._pos(bid + GL.B_TITLE, pad, self._title_y)
        self._pos(bid + GL.B_SMALL, pad, self._small_y)
        if lit and title:
            title = '[COLOR %s]%s[/COLOR]' % (on, title)
        elif past and title:
            title = '[COLOR %s]%s[/COLOR]' % (self.c_muted, title)
        self._label(bid + GL.B_TITLE, title)
        self._label(bid + GL.B_SMALL, ('[COLOR %s]%s[/COLOR]' % (self.c_on_soft, small)) if (lit and small) else small)
        self._vis(bid + GL.B_TITLE, width >= 44)
        self._vis(bid + GL.B_SMALL, width >= 120)
        if airing and not prog.get('_empty') and width > 40:
            done = (time.time() - prog['start']) / float(max(1, prog['end'] - prog['start']))
            self._width(bid + GL.B_BAR, max(2, (width - 24) * max(0.0, min(1.0, done))))
            self._pos(bid + GL.B_BAR, 12, self._bar_y)
            self._diffuse(bid + GL.B_BAR, self.c_on if lit else self.c_accent)
            self._vis(bid + GL.B_BAR, True)
        else:
            self._vis(bid + GL.B_BAR, False)
        self._vis(bid, True)

    @property
    def _title_y(self):
        return 8

    @property
    def _small_y(self):
        return 46 if self.L.TILE_H >= 80 else 40

    @property
    def _bar_y(self):
        return self.L.TILE_H - 10

    def _set_cursor(self):
        """The cursor's block and row lit (while the grid has the focus), the info shown."""
        in_grid = self.focus_id() in (GL.GRID,) or getattr(self, '_grid_on', False)
        slot = self.ci % self.L.ROWS
        now = time.time()
        if self.prog is not None and not self.prog.get('_empty') and self.prog['end'] <= now:
            # the programme under the cursor ended: the cursor moves on to the next one
            self.anchor = max(self.anchor, now)
            self.prog = self._pick(self._tile(), self.anchor)
        target = None
        if self.channels and self._rows.get(slot) == self.ci:
            for number, (prog, _title, _small) in enumerate(self._blocks.get(slot) or []):
                if prog is self.prog or (self.prog is not None and not prog.get('_empty')
                                         and prog.get('start') == self.prog.get('start')
                                         and prog.get('title') == self.prog.get('title')):
                    target = (slot, number)
                    break
            if target is None and (self._blocks.get(slot) or [None])[0] is not None:
                first = self._blocks[slot][0]
                if first[0].get('_empty'):
                    target = (slot, 0)
        lit = target if in_grid else None
        if self._debug:
            self.trace('cursor ci=%s slot=%s target=%s lit=%s was=%s grid=%s' % (
                self.ci, slot, target, lit, self._lit, in_grid))
        if self._lit != lit:
            if self._lit is not None:
                self._relight(self._lit, False)
                self._vis(GL.row_id(self._lit[0]) + GL.R_MARK, False)
            if lit is not None:
                self._relight(lit, True)
                self._vis(GL.row_id(lit[0]) + GL.R_MARK, True)
            self._lit = lit
        self._show_info()

    def _relight(self, where, lit):
        slot, number = where
        blocks = self._blocks.get(slot) or []
        if number >= len(blocks):
            return
        prog, title, small = blocks[number]
        now = time.time()
        airing = (not prog.get('_empty')) and prog['start'] <= now < prog['end']
        self._draw_block(GL.block_id(slot, number), prog, title, small, airing, lit=lit)

    def _now_line(self):
        """Now: a line behind the blocks (between the rows) and a dot on the timeline."""
        L = self.L
        now = time.time()
        if self.channels and self.t0 <= now < self.t0 + self.WINDOW:
            x = int(round(L.PROG_X + (now - self.t0) / 60.0 * L.px_per_min()))
            self._pos(GL.NOW_LINE, x - 1, -10)
            # v5.10.193: a chip with the clock on the timeline's line
            self._pos(GL.NOW_DOT, x - 38, L.HEADER_H - 12)
            self.prop('g.now', _clock(now))
            self._vis(GL.NOW_LINE, True)
            self._vis(GL.NOW_DOT, True)
        else:
            self._vis(GL.NOW_LINE, False)
            self._vis(GL.NOW_DOT, False)

    def _header(self):
        now = time.time()
        today = time.localtime(now)[:3]
        day = time.localtime(self.t0)[:3]
        if day == today:
            label = self.tr('اليوم')
        elif day == time.localtime(now + 86400)[:3]:
            label = self.tr('غداً')
        else:
            label = time.strftime('%d/%m', time.localtime(self.t0))
        self.prop('g.day', label)
        for index in range(self.WINDOW // SLOT):
            self.prop('g.t%d' % index, _clock(self.t0 + index * SLOT))

    # ---------------------------------------------------------------- info
    def _clear_info(self):
        for key in ('g.ch.line', 'g.ch.logo', 'g.ch.name', 'g.p.title', 'g.p.time', 'g.p.plot',
                    'g.p.next', 'g.p.on'):
            self.prop(key, '')

    def _show_info(self):
        tile = self._tile()
        if tile is None:
            self._clear_info()
            return
        prog = self.prog
        now = time.time()
        parts = []
        if tile.get('number'):
            parts.append(tile['number'])
        parts.append(tile.get('title') or '')
        if self.cat is not None and self.cat.get('key') not in ('fav', 'recent', 'search'):
            parts.append(self.cat.get('title') or '')
        elif tile.get('group'):
            parts.append(tile['group'])
        self.prop('g.ch.line', '   •   '.join([p for p in parts if p]))
        self.prop('g.ch.logo', self._logo(tile))
        self.prop('g.ch.name', tile.get('title') or '')
        if prog is None or prog.get('_empty'):
            self.prop('g.p.title', tile.get('title') or '')
            state = self._state(tile)
            self.prop('g.p.time', self.tr('بث مباشر') if state != 'wait' else self.tr('جاري تحميل الدليل…'))
            self.prop('g.p.plot', tile.get('desc') or '')
            self.prop('g.p.next', '')
            self.prop('g.p.on', '')
            return
        airing = prog['start'] <= now < prog['end']
        # v5.10.184: kept left to right in an Arabic line (it read 17:15 - 15:45)
        span = u'\u202a%s - %s\u202c' % (_clock(prog['start']), _clock(prog['end']))
        if airing:
            left = max(0, int(prog['end'] - now) // 60)
            line = '%s   •   %s' % (span, self.tr('متبقي %d دقيقة') % left) if left else span
        else:
            day = time.localtime(prog['start'])[:3]
            if day == time.localtime(now)[:3]:
                line = span
            elif day == time.localtime(now + 86400)[:3]:
                line = '%s  %s' % (self.tr('غداً'), span)
            else:
                line = '%s  %s' % (time.strftime('%d/%m', time.localtime(prog['start'])), span)
        extra = [x for x in (prog.get('episode'), prog.get('genre')) if x]
        if extra:
            line = '%s   •   %s' % (line, '  ·  '.join(extra))
        self.prop('g.p.title', prog.get('title') or '')
        self.prop('g.p.time', line)
        self.prop('g.p.plot', prog.get('plot') or tile.get('desc') or '')
        following = ''
        if airing:
            for item in self._progs(tile):
                if item['start'] >= prog['end'] - 1:
                    following = self.tr('التالي: %s') % ('%s  %s' % (_clock(item['start']), item.get('title') or ''))
                    break
        self.prop('g.p.next', following)
        self.prop('g.p.on', '1' if airing else '')
        if airing:
            done = (now - prog['start']) / float(max(1, prog['end'] - prog['start']))
            with self._draw:
                self._width(GL.BAR_FG, max(2, self.L.BAR_W * max(0.0, min(1.0, done))))

    # ------------------------------------------------------------ the grid
    def _pick(self, tile, anchor):
        """The programme of ``tile`` the cursor lands on at ``anchor``."""
        progs = [p for p in self._progs(tile) if p['end'] > time.time()]
        end = self.t0 + self.WINDOW
        for prog in progs:
            if prog['start'] <= anchor < prog['end']:
                return prog
            if prog['start'] > anchor:
                return prog if prog['start'] < end else None
        for prog in reversed(progs):
            if prog['end'] > self.t0:
                return prog
        return None

    def _enter_grid(self):
        if not self.channels:
            return
        if self.focus_id() != GL.GRID:
            self._focus_set = True
        self.focus(GL.GRID)

    def _open_panel(self):
        self._grid_on = False
        self._set_panel(True)
        self.focus(GL.CATS)
        with self._draw:
            self._set_cursor()

    def _set_panel(self, open_):
        self.panel = bool(open_)
        self.prop('g.panel', '1' if open_ else '')

    def _toggle_full(self):
        self.full = not self.full
        self.prop('g.full', '1' if self.full else '')
        self._fill_tools()
        with self._draw:
            self._scroll_to(self.ci)
            if not self.full:
                # back to the preview: fewer rows, the cursor's row still in sight
                self.first = max(0, min(self.first, max(0, len(self.channels) - self._visible())))
                self._scroll_to(self.ci)
            self._layout()
            self._set_cursor()
        self._want_guides()

    def _jump_now(self):
        with self._draw:
            now = time.time()
            self.t0 = _slot(now)
            self.anchor = now
            self._header()
            self.prog = self._pick(self._tile(), self.anchor)
            self._layout()
            self._set_cursor()

    def _set_window(self, t0):
        """Move the timeline to start at ``t0`` (the rows are drawn again)."""
        now = time.time()
        t0 = max(_slot(now), min(int(t0), _slot(now + AHEAD)))
        if t0 == self.t0:
            return False
        self.t0 = t0
        self._header()
        self._layout()
        return True

    def _move_programme(self, step):
        tile = self._tile()
        if tile is None:
            return
        now = time.time()
        progs = [p for p in self._progs(tile) if p['end'] > now]
        cur = self.prog if (self.prog is not None and not self.prog.get('_empty')) else None
        with self._draw:
            if step > 0:
                nxt = None
                for prog in progs:
                    if prog['start'] >= ((cur or {}).get('end') or self.anchor + 1) - 1 and prog is not cur:
                        nxt = prog
                        break
                if nxt is None:
                    # nothing more on this channel: the timeline still moves on
                    if self._set_window(self.t0 + SLOT):
                        self.anchor = max(self.anchor, self.t0)
                        self.prog = self._pick(tile, self.anchor)
                        self._set_cursor()
                        self._want_guides()
                    return
                self.anchor = nxt['start']
                if nxt['start'] >= self.t0 + self.WINDOW - SLOT:
                    self._set_window(_slot(nxt['start']) - SLOT)
                    self._want_guides()
                self.prog = nxt
                self._set_cursor()
                return
            prev = None
            if cur is not None:
                for prog in reversed(progs):
                    if prog['end'] <= cur['start'] + 1:
                        prev = prog
                        break
            if prev is None:
                if self.t0 > _slot(now):
                    self._set_window(self.t0 - SLOT)
                    self.anchor = max(self.t0, now) if self.t0 <= now else self.t0
                    self.prog = self._pick(tile, self.anchor)
                    self._set_cursor()
                    return
                self._open_panel()
                return
            self.anchor = max(prev['start'], now) if prev['start'] <= now else prev['start']
            if prev['start'] < self.t0 and self.t0 > _slot(now):
                self._set_window(_slot(max(prev['start'], now)))
            self.prog = prev
            self._set_cursor()

    def _move_channel(self, step):
        count = len(self.channels)
        if not count:
            return
        index = max(0, min(count - 1, self.ci + step))
        if index == self.ci:
            if step > 0 and self.more:
                self._load_more()
            return
        with self._draw:
            self.ci = index
            if self._scroll_to(index):
                self._layout()
            self.prog = self._pick(self._tile(), self.anchor)
            self._set_cursor()
        if self.more and index >= count - self._visible() * 2:
            self._load_more()
        self._want_guides()
        self._channel_changed()

    def _channel_changed(self):
        """The cursor is on another channel: its preview after a short rest."""
        tile = self._tile()
        if tile is None:
            return
        if self._hero_tile is not None and self._hero_tile.get('path') == tile.get('path'):
            return
        self._hero_tile = tile
        self.schedule_trailer(tile)

    def _grid_action(self, aid):
        entered, self._entered = self._entered, False
        if entered and aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
            # the key that brought the focus here (Right from the panel, Down
            # from the tools): Kodi hands the window its focus, then the key
            return
        if aid in (A_UP, A_DOWN, A_PGUP, A_PGDN):
            if aid == A_UP and self.ci == 0:
                self.focus(GL.TOOLS)
                return
            visible = self._visible()
            self._move_channel({A_UP: -1, A_DOWN: 1, A_PGUP: -visible, A_PGDN: visible}[aid])
        elif aid == A_RIGHT:
            self._move_programme(1)
        elif aid == A_LEFT:
            self._move_programme(-1)
        elif aid in A_BACK:
            if self.full:
                self._toggle_full()
                return
            self._open_panel()
        elif aid in (A_CONTEXT, A_MENU):
            self.context_menu(self._tile())
        elif aid == A_INFO:
            self._programme_info()
        elif aid in A_PLAY:
            self.watch_channel(self._tile())

    def _grid_select(self):
        tile = self._tile()
        if tile is None:
            return
        prog = self.prog
        now = time.time()
        if prog is None or prog.get('_empty') or prog['start'] <= now:
            self.watch_channel(tile)
            return
        from .options import choose
        options = [self.tr('شاهد القناة الآن'), self.tr('تفاصيل البرنامج')]
        choice = choose(prog.get('title') or '', options,
                        subtitle=u'%s  •  \u202a%s - %s\u202c' % (tile.get('title') or '', _clock(prog['start']),
                                                     _clock(prog['end'])))
        if choice == 0:
            self.watch_channel(tile)
        elif choice == 1:
            self._programme_info()

    def _programme_info(self):
        tile = self._tile()
        prog = self.prog
        if tile is None:
            return
        if prog is None or prog.get('_empty'):
            self.channel_guide(tile)
            return
        lines = [u'%s   •   \u202a%s - %s\u202c' % (tile.get('title') or '', _clock(prog['start']), _clock(prog['end']))]
        for extra in (prog.get('episode'), prog.get('genre')):
            if extra:
                lines.append(extra)
        lines.append('')
        lines.append(prog.get('plot') or self.tr('لا يوجد وصف لهذا البرنامج'))
        xbmcgui.Dialog().textviewer(prog.get('title') or '', '\n'.join(lines))

    # --------------------------------------------------------------- guide
    def _on_screen(self, path):
        with self._draw:
            for index in list(self._rows.values()):
                if 0 <= index < len(self.channels) and self.channels[index].get('path') == path:
                    return True
        return False

    def _want_guides(self):
        """Ask for the programmes of the channels on screen, the cursor's first."""
        from ..ui_preferences import enabled
        if not enabled('iptv_epg_enabled'):
            return
        if not self.channels or self.vod:
            return
        order = [self.ci] + [i for i in range(self.first, self.first + self._shown()) if i != self.ci]
        gen = self._gen
        for rank, index in enumerate(order):
            tile = self._tile(index)
            if tile is None or not has_guide(tile):
                continue
            path = tile.get('path')
            if not path or path in self._asked or cached_guide(path) is not None:
                continue
            self._asked.add(path)
            self.app.epg.submit(lambda t=tile: self._fetch_guide(t, gen), priority=0 if rank == 0 else 1,
                                key=('guide', path))

    def _fetch_guide(self, tile, gen):
        from ..ui_preferences import enabled
        if not enabled('iptv_epg_enabled'):
            self._asked.discard(tile.get('path'))
            return
        path = tile.get('path')
        if gen != self._gen or self._closing or not self._on_screen(path):
            self._asked.discard(path)
            return
        try:
            if tile.get('src'):
                from . import live_sources as LS
                items = LS.guide(tile, self.app)
            else:
                from . import live
                items = live.guide(tile.get('channelid') or 0, limit=60)
        except Exception:
            items = []
        items = sorted([p for p in items or [] if p.get('start') and p.get('end') and p['end'] > p['start']],
                       key=lambda p: p['start'])
        if not items:
            # a guide that says nothing: now and next of the channel itself, if any
            items = [p for p in (tile.get('now'), tile.get('next')) if p and p.get('start') and p.get('end')]
        _store_guide(path, items)
        self._asked.discard(path)
        if gen != self._gen or self._closing:
            return
        with self._draw:
            for slot, index in list(self._rows.items()):
                if 0 <= index < len(self.channels) and self.channels[index].get('path') == path:
                    self._draw_row(slot, index)
                    if index == self.ci:
                        self.prog = self._pick(tile, self.anchor)
                        self._set_cursor()

    # ---------------------------------------------------------------- tick
    def _tick(self):
        self._tick_at = _now()
        if self._closing or not self.channels or self.vod:
            return
        now = time.time()
        with self._draw:
            if self.t0 < _slot(now):
                # the timeline keeps up with now (programmes that ended go)
                self.t0 = _slot(now)
                self._header()
                self.anchor = max(self.anchor, now)
            self._layout()
            if self.prog is not None and not self.prog.get('_empty') and self.prog['end'] <= now:
                self.prog = self._pick(self._tile(), max(self.anchor, now))
            self._set_cursor()
        self._want_guides()

    # --------------------------------------------------------------- search
    def _ask_search(self):
        if self.source is None:
            return
        query = (xbmcgui.Dialog().input(self.tr('ابحث عن قناة')) or '').strip()
        if not query:
            return
        gen = self._gen = self._gen + 1
        self.prop('g.msg', self.tr('جاري البحث…'))
        self.app.pages.submit(lambda: self._run_search(query, gen), priority=0, key=('guide-search', self.key, gen))

    def _run_search(self, query, gen):
        from . import live_sources as LS
        try:
            tiles = self._search_tiles(query)
        except Exception as exc:
            self.app.log('channel search failed: %s' % LS.failure(exc), xbmc.LOGWARNING)
            tiles = []
        if gen != self._gen or self._closing:
            return
        cat = {'key': 'search', 'title': '%s: %s' % (self.tr('بحث'), query), 'tiles': tiles,
               'count': str(len(tiles))}
        self.cats = [c for c in self.cats if c['key'] != 'search']
        self.cats.insert(0, cat)
        self._search = cat
        self.cat = cat
        self._fill_cats(select=cat)
        self._show_channels(cat, tiles, None, gen=gen,
                            message='' if tiles else self.tr('لا توجد قنوات بهذا الاسم'))
        if tiles:
            self._enter_grid()
        else:
            self.focus(GL.CATS)

    def _search_tiles(self, query):
        """Channels of the source whose name holds ``query``."""
        from . import live, live_sources as LS
        source = self.source or {}
        key = source.get('key') or ''
        wanted = fold(query)
        card = self.app.media_path('channel_card.jpg')
        found = []
        seen = set()

        def take(tiles):
            for tile in tiles:
                path = tile.get('path')
                if path in seen or wanted not in fold(tile.get('title')):
                    continue
                seen.add(path)
                found.append(tile)
                if len(found) >= SEARCH_LIMIT:
                    return True
            return False
        if key.startswith('xt:'):
            from . import live_iptv
            conf = live_iptv._conf(self.app, key)
            for cat in live_iptv.categories(self.app.profile, conf):
                rows = live_iptv.streams(self.app.profile, conf, cat['id'], whole_first=True)
                hits = [r for r in rows if wanted in fold(r[0] if r else '')]
                tiles = []
                for item in hits:
                    name, icon, sid, number, _epg = (list(item) + [''] * 5)[:5]
                    tiles.append(LS.channel_tile(key, sid, name, card, logo=icon, group=cat.get('name') or '',
                                                 number=number, stream={'id': sid}, epg={'xt': sid}))
                if take(tiles):
                    break
            return found
        if key.startswith('m3u:'):
            from . import live_iptv
            params = {'live_src': key, 'group': -1, 'start': 0, 'label': ''}
            tiles, _more = live_iptv.m3u_tiles(self.app, params, card, count=100000)
            take(tiles)
            return found
        if key == 'pvr':
            for group in live.groups():
                if group['all']:
                    start = 0
                    while True:
                        tiles, more, _total = live.group_tiles(group['id'], '', card, start=start,
                                                               count=live.PAGE_SIZE)
                        if take(tiles) or not more:
                            break
                        start = more['start']
                    break
            return found
        # a Stremio source: its own channel search when it has one, then the
        # categories already opened in this guide
        from . import live_stremio, stremio_catalogs as SC
        provider = live_stremio.provider_for(key) or {}
        for cat in (provider.get('manifest') or {}).get('catalogs') or []:
            if not isinstance(cat, dict) or not SC.is_live(cat) or not SC.supports(cat, 'search'):
                continue
            try:
                data = live_stremio._client().fetch_catalog(provider, cat.get('type'), cat.get('id'),
                                                            extra={'search': query}, timeout_override=12,
                                                            retry=False) or {}
            except Exception:
                continue
            dw = bool(live_stremio._dexworld_of(key))
            sources = live_stremio._guide_sources(provider) if dw else set()
            metas = [m for m in data.get('metas') or [] if isinstance(m, dict) and m.get('id')]
            tiles = [live_stremio.meta_tile(key, m, card, '', dw, sources) for m in metas]
            for tile in tiles:
                if tile.get('path') not in seen:
                    seen.add(tile.get('path'))
                    found.append(tile)
            if found:
                return found
        # the channels of the categories already opened in this guide
        for key_, tiles in list(self._loaded.items()):
            if take(tiles):
                break
        return found

    # -------------------------------------------------------- live actions
    def live_refresh(self, force=False):
        """Read the source again; the guide stays where it is until the fresh one is in."""
        if self.source is None:
            self.app.pages.submit(self._load_sources, priority=0, key=('guide-sources', self.key))
            return
        source = self.source
        if self.vod:
            # the films or series read again (the category stays chosen)
            from . import vod
            self._vod_refresh = bool(force)
            vod.invalidate(self.app.profile, source['key'])
            if force and (source['key'] == 'dw' or str(source['key']).startswith('st:')):
                from . import live_stremio
                live_stremio.refresh(source['key'])
            self._open_vod(self.vod)
            return
        if force:
            if source['key'] == 'dw' or str(source['key']).startswith('st:'):
                from . import live_stremio
                live_stremio.refresh(source['key'])
            with _guides_lock:
                for path in [p for p in _guides if str(p).startswith('live://%s/' % source['key'])]:
                    _guides.pop(path, None)
        keep = (self.cat or {}).get('key')

        def job():
            from . import live_sources as LS
            try:
                sources = [s for s in LS.sources(self.app) if s.get('kind') != 'setup']
            except Exception:
                sources = self.sources
            self.sources = sources
            match = [s for s in sources if s['key'] == source['key']]
            if not match:
                self._load_sources()
                return
            if keep:
                LS.remember_tab(self.app.profile, 'live_cat:%s' % source['key'], keep)
            self._open_source(match[0])
        self.app.pages.submit(job, priority=0, key=('guide-refresh', self.key))

    def live_source_added(self, key):
        from . import live_sources as LS
        LS.remember_tab(self.app.profile, 'live', key)
        self.app.pages.submit(self._load_sources, priority=0, key=('guide-sources', self.key))

    def channel_watched(self, tile):
        super(GuideWindow, self).channel_watched(tile)
        recent = self._cat('recent')
        if recent is None or self.source is None:
            return
        try:
            from . import live, live_sources as LS
            card = self.app.media_path('channel_card.jpg')
            if self.source['kind'] == 'pvr':
                recent['tiles'] = live.recent_tiles(self.app.profile, card)
            else:
                recent['tiles'] = LS.recent_tiles(self.app.profile, self.source['key'], card)
            recent['count'] = len(recent['tiles'])
        except Exception:
            pass

    # --------------------------------------------------------------- events
    def onFocus(self, control_id):
        if control_id == GL.VOD:
            if not self.vod_tiles:
                self.focus(GL.CATS)
                return
            self._grid_on = False
            self._set_panel(False)
            self._vod_near_end()
            return
        if control_id == GL.GRID:
            if not self.channels:
                self.focus(GL.CATS)
                return
            # a key moved the focus here, unless the guide did (_enter_grid)
            self._entered = not self._focus_set
            self._focus_set = False
            self._grid_on = True
            self._set_panel(False)
            with self._draw:
                self._set_cursor()
            self._channel_changed()
        elif control_id in (GL.CATS, GL.BTNS, GL.RAIL):
            self._grid_on = False
            self._set_panel(True)
            with self._draw:
                self._set_cursor()
        elif control_id == GL.TOOLS:
            self._grid_on = False
            self._set_panel(False)
            with self._draw:
                self._set_cursor()

    def onClick(self, control_id):
        if control_id == GL.VOD:
            self._vod_click()
        elif control_id == GL.GRID:
            self._grid_select()
        elif control_id == GL.CATS:
            self._cat_click()
        elif control_id == GL.RAIL:
            self._rail_click()
        elif control_id == GL.BTNS:
            self._button_click()
        elif control_id == GL.TOOLS:
            self._tool_click()

    def onAction(self, action):
        aid = action.getId()
        if aid in A_IGNORE:
            return
        if self._back_bounce(aid):
            self.app.log('Back right after the page showed: taken as the same press')
            return
        self.input_seen()
        focus = self.focus_id()
        if focus == GL.VOD:
            if aid in A_BACK:
                self._open_panel()
            elif aid in (A_CONTEXT, A_MENU) and self.source is not None:
                self.manage_live_source(self.source['key'])
            else:
                self._vod_near_end()
            return
        if focus == GL.GRID:
            self._grid_action(aid)
            return
        if aid in A_PLAY and self._real_playing:
            return
        if focus == GL.CATS:
            if aid in (A_UP, A_DOWN, A_PGUP, A_PGDN):
                self.app.scheduler.call_later(self.key + ':cat', CAT_SETTLE, self._cat_settled)
            elif aid in (A_CONTEXT, A_MENU) and self.source is not None:
                self.manage_live_source(self.source['key'])
            elif aid in A_BACK:
                self.focus(GL.RAIL)
            return
        if focus == GL.BTNS:
            if aid in A_BACK:
                self.focus(GL.RAIL)
            return
        if focus == GL.TOOLS:
            if aid in (A_DOWN, A_PGDN) or aid in A_BACK:
                self._enter_grid()
            return
        if focus == GL.RAIL:
            if aid in A_BACK:
                self.leave()
            elif aid in (A_CONTEXT, A_MENU):
                key = self._selected_key(GL.RAIL)
                if key.startswith('src:'):
                    self.manage_live_source(key[4:])
            return
        if aid in A_BACK:
            self.leave()
            return
        self.focus(self.default_focus())
