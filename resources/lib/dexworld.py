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


def spec_of(row):
    """A bar section's catalog for a block (bar_sections spec)."""
    style = 'ranked' if row.get('style') == 'ranked' else ''
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
