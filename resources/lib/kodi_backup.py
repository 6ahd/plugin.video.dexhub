# -*- coding: utf-8 -*-
"""Whole-Kodi backups and their restore (v5.10.569).

Two kinds of ZIP, next to Dex Hub's own part-by-part backup (backup.py):

  full      everything in Kodi's home folder: the add-ons (with their
            repositories), userdata (Kodi's settings, every add-on's
            settings and data, sources, favourites, keymaps, the library
            databases, profiles). A new device restored from it is the old one.
  settings  userdata only: Kodi's own settings and every add-on's settings
            and data, the library and favourites, without the add-ons
            themselves (the ZIP lists them; the restore installs the missing
            ones from their repositories when it can).

Left out of both: the picture cache (Thumbnails, Textures*.db, which Kodi
rebuilds), downloaded add-on ZIPs (addons/packages), temp folders, logs.
Each ZIP holds dexhub_kodi_backup.json (kind, date, device, Kodi version,
the installed add-ons).

A restore writes the files back into Kodi's home folder, then closes Kodi at
once (without letting it save its settings over the restored ones): the next
start is the restored Kodi.
"""
import json
import os
import shutil
import time
import zipfile

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

MANIFEST = 'dexhub_kodi_backup.json'
FORMAT = 'dexhub_kodi_backup'
SKIP_DIRS = {'thumbnails', 'packages', 'temp', 'cache', '.cache', '__pycache__', 'archive_cache', 'http_cache',
             'artcache', 'thumbs', 'lost+found', 'dexhub_backups'}
SKIP_FILES = ('kodi.log', 'kodi.old.log', 'xbmc.log', 'xbmc.old.log')


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] Kodi backup: %s' % msg, level)


def home():
    return xbmcvfs.translatePath('special://home/')


def _skip_file(rel, name):
    low = name.lower()
    if low in SKIP_FILES or low.endswith(('.tmp', '.log', '.dexhub_new')):
        return True
    if low.startswith(('kodi_full_', 'kodi_settings_')) and low.endswith('.zip'):
        return True                     # an earlier backup kept inside Kodi's folder
    if rel.replace('\\', '/').lower().startswith('userdata/database/') and low.startswith('textures'):
        return True
    return False


def plan(kind):
    """[(absolute path, path in the ZIP)] and the total size."""
    root = home()
    tops = ['userdata'] if kind == 'settings' else ['addons', 'userdata', 'media', 'system']
    out, total = [], 0
    for top in tops:
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        for folder, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            for name in names:
                path = os.path.join(folder, name)
                rel = os.path.relpath(path, root).replace('\\', '/')
                if _skip_file(rel, name) or os.path.islink(path):
                    continue
                try:
                    total += os.path.getsize(path)
                except OSError:
                    continue
                out.append((path, rel))
    return out, total


def installed_addons():
    try:
        answer = json.loads(xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.GetAddons',
            'params': {'properties': ['name', 'version', 'enabled']}})))
        return [{'id': a.get('addonid'), 'name': a.get('name'), 'version': a.get('version'), 'enabled': a.get('enabled')}
                for a in (answer.get('result') or {}).get('addons') or []]
    except Exception:
        return []


def _manifest(kind, files_count, size):
    return {'format': FORMAT, 'version': 1, 'kind': kind, 'created': int(time.time()),
            'device': xbmc.getInfoLabel('System.FriendlyName') or 'Kodi',
            'platform': next((p for p in ('Android', 'Windows', 'OSX', 'IOS', 'TVOS', 'Linux')
                              if xbmc.getCondVisibility('System.Platform.%s' % p)), ''),
            'kodi': xbmc.getInfoLabel('System.BuildVersion'), 'files': files_count, 'size': size,
            'addons': installed_addons()}


