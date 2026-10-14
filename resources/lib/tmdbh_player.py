# -*- coding: utf-8 -*-
"""Register Dex Hub as a TMDb Helper player.

This module copies the bundled player JSON (resources/players/dexhub.json)
into the TMDb Helper addon's players folder so it shows up in the
"Play with..." dialog and the autoplay settings.

TMDb Helper looks for player files in:
    special://profile/addon_data/plugin.video.themoviedb.helper/players/

We never touch TMDb Helper's own files — we only drop our JSON alongside.
"""
import os
import shutil
from functools import lru_cache

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .i18n import tr

TMDBH_ID = 'plugin.video.themoviedb.helper'
ADDON_ID = 'plugin.video.dexhub'
PLAYER_FILENAME = 'dexhub.json'
_STALE_PLAYER_PREFIXES = ('dexhub', 'dex_hub', 'plugin.video.dexhub')


def _addon():
    return xbmcaddon.Addon()


def _addon_path():
    return _addon().getAddonInfo('path')


def _bundled_player_path():
    return os.path.join(_addon_path(), 'resources', 'players', PLAYER_FILENAME)


def _tmdbh_players_dir():
    return xbmcvfs.translatePath(
        'special://profile/addon_data/%s/players/' % TMDBH_ID
    )


def _read_player_text(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return fh.read()
    except Exception:
        return ''


def _is_dexhub_player_file(folder, filename):
    """Identify only Dex Hub's current/duplicated TMDb Helper players."""
    lower = str(filename or '').strip().lower()
    if not lower.endswith('.json'):
        return False
    if lower == PLAYER_FILENAME or any(lower.startswith(prefix) for prefix in _STALE_PLAYER_PREFIXES):
        return True
    raw = _read_player_text(os.path.join(folder, filename))
    if not raw:
        return False
    try:
        import json
        data = json.loads(raw)
        return (str(data.get('plugin') or '').strip() == ADDON_ID or
                str(data.get('name') or '').strip().lower() == 'dex hub')
    except Exception:
        return ADDON_ID in raw


def _stale_player_files():
    """Return duplicate/legacy Dex Hub player JSON files, not other players."""
    folder = _tmdbh_players_dir()
    try:
        names = os.listdir(folder)
    except Exception:
        return []
    return [name for name in names if _is_dexhub_player_file(folder, name)]


def _clean_stale_players():
    """Remove stale Dex Hub player JSON copies before writing the current one.

    TMDb Helper scans every JSON in its player folder.  A file such as
    ``dexhub (1).json`` can otherwise remain selectable and keep launching an
    old route after Dex Hub has been updated.
    """
    folder = _tmdbh_players_dir()
    removed = []
    for name in _stale_player_files():
        try:
            os.remove(os.path.join(folder, name))
            removed.append(name)
        except FileNotFoundError:
            continue
        except Exception as exc:
            xbmc.log('[DexHub] stale TMDbH player cleanup failed for %s: %s' % (name, exc), xbmc.LOGWARNING)
    if removed:
        xbmc.log('[DexHub] removed stale TMDb Helper player file(s): %s' % ', '.join(removed), xbmc.LOGINFO)
    return removed


@lru_cache(maxsize=1)
def has_tmdbhelper():
    """Check if TMDb Helper is installed. Result cached for the lifetime of
    this Python invocation — install/uninstall is rare and never happens
    inside a single plugin call, so the per-item lookup overhead is wasted."""
    try:
        return bool(xbmc.getCondVisibility('System.HasAddon(%s)' % TMDBH_ID))
    except Exception:
        return False


def player_installed():
    """True if our player JSON is already present in TMDb Helper's folder."""
    target = os.path.join(_tmdbh_players_dir(), PLAYER_FILENAME)
    try:
        return os.path.exists(target)
    except Exception:
        return False


def install(silent=False):
    """Copy resources/players/dexhub.json into TMDb Helper's players folder.

    Returns True if the file is now in place, False otherwise.
    When silent=False, shows user-facing notifications on failure.
    """
    if not has_tmdbhelper():
        if not silent:
            xbmcgui.Dialog().notification(
                'Dex Hub',
                tr('TMDb Helper غير مثبّت'),
                xbmcgui.NOTIFICATION_WARNING,
                3500,
            )
        return False

    src = _bundled_player_path()
    if not os.path.exists(src):
        if not silent:
            xbmcgui.Dialog().notification(
                'Dex Hub',
                tr('ملف المشغّل غير موجود داخل الإضافة'),
                xbmcgui.NOTIFICATION_ERROR,
                3500,
            )
        xbmc.log('[DexHub] tmdbh player source missing: %s' % src, xbmc.LOGERROR)
        return False

    dst_dir = _tmdbh_players_dir()
    try:
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir, exist_ok=True)
    except Exception as exc:
        xbmc.log('[DexHub] tmdbh players dir create failed: %s' % exc, xbmc.LOGERROR)
        if not silent:
            xbmcgui.Dialog().notification(
                'Dex Hub',
                tr('تعذّر إنشاء مجلد مشغّلات TMDb Helper'),
                xbmcgui.NOTIFICATION_ERROR,
                3500,
            )
        return False

    dst = os.path.join(dst_dir, PLAYER_FILENAME)
    # Mirror the robust Dplex installation pattern: make the target folder
    # contain exactly one Dex Hub player definition.  Do not touch JSON files
    # belonging to any other installed player.
    _clean_stale_players()
    try:
        shutil.copyfile(src, dst)
    except Exception as exc:
        xbmc.log('[DexHub] tmdbh player copy failed: %s' % exc, xbmc.LOGERROR)
        if not silent:
            xbmcgui.Dialog().notification(
                'Dex Hub',
                tr('تعذّر نسخ ملف المشغّل'),
                xbmcgui.NOTIFICATION_ERROR,
                3500,
            )
        return False

    xbmc.log('[DexHub] registered as TMDb Helper player at %s' % dst, xbmc.LOGINFO)
    if not silent:
        xbmcgui.Dialog().notification(
            'Dex Hub',
            tr('تم تسجيل Dex Hub كمشغّل TMDb Helper'),
            xbmcgui.NOTIFICATION_INFO,
            3000,
        )
    return True


def uninstall(silent=False):
    """Remove the Dex Hub player JSON from TMDb Helper's folder."""
    dst = os.path.join(_tmdbh_players_dir(), PLAYER_FILENAME)
    try:
        if os.path.exists(dst):
            os.remove(dst)
            if not silent:
                xbmcgui.Dialog().notification(
                    'Dex Hub',
                    tr('تم إزالة Dex Hub من TMDb Helper'),
                    xbmcgui.NOTIFICATION_INFO,
                    2500,
                )
            return True
    except Exception as exc:
        xbmc.log('[DexHub] tmdbh player uninstall failed: %s' % exc, xbmc.LOGWARNING)
    return False


def ensure_installed_once():
    """Ensure the Dex Hub TMDb Helper player exists and is up to date."""
    if not has_tmdbhelper():
        return False
    src = _bundled_player_path()
    dst = os.path.join(_tmdbh_players_dir(), PLAYER_FILENAME)
    try:
        if os.path.exists(src) and os.path.exists(dst):
            try:
                with open(src, 'rb') as fh:
                    src_bytes = fh.read()
                with open(dst, 'rb') as fh:
                    dst_bytes = fh.read()
                # A byte-identical primary file is not enough: TMDb Helper
                # also loads duplicated legacy JSON files in the same folder.
                if src_bytes == dst_bytes and _stale_player_files() == [PLAYER_FILENAME]:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return install(silent=True)
