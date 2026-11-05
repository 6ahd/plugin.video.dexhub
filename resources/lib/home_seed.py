# -*- coding: utf-8 -*-
"""DexWorld's rows on the Home, Movies and TV Shows (v5.10.509).

TV networks and streamers on the Home; networks, the streamers' top 10,
reality and anime on TV Shows; studios, genres and documentaries on Movies.
Arabic (the device's language, or an Arab country) adds the Arabic
collection, Gulf, Levantine, Turkish and Egyptian series and Arabic films.
They go right after the Continue rows.

v5.10.521: none by default. The user puts them in, or takes them out, page
by page in Control centre > Home & rows > DexWorld rows (choose). 5.10.509
added them by itself after a start to every Home the user had not arranged;
those are taken out once (run). Every row is an ordinary added row: the
Home's editor hides, moves or removes it.
"""
import time

import xbmc

from . import profile_scope

STAMP = 'homeui/seed_dexworld.json'
VERSION = 3              # 3 (5.10.521): none by default; 5.10.509's own taken out
WORLD = {'all': ('k:networks', 'k:platforms'),
         'series': ('k:networks', 'k:top10', 'k:reality', 'b:dexworld-anime'),
         'movie': ('k:studios', 'k:genres', 'k:documentaries')}
ARABIC = {'all': ('k:arabic',),
          'series': ('b:dexworld-gulf-series', 'b:dexworld-levant-series', 'b:dexworld-turkish-series',
                     'b:dexworld-egyptian-series'),
          'movie': ('b:dexworld-arabic-movies',)}
ARAB = ('SA', 'AE', 'KW', 'QA', 'BH', 'OM', 'YE', 'IQ', 'SY', 'JO', 'LB', 'PS', 'EG', 'SD', 'LY', 'TN', 'DZ', 'MA', 'MR')


def _log(text):
    xbmc.log('[DexHub] home seed: %s' % text, xbmc.LOGINFO)


def _arabic():
    from . import dexworld as DW
    if DW.lang() == 'ar':
        return True
    try:
        from . import device_region as R
        return R.country() in ARAB
    except Exception:
        return False


def plan():
    """{page: [DexWorld row ids]} for this device."""
    out = dict((page, list(ids)) for page, ids in WORLD.items())
    if _arabic():
        for page, ids in ARABIC.items():
            out[page] = list(ids) + out[page] if page == 'all' else out[page] + list(ids)
    return out



def _specs(ids):
    from . import dexworld as DW
    by_id = DW.rows_by_id(DW.page())
    return [DW.spec_of(by_id[i]) for i in ids if i in by_id]


def add(app, wanted):
    """The rows into each page, after its Continue rows; returns how many were added."""
    from .skinui import rows as R
    from .homeui import layout as L
    added = 0
    for media, ids in wanted.items():
        specs = _specs(ids)
        if not specs:
            continue
        page = app.layout.page(media)
        have = set(str((s.get('params') or {}).get('id')) for s in page.get('custom') or [])
        fresh = []
        for spec in specs:
            if str(spec['params'].get('id')) in have:
                continue
            row = L.new_custom({'type': 'route', 'label': spec['title'], 'source': 'catalog', 'source_label': 'DexWorld',
                                'subtitle': 'DexWorld', 'params': dict(spec['params']), 'shape': ''})
            fresh.append(row)
        if not fresh:
            continue
        try:
            arranged = [row for row, _hidden in app.layout.arrange(R.HeadlessHome(app, media).layout_rows(media), media)]
        except Exception:
            arranged = []
        keys = [row.key for row in arranged]
        lead = 0
        while lead < len(arranged) and L.is_progress_row(arranged[lead]):
            lead += 1
        order = keys[:lead] + [L.custom_key(r) for r in fresh] + keys[lead:]
        app.layout.save_page(media, order=order, custom=list(page.get('custom') or []) + fresh)
        added += len(fresh)
    return added


PAGES = (('all', 'الرئيسية', 'Home'), ('movie', 'الأفلام', 'Movies'), ('series', 'المسلسلات', 'TV Shows'))


