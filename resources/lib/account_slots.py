# -*- coding: utf-8 -*-
"""More than one account per media server service (v5.10.605; the user, 23 Nov 2026:
"add the ability to add another Silo, Plex, Emby... account").

The first account of a service stays where it always was (the `<service>_auth_json`
setting and everything built on it). Every further account is a "slot": its own sign-in
and, for Plex, its own list of servers, kept in addon_data/server_accounts.json.

The clients read and write their account through one setting name. While a slot is in use
(`with use(service, slot):`, per thread) that name means the slot's copy instead, so the
sign-in flows, Silo's session renewal and Plex's server discovery work for a slot exactly
as they do for the first account, with no second code path.

Every server an extra account reaches carries `slot` in its dict; a call made for such a
server enters its slot (`for_server`). A request that cannot tell (an old source row, a
thread started without it) uses the first account, and Silo refuses a mismatched account
instead of mixing them up.
"""
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager

SERVICES = ('plex', 'emby', 'jellyfin', 'silo')
FILE = 'server_accounts.json'
_local = threading.local()
_lock = threading.RLock()
# the setting names a slot keeps its own copy of
_NAMES = {'auth_json': 'auth', 'servers_cache_json': 'servers_cache'}


def _path():
    try:
        import xbmcvfs
        folder = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub')
    except Exception:
        folder = os.path.join(os.path.expanduser('~'), '.dexhub')
    return os.path.join(folder, FILE)


def _read():
    try:
        with open(_path(), encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data):
    path = _path()
    tmp = path + '.tmp'
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)
    except Exception:
        pass


def _mark_changed(service):
    try:
        from . import server_accounts
        server_accounts.changed(service)
    except Exception:
        pass


# ---------------------------------------------------------------- the active slot
def current(service):
    """The slot in use on this thread for `service` ('' for the first account)."""
    return (getattr(_local, 'slots', None) or {}).get(service, '')


@contextmanager
def use(service, slot):
    slots = dict(getattr(_local, 'slots', None) or {})
    before = slots.get(service, '')
    slots[service] = slot or ''
    _local.slots = slots
    try:
        yield
    finally:
        slots = dict(getattr(_local, 'slots', None) or {})
        slots[service] = before
        _local.slots = slots


def split(name):
    """('silo', 'auth') for 'silo_auth_json' while a silo slot is in use, else None."""
    name = str(name or '')
    service, _sep, rest = name.partition('_')
    if service not in SERVICES or rest not in _NAMES:
        return None
    slot = current(service)
    return (service, slot, _NAMES[rest]) if slot else None


# ---------------------------------------------------------------- the store
def slots(service):
    """[{'slot', 'auth', 'added'}] of the extra accounts, in the order they were added."""
    rows = _read().get(service) or []
    return [r for r in rows if isinstance(r, dict) and r.get('slot')]


def ids(service):
    return [r['slot'] for r in slots(service)]


def read(service, slot, key, default=None):
    for row in slots(service):
        if row.get('slot') == slot:
            value = row.get(key)
            return value if value is not None else default
    return default


def write(service, slot, key, value):
    with _lock:
        data = _read()
        rows = [r for r in (data.get(service) or []) if isinstance(r, dict)]
        for row in rows:
            if row.get('slot') == slot:
                row[key] = value
                break
        else:
            rows.append({'slot': slot, 'added': int(time.time()), key: value})
        data[service] = rows
        _write(data)
    if key == 'auth':
        _mark_changed(service)


def new_slot():
    return uuid.uuid4().hex[:8]


def linked(service, slot):
    auth = read(service, slot, 'auth') or {}
    return bool(auth.get('token'))


def remove(service, slot):
    with _lock:
        data = _read()
        data[service] = [r for r in (data.get(service) or []) if isinstance(r, dict) and r.get('slot') != slot]
        _write(data)
    _mark_changed(service)


def drop_unlinked(service):
    """A slot whose sign-in was cancelled leaves nothing behind."""
    with _lock:
        data = _read()
        rows = [r for r in (data.get(service) or []) if isinstance(r, dict)]
        keep = [r for r in rows if (r.get('auth') or {}).get('token')]
        if len(keep) != len(rows):
            data[service] = keep
            _write(data)


def promote(service):
    """The first account was removed: the oldest extra account takes its place.
    Returns its auth (the caller saves it as the first account) or {}."""
    with _lock:
        data = _read()
        rows = [r for r in (data.get(service) or []) if isinstance(r, dict) and (r.get('auth') or {}).get('token')]
        if not rows:
            return {}
        first = rows.pop(0)
        data[service] = rows
        _write(data)
    return dict(first.get('auth') or {})


# ---------------------------------------------------------------- servers
def _base(url):
    return str(url or '').strip().rstrip('/').lower()


def find(service, server):
    """The slot that owns `server` (a server dict, a session's auth or a source's hints):
    its own `slot`, else the extra account with the same address and account/token."""
    server = server or {}
    if server.get('slot'):
        return server['slot']
    url = _base(server.get('url') or server.get('server_url'))
    token = str(server.get('token') or server.get('access_token') or '')
    who = str(server.get('account_id') or '')
    user = str(server.get('user_id') or server.get('profile_id') or '')
    for row in slots(service):
        auth = row.get('auth') or {}
        if url and _base(auth.get('url')) != url:
            continue
        if token and token in (str(auth.get('token') or ''), str(auth.get('access_token') or '')):
            return row['slot']
        if who and who == str(auth.get('account_id') or ''):
            return row['slot']
        if url and user and user in (str(auth.get('user_id') or ''), str(auth.get('profile_id') or '')):
            return row['slot']
    return ''


@contextmanager
def for_server(service, server):
    with use(service, find(service, server)):
        yield


def label(service, auth):
    auth = auth or {}
    who = (auth.get('account_name') or auth.get('username') or auth.get('profile_name') or '').strip()
    where = (auth.get('server_name') or '').strip()
    return ' • '.join(x for x in (who, where) if x) or service.title()
