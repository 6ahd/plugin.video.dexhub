# -*- coding: utf-8 -*-
"""Backup and restore, part by part (v5.10.212).

A backup is one JSON file. The user picks what goes into it, and later
what comes back out of it, from these parts:

  settings     every Dex Hub setting (sign-ins and keys are in accounts)
  sources      the Stremio / Nuvio add-ons, their catalogs, the folders
  home         the Home's rows, their order and shapes, Dex Community's rows
  collections  the collections (imported, Nuvio's) and which one leads
  library      your own favourites
  progress     where you are in every title (Continue Watching)
  accounts     Trakt, Simkl, Nuvio, Stremio, Plex, the servers' sign-ins and
               the keys (TMDb, MDBList...): the file holds them, keep it safe

Files of the profile are copied as they are (small JSON). The favourites and
the progress go through their stores (their databases stay open while Kodi
runs). Files written back are read anew after Kodi restarts, so the restore
offers to restart it.

The old export (config_export, dexhub_export_version 1) is still read.
"""
import base64
import json
import os
import time
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

FORMAT = 'dexhub_backup'
VERSION = 2

# (key, title, line)
PARTS = (
    ('settings', 'الإعدادات', 'كل خيارات Dex Hub'),
    ('sources', 'المصادر والإضافات', 'إضافات Stremio وNuvio وكتالوجاتها ومجلداتها'),
    ('home', 'الرئيسية وشكلها', 'الصفوف وترتيبها وأشكالها وصفوف Dex Community'),
    ('collections', 'المجموعات', 'المجموعات المستوردة وNuvio والمجموعة الأساسية'),
    ('library', 'المفضلة', 'مفضلتك المحلية'),
    ('progress', 'متابعة المشاهدة', 'وين وصلت في كل فيلم وحلقة'),
    ('accounts', 'الحسابات والمفاتيح', 'Trakt وSimkl وNuvio وPlex والسيرفرات ومفاتيح TMDb وغيرها'),
)
FILES = {
    'sources': ('providers.json', 'hub_catalog_pins.json', 'hub_home_folders.json', 'hub_home_subfolders.json',
                'hub_folder_prefs.json', 'hub_folder_filters.json', 'bucket_meta_map.json', 'catalog_state.json',
                'sort_prefs.json', 'nuvio_enabled_addons.json', 'last_source_map.json'),
    'home': ('homeui/layout.json', 'homeui/live_favorites.json', 'homeui/profiles.json'),
    'collections': ('collection_sets.json', 'active_collection.json', 'dex_collection_prefs.json'),
    'accounts': ('trakt_token.json', 'simkl_token.json', 'nuvio_token.json', 'stremio_token.json',
                 'nuvio_profiles.json', 'nuvio_profile_prefs.json',
                 'plex_account.json', 'plex_servers.json', 'plex_client_id.txt'),
}
# settings that are sign-ins or keys travel with accounts
_SECRET = ('auth', 'token', 'password', 'passwd', 'api_key', 'apikey', 'secret', 'session', 'cookie', 'pin_code',
           'user_key', 'access')


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _profile():
    return xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile'))


def _secret(setting_id):
    low = setting_id.lower()
    return any(mark in low for mark in _SECRET)


def _settings():
    addon = xbmcaddon.Addon('plugin.video.dexhub')
    descriptor = os.path.join(addon.getAddonInfo('path'), 'resources', 'settings.xml')
    return {node.get('id'): addon.getSetting(node.get('id'))
            for node in ET.parse(descriptor).getroot().iter('setting')
            if node.get('id') and node.get('type') not in ('action', 'sep', 'lsep')}


# ---------------------------------------------------------------- collecting
def _file_path(name):
    from . import profile_scope
    if name in profile_scope.FILES or name.startswith('homeui/'):
        return profile_scope.path(name)
    return os.path.join(_profile(), *name.split('/'))


def _files(names):
    root = _profile()
    out = {}
    for name in names:
        path = _file_path(name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'rb') as handle:
                out[name] = base64.b64encode(handle.read()).decode('ascii')
        except Exception:
            continue
    return out


