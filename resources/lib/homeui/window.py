# -*- coding: utf-8 -*-
"""Dex Hub Home windows: the Nuvio-style browse page and the poster grid.

Layout contract with resources/skins/Default/1080i/dexhub_browse.xml:

  90                top navigation (home mode)
  200               hero buttons (billboard: play / details / trailer)
  210               billboard page dots (never focused)
  220               hero progress bar (one item, never focused)
  5000/5001/5002    row A (poster / landscape / square), the focused row; it
                    always sits at the same height
  5100/5101/5102    row B, the next row peeking in below
  95                now-playing controls (player / pause / stop) while a
                    video plays in the background
  9990              invisible focus sink used while the immersive trailer
                    view hides everything but the title

Rows are paged rather than scrolled: moving down swaps row B into row A's
place with a short slide, so the focused row keeps its position under the
hero exactly like Nuvio's modern home. Each tile shape has its own list and
only the list matching the current row holds items, because Kodi keeps the
first layout a list item was drawn with. List items are created fresh for
every render for the same reason.
"""
import json
import os
import re
import threading
import time
from .. import lazy_tb as traceback
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui

from . import rows as R

NAV, HERO_BTNS, DOTS, HERO_PROGRESS, SINK, NP = 90, 200, 210, 220, 9990, 95
NAV_ICONS = 91   # Search and Menu as icon tabs, right of the text tabs (v5.10.103)
PROFILE = 89     # the Dex Hub logo: the Nuvio profile button (v5.10.98)
TABS = 92        # a page's own tabs: its servers, or its live sources (v5.10.104)
# v5.10.167: the Home's tabs are skin.dexhub's buttons (in grouplist 90),
# on the Home, My Library, My Servers and Add-ons as skin.dexhub's tabs are.
# v5.10.189: icon tabs (9010..; 9030.. draws a tab that is not focused, 9070..
# its icon); the text tabs (9050..) when homeui_nav_names is on
NAV_KEYS = ('home', 'movie', 'series', 'servers', 'live', 'library', 'addons')
NAV_BUTTONS = (9010, 9011, 9012, 9013, 9014, 9015, 9016)
NAV_NAME_BUTTONS = (9050, 9051, 9052, 9053, 9054, 9055, 9056)
NAV_BUTTON_KEYS = dict(list(zip(NAV_BUTTONS, NAV_KEYS)) + list(zip(NAV_NAME_BUTTONS, NAV_KEYS)))
NAV_KEY_BUTTONS = dict(zip(NAV_KEYS, NAV_BUTTONS))
NAV_KEY_NAME_BUTTONS = dict(zip(NAV_KEYS, NAV_NAME_BUTTONS))
TAB_MODES = ('home', 'library', 'servers', 'addons')
# the chips' colours on skin.dexhub (routes/library.py, routes/servers_tab.py)
_LIBRARY_TONES = {'fav': 'FFF5B428', 'floppy': 'FF8B7CF8', 'trakt': 'FFED3B42', 'simkl': 'FF3FB0F0',
                  'mdblist': 'FF2BC48A', 'nuvio': 'FF6474EF', 'stremio': 'FF8B5CF6'}
_SERVER_TONES = {'plex': 'FFE5A00D', 'emby': 'FF52B54B', 'jellyfin': 'FFAA5CC3', 'silo': 'FF3FB0F0'}
RANKED_MAX = 10
LOGO_SHAPES = ('wide', 'wide_ranked', 'tall', 'tall_ranked', 'feature')
SHAPE_OFFSET = {'poster': 0, 'landscape': 1, 'square': 2, 'wide': 3, 'ranked': 0, 'tall': 4,
                'tall_ranked': 4, 'collage': 5, 'wide_ranked': 3, 'mosaic': 5, 'stack': 5, 'shelf': 5,
                'poster_large': 0, 'feature': 3, 'landscape_info': 1,
                'collection_spotlight': 5, 'collection_compact': 5, 'collection_ranking': 5,
                'circle': 2}     # the add-on's own Home draws circles as squares
RANKED_SHAPES = ('ranked', 'tall_ranked', 'wide_ranked')     # the place beside each card
COLLAGE_SHAPES = ('collage', 'mosaic', 'stack', 'collection_spotlight', 'collection_compact', 'collection_ranking')
ROW_A_BASE, ROW_B_BASE, GRID_BASE = 5000, 5100, 6000
ROW_A_IDS = (5000, 5001, 5002, 5003, 5004, 5005)
ROW_B_IDS = (5100, 5101, 5102, 5103, 5104, 5105)
GRID_IDS = (6000, 6001, 6002)

A_LEFT, A_RIGHT, A_UP, A_DOWN, A_PGUP, A_PGDN = 1, 2, 3, 4, 5, 6
A_SELECT, A_INFO, A_CONTEXT, A_MENU = 7, 11, 117, 163
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
A_PLAY = {68, 79, 229}
A_MOUSE = set(range(100, 110))
# volume, mute and amp keys belong to Kodi and never count as navigation
# 203: Kodi reading its keymaps again (keymap.py), not a key press
A_IGNORE = A_MOUSE | {88, 89, 91, 93, 94, 95, 96, 203}

_WORK_TYPES = {'movie': 'فيلم', 'series': 'مسلسل', 'episode': 'حلقة'}
_UI_ART_ACTIONS = frozenset(('play_item', 'streams', 'episode_streams', 'cw_resume'))
# v5.10.107: a server title opened from the Home (its search, its rows) takes
# the art the page showed to the player, the sources window and its seasons;
# a server's art carries its address and token, so it goes by a cache key,
# never in the plugin URL (kodi.log shows every URL)
_SERVER_ART_ACTIONS = frozenset(('plex_play', 'emby_play', 'jellyfin_play', 'silo_play', 'plex_children'))
# v5.10.105: Dex Hub routes opened from the Home carry dh_home=1, so the
# classic "open in TMDb Helper" click mode never takes one of its titles
# elsewhere (the Home's own title page setting decides)
_DEXHUB_ROUTE_RE = re.compile(r'(plugin://plugin\.video\.dexhub/?\?)(?!dh_home=1)')
# BrowseWindow pages whose Back on the first window leaves Dex Hub: asked first
_CONFIRM_EXIT_MODES = frozenset(('home', 'live', 'servers'))
# v5.10.103: a movie or show from a catalog, a collection or the search opens
# TMDb Helper's details page; a title in the user's own library still plays
# from it (plex_play and the like), and Continue Watching resumes
_HELPER_ACTIONS = frozenset(('item_open', 'series_meta', 'seasons', 'season', 'play_item', 'streams', 'episode_streams', 'tmdb_player'))
# trailers (v5.10.103): the shortest wait on a title before its preview
# starts, and when the preview's lookup begins while that wait runs
_TRAILER_SETTLE = 0.5
_TRAILER_LOOKUP_AFTER = 0.25
# live TV (v5.10.103): how long the cursor rests on a channel before it plays
# behind the page, and how often the programme lines move on
_CHANNEL_SETTLE = 0.9
_LIVE_TICK = 30.0
TMDBH_ID = 'plugin.video.themoviedb.helper'


def _now():
    return time.monotonic()


# v5.10.108: a Back this soon after a page showed again is the same press:
# back from the player (a remote's repeat, a key held a little long), or a
# page above it closed (a shorter wait, so a quick double Back still counts)
_BACK_SETTLE = 0.6
_BACK_SETTLE_PAGE = 0.35


_URL_QUERY_RE = re.compile(r'''((?:https?://|(?<![\w.])/)[^\s?#'"<>]*)[?#][^\s'"<>]*''', re.I)
_SECRET_RE = re.compile(r'''\b(api_?key|x-plex-token|access_token|token|password|passwd)=[^&\s'"<>]+''', re.I)


