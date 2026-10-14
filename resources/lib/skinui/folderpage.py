# -*- coding: utf-8 -*-
"""A collection folder as a page of rows in skin.dexhub 3.2 (v5.10.115).

The add-on's Home opens a collection folder (a card of a collection row:
"Netflix", "Action", ...) as a page with one row per catalog of the folder,
the focused title large above them and its trailer behind. skin.dexhub 3.1
listed the catalogs as cards instead. 3.2 has the page (window 1190,
Custom_1190_DexFolder.xml); its rows are a tab of their own, "fold", read and
cached like the Home's rows (skin_row&m=fold): the service reads them again
when they get old and gives them the TMDb pass, and the trailer follows the
focused title (spotlight.py).

skin_fold works the folder's catalogs out, publishes them (dhs.fold.*), opens
the page, then reads its first rows at once, side by side, so the page's own
calls find them ready. The page has eight rows: a folder with more catalogs
(Kaptain's "Netflix" has dozens) shows the first seven as rows and all of
them as cards in the last row, each opening its catalog's grid.

v5.10.119: Arctic Fuse 3 with Dex Hub's pages in (af3pages.py) has the same
page as a window of its own (1981, drawn with the skin's fonts); a skin
without it opens the cards page.
"""
import json
import threading
import time

import xbmc

from . import common as C

TAB = 'fold'
WINDOW = 1190           # skin.dexhub's; Arctic Fuse 3 with Dex Hub's pages: af3pages.fold_window()
FIRST_ROW = 7001        # the page's row n is list 7000 + n
FOCUS_HOLDER = 7099
PREFETCH = 2            # rows read while the page opens
SKIN_ROWS = 8           # the page's rows (gen3 FOLD_ROWS)
CARDS = 'cards'         # the row of all the catalogs, when they are more


def window_id():
    """The folder page's window in the skin in use, or 0 (it has none)."""
    mode = C.served()
    if mode == 'dexhub':
        return WINDOW
    if mode == 'af3':
        from . import af3pages
        if af3pages.fold_ready():
            return af3pages.fold_window()
    return 0


def page_url(ref, title):
    return C.url('skin_fold', set_id=ref.get('set_id') or '', group_id=ref.get('group_id') or '',
                 folder_id=ref.get('folder_id') or '', media_filter=ref.get('media_filter') or '',
                 t=title or '')


def _specs(ref, sources):
    base = '%s|%s|%s|%s' % (ref.get('set_id') or '', ref.get('group_id') or '',
                            ref.get('folder_id') or '', ref.get('media_filter') or '')
    specs = []
    for key, name, subtitle, route in sources:
        ident = '%s|%s|%s' % (base, key, json.dumps(route, sort_keys=True))
        specs.append({
            'key': 'fold:%s' % key, 'id': C.short_id(ident), 'tab': TAB, 'title': name or '',
            'sub': subtitle or '', 'shape': 'poster', 'kind': 'catalog', 'params': dict(route or {}),
            'progress': False, 'auto_shape': True, 'own': False,
        })
    return specs


def open_page(params, handle):
    from . import rows
    from ..homeui import window as W
    started = time.monotonic()
    ref = {'set_id': params.get('set_id') or '', 'group_id': params.get('group_id') or '',
           'folder_id': params.get('folder_id') or '', 'media_filter': params.get('media_filter') or ''}
    app = rows.app()
    home = rows.HeadlessHome(app, 'all')
    try:
        folder, sources = W.BrowseWindow.__dict__['_folder_sources'](home, ref)
    except Exception as exc:
        C.log('collection folder failed: %s' % exc, xbmc.LOGWARNING)
        folder, sources = None, []
    title = params.get('t') or (folder or {}).get('name') or ''
    window = window_id()
    if not sources or not window:
        # nothing to show as rows (or no page for them): the cards page of skin.dexhub 3.1
        xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % C.url(
            'skin_folder', t=title, **dict((k, v) for k, v in ref.items() if v)))
        return None
    wrap = getattr(rows.api(), '_wrap_tokenized_url', None) or (lambda u: u)
    background = ''
    if folder:
        background = wrap(str(folder.get('background') or folder.get('poster') or ''))
    specs = _specs(ref, sources)
    C.write_json(C.spec_file(TAB), {'t': C.now(), 'rows': specs, 'ref': ref, 'title': title})
    C.set_prop('dhs.fold.title', title)
    C.set_prop('dhs.fold.bg', background)
    shown = _publish(specs, ref, title, app.tr)
    C.log('folder "%s": %d row(s) in %.0f ms' % (title, shown, (time.monotonic() - started) * 1000))
    # the page starts at its first row (dhs.fold.new: not when it shows again)
    C.set_prop('dhs.fold.new', '1')
    xbmc.executebuiltin('ActivateWindow(%d)' % window)
    threads = _prefetch(specs[:min(PREFETCH, SKIN_ROWS - 1)])
    _focus_first_row(window)
    started = time.monotonic()
    for thread in threads:
        thread.join(max(0.1, 30.0 - (time.monotonic() - started)))
    return None


