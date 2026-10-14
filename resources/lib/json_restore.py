"""Validated DexHub JSON imports, staged until the next Kodi launch."""
import base64
import json
import math
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PENDING = '.dexhub-json-restore-pending.json'
MAX_JSON = 32 * 1024 * 1024

def normalize(data, allowed_settings):
    from . import backup
    if not isinstance(data, dict):
        raise ValueError('Select a DexHub settings backup JSON')
    if data.get('dexhub_export_version') == 1:
        settings = dict(data.get('settings') or {})
        if not isinstance(data.get('settings', {}), dict):
            raise ValueError('Invalid settings')
        if 'bucket_art_map' in data:
            if not isinstance(data['bucket_art_map'], dict):raise ValueError('Invalid artwork map')
            settings['bucket_art_map'] = json.dumps(data['bucket_art_map'], ensure_ascii=False)
        files = {}
        for field, name in [('providers','providers.json'), ('catalog_pins','hub_catalog_pins.json')]:
            if field in data:
                if not isinstance(data[field],list):raise ValueError('Invalid '+field)
                files[name] = base64.b64encode(json.dumps(data[field],ensure_ascii=False).encode()).decode()
        data = {'format':backup.FORMAT,'version':backup.VERSION,'parts':{
            'settings':{'settings':settings},'sources':{'files':files}}}
    if data.get('format') != backup.FORMAT or data.get('version') != backup.VERSION:
        raise ValueError('Unsupported DexHub JSON backup format')
    parts = data.get('parts')
    known = {row[0] for row in backup.PARTS}
    if not isinstance(parts,dict) or not parts or set(parts)-known:
        raise ValueError('Invalid backup sections')
    result = {'format':backup.FORMAT,'version':backup.VERSION,'parts':{}}
    total = 0
    for part, body in parts.items():
        if not isinstance(body,dict):raise ValueError('Invalid backup section')
        target = {'settings':{},'files':{}}
        settings = body.get('settings') or {}
        files = body.get('files') or {}
        if not isinstance(settings,dict) or not isinstance(files,dict):raise ValueError('Invalid backup values')
        for key,value in settings.items():
            if not isinstance(key,str) or not isinstance(value,(str,int,float,bool,type(None))):raise ValueError('Invalid setting value')
            if key in allowed_settings:
                if isinstance(value,float) and not math.isfinite(value):raise ValueError('Invalid numeric setting')
                text = '' if value is None else ('true' if value else 'false') if isinstance(value,bool) else str(value)
                if len(text)>1024*1024:raise ValueError('Setting too large')
                target['settings'][key] = text
        for name,encoded in files.items():
            if name not in backup.FILES.get(part,()):raise ValueError('Unexpected backup file')
            if not isinstance(encoded,str):raise ValueError('Invalid backup encoding')
            payload = base64.b64decode(encoded,validate=True)
            total += len(payload)
            if total>MAX_JSON:raise ValueError('Expanded JSON backup too large')
            if name.endswith('.json'):json.loads(payload.decode('utf-8-sig'))
            target['files'][name] = encoded
        for field in ('favorites','playback'):
            if field in body:
                if (part,field) not in [('library','favorites'),('progress','playback')]:raise ValueError('Unexpected records')
                rows=body[field]
                if not isinstance(rows,list) or len(rows)>20000 or any(not isinstance(row,dict) for row in rows):raise ValueError('Invalid records')
                target[field] = rows
        result['parts'][part] = target
    return result

def stage(path, home):
    from .full_backup import context, create_archive, virtual_xml
    home=Path(home)
    if Path(path).stat().st_size>MAX_JSON:raise ValueError('Select a JSON under 32 MiB')
    import xbmcaddon
    addon=xbmcaddon.Addon('plugin.video.dexhub')
    descriptor=Path(addon.getAddonInfo('path'))/'resources/settings.xml'
    allowed={node.get('id') for node in ET.parse(descriptor).getroot().iter('setting')
             if node.get('id') and node.get('type') not in ('action','sep','lsep')}
    normalized=normalize(json.loads(Path(path).read_text(encoding='utf-8-sig')),allowed)
    _,roots,settings,_=context()
    identifier='plugin.video.dexhub'
    profile=roots[identifier];profile.mkdir(parents=True,exist_ok=True)
    pending=profile/PENDING
    if pending.exists() or (home/'dexhub-restore-pending').exists():raise ValueError('Restart Kodi to apply the pending restore first')
    folder=home/'dexhub-json-restore-backups';folder.mkdir(parents=True,exist_ok=True)
    archive=folder/('before-json-%s-%s.zip'%(time.strftime('%Y%m%d-%H%M%S'),time.time_ns()))
    selected={identifier:profile}
    create_archive(archive,selected,{identifier:settings[identifier]},virtual=virtual_xml(selected))
    temporary=pending.with_suffix('.tmp')
    temporary.write_text(json.dumps(normalized,ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,pending)
    return {'ok':True,'restart_required':True,'type':'dexhub_json','sections':list(normalized['parts'])}

def apply_pending():
    from . import backup
    import xbmc, xbmcaddon, xbmcvfs
    addon=xbmcaddon.Addon('plugin.video.dexhub')
    pending=Path(xbmcvfs.translatePath(addon.getAddonInfo('profile')))/PENDING
    if not pending.is_file():return False
    if xbmc.Player().isPlayingVideo():return False
    data=json.loads(pending.read_text(encoding='utf-8'))
    backup.restore(data,list(data['parts']),strict=True)
    pending.unlink()
    xbmc.executebuiltin('ReloadSkin()')
    return True