def screen_reason(text, limit=160):
    """A row's failure text made fit for the screen.

    Exception texts can quote a request URL or a bare path with its query
    (requests: "url: /Items?api_key=..."), and a query may carry an account
    token (Plex, Emby, debrid), so every query is cut off and any leftover
    secret parameter is masked.
    """
    text = _URL_QUERY_RE.sub(r'\1', str(text or ''))
    text = _SECRET_RE.sub(r'\1=***', text).strip()
    text = ' '.join(text.split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + '…'
    return text


def _notes_begin():
    try:
        from .. import capture
        capture.begin_notes()
    except Exception:
        pass


def _notes_last():
    try:
        from .. import capture
        return capture.last_notice()
    except Exception:
        return ''


def _shape_id(base, shape):
    return base + SHAPE_OFFSET.get(shape or 'poster', 0)


def episode_code(tile):
    """'S1:E3' for an episode tile, '' otherwise."""
    season, episode = (tile or {}).get('season'), (tile or {}).get('episode')
    if not (season or episode):
        return ''
    if not episode:
        return 'S%s' % season      # a season card
    return 'S%s:E%s' % (season or '?', episode)


def same_work(a, b):
    """True when two tiles stand for the same title (a refreshed row makes new tile dicts)."""
    if a is b:
        return a is not None
    if not a or not b:
        return False
    for key in ('path', 'imdb_id'):
        if a.get(key) and a.get(key) == b.get(key):
            return True
    ref_a, ref_b = a.get('folder_ref'), b.get('folder_ref')
    return bool(ref_a) and ref_a == ref_b


# v5.10.195: Kodi's skin reload (a font, colour or skin setting change, an
# add-on such as a font patcher writing into the skin) destroys every control
# of every window, then shows the window that was on top again: its onInit
# runs again. A control fetched before (getControl hands out a pointer into
# that tree) then points at freed memory, and the next call on it crashed
# Kodi (macOS log: SIGSEGV in ControlList::getSelectedPosition from onAction).
# Kodi also keeps the control objects a window handed out (Window.vecControls)
# and getControl returns those same stale objects, so a window cannot fetch
# them anew: a window that lived through a reload is never touched again
# (_control raises) and the Home closes its pages and opens itself fresh.
_SKIN = {'gen': 0, 'unloading': False}


def skin_event(unloading):
    _SKIN['unloading'] = bool(unloading)
    _SKIN['gen'] += 1
    xbmc.log('[DexHub] homeui: skin %s' % ('unloading' if unloading else 'loaded again'), xbmc.LOGINFO)


class _BaseWindow(xbmcgui.WindowXML):
    """Hero, trailer, immersive view and activation handling shared by both pages."""
    mode = ''           # v5.10.209: the Home sets its own (home, servers, library...)

    def __init__(self, *args, **kwargs):
        super(_BaseWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.s = self.app.settings
        self.key = 'w%d' % id(self)
        self._lock = threading.RLock()
        self._inited = False
        self._closing = False
        self._covered = False
        self._window_id = 0
        self._hero_tile = None
        self._hero_anim = 'a'
        self._props = {}
        self._displayed = {}
        self._last_input = _now()
        self._immersive = False
        self._focus_before_immersive = 0
        self._trailer_tile = None
        self._trailer_auto = 0
        self._trailer_visible_at = 0.0
        self._real_playing = False
        self._resume_at = 0.0
        from .. import progress_events
        self._progress_revision = progress_events.revision()
        self._user_moved = False
        # v5.10.108: when the page showed again (back from the player or from
        # a page above it): a Back that arrives right then is the same press
        # (a remote's repeat, a key still down), not a new one
        self._shown_at = 0.0
        self._settle = _BACK_SETTLE
        self._uncovered_at = 0.0
        self._expect = {}
        self._np_at = 0.0
        # controls fetched once, and the ListItems this window created for
        # each list (see _control)
        self._ctrls = {}
        self._ctrls_gen = _SKIN['gen']
        self._items = {}
        self._keys = {}
        self.trailers_on = self.s.flag('homeui_trailers', True)
        self.trailer_delay = self.s.number('homeui_trailer_delay', 3, 0, 20)
        self.trailer_sound = self.s.flag('homeui_trailer_sound', True)
        self.immersive_on = self.s.flag('homeui_immersive', True)
        self.immersive_after = self.s.number('homeui_immersive_delay', 16, 4, 60)
        self.live_preview = self.s.flag('homeui_live_preview', True)
        # live TV: the channel OK asked for while its preview opens (its key)
        self._watch_pending = ''
        self._watch_tile = None
        self.gif_on = self.s.flag('homeui_focus_gif', True)
        self._debug = self.s.flag('homeui_debug', False)

    # ------------------------------------------------------------ helpers
    def trace(self, msg):
        if self._debug:
            self.app.log('[%s] %s' % (type(self).__name__, msg))

    def tr(self, text):
        return self.app.tr(text)

    def prop(self, key, value):
        value = '' if value is None else str(value)
        with self._lock:
            if self._props.get(key) == value:
                return
            self._props[key] = value
            try:
                if value:
                    self.setProperty(key, value)
                else:
                    self.clearProperty(key)
            except Exception:
                pass

    def _theme(self):
        self.prop('dh.gif.enabled', '1' if self.s.flag('homeui_focus_gif', True) else '')
        self.prop('dh.plot.scroll', '1' if self.s.flag('homeui_plot_scroll', True) else '')
        self.prop('dh.plot.off', '1' if self.s.flag('homeui_hide_plot', False) else '')
        self.prop('dh.motion', (self.s.text('homeui_focus_motion', 'zoom') or 'zoom').strip().lower())
        # v5.10.172: the cards' titles under the picture (setting Card titles)
        # (Below: '1'; None: 'none', no title on or under the picture, the
        # hero above names the focused title with its logo)
        self.prop('dh.cards.below', {'Below': '1', 'None': 'none'}.get(
            self.s.text('homeui_card_style', 'Overlay') or '', ''))
        if not hasattr(self, '_prefs_language'):
            self._prefs_language = self._tmdb_language()
        try:
            from .. import skin_theme
            skin_theme.publish_theme(window=self)
        except Exception:
            pass

    def is_active(self):
        if self._closing or self._covered or not self._window_id:
            return False
        try:
            return xbmcgui.getCurrentWindowId() == self._window_id
        except Exception:
            return False

    def dialog_open(self):
        try:
            return xbmcgui.getCurrentWindowDialogId() not in (9999, 0)
        except Exception:
            return False

    def focus_id(self):
        try:
            return self.getFocusId()
        except Exception:
            return 0

    # ----------------------------------------------------------- controls
    # Kodi's list API is not safe across threads. Window.getControl walks and
    # grows a C++ vector with the interpreter lock released, and the item that
    # getListItem / getSelectedItem hand out is only borrowed from the list:
    # a reset on another thread frees it while a setArt on it still waits for
    # the GUI lock, which crashed Kodi in CGUIListItem::SetArt. So controls
    # are fetched once, every list change happens under self._lock, and late
    # changes go to the ListItem objects this window created and still holds.
    def _control(self, control_id):
        if _SKIN['unloading'] or self._ctrls_gen != _SKIN['gen'] or getattr(self, '_stale', False):
            raise RuntimeError('the skin was reloaded under this window')
        ctrl = self._ctrls.get(control_id)
        if ctrl is not None:
            return ctrl
        with self._lock:
            ctrl = self._ctrls.get(control_id)
            if ctrl is None:
                ctrl = self.getControl(control_id)      # raises for a missing id
                self._ctrls[control_id] = ctrl
            return ctrl

    def _fresh_controls(self):
        """Every onInit (v5.10.195): True when the skin was reloaded under this
        window (its controls are gone): the Home then closes its pages and
        opens itself again (App.after_skin_reload), and onInit stops here."""
        if getattr(self, '_stale', False):
            return True
        reloaded = self._ctrls_gen != _SKIN['gen']
        if not reloaded and self._ctrls:
            filled = [cid for cid, items in self._items.items() if items]
            try:
                reloaded = any(not int(xbmc.getInfoLabel('Container(%d).NumItems' % cid) or 0) for cid in filled)
            except ValueError:
                reloaded = bool(filled)
        if not reloaded:
            return False
        self._stale = True
        _SKIN['unloading'] = False
        xbmc.log('[DexHub] homeui: the skin was reloaded under %s: Dex Hub opens again' % type(self).__name__,
                 xbmc.LOGINFO)
        try:
            self.app.after_skin_reload()
        except Exception:
            xbmc.log('[DexHub] homeui: reopening after the skin reload failed:\n%s' % traceback.format_exc(),
                     xbmc.LOGWARNING)
        return True


    def _warm_controls(self, ids):
        """Fetch the page's controls on the GUI callback thread, before workers start."""
        for control_id in ids:
            try:
                self._control(control_id)
            except Exception:
                pass

    def _set_items(self, control_id, items, keys=None):
        """Replace a list's items, keeping the ListItem objects alive here."""
        with self._lock:
            ctrl = self._control(control_id)
            ctrl.reset()
            if items:
                ctrl.addItems(items)
            self._items[control_id] = list(items or [])
            self._keys[control_id] = list(keys or [])
            return ctrl

    def _selected_key(self, control_id):
        """The 'key' of a button list's selected entry, read from our own copy."""
        with self._lock:
            keys = list(self._keys.get(control_id) or [])
            try:
                pos = int(self._control(control_id).getSelectedPosition())
            except Exception:
                return ''
        return keys[pos] if 0 <= pos < len(keys) else ''

    # --------------------------------------------------------------- hero
    def hero_props(self, tile):
        tile = tile or {}
        kind = tile.get('kind')
        if kind == 'channel':
            return self._channel_hero(tile)
        media = tile.get('media_type') or ''
        title = tile.get('title') or tile.get('label') or ''
        show = tile.get('show') or ''
        subtitle = ''
        if media == 'episode' and show:
            # the show leads, the episode is the line under it (as in Nuvio)
            parts = [episode_code(tile), R.card_episode_name(tile)]
            subtitle = '  ·  '.join([p for p in parts if p])
            title = show
        elif tile.get('hint') and not tile.get('cw'):
            # (v5.10.167: Continue Watching's hint is the episode's code, which
            # its title already names; skin.dexhub's hero shows it once)
            subtitle = tile.get('hint')
        if len(title) > 64:
            title = title[:62].rstrip() + '…'
        meta = []
        star = ''
        if kind == 'addon':
            meta.append(('v%s' % tile['version']) if tile.get('version') else '')
            meta.append(tile.get('author') or '')
        if kind == 'folder':
            count = int(tile.get('source_count') or 0)
            if count > 1:
                meta.append(self.tr('%d كتالوج') % count)
            elif count == 1:
                meta.append(self.tr('كتالوج واحد'))
        elif kind == 'work':
            if tile.get('year'):
                meta.append(str(tile.get('year')))
            if tile.get('rating'):
                # v5.10.167: on the ratings line under the facts, as skin.dexhub's hero
                try:
                    star = '%.1f' % float(tile.get('rating'))
                except (TypeError, ValueError):
                    star = ''
                if star in ('0.0', ''):
                    star = ''
            from ..durations import sane  # v5.10.286
            duration = sane(tile.get('duration'))
            if duration >= 60:
                hours, minutes = divmod(duration // 60, 60)
                if self.s.arabic:
                    meta.append(('%dس %dد' % (hours, minutes)) if hours else ('%dد' % minutes))
                else:
                    meta.append(('%dh %dm' % (hours, minutes)) if hours else ('%dm' % minutes))
            genres = tile.get('genres') or []
            if genres:
                meta.append(' / '.join(genres[:3]))
            if tile.get('mpaa'):
                meta.append(str(tile.get('mpaa')))
            if tile.get('cw') and tile.get('label2'):
                # Continue Watching: the source the title was playing from
                meta.append(str(tile.get('label2')))
            if tile.get('cw') and tile.get('facts'):
                meta.append(str(tile.get('facts')))
        remaining = ''
        progress = int(tile.get('progress') or 0)
        if progress and tile.get('total'):
            left = max(0, int(float(tile.get('total')) - float(tile.get('resume') or 0)) // 60)
            if left:
                remaining = self.tr('متبقي %d دقيقة') % left
        badge = ''
        if kind == 'library':
            badge = self.tr('مكتبة')
            meta.append(tile.get('brand') or '')
        elif kind == 'action':
            badge = self.tr('إعداد')
        if kind == 'folder':
            badge = self.tr('مجموعة')
        elif kind == 'query':
            badge = self.tr('بحث')
        elif tile.get('season_card'):
            badge = self.tr('موسم')
        elif media in _WORK_TYPES:
            badge = self.tr(_WORK_TYPES[media])
        backdrop = tile.get('fanart') or tile.get('landscape') or ''
        if kind in ('library', 'action', 'addon'):
            backdrop = tile.get('fanart') or ''     # the card is not a backdrop
        if kind == 'folder' and not backdrop:
            backdrop = tile.get('group_fanart') or ''
        logo = tile.get('clearlogo') or ''
        if logo and self.app.logos.state(logo) is not True:
            logo = ''       # shown once it has loaded as an image (_check_logo)
        if logo and kind == 'work' and self._tmdb_pending(tile):
            logo = ''       # the logo in the user's language is on its way
        from ..ui_preferences import enabled
        extras_on = enabled('metadata_badges')
        studio = (tile.get('studio_logo') or '') if kind == 'work' and extras_on else ''
        from . import ratings as RB
        out = RB.props(tile.get('ratings') if kind == 'work' and extras_on else None)
        # the star when the services' own scores are not shown (skin.dexhub: the
        # star stands in for IMDb and TMDb)
        if out.get('dh.rating.imdb') or out.get('dh.rating.tmdb'):
            star = ''
        out.update({
            'dh.rating.star': star,
            'dh.hero.icon': (tile.get('icon') or '') if kind == 'addon' else '',
            'dh.backdrop': backdrop,
            'dh.poster': tile.get('poster') or '',
            'dh.logo': logo,
            # v5.10.105: the network (series) or studio (film) beside the meta line
            'dh.studio': studio,
            'dh.studio.chip': '1' if studio and tile.get('studio_logo_chip') else '',
            'dh.title': title,
            'dh.title.long': '1' if len(title) > 26 else '',
            'dh.subtitle': subtitle,
            'dh.meta': '   •   '.join([m for m in meta if m]),
            'dh.plot': tile.get('plot') or tile.get('tagline') or '',
            'dh.badge': badge,
            'dh.progress': str(progress) if progress else '',
            'dh.remaining': remaining,
            'dh.provider': tile.get('direct_from') or '',
        })
        return out

    def _channel_hero(self, tile):
        """A TV channel: its logo, the programme on now and the one after it."""
        from . import live
        on = live.update_times(tile)
        cur, nxt = tile.get('now') or {}, tile.get('next') or {}
        title = tile.get('title') or ''
        logo = tile.get('clearlogo') or ''
        if logo and self.app.logos.state(logo) is not True:
            logo = ''       # shown once it has loaded as an image (_check_logo)
        meta = []
        if logo and title:
            meta.append(title)      # the logo alone may not name the channel
        if on:
            meta.append('%s - %s' % (live.clock(cur.get('start')), live.clock(cur.get('end'))))
            if cur.get('genre'):
                meta.append(cur['genre'])
        elif tile.get('genre'):
            meta.append(tile['genre'])
        next_line = self.tr('التالي: %s') % ('%s  %s' % (live.clock(nxt.get('start')), nxt['title'])) if nxt.get('title') else ''
        if tile.get('number'):
            meta.append(self.tr('القناة %s') % tile['number'])
        if tile.get('src') and tile.get('group') and not on and tile['group'] != tile.get('genre'):
            meta.append(tile['group'])
        subtitle = (cur.get('title') or '') if on else ''
        if subtitle and cur.get('episode'):
            subtitle = '%s  ·  %s' % (subtitle, cur['episode'])
        if not subtitle:
            # a source without a guide (most Stremio add-ons) says nothing
            # about it; a guide that is still on its way is not "missing"
            guided = not tile.get('src') or (tile.get('epg') or {}).get('dw') or (tile.get('epg') or {}).get('xt')
            if not tile.get('src'):
                subtitle = self.tr('لا يوجد دليل برامج لهذه القناة')
            elif guided and tile.get('_epg_done'):
                subtitle = self.tr('لا يوجد دليل برامج لهذه القناة')
        remaining = ''
        if on:
            left = max(0, int(cur.get('end', 0) - time.time()) // 60)
            if left:
                remaining = self.tr('متبقي %d دقيقة') % left
        from . import ratings as RB
        out = RB.props(None)
        out.update({
            'dh.rating.star': '',
            'dh.hero.icon': '',
            'dh.backdrop': ((cur.get('thumb') or '') if on else '') or tile.get('fanart') or '',
            'dh.logo': logo,
            'dh.title': title,
            'dh.title.long': '1' if len(title) > 26 else '',
            'dh.subtitle': subtitle,
            'dh.meta': '   •   '.join([m for m in meta if m]),
            'dh.plot': ((cur.get('plot') or '') if on else '') or tile.get('desc') or '',
            'dh.next': next_line,
            'dh.now': subtitle or self.tr('بث مباشر'),
            'dh.badge': self.tr('مباشر'),
            'dh.studio': '',
            'dh.studio.chip': '',
            'dh.progress': str(tile.get('progress')) if tile.get('progress') else '',
            'dh.remaining': remaining,
            'dh.provider': '',
        })
        return out

    def show_hero(self, tile, animate=True):
        self._check_logo(tile)
        with self._lock:
            same = tile is self._hero_tile
            self._hero_tile = tile
            props = self.hero_props(tile)
            props['dh.hero.channel'] = '1' if (tile or {}).get('kind') == 'channel' else ''
            if (tile or {}).get('kind') != 'channel':
                props['dh.next'] = ''
                props['dh.now'] = ''
            changed = any(self._props.get(k, '') != v for k, v in props.items())
            if not changed:
                return
            for key, value in props.items():
                self.prop(key, value)
            self._hero_progress(tile)
            self._hero_badges(tile)
            if animate and not same:
                self._hero_anim = 'b' if self._hero_anim == 'a' else 'a'
                self.prop('dh.hero.anim', self._hero_anim)

    # v5.10.164: a server copy's stream badges on the hero (the player's
    # style, or the Library's own), skin.dexhub's row in a lighter shape:
    # each badge is drawn in the nearest of a few widths
    _BADGE_Q = (6, 8, 10, 12, 16, 20, 24, 32)

    def _hero_badges(self, tile):
        badges = []
        facts = (tile or {}).get('facts_line') or ''
        # v5.10.209: the Grid and title pages share this and have no mode
        # (an AttributeError broke their onInit and the Grid's hero timer)
        if facts and getattr(self, 'mode', '') in ('servers', 'library'):
            try:
                from ..ui_preferences import enabled
                if enabled('badges_home'):
                    from .. import player_badges
                    badges = player_badges.hero_badges(facts)[:8]
            except Exception as exc:
                self.trace('hero badges: %s' % exc)
        for n in range(1, 9):
            badge = badges[n - 1] if n <= len(badges) else {}
            image = badge.get('image') or ''
            q = ''
            if image:
                digits = ''.join(c for c in str(badge.get('w') or 'k12') if c.isdigit())
                want = int(digits or 12)
                q = str(next((b for b in self._BADGE_Q if b >= want), self._BADGE_Q[-1]))
            self.prop('dh.sb.%d.image' % n, image)
            self.prop('dh.sb.%d.q' % n, q)
            self.prop('dh.sb.%d.text' % n, '' if image else (badge.get('text') or ''))
        self.prop('dh.sb.count', str(len(badges)) if badges else '')

    def _hero_progress(self, tile):
        progress = int((tile or {}).get('progress') or 0)
        items = []
        if progress:
            try:
                li = xbmcgui.ListItem('progress')
                li.getVideoInfoTag().setResumePoint(float(progress), 100.0)
                items.append(li)
            except Exception:
                items = []
        try:
            self._set_items(HERO_PROGRESS, items)
        except Exception:
            pass

    def refresh_hero_if(self, tile):
        if tile is not None and tile is self._hero_tile:
            self.show_hero(tile, animate=False)

    def prefetch_logos(self, tiles):
        """Check the logos of the tiles about to be focused, before they are."""
        if self._real_playing:
            return
        for tile in list(tiles or [])[:12]:
            self._check_logo(tile, priority=6, background=True)

    def _check_logo(self, tile, priority=2, background=False):
        """Load-test a title logo once; a dead one gives way to the title text."""
        logo = (tile or {}).get('clearlogo') or ''
        if not logo or self.app.logos.state(logo) is not None:
            return

        def job():
            ok = self.app.logos.check(logo)
            # the same title may sit in several rows: settle every copy that
            # is on the hero now, not only the tile that asked
            hero = self._hero_tile
            for candidate in (tile, hero):
                if candidate is None or candidate.get('clearlogo') != logo:
                    continue
                if not ok:
                    candidate['clearlogo'] = ''
                    candidate.pop('_enriched', None)
                    self.update_tile(candidate, art={'clearlogo': ''})
                self.refresh_hero_if(candidate)
                if not ok and candidate is hero:
                    self.enrich(candidate, priority=2)
        lane = self.app.bg if background else self.app.fast
        lane.submit(job, priority=priority, key=('logo', logo))

    # -------------------------------------------------------- enrichment
    def tmdb_settings(self):
        """How the hero takes its plot and title logo from TMDb (v5.10.98).

        With a synced Nuvio profile, Nuvio's own TMDb settings decide: its
        language, and whether TMDb supplies the text (basic info) and the
        artwork; TMDb switched off there means the catalog's own plot and
        logo stay. Without Nuvio, TMDb follows Dex Hub's interface language.
        None: no TMDb pass.
        """
        cached = getattr(self.app, '_tmdb_settings', None)
        if cached is not None and _now() - cached[0] < 60.0:
            return cached[1]
        try:
            from ..dexhub import nuvio_profile_prefs as npp
            prefs = npp.normalized_tmdb() or {}
        except Exception:
            prefs = {}
        try:
            from ..meta_source import ADDON as _MA, meta_language as _meta_language
            one_language = (_MA.getSetting('global_meta_source_id') or '') == 'tmdb'
        except Exception:
            one_language = False
        if one_language:
            # v5.10.301: TMDb in one language everywhere: the Home's text and
            # logos too, whatever a synced Nuvio profile says, with the title
            settings = {'language': _meta_language(), 'use_artwork': True, 'use_basic_info': True,
                        'use_details': True, 'localized_title_on_home': True}
        elif prefs.get('synced'):
            settings = dict(prefs) if prefs.get('enabled') else None
        else:
            settings = {'language': 'ar' if self.s.arabic else 'en', 'use_artwork': True,
                        'use_basic_info': True, 'use_details': True, 'localized_title_on_home': False}
        override = self.s.text('homeui_metadata_language', 'Auto').strip().lower()
        if settings and override in ('arabic', 'english'):
            settings['language'] = 'ar' if override == 'arabic' else 'en'
        self.app._tmdb_settings = (_now(), settings)
        return settings

    def _refresh_row_sources(self):
        """Update visible source captions without fetching any catalog again."""
        from ..ui_preferences import enabled
        show = enabled('homeui_show_row_source')
        rows = getattr(self, 'rows', None)
        if rows is None or not hasattr(self, '_row_visible'):
            return
        with self._lock:
            index = self.ri
            first = rows[index] if self._row_visible(index) else None
            next_index = self._next_visible(index, 1) if first is not None else None
            second = rows[next_index] if next_index is not None else None
            for slot, row in (('A', first), ('B', second)):
                self.prop('dh.row%s.subtitle' % slot, (row.subtitle or '') if show and row else '')

    def reload_preferences(self):
        """Apply motion switches and locale while this page stays open."""
        before = getattr(self, '_prefs_language', '')
        had_gifs = self.gif_on
        self.s = self.app.settings
        self.app._tmdb_settings = None
        self.gif_on = self.s.flag('homeui_focus_gif', True)
        self.prop('dh.gif.enabled', '1' if self.gif_on else '')
        self.prop('dh.plot.scroll', '1' if self.s.flag('homeui_plot_scroll', True) else '')
        self.prop('dh.plot.off', '1' if self.s.flag('homeui_hide_plot', False) else '')
        self._refresh_row_sources()     # v5.10.286
        self.prop('dh.motion', (self.s.text('homeui_focus_motion', 'zoom') or 'zoom').strip().lower())
        self.prop('dh.kenburns', '1' if self.s.flag('homeui_kenburns', True) else '')
        self.kenburns = self.s.flag('homeui_kenburns', True)
        self.trailers_on = self.s.flag('homeui_trailers', True)
        self.trailer_delay = self.s.number('homeui_trailer_delay', 3, 0, 20)
        self.trailer_sound = self.s.flag('homeui_trailer_sound', True)
        self.app.director.update_preview_audio(self, not self.trailer_sound,
                                              self.s.text('homeui_trailer_volume', '30'))
        self.immersive_on = self.s.flag('homeui_immersive', True)
        self.live_preview = self.s.flag('homeui_live_preview', True)
        self.carousel_on = self.s.flag('homeui_carousel', True) and getattr(self, 'mode', '') == 'home'
        self.immersive_after = self.s.number('homeui_immersive_delay', 16, 4, 60)
        if getattr(self, 'mode', '') in TAB_MODES:
            # v5.10.364: the Servers switch applies at once
            hidden = self._servers_hidden()
            self.prop('dh.nav.noservers', '1' if hidden else '')
            if hidden and self.mode == 'servers':
                self.close_window()
                return
        shuffle = self.s.flag('homeui_hero_shuffle', False)
        if shuffle != getattr(self, '_hero_shuffle', shuffle) and getattr(self, 'mode', '') == 'home':
            self.carousel = []          # v5.10.364: picked again in the new order
        self._hero_shuffle = shuffle
        if not self.s.flag('homeui_trailers', True):
            self.stop_trailer(wait=False)
        language = self._tmdb_language()
        self._prefs_language = language
        if self._trailer_tile and not self.trailer_wanted(self._trailer_tile):
            self.stop_trailer(wait=False)
        if before != language:
            self._shown = {}
            for row in getattr(self, 'rows', []):
                for tile in row.tiles:
                    tile.pop('_enriched', None)
                    tile.pop('_tmdb_done', None)
            tile = self._hero_tile
            if tile:
                tile.pop('_enriched', None)
                tile.pop('_tmdb_done', None)
                self.enrich(tile, focus=True, priority=0)
        if (before != language or had_gifs != self.gif_on) and hasattr(self, '_start_build'):
            self._start_build()
        # GIF textures may already be cached. Visibility still follows the
        # switch immediately, rather than waiting for another catalog read.
        # v5.10.288: another poster choice re-dresses the cards on this page
        # (and the pages read ahead go: they carry the old posters)
        try:
            from ..skinui import common as _SC
            stamp = _SC.poster_gen()
            if stamp != getattr(self.app, '_poster_stamp', stamp):
                R.forget_pages()
            self.app._poster_stamp = stamp
            for row in list(getattr(self, 'rows', None) or []):
                self._dress_posters(row.tiles)
            if getattr(self, 'tiles', None):
                self._dress_posters(self.tiles)
        except Exception:
            self.app.log('poster choice:\n%s' % traceback.format_exc(), xbmc.LOGDEBUG)

    def _dress_posters(self, tiles):
        """v5.10.288: the cards' posters follow the poster choice in the
        settings now (render_metadata.poster_for, in the background; a title
        the chosen source has not answered for yet is asked). A card keeps
        the choice's stamp ('_pg'), so it is dressed once per choice."""
        from ..skinui import common as _SC
        stamp = _SC.poster_gen()
        active = _SC.poster_choice_active()
        todo = []
        for tile in list(tiles or []):
            if not isinstance(tile, dict) or tile.get('_pg') == stamp or not R.poster_layer_tile(tile):
                continue
            if active or ('source_poster' in tile and tile['source_poster'] != tile.get('poster')):
                todo.append(tile)
            else:
                tile['_pg'] = stamp     # its own poster already: nothing to do
        if not todo:
            return

        def job():
            for tile in todo:
                if self._closing or getattr(self, '_real_playing', False):
                    return              # unstamped titles are dressed when their row loads again
                try:
                    poster = R.dress_poster(tile, network=True)
                except Exception:
                    poster = ''
                tile['_pg'] = stamp
                if poster and poster != tile.get('poster'):
                    tile['poster'] = poster
                    self.update_tile(tile, art={'poster': poster, 'thumb': poster})
                    try:
                        self.refresh_hero_if(tile)
                    except Exception:
                        pass
        self.app.bg.submit(job, priority=7, key=('posters', self.key, id(todo[0])))

    def _tmdb_language(self):
        from ..meta_policy import source_appearance
        if source_appearance():
            # v5.10.302: Like Nuvio: the profile's language when Nuvio's Home
            # uses TMDb, none (the catalog's own text and art) when it does not
            from ..meta_policy import nuvio_home_prefs
            prefs = nuvio_home_prefs()
            return str(prefs.get('language') or 'en') if prefs else ''
        settings = self.tmdb_settings()
        return str((settings or {}).get('language') or '') if settings else ''

    def _tmdb_pending(self, tile):
        language = self._tmdb_language()
        return bool(language) and (tile or {}).get('_tmdb_done') != language

    def enrich(self, tile, then=None, priority=3, background=False, focus=False):
        """TMDb text, logo and art for a title. ``focus=True``: for the hero.

        A hero lookup that is still waiting when the cursor has moved on is
        dropped (v5.10.103): after a fast scroll the title the cursor rests on
        is looked up at once instead of after every title it passed.
        """
        if not tile or tile.get('kind') != 'work':
            return
        from ..meta_policy import source_appearance
        if source_appearance() and not self._tmdb_language():
            from ..ui_preferences import enabled
            if not focus or not enabled('metadata_badges'):
                return
            def supplement():
                if self._closing or self._covered or self._real_playing or not same_work(tile, self._hero_tile):
                    return
                self._supplement_badges(tile, network=True)
                self.refresh_hero_if(tile)
            self.app.bg.submit(supplement, priority=3, key=('source-badges', id(tile)))
            return
        if focus and not tile.get('_ratings_net'):
            self._queue_ratings(tile)
        language = self._tmdb_language()
        from ..auto_metadata import enabled as auto_enabled
        auto = auto_enabled()
        if tile.get('_enriched') and tile.get('_tmdb_lang', '') == language and \
                not (tile.get('_auto_retry_at') and time.time() >= tile['_auto_retry_at']):
            return
        if not auto and not language and tile.get('fanart') and tile.get('clearlogo') and tile.get('imdb_id') \
                and tile.get('poster'):
            tile['_enriched'] = True
            return
        if not auto and tile.get('plot') and tile.get('fanart') and tile.get('clearlogo') and tile.get('poster'):
            # v5.10.198: a server title with its own text and art is not
            # looked up (its server's metadata stays, meta_policy)
            from ..meta_policy import keep_server_meta
            if keep_server_meta(tile):
                tile['_enriched'] = True
                tile['_tmdb_lang'] = language
                return
        if background and self._real_playing:
            return      # a video plays behind the page: only the focused title

        def job():
            if self._closing or self._covered or self._real_playing:
                return
            if tile.get('_enriched') and tile.get('_tmdb_lang', '') == language and \
                    not (tile.get('_auto_retry_at') and time.time() >= tile['_auto_retry_at']):
                return
            if focus and not same_work(tile, self._hero_tile):
                return      # the cursor moved on; the rest waits for its turn again
            tile['_enriched'] = True
            tile['_tmdb_lang'] = language
            had_poster = bool(tile.get('poster'))
            had_logo = tile.get('clearlogo') or ''
            try:
                self._enrich_now(tile, language)
            except Exception:
                self.app.log('enrich failed:\n%s' % traceback.format_exc())
            tile['_tmdb_done'] = language
            self.refresh_hero_if(tile)
            art = {}
            if tile.get('fanart'):
                art.update({'fanart': tile['fanart'], 'landscape': tile.get('landscape') or tile['fanart'],
                            'wide': tile['fanart']})
            if tile.get('poster') and not had_poster:
                # v5.10.107: a title its source gave no poster (a server copy
                # without one) takes TMDb's
                art.update({'poster': tile['poster'], 'thumb': tile['poster']})
            if tile.get('clearlogo') and tile['clearlogo'] != had_logo:
                # v5.10.111: the tile itself shows the logo found (landscape
                # tiles draw it), not only the banner
                art.update({'clearlogo': tile['clearlogo']})
            if art:
                self.update_tile(tile, art=art)
            if then:
                try:
                    then(tile)
                except Exception:
                    pass
        lane = self.app.bg if background else self.app.fast
        lane.submit(job, priority=priority, key=('enrich', id(tile)))

    def _enrich_now(self, tile, language=''):
        from ..meta_policy import source_appearance
        if source_appearance() and language:
            # v5.10.302: Like Nuvio with Nuvio's Home on TMDb
            self._nuvio_parity_pass(tile, language)
            return
        if source_appearance():
            from ..home_art import repair_missing_poster
            repair_missing_poster(tile)
            return
        self.app.api()
        media = 'series' if tile.get('media_type') in ('series', 'episode') else 'movie'
        from ..auto_metadata import enabled as auto_enabled, enrich_tile
        if auto_enabled():
            enrich_tile(tile, language)
            return
        elif language:
            try:
                self._tmdb_localize(tile, media, language)
            except Exception:
                self.app.log('tmdb text/logo failed:\n%s' % traceback.format_exc())
        if media == 'series' and not tile.get('seasons_count'):
            self._show_status(tile)
        # v5.10.448: a backdrop that is only the poster again (a listing
        # without one fills it so) is no backdrop: the pass looks for one
        if tile.get('fanart') and tile.get('fanart') == tile.get('poster'):
            tile['fanart'] = ''
            if tile.get('landscape') == tile.get('poster'):
                tile['landscape'] = ''
        if tile.get('fanart') and tile.get('clearlogo') and tile.get('imdb_id') and tile.get('poster'):
            return
        params = R.params_of(tile.get('path'))
        canonical = params.get('canonical_id') or tile.get('imdb_id') or ''
        meta = {
            'id': canonical, 'name': tile.get('show') or tile.get('title') or '',
            'type': media, 'imdb_id': tile.get('imdb_id') or '',
            'moviedb_id': tile.get('tmdb_id') or '', 'tmdb_id': tile.get('tmdb_id') or '',
            'poster': tile.get('poster') or '', 'background': tile.get('fanart') or '',
            'logo': tile.get('clearlogo') or '', 'year': tile.get('year') or '',
        }
        try:
            from ..art import enrich_meta_art
            art = enrich_meta_art(meta, media, fallback_art={'poster': tile.get('poster') or ''}) or {}
        except Exception:
            art = {}
        fanart = R._clean_art(art.get('fanart'))
        if fanart and fanart != tile.get('poster') and not tile.get('fanart'):
            tile['fanart'] = fanart
            if not tile.get('landscape') or tile.get('landscape') == tile.get('poster'):
                tile['landscape'] = fanart
        logo = R._clean_art(art.get('clearlogo'))
        if logo and 'image.tmdb.org' in logo and logo.lower().endswith('.svg'):
            logo = logo[:-4] + '.png'       # Kodi cannot draw SVG
        if logo and not tile.get('clearlogo') and logo not in (tile.get('poster'), tile.get('landscape')):
            tile['clearlogo'] = logo
        poster = R._clean_art(art.get('poster'))
        if poster and not tile.get('poster') and poster != tile.get('fanart'):
            tile['poster'] = poster
        if not tile.get('poster'):
            from ..home_art import repair_missing_poster
            repair_missing_poster(tile)
        if not tile.get('imdb_id') and tile.get('tmdb_id'):
            try:
                from ..tmdb_direct import imdb_id_for
                imdb = imdb_id_for(tile.get('tmdb_id'), media_type=media, timeout=4) or ''
                if str(imdb).startswith('tt'):
                    tile['imdb_id'] = str(imdb)
            except Exception:
                pass

    def _nuvio_parity_pass(self, tile, language):
        """v5.10.302: a title as Nuvio's Home shows it for the signed-in
        profile (merge_home_preview's rules): TMDb's backdrop and logo replace
        the catalog's with Artwork on, its plot and genres with Basic info on,
        its name only on the Modern layout; the poster stays the catalog's."""
        from ..meta_policy import nuvio_home_prefs
        prefs = nuvio_home_prefs()
        if not prefs:
            return
        media = 'series' if tile.get('media_type') in ('series', 'episode') else 'movie'
        from ..tmdb_direct import meta_for, meta_for_cached_only
        query = dict(tmdb_id=tile.get('tmdb_id') or '', imdb_id=tile.get('imdb_id') or '',
                     media_type=media, title=tile.get('show') or tile.get('title') or '',
                     year=tile.get('year') or '', language_override=language, nuvio_exact=True)
        if not (query['tmdb_id'] or str(query['imdb_id']).startswith('tt')):
            params = R.params_of(tile.get('path'))
            canonical = str(params.get('canonical_id') or '')
            if canonical.startswith('tt'):
                query['imdb_id'] = canonical.split(':')[0]
            elif canonical.startswith('tmdb:'):
                query['tmdb_id'] = canonical.split(':')[1]
            else:
                return
        found = meta_for_cached_only(**query) or meta_for(**query) or {}
        if not found:
            return
        if not tile.get('tmdb_id') and found.get('tmdb_id'):
            tile['tmdb_id'] = str(found['tmdb_id'])
        if not tile.get('imdb_id') and str(found.get('imdb_id') or '').startswith('tt'):
            tile['imdb_id'] = str(found['imdb_id'])
        episode = tile.get('media_type') == 'episode'
        if prefs.get('use_basic_info', True) and not episode:
            overview = str(found.get('overview') or found.get('description') or '').strip()
            if overview:
                tile['plot'] = overview
            genres = [str(g) for g in (found.get('genres') or []) if g]
            if genres:
                tile['genres'] = genres
            if prefs.get('localized_title_on_home'):
                name = str(found.get('title') or found.get('name') or '').strip()
                if name:
                    tile['title'] = name
        if prefs.get('use_details', True) and not episode:
            try:
                runtime = int(found.get('runtime') or 0)
            except Exception:
                runtime = 0
            if runtime:
                tile['duration'] = runtime * 60
        if prefs.get('use_artwork', True):
            logo = R._clean_art(found.get('clearlogo'))
            if logo and logo.lower().endswith('.svg'):
                logo = logo[:-4] + '.png'       # Kodi cannot draw SVG
            if logo:
                tile['clearlogo'] = logo
            fanart = R._clean_art(found.get('fanart'))
            if fanart:
                tile['fanart'] = fanart
        if media == 'series':
            if found.get('number_of_seasons'):
                tile['seasons_count'] = int(found['number_of_seasons'])
            if found.get('status'):
                tile['tv_status'] = str(found['status'])
        if found.get('certification') and not tile.get('mpaa') and tile.get('media_type') != 'episode':
            tile['mpaa'] = str(found['certification'])     # v5.10.322 (GitHub #20)

    def _tmdb_localize(self, tile, media, language):
        """Plot and title logo from TMDb in the chosen language (Nuvio's own rules).

        TMDb's localized details give the overview (kept only when TMDb has
        one in that language, so an untranslated title keeps its catalog
        plot) and the logo is ranked by language: that language first, then
        English, then textless. An episode keeps its own plot; its show's
        logo is still taken.
        """
        settings = self.tmdb_settings() or {}
        from ..tmdb_direct import meta_for, meta_for_cached_only
        query = dict(tmdb_id=tile.get('tmdb_id') or '', imdb_id=tile.get('imdb_id') or '',
                     media_type=media, title=tile.get('show') or tile.get('title') or '',
                     year=tile.get('year') or '', language_override=language, nuvio_exact=True)
        # v5.10.105: Dex Hub's own cache, then TMDb Helper's (a title it holds
        # in this language with its logo fills the hero at once), and only
        # then a TMDb request.
        found = meta_for_cached_only(**query) or self._helper_localized(tile, media, language)
        if not found:
            found = meta_for(**query) or {}
        if not found:
            return
        if not tile.get('tmdb_id') and found.get('tmdb_id'):
            tile['tmdb_id'] = str(found['tmdb_id'])
        if not tile.get('imdb_id') and str(found.get('imdb_id') or '').startswith('tt'):
            tile['imdb_id'] = str(found['imdb_id'])
        episode = tile.get('media_type') == 'episode'
        # v5.10.198: a server title keeps its server's own title, plot,
        # genres and logo; TMDb fills only what the server left empty
        from ..meta_policy import keep_server_meta
        keep = keep_server_meta(tile)
        if settings.get('use_basic_info', True) and not episode:
            overview = str(found.get('overview') or found.get('description') or '').strip()
            if overview and not (keep and tile.get('plot')):
                tile['plot'] = overview
            genres = [str(g) for g in (found.get('genres') or []) if g]
            if genres and not (keep and tile.get('genres')):
                tile['genres'] = genres
        if settings.get('localized_title_on_home') and not episode and not keep:
            name = str(found.get('title') or found.get('name') or '').strip()
            if name:
                tile['title'] = name
        if settings.get('use_details', True) and not tile.get('duration'):
            try:
                runtime = int(found.get('runtime') or 0)
            except Exception:
                runtime = 0
            if runtime and not episode:
                tile['duration'] = runtime * 60
        if settings.get('use_artwork', True):
            logo = R._clean_art(found.get('clearlogo'))
            if logo and logo.lower().endswith('.svg'):
                logo = logo[:-4] + '.png'       # Kodi cannot draw SVG
            if logo and logo not in (tile.get('poster'), tile.get('landscape')) and \
                    not (keep and tile.get('clearlogo')):
                tile['clearlogo'] = logo
            fanart = R._clean_art(found.get('fanart'))
            if fanart and not tile.get('fanart') and fanart != tile.get('poster'):
                tile['fanart'] = fanart
            poster = R._clean_art(found.get('poster'))
            if poster and not tile.get('poster') and tile.get('media_type') != 'episode':
                tile['poster'] = poster
        if found.get('studio_logos'):
            tile['_studio_rows'] = list(found['studio_logos'])
        if media == 'series' and not found.get('number_of_seasons'):
            # TMDb Helper's copy has no seasons or status: Dex Hub's own
            # details (its cache first) give them
            try:
                found = dict(found, **{k: v for k, v in (meta_for(**query) or {}).items()
                                       if k in ('number_of_seasons', 'status') and v})
            except Exception:
                pass
        if media == 'series':
            if found.get('number_of_seasons'):
                tile['seasons_count'] = int(found['number_of_seasons'])
            if found.get('status'):
                tile['tv_status'] = str(found['status'])
        if found.get('certification') and not tile.get('mpaa') and tile.get('media_type') != 'episode':
            tile['mpaa'] = str(found['certification'])     # v5.10.322 (GitHub #20)

    def _show_status(self, tile):
        """v5.10.228: a show's seasons and status for the Home's hero when no
        TMDb language pass gave them (Continue Watching without a TMDb
        language): TMDb's details, Dex Hub's cache first."""
        try:
            from ..tmdb_direct import meta_for, meta_for_cached_only
            query = dict(tmdb_id=tile.get('tmdb_id') or '', imdb_id=tile.get('imdb_id') or '',
                         media_type='series', title=tile.get('show') or tile.get('title') or '',
                         year=tile.get('year') or '')
            if not (query['tmdb_id'] or str(query['imdb_id']).startswith('tt')):
                params = R.params_of(tile.get('path'))
                canonical = str(params.get('canonical_id') or '')
                if canonical.startswith('tt'):
                    query['imdb_id'] = canonical.split(':')[0]
                elif canonical.startswith('tmdb:'):
                    query['tmdb_id'] = canonical.split(':')[1]
            found = meta_for_cached_only(**query) or meta_for(**query) or {}
        except Exception:
            return
        if found.get('number_of_seasons'):
            tile['seasons_count'] = int(found['number_of_seasons'])
        if found.get('status'):
            tile['tv_status'] = str(found['status'])
        if not tile.get('tmdb_id') and found.get('tmdb_id'):
            tile['tmdb_id'] = str(found['tmdb_id'])

    def _helper_localized(self, tile, media, language):
        """The hero's TMDb text and logo from TMDb Helper's cache (v5.10.105).

        Used only when TMDb Helper holds the title in this very language and
        has its logo, so the hero shows exactly what a TMDb request would
        have given, without the request.
        """
        try:
            from .. import tmdbhelper
            local = tmdbhelper.get_localized_from_db(
                tmdb_id=tile.get('tmdb_id') or '', media_type=media,
                imdb_id=tile.get('imdb_id') or '', language=language) or {}
        except Exception:
            return {}
        if not (local.get('complete') and local.get('clearlogo') and local.get('clearlogo_exact')):
            return {}
        return {
            'tmdb_id': local.get('tmdb_id') or '', 'imdb_id': local.get('imdb_id') or '',
            'title': local.get('title') or '', 'overview': local.get('plot') or '',
            'genres': list(local.get('genres') or []), 'runtime': local.get('runtime') or 0,
            'clearlogo': local.get('clearlogo') or '', 'fanart': local.get('fanart') or '',
        }

    def _supplement_badges(self, tile, network=False):
        """Add ratings/network only. Source titles, plots and artwork stay intact."""
        from ..ui_preferences import enabled
        if not enabled('metadata_badges') or tile.get('kind') != 'work':
            return
        media = 'series' if tile.get('media_type') in ('series', 'episode') else 'movie'
        self._studio_badge(tile, media)
        self._rating_badges(tile, media, network=network)

    def _rating_badges(self, tile, media, network=False):
        """Ratings with their logos for the hero (v5.10.105, ratings.py).

        Every enriched title reads TMDb Helper's cache and Dex Hub's MDbList
        memory; the title under the cursor may also ask MDbList once.
        """
        if tile.get('kind') != 'work':
            return
        if tile.get('ratings') is not None and (tile['ratings'] or not network or tile.get('_ratings_net')):
            return
        from . import ratings as RB
        found = RB.collect(tmdb_id=tile.get('tmdb_id') or '', imdb_id=tile.get('imdb_id') or '',
                           media_type=media, network=network)
        if network:
            tile['_ratings_net'] = True
        tile['ratings'] = found

    def _queue_ratings(self, tile):
        from ..ui_preferences import enabled
        if not enabled('metadata_badges'):
            return
        media = 'series' if tile.get('media_type') in ('series', 'episode') else 'movie'

        def job():
            if not same_work(tile, self._hero_tile) or self._closing or self._covered or self._real_playing:
                return      # the cursor moved on
            before = dict(tile.get('ratings') or {})
            logo_before = tile.get('studio_logo')
            try:
                self._studio_badge(tile, media)
                self._rating_badges(tile, media, network=True)
            except Exception:
                return
            if (tile.get('ratings') or {}) != before or tile.get('studio_logo') != logo_before:
                self.refresh_hero_if(tile)
        # an MDbList request may wait its whole timeout: never ahead of the
        # trailer, logo and text of the title under the cursor (fast lane)
        self.app.bg.submit(job, priority=3, key=('ratings', id(tile)))

    def _studio_badge(self, tile, media, found=None):
        """The network (series) or studio (film) logo for the hero (v5.10.105).

        TMDb Helper's cache first, then the TMDb answer the hero already has;
        an installed studio pack draws it as is, a TMDb logo sits on a light
        chip (most are dark).
        """
        found = found or {'studio_logos': tile.get('_studio_rows') or []}
        if tile.get('studio_logo') or tile.get('kind') != 'work':
            return
        if tile.get('studio_logo') is not None and not (found or {}).get('studio_logos'):
            return      # looked up already, nothing new to try
        rows = list(found.get('studio_logos') or [])[:3]
        if not rows:
            try:
                from .. import tmdbhelper
                rows = tmdbhelper.get_studio_logos_from_db(
                    tmdb_id=tile.get('tmdb_id') or '', media_type=media,
                    imdb_id=tile.get('imdb_id') or '', limit=3) or []
            except Exception:
                rows = []
        try:
            from .. import studio_art
            picked = studio_art.pick(rows, tile.get('studios') or tile.get('studio') or [], limit=1)
        except Exception:
            picked = []
        tile['studio_logo'] = picked[0][0] if picked else ''
        tile['studio_logo_chip'] = bool(picked and picked[0][1])

    # ----------------------------------------------------------- trailers
    def trailer_wanted(self, tile):
        if not tile:
            return False
        if tile.get('kind') == 'channel':
            # a channel plays behind the page like a trailer (live TV page):
            # a PVR channel, or a channel of another source with its own stream
            return (self.live_preview and not tile.get('locked') and not tile.get('nopreview')
                    and bool(tile.get('channelid') or tile.get('src')))
        if not self.trailers_on:
            return False
        if tile.get('kind') == 'folder':
            return self.s.flag('homeui_kenburns', True) and bool(tile.get('hero_video'))
        return tile.get('kind') == 'work' and bool(tile.get('imdb_id') or tile.get('tmdb_id'))

    def schedule_trailer(self, tile, delay=None):
        if self._covered or self._closing:
            return
        director = self.app.director
        if same_work(tile, self._trailer_tile) and director.active and director.owner() is self:
            return      # this title's preview is already running
        self.app.scheduler.cancel(self.key + ':trailer')
        if not self.trailer_wanted(tile) or self._real_playing:
            return
        if delay is None and tile.get('kind') == 'channel':
            delay = _CHANNEL_SETTLE
        if delay is None:
            delay = self.trailer_delay
            if tile.get('kind') == 'folder':
                delay = min(delay, 1.0)     # a collection's banner video
            # "at once" still lets the cursor settle, so scrolling through a
            # row does not start a preview on every title it passes
            delay = max(_TRAILER_SETTLE, delay)
        if tile.get('kind') == 'work' and delay > _TRAILER_LOOKUP_AFTER:
            # look the trailer up while the delay runs (v5.10.103): the start
            # then only has to open the video
            self.app.scheduler.call_later(self.key + ':trailer-pre', _TRAILER_LOOKUP_AFTER,
                                          lambda: self._prefetch_trailer(tile))
        self.app.scheduler.call_later(self.key + ':trailer', delay,
                                      lambda: self._trailer_due(tile))

    def _prefetch_trailer(self, tile):
        if (not same_work(tile, self._hero_tile) or self._real_playing or self._covered
                or self._closing or not self.trailer_wanted(tile)):
            return

        def job():
            if not same_work(tile, self._hero_tile) or self._real_playing or self._closing:
                return      # the cursor moved on while this waited
            try:
                if not tile.get('imdb_id'):
                    self._enrich_now(tile)
                imdb_id = tile.get('imdb_id')
                if imdb_id and not self.app.resolver.cached(imdb_id)[0]:
                    started = _now()
                    found = self.app.resolver.resolve(imdb_id)
                    self.trace('trailer lookup %s: %s in %d ms' % (
                        imdb_id, 'found' if found else 'none', (_now() - started) * 1000))
            except Exception:
                self.app.log('trailer lookup failed:\n%s' % traceback.format_exc())
        key = tile.get('imdb_id') or tile.get('tmdb_id') or tile.get('id') or id(tile)
        self.app.pre.submit(job, priority=1, key=('trailer-pre', self.key, key))

    def _trailer_due(self, tile, force=False):
        """Start the preview of ``tile``; ``force``: right now, for OK on a channel."""
        if not force and (not same_work(tile, self._hero_tile) or self.dialog_open()):
            return
        if not self.is_active():
            return
        if self.app.director.foreign_playback():
            return
        token = self.app.director.next_token()
        channel = tile.get('kind') == 'channel'

        def job():
            if not self.app.director.is_current(token):
                return
            stream = None
            try:
                if channel and tile.get('channelid'):
                    stream = {'url': tile.get('path'), 'channelid': tile.get('channelid'),
                              'key': tile.get('path')}
                elif channel:
                    # a channel with its own address (Stremio, subscription,
                    # M3U, Xtream): asked for now, so it is never stale
                    from . import live_sources
                    stream = live_sources.resolve(tile, self.app)
                elif tile.get('kind') == 'folder' and tile.get('hero_video'):
                    stream = {'url': tile['hero_video'], 'mime': 'video/mp4'}
                else:
                    # the test harness's own video (a local file only, as the skin's)
                    test = xbmcgui.Window(10000).getProperty('dhs.test.trailer')
                    if test and os.path.isfile(test):
                        stream = {'url': test, 'mime': 'video/mp4', 'title': 'Trailer'}
                    else:
                        if not tile.get('imdb_id'):
                            self._enrich_now(tile)
                        stream = self.app.resolver.resolve(tile.get('imdb_id'))
            except Exception:
                stream = None
            if not stream and force and self._watch_pending == tile.get('path'):
                # OK asked for this channel and it has no stream
                self._watch_pending = ''
                self.notify(self.tr('تعذر تشغيل القناة'))
                return
            if not stream or not self.app.director.is_current(token):
                return
            if not self.is_active() or (not force and not same_work(tile, self._hero_tile)):
                return
            self._trailer_tile = tile
            self.trace('trailer start: %s' % (tile.get('title') or ''))
            self.prop('dh.trailer.on', '1')
            # a channel is heard (it is the programme itself); trailers follow
            # the sound setting
            ok = self.app.director.play(self, token, stream,
                                        title=tile.get('show') or tile.get('title') or '',
                                        muted=False if channel else not self.trailer_sound,
                                        volume=None if channel else self.s.text('homeui_trailer_volume', '30'))
            if not ok:
                self._trailer_tile = None
                self.prop('dh.trailer.on', '')
                if force and self._watch_pending:
                    self._watch_pending = ''
                    self.notify(self.tr('تعذر تشغيل القناة'))
        self.app.fast.submit(job, priority=0 if force else 1, key=('trailer', self.key, token))

    def stop_trailer(self, wait=False, park=False):
        """Stop this window's preview. ``wait=True`` only right before real playback.

        ``park=True`` (a move to another title) only hides and pauses it, so
        the next title's preview can take the open player over (see
        director.py). A window that is covered or closing never touches a
        preview another window owns (or one it is about to request).
        """
        self.app.scheduler.cancel(self.key + ':trailer')
        self._watch_pending = ''
        owner = self.app.director.owner()
        if owner is self or (owner is None and self.app.top() is self):
            if park and not wait:
                # long enough for the next title's preview to take over
                settle = _CHANNEL_SETTLE if self.app.director.live else self.trailer_delay
                self.app.director.park(max(3.0, settle + 3.0))
            else:
                self.app.director.stop(wait=wait)
        self._clear_trailer_props()

    def _clear_trailer_props(self):
        self.app.scheduler.cancel(self.key + ':trailer-reveal')
        self._trailer_tile = None
        self._revealed = False
        self.prop('dh.trailer.visible', '')
        self.prop('dh.trailer.muted', '')
        self.prop('dh.trailer.on', '')
        self.prop('dh.trailer.cardactive', '')
        if getattr(self, '_stage', False):
            self._stage = False
            self.prop('dh.stage', '')
        if self._immersive:
            self.exit_immersive()

    def _card_preview(self):
        # Card previews apply to browse rows and grids, never to Live TV or
        # the title page's playback actions.
        from ..trailer_preferences import display_mode
        return (display_mode(self.s.text('homeui_trailer_mode', 'background')) == 'card'
                and getattr(self, 'mode', '') != 'live'
                and self.focus_id() in ROW_A_IDS + ROW_B_IDS + GRID_IDS)

    def on_trailer_visible(self):
        director = self.app.director
        if not director.visible or director.owner() is not self:
            return      # a late event for a preview that was already stopped
        pending = self._watch_pending
        if pending and director.preview_key() == pending:
            # OK was pressed on this channel while it opened: watch it now
            self._watch_pending = ''
            self._hand_over_channel(self._watch_tile or {'path': pending})
            return
        self.prop('dh.trailer.cardactive', '1' if self._card_preview() else '')
        self.trace('trailer visible')
        self._trailer_visible_at = _now()
        self._revealed = False
        self.prop('dh.trailer.on', '1')
        self.prop('dh.trailer.muted', '1' if director.silenced else '')
        stamp, tile, token = self._trailer_visible_at, self._trailer_tile, director.current_token()
        def reveal():
            if (self._closing or self._covered or self._trailer_visible_at != stamp
                    or not same_work(tile, self._trailer_tile) or not director.is_current(token)
                    or director.owner() is not self or not director.visible):
                return
            if not director.ready_to_reveal(settled=_now() - stamp >= 1.5):
                self.app.scheduler.call_later(self.key + ':trailer-reveal', 0.1, reveal)
                return
            self._revealed = True
            self.prop('dh.trailer.visible', '1')
        self.app.scheduler.call_later(self.key + ':trailer-reveal', 0.25, reveal)

    def on_trailer_finished(self, natural):
        self.trace('trailer finished natural=%s' % natural)
        if self._watch_pending:
            # the channel OK asked for did not open
            self._watch_pending = ''
            self.notify(self.tr('تعذر تشغيل القناة'))
        self._clear_trailer_props()
        hero = self._hero_tile
        if natural and hero and hero.get('kind') == 'folder' and hero.get('hero_video'):
            # a collection's banner video loops while its tile or page is shown
            self.schedule_trailer(hero, delay=0.2)
            return
        if natural:
            try:
                self.after_trailer_end()
            except Exception:
                pass

    def after_trailer_end(self):
        pass

    def play_trailer_fullscreen(self, tile):
        if not tile:
            return
        self.stop_trailer(wait=False)
        token = self.app.director.next_token()

        def job():
            try:
                if not tile.get('imdb_id'):
                    self._enrich_now(tile)
                stream = self.app.resolver.resolve(tile.get('imdb_id'))
            except Exception:
                stream = None
            if not stream:
                self.notify(self.tr('لا يوجد تريلر لهذا العمل'))
                return
            self.app.director.play(self, token, stream, title=tile.get('title') or '',
                                   muted=False, fullscreen=True)
        self.app.fast.submit(job, priority=0, key=('trailer-full', self.key, token))

    def notify(self, message):
        try:
            xbmcgui.Dialog().notification('Dex Hub', message, xbmcgui.NOTIFICATION_INFO, 2500,
                                          sound=False)
        except Exception:
            pass

    # ---------------------------------------------------------- immersive
    def enter_immersive(self):
        if self._immersive or not self.immersive_on:
            return
        self._focus_before_immersive = self.focus_id()
        self._immersive = True
        self.trace('immersive on (focus was %s)' % self._focus_before_immersive)
        self.prop('dh.immersive', '1')
        try:
            self.setFocusId(SINK)
        except Exception:
            pass

    def exit_immersive(self):
        if not self._immersive:
            return
        self._immersive = False
        self.prop('dh.immersive', '')
        self._stage = False
        self.prop('dh.stage', '')
        target = self._focus_before_immersive
        if target in (0, SINK) or (target in ROW_A_IDS and not self._displayed.get(target)):
            target = self.default_focus()
        self.trace('immersive off -> focus %s' % target)
        self.focus(target)

    def focus(self, control_id):
        try:
            self.setFocusId(control_id)
        except Exception:
            pass

    def default_focus(self):
        return SINK

    # ------------------------------------------------------------- ticks
    def watch_tick(self):
        """4 Hz from the app's watch thread while this window is on top."""
        if self._closing:
            return
        active = self.is_active()
        director = self.app.director
        foreign = director.foreign_playback() and self._video_playing()
        if active and not getattr(self, '_was_active', False) and foreign:
            # v5.10.108: one line per return to a page with the video behind it
            self.app.log('video playing behind %s' % type(self).__name__)
        self._was_active = active
        if foreign and not self._real_playing:
            self._real_playing = True
            self.app.scheduler.cancel(self.key + ':trailer')
            self.prop('dh.playing', '1')
            self._np_at = 0.0
            try:
                self.on_real_playback(True)
            except Exception:
                self.app.log('playback handler failed:\n%s' % traceback.format_exc())
        elif self._real_playing and not foreign:
            self._real_playing = False
            self._resume_at = _now() + 1.2
            self.prop('dh.playing', '')
            self._clear_now_playing()
            try:
                self.on_real_playback(False)
            except Exception:
                self.app.log('playback handler failed:\n%s' % traceback.format_exc())
        if self._real_playing and active:
            self.update_now_playing()
        if self._resume_at and _now() >= self._resume_at and active:
            self._resume_at = 0.0
            try:
                self.on_playback_returned()
            except Exception:
                self.app.log('return handler failed:\n%s' % traceback.format_exc())
        if not active:
            if not director.active:
                # live TV: Kodi's own full-screen setting is the user's again
                # while the page is covered (the player, Kodi's TV windows)
                try:
                    from . import live
                    live.release_windowed(self.app.profile)
                except Exception:
                    pass
            return
        self._check_progress_changed()
        showing = director.visible and director.owner() is self and not self.dialog_open()
        card_preview = self._card_preview()
        self.prop('dh.trailer.cardactive', '1' if card_preview and showing else '')
        # v5.10.176: Apple TV's way: the trailer first takes most of the
        # screen (the hero's text and the rows slide down), then full screen
        stage = (showing and not card_preview and not self._immersive and _now() - self._last_input > 2.5
                 and _now() - self._trailer_visible_at > 1.5)
        if stage != getattr(self, '_stage', False):
            self._stage = stage
            self.prop('dh.stage', '1' if stage else '')
        if (showing and not card_preview and not self._immersive
                and self.immersive_on and _now() - self._last_input > self.immersive_after
                and _now() - self._trailer_visible_at > self.immersive_after - 1):
            self.enter_immersive()
        self.tick_extra()

    def tick_extra(self):
        pass

    def on_playback_returned(self):
        pass

    def _check_progress_changed(self):
        from .. import progress_events
        revision = progress_events.revision()
        if revision and revision != self._progress_revision:
            self._progress_revision = revision
            self.on_progress_changed()
            return True
        return False

    def on_progress_changed(self):
        self.on_playback_returned()

    def on_real_playback(self, playing):
        pass

    @staticmethod
    def _video_playing():
        try:
            return bool(xbmc.Player().isPlayingVideo())
        except Exception:
            return False

    # ------------------------------------------------------- now playing
    @staticmethod
    def _playback_ctx():
        try:
            raw = xbmcgui.Window(10000).getProperty('dexhub.play_context') or ''
            ctx = json.loads(raw) if raw else {}
            return ctx if isinstance(ctx, dict) else {}
        except Exception:
            return {}

    def update_now_playing(self, force=False):
        """Title lines of the now-playing card (time and progress come from Kodi)."""
        now = _now()
        if not force and now - self._np_at < 1.0:
            return
        self._np_at = now
        label = lambda name: (xbmc.getInfoLabel(name) or '').strip()
        title = label('VideoPlayer.Title') or label('Player.Title')
        show = label('VideoPlayer.TVShowTitle')
        season, episode = label('VideoPlayer.Season'), label('VideoPlayer.Episode')
        ctx = self._playback_ctx()
        if ctx and not self._ctx_is_playing(ctx):
            ctx = {}        # the context of an earlier Dex Hub playback
        if ctx:
            # Dex Hub hands Kodi 22 a minimal item, so its own context names
            # the title when Kodi only knows the file
            looks_like_file = (bool(re.search(r'\.(mkv|mp4|m3u8|ts|avi|mov|webm|m2ts)$', title, re.I))
                               or title.startswith(('http://', 'https://', '/', 'plugin://'))
                               # an id where the name belongs (v5.10.108)
                               or bool(re.match(r'^(?:tt\d+|(?:tmdb|tvdb|kitsu|mal|anilist|anidb):\d+)(?::\d+){0,2}$',
                                                title)))
            if not title or looks_like_file:
                title = str(ctx.get('title') or '') or title
            show = show or str(ctx.get('show_title') or '')
            season = season or str(ctx.get('season') or '')
            episode = episode or str(ctx.get('episode') or '')
        title = re.sub(r'\.(mkv|mp4|m3u8|ts|avi|mov|webm|m2ts)$', '', title or '', flags=re.I)
        sub = ''
        watching = getattr(self.app, 'live_watching', None)
        if watching and not ctx:
            try:
                playing = (xbmc.Player().getPlayingFile() or '').split('|', 1)[0]
            except Exception:
                playing = ''
            if playing and playing == watching.get('url'):
                # a live channel watched from the Live TV page (not a PVR one)
                from . import live
                tile = watching.get('tile') or {}
                live.update_times(tile)
                self.prop('dh.np.title', tile.get('title') or title)
                self.prop('dh.np.sub', tile.get('hint') or tile.get('group') or '')
                self.prop('dh.np.on', '1')
                return
        channel = label('VideoPlayer.ChannelName')
        if channel and xbmc.getCondVisibility('VideoPlayer.Content(livetv)'):
            # live TV: the channel, then the programme on it
            self.prop('dh.np.title', channel)
            self.prop('dh.np.sub', title if title != channel else '')
            self.prop('dh.np.on', '1')
            return
        if show and (season or episode) and str(season) not in ('0', '-1'):
            code = episode_code({'season': season, 'episode': episode})
            name = title if title and title != show else ''
            sub = '  ·  '.join([p for p in (code, name) if p])
            title = show
        else:
            sub = label('VideoPlayer.Year')
        self.prop('dh.np.title', title or self.tr('يعمل الآن'))
        self.prop('dh.np.sub', sub)
        self.prop('dh.np.on', '1')

    @staticmethod
    def _ctx_is_playing(ctx):
        """Does Dex Hub's saved playback context describe the file playing now?"""
        url = str((ctx or {}).get('stream_url') or '')
        try:
            playing = xbmc.Player().getPlayingFile() or ''
        except Exception:
            playing = ''
        if not url or not playing:
            return False
        if playing == url or url in playing or playing in url:
            return True
        return playing.split('?', 1)[0] == url.split('?', 1)[0]

    def _clear_now_playing(self):
        for key in ('dh.np.on', 'dh.np.title', 'dh.np.sub'):
            self.prop(key, '')

    def _setup_np_buttons(self):
        if self._items.get(NP):
            return
        items, keys = [], []
        for key, label, alt in (('player', 'المشغل', ''), ('pause', 'إيقاف مؤقت', 'تشغيل'),
                                ('stop', 'إيقاف', '')):
            li = xbmcgui.ListItem(label=self.tr(label))
            li.setProperty('key', key)
            li.setProperty('icon', self.app.media_path('np_%s.png' % key))
            if alt:
                li.setProperty('label_alt', self.tr(alt))
                li.setProperty('icon_alt', self.app.media_path('np_play.png'))
            items.append(li)
            keys.append(key)
        try:
            self._set_items(NP, items, keys)
        except Exception:
            pass

    def player_action(self, key):
        if key == 'player':
            self.go_player()
        elif key == 'pause':
            xbmc.executebuiltin('PlayerControl(Play)')
        elif key == 'stop':
            xbmc.executebuiltin('PlayerControl(Stop)')

    def go_player(self, osd=True):
        """Back to the video playing behind the page, in Kodi's own player.

        The skin's player controls (its video OSD) open with it, the way the
        player looks when it is reached from the skin itself. This window
        stays in Kodi's history underneath, so leaving the player comes back
        here, never out of the add-on.
        """
        def run():
            xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
            if not osd:
                return
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not self.app.stop_event.is_set():
                if xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)'):
                    xbmc.sleep(300)
                    if xbmc.getCondVisibility('Window.IsActive(fullscreenvideo) + Player.HasVideo + '
                                              '!System.HasModalDialog'):
                        # the same action the remote's OK sends in the player,
                        # so the skin opens its controls its own way
                        xbmc.executebuiltin('Action(OSD,fullscreenvideo)')
                        xbmc.sleep(500)
                        shown = xbmc.getCondVisibility('Window.IsVisible(videoosd)')
                        self.app.log('player controls %s' % ('shown' if shown else 'not shown by the skin'))
                    return
                xbmc.sleep(60)
        thread = threading.Thread(target=run, name='DexHub-homeui-player')
        thread.daemon = True
        thread.start()

    def _mark_shown_again(self):
        """Kodi showed this page again: after a page above it closed (Kodi
        re-inits it right after on_uncover) or after the player."""
        now = _now()
        self._shown_at = now
        self._settle = _BACK_SETTLE_PAGE if now - self._uncovered_at < 1.0 else _BACK_SETTLE

    def _back_bounce(self, aid):
        """Is this Back the press that just brought the page back? (v5.10.108)

        Leaving the player with Back (or closing a page above this one) can
        deliver the same key again a moment later: a remote's repeat, CEC's
        double press, a key held a little long. Taken as a new press it closed
        the page, or sent the Home straight back to the video, so the video
        never stayed playing behind Dex Hub.
        """
        if aid not in A_BACK:
            return False
        return _now() - self._shown_at < self._settle

    def input_seen(self):
        self._user_moved = True
        self._last_input = _now()
        if getattr(self, '_stage', False):
            # v5.10.176: a key brings the page back up over the trailer
            self._stage = False
            self.prop('dh.stage', '')
        self._trailer_auto = 0
        self.app.touch_interactive()

    # -------------------------------------------------------- lifecycle
    def on_cover(self):
        self._was_active = False
        self._covered = True
        self.stop_trailer(wait=False)
        self.app.scheduler.cancel_prefix(self.key)
        cancel = getattr(self, '_cancel_row_jobs', None)
        if cancel:
            cancel()

    def on_uncover(self):
        self._covered = False
        self._last_input = _now()
        self._shown_at = self._uncovered_at = _now()
        self._settle = _BACK_SETTLE_PAGE
        tile = self._hero_tile
        if tile is not None:
            self.schedule_trailer(tile)
        load = getattr(self, '_ensure_loaded', None)
        if load:
            load(self.ri)

    def on_close(self):
        self._closing = True
        self.app.scheduler.cancel_prefix(self.key)
        cancel = getattr(self, '_cancel_row_jobs', None)
        if cancel:
            cancel()
        if self.app.director.owner() is self:
            self.app.director.stop(wait=True)
        self._release_live_hold()

    def leave(self):
        """Back with nothing left to go back to on this page (v5.10.105).

        On the first window of Dex Hub (the Home, or Live TV or Servers when
        Dex Hub was opened on them) that is leaving Dex Hub, so it is asked
        first. Settings: Ask before leaving Dex Hub.
        """
        if (getattr(self, 'mode', '') in _CONFIRM_EXIT_MODES and self.app._root() is self
                and not getattr(self.app, 'skin_page', False)
                and self.s.flag('homeui_confirm_exit', True)):
            from .options import choose
            choice = choose(self.tr('الخروج من Dex Hub؟'),
                            [self.tr('الخروج'), self.tr('البقاء في Dex Hub')], preselect=1)
            if choice != 0:
                return
        self.close_window()

    def close_window(self):
        self._closing = True
        self.app.scheduler.cancel_prefix(self.key)
        if self.app.director.owner() is self:
            self.app.director.stop(wait=True)
        self._release_live_hold()
        self.close()

    def _release_live_hold(self):
        """Kodi's full-screen setting for channels is the user's again (live TV)."""
        if self.app.director.active:
            return
        try:
            from . import live
            live.release_windowed(self.app.profile)
        except Exception:
            pass

    # ------------------------------------------------------------ actions
    def run_builtin(self, command):
        command = str(command or '').strip()
        if not command:
            return
        match = re.match(r'^Container\.Update\((.*?)(?:,\s*replace)?\)$', command, re.I)
        if match:
            command = 'ActivateWindow(Videos,%s,return)' % match.group(1)
        if command.lower().startswith(('runplugin', 'playmedia', 'activatewindow')):
            self._launch(command)
        else:
            xbmc.executebuiltin(command)

    @staticmethod
    def _arg(path):
        """Quote a path for a builtin (plugin paths may contain commas)."""
        return '"%s"' % str(path).replace('\\', '\\\\').replace('"', '\\"')

    def _launch(self, command):
        """Leave the page for real playback or another Kodi window.

        The preview has to be fully stopped first, which can take a moment on
        some boxes, so the stop and the builtin run on a short-lived thread
        and the remote never waits for the player.
        """
        self.app.scheduler.cancel(self.key + ':trailer')
        self._clear_trailer_props()
        if self.title_page() != 'tmdbhelper' and 'dh_home=1' not in command:
            command = _DEXHUB_ROUTE_RE.sub(r'\1dh_home=1&', command)

        def run():
            try:
                self.app.director.stop(wait=True)
            except Exception:
                pass
            if not self._closing:
                xbmc.executebuiltin(command)
        thread = threading.Thread(target=run, name='DexHub-homeui-launch')
        thread.daemon = True
        thread.start()

    def _with_ui_art(self, path, action, tile):
        """Hand the title's art (the logo verified here) to the player routes.

        The source search screen opens in a fresh invocation that knows
        nothing of this page, and without these it fell back to the add-on's
        own artwork.
        """
        if action not in _UI_ART_ACTIONS and action not in _SERVER_ART_ACTIONS:
            return path
        params = R.params_of(path)
        if action in _SERVER_ART_ACTIONS and params.get('ui_seed_key'):
            return path
        extra = {}
        logo = tile.get('clearlogo') or ''
        if logo and self.app.logos.state(logo) is True and not params.get('ui_clearlogo'):
            extra['ui_clearlogo'] = logo
        if tile.get('fanart') and not params.get('ui_fanart'):
            extra['ui_fanart'] = tile.get('fanart')
        if tile.get('poster') and not params.get('ui_poster') and tile.get('media_type') != 'episode':
            extra['ui_poster'] = tile.get('poster')
        if not extra:
            return path
        if action in _SERVER_ART_ACTIONS:
            try:
                from .. import cache_store
                key = cache_store.put('ui_seed', extra, ttl_hours=6)
            except Exception:
                return path
            return path + ('&' if '?' in path else '?') + urlencode({'ui_seed_key': key})
        return path + ('&' if '?' in path else '?') + urlencode(extra)

    def title_page(self):
        """Where a movie or show opens (v5.10.103): 'dexhub', 'tmdbhelper' or 'classic'."""
        value = str(self.s.text('homeui_title_page', 'Dex Hub') or '').strip().lower()
        if value.startswith('tmdb'):
            return 'tmdbhelper'
        if value.startswith('classic') or value.startswith('كلاسيك'):
            return 'classic'
        return 'dexhub'

    def tmdbhelper_path(self, tile, force=False):
        """TMDb Helper's details page for a movie or show tile, or ''."""
        tile = tile or {}
        media = tile.get('media_type') or ''
        if tile.get('kind') != 'work' or tile.get('cw') or media not in ('movie', 'series'):
            return ''
        path = tile.get('path') or ''
        if R.is_dexhub_path(path) and R.params_of(path).get('action', '') not in _HELPER_ACTIONS:
            return ''
        if not force and self.title_page() != 'tmdbhelper':
            return ''
        try:
            if not xbmc.getCondVisibility('System.HasAddon(%s)' % TMDBH_ID):
                return ''
        except Exception:
            return ''
        tmdb = str(tile.get('tmdb_id') or '').strip()
        imdb = str(tile.get('imdb_id') or '').strip()
        params = {'info': 'details', 'tmdb_type': 'movie' if media == 'movie' else 'tv'}
        if tmdb.isdigit():
            params['tmdb_id'] = tmdb
        elif imdb.startswith('tt'):
            params['imdb_id'] = imdb
        else:
            return ''
        return 'plugin://%s/?%s' % (TMDBH_ID, urlencode(params))

    def open_path(self, tile, helper=True):
        """Open a captured Dex Hub item the way its native listing would.

        ``helper``: a movie or show opens on its title page (v5.10.103), or in
        TMDb Helper when the user chose that; False plays or lists it directly.
        """
        if tile.get('kind') == 'channel':
            self.watch_channel(tile)
            return
        path = tile.get('path') or ''
        from ..catalog_navigation import folder_ref
        ref = folder_ref(R.params_of(path)) if R.is_dexhub_path(path) else None
        if ref is not None:
            self.app.open_folder(title=tile.get('title') or tile.get('label') or '', **ref)
            return
        if R.is_dexhub_path(path) and R.params_of(path).get('action') in ('home_vod_list', 'home_vod_series'):
            # (home_vod_series: an IPTV series from the search or Continue Watching, v5.10.239)
            grid = dict(R.params_of(path), action='home_vod_list')
            self.app.open_grid(grid, title=tile.get('title') or tile.get('label') or '',
                               shape='landscape' if grid.get('season') else 'poster')
            return
        if helper and self.title_page() == 'dexhub':
            from . import details
            if details.supported(tile):
                self.app.open_details(tile)
                return
        if not path:
            return
        helper = self.tmdbhelper_path(tile) if helper else ''
        if helper:
            self._launch('ActivateWindow(Videos,%s,return)' % self._arg(helper))
            return
        dexhub = R.is_dexhub_path(path)
        action = R.params_of(path).get('action', '') if dexhub else ''
        if dexhub:
            path = self._with_ui_art(path, action, tile)
        if action in R.PLAYER_ACTIONS or not tile.get('folder'):
            # Dex Hub's own play routes start the player themselves; items of
            # other add-ons resolve through Kodi, which needs PlayMedia
            self._launch('%s(%s)' % ('RunPlugin' if dexhub else 'PlayMedia', self._arg(path)))
            return
        if dexhub and action in R.SERVER_FOLDER_ACTIONS:
            # v5.10.108: a server show's seasons, then a season's episodes, as
            # the Home's own pages: Back always returns here
            self.open_server_folder(tile, path)
            return
        if not dexhub or action in R.WORK_FOLDER_ACTIONS or tile.get('kind') == 'work':
            self._launch('ActivateWindow(Videos,%s,return)' % self._arg(path))
            return
        self.app.open_grid(R.params_of(path), title=tile.get('title') or tile.get('label') or '')

    @staticmethod
    def server_folder_params(tile):
        """The route of a server show or season tile (v5.10.108), or None."""
        tile = tile or {}
        path = tile.get('path') or ''
        if tile.get('kind') != 'work' or not R.is_dexhub_path(path):
            return None
        params = R.params_of(path)
        return params if params.get('action') in R.SERVER_FOLDER_ACTIONS else None

    def _prefetch_server_if_still(self, tile):
        """A short rest on a server show reads its seasons ahead (v5.10.108):
        opening it then takes them from memory, like a collection's rows."""
        if self._covered or self._closing or self._real_playing:
            return
        current = getattr(self, '_current_tile', None)
        if current is None or current() is not tile:
            return
        params = self.server_folder_params(tile)
        if not params or R.known_page(params):
            return

        def job():
            if self._closing or self._real_playing or self._covered or current() is not tile:
                return      # opened already, or the cursor moved on
            try:
                tiles, more = R.capture_tiles(params, self.app.api(), 'poster',
                                              works_only=False, limit=200)
            except Exception:
                return
            if tiles:
                R.remember_page(params, tiles, more)
                self.prefetch_logos(tiles[:6])
        self.app.bg.submit(job, priority=7, key=('server-pre', R.page_memo_key(params)))

    def open_server_folder(self, tile, path):
        """A server show or season in the Home's grid (v5.10.108)."""
        self.stop_trailer(wait=False, park=True)
        title = tile.get('title') or tile.get('label') or ''
        show = tile.get('show') or ''
        if not show and tile.get('season_card') and getattr(self, '_episode_names', False):
            show = (getattr(self, 'title', '') or '').split('  •  ')[0]     # the show's own page
        if show and title and show != title:
            title = '%s  •  %s' % (show, title)
        hero = dict(tile)
        hero.pop('_src_li', None)
        params = R.params_of(path)
        # a season lists episodes: wide stills, like Continue Watching
        shape = 'landscape' if (tile.get('season_card') or params.get('action') == 'silo_episodes') else 'poster'
        self.app.open_grid(params, title=title, shape=shape, page_tile={'hero': hero})

    def show_info(self, tile):
        li = (tile or {}).get('_src_li')
        if li is None:
            return
        self.stop_trailer(wait=False)
        # v5.10.157: the skin's own page (Arctic Fuse 3's) draws over this page
        # with see-through parts: the page hides while it is open
        self.prop('dh.info.cover', '1')
        try:
            from ..skinui import common as SC
            SC.show_info(li)
        except Exception:
            pass

    def context_menu(self, tile):
        entries = []
        if tile and tile.get('kind') == 'channel':
            from . import live_favorites as LF
            favorite = LF.key(tile) in LF.keys(self.app.profile)
            entries = [(self.tr('مشاهدة القناة'), '__channel_watch__'),
                       (self.tr('إزالة من مفضلة القنوات') if favorite else self.tr('أضف لمفضلة القنوات'), '__channel_favorite__'),
                       (self.tr('دليل البرامج'), '__channel_guide__'),
                       (self.tr('تحديث القنوات'), '__live_refresh__'),
                       (self.tr('إعداد IPTV'), '__live_setup__')]
        elif tile and tile.get('kind') == 'library':
            entries.append((self.tr('أضف للصفحة الرئيسية'), '__add_home__'))
        elif tile and tile.get('kind') not in ('more', 'action'):
            if tile.get('kind') == 'work':
                from . import details
                page = self.title_page()
                if page != 'dexhub' and details.supported(tile):
                    entries.append((self.tr('صفحة العمل'), '__details__'))
                if page != 'classic' and (details.supported(tile) or self.tmdbhelper_path(tile)):
                    # the click opens a page; playing or listing it stays one step away
                    entries.append((self.tr('تشغيل مباشر') if tile.get('media_type') == 'movie'
                                    else self.tr('فتح في Dex Hub'), '__dexhub_open__'))
                from ..play_with import from_tile, command as player_command
                original_menu = tile.get('menu') or []
                if not any('action=choose_player' in str(item[1]) for item in original_menu if len(item) > 1):
                    entries.append((self.tr('التشغيل باستخدام…'), player_command(from_tile(tile))))
                if tile.get('_src_li') is not None and not details.supported(tile):
                    entries.append((self.tr('التفاصيل'), '__info__'))
                if tile.get('imdb_id') or tile.get('tmdb_id'):
                    entries.append((self.tr('مشاهدة التريلر'), '__trailer__'))
            for item in tile.get('menu') or []:
                try:
                    entries.append((item[0], item[1]))
                except Exception:
                    continue
        from ..context_menu import clean
        entries = [(self.tr(label), cmd) for label, cmd in clean(entries)]
        row_entries = [(self.tr(label), cmd) for label, cmd in clean(self.row_menu())]
        if row_entries:
            entries.append((self.tr('خيارات الصف…'), '__row_options__'))
        if not entries:
            return
        from .options import choose
        heading, subtitle = self._menu_heading(tile)
        while True:
            choice = choose(heading, [e[0] for e in entries], subtitle=subtitle)
            if not 0 <= choice < len(entries): return
            command = entries[choice][1]
            if command != '__row_options__': break
            row_choice = choose(self.tr('خيارات الصف…'), [e[0] for e in row_entries], subtitle=heading)
            if 0 <= row_choice < len(row_entries):
                command = row_entries[row_choice][1]
                break
            # Back from row tools returns to the title menu.
        if command == '__channel_watch__':
            self.watch_channel(tile)
        elif command == '__channel_favorite__':
            from . import live_favorites as LF
            try:
                added = LF.toggle(self.app.profile, tile)
            except OSError:
                self.notify(self.tr('تعذر حفظ مفضلة القنوات'))
                return
            self._favorite_keys = LF.keys(self.app.profile)
            self.update_tile(tile, props={'favorite': '1' if added else ''})
            self.notify(self.tr('أضيفت القناة للمفضلة') if added else self.tr('أزيلت القناة من المفضلة'))
            if getattr(self, 'mode', '') == 'live':
                self.live_refresh()
        elif command == '__channel_guide__':
            self.channel_guide(tile)
        elif command == '__live_refresh__':
            self.live_refresh()
        elif command == '__live_setup__':
            self.live_setup_menu()
        elif command == '__add_home__':
            self._add_library_to_home(tile)
        elif command == '__details__':
            self.app.open_details(tile)
        elif command == '__helper__':
            self._launch('ActivateWindow(Videos,%s,return)' % self._arg(self.tmdbhelper_path(tile, force=True)))
        elif command == '__dexhub_open__':
            self.open_path(tile, helper=False)
        elif command == '__info__':
            self.show_info(tile)
        elif command == '__trailer__':
            self.play_trailer_fullscreen(tile)
        elif command.startswith('__row_'):
            self.row_command(command)
        elif any('action=' + action in command for action in
                 ('cw_remove', 'cw_mark_watched', 'nextup_remove', 'iptv_cw_remove', 'iptv_cw_watched')):
            self.cw_remove(tile, command)
        else:
            self.run_builtin(command)

    def _discard_progress_tile(self, tile):
        """Hide the chosen card immediately; persisted filtering follows."""
        path = (tile or {}).get('path') or ''
        touched = []
        with self._lock:
            for row in getattr(self, 'rows', None) or []:
                tiles = list(row.tiles or [])
                kept = [t for t in tiles if not (t is tile or (path and t.get('path') == path))]
                if len(kept) != len(tiles):
                    row.tiles = kept
                    row.selected = min(row.selected, max(0, len(kept) - 1))
                    row.state = 'ready' if kept else 'empty'
                    row.version += 1
                    row.meta['choice_changed'] = True
                    row.meta['_choice_epoch'] = int(row.meta.get('_choice_epoch') or 0) + 1
                    touched.append(row)
        loaded = getattr(self, '_row_loaded', None)
        for row in touched:
            if loaded:
                loaded(row)

    def cw_remove(self, tile, command):
        """Apply Remove/Watched here; a Container.Refresh cannot redraw WindowXML."""
        from urllib.parse import parse_qsl, urlsplit
        match = re.search(r'plugin://[^)"\s]+', str(command or ''))
        params = dict(parse_qsl(urlsplit(match.group(0)).query)) if match else {}
        action = params.get('action')
        if action not in ('cw_remove', 'cw_mark_watched', 'nextup_remove', 'iptv_cw_remove', 'iptv_cw_watched'):
            return
        self._discard_progress_tile(tile)

        def job():
            ok = False
            try:
                api = self.app.api()
                if action.startswith('iptv_'):
                    ok = api.iptv_cw_change(video_id=params.get('video_id', ''),
                                            watched=action == 'iptv_cw_watched', refresh=False)
                else:
                    extra = {key:params.get(key) for key in ('season','episode','title','imdb_id','show_tmdb_id')} if action in ('cw_mark_watched','nextup_remove') else {}
                    ok = getattr(api, action)(media_type=params.get('media_type', ''),
                                               canonical_id=params.get('canonical_id', ''),
                                               video_id=params.get('video_id', ''), refresh=False, **extra)
            except Exception:
                self.app.log('progress edit failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            if not ok and not self._closing:
                self.on_progress_changed()  # Restore the saved state if the write failed.
        from .. import profile_scope
        thread = threading.Thread(target=profile_scope.capture(job), name='DexHub-homeui-progress-edit')
        thread.daemon = True
        thread.start()

    def row_menu(self):
        """Entries about the focused row itself (the Home adds its own)."""
        return []

    def _menu_heading(self, tile):
        """(title, subtitle) of the options menu of ``tile``."""
        tile = tile or {}
        title = tile.get('title') or tile.get('label') or ''
        if tile.get('media_type') == 'episode' and tile.get('show'):
            parts = [episode_code(tile), tile.get('episode_name') or '']
            return tile['show'], '  ·  '.join([p for p in parts if p])
        kind = tile.get('kind')
        if kind == 'library':
            return title, '%s  •  %s' % (tile.get('brand') or '', tile.get('server') or '')
        if kind == 'channel':
            parts = [self.tr('القناة %s') % tile['number'] if tile.get('number') else '',
                     tile.get('group') or '']
            return title, '  ·  '.join([p for p in parts if p])
        if kind == 'work':
            media = tile.get('media_type') or ''
            parts = [self.tr(_WORK_TYPES[media]) if media in _WORK_TYPES else '', str(tile.get('year') or '')]
            return title, '  ·  '.join([p for p in parts if p])
        return title or self.tr('خيارات'), ''

    def row_command(self, command):
        pass

    # ------------------------------------------------------------ live TV
    def watch_channel(self, tile):
        """OK on a channel: watch it full screen.

        Its preview, when it already plays behind the page, simply carries on
        in Kodi's player; otherwise the channel starts now and the player
        opens as soon as it shows. A channel is known by its tile's path
        (PVR channels and channels with their own stream alike).
        """
        tile = tile or {}
        key = tile.get('path') or ''
        if not key or not (tile.get('channelid') or tile.get('src')):
            return
        director = self.app.director
        self.app.scheduler.cancel(self.key + ':trailer')
        if tile.get('nopreview'):
            self.stop_trailer(wait=False)
            self._switch_channel(tile)
            return
        if director.owner() is self and director.preview_key() == key:
            if director.visible:
                self._hand_over_channel(tile)
                return
            # on_trailer_visible hands it over once it shows; a parked one is
            # brought back first
            self._watch_pending, self._watch_tile = key, tile
            if not director.opening:
                self._trailer_due(tile, force=True)
            return
        if self._real_playing or director.foreign_playback():
            self._switch_channel(tile)
            return
        self._watch_pending, self._watch_tile = key, tile
        self._trailer_due(tile, force=True)

    def _hand_over_channel(self, tile):
        playing = ''
        if tile.get('src'):
            try:
                playing = (xbmc.Player().getPlayingFile() or '').split('|', 1)[0]
            except Exception:
                playing = ''
        self.app.director.adopt()
        self._clear_trailer_props()
        self.go_player(osd=False)
        self.channel_watched(tile)
        if playing:
            self.app.live_watching = {'url': playing, 'tile': tile}

    def channel_watched(self, tile):
        """Remember a channel the user watched; its source shows it first."""
        try:
            from . import live_sources
            live_sources.remember(self.app.profile, tile or {})
        except Exception:
            pass

    def _switch_channel(self, tile):
        """Another channel while the user's own playback runs: switch, then full screen."""
        from . import live
        app = self.app
        cid = int(tile.get('channelid') or 0)
        if cid and live.playing_channel() == cid:
            self.go_player(osd=False)
            self.channel_watched(tile)
            return

        def run():
            if cid:
                live.hold_windowed(app.profile)
                if not live.open_channel(cid):
                    self.notify(self.tr('تعذر تشغيل القناة'))
                    return
                deadline = time.monotonic() + 20.0
                while time.monotonic() < deadline and not app.stop_event.is_set():
                    if live.playing_channel() == cid:
                        xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
                        self.channel_watched(tile)
                        return
                    xbmc.sleep(100)
                self.notify(self.tr('تعذر تشغيل القناة'))
                return
            from . import live_sources
            stream = live_sources.resolve(tile, app)
            if not stream:
                self.notify(self.tr('تعذر تشغيل القناة'))
                return
            item = xbmcgui.ListItem(label=tile.get('title') or '', path=stream['url'])
            try:
                if stream.get('mime'):
                    item.setMimeType(stream['mime'])
                    item.setContentLookup(False)
                for name, value in (stream.get('props') or {}).items():
                    item.setProperty(name, value)
                if tile.get('logo'):
                    item.setArt({'icon': tile['logo'], 'thumb': tile['logo']})
                item.getVideoInfoTag().setTitle(tile.get('title') or '')
            except Exception:
                pass
            from .player_handoff import play_video
            play_video(stream['url'], item)
            self.channel_watched(tile)
            app.live_watching = {'url': stream['url'].split('|', 1)[0], 'tile': tile}
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and not app.stop_event.is_set():
                try:
                    if xbmc.Player().isPlayingVideo():
                        xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
                        return
                except Exception:
                    pass
                xbmc.sleep(150)
        thread = threading.Thread(target=run, name='DexHub-homeui-channel')
        thread.daemon = True
        thread.start()

    def fill_epg(self, tiles):
        """Now and next of channels whose source has a guide (DexWorld, Xtream)."""
        from ..ui_preferences import enabled
        if not enabled('iptv_epg_enabled'):
            return
        from . import live_sources
        try:
            changed = live_sources.fill_epg(tiles, self.app, limit=48)
        except Exception:
            self.app.log('guide lookup failed:\n%s' % live_sources.clean_trace(traceback.format_exc()))
            changed = []
        for tile in changed:
            self.update_tile(tile, props={'subtitle': tile.get('hint') or ''},
                             progress=tile.get('progress') or 0)
        hero = self._hero_tile
        if hero is not None and any(hero is tile for tile in tiles or []):
            self.refresh_hero_if(hero)

    def channel_epg_soon(self, tile):
        """The focused channel's now and next, when its row has not brought them."""
        from ..ui_preferences import enabled
        if not enabled('iptv_epg_enabled'):
            return
        if (not tile or tile.get('kind') != 'channel' or not tile.get('src') or tile.get('now')
                or tile.get('_epg_done') or tile.get('_epg_asked')):
            return
        epg = tile.get('epg') or {}
        if not (epg.get('dw') or epg.get('xt')):
            return
        tile['_epg_asked'] = True
        self.app.bg.submit(lambda: self.fill_epg([tile]), priority=2,
                           key=('epg1', self.key, tile.get('path')))

    def channel_guide(self, tile):
        """The channel's programmes from now on; one of them shows its story."""
        from ..ui_preferences import enabled
        if not enabled('iptv_epg_enabled'):
            self.notify(self.tr('دليل البرامج EPG متوقف في الإعدادات'))
            return
        from . import live
        from .options import choose
        tile = tile or {}
        if tile.get('src'):
            from . import live_sources
            xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
            try:
                items = live_sources.guide(tile, self.app)
            finally:
                xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
        else:
            items = live.guide(tile.get('channelid') or 0)
        if not items:
            self.notify(self.tr('لا يوجد دليل برامج لهذه القناة'))
            return
        now = time.time()
        today = time.localtime(now)[:3]
        tomorrow = time.localtime(now + 86400)[:3]
        options, spans = [], []
        current = -1
        for index, programme in enumerate(items):
            start, end = programme.get('start') or 0, programme.get('end') or 0
            span = '%s - %s' % (live.clock(start), live.clock(end))
            day = time.localtime(start)[:3] if start else today
            if start <= now < end:
                when = self.tr('الآن حتى %s') % live.clock(end)
                span = '%s  ·  %s' % (self.tr('الآن'), span)
                current = index
            elif day == tomorrow:
                when = '%s %s' % (self.tr('غداً'), live.clock(start))
                span = '%s  ·  %s' % (self.tr('غداً'), span)
            elif day != today:
                stamp = time.strftime('%d/%m', time.localtime(start))
                when = '%s %s' % (stamp, live.clock(start))
                span = '%s  ·  %s' % (stamp, span)
            else:
                when = span
            options.append((programme.get('title') or '', when))
            spans.append(span)
        while True:
            choice = choose(tile.get('title') or '', options, preselect=max(0, current),
                            subtitle=self.tr('دليل البرامج'))
            if choice < 0:
                return
            programme = items[choice]
            lines = [spans[choice]]
            if programme.get('episode'):
                lines.append(programme['episode'])
            if programme.get('genre'):
                lines.append(programme['genre'])
            lines.append('')
            lines.append(programme.get('plot') or self.tr('لا يوجد وصف لهذا البرنامج'))
            xbmcgui.Dialog().textviewer(programme.get('title') or '', '\n'.join(lines))

    def live_refresh(self):
        pass

    def live_setup_menu(self):
        from .options import choose
        options = [('m3u', self.tr('قائمة M3U')), ('xtream', self.tr('اشتراك Xtream Codes')),
                   ('dexworld', self.tr('اشتراك DexWorld')), ('stremio', self.tr('إضافات Stremio')),
                   ('pvr', self.tr('عبر Kodi PVR (IPTV Simple)'))]
        choice = choose(self.tr('إعداد IPTV'), [label for _key, label in options])
        if choice >= 0:
            self.live_action(options[choice][0])

    def _modal(self, work):
        """Run a setup dialog flow over this page (trailer off, page covered meanwhile)."""
        self.stop_trailer(wait=False)
        self.on_cover()
        try:
            return work()
        except Exception:
            from . import live_sources
            self.app.log('live setup failed:\n%s' % live_sources.clean_trace(traceback.format_exc()),
                         xbmc.LOGWARNING)
            return None
        finally:
            self._covered = False
            self._last_input = _now()

    def live_action(self, what):
        """The live TV setup actions; the Live TV page rebuilds its rows after them."""
        from . import live
        if what in ('m3u', 'xtream', 'dexworld'):
            from . import live_iptv, live_stremio
            work = {'m3u': lambda: live_iptv.add_m3u(self.app),
                    'xtream': lambda: live_iptv.add_xtream(self.app),
                    'dexworld': lambda: live_stremio.link(self.app)}[what]
            key = self._modal(work)
            if key:
                self.live_source_added(key)
            elif self._hero_tile is not None:
                self.schedule_trailer(self._hero_tile)
        elif what == 'stremio':
            from . import live_stremio
            key = self._modal(lambda: live_stremio.add_addon(self.app))
            if key:
                self.live_source_added(key)
            elif self._hero_tile is not None:
                self.schedule_trailer(self._hero_tile)
        elif what == 'pvr':
            from .options import choose
            options = [('pvr_m3u', self.tr('قائمة M3U في IPTV Simple')),
                       ('pvr_xtream', self.tr('اشتراك Xtream في IPTV Simple')),
                       ('simple', self.tr('إعدادات IPTV Simple')),
                       ('pvrsettings', self.tr('إعدادات التلفاز في Kodi'))]
            choice = choose(self.tr('Kodi PVR'), [label for _key, label in options],
                            subtitle=self.tr('القنوات عبر IPTV Simple في Kodi'))
            if choice >= 0:
                self.live_action(options[choice][0])
        elif what in ('pvr_m3u', 'pvr_xtream', 'simple'):
            before = live.channel_count()

            def work():
                if what == 'simple':
                    if live.ensure_simple():
                        xbmc.executebuiltin('Addon.OpenSettings(%s)' % live.IPTV_SIMPLE, True)
                        return True
                    xbmcgui.Dialog().ok('Dex Hub', self.tr('تعذر تثبيت IPTV Simple. ثبّته من مستودع Kodi ثم حاول مرة أخرى.'))
                    return False
                return live.configure(self.tr, 'xtream' if what == 'pvr_xtream' else 'm3u')
            if self._modal(work):
                self._live_after_setup(before, quiet=what == 'simple')
            elif self._hero_tile is not None:
                self.schedule_trailer(self._hero_tile)
        elif what == 'pvrsettings':
            self._launch('ActivateWindow(pvrsettings)')
        elif what.startswith('src:'):
            self.manage_live_source(what[4:])
        elif what == 'refresh':
            self.live_refresh()

    def live_source_added(self, key):
        """A new live source: the Live TV page opens its tab (other pages only say so)."""
        pass

    def manage_live_source(self, key):
        """Open, refresh, rename or remove a live source (its card on the Sources tab)."""
        from . import live_sources as LS
        from .options import choose
        known = getattr(self, '_live_sources', None) or []
        sources = [s for s in known if s['key'] == key] or \
            [s for s in LS.sources(self.app) if s['key'] == key]
        if not sources:
            return
        source = sources[0]
        kind = source['kind']
        options = [('open', self.tr('عرض القنوات'))]
        if kind in ('m3u', 'xtream'):
            options += [('refresh', self.tr('تحديث الآن')), ('rename', self.tr('تغيير الاسم')),
                        ('remove', self.tr('حذف من البث المباشر'))]
        elif key == 'dw':
            options += [('unlink', self.tr('فصل الاشتراك'))]
        elif kind == 'pvr':
            options += [('pvr', self.tr('إعداد Kodi PVR'))]
        choice = choose(source['label'], [label for _key, label in options], subtitle=source.get('hint') or '')
        if choice < 0:
            return
        action = options[choice][0]
        if action == 'open':
            self.live_source_added(key)
        elif action == 'pvr':
            self.live_action('pvr')
        elif action == 'refresh':
            from . import live_iptv
            conf = source.get('conf') or {}
            changed = self._live_changed_callback()

            def work():
                try:
                    live_iptv.refresh(self.app, conf, changed)
                except Exception as exc:
                    xbmcgui.Dialog().ok('Dex Hub', '%s\n%s' % (self.tr('تعذر تحديث المصدر'),
                                                                self.tr(LS.safe(exc))))
            try:
                self._modal(work)
            finally:
                self.live_refresh()
        elif action == 'rename':
            conf = dict(source.get('conf') or {})
            name = (xbmcgui.Dialog().input(self.tr('اسم يظهر في شريط المصادر'),
                                           defaultt=conf.get('name') or '') or '').strip()
            if name and conf.get('id'):
                conf['name'] = name
                LS.save_source(self.app.profile, conf)
                self.live_refresh()
        elif action == 'remove':
            if xbmcgui.Dialog().yesno('Dex Hub', self.tr('حذف "%s" من البث المباشر؟') % source['label']):
                LS.remove_source(self.app.profile, (source.get('conf') or {}).get('id') or '')
                self.notify(self.tr('حُذف المصدر'))
                self.live_refresh()
        elif action == 'unlink':
            # v5.10.110: only Live TV lets the subscription go; its key stays
            # saved for the rest of Dex Hub
            if xbmcgui.Dialog().yesno('Dex Hub', self.tr('فصل اشتراك DexWorld عن البث المباشر؟ يبقى مفتاحك محفوظاً في Dex Hub.')):
                from . import live_stremio
                if live_stremio.unlink():
                    self.notify(self.tr('فُصل الاشتراك'))
                else:
                    self.notify(self.tr('تعذر فصل الاشتراك'))
                self.live_refresh()

    def _live_changed_callback(self):
        return None

    def _live_after_setup(self, before, quiet=False):
        """Wait (in the background) for the PVR to list the new channels, then reload."""
        from . import live
        app = self.app

        def job():
            bar = None
            if not quiet:
                bar = xbmcgui.DialogProgressBG()
                bar.create('Dex Hub', self.tr('جاري تحميل القنوات ودليل البرامج…'))
            count = 0
            try:
                # the client restarts: give it a moment before counting
                deadline = time.monotonic() + (20.0 if quiet else 90.0)
                monitor = xbmc.Monitor()
                if not monitor.waitForAbort(3.0):
                    while time.monotonic() < deadline and not app.stop_event.is_set():
                        count = live.channel_count()
                        if count and count != before:
                            break
                        if monitor.waitForAbort(1.0):
                            break
            finally:
                if bar is not None:
                    bar.close()
            if self._closing:
                return
            if count and count != before:
                self.notify(self.tr('القنوات جاهزة: %d قناة') % count)
            elif not quiet and not count:
                self.notify(self.tr('لم تظهر القنوات بعد. تأكد من الرابط أو حاول لاحقاً'))
            self.live_refresh()
        app.workers.submit(job, priority=0, key=('live-setup', self.key))

    # ------------------------------------------------------------ tiles
    def make_listitem(self, tile):
        """A fresh ListItem for ``tile`` (never shared between lists)."""
        label = tile.get('title') or tile.get('label') or ''
        own_show = getattr(self, '_episode_names', False)
        if tile.get('media_type') == 'episode' and tile.get('show') and not own_show:
            label = tile['show']
        li = xbmcgui.ListItem(label=label, label2=tile.get('show') or '')
        poster = tile.get('poster') or tile.get('landscape') or ''
        landscape = R.card_landscape(tile)
        art = {'poster': poster, 'thumb': poster, 'landscape': landscape,
               'fanart': tile.get('fanart') or landscape,
               # v5.10.173: the big banner card (the show's backdrop, its logo)
               'wide': tile.get('fanart') or landscape,
               'clearlogo': tile.get('clearlogo') or ''}
        try:
            li.setArt(art)
        except Exception:
            pass
        if tile.get('kind') == 'channel':
            # the channel logo sits on the card, whole (the skin keeps its shape)
            try:
                li.setArt({'icon': tile.get('logo') or ''})
            except Exception:
                pass
        props = {
            'kind': tile.get('kind') or '',
            'hide_title': '1' if tile.get('hide_title') else '',
            # v5.10.108: a season's card names the season (seasons often share
            # their show's poster)
            'show_title': '1' if tile.get('show_title') or (
                tile.get('season_card') and not tile.get('season_as_show')) else '',
            'initials': R.initials(label),
            'gif': tile.get('gif') or '',
            'subtitle': '',
            'number': tile.get('number') or '',
            # v5.10.167: the card's name under it (the Add-ons page, as skin.dexhub's)
            'under': '1' if tile.get('under') else '',
        }
        if tile.get('kind') == 'channel':
            from . import live_favorites as LF
            if not hasattr(self, '_favorite_keys'):
                self._favorite_keys = LF.keys(self.app.profile)
            props['favorite'] = '1' if LF.key(tile) in self._favorite_keys else ''
            props['live'] = self.tr('مباشر')
        if tile.get('media_type') == 'episode' and own_show:
            # a season's own page (v5.10.108): the episode's name over its number
            props['subtitle'] = episode_code(tile)
        elif tile.get('media_type') == 'episode' and (tile.get('season') or tile.get('episode')):
            parts = [episode_code(tile), R.card_episode_name(tile)]
            props['subtitle'] = '  ·  '.join([p for p in parts if p])
        elif tile.get('hint'):
            props['subtitle'] = tile.get('hint')
        elif tile.get('cw') and tile.get('label2'):
            props['subtitle'] = tile.get('label2')
        # v5.10.172: the line under the title (cards with their title under the
        # picture) and Continue Watching's time on the picture
        props['below2'] = props['subtitle'] or str(tile.get('year') or '')
        try:
            pct = float(tile.get('progress') or 0)
            length = float(tile.get('duration') or 0)
        except (TypeError, ValueError):
            pct, length = 0.0, 0.0
        if 0 < pct < 100 and length > 60:
            left = max(1, int(round(length * (100.0 - pct) / 100.0 / 60.0)))
            seen = int(length * pct / 100.0)
            clock = ('%d:%02d:%02d' % (seen // 3600, seen % 3600 // 60, seen % 60) if seen >= 3600
                     else '%d:%02d' % (seen // 60, seen % 60))
            props['cw_left'] = self.tr('شاهدت %s  ·  باقي %d د') % (clock, left)
        props['wide_meta'] = self._wide_meta(tile, pct, length)
        props['plot'] = str(tile.get('plot') or '')[:400]
        if tile.get('collage') is not None:
            for index, poster in enumerate(tile['collage'][:20]):
                props['p%d' % (index + 1)] = poster or ''
            props['collage_line'] = tile.get('collage_line') or ''
            props['collage_button'] = tile.get('collage_button') or ''
            props['cstyle'] = tile.get('cstyle') or 'collage'
            props.update(tile.get('collection_preview') or {})
        if tile.get('catalog_node') and not tile.get('catalog_banner'):
            props['dhs.catalog'] = '1'
            props['dhs.catalog_icon'] = tile.get('catalog_icon') or ''
        for key, value in props.items():
            if value:
                li.setProperty(key, value)
        progress = int(tile.get('progress') or 0)
        if progress:
            try:
                li.getVideoInfoTag().setResumePoint(float(progress), 100.0)
            except Exception:
                pass
        return li

    def _wide_meta(self, tile, pct=0.0, length=0.0):
        """The line on a big banner card: S2:E1 · 33 min left, or year · genre."""
        parts = []
        if tile.get('media_type') == 'episode':
            parts.append(episode_code(tile))
            name = R.card_episode_name(tile)
            if name:
                # v5.10.214: the name fills the line; the bar shows what is left
                parts.append(name)
                return '  ·  '.join([p for p in parts if p])
        if 0 < pct < 100 and length > 60:
            left = max(1, int(round(length * (100.0 - pct) / 100.0 / 60.0)))
            parts.append(self.tr('باقي %d د') % left)
        elif tile.get('kind') == 'work':
            genres = tile.get('genres') or []
            if isinstance(genres, str):
                genres = [genres]
            parts.extend([str(tile.get('year') or ''), str(genres[0]) if genres else ''])
        elif tile.get('hint'):
            parts.append(tile.get('hint'))
        return '  ·  '.join([p for p in parts if p])

    def fill_list(self, control_id, tiles, select=None):
        try:
            self._control(control_id)
        except Exception:
            return None
        # fresh items are nobody else's yet: built before taking the lock
        items = [self.make_listitem(t) for t in tiles]
        with self._lock:
            ctrl = self._set_items(control_id, items)
            pos = 0
            if items:
                pos = max(0, min(int(select or 0), len(items) - 1))
                if pos:
                    try:
                        ctrl.selectItem(pos)
                    except Exception:
                        pass
            self._displayed[control_id] = list(tiles)
            # Kodi applies reset/add/select on its next frame; until then the
            # control still reports its old position
            self._expect[control_id] = (pos, _now())
        return ctrl

    def append_list(self, control_id, tiles):
        """Add tiles at the end of a list that already shows ``_displayed``."""
        items = [self.make_listitem(t) for t in tiles]
        if not items:
            return
        with self._lock:
            ctrl = self._control(control_id)
            pos = max(0, self.selected_pos(control_id, 0))
            # addItems rebinds the whole list and selects the first item
            ctrl.addItems(items)
            if pos:
                ctrl.selectItem(pos)
            self._items.setdefault(control_id, []).extend(items)
            self._displayed.setdefault(control_id, []).extend(tiles)
            self._expect[control_id] = (pos, _now())

    def selected_pos(self, control_id, fallback=0):
        """The list's selected position, trusting a just-queued selection."""
        expected = self._expect.get(control_id)
        if expected is not None and _now() - expected[1] < 0.4:
            return expected[0]
        try:
            with self._lock:
                return int(self._control(control_id).getSelectedPosition())
        except Exception:
            return fallback

    def clear_list(self, control_id):
        with self._lock:
            self._expect.pop(control_id, None)
            if not self._displayed.get(control_id):
                return
            try:
                self._set_items(control_id, [])
            except Exception:
                pass
            self._displayed[control_id] = []

    def update_tile(self, tile, art=None, props=None, progress=None):
        """Push a late change (focus art, backdrop, programme progress) to wherever the tile is shown."""
        targets = []
        with self._lock:
            for control_id, tiles in list(self._displayed.items()):
                items = self._items.get(control_id) or []
                for index, candidate in enumerate(tiles or []):
                    if candidate is tile and index < len(items):
                        targets.append(items[index])
        # our own references: an item a later reset took off screen stays a
        # valid object, and the change simply lands on nothing visible
        for li in targets:
            try:
                if art:
                    li.setArt(art)
                for key, value in (props or {}).items():
                    li.setProperty(key, value)
                if progress is not None:
                    li.getVideoInfoTag().setResumePoint(float(progress or 0), 100.0)
            except Exception:
                pass

    def ensure_focus_art(self, tile, priority=2, background=False):
        """Download the collection tile's focus GIF (once) and attach it."""
        if not self.gif_on or not tile or tile.get('kind') != 'folder' or self._real_playing:
            return
        url = tile.get('gif_url') or ''
        if not url or tile.get('gif') or tile.get('_gif_failed'):
            return
        local = self.app.media.local(url)
        if local:
            self._attach_gif(tile, local)
            return

        def job():
            path = self.app.media.fetch(url)
            if path:
                self._attach_gif(tile, path)
            else:
                tile['_gif_failed'] = True
        (self.app.bg if background else self.app.fast).submit(job, priority=priority, key=('gif', url))

    def _attach_gif(self, tile, path):
        tile['gif'] = path
        self.update_tile(tile, props={'gif': path})


class BrowseWindow(_BaseWindow):
    def __init__(self, *args, **kwargs):
        super(BrowseWindow, self).__init__(*args, **kwargs)
        self.mode = kwargs.get('mode') or 'home'
        self.media = kwargs.get('media') or 'all'
        self.folder_ref = kwargs.get('folder_ref') or {}
        self.page_title = kwargs.get('title') or ''
        # v5.10.102: the search page (None on every other page)
        self.search = kwargs.get('search')
        self.rows = []
        self.ri = 0
        self.zone = 'rows'
        self.carousel = []
        self.ci = 0
        self._carousel_at = 0.0
        self._a_id = ROW_A_IDS[0]
        self._b_id = ROW_B_IDS[0]
        self._btn_edge = 'left'
        self._buttons_ready = False
        self._shown = {}
        self._row_anim = 0
        self._nav_items = []
        self._page_tile = None
        self._brand_tile = None
        self._focused_once = False
        self._built = False
        self._build_gen = 0
        self._save_due = 0.0
        self.carousel_on = self.s.flag('homeui_carousel', True) and self.mode == 'home'
        # A collection folder opens on its own banner (backdrop and banner
        # video); the hero follows the tiles once the user starts moving.
        self._banner_hold = False
        self._back_count = 0
        self._back_last = 0.0
        self._exit_armed = False
        self._request = ''
        self.carousel_interval = self.s.number('homeui_carousel_interval', 10, 5, 60)
        self.kenburns = self.s.flag('homeui_kenburns', True)
        self._opened_at = _now()
        self._live_tick_at = _now()
        # v5.10.104: the tab bar of the Servers and Live TV pages (one server,
        # or one live source, at a time)
        self._tabs = []
        self._tab = ''
        self._tab_items = []
        self._tabs_sig = None
        self._tab_rows_after = False
        self._build_reason = ''
        self._server_libs = {}
        self._server_sections = {}
        self._live_sources = []
        # v5.10.131: the guide opens this page on a source's films or series;
        # its Channels button then goes back to the guide
        self._live_section = kwargs.get('live_section') or 'channels'
        self.vod_only = bool(kwargs.get('live_section'))
        self._fresh_rows = False
        self._build_scope = threading.local()

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._fresh_controls():
            return
        try:
            self._window_id = xbmcgui.getCurrentWindowId()
        except Exception:
            self._window_id = 0
        if self._inited:
            self._mark_shown_again()
            self._reactivated()
            return
        self._inited = True
        # the tabs (90) are a grouplist of buttons since 5.10.167: Python has
        # no type for a grouplist, so it is not fetched (Kodi logs an error)
        self._warm_controls((NAV_ICONS, PROFILE, HERO_BTNS, DOTS, HERO_PROGRESS, SINK, NP, TABS, 93, 94, 96, 97)
                            + ROW_A_IDS + ROW_B_IDS)
        self._theme()
        self._setup_np_buttons()
        self.set_zone('rows' if self.mode != 'home' else 'hero')
        self.prop('dh.mode', self.mode)
        self.prop('dh.page.tools', self.tr('خيارات المكتبة') if self.mode == 'library' else
                  self.tr('القنوات والمصادر') if self.mode == 'live' else self.tr('خيارات السيرفر'))
        self.prop('dh.catalog.tools', self.tr('قنوات') if self.mode == 'live' else self.tr('فلتر'))
        self.prop('dh.kenburns', '1' if self.kenburns else '')
        self.prop('dh.loading', '1')
        self.prop('dh.brand.fanart', os.path.join(self.app.addon_path, 'resources', 'media', 'fanart.jpg'))
        if self.mode in TAB_MODES:
            self._setup_nav()
        if self.mode == 'home':
            self._show_brand_hero()
            cached = self._load_cache()
            if cached:
                self._install_rows(cached, initial=True)
        else:
            self.prop('dh.page.title', self.page_title)
        self._start_build()
        if self.search is not None and not self.search:
            # the keyboard opens with the page, over its recent searches
            self.post_request('keyboard')

    def _show_brand_hero(self):
        brand = {
            'kind': 'brand', 'title': 'Dex Hub',
            'plot': self.tr('جاري تجهيز الصفحة الرئيسية…'),
            'fanart': os.path.join(self.app.addon_path, 'resources', 'media', 'fanart.jpg'),
        }
        self._brand_tile = brand
        self.show_hero(brand, animate=False)

    def _reactivated(self):
        if self.mode == 'live':
            from . import live_favorites as LF
            self._favorite_keys = LF.keys(self.app.profile)
        self._covered = False
        self._last_input = _now()
        self._theme()
        if self.mode in TAB_MODES:
            # the accent or the tab names may have changed meanwhile (the
            # icon tabs and the text tabs are different buttons)
            names = getattr(self, '_nav_with_names', False)
            self._mark_nav()
            if self.zone == 'nav' and names != self._nav_with_names:
                self.focus(self._nav_target())
        with self._lock:
            self._shown = {}
            self._render_rows()
        if self.zone == 'np' and not self._real_playing:
            self.go('hero' if (self.mode == 'home' and self.carousel) else 'rows')
        elif self.zone == 'rows' and self._row_a() is not None:
            self.focus(self.rows_focus())
        if self._hero_tile is not None:
            self.schedule_trailer(self._hero_tile)

    def set_zone(self, zone):
        self.zone = zone
        self.prop('dh.zone', zone)

    def default_focus(self):
        if self.zone == 'np' and self._real_playing:
            return NP
        if self.zone == 'hero' and self.carousel:
            return HERO_BTNS
        if self.zone == 'nav' and self.mode in TAB_MODES:
            return self._nav_target()
        if self.zone == 'tabs' and self._tabs:
            return TABS
        if self.zone == 'tools' and self.mode in ('live', 'servers'):
            return 93
        if self._row_a() is not None:
            return self.rows_focus()
        if self._tabs:
            return TABS
        return self._nav_target() if self.mode == 'home' else SINK

    def go(self, zone):
        """Move focus to a zone; the zone is published first because the hero
        buttons are hidden while the rows own the focus."""
        if zone == 'hero' and not (self.mode == 'home' and self.carousel):
            zone = 'nav' if self.mode == 'home' else 'rows'
        if zone == 'tabs' and not self._tabs:
            zone = 'rows'
        if zone == 'rows' and self._row_a() is None:
            return
        self.set_zone(zone)
        target = {'nav': self._nav_target(), 'hero': HERO_BTNS, 'tabs': TABS}.get(zone) or self.rows_focus()
        self.focus(target)
        if zone in ('hero', 'rows'):
            self._focus_changed()
        else:
            self.stop_trailer(wait=False)

    def _focus_page_tools(self):
        """Keep header focus out of row refreshes; choose the active live section."""
        self.set_zone('tools')
        self.stop_trailer(wait=False)
        target = 93
        if self.mode == 'live' and (self.getProperty('dh.live.vod.movie') or self.getProperty('dh.live.vod.series')):
            target = {'channels': 94, 'movie': 96, 'series': 97}.get(self._live_section, 94)
        self.focus(target)

    # --------------------------------------------------------------- nav
    NAV_TABS = (('home', 'الرئيسية'), ('movie', 'أفلام'), ('series', 'مسلسلات'),
                ('servers', 'السيرفرات'), ('live', 'IPTV'), ('library', 'مكتبتي'), ('addons', 'الإضافات'))
    NAV_ICON_TABS = (('search', 'بحث'), ('menu', 'القائمة'))

    def _servers_hidden(self):
        """v5.10.364: the Servers switch as it is now (it was read once, when
        the Home first opened, so the tab came back until Kodi restarted)."""
        shown = xbmcgui.Window(10000).getProperty('dhs.pref.show_servers')
        if shown in ('true', 'false'):
            return shown == 'false'
        return (self.app.settings.text('show_servers', 'true') or 'true').lower() == 'false'

    def _setup_nav(self):
        hide_servers = self._servers_hidden()
        # v5.10.167: tabs as skin.dexhub's: labels and colours are properties
        for key, label in self.NAV_TABS:
            self.prop('dh.nav.%s' % key, self.tr(label))
        self.prop('dh.nav.noservers', '1' if hide_servers else '')     # v5.10.156 (issue #6)
        items = []
        for key, label in self.NAV_ICON_TABS:
            li = xbmcgui.ListItem(label=self.tr(label))
            li.setProperty('key', key)
            li.setProperty('icon', self.app.media_path('nav/%s.png' % key))
            items.append(li)
        try:
            self._set_items(NAV_ICONS, items, [key for key, _label in self.NAV_ICON_TABS])
        except Exception:
            pass
        self._mark_nav()
        from . import profile
        self.prop('dh.profile', profile.current_name())

    def _mark_nav(self):
        if self.mode in ('library', 'servers', 'addons'):
            active = self.mode
        else:
            active = {'all': 'home'}.get(self.media, self.media)
        self._nav_active = active
        self.prop('dh.nav.active', active)
        # the page's tab: white with the accent line under it; the others dimmed
        accent = ''
        try:
            accent = xbmcgui.Window(10000).getProperty('dexhub.tvos.theme.accent')
        except Exception:
            pass
        # v5.10.189: icon tabs (skin.dexhub 3.27.3): the page's tab gets the
        # filled icon in the accent colour and its name (dh.nav.a); the text
        # tabs instead when homeui_nav_names is on (dh.nav.names)
        names = self._nav_names()
        self._nav_with_names = names
        self.prop('dh.nav.names', '1' if names else '')
        for key in NAV_KEY_BUTTONS:
            on = key == active
            self.prop('dh.nav.tc.%s' % key, 'FFFFFFFF' if on else 'B3FFFFFF')
            self.prop('dh.nav.line.%s' % key, (accent or 'FF9B6BFF') if on else '00FFFFFF')
            self.prop('dh.nav.a.%s' % key, (self._props.get('dh.nav.%s' % key) or '') if on else '')
            self.prop('dh.nav.ic.%s' % key, self.app.media_path('nav/%s%s.png' % (key, '_on' if on else '')))
            self.prop('dh.nav.it.%s' % key, (accent or 'FF9B6BFF') if on else 'B3FFFFFF')

    def _nav_names(self):
        """Names instead of icons on the top bar (homeui_nav_names): the
        published value first, so a change in Settings counts at once."""
        try:
            shown = xbmcgui.Window(10000).getProperty('dhs.pref.homeui_nav_names')
        except Exception:
            shown = ''
        if shown in ('true', 'false'):
            return shown == 'true'
        return self.s.flag('homeui_nav_names', False)

    def _nav_target(self):
        """The tab button of this page."""
        buttons = NAV_KEY_NAME_BUTTONS if getattr(self, '_nav_with_names', False) else NAV_KEY_BUTTONS
        return buttons.get(getattr(self, '_nav_active', 'home'), buttons['home'])

    def _nav_click(self, control_id=NAV):
        key = NAV_BUTTON_KEYS.get(control_id) or self._selected_key(control_id)
        if not key:
            return
        if key == 'addons':
            self._launch('RunPlugin(plugin://plugin.video.dexhub/?action=open_kodi_addons)')
            return
        if self.mode in ('library', 'servers', 'addons'):
            # a tab of another page: back to the Home, which opens it (v5.10.167)
            if key == self.mode:
                self.go('tabs' if self._tabs else 'rows')
                return
            if key in ('home', 'movie', 'series', 'library', 'servers', 'addons'):
                self.app.nav_next = key
                self.close_window()
                return
        if key in ('home', 'movie', 'series'):
            media = 'all' if key == 'home' else key
            if self.mode != 'home':
                return
            if media != self.media:
                self._switch_media(media)
            else:
                self.go('hero' if self.carousel else 'rows')
        elif key == 'search':
            self.stop_trailer(wait=False)
            self.app.open_search()
        elif key == 'live':
            self.stop_trailer(wait=False)
            self.app.open_live()
        elif key in ('servers', 'library', 'addons'):
            self._open_tab_page(key)
        elif key == 'menu':
            self._menu_popup()

    def _open_tab_page(self, key):
        """My Servers, My Library or Add-ons over the Home; a tab picked on
        that page comes back here and is opened in its turn (v5.10.167)."""
        self.stop_trailer(wait=False)
        while key and not self._closing:
            self.app.nav_next = ''
            if key == 'servers':
                if self._servers_hidden():
                    return          # v5.10.364: switched off
                self.app.open_servers()
            elif key == 'library':
                self.app.open_library()
            elif key == 'addons':
                self.app.open_addons()
            elif key in ('home', 'movie', 'series'):
                media = 'all' if key == 'home' else key
                if media != self.media:
                    self._switch_media(media)
                self.go('nav')
                return
            key = getattr(self.app, 'nav_next', '')
        if not self._closing:
            # Back from the page: the Home's own tab has the focus again
            self.go('nav')

    # ------------------------------------------------------------ profile
    def _profile_click(self):
        """The Dex Hub logo: switch the Nuvio profile, then load its Home."""
        from . import profile
        if not profile.linked():
            if xbmcgui.Dialog().yesno('Dex Hub', self.tr('لا يوجد حساب Nuvio مربوط. تربطه الآن؟')):
                self._launch('RunPlugin(plugin://plugin.video.dexhub/?action=nuvio_connect)')
            return
        self.stop_trailer(wait=False)
        self.on_cover()
        try:
            name = profile.choose(self.tr)
        except Exception:
            self.app.log('profile switch failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            name = ''
        finally:
            self._covered = False
            self._last_input = _now()
        if not name:
            if self._hero_tile is not None:
                self.schedule_trailer(self._hero_tile)
            return
        self.prop('dh.profile', name)
        self.app.log('Nuvio profile switched from the Home')
        app, media = self.app, self.media

        def job():
            bar = xbmcgui.DialogProgressBG()
            bar.create('Dex Hub', self.tr('تحميل بروفايل %s…') % name)
            try:
                # a fresh handle: the sync choices may have changed in another
                # invocation since the Home opened
                failed = profile.pull(xbmcaddon.Addon('plugin.video.dexhub'), app.log)
            finally:
                bar.close()
            app._tmdb_settings = None
            for page in ('all', 'movie', 'series'):
                try:
                    os.remove(os.path.join(app.profile, 'home_%s.json' % page))
                except Exception:
                    pass
            if failed:
                xbmcgui.Dialog().notification(
                    'Dex Hub', self.tr('تعذر تحديث بعض بيانات البروفايل: %s') % ', '.join(failed),
                    xbmcgui.NOTIFICATION_WARNING, 4000)
            if not app.stop_event.is_set() and media == self.media:
                self._switch_media(self.media)
        self.app.workers.submit(job, priority=0, key=('profile', self.key))

    def _menu_popup(self):
        """The Home's Menu: the control centre (v5.10.198, control.py). Its
        settings change in place; a page chosen in it opens here."""
        from . import control, ui_size
        key = control.open_centre(tr=self.tr, media=self.media)
        if key is None:
            key = control.fallback_choose(self.tr)      # a skin without the window
        if not key:
            return
        if key == 'size':
            self.stop_trailer(wait=False)
            if ui_size.choose(self.tr):
                # the windows are laid out per size: the Home opens again
                self.app.reopen = self.media or 'all'
                self.app.close_all()
            elif self._hero_tile is not None:
                self.schedule_trailer(self._hero_tile)
        elif key == 'layout':
            self.open_layout()
        elif key == 'community':
            self.open_community()
        elif key == 'follow':
            # v5.10.207: My watching, over the Home (Back comes back here)
            self.stop_trailer(wait=False)
            self.app.open_follow(self.media if self.media in ('movie', 'series') else '')
        elif key == 'setup':
            self.open_setup()
        elif key == 'search':
            self.stop_trailer(wait=False)
            self.app.open_search()
        elif key == 'refresh':
            self._refresh_rows()
        elif key == 'screensaver':
            from . import screensaver
            self.stop_trailer(wait=False)
            screensaver.setup_menu(self.tr)
            if self._hero_tile is not None:
                self.schedule_trailer(self._hero_tile)
        elif key == 'badges':
            self.stop_trailer(wait=False)
            from . import wizard
            wizard.open_wizard(steps=('badges',))
        elif key == 'player':
            self.stop_trailer(wait=False)
            from ..skinui import player_options
            player_options.show(self.tr)
        elif key == 'classic':
            self._launch('ActivateWindow(Videos,plugin://plugin.video.dexhub/?action=home_classic,return)')
        elif key == 'sources':
            self._launch('ActivateWindow(Videos,plugin://plugin.video.dexhub/?action=setup_connections,return)')
        elif key == 'skin':
            # run apart from this window: the switch closes it first (skinui/switch.py)
            self._launch('RunPlugin(plugin://plugin.video.dexhub/?action=skin_use)')
        elif key == 'skin_pick':
            self._launch('RunPlugin(plugin://plugin.video.dexhub/?action=skin_pick)')
        elif key == 'settings':
            self._launch('ActivateWindow(Videos,plugin://plugin.video.dexhub/?action=setup_center,return)')
        elif key == 'leave_dexhub':
            self._launch('RunPlugin(plugin://plugin.video.dexhub/?action=temporary_skin_exit)')
        elif key == 'kodi':
            self._launch('RunPlugin(plugin://plugin.video.dexhub/?action=open_kodi_settings)')
        elif key == 'power':
            self._launch('ActivateWindow(ShutdownMenu)')
        elif key == 'android':
            from .. import power
            self.stop_trailer(wait=False)
            power.reboot_to_android(self.tr)
        else:
            control.launch(key)

    def _refresh_rows(self):
        """The page's rows read again (the Menu's Refresh, v5.10.198)."""
        self.stop_trailer(wait=False)
        self.notify(self.tr('جاري تحديث الرئيسية'))
        if self.mode != 'home':
            self._start_build()
            return
        for page in ('all', 'movie', 'series'):
            try:
                os.remove(os.path.join(self.app.profile, 'home_%s.json' % page))
            except Exception:
                pass
        self._switch_media(self.media)

    # ------------------------------------------------------------- layout
    def row_menu(self):
        if self.mode == 'servers':
            row = self._row_a()
            if row is not None and self.zone == 'rows':
                out = [(self.tr('شكل الصف'), '__row_shape__'), (self.tr('الصف التابع…'), '__row_follow__')]
                if (row.meta or {}).get('params'):
                    out.append((self.tr('أضف هذا الصف للرئيسية'), '__row_add_home__'))
                return out
            return []
        if self.zone != 'rows' or self._row_a() is None or self.mode == 'live':
            return []
        shape = [(self.tr('شكل الصف'), '__row_shape__'),
                 (self.tr('الصف التابع…'), '__row_follow__')]
        if self.mode == 'folder' and self.folder_ref.get('folder_id'):
            shape.insert(0, (self.tr('العرض داخل الفولدرات'), '__folder_view__'))
        if self.mode != 'home':
            return shape
        return shape + [(self.tr('إخفاء هذا الصف'), '__row_hide__'),
                        (self.tr('ترتيب الصفحة الرئيسية'), '__row_layout__')]

    def row_command(self, command):
        row = self._row_a()
        if command == '__folder_view__':
            from ..folder_view import choose
            if choose():
                self._switch_media(self.media)
        elif command == '__row_hide__' and row is not None:
            self.app.layout.hide(self.media, row.key, True)
            try:
                xbmcgui.Dialog().notification(
                    'Dex Hub', self.tr('أُخفي الصف. ترجعه من ترتيب الصفحة الرئيسية'),
                    xbmcgui.NOTIFICATION_INFO, 3000)
            except Exception:
                pass
            self._reload_layout()
        elif command == '__row_layout__':
            self.open_layout(select_key=row.key if row is not None else '')
        elif command == '__row_follow__' and row is not None:
            from .. import row_follow
            if row_follow.edit(self.media, row.key, row.title, self.tr):
                from .catalog_follow import update
                update(self, self._selected_tile() or {})
        elif command == '__row_shape__' and row is not None:
            self._pick_row_shape(row)
        elif command in ('__row_search_forget__', '__row_search_clear__'):
            self._search_forget(everything=command == '__row_search_clear__')
        elif command == '__row_add_home__' and row is not None:
            self._add_row_to_home(row)

    def _pick_row_shape(self, row):
        """Choose how one row draws its cards (v5.10.173); kept for that row everywhere."""
        from .layout import pick_style, style_choices_for, is_progress_row, suggested_styles
        current = self.app.layout.shape(row.key)
        shelf = bool(row.meta.get('shelf'))
        key = pick_style(self.tr, self.app.media_path, current, row.title,
                         only=style_choices_for(shelf=shelf, progress=is_progress_row(row)),
                         suggested=suggested_styles(row.title, collection=getattr(row, 'kind', '') == 'collection',
                                                    progress=is_progress_row(row)))
        if key is None or key == current:
            return
        self.app.layout.set_shape(row.key, key)
        if shelf:
            with self._lock:
                self._shown.clear()
                self._render_rows()
            return
        if not key:
            # back to its own shape: the page builds the row again
            self._reload_layout()
            return
        with self._lock:
            row.shape = key
            row.meta['auto_shape'] = False
            self._shown.clear()
            self._render_rows()
        if self.zone == 'rows':
            self.focus(self.rows_focus())
        self._prefetch_focus_art()

    def open_layout(self, select_key=''):
        """The Home layout editor, over this page; the rows follow it when it closes."""
        from .layout_window import open_editor
        self.on_cover()
        try:
            changed = open_editor(self.app, self, media=self.media, select_key=select_key)
        except Exception:
            self.app.log('layout editor failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            changed = set()
        finally:
            self._covered = False
            self._last_input = _now()
        if self.media in changed:
            self._reload_layout()
        elif self._hero_tile is not None:
            self.schedule_trailer(self._hero_tile)

    def open_community(self):
        """Dex Community (v5.10.195), over this page; the rows follow what was added."""
        from .community_window import open_community
        self.stop_trailer(wait=False)
        self.on_cover()
        page = self.media if self.media in ('movie', 'series') else 'all'
        try:
            changed = open_community(self.app, page=page)
        except Exception:
            self.app.log('Dex Community failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            changed = set()
        finally:
            self._covered = False
            self._last_input = _now()
        if (self.media or 'all') in changed:
            self._reload_layout()
        elif self._hero_tile is not None:
            self.schedule_trailer(self._hero_tile)

    def open_setup(self):
        """Accounts and sources (v5.10.102), over this page; a new link rebuilds the rows."""
        from . import setup
        self.stop_trailer(wait=False)
        self.on_cover()
        try:
            changed = setup.open_setup(tr=self.tr)
        except Exception:
            self.app.log('accounts screen failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            changed = False
        finally:
            self._covered = False
            self._last_input = _now()
        if not changed:
            if self._hero_tile is not None:
                self.schedule_trailer(self._hero_tile)
            return
        self.app.log('accounts or sources changed: rebuilding the Home')
        app = self.app
        app._tmdb_settings = None
        for page in ('all', 'movie', 'series'):
            try:
                os.remove(os.path.join(app.profile, 'home_%s.json' % page))
            except Exception:
                pass
        try:
            from .profile import current_name
            self.prop('dh.profile', current_name() or '')
        except Exception:
            pass
        self._switch_media(self.media)

    def _reload_layout(self):
        """Rebuild this page's rows in the new order, keeping every row already loaded."""
        self.stop_trailer(wait=False)
        media = self.media

        def job():
            try:
                fresh = self._home_rows()
            except Exception:
                self.app.log('layout reload failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
                return
            if media != self.media:
                return
            with self._lock:
                loaded = dict((row.key, row) for row in self.rows)
                rows = [loaded.get(row.key) or row for row in fresh]
            self._install_rows(rows, initial=False)
            self._save_cache_soon()
        self.app.workers.submit(job, priority=0, key=('layout', self.key))

    def _switch_media(self, media):
        self.stop_trailer(wait=False)
        with self._lock:
            self._build_gen += 1
            self.media = media
            self._mark_nav()
            self._built = False
            self._save_due = 0.0
            self.rows = []
            self.ri = 0
            self.carousel = []
            self._shown = {}
            self.prop('dh.loading', '1')
            self._render_rows()
            self._render_dots()
        cached = self._load_cache()
        if cached:
            self._install_rows(cached, initial=True)
        self._start_build()

    # ------------------------------------------------------------- build
    def _start_build(self):
        with self._lock:
            self._build_gen += 1
            gen, media = self._build_gen, self.media
        # a page's rows are worked out on a lane of their own (v5.10.104): a
        # slow server holding every row worker must not hold the page too
        lane = getattr(self.app, 'pages', None) or self.app.workers
        lane.submit(lambda: self._build_rows_job(gen, media), priority=0,
                    key=('build', self.key, gen))

    def _build_is_current(self):
        generation = getattr(getattr(self, '_build_scope', None), 'generation', None)
        return generation is None or generation == self._build_gen

    def _build_rows_job(self, gen=0, media=None):
        if gen != self._build_gen or (media is not None and media != self.media):
            return
        if not hasattr(self, '_build_scope'):
            self._build_scope = threading.local()
        previous = getattr(self._build_scope, 'generation', None)
        self._build_scope.generation = gen
        try:
            self._build_rows_generation(gen, media)
        finally:
            if previous is None:
                del self._build_scope.generation
            else:
                self._build_scope.generation = previous

    def _build_rows_generation(self, gen=0, media=None):
        started = _now()
        self._build_reason = ''
        try:
            if self.search is not None:
                rows = self._search_rows(gen)
            elif self.mode == 'library':
                rows = self._library_rows()
            elif self.mode == 'servers':
                rows = self._server_rows()
            elif self.mode == 'addons':
                rows = self._addons_rows()
            elif self.mode == 'live':
                rows = self._live_rows()
            else:
                rows = self._folder_rows() if self.mode == 'folder' else self._home_rows()
        except Exception:
            self.app.log('row build failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            rows = []
        if gen != self._build_gen or (media is not None and media != self.media):
            return      # the user switched tabs while this build ran
        if self.mode in ('servers', 'live', 'library') and self.search is None:
            # a rebuild of the same tab keeps the rows it already has (their
            # tiles, and whatever is loading): only new rows load
            with self._lock:
                had = dict((row.key, row) for row in self.rows)
                fresh, self._fresh_rows = getattr(self, '_fresh_rows', False), False
            # a refresh the user asked for reads the empty rows again too
            reusable = ('ready', 'loading') if fresh else ('ready', 'loading', 'empty')
            kept = []
            for row in rows:
                old = had.get(row.key)
                if old is not None and old.loader is not None and row.loader is not None \
                        and old.state in reusable:
                    old.title, old.subtitle = row.title, row.subtitle
                    kept.append(old)
                else:
                    kept.append(row)
            rows = kept
        self.app.log('%s rows ready: %d in %.0f ms' % (
            'search' if self.search is not None else self.mode, len(rows), (_now() - started) * 1000))
        self._install_rows(rows, initial=False)
        if self.search is not None and self._build_is_current():
            self._search_shown(rows)

    def _home_rows(self):
        """The rows of this page as the user arranged them (v5.10.98)."""
        self._layout_stamp = self.app.layout.stamp()
        rows = self.app.layout.apply(self.layout_rows(self.media), self.media)
        return [row for row in rows if not row.meta.get('missing')]

    def layout_rows(self, media):
        """Every row a page can show, hidden ones too, then the rows the user added.

        The Home layout editor lists these; nothing here loads a row.
        """
        rows = self._candidate_rows(media)
        progress = [r for r in rows if r.key in ('continue', 'nextup')]
        return progress + [r for r in rows if r not in progress] + self._custom_rows(media)

    def _candidate_rows(self, media):
        api = self.app.api()
        rows = []
        # the two progress rows always exist; their settings only decide
        # whether they start shown, and the layout editor can change that
        rows.append(R.Row('continue', self.tr('متابعة المشاهدة'), shape='wide',
                          kind='continue', priority=0, loader=self._progress_loader('continue', media),
                          meta={'params': {'action': 'continue'},
                                'default_hidden': not self.s.flag('homeui_show_continue', True)}))
        if media != 'movie':
            rows.append(R.Row('nextup', self.tr('الحلقة التالية'), shape='wide', kind='continue',
                              priority=1, loader=self._progress_loader('nextup', media),
                              meta={'params': {'action': 'nextup'},
                                    'default_hidden': not self.s.flag('homeui_show_nextup', False)}))
        # v5.10.204: rows the user can show from Arrange Home rows
        if media == 'all':
            for key, title in (('continue_movies', 'متابعة المشاهدة • أفلام'),
                               ('continue_shows', 'متابعة المشاهدة • مسلسلات')):
                rows.append(R.Row(key, self.tr(title), shape='wide', kind='continue', priority=2,
                                  loader=self._progress_loader(key, media),
                                  meta={'params': dict(R.OWN_ROWS[key][0]), 'default_hidden': True}))
        for key, title, tab in (('recent_movies', 'شاهدتها مؤخراً • أفلام', 'movie'),
                                ('recent_shows', 'شاهدتها مؤخراً • مسلسلات', 'series')):
            if media in ('all', tab):
                rows.append(R.Row(key, self.tr(title), shape='poster', kind='catalog', priority=3,
                                  loader=self._progress_loader(key, media),
                                  meta={'params': dict(R.OWN_ROWS[key][0]), 'default_hidden': True}))
        # v5.10.208: Because you watched <the last title>, shown from Arrange Home rows
        from .follow import last_title
        for key, tab in (('because_movies', 'movie'), ('because_shows', 'series')):
            if media in ('all', tab):
                seed = last_title(tab)
                title = (self.tr('لأنك شاهدت %s') % seed) if seed else self.tr(
                    'لأنك شاهدت • أفلام' if tab == 'movie' else 'لأنك شاهدت • مسلسلات')
                rows.append(R.Row(key, title, shape='poster', kind='catalog', priority=3,
                                  loader=self._progress_loader(key, media),
                                  meta={'params': dict(R.OWN_ROWS[key][0]), 'default_hidden': True}))
        tabs, has_nuvio = [], False
        try:
            tabs, has_nuvio = R.nuvio_tabs(api, media)
        except Exception:
            self.app.log('nuvio home model failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        if media == 'all':
            # Nuvio's own Home stays exactly as the user arranged it; the
            # BetterPosters discovery rows only fill a Home without Nuvio.
            tabs = [t for t in tabs if 'betterposters_catalog' not in str(t.get('path') or '')]
            if not has_nuvio:
                kinds = {'movie': 'أفلام', 'series': 'مسلسلات'}
                for tab in R.betterposters_tabs(api, 'all'):
                    tab = dict(tab)
                    label = tab.get('label') or ''
                    kind = kinds.get(tab.get('media_type'))
                    if kind:
                        # the suffix follows the row label's own language
                        if not re.search(u'[\u0600-\u06ff]', label):
                            kind = self.tr(kind)
                        tab['label'] = '%s  ·  %s' % (label, kind)
                    tabs.append(tab)
        wrap = getattr(api, '_wrap_tokenized_url', None)
        media_filter = media if media in ('movie', 'series') else ''
        # v5.10.236: Group recommendation rows: an add-on's "Because you
        # watched ..." catalogs (Bingecat makes one per title) as one row
        # of catalog cards in the place of the first of them
        group_recs = self.s.flag('home_group_recs', False)
        recs_at = None
        recs = 0
        for tab in tabs:
            if group_recs and tab.get('kind') != 'collection' and tab.get('path') \
                    and R.is_recommendation_row(tab.get('label') or tab.get('native_label') or ''):
                recs += 1
                if recs_at is None:
                    recs_at = len(rows)
                continue
            if tab.get('kind') == 'collection' and tab.get('entry'):
                entry = tab['entry']
                tiles = R.folder_tiles(entry, tab.get('set_id') or '', wrap=wrap, gif_enabled=self.gif_on,
                                       media_filter=media_filter)
                if tiles:
                    rows.append(R.Row('col:%s' % (entry.get('id') or tab.get('key')),
                                      tab.get('label') or entry.get('name') or '',
                                      shape=R.dominant_shape(tiles, 'landscape'), kind='collection',
                                      tiles=tiles, meta={'set_id': tab.get('set_id') or ''}))
                continue
            path = tab.get('path') or ''
            if not path:
                continue
            params = R.params_of(path)
            label = tab.get('label') or tab.get('native_label') or ''
            subtitle = (tab.get('info') or {}).get('tagline') or ''
            # a Continue Watching / Next Up row of any source reads like
            # Dex Hub's own: landscape tiles that name the title and episode
            progress = R.is_progress_row(label)
            rows.append(R.Row('cat:%s' % (tab.get('key') or path), label,
                              shape='wide' if progress else 'poster',
                              kind='catalog', subtitle=subtitle,
                              loader=self._catalog_loader(params, progress=progress),
                              meta={'params': params, 'progress': progress}))
        if recs_at is not None:
            params = {'action': 'home_shelf', 'id': 'auto:recs', 'm': media}
            rows.insert(recs_at, R.Row('shelf:recs', self.tr('توصيات لك'), shape='shelf', kind='catalog',
                                       subtitle=str(recs), loader=self._catalog_loader(params),
                                       meta={'params': params, 'shelf': True, 'auto_shape': False}))
        if self.s.flag('home_hide_new_rows', False):
            # v5.10.236: Hide new rows from add-ons
            try:
                self.app.layout.hide_new(media, [r.key for r in rows if r.key.startswith('cat:')])
            except Exception:
                pass
        if has_nuvio:
            # v5.10.448: Dex Hub's own collection rows join a Home that follows
            # Nuvio's, after its first rows (Arrange Home rows moves or hides them)
            ours = []
            for set_id, entry in R.dex_collection_entries():
                if any(r.key == 'col:%s' % entry.get('id') for r in rows):
                    continue
                tiles = R.folder_tiles(entry, set_id, wrap=wrap, gif_enabled=self.gif_on,
                                       media_filter=media_filter)
                if tiles:
                    ours.append(R.Row('col:%s' % entry.get('id'), entry.get('name') or '',
                                      shape=R.dominant_shape(tiles, 'landscape'),
                                      kind='collection', tiles=tiles, meta={'set_id': set_id}))
            catalogs = [i for i, r in enumerate(rows) if r.key.startswith(('cat:', 'col:'))]
            at = catalogs[3] if len(catalogs) > 3 else len(rows)
            rows[at:at] = ours
        if not has_nuvio and media == 'all':
            for set_id, entry in R.fallback_collection_entries():
                tiles = R.folder_tiles(entry, set_id, wrap=wrap, gif_enabled=self.gif_on,
                                       media_filter=media_filter)
                if tiles:
                    rows.append(R.Row('col:%s' % entry.get('id'), entry.get('name') or '',
                                      shape=R.dominant_shape(tiles, 'landscape'),
                                      kind='collection', tiles=tiles, meta={'set_id': set_id}))
        return rows

    def _custom_rows(self, media):
        """Rows the user added in the layout editor (v5.10.98)."""
        from .layout import custom_key
        rows = []
        specs = self.app.layout.page(media)['custom']
        if not specs:
            return rows
        wrap = getattr(self.app.api(), '_wrap_tokenized_url', None)
        media_filter = media if media in ('movie', 'series') else ''
        for spec in specs:
            key = custom_key(spec)
            label = spec.get('label') or ''
            meta = {'custom_id': spec.get('id') or '', 'source_label': spec.get('source_label') or ''}
            if spec.get('type') == 'collection':
                from .layout_pick import find_collection
                _set, entry = find_collection(spec.get('set_id'), spec.get('entry_id'))
                tiles = []
                if entry:
                    tiles = R.folder_tiles(entry, spec.get('set_id') or '', wrap=wrap,
                                           gif_enabled=self.gif_on, media_filter=media_filter)
                if not tiles:
                    meta['missing'] = True
                meta['set_id'] = spec.get('set_id') or ''
                rows.append(R.Row(key, label, shape=R.dominant_shape(tiles, 'landscape'),
                                  kind='collection', tiles=tiles, meta=meta))
                continue
            if spec.get('type') == 'folders':
                # v5.10.197: folders picked in Dex Community, one collection row
                from . import community as CM
                tiles = CM.folder_row_tiles(spec, wrap=wrap, gif_enabled=self.gif_on, media_filter=media_filter)
                if not tiles:
                    meta['missing'] = True
                meta['set_id'] = spec.get('set_id') or ''
                rows.append(R.Row(key, label, shape=R.dominant_shape(tiles, 'landscape'),
                                  kind='collection', tiles=tiles, meta=meta))
                continue
            if spec.get('type') == 'shelf':
                # v5.10.179: a collection row the user made (shelf.py)
                params = {'action': 'home_shelf', 'id': spec.get('id') or '', 'm': media}
                if not spec.get('members'):
                    meta['missing'] = True
                meta.update({'params': params, 'shelf': True, 'auto_shape': False})
                rows.append(R.Row(key, label, shape='shelf', kind='catalog', subtitle=spec.get('source_label') or '',
                                  loader=self._catalog_loader(params), meta=meta))
                continue
            params = dict(spec.get('params') or {})
            if not params.get('action'):
                meta['missing'] = True
            from . import servers as S
            progress = S.is_progress(params)
            meta.update({'params': params, 'auto_shape': not spec.get('shape') and not progress})
            rows.append(R.Row(key, label, shape=spec.get('shape') or ('wide' if progress else 'poster'),
                              kind='continue' if progress else 'catalog',
                              subtitle=spec.get('subtitle') or '',
                              loader=(self._catalog_loader(params, progress=progress)
                                      if params.get('action') else None),
                              meta=meta))
        return rows

    # --------------------------------------------------------------- tabs
    def _pick_tab(self, page, tabs, avoid=''):
        """The tab to show: the one chosen on this page, else the last one used,
        else the first (one that is not ``avoid`` when there is another)."""
        keys = [tab['key'] for tab in tabs]
        for key in (self._tab, self._remembered_tab(page)):
            if key and key in keys:
                return key
        for key in keys:
            if key != avoid:
                return key
        return keys[0] if keys else ''

    def _remembered_tab(self, page):
        from . import live_sources
        return live_sources.remembered_tab(self.app.profile, page)

    def _show_tabs(self, tabs, selected):
        """The page's tab bar (v5.10.104): its servers, or its live sources."""
        signature = [(t['key'], t['label'], t.get('hint') or '', t.get('icon') or '',
                      t.get('count') or '', t.get('tone') or '') for t in tabs]
        with self._lock:
            if not self._build_is_current():
                return
            self._tabs = list(tabs)
            # a tab chosen while this build ran stays chosen (that build is
            # thrown away, and the next one shows it)
            if self._tab not in [t['key'] for t in tabs]:
                self._tab = selected
            if signature != self._tabs_sig:
                self._tabs_sig = signature
                items = []
                for tab in tabs:
                    li = xbmcgui.ListItem(label=tab['label'], label2=tab.get('count') or '')
                    li.setProperty('key', tab['key'])
                    li.setProperty('hint', tab.get('hint') or '')
                    li.setProperty('icon', tab.get('icon') or '')
                    # v5.10.167: skin.dexhub's chips (My Library, My Servers): the
                    # source's colour in a dot, the count after the name
                    li.setProperty('tone', tab.get('tone') or '')
                    items.append(li)
                self._tab_items = items
                try:
                    self._set_items(TABS, items, [t['key'] for t in tabs])
                except Exception:
                    pass
        self._mark_tabs()
        self.prop('dh.tabs', '1' if tabs else '')

    def _mark_tabs(self):
        with self._lock:
            keys = list(self._keys.get(TABS) or [])
            for li, key in zip(self._tab_items, keys):
                li.setProperty('active', '1' if key == self._tab else '')
            if self.focus_id() != TABS and self._tab in keys:
                try:
                    self._control(TABS).selectItem(keys.index(self._tab))
                except Exception:
                    pass
        label = ''
        for tab in self._tabs:
            if tab['key'] == self._tab:
                label = tab['label']
        self.prop('dh.tab.label', label)

    def _select_tab(self, key, rows_after=False):
        """Show another server or live source; the rows follow."""
        if not key:
            return
        if key == self._tab:
            if rows_after:
                self.go('rows')
            return
        from . import live_sources
        self.stop_trailer(wait=False)
        self._cancel_row_jobs()
        self._tab = key
        self._tab_rows_after = rows_after
        live_sources.remember_tab(self.app.profile, self.mode, key)
        self._mark_tabs()
        with self._lock:
            self.rows = []
            self.ri = 0
            self._shown = {}
            self.prop('dh.loading', '1')
            self.prop('dh.empty', '')
            self.prop('dh.empty.reason', '')
            self._render_rows()
        for tab in self._tabs:
            if tab['key'] == key:
                self._show_tab_hero(tab)
        self._start_build()

    def _show_tab_hero(self, tab):
        with self._lock:
            if not self._build_is_current():
                return
            brand = {
                'kind': 'brand', 'title': tab.get('label') or '',
                'plot': tab.get('plot') or '',
                'fanart': os.path.join(self.app.addon_path, 'resources', 'media', 'fanart.jpg'),
            }
            self._brand_tile = brand
            self.show_hero(brand)
            self.prop('dh.subtitle', tab.get('hint') or '')

    def catalog_menu(self):
        row = self._row_a()
        route = (row.meta or {}).get('params') if row is not None else None
        if self.mode == 'live' and not (route and route.get('catalog')):
            self.page_tools()
            return
        if not route or row.kind != 'catalog' or (row.meta or {}).get('progress'):
            self.page_tools()
            return
        from . import catalog_controls as CC
        if CC.choose(route, self.app, row.title, row.tiles):
            row.meta['choice_changed'] = True
            self._load_row(row, priority=0, refresh=True)
        self.go('rows')

    def page_tools(self):
        from .options import choose
        labels, commands = [], []
        if self.mode == 'live':
            labels += [self.tr('المجموعات والقنوات'), self.tr('تحديث المصدر'), self.tr('إدارة المصادر')]
            commands += ['groups', 'refresh_live', 'setup_live']
            for media, label in (('channels', 'القنوات'), ('movie', 'أفلام VOD'), ('series', 'مسلسلات VOD')):
                if media == 'channels' or self.getProperty('dh.live.vod.' + media):
                    labels.append(self.tr(label))
                    commands.append('section:' + media)
        elif self.mode == 'library':
            labels += [self.tr('انتقل إلى'), self.tr('الحسابات والمصادر')]
            commands += ['groups', 'accounts']
        elif self.mode == 'servers':
            labels += [self.tr('المكتبات والصفوف'), self.tr('تحديث السيرفر'), self.tr('الحسابات والمصادر')]
            commands += ['groups', 'refresh_server', 'accounts']
        else:
            return
        picked = choose(self.page_title, labels, subtitle=self.getProperty('dh.tab.label'))
        if picked < 0 or picked >= len(commands):
            return
        command = commands[picked]
        if command.startswith('section:'):
            self.select_live_section(command.split(':', 1)[1])
        elif command == 'groups':
            available = [(i, row) for i, row in enumerate(self.rows) if row.state not in ('empty', 'error')]
            picked = choose(self.tr('انتقل إلى'), [row.title for i, row in available])
            if 0 <= picked < len(available):
                self._goto_row(available[picked][0])
        elif command == 'refresh_live':
            self.live_refresh()
        elif command == 'setup_live':
            self.live_setup_menu()
        elif command == 'refresh_server':
            self._fresh_rows = True
            for row in self.rows:
                row.meta['stale'] = True
            self._start_build()
        else:
            self.open_setup()

    def _tab_click(self):
        key = self._selected_key(TABS)
        if key:
            self._select_tab(key)

    def _library_rows(self):
        from ..routes import library
        linked = library.linked_sources()
        try:
            from .. import favorites_store
            counts, total = favorites_store.source_counts(), favorites_store.count()
        except Exception:
            counts, total = {}, 0

        def count(value):
            return str(value) if value else ''
        tabs = [{'key': 'follow', 'label': library._t('متابعتي', 'My watching'), 'count': '',
                 'tone': 'FFF5C518'},
                {'key': 'all', 'label': library._t('الكل', 'All'), 'count': count(total)},
                {'key': 'fav', 'label': library._t('مفضلتي', 'Favorites'), 'count': count(counts.get('local', 0)),
                 'tone': _LIBRARY_TONES['fav']}]
        tabs += [{'key': name.lower(), 'label': name, 'count': count(counts.get(name.lower(), 0)),
                  'tone': _LIBRARY_TONES[name.lower()]} for name in
                 ('Floppy', 'Trakt', 'Simkl', 'MDBList', 'Nuvio', 'Stremio') if name in linked]
        selected = self._pick_tab('library', tabs, avoid='follow')
        self._show_tabs(tabs, selected)
        self._library_header(linked)
        if not self._hero_tile or self._hero_tile.get('kind') == 'brand':
            self._show_tab_hero({'label': library._t('مكتبتي', 'My Library'),
                                'hint': ' · '.join(linked),
                                'plot': library._t('متابعة المشاهدة، المفضلة وقوائمك حسب المصدر',
                                                   'Continue watching, favorites and your lists by source')})
        library.refresh_later(on_done=lambda: self._start_build() if not self._closing else None,
                              submit=self.app.bg.submit)
        rows = []
        # v5.10.212: My watching as a tab of its own, and the Following and
        # library choices (merged watchlists, Continue Watching split)
        for key, title, shape, params in library.tab_specs(linked, self._tab):
            progress = key in R.OWN_KEYS
            loader = (self._progress_loader(key, 'library') if progress
                      else self._catalog_loader(params, progress=False))
            rows.append(R.Row(key, title, shape=shape, kind='continue' if progress else 'catalog',
                              loader=loader, meta={'params': dict(params), 'progress': progress}))
        return rows

    # ------------------------------------------------------------ add-ons
    def _addons_rows(self):
        """Kodi's programs and video add-ons (v5.10.167), as skin.dexhub's
        Add-ons tab: the video row ends with Dex Hub's Stremio sources and a
        tile to add one (routes.simple_entry.addons_row)."""
        if not self._hero_tile or self._hero_tile.get('kind') == 'brand':
            self._show_tab_hero({'label': self.tr('الإضافات'),
                                 'plot': self.tr('البرامج وإضافات الفيديو المثبتة في Kodi')})
        rows = []
        for key, title, content in (('addons:programs', self.tr('البرامج'), 'executable'),
                                    ('addons:video', self.tr('الفيديو'), 'video')):
            tiles = self._addon_tiles(content)
            if content == 'video':
                tiles += self._source_tiles()
            rows.append(R.Row(key, title, shape='square', kind='catalog', tiles=tiles,
                              subtitle=str(len(tiles)) if tiles else ''))
        return rows

    def _addon_tiles(self, content):
        params = {'type': 'xbmc.python.script' if content == 'executable' else 'xbmc.addon.video',
                  'enabled': True,
                  'properties': ['name', 'thumbnail', 'fanart', 'description', 'summary', 'version', 'author']}
        if content == 'executable':
            params['content'] = 'executable'
        try:
            found = (json.loads(xbmc.executeJSONRPC(json.dumps(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.GetAddons', 'params': params}))).get('result')
                or {}).get('addons') or []
        except Exception:
            found = []
        tiles = []
        for addon in sorted(found, key=lambda a: (a.get('name') or a.get('addonid') or '').lower()):
            addon_id = addon.get('addonid') or ''
            if not addon_id:
                continue
            name = R._TAGS_RE.sub('', addon.get('name') or addon_id)
            icon = addon.get('thumbnail') or ''
            tiles.append({'kind': 'addon', 'addon_id': addon_id, 'content': content,
                          'title': name, 'label': name, 'shape': 'square', 'under': '1',
                          'poster': icon, 'landscape': icon, 'icon': icon,
                          'fanart': addon.get('fanart') or '',
                          'plot': addon.get('description') or addon.get('summary') or '',
                          'version': addon.get('version') or '', 'author': addon.get('author') or '',
                          'initials': R.initials(name)})
        return tiles

    def _source_tiles(self):
        """Dex Hub's Stremio sources and the tile that adds one."""
        tiles = []
        try:
            from .. import store
            from ..art import provider_art
            providers = store.list_providers()
        except Exception:
            providers = []
        for row in providers:
            name = row.get('name') or row.get('id') or 'Stremio'
            manifest = row.get('manifest') or {}
            try:
                art = provider_art(name, manifest, base_url=row.get('base_url') or '')
            except Exception:
                art = {}
            icon = art.get('icon') or art.get('thumb') or ''
            tiles.append({'kind': 'addon', 'source_id': row.get('id') or '', 'content': 'stremio',
                          'title': name, 'label': name, 'shape': 'square', 'under': '1',
                          'poster': icon, 'landscape': icon, 'icon': icon, 'fanart': art.get('fanart') or '',
                          'plot': manifest.get('description') or '', 'version': manifest.get('version') or '',
                          'author': 'Stremio', 'initials': R.initials(name)})
        plus = self.app.media_path('addons_plus.png')
        if not os.path.exists(plus):
            plus = os.path.join(self.app.addon_path, 'resources', 'media', 'addons_plus.png')
        tiles.append({'kind': 'addon', 'content': 'add', 'title': self.tr('إضافة مصدر'),
                      'label': self.tr('إضافة مصدر'), 'shape': 'square', 'under': '1',
                      'poster': plus, 'landscape': plus, 'icon': plus, 'fanart': '',
                      'plot': self.tr('أضف رابط Manifest لمصدر Stremio'), 'initials': '+'})
        return tiles

    # ------------------------------------------------------------ servers
    def _server_rows(self):
        """The Servers page (v5.10.104): one linked server at a time, chosen in the tab bar.

        Its rows: what is being watched on it, what was added lately (Plex),
        one row of posters per library once its libraries are known, Silo's
        own Home sections, and the libraries as cards.
        """
        if self._servers_hidden():
            return []           # v5.10.364
        from . import servers as S
        api = self.app.api()
        found = S.linked_servers(api)
        try:
            # v5.10.180: a server a slow answer left out stays from the last
            # good list (skin.dexhub's Servers tab keeps one)
            from ..routes import servers_tab as ST
            saved = ST._load()
            if saved.get('account') == ST._account_key():
                signed = set(sv for sv, _b, client in S.SERVICES if S._signed_in(api, client))
                found = S.ordered(S.merge_known(found, [
                    {k: s.get(k) for k in ('service', 'brand', 'id', 'name')}
                    for s in saved.get('servers') or [] if s.get('service') in signed]))
        except Exception:
            pass
        self._servers_found = found
        if not found:
            self._show_tabs([], '')
            tile = {'kind': 'action', 'command': 'setup', 'title': self.tr('ربط سيرفر'),
                    'label': self.tr('ربط سيرفر'), 'shape': 'landscape',
                    'hint': self.tr('Plex أو Emby أو Jellyfin أو Silo'),
                    'plot': self.tr('اربط سيرفرك من الحسابات والمصادر، وتطلع مكتباته هنا '
                                    'وتقدر تضيفها للصفحة الرئيسية.'),
                    'poster': self.app.media_path('lib_plex.jpg'),
                    'landscape': self.app.media_path('lib_plex.jpg')}
            return [R.Row('srv:setup', self.tr('لا يوجد سيرفر مربوط'), shape='landscape',
                          kind='catalog', tiles=[tile], meta={})]
        root = os.path.join(self.app.addon_path, 'resources', 'media')
        # v5.10.167: skin.dexhub's chips: All (every server's rows) first, then
        # each server with its libraries' count, in its brand's colour
        libs_of = getattr(self, '_server_libs', None) or {}
        tabs = []
        if len(found) > 1:
            tabs.append({'key': 'all', 'label': self.tr('الكل'), 'count': str(len(found))})
        for tab, server in zip(S.tabs(found, root), found):
            libs = len(libs_of.get(S.server_key(server)) or [])
            tab['count'] = str(libs) if libs else ''
            tab['tone'] = _SERVER_TONES.get(server['service'], 'FFFFFFFF')
            tabs.append(tab)
        selected = self._pick_tab('servers', tabs)
        self._show_tabs(tabs, selected)
        many = selected == 'all'
        chosen = found if many else [s for s in found if S.server_key(s) == selected]
        # the header as on skin.dexhub's My Servers
        brands = []
        for one in found:
            if one['brand'] not in brands:
                brands.append(one['brand'])
        libs = sum(len(libs_of.get(S.server_key(s)) or []) for s in chosen)
        if many:
            stats = [(str(len(found)), self.tr('سيرفر متصل'))]
        else:
            stats = [(chosen[0]['brand'], self.tr('السيرفر'))]
        if libs:
            stats.append((str(libs), self.tr('مكتبة')))
        self._header(self.tr('متصل'), u'  \u00b7  '.join(brands), self.tr('سيرفراتي'), stats)
        if many:
            return self._servers_rows(chosen)
        return self._one_server_rows(chosen[0])

    def _servers_rows(self, servers):
        """Every server at once (the All chip), in skin.dexhub's order: what
        is being watched on each, then what was added (v5.10.168: no
        libraries here, they are on each server's chip)."""
        from . import servers as S
        rows = []

        def named(title, server):
            return '%s  \u00b7  %s' % (title, server['name'])
        for server in servers:
            params = S.continue_params(server)
            if params:
                rows.append(R.Row('srv:%s:continue' % S.server_key(server), named(self.tr('كمّل المشاهدة'), server),
                                  shape='wide', kind='continue', subtitle=server['brand'],
                                  loader=self._catalog_loader(params, progress=True),
                                  meta={'params': params, 'server': server, 'progress': True}))
        for server in servers:
            params = S.recent_params(server, self.tr('أضيف مؤخراً'))
            if params:
                rows.append(R.Row('srv:%s:recent' % S.server_key(server), named(self.tr('أضيف مؤخراً'), server),
                                  shape='poster', kind='catalog', subtitle=server['brand'],
                                  loader=self._catalog_loader(params),
                                  meta={'params': params, 'server': server, 'auto_shape': True}))
        # v5.10.168: All stays light: what is being watched and what was added
        # on every server (Silo's own sections for both), no libraries; a
        # server's chip has everything
        for server in servers:
            if server['service'] != 'silo':
                continue
            # v5.10.198: Next up too, when Silo shows it as a row of its own
            for kind, title in (('continue', self.tr('كمّل المشاهدة')), ('next_up', self.tr('التالي')),
                                ('recent', self.tr('أضيف مؤخراً'))):
                progress = kind in ('continue', 'next_up')
                rows.append(R.Row('srv:%s:silo_%s' % (S.server_key(server), kind), named(title, server),
                                  shape='wide' if progress else 'poster',
                                  kind='continue' if progress else 'catalog', subtitle=server['brand'],
                                  loader=self._silo_pick_loader(server, kind),
                                  meta={'server': server, 'progress': progress, 'auto_shape': not progress}))
        # every server's Continue watching first, then what was added
        rows.sort(key=lambda row: 0 if row.kind == 'continue' else 1)
        return rows

    def _silo_pick_loader(self, server, kind):
        """A Silo Home section standing for Continue watching or Recently
        added (servers.picks); Silo Home is read once for both rows."""
        from . import servers as S
        skey = S.server_key(server)

        def load(patience=0.0):
            memo = getattr(self.app, '_silo_home_memo', None) or {}
            hit = memo.get(skey)
            if hit and time.time() - hit[0] < 600:
                sections = hit[1]
            else:
                from .. import capture
                entries, _elapsed = capture.run_route({'action': 'silo_home'}, self.app.api(),
                                                      patience=max(patience, 6.0))
                sections = S.section_rows(entries or [])
                memo[skey] = (time.time(), sections)
                self.app._silo_home_memo = memo
                self._server_sections.setdefault(skey, sections)
            for picked, _title, params in S.picks(sections):
                if picked == kind:
                    return self._catalog_loader(params, progress=kind in ('continue', 'next_up'))(patience)
            return [], None
        return load

    def _one_server_rows(self, server):
        from . import servers as S
        brand = server['brand']
        skey = S.server_key(server)
        base = 'srv:%s' % skey
        rows = []
        params = S.continue_params(server)
        if params:
            rows.append(R.Row(base + ':continue', self.tr('كمّل المشاهدة'), shape='wide',
                              kind='continue', subtitle=brand,
                              loader=self._catalog_loader(params, progress=True),
                              meta={'params': params, 'server': server, 'progress': True}))
        params = S.recent_params(server, self.tr('أضيف مؤخراً'))
        if params:
            rows.append(R.Row(base + ':recent', self.tr('أضيف مؤخراً'), shape='poster',
                              kind='catalog', subtitle=brand, loader=self._catalog_loader(params),
                              meta={'params': params, 'server': server, 'auto_shape': True}))
        sections = list(self._server_sections.get(skey) or [])
        for section_title, params in sections:
            if params.get('section_type') not in S.LEAD_SECTIONS:
                continue
            # v5.10.198: a Silo server opens on what is being watched, what is
            # next and what was added, as the other servers do
            progress = params.get('section_type') in ('continue_watching', 'next_up')
            rows.append(R.Row('%s:%s' % (base, params.get('section_id')), section_title,
                              shape='wide' if progress else 'poster',
                              kind='continue' if progress else 'catalog', subtitle=brand,
                              loader=self._catalog_loader(params, progress=progress),
                              meta={'params': params, 'server': server, 'progress': progress,
                                    'auto_shape': not progress}))
        rows.append(R.Row(base + ':libraries', self.tr('المكتبات'), shape='landscape',
                          kind='catalog', subtitle=brand,
                          loader=self._library_loader(server, S.menu_params(server), skey),
                          meta={'server': server, 'libraries': True}))
        for lib in self._server_libs.get(skey) or []:
            params = lib['params']
            rows.append(R.Row('%s:lib:%s' % (base, S.library_key(params)), lib['title'], shape='poster',
                              kind='catalog', subtitle=brand, loader=self._catalog_loader(params),
                              meta={'params': params, 'server': server, 'auto_shape': True,
                                    'library': True}))
        for section_title, params in sections:
            if params.get('section_type') in S.LEAD_SECTIONS:
                continue                # shown first
            rows.append(R.Row('%s:%s' % (base, params.get('section_id')), section_title, shape='poster',
                              kind='catalog', subtitle=brand, loader=self._catalog_loader(params),
                              meta={'params': params, 'server': server, 'auto_shape': True}))
        # v5.10.198: Silo's collections
        for key, names, params in S.silo_collection_rows(server):
            rows.append(R.Row('%s:%s' % (base, key), self.tr(names[0]), shape='poster', kind='catalog',
                              subtitle=brand, loader=self._catalog_loader(params),
                              meta={'params': params, 'server': server}))
        return rows

    # ------------------------------------------------------------ live TV
    def _live_rows(self):
        """The Live TV page (v5.10.104): one source at a time, chosen in the tab bar."""
        from . import live_sources as LS
        sources = LS.sources(self.app)
        self._live_sources = sources
        tabs = [{'key': s['key'], 'label': s['label'], 'hint': s.get('hint') or '',
                 'icon': s.get('icon') or '', 'plot': s.get('description') or ''} for s in sources]
        selected = self._pick_tab('live', tabs, avoid='setup')
        self._show_tabs(tabs, selected)
        source = [s for s in sources if s['key'] == selected][0]
        from . import vod
        support = vod.available(source)
        from ..ui_preferences import iptv_available
        self.prop('dh.live.channels.enabled', '1' if iptv_available('channels') else '')
        self.prop('dh.live.vod.movie', '1' if 'movie' in support else '')
        self.prop('dh.live.vod.series', '1' if 'series' in support else '')
        self.prop('dh.live.channels.label', self.tr('القنوات'))
        self.prop('dh.live.movie.label', self.tr('الأفلام'))
        self.prop('dh.live.series.label', self.tr('المسلسلات'))
        self.prop('dh.live.now.label', self.tr('الآن'))
        self.prop('dh.live.next.label', self.tr('بعده'))
        if not iptv_available('channels'):
            self._live_section = self._live_section if self._live_section in support else (support[0] if support else '')
        elif self._live_section != 'channels' and self._live_section not in support:
            self._live_section = 'channels'
        self.prop('dh.live.section', self._live_section)
        if source['kind'] == 'setup':
            # v5.10.140: the sources tab works with the channels section
            # hidden too (it was empty, and no source could be added)
            return self._live_setup_rows(sources)
        if not self._live_section:
            return []
        if self._live_section in vod.TYPES:
            return vod.rows(source, self.app, self._live_section)
        if source['kind'] == 'pvr':
            return self._pvr_rows()
        return self._source_rows(source)

    def select_live_section(self, section):
        if self.mode != 'live' or section not in ('channels', 'movie', 'series'):
            return
        from ..ui_preferences import iptv_available
        if not iptv_available(section):
            return
        if section == 'channels' and self.vod_only:
            self.close_window()
            return
        if section != 'channels' and not self.getProperty('dh.live.vod.' + section):
            return
        if section == self._live_section:
            self.go('rows')
            return
        self.stop_trailer(wait=False)
        self._live_section = section
        self.prop('dh.live.section', section)
        with self._lock:
            self.rows = []
            self.ri = 0
            self._shown = {}
            self._tab_rows_after = True
            self.prop('dh.loading', '1')
            self.prop('dh.empty', '')
            self._render_rows()
            self.show_hero(None, animate=False)
        self._start_build()

    def _pvr_rows(self):
        """Kodi's PVR: its channel groups, recently watched first."""
        from . import live
        card = self.app.media_path('channel_card.jpg')
        rows = []
        try:
            groups = live.groups()
        except live.LiveError:
            groups = []
        total = live.channel_count() if groups else 0
        with self._lock:
            had = any(row.key.startswith('live:g:') for row in self.rows)
        if total and not had and self.rows:
            # the first channels of a new setup: the page opens on them
            self._jump_first = True
        if total:
            rows.append(self._favorite_row('pvr', card))
            recent = R.Row('live:recent:pvr', self.tr('شاهدتها مؤخراً'), shape='landscape', kind='catalog',
                           meta={'live': 'recent'})
            recent.loader = lambda patience=0.0: (live.recent_tiles(self.app.profile, card), None)
            rows.append(recent)
            # the playlist's own groups first; every channel together last
            for group in [g for g in groups if not g['all']] + [g for g in groups if g['all']]:
                label = self.tr('كل القنوات') if group['all'] else (group['label'] or self.tr('قنوات'))
                row = R.Row('live:g:%d' % group['id'], label, shape='landscape', kind='catalog',
                            meta={'live': 'group',
                                  'params': {'live_group': group['id'], 'label': label, 'start': 0}})
                row.loader = self._channel_loader(row, group['id'], label, card)
                rows.append(row)
            return rows
        if live.has_client() and _now() - self._opened_at < 45.0:
            # Kodi's PVR is still starting (Kodi just started, or a playlist
            # was just saved): look again shortly
            self.app.scheduler.call_later(self.key + ':live-retry', 4.0, self._start_build)
        cards = self._setup_cards((('live:pvr', 'Kodi PVR', 'IPTV Simple',
                                    'قائمة M3U أو اشتراك Xtream عبر IPTV Simple في Kodi، مع دليل Kodi والتسجيل المؤقت.',
                                    'live_pvr.jpg'),))
        rows.append(R.Row('live:setup', self.tr('لا توجد قنوات في Kodi بعد'), shape='landscape',
                          kind='catalog', tiles=cards,
                          subtitle=self.tr('أضف قائمة في IPTV Simple، أو أضف مصدراً من تبويب المصادر'),
                          meta={'live': 'setup'}))
        return rows

    def _channel_loader(self, row, group_id, label, card):
        def load(patience=0.0):
            from . import live
            tiles, more, total = live.group_tiles(group_id, label, card)
            row.subtitle = self.tr('عدد القنوات: %d') % total if total else ''
            return tiles, more
        return load

    def _favorite_row(self, source, card):
        from . import live_favorites as LF
        row = R.Row('live:favorites:%s' % source, self.tr('مفضلة القنوات'), shape='landscape',
                    kind='catalog', meta={'live': 'favorites', 'src': source, 'epg': source != 'pvr'})
        load = lambda patience=0.0: (LF.tiles(self.app.profile, source, card), None)
        row.loader = self._live_loader(row, load) if source != 'pvr' else load
        return row

    def _source_rows(self, source):
        """A live source other than the PVR: recently watched, then its own rows."""
        from . import live_sources as LS
        card = self.app.media_path('channel_card.jpg')
        key = source['key']
        rows = [self._favorite_row(key, card)]
        recent = R.Row('live:recent:%s' % key, self.tr('شاهدتها مؤخراً'), shape='landscape',
                       kind='catalog', meta={'live': 'recent', 'src': key, 'epg': True})
        recent.loader = self._live_loader(
            recent, lambda patience=0.0: (LS.recent_tiles(self.app.profile, key, card), None))
        rows.append(recent)
        try:
            specs = LS.rows(source, self.app, changed=self._live_source_changed)
        except Exception as exc:
            self.app.log('live source %s not read: %s' % (source['kind'], LS.failure(exc)), xbmc.LOGWARNING)
            self._build_reason = self.tr(LS.safe(exc))
            specs = []
        for spec in specs:
            row = R.Row(spec['key'], spec['title'], shape='landscape', kind='catalog',
                        subtitle=spec.get('subtitle') or '',
                        meta={'live': 'group', 'params': spec['params'], 'src': key,
                              'epg': bool(spec.get('epg'))})
            row.loader = self._live_loader(row, spec['loader'])
            rows.append(row)
        if len(rows) == 2 and not self._build_reason:
            self._build_reason = self.tr('لا توجد قنوات في هذا المصدر')
        return rows

    def _live_loader(self, row, inner):
        def load(patience=0.0):
            from . import live_sources as LS
            try:
                if (row.meta.get('params') or {}).get('catalog'):
                    from . import catalog_controls as CC
                    (tiles, more), summary = CC.load(row.meta['params'], self.app,
                        lambda params: LS.page_tiles(params, self.app))
                    from . import grid_filters as GF
                    summaries = getattr(self, '_filter_summaries', None)
                    if summaries is None:
                        summaries = self._filter_summaries = {}
                    summaries[GF.page_key(row.meta['params'])] = summary
                else:
                    tiles, more = inner(patience)
            except Exception as exc:
                raise RuntimeError(self.tr(LS.safe(exc)))
            if tiles and row.meta.get('epg'):
                self.app.bg.submit(lambda: self.fill_epg(tiles), priority=4,
                                   key=('epg', self.key, row.key))
            return tiles, more
        return load

    def _live_source_changed(self, key):
        """A source read again in the background (its playlist or guide): its rows follow."""
        if self._closing or self.mode != 'live' or (key and key != self._tab):
            return
        with self._lock:
            busy = any(row.state == 'loading' for row in self.rows)
        if busy:
            # rows still loading read the old data: once they are in, again
            self.app.scheduler.call_later(self.key + ':live-changed', 1.5,
                                          lambda: self._live_source_changed(key))
            return
        self.live_refresh()

    def _live_changed_callback(self):
        return self._live_source_changed

    def _setup_cards(self, cards):
        tiles = []
        for command, title, hint, plot, art in cards:
            path = self.app.media_path(art)
            tiles.append({'kind': 'action', 'command': command, 'title': self.tr(title),
                          'label': self.tr(title), 'shape': 'landscape', 'hint': self.tr(hint),
                          'plot': self.tr(plot), 'poster': path, 'landscape': path})
        return tiles

    def _live_setup_rows(self, sources):
        """The Sources tab: add a source, and the sources already there."""
        from . import live_stremio
        dexworld = any(s.get('dexworld') for s in sources)
        add = self._setup_cards((
            ('live:m3u', 'قائمة M3U', 'داخل Dex Hub',
             'أضف رابط قائمة قنوات M3U، ومعه رابط دليل البرامج XMLTV إن وجد. تظهر مجموعاتها صفوفاً هنا بدون أي إضافة ثانية.',
             'live_m3u.jpg'),
            ('live:xtream', 'اشتراك Xtream', 'السيرفر والحساب',
             'أضف اشتراك IPTV بنظام Xtream Codes. تظهر أقسامه صفوفاً هنا مع البرنامج الحالي والتالي.',
             'live_xtream.jpg'),
            ('live:dexworld', 'اشتراك DexWorld', 'مربوط' if dexworld else 'مفتاح الاشتراك',
             'قنوات اشتراكك في DexWorld مع دليل البرامج. يكفي مفتاح الاشتراك، ولا يضيف شيئاً للصفحة الرئيسية.',
             'live_dexworld.jpg'),
            ('live:stremio', 'إضافات Stremio', 'القنوات من إضافاتك',
             'أي إضافة Stremio فيها كتالوجات قنوات تظهر هنا تبويباً مستقلاً. أضفها برابط manifest.json.',
             'live_stremio.jpg'),
            ('live:pvr', 'Kodi PVR', 'IPTV Simple',
             'قائمة M3U أو اشتراك Xtream عبر IPTV Simple في Kodi، مع دليل Kodi والتسجيل المؤقت.',
             'live_pvr.jpg'),
        ))
        if live_stremio.live_off() and live_stremio.saved_key() and not dexworld:
            # v5.10.110: unlinked from Live TV, its key still saved: the card
            # links it again at once
            add[2]['hint'] = self.tr('مفصول عن البث المباشر')
        elif live_stremio.addon_key() and not dexworld:
            add[2]['hint'] = self.tr('مفتاحك في Dex IPTV')
        rows = [R.Row('live:setup:add', self.tr('أضف مصدر قنوات'), shape='landscape', kind='catalog',
                      tiles=add, meta={'live': 'setup'})]
        mine = []
        for source in sources:
            if source['kind'] == 'setup':
                continue
            art = self.app.media_path(source.get('card') or 'live_stremio.jpg')
            mine.append({'kind': 'action', 'command': 'live:src:%s' % source['key'],
                         'title': source['label'], 'label': source['label'], 'shape': 'landscape',
                         'hint': source.get('hint') or '',
                         'plot': self.tr('اضغط لعرض قنواته أو تحديثه أو حذفه.'),
                         'poster': art, 'landscape': art})
        if mine:
            rows.append(R.Row('live:setup:mine', self.tr('مصادرك'), shape='landscape', kind='catalog',
                              tiles=mine, meta={'live': 'setup'}))
        return rows

    def live_source_added(self, key):
        if self.mode != 'live':
            self.notify(self.tr('أُضيف المصدر'))
            return
        self._tabs_sig = None
        self._tab = ''
        from . import live_sources
        live_sources.remember_tab(self.app.profile, 'live', key)
        with self._lock:
            self.rows = []
            self.ri = 0
            self._shown = {}
            self.prop('dh.loading', '1')
            self._render_rows()
        self._tab_rows_after = True
        self._start_build()

    def channel_watched(self, tile):
        super(BrowseWindow, self).channel_watched(tile)
        with self._lock:
            recent = [row for row in self.rows if row.key.startswith('live:recent')]
        if recent and recent[0].loader is not None:
            self._load_row(recent[0], priority=5, refresh=True)

    def live_refresh(self):
        """Read the channels again; the rows on screen stay until the fresh ones load."""
        if self.mode != 'live':
            return
        if self._tab == 'dw' or str(self._tab).startswith('st:'):
            from . import live_stremio
            live_stremio.refresh(self._tab)
        if self._live_section in ('movie', 'series'):
            from . import vod
            vod.invalidate(self.app.profile, self._tab)
        with self._lock:
            for row in self.rows:
                if row.tiles:
                    row.meta['stale'] = True
            self._fresh_rows = True
        self._opened_at = _now()
        self._start_build()

    def _live_tick(self):
        """Every half minute: programme lines and progress move on in place.

        Nothing is rebuilt while the guide already knows what comes next. A
        row whose channels ran past what it knows is read again (at most every
        five minutes), which also brings in guide updates.
        """
        from . import live

        def job():
            now = time.time()
            with self._lock:
                rows = [r for r in self.rows if (r.meta or {}).get('live') in ('group', 'recent', 'favorites')
                        and r.state == 'ready' and r.tiles]
            due = []
            for row in rows:
                behind = False
                for tile in list(row.tiles):
                    if tile.get('kind') != 'channel':
                        continue
                    before = (tile.get('hint'), tile.get('progress'))
                    live.update_times(tile, now)
                    if (tile.get('hint'), tile.get('progress')) != before:
                        self.update_tile(tile, props={'subtitle': tile.get('hint') or ''},
                                         progress=tile.get('progress') or 0)
                    cur = tile.get('now') or {}
                    if cur and (not tile.get('next') or (cur.get('end') and cur['end'] <= now)):
                        behind = True
                if behind and now - (row.loaded_at or 0) > 300:
                    due.append(row)
            hero = self._hero_tile
            if hero is not None and hero.get('kind') == 'channel':
                self.refresh_hero_if(hero)
            if due:
                for row in due:
                    row.meta['stale'] = True
                self._ensure_loaded(self.ri)
        self.app.bg.submit(job, priority=4, key=('live-tick', self.key))

    def _library_loader(self, server, params, skey=None):
        def load(patience=0.0):
            from .. import capture
            from . import servers as S
            api = self.app.api()
            entries, _elapsed = capture.run_route(params, api, patience=patience)
            if not entries and capture.last_error():
                raise RuntimeError(capture.last_error())
            tiles = S.library_tiles(entries, server, self.app.media_path)
            hint = self.tr('افتحها لعرض كل ما فيها، ومن زر القائمة تضيفها للصفحة الرئيسية.')
            for tile in tiles:
                tile['plot'] = hint
            if skey is not None:
                sections = []
                if server['service'] == 'silo':
                    # Silo's own Home sections, in the server's order
                    try:
                        found, _elapsed = capture.run_route({'action': 'silo_home'}, api, patience=patience)
                        sections = S.section_rows(found)
                    except Exception:
                        sections = []
                known = skey in self._server_libs
                self._server_libs[skey] = [{'title': t['title'], 'params': t['params']} for t in tiles]
                self._server_sections[skey] = sections
                if not known and (tiles or sections) and self.mode == 'servers' and self._tab in (skey, 'all'):
                    # the libraries are known now: their rows join the page
                    # (the rows already there stay as they are). A page that
                    # opened on the library cards (a server with no other
                    # row yet) opens on its first row instead. (v5.10.167:
                    # on All, the chips and the header get the new count)
                    if self._tab == skey:
                        with self._lock:
                            current = self.rows[self.ri] if 0 <= self.ri < len(self.rows) else None
                        if current is None or current.key.endswith(':libraries'):
                            self._jump_first = True
                    self._start_build()
            return tiles, None
        return load

    def add_to_home(self, spec):
        """Save ``spec`` as a row of the Home, right under its progress rows."""
        from .layout import new_custom, custom_key
        layout = self.app.layout
        page = layout.page('all')
        for existing in page['custom']:
            if existing.get('params') and existing.get('params') == spec.get('params'):
                self.notify(self.tr('هذا الصف موجود في الرئيسية'))
                return False
        spec = new_custom(spec)
        order = list(page['order'])
        root = self.app._root()
        if not order and root is not None and getattr(root, 'mode', '') == 'home' \
                and getattr(root, 'media', '') == 'all':
            order = [row.key for row in list(root.rows)]
        if order:
            at = 0
            for key in ('continue', 'nextup'):
                if key in order:
                    at = max(at, order.index(key) + 1)
            order.insert(at, custom_key(spec))
        ok = layout.save_page('all', order=order or None, custom=page['custom'] + [spec])
        self.notify(self.tr('أُضيف للصفحة الرئيسية') if ok else self.tr('تعذر الحفظ'))
        return ok

    def _add_library_to_home(self, tile):
        from .layout_pick import library_spec
        spec = library_spec(self.app, tile.get('brand') or '', tile.get('params') or {},
                            tile.get('title') or '')
        if not spec:
            return
        where = '%s  •  %s' % (tile.get('brand') or '', tile.get('server') or '')
        spec.update({'subtitle': where, 'source_label': where, 'server_row': 'library'})
        self.add_to_home(spec)

    def _add_row_to_home(self, row):
        from . import servers as S
        params = (row.meta or {}).get('params') or {}
        server = (row.meta or {}).get('server') or {}
        if not params.get('action'):
            return
        # the server's name goes with the row: on the Home it stands among others
        title = row.title
        name = server.get('name') or ''
        if name and name not in title:
            title = '%s  •  %s' % (name, title)
        self.add_to_home(S.row_spec(title, server, params,
                                    progress=bool((row.meta or {}).get('progress')),
                                    library=bool((row.meta or {}).get('library'))))

    def _progress_loader(self, action, media=None):
        media = media or self.media
        # v5.10.204: Continue Watching split into films and series, and the
        # recently watched films or series, as rows of their own
        route, only, shape = R.OWN_ROWS.get(action, ({'action': action}, '', 'landscape'))
        playing = route.get('action') not in ('recently_watched', 'lib_because')

        def load(patience=0.0):
            # v5.10.117: one read serves the all, movies and series tabs
            tiles, more = R.progress_tiles(route['action'], self.app.api(), patience=patience,
                                           params=route, shape=shape)
            kind = only or media
            if kind == 'movie':
                tiles = [t for t in tiles if t.get('media_type') == 'movie']
            elif kind == 'series':
                tiles = [t for t in tiles if t.get('media_type') in ('series', 'episode')]
            if playing:
                for tile in tiles:
                    tile['cw'] = True
            return tiles, more
        return load

    def _catalog_loader(self, params, progress=False):
        def load(patience=0.0):
            from ..dexhub import client as _client
            from . import catalog_controls as CC
            from . import grid_filters as GF
            def fetch(route):
                hit = None if getattr(_client._FRESH, 'on', False) else R.recall_page(route)
                if hit is not None:
                    return hit
                tiles, more = R.capture_tiles(route, self.app.api(), 'landscape' if progress else 'poster',
                                              works_only=False, limit=200, patience=patience)
                if tiles and not progress:
                    R.remember_page(route, tiles, more)
                return tiles, more
            if progress:
                tiles, more = fetch(params)
                for tile in tiles:
                    tile['cw'] = True
            else:
                (tiles, more), summary = CC.load(params, self.app, fetch)
                if not tiles and summary:
                    title = self.tr('لا توجد نتائج — غيّر الفلتر')
                    card = self.app.media_path('grid_filter.png')
                    tiles = [{'kind': 'action', 'command': 'catalog_filter', 'title': title,
                              'label': title, 'hint': summary, 'poster': card, 'landscape': card,
                              'folder': False}]
                summaries = getattr(self, '_filter_summaries', None)
                if summaries is None:
                    summaries = self._filter_summaries = {}
                summaries[GF.page_key(params)] = summary
            return tiles, more
        return load

    def _folder_sources(self, ref):
        """(folder, [(key, title, subtitle, params)]) of a collection folder's rows."""
        from .. import nuvio_collection_ui as ncu
        _set, _group, folder = ncu._nuvio_find_group_folder(
            ref.get('set_id'), ref.get('group_id'), ref.get('folder_id'))
        if not folder:
            return None, []
        out = []
        sources = (folder.get('payload') or {}).get('sources') or []
        media_filter = ref.get('media_filter') or ''
        indexes = R.matching_sources(folder, media_filter) or R.matching_sources(folder, '')
        try:
            show_all = ncu._collection_show_all(ref.get('set_id'), ref.get('group_id'), len(indexes))
        except Exception:
            show_all = len(indexes) > 1
        if show_all:
            # Nuvio's "All" tab: every source of the folder merged, in order
            params = {'action': 'collection_nuvio_sources_open', 'set_id': ref.get('set_id') or '',
                      'group_id': ref.get('group_id') or '', 'folder_id': ref.get('folder_id') or '',
                      'source_indexes': ','.join(str(i) for i in indexes), 'page': '1'}
            out.append(('src:all', self.tr('الكل'), self.tr('%d كتالوج') % len(indexes), params))
        for idx in indexes:
            source = ncu._compat_nuvio_source(sources[idx])
            try:
                name, suffix = ncu._nuvio_source_label(source)
            except Exception:
                name, suffix = (source.get('name') or 'Catalog'), ''
            params = {'action': 'collection_nuvio_source_open', 'set_id': ref.get('set_id') or '',
                      'group_id': ref.get('group_id') or '', 'folder_id': ref.get('folder_id') or '',
                      'source_index': str(idx), 'page': '1'}
            out.append(('src:%d' % idx, name, suffix, params))
        return folder, out

    _PREFETCH_ROWS = 3

    def prefetch_folder(self, tile):
        """The first rows of a collection folder, read while the cursor rests on
        its card (v5.10.106, after Nuvio Hub): opening it then takes them
        from memory. Nothing is read while a video plays or the cursor moved on."""
        from ..ui_preferences import enabled
        if not enabled('homeui_follow_rows'):
            return
        ref = (tile or {}).get('folder_ref') or {}
        if not ref.get('folder_id') or self._real_playing:
            return
        mark = (ref.get('set_id'), ref.get('group_id'), ref.get('folder_id'), ref.get('media_filter'))

        def still_there():
            current = self._current_tile() if not (self._closing or self._covered) else None
            cref = (current or {}).get('folder_ref') or {}
            return (cref.get('set_id'), cref.get('group_id'), cref.get('folder_id'),
                    cref.get('media_filter')) == mark

        def job():
            try:
                _folder, specs = self._folder_sources(ref)
            except Exception:
                return
            started, read = _now(), 0
            for _key, _title, _sub, params in specs[:self._PREFETCH_ROWS]:
                if self._real_playing or not still_there():
                    break       # the cursor moved on, or a video started
                if R.known_page(params):
                    continue
                try:
                    tiles, more = R.capture_tiles(params, self.app.api(), 'poster',
                                                  works_only=False, limit=200)
                except Exception:
                    break
                if tiles:
                    read += 1
                    R.remember_page(params, tiles, more)
                    self.prefetch_logos(tiles[:6])
            if read:
                self.app.log('folder "%s": %d row(s) read ahead in %.0f ms' % (
                    tile.get('title') or '', read, (_now() - started) * 1000))
        self.app.bg.submit(job, priority=7, key=('folder-pre', mark))

    def _folder_rows(self):
        api = self.app.api()
        ref = self.folder_ref
        from ..folder_view import rows_enabled
        self._folder_view_mode = rows_enabled()
        folder, specs = self._folder_sources(ref)
        if not folder:
            return []
        wrap = getattr(api, '_wrap_tokenized_url', None) or (lambda u: u)
        tiles = R.folder_tiles({'id': ref.get('group_id'), 'payload': {'folders': [folder]}},
                               ref.get('set_id'), wrap=wrap, gif_enabled=False)
        page = tiles[0] if tiles else {'kind': 'folder', 'title': self.page_title}
        self._page_tile = page
        self.prop('dh.page.title', page.get('title') or self.page_title)
        self._banner_hold = not self._user_moved
        self.show_hero(page)
        if page.get('hero_video'):
            # the folder's banner video plays while its rows load
            self.schedule_trailer(page, delay=0.6)
        from ..folder_view import rows_enabled
        if not rows_enabled():
            from ..catalog_navigation import folder_tiles
            cards = folder_tiles(ref, api)
            return [R.Row('folder:catalogs', self.page_title, shape='landscape', kind='nav',
                          tiles=cards, subtitle=self.tr('اختر كتالوجًا'))]
        rows = []
        for key, title, subtitle, params in specs:
            rows.append(R.Row(key, title, shape='poster', kind='catalog', subtitle=subtitle,
                              loader=self._catalog_loader(params), meta={'params': params}))
        return rows

    # ------------------------------------------------------------ search
    # v5.10.102: search in the Home's own design. The search runs the same
    # engine as Dex Hub's search screen (TMDb, the Stremio add-ons and the
    # media servers together) and its results come back as rows: the user's
    # own libraries, movies, series, anime and people, the best match first.
    # The first row starts a new search or repeats a recent one.
    SEARCH_ROWS = (('lib', 'من مكتباتك'), ('iptv', 'من اشتراك IPTV'), ('movie', 'أفلام'), ('series', 'مسلسلات'),
                   ('anime', 'أنمي'), ('person', 'أشخاص'), ('tv', 'قنوات'))
    SEARCH_ROW_LIMIT = 60

    def ask_search(self):
        """Kodi's keyboard for a new query, on the window's GUI thread."""
        self.stop_trailer(wait=False)
        keyboard = xbmc.Keyboard(self.search or '', self.tr('ابحث عن فيلم أو مسلسل أو أنمي أو شخص'))
        keyboard.doModal()
        self._last_input = _now()
        if not keyboard.isConfirmed():
            return
        query = (keyboard.getText() or '').strip()
        if query:
            self.start_search(query)

    def start_search(self, query):
        self.stop_trailer(wait=False)
        self.search = str(query or '').strip()
        # the page shows the search itself until the user moves
        self._user_moved = False
        self._search_moved = False
        self._banner_hold = True
        self.prop('dh.page.title', self.tr('بحث: %s') % self.search)
        self.prop('dh.loading', '1')
        with self._lock:
            if self.ri != 0 and self.rows:
                self._goto_row(0, anim='u')
        self._start_build()

    def _brand_fanart(self):
        return os.path.join(self.app.addon_path, 'resources', 'media', 'fanart.jpg')

    def _search_bar_row(self):
        art = self.app.media_path('search_new.png')
        tiles = [{'kind': 'query', 'query': '', 'title': self.tr('بحث جديد'),
                  'label': self.tr('بحث جديد'), 'shape': 'landscape', 'poster': art, 'landscape': art,
                  'fanart': self._brand_fanart(),
                  'plot': self.tr('اكتب اسم فيلم أو مسلسل أو أنمي أو شخص، ويبحث Dex Hub في '
                                  'TMDb وإضافاتك وسيرفراتك معاً.')}]
        try:
            from .. import search_history
            recent = search_history.recent(limit=16) or []
        except Exception:
            recent = []
        current = (self.search or '').strip().casefold()
        art = self.app.media_path('search_recent.png')
        for entry in recent:
            query = str((entry or {}).get('query') or '').strip()
            if not query or query.casefold() == current:
                continue
            tiles.append({'kind': 'query', 'query': query, 'title': query, 'label': query,
                          'shape': 'landscape', 'poster': art, 'landscape': art,
                          'fanart': self._brand_fanart(),
                          'plot': self.tr('من عمليات البحث السابقة. اضغط لتبحث عنه مرة أخرى.'),
                          'menu': [[self.tr('حذف من السجل'), '__row_search_forget__'],
                                   [self.tr('مسح سجل البحث'), '__row_search_clear__']]})
            if len(tiles) > 12:
                break
        subtitle = (self.tr('بحث جديد أو بحث سابق') if len(tiles) > 1
                    else self.tr('ابحث في كل مصادرك'))
        return R.Row('search:bar', self.tr('البحث'), shape='landscape', kind='search',
                     tiles=tiles, subtitle=subtitle)

    def _search_hero(self, title, plot, hint=''):
        tile = {'kind': 'query', 'title': title, 'plot': plot, 'hint': hint,
                'fanart': self._brand_fanart()}
        self._page_tile = tile
        self.show_hero(tile)
        return tile

    def _set_row_subtitle(self, row, text):
        with self._lock:
            row.subtitle = text
            for which in ('A', 'B'):
                shown = self._shown.get(which)
                if shown and shown[0] == row.key:
                    self.prop('dh.row%s.subtitle' % which, text)

    def _search_rows(self, gen):
        query = (self.search or '').strip()
        bar = self._search_bar_row()
        if not query:
            self.prop('dh.page.title', self.tr('البحث'))
            self._search_hero(self.tr('البحث'), self.tr(
                'ابحث عن فيلم أو مسلسل أو أنمي أو شخص في TMDb وإضافاتك وسيرفراتك معاً.'))
            return [bar]

        def stop():
            return self._closing or gen != self._build_gen

        # the bar and a loading row at once, the results when they are in
        pending = R.Row('search:pending', self.tr('النتائج'), shape='poster', kind='search',
                        subtitle=self.tr('جاري البحث…'))
        if stop():
            return []
        self._install_rows([bar, pending], initial=True)
        hero = self._search_hero(query, self.tr('جاري البحث في TMDb وإضافاتك وسيرفراتك…'))
        started = _now()
        seen = {'at': 0.0}

        def progress(done, total, results):
            now = _now()
            if now - seen['at'] < 0.25 and done < total:
                return
            seen['at'] = now
            text = self.tr('%d من %d مصدر') % (done, total)
            if results:
                text = '%s  •  %s' % (text, self.tr('%d نتيجة') % results)
            self._set_row_subtitle(pending, text)
            hero['hint'] = text
            self.refresh_hero_if(hero)

        early = {'n': 0}

        def partial(found_so_far):
            # v5.10.117: the providers that answered show at once (TMDb in
            # well under a second); the rows grow as the others answer
            if stop():
                return
            rows = self._search_result_rows(found_so_far)
            total = sum(len(row.tiles) for row in rows)
            if not rows or total <= early['n']:
                return
            first = not early['n']
            early['n'] = total
            self._keep_row_places(rows)
            self._install_rows([bar] + rows, initial=True)
            hero['plot'] = self.tr('انزل للنتائج: الأقرب لبحثك أولاً.')
            self.refresh_hero_if(hero)
            if first:
                self.app.log('search "%s": first results in %.0f ms' % (query, (_now() - started) * 1000))
                self._search_shown([bar] + rows)

        try:
            found, status = self.app.api().hub_search_collect(query, progress=progress, should_stop=stop,
                                                              partial=partial)
        except Exception:
            self.app.log('search failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            found, status = [], 'error'
        if stop():
            return []
        rows = self._search_result_rows(found)
        self._keep_row_places(rows)
        self.app.log('search "%s": %s, %d result(s) in %d row(s), %.0f ms' % (
            query, status, len(found), len(rows), (_now() - started) * 1000))
        if not rows:
            hero['title'] = self.tr('لا توجد نتائج لـ "%s"') % query
            hero['plot'] = self.tr('جرّب كلمة أخرى، أو اسم العمل بالإنجليزية، أو معرّفاً مثل tt1234567.')
            if status == 'no_catalogs':
                hero['plot'] = self.tr('لا توجد مصادر تدعم البحث. اربط حساباً أو أضف إضافة من الحسابات والمصادر.')
            elif status == 'error':
                hero['plot'] = self.tr('تعذر البحث الآن، حاول مرة أخرى بعد قليل.')
            hero['hint'] = ''
            self.refresh_hero_if(hero)
            return [bar]
        total = sum(len(row.tiles) for row in rows)
        hero['hint'] = self.tr('%d نتيجة') % total
        hero['plot'] = self.tr('انزل للنتائج: الأقرب لبحثك أولاً.')
        self.refresh_hero_if(hero)
        return [bar] + rows

    def _keep_row_places(self, rows):
        """A row shown again keeps the title the user is on (v5.10.117: the
        search's rows are replaced as more providers answer)."""
        with self._lock:
            before = dict((row.key, row.selected) for row in self.rows)
        for row in rows:
            if row.key in before and row.tiles:
                row.selected = min(before[row.key], len(row.tiles) - 1)

    def _search_result_rows(self, found):
        groups = {}
        best = {}
        for entry in found or []:
            tile = R.search_tile(entry)
            if tile is None:
                continue
            if entry.get('server'):
                key = 'lib'
            elif entry.get('iptv'):
                key = 'iptv'       # v5.10.239: from the DexWorld IPTV subscription
            else:
                media = str(entry.get('media_type') or '').lower()
                if media in ('person', 'people') or entry.get('is_person'):
                    key = 'person'
                elif entry.get('is_anime') or media == 'anime':
                    key = 'anime'
                elif media == 'movie':
                    key = 'movie'
                elif media in ('series', 'show', 'tvshow'):
                    key = 'series'
                else:
                    key = 'tv'
            tiles = groups.setdefault(key, [])
            if len(tiles) < self.SEARCH_ROW_LIMIT:
                tiles.append(tile)
            best[key] = max(best.get(key, 0.0), tile.get('_score') or 0.0)
        order = dict((key, index) for index, (key, _title) in enumerate(self.SEARCH_ROWS))
        titles = dict(self.SEARCH_ROWS)
        rows = []
        for key in sorted(groups, key=lambda k: (-round(best.get(k, 0.0), 3), order.get(k, 99))):
            tiles = groups[key]
            if key == 'lib':
                tiles.sort(key=lambda t: -(t.get('_score') or 0.0))
            R.drop_brand_logos(tiles)
            rows.append(R.Row('search:%s' % key, self.tr(titles.get(key, key)), shape='poster',
                              kind='search', tiles=tiles,
                              subtitle=self.tr('%d نتيجة') % len(tiles)))
        # v5.10.174: the best matches first, as big banners with their logos
        # (Apple TV's Top Results); the rows by kind follow
        picks = [t for key in ('movie', 'series', 'anime') for t in groups.get(key, [])
                 if t.get('fanart') and t.get('fanart') != t.get('poster')]
        picks.sort(key=lambda t: -(t.get('_score') or 0.0))
        if len(picks) >= 3:
            rows.insert(0, R.Row('search:top', self.tr('أفضل النتائج'), shape='wide', kind='search',
                                 tiles=picks[:8], subtitle=self.query_hint()))
        return rows

    def query_hint(self):
        return self.tr('لـ "%s"') % (self.search or '').strip() if (self.search or '').strip() else ''

    def _search_shown(self, rows):
        """The results are in: go down to them unless the user went somewhere."""
        if self._closing or getattr(self, '_search_moved', False) or len(rows) < 2:
            return
        with self._lock:
            index = self._next_visible(0, 1)
            if index is None or self.ri != 0:
                return
            self._banner_hold = False
            self._goto_row(index, anim='d')
        self._focus_changed()
        self._prefetch_focus_art()

    def _search_forget(self, everything=False):
        from .. import search_history
        tile = self._selected_tile()
        try:
            if everything:
                if not xbmcgui.Dialog().yesno('Dex Hub', self.tr('مسح كل سجل البحث؟')):
                    return
                search_history.clear_all()
            elif tile and tile.get('query'):
                search_history.remove(tile['query'])
        except Exception:
            self.app.log('search history change failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        bar = self._search_bar_row()
        with self._lock:
            for index, row in enumerate(self.rows):
                if row.key == bar.key:
                    bar.selected = min(row.selected, len(bar.tiles) - 1)
                    self.rows[index] = bar
            self._shown = {}
            self._render_rows()
        self._focus_changed()

    # ------------------------------------------------------------- cache
    def _cache_path(self):
        return os.path.join(self.app.profile, 'home_%s.json' % self.media)

    def _read_cache_rows(self):
        try:
            with open(self._cache_path(), 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
        except Exception:
            return []
        if time.time() - float(data.get('t') or 0) > 7 * 86400:
            return []
        from .layout import retired_af3
        return [snap for snap in (data.get('rows') or []) if isinstance(snap, dict) and not retired_af3(snap)]

    def _load_cache(self):
        if self.mode != 'home':
            return []
        rows = []
        for snap in self._read_cache_rows():
            tiles = [dict(t) for t in (snap.get('tiles') or []) if isinstance(t, dict)]
            # Continue Watching reloads within a second, and its native resume
            # links expire after a day, so an old copy is never shown
            if not tiles or snap.get('kind') == 'continue':
                continue
            row = R.Row(snap.get('key') or '', snap.get('title') or '', shape=snap.get('shape') or 'poster',
                        kind=snap.get('kind') or 'catalog', tiles=tiles,
                        subtitle=snap.get('subtitle') or '', meta=snap.get('meta') or {})
            row.more = snap.get('more')
            row.meta['stale'] = True
            rows.append(row)
        # the saved copy follows the current layout, so a row the user just
        # hid or moved never flashes back in its old place
        return self.app.layout.apply(rows, self.media)

    def _save_cache_soon(self):
        # at most one write every 20 s while rows keep arriving
        self._save_due = max(_now() + 4.0, getattr(self, '_saved_at', 0.0) + 20.0)

    def _save_cache(self):
        """Keep the last good tiles of every row for the next start.

        A row whose server is slow or down this time keeps the tiles it had
        last time (v5.10.97: the whole Home, not only its first rows, so a
        slow Continue Watching or Next Up row is never blank on the next
        visit either).
        """
        if self.mode != 'home':
            return
        self._saved_at = _now()
        try:
            previous = {}
            for snap in self._read_cache_rows():
                previous[snap.get('key') or ''] = snap
            snaps = []
            for row in self.rows:
                snap = None
                if row.kind == 'continue':
                    continue
                if row.state == 'ready' and row.tiles and not row.meta.get('stale'):
                    snap = row.snapshot()
                    snap['tiles'] = snap['tiles'][:30]
                    snap['meta'] = {k: v for k, v in (row.meta or {}).items()
                                    if not k.startswith('_') and k != 'stale'}
                elif row.kind != 'continue':
                    snap = previous.get(row.key)
                if snap:
                    snaps.append(snap)
                if len(snaps) >= 40:
                    break
            if not snaps:
                return
            tmp = self._cache_path() + '.tmp'
            text = json.dumps({'t': time.time(), 'rows': snaps}, ensure_ascii=False, separators=(',', ':'))
            with open(tmp, 'w', encoding='utf-8') as handle:
                handle.write(text)
            os.replace(tmp, self._cache_path())
        except Exception:
            self.app.log('home cache save failed:\n%s' % traceback.format_exc())

    # ------------------------------------------------------ install/load
    def _install_rows(self, rows, initial=False):
        first_install = False
        with self._lock:
            if self._closing or not self._build_is_current():
                return
            focus_before = self.focus_id()
            prev_key = self.rows[self.ri].key if 0 <= self.ri < len(self.rows) else None
            jumped = getattr(self, '_jump_first', False)
            if jumped:
                self._jump_first = False
                prev_key = None
                self.ri = 0
            if not initial and not rows and self.rows:
                rows = self.rows
            elif not initial and self.rows:
                rows = self._merge_fresh(rows)
            self.rows = rows
            if prev_key is not None:
                for index, candidate in enumerate(rows):
                    if candidate.key == prev_key:
                        self.ri = index
                        break
            if not initial:
                self._built = True
            if self.ri >= len(self.rows) or not self._row_visible(self.ri):
                self.ri = self._first_visible()
            if not initial:
                self.prop('dh.loading', '')
                self.prop('dh.empty', '' if rows else '1')
                self.prop('dh.empty.reason', '' if rows else self._build_reason)
                brand = self._brand_tile
                if brand is not None and brand.get('plot'):
                    brand['plot'] = ''
                    self.refresh_hero_if(brand)
            self._shown = {}
            self._render_rows()
            if (self.zone == 'rows' and not self._immersive and self._focused_once
                    and focus_before in ROW_A_IDS + (0, SINK) and focus_before != self.rows_focus()):
                self.focus(self.rows_focus())
            if rows and not self._focused_once:
                self._focused_once = True
                first_install = True
        if first_install:
            self._initial_focus()
        elif jumped:
            self._focus_changed()
        if not initial and self._tab_rows_after and rows:
            # a tab was opened with Down: its rows take the focus
            self._tab_rows_after = False
            self.go('rows')
        elif self.zone == 'tools' and self.mode == 'live':
            selected = self.focus_id()
            if ((selected == 96 and not self.getProperty('dh.live.vod.movie'))
                    or (selected == 97 and not self.getProperty('dh.live.vod.series'))
                    or (selected == 94 and not (self.getProperty('dh.live.vod.movie')
                                                or self.getProperty('dh.live.vod.series')))):
                self._focus_page_tools()
        elif not initial and not rows and self._tabs and self.zone == 'rows':
            # nothing on this tab: the tab bar keeps the focus
            self._tab_rows_after = False
            self.go('tabs')
        if not initial:
            self._ensure_loaded(self.ri)
        self._pick_carousel()

    def _merge_fresh(self, fresh):
        """Keep cached tiles on screen until each fresh row has loaded."""
        old = {r.key: r for r in self.rows}
        for row in fresh:
            prev = old.get(row.key)
            if prev is not None and row.state == 'pending' and prev.tiles:
                row.tiles = prev.tiles
                row.more = prev.more
                row.selected = prev.selected
                row.meta['stale'] = True
        return fresh

    def _initial_focus(self):
        self.trace('initial focus (carousel %d)' % len(self.carousel))
        if self.mode == 'home' and self.carousel_on and self.carousel:
            self.go('hero')
        elif self.mode in ('library', 'servers') and self._tabs:
            # v5.10.164: the page opens on its header and chips, as skin.dexhub
            self.go('tabs')
        else:
            self.go('rows')

    def _header(self, synced, linked, title, stats):
        """The header of My Library and My Servers (v5.10.164, skin.dexhub's
        design): a line, the title, up to four numbers."""
        self.prop('dh.hdr.synced', synced)
        self.prop('dh.hdr.linked', linked)
        self.prop('dh.hdr.title', title)
        for n in range(1, 5):
            value, label = stats[n - 1] if n <= len(stats) else ('', '')
            self.prop('dh.hdr.s%d' % n, value)
            self.prop('dh.hdr.s%dl' % n, label)

    def _library_header(self, linked):
        def work():
            from ..routes import library
            try:
                library._publish_header(linked)
            except Exception as exc:
                self.app.log('library header: %s' % exc)
            home = xbmcgui.Window(10000)
            stats = []
            for n in range(1, 5):
                value = home.getProperty('dhs.lib.s%d' % n)
                if value:
                    stats.append((value, home.getProperty('dhs.lib.s%dl' % n)))
            if self.mode == 'library':
                self._header(home.getProperty('dhs.lib.synced'), home.getProperty('dhs.lib.linked'),
                             self.tr('مكتبتي'), stats)
        self.app.bg.submit(work, priority=2, key='library-header')

    def _row_visible(self, index):
        return 0 <= index < len(self.rows) and self.rows[index].visible

    def _first_visible(self):
        for index, row in enumerate(self.rows):
            if row.visible:
                return index
        return 0

    def _next_visible(self, start, step):
        index = start + step
        while 0 <= index < len(self.rows):
            if self.rows[index].visible:
                return index
            index += step
        return None

    def _ensure_loaded(self, center):
        # A video playing behind the page needs the network and the CPU more
        # than rows the user has not reached yet.
        from .loading import Budget
        if not hasattr(self, '_load_budget'):
            self._load_budget = Budget()
        ahead = self._load_budget.depth(light=self.s.flag('homeui_light_mode', True),
                                       busy=self._real_playing)
        with self._lock:
            # the row above too: a row still pending there would otherwise
            # stand between the focus and whatever is above the rows
            window = list(self.rows[center:center + ahead])
            if 0 < center <= len(self.rows):
                window.append(self.rows[center - 1])
        self._cancel_row_jobs(keep=window if not self._real_playing else ())
        if self._closing or self._covered or self._real_playing:
            return
        for offset, row in enumerate(window):
            self.app.workers.prioritize(('row', self.key, id(row)), 1 + offset)
            if row.state == 'pending' and row.loader is not None:
                self._load_row(row, priority=1 + offset)
            elif row.meta.get('stale') and row.loader is not None and row.state == 'ready':
                row.meta.pop('stale', None)
                self._load_row(row, priority=3 + offset, refresh=True)

    def _cancel_row_jobs(self, keep=()):
        """A page no longer needs queued rows outside its visible window."""
        kept = {id(row) for row in keep}
        match = lambda key: (isinstance(key, tuple) and len(key) == 3 and
                             key[:2] == ('row', self.key) and key[2] not in kept)
        removed = self.app.workers.cancel_where(match) + self.app.bg.cancel_where(match)
        cancelled = {key[2] for key in removed}
        for row in getattr(self, 'rows', []):
            if id(row) in cancelled:
                row.state = 'ready' if row.tiles else 'pending'
                if row.tiles:
                    row.meta['stale'] = True

    def _load_row(self, row, priority=5, refresh=False, patience=0.0):
        if refresh and row.meta.get('choice_changed'):
            row.meta['_choice_epoch'] = int(row.meta.get('_choice_epoch') or 0) + 1
        choice_epoch = int(row.meta.get('_choice_epoch') or 0)
        if row.state == 'loading':
            return
        if not refresh:
            row.state = 'loading'

        def job():
            if self._closing or self._covered or row not in self.rows or self._real_playing:
                row.state = 'ready' if row.tiles else 'pending'
                if row.tiles:
                    row.meta['stale'] = True
                return
            started = _now()
            _notes_begin()
            try:
                if refresh:
                    # the row's saved copy is on screen: read its source's
                    # current page, not a stale copy of it (v5.10.106)
                    from ..dexhub import client as _client
                    with _client.fresh_reads():
                        tiles, more = row.loader(patience=patience)
                else:
                    tiles, more = row.loader(patience=patience)
                error = ''
            except Exception as exc:
                tiles, more, error = [], None, str(exc)
            notice = '' if tiles else _notes_last()
            if hasattr(self, '_load_budget'):
                self._load_budget.record(_now() - started)
            with self._lock:
                if self._closing or row not in self.rows:
                    return
                obsolete = choice_epoch != int(row.meta.get('_choice_epoch') or 0)
                if obsolete:
                    row.state = 'ready' if row.tiles else 'pending'
            if obsolete:
                # A sort/filter changed while this request was in flight.
                # Wait for the worker key to release, then load the new choice.
                self.app.scheduler.call_later(
                    '%s:choice:%s' % (self.key, row.key), 0.1,
                    lambda: self._load_row(row, priority=0, refresh=True))
                return
            if error and not patience and self._slow_server(error) and not row.meta.get('_patient'):
                # the server answers, only slower than the fast budget: ask
                # once more in the background with time to spare
                row.meta['_patient'] = True
                self.app.scheduler.call_later(
                    '%s:patient:%s' % (self.key, row.key), 1.0,
                    lambda: self._load_row(row, priority=6, refresh=True, patience=12.0))
            with self._lock:
                changed_choice = row.meta.pop('choice_changed', False)
                transient = (not changed_choice) and (bool(error) or (row.kind != 'continue'
                                            and (refresh or row.meta.get('stale'))))
                if not tiles and row.tiles and transient:
                    # offline or a failed refresh: keep what is already on screen
                    row.state = 'ready'
                    row.meta.pop('stale', None)
                    kept = True
                else:
                    kept = False
            if kept:
                self._row_loaded(row)
                return
            with self._lock:
                from .loading import selection
                keep = selection(row.tiles, tiles, row.selected)
                unchanged = row.tiles == tiles and row.more == more and row.error == error
                row.tiles = tiles
                row.more = more
                row.error = error
                row.meta['notice'] = notice
                if row.meta.get('auto_shape') and tiles:
                    # an added row takes the shape of what it holds
                    row.shape = R.dominant_shape(tiles, row.shape)
                row.state = 'ready' if tiles else ('error' if error else 'empty')
                row.selected = min(keep, max(0, len(tiles) - 1))
                if not unchanged:
                    row.version += 1
                row.loaded_at = time.time()
                row.meta.pop('stale', None)
            why = ''
            if error:
                # v5.10.117: why a row came back empty (addresses and
                # accounts masked: an IPTV address carries the password)
                try:
                    from . import live_sources as _LS
                    why = ': %s' % _LS.clean_trace(error)[:200]
                except Exception:
                    why = ''
            self.app.log('row "%s" %d item(s) in %.0f ms%s' % (row.title, len(tiles), (_now() - started) * 1000, why))
            self._row_loaded(row)
        lane = self.app.bg if patience else self.app.workers
        if not lane.submit(job, priority=priority, key=('row', self.key, id(row))):
            if not refresh and row.state == 'loading':
                row.state = 'pending'

    @staticmethod
    def _slow_server(error):
        text = str(error or '').lower()
        return 'timed out' in text or 'timeout' in text

    def _empty_reason(self):
        """Why a page with no rows is empty, in the route's own words when it said so."""
        if self.mode == 'servers' and getattr(self, '_tab', '') == 'all' and not any(
                r.state == 'error' for r in self.rows):
            # v5.10.172: All holds only what is being watched and what was added
            return self.tr('لا شيء تكمله أو أضيف مؤخراً على سيرفراتك. اختر سيرفراً لترى مكتباته.')
        texts = []
        for row in self.rows:
            if row.state not in ('error', 'empty'):
                continue
            notice = row.meta.get('notice') or ''
            if notice:
                texts.append(notice)
        for row in self.rows:
            if row.state == 'error' and row.error:
                texts.append(row.error)
        for text in texts:
            if self.mode != 'live' and re.search(r'HTTP (401|403)\b', str(text)):
                # the server answers but refuses the account (v5.10.104); an
                # IPTV source's row says it in its own words (v5.10.110)
                return self.tr('السيرفر رفض حسابك (HTTP 401). اربط الحساب من جديد، أو اطلب من صاحب السيرفر مشاركته معك مرة ثانية.')
        for row in self.rows:
            if row.state not in ('error', 'empty'):
                continue
            notice = row.meta.get('notice') or ''
            if notice:
                return screen_reason(self.tr(notice))
        for row in self.rows:
            if row.state == 'error' and row.error:
                if self._slow_server(row.error):
                    return self.tr('الخادم بطيء في الرد الآن، حاول مرة أخرى بعد قليل')
                return screen_reason(row.error)
        return self._build_reason or ''

    def _row_loaded(self, row):
        refocus = False
        try:
            self._dress_posters(row.tiles)      # v5.10.288: the poster choice
        except Exception:
            pass
        with self._lock:
            if self._closing:
                return
            current = self.rows[self.ri] if 0 <= self.ri < len(self.rows) else None
            if current is row and not row.visible:
                nxt = self._next_visible(self.ri, 1)
                if nxt is None:
                    nxt = self._next_visible(self.ri, -1)
                self.ri = nxt if nxt is not None else self._first_visible()
                self._shown = {}
            elif row.visible and self.mode in TAB_MODES and (self.zone != 'rows' or not self._user_moved):
                # v5.10.167: the rows not entered yet (or entered by the page
                # itself, no key pressed yet) show from the first one, as
                # skin.dexhub's Home does: Continue Watching arriving after a
                # catalog row comes first, not above the screen
                first = self._first_visible()
                if first < self.ri:
                    self.ri = first
                    self._shown = {}
            focus_before = self.focus_id()
            a_before = self._shown.get('A')
            self._render_rows()
            a_changed = self._shown.get('A') != a_before
            self.prop('dh.loading', '')
            nothing = not any(r.visible for r in self.rows)
            self.prop('dh.empty', '1' if nothing else '')
            self.prop('dh.empty.reason', self._empty_reason() if nothing else '')
            in_rows = self.zone == 'rows' and not self._immersive
            parked = focus_before in (0, SINK) or (focus_before in ROW_A_IDS
                                                   and focus_before != self._a_id)
            refocus = in_rows and not nothing and (a_changed or parked)
            if refocus and parked and self._displayed.get(self._a_id):
                self.focus(self._a_id)
        if nothing and self.zone == 'rows' and not self._immersive and self.mode == 'home':
            self.go('hero' if self.carousel else 'nav')
        elif nothing and self.zone == 'rows' and not self._immersive and self._tabs:
            # an empty server or source: the tab bar keeps the focus
            self.go('tabs')
        elif refocus:
            self._focus_changed()
        self._pick_carousel()
        self._save_cache_soon()
        self._ensure_loaded(self.ri)
        self._prefetch_focus_art()

    def _prefetch_focus_art(self):
        """Fetch what the row on screen will need on focus: logo checks and GIFs."""
        with self._lock:
            row = self.rows[self.ri] if self._row_visible(self.ri) else None
            tiles = list(row.tiles[:12]) if row is not None else []
            kind = row.kind if row is not None else ''
            under_index = self._next_visible(self.ri, 1) if row is not None else None
            under = list(self.rows[under_index].tiles[:8]) if under_index is not None else []
        self.prefetch_logos(tiles)
        if self._real_playing:
            return
        # v5.10.173: big banner cards draw each title's logo, so their titles
        # look theirs up ahead (both rows on screen), whatever the language
        with self._lock:
            under_row = self.rows[under_index] if under_index is not None else None
        # v5.10.174: My Library and My Servers too (tracking lists and server
        # copies seldom carry a logo)
        everywhere = getattr(self, 'mode', '') in ('library', 'servers')
        wide = (tiles[:10] if row is not None and (row.shape in LOGO_SHAPES or everywhere) else []) + \
            (under[:6] if under_row is not None and (under_row.shape in LOGO_SHAPES or everywhere) else [])
        for tile in wide:
            if tile.get('kind') == 'work' and not tile.get('clearlogo'):
                self.enrich(tile, priority=6, background=True)
        language = self._tmdb_language()
        if language:
            # the row's titles get their plot and logo in the user's language
            # before the cursor reaches them
            for tile in tiles[:8]:
                self.enrich(tile, priority=7, background=True)
        # v5.10.111: a title its source gave no poster (most search results
        # from add-ons, a server copy without one) takes TMDb's, on both rows
        # on screen, whatever the TMDb language: it is not left an empty card
        for tile in (tiles[:8] if not language else []) + under:
            if tile.get('kind') == 'work' and not tile.get('poster'):
                self.enrich(tile, priority=8, background=True)
        if not self.gif_on or kind != 'collection':
            return
        if not self.s.flag('homeui_light_mode', True):
            for tile in tiles[:8]:
                self.ensure_focus_art(tile, priority=5, background=True)

    # ------------------------------------------------------------ render
    def _render_rows(self, anim=''):
        a = self.rows[self.ri] if self._row_visible(self.ri) else None
        b_index = self._next_visible(self.ri, 1) if a is not None else None
        b = self.rows[b_index] if b_index is not None else None
        self._render_one('A', a)
        self._render_one('B', b)
        if anim:
            self._row_anim += 1
            self.prop('dh.rows.anim', '%s%d' % (anim, self._row_anim % 2))

    def _render_one(self, which, row):
        prefix = 'dh.row%s.' % which
        ids = ROW_A_IDS if which == 'A' else ROW_B_IDS
        base = ROW_A_BASE if which == 'A' else ROW_B_BASE
        token = (row.key, row.version, row.state, len(row.tiles),
                 self.app.layout.shape(row.key) or row.shape) if row is not None else None
        if which in self._shown and self._shown[which] == token:
            return
        self._shown[which] = token
        if row is None:
            for control_id in ids:
                self.clear_list(control_id)
            for key in ('title', 'subtitle', 'shape', 'state', 'kind'):
                self.prop(prefix + key, '')
            return
        pick = self.app.layout.shape(row.key)
        from .layout import is_progress_row
        if pick in COLLAGE_SHAPES and is_progress_row(row):
            pick = ''           # v5.10.189: Continue Watching keeps its cards
        look = ''
        walls = row.kind == 'collection' and ((pick or row.shape) in COLLAGE_SHAPES or
                                              (row.shape == 'shelf' and not pick and row.meta.get('walls')))
        if row.meta.get('shelf') or walls:
            # v5.10.179: a collection row the user made: its cards' look is
            # the row's style (wall, mosaic, stack); its shape stays.
            # v5.10.184: a collection row (Networks...) in a collection style
            # shows each folder as a wall card, as skin.dexhub does
            look = pick if pick in COLLAGE_SHAPES else (row.shape if row.shape in COLLAGE_SHAPES else
                                                        (row.meta.get('walls') or 'collage'))
            if walls:
                row.meta['walls'] = look
            row.shape, pick = 'shelf', ''
        if pick and pick != row.shape:
            row.shape = pick        # v5.10.173: the shape the user chose for this row
            row.meta['auto_shape'] = False
        target = _shape_id(base, row.shape)
        if which == 'A':
            self._a_id = target
        else:
            self._b_id = target
        self.prop(prefix + 'title', row.title)
        from . import grid_filters as GF
        summary = getattr(self, '_filter_summaries', {}).get(GF.page_key((row.meta or {}).get('params') or {}), '')
        from ..ui_preferences import enabled
        self.prop(prefix + 'subtitle', row.subtitle if enabled('homeui_show_row_source') else '')
        self.prop(prefix + 'shape', row.shape)
        self.prop(prefix + 'kind', row.kind)
        self.prop(prefix + 'state', 'loading' if (row.state in ('pending', 'loading') and not row.tiles) else 'ready')
        tiles = list(row.tiles)
        if row.shape in COLLAGE_SHAPES:
            # v5.10.176: the whole catalog as one collection banner (5.10.177: three looks)
            tiles = [self._collage_tile(row)] if tiles else []
        elif look:
            if walls:
                from ..skinui import walls as W
                tiles = W.dress('addon', {'id': row.key, 'title': row.title}, tiles,
                                on_done=lambda r=row: self._walls_done(r))
            for tile in tiles:
                tile['cstyle'] = look
        # v5.10.286: See all only when the source has more than the row shows
        if row.shape not in COLLAGE_SHAPES and row.kind == 'catalog' and not row.meta.get('shelf') and tiles and (row.more or (row.meta.get('params') or {}).get('action')) \
                and R.row_has_more(row.tiles, row.more):
            tiles.append(self._more_tile(row))
        self.fill_list(target, tiles, select=row.selected if which == 'A' else 0)
        if row.shape in RANKED_SHAPES:
            # v5.10.175: Top 10 style; 5.10.177: the number stands beside the card
            with self._lock:
                items = list(self._items.get(target) or [])
            for index, li in enumerate(items[:RANKED_MAX]):
                if (tiles[index] or {}).get('kind') in ('work', 'folder'):
                    li.setProperty('rank', str(index + 1))
        for control_id in ids:
            if control_id != target:
                self.clear_list(control_id)

    def _walls_done(self, row):
        """The posters of a collection row's folders came in: draw it again."""
        try:
            with self._lock:
                if self._closing:
                    return
                row.version = (row.version or 0) + 1 if isinstance(row.version, int) else row.version
                self._shown = {}
            self._row_loaded(row)
        except Exception:
            pass

    def _collage_tile(self, row):
        """One card for the row (style Collection): its posters as a wall."""
        # v5.10.177: a title without a poster lends its other art, so the
        # banner is never a wall of empty cards
        def art(t):
            return t.get('poster') or t.get('thumb') or t.get('cover') or t.get('landscape') or t.get('fanart') or ''
        works = [t for t in row.tiles if art(t) and t.get('kind') != 'more']
        lead = next((t for t in row.tiles if t.get('fanart')), None) or (works[0] if works else {})
        count = len(row.tiles)
        line = row.subtitle or ''
        tile = {'kind': 'more', 'title': row.title, 'label': row.title, 'shape': row.shape,
                'cstyle': row.shape,
                'poster': art(lead), 'landscape': lead.get('fanart') or '',
                'fanart': lead.get('fanart') or '', 'row_key': row.key,
                'plot': line or (self.tr('%d عمل') % count),
                'collage': ([art(t) for t in works] * (20 // max(1, len(works)) + 1))[:20],
                'collage_line': '  ·  '.join(p for p in (line, self.tr('%d عمل') % count) if p),
                'collage_button': self.tr('عرض الكل')}
        from .row_styles import preview_fields
        tile['collection_preview'] = preview_fields(works)
        return tile

    def _more_tile(self, row):
        tile = row.meta.get('_more_tile')
        if tile is None:
            art = self.app.media_path('more_tile.png')
            tile = {'kind': 'more', 'title': self.tr('عرض الكل'), 'label': self.tr('عرض الكل'),
                    'shape': row.shape, 'poster': art, 'landscape': art, 'fanart': '',
                    'row_key': row.key}
            row.meta['_more_tile'] = tile
        return tile

    # ------------------------------------------------------------ moving
    def _row_a(self):
        with self._lock:
            return self.rows[self.ri] if self._row_visible(self.ri) else None

    def _selected_tile(self):
        with self._lock:
            row = self._row_a()
            if row is None:
                return None
            list_id = self._a_id
            shown = self._displayed.get(list_id) or []
        pos = self.selected_pos(list_id, row.selected)
        if pos < 0 or pos >= len(shown):
            return None
        row.selected = pos
        return shown[pos]

    def _goto_row(self, index, anim=''):
        row = self._row_a()
        if row is not None and self._displayed.get(self._a_id):
            row.selected = max(0, self.selected_pos(self._a_id, row.selected))
        self.ri = index
        self._render_rows(anim=anim)
        if self.zone == 'rows':
            self.focus(self.rows_focus())

    def _move_rows(self, step):
        if self._trailer_tile is not None or self.app.director.active:
            self.stop_trailer(wait=False, park=True)
        with self._lock:
            target = self._next_visible(self.ri, step)
            if target is not None:
                self._goto_row(target, anim='d' if step > 0 else 'u')
        if target is None:
            if step < 0 and self.mode == 'home':
                self.go('hero' if self.carousel else 'nav')
            elif step < 0 and self.mode == 'addons':
                self.go('nav')
            elif step < 0 and self._tabs:
                if self.mode == 'live':
                    self._focus_page_tools()
                else:
                    self.go('tabs')
            elif step < 0 and self._real_playing:
                self.set_zone('np')
                self.focus(NP)
            return
        self._focus_changed()
        self._ensure_loaded(self.ri)
        self._prefetch_focus_art()

    # ------------------------------------------------------------- focus
    def rows_focus(self):
        """Row A's list when it has items; the sink while it is still loading.

        Kodi refuses to focus an empty list and leaves nothing focused, so a
        loading row parks the focus on the sink and _row_loaded moves it on.
        """
        return self._a_id if self._displayed.get(self._a_id) else SINK

    def _current_tile(self):
        if self.zone == 'rows':
            return self._selected_tile()
        carousel = self.carousel
        if self.zone == 'hero' and carousel:
            return carousel[self.ci % len(carousel)]
        return self._hero_tile

    def _focus_changed(self):
        """The item under the cursor changed: debounce hero, art and trailer."""
        if self._covered or self._closing:
            return
        tile = self._current_tile()
        if tile is None:
            return
        if self._banner_hold:
            if not self._user_moved:
                self.ensure_focus_art(tile)
                return
            self._banner_hold = False
        if not same_work(tile, self._trailer_tile):
            self.stop_trailer(wait=False, park=True)
        self.app.scheduler.call_later(self.key + ':hero', 0.12, self._hero_due)
        self.ensure_focus_art(tile)

    def _hero_due(self):
        # resolved again here: right after a row change the list only now
        # reports the position that was queued for it
        if self._covered or self._closing:
            return
        tile = self._current_tile()
        if tile is None or tile.get('kind') == 'more':
            return
        self.show_hero(tile)
        self.ensure_focus_art(tile)
        self.enrich(tile, priority=2, focus=True)
        from .catalog_follow import update as follow_catalog
        self.app.scheduler.call_later(self.key + ':follow', 0.4, lambda: follow_catalog(self, self._current_tile())
                                      if not self._closing and not self._covered and self._current_tile() else None)
        self.channel_epg_soon(tile)
        self.schedule_trailer(tile)
        if tile.get('kind') == 'folder' and tile.get('folder_ref'):
            # a short rest on a collection's card reads its first rows ahead
            self.app.scheduler.call_later(self.key + ':folder-pre', 0.6,
                                          lambda: self._prefetch_if_still(tile))
        elif self.server_folder_params(tile):
            self.app.scheduler.call_later(self.key + ':folder-pre', 0.6,
                                          lambda: self._prefetch_server_if_still(tile))

    def _prefetch_if_still(self, tile):
        if not self._covered and not self._closing and self._current_tile() is tile:
            self.prefetch_folder(tile)

    # ---------------------------------------------------------- carousel
    def _pick_carousel(self):
        if not self.carousel_on or self.carousel:
            return
        with self._lock:
            if self.carousel:
                return
            source = fallback = None
            for row in self.rows:
                if row.kind != 'catalog' or row.state != 'ready' or len(row.tiles) < 3:
                    continue
                if not any(t.get('kind') == 'work' for t in row.tiles):
                    continue
                if not row.meta.get('stale'):
                    source = row
                    break
                if fallback is None:
                    fallback = row
            if source is None:
                # cached rows only feed the billboard once the fresh rows are
                # in and nothing better is still loading (offline start)
                if (fallback is None or not self._built
                        or any(r.state == 'loading' for r in self.rows)):
                    return
                source = fallback
            from ..ui_preferences import hero_order
            picks = hero_order([t for t in source.tiles if t.get('kind') == 'work'],
                               getattr(source, 'key', '') or '')[:8]
            if not picks:
                return
            self.carousel = picks
            self.ci = 0
            self._carousel_at = _now()
        for tile in picks:
            self.enrich(tile, priority=4, background=True)
        self.prefetch_logos(picks)
        self._render_dots()
        if self.zone == 'hero':
            self._focus_changed()
            if self.focus_id() in (0, SINK):
                self.focus(HERO_BTNS)
        elif not self._user_moved and self.mode == 'home':
            # The billboard arrived after the first rows: open on it, as long
            # as the user has not started moving around yet.
            self.go('hero')

    def _render_dots(self):
        items = [xbmcgui.ListItem(label=str(index)) for index in range(len(self.carousel))]
        try:
            with self._lock:
                ctrl = self._set_items(DOTS, items)
                if items:
                    ctrl.selectItem(self.ci)
        except Exception:
            pass
        self._setup_hero_buttons()

    def _setup_hero_buttons(self):
        with self._lock:
            if not self.carousel:
                if self._items.get(HERO_BTNS):
                    try:
                        self._set_items(HERO_BTNS, [])
                    except Exception:
                        pass
                self._buttons_ready = False
                return
            if self._buttons_ready or self._items.get(HERO_BTNS):
                return
            self._buttons_ready = True
        items, keys = [], []
        for key, label in (('play', 'تشغيل'), ('info', 'معلومات أكثر'), ('trailer', 'التريلر')):
            li = xbmcgui.ListItem(label=self.tr(label))
            li.setProperty('key', key)
            li.setProperty('icon', self.app.media_path('btn_%s.png' % key))
            items.append(li)
            keys.append(key)
        try:
            self._set_items(HERO_BTNS, items, keys)
        except Exception:
            self._buttons_ready = False

    def _advance_carousel(self, step=1):
        with self._lock:
            if not self.carousel:
                return
            self.ci = (self.ci + step) % len(self.carousel)
            self._carousel_at = _now()
            try:
                self._control(DOTS).selectItem(self.ci)
            except Exception:
                pass
        self._focus_changed()

    def after_trailer_end(self):
        if self.zone == 'hero' and self.carousel and self.is_active():
            self._trailer_auto += 1
            self.app.scheduler.call_later(self.key + ':advance', 1.2, lambda: self._advance_carousel(1))

    def trailer_wanted(self, tile):
        if self._trailer_auto >= 4 and self.zone == 'hero':
            return False
        return super(BrowseWindow, self).trailer_wanted(tile)

    def tick_extra(self):
        now = _now()
        if (self.zone == 'hero' and self.carousel and not self.app.director.active
                and not self._immersive
                and now - self._carousel_at > self.carousel_interval
                and now - self._last_input > 4.0 and not self.dialog_open()):
            self._advance_carousel(1)
        if self._save_due and now >= self._save_due:
            self._save_due = 0.0
            self._save_cache()
        if self.mode == 'live' and now - self._live_tick_at >= _LIVE_TICK:
            self._live_tick_at = now
            self._live_tick()

    # --------------------------------------------------------- playback
    def on_real_playback(self, playing):
        if playing:
            if self._immersive:
                self.exit_immersive()
        else:
            if self.zone == 'np':
                self.go('hero' if (self.mode == 'home' and self.carousel) else 'rows')
            self._ensure_loaded(self.ri)

    # Holding Back leaves Dex Hub while a video plays; a press returns to the
    # video. Three ways a hold reaches the Home:
    #   * a key Kodi has a long-press mapping for (Esc on a keyboard) arrives
    #     once, with the long-press bit set in its button code;
    #   * browser_back (the box's remote) is mapped by Kodi itself to
    #     ActivateWindow(Home), which App._check_left_behind turns into the
    #     same exit;
    #   * any other key (Backspace, most CEC remotes) repeats: a burst of
    #     Back actions, the first repeat 250 to 600 ms after the press.
    # So a plain press waits for that first repeat before opening the player.
    _MODIFIER_LONG = 0x01000000
    _BACK_FIRST_GAP = 0.6
    _BACK_GAP = 0.32
    _BACK_HELD = 3

    def _back_while_playing(self, action=None):
        try:
            held = bool(int(action.getButtonCode()) & self._MODIFIER_LONG) if action is not None else False
        except Exception:
            held = False
        now = _now()
        if self._exit_armed:
            # still held: every repeat is swallowed here, so none of them
            # reaches Kodi's Home (where Back would open the video again)
            self._back_last = now
            return
        gap = self._BACK_FIRST_GAP if self._back_count == 1 else self._BACK_GAP
        if now - self._back_last > gap:
            self._back_count = 0
        self._back_count += 1
        self._back_last = now
        if held or self._back_count >= self._BACK_HELD:
            self._back_count = 0
            self._exit_armed = True
            self.app.log('Back held on the Home while a video plays: leaving Dex Hub')
            self.app.scheduler.call_later(self.key + ':back', self._BACK_GAP + 0.05, self._exit_when_released)
            return
        wait = self._BACK_FIRST_GAP if self._back_count == 1 else self._BACK_GAP
        self.app.scheduler.call_later(self.key + ':back', wait + 0.03, self._back_settled)

    def _exit_when_released(self):
        if _now() - self._back_last < self._BACK_GAP:
            self.app.scheduler.call_later(self.key + ':back', 0.1, self._exit_when_released)
            return
        self._exit_armed = False
        self.app.close_all()

    def _back_settled(self):
        gap = self._BACK_FIRST_GAP if self._back_count == 1 else self._BACK_GAP
        if _now() - self._back_last < gap:
            self.app.scheduler.call_later(self.key + ':back', 0.1, self._back_settled)
            return
        pressed, self._back_count = self._back_count, 0
        if pressed and self._real_playing and self.is_active() and not self.dialog_open():
            self.go_player()

    def on_playback_returned(self):
        with self._lock:
            for row in self.rows:
                if row.kind == 'continue' and row.loader is not None:
                    self._load_row(row, priority=0, refresh=True)

    def on_progress_changed(self):
        with self._lock:
            for row in self.rows:
                params = row.meta.get('params') or {}
                if row.loader is not None and (row.kind == 'continue' or
                        row.meta.get('progress') or row.key in R.OWN_KEYS or
                        str(params.get('action') or '').startswith('silo_')):
                    row.meta['choice_changed'] = True
                    self._load_row(row, priority=0, refresh=True)

    def on_uncover(self):
        super(BrowseWindow, self).on_uncover()
        if self.mode == 'folder':
            from ..folder_view import rows_enabled
            if getattr(self, '_folder_view_mode', rows_enabled()) != rows_enabled():
                self._switch_media(self.media)
        if (self.mode == 'home' and self._built
                and self.app.layout.stamp() != getattr(self, '_layout_stamp', None)):
            # a row was added to the Home from another page (the Servers page)
            self._reload_layout()
        self.on_playback_returned()

    def _preview_addons_tab(self, key):
        if key == 'addons' and self.mode in ('home', 'library', 'servers') and self.search is None:
            self._addons_return = (self.mode, self.media)
            self.mode = 'addons'
            self._show_tabs([], '')
        elif key != 'addons' and getattr(self, '_addons_return', None):
            self.mode, self.media = self._addons_return
            self._addons_return = None
        else:
            return
        self._hero_tile = None
        self.prop('dh.mode', self.mode)
        self._switch_media(self.media)

    # ----------------------------------------------------------- events
    def onFocus(self, control_id):
        self.trace('focus %s' % control_id)
        if control_id == SINK:
            return
        if control_id in ROW_A_IDS:
            zone = 'rows'
        elif control_id in NAV_BUTTON_KEYS:
            zone = 'nav'
            self._preview_addons_tab(NAV_BUTTON_KEYS[control_id])
        else:
            zone = {NAV: 'nav', NAV_ICONS: 'nav', PROFILE: 'nav', HERO_BTNS: 'hero',
                    NP: 'np', TABS: 'tabs', 93: 'tools', 94: 'tools',
                    96: 'tools', 97: 'tools'}.get(control_id)
        if control_id == HERO_BTNS:
            try:
                pos, last = self._button_pos(HERO_BTNS)
                self._btn_edge = 'left' if pos <= 0 else ('right' if pos >= last else '')
            except Exception:
                pass
        if zone is None or zone == self.zone:
            return
        self.set_zone(zone)
        if zone in ('hero', 'rows'):
            self._focus_changed()
        else:
            self.stop_trailer(wait=False)

    def onClick(self, control_id):
        if control_id in ROW_A_IDS:
            tile = self._selected_tile()
            if tile is not None:
                self._activate(tile)
        elif control_id == HERO_BTNS:
            self._hero_button()
        elif control_id in (NAV, NAV_ICONS) or control_id in NAV_BUTTON_KEYS:
            self._nav_click(control_id)
        elif control_id == 6009:
            self.catalog_menu()
        elif control_id == 93:
            self.page_tools()
        elif control_id in (94, 96, 97):
            self.select_live_section({94: 'channels', 96: 'movie', 97: 'series'}[control_id])
        elif control_id == TABS:
            self._tab_click()
        elif control_id == PROFILE:
            self._profile_click()
        elif control_id == NP:
            self._np_click()
        elif control_id == SINK:
            # a click posted by the app (SendClick) to reach this thread:
            # an editor asked for from Settings while the Home was open
            request, self._request = self._request, ''
            if request == 'layout':
                self.open_layout()
            elif request == 'community':
                self.open_community()
            elif request == 'menu':
                self._menu_popup()
            elif request == 'setup':
                self.open_setup()
            elif request == 'keyboard':
                self.ask_search()
            elif request == 'live':
                self.stop_trailer(wait=False)
                self.app.open_live()
            elif request == 'library':
                self._open_tab_page('library')
            elif request == 'servers':
                self._open_tab_page('servers')
            elif request == 'search':
                self.stop_trailer(wait=False)
                self.app.open_search()

    def _np_click(self):
        key = self._selected_key(NP)
        if key:
            self.player_action(key)

    def post_request(self, request):
        """Hand a job to this window's GUI thread (called from the app's watch loop)."""
        self._request = request
        xbmc.executebuiltin('SendClick(%d,%d)' % (self._window_id, SINK))

    def _button_pos(self, control_id):
        """(selected position, last index) of a button list."""
        with self._lock:
            last = len(self._items.get(control_id) or []) - 1
            pos = int(self._control(control_id).getSelectedPosition())
        return pos, last

    def _hero_edge_step(self, aid):
        """Left on the first button or Right on the last one flips the billboard.

        Kodi moves the button list before Python sees the key, so a press
        counts as a flip only when the list was already resting on that edge.
        """
        try:
            pos, last = self._button_pos(HERO_BTNS)
        except Exception:
            return
        edge = self._btn_edge
        self._btn_edge = 'left' if pos <= 0 else ('right' if pos >= last else '')
        if not self.carousel or len(self.carousel) < 2:
            return
        if aid == A_RIGHT and edge == 'right' and pos >= last:
            self._advance_carousel(1)
        elif aid == A_LEFT and edge == 'left' and pos <= 0:
            self._advance_carousel(-1)

    def _hero_button(self):
        key = self._selected_key(HERO_BTNS)
        if not key:
            return
        tile = self.carousel[self.ci % len(self.carousel)] if self.carousel else None
        if tile is None:
            return
        if key == 'play':
            if tile.get('kind') == 'work':
                # Play keeps Dex Hub's own route (sources or its page)
                self.open_path(tile, helper=False)
            else:
                self._activate(tile)
        elif key == 'info':
            from . import details
            helper = self.tmdbhelper_path(tile)
            if self.title_page() == 'dexhub' and details.supported(tile):
                self.app.open_details(tile)
            elif helper:
                self._launch('ActivateWindow(Videos,%s,return)' % self._arg(helper))
            else:
                self.show_info(tile)
        elif key == 'trailer':
            self.play_trailer_fullscreen(tile)

    def _activate(self, tile):
        kind = tile.get('kind')
        if kind == 'addon':
            # v5.10.167: the Add-ons page: a program runs, a video add-on and a
            # Stremio source open as lists, the last tile adds a source
            addon_id = tile.get('addon_id') or ''
            content = tile.get('content')
            if content == 'add':
                xbmc.executebuiltin('RunPlugin(plugin://plugin.video.dexhub/?action=add_provider)')
            elif content == 'stremio' and tile.get('source_id'):
                xbmc.executebuiltin('ActivateWindow(Videos,plugin://plugin.video.dexhub/?action=provider_menu'
                                    '&provider_id=%s,return)' % tile['source_id'])
            elif addon_id and content == 'video':
                xbmc.executebuiltin('ActivateWindow(Videos,plugin://%s/,return)' % addon_id)
            elif addon_id:
                xbmc.executebuiltin('RunAddon(%s)' % addon_id)
            return
        if kind == 'library':
            title = tile.get('title') or ''
            if tile.get('server'):
                title = '%s  •  %s' % (tile.get('server'), title)
            self.app.open_grid(tile.get('params') or {}, title=title, shape='poster')
            return
        if kind == 'action':
            command = tile.get('command') or ''
            if command == 'catalog_filter':
                self.catalog_menu()
            elif command == 'setup':
                self.open_setup()
            elif command.startswith('live:'):
                self.live_action(command[5:])
            return
        if kind == 'query':
            if tile.get('query'):
                self.start_search(tile['query'])
            else:
                self.ask_search()
            return
        if kind == 'more':
            row = None
            for candidate in self.rows:
                if candidate.key == tile.get('row_key'):
                    row = candidate
                    break
            if row is not None:
                # a fresh row holds the whole first page; a cached one may be
                # cut short, so its grid starts again from page one
                seed = None if row.meta.get('stale') else {'tiles': row.tiles, 'more': row.more}
                self.app.open_grid(row.meta.get('params') or {}, title=row.title, shape=row.shape,
                                   page_tile=seed)
            return
        if kind == 'folder' and tile.get('catalog_node'):
            self.open_path(tile)
            return
        if kind == 'folder':
            ref = tile.get('folder_ref') or {}
            indexes = tile.get('source_indexes') or []
            if len(indexes) == 1:
                params = {'action': 'collection_nuvio_source_open', 'set_id': ref.get('set_id') or '',
                          'group_id': ref.get('group_id') or '', 'folder_id': ref.get('folder_id') or '',
                          'source_index': str(indexes[0]), 'page': '1'}
                self.app.open_grid(params, title=tile.get('title') or '', page_tile={'hero': tile})
            else:
                self.app.open_folder(ref.get('set_id') or '', ref.get('group_id') or '',
                                     ref.get('folder_id') or '', title=tile.get('title') or '',
                                     media_filter=ref.get('media_filter') or '')
            return
        self.open_path(tile)

    def onAction(self, action):
        aid = action.getId()
        if aid in A_IGNORE:
            return
        if self._back_bounce(aid):
            self.app.log('Back right after the page showed: taken as the same press')
            return
        was_immersive = self._immersive
        self.input_seen()
        if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN, A_PGUP, A_PGDN) or aid in A_BACK:
            # Kodi hands a window its click before the key that made it, so
            # the OK that started a search does not count as moving away
            self._search_moved = True
        if was_immersive:
            self.exit_immersive()
            return
        focus = self.focus_id()
        if focus == 6009 and (aid == A_RIGHT or aid in A_BACK):
            self.go('rows')
            return
        if focus in (93, 94, 96, 97) and aid in (A_DOWN, A_PGDN):
            self.go('tabs' if self.mode in ('servers', 'library') and self._tabs else 'rows')
            return
        if focus == 93 and aid in (A_UP, A_PGUP) and self.mode in ('servers', 'library'):
            self.go('nav')      # v5.10.167: the Home's tabs above
            return
        if focus in (93, 94, 96, 97) and (aid in (A_UP, A_PGUP) or aid in A_BACK):
            self.go('tabs')
            return
        if aid in A_BACK and self._real_playing and self.mode == 'home' and self.app._root() is self:
            # a video plays behind the Home: a press returns to it, a held
            # Back leaves Dex Hub (the video keeps playing)
            self._back_while_playing(action)
            return
        if aid in A_PLAY and self._real_playing and focus != NP:
            return      # Kodi's own play/pause handles the running video
        if focus == NP:
            if aid in (A_DOWN, A_PGDN):
                if self.mode == 'home':
                    self.go('hero' if self.carousel else ('rows' if self._row_a() is not None else 'nav'))
                else:
                    self.go('tabs' if self._tabs else 'rows')
            elif aid == A_LEFT and self._tabs:
                self.go('tabs')
            elif aid in A_BACK:
                self.leave()
            return
        if self._debug:
            self._act_n = getattr(self, '_act_n', 0) + 1
            pos = self.selected_pos(focus, -2) if focus in ROW_A_IDS else -1
            self.trace('action %s #%d focus %s zone %s ri %s pos %s' % (aid, self._act_n, focus, self.zone, self.ri, pos))
        if focus in (SINK, 0):
            if self.zone == 'rows' and self._row_a() is not None:
                if aid in (A_DOWN, A_PGDN):
                    self._move_rows(1)
                    return
                if aid in (A_UP, A_PGUP):
                    self._move_rows(-1)
                    return
                if aid in A_BACK:
                    self._back(self._a_id)
                    return
            if aid in A_BACK:
                self.leave()
                return
            target = self.default_focus()
            if target not in (0, SINK):
                self.focus(target)
            return
        if aid in A_BACK:
            self._back(focus)
            return
        if aid in (A_CONTEXT, A_MENU):
            if focus in ROW_A_IDS:
                self.context_menu(self._selected_tile())
            elif focus == HERO_BTNS and self.carousel:
                self.context_menu(self.carousel[self.ci % len(self.carousel)])
            elif focus == TABS and self.mode == 'live':
                key = self._selected_key(TABS)
                if key and key != 'setup':
                    self.manage_live_source(key)
            return
        if aid == A_INFO:
            if focus in ROW_A_IDS:
                self.show_info(self._selected_tile())
            elif focus == HERO_BTNS and self.carousel:
                self.show_info(self.carousel[self.ci % len(self.carousel)])
            return
        if focus in ROW_A_IDS:
            if aid in (A_LEFT, A_RIGHT):
                self._expect.pop(focus, None)
                self._focus_changed()
            elif aid in (A_DOWN, A_PGDN):
                self._move_rows(1)
            elif aid in (A_UP, A_PGUP):
                self._move_rows(-1)
            elif aid in A_PLAY:
                tile = self._selected_tile()
                if tile is not None:
                    self._activate(tile)
        elif focus == HERO_BTNS:
            if aid == A_UP:
                self.go('nav')
            elif aid == A_DOWN:
                self.go('rows')
            elif aid in (A_LEFT, A_RIGHT):
                self._hero_edge_step(aid)
            elif aid in A_PLAY and self.carousel:
                self._activate(self.carousel[self.ci % len(self.carousel)])
        elif focus in (NAV, NAV_ICONS, PROFILE) or focus in NAV_BUTTON_KEYS:
            if aid == A_DOWN:
                if self.mode in ('servers', 'library'):
                    self.go('tabs' if self._tabs else 'rows')
                elif self.mode == 'addons':
                    self.go('rows')
                else:
                    self.go('hero' if self.carousel else 'rows')
        elif focus == TABS:
            if aid in (A_DOWN, A_PGDN):
                # down on another tab opens it and goes to its rows
                key = self._selected_key(TABS)
                if key and key != self._tab:
                    self._select_tab(key, rows_after=False)
                    if self.mode == 'live':
                        self._focus_page_tools()
                    else:
                        self.go('rows')
                elif self.mode == 'live':
                    self._focus_page_tools()
                else:
                    # v5.10.164: My Library and My Servers: the chips sit
                    # right above the rows (the page's tools above them)
                    self.go('rows')
            elif aid in (A_UP, A_PGUP) and self._real_playing:
                self.set_zone('np')
                self.focus(NP)
            elif aid in (A_UP, A_PGUP) and self.mode == 'servers':
                self._focus_page_tools()
            elif aid in (A_UP, A_PGUP) and self.mode == 'library':
                # v5.10.167: My Library has no tools button (Floppy, Trakt and
                # the others are linked in the settings): the Home's tabs. Up
                # went to the hidden button and left nothing focused.
                self.go('nav')

    def _back(self, focus):
        if focus in ROW_A_IDS:
            first = self._first_visible()
            if self.ri != first:
                with self._lock:
                    self._goto_row(first, anim='u')
                self._focus_changed()
                return
            if self.mode == 'home':
                self.go('hero' if self.carousel else 'nav')
                return
        if focus == HERO_BTNS and self.mode == 'home':
            self.go('nav')
            return
        self.leave()


# v5.10.107: Sort, Genre and Filter are one row of three buttons above the
# grid's right end (a list, so Left and Right move between them), always in
# sight; Up from the first row, Right at a row's end and Left at its start
# reach them (the page's XML), Down returns to the titles
BAR = 6100
BAR_KEYS = ('sort', 'genre', 'filter')
# v5.10.108: Left at the start of a row opens the same three as a panel from
# the left (with Reset once a choice differs from the source's own)
SHEET = 6110
# local filters (v5.10.106): pages read on, without the cursor moving, while
# the titles that match fill fewer than this many cards
_LOCAL_FILL = 36
_LOCAL_READS = 8


class GridWindow(_BaseWindow):
    """A catalog, list or folder source as a poster grid with endless paging.

    v5.10.106: a catalog or a server library also gets the Sort, Filter and
    Genre buttons (homeui/grid_filters.py); the choice is saved per page and
    reopens with it. v5.10.107: the buttons sit above the grid's right end and
    every page of titles has them.
    """

    def __init__(self, *args, **kwargs):
        super(GridWindow, self).__init__(*args, **kwargs)
        self.params = dict(kwargs.get('params') or {})
        self._orig_params = dict(self.params)     # the route the page was opened with
        self.title = kwargs.get('title') or ''
        self.shape = kwargs.get('shape') or 'poster'
        seed = kwargs.get('page_tile') or {}
        self.tiles = list(seed.get('tiles') or [])
        self.more = seed.get('more') if self.tiles else None
        self.page_hero = seed.get('hero')
        self._loading = False
        self._exhausted = False
        self._grid_id = _shape_id(GRID_BASE, self.shape)
        self._facets = None         # what the page can filter (grid_filters.describe)
        self._fstate = None         # the page's current choice
        self._rules = {}            # the checks made here on each title
        self._gen = 0               # each choice starts a new generation of pages
        self._local_reads = 0
        self._seen_genres = {}      # genre (lower case) -> [name, titles] of what was read
        self._order = ''            # an order made here (grid_filters.local_sort)
        self._capped = False        # ... over the first titles of a longer catalog
        self._bar_key = 'sort'      # the button the cursor was on last
        # v5.10.108: a server show's or season's own page names its episodes
        self._episode_names = R.is_show_page(self.params)
        self._bar_items = None
        self._sheet_items = None
        self._sheet_keys = []
        self._pending_local = None  # a page that gets its buttons once it shows titles

    def default_focus(self):
        if self.tiles:
            return self._grid_id
        return BAR if self._facets is not None else SINK

    def onInit(self):
        if self._fresh_controls():
            return
        try:
            self._window_id = xbmcgui.getCurrentWindowId()
        except Exception:
            self._window_id = 0
        if self._inited:
            self._mark_shown_again()
            self._covered = False
            self._theme()
            if self.tiles and self.focus_id() not in (BAR, SHEET):
                self.focus(self._grid_id)
            if self._hero_tile is not None:
                self.schedule_trailer(self._hero_tile)
            return
        self._inited = True
        self._gui_thread = threading.current_thread()
        self._warm_controls(GRID_IDS + (HERO_PROGRESS, SINK, NP, BAR, SHEET))
        self._theme()
        self._setup_np_buttons()
        self.prop('dh.kenburns', '1' if self.s.flag('homeui_kenburns', True) else '')
        self.prop('dh.page.title', self.title)
        self.prop('dh.sheet.title', self.tr('ترتيب وفلترة'))
        self.prop('dh.sheet.hint', self.tr('يمين أو رجوع: العودة للعناوين'))
        self.prop('dh.brand.fanart', os.path.join(self.app.addon_path, 'resources', 'media', 'fanart.jpg'))
        self._setup_filters()
        if self.page_hero:
            self.show_hero(self.page_hero)
        if self.tiles:
            self._show(reset=True)
            self.focus(self._grid_id)
            self._focus_changed()
            self.prefetch_logos(self.tiles)
            self._prefetch_text(self.tiles)
            if self.more and len(self.tiles) < 24:
                self._load_page(self.more)
        else:
            self.prop('dh.loading', '1')
            self._load_page(self.params, first=True)

    def _fetch(self, params):
        """(tiles, more, reason) of one page of the grid's source."""
        reason = ''
        try:
            if params.get('live_group') is not None:
                from . import live
                tiles, more = live.page_tiles(params, self.app.media_path('channel_card.jpg'))
            elif params.get('live_src'):
                # a live source's row, page after page (v5.10.104)
                from . import live_sources
                tiles, more = live_sources.page_tiles(params, self.app)
                if any((t.get('epg') or {}).get('dw') or (t.get('epg') or {}).get('xt') for t in tiles):
                    added = list(tiles)
                    self.app.bg.submit(lambda: self.fill_epg(added), priority=4,
                                       key=('epg', self.key, json.dumps(params, sort_keys=True)))
            else:
                from ..dexhub import client as _client
                hit = (R.take_page(params) if params.get('action') in R.SERVER_FOLDER_ACTIONS
                       and not getattr(_client._FRESH, 'on', False) else None)
                if hit is not None:
                    # read ahead while the cursor rested on it (used once, so
                    # watched marks and progress are fresh the next time)
                    tiles, more = hit
                    for tile in tiles:
                        tile['shape'] = self.shape
                else:
                    tiles, more = R.capture_tiles(params, self.app.api(), self.shape,
                                                  works_only=False, limit=200)
        except Exception as exc:
            tiles, more = [], None
            if params.get('live_src'):
                from . import live_sources
                reason = self.tr(live_sources.safe(exc))
            else:
                reason = screen_reason(exc)
        return tiles, more, reason

    def _load_page(self, params, first=False, gen=None):
        with self._lock:
            if gen is not None and gen != self._gen:
                return      # a page of an earlier choice
            if self._loading or not params or self._closing:
                return
            self._loading = True
            gen = self._gen
        if self._order:
            self._load_ordered(params, gen)
            return

        def job():
            if self._closing:
                return
            _notes_begin()
            if gen == getattr(self, '_progress_fresh_gen', -1):
                from ..dexhub import client as _client
                with _client.fresh_reads():
                    tiles, more, reason = self._fetch(params)
            else:
                tiles, more, reason = self._fetch(params)
            read = len(tiles)
            read_on = False
            with self._lock:
                if gen != self._gen:
                    return      # the page was asked for again with another choice
                self._note_genres(tiles)
                if self._rules:
                    from . import grid_filters as GF
                    tiles = [t for t in tiles if GF.passes(t, self._rules)]
                # the first titles may arrive on a later page while local
                # filters skip whole pages
                first_shown = first or (self._rules and not self.tiles)
                if self._rules and more and (len(self.tiles) + len(tiles) < _LOCAL_FILL):
                    if self._local_reads < _LOCAL_READS:
                        self._local_reads += 1
                        read_on = True
            if not tiles and not read_on:
                # the route's own message (toasts stay off while rows load)
                notice = _notes_last()
                if notice:
                    reason = screen_reason(self.tr(notice))
                elif not reason:
                    reason = self._choice_reason(read)
            with self._lock:
                if gen != self._gen:
                    return
                self._loading = False
                self.more = more
                if not more:
                    self._exhausted = True
                if first_shown and tiles:
                    if self._episode_names and self.shape == 'poster' and sum(
                            1 for t in tiles if t.get('media_type') == 'episode') * 2 > len(tiles):
                        # a show that lists its episodes without seasons
                        # (Plex can hide a single season): wide cards
                        for tile in tiles:
                            tile['shape'] = 'landscape'
                    shape = R.dominant_shape(tiles, self.shape)
                    if any(t.get('kind') == 'folder' for t in tiles) or shape != 'poster':
                        self.shape = shape
                    self._grid_id = _shape_id(GRID_BASE, self.shape)
                self.tiles.extend(tiles)
                self._show(reset=bool(first_shown and tiles) or first, added=tiles)
                if not read_on:
                    self.prop('dh.loading', '')
                if first_shown and not read_on:
                    self.prop('dh.empty', '' if self.tiles else '1')
                    self.prop('dh.empty.reason', '' if self.tiles else reason)
            if first_shown and tiles and self._pending_local is not None:
                self._activate_local(tiles)
            self._after_first(first_shown, tiles, read_on)
            self.prefetch_logos(tiles)
            self._prefetch_text(tiles)
            if read_on and not self._closing:
                self._load_page(more, first=first_shown and not self.tiles, gen=gen)
        if not self.app.workers.submit(job, priority=0,
                                       key=('grid', self.key, gen, json.dumps(params, sort_keys=True))):
            with self._lock:
                if gen == self._gen:
                    self._loading = False

    def _load_ordered(self, params, gen):
        """An order made here: the first pages (up to SORT_CAP titles), read
        together and shown in that order; the grid does not page on."""
        from . import grid_filters as GF
        order = self._order

        def job():
            _notes_begin()
            collected, seen, nxt, pages, reason = [], set(), params, 0, ''
            while nxt and pages < GF.SORT_PAGES and len(collected) < GF.SORT_CAP:
                if gen == getattr(self, '_progress_fresh_gen', -1):
                    from ..dexhub import client as _client
                    with _client.fresh_reads():
                        tiles, more, failed = self._fetch(nxt)
                else:
                    tiles, more, failed = self._fetch(nxt)
                if gen != self._gen or self._closing:
                    return
                pages += 1
                reason = reason or failed
                fresh = 0
                for tile in tiles:
                    mark = tile.get('path') or id(tile)
                    if mark in seen:
                        continue
                    seen.add(mark)
                    collected.append(tile)
                    fresh += 1
                nxt = more if fresh else None
            if len(collected) > GF.SORT_CAP:
                collected, nxt = collected[:GF.SORT_CAP], nxt or params
            with self._lock:
                if gen != self._gen:
                    return
                self._note_genres(collected)
                kept = [t for t in collected if GF.passes(t, self._rules)] if self._rules else collected
                kept = GF.sort_tiles(kept, order)
            if not kept:
                notice = _notes_last()
                if notice:
                    reason = screen_reason(self.tr(notice))
                elif not reason:
                    reason = self._choice_reason(len(collected))
            with self._lock:
                if gen != self._gen:
                    return
                self._loading = False
                self.more = None
                self._exhausted = True
                if kept:
                    shape = R.dominant_shape(kept, self.shape)
                    if any(t.get('kind') == 'folder' for t in kept) or shape != 'poster':
                        self.shape = shape
                    self._grid_id = _shape_id(GRID_BASE, self.shape)
                self.tiles = list(kept)
                self._show(reset=True)
                self.prop('dh.loading', '')
                self.prop('dh.empty', '' if kept else '1')
                self.prop('dh.empty.reason', '' if kept else reason)
                # the order covers the first titles only when the catalog goes on
                self._capped = bool(nxt)
            self._filter_props()
            self._after_first(True, kept, False)
            self.prefetch_logos(kept[:40])
            self._prefetch_text(kept)
        if not self.app.workers.submit(job, priority=0,
                                       key=('grid-order', self.key, gen, json.dumps(params, sort_keys=True))):
            with self._lock:
                if gen == self._gen:
                    self._loading = False

    def _choice_reason(self, read):
        """Why a page with a choice shows nothing (empty without a choice)."""
        from . import grid_filters as GF
        if self._facets is None or GF.is_empty(self._fstate):
            return ''
        if self._rules and read:
            return self.tr('لا توجد عناوين بهذا الاختيار في ما قُرئ من الصفحة. غيّر الفلتر أو النوع.')
        return self.tr('لا توجد عناوين بهذا الاختيار. غيّر الفلتر أو النوع.')

    def _after_first(self, first_shown, tiles, read_on):
        if first_shown and tiles:
            if self.focus_id() not in (BAR, SHEET):
                # (on the buttons the cursor stays: a choice made there may
                # be followed by another)
                self.focus(self._grid_id)
            self._focus_changed()
        elif first_shown and not self.tiles and not read_on and self._facets is not None:
            # nothing to show: the buttons, so the choice can change (the
            # panel from the left keeps the cursor: it has Clear choices)
            if self.focus_id() != SHEET:
                self._focus_bar()

    def _edge(self):
        """The edge a list's onleft/onright marked (read once)."""
        try:
            value = self.getProperty('dh.edge') or ''
            if value:
                self.clearProperty('dh.edge')
            return value
        except Exception:
            return ''

    def _focus_bar(self, key=None):
        """The Sort, Genre and Filter row, on the button used last (or ``key``)."""
        key = key or self._bar_key
        index = BAR_KEYS.index(key) if key in BAR_KEYS else 0
        try:
            self._control(BAR).selectItem(index)
        except Exception:
            pass
        self.focus(BAR)

    def _activate_local(self, tiles):
        """A page of any other source gets its buttons once its first page
        shows movies and series (v5.10.107); folders, channels and menus do not."""
        from . import grid_filters as GF
        pending, self._pending_local = self._pending_local, None
        if pending is None or self._facets is not None or not GF.titles_page(tiles):
            return
        with self._lock:
            self._facets = pending
            self._fstate = GF.empty_state()
            self._rules, self._order = {}, ''
            self._note_genres(self.tiles)
        self._filter_props()

    # ------------------------------------- Sort, Filter and Genre (v5.10.106)
    def _setup_filters(self):
        from . import grid_filters as GF
        if not self.tiles:
            self.prop('dh.loading', '1')
        facets = GF.describe(self.params, self.app)
        if facets is None:
            # v5.10.107: any other page of titles sorts and filters here; it
            # gets the buttons at once when a choice was saved for it or the
            # row's own titles show it lists movies and series, else once its
            # first page does
            local = GF.describe_local(self.params)
            if local is not None:
                saved = GF.load_state(self.app.profile, local['key'])
                if not GF.is_empty(saved) or GF.titles_page(self.tiles):
                    facets = local
                else:
                    self._pending_local = local
        self._facets = facets
        self._capped = False
        self.app.log('grid page %s: %s' % (
            self.params.get('action') or '?',
            facets['kind'] if facets is not None else
            ('buttons once titles show' if self._pending_local is not None else 'no sort or filter')))
        if facets is None:
            self.prop('dh.grid.facets', '')
            return
        with self._lock:
            # the row's own titles give the first genres to pick from
            self._note_genres(self.tiles)
        saved = GF.native_state(facets, GF.load_state(self.app.profile, facets['key']))
        if not GF.is_empty(saved):
            # the saved choice: the page opens with it, so the row's own
            # (unfiltered) titles are not reused
            state = saved
            self.params = GF.route_params(facets, state)
            self.tiles = []
            self.more = None
        else:
            # a page opened from a genre folder starts with that genre
            state = facets.get('preset') or GF.empty_state()
        self._fstate = state
        self._rules = GF.local_rules(facets, state)
        self._order = GF.local_sort(state)
        if self._rules and self.tiles:
            self.tiles = [t for t in self.tiles if GF.passes(t, self._rules)]
        if self._order and self.tiles:
            # an order made here reads the first pages itself
            self.tiles = []
            self.more = None
        self._filter_props()
        genre = facets['genre']
        if genre['mode'] == 'server' and genre['options'] is None:
            self.app.bg.submit(self._server_genres, priority=6, key=('grid-genres', self.key))

    def _filter_props(self):
        from . import grid_filters as GF
        facets = self._facets
        if facets is None:
            self.prop('dh.grid.facets', '')
            return
        state = self._fstate or GF.empty_state()
        genre = state.get('genre') or {}
        chosen_genre = str(genre.get('label') or genre.get('value') or '')
        sort = state.get('sort') or {}
        chosen_sort = self.tr(str(sort.get('label') or sort.get('value') or ''))
        active = [f for f in facets['filters'] if (state.get('filters') or {}).get(f['name'])]
        if active:
            first = state['filters'][active[0]['name']]
            text = self.tr(str(first.get('label') or first.get('value') or ''))
            if len(active) > 1:
                text = '%s  +%d' % (text, len(active) - 1)
        else:
            text = self.tr('الكل')
        values = {
            'sort': (self.tr('ترتيب'), chosen_sort or self.tr('ترتيب المصدر'), bool(chosen_sort), 'grid_sort.png'),
            'genre': (self.tr('النوع'), chosen_genre or self.tr('الكل'), bool(chosen_genre), 'grid_genre.png'),
            'filter': (self.tr('فلتر'), text, bool(active), 'grid_filter.png'),
        }
        self._bar_update(values)
        self._sheet_update(values, not GF.same_state(state, facets.get('preset') or GF.empty_state()))
        self.prop('dh.grid.facets', '1')
        line = GF.summary(facets, state, self.tr)
        if line and self._order and self._capped:
            line = '%s  ·  %s' % (line, self.tr('أول %d عنوان') % GF.SORT_CAP)
        self.prop('dh.page.filters', line)

    def _bar_update(self, values):
        """The three buttons: name, current choice, icon and a mark when the
        choice is not the source's own. Made once, then changed in place."""
        items = self._bar_items
        if items is None:
            items = []
            for key in BAR_KEYS:
                label, value, on, icon = values[key]
                li = xbmcgui.ListItem(label=label, label2=value)
                li.setProperty('key', key)
                li.setProperty('icon', self.app.media_path(icon))
                li.setProperty('on', '1' if on else '')
                items.append(li)
            try:
                self._set_items(BAR, items, list(BAR_KEYS))
            except Exception:
                return
            self._bar_items = items
            return
        for li, key in zip(items, BAR_KEYS):
            label, value, on, _icon = values[key]
            li.setLabel(label)
            li.setLabel2(value)
            li.setProperty('on', '1' if on else '')

    def _sheet_update(self, values, can_reset):
        """The panel from the left: the same three, and Reset when the choice
        differs from the source's own (v5.10.108)."""
        with self._lock:
            self._sheet_update_locked(values, can_reset)

    def _sheet_update_locked(self, values, can_reset):
        keys = list(BAR_KEYS) + (['reset'] if can_reset else [])
        items = self._sheet_items
        if items is None or self._sheet_keys != keys:
            focused = self.focus_id() == SHEET and self._on_gui_thread()
            items = []
            for key in keys:
                if key == 'reset':
                    li = xbmcgui.ListItem(label='', label2=self.tr('مسح الاختيارات'))
                    li.setProperty('icon', self.app.media_path('lay_reset.png'))
                else:
                    label, value, on, icon = values[key]
                    li = xbmcgui.ListItem(label=label, label2=value)
                    li.setProperty('icon', self.app.media_path(icon))
                    li.setProperty('on', '1' if on else '')
                li.setProperty('key', key)
                items.append(li)
            try:
                self._set_items(SHEET, items, keys)
            except Exception:
                return
            self._sheet_items, self._sheet_keys = items, keys
            index = keys.index(self._bar_key) if self._bar_key in keys else 0
            try:
                self._control(SHEET).selectItem(index)
            except Exception:
                pass
            if focused:
                self.focus(SHEET)
            return
        for li, key in zip(items, keys):
            if key == 'reset':
                continue
            label, value, on, _icon = values[key]
            li.setLabel(label)
            li.setLabel2(value)
            li.setProperty('on', '1' if on else '')

    def _on_gui_thread(self):
        return threading.current_thread() is getattr(self, '_gui_thread', None)

    def _open_sheet(self):
        """Left at the start of a row: the panel from the left. A page without
        buttons yet gets them here when it shows titles (v5.10.108)."""
        from . import grid_filters as GF
        if self._facets is None:
            local = self._pending_local or GF.describe_local(self.params)
            if local is None or not any((t or {}).get('kind') == 'work' for t in self.tiles):
                return False
            self._pending_local = None
            with self._lock:
                # (a saved choice would have given the page its buttons when
                # it opened, so this one starts from the source's own)
                self._facets = local
                self._fstate = GF.empty_state()
                self._rules, self._order = {}, ''
            with self._lock:
                self._note_genres(self.tiles)
            self.app.log('grid page %s: local sort and filter on demand' % (self.params.get('action') or '?'))
            self._filter_props()
        keys = self._sheet_keys or list(BAR_KEYS)
        index = keys.index(self._bar_key) if self._bar_key in keys else 0
        try:
            self._control(SHEET).selectItem(index)
        except Exception:
            pass
        self.focus(SHEET)
        return True

    def _reset_choice(self):
        """Back to the source's own order, genre and filters."""
        from . import grid_filters as GF
        facets = self._facets
        if facets is None:
            return
        base = facets.get('preset') or GF.empty_state()
        if GF.same_state(self._fstate or GF.empty_state(), base):
            return
        self._bar_key = 'sort'
        state = {'sort': base.get('sort'), 'genre': base.get('genre'),
                 'filters': dict(base.get('filters') or {})}
        if facets.get('preset'):
            # a page opened from a genre folder: its own route again, and no
            # saved choice (saving the preset would read the page differently
            # every time it opens)
            GF.save_state(self.app.profile, facets['key'], GF.empty_state())
            self._set_state(state, save=False, params=dict(self._orig_params))
        else:
            self._set_state(state)

    def _grid_focus(self):
        """Back to the titles (a stale edge mark must not open the panel)."""
        try:
            self.clearProperty('dh.edge')
        except Exception:
            pass
        self.focus(self._grid_id)

    def _note_genres(self, tiles):
        for tile in tiles or []:
            if tile.get('kind') != 'work':
                continue
            for name in tile.get('genres') or []:
                name = str(name or '').strip()
                if not name:
                    continue
                entry = self._seen_genres.setdefault(name.lower(), [name, 0])
                entry[1] += 1

    def _server_genres(self):
        """A server library's genres, asked for in the background when the page opens."""
        from . import grid_filters as GF
        facets = self._facets
        if facets is None or facets['genre']['options'] is not None:
            return facets['genre']['options'] if facets else []
        try:
            found = GF.server_genres(facets, self.app, language=self._tmdb_language()
                                     or ('ar' if self.s.arabic else 'en')) or []
        except Exception as exc:
            self.app.log('library genres not read: %s' % screen_reason(exc), xbmc.LOGWARNING)
            return []
        if found:
            facets['genre']['options'] = list(found)
        return list(found)

    def _genre_entries(self):
        """[(value, label, hint)] of the Genre button."""
        genre = self._facets['genre']
        if genre['mode'] == 'server':
            options = genre['options']
            if options is None:
                self.prop('dh.loading', '1')
                try:
                    options = self._server_genres()
                finally:
                    self.prop('dh.loading', '1' if self._loading else '')
            return [(value, label, '') for value, label in options or []]
        with self._lock:
            seen = sorted(self._seen_genres.values(), key=lambda e: (-e[1], e[0].lower()))
        chosen = ((self._fstate or {}).get('genre') or {}).get('value') or ''
        out = [(name, name, str(count)) for name, count in seen[:60]]
        if chosen and chosen.lower() not in [name.lower() for name, _l, _h in out]:
            out.insert(0, (chosen, chosen, ''))
        return out

    def _choose_sort(self):
        from . import options
        from . import grid_filters as GF
        facets = self._facets
        if facets is None:
            return
        self._bar_key = 'sort'
        sorts = list(facets.get('sorts') or [])
        if not sorts:
            return
        state = self._fstate or GF.empty_state()
        current = state.get('sort') or {}
        rows = [(self.tr('ترتيب المصدر'), '')]
        values = [None]
        for item in sorts:
            # an order made here covers the catalog's first titles only
            hint = getattr(self, '_local_hint', self.tr('أول %d عنوان') % GF.SORT_CAP) if item.get('local') else ''
            rows.append((self.tr(item['label']), hint))
            values.append(item)
        pre = 0
        for index, item in enumerate(values):
            if item and GF.same_sort(item, current):
                pre = index
        choice = options.choose(self.tr('ترتيب'), rows, preselect=pre, subtitle=self.title)
        if choice < 0 or choice >= len(values):
            return
        picked = values[choice]
        new_sort = None
        if picked is not None:
            new_sort = {'value': picked['value'], 'label': picked['label'],
                        'local': bool(picked.get('local'))}
            if picked.get('extra'):
                new_sort['extra'] = picked['extra']
        if GF.same_sort(new_sort, current) and bool((new_sort or {}).get('local')) == bool(current.get('local')):
            return
        self._set_state({'sort': new_sort, 'genre': state.get('genre'),
                         'filters': dict(state.get('filters') or {})})

    def _choose_genre(self):
        from . import options
        from . import grid_filters as GF
        facets = self._facets
        if facets is None:
            return
        self._bar_key = 'genre'
        entries = self._genre_entries()
        required = facets['genre'].get('required')
        if not entries:
            self.notify(self.tr('لا توجد أنواع لهذا المصدر بعد'))
            return
        state = self._fstate or GF.empty_state()
        current = str((state.get('genre') or {}).get('value') or '')
        rows, values = [], []
        if not required:
            rows.append((self.tr('الكل'), ''))
            values.append(None)
        for value, label, hint in entries:
            rows.append((label, hint))
            values.append((value, label))
        pre = 0
        for index, value in enumerate(values):
            if value and current and str(value[0]).lower() == current.lower():
                pre = index
        choice = options.choose(self.tr('النوع'), rows, preselect=pre, subtitle=self.title)
        if choice < 0 or choice >= len(values):
            return
        picked = values[choice]
        new = {'sort': state.get('sort'),
               'genre': {'value': picked[0], 'label': picked[1]} if picked else None,
               'filters': dict(state.get('filters') or {})}
        if (new['genre'] or {}).get('value') == (state.get('genre') or {}).get('value'):
            return
        self._set_state(new)

    def _filter_hint(self, item):
        chosen = ((self._fstate or {}).get('filters') or {}).get(item['name'])
        if chosen and str(chosen.get('value') or ''):
            return self.tr(str(chosen.get('label') or chosen['value']))
        return self.tr(item.get('default') or 'الكل')

    def _choose_filter(self):
        from . import options
        from . import grid_filters as GF
        facets = self._facets
        if facets is None:
            return
        self._bar_key = 'filter'
        items = list(facets['filters'])
        if not items:
            return
        state = self._fstate or GF.empty_state()
        while True:
            if len(items) == 1:
                item = items[0]
            else:
                rows = [(self.tr(f['label']), self._filter_hint(f)) for f in items]
                clear = -1
                if state.get('filters'):
                    rows.append((self.tr('مسح الفلاتر'), ''))
                    clear = len(rows) - 1
                pick = options.choose(self.tr('فلتر'), rows, subtitle=self.title)
                if pick < 0:
                    return
                if pick == clear:
                    self._set_state({'sort': state.get('sort'), 'genre': state.get('genre'), 'filters': {}})
                    return
                item = items[pick]
            if item.get('input'):
                previous = ((state.get('filters') or {}).get(item['name']) or {}).get('value') or ''
                value = xbmcgui.Dialog().input(self.tr(item['label']), defaultt=str(previous)).strip()
                if not value:
                    return
                filters = dict(state.get('filters') or {})
                filters[item['name']] = {'value': value, 'label': value}
                self._set_state({'sort': state.get('sort'), 'genre': state.get('genre'), 'filters': filters})
                return
            values = [] if item.get('required') else [None]
            rows = [] if item.get('required') else [(self.tr(item.get('default') or 'الكل'), '')]
            for value, label in item['options']:
                values.append((value, label))
                rows.append((self.tr(label), ''))
            current = str((((state.get('filters') or {}).get(item['name'])) or {}).get('value') or '')
            pre = 0
            for index, value in enumerate(values):
                if value and current and str(value[0]) == current:
                    pre = index
            choice = options.choose(self.tr(item['label']), rows, preselect=pre, subtitle=self.title)
            if choice < 0 or choice >= len(values):
                if len(items) == 1:
                    return
                continue
            filters = dict(state.get('filters') or {})
            picked = values[choice]
            if picked is None:
                filters.pop(item['name'], None)
            else:
                filters[item['name']] = {'value': picked[0], 'label': picked[1]}
            if filters == (state.get('filters') or {}):
                return
            self._set_state({'sort': state.get('sort'), 'genre': state.get('genre'), 'filters': filters})
            return

    def _set_state(self, state, save=True, params=None):
        """Save a new choice and read the page again with it."""
        from . import grid_filters as GF
        facets = self._facets
        if save:
            GF.save_state(self.app.profile, facets['key'], state)
        if params is None:
            params = GF.route_params(facets, state)
        rules, order = GF.local_rules(facets, state), GF.local_sort(state)
        with self._lock:
            # a load of the earlier choice still running finds a newer
            # generation and drops what it read
            self._gen += 1
            self._fstate = state
            self._rules = rules
            self._order = order
            self._capped = False
            self._loading = False
            self._exhausted = False
            self._local_reads = 0
            self.tiles = []
            self.more = None
            self.params = params
        self._filter_props()
        self.stop_trailer(wait=False, park=True)
        # the old selection leaves the hero; the first new title takes it
        self.show_hero(self.page_hero or {'kind': 'nav', 'title': self.title, 'label': self.title})
        for control_id in GRID_IDS:
            self.clear_list(control_id)
        self.prop('dh.grid.count', '0')
        self.prop('dh.empty', '')
        self.prop('dh.empty.reason', '')
        self.prop('dh.loading', '1')
        self._load_page(params, first=True)

    def _show(self, reset=False, added=None):
        if reset:
            self.fill_list(self._grid_id, self.tiles)
            for control_id in GRID_IDS:
                if control_id != self._grid_id:
                    self.clear_list(control_id)
        elif added:
            try:
                self.append_list(self._grid_id, list(added))
            except Exception:
                pass
        self.prop('dh.grid.count', str(len(self.tiles)))

    def _selected(self):
        pos = self.selected_pos(self._grid_id, -1)
        shown = self._displayed.get(self._grid_id) or []
        return shown[pos] if 0 <= pos < len(shown) else None

    def _prefetch_text(self, tiles):
        """The first titles get their plot and logo in the user's language early."""
        if self._real_playing or not self._tmdb_language():
            return
        for tile in list(tiles or [])[:10]:
            self.enrich(tile, priority=7, background=True)

    def _focus_changed(self):
        if self._covered or self._closing:
            return
        tile = self._selected()
        if tile is None:
            return
        if not same_work(tile, self._trailer_tile):
            self.stop_trailer(wait=False, park=True)
        self.app.scheduler.call_later(self.key + ':hero', 0.12, self._hero_due)
        self.ensure_focus_art(tile)
        pos = self.selected_pos(self._grid_id, 0)
        if self.more and not self._exhausted and pos >= len(self.tiles) - 16:
            if not self._loading:
                self._local_reads = 0
            self._load_page(self.more)

    def _hero_due(self):
        if self._covered or self._closing:
            return
        tile = self._selected()
        if tile is None:
            return
        self.show_hero(tile)
        self.ensure_focus_art(tile)
        self.enrich(tile, priority=2, focus=True)
        self.channel_epg_soon(tile)
        self.schedule_trailer(tile)
        if self.server_folder_params(tile):
            self.app.scheduler.call_later(self.key + ':folder-pre', 0.6,
                                          lambda: self._prefetch_server_if_still(tile))

    def _current_tile(self):
        return self._selected()

    def onFocus(self, control_id):
        if control_id in GRID_IDS:
            self._focus_changed()

    def on_real_playback(self, playing):
        if not playing and self.focus_id() == NP:
            self.focus(self.default_focus())

    def _discard_progress_tile(self, tile):
        path = (tile or {}).get('path') or ''
        pos = self.selected_pos(self._grid_id, 0)
        with self._lock:
            self._gen += 1  # An older in-flight page cannot put the card back.
            self._loading = False
            self.tiles = [t for t in self.tiles if not
                          (t is tile or (path and t.get('path') == path))]
            self._show(reset=True)
            self.prop('dh.empty', '' if self.tiles else '1')
        try:
            self._control(self._grid_id).selectItem(min(pos, max(0, len(self.tiles) - 1)))
        except Exception:
            pass
        if not self.tiles:
            self.show_hero(self.page_hero or {'kind': 'nav', 'title': self.title, 'label': self.title})
        self._focus_changed()

    def on_progress_changed(self):
        """Manual edits reload this catalog, including an empty Continue page."""
        if self._closing or self.params.get('live_src') or self.params.get('live_group') is not None:
            return
        with self._lock:
            self._gen += 1
            self._loading = False
            self._exhausted = False
            self._local_reads = 0
            self._capped = False
            self.tiles = []
            self.more = None
            self._show(reset=True)
        self.prop('dh.empty', '')
        self.prop('dh.loading', '1')
        # Fresh client reads bypass remote/plugin replay caches for this edit.
        self._progress_fresh_gen = self._gen
        self._load_page(dict(self.params), first=True)

    def on_playback_returned(self):
        """A show's or season's own page shows the progress of what was
        just watched (v5.10.108; Kodi's Videos window read it again too)."""
        if not self._episode_names or self._closing or not self.tiles:
            return
        params = dict(self.params)

        def job():
            try:
                fresh, _more = R.capture_tiles(params, self.app.api(), self.shape,
                                               works_only=False, limit=200)
            except Exception:
                return
            progress = dict((t.get('path'), int(t.get('progress') or 0)) for t in fresh if t.get('path'))
            with self._lock:
                shown = list(self.tiles)
            for tile in shown:
                value = progress.get(tile.get('path'))
                if value is None or value == int(tile.get('progress') or 0):
                    continue
                tile['progress'] = value
                self.update_tile(tile, progress=value)
        self.app.bg.submit(job, priority=3, key=('grid-progress', self.key))

    def onClick(self, control_id):
        if control_id == NP:
            key = self._selected_key(NP)
            if key:
                self.player_action(key)
            return
        if control_id in (BAR, SHEET):
            key = self._selected_key(control_id)
            if key == 'sort':
                self._choose_sort()
            elif key == 'genre':
                self._choose_genre()
            elif key == 'filter':
                self._choose_filter()
            elif key == 'reset':
                self._reset_choice()
            return
        if control_id not in GRID_IDS:
            return
        tile = self._selected()
        if tile is None:
            return
        if tile.get('kind') == 'folder' and tile.get('catalog_node'):
            self.open_path(tile)
            return
        if tile.get('kind') == 'folder':
            ref = tile.get('folder_ref') or {}
            self.app.open_folder(ref.get('set_id') or '', ref.get('group_id') or '',
                                 ref.get('folder_id') or '', title=tile.get('title') or '',
                                 media_filter=ref.get('media_filter') or '')
            return
        self.open_path(tile)

    def onAction(self, action):
        aid = action.getId()
        if aid in A_IGNORE:
            return
        if self._back_bounce(aid):
            self.app.log('Back right after the page showed: taken as the same press')
            return
        # the edge a list marked with this key (read once: an older mark from
        # another list must not open the panel on a later Left)
        edge = self._edge()
        was_immersive = self._immersive
        self.input_seen()
        if was_immersive:
            self.exit_immersive()
            return
        focus = self.focus_id()
        if focus == BAR:
            # Sort, Genre and Filter: Down or Back return to the titles (Left
            # and Right move along the row, Up goes to the playing video)
            key = self._selected_key(BAR)
            if key:
                self._bar_key = key
            if aid in (A_DOWN, A_PGDN) or aid in A_BACK:
                if self.tiles:
                    self._grid_focus()
                elif aid in A_BACK and not self._loading:
                    # (while a choice reloads the page Back waits for it)
                    self.close_window()
            return
        if focus == SHEET:
            # the panel from the left: Right or Back return to the titles
            key = self._selected_key(SHEET)
            if key in BAR_KEYS:
                self._bar_key = key
            if aid == A_RIGHT or aid in A_BACK:
                if self.tiles:
                    self._grid_focus()
                elif self._loading:
                    pass        # a choice is reloading the page: stay here
                elif aid in A_BACK:
                    self.close_window()
                else:
                    self._focus_bar()
            return
        if aid in A_BACK:
            self.close_window()
            return
        if aid in A_PLAY and self._real_playing and focus != NP:
            return      # Kodi's own play/pause handles the running video
        if focus == NP:
            if aid in (A_DOWN, A_PGDN):
                if self._facets is not None:
                    self._focus_bar()
                elif self.tiles:
                    self.focus(self._grid_id)
            return
        if focus in (SINK, 0):
            if self.tiles:
                self.focus(self._grid_id)
            elif self._real_playing and aid in (A_UP, A_PGUP):
                self.focus(NP)
            elif self._facets is not None and not self._loading:
                self._focus_bar()
            return
        tile = self._selected()
        if aid in (A_CONTEXT, A_MENU):
            self.context_menu(tile)
        elif aid == A_INFO:
            self.show_info(tile)
        elif aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN, A_PGUP, A_PGDN):
            if aid == A_LEFT and focus in GRID_IDS and self._facets is None and edge == 'left':
                # the start of a row on a page without the buttons yet
                if self._open_sheet():
                    return
            self._expect.pop(self._grid_id, None)
            self._focus_changed()
        elif aid in A_PLAY and tile is not None:
            self.open_path(tile)
