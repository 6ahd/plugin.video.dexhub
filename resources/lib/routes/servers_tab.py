# -*- coding: utf-8 -*-
"""The skin's Servers tab, built like My Library (v5.10.157).

skin.dexhub 3.22: the Servers button opens a Home tab of its own ("servers")
instead of the separate Servers window. Its header is the Library's: a
line with the linked brands, the title, four numbers and a row of chips.
The chips are the servers themselves (All, then every linked Plex, Emby,
Jellyfin or Silo server), where the Library's chips are its services.

  All          what is being watched and what was added lately on every
               server, then each server's libraries as cards
  one server   its continue row, recently added, its libraries as cards,
               one row of posters per library, Silo's own Home sections

The rows are ordinary Home rows: the service reads them on demand like any
other (only the tab on show reads ahead), so a server is not asked for
anything until the tab is opened.

The list of servers and their libraries is kept in skin/servers_tab.json
(six hours); a stale list is read again in the background and the tab is
published again when it changed, so publishing never waits on a server.
"""
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

from ..i18n import is_english


def _setting(key):
    """A Dex Hub setting through the short-lived cache (v5.10.184: each Home
    publish made a new Addon object per read, 10 to 30 ms each while Kodi
    draws)."""
    try:
        from ..settings_cache import cached_addon
        return cached_addon().getSetting(key)
    except Exception:
        import xbmcaddon
        return xbmcaddon.Addon('plugin.video.dexhub').getSetting(key)

STALE = 6 * 3600.0
# v5.10.198: a list read before every server's libraries answered (a server
# linked a moment ago, a Plex still picking its connection) is read again
# soon, not six hours later
RETRY = (45.0, 90.0, 180.0, 600.0)
# the background read waits longer than a page would for a server's menu
PATIENCE = 12.0
MAX_CHIPS = 8               # All plus seven servers (skin.dexhub's 8 slots; v5.10.180: all of them)
TONES = {'plex': 'FFE5A00D', 'emby': 'FF52B54B', 'jellyfin': 'FFAA5CC3', 'silo': 'FF3FB0F0'}
_AUTH_FIELDS = ('url', 'user_id', 'profile_id', 'username', 'token')
_refreshing = threading.Lock()
_retry_at = 0.0
_retry_owner = ''


def _account_key():
    """Local account identity; never put credentials or their values in logs.

    v5.10.198: who is linked where, from each account's own fields (and its
    window copy when the setting reads empty in this interpreter): the same
    account written again with other spacing keeps its key and its servers."""
    import hashlib
    import json
    parts, linked = [], False
    for service in ('plex', 'emby', 'jellyfin', 'silo'):
        raw = _setting('%s_auth_json' % service) or ''
        if not raw:
            try:
                raw = _win().getProperty('dexhub.%s.auth' % service) or ''
            except Exception:
                raw = ''
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        ident = '|'.join(str(data.get(k) or '') for k in _AUTH_FIELDS)
        linked = linked or bool(ident.strip('|'))
        parts.append(ident)
    if not linked:
        parts = ['', '', '', '']          # no account: the same key as before 5.10.198
    return hashlib.sha256('\0'.join(parts).encode('utf-8')).hexdigest()


def _t(ar, en):
    return en if is_english() else ar


def _win():
    return xbmcgui.Window(10000)


def _cache_path():
    from ..skinui import common as C
    import os
    return os.path.join(C.folder(), 'servers_tab.json')


def _load():
    from ..skinui import common as C
    return C.read_json(_cache_path()) or {}


def _save(data):
    from ..skinui import common as C
    C.write_json(_cache_path(), data)


