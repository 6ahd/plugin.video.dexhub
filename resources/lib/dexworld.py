# -*- coding: utf-8 -*-
"""DexWorld (v5.10.496): the sections of https://blocks.dexworld.cc in Dex Hub.

The blocks site serves its page as JSON (GET /api/dexhub?lang=ar|en): the
filters, the bundle cards, and every row in the page's order, each with the
titles the page shows (TMDb ids, posters). A block's whole list is
/api/dexhub/row?id=&lang=&page= (40 a page).

The page is kept on disk (addon_data/.../cache/dexworld/<lang>.json, shared
by every profile: it is public) and read again after 30 minutes, with the
ETag; when the site does not answer, the copy on disk is used, however old.
"""
import json
import re
import os
import threading
import time
import uuid

BASE = 'https://blocks.dexworld.cc'
TTL = 1800
TIMEOUT = 12
UA = 'DexHub-Kodi/5.10 (+https://github.com/6ahd/plugin.video.dexhub)'
_lock = threading.RLock()
_mem = {}           # lang -> (read at, data)


def _t(ar, en):
    return en if lang() == 'en' else ar


def lang():
    try:
        from .i18n import is_english
        return 'en' if is_english() else 'ar'
    except Exception:
        return 'ar'


def _log(text):
    try:
        import xbmc
        xbmc.log('[DexHub] dexworld: %s' % text, xbmc.LOGINFO)
    except Exception:
        pass


def _cache_path(name):
    try:
        from .profile_scope import root
        base = root()
    except Exception:
        base = os.path.join(os.path.expanduser('~'), '.kodi', 'userdata', 'addon_data', 'plugin.video.dexhub')
    return os.path.join(base, 'cache', 'dexworld', name)


