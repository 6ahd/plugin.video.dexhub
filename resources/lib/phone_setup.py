# -*- coding: utf-8 -*-
"""Set up Dex Hub from a phone (v5.10.229).

The TV shows a QR code; the phone opens a page served by this invocation on
the home network (resources/web/phone_setup.html). Every change on the page is
written at once (no save button): accounts, source add-ons, the Home's rows
and their styles, ready collections, metadata, the look and subtitles.

Security: the page lives under a random token path, on the LAN address only,
and only while the TV window is open (it closes after a long idle time).
Nothing leaves the home network except the services' own sign-ins (Nuvio,
Trakt and Plex are approved on their own sites, no password enters Dex Hub
for them).

Routes (all under /<token>/):
  GET  ''              the page
  GET  api/state       everything the page shows
  POST api/set         {key, value}            a setting
  POST api/addon       {op: add|remove, url|id}
  POST api/login       {service, server, user, password}
  POST api/code        {service}               Nuvio, Trakt, Plex: a code
  POST api/tv          {service}               open that sign-in on the TV
  POST api/sync        {service: stremio, op: configure|run|status, direction}
  POST api/logout      {service}
  POST api/home        {page, op, ...}         rows, styles, collections (every page)
  POST api/pref        {key, value}            v5.10.286: a Home/display preference
                       (ui_preferences.OPTIONS key, through set_value) or a
                       control centre entry (homeui.control PICKS/TOGGLES,
                       continue_split, through apply_entry); state() carries
                       their values as 'prefs' and 'centre'
  GET  api/img/<id>    a picture of the TV's own for the preview
  POST api/done        the phone finished
"""
from . import profile_scope as _scope

import json
import os
import threading
import time
import tempfile
import shutil
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from . import lazy_tb as traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import xbmc
import xbmcaddon
import xbmcgui

ADDON_ID = 'plugin.video.dexhub'
IDLE_SECONDS = 30 * 60          # no request for this long: the TV window closes
FIRST_WAIT = 15 * 60            # nobody opened the page at all

TOGGLES = {
    'trailers': ('homeui_trailers', True),
    'trailer_sound': ('homeui_trailer_sound', True),
    'autostart': ('homeui_autostart', False),
    'autosync': ('autosync_enabled', True),
    'meta_badges': ('metadata_badges', True),
}
LABELS = (
    ('homeui_show_row_source', ('true', 'false')),
    ('homeui_title_style', ('logo', 'text')),
    ('homeui_background_style', ('cinematic', 'soft', 'dark', 'fullscreen')),
    ('library_split_media', ('true', 'false')),
    ('player_badges_visible', ('true', 'false')),
    ('player_badges_brightness', ('solid', 'soft')),

    # v5.10.286: the new cinema and netflix layouts (cinematic stays first: the default)
    ('homeui_layout_mode', ('cinematic', 'two_rows', 'spotlight_posters', 'cinema', 'netflix', 'shelf', 'legacy')),
    ('homeui_nav_position', ('top', 'side')),
    ('ui_language', ('Arabic', 'English')),
    ('homeui_size', ('Normal', 'Large', 'Extra large')),
    ('homeui_metadata_language', ('Auto', 'Arabic', 'English')),
    ('server_metadata', ('0', '1')),
)
# key: (name, sign-in kind, TV action, TV sign-out action)
SERVICES = (
    ('nuvio', 'Nuvio', 'code', 'nuvio_connect', 'nuvio_logout'),
    ('trakt', 'Trakt', 'code', 'trakt_auth', 'trakt_logout'),
    ('stremio', 'Stremio', 'login', 'stremio_qr_login', 'stremio_logout'),
    ('plex', 'Plex', 'code', 'plex_login', 'plex_logout'),
    ('emby', 'Emby', 'server', 'emby_login', 'emby_logout'),
    ('jellyfin', 'Jellyfin', 'server', 'jellyfin_login', 'jellyfin_logout'),
    ('silo', 'Silo', 'silo', 'silo_qr_login', 'silo_logout'),
    ('iptv', 'IPTV', 'tv', 'iptv_menu', ''),
)
_BY_KEY = dict((row[0], row) for row in SERVICES)


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] phone setup: %s' % msg, level)


def _tr(text):
    try:
        from .i18n import tr
        return tr(text)
    except Exception:
        return text


def _setting(key, default=''):
    try:
        value = _addon().getSetting(key)
    except Exception:
        value = ''
    return value if value != '' else default


def _flag(key, default):
    value = (_setting(key, '') or '').strip().lower()
    if not value:
        return default
    return value == 'true'


def _scale():
    """v5.10.288: the Home size in force (clamped, the old skin size too)."""
    try:
        from .home_preferences import current_scale
        return str(current_scale(setting=_setting('homeui_poster_scale', '100')))
    except Exception:
        return _setting('homeui_poster_scale', '100')


def _set(key, value):
    if key == 'homeui_poster_scale':
        from .home_preferences import set_poster_scale
        set_poster_scale(value)
    elif key in ('homeui_layout_mode', 'homeui_nav_position'):
        from .home_preferences import write_setting
        write_setting(key, value)
    elif key in ('homeui_trailer_mode', 'homeui_trailer_volume', 'homeui_trailers', 'homeui_trailer_sound', 'homeui_trailer_delay'):
        from .trailer_preferences import write_setting
        write_setting(key, value)
    else:
        _addon().setSetting(key, str(value))
    try:
        from . import settings_cache
        settings_cache.invalidate()
        from .ui_preferences import publish
        publish()
    except Exception:
        pass


def _republish(reason):
    try:
        if xbmc.getSkinDir() == 'skin.dexhub':
            from .skinui import common
            common.ask_republish(reason)
        else:
            xbmcgui.Window(10000).setProperty('widgetreload', str(time.time()))
    except Exception:
        pass


# ------------------------------------------------------------------ state
def _ui_options():
    """v5.10.286: the keys ui_preferences can set ({} when it fails to load)."""
    try:
        from . import ui_preferences
        return ui_preferences.OPTIONS
    except Exception:
        return {}


def _linked(key):
    if key == 'iptv':
        try:
            from .skinui import rows as SR
            from .homeui import live_sources as LS
            return bool(LS.saved(SR.app().profile))
        except Exception:
            return False
    try:
        from .homeui import setup
        return bool(setup._linked(key))
    except Exception:
        return False


def _themes():
    from . import skin_theme
    out = []
    for key in sorted(skin_theme.THEME_PRESETS.keys(), key=int):
        p = skin_theme.THEME_PRESETS[key]
        col = lambda name, d: '#' + str(p.get(name) or d)[-6:]
        out.append({'key': key, 'name': p.get('name') or key, 'accent': col('accent', 'FF9B6BFF'),
                    'secondary': col('secondary', 'FF4F8BFF'), 'surface': col('surface', 'FF0B0E1C'),
                    'card': col('surface_card', 'FF161B30')})
    return out


def _current_theme():
    """The preset picked in Dex Hub; with Auto, the skin's colour theme."""
    from . import skin_theme
    current = (_setting('theme_preset', '7') or '7').strip()
    if current in skin_theme.THEME_PRESETS:
        return current
    try:
        return skin_theme.preset_for_colour(skin_theme.skin_colour_theme()) or '7'
    except Exception:
        return '7'


