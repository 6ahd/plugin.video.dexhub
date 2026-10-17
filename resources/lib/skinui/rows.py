# -*- coding: utf-8 -*-
"""The skin's Home rows: which rows each tab has, reading them, publishing them.

The rows are the Dex Hub Home's own (homeui): the same Nuvio Home, the same
collections, the same catalogs and Continue Watching, in the order the user
arranged with the layout editor, read through the same routes (capture.py).
Here they become row caches (rows/<tab>_<id>.json) that skin_row hands to
Kodi in a few milliseconds, and Home window properties the skin reads:

  dhs.<tab>.<n>.path    plugin://...?action=skin_row&m=<tab>&id=<row id>&v=<version>
  dhs.<tab>.<n>.title / .sub / .shape
  dhs.ready             the rows are published

The version in the path changes only when a row's content changed, so Kodi
reads a row again exactly then.
"""
from .. import profile_scope as _scope
import hashlib
import json
import os
import re
import threading
import time
from .. import lazy_tb as traceback
from urllib.parse import urlencode

import xbmc
import xbmcvfs

from . import common as C
from .board import (load_specs, find_spec, row_path, _version, bump,  # noqa: F401
                    _publish_tab, _row_brief, is_published, published, update_spot, SPOT,
                    show_source)    # v5.10.286
from ..homeui import rows as R

_api_lock = threading.Lock()
_API = [None]
_publish_lock = threading.Lock()
_fetch_locks = {}
_fetch_locks_lock = threading.Lock()


# --------------------------------------------------------------------------
# the Home's row model without its window
# --------------------------------------------------------------------------




# v5.10.226: a show's status, its words and its chip's colour (green on,
# grey ended, red cancelled, amber in production, blue upcoming)
TV_STATUS = {'returning series': ('مستمر', '8C16A34A'), 'ended': ('انتهى', '594B5563'),
             'canceled': ('ملغي', '8CDC2626'), 'cancelled': ('ملغي', '8CDC2626'),
             'in production': ('قيد الإنتاج', '8CD97706'), 'planned': ('قادم', '8C2563EB'),
             'pilot': ('حلقة تجريبية', '8C2563EB')}


def show_chips(tr, count, status):
    """(seasons label, status label, status colour) of a show."""
    count = int(count or 0)
    seasons = (tr('موسم واحد') if count == 1 else tr('%d مواسم') % count) if count > 0 else ''
    found = TV_STATUS.get(str(status or '').strip().lower())
    return seasons, (tr(found[0]) if found else ''), (found[1] if found else '')

def api():
    """The Dex Hub router, imported once per interpreter."""
    if _API[0] is not None:
        return _API[0]
    with _api_lock:
        if _API[0] is None:
            started = time.monotonic()
            try:
                from ..i18n import reset_language_cache
                reset_language_cache()
            except Exception:
                pass
            from .. import plugin as _plugin
            try:
                _plugin._reset_invocation_state()
            except Exception:
                try:
                    from ..runtime_binding import bind
                    bind(_plugin)
                except Exception:
                    pass
            _API[0] = _plugin
            C.log('router ready in %.0f ms' % ((time.monotonic() - started) * 1000))
    return _API[0]


class HeadlessApp(object):
    """What the Home's row code needs from homeui.app.App, without its threads."""

    def __init__(self):
        from ..homeui.app import Settings
        from ..homeui.layout import Layout
        self.settings = Settings()
        addon = self.settings._addon
        self.addon_path = addon.getAddonInfo('path')
        self.profile = _scope.ScopedPath('homeui')
        try:
            os.makedirs(self.profile, exist_ok=True)
        except Exception:
            pass
        self.layout = Layout(self.profile)
        self._tmdb_settings = None

    def api(self):
        return api()

    def log(self, msg, level=None):
        C.log(msg, xbmc.LOGINFO if level is None else level)

    def tr(self, text):
        try:
            from ..i18n import tr
            return tr(text)
        except Exception:
            return text

    def media_path(self, name):
        return os.path.join(self.addon_path, 'resources', 'media', 'homeui', name)


def _window_functions(*names):
    from ..homeui import window as W
    out = {}
    for name in names:
        for cls in (W.BrowseWindow, W._BaseWindow):
            if name in cls.__dict__:
                out[name] = cls.__dict__[name]
                break
    return out


class HeadlessHome(object):
    """The Python Home's row list (homeui BrowseWindow) for one tab, without a window."""

    def __init__(self, app, media):
        self.app = app
        self.media = media
        self.s = app.settings
        self.gif_on = self.s.flag('homeui_focus_gif', True)

    def tr(self, text):
        return self.app.tr(text)


class HeadlessEnricher(object):
    """The Python Home's TMDb pass on a title (its plot and logo in the user's
    language, art a catalog lacks), without a window."""

    def __init__(self, app):
        self.app = app
        self.s = app.settings


def _bind_classes():
    if getattr(HeadlessHome, '_bound', False):
        return
    for name, fn in _window_functions('_candidate_rows', '_custom_rows', 'layout_rows',
                                      '_progress_loader', '_catalog_loader').items():
        setattr(HeadlessHome, name, fn)
    for name, fn in _window_functions('tmdb_settings', '_tmdb_language', '_enrich_now', '_tmdb_localize',
                                      '_nuvio_parity_pass',
                                      '_helper_localized', '_show_status', '_studio_badge', '_rating_badges', '_supplement_badges').items():
        setattr(HeadlessEnricher, name, fn)
    HeadlessHome._bound = True


_APP = [None]
_APP_SCOPE = [None]
_MEDIA = [None]


def media():
    """The Python Home's cache of collection GIFs (homeui/focusart): Kodi
    animates a GIF only from a local file."""
    if _MEDIA[0] is None:
        from ..homeui.media_cache import MediaCache
        _MEDIA[0] = MediaCache(os.path.join(app().profile, 'focusart'), log=lambda msg: C.log(msg, xbmc.LOGDEBUG))
    return _MEDIA[0]


def _local_gif(tile):
    gif = tile.get('gif') or ''
    if gif and not gif.startswith(('http://', 'https://')) and os.path.exists(gif):
        return gif
    url = tile.get('gif_url') or (gif if gif.startswith(('http://', 'https://')) else '')
    if not url:
        return ''
    try:
        return media().local(url)
    except Exception:
        return ''


def app():
    if _APP[0] is None or _APP_SCOPE[0] != _scope.key():
        _bind_classes()
        _APP[0] = HeadlessApp()
        _APP_SCOPE[0] = _scope.key()
        _MEDIA[0] = None
    return _APP[0]


@_scope.bound
def compute_rows(tab):
    """The rows of a tab as the user arranged them (homeui Row objects)."""
    if tab == 'library':
        # v5.10.153: the Library tab's rows come from the tracking services
        # v5.10.238: in the order the user gave them (phone setup)
        from ..routes import library
        return app().layout.apply(library.skin_rows(), 'library')
    if tab == 'servers':
        # v5.10.157: the Servers tab, built like the Library (its chips are the servers)
        from ..routes import servers_tab
        return app().layout.apply(servers_tab.skin_rows(), 'servers')
    a = app()
    home = HeadlessHome(a, tab)
    rows = a.layout.apply(home.layout_rows(tab), tab)
    return [row for row in rows if not row.meta.get('missing')]


def profile_compute(tab):
    """compute_rows under the profiler (v5.10.188): (rows, where the time
    went) for the log line of a tab that took long (once a session)."""
    import cProfile
    import pstats
    prof = cProfile.Profile()
    started = time.monotonic()
    prof.enable()
    try:
        rows = compute_rows(tab)
    finally:
        prof.disable()
    spent = (time.monotonic() - started) * 1000
    stats = pstats.Stats(prof).stats
    ours, raw = [], []
    for (path, _line, name), (_cc, _nc, tt, ct, _callers) in stats.items():
        if 'plugin.video.dexhub' in path:
            module = os.path.splitext(path.replace('\\', '/').split('/resources/lib/')[-1])[0].replace('/', '.')
            ours.append((ct, '%s.%s' % (module, name)))
        else:
            raw.append((tt, name if path == '~' else '%s.%s' % (os.path.basename(path).split('.')[0], name)))
    ours.sort(reverse=True)
    raw.sort(reverse=True)
    skip = {'skinui.rows.compute_rows', 'homeui.window.layout_rows'}
    parts = []
    for ct, name in ours:
        if name not in skip and ct >= 0.005:
            skip.add(name)              # a name once (two functions can share one)
            parts.append('%s %.0f' % (name, ct * 1000))
    text = '%.0f ms (profiled): %s; most own time: %s' % (
        spent, ', '.join(parts)[:900], ', '.join('%s %.0f' % (name, tt * 1000) for tt, name in raw[:6] if tt >= 0.002))
    return rows, text, spent


def reset_settings():
    """Fresh settings and locale; keep catalogs cached while relocalizing."""
    from ..homeui.app import Settings
    from ..settings_cache import invalidate
    from ..i18n import reset_language_cache
    invalidate()
    reset_language_cache()
    try:
        from ..homeui import rows as HR
        HR.forget_nuvio_tabs()      # labels follow the language
    except Exception:
        pass
    a = app()
    a.settings = Settings()
    a._tmdb_settings = None
    language = HeadlessEnricher(a)._tmdb_language() or ''
    active = C.prop('dhs.tab') or 'all'
    for tab in [active] + [t for t in C.TABS if t != active]:
        for slot, spec in published(tab):
            path = C.row_file(tab, spec['id'])
            with C.file_lock(path):
                data = C.read_json(path)
                if not data or data.get('lang') == language:
                    continue
                data['lang'] = language
                data['enriched'] = False
                # Old work must not overwrite this newer language generation.
                data['h'] = C.content_hash(data.get('items') or [], {'lang': language})
                C.write_json(path, data)
            C.request_refresh(tab, spec['id'], enrich_only=True)


