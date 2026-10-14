"""Phone file operations inside Kodi's writable application home."""
import os
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED

LIMIT = 256 * 1024 * 1024
ROOTS = {'files': 'dexhub-files', 'imports': 'dexhub-imports',
         'fonts': 'media/Fonts', 'backups': 'dexhub-json-restore-backups',
         'trash': 'dexhub-file-trash', 'addons': 'addons'}


def kodi_home():
    import xbmcvfs
    return Path(xbmcvfs.translatePath('special://home/'))


def resolve(home, root, relative='', write=False):
    if root not in ROOTS:
        raise ValueError('Unknown folder')
    if write and root == 'addons':
        raise ValueError('Install addon ZIPs with Kodi; installed addons are read-only here')
    value = str(relative)
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '\\' in value or '\x00' in value:
        raise ValueError('Invalid file path')
    base = Path(home) / ROOTS[root]
    target = base.joinpath(*path.parts)
    current = Path(home)
    for part in target.relative_to(current).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symbolic links are not supported')
    if not target.resolve().is_relative_to(Path(home).resolve()):
        raise ValueError('File is outside Kodi home')
    return target


def trash(home, path):
    destination = resolve(home, 'trash', uuid.uuid4().hex + '-' + path.name, write=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))


def tree_size(path):
    items = [path] if path.is_file() else path.rglob('*')
    size = count = 0
    for item in items:
        if item.is_symlink():
            raise ValueError('Folder contains a symbolic link')
        if item.is_file():
            count += 1
            size += item.stat().st_size
            if size > LIMIT or count > 10000:
                raise ValueError('Folder exceeds 256 MiB or 10000 files')
    return size


def operate(home, body):
    operation = body.get('op', 'list')
    root = body.get('root', 'files')
    relative = str(body.get('path') or '')
    path = resolve(home, root, relative, write=operation != 'list')
    if operation == 'list':
        if not path.exists() and not relative:
            return {'ok': True, 'entries': []}
        if not path.is_dir():
            raise ValueError('Choose a folder')
        entries = []
        for item in path.iterdir():
            if item.is_symlink():
                continue
            entries.append({'name': item.name, 'directory': item.is_dir(),
                            'size': item.stat().st_size if item.is_file() else 0})
            if len(entries) > 10000:
                raise ValueError('Too many entries in this folder')
        return {'ok': True, 'entries': sorted(entries, key=lambda x: (not x['directory'], x['name'].casefold()))}
    if not relative:
        raise ValueError('Choose a file or subfolder; roots cannot be changed')
    if operation == 'mkdir':
        path.mkdir(parents=True, exist_ok=False)
    elif operation == 'trash':
        if root == 'trash':
            raise ValueError('Move trash items to another folder to restore them')
        trash(home, path)
    elif operation in ('move', 'copy'):
        destination = resolve(home, body.get('target_root', root), body.get('target', ''), write=True)
        if not body.get('target') or destination == path or destination.is_relative_to(path):
            raise ValueError('Invalid destination')
        if destination.exists():
            raise ValueError('Destination already exists; rename it first')
        tree_size(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if operation == 'move':
            shutil.move(str(path), str(destination))
        elif path.is_dir():
            shutil.copytree(path, destination)
        else:
            shutil.copy2(path, destination)
    else:
        raise ValueError('Unknown file operation')
    return {'ok': True}


def upload(home, root, relative, source, replace=False):
    target = resolve(home, root, relative, write=True)
    if not relative or target.is_dir():
        raise ValueError('Choose a filename')
    if Path(source).stat().st_size > LIMIT:
        raise ValueError('File exceeds 256 MiB')
    if target.exists() and not replace:
        raise ValueError('File already exists; select Replace to keep the old file in trash')
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged = tempfile.mkstemp(prefix='.dex-upload-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output, open(source, 'rb') as incoming:
            shutil.copyfileobj(incoming, output, 64 * 1024)
        if target.exists():
            trash(home, target)
        os.replace(staged, target)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)
    return {'ok': True, 'filename': target.name}


def download(home, root, relative):
    path = resolve(home, root, relative)
    if path.is_file():
        tree_size(path)
        return path, path.name, False
    if not path.is_dir():
        raise ValueError('File not found')
    tree_size(path)
    descriptor, temporary = tempfile.mkstemp(prefix='dexhub-folder-', suffix='.zip')
    os.close(descriptor)
    try:
        with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
            for item in sorted(path.rglob('*')):
                if item.is_symlink():
                    raise ValueError('Folder contains a symbolic link')
                archive.write(item, Path(path.name) / item.relative_to(path))
        return Path(temporary), path.name + '.zip', True
    except Exception:
        os.unlink(temporary)
        raise
