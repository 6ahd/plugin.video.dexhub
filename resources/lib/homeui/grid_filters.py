# -*- coding: utf-8 -*-
"""Sort, Filter and Genre on an opened catalog or server library (v5.10.106).

The Home's rows stay as they are. A catalog or a library opened from them
(the poster grid, GridWindow) shows three buttons at its left edge, reached
with Left from the first column: Sort (the orders its source offers), Filter
(year, rating, unwatched and the other choices its source offers) and Genre
(the genres its source offers). The choice is saved per catalog or library
and applied whenever that page opens again.

  catalog    a Stremio or Nuvio catalog (catalog, catalog_all, catalog_extra):
             the genres, orders and other choices its manifest lists are sent
             to the add-on with each page (the route's ``extras``). A catalog
             without genres of its own is filtered here by its titles' genres;
             the year and the rating are filtered here, and when the add-on
             has no order of its own, its titles are put in order here.
  plex       plex_library: Plex's sorts, unwatched, decade and genres
  emby, jellyfin    emby_library, jellyfin_library: their sorts, unwatched,
             decade, lowest rating and genres
  silo       silo_catalog: native server sorts, genres, years, rating and watched

A filter made here (local) keeps the titles of each page that match; the
grid reads on while a page leaves too few. An order made here reads the
catalog's first pages (up to SORT_CAP titles) and shows them in that order.

v5.10.107: the buttons sit above the grid's right end, always in sight
(reached with Up from the first row, Right at a row's end or Left at its
start), and every page of titles has them:
  tmdb       a collection folder's TMDb Discover source: TMDb's own orders,
             genres, years and ratings (the route's ``tmdb``); a TMDb list
             or film collection, and a Trakt list, sort and filter here
  local      any other page of movies and series (lists, favourites, a
             person's films, a server folder): sorted and filtered here;
             the buttons appear once its first page shows titles
"""
from .. import profile_scope as _scope
import json
import os
import re
import threading
import time

import xbmc

STORE = 'grid_filters.json'
_lock = threading.Lock()

DECADES = tuple(str(y) for y in range(2020, 1940, -10))
RATINGS = ('8', '7', '6')
SORT_CAP = 200          # titles read for an order made here
SORT_PAGES = 12

PLEX_SORTS = (('addedAt:desc', 'آخر إضافة'), ('originallyAvailableAt:desc', 'آخر إصدار'),
              ('rating:desc', 'التقييم'), ('titleSort', 'الاسم'), ('year:desc', 'السنة'))
PLEX_SHOW_SORTS = (('episode.addedAt:desc', 'آخر حلقة نزلت'),)
EMBY_SORTS = (('DateCreated:desc', 'الأحدث إضافة'), ('PremiereDate:desc', 'الأحدث إصدارًا'),
              ('CommunityRating:desc', 'التقييم'), ('SortName', 'الاسم'),
              ('ProductionYear:desc', 'السنة'))
EMBY_SHOW_SORTS = (('DateLastContentAdded:desc', 'آخر حلقة نزلت'),)
SILO_SORTS = (('added_at:desc', 'آخر إضافة'), ('release_date:desc', 'آخر إصدار'),
              ('rating_imdb:desc', 'التقييم'), ('title:asc', 'الاسم'), ('year:desc', 'السنة'),
              ('year:asc', 'الأقدم'))
SILO_SHOW_SORTS = (('latest_episode_added:desc', 'آخر حلقة نزلت'),
                   ('last_air_date:desc', 'آخر حلقة عُرضت'))
LOCAL_SORTS = (('l.new', 'الأحدث'), ('l.old', 'الأقدم'), ('l.name', 'الاسم'),
               ('l.rating', 'الأعلى تقييمًا'))
# TMDb Discover's own orders (v5.10.107)
TMDB_MOVIE_SORTS = (('popularity.desc', 'الأكثر شهرة'), ('primary_release_date.desc', 'الأحدث إصدارًا'),
                    ('primary_release_date.asc', 'الأقدم'), ('vote_average.desc', 'الأعلى تقييمًا'),
                    ('revenue.desc', 'الأعلى إيرادًا'), ('title.asc', 'الاسم'))
TMDB_TV_SORTS = (('popularity.desc', 'الأكثر شهرة'), ('first_air_date.desc', 'الأحدث إصدارًا'),
                 ('first_air_date.asc', 'الأقدم'), ('vote_average.desc', 'الأعلى تقييمًا'),
                 ('name.asc', 'الاسم'))