def collect(parts):
    """The backup's body for the chosen ``parts``."""
    data = {'format': FORMAT, 'version': VERSION, 'created': int(time.time()),
            'plugin_version': xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('version'), 'parts': {}}
    settings = _settings() if ('settings' in parts or 'accounts' in parts) else {}
    for part in parts:
        body = {}
        if part == 'settings':
            body['settings'] = dict((k, v) for k, v in settings.items() if not _secret(k))
        if part == 'accounts':
            body['settings'] = dict((k, v) for k, v in settings.items() if _secret(k))
        if part in FILES:
            body['files'] = _files(FILES[part])
        if part == 'library':
            from . import favorites_store
            body['favorites'] = favorites_store.list_favorites(limit=0, source='local') or []
        if part == 'progress':
            from . import playback_store
            body['playback'] = playback_store.list_recent_items(limit=20000) or []
        data['parts'][part] = body
    return data


# ---------------------------------------------------------------- restoring
def _write_file(name, b64):
    path = _file_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.restore'
    with open(tmp, 'wb') as handle:
        handle.write(base64.b64decode(b64))
    os.replace(tmp, path)


def restore(data, parts, strict=False):
    """Write the chosen ``parts`` back; returns {part: count}."""
    addon = xbmcaddon.Addon('plugin.video.dexhub')
    done = {}
    for part in parts:
        body = (data.get('parts') or {}).get(part) or {}
        count = 0
        for sid, value in (body.get('settings') or {}).items():
            try:
                addon.setSetting(sid, '' if value is None else str(value))
                count += 1
            except Exception:
                if strict:raise
                pass
        for name, b64 in (body.get('files') or {}).items():
            if name not in FILES.get(part, ()):
                continue        # only the files this part owns
            try:
                _write_file(name, b64)
                count += 1
            except Exception as exc:
                if strict:raise
                xbmc.log('[DexHub] restore %s: %s' % (name, exc), xbmc.LOGWARNING)
        if part == 'library':
            from . import favorites_store
            for row in body.get('favorites') or []:
                try:
                    favorites_store.add(row.get('media_type') or 'movie', row.get('canonical_id') or '',
                                        row.get('title') or '', poster=row.get('poster') or '',
                                        background=row.get('background') or '', clearlogo=row.get('clearlogo') or '',
                                        year=row.get('year') or 0, plot=row.get('plot') or '', source='local')
                    count += 1
                except Exception:
                    if strict:raise
                    continue
        if part == 'progress':
            from . import playback_store
            rows = []
            for row in body.get('playback') or []:
                row = dict(row)
                row['ext_updated_at'] = row.get('updated_at')
                rows.append(row)
            if rows:
                try:
                    count += int(playback_store.upsert_entries(rows, mark_dirty=False) or 0)
                except Exception as exc:
                    if strict:raise
                    xbmc.log('[DexHub] restore progress: %s' % exc, xbmc.LOGWARNING)
        done[part] = count
    try:
        from . import settings_cache
        settings_cache.invalidate()
    except Exception:
        pass
    return done


# ---------------------------------------------------------------- the dialogs
def _choose_parts(title, available, preselect_all=True):
    labels = []
    for key, name, line in PARTS:
        if key in available:
            labels.append(xbmcgui.ListItem(_t(name, _EN.get(name, name)), _t(line, _EN.get(line, line))))
    keys = [key for key, _n, _l in PARTS if key in available]
    picked = xbmcgui.Dialog().multiselect(title, labels, preselect=list(range(len(keys))) if preselect_all else [],
                                          useDetails=True)
    if not picked:
        return []
    return [keys[i] for i in picked]


def backup_dialog():
    if xbmc.getCondVisibility('System.Platform.TVOS'):
        from .phone_setup import run
        return run(section='backup')
    parts = _choose_parts(_t('نسخة احتياطية: وش تحفظ؟', 'Backup: what to keep?'), [k for k, _n, _l in PARTS])
    if not parts:
        return False
    if 'accounts' in parts and not xbmcgui.Dialog().yesno('Dex Hub', _t(
            'الملف بيكون فيه دخول حساباتك ومفاتيحك. احفظه في مكان آمن ولا تشاركه. نكمل؟',
            'The file will hold your sign-ins and keys. Keep it somewhere safe and do not share it. Go on?')):
        return False
    folder = xbmcgui.Dialog().browse(3, _t('وين تحفظ الملف؟', 'Where to save the file?'), 'files')
    if not folder:
        return False
    data = collect(parts)
    name = 'dexhub-backup-%s.json' % time.strftime('%Y%m%d-%H%M')
    path = os.path.join(folder, name) if not folder.endswith(('/', '\\')) else folder + name
    try:
        handle = xbmcvfs.File(path, 'w')
        try:
            handle.write(json.dumps(data, ensure_ascii=False))
        finally:
            handle.close()
    except Exception as exc:
        xbmcgui.Dialog().ok('Dex Hub', _t('تعذر الحفظ: %s', 'Could not save: %s') % exc)
        return False
    xbmcgui.Dialog().ok('Dex Hub', _t('انحفظت النسخة:\n%s', 'Backup saved:\n%s') % path)
    return True


