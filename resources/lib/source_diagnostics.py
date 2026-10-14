"""Bounded in-process status; credentials/URLs never enter diagnostic logs."""
import threading
import time

_lock = threading.RLock()
_states = {}

def trace(stage, index=-1, started=None):
    import xbmc
    elapsed = 0 if started is None else (time.monotonic()-started)*1000
    xbmc.log('[DexHub timing] stage=%s row=%d ms=%.1f' % (stage, index, elapsed), xbmc.LOGDEBUG)

def reset():
    with _lock:
        _states.clear()

def begin(target, media_type):
    provider, request = target
    key = (str(provider.get('id') or provider.get('name')), str(request))
    token = object()
    with _lock:
        if len(_states) >= 64 and key not in _states:
            _states.pop(next(iter(_states)))
        _states[key] = dict(name=provider.get('name') or 'Source', status='searching',
                            target=target, media_type=media_type, token=token, started=time.monotonic())
    return key, token

def finish(handle, result):
    key, token = handle
    with _lock:
        state = _states.get(key)
        if not state or state['token'] is not token:
            return
        if isinstance(result, Exception):
            status = 'error'
        elif not isinstance(result, dict):
            status = 'error'
        else:
            status = result.get('_source_status') or ('complete' if result.get('streams') else 'empty')
        state.update(status=status, elapsed=time.monotonic()-state['started'])

def snapshot():
    with _lock:
        return [dict(value) for value in _states.values()]

LABELS = {'searching':'يبحث', 'complete':'اكتمل', 'empty':'لا توجد نتائج',
          'error':'خطأ اتصال أو استجابة', 'skipped':'تم تخطيه مؤقتًا'}
