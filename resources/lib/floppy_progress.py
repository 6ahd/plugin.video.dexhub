# -*- coding: utf-8 -*-
"""Floppy's resume positions in Dex Hub's Continue Watching (v5.10.147).

The Floppy service add-on (service.dexhub.floppy) holds the server address
and the app token; this module only reads them. Floppy's
GET /api/v1/playback/progress/ lists the saved positions newest first
(movies and episodes; an episode carries its show's ids), so the rows are
stored like Simkl's and Trakt's: in playback_store with provider_name
'Floppy', where the Home, the widgets and the resume prompt already look.

The token needs the progress:read scope. Nothing here prints the token.
"""
import json
import ssl
import time
import urllib.error
import urllib.request

import xbmc

SERVICE_ID = 'service.dexhub.floppy'
PROVIDER = 'Floppy'
TIMEOUT = 6
_STATE = {'error': '', 'at': 0.0, 'count': 0}


_CREDS = {'t': 0.0, 'v': ('', '')}


def _service():
    try:
        if not xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % SERVICE_ID):
            return None
        import xbmcaddon
        return xbmcaddon.Addon(SERVICE_ID)
    except Exception:
        return None


def credentials():
    """(server, token) from the Floppy add-on, or ('', '').

    v5.10.184: kept 30 seconds. Each Home publish asked it (an add-on check
    that pauses Kodi's frame, then two settings): 40 to 60 ms a time."""
    import time
    now = time.time()
    if now - _CREDS['t'] < (30.0 if _CREDS['v'][0] else 5.0):
        return _CREDS['v']
    addon = _service()
    if addon is None:
        server, token = '', ''
    else:
        server = (addon.getSetting('server') or '').strip().rstrip('/')
        token = (addon.getSetting('token') or '').strip()
        if server and not server.startswith(('http://', 'https://')):
            server = 'https://' + server
    _CREDS['t'], _CREDS['v'] = now, (server, token)
    return server, token


def configured():
    server, token = credentials()
    return bool(server and token)


def last_error():
    return _STATE.get('error') or ''


def _get(path):
    server, token = credentials()
    if not (server and token):
        return 0, None
    req = urllib.request.Request(server + '/api/v1/' + path, headers={
        'Accept': 'application/json',
        'Authorization': 'Bearer ' + token,
        'User-Agent': 'DexHub Kodi',
    })
    try:
        context = ssl.create_default_context()
    except Exception:
        context = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=context) as resp:
            return resp.getcode(), json.loads(resp.read().decode('utf-8') or 'null')
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:
        xbmc.log('[DexHub] floppy progress: %s' % exc.__class__.__name__, xbmc.LOGINFO)
        return 0, None


def _epoch(value):
    from .native_continue import _epoch as parse
    return parse(value)


def _canonical(ids):
    imdb = str(ids.get('imdb') or '').strip()
    if imdb:
        return imdb if imdb.startswith('tt') else 'tt' + imdb
    for key in ('tmdb', 'tvdb'):
        value = str(ids.get(key) or '').strip()
        if value:
            return '%s:%s' % (key, value)
    return ''


def to_row(entry):
    """One Floppy entry as a playback_store row, or None."""
    if not isinstance(entry, dict) or entry.get('completed'):
        return None
    kind = entry.get('media_type')
    if kind not in ('movie', 'episode'):
        return None
    ids = entry.get('ids') or {}
    canonical = _canonical(ids)
    if not canonical:
        return None
    position = float(entry.get('position_seconds') or 0)
    duration = float(entry.get('duration_seconds') or 0)
    if position <= 0:
        return None
    percent = round(position * 100.0 / duration, 2) if duration > 0 else 0.0
    if percent >= 90:      # v5.10.156: as the Nuvio apps (issue #4)
        return None
    stamp = int(_epoch(entry.get('updated_at')) or time.time())
    row = {
        'canonical_id': canonical, 'provider_name': PROVIDER,
        'poster': str(entry.get('image') or ''), 'background': '', 'clearlogo': '',
        'position': position, 'duration': duration, 'percent': percent,
        'stream_url': '', 'event_type': 'progress', 'ext_updated_at': stamp,
        'imdb_id': str(ids.get('imdb') or ''), 'tmdb_id': str(ids.get('tmdb') or ''),
        'tvdb_id': str(ids.get('tvdb') or ''), 'show_tmdb_id': '',
    }
    if kind == 'episode':
        try:
            season = int(entry.get('season_number') or 0)
            episode = int(entry.get('episode_number') or 0)
        except (TypeError, ValueError):
            return None
        if season <= 0 or episode <= 0:
            return None
        row.update({'media_type': 'series', 'video_id': '%s:%d:%d' % (canonical, season, episode),
                    'title': str(entry.get('series_title') or entry.get('title') or ''),
                    'season': season, 'episode': episode,
                    'show_tmdb_id': str(ids.get('tmdb') or '')})
    else:
        row.update({'media_type': 'movie', 'video_id': canonical,
                    'title': str(entry.get('title') or ''), 'season': 0, 'episode': 0})
    return row