def read_file(path):
    handle = xbmcvfs.File(path)
    try:
        raw = handle.readBytes()
    finally:
        handle.close()
    return json.loads(bytes(raw).decode('utf-8-sig'))


def restore_dialog():
    if xbmc.getCondVisibility('System.Platform.TVOS'):
        from .phone_setup import run
        return run(section='backup')
    path = xbmcgui.Dialog().browse(1, _t('ملف النسخة الاحتياطية', 'Backup file'), 'files', '.json')
    if not path:
        return False
    try:
        data = read_file(path)
    except Exception as exc:
        xbmcgui.Dialog().ok('Dex Hub', _t('تعذر قراءة الملف: %s', 'Could not read the file: %s') % exc)
        return False
    if isinstance(data, dict) and data.get('dexhub_export_version'):
        # the old export (settings, sources and pins together)
        from . import plugin as P
        P.config_import_data(data)
        return True
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        xbmcgui.Dialog().ok('Dex Hub', _t('هذا الملف ليس نسخة Dex Hub', 'This file is not a Dex Hub backup'))
        return False
    available = list((data.get('parts') or {}).keys())
    when = time.strftime('%Y-%m-%d %H:%M', time.localtime(int(data.get('created') or 0)))
    parts = _choose_parts(_t('استرجاع (%s): وش ترجّع؟', 'Restore (%s): what to bring back?') % when, available)
    if not parts:
        return False
    if not xbmcgui.Dialog().yesno('Dex Hub', _t('اللي تختاره يحل محل الحالي. نكمل؟',
                                                'What you pick replaces what is here now. Go on?')):
        return False
    done = restore(data, parts)
    names = dict((k, _t(n, _EN.get(n, n))) for k, n, _l in PARTS)
    lines = '\n'.join('• %s: %d' % (names.get(k, k), v) for k, v in done.items())
    if any(p in parts for p in ('sources', 'home', 'collections', 'accounts', 'settings')):
        if xbmcgui.Dialog().yesno('Dex Hub', _t('رجعت:\n%s\n\nنعيد تشغيل Kodi الحين عشان يقرأها؟',
                                                'Restored:\n%s\n\nRestart Kodi now so it reads them?') % lines):
            xbmc.executebuiltin('RestartApp')
    else:
        xbmcgui.Dialog().ok('Dex Hub', _t('رجعت:\n%s', 'Restored:\n%s') % lines)
    return True


def menu():
    """Backup or restore (Menu, System)."""
    if xbmc.getCondVisibility('System.Platform.TVOS'):
        from .phone_setup import run
        return run(section='backup')
    from .homeui.options import choose
    options = [_t('نسخة احتياطية جديدة', 'New backup'), _t('استرجاع من نسخة', 'Restore from a backup')]
    pick = choose(_t('النسخ الاحتياطي', 'Backup'), options,
                  subtitle=_t('تختار وش تحفظ ووش ترجّع: كل الإعداد أو أجزاء منه',
                              'Pick what to keep and what to bring back: everything or parts'))
    if pick == 0:
        return backup_dialog()
    if pick == 1:
        return restore_dialog()
    return False


_EN = {
    'الإعدادات': 'Settings', 'كل خيارات Dex Hub': "Every Dex Hub option",
    'المصادر والإضافات': 'Sources and add-ons', 'إضافات Stremio وNuvio وكتالوجاتها ومجلداتها':
        'Stremio and Nuvio add-ons, their catalogs and folders',
    'الرئيسية وشكلها': 'The Home', 'الصفوف وترتيبها وأشكالها وصفوف Dex Community':
        "Its rows, their order and shapes, Dex Community's rows",
    'المجموعات': 'Collections', 'المجموعات المستوردة وNuvio والمجموعة الأساسية':
        "Imported collections, Nuvio's and which one leads",
    'المفضلة': 'Favorites', 'مفضلتك المحلية': 'Your own favorites',
    'متابعة المشاهدة': 'Continue Watching', 'وين وصلت في كل فيلم وحلقة': 'Where you are in every film and episode',
    'الحسابات والمفاتيح': 'Accounts and keys', 'Trakt وSimkl وNuvio وPlex والسيرفرات ومفاتيح TMDb وغيرها':
        "Trakt, Simkl, Nuvio, Plex, the servers' sign-ins, TMDb and other keys",
}