def _publish(specs, ref, title, tr):
    """The page's rows (dhs.fold.<n>.*): its catalogs, and with more than
    the page holds, a last row of all of them as cards (skin_folder)."""
    from . import board
    shown = board._publish_tab(TAB, specs)
    if shown <= SKIN_ROWS:
        return shown
    prefix = 'dhs.fold.%d.' % SKIN_ROWS
    C.set_prop(prefix + 'filterable', '')
    C.set_prop(prefix + 'id', CARDS)
    C.set_prop(prefix + 'title', tr('كل الكتالوجات'))
    C.set_prop(prefix + 'sub', tr('%d كتالوج') % len([s for s in specs if s.get('key') != '%s:src:all' % TAB]))
    C.set_prop(prefix + 'shape', 'landscape')
    C.set_prop(prefix + 'key', '%s:%s' % (TAB, CARDS))
    C.set_prop(prefix + 'path', C.url('skin_folder', row='%s:%s' % (TAB, CARDS), t=title,
                                      **dict((k, v) for k, v in (ref or {}).items() if v)))
    for n in range(SKIN_ROWS + 1, C.ROWS + 1):
        prefix = 'dhs.fold.%d.' % n
        if not C.prop(prefix + 'path') and not C.prop(prefix + 'id'):
            break
        for field in ('id', 'title', 'sub', 'shape', 'key', 'path'):
            C.set_prop(prefix + field, '')
    C.set_prop('dhs.fold.count', SKIN_ROWS)
    return SKIN_ROWS


def _focus_first_row(window=WINDOW, timeout=10.0):
    """The first row has the focus once it has titles (the page opens with
    an invisible button holding it: a list with no items takes none)."""
    monitor = xbmc.Monitor()
    started = time.monotonic()
    cond = xbmc.getCondVisibility
    first = FIRST_ROW
    while time.monotonic() - started < timeout and not monitor.abortRequested():
        if cond('Window.IsActive(%d)' % window):
            if cond('Integer.IsGreater(Container(%d).NumItems,0)' % first):
                if cond('Control.HasFocus(%d)' % FOCUS_HOLDER):
                    xbmc.executebuiltin('SetFocus(%d)' % first)
                return
        elif time.monotonic() - started > 3.0:
            return          # the page was left (or never opened)
        if monitor.waitForAbort(0.08):
            return


def _prefetch(specs):
    """The first rows, read side by side (the page's calls for them wait for
    these reads instead of reading again). Returns the reading threads."""
    from . import rows
    todo = [spec for spec in specs
            if not C.fresh_copy(C.read_json(C.row_file(TAB, spec['id'])), rows.ttl_of(spec))]
    started = time.monotonic()

    def read(spec):
        try:
            rows.fetch(TAB, spec['id'], spec, wait_other=False)
        except Exception as exc:
            C.log('folder row "%s": %s' % (spec.get('title'), exc), xbmc.LOGWARNING)
        C.log('folder row "%s" read ahead in %.0f ms' % (spec.get('title'), (time.monotonic() - started) * 1000),
              xbmc.LOGDEBUG)
    threads = [threading.Thread(target=read, args=(spec,), name='dexhub-fold-row') for spec in todo]
    for thread in threads:
        thread.daemon = True
        thread.start()
    return threads


def republish():
    """A row of the page came back empty: the page's rows again, without it."""
    data = C.read_json(C.spec_file(TAB), {}) or {}
    try:
        from . import rows
        tr = rows.app().tr
    except Exception:
        tr = (lambda text: text)
    _publish(list(data.get('rows') or []), data.get('ref') or {}, data.get('title') or '', tr)