TMDB_MIN_VOTES = '150'      # a score order or floor without a few votes shows unknown titles first
# pages that list no titles of their own: no buttons there
_NO_TITLES = re.compile(r'(?:^|_)(?:continue|nextup|menu|server|library_menu|settings|setup|accounts)$|'
                        r'^(?:silo_library|silo_home|plex_server|collection_set_browse|providers|live)')
_VOLATILE = ('page', 'start', 'offset', 'skip', 'cursor', 'after', 'limit', 'title', 'label',
             'extras', 'tmdb', 'dh_home', 'ui_seed_key')
_SHOW_PAGES = frozenset(('emby_children', 'jellyfin_children', 'silo_series', 'silo_episodes'))

# Stremio extras a manifest may list besides the genre (labels in Arabic,
# translated by the window like every label)
EXTRA_LABELS = {
    'sort': 'الترتيب', 'sortby': 'الترتيب', 'orderby': 'الترتيب', 'order': 'الترتيب',
    'year': 'السنة', 'language': 'اللغة', 'country': 'البلد', 'contentrating': 'التصنيف العمري',
    'studio': 'الاستوديو', 'network': 'الشبكة', 'collection': 'المجموعة', 'rating': 'التقييم',
    'availability': 'التوفر', 'type': 'النوع الفرعي', 'category': 'الفئة', 'provider': 'المنصة',
}
_SORT_EXTRAS = ('sort', 'sortby', 'orderby', 'order', 'sorting')
_NOT_FILTERS = ('genre', 'skip', 'search', 'limit', 'offset', 'page', 'cursor')


def sort_label(value):
    """Translate familiar source labels without changing their wire values."""
    key = re.sub(r'[^a-z0-9]', '', str(value).lower())
    return {
        'latestep': 'آخر حلقة نزلت', 'latestepisode': 'آخر حلقة نزلت',
        'latestepisodes': 'آخر حلقة نزلت', 'lastepisode': 'آخر حلقة نزلت',
        'lastairdate': 'آخر حلقة عُرضت', 'latestrelease': 'آخر إصدار',
        'releasedate': 'آخر إصدار', 'latest': 'آخر إصدار',
        'recent': 'الأحدث', 'recentlyadded': 'آخر إضافة', 'dateadded': 'آخر إضافة',
        'latestadded': 'آخر إضافة', 'newest': 'الأحدث',
        'trending': 'الرائج', 'trend': 'الرائج', 'popular': 'الأكثر شهرة',
        'popularity': 'الأكثر شهرة', 'rating': 'التقييم',
        'az': 'الاسم', 'title': 'الاسم', 'old': 'الأقدم', 'oldest': 'الأقدم',
    }.get(key, str(value))


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] homeui grid filters: %s' % msg, level)


# ------------------------------------------------------------------ store
def _path(profile):
    return _scope.path('homeui/' + STORE)


def _read(profile):
    try:
        with open(_path(profile), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _chosen(value):
    return value if isinstance(value, dict) and str(value.get('value') or '') else None


def load_state(profile, key):
    """The saved choice of one page: {'sort': {...}, 'genre': {...}, 'filters': {name: {...}}}."""
    with _lock:
        entry = (_read(profile).get('pages') or {}).get(key) or {}
    state = empty_state()
    state['sort'] = _chosen(entry.get('sort'))
    state['genre'] = _chosen(entry.get('genre'))
    for name, chosen in (entry.get('filters') or {}).items():
        if _chosen(chosen):
            state['filters'][name] = chosen
    return state


def save_state(profile, key, state):
    with _lock:
        data = _read(profile)
        pages = data.get('pages') if isinstance(data.get('pages'), dict) else {}
        if is_empty(state):
            pages.pop(key, None)
        else:
            pages[key] = {'sort': state.get('sort'), 'genre': state.get('genre'),
                          'filters': state.get('filters') or {}, 'ts': int(time.time())}
        if len(pages) > 300:
            # the pages chosen longest ago go first
            for old in sorted(pages, key=lambda k: pages[k].get('ts', 0))[:len(pages) - 300]:
                pages.pop(old, None)
        data['v'] = 1
        data['pages'] = pages
        try:
            if not os.path.isdir(profile):
                os.makedirs(profile)
            tmp = _path(profile) + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False)
            os.replace(tmp, _path(profile))
        except Exception as exc:
            _log('not saved: %s' % exc, xbmc.LOGWARNING)