def _addons():
    out = []
    try:
        from .dexhub import store
        for row in store.list_providers() or []:
            url = str(row.get('manifest_url') or '')
            host = url.split('://', 1)[-1].split('/', 1)[0]
            out.append({'id': row.get('id') or '', 'name': row.get('name') or host, 'host': host})
    except Exception:
        pass
    return out


def _meta():
    from . import meta_mode
    sources = [{'id': 'auto', 'name': _tr('تلقائي'), 'line': _tr('يختار الأنسب لكل كتالوج'), 'best': True}]
    if meta_mode.helper_installed():
        sources.append({'id': 'tmdb_helper', 'name': 'TMDb Helper', 'line': _tr('من قاعدة TMDb Helper على جهازك')})
    for row in meta_mode.meta_providers() or []:
        sources.append({'id': row.get('id'), 'name': row.get('name') or row.get('id'),
                        'line': _tr('من إضافتك') if not meta_mode.is_aiometadata(row)
                        else _tr('من إضافتك، تحكم كامل بالشكل')})
    sources.append({'id': 'native', 'name': _tr('من الإضافة نفسها'), 'line': _tr('بدون أي تبديل')})
    current = (_setting('global_meta_source_id', '') or '').strip() or 'auto'
    posters = [{'id': 'same', 'name': _tr('نفس المصدر')}, {'id': 'betterposters', 'name': 'BetterPosters'}]
    try:
        from . import meta_source
        posters += [{'id': s['id'], 'name': s['name']} for s in meta_source.list_available_meta_sources()
                    if s['id'] not in ('auto', 'native')]
    except Exception:
        pass
    return {'sources': sources, 'source': current, 'posters': posters,
            'poster': _setting('global_poster_source_id', 'same'),
            'language': _setting('homeui_metadata_language', 'Auto'),
            'servers': _setting('server_metadata', '0')}


# the pages the phone arranges (v5.10.238): the Home's three, then
# skin.dexhub's Library and Servers tabs
PAGES = (('all', 'الرئيسية'), ('movie', 'أفلام'), ('series', 'مسلسلات'),
         ('library', 'المكتبة'), ('servers', 'السيرفرات'))
_EDITABLE = ('all', 'movie', 'series')     # rows can be added here
_PLACEHOLDER = ('srv:loading', 'srv:setup')


def _page_rows(page):
    """Every row of a page, hidden ones too, in the page's own order."""
    from .skinui import rows as SR
    if page == 'library':
        from .routes import library
        return library.skin_rows(src='all', header=False)
    if page == 'servers':
        from .routes import servers_tab
        rows = servers_tab.skin_rows(src='all', header=False)
        return [r for r in rows if r.key not in _PLACEHOLDER and not r.key.startswith('srv:waiting')]
    return SR.HeadlessHome(SR.app(), page).layout_rows(page)


def _pages():
    skin = xbmc.getSkinDir() == 'skin.dexhub'
    out = []
    for key, label in PAGES:
        if key in ('library', 'servers') and not skin:
            continue
        if key == 'servers':
            try:
                if not _page_rows('servers'):
                    continue
            except Exception:
                continue
        out.append({'key': key, 'label': label, 'editable': key in _EDITABLE})
    return out


def _unwrap(path):
    """Kodi's image://<quoted>/ wrapper around the real address."""
    path = str(path or '')
    if path.startswith('image://'):
        from urllib.parse import unquote
        path = unquote(path[len('image://'):].rstrip('/'))
    return path


_PRIVATE = ('token', 'api_key', 'apikey', 'x-emby', 'x-plex', 'password')