# ---------------------------------------------------------------- discovery
def _discover(on_found=None):
    """{'t', 'servers': [{service, brand, id, name, key, libs: [{title, params}], sections}]}"""
    from ..homeui import servers as S
    from ..skinui import rows as SR
    app = SR.app()
    api = app.api()
    out = []
    # v5.10.180: in the background Plex's account list gets longer to answer
    # (a slow plex.tv left servers out), and the servers the last list had
    # stay when a service answers with nothing this time
    found = S.linked_servers(api, plex_budget=12.0)
    signed = set(service for service, _brand, client in S.SERVICES if S._signed_in(api, client))
    saved = _load()
    known = [s for s in (saved.get('servers') or []) if s.get('service') in signed] \
        if saved.get('account') == _account_key() else []
    found = S.merge_known(found, [{k: s.get(k) for k in ('service', 'brand', 'id', 'name')} for s in known])
    if on_found:
        on_found([dict(s, key=S.server_key(s), libs=[], sections=[], tiles=[]) for s in found])
    # every server's libraries side by side (they were read one after another)
    results = {}

    def read(server):
        results[S.server_key(server)] = _server_entry(server, api, app)
    threads = [threading.Thread(target=read, args=(s,), name='dexhub-servers-libs') for s in found]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(2 * PATIENCE + 6.0)
    incomplete = not found and bool(signed)
    for server in found:
        entry = results.get(S.server_key(server))
        old = next((s for s in known if s.get('key') == S.server_key(server)), None)
        if entry is None:
            entry = dict(old) if old else dict(server, key=S.server_key(server), libs=[], sections=[], tiles=[])
        elif old and not entry.get('libs') and old.get('libs'):
            # its libraries did not answer this time: the last ones stay
            entry.update({'libs': old.get('libs'), 'tiles': old.get('tiles') or [],
                          'sections': entry.get('sections') or old.get('sections') or []})
        if not entry.get('libs') and not entry.get('sections'):
            incomplete = True
        out.append(entry)
    return {'t': time.time(), 'servers': out, 'incomplete': incomplete}


def _server_entry(server, api, app):
    from .. import capture
    from ..homeui import servers as S
    if server:
        entry = dict(server, key=S.server_key(server), libs=[], sections=[], tiles=[])
        try:
            entries, _elapsed = capture.run_route(S.menu_params(server), api, patience=PATIENCE)
            tiles = S.library_tiles(entries or [], server, app.media_path)
            hint = _t('افتحها لعرض كل ما فيها', 'Open it to see everything in it')
            for tile in tiles:
                tile['plot'] = hint
            entry['tiles'] = tiles
            entry['libs'] = [{'title': t['title'], 'params': t['params']} for t in tiles]
        except Exception as exc:
            xbmc.log('[DexHub] servers tab: libraries of %s: %s' % (server['brand'], exc.__class__.__name__),
                     xbmc.LOGINFO)
        if server['service'] == 'silo':
            try:
                found, _elapsed = capture.run_route({'action': 'silo_home'}, api, patience=PATIENCE)
                entry['sections'] = [[title, params] for title, params in S.section_rows(found)]
            except Exception:
                pass
        return entry


def _shape(data):
    """What the tab is built from, without the time (to see a change)."""
    import json
    return json.dumps([[s.get('key'), s.get('name'), s.get('libs'),
                        s.get('sections'), s.get('tiles')] for s in data.get('servers') or []],
                      sort_keys=True)


def _refresh_later(force=False):
    """Read the servers again in the background; publish the tab when it changed.

    force (v5.10.198): an account was just linked or removed, so the 30 s
    pause between two reads does not apply. True when a read started."""
    global _retry_at, _retry_owner
    account = _account_key()
    if account != _retry_owner:
        _retry_owner, _retry_at = account, 0.0
    if force:
        _retry_at = 0.0
    if time.time() < _retry_at or not _refreshing.acquire(False):
        return False
    _retry_at = time.time() + 30.0

    def run():
        try:
            old = _load()
            same = old.get('account') == account

            def first(servers):
                if servers and _account_key() == account and (not same or not old.get('servers')):
                    _save({'account': account, 't': 0, 'servers': servers, 'incomplete': True})
                    from ..skinui import common as C
                    C.ask_republish('servers source')
            new = _discover(on_found=first)
            if _account_key() != account:
                return                    # an old account must not replace a new one
            new['account'] = account
            if not new.get('servers') and old.get('servers') and same:
                # temporary discovery failure: keep the last good copy
                old['checked'] = time.time()
                _save(old)
                return
            # v5.10.198: a read some server did not answer is read again
            # soon, a little later each time (RETRY)
            new['tries'] = (int(old.get('tries') or 0) + 1 if same else 1) if new.get('incomplete') else 0
            new['checked'] = time.time()
            _save(new)
            if (_shape(new) != _shape(old) or not same or bool(new.get('incomplete')) != bool(old.get('incomplete'))
                    or new['tries'] == 2):          # the waiting row's words change after two reads
                from ..skinui import common as C
                C.ask_republish('servers source')
            if new.get('incomplete'):
                xbmc.log('[DexHub] servers tab: %d server(s) found, not every library read yet (try %d)'
                         % (len(new.get('servers') or []), new['tries']), xbmc.LOGINFO)
        except Exception as exc:
            xbmc.log('[DexHub] servers tab refresh: %s' % exc.__class__.__name__, xbmc.LOGINFO)
        finally:
            _refreshing.release()
    thread = threading.Thread(target=run, name='dexhub-servers-tab')
    thread.daemon = True
    thread.start()
    return True