def is_empty(state):
    state = state or {}
    return not state.get('sort') and not state.get('genre') and not state.get('filters')


def empty_state():
    return {'sort': None, 'genre': None, 'filters': {}}


def native_state(facets, state):
    """Migrate saved local choices when a server now performs them itself."""
    if facets.get('kind') not in ('silo', 'plex', 'emby', 'jellyfin'):
        return state
    state = dict(state or empty_state())
    chosen = dict(state.get('sort') or {})
    value = chosen.get('value')
    if facets['kind'] == 'silo':
        value = {'l.new': 'year:desc', 'l.old': 'year:asc', 'l.name': 'title:asc',
                 'l.rating': 'rating_imdb:desc'}.get(value, value)
    state['sort'] = next((dict(s) for s in facets['sorts'] if s['value'] == value), None)
    allowed = {f['name'] for f in facets['filters']}
    filters = {}
    for name, entry in (state.get('filters') or {}).items():
        name = {'l.decade': 'decade', 'l.rating': 'rating'}.get(name, name)
        if name in allowed:
            filters[name] = dict(entry)
            if facets['kind'] == 'plex' and name == 'rating':
                filters[name]['label'] = 'أعلى من %s' % entry.get('value', '')
    state['filters'] = filters
    return state


# -------------------------------------------------------------- the pages
def describe(params, app):
    """What a grid page can sort and filter, or None (favourites, channels, folders...)."""
    params = dict(params or {})
    action = params.get('action') or ''
    try:
        if params.get('live_src') and params.get('catalog'):
            return _live_catalog(params, app)
        if action in ('catalog', 'catalog_all', 'catalog_extra'):
            return _catalog(params, app)
        if action == 'collection_nuvio_source_open':
            return _collection_source(params, app)
        if action == 'collection_nuvio_sources_open':
            # a folder's "All": its sources merged, sorted and filtered here
            return describe_local(params)
        if action == 'plex_library' and params.get('server_id') and params.get('key'):
            return _server('plex', params, 'plex|%s|%s' % (params['server_id'], params['key']))
        if action == 'emby_library' and params.get('key'):
            return _server('emby', params, 'emby|%s' % params['key'])
        if action == 'jellyfin_library' and params.get('key'):
            return _server('jellyfin', params, 'jf|%s' % params['key'])
        if ((action == 'silo_catalog' and params.get('library_id')) or action in
                ('silo_collection', 'silo_user_collection', 'silo_personal',
                 'silo_search', 'silo_section', 'silo_library_section')):
            return _silo(params)
    except Exception as exc:
        _log('page not described: %s' % exc, xbmc.LOGWARNING)
    return None


def _local_sorts():
    return [{'value': value, 'label': label, 'local': True} for value, label in LOCAL_SORTS]


def _local_filters(skip_year=False):
    out = []
    if not skip_year:
        out.append({'name': 'l.decade', 'label': 'سنة الإصدار', 'default': 'الكل', 'local': True,
                    'options': [(d, '%ss' % d) for d in DECADES]})
    out.append({'name': 'l.rating', 'label': 'التقييم', 'default': 'الكل', 'local': True,
                'options': [(r, '%s+' % r) for r in RATINGS]})
    return out


