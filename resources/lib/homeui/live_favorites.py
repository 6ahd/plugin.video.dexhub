# -*- coding: utf-8 -*-
"""Favorite channels per source, with atomic private storage."""
import json
import os
import threading

_LOCK = threading.RLock()
_FILE = 'live_favorites.json'

def _read(profile):
    try:
        with open(os.path.join(profile, _FILE), encoding='utf-8') as f:
            data = json.load(f)
        return [e for e in data if isinstance(e, dict) and e.get('key') and e.get('src')] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []

def key(tile):
    source = str(tile.get('src') or 'pvr')
    if source == 'pvr':
        try:
            return 'pvr:%d' % int(tile.get('channelid') or 0)
        except (ValueError, TypeError):
            return ''
    return str(tile.get('path') or '')

def keys(profile):
    with _LOCK:
        return {e['key'] for e in _read(profile)}

def toggle(profile, tile):
    from . import live_sources as LS
    channel_key = key(tile)
    if not channel_key or channel_key == 'pvr:0':
        return False
    with _LOCK:
        entries = _read(profile)
        added = not any(e['key'] == channel_key for e in entries)
        entries = [e for e in entries if e['key'] != channel_key]
        if added:
            entries.append({'key': channel_key, 'src': str(tile.get('src') or 'pvr'),
                            'id': tile.get('channelid') or 0,
                            'tile': {k: tile.get(k) for k in LS._MINIMAL}})
        path = os.path.join(profile, _FILE)
        os.makedirs(profile, exist_ok=True)
        tmp = path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(entries, f, ensure_ascii=False)
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return added

def tiles(profile, source, card=''):
    from . import live, live_sources as LS
    with _LOCK:
        entries = [e for e in _read(profile) if e['src'] == source]
    out = []
    for entry in entries:
        if source == 'pvr':
            details = live._channel_details(entry.get('id'))
            if not details or details.get('hidden'):
                continue
            tile = live.channel_tile(details, '', card)
        else:
            saved = entry.get('tile') or {}
            path = str(saved.get('path') or '')
            if not path.startswith('live://%s/' % source):
                continue
            tile = LS.channel_tile(source, path.split('/', 3)[-1], saved.get('title'), card,
                                   logo=saved.get('logo'), group=saved.get('group'),
                                   number=saved.get('number'), stream=saved.get('stream'),
                                   desc=saved.get('desc'), fanart=saved.get('fanart'),
                                   epg=saved.get('epg'), genre=saved.get('genre'))
        out.append(tile)
    return out
