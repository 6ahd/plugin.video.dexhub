# -*- coding: utf-8 -*-
"""Plugin routes of skin.dexhub (entered from bootstrap before the router).

skin_row is the hot path: Kodi asks for it whenever a row of the skin's Home
comes into view. It is answered from the row's cache without importing the
Dex Hub router (a few milliseconds); an old copy is shown at once and the
service is asked to read the row again. Only a row that was never read is
read here, through the router.
"""
import sys
import time

import xbmc

from . import common as C


def _handle():
    try:
        return int(sys.argv[1])
    except Exception:
        return -1


_WIDGET_ACTIONS = ('skin_row', 'skin_search')
WARM_REQUEST = 'dhs.warm.request'


def _to_service(prop, params):
    """Hand a call to the service when it runs (its heartbeat, dhs.worker,
    is at most 15 s old); False: do it here."""
    import json
    try:
        if time.time() - float(C.prop('dhs.worker') or 0) > 15:
            return False
        from .. import profile_scope
        C.set_prop(prop, json.dumps({'params': dict(params), 'scope': profile_scope.key(), 't': time.time()}))
        return True
    except Exception:
        return False


def run(params):
    action = params.get('action') or ''
    if action in ('skin_af3_catalog', 'skin_af3_import'):
        handle = _handle()
        if handle >= 0:
            from .items import directory
            directory(handle, [], content='videos')
        return None
    if action == 'skin_cover_pick':
        # v5.10.426: a cover from Collections+ for a folder or catalog card
        from ..cover_picker import pick
        return pick(params)
    if action == 'skin_folder_view':
        from ..folder_view import native_options
        return native_options(params, _handle())
    if action == 'skin_fold':
        from ..folder_view import rows_enabled
        if rows_enabled() or C.served() == 'dexhub':
            from .folderpage import request_open
            return request_open(params, _handle())
        from ..catalog_navigation import open_selector
        return open_selector(params, _handle())
    if action == 'skin_empty':
        from .items import directory
        handle = _handle()
        if handle >= 0:
            directory(handle, [], content='videos')
        return None
    if action == 'skin_trailer_config':
        from ..trailer_preferences import publish_presentation
        publish_presentation()
        from ..home_preferences import publish as publish_home
        publish_home()
        return None
    if action == 'skin_exit':
        # v5.10.363 (GitHub #27): Back on the Home's menu asks to exit Kodi
        from .exit_prompt import ask
        return ask()
    if action in _WIDGET_ACTIONS:
        return widget(action, params)
    started = time.monotonic()
    try:
        if action == 'skin_af3':
            # Dex Hub's rows into Arctic Fuse 3, or out again (v5.10.111)
            from . import af3
            return af3.run(params, _handle())
        if action in ('skin_use', 'skin_back', 'skin_pick'):
            # Kodi's skin becomes skin.dexhub, or the one before it (v5.10.113),
            # or any installed skin picked from a list (v5.10.118)
            from . import switch
            run_it = {'skin_use': switch.use, 'skin_back': switch.back, 'skin_pick': switch.pick}[action]
            return run_it(params, _handle())
        if action == 'skin_go':
            # a click in a Kodi list on a Dex Hub item (v5.10.114)
            return go(params, _handle())
        if action.startswith('skin_osd_'):
            # the player's panels (Arctic Fuse 3's OSD in skin.dexhub 3.1, v5.10.114)
            return osd(action, params, _handle())
        if action == 'skin_warm_sources' and _to_service(WARM_REQUEST, params):
            # v5.10.448: the search runs in the service, which lives on: a
            # plugin call's interpreter torn down after a search's threads
            # crashed Kodi 22 (Python 3.14) now and then
            return None
        # v5.10.132: grids and title lists come from the service, which has
        # the router loaded already (listing.py); worked out here otherwise
        from . import listing
        if listing.serve_call(action, params, _handle()):
            return None
        # everything else needs the router
        if action in ('skin_seasons', 'skin_episodes', 'skin_cast', 'skin_related', 'skin_collection', 'skin_studios',
                      'skin_reviews', 'skin_artwork'):
            from . import title
            return title.listing(action, params, _handle())
        from . import actions
        return actions.run(action, params, _handle())
    finally:
        C.log('%s in %.0f ms' % (action, (time.monotonic() - started) * 1000), xbmc.LOGDEBUG)
        _threads_left(action)