def _catalog(params, app, provider=None, catalog_def=None):
    from . import stremio_catalogs as SC
    pid, media, cid = params.get('provider_id'), params.get('media_type'), params.get('catalog_id')
    if not (pid and media and cid):
        return None
    if provider is None:
        from .. import store
        provider = store.get_provider(pid)
    if not provider:
        return None
    if catalog_def is None:
        catalog_def = app.api()._catalog_definition(provider, cid, media) or {}
    genres, required, filters, sorts, has_year = [], False, [], [], False
    extras = SC.extras(catalog_def)
    for extra in extras.values():
        name = str(extra.get('name') or '').strip()
        low = name.lower()
        options = [str(o) for o in (extra.get('options') or []) if str(o or '').strip()]
        if low == 'genre':
            genres = options
            required = bool(extra.get('isRequired'))
            continue
        if not name or low in _NOT_FILTERS:
            continue
        if options and low.replace('_', '') in _SORT_EXTRAS:
            # the add-on's own orders
            sorts.extend({'value': o, 'label': sort_label(o), 'extra': name, 'local': False} for o in options)
            continue
        if low == 'year':
            has_year = True
        filters.append({'name': 'x.%s' % name, 'label': EXTRA_LABELS.get(low.replace('_', ''), name),
                        'default': 'الكل', 'local': False, 'required': bool(extra.get('isRequired')),
                        'input': not bool(options),
                        'options': [(o, o) for o in options]})
    if 'genre' in [str(x).lower() for x in catalog_def.get('extraRequired') or []]:
        required = True
    if not genres and isinstance(catalog_def.get('genres'), list):
        genres = [str(o) for o in catalog_def['genres'] if str(o or '').strip()]
    if params.get('genre') and params['genre'] not in genres:
        # the page's own genre is the add-on's, even when its manifest lists none
        genres = [params['genre']] + genres
    filters.extend(_local_filters(skip_year=has_year))
    preset = empty_state()
    route_extras = SC.request_extras(catalog_def, params)
    if params.get('genre'):
        preset['genre'] = {'value': params['genre'], 'label': params['genre']}
    for name, value in list(route_extras.items()) + [('year', params.get('year')),
                        (params.get('extra_name'), params.get('extra_value'))]:
        if not (name and value):
            continue
        if str(name).lower() == 'genre':
            preset['genre'] = {'value': value, 'label': value}
        elif str(name).lower().replace('_', '') in _SORT_EXTRAS:
            preset['sort'] = {'value': value, 'label': sort_label(value), 'extra': name}
        else:
            preset['filters']['x.%s' % name] = {'value': value, 'label': value}
    base = dict(params)
    for name in ('genre', 'year', 'extra_name', 'extra_value', 'page', 'cursor', 'extras',
                 'offset', 'skip', 'after', 'start'):
        base.pop(name, None)
    key = 'cat|%s|%s|%s' % (pid, media, cid)
    if not is_empty(preset):
        # a page opened from one genre (or year) folder keeps its own choice,
        # apart from the catalog's
        marks = ['g=%s' % (preset.get('genre') or {}).get('value', '')]
        marks.extend('%s=%s' % (name, value.get('value')) for name, value in sorted(preset['filters'].items()))
        if preset.get('sort'):
            marks.append('s=%s' % preset['sort'].get('value'))
        key = '%s|%s' % (key, '|'.join(marks))
    if SC.dexworld_sortable(provider, catalog_def):
        sorts = [{'value': value, 'label': label, 'extra': 'sort', 'local': False}
                 for value, label in (('Latest', 'آخر إضافة'), ('A-Z', 'الاسم'), ('Old', 'الأقدم'))]
    return {
        'kind': 'catalog', 'key': key, 'base': base,
        'sorts': sorts + _local_sorts(),
        'genre': {'mode': 'server' if genres else 'local', 'required': bool(required and genres),
                  'options': [(g, g) for g in genres]},
        'filters': filters,
        'preset': None if is_empty(preset) else preset,
    }


def _live_catalog(params, app):
    from . import live_stremio as ST, stremio_catalogs as SC
    provider = ST.provider_for(params.get('live_src'))
    if not provider:
        return None
    definition = SC.definition(provider, params)
    if not definition:
        return None
    canonical = dict(params, provider_id=provider.get('id') or params['live_src'],
                     media_type=definition.get('type'), catalog_id=definition.get('id'))
    facets = _catalog(canonical, app, provider, definition)
    if facets is None:
        return None
    facets['kind'] = 'live_catalog'
    facets['base'] = dict(params)
    for key in ('offset', 'skip', 'after', 'extras', 'genre', 'sort'):
        facets['base'].pop(key, None)
    facets['key'] = 'live|' + str(params['live_src']) + '|' + facets['key']
    if SC.is_live(definition):
        facets['filters'] = [f for f in facets['filters'] if not f.get('local')]
        facets['sorts'] = [s for s in facets['sorts'] if not s.get('local')]
    elif SC.dexworld_sortable(provider, definition):
        facets['sorts'] = [s for s in facets['sorts'] if not s.get('local')]
    return facets


