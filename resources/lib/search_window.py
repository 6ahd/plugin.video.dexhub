from resources.lib.tvos_assets import resolve_assets as _tvos_assets
# -*- coding: utf-8 -*-
"""Cinematic, local-only renderer for Dex Hub search results.

The network search is completed by plugin.py before this window opens.  This
module only paints the prepared rows; the one exception (v5.10.97) is the
title logo: a small background thread gives the result under the cursor, and
its neighbours, the title's own logo (TMDb Helper's local database first,
then TMDb) when the result came without one, and drops a logo that does not
load, so the hero never shows an add-on's logo or an empty space.
"""
import threading
import xbmc
import xbmcaddon
import xbmcgui
import time
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen

from .i18n import tr
from . import skin_theme


ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo('path')

LIST_RESULTS = 3000
BTN_ALL = 3101
BTN_MOVIES = 3102
BTN_SERIES = 3103
BTN_ANIME = 3104
BTN_PEOPLE = 3105
BTN_SEARCH = 3200

ACTION_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
ACTION_MOVE = {3, 4, 5, 6}


def _clean(value):
    return str(value or '').strip()


_TRUSTED_LOGO_HOSTS = ('image.tmdb.org', 'fanart.tv', 'artworks.thetvdb.com',
                       'm.media-amazon.com', 'images.plex.tv')
_LOGO_STATE = {}


def _logo_trusted(url):
    url = _clean(url).lower()
    if not url:
        return False
    if url.split('?', 1)[0].endswith('.svg'):
        return False        # Kodi cannot draw SVG
    if not url.startswith(('http://', 'https://')):
        return True
    return any(host in url for host in _TRUSTED_LOGO_HOSTS)


def _logo_loads(url, timeout=4.0):
    """True when the logo URL answers with an image (remembered per URL).

    Failures are forgotten after half an hour: the interpreter can live on
    across searches, and a host may just have been unreachable.
    """
    if _logo_trusted(url):
        return True
    if _clean(url).lower().split('?', 1)[0].endswith('.svg'):
        return False
    known = _LOGO_STATE.get(url)
    if known is not None and (known[0] or time.time() - known[1] < 1800):
        return known[0]
    ok = False
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0 (Kodi; Dex Hub)', 'Range': 'bytes=0-15'})
        with urlopen(req, timeout=timeout) as resp:
            head = resp.read(16)
        # PNG, JPEG, GIF or WebP; Kodi cannot draw SVG
        ok = (head.startswith(b'\x89PNG') or head[:3] == b'\xff\xd8\xff'
              or head[:4] in (b'GIF8', b'RIFF'))
    except Exception:
        ok = False
    _LOGO_STATE[url] = (ok, time.time())
    return ok


def drop_brand_logos(rows):
    """Clear a result's logo when it is an add-on's own logo, not the title's.

    A provider whose results have no title logos sometimes sends its own logo
    with every result; a real title logo belongs to one title only.
    """
    owners = {}
    for row in rows or []:
        logo = _clean(row.get('clearlogo'))
        if logo:
            owners.setdefault(logo, set()).add(_clean(row.get('path')) or _clean(row.get('title')))
    for row in rows or []:
        logo = _clean(row.get('clearlogo'))
        if logo and (len(owners.get(logo) or ()) > 1 or logo == _clean(row.get('poster'))):
            row['clearlogo'] = ''
    return rows


def _row_ids(row):
    try:
        params = dict(parse_qsl(urlsplit(_clean(row.get('path'))).query))
    except Exception:
        params = {}
    ids = {'tmdb_id': _clean(params.get('tmdb_id') or row.get('tmdb_id')),
           'imdb_id': _clean(params.get('imdb_id') or row.get('imdb_id'))}
    if not ids['imdb_id'].startswith('tt'):
        ids['imdb_id'] = ''
    canonical = _clean(params.get('canonical_id'))
    if not ids['imdb_id'] and canonical.startswith('tt'):
        ids['imdb_id'] = canonical.split(':', 1)[0]
    if not ids['tmdb_id'] and canonical.lower().startswith('tmdb:'):
        ids['tmdb_id'] = canonical.split(':', 1)[1].split(':', 1)[0]
    return ids


