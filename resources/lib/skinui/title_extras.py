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
import struct
import threading
import time
import zlib

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


def _png_white(raw):
    """A cut-out logo (PNG with transparency) as white on its own alpha;
    None for a logo with its own background, or a PNG this does not read.
    Only the alpha channel is unfiltered: each channel's filter reads the
    same channel, so a quarter of the work of a full decode."""
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    pos, ihdr, idat, trns = 8, None, [], None
    while pos + 8 <= len(raw):
        size = struct.unpack('>I', raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + size]
        pos += 12 + size
        if kind == b'IHDR':
            ihdr = body
        elif kind == b'IDAT':
            idat.append(body)
        elif kind == b'tRNS':
            trns = body
        elif kind == b'IEND':
            break
    if not ihdr or not idat:
        return None
    width, height, depth, ctype, _comp, _filt, interlace = struct.unpack('>IIBBBBB', ihdr)
    if depth != 8 or interlace or width * height > 600000:
        return None
    if ctype == 6:
        bpp, off = 4, 3
    elif ctype == 4:
        bpp, off = 2, 1
    elif ctype == 3 and trns:
        bpp, off = 1, 0
    else:
        return None
    data = zlib.decompress(b''.join(idat))
    stride = width * bpp + 1
    if len(data) < stride * height:
        return None
    prev = [0] * width
    alpha_rows = []
    for y in range(height):
        line = data[y * stride:(y + 1) * stride]
        f = line[0]
        vals = line[1 + off::bpp]
        cur = [0] * width
        if f == 0:
            cur = list(vals)
        elif f == 2:
            cur = [(v + p) & 255 for v, p in zip(vals, prev)]
        elif f == 1:
            left = 0
            for x in range(width):
                left = (vals[x] + left) & 255
                cur[x] = left
        elif f == 3:
            left = 0
            for x in range(width):
                left = (vals[x] + ((left + prev[x]) >> 1)) & 255
                cur[x] = left
        elif f == 4:
            left = upleft = 0
            for x in range(width):
                up = prev[x]
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                pred = left if pa <= pb and pa <= pc else (up if pb <= pc else upleft)
                left = (vals[x] + pred) & 255
                cur[x] = left
                upleft = up
        else:
            return None
        alpha_rows.append(cur)
        prev = cur
    if ctype == 3:
        table = list(trns) + [255] * (256 - len(trns))
        alpha_rows = [[table[i] for i in row] for row in alpha_rows]
    corners = (alpha_rows[0][0], alpha_rows[0][-1], alpha_rows[-1][0], alpha_rows[-1][-1])
    clear = sum(1 for row in alpha_rows for a in row[::4] if a < 16)
    if min(corners) > 200 or clear * 4 < (width * height) // 20:
        return None                     # its own background: drawn as it is
    body = bytearray()
    white = b'\xff' * (width * 4)
    for row in alpha_rows:
        line = bytearray(white)
        line[3::4] = bytes(row)
        body += b'\x00' + line

    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(body), 6)) + chunk(b'IEND', b''))


def _logo_files(url):
    name = C.short_id(url)
    folder = _logos_folder()
    return os.path.join(folder, 'w_%s.png' % name), os.path.join(folder, 'k_%s' % name)


def _whiten(url):
    white, keep = _logo_files(url)
    try:
        import urllib.request
        request = urllib.request.Request(url, headers={'User-Agent': 'DexHub'})
        with urllib.request.urlopen(request, timeout=5) as answer:
            raw = answer.read(2 * 1024 * 1024)
        out = _png_white(raw)
    except Exception as exc:
        C.log('studio logo %s: %s' % (url, exc), xbmc.LOGDEBUG)
        return
    try:
        if out:
            tmp = white + '.tmp'
            with open(tmp, 'wb') as handle:
                handle.write(out)
            os.replace(tmp, white)
        else:
            with open(keep, 'w') as handle:
                handle.write('1')
    except Exception:
        pass


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
    return RW.list_url({'action': 'tmdb_titles', 'mode': mode, 'id': row['id'], 'media': media,
                        'name': row.get('name') or ''}, title=row.get('name') or '', shape='poster')


def studio_items(title):
    rows = list((title.extras or {}).get('studios') or [])
    if not rows:
        return []
    pending = [r['logo'] for r in rows if _logo_for(r)[1] == 'wait']
    if pending:
        # made once per logo (kept in the profile): a few at a time, for a
        # few seconds; the ones not ready keep their plate this time
        threads = [threading.Thread(target=_whiten, args=(u,), name='DexHubLogo', daemon=True)
                   for u in pending[:8]]
        for th in threads:
            th.start()
        deadline = time.monotonic() + 4.0
        for th in threads:
            th.join(max(0.0, deadline - time.monotonic()))
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
