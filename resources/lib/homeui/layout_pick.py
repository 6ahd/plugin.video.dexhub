# -*- coding: utf-8 -*-
"""Pickers for "Add a row" in the Home layout editor (v5.10.98).

A new row is one of:

  * a catalog of an installed source (Stremio add-ons, Nuvio account add-ons);
  * a collection (Nuvio collections and the saved collection sets);
  * a view of a Plex, Emby, Jellyfin or Silo library, found by walking the
    service's own Dex Hub menus (the same listings the classic pages show),
    so every view those menus offer can become a row.

Each picker returns a row spec for homeui.layout (without an id) or None.
Listings are read with the capture layer, exactly like the Home rows.
"""
import re

import xbmc
import xbmcgui

from . import rows as R

_TYPE_LABELS = {'movie': 'أفلام', 'series': 'مسلسلات'}
_LIBRARY_ROOTS = (
    ('plex', 'Plex', 'plex_menu', 'plex_client'),
    ('emby', 'Emby', 'emby_menu', 'emby_client'),
    ('jellyfin', 'Jellyfin', 'jellyfin_menu', 'jellyfin_client'),
    ('silo', 'Silo', 'silo_menu', 'silo_client'),
)
# menu entries that ask for input or change settings are never walked into
_SKIP_ACTIONS = re.compile(
    r'(search|login|logout|refresh|_sort$|sort_|settings|setup|toggle|pair|qr|install|'
    r'clear|remove|delete|profile|_add|edit|filter|play$|_play)', re.I)
_SORTS = {
    'plex_library': [('آخر إضافة', 'addedAt:desc'), ('آخر إصدار', 'originallyAvailableAt:desc'),
                     ('التقييم', 'rating:desc'), ('الاسم', 'titleSort'), ('السنة', 'year:desc')],
    'emby_library': [('آخر إضافة', 'DateCreated:desc'), ('آخر إصدار', 'PremiereDate:desc'),
                     ('التقييم', 'CommunityRating:desc'), ('الاسم', 'SortName'),
                     ('السنة', 'ProductionYear:desc')],
}
_TAGS = re.compile(r'\[/?(?:COLOR|B|I|UPPERCASE|LOWERCASE|CAPITALIZE|LIGHT)[^\]]*\]', re.I)


def clean_label(text):
    return ' '.join(_TAGS.sub('', str(text or '')).split())


def _select(app, heading, choices):
    """choices: [(label, label2, icon)] -> index or -1."""
    items = []
    for label, label2, icon in choices:
        li = xbmcgui.ListItem(label=label, label2=label2 or '')
        if icon:
            path = icon if '/' in icon or '\\' in icon else app.media_path(icon)
            li.setArt({'icon': path, 'thumb': path})
        items.append(li)
    try:
        return xbmcgui.Dialog().select(heading, items, useDetails=True)
    except TypeError:
        return xbmcgui.Dialog().select(heading, [c[0] for c in choices])


def _notify(app, text):
    try:
        xbmcgui.Dialog().notification('Dex Hub', app.tr(text), xbmcgui.NOTIFICATION_INFO, 2600)
    except Exception:
        pass


class _Busy(object):
    def __enter__(self):
        xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
        return self

    def __exit__(self, *exc):
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
        return False


# ---------------------------------------------------------------- entry
def pick_row(app, media='all'):
    tr = app.tr
    api = app.api()
    choices = [
        ('catalog', tr('كتالوج من المصادر'), tr('كتالوجات إضافات Stremio وحساب Nuvio'), 'lay_catalog.png'),
        ('collection', tr('مجموعة'), tr('مجموعات Nuvio والمجموعات المحفوظة'), 'lay_collection.png'),
        # v5.10.179: a row of collection cards, one for each catalog the user picks
        ('shelf', tr('كوليكشن من كتالوجاتك'), tr('تختار كتالوجات وكل واحد يصير بطاقة كوليكشن بنفس الصف'),
         'lay_collection.png'),
        # v5.10.612: the favourites and lists of My Library (the user did not find them for the Home)
        ('mine', tr('مفضلتي ومكتبتي'), tr('المفضلة وقوائمك في Trakt وNuvio وSimkl وغيرها'), 'lay_library.png'),
        # v5.10.612: any list of any Dex Hub section (the user: not the libraries alone)
        ('sections', tr('أي قسم من Dex Hub'), tr('صفوف الأفلام والمسلسلات واكتشف والبث، DexWorld، YouTube، Trakt، المجموعات… أي قائمة تصير صف'),
         'lay_catalog.png'),
    ]
    for service, label, _root, client in _LIBRARY_ROOTS:
        if _linked(api, client):
            choices.append((service, label, tr('مكتبة أو قسم من %s') % label, 'lay_library.png'))
    while True:
        index = _select(app, tr('إضافة صف'), [(c[1], c[2], c[3]) for c in choices])
        if index < 0:
            return None
        kind = choices[index][0]
        if kind == 'catalog':
            spec = pick_catalog(app, media)
        elif kind == 'collection':
            spec = pick_collection(app)
        elif kind == 'shelf':
            from .shelf import pick as pick_shelf
            spec = pick_shelf(app, media)
        elif kind == 'mine':
            spec = pick_mine(app)
        elif kind == 'sections':
            spec = pick_section(app)
        else:
            spec = pick_library(app, kind)
        if spec:
            return spec