def _collection_source(params, app):
    """A collection folder's catalog (an add-on catalog source): the catalog's
    own choices, the source's genre as the page's starting genre. The page
    keeps its collection route until a choice is made."""
    from .. import nuvio_collection_ui as ncu
    _row, _group, folder = ncu._nuvio_find_group_folder(
        params.get('set_id'), params.get('group_id'), params.get('folder_id'))
    sources = ((folder or {}).get('payload') or {}).get('sources') or []
    try:
        source = ncu._compat_nuvio_source(sources[int(params.get('source_index') or 0)])
    except Exception:
        return None
    kind = str(source.get('sourceKind') or '')
    if kind == 'tmdb':
        return _tmdb_source(params, source)
    if kind != 'addonCatalog':
        # a Trakt list (and anything newer): sorted and filtered here
        return describe_local(params)
    provider = ncu._collections_mod.find_nuvio_provider_by_addon_id(source.get('addonId') or '')
    if not provider or not source.get('catalogId'):
        return describe_local(params)
    media = ncu._contract.catalog_type(source.get('catalogType') or source.get('mediaType') or 'movie') or 'movie'
    catalog = {'action': 'catalog_all', 'provider_id': str(provider.get('id') or ''), 'media_type': media,
               'catalog_id': str(source.get('catalogId')), 'label': source.get('name') or '', 'nuvio': '1'}
    extra = source.get('extra') if isinstance(source.get('extra'), dict) else {}
    genre = source.get('genre') or extra.get('genre') or ''
    if genre:
        catalog['genre'] = str(genre)
    # Keep every declared source extra together (genre + year + platform,
    # etc.), instead of retaining only the first one in a collection.
    if extra:
        catalog['extras'] = json.dumps(extra, ensure_ascii=False)
    return _catalog(catalog, app)


def _server(kind, params, key):
    show = params.get('library_type') == 'show'
    if kind == 'plex':
        pairs = list(PLEX_SORTS)
        if show:
            pairs[1:1] = list(PLEX_SHOW_SORTS)
    else:
        pairs = list(EMBY_SORTS)
        if show:
            pairs[1:1] = list(EMBY_SHOW_SORTS)
    sorts = [{'value': value, 'label': label, 'local': False} for value, label in pairs]
    filters = [
        {'name': 'unwatched', 'label': 'حالة المشاهدة', 'default': 'الكل', 'local': False,
         'options': [('1', 'غير المشاهدة فقط')]},
        {'name': 'decade', 'label': 'سنة الإصدار', 'default': 'الكل', 'local': False,
         'options': [(d, '%ss' % d) for d in DECADES]},
    ]
    if kind == 'plex':
        filters.append({'name': 'rating', 'label': 'التقييم', 'default': 'الكل', 'local': False,
                        'options': [(r, 'أعلى من %s' % r) for r in RATINGS]})
    else:
        filters.append({'name': 'rating', 'label': 'التقييم', 'default': 'الكل', 'local': False,
                        'options': [(r, '%s+' % r) for r in RATINGS]})
    base = dict(params)
    for name in ('start', 'genre', 'unwatched', 'decade', 'rating'):
        base.pop(name, None)
    return {'kind': kind, 'key': key, 'base': base, 'sorts': sorts,
            'genre': {'mode': 'server', 'required': False, 'options': None},
            'filters': filters, 'preset': None}


def _silo(params):
    base = dict(params)
    for name in ('offset', 'sort', 'genre', 'unwatched', 'decade', 'rating'):
        base.pop(name, None)
    pairs = list(SILO_SORTS)
    if params.get('media_type') in ('series', 'show', 'tvshow'):
        pairs[1:1] = SILO_SHOW_SORTS
    key = 'silo|%s|%s' % (params.get('library_id'), params.get('media_type') or '')
    if params.get('action') != 'silo_catalog':
        key += '|%s|%s|%s|%s' % (params.get('action'), params.get('collection_id') or '',
                                  params.get('section_id') or '', params.get('source') or '')
    return {'kind': 'silo', 'key': key,
            'base': base, 'sorts': [{'value': v, 'label': l, 'local': False} for v, l in pairs],
            'genre': {'mode': 'server', 'required': False, 'options': None},
            'filters': [
                {'name': 'unwatched', 'label': 'حالة المشاهدة', 'default': 'الكل', 'local': False,
                 'options': [('1', 'غير المشاهدة فقط')]},
                {'name': 'decade', 'label': 'سنة الإصدار', 'default': 'الكل', 'local': False,
                 'options': [(d, '%ss' % d) for d in DECADES]},
                {'name': 'rating', 'label': 'التقييم', 'default': 'الكل', 'local': False,
                 'options': [(r, '%s+' % r) for r in RATINGS]},
            ], 'preset': None}


