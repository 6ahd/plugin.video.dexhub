"""Shared, bounded TheIntroDB v4 reads. No SDK or companion add-on required."""
import json
import math
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_LOCK = threading.Lock()


def segments(data, duration):
    result = {}
    for kind in ('intro', 'recap', 'credits', 'preview'):
        valid = []
        rows = data.get(kind, []) if isinstance(data, dict) else []
        for row in rows[:256] if isinstance(rows, list) else []:
            try:
                a, b = row.get('start_ms'), row.get('end_ms')
                if isinstance(a, bool) or isinstance(b, bool):
                    continue
                a = 0.0 if a is None else float(a) / 1000.0
                b = duration if b is None else float(b) / 1000.0
                if all(math.isfinite(n) for n in (a, b)) and 0 <= a < b <= duration:
                    valid.append((a, b))
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
        result[kind] = sorted(set(valid))
    return result


def lookup(playing, duration, diagnostics=None):
    """Return segments and optionally explain a missing match, without stream URLs."""
    from .credits_panel import _introdb_params
    from . import tmdb_direct as cache
    params = _introdb_params(playing, duration)
    if not params:
        state = 'invalid_identity'
        if playing.get('series') and (playing.get('season') is None or playing.get('episode') is None):
            state = 'missing_episode'
        if diagnostics is not None:
            diagnostics.update(state=state, api='v4', params={})
        return {}
    query = urlencode(params)
    key = 'tidb_v4_media_status:' + query
    # Credits and skip workers share one request, including simultaneous calls.
    with _LOCK:
        stored = cache._cache_get(key)
        cached = stored is not None
        if stored is None:
            ttl, code, state, data = 7 * 86400, 200, 'ready', {}
            try:
                request = Request('https://api.theintrodb.org/v4/media?' + query,
                                  headers={'Accept': 'application/json', 'User-Agent': 'DexHub/5.10.353'})
                with urlopen(request, timeout=5) as response:
                    body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise ValueError('oversized response')
                data = json.loads(body.decode('utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('invalid response')
                if not any(data.get(k) for k in ('intro', 'recap', 'credits', 'preview')):
                    ttl, state = 6 * 3600, 'no_segments'
            except HTTPError as exc:
                code = exc.code
                state = {404: 'not_found', 400: 'invalid_request', 422: 'invalid_request',
                         429: 'rate_limited'}.get(code, 'service_error')
                # v4 distinguishes a missing episode from no submitted timestamps.
                try:
                    error = json.loads(exc.read(8192).decode('utf-8')).get('code')
                    if code == 404 and error in ('no_submissions', 'episode_not_found', 'media_not_found'):
                        state = error
                except Exception:
                    pass
                data, ttl = {}, 6 * 3600 if code == 404 else 60
            except (ValueError, UnicodeError):
                data, ttl, state, code = {}, 60, 'invalid_response', 0
            except Exception:
                data, ttl, state, code = {}, 60, 'network_error', 0
            stored = {'data': data, 'state': state, 'http': code}
            cache._cache_set(key, stored, ttl)
    result = segments(stored.get('data', {}), float(duration))
    state = stored.get('state', 'no_segments')
    if state == 'ready' and not any(result.values()):
        state = 'no_segments'
    if diagnostics is not None:
        diagnostics.update(state=state, api='v4', params=dict(params),
                           http=stored.get('http', 0), cached=cached,
                           segments={k: v for k, v in result.items() if v})
    return result