def pick_mine(app):
    """v5.10.612: one of My Library's lists as a row: My Favourites (films, series), the
    Nuvio / Stremio lists, the Trakt, Simkl and MDBList watchlists, Floppy..."""
    tr = app.tr
    try:
        from ..routes import library
        specs = [s for s in library._specs(library.linked_sources()) if s[0] not in ('continue',)]
    except Exception:
        specs = []
    if not specs:
        _notify(app, 'لا يوجد شيء هنا')
        return None
    index = _select(app, tr('مفضلتي ومكتبتي'), [(title, '', 'lay_library.png') for _key, title, _shape, _params in specs])
    if index < 0:
        return None
    _key, title, shape, params = specs[index]
    return {'type': 'route', 'label': title, 'source': 'library',
            'source_label': '%s  •  %s' % ('Dex Hub', tr('مكتبتي')), 'subtitle': tr('مكتبتي'),
            'params': dict(params), 'shape': '' if shape == 'poster' else shape}


# v5.10.612: the Dex Hub sections a row can come from; the menus are walked like a library
_SECTIONS = (
    ('tabs', 'صفوف أقسام Dex Hub', 'أي صف في الأفلام والمسلسلات واكتشف والبث المباشر ومكتبتي والسيرفرات وأقسامك', None),
    ('dexworld', 'DexWorld', 'كوليكشنات وقوائم DexWorld', None),
    ('youtube', 'YouTube', 'حسابك والبث المباشر والرائج والأقسام وقنواتك', None),
    ('nas', 'NAS وروابط SMB', 'أفلام ومسلسلات وجديد مجلداتك', None),
    ('lists', 'قوائم Trakt وSimkl وMDBList', 'قائمة المشاهدة والمفضلة وقوائمك', None),
    ('movies', 'كتالوجات الأفلام', 'كل كتالوجات الأفلام من مصادرك', {'action': 'nuvio_home_media', 'media': 'movie'}),
    ('series', 'كتالوجات المسلسلات', 'كل كتالوجات المسلسلات من مصادرك', {'action': 'nuvio_home_media', 'media': 'series'}),
    ('ready', 'مجموعات جاهزة', 'مجموعات Dex Hub الجاهزة', {'action': 'ready_collections_menu'}),
    ('sets', 'المجموعات', 'مجموعات Nuvio والمحفوظة', {'action': 'collection_sets'}),
    ('libraries', 'مكتباتي', 'Plex وEmby وJellyfin وSilo', {'action': 'media_libraries_menu'}),
    ('root', 'Dex Hub', 'القائمة الرئيسية كلها', {}),
)
# the tabs of skin.dexhub's bar whose rows can be copied to another page
_TABS = (('movie', 'الأفلام'), ('series', 'المسلسلات'), ('discover', 'اكتشف'), ('live', 'البث المباشر'),
         ('library', 'مكتبتي'), ('servers', 'السيرفرات'), ('c1', 'قسمك الأول'), ('c2', 'قسمك الثاني'),
         ('c3', 'قسمك الثالث'), ('all', 'الرئيسية'))


def pick_section(app):
    tr = app.tr
    while True:
        index = _select(app, tr('أي قسم من Dex Hub'),
                        [(tr(label), tr(sub), 'lay_catalog.png') for _k, label, sub, _p in _SECTIONS])
        if index < 0:
            return None
        key, label, _sub, params = _SECTIONS[index]
        if key == 'dexworld':
            spec = pick_dexworld(app)
        elif key == 'tabs':
            spec = pick_tab_row(app)
        elif key == 'lists':
            spec = pick_mine(app)
        elif key in ('youtube', 'nas'):
            spec = pick_found(app, key, tr(label))
        else:
            spec = walk(app, dict(params), tr(label), source='dexhub',
                        source_label='%s  •  %s' % ('Dex Hub', tr(label)))
        if spec:
            return spec