class _Images(object):
    """Art for the phone's preview: public web addresses as they are; the
    TV's own files and addresses only the TV can reach (127.0.0.1, its own
    proxies) through api/img/<id>, only the ones handed out here."""

    def __init__(self):
        self.lock = threading.Lock()
        self.files = {}
        self.ids = {}

    def _id(self, where):
        with self.lock:
            ident = self.ids.get(where)
            if not ident:
                if len(self.files) > 4000:
                    self.files.clear()
                    self.ids.clear()
                ident = os.urandom(6).hex()
                self.files[ident] = where
                self.ids[where] = ident
        return 'api/img/' + ident

    def url(self, path):
        path = _unwrap(path)
        if not path:
            return ''
        low = path.lower()
        if low.startswith(('http://', 'https://')):
            host = low.split('://', 1)[1].split('/', 1)[0].rsplit('@', 1)[-1].split(':', 1)[0]
            if host in ('127.0.0.1', 'localhost', '[::1]'):
                return self._id(path)
            return '' if any(word in low for word in _PRIVATE) else path
        try:
            import xbmcvfs
            real = xbmcvfs.translatePath(path) if '://' in path else path
        except Exception:
            real = path
        if os.path.splitext(real)[1].lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
            return ''
        if not os.path.isfile(real):
            return ''
        return self._id(real)

    def read(self, ident):
        with self.lock:
            where = self.files.get(ident)
        if not where:
            return None, ''
        if where.startswith(('http://', 'https://')):
            from urllib.request import urlopen
            with urlopen(where, timeout=6) as answer:
                ctype = answer.headers.get('Content-Type') or 'image/jpeg'
                if not ctype.startswith('image/'):
                    return None, ''
                return answer.read(8 * 1024 * 1024), ctype
        ext = os.path.splitext(where)[1].lower()
        ctype = {'.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}.get(ext, 'image/jpeg')
        with open(where, 'rb') as handle:
            return handle.read(), ctype


IMAGES = _Images()


def _art(item, *names):
    art = item.get('art') or {}
    for name in names:
        url = IMAGES.url(art.get(name))
        if url:
            return url
    return ''


def _row_items(page, row):
    """The first titles a row showed on the TV (its cache), for the preview."""
    from .skinui import rows as SR
    from .skinui import common as C
    try:
        spec = SR.spec_of(row, page)
        data = C.read_row(C.row_file(page, spec['id'])) or {}
    except Exception:
        return []
    out = []
    for item in (data.get('items') or [])[:10]:
        props = item.get('props') or {}
        if props.get('kind') == 'more':
            continue
        label = item.get('label') or props.get('title') or ''
        out.append({'t': label,
                    'p': _art(item, 'poster', 'thumb', 'landscape', 'fanart'),
                    'w': _art(item, 'landscape', 'fanart', 'thumb', 'poster'),
                    'b': _art(item, 'fanart', 'landscape'),
                    'l': _art(item, 'clearlogo')})
    return out


def _row_kind(row, spec):
    """A short line under a row's name: what it is."""
    from .homeui import layout as L
    meta = row.meta or {}
    if spec:
        if spec.get('community'):
            return 'community', 'Dex Community'
        if spec.get('type') == 'shelf':
            return 'collection', 'كوليكشن'
        return 'added', meta.get('source_label') or spec.get('source_label') or 'صف مضاف'
    if L.is_progress_row(row):
        return 'progress', 'تقدّم المشاهدة'
    if row.kind == 'collection':
        return 'collection', row.subtitle or 'مجموعة'
    return 'catalog', row.subtitle or 'كتالوج'


def _home(page=None):
    from .skinui import rows as SR
    from .homeui import layout as L
    from .homeui import community
    page = page if page in dict(PAGES) else 'all'
    app = SR.app()
    raw = _page_rows(page)
    customs = dict((c.get('id'), c) for c in app.layout.page(page)['custom'])
    out = []
    for row, hidden in app.layout.arrange(raw, page):
        meta = row.meta or {}
        spec = customs.get(meta.get('custom_id') or '')
        kind, line = _row_kind(row, spec)
        out.append({'key': row.key, 'label': row.title or row.key, 'shape': app.layout.shape(row.key),
                    'auto': row.shape if row.shape in L.SHAPE_CHOICES else 'poster', 'hidden': bool(hidden),
                    'progress': L.is_progress_row(row), 'shelf': bool(meta.get('shelf')), 'removable': bool(spec),
                    'community': (spec or {}).get('community') or '',
                    'kind': kind, 'line': line, 'missing': bool(meta.get('missing')),
                    'items': _row_items(page, row)})
    groups = []
    if page in _EDITABLE:
        try:
            for g in community.groups():
                if g.get('loose'):
                    continue
                groups.append({'id': g['id'], 'name': g['name'],
                               'folders': [{'id': f['id'], 'name': f['name']} for f in g.get('folders') or []]})
        except Exception:
            _log('community groups: %s' % traceback.format_exc(), xbmc.LOGWARNING)
    return {'page': page, 'pages': _pages(), 'rows': out, 'groups': groups,
            'editable': page in _EDITABLE}


# ------------------------------------------------------------------ the server
class Session(object):

    def __init__(self):
        self.token = os.urandom(9).hex()
        self.lock = threading.RLock()
        self.seen = 0.0             # the last request from the phone
        self.connected = False
        self.done = False
        self.codes = {}             # service: {code, url, link, status, error}
        self.silo_pending = None    # a Silo account waiting for its profile
        self.stremio_job = None
        self.changes = 0
        self.uploads = {}
        self.refresh_after = False  # v5.10.286: rows read again once the TV window closes

    def transfer(self, body):
        from .full_backup import context
        operation = body.get('op')
        if operation == 'restart':
            self.done = True
            xbmc.executebuiltin('RestartApp')
            return {'ok': True, 'queued': True}
        if operation == 'install':
            identifier = str(body.get('upload_id') or '')
            row = self.uploads.get(identifier)
            if not row or row.get('type') != '.zip': raise ValueError('Upload a Kodi addon ZIP first')
            result = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc':'2.0','id':1,
                'method':'Settings.GetSettingValue','params':{'setting':'videoplayer.dexhubnativeversion'}})))
            if result.get('result',{}).get('value',0) < 13:
                raise ValueError('Direct ZIP install needs the new Kodi v13 native build')
            xbmc.executebuiltin('DexHub.InstallZip(%s)' % json.dumps(row['path']))
            return {'ok':True,'queued':True,'message':'Confirm the install on Apple TV; Kodi will show the result.'}
        raise ValueError('Unknown transfer operation')

    def files(self, body):
        from .phone_files import operate, kodi_home
        with self.lock:
            return operate(kodi_home(), body)

    # -------------------------------------------------------------- state
    def state(self):
        services = {}
        for key, name, kind, _a, _o in SERVICES:
            row = {'name': name, 'kind': kind, 'linked': _linked(key)}
            with self.lock:
                pending = dict(self.codes.get(key) or {})
            if key == 'nuvio' and row['linked']:
                # v5.10.304: signed in with no profile chosen yet: the phone
                # shows the profiles, and the account counts as linked after
                choices = self._nuvio_choices(pick=False)
                if choices:
                    row['linked'] = False
                    pending = {'status': 'profile', 'profiles': choices}
            if pending and not row['linked']:
                row['pairing'] = pending
            if key == 'stremio':
                from .dexhub import nuvio_stremio_sync as sync
                row['sync'] = dict(enabled=_flag('stremio_sync_enabled',False),direction=sync._direction_for('stremio'), **sync._sections_for('stremio'))
                with self.lock:row['sync']['job'] = dict(self.stremio_job or {})
            services[key] = row
        from .dexhub.nuvio_stremio_sync import Nuvio
        identity = Nuvio.token()
        saved = _scope._read(os.path.join(_scope.root(), 'nuvio_profiles.json'), {})
        profiles = saved.get('rows', []) if saved.get('user') == identity.get('user_id') else []
        data = {
            'profile': dict(_scope.status(), name=identity.get('profile_name') or 'Local',
                            index=identity.get('profile_index'),
                            choices=[{'index': r.get('profile_index', r.get('profileIndex', r.get('index'))),
                                      'name': r.get('name') or r.get('title') or ''} for r in profiles if isinstance(r, dict)]),
            'tv': xbmc.getInfoLabel('System.FriendlyName') or 'Kodi',
            'services': services, 'addons': _addons(),
            'themes': _themes(), 'theme': _current_theme(),
            'toggles': dict((k, _flag(s, d)) for k, (s, d) in TOGGLES.items()),
            'subs_before': (_setting('subtitle_search_mode', 'Play with subtitles') != 'Play only'),
            'subs': [c for c in (_setting('preferred_subtitle_langs', 'ar,en') or '').split(',') if c],
            'meta': _meta(),
            'homeui_trailer_volume': _setting('homeui_trailer_volume', '30'),
            # v5.10.288: the size in force, as the TV's Menu shows it
            'homeui_poster_scale': _scale(),
            'trailer_status': xbmcgui.Window(10000).getProperty('dhs.trailer.notice'),
            'addon_version': _addon().getAddonInfo('version'),
            'skin_id': xbmc.getSkinDir(),
        }
        for key, values in LABELS:
            data[key] = _setting(key, values[0])
        # v5.10.286: every Home/display preference and control centre entry,
        # so the phone can show and set the new browsing options
        try:
            from . import ui_preferences as UP
            data['prefs'] = dict((k, UP.value(k)) for k in UP.OPTIONS)
            data['prefs_choices'] = dict((k, list(v[1]) if v[1] else None) for k, v in UP.OPTIONS.items())
            if xbmc.getSkinDir() != 'skin.dexhub':
                # v5.10.364: layouts and the tabs' place are skin.dexhub's
                data['prefs_choices']['homeui_layout_mode'] = []
                data['prefs'].pop('homeui_nav_position', None)
        except Exception:
            _log('prefs: %s' % traceback.format_exc(), xbmc.LOGWARNING)
            data['prefs'], data['prefs_choices'] = {}, {}
        try:
            from .homeui import control
            data['centre'] = control.phone_values()
            data['centre_choices'] = dict((k, [v for v, _l in opts]) for k, (_s, opts) in control.PICKS.items())
        except Exception:
            _log('centre values: %s' % traceback.format_exc(), xbmc.LOGWARNING)
            data['centre'], data['centre_choices'] = {}, {}
        for key, values in LABELS:
            # v5.10.286: the same reading the TV uses (aliases, defaults)
            if key in data['prefs']:
                data[key] = data['prefs'][key]
        try:
            data['home'] = _home('all')
        except Exception:
            _log('home rows: %s' % traceback.format_exc(), xbmc.LOGWARNING)
            data['home'] = {'page': 'all', 'pages': [], 'rows': [], 'groups': [], 'editable': True}
        return data

    # -------------------------------------------------------------- writes
    def profile(self, body):
        from .dexhub.nuvio_stremio_sync import Nuvio
        from .homeui import profile as profiles
        import xbmcaddon
        wanted = int(body.get('index'))
        row = next((r for r in Nuvio.profiles() if profiles._row_index(r) == wanted), None)
        if row is None or not Nuvio.select_profile(row):
            raise ValueError('Profile is unavailable')
        failed = profiles.pull(xbmcaddon.Addon(ADDON_ID), _log)
        with self.lock:
            linking = (self.codes.get('nuvio') or {}).get('status') == 'profile'
            if linking or body.get('link'):
                self.codes.pop('nuvio', None)
        if linking or body.get('link'):
            # v5.10.304: the profile picked on the phone right after the sign in
            _set('nuvio_sync_enabled', 'true')
            _log('nuvio linked from the phone (%s)' % (Nuvio.profile_name() or wanted))
            _republish('accounts')
        return {'ok': True, 'partial': failed, 'profile': _scope.status()}

    def _nuvio_choices(self, pick=True):
        """v5.10.304: [] once a Nuvio profile is in use (or the account has
        one, taken at once when pick is set); else the profiles to choose."""
        try:
            from .dexhub.nuvio_stremio_sync import Nuvio
            if Nuvio.has_profile():
                return []
            if pick and Nuvio.auto_profile():
                return []
            rows = Nuvio.profiles() or []
        except Exception as exc:
            _log('nuvio profiles: %s' % exc, xbmc.LOGWARNING)
            return []
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            try:
                index = int(r.get('profile_index', r.get('profileIndex', r.get('index'))))
            except (TypeError, ValueError):
                continue
            if index > 0:
                out.append({'index': index, 'name': str(r.get('name') or r.get('title') or ('Profile %d' % index))})
        return out

    def set(self, body):
        key, value = str(body.get('key') or ''), body.get('value')
        if key == 'theme':
            return self._theme(str(value))
        if key in TOGGLES:
            _set(TOGGLES[key][0], 'true' if value else 'false')
        elif key == 'homeui_poster_scale':
            if isinstance(value, bool) or not str(value).isdigit() or not 50 <= int(value) <= 200:
                raise ValueError('Poster size must be an integer from 50 to 200')
            _set(key, str(int(value)))
        elif key == 'homeui_trailer_volume':
            # Reject bad input rather than silently persisting NaN or out-of-range levels.
            if isinstance(value, bool) or not str(value).isdigit() or not 0 <= int(value) <= 100:
                raise ValueError('Volume must be an integer from 0 to 100')
            _set(key, str(int(value)))
        elif key in _ui_options():
            # v5.10.286: Home/display preferences (the older LABELS ones too,
            # such as the layout and the row source) go through the TV's own
            # path: ui_preferences.set_value (aliases, skin hooks, properties)
            self._ui_pref(key, value)
        elif key == 'subs_before':
            _set('subtitle_search_mode', 'Play with subtitles' if value else 'Play only')
        elif key == 'subs':
            codes = [str(c).strip().lower() for c in (value or []) if str(c).strip()]
            _set('preferred_subtitle_langs', ','.join(codes[:6]))
        elif key in dict(LABELS):
            if str(value) not in dict(LABELS)[key]:
                raise ValueError('bad value')
            _set(key, value)
            if key in ('homeui_metadata_language', 'server_metadata'):
                self._meta_refresh()
        elif key == 'meta_source':
            from . import meta_mode
            meta_mode.apply_source(str(value or 'auto'), notify=False)
        elif key == 'poster':
            _set('global_poster_source_id', str(value or 'same'))
            if value == 'betterposters':
                _set('betterposters_enabled', 'true')
            self._meta_refresh()
        elif key == 'tmdb_key':
            value = str(value or '').strip()
            if value:
                _set('tmdb_api_key', value)
        else:
            raise ValueError('unknown setting')
        _republish('phone')
        return {'ok': True}

    def _ui_pref(self, key, value):
        """v5.10.286: one ui_preferences option, set the way the TV sets it."""
        from . import ui_preferences as UP
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        value = str(value if value is not None else '')
        if UP.OPTIONS[key][1] is None:
            value = 'true' if value.strip().lower() == 'true' else 'false'
        if not UP.set_value(key, value):
            raise ValueError('bad value')

    def pref(self, body):
        """v5.10.286: a Home/display preference or a control centre entry."""
        key, value = str(body.get('key') or ''), body.get('value')
        options = _ui_options()
        if key in options:
            self._ui_pref(key, value)
            from . import ui_preferences as UP
            if key not in UP.DISPLAY_ONLY:
                _republish('phone')
            return {'ok': True, 'key': key, 'value': UP.value(key)}
        from .homeui import control
        if key in control.PICKS or key in control.TOGGLES or key == 'continue_split':
            later = control.apply_entry(key, value)
            if later:
                # v5.10.210: the rows are read again once the TV window closes
                with self.lock:
                    self.refresh_after = True
            return {'ok': True, 'key': key, 'value': control.phone_values().get(key)}
        raise ValueError('unknown setting')

    def _meta_refresh(self):
        try:
            from . import meta_mode
            meta_mode._refresh_everything(_addon())
        except Exception:
            pass

    def _theme(self, key):
        from . import skin_theme
        if key not in skin_theme.THEME_PRESETS:
            raise ValueError('unknown theme')
        _set('theme_preset', key)
        try:
            if xbmc.getSkinDir() == skin_theme.DEXHUB_SKIN:
                from . import theme_sync
                theme_sync.picked(key)
        except Exception:
            _log('skin colours: %s' % traceback.format_exc(), xbmc.LOGWARNING)
        try:
            skin_theme.publish_theme()
        except Exception:
            pass
        return {'ok': True}

    def addon(self, body):
        from .dexhub import api, store
        if body.get('op') == 'remove':
            store.remove_provider(str(body.get('id') or ''))
        else:
            url = str(body.get('url') or '').strip()
            if not url:
                raise ValueError('no url')
            if url.startswith('http://'):
                url = 'https://' + url[7:]       # the TV's own add flow upgrades the same way
            row = api.add_provider_from_url(url)
            _log('add-on added: %s' % ((row or {}).get('name') or '?'))
        _republish('sources')
        return {'ok': True, 'addons': _addons()}

    def login(self, body):
        service = str(body.get('service') or '')
        server, user = str(body.get('server') or '').strip(), str(body.get('user') or '').strip()
        password = str(body.get('password') or '')
        if service == 'stremio':
            from .dexhub import nuvio_stremio_sync as sync
            sync.Stremio.login(user, password)
            _set('stremio_sync_enabled', 'true')
            self._after_link('stremio')
        elif service in ('emby', 'jellyfin'):
            from . import emby_client
            emby_client.sign_in(server, user, password, backend=service)
            self._servers_changed(service)
        elif service == 'silo':
            # v5.10.242: the profile (and its PIN) is chosen on the phone next
            from .providers import silo_provider
            return self._silo_profiles(silo_provider.login_account(server, user, password))
        elif service == 'iptv':
            self._iptv(server, user, password)
        else:
            raise ValueError('unknown service')
        _log('%s linked from the phone' % service)
        return {'ok': True}

    def _iptv(self, server, user, password):
        """An Xtream account (or an M3U playlist when no account is given)."""
        import uuid
        from .skinui import rows as SR
        from .homeui import live_iptv as LI
        from .homeui import live_sources as LS
        profile = SR.app().profile
        try:
            host, q_user, q_pass = LI._xt_server(server)
        except ValueError:
            raise ValueError(_tr('رابط غير صالح، صححه'))
        user, password = user or q_user, password or q_pass
        now = int(time.time())
        if user and password:
            conf = {'id': uuid.uuid4().hex[:10], 'kind': 'xtream', 'name': LI._host(host), 'server': host,
                    'username': user, 'password': password, 'added': now}
            try:
                LI.login(conf)
                LI.categories(profile, conf, refresh=True)
            except Exception:
                LS.drop_cache(profile, conf['id'])
                raise
        else:
            url = server if '://' in server else 'http://' + server
            conf = {'id': uuid.uuid4().hex[:10], 'kind': 'm3u', 'name': LI._host(url), 'url': url,
                    'epg': '', 'added': now}
            LI.playlist(profile, conf, refresh=True)
        LS.save_source(profile, conf)
        _republish('live')

    def _servers_changed(self, service):
        try:
            from . import server_accounts
            server_accounts.changed(service)
        except Exception:
            pass
        _republish('accounts')

    def sync(self, body):
        """Explicit Stremio settings and one-shot background runs from phone."""
        from .dexhub import nuvio_stremio_sync as cloud
        if body.get('service') != 'stremio':
            raise ValueError('Unknown sync service')
        operation = body.get('op','run')
        if operation == 'status':
            with self.lock:return {'ok':True,'job':dict(self.stremio_job or {})}
        direction = body.get('direction','both')
        if direction not in ('pull','push','both'):
            raise ValueError('Unknown sync direction')
        if operation == 'configure':
            if any(type(body.get(k)) is not bool for k in ('enabled','addons','progress')):
                raise ValueError('Invalid sync switches')
            for field in ('enabled','addons','progress'):
                _set('stremio_sync_'+field,'true' if body[field] else 'false')
            _set('stremio_sync_direction',{'both':'Two-way','push':'Upload only','pull':'Download only'}[direction])
            return {'ok':True}
        if operation != 'run' or not cloud.Stremio.is_linked():
            raise ValueError('Link Stremio first')
        if not any(_flag('stremio_sync_'+k,True) for k in ('addons','progress')):
            raise ValueError('Select add-ons or watch progress first')
        with self.lock:
            if self.stremio_job and self.stremio_job.get('status') == 'running':
                return {'ok':True,'job':dict(self.stremio_job)}
            self.stremio_job = {'status':'running','direction':direction}
        def run():
            try:
                result = cloud.run_sync(direction=direction,targets=['stremio'],force_full=True,
                    sections={'addons':_flag('stremio_sync_addons',True),'progress':_flag('stremio_sync_progress',True),'collections':False,'library':False})
                job = {'status':'done','result':result}
                self.refresh_after = True
            except Exception as exc:
                job = {'status':'failed','error':cloud._short_url(str(exc))}
            with self.lock:self.stremio_job = job
        thread = threading.Thread(target=_scope.capture(run),name='StremioPhoneSync',daemon=True)
        thread.start()
        return {'ok':True,'job':{'status':'running','direction':direction}}

    def _after_link(self, service):
        """Pull the account's add-ons and Home the way the TV sign-in does."""
        _republish('accounts')
        xbmc.executebuiltin('RunPlugin(plugin://%s/?action=cloud_sync_now)' % ADDON_ID)

    def tv(self, body):
        row = _BY_KEY.get(str(body.get('service') or ''))
        if not row or not row[3]:
            raise ValueError('unknown service')
        xbmc.executebuiltin('RunPlugin(plugin://%s/?action=%s)' % (ADDON_ID, row[3]))
        return {'ok': True}

    def logout(self, body):
        service = str(body.get('service') or '')
        if service == 'plex':
            from . import plex_client
            plex_client.sign_out()
            self._servers_changed('plex')
        elif service in ('emby', 'jellyfin'):
            from . import emby_client
            emby_client.sign_out(service)
            self._servers_changed(service)
        elif service == 'silo':
            from .providers import silo_provider
            silo_provider.sign_out()
            self._servers_changed('silo')
        elif service == 'trakt':
            from . import trakt
            trakt.logout()
        elif service in ('nuvio', 'stremio'):
            from .routes import accounts
            accounts.logout(service, _tr)
        else:
            raise ValueError('unknown service')
        _republish('accounts')
        return {'ok': True}

    # -------------------------------------------------------------- codes
    def code(self, body):
        service = str(body.get('service') or '')
        if service == 'nuvio':
            from .dexhub.nuvio_stremio_sync import Nuvio
            st = Nuvio.start_tv_login(device_name='Dex Hub • Kodi')
            info = {'code': st['code'], 'url': st['url'], 'status': 'pending'}
            poll = lambda: (Nuvio.poll_tv_login(st) or {}).get('status')
            every, life = st.get('poll_interval') or 3, 600
        elif service == 'trakt':
            from . import trakt
            trakt.ensure_enabled()
            if not trakt.credentials_configured():
                raise ValueError(_tr('أدخل Trakt Client ID و Client Secret في الإعدادات أولاً'))
            c = trakt._request('/oauth/device/code', payload={'client_id': trakt.client_id()},
                               method='POST', auth=False) or {}
            info = {'code': c.get('user_code') or '', 'url': c.get('verification_url') or 'https://trakt.tv/activate',
                    'status': 'pending'}

            def poll():
                try:
                    token = trakt._request('/oauth/device/token', payload={
                        'code': c.get('device_code'), 'client_id': trakt.client_id(),
                        'client_secret': trakt.client_secret()}, method='POST', auth=False)
                except Exception:
                    return 'pending'
                if isinstance(token, dict) and token.get('access_token'):
                    trakt.save_token(token)
                    try:
                        trakt.invalidate_cache()
                    except Exception:
                        pass
                    return 'ok'
                return 'pending'
            every, life = max(3, int(c.get('interval') or 5)), int(c.get('expires_in') or 600)
        elif service == 'plex':
            from . import plex_client
            pin = plex_client.request_pin(strong=True)
            info = {'code': '', 'url': plex_client.auth_link_url(pin.get('code') or ''), 'status': 'pending'}

            def poll():
                linked = plex_client.poll_pin(pin.get('id'), pin.get('code'), flavor=pin.get('flavor') or 'v2')
                return 'ok' if linked else 'pending'
            every, life = 2, 300
        elif service == 'silo':
            # v5.10.242: the Silo site opens on the phone; after the approval
            # the profile is chosen on the phone too
            from .providers import silo_provider
            server = str(body.get('server') or '').strip()
            if not server:
                raise ValueError(_tr('اكتب عنوان السيرفر'))
            if '://' not in server:
                server = 'https://' + server
            pairing = silo_provider.start_device_login(server)
            url = str(pairing.get('verification_uri_complete') or pairing.get('verification_uri') or '')
            info = {'code': str(pairing.get('user_code') or ''), 'url': url, 'status': 'pending'}

            def poll():
                status, value = silo_provider.poll_device_login(pairing)
                if status == 'approved' and isinstance(value, dict) and value.get('token'):
                    return 'ok' if self._silo_profiles(value).get('linked') else 'profile'
                if status in ('denied', 'expired', 'consumed'):
                    return status
                return 'pending'
            every = max(2, min(int(pairing.get('interval') or 5), 10))
            life = max(60, min(int(pairing.get('expires_in') or 300), 900))
        else:
            raise ValueError('unknown service')
        if not info.get('url'):
            raise ValueError('no link')
        with self.lock:
            self.codes[service] = dict(info)
        threading.Thread(target=self._poll, args=(service, poll, every, life),
                         name='DexHubPhone-%s' % service, daemon=True).start()
        return dict(info, ok=True)

    def _poll(self, service, poll, every, life):
        end = time.time() + life
        monitor = xbmc.Monitor()
        status = 'pending'
        while time.time() < end and not monitor.abortRequested() and not self.done:
            try:
                status = str(poll() or 'pending').lower()
            except Exception as exc:
                _log('%s poll: %s' % (service, exc), xbmc.LOGDEBUG)
                status = 'pending'
            if status in ('ok', 'profile') or status in ('expired', 'cancelled', 'denied', 'rejected',
                                                          'exchange_failed', 'consumed'):
                break
            if monitor.waitForAbort(every):
                break
        if status == 'profile':
            return                      # the phone picks the profile now
        with self.lock:
            if service in self.codes:
                self.codes[service]['status'] = status if status != 'pending' else 'expired'
        if status == 'ok' and service == 'silo':
            with self.lock:
                self.codes.pop(service, None)
            return
        if status == 'ok' and service == 'nuvio':
            _set('nuvio_sync_enabled', 'true')
            choices = self._nuvio_choices()
            if choices:
                # v5.10.304: an account with several profiles: the phone asks
                # which one before anything is synced (the sync used to start
                # at once and fail five times with 'Select a Nuvio profile')
                with self.lock:
                    self.codes['nuvio'] = {'status': 'profile', 'profiles': choices}
                _log('nuvio approved on the phone; waiting for the profile')
                _republish('accounts')
                return
        if status == 'ok':
            _log('%s linked from the phone' % service)
            if service == 'nuvio':
                self._after_link('nuvio')
            elif service == 'plex':
                try:
                    from . import plex_client
                    plex_client.servers(force=True)
                except Exception:
                    pass
                self._servers_changed('plex')
            else:
                _republish('accounts')
            with self.lock:
                self.codes.pop(service, None)

    # -------------------------------------------------------------- Silo
    def _silo_profiles(self, pending):
        """A signed-in Silo account: one open profile links at once; else the
        phone shows the profiles (a locked one asks for its PIN there)."""
        from .providers import silo_provider
        profiles = [p for p in silo_provider.list_profiles(pending) or [] if isinstance(p, dict)]
        if not profiles:
            raise ValueError(_tr('لا توجد Profiles في هذا الحساب'))
        if len(profiles) == 1 and not profiles[0].get('has_pin'):
            self._silo_link(pending, profiles[0], '')
            return {'ok': True, 'linked': True}
        rows = [{'id': str(p.get('id') or ''), 'name': str(p.get('name') or 'Profile'),
                 'pin': bool(p.get('has_pin'))} for p in profiles]
        with self.lock:
            self.silo_pending = {'auth': dict(pending), 'profiles': dict((str(p.get('id') or ''), p) for p in profiles)}
            self.codes['silo'] = {'code': '', 'url': '', 'status': 'profile', 'profiles': rows}
        return {'ok': True, 'profiles': rows}

    def _silo_link(self, pending, profile, token):
        from .providers import silo_provider
        silo_provider.select_profile(pending, profile, token)
        _set('silo_in_sources', 'true')
        with self.lock:
            self.silo_pending = None
            self.codes.pop('silo', None)
        _log('silo linked from the phone (%s)' % (profile.get('name') or '?'))
        self._servers_changed('silo')

    def silo(self, body):
        """The profile picked on the phone (with its PIN when it has one)."""
        from .providers import silo_provider
        with self.lock:
            pending = dict(self.silo_pending or {})
        profile = (pending.get('profiles') or {}).get(str(body.get('profile') or ''))
        if not pending.get('auth') or not profile:
            raise ValueError(_tr('انتهى طلب الربط، ابدأ من جديد'))
        token = ''
        if profile.get('has_pin'):
            pin = str(body.get('pin') or '').strip()
            if not pin:
                raise ValueError(_tr('اكتب PIN البروفايل'))
            token = silo_provider.verify_pin(pending['auth'], profile.get('id'), pin)
        self._silo_link(pending['auth'], profile, token)
        return {'ok': True}

    # -------------------------------------------------------------- the Home
    def home(self, body):
        from .skinui import rows as SR
        from .homeui import community
        layout = SR.app().layout
        page = str(body.get('page') or 'all')
        if page not in dict(PAGES):
            raise ValueError('unknown page')
        op = body.get('op')
        if op == 'view':
            return {'ok': True, 'home': _home(page)}
        if op == 'arrange':
            keys = [str(k) for k in body.get('order') or []]
            hidden_in = dict((str(k), bool(v)) for k, v in (body.get('hidden') or {}).items())
            raw = _page_rows(page)
            defaults = dict((r.key, bool((r.meta or {}).get('default_hidden'))) for r in raw)
            hidden = dict((k, v) for k, v in hidden_in.items() if k in defaults and v != defaults[k])
            # rows this page does not show now (another server, a chip) keep their place
            known = [k for k in layout.page(page)['order'] if k not in defaults]
            layout.save_page(page, order=[k for k in keys if k in defaults] + known, hidden=hidden)
        elif op == 'shape':
            layout.set_shape(str(body.get('key') or ''), str(body.get('shape') or ''))
        elif op == 'reset':
            layout.reset_page(page)
        elif op == 'add':
            if page not in _EDITABLE:
                raise ValueError('rows are added on the Home pages')
            group = community._group(str(body.get('group') or ''))
            if not group:
                raise ValueError('unknown collection')
            picks = [str(f) for f in body.get('folders') or []]
            if picks and len(picks) < len(group.get('folders') or []):
                chosen = [(group, f) for f in group.get('folders') or [] if f['id'] in picks]
                community.add_folders(layout, page, group['name'], chosen)
            else:
                community.add_group(layout, page, group)
        elif op == 'remove':
            key = str(body.get('key') or '')
            current = layout.page(page)
            keep = [c for c in current['custom'] if 'my:%s' % c.get('id') != key]
            if len(keep) == len(current['custom']):
                raise ValueError('not a row you added')
            layout.save_page(page, order=[k for k in current['order'] if k != key], custom=keep)
        else:
            raise ValueError('unknown op')
        _republish('layout')
        return {'ok': True, 'home': _home(page)}

    def finish(self, body=None):
        self.done = True
        return {'ok': True}


