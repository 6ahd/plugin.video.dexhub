# -*- coding: utf-8 -*-
"""Live settings reads — one implementation for the whole addon.

v5.4.4: a setting can be read from three places, and they disagree at the
worst possible moment:

  1. the profile's settings.xml — the file Kodi rewrites the instant the
     settings dialog is confirmed, and the only source that cannot lag;
  2. a window property the service republishes on every settings change,
     which crosses process boundaries instantly;
  3. this interpreter's own copy, which under reuselanguageinvoker can be
     hours old.

Reading those in the wrong order, or giving one of them a different
fallback from the others, is what made the quality badges stay invisible
after the user switched them on: the service published "off" for a key
nobody had written, and the published value outranked the setting. That
logic lived inside source_browser.py, so nothing else could reuse it and
every new live setting reinvented it. It lives here now.
"""
import os

import xbmcaddon
import xbmcgui
import xbmcvfs

from .log import log

ADDON = xbmcaddon.Addon()

_SETTINGS_FILE_MEMO = {'sig': None, 'values': {}}
def _settings_file_value(key):
    """Read a setting straight out of the profile's settings.xml.

    v4.7.7: this is the file Kodi rewrites the moment the settings dialog
    is confirmed, so it is the one place that cannot be stale. Every
    in-memory route can be: the addon runs in an interpreter Kodi reuses
    for hours, our own proxy caches reads, and the service publishes from
    yet another copy. Chasing those layers one at a time is what kept the
    badges URL on its old value; the file settles it.

    Memoized on (mtime, size), so a list build re-reading it per row costs
    a stat() call.
    """
    try:
        profile = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
        path = os.path.join(profile, 'settings.xml')
        stat = os.stat(path)
        sig = (stat.st_mtime, stat.st_size)
        if _SETTINGS_FILE_MEMO.get('sig') != sig:
            import xml.etree.ElementTree as _ET
            values = {}
            for node in _ET.parse(path).getroot().iter('setting'):
                sid = node.get('id')
                if not sid:
                    continue
                # Kodi 19+ stores the value as text; older builds used an
                # attribute. Accept both so upgraded profiles keep working.
                raw = node.text if node.text is not None else node.get('value')
                values[sid] = (raw or '').strip()
            _SETTINGS_FILE_MEMO.update({'sig': sig, 'values': values})
        return _SETTINGS_FILE_MEMO['values'].get(key)
    except Exception:
        return None


def live_setting(key, window_property='', default=''):
    """Read a setting from the freshest source available.

    Order: profile settings.xml, then the published window property, then
    this interpreter's copy. `default` is used by ALL of them — passing one
    value here is what keeps the three layers from contradicting each other.
    """
    disk = _settings_file_value(key)
    # v5.4.38: the profile file is authoritative even when the value is
    # deliberately empty. Falling through on an empty stored value can revive
    # a stale in-memory key after the user deletes/changes it in settings.
    if disk is not None:
        return disk.strip()
    if window_property:
        try:
            live = (xbmcgui.Window(10000).getProperty(window_property) or '').strip()
        except Exception:
            live = ''
        if live:
            return '' if live == '-' else live
    try:
        return (ADDON.getSetting(key) or default).strip()
    except Exception:
        return default


def live_bool(key, window_property='', default=True):
    raw = live_setting(key, window_property,
                       'true' if default else 'false').strip().lower()
    return raw in ('true', '1', 'yes', 'on')
