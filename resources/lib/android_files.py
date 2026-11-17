# -*- coding: utf-8 -*-
"""All files access for the Dex Hub Android app (v5.10.538).

From Android 11 an app sees folders but not zips and other files without this
one permission, and only the system's own settings page can give it: Kodi's
file browser came up empty (the AM8, 6 Nov 2026). The app asks once.

Whether it is given shows by writing at the top of the shared storage, which
is refused without it.
"""
import os
import re

import xbmc
import xbmcgui
import xbmcvfs

PACKAGE = 'com.dexhub.tv'
STORAGE = '/storage/emulated/0'
INTENT = 'android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION'


def _t(ar, en):
    try:
        from .settings_cache import cached_addon
        arabic = (cached_addon().getSetting('ui_language') or '').strip().lower().startswith('ar')
    except Exception:
        arabic = False
    return ar if arabic else en


def package():
    match = re.search(r'/data/(?:user/\d+|data)/([^/]+)/', xbmcvfs.translatePath('special://xbmc/'))
    return match.group(1) if match else ''


def allowed():
    path = os.path.join(STORAGE, '.dexhub-files-check')
    try:
        with open(path, 'w') as handle:
            handle.write('1')
        os.remove(path)
        return True
    except OSError:
        return False


def _marker():
    from . import profile_scope
    return profile_scope.path('android_files_asked')


def ask():
    """The system's page for this app, once, when the access is missing."""
    if not xbmc.getCondVisibility('System.Platform.Android') or package() != PACKAGE or allowed():
        return False
    marker = _marker()
    if os.path.exists(marker):
        return False
    with open(marker, 'w') as handle:
        handle.write('1')
    yes = xbmcgui.Dialog().yesno(
        'Dex Hub',
        _t('عشان تشوف ملفات الجهاز (مثل zip)، Dex Hub يحتاج إذن الوصول لكل الملفات. تفتح صفحة الإذن الحين؟',
           'To see the files on this device (such as zips), Dex Hub needs All files access. Open that page now?'))
    if yes:
        xbmc.executebuiltin('StartAndroidActivity("",%s,"",package:%s)' % (INTENT, PACKAGE))
    return yes