def _tmdb_source(params, source):
    """A collection folder's TMDb source (v5.10.107). A Discover source takes
    TMDb's own orders, genres, years and ratings with each page; a list or a
    film collection (one fixed set of titles) is sorted and filtered here."""
    from .. import tmdb_direct as T
    tv = T._normalize_media_type(source.get('mediaType') or source.get('type') or 'movie') == 'tv'
    key = 'tmdb|%s|%s|%s|%s' % (params.get('set_id') or '', params.get('group_id') or '',
                                params.get('folder_id') or '', params.get('source_index') or '0')
    base = dict(params)
    for name in ('page', 'tmdb'):
        base.pop(name, None)
    if str(source.get('tmdbSourceType') or 'DISCOVER').strip().upper() != 'DISCOVER':
        facets = describe_local(params)
        if facets is not None:
            facets['key'] = key
        return facets
    pairs = TMDB_TV_SORTS if tv else TMDB_MOVIE_SORTS
    return {
        'kind': 'tmdb', 'key': key, 'base': base, 'tv': tv,
        'sorts': [{'value': value, 'label': label, 'local': False} for value, label in pairs],
        'genre': {'mode': 'server', 'required': False, 'options': None},
        'filters': [
            {'name': 'decade', 'label': 'سنة الإصدار', 'default': 'الكل', 'local': False,
             'options': [(d, '%ss' % d) for d in DECADES]},
            {'name': 'rating', 'label': 'التقييم', 'default': 'الكل', 'local': False,
             'options': [(r, '%s+' % r) for r in RATINGS]},
        ],
        'preset': None,
    }


def page_key(params):
    """A short stable key for any other page: its route without paging."""
    import hashlib
    clean = dict((k, str(v)) for k, v in (params or {}).items() if k not in _VOLATILE and v not in (None, ''))
    blob = json.dumps(clean, sort_keys=True, ensure_ascii=True)
    return 'loc|%s|%s' % (clean.get('action') or '', hashlib.sha1(blob.encode('utf-8')).hexdigest()[:16])


def describe_local(params):
    """Sort, Filter and Genre made here, for a page of titles of any source
    (v5.10.107), or None for a page that lists no titles of its own."""
    params = dict(params or {})
    action = str(params.get('action') or '')
    if not action or params.get('live_group') is not None or params.get('live_src') or _NO_TITLES.search(action):
        return None
    if action in _SHOW_PAGES or (action == 'plex_children'
                                 and str(params.get('key') or '').startswith('/library/metadata/')):
        # (rows.is_show_page)
        # a show's seasons or a season's episodes keep their own order
        # (v5.10.108: they open in the Home's grid now)
        return None
    try:
        from . import servers as S
        if S.is_progress(params):
            return None
    except Exception:
        pass
    library = action in ('lib_watchlist', 'lib_favs') or (action == 'favorites' and params.get('source'))
    filters = [f for f in _local_filters() if not library or f['name'] != 'l.rating']
    if library and params.get('media') not in ('movie', 'series'):
        # v5.10.286 (issue #16): a Library list of films and series together
        # (Nuvio's My list, the merged Watchlist) can show one of them only
        filters.insert(0, {'name': 'l.media', 'label': 'أفلام ومسلسلات', 'default': 'الكل', 'local': True,
                           'options': [('movie', 'أفلام'), ('series', 'مسلسلات')]})
    return {'kind': 'local', 'key': page_key(params), 'base': dict(params),
            'sorts': [s for s in _local_sorts() if not library or s['value'] != 'l.rating'],
            'genre': {'mode': 'local', 'required': False, 'options': [], 'available': not library},
            'filters': filters, 'preset': None}


def titles_page(tiles):
    """Does a page show movies and series (not folders, channels or menus)?"""
    tiles = list(tiles or [])
    works = sum(1 for t in tiles if (t or {}).get('kind') == 'work'
                and (t or {}).get('media_type') in ('movie', 'series', 'episode', ''))
    return bool(tiles) and works * 2 >= len(tiles)


# ------------------------------------------------------------ the choice
def facet(facets, name):
    for item in (facets or {}).get('filters') or []:
        if item['name'] == name:
            return item
    return None


def same_sort(a, b):
    a, b = a or {}, b or {}
    return (str(a.get('value') or '') == str(b.get('value') or '')
            and str(a.get('extra') or '') == str(b.get('extra') or ''))


