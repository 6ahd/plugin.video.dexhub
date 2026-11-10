# -*- coding: utf-8 -*-
"""The Home bar's sections (v5.10.479): which show, and sections of your own.

Every section of skin.dexhub's bar can be hidden (Home always shows); the
skin reads Window(Home) dhs.bar.off.<key>. Up to three sections of your own
(tabs c1, c2, c3) take a name and the catalogs you pick from the Home's own
rows (Home, Movies, Series); they show on the bar after Servers and their
rows on the Home like Movies' (skinui.rows.compute_rows).

v5.10.481, the editor as Arctic Fuse 3's: the icon is any picture (the bar's,
Dex Hub's, Arctic Fuse 3's 2000 icons searched by name, a picture of yours),
each catalog has a style from the Home's gallery (spec['style'], '' as its
titles suit), moves up and down, and an add-on's folders are added several or
all at once (each one a catalog).

Saved per profile in homeui/bar_sections.json:
  {"hidden": ["discover", ...], "custom": [{"name": "...", "icon": "...", "rows": [spec, ...]}, ...]}
"""
import json
import os
import uuid

KEYS = (('movie', 'الأفلام', 'Movies'), ('series', 'المسلسلات', 'Series'), ('servers', 'السيرفرات', 'Servers'),
        ('discover', 'Discover', 'Discover'), ('live', 'Live TV', 'Live TV'), ('library', 'مكتبتي', 'My Library'),
        ('dexworld', 'DexWorld', 'DexWorld'), ('youtube', 'YouTube', 'YouTube'),   # v5.10.496, .497
        ('addons', 'الإضافات', 'Add-ons'))   # v5.10.551: back on the bar (it was in no menu)
CUSTOM = ('c1', 'c2', 'c3')
# the bar's own icons a section can take (skin.dexhub's dexhub/nav/<name>.png);
# v5.10.481: any picture too (Dex Hub's menu icons, Arctic Fuse 3's icons when
# that skin is installed, a picture of yours): dhs.custom.<n>.icon is its path
ICONS = (('movie', 'أفلام', 'Films'), ('series', 'مسلسلات', 'Series'),
         ('discover', 'اكتشاف', 'Discover'), ('live', 'بث مباشر', 'Live'), ('library', 'مكتبة', 'Library'),
         ('sport', 'رياضة', 'Sport'), ('servers', 'سيرفرات', 'Servers'), ('calendar', 'تقويم', 'Calendar'),
         ('home', 'بيت', 'Home'), ('addons', 'إضافات', 'Add-ons'))
LIST_LIMIT = 80
MAX_ROWS = 20         # the Home's row slots (skinui.common.ROWS)
# v5.10.481: how a catalog's titles are ordered (spec['sort']; '' as the source gives them)
SORTS = (('', 'كما في المصدر', 'As the source'), ('new', 'الأحدث', 'Newest'), ('old', 'الأقدم', 'Oldest'),
         ('rating', 'الأعلى تقييماً', 'Top rated'), ('az', 'أ - ي', 'A–Z'), ('shuffle', 'عشوائي', 'Shuffle'))
AF3_ICONS = 'special://home/addons/skin.arctic.fuse.3/extras/icons/'
MENU_ICONS = 'special://home/addons/plugin.video.dexhub/resources/media/menu/'
ICON_PATH = 'special://home/addons/skin.dexhub/media/dexhub/nav/%s.png'
NAV = 'dexhub/nav/%s.png'
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
        home.setProperty('dhs.custom.%d.icon' % (n + 1), icon_texture(icon))
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
        # v5.10.481: a style picked for the row (like Arctic Fuse 3's widget
        # styles) wins; without one the row takes the shape its titles suit
        style = spec.get('style') or ''
        # v5.10.481: the sort is part of the row's key, so a new sort reads the row anew
        sort = spec.get('sort') or ''
        key = 'bar:%s' % spec.get('key') + (':' + sort if sort else '')
        if params.get('action') == 'dexworld_row':
            # v5.10.505: a DexWorld collection: its lists as covers (a collection row)
            from . import dexworld as DW
            tiles = DW.kit_tiles(params.get('id') or '')
            if tiles:
                out.append(R.Row(key, spec.get('title') or '', shape=style or 'landscape', kind='collection',
                                 tiles=tiles, meta={}))
                continue
        out.append(R.Row(key, spec.get('title') or '', shape=style or spec.get('shape') or 'poster',
                         kind='catalog', subtitle=spec.get('sub') or '', meta={'params': params, 'auto_shape': not style, 'sort': sort}))
    return out


def sort_name(sort):
    for key, ar, en in SORTS:
        if key == (sort or ''):
            return _t(ar, en)
    return ''


def sort_tiles(tiles, sort):
    """A catalog's titles in the order picked for it (skinui.rows reads them)."""
    import random
    import re

    def year(tile):
        m = re.match(r'(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?', str(tile.get('aired') or ''))
        if m:
            return tuple(int(x or 0) for x in m.groups())
        m = re.match(r'\d{4}', str(tile.get('year') or ''))
        return (int(m.group(0)), 0, 0) if m else None

    tiles = list(tiles or [])
    if sort in ('new', 'old'):
        dated = [t for t in tiles if year(t)]
        rest = [t for t in tiles if not year(t)]
        return sorted(dated, key=year, reverse=sort == 'new') + rest
    if sort == 'rating':
        return sorted(tiles, key=lambda t: -float(t.get('rating') or 0))
    if sort == 'az':
        return sorted(tiles, key=lambda t: str(t.get('title') or t.get('label') or '').lower())
    if sort == 'shuffle':
        random.shuffle(tiles)
    return tiles


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
    """The page (v5.10.481: one window, homeui/bar_editor.py)."""
    from .homeui import bar_editor
    return bar_editor.open_editor()


def icon_texture(icon):
    """The texture the skin draws: a bar icon by its name, or a picture's path."""
    icon = str(icon or '')
    if icon in dict((k, 1) for k, _a, _e in ICONS):
        return NAV % icon
    return icon


def _icon_preview(icon):
    icon = str(icon or '')
    return ICON_PATH % icon if icon in dict((k, 1) for k, _a, _e in ICONS) else icon


def _files(folder):
    import xbmcvfs
    try:
        _dirs, files = xbmcvfs.listdir(folder)
    except Exception:
        return []
    return sorted(f for f in files if f.lower().endswith('.png'))


def _styles():
    """The row styles a catalog of a section takes (the collection walls are the Home's own)."""
    from .homeui.layout import PROGRESS_STYLES
    return PROGRESS_STYLES


def _rpc(method, params):
    import xbmc
    try:
        answer = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
    except Exception:
        return {}
    return answer.get('result') or {}


def _folder_spec(path, title, addon_name):
    import hashlib
    key = 'kodi:' + hashlib.sha1(path.encode('utf-8')).hexdigest()[:12]
    return {'key': key, 'title': title, 'sub': addon_name, 'shape': 'poster', 'style': '',
            'params': {'action': 'kodi_route_browse', 'route': path, 'title': title}}


def _republish(index):
    try:
        from .skinui import common as C
        C.ask_republish('bar sections')
    except Exception:
        pass
