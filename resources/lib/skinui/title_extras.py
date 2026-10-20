# -*- coding: utf-8 -*-
"""The title page's Collection card and Studios row (v5.10.313), as Nuvio
Legacy draws them under More like this (detail.c):

  Collection  one wide card: the saga's backdrop, its name, "4 movies ·
              2017-2026", a View collection pill and the first parts'
              posters fanned on the right. OK opens the saga's titles in
              the grid, in release order.
  Studios     a tile per studio or network with its logo (white on the
              page's glass when the logo is a cut-out, as it came when it
              has its own background); OK opens the studio's titles.

Both come from the title's TMDb read (belongs_to_collection,
production_companies, networks), kept with the title (title.py); the
collection's parts are one more TMDb call, cached a month.
"""
import os

import xbmc

from . import common as C

TMDB_IMG = 'https://image.tmdb.org/t/p/'


def _t(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _img(path, size):
    path = str(path or '')
    if not path:
        return ''
    if path.lower().endswith('.svg'):
        path = path[:-4] + '.png'
    return path if path.startswith('http') else TMDB_IMG + size + path


def extract(data, series):
    """{'collection': {...}, 'studios': [...]} of a TMDb title read."""
    data = data or {}
    coll = data.get('belongs_to_collection') if not series else None
    collection = {}
    if isinstance(coll, dict) and coll.get('id'):
        collection = {'id': str(coll.get('id')), 'name': str(coll.get('name') or ''),
                      'poster': _img(coll.get('poster_path'), 'w342'),
                      'backdrop': _img(coll.get('backdrop_path'), 'w780')}
    studios, seen = [], set()
    groups = ((data.get('networks') or [], 'network'), (data.get('production_companies') or [], 'company')) \
        if series else ((data.get('production_companies') or [], 'company'),)
    for rows, kind in groups:
        for row in rows:
            if not isinstance(row, dict) or not row.get('id'):
                continue
            name = str(row.get('name') or '').strip()
            key = (kind, str(row['id']))
            if not name or key in seen:
                continue
            seen.add(key)
            studios.append({'id': str(row['id']), 'name': name, 'kind': kind,
                            'logo': _img(row.get('logo_path'), 'w300')})
    # the ones with a logo first (a name alone reads as a gap in the row)
    studios.sort(key=lambda s: 0 if s['logo'] else 1)
    return {'collection': collection, 'studios': studios[:10]}


# ------------------------------------------------------------------ collection
def _lang():
    try:
        from ..i18n import is_english
        return 'en-US' if is_english() else 'ar'
    except Exception:
        return 'en-US'


def collection_parts(coll_id, language=''):
    """[{'tmdb', 'title', 'year', 'poster', 'backdrop', 'overview', 'rating'}]
    of a TMDb collection, in release order; and the collection's own art."""
    from .. import tmdb_direct as T
    language = language or _lang()
    key = 'dh-coll1:%s:%s' % (coll_id, language)
    cached = T._cache_get(key)
    if cached is not None:
        return cached or {}
    try:
        raw = T._request('/collection/%s' % coll_id, {'language': language}, timeout=6, max_attempts=2) or {}
    except Exception as exc:
        C.log('collection %s: %s' % (coll_id, exc), xbmc.LOGWARNING)
        raw = {}
    parts = []
    for row in raw.get('parts') or []:
        if not isinstance(row, dict) or not row.get('id'):
            continue
        date = str(row.get('release_date') or '')
        parts.append({'tmdb': str(row['id']), 'title': str(row.get('title') or row.get('name') or ''),
                      'date': date, 'year': date[:4], 'overview': str(row.get('overview') or ''),
                      'poster': _img(row.get('poster_path'), 'w342'),
                      'backdrop': _img(row.get('backdrop_path'), 'w780'),
                      'rating': round(float(row.get('vote_average') or 0), 1)})
    # released first by day; the ones without a day (announced) last
    parts.sort(key=lambda p: (not p['date'], p['date']))
    out = {'name': str(raw.get('name') or ''), 'overview': str(raw.get('overview') or ''),
           'poster': _img(raw.get('poster_path'), 'w342'), 'backdrop': _img(raw.get('backdrop_path'), 'w1280'),
           'parts': parts}
    T._cache_set(key, out if parts else {}, T.POSITIVE_TTL // 4 if parts else 3600)
    return out if parts else {}


def collection_route(coll_id, name):
    from . import rows as RW
    return RW.list_url({'action': 'tmdb_titles', 'mode': 'collection', 'id': coll_id, 'name': name},
                       title=name, shape='poster')


def collection_items(title):
    coll = (title.extras or {}).get('collection') or {}
    if not coll.get('id'):
        return []
    got = collection_parts(coll['id'])
    parts = got.get('parts') or []
    if len(parts) < 2:
        return []
    name = got.get('name') or coll.get('name') or ''
    years = sorted(int(p['year']) for p in parts if str(p.get('year') or '').isdigit())
    span = '' if not years else (str(years[0]) if years[0] == years[-1] else '%d–%d' % (years[0], years[-1]))
    meta = '  ·  '.join(x for x in (_t('%d أفلام', '%d movies') % len(parts) if len(parts) <= 10
                                    else _t('%d فيلماً', '%d movies') % len(parts), span) if x)
    target = collection_route(coll['id'], name)
    here = str(title.tmdb_id or '')
    current = next((i for i, p in enumerate(parts) if p['tmdb'] == here), -1)
    posters = [p['poster'] for p in parts if p.get('poster')][:3]
    props = {'kind': 'collection', 'meta': meta, 'dhs.click': 'run', 'dhs.target': C.url('skin_opengrid', u=target),
             'count': str(len(parts)), 'here': str(current + 1) if current >= 0 else '',
             'here_label': (_t('هذا الجزء %d من %d', 'This is part %d of %d') % (current + 1, len(parts))
                            if current >= 0 else '')}
    for index, poster in enumerate(posters):
        props['p%d' % (index + 1)] = poster
    backdrop = coll.get('backdrop') or got.get('backdrop') or next((p['backdrop'] for p in parts if p.get('backdrop')), '')
    return [{'label': name, 'label2': meta, 'path': target, 'folder': True,
             'art': {'fanart': backdrop, 'landscape': backdrop, 'thumb': backdrop,
                     'poster': got.get('poster') or coll.get('poster') or (posters[0] if posters else '')},
             'props': props}]


# ------------------------------------------------------------------ studios
def _logos_folder():
    return C.folder('logos')


def _logo_files(url):
    name = C.short_id(url)
    folder = _logos_folder()
    return os.path.join(folder, 'w_%s.png' % name), os.path.join(folder, 'k_%s' % name)


def _logo_for(row):
    """(texture, mode): mode 'white' (a light logo on the glass: a studio
    pack's white logo or a cut-out made white), 'plate' (a logo with its own
    background), 'name' (no logo: the name is written), 'wait' (being made)."""
    try:
        from .. import studio_art
        pack = studio_art.resolve_player(row.get('name') or '')
    except Exception:
        pack = ''
    if pack:
        return pack, 'white'
    url = row.get('logo') or ''
    if not url:
        return '', 'name'
    white, keep = _logo_files(url)
    if os.path.exists(white):
        return white, 'white'
    if os.path.exists(keep):
        return url, 'plate'
    return url, 'wait'


def studio_route(row, series):
    from . import rows as RW
    mode = 'network' if row.get('kind') == 'network' else 'company'
    media = 'series' if (series or mode == 'network') else 'movie'
    # v5.10.360: its page shows the logo in place of the name (a light logo,
    # or one on its own background; a cut-out still being made white could be
    # dark on the page, so the name stays for it)
    texture, logo_mode = _logo_for(row)
    return RW.list_url({'action': 'tmdb_titles', 'mode': mode, 'id': row['id'], 'media': media,
                        'name': row.get('name') or ''}, title=row.get('name') or '', shape='poster',
                       logo=texture if logo_mode in ('white', 'plate') else '')


def studio_items(title):
    rows = list((title.extras or {}).get('studios') or [])
    if not rows:
        return []
    # Keep the listing CPU/network-free. Kodi's background texture loader
    # fetches the original small logo; cached white/installed pack art still wins.
    out = []
    for row in rows:
        texture, mode = _logo_for(row)
        if mode == 'wait':
            mode = 'plate'
        target = studio_route(row, title.series)
        kind = _t('شبكة', 'Network') if row.get('kind') == 'network' else _t('استوديو', 'Studio')
        out.append({'label': row.get('name') or '', 'label2': kind, 'path': target, 'folder': True,
                    'art': {'icon': texture, 'thumb': texture},
                    'props': {'kind': 'studio', 'logo_mode': mode, 'logo': texture,
                              'dhs.click': 'run', 'dhs.target': C.url('skin_opengrid', u=target)}})
    return out



def collection_tiles(coll_id):
    """Reuse the exact parts already read for the title page's collection card."""
    if not str(coll_id).isdigit():
        return []
    tiles = []
    for part in (collection_parts(str(coll_id)) or {}).get('parts') or []:
        tmdb = part.get('tmdb') or ''
        if not str(tmdb).isdigit():
            continue
        title = part.get('title') or ''
        tiles.append({'kind': 'work', 'media_type': 'movie', 'tmdb_id': str(tmdb),
                      'title': title, 'year': part.get('year') or '', 'plot': part.get('overview') or '',
                      'poster': part.get('poster') or '', 'fanart': part.get('backdrop') or '',
                      'landscape': part.get('backdrop') or '', 'rating': part.get('rating') or 0,
                      'folder': True, 'path': C.url('item_open', media_type='movie',
                          canonical_id='tmdb:' + str(tmdb), tmdb_id=str(tmdb), title=title)})
    return tiles