def _playing():
    """A video starts or plays (a Home trailer aside): the servers wait."""
    try:
        from ..skinui import common as C
        return bool(xbmc.Player().isPlayingVideo() and not C.prop('dexhub.trailer.active'))
    except Exception:
        return False


def _retry_limit(data):
    """How old the saved list may be before it is read again."""
    if not data.get('servers'):
        return 30.0
    if data.get('incomplete'):
        tries = max(1, int(data.get('tries') or 1))
        if tries > len(RETRY):
            # v5.10.239: a server that keeps refusing (a Plex 401, Silo asking
            # for its profile) was read every ten minutes all night, six calls
            # each time: after four tries, as a complete list (Refresh, or an
            # account change, reads it again)
            return STALE
        return RETRY[tries - 1]
    return STALE


def _servers():
    data = _load()
    account = _account_key()
    if data.get('account') != account:
        data = {}                         # login/logout is independent of the six-hour TTL
    from ..skinui import common as C
    from .. import server_accounts
    seen = max(float(data.get('t') or 0), float(data.get('checked') or 0))
    if server_accounts.stamp() > seen:
        # v5.10.198: an account was linked or removed after this list was read
        _refresh_later(force=True)
    elif ((C.prop('dhs.tab') == 'servers' or not data.get('servers') or data.get('incomplete')) and
            time.time() - float(data.get('t') or 0) > _retry_limit(data) and not _playing()):
        # v5.10.180: the list is read ahead (at start and on every tab) while it
        # is unknown, so the Servers tab opens on its servers, not on an empty page
        _refresh_later()
    from ..homeui import servers as S
    return S.ordered(data.get('servers') or [])     # v5.10.168: a copy saved before keeps working


def accounts_changed():
    """A server account was linked, changed or removed (v5.10.198; the skin's
    service calls this when an account setting changes): the servers are
    read again at once when the saved list is of other accounts, or was read
    before the change. True when a read started."""
    from .. import server_accounts
    data = _load()
    seen = max(float(data.get('t') or 0), float(data.get('checked') or 0))
    if server_accounts.stamp() <= seen and data.get('account') == _account_key():
        return False                      # the same accounts, written again
    xbmc.log('[DexHub] servers tab: an account changed, reading the servers again', xbmc.LOGINFO)
    return _refresh_later(force=True)


# ---------------------------------------------------------------- the chips
def current_source(servers):
    src = (_win().getProperty('dhs.srv.want') or
           _setting('servers_src') or 'all').strip()
    keys = [s['key'] for s in servers]
    return src if src in keys else 'all'


def set_source(src):
    """A chip was clicked: 'all' or the chip's number (1 is All)."""
    from ..skinui import common as C
    servers = _servers()
    key = 'all'
    if src.isdigit() and 2 <= int(src) <= len(servers) + 1:
        key = servers[int(src) - 2]['key']
    _win().setProperty('dhs.srv.want', key)
    try:
        xbmcaddon.Addon('plugin.video.dexhub').setSetting('servers_src', key)
    except Exception:
        pass
    _win().setProperty('dhs.srv.src', str(_chip_number(servers, key)))
    C.ask_republish('servers source')


