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
        else:
            spec = pick_library(app, kind)
        if spec:
            return spec


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
    stack = [({'action': action}, label)]
    while stack:
        params, title = stack[-1]
        folders, works = _level(app, api, params)
        choices = []
        if len(stack) > 1:
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
            return library_spec(app, label, params, title)
        stack.append((R.params_of(entry.get('path')), clean_label(entry.get('label'))))
    return None


def library_spec(app, service_label, params, title):
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
        'type': 'route', 'label': label, 'source': service_label.lower(),
        'source_label': '%s  •  %s' % (service_label, tr('مكتبة')),
        'subtitle': service_label,
        'params': params, 'shape': '',
    }