def _gb(size):
    return '%.2f GB' % (size / 1073741824.0) if size >= 1073741824 else '%d MB' % max(1, size // 1048576)


# ================================================================ making one
def create(kind, destination_folder=''):
    """Write the ZIP; its path, or '' when cancelled or failed."""
    dialog = xbmcgui.Dialog()
    label = _t('نسخة كاملة لكودي', 'Full Kodi backup') if kind == 'full' else _t('نسخة إعدادات كودي', 'Kodi settings backup')
    progress = xbmcgui.DialogProgress()
    progress.create(label, _t('نحسب الملفات…', 'Counting the files…'))
    entries, total = plan(kind)
    progress.close()
    if not entries:
        dialog.ok(label, _t('ما لقينا ملفات نحفظها.', 'Nothing was found to back up.'))
        return ''
    if not destination_folder:
        destination_folder = dialog.browse(3, _t('وين نحفظ النسخة؟ (%s)', 'Where should the backup go? (%s)') % _gb(total),
                                           'files', '', False, False, 'special://home/')
    if not destination_folder:
        return ''
    stamp = time.strftime('%Y-%m-%d_%H%M')
    name = 'kodi_%s_%s.zip' % ('full' if kind == 'full' else 'settings', stamp)
    local_target = xbmcvfs.translatePath(destination_folder)
    direct = os.path.isdir(local_target) and os.access(local_target, os.W_OK)
    work = os.path.join(local_target, name) if direct else os.path.join(xbmcvfs.translatePath('special://temp/'), name)
    progress.create(label, _t('نحفظ…', 'Saving…'))
    done_bytes, failed = 0, 0
    try:
        with zipfile.ZipFile(work, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr(MANIFEST, json.dumps(_manifest(kind, len(entries), total), ensure_ascii=False, indent=1))
            for index, (path, rel) in enumerate(entries):
                if progress.iscanceled():
                    raise KeyboardInterrupt
                if index % 25 == 0:
                    progress.update(int(done_bytes * 100 / max(1, total)),
                                    '%s\n%s / %s' % (rel[-70:], _gb(done_bytes), _gb(total)))
                try:
                    compress = zipfile.ZIP_STORED if rel.lower().endswith(('.zip', '.jpg', '.png', '.mp4', '.mkv', '.ttf')) \
                        else zipfile.ZIP_DEFLATED
                    archive.write(path, rel, compress_type=compress)
                    done_bytes += os.path.getsize(path)
                except (OSError, ValueError) as exc:
                    failed += 1
                    _log('left out %s: %s' % (rel, exc), xbmc.LOGWARNING)
        if not direct:
            progress.update(99, _t('ننقل الملف للمكان اللي اخترته…', 'Copying it to the place you chose…'))
            target = destination_folder.rstrip('/\\') + '/' + name
            if not xbmcvfs.copy(work, target):
                raise IOError('copy to %s failed' % destination_folder)
            os.remove(work)
            work = target
    except KeyboardInterrupt:
        progress.close()
        _remove(work)
        return ''
    except Exception as exc:
        progress.close()
        _remove(work)
        _log('backup failed: %s' % exc, xbmc.LOGERROR)
        dialog.ok(label, _t('ما كملت النسخة: %s', 'The backup did not finish: %s') % exc)
        return ''
    progress.close()
    note = _t('\n%d ملف ما انحفظ (مفتوح أو ممنوع).', '\n%d files could not be read (open or locked).') % failed if failed else ''
    dialog.ok(label, _t('تم ✓\n%s\n%d ملف، %s', 'Done ✓\n%s\n%d files, %s') % (work, len(entries) - failed, _gb(done_bytes)) + note)
    return work


def _remove(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


# ================================================================ restoring one
def read_manifest(path):
    with zipfile.ZipFile(path) as archive:
        try:
            data = json.loads(archive.read(MANIFEST).decode('utf-8'))
        except KeyError:
            return None
    return data if data.get('format') == FORMAT else None


def _safe(rel):
    rel = rel.replace('\\', '/')
    parts = [p for p in rel.split('/') if p not in ('', '.')]
    if not parts or '..' in parts or rel.startswith('/') or ':' in parts[0]:
        return ''
    return '/'.join(parts)


def extract(path, root=None, progress=None, keep=None):
    """Write the ZIP's files under Kodi's home; (written, failed)."""
    root = root or home()
    written, failed = 0, 0
    with zipfile.ZipFile(path) as archive:
        members = [m for m in archive.infolist() if m.filename != MANIFEST and not m.is_dir()]
        for index, member in enumerate(members):
            rel = _safe(member.filename)
            if not rel or (keep and not keep(rel)):
                continue
            if progress is not None and index % 25 == 0:
                if progress.iscanceled():
                    break
                progress.update(int(index * 100 / max(1, len(members))), rel[-70:])
            target = os.path.join(root, *rel.split('/'))
            try:
                folder = os.path.dirname(target)
                if not os.path.isdir(folder):
                    os.makedirs(folder)
                with archive.open(member) as source, open(target + '.dexhub_new', 'wb') as out:
                    shutil.copyfileobj(source, out, 1048576)
                os.replace(target + '.dexhub_new', target)
                written += 1
            except Exception as exc:
                failed += 1
                _remove(target + '.dexhub_new')
                _log('not restored %s: %s' % (rel, exc), xbmc.LOGWARNING)
    return written, failed


def restore(path=''):
    dialog = xbmcgui.Dialog()
    if not path:
        path = dialog.browse(1, _t('اختر ملف النسخة', 'Pick the backup file'), 'files', '.zip|.json')
    if not path:
        return False
    if path.lower().endswith('.json'):
        from . import backup
        return backup.restore_dialog(path)
    local = xbmcvfs.translatePath(path)
    copied = ''
    if not os.path.isfile(local):
        copied = os.path.join(xbmcvfs.translatePath('special://temp/'), 'dexhub_restore.zip')
        if not xbmcvfs.copy(path, copied):
            dialog.ok('Dex Hub', _t('ما قدرنا نقرأ الملف.', 'The file could not be read.'))
            return False
        local = copied
    try:
        manifest = read_manifest(local)
    except zipfile.BadZipFile:
        manifest = None
    if not manifest:
        dialog.ok('Dex Hub', _t('هذا الملف مو نسخة كودي من Dex Hub.', 'This file is not a Kodi backup made by Dex Hub.'))
        _remove(copied)
        return False
    kind = manifest.get('kind')
    when = time.strftime('%Y-%m-%d %H:%M', time.localtime(manifest.get('created') or 0))
    what = _t('نسخة كاملة', 'A full backup') if kind == 'full' else _t('نسخة الإعدادات', 'A settings backup')
    here = next((p for p in ('Android', 'Windows', 'OSX', 'IOS', 'TVOS', 'Linux')
                 if xbmc.getCondVisibility('System.Platform.%s' % p)), '')
    warn = ''
    if kind == 'full' and manifest.get('platform') and here and manifest['platform'] != here:
        warn = _t('\n\nتنبيه: النسخة من %s وهذا الجهاز %s؛ بعض الإضافات المبنية للجهاز (مثل inputstream) ما بتشتغل.',
                  '\n\nNote: the backup is from %s and this device is %s; add-ons built for a device (inputstream…) will not work.') % (
            manifest['platform'], here)
    if not dialog.yesno(_t('استرجاع', 'Restore'), _t(
            '%s من %s (%s، كودي %s).\n\nبترجع ملفاته مكان الحالية، وبعدها يتسكّر كودي عشان يفتح بالنسخة.',
            '%s from %s (%s, Kodi %s).\n\nIts files replace the current ones, then Kodi closes so it opens as the backup.') % (
            what, manifest.get('device') or '?', when, manifest.get('kodi') or '?') + warn):
        _remove(copied)
        return False
    progress = xbmcgui.DialogProgress()
    progress.create(_t('استرجاع', 'Restore'), '')
    written, failed = extract(local, progress=progress)
    progress.close()
    _remove(copied)
    missing = []
    if kind == 'settings':
        have = {a['id'] for a in installed_addons()}
        missing = [a['id'] for a in manifest.get('addons') or [] if a.get('id') and a['id'] not in have]
        _save_missing(missing)
    note = _t('\n%d ملف ما ترجّع.', '\n%d files were not restored.') % failed if failed else ''
    if missing:
        note += _t('\n%d إضافة ناقصة، بنثبّتها من مستودعاتها لما يفتح كودي.',
                   '\n%d add-ons are missing; they are installed from their repositories when Kodi opens.') % len(missing)
    dialog.ok(_t('استرجاع', 'Restore'), _t('ترجّع %d ملف ✓', '%d files restored ✓') % written + note +
              _t('\n\nكودي بيتسكّر الحين، افتحه من جديد.', '\n\nKodi closes now; open it again.'))
    _log('restored %d files (%d failed) from a %s backup; closing Kodi' % (written, failed, kind))
    # closed at once: a normal exit would write the current settings over the restored guisettings.xml
    os._exit(0)


# ================================================================ add-ons missing after a settings restore
def _missing_file():
    return xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/restore_missing_addons.json')


def _save_missing(ids):
    try:
        if ids:
            with open(_missing_file(), 'w', encoding='utf-8') as handle:
                json.dump(ids, handle)
    except Exception as exc:
        _log('missing add-ons not noted: %s' % exc, xbmc.LOGWARNING)


def install_missing():
    """Called by the service at start: InstallAddon for each add-on the restored settings belong to."""
    path = _missing_file()
    if not os.path.isfile(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            ids = json.load(handle) or []
        os.remove(path)
    except Exception:
        return
    monitor = xbmc.Monitor()
    for addon_id in ids:
        if monitor.abortRequested():
            break
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % addon_id):
            xbmc.executebuiltin('InstallAddon(%s)' % addon_id, True)
            monitor.waitForAbort(2)


# ================================================================ the menu
def menu():
    from .homeui.options import choose
    options = [
        (_t('نسخة كاملة لكودي', 'Full Kodi backup'),
         _t('كل شي: الإضافات والإعدادات والمكتبة. جهاز جديد يصير نفس القديم', 'Everything: add-ons, settings, the library. A new device becomes the old one')),
        (_t('نسخة إعدادات كودي كلها', 'All of Kodi\'s settings'),
         _t('إعدادات كودي وكل الإضافات وبياناتها، بدون الإضافات نفسها', 'Kodi\'s settings and every add-on\'s settings and data, without the add-ons')),
        (_t('نسخة Dex Hub فقط', 'Dex Hub only'),
         _t('إعدادات Dex Hub ومصادره وحساباته، جزء جزء', 'Dex Hub\'s settings, sources and accounts, part by part')),
        (_t('استرجاع من نسخة', 'Restore from a backup'),
         _t('أي نسخة من الثلاث (ZIP أو JSON)', 'Any of the three (ZIP or JSON)')),
    ]
    pick = choose(_t('النسخ الاحتياطي', 'Backup'), options,
                  subtitle=_t('اختر وش تحفظ', 'Pick what to keep'))
    if pick == 0:
        return bool(create('full'))
    if pick == 1:
        return bool(create('settings'))
    if pick == 2:
        from . import backup
        return backup.backup_dialog()
    if pick == 3:
        return restore()
    return False