class _Handler(BaseHTTPRequestHandler):
    session = None
    page = b''
    server_version = 'DexHub'

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8', cache='no-store'):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        s = self.session
        prefix = '/%s/' % s.token
        if not self.path.startswith(prefix):
            return None
        with s.lock:
            s.seen = time.time()
            s.connected = True
        return self.path[len(prefix):].split('?', 1)[0]

    def do_GET(self):
        route = self._route()
        if route is None:
            return self._send(404, {'ok': False})
        if route == '':
            return self._send(200, self.page, 'text/html; charset=utf-8')
        if route == 'api/files/download':
            temporary = None
            started = False
            try:
                from .phone_files import download, kodi_home
                from urllib.parse import quote
                query = parse_qs(urlsplit(self.path).query)
                with self.session.lock:
                    path, name, cleanup = download(kodi_home(), query.get('root', ['files'])[0], query.get('path', [''])[0])
                    temporary = path if cleanup else None
                    with open(path, 'rb') as handle:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Length', str(os.fstat(handle.fileno()).st_size))
                        self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(name, safe=''))
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('Referrer-Policy', 'no-referrer')
                        self.end_headers()
                        started = True
                        shutil.copyfileobj(handle, self.wfile, 64 * 1024)
            except Exception as error:
                if not started:
                    return self._send(400, {'ok': False, 'error': str(error)})
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return
        if route == 'api/backup/full':
            from .full_backup import context, create_archive, virtual_xml
            path = None
            started = False
            try:
                home, roots, settings, core = context()
                with self.session.lock:
                    descriptor, path = tempfile.mkstemp(prefix='dexhub-backup-',suffix='.zip')
                    os.close(descriptor)
                    create_archive(path, roots, settings, core, home/'media/Fonts', virtual_xml(roots))
                self.send_response(200)
                self.send_header('Content-Type','application/zip')
                self.send_header('Content-Length',str(os.path.getsize(path)))
                self.send_header('Content-Disposition','attachment; filename="dexhub-full-%s.zip"' % time.strftime('%Y%m%d-%H%M%S'))
                self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer')
                self.end_headers()
                started = True
                with open(path,'rb') as handle:shutil.copyfileobj(handle,self.wfile,64*1024)
            except Exception:
                if not started:
                    return self._send(400,{'ok':False,'error':'Could not create the full backup.'})
            finally:
                if path and os.path.exists(path):os.remove(path)
            return
        if route.startswith('api/img/'):
            try:
                data, ctype = IMAGES.read(route[len('api/img/'):])
            except Exception:
                data = None
            if data is None:
                return self._send(404, {'ok': False})
            return self._send(200, data, ctype, 'private, max-age=3600')
        if route == 'api/state':
            try:
                return self._send(200, self.session.state())
            except Exception as exc:
                _log('state: %s' % traceback.format_exc(), xbmc.LOGWARNING)
                return self._send(500, {'ok': False, 'error': str(exc)})
        return self._send(404, {'ok': False})

    def do_POST(self):
        route = self._route()
        if route is None:
            return self._send(404, {'ok': False})
        expected = self.headers.get('X-DexHub-Profile')
        if expected != _scope.active_key():
            return self._send(409, {'ok': False, 'error': 'Profile changed. Refresh this page before saving.'})
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            return self._send(403, {'ok': False, 'error':'Invalid request origin'})
        if route in ('api/backup/restore','api/upload','api/files/upload'):
            path = None
            try:
                from .full_backup import context, stage_restore, receive_file, LIMIT
                length=int(self.headers.get('Content-Length') or 0)
                if length < 0 or (length == 0 and route != 'api/files/upload') or length>LIMIT:return self._send(413,{'ok':False,'error':'Choose a file under 256 MiB'})
                descriptor,path=tempfile.mkstemp(prefix='dexhub-upload-',suffix='.bin')
                with os.fdopen(descriptor,'wb') as handle:
                    remaining=length
                    while remaining:
                        chunk=self.rfile.read(min(remaining,64*1024))
                        if not chunk:raise ValueError('Incomplete upload')
                        handle.write(chunk);remaining-=len(chunk)
                if route == 'api/files/upload':
                    from .phone_files import kodi_home
                    home = kodi_home()
                else:
                    home, roots, _, _ = context()
                with self.session.lock:
                    if route=='api/backup/restore':
                        from zipfile import is_zipfile
                        if is_zipfile(path):
                            from .json_restore import PENDING
                            from pathlib import Path
                            profile=roots.get('plugin.video.dexhub')
                            if profile and (Path(profile)/PENDING).exists():raise ValueError('Restart Kodi to apply the pending JSON restore first')
                            result=stage_restore(path,home)
                        else:
                            from .json_restore import stage
                            result=stage(path,home)
                    elif route == 'api/files/upload':
                        from .phone_files import upload
                        query = parse_qs(urlsplit(self.path).query)
                        result = upload(home, query.get('root', ['files'])[0],
                                        query.get('path', [''])[0], path,
                                        replace=query.get('replace', ['0'])[0] == '1')
                    else:
                        name=(parse_qs(urlsplit(self.path).query).get('name') or ['file'])[0]
                        result=receive_file(path,home,name)
                        self.session.uploads[result['upload_id']]=result
                        if result['type'] in ('.srt','.ass','.ssa','.vtt') and xbmc.Player().isPlayingVideo():
                            xbmc.Player().setSubtitles(result['path']);result['applied']=True
                    self.session.changes+=1
                return self._send(200,result)
            except Exception as error:
                return self._send(400,{'ok':False,'error':str(error) or 'Transfer failed'})
            finally:
                if path and os.path.exists(path):os.remove(path)
        try:
            length = min(int(self.headers.get('Content-Length') or 0), 2 * 1024 * 1024)
            body = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
        except Exception:
            return self._send(400, {'ok': False, 'error': 'bad request'})
        handlers = {'api/profile': 'profile', 'api/set': 'set', 'api/addon': 'addon', 'api/login': 'login', 'api/code': 'code',
                    'api/tv': 'tv', 'api/logout': 'logout', 'api/home': 'home', 'api/done': 'finish',
                    'api/silo': 'silo', 'api/transfer': 'transfer', 'api/files': 'files',
                    'api/pref': 'pref', 'api/sync':'sync'}  # v5.10.286
        name = handlers.get(route)
        if not name:
            return self._send(404, {'ok': False})
        try:
            result = getattr(self.session, name)(body if isinstance(body, dict) else {})
            with self.session.lock:
                if not (name == 'files' and (body.get('op') or 'list') == 'list'):
                    self.session.changes += 1
            return self._send(200, result)
        except Exception as exc:
            _log('%s: %s' % (route, traceback.format_exc()), xbmc.LOGWARNING)
            return self._send(200, {'ok': False, 'error': str(exc) or exc.__class__.__name__})


