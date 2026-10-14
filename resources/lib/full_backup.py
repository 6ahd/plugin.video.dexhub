"""Full addon-data snapshots and staged, startup-only restore for tvOS."""
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZIP_DEFLATED

ROOTS = ('plugin.video.dexhub', 'skin.dexhub', 'script.dexhub.tvos.players',
         'plugin.video.themoviedb.helper', 'script.skinvariables')
FORMAT = 'dexhub_full_backup'
LIMIT = 256 * 1024 * 1024
SKIP_DIRS = {'cache', '.cache', 'thumbs', 'thumbnails', 'artcache', 'http_cache', '__pycache__'}
CORE_SETTINGS = ('videoplayer.dexhubengine', 'videoplayer.dexhubexternal', 'subtitles.font',
                 'subtitles.height', 'subtitles.style', 'subtitles.color', 'subtitles.charset')

def context():
    import xbmcaddon
    import xbmcvfs
    home = Path(xbmcvfs.translatePath('special://home/'))
    roots, settings = {}, {}
    for identifier in ROOTS:
        try:
            addon = xbmcaddon.Addon(identifier)
        except RuntimeError:
            continue
        roots[identifier] = Path(xbmcvfs.translatePath(addon.getAddonInfo('profile')))
        values = {}
        descriptor = Path(addon.getAddonInfo('path')) / 'resources/settings.xml'
        if descriptor.is_file():
            for setting in ET.parse(descriptor).getroot().iter('setting'):
                sid = setting.get('id')
                if sid and setting.get('type') not in ('action', 'sep', 'lsep'):
                    try:values[sid] = addon.getSetting(sid)
                    except RuntimeError:pass
        saved = roots[identifier] / 'settings.xml'
        if saved.is_file() and identifier != 'skin.dexhub':
            for setting in ET.parse(saved).getroot().iter('setting'):
                sid = setting.get('id')
                if sid:
                    try:values[sid] = addon.getSetting(sid)
                    except RuntimeError:pass
        settings[identifier] = values
    import xbmc
    core = {}
    for setting in CORE_SETTINGS:
        result = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc':'2.0','id':1,
            'method':'Settings.GetSettingValue','params':{'setting':setting}})))
        if 'value' in result.get('result', {}):core[setting] = result['result']['value']
    return home, roots, settings, core

def virtual_xml(roots):
    """tvOS stores userdata XML in NSUserDefaults; Kodi VFS can read it."""
    import xbmcvfs
    result = {}
    for identifier, root in roots.items():
        def walk(folder):
            directories, files = xbmcvfs.listdir(str(folder) + '/')
            for name in files:
                if not name.lower().endswith('.xml'):continue
                path=folder/name
                handle=xbmcvfs.File(str(path))
                try:payload=bytes(handle.readBytes())
                finally:handle.close()
                result['profiles/'+identifier+'/'+path.relative_to(root).as_posix()]=payload
            for name in directories:
                path=folder/name
                if name.lower() not in SKIP_DIRS and not path.is_symlink():walk(path)
        walk(Path(root))
    return result