_WORK_TYPES = ('movie', 'series', 'anime', 'tv', 'show', 'tvshow')
# square search, category and provider glyphs and Dex Hub's own placeholder
# posters: not a title's poster
_NONPOSTER = ('cat_search.png', 'root_search.png', 'provider.png',
              'provider_aiostreams.png', 'icon.png',
              'default_movie_poster.png', 'default_series_poster.png')


def _has_poster(row):
    poster = _clean(row.get('poster')).lower()
    return bool(poster) and not any(poster.endswith(name) for name in _NONPOSTER)


def _resolve_logo(row):
    """The title's own logo for a result, '' to clear a dead one, None to keep.

    v5.10.111: a result with no poster gets TMDb's too (row['_tmdb_poster']),
    and a result an add-on gave only its own id (Arabic add-ons) is looked up
    on TMDb by its title and year."""
    current = _clean(row.get('clearlogo'))
    want_poster = not _has_poster(row)
    if current and _logo_loads(current) and not want_poster:
        return None
    media_type = _clean(row.get('media_type')).lower()
    if media_type in ('person', 'people'):
        return '' if current else None
    ids = _row_ids(row)
    title, year = _clean(row.get('title')), _clean(row.get('year'))
    if not (ids.get('tmdb_id') or ids.get('imdb_id')) and not (
            title and year and media_type in _WORK_TYPES):
        return '' if current else None
    tmdb_type = 'movie' if media_type == 'movie' else 'tv'
    logo = ''
    if ids.get('tmdb_id') or ids.get('imdb_id'):
        try:
            from .art import get_clearlogo_from_db
            logo = get_clearlogo_from_db(tmdb_id=ids.get('tmdb_id') or '', media_type=tmdb_type,
                                         imdb_id=ids.get('imdb_id') or '') or ''
        except Exception:
            logo = ''
    if not logo or want_poster:
        try:
            from .art import get_tmdb_direct_art
            bundle = get_tmdb_direct_art(tmdb_id=ids.get('tmdb_id') or '', imdb_id=ids.get('imdb_id') or '',
                                         media_type=tmdb_type, title=title, year=year) or {}
        except Exception:
            bundle = {}
        logo = logo or _clean(bundle.get('clearlogo'))
        if want_poster and _clean(bundle.get('poster')):
            row['_tmdb_poster'] = _clean(bundle.get('poster'))
    if logo and 'image.tmdb.org' in logo and logo.lower().endswith('.svg'):
        logo = logo[:-4] + '.png'
    if current and _logo_loads(current):
        logo = current
    if logo:
        return logo
    if row.get('_tmdb_poster'):
        return current
    return '' if current else None


class _LogoResolver(threading.Thread):
    """Gives the results around the cursor their title logos, nearest first."""

    def __init__(self, window):
        super(_LogoResolver, self).__init__(name='DexHub-search-logos')
        self.daemon = True
        self.window = window
        self.stop_event = threading.Event()
        self.done = set()

    def run(self):
        try:
            self._run()
        finally:
            # v5.10.165: the window and this thread pointed at each other, so
            # the closed window lived until Python's garbage collector ran;
            # Kodi 22 processes every Python dialog that exists each frame
            self.window = None

    def _run(self):
        monitor = xbmc.Monitor()
        while not self.stop_event.is_set() and not monitor.abortRequested():
            window = self.window
            if window is None:
                break
            job = window._next_logo_job(self.done)
            if job is None:
                if self.stop_event.wait(0.35):
                    break
                continue
            self.done.add(id(job))
            try:
                logo = _resolve_logo(job)
            except Exception:
                logo = None
            if logo is not None and not self.stop_event.is_set():
                window._apply_logo(job, logo)
            window = None


