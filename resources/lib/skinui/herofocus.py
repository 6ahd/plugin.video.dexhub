# -*- coding: utf-8 -*-
"""Ratings with their logos for the title skin.dexhub shows large (v5.10.115).

The add-on's Home and title page show each rating of the title with its
source's logo (homeui/ratings.py): IMDb, TMDb's ring, Rotten Tomatoes'
tomato and popcorn, Metacritic's box, Trakt, Letterboxd, MDBList and MAL.
skin.dexhub showed one star. The service follows the title the skin shows
large (the Home's spotlight or focused row, a collection folder's page, a
grid, the title page) and publishes its ratings as dhs.hr.* Home
properties; the skin draws them while dhs.hr.id names that title.

As the add-on does: TMDb Helper's cache and Dex Hub's MDbList memory first
(no request), then one MDbList request for a title the cursor rests on
(only with an MDbList key, and when the cache had fewer than three).
"""
import threading
import time
from collections import OrderedDict

import xbmc

from .. import guistate
from . import common as C

PREFIX = 'dhs.hr.'
POLL = 0.2
SETTLE = 0.25           # the cursor rests this long before a lookup
NETWORK_AFTER = 1.2     # ... and this long before an MDbList request
KEEP = 400
# v5.10.334: the Home's full-screen backdrop changes once the cursor rests
# this long (each key press loaded and faded a new 1080p picture while the
# remote moved; skin.dexhub shows dhs.bg while dhs.bg.on is set)
BG_SETTLE = 0.3
_FOLD_WINDOW = 1190
_FOLD_BASE = 7000


def _count(text):
    try:
        return int(text or 0)
    except ValueError:
        return 0