def _chip_number(servers, key):
    for n, server in enumerate(servers[:MAX_CHIPS - 1], 2):
        if server['key'] == key:
            return n
    return 1


# ---------------------------------------------------------------- the rows
def skin_rows(src=None, header=True):
    """The Servers tab for skin.dexhub's Home (homeui Row objects).

    v5.10.238: src='all', header=False gives the tab's All rows without
    touching the screen (phone setup arranges them)."""
    from ..homeui import rows as R
    from ..homeui import servers as S
    from ..skinui import rows as SR
    if (SR.app().settings.text('show_servers', 'true') or 'true').lower() == 'false':
        return []                       # the section is hidden (v5.10.156)
    servers = _servers()
    src = src or current_source(servers)
    if header:
        try:
            _publish_header(servers, src)
        except Exception as exc:
            xbmc.log('[DexHub] servers header: %s' % exc, xbmc.LOGINFO)
    if not servers and _refreshing.locked():
        # v5.10.180: the servers are being read for the first time
        app = SR.app()
        tile = {'kind': 'action', 'command': 'refresh', 'title': _t('جاري جلب السيرفرات', 'Finding your servers'),
                'label': _t('جاري جلب السيرفرات', 'Finding your servers'), 'shape': 'landscape',
                'hint': 'Plex  ·  Emby  ·  Jellyfin  ·  Silo',
                'poster': app.media_path('lib_plex.jpg'), 'landscape': app.media_path('lib_plex.jpg')}
        return [R.Row('srv:loading', _t('جاري جلب السيرفرات', 'Finding your servers'), shape='landscape',
                      kind='collection', tiles=[tile], meta={})]
    if not servers:
        app = SR.app()
        tile = {'kind': 'action', 'command': 'setup', 'title': _t('ربط سيرفر', 'Link a server'),
                'label': _t('ربط سيرفر', 'Link a server'), 'shape': 'landscape',
                'hint': 'Plex  ·  Emby  ·  Jellyfin  ·  Silo',
                'plot': _t('اربط سيرفرك من الحسابات والمصادر، وتطلع مكتباته هنا.',
                           'Link your server in Accounts and sources and its libraries show here.'),
                'poster': app.media_path('lib_plex.jpg'), 'landscape': app.media_path('lib_plex.jpg')}
        return [R.Row('srv:setup', _t('لا يوجد سيرفر مربوط', 'No server linked'), shape='landscape',
                      kind='collection', tiles=[tile], meta={})]
    many = len(servers) > 1
    chosen = [s for s in servers if s['key'] == src] if src != 'all' else servers
    rows = []

    def named(title, server):
        return '%s  ·  %s' % (title, server['name']) if many and src == 'all' else title

    # what is being watched, then what was added, on every chosen server
    for server in chosen:
        params = S.continue_params(server)
        if params:
            rows.append(R.Row('srv:%s:continue' % server['key'], named(_t('كمّل المشاهدة', 'Continue watching'), server),
                              shape='wide', kind='continue', subtitle=server['brand'],
                              meta={'params': params, 'progress': True}))
    for server in chosen:
        params = S.recent_params(server, _t('أضيف مؤخراً', 'Recently added'))
        if params:
            rows.append(R.Row('srv:%s:recent' % server['key'], named(_t('أضيف مؤخراً', 'Recently added'), server),
                              shape='poster', kind='catalog', subtitle=server['brand'],
                              meta={'params': params, 'auto_shape': True}))
    if src == 'all':
        # v5.10.168: All stays light: what is being watched and what was added
        # on every server (Silo's own Home sections for both), no libraries;
        # a server's chip has everything
        for server in chosen:
            if server.get('service') != 'silo':
                continue
            for kind, title, params in S.picks(server.get('sections') or []):
                # v5.10.198: Next up too, when Silo shows it as a row of its own
                progress = kind in ('continue', 'next_up')
                label = _t(*S.PICK_TITLES[kind])
                rows.append(R.Row('srv:%s:silo_%s' % (server['key'], kind), named(label, server),
                                  shape='wide' if progress else 'poster',
                                  kind='continue' if progress else 'catalog', subtitle=server['brand'],
                                  meta={'params': params, 'progress': progress, 'auto_shape': not progress}))
        # every server's Continue watching first, then what was added
        rows.sort(key=lambda row: 0 if row.kind == 'continue' else 1)
        # v5.10.180: then every server's libraries as cards (kept with the
        # list, nothing to read): All is never an empty page when a server
        # has nothing to continue and nothing new
        for server in chosen:
            tiles = server.get('tiles') or []
            if tiles:
                rows.append(R.Row('srv:%s:libraries' % server['key'], named(_t('المكتبات', 'Libraries'), server),
                                  shape='landscape', kind='collection', subtitle=server['brand'], tiles=tiles,
                                  meta={}))
        return _or_waiting(rows, chosen)[:20]
    sections = list(chosen[0].get('sections') or [])
    lead = [one for one in sections if one[1].get('section_type') in S.LEAD_SECTIONS]
    for title, params in lead:
        # v5.10.198: a Silo server opens on what is being watched, what is
        # next and what was added, as the other servers do
        progress = params.get('section_type') in ('continue_watching', 'next_up')
        rows.append(R.Row('srv:%s:%s' % (chosen[0]['key'], params.get('section_id')), title,
                          shape='wide' if progress else 'poster',
                          kind='continue' if progress else 'catalog', subtitle=chosen[0]['brand'],
                          meta={'params': params, 'progress': progress, 'auto_shape': not progress}))
    for server in chosen:
        tiles = server.get('tiles') or []
        if tiles:
            rows.append(R.Row('srv:%s:libraries' % server['key'],
                              named(_t('المكتبات', 'Libraries'), server) if src == 'all' else _t('المكتبات', 'Libraries'),
                              shape='landscape', kind='collection', subtitle=server['brand'], tiles=tiles,
                              meta={}))
    if src != 'all':
        server = chosen[0]
        for lib in server.get('libs') or []:
            params = lib['params']
            rows.append(R.Row('srv:%s:lib:%s' % (server['key'], S.library_key(params)), lib['title'],
                              shape='poster', kind='catalog', subtitle=server['brand'],
                              meta={'params': params, 'auto_shape': True}))
        for title, params in sections:
            if params.get('section_type') in S.LEAD_SECTIONS:
                continue                # shown first
            rows.append(R.Row('srv:%s:%s' % (server['key'], params.get('section_id')), title, shape='poster',
                              kind='catalog', subtitle=server['brand'],
                              meta={'params': params, 'auto_shape': True}))
        # v5.10.198: Silo's collections
        for key, names, params in S.silo_collection_rows(server):
            rows.append(R.Row('srv:%s:%s' % (server['key'], key), _t(*names), shape='poster',
                              kind='catalog', subtitle=server['brand'], meta={'params': params}))
    return _or_waiting(rows, chosen)[:20]