def _tab_names():
    """The bar's own names for the user's sections (c1, c2, c3), when it has them."""
    names = {}
    try:
        from .. import bar_sections as B
        for n, sec in enumerate(B.read().get('custom') or []):
            name = (sec or {}).get('name') or (sec or {}).get('title') if isinstance(sec, dict) else ''
            if name:
                names['c%d' % (n + 1)] = clean_label(name)
    except Exception:
        pass
    return names


def pick_tab_row(app):
    """Any row another tab of the bar shows (Movies, Series, Discover, Live TV, My Library...),
    copied as a row of this page."""
    import json
    tr = app.tr
    try:
        from ..skinui import board
    except Exception:
        return None
    named = _tab_names()
    tabs = []
    for tab, title in _TABS:
        rows, seen = [], set()
        try:
            specs = board.load_specs(tab)
        except Exception:
            specs = []
        for spec in specs:
            params = spec.get('params') or {}
            if spec.get('progress') or not params.get('action') or not spec.get('title'):
                continue
            ident = json.dumps(params, sort_keys=True)
            if ident in seen:
                continue
            seen.add(ident)
            rows.append(spec)
        if rows:
            tabs.append((named.get(tab) or tr(title), rows))
    if not tabs:
        _notify(app, 'افتح الرئيسية مرة وارجع')
        return None
    while True:
        index = _select(app, tr('صفوف أقسام Dex Hub'),
                        [(name, tr('%d صف') % len(rows), 'lay_catalog.png') for name, rows in tabs])
        if index < 0:
            return None
        name, rows = tabs[index]
        pick = _select(app, name, [(clean_label(r.get('title')), clean_label(r.get('sub') or ''), 'lay_catalog.png')
                                   for r in rows])
        if pick < 0:
            continue
        spec = rows[pick]
        shape = spec.get('shape') or ''
        return {'type': 'route', 'label': clean_label(spec.get('title')), 'source': 'dexhub',
                'source_label': '%s  •  %s' % ('Dex Hub', name), 'subtitle': clean_label(spec.get('sub') or '') or name,
                'params': dict(spec.get('params') or {}), 'shape': '' if shape == 'poster' else shape}


def pick_found(app, key, heading):
    """The rows the top bar's editor offers for YouTube and the NAS, one of them as a Home row."""
    try:
        with _Busy():
            if key == 'youtube':
                from .. import youtube_feed as Y
                found = Y.row_choices()
            else:
                from .. import nas_sources as N
                if N.import_kodi_sources():
                    N.scan()
                found = N.row_choices()
    except Exception:
        found = []
    found = [(spec, where) for spec, where in found or [] if spec.get('params')]
    if not found:
        _notify(app, 'لا يوجد شيء هنا')
        return None
    index = _select(app, heading, [(clean_label(spec.get('title')), clean_label(where or ''), 'lay_catalog.png')
                                   for spec, where in found])
    if index < 0:
        return None
    spec = found[index][0]
    return {'type': 'route', 'label': clean_label(spec.get('title')), 'source': key, 'source_label': heading,
            'subtitle': heading, 'params': dict(spec['params']), 'shape': ''}


def pick_dexworld(app):
    """Every collection and list of DexWorld's page (the kits open as cover cards)."""
    tr = app.tr
    try:
        from .. import dexworld as DW
        with _Busy():
            rows = [r for r in (DW.page().get('rows') or []) if r.get('id') and r.get('title')]
    except Exception:
        rows = []
    if not rows:
        _notify(app, 'لا يوجد شيء هنا')
        return None
    rows.sort(key=lambda r: 0 if (r.get('kind') == 'kit' or r.get('members')) else 1)
    shown = [(clean_label(r.get('title')),
              tr('كوليكشن') if (r.get('kind') == 'kit' or r.get('members')) else clean_label(r.get('meta') or r.get('desc') or ''),
              r.get('logo') or 'lay_collection.png') for r in rows]
    index = _select(app, 'DexWorld', shown)
    if index < 0:
        return None
    spec = DW.spec_of(rows[index])
    return {'type': 'route', 'label': spec['title'], 'source': 'catalog', 'source_label': 'DexWorld',
            'subtitle': 'DexWorld', 'params': dict(spec['params']), 'shape': ''}