class HeroFocus(threading.Thread):
    """Service thread: skinui/worker.py for skin.dexhub; af3.Service for
    Arctic Fuse 3 with Dex Hub's pages in (v5.10.119: its title page and
    folder page only, ``home`` False, the folder page in ``fold_window``)."""

    def __init__(self, monitor, busy, fold_window=_FOLD_WINDOW, home=True):
        super(HeroFocus, self).__init__(name='dexhub-skin-ratings')
        self.daemon = True
        self.monitor = monitor
        self.busy = busy                # the user's video plays: no requests
        self.fold_window = fold_window
        self.home = home
        self._halt = threading.Event()
        self.shown = ''                 # the title whose ratings are published
        self.netted = set()             # titles asked of MDbList already
        self.cache = OrderedDict()      # title id -> ratings
        self.studios = OrderedDict()    # focused title only, cache hits included
        self._studio_seeds = {}
        self._shown_studio_seed = None

    def stop(self):
        self._halt.set()

    def done(self):
        return self._halt.is_set() or self.monitor.abortRequested()

    # --------------------------------------------------------- the title
    def focused(self):
        """(title id, imdb, tmdb, media, title page) of the title shown large, or None."""
        # v5.10.140: window and dialog ids instead of getCondVisibility (five
        # calls every 0.2 s here, each one a pause in Kodi's frame; guistate.py)
        info = xbmc.getInfoLabel
        active = guistate.active()
        if guistate.video_info_up():
            prefix, page = 'ListItem.', True
        elif active == guistate.window_id(self.fold_window):
            if C.prop('dhs.fold.view') == 'catalogs':
                # v5.10.448: the folder view's titles (7110 posters, 7111 ranked);
                # its catalog tabs have no ratings
                focus = info('System.CurrentControlId')
                if focus not in ('7110', '7111'):
                    return None
                prefix, page = 'Container(%s).ListItem.' % focus, False
            else:
                try:
                    row = int(C.prop('dhs.ffr') or 1)
                except ValueError:
                    row = 1
                prefix, page = 'Container(%d).ListItem.' % (_FOLD_BASE + row), False
        elif not self.home:
            return None
        elif active == guistate.HOME:
            layout = C.layout()
            if layout.hubs or (C.prop('dhs.tab') or 'all') not in C.TABS:
                return None
            fr = C.prop('dhs.fr') or '0'
            if fr == '0' and _count(info('Container(%d).NumItems' % layout.spotlight)) > 0:
                container = layout.spotlight
            else:
                try:
                    container = layout.row_list(int(fr) if fr != '0' else 1)
                except ValueError:
                    return None
            prefix, page = 'Container(%d).ListItem.' % container, False
        elif active == guistate.VIDEOS and info('Container.Property(dhs.grid)'):
            prefix, page = 'ListItem.', False
        else:
            return None
        title_id = info(prefix + 'Property(dhs.id)') or info(prefix + 'Property(dhs.gid)')
        # v5.10.334: the card of the last look is not read again (eight labels
        # five times a second, each a wait on Kodi's graphics lock)
        cached = getattr(self, '_focus_cache', None)
        if (title_id and cached is not None and cached[0] == (prefix, page, title_id)
                and time.monotonic() - cached[4] < 3.0):
            self._studio_seed, self._facts = cached[2], cached[3]
            return cached[1]
        self._focus_cache = None
        found = self._focused_read(info, prefix, page, title_id)
        if title_id:
            self._focus_cache = ((prefix, page, title_id), found,
                                 getattr(self, '_studio_seed', ('', '')), getattr(self, '_facts', ('', '')),
                                 time.monotonic())
        return found

    def _focused_read(self, info, prefix, page, title_id):
        if not title_id or info(prefix + 'Property(kind)') not in ('work', ''):
            return None
        self._studio_seed = (title_id, info(prefix + 'Property(dhs.studio_seed)'))
        # v5.10.144: a server copy's facts, for its badges
        self._facts = (title_id, info(prefix + 'Property(dhs.facts)'))
        imdb = info(prefix + 'UniqueID(imdb)')
        tmdb = info(prefix + 'UniqueID(tmdb)')
        if not (imdb.startswith('tt') or tmdb) and not self._facts[1]:
            # v5.10.161: a server copy without ids still shows its stream
            # badges (the Servers tab); only its ratings are left out
            return None
        media = info(prefix + 'Property(dhs.media)') or ''
        if not media:
            kind = (info(prefix + 'DBType') or '').lower()
            media = 'series' if kind in ('tvshow', 'season', 'episode') else 'movie'
        return title_id, imdb, tmdb, media, page

    # -------------------------------------------------------------- loop
    def run(self):
        try:
            self._run()
        except Exception:
            import traceback
            C.log('ratings stopped:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    def _backdrop(self):
        """v5.10.334: dhs.bg, the backdrop the skin shows, once the cursor rests."""
        now = time.monotonic()
        if now - getattr(self, '_bg_checked', -1e9) > 5.0:
            from ..ui_preferences import enabled
            self._bg_checked = now
            self._bg_wanted = self.home and enabled('homeui_bg_settle')
        if not self._bg_wanted or guistate.active() != guistate.HOME or self.busy():
            if getattr(self, '_bg_on', False) and not self._bg_wanted:
                C.set_prop('dhs.bg.on', '')
                self._bg_on = False
            return
        # Never resolve $VAR from a Python worker: Kodi can unload its
        # variable table during a skin switch (native GetValue crash).
        if xbmc.getSkinDir() not in ('skin.dexhub', 'skin.dexhub.tvos'):
            return
        layout = C.layout()
        if C.prop('dhs.tab') == 'addons':
            container = 5102 if C.prop('dhs.afr') == '2' else 5101
        else:
            row = _count(C.prop('dhs.fr'))
            if row == 0 and _count(xbmc.getInfoLabel('Container(%d).NumItems' % layout.spotlight)) > 0:
                container = layout.spotlight
            else:
                container = layout.row_list(max(1, min(row, 20)))
        value = xbmc.getInfoLabel('Container(%d).ListItem.Art(fanart)' % container)
        if value != getattr(self, '_bg_seen', None):
            self._bg_seen, self._bg_since = value, now
            return
        if now - self._bg_since >= BG_SETTLE and value != getattr(self, '_bg_shown', None):
            self._bg_shown = value
            C.set_prop('dhs.bg', value)
            if not getattr(self, '_bg_on', False):
                C.set_prop('dhs.bg.on', '1')
                self._bg_on = True

    def _page_colour(self):
        """v5.10.448: with the page colour "follows the title" the title page
        and the collection page take the colour of the title on show, once
        the cursor rests (art_colour.py works it out)."""
        from .. import art_colour
        now = time.monotonic()
        if now - getattr(self, '_tone_checked', -1e9) > 2.0:
            self._tone_checked = now
            self._tone_wanted = xbmc.getInfoLabel('Skin.String(dh.pagetone)') == 'artwork'
        if not self._tone_wanted:
            art_colour.stop_following()
            return
        info = xbmc.getInfoLabel
        if guistate.video_info_up():
            prefix, extra = 'ListItem.', ''
        elif guistate.active() == guistate.window_id(self.fold_window):
            if C.prop('dhs.fold.view') == 'catalogs':
                focus = info('System.CurrentControlId')
                prefix = 'Container(%s).ListItem.' % (focus if focus in ('7110', '7111') else '7110')
            else:
                prefix = 'Container(%d).ListItem.' % (_FOLD_BASE + max(1, _count(C.prop('dhs.ffr'))))
            extra = C.prop('dhs.fold.bg')
        else:
            return                          # other pages keep the last colour
        # its backdrop, its poster, then the collection's own picture
        value = (info(prefix + 'Art(fanart)'), info(prefix + 'Art(poster)'), extra)
        if value != getattr(self, '_tone_seen', None):
            self._tone_seen, self._tone_since = value, now
            if art_colour.known(*value):
                art_colour.follow(*value)   # known: at once
            return
        if now - self._tone_since >= 0.06:
            art_colour.follow(*value)
            self._row_ahead(art_colour, info, prefix)

    def _row_ahead(self, art_colour, info, prefix):
        """Every title of the row on show, nearest first, worked out ahead
        so a step to any of them changes the colour at once (cached)."""
        if prefix == 'ListItem.':
            return
        container = prefix[:-len('ListItem.')]
        count = min(_count(info(container + 'NumItems')), 40)
        key = (container, count, info(container + 'ListItemAbsolute(0).Art(fanart)'))
        if not count or key == getattr(self, '_tone_row', None):
            return
        self._tone_row = key
        here = max(0, _count(info(container + 'CurrentItem')) - 1)
        order = sorted(range(count), key=lambda n: (abs(n - here), n < here))
        urls = []
        for n in order:
            for art in ('fanart', 'poster'):
                value = info('%sListItemAbsolute(%d).Art(%s)' % (container, n, art))
                if value and value not in urls:
                    urls.append(value)
        art_colour.ahead(*urls)

    def _colour_loop(self):
        """The page colour on its own quick beat: the ratings loop waits on
        lookups, and a colour change waited with it (0.5 to 0.8 s)."""
        while not self.done():
            try:
                self._page_colour()
            except Exception:
                pass
            self._halt.wait(0.05)

    def _run(self):
        from ..ui_preferences import enabled
        colour = threading.Thread(target=self._colour_loop, name='DexHubPageColour')
        colour.daemon = True
        colour.start()
        since, current = 0.0, None
        try:
            self._loop_ratings(enabled, since, current)
        finally:
            # the skin follows the focus again by itself (an update, a stop)
            if getattr(self, '_bg_on', False):
                C.set_prop('dhs.bg.on', '')

    def _loop_ratings(self, enabled, since, current):
        while not self.done():
            try:
                self._backdrop()
            except Exception:
                pass
            if not (enabled('metadata_badges') or enabled('badges_home')) or self.busy():
                if self.shown:
                    C.set_prop(PREFIX + 'id', '')
                    self.shown = ''
                self._halt.wait(C.poll_wait(POLL))
                continue
            try:
                found = self.focused()
            except Exception:
                found = None
            now = time.monotonic()
            key = found[0] if found else ''
            if key != (current[0] if current else ''):
                current, since = found, now
            elif current and key:
                rested = now - since
                if (rested >= SETTLE and (self.shown != key
                        or getattr(self, '_studio_seed', None) != self._shown_studio_seed)):
                    ratings = self.lookup(current, network=False)
                    self.lookup_studio(current)
                    if self.focused_key() == key:
                        self.publish(current, ratings)
                # the title page asks at once, a hero once the cursor rests
                wait = 0.0 if current[4] else NETWORK_AFTER
                # v5.10.297: the Home hero too (once the cursor rests), so the
                # platforms beside the star match the sources window
                extra_ratings = True
                if (extra_ratings and rested >= max(SETTLE, wait)
                        and key not in self.netted and self.wants_network(key)):
                    self.netted.add(key)
                    ratings = self.lookup(current, network=True)
                    if self.focused_key() == key:
                        self.publish(current, ratings)
            self._halt.wait(C.poll_wait(POLL))

    def focused_key(self):
        try:
            found = self.focused()
        except Exception:
            return ''
        return found[0] if found else ''

    def wants_network(self, key):
        from ..ui_preferences import enabled
        if not enabled('metadata_badges'):
            return False
        if len(self.cache.get(key) or {}) >= 3:
            return False
        try:
            if self.busy():
                return False
            # v5.10.301: TMDb, Cinemeta and TMDb Helper's keys need no key of ours
            return True
        except Exception:
            return False

    def lookup(self, found, network=False):
        key, imdb, tmdb, media, _page = found
        if not (imdb.startswith('tt') or tmdb):
            return {}
        if not network and key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        try:
            from ..homeui import ratings as RB
            ratings = RB.collect(tmdb_id=tmdb, imdb_id=imdb, media_type=media, network=network)
        except Exception as exc:
            C.log('ratings of %s: %s' % (imdb or tmdb, exc), xbmc.LOGDEBUG)
            ratings = {}
        before = self.cache.get(key) or {}
        if len(ratings) >= len(before):
            self.cache[key] = ratings
            self.cache.move_to_end(key)
            while len(self.cache) > KEEP:
                self.cache.popitem(last=False)
        return self.cache.get(key) or ratings

    def publish(self, found, ratings):
        from ..homeui import ratings as RB
        from ..ui_preferences import enabled
        key = found[0]
        home = C.home()
        # Identity is a commit marker only when changing titles. Clearing it
        # on every ratings update briefly selected the legacy tile artwork.
        if home.getProperty(PREFIX + 'id') != key:
            home.setProperty(PREFIX + 'id', '')
        if not enabled('metadata_badges'):
            ratings = {}
        # v5.10.297: every platform the sources window shows (not six)
        for name, value in RB.props(ratings, prefix=PREFIX, limit=len(RB.ORDER)).items():
            if home.getProperty(name) != value:
                home.setProperty(name, value)
        self.publish_badges(key)
        logo, chip = self.studios.get(key) or ('', False)
        try:
            from ..studio_mark import slot
            width = slot(logo, chip)
        except Exception:
            width = ''
        for name, value in (('studio_logo', logo), ('studio_chip', '1' if chip else ''), ('studio_w', width)):
            if home.getProperty(PREFIX + name) != value:
                home.setProperty(PREFIX + name, value)
        # last: the skin shows them once the id names its title
        if home.getProperty(PREFIX + 'id') != key:
            home.setProperty(PREFIX + 'id', key)
        self.shown = key
        self._shown_studio_seed = (key, self._studio_seeds.get(key, ''))

    def publish_badges(self, key):
        """The focused server copy's badges (dhs.hr.badge.*), its facts line
        drawn in the player's badge style, the size last (v5.10.144)."""
        from ..ui_preferences import enabled
        facts_key, facts = getattr(self, '_facts', ('', ''))
        badges = []
        if facts and facts_key == key and enabled('badges_home'):
            try:
                from .. import player_badges
                badges = player_badges.hero_badges(facts)
            except Exception as exc:
                C.log('hero badges: %s' % exc, xbmc.LOGDEBUG)
        # v5.10.151: the same badges as last time (mostly none: catalog
        # titles have no server facts) write nothing: _write asks Kodi for
        # every slot's properties, each a call into the GUI
        signature = repr(badges)
        if signature == getattr(self, '_badge_signature', None):
            return
        self._badge_signature = signature
        try:
            from .. import player_badges
            player_badges._write(C.home(), PREFIX + 'badge', badges)
        except Exception:
            pass

    def lookup_studio(self, found):
        key, imdb, tmdb, media, _page = found
        seed_key, seed = getattr(self, '_studio_seed', ('', ''))
        seed = seed if seed_key == key else ''
        seeds = getattr(self, '_studio_seeds', None)
        if seeds is None:
            self._studio_seeds = seeds = {}
        if key in self.studios and seeds.get(key) == seed:
            self.studios.move_to_end(key)
            return self.studios[key]
        tile = {'kind': 'work', 'imdb_id': imdb, 'tmdb_id': tmdb, 'media_type': media}
        try:
            import json
            from . import rows
            enricher = rows.HeadlessEnricher(rows.app())
            try:
                logos = json.loads(seed) if seed else []
            except (TypeError, ValueError):
                logos = []
            found = {'studio_logos': logos[:3] if isinstance(logos, list) else []}
            enricher._studio_badge(tile, media, found)
        except Exception:
            pass
        value = (tile.get('studio_logo') or '', bool(tile.get('studio_logo_chip')))
        self.studios[key] = value
        seeds[key] = seed
        while len(self.studios) > KEEP:
            evicted, _value = self.studios.popitem(last=False)
            seeds.pop(evicted, None)
        return value