def same_state(a, b):
    """Do two choices pick the same order, genre and filters? (v5.10.108)"""
    a, b = a or empty_state(), b or empty_state()
    if not same_sort(a.get('sort'), b.get('sort')):
        return False
    if bool((a.get('sort') or {}).get('local')) != bool((b.get('sort') or {}).get('local')):
        return False
    if str((a.get('genre') or {}).get('value') or '') != str((b.get('genre') or {}).get('value') or ''):
        return False

    def _filters(state):
        return dict((name, str((chosen or {}).get('value') or ''))
                    for name, chosen in (state.get('filters') or {}).items()
                    if str((chosen or {}).get('value') or ''))
    return _filters(a) == _filters(b)


def route_params(facets, state):
    """The page's route parameters with the choices sent to the source."""
    params = dict(facets['base'])
    state = state or empty_state()
    chosen = {}
    for name, value in (state.get('filters') or {}).items():
        item = facet(facets, name)
        if item is not None and not item.get('local') and str(value.get('value') or ''):
            chosen[name] = str(value['value'])
    genre = state.get('genre') or {}
    server_genre = facets['genre']['mode'] == 'server' and str(genre.get('value') or '')
    sort = state.get('sort') or {}
    server_sort = '' if sort.get('local') else str(sort.get('value') or '')
    kind = facets['kind']
    if kind in ('catalog', 'live_catalog'):
        extras = {}
        if server_genre:
            extras['genre'] = str(genre['value'])
        if server_sort and sort.get('extra'):
            extras[str(sort['extra'])] = server_sort
        for name, value in chosen.items():
            if name.startswith('x.'):
                extras[name[2:]] = value
        if extras or not is_empty(state):
            # a choice shows the catalog's titles, never its genre folders,
            # and only the choice made here goes to the add-on (an empty
            # ``extras`` tells the route to leave the classic filters out)
            if kind == 'catalog':
                params['action'] = 'catalog_all'
            params['extras'] = json.dumps(extras, sort_keys=True, ensure_ascii=False)
        return params
    if kind in ('plex', 'emby', 'jellyfin', 'silo'):
        if server_genre:
            params['genre'] = str(genre['value'])
        if server_sort:
            params['sort'] = server_sort
        for name, value in chosen.items():
            params[name] = value
        return params
    if kind == 'tmdb':
        over = tmdb_overrides(facets, server_sort, str(genre.get('value') or '') if server_genre else '', chosen)
        if over:
            params['tmdb'] = json.dumps(over, sort_keys=True)
        return params
    return params


def tmdb_overrides(facets, sort='', genre='', chosen=None):
    """TMDb Discover parameters for a choice (v5.10.107). A choice narrows the
    source rather than replacing it: tmdb_direct merges the ``dex_`` keys with
    the source's own query (a genre is added to the source's genres, a vote
    or score floor never goes below the source's); '' removes a source value
    that would contradict the choice (its fixed year when a decade is chosen)."""
    chosen = chosen or {}
    tv = bool((facets or {}).get('tv'))
    date = 'first_air_date' if tv else 'primary_release_date'
    over = {}
    decade = str(chosen.get('decade') or '')
    if sort:
        over['sort_by'] = sort
        if sort.startswith('vote_average'):
            over['dex_min_votes'] = TMDB_MIN_VOTES
        if sort.endswith('_date.desc'):
            # newest first: released titles, not next year's announcements
            # (a decade's own end stays when it is earlier)
            over['dex_until_today'] = '1'
    if genre:
        over['dex_genre'] = genre
    if decade.isdigit():
        start = int(decade)
        over['%s.gte' % date] = '%d-01-01' % start
        over['%s.lte' % date] = '%d-12-31' % (start + 9)
        # a source's own year or dates would contradict the decade
        for name in ('first_air_date_year', 'primary_release_year', 'year', 'release_date.gte',
                     'release_date.lte', 'air_date.gte', 'air_date.lte'):
            over[name] = ''
    rating = str(chosen.get('rating') or '')
    if rating:
        over['dex_min_rating'] = rating
        over['dex_min_votes'] = TMDB_MIN_VOTES
    return over


def local_sort(state):
    """The order made here ('l.new', 'l.old', 'l.name', 'l.rating') or ''."""
    sort = (state or {}).get('sort') or {}
    return str(sort.get('value') or '') if sort.get('local') else ''


