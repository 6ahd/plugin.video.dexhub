"""Keep external playback identity across Kodi suspension and process restart."""
import json
import math
import os
import time
import uuid


def _path(name):
    import xbmcvfs
    return xbmcvfs.translatePath('special://home/dexhub-infuse-' + name + '.json')


def _read(name):
    try:
        with open(_path(name), encoding='utf-8') as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(name, value):
    path = _path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.replace(temporary, path)


def duration_seconds(ctx):
    for key, divisor in (('duration', 1), ('duration_s', 1), ('duration_ms', 1000)):
        try:
            value = float((ctx or {}).get(key) or 0) / divisor
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 0.0


def prepare(video, position, filename='', ctx=None, duration=0):
    from urllib.parse import urlencode
    token = uuid.uuid4().hex
    context = dict(ctx or {})
    if not context:
        from .session_store import load_session
        current = load_session()
        if (current.get('stream_url') or '').partition('|')[0] == video:
            context = current
    _write('pending', {'token': token, 'video': video, 'kodi_path': video,
        'position': max(0, float(position or 0)), 'duration': duration or duration_seconds(context),
        'filename': filename, 'context': context, 'created': time.time()})
    return {key: 'dexhub-player://' + status + '?' + urlencode({'token': token})
            for key, status in (('x-success', 'success'), ('x-error', 'error'))}


def process_return(save_progress=None):
    import xbmc
    incoming = _read('return')
    if not incoming:
        return False
    pending = _read('pending')
    if pending.get('returned') or not pending.get('token') or incoming.get('token') != pending['token']:
        os.remove(_path('return'))
        return False
    if incoming.get('status') == 'error':
        os.remove(_path('return'))
        xbmc.log('[DexHub] Infuse returned a playback error; progress unchanged', xbmc.LOGWARNING)
        return False
    if incoming.get('status') != 'success' or incoming.get('lastPlayedUrl') != pending.get('video'):
        os.remove(_path('return'))
        return False
    try:
        position = float(incoming['position'])
        duration = float(pending.get('duration') or 0)
        if not math.isfinite(position) or position < 0 or not math.isfinite(duration) or duration < 0:
            raise ValueError('Invalid playback time')
    except (ValueError, TypeError, KeyError):
        os.remove(_path('return'))
        return False
    if duration > 0:
        position = min(position, duration)
    finished = duration > 0 and position / duration >= 0.9
    # Update Kodi's file bookmark and DexHub's existing continue-watching store.
    result = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1,
        'method': 'Files.SetFileDetails', 'params': {'file': pending.get('kodi_path') or pending['video'],
            'media': 'video', 'resume': {'position': 0 if finished else position, 'total': duration}}})))
    if result.get('error'):
        xbmc.log('[DexHub] Kodi file bookmark update failed', xbmc.LOGWARNING)
    if pending.get('context'):
        if save_progress is None:
            from .companion import _save_local_progress
            save_progress = _save_local_progress
        save_progress(pending['context'], position * 1000, duration * 1000, finished=finished)
    pending.update(position=position, completed=finished, returned=True)
    _write('pending', pending)
    os.remove(_path('return'))
    xbmc.log('[DexHub] Infuse return saved: position=%.1fs, duration=%.1fs' % (position, duration), xbmc.LOGINFO)
    return True


def run(monitor):
    import xbmc
    while not monitor.abortRequested():
        try:
            process_return()
        except Exception:
            xbmc.log('[DexHub] Infuse return processing failed; retrying', xbmc.LOGWARNING)
        if monitor.waitForAbort(1):
            break