def _or_waiting(rows, chosen):
    """v5.10.198: servers whose libraries are not known yet (still being read,
    or not answering) say so, with Refresh: their other rows are read on
    demand and come back empty from a server that does not answer, which
    left an empty tab."""
    waiting = [s for s in chosen or [] if not s.get('libs') and not s.get('sections')]
    if not waiting:
        return rows
    chosen = waiting
    from ..homeui import rows as R
    from ..skinui import rows as SR
    app = SR.app()
    names = u'  \u00b7  '.join(s['name'] for s in chosen[:3])
    data = _load()
    # still being read: the first reads; after two that got nothing, it says so
    reading = _refreshing.locked() or (bool(data.get('incomplete')) and int(data.get('tries') or 0) < 2)
    if reading:
        title = _t('جاري جلب مكتبات %s', 'Reading the libraries of %s') % names
        plot = _t('السيرفر مربوط. نقرأ مكتباته الحين، وتطلع هنا أول ما يرد.',
                  'The server is linked. Its libraries are being read and show here as soon as it answers.')
    else:
        title = _t('ما وصلنا لمكتبات %s', 'Could not read the libraries of %s') % names
        plot = _t('تأكد إن السيرفر شغال وإن الجهاز يوصله، ثم اضغط تحديث.',
                  'Check that the server is on and this device can reach it, then press Refresh.')
    art = app.media_path('lib_%s.jpg' % chosen[0].get('service', 'plex'))
    tile = {'kind': 'action', 'command': 'srv_refresh', 'title': _t('تحديث', 'Refresh'),
            'label': _t('تحديث', 'Refresh'), 'shape': 'landscape', 'hint': names, 'plot': plot,
            'poster': art, 'landscape': art}
    return rows[:19] + [R.Row('srv:waiting', title, shape='landscape', kind='collection', tiles=[tile], meta={})]