def local_rules(facets, state):
    """The checks made here on each title: {'genre': name, 'decade': 1990, 'rating': 7.0}."""
    state = state or empty_state()
    rules = {}
    genre = state.get('genre') or {}
    if facets['genre']['mode'] == 'local' and str(genre.get('value') or '').strip():
        rules['genre'] = str(genre['value']).strip().lower()
    for name, value in (state.get('filters') or {}).items():
        item = facet(facets, name)
        if item is None or not item.get('local'):
            continue
        try:
            if name == 'l.decade':
                rules['decade'] = int(value.get('value'))
            elif name == 'l.rating':
                rules['rating'] = float(value.get('value'))
            elif name == 'l.media' and value.get('value') in ('movie', 'series'):
                rules['media'] = value.get('value')     # v5.10.286
        except Exception:
            continue
    return rules


def _year(tile):
    text = str((tile or {}).get('year') or '')[:4]
    return int(text) if text.isdigit() else 0


def _rating(tile):
    try:
        return float((tile or {}).get('rating') or 0)
    except Exception:
        return 0.0


def passes(tile, rules):
    """Does a title match the checks made here? Folders and other cards always do."""
    if not rules or (tile or {}).get('kind') not in ('work',):
        return True
    if rules.get('genre'):
        names = [str(g).strip().lower() for g in tile.get('genres') or []]
        if rules['genre'] not in names:
            return False
    if rules.get('decade'):
        year = _year(tile)
        if not (rules['decade'] <= year < rules['decade'] + 10):
            return False
    if rules.get('rating'):
        if _rating(tile) < rules['rating']:
            return False
    if rules.get('media'):
        # v5.10.286: films or series only (Library lists)
        kind = str(tile.get('media_type') or '').lower()
        series = kind in ('series', 'tv', 'tvshow', 'show', 'anime', 'episode')
        if series != (rules['media'] == 'series'):
            return False
    return True


_DIGITS = re.compile(r'(\d+)')


def _name(tile):
    """A title's sort key, numbers in it by value ('Saw 2' before 'Saw 10')."""
    text = str((tile or {}).get('title') or (tile or {}).get('label') or '').strip().casefold()
    return [(0, int(part), '') if part.isdigit() else (1, 0, part)
            for part in _DIGITS.split(text) if part]


def sort_tiles(tiles, key):
    """Titles in an order made here; titles without a year or a rating go last."""
    tiles = list(tiles or [])
    if key == 'l.name':
        return sorted(tiles, key=_name)
    if key == 'l.new':
        return sorted(tiles, key=lambda t: (_year(t) == 0, -_year(t), _name(t)))
    if key == 'l.old':
        return sorted(tiles, key=lambda t: (_year(t) == 0, _year(t), _name(t)))
    if key == 'l.rating':
        return sorted(tiles, key=lambda t: (_rating(t) <= 0, -_rating(t), _name(t)))
    return tiles


def server_genres(facets, app, language=''):
    """[(id, name)] of a server library's genres (asked for once, then remembered);
    TMDb's genre list in the page's language for a TMDb source."""
    kind, base = facets['kind'], facets['base']
    if kind == 'tmdb':
        from .. import tmdb_direct as T
        return T.genre_list('tv' if facets.get('tv') else 'movie', language=language)
    show = base.get('library_type') == 'show'
    api = app.api()
    if kind == 'plex':
        from .. import plex_client
        with plex_client.deadline(12.0):
            server = api._plex_server(base.get('server_id') or '')
            return plex_client.genres(server, base.get('key') or '')
    if kind == 'emby':
        from .. import emby_client
        return emby_client.genres(api._emby_server(), base.get('key') or '',
                                  'Series' if show else 'Movie')
    if kind == 'jellyfin':
        from ..providers import jellyfin_provider
        servers = jellyfin_provider.servers()
        if not servers:
            return []
        return jellyfin_provider.genres(servers[0], base.get('key') or '', 'Series' if show else 'Movie')
    if kind == 'silo':
        return api.silo_client.genres(api._silo_server(), base.get('library_id') or '')
    return []


def summary(facets, state, tr):
    """The choice in a few words for the page's title line: 'Action  ·  Newest'."""
    state = state or empty_state()
    parts = []
    genre = state.get('genre') or {}
    if str(genre.get('value') or ''):
        parts.append(str(genre.get('label') or genre['value']))
    sort = state.get('sort') or {}
    if str(sort.get('value') or ''):
        parts.append(tr(str(sort.get('label') or sort['value'])))
    for item in (facets or {}).get('filters') or []:
        chosen = (state.get('filters') or {}).get(item['name'])
        if chosen and str(chosen.get('value') or ''):
            parts.append(tr(str(chosen.get('label') or chosen['value'])))
    return '  ·  '.join(parts)
