# -*- coding: utf-8 -*-
"""Add to the Home screen (v5.10.501): any row (a DexWorld list, a YouTube row,
a channel) into Home, Movies, TV Shows, one of the user's sections or a new
one, from the menu of the page it is on.

A row here is a bar section's catalog spec ({'key', 'title', 'sub', 'shape',
'style', 'params': {'action': ...}}). In an own section it is kept as it is
(bar_sections.json); in Home, Movies or TV Shows it becomes one of the page's
added rows (layout.json, type 'route'), the same as the Home's editor adds.
"""
import xbmc
import xbmcgui

from . import bar_sections as B

_t = B._t
PAGES = (('all', 'الرئيسية', 'Home'), ('movie', 'الأفلام', 'Movies'), ('series', 'المسلسلات', 'TV Shows'))


def _to_page(specs, media):
    from .skinui import rows as R
    from .homeui import layout as L
    app = R.app()
    page = app.layout.page(media)
    custom = list(page.get('custom') or [])
    have = set(str((s.get('params') or {})) for s in custom)
    order = list(page.get('order') or [])
    added = 0
    for spec in specs:
        if str(spec.get('params') or {}) in have:
            continue
        row = L.new_custom({'type': 'route', 'label': spec.get('title') or '', 'source': 'catalog',
                            'source_label': spec.get('sub') or '', 'subtitle': spec.get('sub') or '',
                            'params': dict(spec.get('params') or {}), 'shape': ''})
        custom.append(row)
        # first on the page, where it is seen
        order.insert(0, L.custom_key(row))
        if spec.get('style'):
            app.layout.set_shape(L.custom_key(row), spec['style'])
        added += 1
    app.layout.save_page(media, order=order if page.get('order') else None, custom=custom)
    from .homeui.bar_editor import _home_changed
    _home_changed()
    return added


def _to_section(specs, slot=None, name='', icon=''):
    data = B.read()
    custom = data.get('custom') or []
    if slot is None:
        if len(custom) >= len(B.CUSTOM):
            return ''
        custom.append({'name': name, 'icon': icon, 'rows': []})
        slot = len(custom) - 1
    section = custom[slot]
    have = set(spec.get('key') for spec in section.get('rows') or [])
    for spec in specs:
        if spec.get('key') not in have:
            section.setdefault('rows', []).append(dict(spec))
            have.add(spec.get('key'))
    section['rows'] = (section.get('rows') or [])[:B.MAX_ROWS]
    data['custom'] = custom
    B.write(data)
    from .homeui.bar_editor import _home_changed
    _home_changed()
    return section.get('name') or ''


def add(specs, name_hint='', icon=''):
    """Asks where, then adds. True when something was added."""
    specs = [s for s in specs or [] if (s.get('params') or {}).get('action')]
    if not specs:
        return False
    custom = B.read().get('custom') or []
    options = [(('page', media), _t('الرئيسية', 'Home') if media == 'all' else _t(ar, en)) for media, ar, en in PAGES]
    options += [(('section', n), c.get('name') or '') for n, c in enumerate(custom)]
    if len(custom) < len(B.CUSTOM):
        options.append((('new', None), _t('قسم جديد في الشريط…', 'A new section on the bar…')))
    title = _t('أضف للواجهة', 'Add to the Home screen') if len(specs) == 1 else \
        _t('أضف %d صف للواجهة', 'Add %d rows to the Home screen') % len(specs)
    pick = xbmcgui.Dialog().select(title, [label for _w, label in options])
    if pick < 0:
        return False
    (where, which), label = options[pick]
    if where == 'page':
        done = _to_page(specs, which) and label
    elif where == 'section':
        done = _to_section(specs, slot=which)
    else:
        name = (xbmcgui.Dialog().input(_t('اسم القسم', 'Section name'), name_hint or '') or '').strip()[:24]
        if not name:
            return False
        done = _to_section(specs, name=name, icon=icon)
    if done:
        xbmcgui.Dialog().notification('Dex Hub', _t('انضاف إلى «%s»', 'Added to "%s"') % done, time=2500, sound=False)
    return bool(done)
