# -*- coding: utf-8 -*-
"""Catalog controls shared by native skin rows and Python browse rows."""
from . import grid_filters as GF

def describe(route, app):
    return GF.describe(route, app) or GF.describe_local(route)

def state(facets, app):
    saved = GF.native_state(facets, GF.load_state(app.profile, facets['key']))
    return saved if not GF.is_empty(saved) else (facets.get('preset') or GF.empty_state())

def choose(route, app, title='', tiles=(), which=None):
    from .options import choose as menu
    from ..skinui.actions import _FacetPicker, _bind_picker
    facets = describe(route, app)
    if facets is None:
        return None
    current = state(facets, app)
    entries = [(app.tr('ترتيب'), 'sort'), (app.tr('النوع'), 'genre'), (app.tr('فلتر'), 'filter')]
    if facets.get('genre', {}).get('available') is False:
        entries = [e for e in entries if e[1] != 'genre']
    if not GF.is_empty(current):
        entries.append((app.tr('إعادة ضبط'), 'reset'))
    if which is None:
        selected = menu(app.tr('خيارات الكتالوج'), [e[0] for e in entries], subtitle=title)
        if selected < 0 or selected >= len(entries):
            return None
        which = entries[selected][1]
    if which == 'reset':
        picked = GF.empty_state()
    else:
        genres = {}
        for tile in tiles:
            for genre in tile.get('genres') or (tile.get('info') or {}).get('genres') or []:
                name = str(genre).strip()
                if name:
                    old = genres.get(name.lower(), [name, 0]);genres[name.lower()] = [name, old[1] + 1]
        _bind_picker()
        picker = _FacetPicker(app, facets, current, title, genres)
        picker._local_hint = app.tr('العناوين المحملة فقط')
        {'sort': picker._choose_sort, 'genre': picker._choose_genre, 'filter': picker._choose_filter}[which]()
        picked = picker.picked
    if picked is None or GF.same_state(current, picked):
        return None
    GF.save_state(app.profile, facets['key'], picked)
    return facets, picked

def load(route, app, loader):
    facets = describe(route, app)
    if facets is None:
        return loader(route), ''
    current = state(facets, app)
    effective = GF.route_params(facets, current) if not GF.is_empty(current) else dict(route)
    for name in ('skip', 'offset', 'after'):
        if name in route:
            effective[name] = route[name]
    tiles, more = loader(effective)
    rules = GF.local_rules(facets, current)
    tiles = [t for t in tiles if GF.passes(t, rules)]
    order = GF.local_sort(current)
    if order:
        tiles = GF.sort_tiles(tiles, order)
    return (tiles, more), GF.summary(facets, current, app.tr)