def _lan_ip():
    from .qr_pair import _lan_ip as ip
    return ip()


def _page():
    path = os.path.join(_addon().getAddonInfo('path'), 'resources', 'web', 'phone_setup.html')
    with open(path, 'rb') as handle:
        return handle.read()


# ------------------------------------------------------------------ the TV
class _Window(xbmcgui.WindowDialog):

    def __init__(self, *args, **kwargs):
        self.cancelled = False
        qr_path, url = kwargs.get('qr_path') or '', kwargs.get('url') or ''
        first = kwargs.get('first_run')
        cloud = kwargs.get('cloud') or {}
        # v5.10.378: on the first run the TV can also be linked to DexCloud
        # from the same screen (its own small QR and code under the text)
        w, h = (1120, 620) if cloud else (1000, 540)
        from . import pairing_ui
        x, y = pairing_ui.surface(self, w, h)
        pairing_ui.qr(self, qr_path, x + 60, y + 80, 380 if not cloud else 340)
        tx = x + (480 if not cloud else 450)
        tw = w - (tx - x) - 40
        self.addControl(xbmcgui.ControlLabel(tx, y + 60, tw, 44, '[B]%s[/B]' % _tr('جهّز Dex Hub من جوالك'),
                                             font='font14', textColor='FFFFFFFF'))
        self._body = xbmcgui.ControlTextBox(tx, y + 120, tw, 330 if not cloud else 250, font='font12', textColor='FFF0F3FA')
        self.addControl(self._body)
        self._lead = (_tr('امسح الباركود بكاميرا الجوال. تفتح لك صفحة تربط فيها حساباتك وترتب الصفحة الرئيسية وتختار الشكل والترجمة، وكل شي يوصل للتلفزيون على طول.')
                      + '\n\n' + _tr('الجوال والتلفزيون لازم يكونون على نفس الشبكة.') + '\n'
                      + '[COLOR FF8FA0C0]%s[/COLOR]' % url)
        if cloud:
            white = os.path.join(_addon().getAddonInfo('path'), 'resources', 'media', 'white.png')
            self.addControl(xbmcgui.ControlImage(tx, y + 392, tw, 2, white, colorDiffuse='33FFFFFF'))
            pairing_ui.qr(self, cloud.get('qr') or '', tx + 10, y + 418, 160)
            note = xbmcgui.ControlTextBox(tx + 200, y + 410, tw - 200, 190, font='font12', textColor='FFF0F3FA')
            self.addControl(note)
            note.setText('[B]%s[/B]\n%s\n%s [B]%s[/B]\n[COLOR FF8FA0C0]%s[/COLOR]' % (
                _tr('عندك حساب DexCloud؟'),
                _tr('امسح هذا الرمز واربط التلفزيون بحسابك، ويجيه إعدادك كله من أي مكان.'),
                _tr('أو افتح الموقع واكتب الرمز:'), cloud.get('code') or '',
                cloud.get('host') or ''))
        self._back = _tr('رجوع: أكمل على التلفزيون') if first else _tr('رجوع: إغلاق')
        self.set_status(_tr('بانتظار الجوال…'))

    def set_status(self, text):
        try:
            self._body.setText('%s\n\n[B]%s[/B]\n\n[COLOR FF8FA0C0]%s[/COLOR]' % (self._lead, text, self._back))
        except Exception:
            pass

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
            self.cancelled = True
            self.close()