# --------------------------------------------------------------------------
# specs and publishing
# --------------------------------------------------------------------------

def spec_of(row, tab):
    meta = row.meta or {}
    progress = bool(meta.get('progress')) or row.kind == 'continue'
    walls = bool(row.kind == 'collection' and (row.shape or '') in COLLAGE_SHAPES)
    params = dict(meta.get('params') or {})
    # what the row reads is part of its id: a row edited to read something
    # else starts from a new cache
    ident = row.key if not params else '%s|%s' % (row.key, json.dumps(params, sort_keys=True))
    return {
        'key': row.key, 'profile_scope': _scope.key(),
        'id': C.short_id(ident),
        'tab': tab,
        'title': row.title or '',
        'sub': row.subtitle or '',
        # v5.10.177: skin.dexhub 3.25 draws every style itself; a collection
        # row the user made (v5.10.179) has the shape 'shelf' and its look
        # v5.10.183: a collection row (folders) in a collection style shows
        # each folder as a wall card (its logo, its catalog's posters)
        'shape': 'shelf' if (meta.get('shelf') or walls) else (row.shape or 'poster'),
        'look': (row.shape if row.shape in COLLAGE_SHAPES else
                 (app().layout.shape(row.key) if app().layout.shape(row.key) in COLLAGE_SHAPES else 'collage'))
                if (meta.get('shelf') or walls) else '',
        'shelf': bool(meta.get('shelf')),
        'walls': walls,
        'kind': row.kind or 'catalog',
        'params': params,
        'progress': progress,
        'auto_shape': bool(meta.get('auto_shape')),
        'own': row.key in R.OWN_KEYS,
    }


def screen_tab():
    """The Home tab on screen (its rows are published first)."""
    tab = C.prop('dhs.tab') or 'all'
    return tab if tab in C.TABS else 'all'


@_scope.bound
def publish(tabs=C.TABS, reason='', computed=None, quiet=False):
    """Work out every tab's rows and hand them to the skin (``computed``:
    {tab: rows} the caller worked out a moment ago, used as they are).

    v5.10.188: the tab on screen first (its rows reach the skin before the
    others are worked out). ``quiet``: no log line (the caller writes one
    for several calls); the timings are left in LAST."""
    tabs = tuple(tabs)
    first = screen_tab()
    if first in tabs:
        tabs = (first,) + tuple(t for t in tabs if t != first)
    with _publish_lock:
        started = time.monotonic()
        total = 0
        C.set_prop(C.PROP_PUBLISHING, '%.1f' % time.time())
        try:
            from . import strings
            strings.publish()
            total = _publish(tabs, computed or {})
        finally:
            C.set_prop(C.PROP_PUBLISHING, '')
        C.set_prop(C.PROP_PUBLISHED, '%.1f' % time.time())
        C.set_prop(C.PROP_READY, '1')
        spent = _PHASES.copy()
        _PHASES.clear()
        tab_ms = dict(_TAB_MS)
        _TAB_MS.clear()
        elapsed = (time.monotonic() - started) * 1000
        LAST.clear()
        LAST.update(ms=elapsed, tab_ms=tab_ms, phases=spent, total=total)
        if quiet:
            return total
        # v5.10.186: the tabs that took longest to work out (a box: 7 s at start)
        slow = sorted(tab_ms.items(), key=lambda kv: -kv[1])
        slow = ', '.join('%s %.0f' % (tab, ms) for tab, ms in slow if ms >= 400)[:200]
        C.log('rows published (%s%s): %d in %.0f ms%s%s' % (
            reason or 'start', (', tab %s' % tabs[0]) if len(tabs) == 1 else '', total, elapsed,
            (' (rows %.0f, collections %.0f, hand over %.0f ms)' % (
                spent.get('compute', 0), spent.get('store', 0), spent.get('tab', 0))) if spent else '',
            ('; slowest tabs: %s ms' % slow) if slow else ''))
        return total


LAST = {}               # v5.10.188: the last publish's timings (a quiet one's)


_PHASES = {}            # v5.10.185: where a publish spends its time (the log line)
_TAB_MS = {}            # v5.10.186: each tab's rows worked out, ms (the log line)


def _phase(name, since):
    _PHASES[name] = _PHASES.get(name, 0.0) + (time.monotonic() - since) * 1000