def _ours(page):
    """The page's rows add() put in (DexWorld's, by their ids)."""
    ids = set(i for group in (WORLD, ARABIC) for page_ids in group.values() for i in page_ids)
    return [row for row in page.get('custom') or []
            if row.get('source_label') == 'DexWorld' and str((row.get('params') or {}).get('id')) in ids]


def remove(app, pages=None):
    """v5.10.519: the rows add() put in out of ``pages`` (None: every page); returns how many."""
    from .homeui import layout as L
    removed = 0
    for media in [p for p, _ar, _en in PAGES] if pages is None else pages:
        page = app.layout.page(media)
        gone = _ours(page)
        if not gone:
            continue
        keys = set(L.custom_key(row) for row in gone)
        app.layout.save_page(media, order=[k for k in page.get('order') or [] if k not in keys],
                             custom=[row for row in page.get('custom') or [] if row not in gone])
        removed += len(gone)
    return removed


def choose():
    """v5.10.521: Control centre > Home & rows > DexWorld rows: the pages
    that have them, ticked; untick a page to take its rows out (all of them:
    untick all), tick one to put them in. The question at start is not
    asked any more after this."""
    import xbmcgui
    from .dexworld import _t
    from .skinui import rows as R
    app = R.app()
    have = dict((media, len(_ours(app.layout.page(media)))) for media, _ar, _en in PAGES)
    labels = [_t('%s (%d صف)', '%s (%d rows)') % (_t(ar, en), have[media]) if have[media] else _t(ar, en)
              for media, ar, en in PAGES]
    picked = xbmcgui.Dialog().multiselect(_t('صفوف DexWorld: شبكات ومنصات واستوديوهات', 'DexWorld rows: networks, streamers, studios'),
                                          labels, preselect=[i for i, (media, _a, _e) in enumerate(PAGES) if have[media]])
    if picked is None:
        return
    want = set(PAGES[i][0] for i in picked)
    path = profile_scope.path(STAMP)
    profile_scope._write(path, dict(profile_scope._read(path, {}) or {}, v=VERSION, asked=True, chosen=time.time()))
    removed = remove(app, [media for media, _a, _e in PAGES if media not in want and have[media]])
    added = 0
    fresh = dict((media, ids) for media, ids in plan().items() if media in want and not have[media])
    if fresh:
        from . import dexworld as DW
        if DW.page():
            added = add(app, fresh)
        else:
            xbmcgui.Dialog().notification('DexWorld', _t('DexWorld ما رد، جرّب بعد شوي', 'DexWorld did not answer, try again later'),
                                          xbmcgui.NOTIFICATION_WARNING, 4000)
    _log('chosen: %s (%d added, %d removed)' % (', '.join(sorted(want)) or 'none', added, removed))
    if added or removed:
        from .homeui.bar_editor import _home_changed
        _home_changed()
        xbmcgui.Dialog().notification('DexWorld', _t('أضيف %d وانحذف %d صف', '%d rows added, %d removed') % (added, removed),
                                      xbmcgui.NOTIFICATION_INFO, 3000)


def run(monitor):
    """Once per profile, a minute after Kodi starts: the rows 5.10.509 put in
    by itself (stamp v1 without 'asked') go, with a word on where they are
    now. Rows the user said yes to (asked) or chose stay; nothing is added."""
    if monitor.waitForAbort(60):
        return
    path = profile_scope.path(STAMP)
    stamp = profile_scope._read(path, {}) or {}
    if stamp.get('v', 0) >= VERSION:
        return
    profile_scope._write(path, dict(stamp, v=VERSION, defaulted=time.time()))
    if not stamp.get('v') or stamp.get('asked'):
        return
    try:
        from .skinui import rows as R
        removed = remove(R.app())
        _log('%d rows 5.10.509 put in taken out' % removed)
        if removed:
            import xbmcgui
            from .dexworld import _t
            from .homeui.bar_editor import _home_changed
            _home_changed()
            xbmcgui.Dialog().notification('DexWorld', _t('صفوف DexWorld انشالت؛ ترجّعها من مركز التحكم ← الرئيسية والصفوف',
                                                         'DexWorld rows removed; add them back in Control centre > Home & rows'),
                                          xbmcgui.NOTIFICATION_INFO, 6000)
    except Exception as exc:
        _log('failed: %s' % exc)