def pick_member(app, media='all', heading=''):
    """One catalog of a collection row: a source's catalog or a library."""
    tr = app.tr
    api = app.api()
    choices = [('catalog', tr('كتالوج من المصادر'), tr('كتالوجات إضافات Stremio وحساب Nuvio'), 'lay_catalog.png')]
    for service, label, _root, client in _LIBRARY_ROOTS:
        if _linked(api, client):
            choices.append((service, label, tr('مكتبة أو قسم من %s') % label, 'lay_library.png'))
    while True:
        index = _select(app, heading or tr('إضافة صف'), [(c[1], c[2], c[3]) for c in choices])
        if index < 0:
            return None
        kind = choices[index][0]
        spec = pick_catalog(app, media) if kind == 'catalog' else pick_library(app, kind)
        if spec and (spec.get('params') or {}).get('action'):
            return spec


def _linked(api, client_name):
    try:
        return bool(getattr(api, client_name).is_signed_in())
    except Exception:
        return False


# -------------------------------------------------------------- catalogs
def _row_capable(catalog):
    """A catalog that needs a search text or a mandatory filter cannot be a row."""
    required = set(str(x) for x in (catalog.get('extraRequired') or []))
    for extra in catalog.get('extra') or []:
        if isinstance(extra, dict) and extra.get('isRequired'):
            required.add(str(extra.get('name') or ''))
    return not (required - {'skip', ''})


def catalog_choices(api):
    from ..dexhub import store
    out, seen = [], set()
    for provider in store.list_providers() or []:
        if not isinstance(provider, dict) or provider.get('enabled') is False:
            continue
        catalogs = []
        for catalog in api._provider_catalogs(provider) or []:
            cid, ctype = str(catalog.get('id') or ''), str(catalog.get('type') or '')
            key = (provider.get('id'), ctype, cid)
            if not cid or not ctype or key in seen or not _row_capable(catalog):
                continue
            seen.add(key)
            catalogs.append(catalog)
        if catalogs:
            out.append((provider, catalogs))
    return out


def pick_catalog(app, media='all'):
    tr = app.tr
    api = app.api()
    sources = catalog_choices(api)
    if not sources:
        _notify(app, 'لا توجد كتالوجات في المصادر المثبّتة')
        return None
    while True:
        if len(sources) == 1:
            provider, catalogs = sources[0]
        else:
            index = _select(app, tr('اختر المصدر'), [
                (_provider_name(p), tr('%d كتالوج') % len(cats), _provider_icon(api, p))
                for p, cats in sources])
            if index < 0:
                return None
            provider, catalogs = sources[index]
        # the page's own media type first
        wanted = {'movie': 'movie', 'series': 'series'}.get(media)
        ordered = sorted(catalogs, key=lambda c: 0 if (not wanted or c.get('type') == wanted) else 1)
        index = _select(app, _provider_name(provider), [
            (clean_label(c.get('name') or c.get('id')), tr(_TYPE_LABELS.get(c.get('type'), c.get('type') or '')), '')
            for c in ordered])
        if index < 0:
            if len(sources) == 1:
                return None
            continue
        return catalog_spec(app, provider, ordered[index])


def catalog_spec(app, provider, catalog):
    tr = app.tr
    name = clean_label(catalog.get('name') or catalog.get('id'))
    ctype = str(catalog.get('type') or '')
    kind = _TYPE_LABELS.get(ctype)
    label = '%s  ·  %s' % (name, tr(kind)) if kind else name
    pname = _provider_name(provider)
    return {
        'type': 'route', 'label': label, 'source': 'catalog',
        'source_label': '%s  •  %s' % (tr('كتالوج'), pname),
        'subtitle': tr('من %s') % pname,
        'params': {'action': 'catalog_all', 'provider_id': str(provider.get('id') or ''),
                   'media_type': ctype, 'catalog_id': str(catalog.get('id') or ''), 'label': name},
        'shape': '',
    }


def _provider_name(provider):
    manifest = provider.get('manifest') or {}
    return clean_label(provider.get('name') or manifest.get('name') or 'Source')


def _provider_icon(api, provider):
    try:
        manifest = provider.get('manifest') or {}
        art = api.provider_art(_provider_name(provider), manifest,
                               provider.get('base_url') or provider.get('manifest_url') or '')
        return art.get('icon') or art.get('thumb') or ''
    except Exception:
        return ''


# ------------------------------------------------------------ collections
def collection_choices():
    from .. import collection_sets as cs
    out = []
    for row in cs.list_sets() or []:
        entries = [e for e in (row.get('entries') or [])
                   if isinstance(e, dict) and e.get('kind') == 'nuvioGroup' and e.get('id')]
        if entries:
            out.append((row, entries))
    return out


def find_collection(set_id, entry_id):
    """(set, entry) for a saved collection row, or (None, None) once it is gone."""
    from .. import collection_sets as cs
    row = cs.get_set(set_id) if set_id else None
    for entry in (row or {}).get('entries') or []:
        if isinstance(entry, dict) and str(entry.get('id') or '') == str(entry_id or ''):
            return row, entry
    return None, None