def _cloud_offer(first_run):
    """v5.10.378: the first run also offers DexCloud: a pairing code from the
    hub and a thread that looks every 3 s whether the phone approved it.
    ({} , state) when not the first run, already linked or the hub is out of reach."""
    state = {'done': False, 'stop': False}
    if not first_run:
        return {}, state
    try:
        from . import hub_client
        if hub_client.linked():
            return {}, state
        start = hub_client.pair_start(timeout=6)
    except Exception as exc:
        _log('DexCloud code not offered: %s' % exc)
        return {}, state
    qr = ''
    try:
        from .plex_qr import qr_png
        qr = qr_png(start.get('url') or '', box_size=6, border=3, family='dexcloud_qr') or ''
    except Exception:
        qr = ''

    def watch():
        monitor = xbmc.Monitor()
        ends = time.time() + hub_client.PAIR_TTL
        while not state['stop'] and time.time() < ends and not monitor.waitForAbort(3):
            done = hub_client.pair_check(start)
            if done is None:
                return
            if done:
                state['done'] = True
                _log('linked to DexCloud from the first run screen')
                return
    threading.Thread(target=watch, name='DexHub-first-run-cloud', daemon=True).start()
    host = hub_client.base().replace('https://', '').replace('http://', '').rstrip('/')
    return {'qr': qr, 'code': start.get('code') or '', 'host': host}, state