def create_archive(destination, roots, settings, core=None, fonts=None, virtual=None):
    destination = Path(destination)
    manifest = {'format':FORMAT, 'version':1, 'created':int(time.time()),
                'roots':sorted(roots), 'settings':settings, 'core':core or {}, 'files':{}}
    total = 0
    with tempfile.TemporaryDirectory() as directory, ZipFile(destination,'w',ZIP_DEFLATED) as archive:
        for identifier, root in roots.items():
            if identifier not in ROOTS:raise ValueError('Unknown backup profile')
            root = Path(root)
            if not root.exists():continue
            for path in sorted(root.rglob('*')):
                relative = path.relative_to(root)
                if path.resolve() == destination.resolve() or not path.is_file() or path.is_symlink() or any(part.lower() in SKIP_DIRS for part in relative.parts):continue
                if path.name in ('.dexhub-json-restore-pending.json', 'nuvio_sync_client_identity.json', 'profile_transition.json', 'muted_by_trailer', 'muted_by_trailer.tmp'):continue
                if path.name.endswith(('-wal','-shm','-journal','.tmp','.log')) or path.name.endswith('_cache.db'):continue
                source = path
                with path.open('rb') as handle:sqlite = handle.read(16)==b'SQLite format 3\x00'
                if sqlite:
                    source = Path(directory)/(uuid.uuid4().hex+'.db')
                    source_db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
                    target_db=sqlite3.connect(str(source))
                    try:source_db.backup(target_db)
                    finally:target_db.close();source_db.close()
                size=source.stat().st_size;total+=size
                if total>LIMIT:raise ValueError('Backup data exceeds 256 MiB')
                name='profiles/'+identifier+'/'+relative.as_posix()
                payload=source.read_bytes()
                if virtual and name in virtual:
                    payload=virtual[name];total+=len(payload)-size
                    if total>LIMIT:raise ValueError('Backup data exceeds 256 MiB')
                archive.writestr(name,payload)
                manifest['files'][name]={'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        for name,payload in (virtual or {}).items():
            if name in manifest['files']:continue
            total+=len(payload)
            if total>LIMIT:raise ValueError('Backup data exceeds 256 MiB')
            archive.writestr(name,payload)
            manifest['files'][name]={'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        if fonts and Path(fonts).is_dir():
            for path in sorted(Path(fonts).glob('*')):
                if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.ttf','.otf'):
                    payload=path.read_bytes();total+=len(payload)
                    if total>LIMIT:raise ValueError('Backup data exceeds 256 MiB')
                    name='fonts/'+path.name;archive.writestr(name,payload)
                    manifest['files'][name]={'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        encoded=json.dumps(manifest,ensure_ascii=False).encode()
        if len(encoded)>2*1024*1024 or len(manifest['files'])>49999:
            raise ValueError('Backup contains too many files')
        archive.writestr('manifest.json',encoded)
    return manifest

def stage_restore(archive_path, home):
    home=Path(home);home.mkdir(parents=True,exist_ok=True)
    pending=home/'dexhub-restore-pending'
    if (home/'userdata/addon_data/plugin.video.dexhub/.dexhub-json-restore-pending.json').exists():
        raise ValueError('A JSON restore is pending; restart Kodi first')
    if pending.exists():raise ValueError('A restore is already pending; restart Kodi first')
    stage=Path(tempfile.mkdtemp(prefix='.dexhub-restore-',dir=str(home)))
    try:
        with ZipFile(archive_path) as archive:
            if archive.getinfo('manifest.json').file_size>2*1024*1024:raise ValueError('Invalid backup manifest')
            manifest=json.loads(archive.read('manifest.json'))
            if manifest.get('format')!=FORMAT or manifest.get('version')!=1:raise ValueError('Select a full DexHub backup ZIP')
            roots=manifest.get('roots') or []
            if not roots or len(set(roots))!=len(roots) or any(root not in ROOTS for root in roots):raise ValueError('Invalid backup profiles')
            if set(manifest.get('settings') or {})-set(roots):raise ValueError('Unexpected settings profile')
            if set(manifest.get('core') or {})-set(CORE_SETTINGS):raise ValueError('Unexpected Kodi setting')
            for values in (manifest.get('settings') or {}).values():
                if not isinstance(values,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in values.items()):raise ValueError('Invalid addon settings')
            names=archive.namelist()
            if len(names)>50000 or len(set(names))!=len(names):raise ValueError('Invalid backup entries')
            if set(names)-{'manifest.json'}!=set(manifest.get('files') or {}):raise ValueError('Backup file list mismatch')
            total=0
            for info in archive.infolist():
                if info.filename=='manifest.json':continue
                name=info.filename;path=PurePosixPath(name)
                if '\\' in name or '\x00' in name or path.is_absolute() or '..' in path.parts or path.as_posix()!=name:
                    raise ValueError('Unsafe backup path')
                if stat.S_ISLNK(info.external_attr>>16) or info.is_dir():raise ValueError('Unsupported backup entry')
                profile=len(path.parts)>=3 and path.parts[0]=='profiles' and path.parts[1] in roots
                font=len(path.parts)==2 and path.parts[0]=='fonts' and path.suffix.lower() in ('.ttf','.otf')
                if not profile and not font:raise ValueError('Unexpected backup content')
                total+=info.file_size
                if total>LIMIT:raise ValueError('Expanded backup exceeds 256 MiB')
                entry=manifest['files'][name]
                if entry.get('size')!=info.file_size:raise ValueError('Backup size mismatch')
                payload=archive.read(info)
                if hashlib.sha256(payload).hexdigest()!=entry.get('sha256'):raise ValueError('Backup checksum mismatch')
                target=stage.joinpath(*path.parts);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
                if payload.startswith(b'SQLite format 3\x00'):
                    db=sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True)
                    try:
                        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid backup database')
                    finally:db.close()
            for root in roots:(stage/'profiles'/root).mkdir(parents=True,exist_ok=True)
            (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False))
        os.rename(stage,pending)
    except Exception:
        shutil.rmtree(stage,ignore_errors=True);raise
    return {'ok':True,'restart_required':True,'profiles':len(roots),'files':len(manifest['files'])}

def receive_file(path, home, name):
    home=Path(home);suffix=Path(name).suffix.lower()
    if suffix not in ('.zip','.srt','.ass','.ssa','.vtt','.ttf','.otf','.m3u','.m3u8','.json'):
        raise ValueError('Unsupported file type')
    if suffix in ('.ttf','.otf'):
        with open(path,'rb') as handle:signature=handle.read(4)
        if signature not in (b'\x00\x01\x00\x00',b'OTTO',b'ttcf'):raise ValueError('Invalid font file')
        target=home/'media/Fonts'/('Uploaded-'+uuid.uuid4().hex+suffix)
    else:target=home/'dexhub-imports'/(uuid.uuid4().hex+suffix)
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    result={'ok':True,'filename':str(name),'path':str(target),'upload_id':target.stem,'type':suffix}
    if suffix=='.zip':
        try:
            with ZipFile(target) as archive:
                if len(archive.infolist())>50000 or sum(i.file_size for i in archive.infolist())>LIMIT:
                    raise ValueError('Expanded addon ZIP exceeds the transfer limit')
                if len(archive.namelist())!=len(set(archive.namelist())):raise ValueError('Duplicate addon ZIP entries')
                descriptors=[n for n in archive.namelist() if n.count('/')==1 and n.endswith('/addon.xml')]
                if len(descriptors)!=1:raise ValueError('Choose a Kodi addon ZIP with one addon.xml')
                if archive.getinfo(descriptors[0]).file_size>2*1024*1024:raise ValueError('Invalid addon descriptor')
                node=ET.fromstring(archive.read(descriptors[0]))
                if node.get('id')!=descriptors[0].split('/')[0]:raise ValueError('Addon ZIP identity mismatch')
                for info in archive.infolist():
                    p=PurePosixPath(info.filename)
                    if not p.parts or p.is_absolute() or '..' in p.parts or '\\' in info.filename or '\x00' in info.filename or p.parts[0]!=node.get('id') or stat.S_ISLNK(info.external_attr>>16):raise ValueError('Unsafe addon ZIP path')
                result.update(addon_id=node.get('id'),version=node.get('version'),name=node.get('name'))
        except Exception:target.unlink(missing_ok=True);raise
    return result

def apply_core_restore():
    import xbmc
    import xbmcvfs
    path=Path(xbmcvfs.translatePath('special://home/dexhub-restored-core.json'))
    if not path.exists():return
    data=json.loads(path.read_text());success=True
    for key,value in data.items():
        if key not in CORE_SETTINGS:continue
        result=json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc':'2.0','id':1,'method':'Settings.SetSettingValue','params':{'setting':key,'value':value}})))
        success=success and bool(result.get('result'))
    if success:path.unlink()