def pick_collection(app):
    tr = app.tr
    sets = collection_choices()
    if not sets:
        _notify(app, 'لا توجد مجموعات محفوظة')
        return None
    while True:
        if len(sets) == 1:
            row, entries = sets[0]
        else:
            index = _select(app, tr('اختر مجموعة'), [
                (clean_label(r.get('name') or r.get('id')), tr('%d مجموعة') % len(es), 'lay_collection.png')
                for r, es in sets])
            if index < 0:
                return None
            row, entries = sets[index]
        set_name = clean_label(row.get('name') or row.get('id'))
        index = _select(app, set_name, [
            (clean_label(e.get('name') or e.get('id')),
             tr('%d عنصر') % len(((e.get('payload') or {}).get('folders') or [])), '')
            for e in entries])
        if index < 0:
            if len(sets) == 1:
                return None
            continue
        entry = entries[index]
        return {
            'type': 'collection', 'label': clean_label(entry.get('name') or entry.get('id')),
            'source': 'collection', 'source_label': '%s  •  %s' % (tr('مجموعة'), set_name),
            'set_id': str(row.get('id') or ''), 'entry_id': str(entry.get('id') or ''),
        }


# -------------------------------------------------------------- libraries
def _level(app, api, params):
    """(folders to walk into, number of titles) of one menu listing."""
    from .. import capture
    with _Busy():
        try:
            entries, _elapsed = capture.run_route(params, api)
        except Exception:
            entries = []
    entries, _more = R.split_more(entries)
    folders, works = [], 0
    for entry in entries:
        try:
            tile = R.tile_from_entry(entry)
        except Exception:
            tile = None
        if tile and tile.get('kind') == 'work':
            works += 1
            continue
        path = entry.get('path') or ''
        if not entry.get('folder') or not R.is_dexhub_path(path):
            continue
        target = R.params_of(path)
        action = target.get('action') or ''
        if not action or _SKIP_ACTIONS.search(action) or target == params:
            continue
        folders.append(entry)
    return folders, works


def pick_library(app, service):
    tr = app.tr
    api = app.api()
    root = next((r for r in _LIBRARY_ROOTS if r[0] == service), None)
    if root is None:
        return None
    _service, label, action, _client = root
    return walk(app, {'action': action}, label)


def walk(app, root_params, label, source='', source_label=''):
    """Walks a Dex Hub menu down to the listing the user adds as a row (v5.10.612: any section)."""
    tr = app.tr
    api = app.api()
    stack = [(dict(root_params), label)]
    while stack:
        params, title = stack[-1]
        folders, works = _level(app, api, params)
        choices = []
        if len(stack) > 1 or (works and folders == []):
            choices.append(('add', None, (tr('أضف «%s» كصف') % title,
                                          (tr('%d عنصر في الصفحة الأولى') % works) if works else '',
                                          'lay_add.png')))
        for entry in folders:
            art = entry.get('art') or {}
            choices.append(('open', entry, (clean_label(entry.get('label')), '',
                                            art.get('icon') or art.get('thumb') or '')))
        if not choices:
            _notify(app, 'لا يوجد شيء هنا')
            stack.pop()
            continue
        heading = '  ›  '.join(t for _p, t in stack)
        index = _select(app, heading, [c[2] for c in choices])
        if index < 0:
            stack.pop()
            continue
        kind, entry, _shown = choices[index]
        if kind == 'add':
            return library_spec(app, label, params, title, source=source, source_label=source_label)
        stack.append((R.params_of(entry.get('path')), clean_label(entry.get('label'))))
    return None


def library_spec(app, service_label, params, title, source='', source_label=''):
    tr = app.tr
    params = dict(params or {})
    params.pop('start', None)
    params.pop('offset', None)
    suffix = ''
    sorts = _SORTS.get(params.get('action') or '')
    if sorts:
        labels = [tr('ترتيب المكتبة المحفوظ')] + [tr(name) for name, _value in sorts]
        try:
            choice = xbmcgui.Dialog().select(tr('ترتيب الصف'), labels)
        except Exception:
            choice = 0
        if choice < 0:
            return None
        if choice > 0:
            name, value = sorts[choice - 1]
            params['sort'] = value
            suffix = tr(name)
    label = '%s  ·  %s' % (title, suffix) if suffix else title
    return {
        'type': 'route', 'label': label, 'source': source or service_label.lower(),
        'source_label': source_label or '%s  •  %s' % (service_label, tr('مكتبة')),
        'subtitle': 'Dex Hub' if source == 'dexhub' else service_label,
        'params': params, 'shape': '',
    }
