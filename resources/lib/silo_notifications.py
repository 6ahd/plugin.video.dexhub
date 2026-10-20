"""Silo's profile inbox projected into Dex Hub's island.

Kodi does not receive Apple APNs. Fetch the native inbox in a background
worker; the island only reads a local snapshot. Read receipts survive restarts
in an owner-scoped outbox and never borrow another profile's credentials.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import time
from urllib.parse import urlencode
from .profile_scope import ADDON_ID


def _provider():
    from .providers import silo_provider
    return silo_provider


def owner(auth):
    parts = [str(auth.get(k) or '') for k in ('url', 'account_id', 'profile_id')]
    if not parts[0] or not parts[2] or not auth.get('token'):
        return ''
    parts[0] = parts[0].rstrip('/')
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _db():
    from . import profile_scope
    root = profile_scope.root()
    os.makedirs(root, exist_ok=True)
    conn = sqlite3.connect(os.path.join(root, 'silo_inbox.db'), timeout=5)
    conn.execute('CREATE TABLE IF NOT EXISTS inbox (owner TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    return conn


def _update(key, change):
    conn = _db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT payload FROM inbox WHERE owner=?', (key,)).fetchone()
        state = json.loads(row[0]) if row else {}
        change(state)
        conn.execute('INSERT OR REPLACE INTO inbox(owner,payload) VALUES(?,?)',
                     (key, json.dumps(state, ensure_ascii=False)))
        conn.commit()
        return state
    finally:
        conn.close()


def _read(key):
    conn = _db()
    try:
        row = conn.execute('SELECT payload FROM inbox WHERE owner=?', (key,)).fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        conn.close()


def _current(auth):
    return owner(auth) == owner(_provider().account() or {})


def project(row, key, auth):
    """Use only known native targets; never execute a server-supplied action."""
    nid = str(row.get('id') or '')
    profile = str(row.get('profile_id') or '')
    if not nid or (profile and profile != str(auth.get('profile_id') or '')) or row.get('read_at'):
        return None
    episode, series = str(row.get('episode_id') or ''), str(row.get('series_id') or '')
    flags = row.get('reason_flags') if isinstance(row.get('reason_flags'), dict) else {}
    item_id = episode or series
    action = 'silo_series' if series and not episode and flags.get('media_type') != 'movie' else 'silo_play'
    params = {'action': action, 'series_id' if action == 'silo_series' else 'item_id': item_id}
    # Request/account notices without a playable target open Silo's page.
    if not item_id:
        params = {'action': 'silo_menu'}
    title = str(row.get('series_title') or row.get('episode_title') or 'Silo')
    detail = str(row.get('episode_title') or '')
    if str(row.get('type') or '').startswith('request.'):
        title = str(row.get('series_title') or flags.get('title') or title)
        detail = str(flags.get('reason') or detail)
    if row.get('episode_number') is not None:
        detail = 'S%s:E%s  ·  %s' % (row.get('season_number') or 0, row['episode_number'], detail)
    try:
        detected = datetime.datetime.fromisoformat(str(row.get('created_at')).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        detected = time.time()
    # Unknown notification types remain visible without assuming they contain
    # the fields of a new-episode notification.
    return {'kind': 'silo', 'key': 'silo|%s|%s' % (key, nid), 'silo_id': nid, 'silo_owner': key,
            'kicker': _caption(str(row.get('type') or '')), 'title': title, 'sub': detail, 'detected_at': detected,
            'poster': _provider()._media_url(auth, row.get('poster_url') or ''),
            'folder': params['action'] in ('silo_series', 'silo_menu'),
            'action': 'plugin://' + ADDON_ID + '/?' + urlencode(params)}


def notices():
    auth = _provider().account() or {}
    key = owner(auth)
    if not key:
        return []
    try:
        state = _read(key)
        pending = set(state.get('pending') or [])
        if state.get('pending_all'):
            return []
        return [item for item in (project(row, key, auth) for row in state.get('items') or [])
                if item and item['silo_id'] not in pending]
    except (OSError, ValueError, sqlite3.Error):
        return []


def mark_read(items, all_visible=False):
    auth = _provider().account() or {}
    key = owner(auth)
    if not key:
        return
    ids = {str(item.get('silo_id')) for item in items
           if item.get('silo_owner') == key and item.get('silo_id')}
    if not ids:
        return
    def change(state):
        state['pending'] = sorted(set(state.get('pending') or []) | ids)
        if all_visible and state.get('read_cutoff'):
            state['pending_all'] = state['read_cutoff']
        state['refresh_after'] = 0
    _update(key, change)
    _changed()


def _changed():
    from . import island
    island.changed()
    try:
        from .skinui import panel
        panel.refresh_alerts()
    except Exception:
        # A panel refresh must never prevent inbox/read-outbox processing.
        pass


def sync(auth=None, interval=60):
    provider = _provider()
    auth = dict(auth or provider.account() or {})
    key = owner(auth)
    if not key or not _current(auth):
        return False
    state = _read(key)
    if time.time() < float(state.get('refresh_after') or 0):
        return False
    major = provider.api_major(auth)
    # These native routes also exist on the v1 bridge. Preserve the captured
    # version for this batch; never switch versions halfway through receipts.
    def api(path, **kw):
        if not _current(auth):
            raise RuntimeError('Silo profile changed')
        value = provider._api(path, auth=auth, major=major, **kw)
        if not _current(auth):
            raise RuntimeError('Silo profile changed')
        return value
    pending_all = state.get('pending_all')
    if pending_all:
        # v1 read-all has no snapshot token. Avoid marking a new event which
        # arrived after the viewer opened the inbox: send individual receipts.
        if major == 2:
            api('notifications/read-all', method='POST', data={'through': pending_all})
            def ack_all(current):
                if current.get('pending_all') == pending_all:
                    current.pop('pending_all', None)
                    current['pending'] = [i for i in current.get('pending') or []
                                          if i not in set(state.get('pending') or [])]
            _update(key, ack_all)
        else:
            pending_all = None
    if not pending_all:
        for nid in state.get('pending') or []:
            try:
                api('notifications/%s/read' % _segment(nid), method='POST')
            except provider.SiloError as exc:
                if exc.status != 404:
                    raise
            def ack(current, done=nid):
                current['pending'] = [i for i in current.get('pending') or [] if i != done]
                if not current['pending']:
                    current.pop('pending_all', None)
            _update(key, ack)
    items, cursor, seen = [], '', set()
    cutoff = ''
    # Bound island data to the newest 200. The native watchlist/catalog still
    # pages independently; fetching the island never walks an entire library.
    for _ in range(4):
        params = {'status': 'unread', 'limit': 50}
        if cursor:
            params['cursor'] = cursor
        page = api('notifications', params=params)
        rows = page.get('items') or page.get('notifications') or []
        items.extend(row for row in rows if isinstance(row, dict))
        cutoff = cutoff or str(page.get('read_cutoff') or '')
        paging = page.get('page') or {}
        cursor = str(paging.get('next_cursor') or '')
        if not paging.get('has_more'):
            break
        if not cursor or cursor in seen:
            raise RuntimeError('Invalid Silo notification cursor')
        seen.add(cursor)
    def publish(current):
        current.update(items=items, read_cutoff=cutoff, refreshed_at=time.time(), refresh_after=time.time() + interval)
    if _current(auth):
        _update(key, publish)
        _changed()
        return True
    return False


def _segment(value):
    from urllib.parse import quote
    return quote(str(value), safe='')


def _caption(kind):
    from .i18n import is_english
    labels = {'episode.available': ('Silo · حلقة جديدة متوفرة', 'Silo · New episode available'),
              'request.fulfilled': ('Silo · أصبح العمل متوفرًا', 'Silo · Title now available'),
              'request.approved': ('Silo · تمت الموافقة على الطلب', 'Silo · Request approved'),
              'request.declined': ('Silo · رُفض الطلب', 'Silo · Request declined')}
    return labels.get(kind, ('Silo · تنبيه', 'Silo · Notification'))[1 if is_english() else 0]


def settings():
    import xbmcgui
    from .silo_social import Client, _t
    try:
        client = Client()
        fields = [('enabled', _t('تشغيل تنبيهات Silo', 'Enable Silo notifications')),
                  ('notify_watchlist', _t('قائمة المشاهدة', 'Watchlist')),
                  ('notify_favorites', _t('المفضلة', 'Favorites')),
                  ('notify_continue_watching', _t('متابعة المشاهدة', 'Continue watching')),
                  ('notify_next_up', _t('الحلقة التالية', 'Next up'))]
        while True:
            prefs = client.api('notifications/preferences')
            pick = xbmcgui.Dialog().select(_t('تنبيهات Silo لهذا البروفايل', 'Silo notifications for this profile'),
                                           [('✓ ' if prefs.get(k) else '○ ') + label for k, label in fields])
            if pick < 0:
                return
            field = fields[pick][0]
            client.api('notifications/preferences', method='PUT', data={field: not bool(prefs.get(field))})
            key = owner(client.auth)
            _update(key, lambda state: state.update(refresh_after=0))
    except Exception as exc:
        xbmcgui.Dialog().notification('Silo', str(exc), xbmcgui.NOTIFICATION_ERROR)


def run(monitor):
    failures = 0
    last_owner = None
    while not monitor.abortRequested():
        try:
            auth = _provider().account() or {}
            key = owner(auth)
            if key != last_owner:
                _changed()
                last_owner = key
            if key:
                import xbmc
                sync(auth, interval=180 if xbmc.getCondVisibility('Player.HasVideo') else 60)
            failures = 0
            delay = 10  # local outbox check; sync() gates ordinary HTTP to 60s
        except Exception:
            failures += 1
            delay = min(600, 30 * 2 ** min(failures - 1, 4))
        if monitor.waitForAbort(delay):
            return
