# -*- coding: utf-8 -*-
"""v5.10.378: Dex Hub's saved settings that this Kodi cannot read.

Kodi keeps an add-on's values in addon_data/<id>/settings.xml with a version
(<settings version="N">). A newer Kodi writes a higher one (Kodi's source has
5) and an older one refuses the file: "unable to read setting values from
version 4 (current version: 2)" (CoreELEC 22.0 beta1, a CoreELEC log). Kodi
then runs the add-on on its defaults and does not save over the file, so every
change seems lost. The file comes from a newer Kodi, a restore, or a backup
from another device.

The values themselves are plain <setting id="...">value</setting> lines in
every version. So when the file's version is above 2 and Kodi shows none of
its values, at the very start of the service (before any code sets a value)
the file is written as version 2, the old one kept as settings.xml.v<N>.bak;
Kodi reads it again while it has not loaded it (a new handle shows the
values). If it still does not, they are set through Kodi (setSetting).
Lab (Kodi 20): while the file is refused Kodi saves nothing, and a value set
in that time (Dex Hub's language detection) was what got lost before.
Restores write version 2 (normalize_file).
"""
import os
import re
import shutil

import xbmc
import xbmcvfs

SAFE = 2                      # a version every Kodi from 18 on reads
SAMPLE = 40                   # values compared with what Kodi shows
_HEAD = re.compile(r'(<settings\b[^>]*\bversion=")(\d+)(")')


def _path(addon_id):
    return xbmcvfs.translatePath('special://profile/addon_data/%s/settings.xml' % addon_id)


def file_version(text):
    found = _HEAD.search(text or '')
    return int(found.group(2)) if found else 0


def lower(text):
    """The same file with version 2 (unchanged when it is 2 or lower)."""
    found = _HEAD.search(text or '')
    if not found or int(found.group(2)) <= SAFE:
        return text
    return text[:found.start(2)] + str(SAFE) + text[found.end(2):]


def _values(text):
    out = {}
    # (a value left at its default is written as <setting id="x" default="true" />)
    for sid, value in re.findall(r'<setting\b[^>]*\bid="([^"]+)"[^>]*>([^<]*)</setting>', text or ''):
        if value.strip():
            out[sid] = value
    return out


def unreadable(addon, text):
    """True when Kodi shows none of the file's values (it refused the file)."""
    values = list(_values(text).items())[:SAMPLE]
    if len(values) < 3:
        return False
    same = 0
    for sid, value in values:
        try:
            shown = addon.getSetting(sid)
        except Exception:
            continue
        if shown == value.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"'):
            same += 1
    return same == 0


def _plain(value):
    return (value.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
            .replace('&apos;', "'").replace('&amp;', '&'))


def check(addon_id, addon=None, ask=True):
    """At the service's very start: when Kodi refused the file, its values are
    set again through Kodi (which then saves them in a version it reads), the
    file kept beside it. No restart needed. True when values were put back."""
    path = _path(addon_id)
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            text = handle.read()
    except Exception:
        return False
    version = file_version(text)
    if version <= SAFE:
        return False
    if addon is None:
        import xbmcaddon
        addon = xbmcaddon.Addon(addon_id)
    if not unreadable(addon, text):
        return False
    backup = '%s.v%d.bak' % (path, version)
    try:
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        tmp = path + '.dexhub.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            handle.write(lower(text))
        os.replace(tmp, path)
    except Exception as exc:
        xbmc.log('[DexHub] settings file version %d could not be changed: %s' % (version, exc), xbmc.LOGWARNING)
    # Kodi reads an add-on's file again while it has not loaded it: a new
    # handle shows the values once the version is one it reads
    import xbmcaddon
    fresh = xbmcaddon.Addon(addon_id)
    put = 0
    how = 'read again by Kodi'
    if unreadable(fresh, text):
        how = 'put back through Kodi'
        for sid, value in _values(text).items():
            try:
                fresh.setSetting(sid, _plain(value))
                put += 1
            except Exception:
                continue
    else:
        put = len(_values(text))
    xbmc.log('[DexHub] settings saved by a newer Kodi (version %d) were unreadable here: written as version %d, '
             '%d value(s) %s (old file: %s)' % (version, SAFE, put, how, os.path.basename(backup)), xbmc.LOGWARNING)
    if ask and put:
        try:
            import xbmcgui
            from .i18n import tr
            xbmcgui.Dialog().notification('Dex Hub', tr('إعداداتك رجعت (كانت محفوظة بنسخة كودي أحدث)'), time=6000)
        except Exception:
            pass
    return put > 0


def kodi_reads(addon_id):
    """False when the add-on's saved file has values and Kodi shows none of
    them (a file Kodi refused): what this TV would send then is defaults.
    DexCloud sends nothing in that case (v5.10.378)."""
    try:
        with open(_path(addon_id), 'r', encoding='utf-8') as handle:
            text = handle.read()
    except Exception:
        return True
    try:
        import xbmcaddon
        return not unreadable(xbmcaddon.Addon(addon_id), text)
    except Exception:
        return True


def normalize_file(path):
    """A settings file written by a restore: version 2 when it says more."""
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            text = handle.read()
        fixed = lower(text)
        if fixed != text:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(fixed)
            return True
    except Exception:
        pass
    return False
