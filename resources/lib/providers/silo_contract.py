"""Silo wire compatibility, based on silo-apple APIv2Probe/catalog contracts.

No credentials or response bodies are logged. A failed v2 request is never
replayed against v1. Legacy selection is permitted only by Go's exact 404.
"""
import json
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode

_lock = threading.RLock()
_servers = {}
_cursors = {}


class ContractError(Exception):
    pass


def version(root, raw):
    with _lock:
        cached = _servers.get(root)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
    try:
        payload = raw(root + '/api/v2/system/info', profile=False, retry=False)
    except HTTPError as exc:
        # Reverse-proxy HTML, auth failures and outages are not old servers.
        if exc.code != 404 or exc.read() not in (b'404 page not found', b'404 page not found\n'):
            raise
        major = 1
    else:
        info = json.loads(payload)
        if not isinstance(info, dict) or info.get('api_major') != 2 or not info.get('contract_digest'):
            raise ContractError('رد هوية Silo غير صالح؛ تحقق من عنوان السيرفر')
        major = 2
    with _lock:
        _servers[root] = (time.monotonic(), major)
    return major


def complete(value):
    """Never publish a finite but incomplete profile/library list as complete."""
    page = value.get('page') or {}
    if page.get('has_more') or page.get('next_cursor'):
        raise ContractError('قائمة Silo غير مكتملة؛ أعد تحميلها')
    return list(value.get('items') or [])


def catalog_params(params):
    params = dict(params or {})
    params.pop('offset', None)
    params.pop('include_total', None)
    field = params.get('sort')
    order = params.pop('order', 'desc')
    if field:
        field = {'rating_imdb': 'rating'}.get(field, field)
        params['sort'] = ('-' if order == 'desc' else '') + field
    rules = {}
    for key in list(params):
        match = re.fullmatch(r'groups\[0\]\[rules\]\[(\d+)\]\[(field|op|value)\]', key)
        if match:
            rules.setdefault(int(match[1]), {})[match[2]] = params.pop(key)
    group_match = params.pop('groups[0][match]', 'all')
    if rules:
        params['groups'] = json.dumps([{'match': group_match, 'rules': [rules[k] for k in sorted(rules)]}],
                                     separators=(',', ':'))
    return params


def request(root, path, method, data, params, auth, raw, decode):
    """Adapt existing Kodi projections without weakening the v2 wire contract."""
    path = path.strip('/')
    params = dict(params or {})
    if path == 'catalog':
        return _catalog(root, method, params, auth, raw, decode)
    url = root + '/api/v2/' + path
    if params:
        url += '?' + urlencode(params, doseq=True)
    value = decode(raw(url, method=method, data=data, auth=auth))
    if not isinstance(value, dict):
        raise ContractError('رد Silo غير صالح')
    if path == 'auth/device/poll' and value.get('status') == 'approved':
        value = dict(value, **(value.get('tokens') or {}))
    if path == 'profiles':
        return dict(value, profiles=complete(value))
    if path == 'user/libraries':
        return dict(value, libraries=complete(value))
    if path == 'collections':
        return dict(value, collections=complete(value))
    if re.fullmatch(r'catalog/series/[^/]+/seasons', path):
        return dict(value, seasons=complete(value))
    if path.endswith('/episodes') and path.startswith('catalog/'):
        return dict(value, episodes=complete(value))
    if path.endswith('/versions') and path.startswith('catalog/items/'):
        return dict(value, versions=complete(value))
    return value


def _catalog(root, method, original, auth, raw, decode):
    offset = max(0, int(original.get('offset') or 0))
    limit = min(100, max(1, int(original.get('limit') or 60)))
    params = catalog_params(original)
    params['limit'] = limit
    # A cursor belongs to exactly one server, account, profile and query.
    scope = (root, str(auth.get('account_id') or ''), str(auth.get('profile_id') or ''),
             str(auth.get('token') or ''), str(auth.get('profile_token') or ''),
             json.dumps(params, sort_keys=True))
    with _lock:
        cached = _cursors.get(scope)
        positions = dict(cached[1]) if cached and time.monotonic() - cached[0] < 300 else {0: ''}
        if offset == 0:
            positions = {0: ''}
    # Kodi's numbered pages are projected onto the server's opaque cursors.
    # On cache loss, walk from the closest known point without changing query.
    start = max((n for n in positions if n <= offset), default=0)
    cursor = positions.get(start, '')
    for _ in range(100):
        query = dict(params)
        if cursor:
            query['cursor'] = cursor
        value = decode(raw(root + '/api/v2/catalog?' + urlencode(query), auth=auth))
        if not isinstance(value, dict) or not isinstance(value.get('items'), list):
            raise ContractError('صفحة Silo غير صالحة')
        rows = value['items']
        page = value.get('page') or {}
        more = bool(page.get('has_more'))
        next_cursor = str(page.get('next_cursor') or '')
        if more and (not next_cursor or next_cursor == cursor or not rows):
            raise ContractError('مؤشر صفحات Silo غير صالح')
        positions[start + len(rows)] = next_cursor
        if start == offset:
            with _lock:
                if len(_cursors) >= 64:
                    _cursors.clear()
                _cursors[scope] = (time.monotonic(), positions)
            return dict(value, has_more=more)
        if not more or start + len(rows) > offset:
            raise ContractError('تغير ترتيب صفحات Silo؛ أعد فتح القائمة')
        start += len(rows)
        cursor = next_cursor
    raise ContractError('قائمة Silo أكبر من حد التصفح الآمن')
