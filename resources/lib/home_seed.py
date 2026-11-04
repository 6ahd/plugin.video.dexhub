# -*- coding: utf-8 -*-
"""DexWorld's rows on the Home for everyone who installs Dex Hub (v5.10.509).

Once per profile, a little after Kodi starts: TV networks and streamers on
the Home; networks, the streamers' top 10, reality and anime on TV Shows;
studios, genres and documentaries on Movies. Arabic (the device's language,
or an Arab country) adds the Arabic collection, Gulf, Levantine, Turkish and
Egyptian series and Arabic films. They go right after the Continue rows.

A Home the user arranged (rows added, hidden or moved) is not touched: the
user is asked once instead. Every row is an ordinary added row: the Home's
editor hides, moves or removes it.
"""
import time

import xbmc

from . import profile_scope

STAMP = 'homeui/seed_dexworld.json'
VERSION = 1
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


def _arranged(app):
    """True when the user arranged the Home (rows added, hidden or moved)."""
    for media in ('all', 'movie', 'series'):
        page = app.layout.page(media)
        if page.get('custom') or page.get('hidden') or page.get('order'):
            return True
    return False


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


def run(monitor):
    """Once per profile, a minute after Kodi starts (the Home's rows and DexWorld's page are in by then)."""
    if monitor.waitForAbort(60):
        return
    path = profile_scope.path(STAMP)
    if (profile_scope._read(path, {}) or {}).get('v', 0) >= VERSION:
        return
    try:
        from . import dexworld as DW
        if not DW.page():
            _log('DexWorld did not answer: next start')
            return
        from .skinui import rows as R
        app = R.app()
        wanted = plan()
        if _arranged(app):
            import xbmcgui
            from .dexworld import _t
            profile_scope._write(path, {'v': VERSION, 'at': time.time(), 'asked': True})
            if not xbmcgui.Dialog().yesno('DexWorld', _t(
                    'فيه صفوف جديدة من DexWorld للرئيسية والأفلام والمسلسلات: شبكات التلفزيون والمنصات والاستوديوهات والوثائقيات وغيرها. تضيفها لواجهتك؟',
                    'New DexWorld rows for Home, Movies and TV Shows: TV networks, streamers, studios, documentaries and more. Add them to your Home screen?'),
                    nolabel=_t('لا', 'No'), yeslabel=_t('أضفها', 'Add them')):
                return
        else:
            profile_scope._write(path, {'v': VERSION, 'at': time.time()})
        added = add(app, wanted)
        _log('%d rows added (%s)' % (added, ', '.join('%s %d' % (k, len(v)) for k, v in wanted.items())))
        if added:
            from .homeui.bar_editor import _home_changed
            _home_changed()
    except Exception as exc:
        _log('failed: %s' % exc)