def _joined(value, limit=3):
    if isinstance(value, (list, tuple)):
        return ' / '.join(_clean(part) for part in value[:limit] if _clean(part))
    return _clean(value)


class SearchResultsWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(SearchResultsWindow, self).__init__(*args)
        self._query = _clean(kwargs.get('query'))
        self._all_rows = [dict(row) for row in (kwargs.get('rows') or []) if isinstance(row, dict)]
        self._visible_rows = []
        self._active_filter = 'all'
        self._pending_builtin = ''
        self._closing = False
        self._last_busy_touch = 0.0
        # Kodi's Python/C++ bridge is the expensive part of rendering a large
        # result set.  Materialize only the rows around the visible rail and
        # append small batches as the cursor approaches the end.
        # The panel shows ten posters (5x2). Keep only two screens across the
        # Kodi bridge initially, then append one screen near the end.
        self._initial_render = 20
        self._append_render = 10
        self._rendered_count = 0
        self._logos = None
        # The logo thread and the GUI callbacks both touch the result list.
        # Kodi only lends out the items of a list (getListItem), and a reset
        # frees them even while another thread is setting their art, so the
        # window keeps its own ListItems and changes the list under one lock.
        self._lock = threading.RLock()
        self._items = []
        self._results = None

    def _results_control(self):
        with self._lock:
            if self._results is None:
                self._results = self.getControl(LIST_RESULTS)
            return self._results

    def _results_pos(self, fallback=-1):
        try:
            with self._lock:
                return int(self._results_control().getSelectedPosition())
        except Exception:
            return fallback

    def _touch_interactive(self):
        now = time.monotonic()
        if (now - self._last_busy_touch) < 2.0:
            return
        self._last_busy_touch = now
        try:
            xbmcgui.Window(10000).setProperty(
                'dexhub.interactive_busy', '%.3f' % time.time())
        except Exception:
            pass

    def onInit(self):
        self._touch_interactive()
        try:
            skin_theme.publish_theme(window=self)
        except Exception:
            pass
        self.setProperty('dexhub.search.query', self._query)
        self.setProperty('dexhub.search.title', tr('البحث'))
        movie_count = sum(1 for row in self._all_rows
                          if self._matches(row, 'movies'))
        series_count = sum(1 for row in self._all_rows
                           if self._matches(row, 'series'))
        anime_count = sum(1 for row in self._all_rows
                          if self._matches(row, 'anime'))
        people_count = sum(1 for row in self._all_rows
                           if self._matches(row, 'people'))
        self.setProperty('dexhub.search.all_label', '%s  %d' % (
            tr('الكل'), len(self._all_rows)))
        self.setProperty('dexhub.search.movies_label', '%s  %d' % (
            tr('الأفلام'), movie_count))
        self.setProperty('dexhub.search.series_label', '%s  %d' % (
            tr('المسلسلات'), series_count))
        self.setProperty('dexhub.search.anime_label', '%s  %d' % (
            tr('الأنمي'), anime_count))
        self.setProperty('dexhub.search.people_label', '%s  %d' % (
            tr('الأشخاص'), people_count))
        self.setProperty('dexhub.search.people_available',
                         'true' if people_count else 'false')
        self.setProperty('dexhub.search.search_label', tr('بحث جديد'))
        self.setProperty('dexhub.search.result_label', tr('نتيجة'))
        self.setProperty('dexhub.search.hint', tr('↑ الفلاتر   •   OK فتح   •   رجوع إغلاق'))
        self._apply_filter('all')
        try:
            self.setFocusId(LIST_RESULTS)
        except Exception:
            pass
        if self._logos is None:
            self._logos = _LogoResolver(self)
            self._logos.start()

    def _next_logo_job(self, done):
        """The nearest result to the cursor still waiting for its logo check."""
        if self._closing:
            return None
        with self._lock:
            rows = list(self._visible_rows)
            limit = min(len(rows), self._rendered_count)
            pos = self._results_pos(0)
        pos = max(0, min(pos, limit - 1)) if limit else 0
        for distance in range(0, 11):
            for index in ((pos + distance, pos - distance) if distance else (pos,)):
                if 0 <= index < limit and id(rows[index]) not in done:
                    return rows[index]
        return None

    def _apply_logo(self, row, logo):
        row['clearlogo'] = logo
        poster = row.pop('_tmdb_poster', '') if not _has_poster(row) else ''
        if poster:
            row['poster'] = poster
        if self._closing:
            return
        with self._lock:
            index = next((i for i, r in enumerate(self._visible_rows) if r is row), -1)
            item = self._items[index] if 0 <= index < len(self._items) else None
        if item is None:
            return
        try:
            art = {'clearlogo': logo}
            if poster:
                art.update({'poster': poster, 'thumb': poster})
                item.setProperty('poster', poster)
            item.setArt(art)
            item.setProperty('clearlogo', logo)
        except Exception:
            pass

    def _matches(self, row, name):
        media_type = _clean(row.get('media_type')).lower()
        if name == 'movies':
            return media_type == 'movie'
        if name == 'series':
            return media_type in ('series', 'tv', 'show', 'tvshow') and not bool(row.get('is_anime'))
        if name == 'anime':
            return bool(row.get('is_anime'))
        if name == 'people':
            return media_type in ('person', 'people')
        return True

    def _apply_filter(self, name):
        if name == self._active_filter and self._rendered_count:
            return
        active = name or 'all'
        visible = [row for row in self._all_rows if self._matches(row, active)]
        # An empty optional filter should not strand the user on a blank rail.
        if not visible and active != 'all':
            active = 'all'
            visible = list(self._all_rows)
        self._active_filter = active
        self.setProperty('dexhub.search.active_filter', active)
        self.setProperty('dexhub.search.count', str(len(visible)))
        count = min(len(visible), self._initial_render)
        items = [self._make_item(row) for row in visible[:count]]
        with self._lock:
            self._visible_rows = visible
            self._rendered_count = count
            self._items = items
            try:
                control = self._results_control()
                control.reset()
                if items:
                    control.addItems(items)
                    control.selectItem(0)
            except Exception:
                pass
        # Hero fields bind directly to Container(3000).ListItem in the XML.
        # No Python callback or sleep is needed while the remote moves.

    def _make_item(self, row):
        """Create one already-prepared row without metadata/network work."""
        title = _clean(row.get('title'))
        year = _clean(row.get('year'))
        media_type = _clean(row.get('media_type')).lower()
        li = xbmcgui.ListItem(label=title, label2=year)
        poster = _clean(row.get('poster'))
        # Search/category/provider glyphs are square and look stretched inside
        # a poster grid. Missing item artwork gets a real 2:3 local placeholder
        # until TMDb's poster arrives (the logo thread, v5.10.111).
        if not _has_poster(row):
            default_name = ('default_series_poster.png'
                            if media_type in ('series', 'tv', 'show', 'tvshow', 'anime')
                            else 'default_movie_poster.png')
            poster = (_tvos_assets('special://home/addons/plugin.video.dexhub/resources/media/')
                      + default_name)
        logo = _clean(row.get('clearlogo'))
        if logo and not (_logo_trusted(logo) or (_LOGO_STATE.get(logo) or (False, 0))[0]):
            logo = ''       # the title shows until the logo has proved to load
        li.setArt({
            'poster': poster,
            'thumb': poster,
            'fanart': _clean(row.get('fanart')),
            'landscape': _clean(row.get('landscape') or row.get('fanart')),
            'clearlogo': logo,
        })
        for key in ('title', 'year', 'media_type', 'plot', 'rating', 'genre',
                    'runtime', 'poster', 'fanart', 'landscape',
                    'path', 'is_folder', 'source_label'):
            li.setProperty(key, _clean(row.get(key)))
        li.setProperty('clearlogo', logo)
        li.setProperty('genre', _joined(row.get('genre')))
        li.setProperty('kind', (
            tr('شخص') if media_type in ('person', 'people') else
            (tr('فيلم') if media_type == 'movie' else
             (tr('أنمي') if bool(row.get('is_anime')) else tr('مسلسل')))))
        return li

    def _maybe_extend_results(self):
        with self._lock:
            start, rows = self._rendered_count, self._visible_rows
        if start >= len(rows):
            return
        position = self._results_pos(-1)
        if position < 0 or position < max(0, start - 10):
            return
        end = min(len(rows), start + self._append_render)
        items = [self._make_item(row) for row in rows[start:end]]
        with self._lock:
            if rows is not self._visible_rows or self._rendered_count != start:
                return      # the filter changed meanwhile
            try:
                self._results_control().addItems(items)
            except Exception:
                return
            self._items.extend(items)
            self._rendered_count = end

    def _selected_row(self):
        pos = self._results_pos(-1)
        with self._lock:
            rows = self._visible_rows
        if 0 <= pos < len(rows):
            return rows[pos]
        return rows[0] if rows else {}

    def _open_selected(self):
        row = self._selected_row()
        path = _clean(row.get('path'))
        if not path:
            return
        is_folder = _clean(row.get('is_folder')).lower() in ('1', 'true', 'yes')
        if is_folder:
            # Replace the underlying search directory instead of pushing it
            # onto Kodi's return stack. Back from the opened work/person no
            # longer re-enters the query box.
            # ``replace`` is important: without it Kodi keeps the unified
            # search plugin route in history and may invoke that route again
            # (including its keyboard) when the opened work is left.
            self._pending_builtin = 'Container.Update(%s,replace)' % path
        else:
            self._pending_builtin = 'RunPlugin(%s)' % path
        self._closing = True
        self.close()

    def onFocus(self, control_id):
        return

    def onClick(self, control_id):
        self._touch_interactive()
        if self._closing:
            return
        if control_id == LIST_RESULTS:
            self._open_selected()
        elif control_id == BTN_ALL:
            self._apply_filter('all')
            self.setFocusId(LIST_RESULTS)
        elif control_id == BTN_MOVIES:
            self._apply_filter('movies')
            self.setFocusId(LIST_RESULTS)
        elif control_id == BTN_SERIES:
            self._apply_filter('series')
            self.setFocusId(LIST_RESULTS)
        elif control_id == BTN_ANIME:
            self._apply_filter('anime')
            self.setFocusId(LIST_RESULTS)
        elif control_id == BTN_PEOPLE:
            self._apply_filter('people')
            self.setFocusId(LIST_RESULTS)
        elif control_id == BTN_SEARCH:
            self._pending_builtin = 'RunPlugin(plugin://plugin.video.dexhub/?action=hub_search_menu)'
            self._closing = True
            self.close()

    def onAction(self, action):
        self._touch_interactive()
        if self._closing:
            return
        action_id = action.getId()
        if action_id in ACTION_BACK:
            self._closing = True
            self.close()
            return
        if action_id in ACTION_MOVE:
            self._maybe_extend_results()

    def consume_pending_builtin(self):
        value, self._pending_builtin = self._pending_builtin, ''
        return value


def open_search_results(query, rows):
    window = SearchResultsWindow(
        'search_results.xml', ADDON_PATH, 'Default', '1080i',
        query=query, rows=rows,
    )
    window.doModal()
    window._closing = True
    if window._logos is not None:
        window._logos.stop_event.set()
        window._logos = None        # v5.10.165: no cycle keeps the closed window
    builtin = window.consume_pending_builtin()
    del window
    if builtin:
        xbmc.executebuiltin(builtin)
