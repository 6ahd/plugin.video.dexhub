"""Account/profile storage boundaries shared by TV, phone setup and sync.

The Nuvio login remains device-wide. Content state belongs to the stable
account id and numeric profile index, never to a display name or access token.
Jobs pin that identity for their lifetime and cannot publish after a switch.
"""
import contextlib
import functools
import hashlib
import json
import os
import sqlite3
import threading
import uuid
import time

ADDON_ID = 'plugin.video.dexhub'
_local = threading.local()
_lock = threading.RLock()


def root():
    import xbmcaddon
    import xbmcvfs
    return xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('profile'))


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + '.tmp.' + uuid.uuid4().hex
    try:
        with open(temp, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def token_key(token):
    token = token if isinstance(token, dict) else {}
    owner = str(token.get('user_id') or '')
    if not owner:
        return 'local'
    index = token.get('profile_index')
    # UUIDs and names can change independently of Nuvio's RPC profile index.
    identity = owner + ':' + str(index if index is not None else 'unselected')
    return 'nuvio-' + hashlib.sha256(identity.encode()).hexdigest()[:32]


def active_key():
    return token_key(_read(os.path.join(root(), 'nuvio_token.json'), {}))


def key():
    return getattr(_local, 'scope', None) or active_key()


def is_current():
    try:
        return key() == active_key() and not os.path.exists(os.path.join(root(), 'profile_transition.json'))
    except RuntimeError:
        # Kodi temporarily unregisters an add-on while replacing its ZIP. Old
        # workers must stop publishing; never fall back to another profile.
        return False


def require_current():
    if not is_current():
        raise RuntimeError('Profile changed; refresh this page and retry')


@contextlib.contextmanager
def bind(scope=None):
    previous = getattr(_local, 'scope', None)
    _local.scope = scope or previous or active_key()
    try:
        yield _local.scope
    finally:
        _local.scope = previous


def bound(fn):
    @functools.wraps(fn)
    def call(*args, **kwargs):
        with bind():
            return fn(*args, **kwargs)
    return call


def capture(fn):
    scope = key()
    @functools.wraps(fn)
    def call(*args, **kwargs):
        with bind(scope):
            if not is_current():
                return None
            return fn(*args, **kwargs)
    return call


_INIT = {'done': False}


def _ensure_initialized():
    """v5.10.292: the legacy files move into the profile on the first scoped
    read of every process. Before, only a profile switch ran initialize(), so
    an install upgraded from 5.10.259 read empty profile folders (no Home
    rows, collections, sources or Nuvio order) until a switch."""
    if _INIT['done']:
        return
    _INIT['done'] = True
    try:
        initialize()
    except Exception:
        pass


def path(relative='', scope=None):
    if os.path.isabs(relative) or '..' in relative.replace('\\', '/').split('/'):
        raise ValueError('Invalid profile path')
    _ensure_initialized()
    scope = scope or key()
    base = os.path.join(root(), 'profiles', scope)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, relative) if relative else base


class ScopedPath(os.PathLike):
    """Resolve module-level filenames at use time in long-lived Kodi services."""
    def __init__(self, relative):
        self.relative = relative
    def __fspath__(self):
        return path(self.relative)
    def __str__(self):
        return os.fspath(self)
    def __add__(self, suffix):
        return os.fspath(self) + suffix


def migrate_file(relative):
    """Only the first profile owns legacy data; never copy it to every child.

    Kept as an explicit startup step so reads cannot resurrect deleted data.
    SQLite's backup API includes committed WAL data. Originals stay intact.
    """
    source, target = os.path.join(root(), relative), path(relative)
    if not os.path.isfile(source) or os.path.exists(target):
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(source, 'rb') as handle:
        sqlite = handle.read(16) == b'SQLite format 3\x00'
    if sqlite:
        from pathlib import Path
        a = sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)
        b = sqlite3.connect(target)
        try:
            a.backup(b)
        finally:
            a.close()
            b.close()
    else:
        import shutil
        shutil.copy2(source, target)


