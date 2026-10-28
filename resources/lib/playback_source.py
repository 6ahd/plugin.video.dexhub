"""Display-only playback origins. Never expose addresses, file paths or tokens."""
import re

_SYNC = {'trakt', 'simkl', 'floppy', 'nuvio', 'stremio', 'kodi', 'local', 'external', 'mdblist'}
_BRANDS = {'plex': 'Plex', 'emby': 'Emby', 'jellyfin': 'Jellyfin', 'silo': 'Silo', 'dexstream': 'Dexstream'}

def safe(value):
    text = str(value or '').strip()
    if not text or len(text) > 96 or any(c in text for c in '/\\:=?&[]\r\n'):
        return ''
    return text

def origin(row):
    name = safe(row.get('provider_name'))
    backend = str(row.get('native_backend') or row.get('server_type') or '').lower()
    head, sep, tail = name.partition('•')
    head, tail = head.strip(), tail.strip()
    word = head.lower().split(' ')[0] if head else ''
    kind = backend if backend in _BRANDS else word if word in _BRANDS else ''
    if kind:
        server = safe(row.get('native_server_name') or row.get('server_name')) or tail
        brand = _BRANDS[kind]
        return (brand + (' • ' + server if server and server.casefold() != brand.casefold() else ''), kind)
    if not name or name.casefold() in _SYNC:
        return None
    return (name, 'addon')

def label(api, row):
    direct = origin(row)
    if direct:
        return direct
    # Exact item/episode history only; this changes no playback route or URL.
    try:
        from . import playback_choices
        record = playback_choices.saved(api, row.get('media_type'), row.get('canonical_id'),
                                        row.get('video_id'), row.get('season'), row.get('episode'))
        return origin(record.get('ctx') or {})
    except Exception:
        return None
