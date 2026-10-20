# -*- coding: utf-8 -*-
"""Profile-scoped personal sources shared by the screensaver and release alerts.

Lists are fetched only by explicit refresh/background workers. Panel reads use
an atomic snapshot. Existing playback/source-availability checks stay in reminders.
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from . import profile_scope as _scope
from urllib.parse import urlparse, parse_qs

_LOCK = threading.RLock()
TTL = 3600
MAX_SOURCES, MAX_ITEMS = 8, 100


def _path(name):
    from . import profile_scope
    return profile_scope.path('homeui/' + name)


def _read(name, default):
    try:
        with open(_path(name), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(name, data):
    from . import profile_scope
    profile_scope.require_current()
    path = _path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.' + uuid.uuid4().hex + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        profile_scope.require_current()
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _t(ar, en):
    from .i18n import is_english
    return en if is_english() else ar


def config():
    data = _read('watch_sources.json', {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault('sources', [
        {'id': 'continue', 'kind': 'continue', 'label': 'Continue Watching', 'display': True, 'monitor': True},
        {'id': 'favorites', 'kind': 'favorites', 'label': 'Watchlists / Favorites', 'display': True, 'monitor': True}])
    data.setdefault('paused', [])
    data.setdefault('read', [])
    data.setdefault('personal_only', False)
    # v5.10.334: the screensaver's look (the sources page sets it)
    look = data.get('look') if isinstance(data.get('look'), dict) else {}
    for key, value in LOOK_DEFAULTS.items():
        look.setdefault(key, value)
    data['look'] = look
    return data


# v5.10.334: style (cinema, spotlight, upnext), seconds a title stays, order
# (shuffle or newest: what you watch first), clock on the page, refresh hours.
# v5.10.357: 'upnext' is Your List (yourlist: the id of the source it shows),
# 'news' the film news
LOOK_DEFAULTS = {'style': 'cinema', 'slide': 14, 'order': 'shuffle', 'clock': True, 'refresh': 1, 'yourlist': ''}
STYLES = ('cinema', 'spotlight', 'upnext', 'news')
SLIDES = (8, 14, 20, 30, 60)
REFRESH = (1, 3, 6, 12, 24)


def look():
    return config()['look']


def set_look(key, value):
    with _LOCK:
        cfg = config()
        cfg['look'][key] = value
        _write('watch_sources.json', cfg)


def left_text(position, duration):
    """'34 min left', '1 h 22 min left' (Arabic: باقي ...)."""
    try:
        left = int((float(duration or 0) - float(position or 0)) // 60)
    except (TypeError, ValueError):
        return ''
    if left <= 0 or not duration:
        return ''
    hours, minutes = divmod(left, 60)
    if hours:
        return _t('باقي %d س %d د', '%d h %d min left') % (hours, minutes)
    return _t('باقي %d د', '%d min left') % minutes


def _progress(raw, info, props):
    """(percent, left text, episode line) of a Continue Watching card or a
    playback row; (0, '', '') for anything else."""
    position = raw.get('position') or info.get('resumetime') or 0
    duration = raw.get('duration') or info.get('totaltime') or 0
    try:
        percent = int(float(raw.get('percent') or raw.get('progress') or 0))
    except (TypeError, ValueError):
        percent = 0
    try:
        if not percent and float(duration or 0) > 0:
            percent = int(100 * float(position or 0) / float(duration))
    except (TypeError, ValueError):
        percent = 0
    left = left_text(position, duration) or str(props.get('dhs.remaining') or '')
    season = raw.get('season') or info.get('season')
    episode = raw.get('episode') or info.get('episode')
    try:
        line = 'S%d · E%d' % (int(season), int(episode)) if season and episode else ''
    except (TypeError, ValueError):
        line = ''
    return max(0, min(100, percent)), left, line


def _fingerprint(data):
    # v5.10.357: the Your List pick is part of it (a hidden source it adds is read at once)
    look = data.get('look') if isinstance(data.get('look'), dict) else {}
    return hashlib.sha256(json.dumps([data.get('sources') or [], str(look.get('yourlist') or '')],
                                     sort_keys=True).encode()).hexdigest()


def cached_catalogs():
    from .skinui import common as C
    out = []
    folder = C.folder('rows')
    for name in sorted(os.listdir(folder)):
        if not re.match(r'^(all|movie|series)_[a-zA-Z0-9_-]+\.json$', name):
            continue
        row = C.read_row(os.path.join(folder, name)) or {}
        if not row.get('items') or row.get('kind') in ('continue', 'collection'):
            continue
        tab, row_id = name[:-5].split('_', 1)
        out.append({'id': 'catalog:' + tab + ':' + row_id, 'kind': 'catalog',
                    'tab': tab, 'row_id': row_id, 'label': row.get('title') or row.get('key') or row_id})
    return out


def normalise(raw, source):
    """Trakt, MDBList, favorites, cached Kodi rows and Home tiles -> one title."""
    if not isinstance(raw, dict):
        return None
    info, props, art = raw.get('info') or {}, raw.get('props') or {}, raw.get('art') or {}
    if props.get('kind') in ('more', 'folder', 'note', 'shelf'):
        return None
    q = parse_qs(str(props.get('dhs.q') or ''))
    data = raw.get('show') if isinstance(raw.get('show'), dict) else raw.get('movie')
    data = data if isinstance(data, dict) else raw
    ids = data.get('ids') if isinstance(data.get('ids'), dict) else {}
    def value(key):
        return str((q.get(key) or [''])[0] or '')
    kind = str(data.get('_type') or data.get('media_type') or data.get('mediatype') or raw.get('type') or
               props.get('dhs.media') or value('m') or info.get('mediatype') or '').lower()
    media = 'series' if isinstance(raw.get('show'), dict) or info.get('tvshowtitle') or kind in (
        'tv', 'show', 'shows', 'series', 'tvshow', 'season', 'episode', 'anime') else 'movie'
    canonical = str(data.get('canonical_id') or '')
    tmdb = str(data.get('show_tmdb_id') or data.get('tmdb_id') or ids.get('tmdb') or value('tmdb') or (data.get('id') if source.get('kind') == 'mdblist' else '') or '')
    imdb = str(data.get('imdb_id') or ids.get('imdb') or value('imdb') or '')
    if canonical.startswith('tmdb:'):
        tmdb = tmdb or canonical.split(':')[1]
    elif canonical.startswith('tt'):
        imdb = imdb or canonical.split(':')[0]
    title = str(info.get('tvshowtitle') or data.get('show_title') or data.get('title') or
                (raw.get('show') if isinstance(raw.get('show'), str) else '') or raw.get('label') or data.get('name') or '')
    if not title or not (tmdb.isdigit() or re.match(r'^tt\d+$', imdb)):
        return None
    imdb = imdb.split(':')[0]
    identity = imdb if re.match(r'^tt\d+$', imdb) else 'tmdb:' + tmdb
    prog, left, line = _progress(raw, info, props) if source.get('kind') == 'continue' else (0, '', '')
    year = str(info.get('year') or data.get('year') or '')[:4]
    return {'prog': prog, 'left': left, 'ep': line, 'year': year if year.isdigit() else '',
            'key': media + '|' + identity, 'media': media, 'tmdb': tmdb if tmdb.isdigit() else '',
            'imdb': imdb, 'title': title, 'poster': art.get('poster') or data.get('poster') or ('https://images.metahub.space/poster/medium/%s/img' % imdb if re.match(r'^tt\d+$', imdb) else ''),
            'fanart': art.get('fanart') or art.get('landscape') or data.get('background') or data.get('fanart') or '',
            'logo': art.get('clearlogo') or data.get('clearlogo') or '',
            'plot': info.get('plot') or data.get('overview') or data.get('plot') or '',
            'source_ids': [source['id']], 'sources': [source['label']]}


def _fetch(source):
    kind = source['kind']
    if kind == 'silo':
        from .providers import silo_provider as S
        from .silo_notifications import owner
        auth = S.account() or {}
        if owner(auth) != source.get('owner'):
            return []
        rows, _total, _more = S.catalog_page(source=source.get('source') or 'watchlist',
                                           collection_id=source.get('collection_id') or '',
                                           library_id=source.get('library_id') or '', limit=MAX_ITEMS)
        if owner(S.account() or {}) != source.get('owner'):
            return []
        return [{'media_type': r.get('media_type'), 'title': r.get('title'), 'year': r.get('year'),
                 'ids': {'tmdb': (r.get('ids') or {}).get('tmdb_id'),
                         'imdb': (r.get('ids') or {}).get('imdb_id')},
                 'poster': r.get('thumb'), 'background': r.get('art'), 'clearlogo': r.get('logo'),
                 'overview': r.get('summary')} for r in rows]
    if kind == 'continue':
        from .skinui import panel
        rows = panel._continue_rows() or []
        # Local playback is included even before the Home has cached rows.
        from .dexhub import playback_store
        return rows + [r for r in playback_store.list_recent_items(limit=MAX_ITEMS, include_watched=True) or [] if float(r.get('updated_at') or 0) > time.time() - 42 * 86400]
    if kind == 'favorites':
        from . import favorites_store
        return favorites_store.list_favorites(limit=MAX_ITEMS) or []
    if kind == 'trakt':
        from . import trakt
        if source.get('list_id'):
            return trakt.fetch_list_by_id(source['list_id'], limit=MAX_ITEMS)
        return trakt.fetch_user_list(source['username'], source['slug'], limit=MAX_ITEMS)
    if kind == 'mdblist':
        from . import mdblist
        return (mdblist.fetch_items_by_id(source['list_id'], limit=MAX_ITEMS) if source.get('list_id') else mdblist.fetch_items(source['username'], source['slug'], limit=MAX_ITEMS))[0]
    if kind == 'catalog':
        from .skinui import common as C, rows as R
        data = R.fetch(source['tab'], source['row_id'], wait_other=False) or C.read_row(
            C.row_file(source['tab'], source['row_id'])) or {}
        return data.get('src') or data.get('items') or []
    return []


def snapshot():
    data = _read('watch_sources_cache.json', {})
    return data if isinstance(data, dict) else {}


@_scope.bound
def refresh(force=False):
    with _LOCK:
        cfg, old = config(), snapshot()
        sig = _fingerprint(cfg)
        ttl = max(1, int(cfg['look'].get('refresh') or 1)) * 3600     # v5.10.334: the page's choice
        if not force and old.get('signature') == sig and time.time() - float(old.get('t') or 0) < ttl:
            return old
        found, errors, aliases = {}, [], {}
        empty_since = {}
        yourlist = str(cfg['look'].get('yourlist') or '')
        for source in cfg['sources'][:MAX_SOURCES]:
            if not source.get('display', True) and not source.get('monitor', True) and source.get('id') != yourlist:
                continue
            try:
                raw = _fetch(source)
                if not raw and source.get('kind') != 'silo':
                    previous = [r for r in old.get('rows') or [] if source['id'] in r.get('source_ids', [])]
                    since = (old.get('empty_since') or {}).get(source['id']) or time.time()
                    empty_since[source['id']] = since
                    if previous and time.time() - since < TTL:
                        for row in previous:
                            found.setdefault(row['key'], row)
                        errors.append({'id': source['id'], 'error': 'EmptyOrUnavailable'})
                for item in list(raw or [])[:MAX_ITEMS]:
                    row = normalise(item, source)
                    if row:
                        identities = [(row['media'], field, row[field]) for field in ('tmdb', 'imdb') if row.get(field)]
                        key = next((aliases[v] for v in identities if v in aliases), row['key'])
                        row['key'] = key
                        for ident in identities:
                            aliases[ident] = key
                        if key not in found:
                            found[key] = row
                        else:
                            for field in ('tmdb', 'imdb', 'poster', 'fanart', 'logo', 'prog', 'left', 'ep', 'year'):
                                if not found[key].get(field):
                                    found[key][field] = row.get(field) or ''
                            for field in ('source_ids', 'sources'):
                                found[key][field] = list(dict.fromkeys(found[key][field] + row[field]))
            except Exception as exc:
                errors.append({'id': source['id'], 'error': exc.__class__.__name__})
                # Keep this source's last snapshot when it failed, never a different profile.
                for row in old.get('rows') or []:
                    if source['id'] in row.get('source_ids', []):
                        found.setdefault(row['key'], row)
        result = {'t': time.time(), 'signature': sig, 'rows': list(found.values()), 'errors': errors, 'empty_since': empty_since}
        _write('watch_sources_cache.json', result)
        return result


@_scope.bound
def selected(purpose='display', fresh=False):
    cfg = config()
    data = refresh() if fresh else snapshot()
    from .providers import silo_provider
    from .silo_notifications import owner
    silo_owner = owner(silo_provider.account() or {})
    active = {s['id'] for s in cfg['sources'] if s.get(purpose, True) and
              (s.get('kind') != 'silo' or s.get('owner') == silo_owner)}
    paused = set(cfg['paused']) if purpose == 'monitor' else set()
    return [r for r in data.get('rows') or [] if active.intersection(r.get('source_ids', [])) and not set(aliases(r)).intersection(paused)]


@_scope.bound
def monitoring_titles():
    """Use existing ID resolution; return the same contract as reminders.titles."""
    from . import reminders
    shows, names, movies = [], {}, []
    rows = selected('monitor', fresh=True)
    # Known IDs are cheap. Rotate only the unresolved IDs; at most twelve
    # new identifier lookups per half-hour pass, not one request per list item.
    budget = 12
    offset = (int(time.time()) // 1800 * budget) % max(1, len(rows))
    for row in rows[offset:] + rows[:offset]:
        tmdb = row.get('tmdb') or reminders._tmdb_of(row.get('imdb'), row['media'], cached_only=True)
        if not tmdb and budget:
            budget -= 1
            tmdb = reminders._tmdb_of(row.get('imdb'), row['media'])
        if not str(tmdb).isdigit():
            continue
        tmdb = str(tmdb)
        target = shows if row['media'] == 'series' else movies
        if tmdb not in target:
            target.append(tmdb)
        names[tmdb] = row['title']

    return shows, names, movies


def paused_ids():
    from . import reminders
    cfg, out = config(), set()
    for row in snapshot().get('rows') or []:
        if not set(aliases(row)).intersection(cfg['paused']):
            continue
        tmdb = row.get('tmdb') or reminders._tmdb_of(row.get('imdb'), row['media'], cached_only=True)
        if tmdb:
            out.add((row['media'], str(tmdb)))
    return out


@_scope.bound
def mark_read(keys):
    with _LOCK:
        cfg = config()
        cfg['read'] = list(dict.fromkeys(cfg['read'] + list(keys)))[-1000:]
        _write('watch_sources.json', cfg)


@_scope.bound
def manage_title(key):
    import xbmcgui
    row = next((r for r in snapshot().get('rows') or [] if r['key'] == key), None)
    if not row:
        return
    cfg = config()
    identifiers = aliases(row)
    paused = bool(set(identifiers).intersection(cfg['paused']))
    choices = [_t('استئناف المراقبة', 'Resume monitoring') if paused else _t('إيقاف مراقبة هذا العمل', 'Pause monitoring'),
               _t('فتح صفحة العمل', 'Open title details')]
    pick = xbmcgui.Dialog().select(row['title'], choices)
    if pick == 0:
        cfg['paused'] = [k for k in cfg['paused'] if k not in identifiers] if paused else list(dict.fromkeys(cfg['paused'] + identifiers))
        _write('watch_sources.json', cfg)
        _changed()
    elif pick == 1:
        from .skinui.panel import _open_url
        import xbmc
        q = {'m': row['media'], 'title': row['title'], 'tmdb': row['tmdb'], 'imdb': row['imdb']}
        xbmc.executebuiltin('RunPlugin(%s)' % _open_url(q))


def _changed():
    import xbmcgui
    home = xbmcgui.Window(10000)
    sync_alerts()
    home.setProperty('dhs.panel.rev', str(time.time()))
    home.setProperty('dexhub.watch.refresh', '1')
    try:
        from .homeui import screensaver
        screensaver._forget()
    except Exception:
        pass


def _add(kind, display=True, monitor=True):
    """Add a source; returns it (v5.10.357: Your List adds its lists with
    Show and Alerts off, so they only feed Your List)."""
    import xbmcgui
    dlg, cfg = xbmcgui.Dialog(), config()
    if len(cfg['sources']) >= MAX_SOURCES:
        dlg.ok('Dex Hub', _t('الحد الأقصى 8 مصادر', 'Up to 8 sources are supported'))
        return
    source = None
    if kind == 'silo':
        from .silo_social import Client
        client = Client()
        choices = [{'source': 'watchlist', 'label': _t('قائمة Silo', 'Silo watchlist')},
                   {'source': 'favorites', 'label': _t('مفضلة Silo', 'Silo favorites')}]
        from .providers import silo_provider as S
        try:
            server_rows = S.server_collections()
        except Exception:
            server_rows = []
        choices += [{'source': 'library_collection', 'collection_id': r['id'],
                     'library_id': r.get('library_id') or '',
                     'label': 'Silo · ' + r['title']} for r in server_rows]
        try:
            personal_rows = S.personal_collections()
        except Exception:
            personal_rows = []
        choices += [{'source': 'user_collection', 'collection_id': r['id'],
                     'label': 'Silo · ' + r['title']} for r in personal_rows]
        client.current()
        pick = dlg.select(_t('مصادر Silo للعرض والمراقبة', 'Silo display and monitoring sources'),
                          [r['label'] for r in choices])
        if 0 <= pick < len(choices):
            client.current()
            source = dict(choices[pick], kind='silo', owner=client.owner)
            source['id'] = 'silo:%s:%s:%s' % (client.owner, source['source'], source.get('collection_id') or '')
    elif kind == 'catalog':
        rows = cached_catalogs()
        pick = dlg.select(_t('كتالوجات الصفحة الرئيسية', 'Home catalogs'), [r['label'] for r in rows])
        if 0 <= pick < len(rows):
            source = rows[pick]
        elif not rows:
            dlg.ok('Dex Hub', _t('افتح الكتالوج على الرئيسية أولاً ليظهر هنا', 'Open a catalog on Home first to make it available here'))
    else:
        try:
            if kind == 'trakt':
                from . import trakt
                choices = trakt.fetch_my_lists() or []
            else:
                from . import mdblist
                choices = mdblist.fetch_my_lists() or []
        except Exception:
            choices = []
        labels = [str(r.get('name') or r.get('title') or r.get('id')) for r in choices]
        pick = dlg.select(kind.title(), labels + [_t('إدخال رابط أو رقم القائمة', 'Enter a list URL or ID')])
        if pick < 0:
            return
        if pick < len(choices):
            row = choices[pick]
            ident = str(row.get('list_id') or row.get('id') or '')
            source = {'id': kind + ':' + ident, 'kind': kind, 'list_id': ident, 'label': labels[pick]}
        else:
            raw = dlg.input(_t('رابط القائمة أو رقمها', 'List URL or numeric ID')).strip()
            parsed = urlparse(raw)
            bits = [b for b in parsed.path.split('/') if b]
            if raw.isdigit():
                source = {'list_id': raw}
            elif kind == 'trakt' and (parsed.hostname or '').lower() in ('trakt.tv', 'www.trakt.tv') and len(bits) == 2 and bits[0] == 'lists' and bits[1].isdigit():
                source = {'list_id': bits[1]}
            elif kind == 'trakt' and (parsed.hostname or '').lower() in ('trakt.tv', 'www.trakt.tv') and len(bits) >= 4 and bits[0] == 'users' and bits[2] == 'lists':
                source = {'username': bits[1], 'slug': bits[3]}
            elif kind == 'mdblist' and (parsed.hostname or '').lower() in ('mdblist.com', 'www.mdblist.com'):
                if bits and bits[-1].isdigit():
                    source = {'list_id': bits[-1]}
                else:
                    from . import mdblist
                    parsed_list = mdblist.parse_list_url(raw)
                    if parsed_list:
                        user, slug = parsed_list if isinstance(parsed_list, tuple) else (parsed_list.get('username'), parsed_list.get('slug'))
                        source = {'username': user, 'slug': slug}
            if source:
                ident = source.get('list_id') or source.get('username', '') + '/' + source.get('slug', '')
                source.update(id=kind + ':' + ident, kind=kind, label=kind.title() + ' · ' + ident)
            elif raw:
                dlg.ok('Dex Hub', _t('الرابط غير مدعوم. اختر القائمة من حسابك أو أدخل رقمها.', 'Unsupported URL. Select an account list or enter its numeric ID.'))
    if source and not any(s['id'] == source['id'] for s in cfg['sources']):
        source.update(display=display, monitor=monitor)
        cfg['sources'].append(source)
        _write('watch_sources.json', cfg)
        _changed()
        return source
    if source:
        return next((s for s in cfg['sources'] if s['id'] == source['id']), source)
    return None


@_scope.bound
def menu(params=None, handle=None):
    import xbmc
    import xbmcgui
    dlg = xbmcgui.Dialog()
    if (params or {}).get('key'):
        return manage_title(params['key'])
    if not (params or {}).get('classic'):
        # v5.10.334: the sources page (styles, sources with Show and Alerts,
        # timing); the lists below stay for ?classic=1
        try:
            from .homeui import ss_page
            return ss_page.open_page()
        except Exception as exc:
            xbmc.log('[DexHub] screensaver page: %s' % exc, xbmc.LOGWARNING)
    while True:
        cfg = config()
        labels = [_t('إضافة قائمة Trakt', 'Add Trakt list'), _t('إضافة قائمة MDBList', 'Add MDBList list'),
                  _t('إضافة كتالوج', 'Add Home catalog'),
                  _t('مصادري فقط في شاشة التوقف', 'Only personal sources in screensaver') + ('  ✓' if cfg['personal_only'] else ''),
                  _t('تحديث المصادر الآن', 'Refresh sources now'), _t('تعليم التنبيهات كمقروءة', 'Mark alerts as read'),
                  _t('إضافة قائمة أو كوليكشن Silo', 'Add Silo watchlist or collection')]
        for s in cfg['sources']:
            labels.append('%s  ·  %s / %s' % (s['label'], _t('عرض', 'Show') if s.get('display') else '—',
                                             _t('مراقبة', 'Monitor') if s.get('monitor') else '—'))
        pick = dlg.select(_t('مصادر العرض والتنبيهات · حتى 100 عمل لكل مصدر', 'Screensaver & alerts · up to 100 titles per source'), labels)
        if pick < 0:
            return
        if pick < 3:
            _add(('trakt', 'mdblist', 'catalog')[pick])
        elif pick == 3:
            cfg['personal_only'] = not cfg['personal_only']
            _write('watch_sources.json', cfg)
            _changed()
        elif pick == 4:
            xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
            try:
                got = refresh(force=True)
            finally:
                xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
            dlg.ok('Dex Hub', _t('الأعمال: %d · مصادر تعذر تحديثها: %d', 'Titles: %d · Sources that could not refresh: %d') % (len(got['rows']), len(got['errors'])))
            _changed()
        elif pick == 5:
            try:
                events = json.loads(xbmcgui.Window(10000).getProperty('dhs.newep.list') or '[]')
                mark_read([e['key'] for e in events if e.get('key')])
                _changed()
            except (ValueError, TypeError):
                pass
        elif pick == 6:
            _add('silo')
        elif pick - 7 < len(cfg['sources']):
            source = cfg['sources'][pick - 7]
            action = dlg.select(source['label'], [_t('تبديل العرض', 'Toggle screensaver display'),
                                                 _t('تبديل مراقبة الإصدارات', 'Toggle release monitoring'),
                                                 _t('حذف المصدر', 'Remove source')])
            if action in (0, 1):
                field = ('display', 'monitor')[action]
                source[field] = not source.get(field, True)
            elif action == 2:
                cfg['sources'].remove(source)
            if action >= 0:
                _write('watch_sources.json', cfg)
                _changed()


@_scope.bound
def features(enrich=True):
    from . import reminders as R
    out, lookups = [], 0
    for row in selected('display', fresh=enrich):
        item = dict(row)
        if not item.get('fanart') and enrich and lookups < 8:
            lookups += 1
            try:
                tmdb = item.get('tmdb') or R._tmdb_of(item.get('imdb'), item['media'])
                data = R._show(tmdb) if item['media'] == 'series' else R._movie(tmdb)
                item['tmdb'] = tmdb
                item['fanart'] = R._art(data.get('backdrop'), 'backdrop')
                item['poster'] = item.get('poster') or R._art(data.get('poster'), 'poster')
            except Exception:
                pass
        if item.get('fanart'):
            kind = 'continue' if 'continue' in item.get('source_ids', []) and item.get('prog') else 'mine'
            out.append({'title': item['title'], 'fanart': item['fanart'], 'logo': item.get('logo') or '',
                        # v5.10.334: what the new styles show
                        'prog': item.get('prog') or 0, 'left': item.get('left') or '', 'ep': item.get('ep') or '',
                        'year': item.get('year') or '', 'kind': kind,
                        'chip': _t('متابعة المشاهدة', 'Continue Watching') if kind == 'continue'
                        else _t('من ', 'From your ') + ' · '.join(item['sources']),
                        'plot': item.get('plot') or '', 'media': item['media'], 'tmdb_id': item.get('tmdb') or '',
                        'open_tmdb': item.get('tmdb') or '', 'open_imdb': item.get('imdb') or '',
                        'meta': _t('مسلسل', 'Series') if item['media'] == 'series' else _t('فيلم', 'Movie'),
                        'section': ' · '.join(item['sources']), 'row': ' · '.join(item['sources'])})
    return out[:80]


def continue_items(limit=3):
    """v5.10.334: what you are watching, with its progress (the Up next style)."""
    out = []
    for row in snapshot().get('rows') or []:
        if 'continue' in row.get('source_ids', []) and row.get('prog') and (row.get('fanart') or row.get('poster')):
            out.append(row)
        if len(out) >= limit:
            break
    return out


def counts():
    """v5.10.334: {source id: titles} of the last refresh, and its age in seconds."""
    data = snapshot()
    found = {}
    for row in data.get('rows') or []:
        for sid in row.get('source_ids', []):
            found[sid] = found.get(sid, 0) + 1
    try:
        age = max(0, time.time() - float(data.get('t') or 0)) if data.get('t') else -1
    except (TypeError, ValueError):
        age = -1
    return found, age, len(data.get('rows') or [])


@_scope.bound
def calendar_events():
    from . import reminders as R
    events = []
    for row in selected('display')[:20]:
        try:
            tmdb = row.get('tmdb') or R._tmdb_of(row.get('imdb'), row['media'])
            data = R._show(tmdb) if row['media'] == 'series' else R._movie(tmdb)
            if row['media'] == 'series':
                for ep in data.get('eps') or []:
                    events.append({'date': ep['date'], 'title': row['title'], 'detail': 'S%02dE%02d' % (ep['season'], ep['episode']),
                                   'poster': row.get('poster') or R._art(data.get('poster'), 'poster'), 'kind': 'mine',
                                   'tmdb_id': tmdb, 'media': 'series'})
            elif data.get('date'):
                events.append({'date': data['date'], 'title': row['title'], 'detail': _t('إصدار رقمي', 'Digital release'),
                               'poster': row.get('poster') or R._art(data.get('poster'), 'poster'), 'kind': 'movie',
                               'tmdb_id': tmdb, 'media': 'movie'})
        except Exception:
            pass
    return events


@_scope.bound
def sync_alerts():
    """Read/pause/source changes update the badge immediately, without network."""
    import xbmcgui
    home = xbmcgui.Window(10000)
    try:
        rows = json.loads(home.getProperty('dhs.newep.list') or '[]')
    except (ValueError, TypeError):
        rows = []
    cfg = config()
    active = {ident for r in selected('monitor') for ident in aliases(r)}
    rows = [r for r in rows if r.get('key') not in cfg['read'] and
            (not r.get('monitor_key') or active.intersection(list(r.get('monitor_aliases') or []) + [r['monitor_key']]))]
    home.setProperty('dhs.newep.list', json.dumps(rows))
    count = str(len(rows)) if rows else ''
    home.setProperty('dhs.newep.count', count)
    home.setProperty('dhs.panel.n.alerts', count)
    first = rows[0] if rows else {}
    for prop, field in [('title', 'title'), ('code', 'code'), ('poster', 'poster'), ('open', 'action')]:
        home.setProperty('dhs.newep.' + prop, first.get(field) or '')
    try:
        from . import island
        island.changed()
        island.refresh(force=True)
    except Exception:
        pass


def aliases(row):
    media = row.get('media') or 'movie'
    ids = [row['key']]
    if row.get('tmdb'):
        ids.append(media + '|tmdb:' + str(row['tmdb']))
    if row.get('imdb'):
        ids.append(media + '|' + str(row['imdb']))
    return list(dict.fromkeys(ids))