def _tiles_digest(tiles):
    """A fingerprint of the cards a collection row is built from."""
    try:
        text = json.dumps(tiles or [], sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        text = repr(tiles)
    return hashlib.sha1(text.encode('utf-8', 'replace')).hexdigest()[:16]


def refresh_collections(computed):
    """Collection rows whose cards changed since they were written (a
    collection edited in Nuvio, a folder added) are written again and asked
    for again; the others are left alone (v5.10.119). ``computed``: {tab:
    rows} just worked out. Returns how many changed."""
    changed = 0
    for tab, rows in (computed or {}).items():
        for row in rows or []:
            if row.kind != 'collection':
                continue
            spec = spec_of(row, tab)
            digest = _tiles_digest(row.tiles)
            brief = _row_brief(C.row_file(tab, spec['id']))
            if brief is not None and brief.get('src') == digest and brief.get('f') == C.FORMAT:
                continue
            _store(tab, spec, row.tiles, None, enriched=True, src=digest)
            if is_published(tab, spec['id']):
                bump(tab, spec['id'])
            changed += 1
    if changed:
        C.log('collection rows written again: %d' % changed)
    return changed


def _publish(tabs, computed=None):
    total = 0
    for tab in tabs:
        try:
            mark = time.monotonic()
            rows = (computed or {}).get(tab)
            if rows is None:
                rows = compute_rows(tab)
            _phase('compute', mark)
            _TAB_MS[tab] = _TAB_MS.get(tab, 0.0) + (time.monotonic() - mark) * 1000
        except Exception:
            C.log('rows of %s failed:\n%s' % (tab, traceback.format_exc()), xbmc.LOGWARNING)
            continue
        specs = []
        for row in rows:
            spec = spec_of(row, tab)
            if row.kind == 'collection':
                # a collection's cards are known now: its cache is written
                # here. v5.10.119: only when they changed (each card is
                # turned into a Kodi item and the row written again: 200
                # cards took 50 to 70 ms on a PC, a box several times that,
                # for every collection row of every tab at every publish)
                digest = _tiles_digest(row.tiles)
                brief = _row_brief(C.row_file(tab, spec['id']))
                if brief is not None and brief.get('src') == digest and brief.get('f') == C.FORMAT:
                    specs.append(spec)
                    continue
                mark = time.monotonic()
                data = _store(tab, spec, row.tiles, None, enriched=True, src=digest)
                _phase('store', mark)
                served = C.prop('dhs.sv.%s.%s' % (tab, spec['id']))
                if served and served != data.get('h'):
                    # the cards changed (another version of the add-on): a
                    # new path makes Kodi read them again
                    C.set_prop('dhs.v.%s.%s' % (tab, spec['id']), _version(tab, spec['id']) + 1)
            specs.append(spec)
        mark = time.monotonic()
        C.write_json(C.spec_file(tab), {'t': C.now(), 'rows': specs})
        total += _publish_tab(tab, specs)
        _phase('tab', mark)
    _legacy_focus()
    return total


def _legacy_focus():
    """v5.10.325: the Legacy Home opens on its first row. Kodi gives the
    window's default control (the hidden hero buttons) the focus before the
    rows are read, so the focus moves once row 1 has its cards."""
    if C.prop('dhs.home.layout') != 'legacy':
        return

    def wait():
        # v5.10.328: only while the Home's focus is still on the hidden hero
        # buttons; a focus anywhere else ends the wait (publish runs on every
        # refresh, and polled for 15 seconds each time before)
        monitor = xbmc.Monitor()
        for _ in range(60):
            if xbmc.getCondVisibility('Window.IsActive(Home)'):
                if not xbmc.getCondVisibility('Control.HasFocus(4010)'):
                    return
                if xbmc.getCondVisibility('Integer.IsGreater(Container(5001).NumItems,0)'):
                    xbmc.executebuiltin('SetFocus(5001)')
                    return
            if monitor.waitForAbort(0.25):
                return
    threading.Thread(target=wait, name='dhs-legacy-focus', daemon=True).start()


# --------------------------------------------------------------------------
# reading a row
# --------------------------------------------------------------------------

def prune(max_age=3600.0):
    """Remove the caches of rows no tab has any more (older than an hour)."""
    keep = set()
    for tab in C.TABS:
        keep.add('%s_%s.json' % (tab, SPOT))
        for spec in load_specs(tab):
            keep.add('%s_%s.json' % (tab, spec.get('id')))
    folder = C.folder('rows')
    removed = 0
    try:
        names = os.listdir(folder)
    except Exception:
        return 0
    for name in names:
        if not name.endswith('.json') or name in keep:
            continue
        path = os.path.join(folder, name)
        try:
            if C.now() - os.path.getmtime(path) > max_age:
                os.remove(path)
                removed += 1
        except Exception:
            pass
    return removed


def _row_lock(tab, row_id):
    with _fetch_locks_lock:
        lock = _fetch_locks.get((tab, row_id))
        if lock is None:
            lock = _fetch_locks[(tab, row_id)] = threading.Lock()
        return lock


def ttl_of(spec):
    if spec.get('kind') == 'collection':
        return C.TTL_COLLECTION
    if spec.get('progress'):
        return C.TTL_PROGRESS
    return C.TTL_CATALOG


EMPTY_GRACE = 10 * 60.0     # a row that had titles stays on show this long when reads come back empty
FAIL_PAUSES = (5 * 60.0, 15 * 60.0, 30 * 60.0, 60 * 60.0)   # a failing row's next reads (v5.10.119)


def fetch(tab, row_id, spec=None, fresh=False, patience=0.0, wait_other=True):
    """Read a row through its route and store it; returns the cache dict or None."""
    spec = spec or find_spec(tab, row_id)
    if spec is None:
        return None
    lock = _row_lock(tab, row_id)
    with lock:
        busy = 'dhs.busy.%s.%s' % (tab, row_id)
        if wait_other and _read_elsewhere(busy):
            # the Home's first read or the service read it meanwhile
            data = C.read_json(C.row_file(tab, row_id))
            if C.fresh_copy(data, ttl_of(spec)):
                return data
        old = C.read_json(C.row_file(tab, row_id))
        if old is not None and old.get('fails') and C.fresh_copy(old, old.get('ttl') or ttl_of(spec)):
            # v5.10.120: a row whose server failed waits out its pause, a
            # read asked by the service included (a box read three failing
            # rows 56 times each in 26 minutes)
            return old
        fresh = bool(fresh or C.fresh_asked())
        C.set_prop(busy, '%.1f' % time.time())
        started = time.monotonic()
        try:
            # v5.10.115: the mark stays until the copy is written: a call
            # waiting for this read (the page's own, serve._first_read) read
            # the row again when it saw the mark go before the file came
            return _fetch_marked(tab, row_id, spec, old, fresh, patience, started)
        finally:
            C.set_prop(busy, '')


def _gone(exc):
    """The catalog is not there (HTTP 404): its row is empty, not offline."""
    text = str(exc)
    return 'HTTP 404' in text or ' 404 ' in (' %s ' % text) or 'Not Found' in text


@_scope.bound
def _fetch_marked(tab, row_id, spec, old, fresh, patience, started):
    gone = False
    try:
        tiles, more = _load(tab, spec, fresh=fresh, patience=patience)
        tiles = [t for t in tiles or [] if not is_note(t)]
    except Exception as exc:
        C.log('row "%s" failed: %s' % (spec.get('title'), exc), xbmc.LOGWARNING)
        gone = _gone(exc)
        if old is not None and not gone:
            # offline or a slow server: keep what was shown. v5.10.119: and
            # read it again after a pause that grows with each failure (each
            # turn waited for the server's timeout again: the Home's next
            # look, the service's next round, the next playback's end)
            fails = int(old.get('fails') or 0) + 1
            pause = FAIL_PAUSES[min(fails, len(FAIL_PAUSES)) - 1]
            old['fails'] = fails
            old['t'] = time.time() - float(old.get('ttl') or ttl_of(spec)) + pause
            try:
                C.write_json(C.row_file(tab, row_id), old)
            except Exception:
                pass
            C.log('row "%s": the last titles stay, read again in %d min' % (spec.get('title'), pause // 60))
            return old
        tiles, more = [], None
    had_titles = bool(old and old.get('items'))
    if (not tiles and had_titles and not gone and not (old or {}).get('choice_changed')
            and not spec.get('own') and spec.get('kind') != 'collection'):
        # a source that answers with nothing once (a timeout it swallowed,
        # a server waking up) does not empty a row that had titles: the
        # row stays until reads come back empty for EMPTY_GRACE
        since = float(old.get('empty_since') or 0) or time.time()
        if time.time() - since < EMPTY_GRACE:
            old['empty_since'] = since
            # read again once the grace is over (not at every turn before)
            old['t'] = time.time() - float(old.get('ttl') or ttl_of(spec)) + EMPTY_GRACE
            C.write_json(C.row_file(tab, row_id), old)
            C.log('row "%s" came back empty: the last titles stay' % spec.get('title'))
            return old
    if spec.get('auto_shape') and tiles:
        spec['shape'] = R.dominant_shape(tiles, spec.get('shape') or 'poster')
    data = _store(tab, spec, tiles, more, old=old)
    if gone:
        # v5.10.120: a catalog the add-on no longer has (HTTP 404) leaves
        # the Home at once, its old titles with it, and is asked again after
        # the pauses of a failing row
        fails = int((old or {}).get('fails') or 0) + 1
        pause = FAIL_PAUSES[min(fails, len(FAIL_PAUSES)) - 1]
        data['fails'] = fails
        data['t'] = time.time() - float(data.get('ttl') or ttl_of(spec)) + pause
        C.write_json(C.row_file(tab, row_id), data)
    shown = is_published(tab, row_id)
    if shown:
        for slot, current in published(tab):
            if current['id'] == row_id:
                sub = (spec.get('sub') or '') if show_source() else ''     # v5.10.286: hidden source
                if sub.strip().lower() == (spec.get('title') or '').strip().lower():
                    sub = ''
                C.set_prop('dhs.%s.%d.sub' % (tab, slot), '  ·  '.join(p for p in (sub, data.get('filter_summary')) if p))
                break
    if tab not in C.TABS:
        # a collection folder's page (folderpage.py): an empty row leaves it
        if not tiles and shown:
            from . import folderpage
            folderpage.republish()
    elif not tiles and shown:
        # an empty row leaves the Home (publishing skips it) until it has titles again
        C.ask_republish('empty row')
    elif tiles and not shown and old is not None and not old.get('items'):
        # a row that was empty has titles again (Continue Watching after a first play)
        C.ask_republish('row back')
    C.log('row "%s" %d item(s) in %.0f ms' % (spec.get('title'), len(tiles), (time.monotonic() - started) * 1000))
    _follow_spot(tab, spec)
    return data


def _follow_spot(tab, spec):
    """A catalog row on show was read again: the tab's spotlight picks may
    come from it (skin.dexhub 2)."""
    if spec.get('own') or spec.get('progress') or (spec.get('kind') or 'catalog') != 'catalog':
        return
    if tab not in C.TABS or not is_published(tab, spec.get('id')):
        return
    try:
        update_spot(tab)
    except Exception as exc:
        C.log('spotlight of %s: %s' % (tab, exc))


def _read_elsewhere(busy, patience=20.0):
    """Another add-on call reads this row now (this interpreter's lock is
    held, so the mark is someone else's): wait for it; True when it did."""
    try:
        since = float(C.prop(busy) or 0)
    except ValueError:
        since = 0.0
    if not since or time.time() - since > 60.0:
        return False            # no mark, or one left by a call that died
    deadline = time.monotonic() + patience
    monitor = xbmc.Monitor()
    while C.prop(busy) and time.monotonic() < deadline:
        if monitor.waitForAbort(0.1):
            break
    return True


_NOTE_RE = re.compile(r'^\[COLOR [^\]]+\].*\[/COLOR\]$', re.S)
_EMPTY_RE = re.compile(u'^(?:\u0644\u0627 \u062a\u0648\u062c\u062f|\u0644\u0627 \u064a\u0648\u062c\u062f|Nothing to |Nothing in |No items)', re.I)


def is_note(tile):
    """A line a listing shows in place of titles ("Nothing in Emby"): the
    Home hides an empty row instead of showing it as a card."""
    if not isinstance(tile, dict) or tile.get('kind') == 'work':
        return False
    label = (tile.get('title') or tile.get('label') or '').strip()
    if _NOTE_RE.match(label):
        return True
    # v5.10.157: the same lines once their colour is taken off ("Nothing to
    # continue on Plex"): an empty server row leaves the Servers tab
    return tile.get('kind') == 'nav' and bool(_EMPTY_RE.match(label))


def _load(tab, spec, fresh=False, patience=0.0):
    home = HeadlessHome(app(), tab)
    if spec.get('kind') == 'collection':
        for row in compute_rows(tab):
            if row.key == spec.get('key'):
                return list(row.tiles), None
        return [], None
    if spec.get('own'):
        loader = home._progress_loader(spec['key'], tab)
    else:
        params = spec.get('params') or {}
        if not params.get('action'):
            return [], None
        loader = home._catalog_loader(params, progress=bool(spec.get('progress')))
    if fresh:
        from ..dexhub import client as _client
        with _client.fresh_reads():
            result = loader(patience=patience)
    else:
        result = loader(patience=patience)
    from ..homeui import grid_filters as GF
    spec['_filter_summary'] = getattr(home, '_filter_summaries', {}).get(GF.page_key(spec.get('params') or {}), '')
    return result


ROW_ITEMS = 40          # titles a Home row holds (See all lists the rest)
ENRICH_ITEMS = 200      # all stored titles, processed in interruptible batches of six


@_scope.bound
def _store(tab, spec, tiles, more, enriched=False, old=None, src=None):
    if spec.get('profile_scope', _scope.key()) != _scope.key():
        raise RuntimeError('Discarded row from a previous profile')
    if spec.get('walls'):
        from . import walls as W
        tiles = W.dress(tab, spec, tiles)
    ctx = Context(tab)
    ctx.batch = True
    items = []
    limit = ROW_ITEMS if spec.get('kind') != 'collection' else 200
    for index, tile in enumerate((tiles or [])[:limit]):
        try:
            items.append(ctx.item(tile, spec))
        except Exception:
            C.log('item failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    ctx.flush_seeds()
    if spec.get('shape') in RANKED_SHAPES:
        # v5.10.175: Top 10 style; 5.10.177: the number stands beside the card
        for index, item in enumerate(items[:10]):
            if (item.get('props') or {}).get('kind') in ('work', 'folder'):
                item.setdefault('props', {})['rank'] = str(index + 1)
    # v5.10.173: Continue Watching and big banner rows show each title's
    # logo, so they get the TMDb pass too (with or without a TMDb language)
    # v5.10.297: a collection's page (fold) shows each title's logo too
    logo_pass = bool(spec.get('own') or spec.get('shape') in LOGO_SHAPES or tab in ('library', 'servers', 'fold'))
    carried = _carry_enrichment(items, old, ctx.language or ('-' if logo_pass else ''), ctx)
    # See all: the row's whole listing in a grid, with its Sort, Genre and
    # Filter. v5.10.286: only when the source has more than the row shows
    # (a next page, or a full first page: user catalog rows of 20 included);
    # a short row has none (a collection banner row is then drawn as cards)
    if (items and spec.get('kind') == 'catalog' and not spec.get('own') and not spec.get('shelf')
            and (more or (spec.get('params') or {}).get('action')) and R.row_has_more(tiles, more)):
        items.append(ctx.more_item(spec, tiles, more))
    if not enriched and ((ctx.language and not spec.get('own')) or logo_pass):
        enriched = carried
    if spec.get('shape') in COLLAGE_SHAPES and not spec.get('own'):
        # v5.10.177: the whole row as one collection banner (skin.dexhub 3.25)
        banner = _collage_item(ctx, spec, items, len(tiles or []))
        if banner is not None:
            items, enriched = [banner], True
    data = {
        't': C.now(), 'key': spec.get('key'), 'title': spec.get('title'), 'shape': spec.get('shape'),
        'kind': spec.get('kind'), 'items': items, 'more': more, 'h': C.content_hash(items),
        'look': spec.get('look') or '',
        'lang': ctx.language,
        'enriched': bool(enriched or ((not ctx.language or spec.get('own')) and not logo_pass)),
        'ttl': ttl_of(spec), 'f': C.FORMAT, 'filter_summary': spec.get('_filter_summary') or '',
        'ev': ENRICH_V,
    }
    # v5.10.288: read under the choice in the settings now; it is done when
    # no choice replaces posters or no title follows it (else: _poster_pass)
    if not C.poster_choice_active() or not any(_poster_item(item) for item in items[:ENRICH_ITEMS]):
        data['pg'] = C.poster_gen()
    from ..meta_policy import source_appearance
    if source_appearance() and not ctx.language:
        # Source text/art need no row-wide TMDb or badge pass; only a show's
        # seasons and status (v5.10.228) are looked up
        data['enriched'] = not _needs_status(items)
    elif any((i.get('props') or {}).get('kind') == 'work' and not (i.get('art') or {}).get('poster') for i in items):
        data['enriched'] = False
    if src:
        data['src'] = src           # the cards it was built from (_publish)
    C.write_json(C.row_file(tab, spec['id']), data)
    return data


def _sane_duration(value):
    # v5.10.286: a runtime read twice as minutes (150h 0m)
    from ..durations import sane
    return sane(value)


RANKED_SHAPES = ('ranked', 'tall_ranked', 'wide_ranked')
LOGO_SHAPES = ('wide', 'wide_ranked', 'tall', 'tall_ranked', 'feature')
COLLAGE_SHAPES = ('collage', 'mosaic', 'stack', 'collection_spotlight', 'collection_compact', 'collection_ranking')


def _art_of(item):
    art = item.get('art') or {}
    return art.get('poster') or art.get('thumb') or art.get('landscape') or art.get('fanart') or ''


def _collage_item(ctx, spec, items, count):
    """One card for a row drawn as a collection banner: its first posters
    (p1 to p14; a title with no poster lends its other art), its name, how
    many titles, and See all behind it. None when the row has no See all
    (the row is then drawn as cards)."""
    more = next((i for i in items if (i.get('props') or {}).get('kind') == 'more'), None)
    works = [i for i in items if (i.get('props') or {}).get('kind') in ('work', 'folder') and _art_of(i)]
    if more is None or not works:
        return None
    lead = next((i for i in works if (i.get('art') or {}).get('fanart')), works[0])
    lead_art = lead.get('art') or {}
    count_line = ctx.tr('%d عمل') % count if count else ''
    line = '  ·  '.join(p for p in (spec.get('sub') or '', count_line) if p)
    props = dict(more.get('props') or {})
    props.update({'kind': 'collage', 'cstyle': spec.get('shape') or 'collage',
                  'collage_line': line, 'collage_button': ctx.tr('عرض الكل'), 'wide_meta': line})
    from ..homeui.row_styles import preview_fields
    props.update(preview_fields(works))
    arts = [_art_of(item) for item in works]
    arts = (arts * (20 // max(1, len(arts)) + 1))[:20]
    for index, art in enumerate(arts):
        props['p%d' % (index + 1)] = art
    return {'label': spec.get('title') or '', 'path': more.get('path') or '', 'folder': True,
            'menu': list(more.get('menu') or []),
            'art': {'poster': _art_of(lead), 'thumb': _art_of(lead),
                    'landscape': lead_art.get('landscape') or lead_art.get('fanart') or '',
                    'fanart': lead_art.get('fanart') or lead_art.get('landscape') or ''},
            'info': {'plot': line}, 'props': props}


_ENRICHED_INFO = ('plot', 'genres', 'duration', 'rating')
_ENRICHED_ART = ('clearlogo', 'fanart', 'landscape', 'poster', 'thumb')


def _src_key(item):
    src = (item or {}).get('src') or {}
    return src.get('imdb_id') or src.get('tmdb_id') or src.get('path') or ''


ENRICH_V = C.ENRICH_V
is_enriched = C.is_enriched


def _ekey(language):
    return '%s~%d' % (language or '-', ENRICH_V)


def _needs_status(items):
    from ..home_art import clean
    for item in list(items or [])[:ENRICH_ITEMS]:
        props, src = item.get('props') or {}, item.get('src') or {}
        if props.get('kind') != 'work':
            continue
        if (not clean((item.get('art') or {}).get('poster')) and src.get('art_checked') != ENRICH_V):
            return True
        if src.get('media_type') in ('series', 'episode') and not props.get('dhs.seasons') and src.get('st') != ENRICH_V:
            return True
    return False


def _keep_progress_art(path, art):
    """v5.10.286 (GitHub #7): art found for a Continue Watching card goes
    into the playback store (fill-only, update_art keeps existing art)."""
    params = R.params_of(path) if path and R.is_dexhub_path(path) else {}
    # the routes that name the progress row exactly (its video id included);
    # update_art changes only an existing row of the store
    action = params.get('action')
    if action not in ('cw_resume', 'cw_play_from_start', 'episode_streams') or not params.get('canonical_id') \
            or (action == 'episode_streams' and not params.get('video_id')):
        return
    try:
        from ..dexhub import playback_store
        playback_store.update_art(params.get('media_type') or '', params['canonical_id'],
                                  params.get('video_id') or '', art.get('poster') or '',
                                  art.get('fanart') or '', '')
    except Exception:
        pass


def _status_pass(path, data, ctx, stop=None, batch=6):
    """Source appearance keeps existing images/text; fill missing posters by ID."""
    from ..home_art import clean, repair_missing_poster
    enricher = HeadlessEnricher(app())
    changed, done = False, 0
    for item in list(data.get('items') or [])[:ENRICH_ITEMS]:
        if (stop is not None and stop()) or done >= batch:
            break
        props, src = item.get('props') or {}, item.get('src') or {}
        art = item.setdefault('art', {})
        if props.get('kind') != 'work':
            continue
        missing = not clean(art.get('poster')) and src.get('art_checked') != ENRICH_V
        status = src.get('media_type') in ('series', 'episode') and not props.get('dhs.seasons') and src.get('st') != ENRICH_V
        if not missing and not status:
            continue
        done += 1
        tile = dict(src, kind='work', poster=clean(art.get('poster')), fanart=clean(art.get('fanart')))
        if missing:
            try:
                if repair_missing_poster(tile):
                    art['poster'] = tile['poster']
                    if not art.get('fanart'):art['fanart'] = tile.get('fanart') or ''
                    # v5.10.286 (GitHub #7): a wide Continue Watching card
                    # draws its landscape; and the art is saved with the
                    # title's progress, so the next read of the row keeps it
                    if not clean(art.get('landscape')):
                        art['landscape'] = art.get('fanart') or ''
                    _keep_progress_art(src.get('path'), art)
                    changed = True
            except Exception:
                pass
            src['art_checked'] = ENRICH_V
        if status:
            try:
                enricher._show_status(tile)
            except Exception:
                pass
            src['st'] = ENRICH_V
            if tile.get('tmdb_id') and not src.get('tmdb_id'):src['tmdb_id'] = tile['tmdb_id']
            if tile.get('seasons_count') or tile.get('tv_status'):
                props['dhs.seasons'], props['dhs.tvstatus'], props['dhs.tvstatus_bg'] = show_chips(ctx.tr, tile.get('seasons_count'), tile.get('tv_status'))
                changed = True
        item['src'], item['props'] = src, props
    current = C.read_json(path)
    if current is not None and current.get('h') != data.get('h'):
        return False
    data['enriched'], data['lang'], data['ev'] = not _needs_status(data.get('items')), ctx.language, ENRICH_V
    if changed:data['h'] = C.content_hash(data.get('items') or [])
    C.write_json(path, data)
    return changed


# v5.10.288: the poster pass. A row's saved copy keeps the posters of the
# choice it was read under; when the choice changes (Settings, Metadata:
# poster source; a unified metadata source; Better Posters' options) every
# saved row of every page is re-dressed from its titles' IDs here, in the
# service, without reading its catalog again. The chosen source's answers
# come from its cache first; a title it has not answered for yet is asked
# (a few per round, interruptible). Before this, only the rows read again
# after the change (their 45 minutes over, or a folder's first row read
# while the page opened) showed the new posters.
_PORTRAIT_SKIP = ('landscape', 'wide', 'wide_ranked', 'feature', 'landscape_info')


def _poster_item(item):
    """A saved title card whose poster follows the choice (homeui.rows.poster_layer_tile)."""
    props, src = (item or {}).get('props') or {}, (item or {}).get('src') or {}
    return R.poster_layer_tile({'kind': props.get('kind'), 'media_type': src.get('media_type'),
                                'imdb_id': src.get('imdb_id'), 'tmdb_id': src.get('tmdb_id'),
                                'path': src.get('path') or ''})


def _poster_pass(path, data, shape='', stop=None, batch=6):
    """Re-dress the row's posters for the choice in the settings now; True
    when a poster changed. The row's stamp ('pg') is written once every
    title has the pass (more rounds follow until then: is_enriched)."""
    from .. import render_metadata
    gen = C.poster_gen()
    changed, asked, finished = False, 0, True
    portrait = shape not in _PORTRAIT_SKIP
    for item in list(data.get('items') or [])[:ENRICH_ITEMS]:
        src = item.get('src') or {}
        if src.get('pg') == gen or not _poster_item(item):
            continue
        if (stop is not None and stop()) or asked >= batch:
            finished = False
            break
        art = item.setdefault('art', {})
        if 'poster0' not in src:
            src['poster0'] = art.get('poster') or ''
        ids = {'imdb_id': src.get('imdb_id') or '', 'tmdb_id': src.get('tmdb_id') or '',
               'tvdb_id': (item.get('ids') or {}).get('tvdb') or ''}
        try:
            poster, net = render_metadata.poster_for(ids, src.get('media_type') or '', src['poster0'], network=True)
        except Exception:
            C.log('poster pass:\n%s' % traceback.format_exc(), xbmc.LOGDEBUG)
            poster, net = '', False
        asked += 1 if net else 0
        if poster and poster != art.get('poster'):
            art['poster'] = poster
            if portrait:
                art['thumb'] = poster
            changed = True
        src['pg'] = gen
        item['src'] = src
    current = C.read_json(path)
    if current is not None and current.get('h') != data.get('h'):
        return False        # the row was read again meanwhile: that copy gets its own pass
    if finished:
        data['pg'] = gen
    if changed:
        data['h'] = C.content_hash(data.get('items') or [])
    C.write_json(path, data)
    return changed


def _carry_enrichment(items, old, language, ctx=None):
    """A row read again keeps the TMDb pass its last copy had (plot, logo,
    genres in the user's language), so the same titles look the same and
    Kodi has nothing to reload. True when every title that gets the pass
    has it."""
    previous = {}
    if old and language and (old.get('lang') or '-') == language:
        for item in old.get('items') or []:
            src = item.get('src') or {}
            if src.get('enriched') == _ekey(language) and _src_key(item):
                previous[_src_key(item)] = item
    complete = True
    for index, item in enumerate(items):
        props = item.get('props') or {}
        if props.get('kind') != 'work' or index >= ENRICH_ITEMS:
            continue
        before = previous.get(_src_key(item))
        if before is None:
            complete = False
            continue
        info, art = item.setdefault('info', {}), item.setdefault('art', {})
        old_info, old_art = before.get('info') or {}, before.get('art') or {}
        for key in _ENRICHED_INFO:
            if old_info.get(key):
                info[key] = old_info[key]
        for key in _ENRICHED_ART:
            if old_art.get(key) and (key in ('clearlogo', 'fanart') or not art.get(key)):
                art[key] = old_art[key]
        ids = item.setdefault('ids', {})
        for key, value in (before.get('ids') or {}).items():
            if value and not ids.get(key):
                ids[key] = value
        # Keep this row's freshly formatted metadata. Older cached copies can
        # contain a full server subtitle, which must not return after refresh.
        for key in ('dhs.rating', 'dhs.ratings', 'dhs.studio_logo', 'dhs.studio_chip', 'dhs.studio_seed',
                    'dhs.seasons', 'dhs.tvstatus', 'dhs.tvstatus_bg'):
            if (before.get('props') or {}).get(key):
                props[key] = before['props'][key]
        if ctx is not None:
            props['dhs.meta'] = ctx.meta(dict(info, kind='work'))
            props['dhs.lgmeta'] = ctx.lg_meta(dict(info, kind='work'))
        src = item.setdefault('src', {})
        old_src = before.get('src') or {}
        for key in ('imdb_id', 'tmdb_id'):
            if old_src.get(key) and not src.get(key):
                src[key] = old_src[key]
        src['enriched'] = _ekey(language)
    return complete


# --------------------------------------------------------------------------
# tiles to cached items
# --------------------------------------------------------------------------

_WORK_TYPES = {'movie': 'فيلم', 'series': 'مسلسل', 'episode': 'حلقة'}
_UI_ART_ACTIONS = frozenset(('play_item', 'streams', 'episode_streams', 'cw_resume'))
_SERVER_ART_ACTIONS = frozenset(('plex_play', 'emby_play', 'jellyfin_play', 'silo_play', 'plex_children'))
_HELPER_ACTIONS = frozenset(('item_open', 'series_meta', 'seasons', 'season', 'play_item', 'streams', 'episode_streams', 'tmdb_player'))
_DEXHUB_ROUTE_RE = re.compile(r'(plugin://plugin\.video\.dexhub/?\?)(?!dh_home=1)')
TMDBH_ID = 'plugin.video.themoviedb.helper'


def episode_code(tile):
    season, episode = (tile or {}).get('season'), (tile or {}).get('episode')
    if not (season or episode):
        return ''
    if not episode:
        return 'S%s' % season      # a season card
    return 'S%s:E%s' % (season or '?', episode)


def list_url(params, title='', shape='', page_tile=None):
    """A Dex Hub listing opened in the Videos window (skin_list)."""
    query = {'r': urlencode(sorted((k, v) for k, v in (params or {}).items() if v not in (None, '')))}
    if title:
        query['t'] = title
    if shape:
        query['s'] = shape
    return C.url('skin_list', **query)


class Context(object):
    """Settings read once per row: the title page choice, the language."""

    def __init__(self, tab):
        a = app()
        self.app = a
        self.tab = tab
        self.tr = a.tr
        self.arabic = a.settings.arabic
        value = str(a.settings.text('homeui_title_page', 'Dex Hub') or '').strip().lower()
        self.title_page = ('tmdbhelper' if value.startswith('tmdb') else
                           'classic' if (value.startswith('classic') or value.startswith('كلاسيك')) else 'dexhub')
        try:
            enricher = HeadlessEnricher(a)
            self.language = enricher._tmdb_language() or ''
        except Exception:
            self.language = ''
        self._helper = None
        self._favorites = None
        self._seeds = {}
        self.batch = False          # True: art keys are written by flush_seeds()

    def flush_seeds(self):
        """Write the art of the server titles listed (one transaction)."""
        seeds, self._seeds = self._seeds, {}
        if not seeds:
            return
        try:
            from .. import cache_store
            cache_store.put_many('ui_seed', seeds, ttl_hours=24)
        except Exception as exc:
            C.log('art keys not saved: %s' % exc, xbmc.LOGWARNING)

    # ------------------------------------------------------------ helpers
    def helper_installed(self):
        if self._helper is None:
            try:
                self._helper = bool(xbmc.getCondVisibility('System.HasAddon(%s)' % TMDBH_ID))
            except Exception:
                self._helper = False
        return self._helper

    def favorite(self, media, canonical):
        if self._favorites is None:
            try:
                from .. import favorites_store as F
                self._favorites = F
            except Exception:
                self._favorites = False
        if not self._favorites or not canonical:
            return False
        try:
            return bool(self._favorites.is_favorite(media, canonical))
        except Exception:
            return False

    def supported(self, tile):
        """A movie or series that opens on the title page (homeui details.supported)."""
        if tile.get('kind') != 'work' or tile.get('cw'):
            return False
        if (tile.get('media_type') or '') not in ('movie', 'series', 'episode'):
            return False
        path = tile.get('path') or ''
        if path and R.is_dexhub_path(path):
            params = R.params_of(path)
            if params.get('action', '') not in _HELPER_ACTIONS:
                # v5.10.194: a server's movie or show with known ids opens on
                # the title page (its episodes play from the server)
                from ..homeui import details as D
                return bool(D.server_route(tile))
            return bool(params.get('canonical_id') or tile.get('imdb_id') or tile.get('tmdb_id'))
        return bool(tile.get('imdb_id') or tile.get('tmdb_id'))

    def helper_path(self, tile):
        media = tile.get('media_type') or ''
        if tile.get('kind') != 'work' or tile.get('cw') or media not in ('movie', 'series', 'episode'):
            return ''
        path = tile.get('path') or ''
        if R.is_dexhub_path(path) and R.params_of(path).get('action', '') not in _HELPER_ACTIONS:
            return ''
        if not self.helper_installed():
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

    def home_route(self, path):
        """A Dex Hub route opened from the Home carries dh_home=1 (see plugin._home_owns_click)."""
        if self.title_page == 'tmdbhelper' or 'dh_home=1' in path:
            return path
        return _DEXHUB_ROUTE_RE.sub(r'\1dh_home=1&', path, count=1)

    def with_ui_art(self, path, action, tile):
        """The title's art for the sources window (homeui window._with_ui_art):
        a server's art goes by a cache key, never in the URL."""
        if action not in _UI_ART_ACTIONS and action not in _SERVER_ART_ACTIONS:
            return path
        params = R.params_of(path)
        if action in _SERVER_ART_ACTIONS and params.get('ui_seed_key'):
            return path
        extra = {}
        if tile.get('clearlogo') and not params.get('ui_clearlogo'):
            extra['ui_clearlogo'] = tile['clearlogo']
        if tile.get('fanart') and not params.get('ui_fanart'):
            extra['ui_fanart'] = tile['fanart']
        if tile.get('poster') and not params.get('ui_poster') and tile.get('media_type') != 'episode':
            extra['ui_poster'] = tile['poster']
        if not extra:
            return path
        if action in _SERVER_ART_ACTIONS:
            # the same art, the same key: a row read again keeps its paths
            # (Kodi is not made to reload it), written once per listing
            key = 'dhs' + hashlib.sha1(json.dumps(extra, sort_keys=True).encode('utf-8')).hexdigest()[:29]
            self._seeds[key] = extra
            if not self.batch:
                self.flush_seeds()
            return path + ('&' if '?' in path else '?') + urlencode({'ui_seed_key': key})
        return path + ('&' if '?' in path else '?') + urlencode(extra)

    # ------------------------------------------------------------ clicks
    def click(self, tile, spec):
        """(dhs.click, dhs.target) of a tile: what OK does on it."""
        kind = tile.get('kind') or ''
        if kind == 'library':
            title = tile.get('title') or ''
            if tile.get('server'):
                title = '%s  •  %s' % (tile.get('server'), title)
            return 'grid', list_url(tile.get('params') or {}, title=title, shape='poster')
        if kind == 'action':
            if tile.get('command') == 'catalog_filter':
                return 'run', C.url('skin_row_facet', m=self.tab, id=spec.get('id') or '')
            if (tile.get('command') or '') == 'setup':
                return 'run', C.url('home_setup')
            if (tile.get('command') or '') == 'srv_refresh':
                return 'run', C.url('srv_refresh')       # v5.10.198: the servers' waiting row
            return '', ''
        if kind == 'folder':
            ref = tile.get('folder_ref') or {}
            indexes = tile.get('source_indexes') or []
            if len(indexes) == 1:
                params = {'action': 'collection_nuvio_source_open', 'set_id': ref.get('set_id') or '',
                          'group_id': ref.get('group_id') or '', 'folder_id': ref.get('folder_id') or '',
                          'source_index': str(indexes[0]), 'page': '1'}
                return 'grid', list_url(params, title=tile.get('title') or '')
            return 'grid', C.url('skin_folder', set_id=ref.get('set_id') or '', group_id=ref.get('group_id') or '',
                                 folder_id=ref.get('folder_id') or '', media_filter=ref.get('media_filter') or '',
                                 t=tile.get('title') or '')
        path = tile.get('path') or ''
        if kind == 'work' and self.title_page == 'dexhub' and self.supported(tile):
            return 'info', ''
        if not path:
            return '', ''
        helper = self.helper_path(tile) if self.title_page == 'tmdbhelper' else ''
        if helper:
            return 'grid', helper
        dexhub = R.is_dexhub_path(path)
        action = R.params_of(path).get('action', '') if dexhub else ''
        if dexhub:
            path = self.home_route(self.with_ui_art(path, action, tile))
        if action in R.PLAYER_ACTIONS or not tile.get('folder'):
            return ('run' if dexhub else 'play'), path
        if dexhub and action in R.SERVER_FOLDER_ACTIONS:
            title = tile.get('title') or tile.get('label') or ''
            show = tile.get('show') or ''
            if show and title and show != title:
                title = '%s  •  %s' % (show, title)
            shape = 'landscape' if (tile.get('season_card') or action == 'silo_episodes') else 'poster'
            return 'grid', list_url(R.params_of(path), title=title, shape=shape)
        if not dexhub or action in R.WORK_FOLDER_ACTIONS or kind == 'work':
            return 'grid', path
        return 'grid', list_url(R.params_of(path), title=tile.get('title') or tile.get('label') or '')

    # ------------------------------------------------------------ display
    def plot_with_facts(self, tile):
        """The story; a server copy's facts line before it while the servers
        page shows no badges for it (Badges setting off)."""
        plot = tile.get('plot') or tile.get('tagline') or ''
        facts = tile.get('facts_line') or ''
        if facts:
            try:
                from ..ui_preferences import enabled
                shown = enabled('badges_home')
            except Exception:
                shown = True
            if not shown:
                return ('%s\n\n%s' % (facts, plot)) if plot else facts
        return plot

    def meta(self, tile):
        kind = tile.get('kind')
        out = []
        if kind == 'folder':
            count = int(tile.get('source_count') or 0)
            if count > 1:
                out.append(self.tr('%d كتالوج') % count)
            elif count == 1:
                out.append(self.tr('كتالوج واحد'))
        elif kind == 'work':
            if tile.get('year'):
                out.append(str(tile['year']))
            from ..durations import sane  # v5.10.286
            duration = sane(tile.get('duration'))
            if duration >= 60:
                hours, minutes = divmod(duration // 60, 60)
                if self.arabic:
                    out.append(('%dس %dد' % (hours, minutes)) if hours else ('%dد' % minutes))
                else:
                    out.append(('%dh %dm' % (hours, minutes)) if hours else ('%dm' % minutes))
            genres = tile.get('genres') or []
            if genres:
                out.append(' / '.join(genres[:3]))
            if tile.get('mpaa'):
                out.append(str(tile['mpaa']))
            # The subtitle has its own bounded label. Only an episode code
            # belongs in the compact facts line, never server/file details.
            code = episode_code(tile)
            if code:
                out.append(code)
        elif kind == 'library':
            out.append(tile.get('brand') or '')
        return '   •   '.join([m for m in out if m])

    @staticmethod
    def rating(tile):
        """The rating shown after the meta line, by a star picture (the
        interface font has no star glyph)."""
        if tile.get('kind') != 'work':
            return ''
        try:
            value = float(tile.get('rating') or 0)
        except Exception:
            return ''
        return ('%.1f' % value) if value > 0 else ''

    def badge(self, tile):
        kind = tile.get('kind')
        media = tile.get('media_type') or ''
        if kind == 'library':
            return self.tr('مكتبة')
        if kind == 'action':
            return self.tr('إعداد')
        if kind == 'folder':
            return self.tr('مجموعة')
        if tile.get('season_card'):
            return self.tr('موسم')
        if media in _WORK_TYPES:
            return self.tr(_WORK_TYPES[media])
        return ''

    def remaining(self, tile):
        progress = int(tile.get('progress') or 0)
        if progress and tile.get('total'):
            left = max(0, int(float(tile.get('total')) - float(tile.get('resume') or 0)) // 60)
            if left:
                return self.tr('متبقي %d دقيقة') % left
        return ''

    def lg_meta(self, tile):
        """Type, genres, year and length, the line under the logo on the
        Legacy Home (v5.10.325)."""
        media = tile.get('media_type') or {'movie': 'movie', 'tvshow': 'series', 'episode': 'episode'}.get(tile.get('mediatype') or '', '')
        kind = {'movie': 'فيلم', 'series': 'مسلسل', 'tv': 'مسلسل', 'episode': 'حلقة'}.get(media, '')
        out = [self.tr(kind) if kind else '']
        genres = tile.get('genres') or []
        if isinstance(genres, str):
            genres = [genres]
        if genres:
            out.append(' / '.join(str(g) for g in genres[:2]))
        if tile.get('year'):
            out.append(str(tile['year']))
        try:
            from ..durations import sane
            duration = sane(tile.get('duration'))
        except Exception:
            duration = 0
        if duration >= 60 and media != 'series':
            hours, minutes = divmod(duration // 60, 60)
            if self.arabic:
                out.append(('%d س %02d د' % (hours, minutes)) if hours else ('%d د' % minutes))
            else:
                out.append(('%d h %02d min' % (hours, minutes)) if hours else ('%d min' % minutes))
        return '  •  '.join([m for m in out if m])

    def wide_meta(self, tile):
        """S2:E1 · 33 min left, or year · genre (Apple TV's line under the logo)."""
        parts = []
        if tile.get('media_type') == 'episode':
            parts.append(episode_code(tile))
            # v5.10.214: the episode's name beside its number (Episode names);
            # it fills the line, the bar under it shows what is left
            name = R.card_episode_name(tile)
            if name:
                parts.append(name)
                return '  ·  '.join([p for p in parts if p])
        try:
            pct = float(tile.get('progress') or 0)
            length = float(tile.get('duration') or tile.get('total') or 0)
        except (TypeError, ValueError):
            pct, length = 0.0, 0.0
        if 0 < pct < 100 and length > 60:
            parts.append(self.tr('باقي %d د') % max(1, int(round(length * (100.0 - pct) / 100.0 / 60.0))))
        elif tile.get('kind') == 'work':
            genres = tile.get('genres') or []
            if isinstance(genres, str):
                genres = [genres]
            parts.extend([str(tile.get('year') or ''), str(genres[0]) if genres else ''])
        elif tile.get('hint'):
            parts.append(tile.get('hint'))
        return '  ·  '.join([p for p in parts if p])

    def item(self, tile, spec):
        """One cached item for a tile of a row (or a grid, or a related list)."""
        tile = R.strip_tile(tile)
        kind = tile.get('kind') or ''
        media = tile.get('media_type') or ''
        title = tile.get('title') or tile.get('label') or ''
        show = tile.get('show') or ''
        label = title
        episode_name = ''
        if media == 'episode':
            episode_name = R.card_episode_name(tile)
        if media == 'episode' and show:
            label = show
        subtitle, hero_sub = '', ''
        if media == 'episode' and (tile.get('season') or tile.get('episode')):
            parts = [episode_code(tile), episode_name]
            subtitle = '  ·  '.join([p for p in parts if p])
            if show:
                hero_sub = subtitle
            if spec.get('grid') and (episode_name or title):
                # a season's page: the cards name the episodes, not the show
                label = episode_name or (title if R.card_episode_name({'episode_name': title}) else
                                         self.tr('الحلقة %s') % (tile.get('episode') or ''))
                subtitle = episode_code(tile)
        elif tile.get('hint'):
            subtitle = hero_sub = tile.get('hint')
        elif tile.get('cw') and tile.get('label2'):
            subtitle = tile.get('label2')
        poster = tile.get('poster') or ''
        landscape = R.card_landscape(tile)
        fanart = tile.get('fanart') or tile.get('landscape') or ''
        if kind in ('library', 'action'):
            fanart = tile.get('fanart') or ''
        if kind == 'folder' and not fanart:
            fanart = tile.get('group_fanart') or ''
        art = {'poster': poster, 'thumb': poster if spec.get('shape') not in ('landscape', 'wide', 'wide_ranked', 'feature', 'landscape_info') else landscape,
               'landscape': landscape, 'fanart': fanart, 'clearlogo': tile.get('clearlogo') or ''}
        if kind == 'channel':
            art['icon'] = tile.get('logo') or ''
        if kind == 'folder' and self.app.settings.flag('homeui_focus_gif', True):
            # a collection's animated cover, once it is on the device (fetch_gifs)
            gif = _local_gif(tile)
            if gif:
                art['gif'] = gif
        click, target = self.click(tile, spec)
        if kind == 'work' and not tile.get('studio_logo') and (tile.get('studios') or tile.get('studio')):
            try:
                from .. import studio_art
                logo = studio_art.resolve(tile.get('studios') or tile.get('studio'))
                if logo:
                    tile['studio_logo'] = logo
                    tile['studio_logo_chip'] = False
            except Exception:
                pass
        hero_title = show if (media == 'episode' and show) else title
        props = {
            'kind': kind,
            'hide_title': '1' if tile.get('hide_title') else '',
            'show_title': '1' if tile.get('season_card') and not tile.get('season_as_show') else '',
            'initials': R.initials(label),
            'subtitle': subtitle,
            'dhs.click': click,
            'dhs.target': target,
            'dhs.badge': self.badge(tile),
            'dhs.meta': self.meta(tile),
            'dhs.rating': self.rating(tile),
            'dhs.ratings': json.dumps(tile.get('ratings') or {}, ensure_ascii=False),
            'dhs.hero_video': (tile.get('hero_video') or '') if kind == 'folder' else '',
            'dhs.gif_key': C.short_id(tile.get('gif_url') or '') if kind == 'folder' and tile.get('gif_url') else '',
            'dhs.studio_logo': tile.get('studio_logo') or '',
            'dhs.studio_chip': '1' if tile.get('studio_logo_chip') else '',
            'dhs.server': tile.get('direct_from') or '',
            # v5.10.223: the name alone (the logos came with their tile and border)
            'dhs.server_logo': '',
            'dhs.sub': hero_sub,
            'dhs.remaining': self.remaining(tile),
            'dhs.long': '1' if len(hero_title) > 26 else '',
            # v5.10.144: a server copy's facts, drawn as badges by the hero
            'dhs.facts': tile.get('facts_line') or '',
            # v5.10.173: the line on a big banner card (row shape 'wide')
            'wide_meta': self.wide_meta(tile),
            'dhs.lgmeta': self.lg_meta(tile) if kind == 'work' else '',
        }
        # v5.10.227: a show's seasons and status, once TMDb told them
        if tile.get('seasons_count') or tile.get('tv_status'):
            props['dhs.seasons'], props['dhs.tvstatus'], props['dhs.tvstatus_bg'] = \
                show_chips(self.tr, tile.get('seasons_count'), tile.get('tv_status'))
        if kind == 'folder' and click == 'grid' and '?action=skin_folder&' in (target or ''):
            # skin.dexhub 3.2: the folder opens as a page of rows (folderpage.py);
            # an older skin and Arctic Fuse 3 keep the cards page (dhs.target)
            from .folderpage import page_url
            props['dhs.fold'] = page_url(tile.get('folder_ref') or {}, tile.get('title') or '')
        if click == 'info' or self.info_page(tile) or self.supported(tile):
            props.update(self.title_props(tile))
            props['dhs.open_title'] = '1' if self.supported(tile) else ''
            props['dhs.raw'] = tile.get('path') or ''
        mediatype = {'movie': 'movie', 'series': 'tvshow', 'episode': 'episode'}.get(media, 'video')
        if tile.get('season_card'):
            mediatype = 'season'
        info = {
            'title': hero_title if kind == 'work' else title,
            'mediatype': mediatype,
            'plot': self.plot_with_facts(tile),
            'year': tile.get('year') or '',
            'genres': list(tile.get('genres') or [])[:3],
            'rating': tile.get('rating') or 0,
            'duration': _sane_duration(tile.get('duration')),
            'mpaa': tile.get('mpaa') or '',
            'tvshowtitle': show,
            'season': tile.get('season') or '',
            'episode': tile.get('episode') or '',
        }
        ids = {'imdb': tile.get('imdb_id') or '', 'tmdb': tile.get('tmdb_id') or '', 'tvdb': tile.get('tvdb_id') or ''}
        menu = [list(m) for m in (tile.get('menu') or []) if isinstance(m, (list, tuple)) and len(m) > 1]
        if tile.get('collage') is not None:
            # v5.10.179: a card of a collection row: its posters and its look
            for index, poster in enumerate(tile['collage'][:20]):
                props['p%d' % (index + 1)] = poster or ''
            props['collage_line'] = tile.get('collage_line') or ''
            props['cstyle'] = spec.get('look') or 'collage'
            props.update(tile.get('collection_preview') or {})
        if spec.get('kind') == 'catalog' and not spec.get('own') and not spec.get('progress') and (spec.get('params') or {}).get('action'):
            menu.append([self.tr('خيارات الكتالوج'), 'RunPlugin(%s)' % C.url('skin_row_facet', m=self.tab, id=spec.get('id') or '')])
        if spec.get('key') and spec.get('id') and not spec.get('grid') and self.tab != 'fold':
            menu.append([self.tr('شكل الصف'), 'RunPlugin(%s)' % C.url('skin_row_shape', m=self.tab, id=spec['id'])])
        return {
            'label': label,
            'label2': tile.get('show') or '',
            'path': target if click in ('grid', 'run', 'play') else (tile.get('path') or ''),
            'folder': click == 'grid',
            'art': art,
            'info': info,
            'ids': ids,
            'progress': int(tile.get('progress') or 0),
            'watched': bool(tile.get('playcount')) and not int(tile.get('progress') or 0),
            'props': props,
            'menu': menu,
            'src': {'imdb_id': tile.get('imdb_id') or '', 'tmdb_id': tile.get('tmdb_id') or '',
                    'media_type': media, 'title': title, 'show': show, 'year': tile.get('year') or '',
                    'path': tile.get('path') or '', 'enriched': '', 'studios': tile.get('studios') or tile.get('studio') or [],
                    'gif_url': (tile.get('gif_url') or '') if kind == 'folder' else '',
                    # v5.10.288: the listing's own poster, when the poster
                    # choice replaced it (render_metadata.apply)
                    **({'poster0': tile.get('source_poster') or ''} if 'source_poster' in tile else {})},
        }

    def info_page(self, tile):
        """A continue watching title: OK resumes it, Info opens its title page
        (the series' for an episode) with what is left of it (v5.10.110)."""
        if self.title_page != 'dexhub' or tile.get('kind') != 'work':
            return False
        if (tile.get('media_type') or '') not in ('movie', 'series', 'episode'):
            return False
        path = tile.get('path') or ''
        if path and not R.is_dexhub_path(path):
            return False
        params = R.params_of(path) if path else {}
        return bool(params.get('canonical_id') or tile.get('imdb_id') or tile.get('tmdb_id'))

    def title_props(self, tile):
        """What the title page needs (dhs.q is its add-on query, dhs.play the play route)."""
        media = 'series' if tile.get('media_type') in ('series', 'episode') else 'movie'
        path = tile.get('path') or ''
        params = R.params_of(path) if R.is_dexhub_path(path) else {}
        canonical = params.get('canonical_id') or tile.get('imdb_id') or ''
        if media == 'series':
            match = re.fullmatch(r'(tt\d+|tmdb:\d+):\d+:\d+', str(canonical))
            if match:
                canonical = match.group(1)
        if not canonical and tile.get('tmdb_id'):
            canonical = 'tmdb:%s' % tile['tmdb_id']
        if not canonical:
            from ..homeui import details as D
            canonical = D.server_canonical(tile)      # v5.10.195: a server title with no ids
        media_type = params.get('media_type') if params.get('media_type') in ('series', 'anime') else media
        title_id = C.short_id('%s|%s' % (media, canonical))
        query = [('m', media_type), ('id', canonical), ('i', title_id)]
        # continue watching rows name a virtual source: the title's own
        # metadata add-ons find it instead
        source = params.get('source_provider_id') or ''
        if source.startswith('virtual.'):
            source = ''
        name = (tile.get('show') if tile.get('media_type') == 'episode' else '') or tile.get('title') or ''
        from ..homeui import details as D
        server = D.server_route(tile)
        if server:
            # v5.10.194: the server show the page plays its episodes from
            query.append(('srv', urlencode(sorted((k, v) for k, v in server.items() if k != 'ui_seed_key'))))
        for key, value in (('src', source), ('tmdb', tile.get('tmdb_id') or ''),
                           ('imdb', tile.get('imdb_id') or ''), ('title', name),
                           ('year', tile.get('year') or '')):
            if value:
                query.append((key, value))
        play = ''
        if path and params.get('action', '') in R.PLAYER_ACTIONS:
            play = self.home_route(self.with_ui_art(path, params.get('action', ''), tile))
        return {
            'dhs.q': urlencode(query),
            'dhs.id': title_id,
            'dhs.media': 'series' if media == 'series' else 'movie',
            'dhs.play': play,
            'dhs.fav': '1' if self.favorite(media_type if media == 'series' else 'movie', canonical) else '',
        }

    def more_item(self, spec, tiles, more):
        art = self.app.media_path('more_tile.png')
        label = self.tr('عرض الكل')
        params = spec.get('params') or {}
        # v5.10.179: See all (and a row drawn as one collection banner, made
        # from it) keeps the row's menu: its style can be changed back
        menu = []
        if spec.get('kind') == 'catalog' and not spec.get('own') and params.get('action'):
            menu.append([self.tr('خيارات الكتالوج'), 'RunPlugin(%s)' % C.url('skin_row_facet', m=self.tab, id=spec.get('id') or '')])
        if spec.get('key') and spec.get('id') and not spec.get('grid') and self.tab != 'fold':
            menu.append([self.tr('شكل الصف'), 'RunPlugin(%s)' % C.url('skin_row_shape', m=self.tab, id=spec['id'])])
        return {
            'label': label, 'path': list_url(params, title=spec.get('title') or '', shape=spec.get('shape') or ''),
            'menu': menu,
            'folder': True, 'art': {'poster': art, 'landscape': art},
            'props': {'kind': 'more', 'dhs.click': 'grid',
                      'dhs.target': list_url(params, title=spec.get('title') or '', shape=spec.get('shape') or '')},
        }


# --------------------------------------------------------------------------
# the TMDb pass (the user's language), made by the service after a row is read
# --------------------------------------------------------------------------

@_scope.bound
def enrich(tab, row_id, limit=ENRICH_ITEMS, stop=None, batch=6):
    """Enrich all cached titles in bounded batches; True when something changed."""
    path = C.row_file(tab, row_id)
    data = C.read_json(path)
    ctx = Context(tab)
    # v5.10.288: the posters of the poster choice in the settings now first
    # (a row saved under another choice), then the TMDb pass as before
    posters = False
    if data and data.get('pg') != C.poster_gen():
        posters = _poster_pass(path, data, data.get('shape') or '', stop, batch)
        data = C.read_json(path)
    if not data or (is_enriched(data) and data.get('lang') == ctx.language):
        return posters
    from ..meta_policy import source_appearance
    if source_appearance() and not ctx.language:
        # (v5.10.302: Like Nuvio with Nuvio's Home on TMDb takes the pass below)
        return _status_pass(path, data, ctx, stop, batch) or posters
    # without a TMDb language the titles still get the art their catalog
    # lacks (logo, backdrop) and the badges (v5.10.114)
    language_key = _ekey(ctx.language)
    enricher = HeadlessEnricher(app())
    spec = find_spec(tab, row_id) or {'shape': data.get('shape') or 'poster'}
    changed = False
    processed = 0
    targets = list(data.get('items') or [])[:limit]
    for index, item in enumerate(targets):
        if (stop is not None and stop()) or processed >= batch:
            break
        src = item.get('src') or {}
        props = item.get('props') or {}
        retry_due = bool(src.get('auto_retry_at') and time.time() >= src['auto_retry_at'])
        if (props.get('kind') != 'work' or (not retry_due and
                (src.get('enriched') == language_key or src.get('enrich_failed') == language_key))):
            continue
        tile = {'kind': 'work', 'media_type': src.get('media_type') or '', 'title': src.get('title') or '',
                'show': src.get('show') or '', 'year': src.get('year') or '', 'path': src.get('path') or '',
                'imdb_id': src.get('imdb_id') or '', 'tmdb_id': src.get('tmdb_id') or '',
                'studios': src.get('studios') or [],
                'plot': (item.get('info') or {}).get('plot') or '',
                'genres': list((item.get('info') or {}).get('genres') or []),
                'duration': (item.get('info') or {}).get('duration') or 0,
                'rating': (item.get('info') or {}).get('rating') or 0,
                'poster': (item.get('art') or {}).get('poster') or '',
                'fanart': (item.get('art') or {}).get('fanart') or '',
                'landscape': (item.get('art') or {}).get('landscape') or '',
                'clearlogo': (item.get('art') or {}).get('clearlogo') or ''}
        before = dict(tile)
        processed += 1
        try:
            enricher._enrich_now(tile, ctx.language or '')
        except Exception:
            src['enrich_failed'] = language_key
            item['src'] = src
            continue
        src['enriched'] = language_key
        src['auto_retry_at'] = tile.get('_auto_retry_at') or 0
        if tile.get('imdb_id') and not src.get('imdb_id'):
            src['imdb_id'] = tile['imdb_id']
        if tile.get('tmdb_id') and not src.get('tmdb_id'):
            src['tmdb_id'] = tile['tmdb_id']
        info = item.setdefault('info', {})
        art = item.setdefault('art', {})
        if tile.get('year') and not info.get('year'):
            info['year'] = tile['year']
            src['year'] = tile['year']
            changed = True
        if tile.get('plot') and tile['plot'] != before['plot']:
            info['plot'] = tile['plot']
            changed = True
        if tile.get('genres') and tile['genres'] != before['genres']:
            info['genres'] = list(tile['genres'])[:3]
            changed = True
        if tile.get('duration') and not before['duration']:
            info['duration'] = tile['duration']
            changed = True
        for key in ('clearlogo', 'fanart'):
            if tile.get(key) and tile[key] != before[key]:
                art[key] = tile[key]
                changed = True
        if tile.get('fanart') and not art.get('landscape'):
            art['landscape'] = tile['fanart']
        if tile.get('poster') and not before['poster']:
            art['poster'] = tile['poster']
            if spec.get('shape') not in ('landscape', 'wide', 'wide_ranked', 'feature', 'landscape_info'):
                art['thumb'] = tile['poster']
            changed = True
        ids = item.setdefault('ids', {})
        for key, name in (('imdb_id', 'imdb'), ('tmdb_id', 'tmdb')):
            if tile.get(key) and not ids.get(name):
                ids[name] = tile[key]
        # the hero's meta line follows the new genres and runtime
        merged = {'kind': 'work', 'year': info.get('year') or '', 'rating': info.get('rating') or 0,
                  'duration': info.get('duration') or 0, 'genres': info.get('genres') or [],
                  'mpaa': info.get('mpaa') or ''}
        if tile.get('rating') and not info.get('rating'):
            info['rating'] = tile['rating']
            merged['rating'] = tile['rating']
            changed = True
        for name, value in (('dhs.studio_logo', tile.get('studio_logo') or ''),
                            ('dhs.studio_chip', '1' if tile.get('studio_logo_chip') else '')):
            if 'studio_logo' not in tile:
                continue  # focused badges are published separately, never erase source badges
            if value != props.get(name, ''):
                props[name] = value
                changed = True
        # v5.10.227: a show's seasons and status, chips on the Home's hero
        if tile.get('seasons_count') or tile.get('tv_status'):
            chips = show_chips(ctx.tr, tile.get('seasons_count'), tile.get('tv_status'))
            for name, value in zip(('dhs.seasons', 'dhs.tvstatus', 'dhs.tvstatus_bg'), chips):
                if value != props.get(name, ''):
                    props[name] = value
                    changed = True
        props['dhs.meta'] = ctx.meta(merged)
        props['dhs.lgmeta'] = ctx.lg_meta(merged)
        props['dhs.rating'] = ctx.rating(merged)
        if tile.get('ratings'):
            props['dhs.ratings'] = json.dumps(tile['ratings'], ensure_ascii=False)
        if tile.get('_studio_rows'):
            seed = json.dumps(tile['_studio_rows'][:3], ensure_ascii=False)
            if seed != props.get('dhs.studio_seed'):
                props['dhs.studio_seed'] = seed
                changed = True
        item['props'] = props
        item['src'] = src
    # a title in the next language pass is told apart from this one
    current = C.read_json(path)
    if current is not None and current.get('h') != data.get('h'):
        return False        # the row was read again meanwhile: that copy wins
    data['enriched'] = all((i.get('props') or {}).get('kind') != 'work'
                           or (i.get('src') or {}).get('enriched') == language_key
                           or (i.get('src') or {}).get('enrich_failed') == language_key for i in targets)
    data['lang'] = ctx.language
    data['ev'] = ENRICH_V
    retries = [(i.get('src') or {}).get('auto_retry_at') for i in targets
               if (i.get('src') or {}).get('auto_retry_at')]
    data['auto_retry_at'] = min(retries) if retries else 0
    if changed:
        data['h'] = C.content_hash(data.get('items') or [])
    C.write_json(path, data)
    if changed:
        # the spotlight shows the localized plots and logos too
        _follow_spot(tab, spec if spec.get('id') else (find_spec(tab, row_id) or {}))
    return changed or posters


GIF_FETCH = 12          # collection covers fetched per row and pass


def focused_gif_target():
    try:
        layout = C.layout()
        slot = int(C.prop('dhs.fr') or 0)
        # v5.10.140: the focused control's id and the window's, not
        # getCondVisibility (a pause in Kodi's frame each, guistate.py)
        if C.served() == 'af3' or layout.hubs:
            container = int(xbmc.getInfoLabel('Window(Home).Property(TMDbHelper.WidgetContainer)') or 0)
            if not container or int(xbmc.getInfoLabel('System.CurrentControlId') or 0) != container:
                return None
        else:
            from .. import guistate
            if guistate.active() != guistate.HOME:
                return None
            container = layout.row_list(slot) if slot else layout.spotlight
        prefix = 'Container(%d).ListItem.Property(' % container
        key = xbmc.getInfoLabel(prefix + 'dhs.gif_key)') or ''
        row = xbmc.getInfoLabel(prefix + 'dhs.row)') or ''
        if key and ':' in row:
            tab, row_id = row.split(':', 1)
            if tab in C.TABS and row_id:
                return tab, row_id, key
    except Exception:
        pass
    return None


def focused_gif_key():
    target = focused_gif_target()
    return target[2] if target else ''


def fetch_gifs(tab, row_id, stop=None):
    """The animated covers of a collection row's cards, onto the device
    (skin.dexhub 3 plays a card's cover while it has the focus); True when a
    card got its cover (the row is written again: the caller bumps it)."""
    if not app().settings.flag('homeui_focus_gif', True):
        return False
    light = app().settings.flag('homeui_light_mode', True)
    wanted = focused_gif_key() if light else ''
    if light and not wanted:
        return False
    path = C.row_file(tab, row_id)
    data = C.read_json(path)
    if not data or data.get('kind') != 'collection':
        return False
    changed = False
    fetched = 0
    for item in data.get('items') or []:
        if stop is not None and stop():
            break
        if (item.get('props') or {}).get('kind') != 'folder' or (item.get('art') or {}).get('gif'):
            continue
        if light and (item.get('props') or {}).get('dhs.gif_key') != wanted:
            continue
        url = (item.get('src') or {}).get('gif_url') or ''
        if not url:
            continue
        local = media().local(url)
        if not local and fetched < GIF_FETCH:
            fetched += 1
            local = media().fetch(url)
        if local:
            item.setdefault('art', {})['gif'] = local
            changed = True
    if changed:
        data['h'] = C.content_hash(data.get('items') or [])
        C.write_json(path, data)
    return changed