FILES = ('favorites.db', 'playback.db', 'providers.json', 'catalog_state.json',
         'collection_sets.json', 'active_collection.json', 'dex_collection_prefs.json',
         'nuvio_home_mirror.json', 'nuvio_profile_prefs.json', 'nuvio_enabled_addons.json',
         'account_sync_fp.json', 'homeui/layout.json', 'homeui/grid_filters.json',
         'homeui/live_favorites.json', 'tracking_state.json', 'sort_prefs.json')


# v5.10.292: what a profile's first visit takes from the profile it comes
# from. Arrangement and sources always; the rest (progress, favorites,
# tracking) only when it comes from the account before any profile was
# chosen (the same person). Nuvio's own data is pulled again for the profile.
ARRANGEMENT = ('homeui/layout.json', 'homeui/grid_filters.json', 'homeui/live_favorites.json',
               'collection_sets.json', 'active_collection.json', 'dex_collection_prefs.json',
               'sort_prefs.json', 'providers.json', 'catalog_state.json')
PERSONAL = ('favorites.db', 'playback.db', 'tracking_state.json')


def _first_visit(scope):
    base = os.path.join(root(), 'profiles', scope)
    return not os.path.exists(os.path.join(base, 'preferences.json'))


def _copy_missing(source_dir, target_dir, names):
    copied = 0
    for relative in names:
        source = os.path.join(source_dir, relative)
        target = os.path.join(target_dir, relative)
        if not os.path.isfile(source) or os.path.exists(target):
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(source, 'rb') as handle:
                sqlite = handle.read(16) == b'SQLite format 3\x00'
            if sqlite:
                from pathlib import Path
                a = sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)
                b = sqlite3.connect(target)
                try:
                    a.backup(b)
                finally:
                    a.close()
                    b.close()
            else:
                import shutil
                shutil.copy2(source, target)
            copied += 1
        except Exception:
            pass
    return copied


def seed_first_use(previous, following, relative=None, full=False):
    """v5.10.286 (issue #12), v5.10.292: a profile opened for the first time
    starts from the profile it comes from: its Home rows, collections,
    sources and catalog state (and with ``full``, coming from the account
    before a profile was chosen, its progress and favorites too). After that
    each profile keeps its own. Never raises."""
    try:
        if not previous or not following or previous == following or not _first_visit(following):
            return False
        names = ARRANGEMENT + (PERSONAL if full else ())
        if relative:
            names = (relative,)
        src = os.path.join(root(), 'profiles', previous)
        dst = os.path.join(root(), 'profiles', following)
        return _copy_missing(src, dst, names) > 0
    except Exception:
        return False


def initialize():
    transition = os.path.join(root(), 'profile_transition.json')
    pending = _read(transition, {})
    if pending and (pending.get('pid') != os.getpid() or time.time() - pending.get('at', 0) > 120):
        # If the token never committed, restore the departing preferences.
        if active_key() != pending.get('next'):
            import xbmcaddon
            old = _read(path('preferences.json', scope=pending.get('previous') or active_key()), {})
            for sid, value in old.items():
                xbmcaddon.Addon(ADDON_ID).setSetting(sid, str(value))
        os.unlink(transition)
    with _lock:
        marker = os.path.join(root(), 'profile_storage_v1.json')
        state = _read(marker, {})
        if not state:
            state = {'owner': key(), 'complete': False}
            _write(marker, state)
        if state.get('owner') == key() and not state.get('complete'):
            for relative in FILES:
                migrate_file(relative)
            state['complete'] = True
            _write(marker, state)
        if not state.get('repair_v292'):
            state['repair_v292'] = True
            _write(marker, state)
            _repair_first_visit(state.get('owner') or '')


def _account_scopes():
    """The scopes of this account before a profile was chosen: 'local' (no
    account id) and the account with no profile index."""
    token = _read(os.path.join(root(), 'nuvio_token.json'), {})
    token = token if isinstance(token, dict) else {}
    out = {'local'}
    if token.get('user_id'):
        out.add(token_key({'user_id': token.get('user_id')}))
    return out


