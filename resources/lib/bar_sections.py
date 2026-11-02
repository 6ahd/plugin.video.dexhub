# -*- coding: utf-8 -*-
"""The Home bar's sections (v5.10.479): which show, and sections of your own.

Every section of skin.dexhub's bar can be hidden (Home always shows); the
skin reads Window(Home) dhs.bar.off.<key>. Up to three sections of your own
(tabs c1, c2, c3) take a name and the catalogs you pick from the Home's own
rows (Home, Movies, Series); they show on the bar after Servers and their
rows on the Home like Movies' (skinui.rows.compute_rows).

Saved per profile in homeui/bar_sections.json:
  {"hidden": ["discover", ...], "custom": [{"name": "...", "rows": [spec, ...]}, ...]}
"""
import json
import os
import uuid

KEYS = (('movie', 'الأفلام', 'Movies'), ('series', 'المسلسلات', 'Series'), ('servers', 'السيرفرات', 'Servers'),
        ('discover', 'Discover', 'Discover'), ('live', 'Live TV', 'Live TV'), ('library', 'مكتبتي', 'My Library'))
CUSTOM = ('c1', 'c2', 'c3')
# the bar's icons a section of your own can take (skin.dexhub draws dexhub/nav/<icon>.png
# and, focused, dexhub/nav/f_<icon>.png)
ICONS = (('', 'بدون (الاسم فقط)', 'None (the name only)'), ('movie', 'أفلام', 'Films'), ('series', 'مسلسلات', 'Series'),
         ('discover', 'اكتشاف', 'Discover'), ('live', 'بث مباشر', 'Live'), ('library', 'مكتبة', 'Library'),
         ('sport', 'رياضة', 'Sport'), ('servers', 'سيرفرات', 'Servers'), ('calendar', 'تقويم', 'Calendar'),
         ('home', 'بيت', 'Home'), ('addons', 'إضافات', 'Add-ons'))
ICON_PATH = 'special://home/addons/skin.dexhub/media/dexhub/nav/%s.png'
SOURCE_TABS = (('all', 'الرئيسية', 'Home'), ('movie', 'الأفلام', 'Movies'), ('series', 'المسلسلات', 'Series'))


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _path():
    from . import profile_scope
    return profile_scope.path('homeui/bar_sections.json')