def _threads_left(action, wait=1.0):
    """The call's threads end with it (v5.10.119). Kodi waits for every
    thread of a finished call, daemon or not, before the interpreter is free
    again ("waiting on thread" in kodi.log: up to half a minute after a
    title page's first read in the test harness); the network lanes the
    router started are shut down here (bootstrap does it too, after this),
    and a thread still busy a moment later is named in the log."""
    if 'threading' not in sys.modules:
        # v5.10.133: nothing in this call could start a thread (a row from
        # its cache): threading is not imported just to look
        return
    try:
        import threading
        from .. import runtime_cleanup
        runtime_cleanup.shutdown_loaded_pools()
        mine = (threading.current_thread(), threading.main_thread())
        deadline = time.monotonic() + wait
        while True:
            left = [t for t in threading.enumerate() if t not in mine and t.is_alive()]
            if not left or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        if left:
            C.log('%s: threads still running as it ends: %s' % (
                action, ', '.join(sorted('%s%s' % (t.name, ' (daemon)' if t.daemon else '') for t in left))))
    except Exception:
        pass


def go(params, handle):
    """What OK on a Dex Hub item in a Kodi list does (items.kodi_click). A
    widget's click (PlayMedia) waits on a resolve: it is answered at once,
    so Kodi stays where it was, and the click runs on its own (a title page,
    or the play route with no resolve waiting on it). The Videos window
    runs the item as a script (no handle)."""
    if handle >= 0:
        try:
            import xbmcgui
            import xbmcplugin
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
            # Kodi ends the failed resolve before the click's own work starts
            xbmc.sleep(350)
        except Exception:
            pass
    do = params.get('do') or ''
    target = params.get('u') or ''
    if do == 'info':
        from urllib.parse import parse_qsl
        q = dict(parse_qsl(params.get('q') or ''))
        if q and (C.served() == 'dexhub' or _title_page()):
            target = C.url('skin_open', **q)
        elif q:
            # Arctic Fuse 3 or any skin: the add-on's own title page over it
            target = C.url('home_details', **q)
    if not target:
        return None
    if do == 'play' and not target.startswith(C.BASE):
        xbmc.executebuiltin('PlayMedia("%s")' % target.replace('"', '%22'))
    else:
        xbmc.executebuiltin('RunPlugin("%s")' % target.replace('"', '%22'))
    return None


def _title_page():
    """Arctic Fuse 3 with Dex Hub's title page in its information dialog
    (af3pages.py, v5.10.119)."""
    if C.served() != 'af3':
        return False
    try:
        from . import af3pages
        return af3pages.title_ready()
    except Exception:
        return False


_OSD_LISTS = ('skin_osd_streams', 'skin_osd_next', 'skin_osd_episodes', 'skin_osd_seasons', 'skin_osd_sources',
              'skin_osd_substyle')


def osd(action, params, handle):
    from . import osd as O
    if action in _OSD_LISTS:
        try:
            if action == 'skin_osd_sources':
                # v5.10.243: the player's Sources panel
                from . import osd_sources
                osd_sources.sources(params, handle)
            elif action == 'skin_osd_substyle':
                # v5.10.314: the subtitle look
                from . import substyle
                substyle.listing(params, handle)
            else:
                {'skin_osd_streams': O.streams, 'skin_osd_next': O.next_listing,
                 'skin_osd_episodes': O.episodes, 'skin_osd_seasons': O.seasons}[action](params, handle)
        except Exception:
            import traceback
            C.log('%s failed:\n%s' % (action, traceback.format_exc()), xbmc.LOGWARNING)
        # like the skin's widgets: no interpreter left for another panel's call
        raise SystemExit
    if action == 'skin_osd_sources_sort':
        # v5.10.297: the Sources panel's sort row
        from . import osd_sources
        return osd_sources.cycle_sort(params)
    if action == 'skin_osd_sources_filter':
        # v5.10.448: the Sources panel's filter row (the sources page's filters)
        from . import osd_sources
        return osd_sources.cycle_filter(params)
    if action == 'skin_osd_stream':
        return O.set_stream(params)
    if action == 'skin_osd_subtitles':
        # v5.10.315: subtitle (or audio) settings and look, one page
        from . import subwindow
        return subwindow.open_window(params)
    if action in ('skin_osd_substyle_set', 'skin_osd_substyle_preset'):
        from . import substyle
        return (substyle.step if action == 'skin_osd_substyle_set' else substyle.preset)(params)
    if action == 'skin_osd_playnext':
        return O.play_next(params)
    if action == 'skin_osd_info':
        return O.info(params)
    if action == 'skin_osd_pick':
        # v5.10.219: the player's Episodes button
        from . import episode_picker
        return episode_picker.run(params)
    return None