def fetch(limit=60):
    """Floppy's unfinished positions as rows; None when Floppy did not answer."""
    status, data = _get('playback/progress/?media_type=movie,episode&completed=false&limit=%d' % int(limit))
    if status in (401, 403):
        _STATE.update(error='scope', at=time.time())
        xbmc.log('[DexHub] floppy progress: the token is refused (needs progress:read)', xbmc.LOGWARNING)
        return None
    if status != 200 or not isinstance(data, dict):
        _STATE.update(error='offline' if not status else 'http %d' % status, at=time.time())
        return None
    rows = [to_row(entry) for entry in (data.get('results') or [])]
    rows = [row for row in rows if row]
    _STATE.update(error='', at=time.time(), count=len(rows))
    return rows


_SEQ_FULL_EVERY = 1800.0      # a full read at least this often, changes or not


def _seq_path():
    import os
    import xbmcvfs
    return os.path.join(xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/'),
                        'floppy_progress_seq.json')


def _seq_load():
    try:
        with open(_seq_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _seq_save(newest, server):
    try:
        with open(_seq_path(), 'w', encoding='utf-8') as handle:
            json.dump({'newest': newest, 'server': server, 'at': time.time()}, handle)
    except Exception:
        pass


def _newest_change():
    """Floppy's newest resume change number (sync/progress-changes, one row),
    or None when the token cannot read it (sync:read) or Floppy is away."""
    status, data = _get('sync/progress-changes/?cursor=0&limit=1')
    if status != 200 or not isinstance(data, dict):
        return None
    newest = data.get('newest_sequence')
    return int(newest) if isinstance(newest, int) else None


def sync(limit=60):
    """Mirror Floppy's positions into playback_store; changed rows, or 0.

    v5.10.154: Floppy's change feed is asked first (one tiny read); when
    nothing changed since the last full read, the full list is not read
    again (at most every 30 minutes anyway)."""
    if not configured():
        return 0
    server, _token = credentials()
    saved = _seq_load()
    newest = _newest_change()
    if (newest is not None and saved.get('server') == server and saved.get('newest') == newest
            and time.time() - float(saved.get('at') or 0) < _SEQ_FULL_EVERY):
        return 0
    rows = fetch(limit)
    if rows is None:
        return 0
    _save_keys(rows)
    if newest is not None:
        _seq_save(newest, server)
    from .dexhub import playback_store
    try:
        return int(playback_store.reconcile_provider_rows(PROVIDER, rows) or 0)
    except Exception as exc:
        xbmc.log('[DexHub] floppy progress store failed: %s' % exc, xbmc.LOGWARNING)
        return 0


def _keys_path():
    import os
    import xbmcvfs
    return os.path.join(xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/'),
                        'floppy_progress_keys.json')


def _save_keys(rows):
    """The titles Floppy listed last, for "Continue Watching from Floppy only"
    (a title also played here keeps its own row name)."""
    try:
        data = sorted({'%s|%s' % (row['canonical_id'], row['video_id']) for row in rows})
        with open(_keys_path(), 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
    except Exception:
        pass


def keys():
    try:
        with open(_keys_path(), 'r', encoding='utf-8') as handle:
            return set(json.load(handle) or [])
    except Exception:
        return set()