def read():
    try:
        with open(_path(), encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data['hidden'] = [k for k in data.get('hidden') or [] if k in dict((k, 1) for k, _a, _e in KEYS)]
    data['custom'] = [c for c in data.get('custom') or [] if isinstance(c, dict) and c.get('name')][:len(CUSTOM)]
    return data


def write(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%s.tmp' % (path, uuid.uuid4().hex)
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, path)
    publish(data)


def publish(data=None):
    """The bar as the skin reads it."""
    import xbmcgui
    data = data if data is not None else read()
    home = xbmcgui.Window(10000)
    hidden = set(data.get('hidden') or [])
    for key, _ar, _en in KEYS:
        value = '1' if key in hidden else ''
        if home.getProperty('dhs.bar.off.' + key) != value:
            home.setProperty('dhs.bar.off.' + key, value)
    custom = data.get('custom') or []
    for n, tab in enumerate(CUSTOM):
        name = (custom[n].get('name') or '') if n < len(custom) else ''
        home.setProperty('dhs.custom.%d.name' % (n + 1), name)
        home.setProperty('dhs.custom.%d.initial' % (n + 1), name[:1].upper() if name else '')
        icon = (custom[n].get('icon') or '') if n < len(custom) else ''
        home.setProperty('dhs.custom.%d.icon' % (n + 1), icon if icon in dict((k, 1) for k, _a, _e in ICONS) else '')
    # a tab hidden or gone while the Home shows it: back to Home
    tab = home.getProperty('dhs.tab')
    gone = tab in hidden or (tab in CUSTOM and CUSTOM.index(tab) >= len(custom))
    if gone:
        home.setProperty('dhs.tab', 'all')


def rows(tab):
    """A section of your own as homeui Row objects (skinui.rows.compute_rows)."""
    from .homeui import rows as R
    if tab not in CUSTOM:
        return []
    custom = read().get('custom') or []
    n = CUSTOM.index(tab)
    if n >= len(custom):
        return []
    out = []
    for spec in custom[n].get('rows') or []:
        params = dict(spec.get('params') or {})
        if not params.get('action'):
            continue
        out.append(R.Row('bar:%s' % spec.get('key'), spec.get('title') or '', shape=spec.get('shape') or 'poster',
                         kind='catalog', subtitle=spec.get('sub') or '', meta={'params': params, 'auto_shape': True}))
    return out


def candidates():
    """[(spec, where)] every catalog row of the Home, Movies and Series (no repeats)."""
    from .skinui import board
    seen, out = set(), []
    for tab, ar, en in SOURCE_TABS:
        for spec in board.load_specs(tab):
            params = spec.get('params') or {}
            if spec.get('own') or spec.get('progress') or not params.get('action') or (spec.get('kind') or 'catalog') != 'catalog':
                continue
            ident = json.dumps(params, sort_keys=True)
            if ident in seen:
                continue
            seen.add(ident)
            out.append(({'key': spec.get('key') or spec.get('id'), 'title': spec.get('title') or '', 'sub': spec.get('sub') or '',
                         'params': params, 'shape': spec.get('shape') or 'poster'}, _t(ar, en)))
    return out


# ------------------------------------------------------------ the page
def open_page():
    import xbmcgui
    from .routes.simple_entry import _select_dialog, _row
    on, off = _t('ظاهر', 'Shown'), _t('مخفي', 'Hidden')
    cursor = 0
    while True:
        data = read()
        hidden = set(data['hidden'])
        custom = data['custom']
        labels = [_row(_t('الرئيسية', 'Home'), _t('دايم ظاهرة', 'Always shown'))]
        for key, ar, en in KEYS:
            labels.append(_row(_t(ar, en), off if key in hidden else on))
        for entry in custom:
            labels.append(_row(_t('قسمك: %s', 'Your section: %s') % entry['name'],
                               _t('%d كتالوج', '%d catalogs') % len(entry.get('rows') or [])))
        if len(custom) < len(CUSTOM):
            labels.append(_row(_t('+ إضافة قسم', '+ Add a section'), _t('اسم وكتالوجات تختارها', 'A name and the catalogs you pick')))
        pick = _select_dialog(_t('أقسام الشريط', 'Bar sections'), labels, preselect=cursor)
        if pick < 0:
            return True
        cursor = pick
        if pick == 0:
            continue
        if pick <= len(KEYS):
            key = KEYS[pick - 1][0]
            if key in hidden:
                hidden.discard(key)
            else:
                hidden.add(key)
            data['hidden'] = sorted(hidden)
            write(data)
            continue
        index = pick - 1 - len(KEYS)
        if index < len(custom):
            _edit(data, index)
        else:
            name = xbmcgui.Dialog().input(_t('اسم القسم', 'Section name'))
            name = (name or '').strip()[:24]
            if not name:
                continue
            icon = _pick_icon('')
            rows_ = _pick_rows([])
            if rows_ is None:
                continue
            data['custom'].append({'name': name, 'icon': icon or '', 'rows': rows_})
            write(data)
            _republish(len(data['custom']) - 1)


def _edit(data, index):
    import xbmcgui
    from .routes.simple_entry import _select_dialog, _row
    entry = data['custom'][index]
    pick = _select_dialog(entry['name'], [
        _row(_t('الكتالوجات', 'Catalogs'), _t('%d مختار', '%d chosen') % len(entry.get('rows') or [])),
        _row(_t('تغيير الاسم', 'Rename'), entry['name']),
        _row(_t('الأيقونة', 'Icon'), _icon_name(entry.get('icon') or ''), ICON_PATH % entry['icon'] if entry.get('icon') else ''),
        _row(_t('حذف القسم', 'Delete the section'))])
    if pick == 0:
        rows_ = _pick_rows(entry.get('rows') or [])
        if rows_ is not None:
            entry['rows'] = rows_
            write(data)
            _republish(index)
    elif pick == 1:
        name = (xbmcgui.Dialog().input(_t('اسم القسم', 'Section name'), entry['name']) or '').strip()[:24]
        if name:
            entry['name'] = name
            write(data)
    elif pick == 2:
        icon = _pick_icon(entry.get('icon') or '')
        if icon is not None:
            entry['icon'] = icon
            write(data)
    elif pick == 3 and xbmcgui.Dialog().yesno(entry['name'], _t('تحذف هالقسم من الشريط؟', 'Remove this section from the bar?')):
        data['custom'].pop(index)
        write(data)
        _republish(None)


def _icon_name(icon):
    for key, ar, en in ICONS:
        if key == icon:
            return _t(ar, en)
    return ''


def _pick_icon(current):
    """The section's icon on the bar: '' for none, None when cancelled."""
    from .routes.simple_entry import _select_dialog, _row
    keys = [k for k, _a, _e in ICONS]
    pick = _select_dialog(_t('أيقونة القسم', 'Section icon'),
                          [_row(_t(ar, en), '', ICON_PATH % key if key else '') for key, ar, en in ICONS],
                          preselect=keys.index(current) if current in keys else 0)
    return None if pick < 0 else keys[pick]


def _pick_rows(chosen):
    """The catalogs of a section: rows of the Home picked in a list, and
    folders of any Kodi video add-on; None when cancelled."""
    import xbmcgui
    from .routes.simple_entry import _select_dialog, _row
    rows_ = list(chosen)
    while True:
        labels = [_row(_t('✓ تم', '✓ Done'), _t('%d كتالوج', '%d catalogs') % len(rows_)),
                  _row(_t('+ من صفوف Dex Hub', '+ From Dex Hub rows'), _t('الرئيسية والأفلام والمسلسلات', 'Home, Movies and Series')),
                  _row(_t('+ من أي إضافة في كودي', '+ From any Kodi add-on'), _t('تختار الإضافة ثم المجلد', 'Pick the add-on, then the folder'))]
        for spec in rows_:
            labels.append(_row(spec.get('title') or '', _t('اضغط للحذف', 'Press to remove')))
        pick = _select_dialog(_t('كتالوجات القسم', 'The section\'s catalogs'), labels)
        if pick < 0:
            return None
        if pick == 0:
            return rows_
        if pick == 1:
            got = _pick_dexhub_rows(rows_)
            if got is not None:
                rows_ = got
        elif pick == 2:
            spec = _pick_addon_folder()
            if spec:
                rows_.append(spec)
        else:
            rows_.pop(pick - 3)


def _pick_dexhub_rows(chosen):
    import xbmcgui
    found = candidates()
    if not found:
        xbmcgui.Dialog().ok(_t('أقسام الشريط', 'Bar sections'),
                            _t('ما فيه كتالوجات للحين. افتح الرئيسية مرة وارجع.', 'No catalogs yet. Open the Home once and come back.'))
        return None
    idents = [json.dumps(spec['params'], sort_keys=True) for spec, _where in found]
    before = [json.dumps(r.get('params') or {}, sort_keys=True) for r in chosen]
    labels = ['%s  ·  %s' % (spec['title'], where) for spec, where in found]
    pre = [i for i, ident in enumerate(idents) if ident in before]
    picked = xbmcgui.Dialog().multiselect(_t('اختر كتالوجات القسم', 'Pick the catalogs of the section'), labels, preselect=pre)
    if picked is None:
        return None
    others = [r for r in chosen if json.dumps(r.get('params') or {}, sort_keys=True) not in idents]
    kept = [r for r in chosen if json.dumps(r.get('params') or {}, sort_keys=True) in [idents[i] for i in picked]]
    new = [found[i][0] for i in picked if idents[i] not in before]
    return others + kept + new


def _rpc(method, params):
    import xbmc
    try:
        answer = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
    except Exception:
        return {}
    return answer.get('result') or {}


def _pick_addon_folder():
    """A folder of any Kodi video add-on as a row (Dex Hub reads it with kodi_route_browse)."""
    import hashlib
    import xbmcgui
    from .routes.simple_entry import _select_dialog, _row
    addons = _rpc('Addons.GetAddons', {'type': 'xbmc.addon.video', 'enabled': True, 'properties': ['name', 'thumbnail']}).get('addons') or []
    addons = sorted((a for a in addons if a.get('addonid') and not str(a['addonid']).startswith('plugin.video.dexhub')),
                    key=lambda a: (a.get('name') or '').lower())
    if not addons:
        xbmcgui.Dialog().ok(_t('أقسام الشريط', 'Bar sections'), _t('ما فيه إضافات فيديو مثبتة', 'No video add-ons installed'))
        return None
    pick = _select_dialog(_t('اختر الإضافة', 'Pick the add-on'),
                          [_row(a.get('name') or a['addonid'], a['addonid'], a.get('thumbnail') or '') for a in addons])
    if pick < 0:
        return None
    addon = addons[pick]
    path, trail = 'plugin://%s/' % addon['addonid'], [addon.get('name') or addon['addonid']]
    paths = [path]
    while True:
        busy = xbmcgui.DialogProgress()
        busy.create(trail[-1], _t('يقرأ المجلد…', 'Reading the folder…'))
        try:
            files = _rpc('Files.GetDirectory', {'directory': path, 'media': 'video', 'properties': ['title', 'thumbnail']}).get('files') or []
        finally:
            busy.close()
        folders = [f for f in files if f.get('filetype') == 'directory' and f.get('file')]
        playable = len(files) - len(folders)
        labels = [_row(_t('✓ استخدم هالمجلد', '✓ Use this folder'),
                       ' / '.join(trail) + ((_t('  ·  %d عنصر', '  ·  %d items') % playable) if playable else ''))]
        labels += [_row(f.get('label') or f.get('title') or '', '', f.get('thumbnail') or '') for f in folders]
        pick = _select_dialog(trail[-1], labels)
        if pick < 0:
            # Back: one folder up, out of the add-on at its top
            if len(paths) <= 1:
                return None
            paths.pop()
            trail.pop()
            path = paths[-1]
            continue
        if pick == 0:
            break
        chosen = folders[pick - 1]
        path = chosen['file']
        paths.append(path)
        trail.append(chosen.get('label') or '')
    title = (xbmcgui.Dialog().input(_t('اسم الصف', 'Row name'), trail[-1]) or '').strip() or trail[-1]
    key = 'kodi:' + hashlib.sha1(path.encode('utf-8')).hexdigest()[:12]
    return {'key': key, 'title': title, 'sub': trail[0], 'shape': 'poster',
            'params': {'action': 'kodi_route_browse', 'route': path, 'title': title}}


def _republish(index):
    try:
        from .skinui import common as C
        C.ask_republish('bar sections')
    except Exception:
        pass
