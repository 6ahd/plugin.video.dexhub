"""Apply staged full backups on standard Kodi before Dex Hub opens its stores.

tvOS retains its existing startup compatibility module (NSUserDefaults/VFS).
Standard Kodi gets the same staged restore and a recoverable pre-restore copy.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid


def apply(home, roots):
    from .full_backup import ROOTS, CORE_SETTINGS, FORMAT
    home = Path(home)
    pending = home / 'dexhub-restore-pending'
    if not pending.is_dir():
        return False
    manifest = json.loads((pending / 'manifest.json').read_text())
    if manifest.get('format') != FORMAT or manifest.get('version') != 1:
        raise ValueError('Invalid staged backup')
    names = manifest.get('roots') or []
    if set(names) - set(ROOTS) or set(names) - set(roots):
        raise ValueError('A required backup add-on is not installed')
    # Recheck integrity before changing live directories.
    for name, info in (manifest.get('files') or {}).items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('Invalid staged path')
        source = pending / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError('Missing staged file')
        payload = source.read_bytes()
        if len(payload) != info['size'] or hashlib.sha256(payload).hexdigest() != info['sha256']:
            raise ValueError('Staged backup checksum mismatch')
    rollback = home / 'dexhub-json-restore-backups' / ('before-full-%d-%s' % (time.time(), uuid.uuid4().hex[:8]))
    rollback.mkdir(parents=True)
    applied = []
    try:
        for identifier in names:
            destination = Path(roots[identifier])
            source = pending / 'profiles' / identifier
            if not source.is_dir() or destination.is_symlink():
                raise ValueError('Invalid backup profile directory')
            previous = rollback / identifier
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                os.replace(destination, previous)
            applied.append((destination, previous, source))
            os.replace(source, destination)
        # Plain Kodi stores settings.xml on disk. The snapshot may have come
        # from tvOS, whose XML exists only in VFS, so materialize saved values.
        import xml.etree.ElementTree as ET
        for identifier, values in (manifest.get('settings') or {}).items():
            if identifier not in names or identifier.startswith('skin.'):
                continue
            filename = Path(roots[identifier]) / 'settings.xml'
            try:
                tree = ET.parse(filename)
                root = tree.getroot()
            except (OSError, ET.ParseError):
                root = ET.Element('settings', version='2')
                tree = ET.ElementTree(root)
            nodes = {node.get('id'): node for node in root.iter('setting')}
            for sid, value in values.items():
                node = nodes.get(sid)
                if node is None:
                    node = ET.SubElement(root, 'setting', id=sid)
                node.attrib.pop('value', None)
                node.text = str(value)
            tree.write(filename, encoding='utf-8', xml_declaration=True)
        fonts = pending / 'fonts'
        if fonts.is_dir():
            target = home / 'media' / 'Fonts'
            target.mkdir(parents=True, exist_ok=True)
            for source in fonts.iterdir():
                # Preserve existing files under their names; imported fonts get
                # unique names instead of overwriting a user's installed font.
                shutil.copy2(source, target / ('Restored-' + uuid.uuid4().hex + source.suffix))
        core = {k: v for k, v in (manifest.get('core') or {}).items() if k in CORE_SETTINGS}
        (home / 'dexhub-restored-core.json').write_text(json.dumps(core))
    except Exception:
        for destination, previous, source in reversed(applied):
            if destination.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, source)
            if previous.exists():
                os.replace(previous, destination)
        raise
    shutil.rmtree(pending)
    return True


def startup():
    import xbmc
    if xbmc.getCondVisibility('System.Platform.TVOS'):
        return False
    from .full_backup import context, apply_core_restore
    home, roots, _settings, _core = context()
    pending = home / 'dexhub-restore-pending' / 'manifest.json'
    saved = json.loads(pending.read_text()).get('settings', {}) if pending.is_file() else {}
    result = apply(home, roots)
    if result:
        import xbmcaddon
        for identifier, values in saved.items():
            if identifier.startswith('skin.'):
                continue
            for sid, value in values.items():
                xbmcaddon.Addon(identifier).setSetting(sid, str(value))
    apply_core_restore()
    return result
