# -*- coding: utf-8 -*-
"""One-time repair of list settings saved as their shown label (v5.10.140).

Kodi keeps a ``labelenum`` with ``lvalues`` as the label shown in Kodi's own
language ("Merged", "كبير") unless the setting names its stored values with
``entries``. Dex Hub's code writes and compares the values ("merged",
"Large"), so those writes were refused and those reads fell back to the
default. 5.10.140 gives every such setting ``entries``; a label saved by an
older version is no longer a valid choice and Kodi would forget it, so this
turns each saved label back into its value once, before anything else in
the service writes a setting.
"""
import os
import re
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcvfs

_DONE = 'settings_labels_v1.done'
_PO = re.compile(r'msgctxt "#(\d+)"\s*\nmsgid "((?:[^"\\]|\\.)*)"\s*\nmsgstr "((?:[^"\\]|\\.)*)"')


def _labels(addon_path):
    """{string id: set of its texts in every language the add-on ships}."""
    out = {}
    folder = os.path.join(addon_path, 'resources', 'language')
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        try:
            with open(os.path.join(folder, name, 'strings.po'), encoding='utf-8') as handle:
                text = handle.read()
        except OSError:
            continue
        for match in _PO.finditer(text):
            texts = out.setdefault(int(match.group(1)), set())
            for value in (match.group(2), match.group(3)):
                value = value.replace('\\"', '"').strip()
                if value:
                    texts.add(value.lower())
    return out


def _choices(addon_path):
    """{setting id: [(entry, label string id)]} for labelenums with entries."""
    out = {}
    root = ET.parse(os.path.join(addon_path, 'resources', 'settings.xml')).getroot()
    for node in root.iter('setting'):
        if node.get('type') != 'labelenum' or not node.get('id'):
            continue
        entries = (node.get('entries') or '').split('|')
        lvalues = (node.get('lvalues') or '').split('|')
        if not node.get('entries') or len(entries) != len(lvalues):
            continue
        try:
            out[node.get('id')] = [(entry, int(label)) for entry, label in zip(entries, lvalues)]
        except ValueError:
            continue
    return out


def _saved(profile):
    """The user's saved values, read from the file Kodi keeps them in."""
    try:
        root = ET.parse(os.path.join(profile, 'settings.xml')).getroot()
    except Exception:
        return {}
    return dict((node.get('id'), (node.text or '').strip()) for node in root.iter('setting') if node.get('id'))


def run():
    try:
        addon = xbmcaddon.Addon()
        profile = xbmcvfs.translatePath(addon.getAddonInfo('profile'))
        marker = os.path.join(profile, _DONE)
        if os.path.exists(marker):
            return 0
        path = xbmcvfs.translatePath(addon.getAddonInfo('path'))
        choices = _choices(path)
        saved = _saved(profile)
        texts = _labels(path) if any(saved.get(k) for k in choices) else {}
        fixed = []
        for key, options in choices.items():
            value = saved.get(key) or ''
            if not value or value in [entry for entry, _ in options]:
                continue
            wanted = value.lower()
            for entry, label in options:
                if wanted == entry.lower() or wanted in texts.get(label, ()):
                    fixed.append((key, entry))
                    break
        for key, entry in fixed:
            addon.setSetting(key, entry)
        try:
            os.makedirs(profile, exist_ok=True)
            with open(marker, 'w') as handle:
                handle.write('1')
        except OSError:
            pass
        if fixed:
            xbmc.log('[DexHub] settings saved as labels restored: %s' % ', '.join(k for k, _ in fixed), xbmc.LOGINFO)
        return len(fixed)
    except Exception as exc:
        xbmc.log('[DexHub] settings label repair skipped: %s' % exc, xbmc.LOGWARNING)
        return 0