def _items_in(prefix, suffix):
    """How many cards the tab's published rows of one kind hold now."""
    from ..skinui import common as C
    from ..skinui import rows as SR
    total = 0
    try:
        for spec in SR.load_specs('servers'):
            key = spec.get('key') or ''
            if key.startswith(prefix) and key.endswith(suffix):
                data = C.read_json(C.row_file('servers', spec['id'])) or {}
                total += len([i for i in data.get('items') or [] if (i.get('props') or {}).get('kind') != 'nav'])
    except Exception:
        pass
    return total


def _publish_header(servers, src):
    win = _win()
    brands = []
    for server in servers:
        if server['brand'] not in brands:
            brands.append(server['brand'])
    if servers:
        win.setProperty('dhs.srv.synced', _t('متصل', 'Connected'))
    else:
        win.setProperty('dhs.srv.synced', _t('اربط Plex أو Emby أو Jellyfin أو Silo', 'Link Plex, Emby, Jellyfin or Silo'))
    win.setProperty('dhs.srv.linked', u'  ·  '.join(brands))
    chosen = [s for s in servers if s['key'] == src] if src != 'all' else servers
    prefix = 'srv:' if src == 'all' else 'srv:%s:' % src
    libs = sum(len(s.get('libs') or []) for s in chosen)
    blocks = []
    if src == 'all':
        blocks.append((str(len(servers)), _t('سيرفر متصل', 'servers linked') if len(servers) != 1 else
                       _t('سيرفر متصل', 'server linked')))
    else:
        blocks.append((chosen[0]['brand'], _t('السيرفر', 'server')))
    if libs:
        blocks.append((str(libs), _t('مكتبة', 'libraries')))
    watching = _items_in(prefix, ':continue')
    if watching:
        blocks.append((str(watching), _t('تكملها', 'to continue')))
    recent = _items_in(prefix, ':recent')
    if recent:
        blocks.append((str(recent), _t('أضيف مؤخراً', 'recently added')))
    for n in range(1, 5):
        value, label = blocks[n - 1] if n <= len(blocks) else ('', '')
        win.setProperty('dhs.srv.s%d' % n, value)
        win.setProperty('dhs.srv.s%dl' % n, label)
    chips = [(_t('الكل', 'All'), str(len(servers)) if len(servers) > 1 else '', 'FFFFFFFF')]
    for server in servers[:MAX_CHIPS - 1]:
        count = len(server.get('libs') or [])
        chips.append((server['name'], str(count) if count else '', TONES.get(server['service'], 'FFFFFFFF')))
    for n in range(1, 9):
        label, count, tone = chips[n - 1] if n <= len(chips) else ('', '', '')
        win.setProperty('dhs.srv.chip.%d.label' % n, label if len(chips) > 1 or n == 1 else '')
        win.setProperty('dhs.srv.chip.%d.count' % n, count)
        win.setProperty('dhs.srv.chip.%d.tone' % n, tone)
    win.setProperty('dhs.srv.src', str(_chip_number(servers, src)))


def refresh():
    """Read the servers again now (the Options menu's "Refresh", the waiting
    row's card). v5.10.198: the skin's service reads them (a plugin call
    would wait for its own reading thread before it ends)."""
    from ..skinui import common as C
    from .. import server_accounts
    server_accounts.changed('')
    C.ask_republish('servers source')


def dispatch(action, params):
    if action == 'srv_src':
        return set_source(params.get('src', '1'))
    if action == 'srv_refresh':
        return refresh()
    return None