def widget(action, params):
    """A row or search widget of a skin (skin.dexhub, or Arctic Fuse 3).

    The call ends by leaving no Python interpreter for Kodi to reuse
    (v5.10.112). Kodi asks a skin's widgets several at a time; when one of
    those calls is handed the interpreter a previous call left, another
    call starting in the same instant can stop that interpreter before it
    ran (the widget comes back empty: "GetDirectory - Error getting") or
    tear it down under it: in the test harness twelve Dex Hub widgets asked
    at once crashed Kodi after a few dozen openings (SIGSEGV in
    CPythonInvoker::execute, take_gil), and lost a row every second opening
    before that. Ending with SystemExit marks the interpreter used up:
    every widget call gets its own, and neither happens (30 openings, no
    error). A widget call hardly gained from reuse anyway: the next widget's
    call starts while this one is still closing.
    """
    try:
        if action == 'skin_row':
            row(params)
        else:
            # Dex Hub's search as a skin's search widget (v5.10.111)
            from . import search
            search.listing(params, _handle())
    except Exception:
        import traceback
        C.log('%s failed:\n%s' % (action, traceback.format_exc()), xbmc.LOGWARNING)
    _threads_left(action)
    raise SystemExit


def row(params):
    handle = _handle()
    tab = params.get('m') or 'all'
    row_id = params.get('id') or ''
    gates = None
    if params.get('af3'):
        # a widget of Arctic Fuse 3: the service leaves the rows alone meanwhile (gates.py)
        from . import gates
        gates.begin()
    try:
        path = C.row_file(tab, row_id)
        data = C.read_row(path)
        if data is None:
            data = _first_read(tab, row_id)
        items = (data or {}).get('items') or []
        if data is None and C.prop('dhs.pending.%s.%s' % (tab, row_id)):
            C.set_prop('dhs.sv.%s.%s' % (tab, row_id), 'pending')
        if data is not None:
            C.set_prop('dhs.pending.%s.%s' % (tab, row_id), '')
            C.log('row %s/%s served (%d)' % (tab, row_id, len(items)), xbmc.LOGDEBUG)
            C.set_prop('dhs.sv.%s.%s' % (tab, row_id), data.get('h') or '')
            if row_id == 'spot':
                pass                    # the spotlight follows its source row
            elif not C.fresh_copy(data, data.get('ttl')):
                C.request_refresh(tab, row_id)
            elif not C.is_enriched(data):
                C.request_refresh(tab, row_id, enrich_only=True)
        if not params.get('af3'):
            # skin.dexhub: the rows under an empty one load at once (board.mark_empty)
            from . import board
            # v5.10.165: the slot from the path (n), checked with one read
            slot = 0 if row_id == board.SPOT else board.slot_of(tab, row_id, params.get('n'))
            board.mark_empty(tab, row_id, not items, slot=slot)
            if row_id != board.SPOT:
                board.mark_pending(tab, row_id, data is None and bool(C.prop('dhs.pending.%s.%s' % (tab, row_id))),
                                   slot=slot)
        if tab == 'fold':
            # v5.10.448: the collection page numbers its titles (its Top 10 tabs)
            for n, item in enumerate(items, 1):
                item.setdefault('props', {})['dhs.n'] = str(n)
        from . import items as I
        # Arctic Fuse 3 clicks its widgets' items itself (items.kodi_click)
        I.directory(handle, items, tab=tab, content=_content(data), row='%s:%s' % (tab, row_id),
                    kodi_clicks='widget' if params.get('af3') else False, cache_hash=(data or {}).get('h') or '')
    finally:
        if gates is not None:
            gates.end()
    return None


def _content(data):
    shape = (data or {}).get('shape') or 'poster'
    return 'episodes' if shape == 'landscape' else 'movies'


def _first_read(tab, row_id):
    """A row never read before: the service may be reading it right now;
    else it is read here."""
    try:
        alive = time.time() - float(C.prop('dhs.worker') or 0) < 90.0
    except ValueError:
        alive = False
    if alive and C.served() == 'dexhub' and row_id != 'spot':
        from . import board
        if board.find_spec(tab, row_id) is not None:
            C.set_prop('dhs.pending.%s.%s' % (tab, row_id), '1')
            C.set_prop('dhs.sv.%s.%s' % (tab, row_id), 'pending')
            C.request_refresh(tab, row_id)
            return None
    busy = 'dhs.busy.%s.%s' % (tab, row_id)
    try:
        since = float(C.prop(busy) or 0)
    except ValueError:
        since = 0.0
    if since and time.time() - since < 60.0:
        deadline = time.monotonic() + 20.0
        monitor = xbmc.Monitor()
        while C.prop(busy) and time.monotonic() < deadline:
            if monitor.waitForAbort(0.1):
                return None
            data = C.read_row(C.row_file(tab, row_id))
            if data is not None:
                return data
    data = C.read_row(C.row_file(tab, row_id))
    if data is not None:
        return data
    try:
        from . import rows
        return rows.fetch(tab, row_id, wait_other=False)
    except Exception as exc:
        C.log('row %s/%s could not be read: %s' % (tab, row_id, exc), xbmc.LOGWARNING)
        return None