def _repair_first_visit(owner):
    """v5.10.292: 5.10.285 to 5.10.291 opened a profile for the first time
    with the default settings and empty folders (Home not in Nuvio's order,
    metadata no longer the source's). When the old data went to the account
    before a profile was chosen and the profile in use has not been left
    since, its missing files come from there, and every setting still at its
    default takes the value it had there. Nothing set since is touched."""
    current = key()
    if not owner or owner == current or owner not in _account_scopes() or not _first_visit(current):
        return
    src = os.path.join(root(), 'profiles', owner)
    dst = os.path.join(root(), 'profiles', current)
    if not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    _copy_missing(src, dst, ARRANGEMENT + PERSONAL)
    old = _read(os.path.join(src, 'preferences.json'), {})
    if isinstance(old, dict) and old:
        import xbmcaddon
        addon = xbmcaddon.Addon(ADDON_ID)
        for sid, default in preference_defaults().items():
            if sid in old and addon.getSetting(sid) == default and str(old[sid]) != default:
                addon.setSetting(sid, str(old[sid]))
    skin = _read(os.path.join(src, 'skin_preferences.json'), {})
    if isinstance(skin, dict):
        import xbmc
        for name, value in skin.items():
            if value and not xbmc.getInfoLabel('Skin.String(%s)' % name):
                xbmc.executebuiltin('Skin.SetString(%s,%s)' % (name, json.dumps(str(value))))


def preference_defaults():
    import xbmcaddon
    import xml.etree.ElementTree as ET
    addon = xbmcaddon.Addon(ADDON_ID)
    descriptor = os.path.join(addon.getAddonInfo('path'), 'resources', 'settings.xml')
    # Hardware, authentication and service scheduling stay device-wide.
    prefixes = ('homeui_', 'library_', 'meta_', 'metadata_', 'source_',
                'continue_', 'episode_', 'poster_', 'badge_', 'player_badge',
                'subtitle_', 'subtitles_', 'tracking_', 'preferred_', 'nuvio_home_',
                'global_meta_', 'global_poster_', 'badges_', 'home_', 'betterposters_', 'sources_', 'tmdbh_poster_')
    explicit = {'fanarttv_enrich', 'player_show_clearlogo', 'ui_language', 'catalog_click_mode', 'quality_profile', 'autoplay_next', 'default_meta_source', 'deep_meta_enrich',
                'provider_meta_map', 'server_metadata', 'enable_source_formatter',
                'stremio_sources_enabled', 'quick_same_source_play', 'remember_last_source',
                'next_episode_same_source', 'player_show_metadata', 'iptv_in_continue'}
    out = {}
    for node in ET.parse(descriptor).getroot().iter('setting'):
        sid = node.get('id') or ''
        if (sid.startswith(prefixes) or sid in explicit) and node.get('type') not in ('action', 'sep', 'lsep'):
            out[sid] = node.get('default', '')
    return out


def switch_settings(new_key):
    """Save departing preferences and activate the next profile's defaults."""
    import xbmcaddon
    addon = xbmcaddon.Addon(ADDON_ID)
    defaults = preference_defaults()
    old = {sid: addon.getSetting(sid) for sid in defaults}
    _write(path('preferences.json'), old)
    first = _first_visit(new_key)
    # v5.10.292: a first visit keeps the settings in use (it took the defaults)
    incoming = dict(old) if first else _read(path('preferences.json', scope=new_key), {})
    import xbmc
    skin_names = ('DexHub.CardSize', 'DexHub.BadgesPlace')
    skin_old = {name: xbmc.getInfoLabel('Skin.String(%s)' % name) for name in skin_names}
    _write(path('skin_preferences.json'), skin_old)
    skin_new = dict(skin_old) if first else _read(path('skin_preferences.json', scope=new_key), {})
    try:
        for sid, default in defaults.items():
            value = str(incoming.get(sid, default))
            if old[sid] != value:
                xbmcaddon.Addon(ADDON_ID).setSetting(sid, value)
        for name in skin_names:
            value = skin_new.get(name, '')
            if value:
                xbmc.executebuiltin('Skin.SetString(%s,%s)' % (name, json.dumps(str(value))))
            else:
                xbmc.executebuiltin('Skin.Reset(%s)' % name)
    except Exception:
        for sid, value in old.items():
            xbmcaddon.Addon(ADDON_ID).setSetting(sid, value)
        raise


def status():
    return {'scope': active_key(), 'isolated': True,
            'preferences': 'profile', 'device_accounts': 'shared',
            'stremio_writes': __import__('xbmcaddon').Addon(ADDON_ID).getSetting('stremio_progress_write_opt_in') == 'true'}