def _read_file(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write_file(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = '%s.%s.tmp' % (path, uuid.uuid4().hex)
        with open(temp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(temp, path)
    except OSError:
        pass


def _get(path, etag=''):
    """(status, json or None, etag)."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    headers = {'User-Agent': UA, 'Accept': 'application/json', 'Accept-Encoding': 'gzip'}
    if etag:
        headers['If-None-Match'] = etag
    try:
        with urlopen(Request(BASE + path, headers=headers), timeout=TIMEOUT) as answer:
            body = answer.read()
            if (answer.headers.get('Content-Encoding') or '').lower() == 'gzip':
                import gzip
                body = gzip.decompress(body)
            return answer.status, json.loads(body.decode('utf-8')), answer.headers.get('ETag') or ''
    except HTTPError as exc:
        return exc.code, None, ''


def page(language=None, fresh=False):
    """The whole page as the site gives it ({} when it was never read and the site is away)."""
    language = language or lang()
    now = time.time()
    with _lock:
        held = _mem.get(language)
        if held and not fresh and now - held[0] < TTL:
            return held[1]
        path = _cache_path('%s.json' % language)
        stored = _read_file(path) or {}
        if not fresh and stored.get('data') and now - float(stored.get('at') or 0) < TTL:
            _mem[language] = (float(stored['at']), stored['data'])
            return stored['data']
        try:
            status, data, etag = _get('/api/dexhub?lang=%s' % language, stored.get('etag') or '')
        except Exception as exc:
            _log('page: %s' % exc.__class__.__name__)
            status, data, etag = 0, None, ''
        if status == 304 and stored.get('data'):
            stored['at'] = now
            _write_file(path, stored)
            data = stored['data']
        elif status == 200 and isinstance(data, dict) and data.get('rows'):
            _write_file(path, {'at': now, 'etag': etag, 'data': data})
        else:
            data = stored.get('data') or {}
            if data:
                _log('page: the site did not answer (%s); the copy on disk' % status)
        if data:
            _mem[language] = (now, data)
        return data


def row_page(row_id, number=1, language=None):
    """One block's whole list, a page at a time: {'items': [...], 'page', 'pages', 'total', 'title'}."""
    language = language or lang()
    from urllib.parse import quote
    key = 'row_%s_%s_%d.json' % (language, row_id.replace(':', '_').replace('/', '_'), int(number))
    path = _cache_path(key)
    stored = _read_file(path) or {}
    if stored.get('data') and time.time() - float(stored.get('at') or 0) < TTL:
        return stored['data']
    try:
        status, data, _etag = _get('/api/dexhub/row?id=%s&lang=%s&page=%d' % (quote(row_id, safe=':'), language, int(number)))
    except Exception as exc:
        _log('row %s: %s' % (row_id, exc.__class__.__name__))
        status, data = 0, None
    if status == 200 and isinstance(data, dict):
        _write_file(path, {'at': time.time(), 'data': data})
        return data
    return stored.get('data') or {}


# ------------------------------------------------------------ the page's parts
def rows_by_id(data):
    return dict((row.get('id'), row) for row in data.get('rows') or [])


def filter_rows(data, key):
    """The rows a filter shows, in the page's order ("all": the main page)."""
    rows = data.get('rows') or []
    if key == 'all':
        return [row for row in rows if row.get('main')]
    return [row for row in rows if key == row.get('cat') or key in (row.get('cats') or [])]


def scope_rows(data, ident):
    """The rows a kit or a bundle opens to (its members)."""
    by_id = rows_by_id(data)
    owner = by_id.get(ident)
    members = []
    if owner:
        members = owner.get('members') or []
    else:
        for bundle in data.get('bundles') or []:
            if bundle.get('id') == ident:
                members = bundle.get('members') or []
    out = []
    for member in members:
        mid = member.get('id') if isinstance(member, dict) else member
        if by_id.get(mid):
            out.append(by_id[mid])
    return out


_WARMING = {}


def _posters(full, limit=8):
    """A list's first posters: from the page, else from its list saved on disk
    (no call here: the Home's rows are being worked out)."""
    items = full.get('items') or []
    if not items:
        language = lang()
        key = 'row_%s_%s_1.json' % (language, str(full.get('id') or '').replace(':', '_').replace('/', '_'))
        items = ((_read_file(_cache_path(key)) or {}).get('data') or {}).get('items') or []
    return [i.get('poster') for i in items if i.get('poster')][:limit]


def _warm(ids):
    """The lists not saved yet are read in the background (once in half an hour),
    so the next publish has their posters."""
    now = time.time()
    todo = [i for i in ids if now - _WARMING.get(i, 0) > 1800]
    if not todo:
        return
    for i in todo:
        _WARMING[i] = now

    def run():
        for ident in todo:
            try:
                row_page(ident, 1)
            except Exception:
                pass
    t = threading.Thread(target=run, name='DexHub-dexworld-warm')
    t.daemon = True
    t.start()


def kit_members(row_id):
    """A collection's lists: [{id, title, cover, items?, ...}] ([] when it is a list)."""
    by_id = rows_by_id(page())
    owner = by_id.get(str(row_id or '')) or {}
    out = []
    for member in owner.get('members') or []:
        ident = member.get('id') if isinstance(member, dict) else member
        full = dict(by_id.get(ident) or {})
        if isinstance(member, dict):
            full.update(dict((k, v) for k, v in member.items() if v))
        if full.get('id') and full.get('ready', True):
            out.append(full)
    return out


def fold_url(kit_id, member_id, title=''):
    """The collection page (skinui.folderpage) for a list of a collection."""
    from urllib.parse import urlencode
    return 'plugin://plugin.video.dexhub/?' + urlencode({'action': 'skin_fold', 'dw': kit_id, 'dwm': member_id, 't': title})


NETWORK_ID = re.compile(r'^b:dxk-net-(\d+)$')
# a network's page (v5.10.507): everything TMDb has on it, its genres after
NETWORK_ROWS = (('popular', '', '', 'الأشهر', 'Most popular'),
                ('rated', '', '', 'الأعلى تقييماً', 'Top rated'),
                ('new', '', '', 'الأحدث', 'Newest'),
                ('', '', 'airing', 'يُعرض الآن', 'On the air now'),
                ('', '18', '', 'دراما', 'Drama'),
                ('', '35', '', 'كوميديا', 'Comedy'),
                ('', '80', '', 'جريمة', 'Crime'),
                ('', '10765', '', 'خيال علمي وفانتازيا', 'Sci-fi and fantasy'),
                ('', '9648', '', 'غموض', 'Mystery'),
                ('', '99', '', 'وثائقي', 'Documentary'),
                ('', '16', '', 'رسوم متحركة', 'Animation'))


def _t(ar, en):
    return en if lang() == 'en' else ar


_LOGOS = {}


def network_logo(tid, fetch=False):
    """A network's clear logo from TMDb ('' unknown). A dark, colourless one
    (HBO, AMC: black on clear) is TMDb's inverted copy, a coloured one as it
    is (png_tone). Kept on disk; without ``fetch`` (the Home's rows being
    worked out) an unknown one is looked up in the background."""
    tid = str(tid or '')
    if not tid.isdigit():
        return ''
    if tid in _LOGOS:
        return _LOGOS[tid]
    path = _cache_path('logos.json')
    stored = _read_file(path) or {}
    if tid in stored and time.time() - float(stored[tid].get('at') or 0) < 30 * 86400:
        _LOGOS[tid] = stored[tid].get('url') or ''
        return _LOGOS[tid]
    if not fetch:
        _warm_logo(tid)
        return ''
    url = ''
    try:
        from . import tmdb_direct as T
        from . import png_tone
        from urllib.request import Request, urlopen
        logos = (T._request('/network/%s/images' % tid, {}, timeout=8, max_attempts=2) or {}).get('logos') or []
        if logos:
            file_path = logos[0].get('file_path') or ''
            with urlopen(Request('https://image.tmdb.org/t/p/w300%s' % file_path, headers={'User-Agent': 'Dex Hub'}),
                         timeout=10) as answer:
                found = png_tone.tone(answer.read())
            dark = bool(found and found[0] < 0.35 and found[1] < 0.25)
            url = 'https://image.tmdb.org/t/p/w500%s%s' % ('_filter(negate,000,666)' if dark else '', file_path)
    except Exception as exc:
        _log('network logo %s: %s' % (tid, exc.__class__.__name__))
        return ''
    _LOGOS[tid] = url
    with _lock:
        stored = _read_file(path) or {}
        stored[tid] = {'url': url, 'at': time.time()}
        _write_file(path, stored)
    return url


def _warm_logo(tid):
    if time.time() - _WARMING.get('logo:' + tid, 0) < 1800:
        return
    _WARMING['logo:' + tid] = time.time()

    def run():
        # the Home's rows again once the logo is in (one ask for several logos:
        # the worker waits a second for more)
        if network_logo(tid, True):
            try:
                from .skinui import common as C
                C.ask_republish('dexworld logos')
            except Exception:
                pass
    t = threading.Thread(target=run, name='DexHub-dexworld-logo')
    t.daemon = True
    t.start()


def network_sources(member):
    """A network (TMDb's number in its id): DexWorld's list of it, then TMDb's rows on it."""
    found = NETWORK_ID.match(str(member.get('id') or ''))
    if not found:
        return []
    tid = found.group(1)
    name = member.get('title') or ''
    out = [('dw:%s' % member['id'], _t('مختارات DexWorld', "DexWorld's picks"), name,
            {'action': 'dexworld_row', 'id': member['id'], 'title': name})]
    for sort, genre, when, ar, en in NETWORK_ROWS:
        route = {'action': 'tmdb_titles', 'mode': 'network', 'id': tid, 'media': 'series', 'name': name}
        for k, v in (('sort', sort), ('genre', genre), ('when', when)):
            if v and v != 'popular':
                route[k] = v
        out.append(('net:%s:%s:%s:%s' % (tid, sort, genre, when), _t(ar, en), name, route))
    return out


def fold_sources(kit_id, first=''):
    """The collection page's rows: the list opened first, then the collection's others;
    a network opens as everything about it (network_sources)."""
    members = kit_members(kit_id)
    members.sort(key=lambda m: m.get('id') != first)
    if members and members[0].get('id') == first:
        network = network_sources(members[0])
        if network:
            return network, members
    return [('dw:%s' % m['id'], m.get('title') or '', m.get('meta') or '',
             {'action': 'dexworld_row', 'id': m['id'], 'title': m.get('title') or ''}) for m in members], members


def kit_tiles(row_id):
    """A collection (a kit: TV Networks, Documentaries) as cover cards for a Home
    collection row (v5.10.505), each opening its list; [] when it is a list."""
    owner = rows_by_id(page()).get(str(row_id or '')) or {}
    members = kit_members(row_id)
    if not members:
        return []
    from urllib.parse import urlencode
    tiles = []
    missing = []
    for full in members:
        first = (full.get('items') or [{}])[0] or {}
        wall = _posters(full)
        if not wall:
            missing.append(full['id'])
        network = NETWORK_ID.match(str(full.get('id') or ''))
        logo = network_logo(network.group(1)) if network else ''
        cover = full.get('cover') or first.get('backdrop') or first.get('poster') or ''
        path = 'plugin://plugin.video.dexhub/?' + urlencode({'action': 'dexworld_row', 'id': full['id'],
                                                             'title': full.get('title') or ''})
        # catalog_node: OK opens the list as a grid; catalog_banner: drawn as its
        # cover, not as the catalog folders' icon card (blank for these)
        tiles.append({'kind': 'folder', 'catalog_node': True, 'catalog_banner': True, 'folder': True, 'path': path,
                      'label': full.get('title') or '', 'title': full.get('title') or '',
                      'hide_title': bool(full.get('cover')), 'shape': 'landscape',
                      'poster': first.get('poster') or cover, 'landscape': cover,
                      'fanart': first.get('backdrop') or cover, 'clearlogo': logo,
                      'group_title': owner.get('title') or '', 'group_fanart': '',
                      # v5.10.507: its titles' posters behind the page, and OK opens
                      # the collection page (this list first, the others after)
                      'wall': wall, 'fold_url': fold_url(row_id, full['id'], full.get('title') or ''),
                      'plot': full.get('desc') or full.get('meta') or '', 'menu': []})
    if missing:
        _warm(missing)
    return tiles


def spec_of(row):
    """A bar section's catalog for a block (bar_sections spec)."""
    style = 'ranked' if row.get('style') == 'ranked' else ('landscape' if row.get('kind') == 'kit' or row.get('members') else '')
    return {'key': 'dexworld:%s' % row.get('id'), 'title': row.get('title') or '', 'sub': 'DexWorld',
            'shape': 'poster', 'style': style,
            'params': {'action': 'dexworld_row', 'id': row.get('id') or '', 'title': row.get('title') or ''}}


def add_to_section(rows, name=None, slot=None, icon=''):
    """Adds blocks to a section of the bar (an own section, made when slot is None). Returns its name or ''."""
    from . import bar_sections as B
    data = B.read()
    custom = data.get('custom') or []
    if slot is None:
        if len(custom) >= len(B.CUSTOM):
            return ''
        custom.append({'name': name or 'DexWorld', 'icon': icon or 'discover', 'rows': []})
        slot = len(custom) - 1
    section = custom[slot]
    have = set(spec.get('key') for spec in section.get('rows') or [])
    added = 0
    for row in rows:
        spec = spec_of(row)
        if spec['key'] in have:
            continue
        section.setdefault('rows', []).append(spec)
        have.add(spec['key'])
        added += 1
    section['rows'] = (section.get('rows') or [])[:B.MAX_ROWS]
    data['custom'] = custom
    B.write(data)
    B._republish(slot)
    return section.get('name') or ''