def run(first_run=False, section=''):
    """Show the QR and serve the page until the phone finishes or the TV
    closes it. True when the phone changed something."""
    session = Session()
    try:
        page = _page()
    except Exception:
        _log('page missing: %s' % traceback.format_exc(), xbmc.LOGERROR)
        return False
    handler = type('PhoneHandler', (_Handler,), {'session': session, 'page': page})
    ip = _lan_ip()
    try:
        server = ThreadingHTTPServer((ip, 0), handler)
    except Exception as exc:
        xbmcgui.Dialog().ok('Dex Hub', _tr('تعذر فتح صفحة الإعداد على الشبكة: %s') % exc)
        return False
    server.daemon_threads = True
    url = 'http://%s:%d/%s/' % (ip, server.server_address[1], session.token)
    if section in ('backup','files'):url += '#' + section
    threading.Thread(target=server.serve_forever, name='DexHubPhoneSetup', daemon=True).start()
    _log('serving on %s:%d' % (ip, server.server_address[1]))
    qr_path = ''
    try:
        from .plex_qr import qr_png
        qr_path = qr_png(url, box_size=8, border=3) or ''
    except Exception:
        pass
    home = xbmcgui.Window(10000)
    home.setProperty('dexhub.phone.url', url)
    cloud, linked = _cloud_offer(first_run)
    win = _Window(qr_path=qr_path, url=url, first_run=first_run, cloud=cloud)
    win.show()
    started = time.time()
    monitor = xbmc.Monitor()
    shown = (False, -1)
    try:
        while not monitor.abortRequested():
            if win.cancelled or session.done or linked['done']:
                break
            with session.lock:
                seen, connected, changes = session.seen, session.connected, session.changes
            now = time.time()
            if (connected and now - seen > IDLE_SECONDS) or (not connected and now - started > FIRST_WAIT):
                break
            if (connected, changes) != shown:
                shown = (connected, changes)
                if connected:
                    win.set_status(_tr('الجوال متصل ✓') + (('  •  ' + _tr('%d تغيير') % changes) if changes else ''))
            if monitor.waitForAbort(0.5):
                break
    finally:
        try:
            win.close()
        except Exception:
            pass
        session.done = True
        home.clearProperty('dexhub.phone.url')
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    linked['stop'] = True
    if linked['done']:
        # v5.10.378: linked to DexCloud from this screen: the account's setup
        try:
            from . import hub_client
            hub_client.after_pair()
        except Exception:
            _log('DexCloud after linking: %s' % traceback.format_exc(), xbmc.LOGWARNING)
        return True
    if session.refresh_after:
        # v5.10.286: Following and library choices from the phone (the control
        # centre does the same once it closes)
        try:
            from .routes.simple_entry import _refresh_continue
            _refresh_continue()
        except Exception:
            pass
    if session.changes:
        _republish('phone')
        xbmcgui.Dialog().notification('Dex Hub', _tr('انحفظ إعداد الجوال ✓'), xbmcgui.NOTIFICATION_INFO, 3500)
    return bool(session.changes)
