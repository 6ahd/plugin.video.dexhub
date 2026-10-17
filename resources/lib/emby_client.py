# -*- coding: utf-8 -*-
"""Native Emby/Jellyfin client for Dex Hub.

Same contract as plex_client: no UI, no Kodi directory code, returns plain
dicts in the SAME item shape as Plex items, so rendering, the source picker,
the switch-source index, resume and subtitles all reuse one code path.

Endpoints follow EmbyCon's proven usage (AuthenticateByName +
X-Emby-Authorization) and MediaSources for versions/sizes.
"""
from __future__ import absolute_import

import json
import hashlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl, quote
from urllib.request import Request, urlopen

import xbmc
import xbmcaddon
import xbmcgui


def _addon():
    """A FRESH Addon handle per access.

    v3.9.186: the module-level singleton was built once per warm Python
    interpreter (reuselanguageinvoker=true), so the interpreter rendering the
    home page could hold a settings snapshot from BEFORE sign-in and keep
    reading an empty token — linking "worked" yet no section, no search results
    and no sources ever appeared.
    """
    return xbmcaddon.Addon()

WINDOW_ID = 10000
CACHE_SECONDS = 15 * 60
PAGE_SIZE = 80
WINDOW_ID = 10000
_VIEWS_MEM = {}

# v5.10.162: MediaSources too, so a library row knows each copy's quality and
# size (the hero and the cards show them as badges, as Plex rows do)
BROWSE_FIELDS = ('Overview,Genres,Studios,ProviderIds,OfficialRating,'
                 'CommunityRating,PremiereDate,ProductionYear,DateCreated,MediaSources')
DETAIL_FIELDS = ('MediaSources,Overview,Genres,Studios,People,ProviderIds,'
                 'OfficialRating,CommunityRating,PremiereDate,ProductionYear,Path')


class EmbyError(Exception):
    pass


def _setting(name, value=None):
    if value is None:
        try:
            return _addon().getSetting(name) or ''
        except Exception:
            return ''
    try:
        _addon().setSetting(name, value)
    except Exception:
        pass
    return value


def _auth_prop(name):
    if not str(name or '').endswith('auth_json'):
        return ''
    return 'dexhub.%s.auth' % str(name).split('_', 1)[0]


def _parse_json_setting(name, default):
    try:
        raw = _setting(name)
        if not raw:
            key = _auth_prop(name)
            if key:
                raw = xbmcgui.Window(WINDOW_ID).getProperty(key) or ''
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _save_json_setting(name, value):
    try:
        blob = json.dumps(value)
        _setting(name, blob)
        key = _auth_prop(name)
        if key:
            # Shared by every warm interpreter, so the home page sees the new
            # sign-in immediately even if a cached Addon handle lags behind.
            xbmcgui.Window(WINDOW_ID).setProperty(key, blob)
    except Exception:
        pass


# v5.10.27: per-thread request-timeout ceiling. The source scan and the
# pre-play PlaybackInfo refresh have their own budgets (5-7s) but every
# request underneath them used the global `timeout` setting (10-20s), so one
# hung request blew straight through the budget. Callers wrap the bounded
# section in `bounded_timeout(seconds)`; nothing else changes.
_TIMEOUT_OVERRIDE = threading.local()


class bounded_timeout(object):
    def __init__(self, seconds):
        self.seconds = float(seconds)
        self.previous = None

    def __enter__(self):
        self.previous = getattr(_TIMEOUT_OVERRIDE, 'value', None)
        _TIMEOUT_OVERRIDE.value = self.seconds
        return self

    def __exit__(self, *exc):
        _TIMEOUT_OVERRIDE.value = self.previous
        return False


def _timeout():
    override = getattr(_TIMEOUT_OVERRIDE, 'value', None)
    try:
        base = max(5, int(_addon().getSetting('timeout') or '20'))
    except Exception:
        base = 20
    if override:
        return max(2, min(base, int(override)))
    return base


def _device_id(backend='emby'):
    backend = _backend({'backend': backend})
    key = '%s_device_id' % backend
    value = _setting(key)
    if not value:
        value = uuid.uuid4().hex
        _setting(key, value)
    return value


def _headers(token='', backend='emby'):
    auth = ('MediaBrowser Client="Dex Hub", Device="Kodi", DeviceId="%s", '
            'Version="1.0.0"' % _device_id(backend))
    headers = {
        'Accept': 'application/json',
        'Accept-Charset': 'UTF-8,*',
        'Content-Type': 'application/json',
        'X-Emby-Authorization': auth,
    }
    if token:
        headers['X-MediaBrowser-Token'] = token
        headers['X-Emby-Token'] = token
    return headers


def _pooled_get(url, headers, timeout):
    """GET through Dex Hub's shared keep-alive pool when urllib3 is present.

    Returning ``None`` means no pool is available and the caller should use
    the original urllib path. Network/status failures from an available pool
    are raised so we do not silently double every failed request.
    """
    try:
        from .dexhub import client as _http
        pool = _http._ensure_pool()
        if pool is None:
            return None
        urllib3 = _http._URLLIB3
    except Exception:
        return None

    last_exc = None
    for attempt in (0, 1):
        try:
            resp = pool.request(
                'GET', url, headers=headers,
                timeout=urllib3.Timeout(connect=min(5, timeout), read=timeout),
                redirect=True, retries=False, decode_content=True,
                preload_content=True,
            )
            status = int(getattr(resp, 'status', 0) or 0)
            if 500 <= status < 600 and attempt == 0:
                time.sleep(0.15)
                continue
            if status >= 400:
                raise EmbyError('Emby HTTP %s' % status)
            return bytes(getattr(resp, 'data', b'') or b'')
        except EmbyError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(0.15)
                continue
            raise EmbyError('%s' % exc)
    if last_exc is not None:
        raise EmbyError('%s' % last_exc)
    return None


def _request(url, method='GET', token='', data=None, timeout=None, backend='emby'):
    try:
        return _request_once(url, method, token, data, timeout, backend)
    except Exception as exc:
        # v5.10.314: just after the box woke the names do not resolve yet
        from .net_wake import wait_after_wake
        try:
            from urllib.parse import urlsplit as _split
            host = _split(url).hostname or ''
        except Exception:
            host = ''
        if wait_after_wake(exc, host):
            return _request_once(url, method, token, data, timeout, backend)
        raise


def _request_once(url, method='GET', token='', data=None, timeout=None, backend='emby'):
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8')
    headers = _headers(token, backend)
    request_timeout = float(timeout or _timeout())
    if str(method or 'GET').upper() == 'GET' and body is None:
        pooled = _pooled_get(url, headers, request_timeout)
        if pooled is not None:
            return pooled
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=request_timeout) as response:
            return response.read()
    except Exception as exc:
        raise EmbyError('%s' % exc)


def _json(raw):
    try:
        return json.loads((raw or b'').decode('utf-8'))
    except Exception as exc:
        raise EmbyError('Emby returned an unreadable response (%s)' % exc)


def _base(server):
    url = str((server or {}).get('url') or '').strip().rstrip('/')
    if url and not url.startswith(('http://', 'https://')):
        url = 'http://' + url
    return url


def _backend(server):
    value = str((server or {}).get('backend') or '').lower()
    return value if value in ('silo', 'jellyfin') else 'emby'


def _compat_prefix(server):
    # Silo exposes its Jellyfin-compatible surface at the server root. Emby
    # and Jellyfin accept the long-standing /emby alias used by EmbyCon.
    return '' if _backend(server) in ('silo', 'jellyfin') else '/emby'


def _api(server, path, params=None, token='', method='GET', data=None):
    pairs = [(str(k), str(v)) for k, v in (params or {}).items() if v not in (None, '')]
    query = urlencode(pairs)
    url = '%s%s%s' % (_base(server), _compat_prefix(server),
                      '/' + str(path or '').lstrip('/'))
    if query:
        url += '?' + query
    return _json(_request(url, method=method,
                          token=token or server.get('token') or '', data=data,
                          backend=_backend(server)))


# ─────────────────────────── auth ───────────────────────────

def account(backend='emby'):
    backend = _backend({'backend': backend})
    return _parse_json_setting('%s_auth_json' % backend, {}) or {}


def is_signed_in(backend='emby'):
    auth = account(backend)
    return bool(auth.get('token') and auth.get('user_id') and auth.get('url'))


def sign_out(backend='emby'):
    backend = _backend({'backend': backend})
    _setting('%s_auth_json' % backend, '')
    try:
        xbmcgui.Window(WINDOW_ID).clearProperty('dexhub.%s.auth' % backend)
    except Exception:
        pass
    _VIEWS_MEM.clear()


def sign_in(url, username, password='', backend='emby'):
    """AuthenticateByName, exactly like EmbyCon."""
    backend = _backend({'backend': backend})
    server = {'url': url, 'backend': backend}
    base = _base(server)
    if not base:
        raise EmbyError('أدخل عنوان السيرفر')
    raw = _request('%s%s/Users/AuthenticateByName?format=json' % (
                       base, _compat_prefix(server)),
                   method='POST',
                   data={'Username': str(username or ''), 'Pw': str(password or '')},
                   backend=backend)
    value = _json(raw)
    token = value.get('AccessToken') or ''
    user = value.get('User') or {}
    if not token or not user.get('Id'):
        raise EmbyError('فشل تسجيل الدخول إلى %s' % ('Silo' if backend == 'silo' else 'Emby'))
    auth = {
        'url': base,
        'token': str(token),
        'user_id': str(user.get('Id')),
        'username': user.get('Name') or username or '',
        'server_name': value.get('ServerName') or ('Silo' if backend == 'silo' else 'Emby'),
        'backend': backend,
        'signed_in_at': int(time.time()),
    }
    _save_json_setting('%s_auth_json' % backend, auth)
    _VIEWS_MEM.clear()
    return auth


def servers(backend='emby'):
    """Emby is one configured server; keep the Plex-shaped list contract."""
    backend = _backend({'backend': backend})
    auth = account(backend)
    if not is_signed_in(backend):
        return []
    return [{
        'id': auth.get('user_id') or 'emby',
        'name': auth.get('server_name') or ('Silo' if backend == 'silo' else 'Emby'),
        'url': auth.get('url') or '',
        'token': auth.get('token') or '',
        'user_id': auth.get('user_id') or '',
        'backend': backend,
    }]


# ─────────────────────── versions & items ───────────────────────

_CH_MAP = {1: '1.0', 2: '2.0', 6: '5.1', 8: '7.1'}


def _size_label(nbytes):
    try:
        n = int(nbytes or 0)
    except (TypeError, ValueError):
        return ''
    if n <= 0:
        return ''
    gb = n / (1024.0 ** 3)
    return '%.1f GB' % gb if gb >= 1 else '%.0f MB' % (n / (1024.0 ** 2))


def _resolution_label(height, width=0):
    try:
        h = int(height or 0)
        w = int(width or 0)
    except (TypeError, ValueError):
        return ''
    if h > 1088 or w >= 3800:
        return '4K'
    if h >= 1080:
        return '1080p'
    if h >= 720:
        return '720p'
    return 'SD' if h else ''


_BITMAP_SUBTITLE_CODECS = {
    'pgs', 'hdmv_pgs_subtitle', 'hdmv-pgs-subtitle', 'vobsub',
    'dvd_subtitle', 'dvd-subtitle', 'dvdsub', 'dvb_subtitle',
    'dvb-subtitle', 'dvbsub', 'xsub',
    # v5.10.237: Emby's own names for the image formats
    'pgssub', 'sup', 'dvbsub', 'vobsub',
}


def _subtitle_ext(codec):
    value = str(codec or '').lower()
    return {
        'subrip': 'srt', 'srt': 'srt', 'ass': 'ass', 'ssa': 'ssa',
        'webvtt': 'vtt', 'vtt': 'vtt', 'mov_text': 'srt',
    }.get(value, value or 'srt')


def _with_emby_token(url, token):
    if not url or not token:
        return url
    try:
        parts = urlsplit(url)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        lowered = {str(k).lower() for k, _v in pairs}
        if not ({'api_key', 'apikey', 'x-emby-token'} & lowered):
            pairs.append(('api_key', str(token)))
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(pairs), parts.fragment))
    except Exception:
        return url + ('&' if '?' in url else '?') + urlencode({'api_key': token})


def _external_subtitles(item_id, source, server):
    rows = []
    source_id = str(source.get('Id') or '')
    base = _base(server)
    token = str(server.get('token') or '')
    for stream in (source.get('MediaStreams') or []):
        if str(stream.get('Type') or '').lower() != 'subtitle':
            continue
        codec = str(stream.get('Codec') or '').lower()
        if codec in _BITMAP_SUBTITLE_CODECS:
            continue
        # Emby/Jellyfin expose sidecars through DeliveryUrl when available.
        # Otherwise build the same direct subtitle route used by EmbyCon.
        delivery = str(stream.get('DeliveryUrl') or '').strip()
        if delivery:
            if delivery.startswith(('http://', 'https://')):
                url = delivery
            else:
                url = base.rstrip('/') + '/' + delivery.lstrip('/')
        elif stream.get('IsExternal') or stream.get('Path'):
            try:
                index = int(stream.get('Index'))
            except Exception:
                continue
            ext = _subtitle_ext(codec)
            url = '%s%s/Videos/%s/%s/Subtitles/%s/Stream.%s' % (
                base.rstrip('/'), _compat_prefix(server), quote(str(item_id), safe=''),
                quote(source_id, safe=''), index, ext)
        else:
            continue
        url = _with_emby_token(url, token)
        lang = str(stream.get('Language') or 'und')
        rows.append({
            'id': str(stream.get('Index') if stream.get('Index') is not None else len(rows) + 1),
            'url': url,
            'key': url,
            'lang': lang,
            'language': lang,
            'languageCode': lang,
            'codec': codec or 'subrip',
            'format': _subtitle_ext(codec),
            'sourceType': 'stream',
            'sourceName': server.get('name') or ('Silo' if _backend(server) == 'silo' else 'Emby'),
            'title': str(stream.get('DisplayTitle') or stream.get('Title') or ''),
            'forced': bool(stream.get('IsForced')),
            'selected': bool(stream.get('IsDefault')),
        })
    return rows


def _media_versions(item, server):
    """Every MediaSource as a version: resolution, HDR, codecs, audio, SIZE."""
    versions = []
    for source in (item.get('MediaSources') or []):
        video = audio = None
        for stream in (source.get('MediaStreams') or []):
            kind = str(stream.get('Type') or '')
            if kind == 'Video' and video is None:
                video = stream
            elif kind == 'Audio' and audio is None:
                audio = stream
        video = video or {}
        audio = audio or {}
        res = _resolution_label(video.get('Height'), video.get('Width'))
        vrange = str(video.get('VideoRange') or '')
        title = str(video.get('DisplayTitle') or '')
        hdr = ''
        if 'DOVI' in title.upper() or 'DOLBY VISION' in title.upper() or 'DOVI' in vrange.upper():
            hdr = 'Dolby Vision'
        elif 'HDR' in vrange.upper() or 'HDR' in title.upper():
            hdr = 'HDR'
        vcodec = str(video.get('Codec') or '').upper()
        acodec = str(audio.get('Codec') or '').upper()
        atitle = str(audio.get('DisplayTitle') or '')
        if acodec:
            channels = audio.get('Channels')
            try:
                if channels:
                    acodec += ' %s' % _CH_MAP.get(int(channels), '%sch' % int(channels))
            except (TypeError, ValueError):
                pass
            if 'Atmos' in atitle:
                acodec += ' Atmos'
        size_bytes = source.get('Size') or 0
        size = _size_label(size_bytes)
        container = str(source.get('Container') or '').upper()
        blocks = ['[ %s ]' % v for v in (res, hdr, vcodec, acodec, size) if v]
        short = [v for v in (res, hdr, size, container) if v]
        versions.append({
            'media_source_id': str(source.get('Id') or ''),
            'file': str(source.get('Path') or ''),
            'resolution': res,
            'hdr': hdr,
            'video_codec': vcodec,
            'audio': acodec,
            'container': container,
            'size_bytes': size_bytes,
            'size_label': size,
            'info_line': ' • '.join(blocks),
            'version_label': ' · '.join(short) or 'Version %d' % (len(versions) + 1),
            'subtitles': _external_subtitles(item.get('Id') or '', source, server),
        })
    return versions


def _ids(item):
    provider = item.get('ProviderIds') or {}
    lower = {str(k).lower(): str(v) for k, v in provider.items() if v}
    return {
        'imdb_id': lower.get('imdb', ''),
        'tmdb_id': lower.get('tmdb', ''),
        'tvdb_id': lower.get('tvdb', ''),
    }


def item_from_node(item, server):
    """Plex-shaped dict so the whole Dex Hub pipeline is reused verbatim."""
    from .server_art import artwork
    kind = str(item.get('Type') or 'Movie')
    media = {'Movie': 'movie', 'Series': 'show', 'Season': 'season',
             'Episode': 'episode'}.get(kind, 'movie')
    versions = _media_versions(item, server)
    ticks = item.get('RunTimeTicks') or 0
    try:
        duration_ms = int(ticks) // 10000
    except (TypeError, ValueError):
        duration_ms = 0
    out = {
        'backend': _backend(server),
        'artwork': artwork(item, dict(server, url=_base(server)), _compat_prefix(server)),
        'server_id': str(server.get('id') or ''),
        'server_name': server.get('name') or ('Silo' if _backend(server) == 'silo' else 'Emby'),
        'rating_key': str(item.get('Id') or ''),
        'key': str(item.get('Id') or ''),
        'media_type': media,
        'title': str(item.get('Name') or ('Silo' if _backend(server) == 'silo' else 'Emby')),
        'raw_title': str(item.get('Name') or ''),
        'library_name': str(item.get('CollectionName') or ''),
        'view_offset_ms': int((item.get('UserData') or {}).get('PlaybackPositionTicks') or 0) // 10000,
        'last_played_date': str((item.get('UserData') or {}).get('LastPlayedDate') or ''),
        'show_title': str(item.get('SeriesName') or ''),
        # SeriesId is present on Episode nodes and lets playback recover the
        # parent show's TMDb/IMDb ids for TheIntroDB.
        'series_id': str(item.get('SeriesId') or ''),
        'summary': str(item.get('Overview') or ''),
        'year': item.get('ProductionYear') or 0,
        'duration_ms': duration_ms,
        'index': item.get('IndexNumber') or 0,
        'season': item.get('ParentIndexNumber') or 0,
        'thumb': str(item.get('Id') or ''),
        'art': str(item.get('Id') or ''),
        'rating': float(item.get('CommunityRating') or 0) or 0,
        'content_rating': str(item.get('OfficialRating') or ''),
        'studio': ', '.join(s.get('Name') for s in (item.get('Studios') or []) if s.get('Name'))[:60],
        'tagline': '',
        'premiered': str(item.get('PremiereDate') or '')[:10],
        'genres': [g for g in (item.get('Genres') or [])][:6],
        'directors': [p.get('Name') for p in (item.get('People') or [])
                      if p.get('Type') == 'Director' and p.get('Name')][:4],
        'writers': [p.get('Name') for p in (item.get('People') or [])
                    if p.get('Type') == 'Writer' and p.get('Name')][:4],
        'cast': [p.get('Name') for p in (item.get('People') or [])
                 if p.get('Type') == 'Actor' and p.get('Name')][:10],
        'versions': versions,
        'ids': _ids(item),
        'info_line': versions[0]['info_line'] if versions else '',
        'size_label': versions[0]['size_label'] if versions else '',
    }
    return out


def logo_url(server, item_id):
    """Emby exposes the clear logo as the `Logo` image type."""
    return artwork_url(server, item_id, kind='Logo') if item_id else ''


def artwork_url(server, item_id, kind='Primary'):
    if not item_id:
        return ''
    return '%s%s/Items/%s/Images/%s?%s' % (
        _base(server), _compat_prefix(server), quote(str(item_id), safe=''), quote(str(kind), safe=''),
        urlencode({'maxWidth': 800, 'quality': 90,
                   'api_key': server.get('token') or ''}))


def playback_url(server, item_id, media_source_id=''):
    params = {
        'static': 'true',
        'api_key': server.get('token') or '',
    }
    if media_source_id:
        params['MediaSourceId'] = media_source_id
    return '%s%s/Videos/%s/stream?%s' % (
        _base(server), _compat_prefix(server), item_id, urlencode(params))


def playback_info(server, item_id):
    """Return a fresh PlaybackInfo response for the current user/device.

    MediaSources cached in library metadata can be stale and do not carry a
    PlaySessionId. Emby/Jellyfin expect clients to refresh PlaybackInfo at the
    actual handoff, especially for remote, STRM and live-stream sources.
    """
    params = {
        'UserId': server.get('user_id') or '',
        'StartTimeTicks': 0,
        'AutoOpenLiveStream': 'false',
    }
    body = {
        'UserId': server.get('user_id') or '',
        'DeviceId': _device_id(_backend(server)),
    }
    return _api(server, '/Items/%s/PlaybackInfo' % quote(str(item_id), safe=''),
                params=params, method='POST', data=body)


def resolve_playback(server, item_id, media_source_id=''):
    """Resolve one source from fresh PlaybackInfo into a playable URL.

    DirectStreamUrl/TranscodingUrl returned by the server wins. The stable
    static stream endpoint remains a fallback for older Emby servers.
    """
    info = playback_info(server, item_id)
    sources = list(info.get('MediaSources') or [])
    wanted = str(media_source_id or '')
    source = next((s for s in sources if str(s.get('Id') or '') == wanted), None)
    if source is None and sources:
        source = sources[0]
    source = source or {}
    source_id = str(source.get('Id') or wanted)
    play_session_id = str(info.get('PlaySessionId') or '')
    candidate = (source.get('DirectStreamUrl') or source.get('DirectUrl') or
                 source.get('TranscodingUrl') or '')
    if candidate:
        if not str(candidate).startswith(('http://', 'https://')):
            candidate = _base(server).rstrip('/') + '/' + str(candidate).lstrip('/')
        url = _with_emby_token(str(candidate), str(server.get('token') or ''))
    else:
        params = {
            'static': 'true',
            'api_key': server.get('token') or '',
        }
        if source_id:
            params['MediaSourceId'] = source_id
        if play_session_id:
            params['PlaySessionId'] = play_session_id
        container = str(source.get('Container') or '').strip().lower()
        suffix = ('.' + container) if container and container.isalnum() else ''
        url = '%s%s/Videos/%s/stream%s?%s' % (
            _base(server), _compat_prefix(server), quote(str(item_id), safe=''), suffix, urlencode(params))
    version = (_media_versions({'Id': item_id, 'MediaSources': [source]}, server) or [{}])[0]
    return {
        'url': url,
        'media_source_id': source_id,
        'play_session_id': play_session_id,
        'version': version,
    }


# ─────────────────────── browsing & search ───────────────────────

def libraries(server, force=False):
    from .server_art import artwork
    key = hashlib.sha256(('%s|%s|%s' % (_backend(server), _base(server), server.get('user_id') or server.get('id') or '')).encode()).hexdigest()[:24]
    cached = _VIEWS_MEM.get(key) or {}
    if not force and cached.get('rows') is not None and cached.get('at', 0) + CACHE_SECONDS > time.time():
        return list(cached['rows'])
    if not force and key:
        try:
            raw = xbmcgui.Window(WINDOW_ID).getProperty('dexhub.emby.libs.%s' % key) or ''
            if raw:
                data = json.loads(raw)
                if data.get('at', 0) + CACHE_SECONDS > time.time():
                    _VIEWS_MEM[key] = data
                    return list(data.get('rows') or [])
        except Exception:
            pass
    data = _api(server, '/Users/%s/Views' % server.get('user_id'))
    rows = []
    for view in (data.get('Items') or []):
        kind = str(view.get('CollectionType') or '').lower()
        if kind not in ('movies', 'tvshows', 'boxsets', ''):
            continue
        rows.append({
            'key': str(view.get('Id') or ''),
            'title': str(view.get('Name') or 'Library'),
            'artwork': artwork(view, dict(server, url=_base(server)), _compat_prefix(server)),
            'type': 'show' if kind == 'tvshows' else 'movie',
        })
    payload = {'at': time.time(), 'rows': rows}
    _VIEWS_MEM[key] = payload
    if key:
        try:
            xbmcgui.Window(WINDOW_ID).setProperty('dexhub.emby.libs.%s' % key,
                                                  json.dumps(payload))
        except Exception:
            pass
    return list(rows)


def children(server, parent_id, start=0, size=PAGE_SIZE, sort='', include_types='', recursive=False,
             filters=None):
    """Browse an Emby/Jellyfin container.

    For top-level movie/TV libraries Dex Hub asks for recursive typed rows so
    virtual folders and filesystem folders are flattened instead of being
    rendered as fake movies. Season/episode navigation keeps the normal direct
    child behaviour. ``filters`` (v5.10.106) are the server's own item filters
    (GenreIds, IsPlayed, Years, MinCommunityRating) of the Home's library page.
    """
    params = {
        'ParentId': parent_id,
        'StartIndex': int(start or 0),
        'Limit': int(size or PAGE_SIZE),
        'Fields': BROWSE_FIELDS,
        'SortBy': sort or 'SortName',
        'SortOrder': 'Descending' if str(sort or '').endswith(':desc') else 'Ascending',
    }
    if str(sort or '').endswith(':desc'):
        params['SortBy'] = str(sort).split(':', 1)[0]
    if include_types:
        params['IncludeItemTypes'] = str(include_types)
    if recursive:
        params['Recursive'] = 'true'
    for name, value in (filters or {}).items():
        if name and value not in (None, '') and name not in params:
            params[name] = value
    data = _api(server, '/Users/%s/Items' % server.get('user_id'), params)
    rows = []
    allowed = {x.strip().lower() for x in str(include_types or '').split(',') if x.strip()}
    for node in (data.get('Items') or []):
        node_type = str(node.get('Type') or '').lower()
        if node_type in ('folder', 'collectionfolder', 'userrootfolder'):
            continue
        if allowed and node_type not in allowed:
            continue
        rows.append(item_from_node(node, server))
    return rows, int(data.get('TotalRecordCount') or len(rows))


_GENRES_MEMO = {}


def genres(server, parent_id, include_types=''):
    """[(genre id, name)] of one library, remembered for 10 minutes (v5.10.106)."""
    memo_key = '%s|%s|%s|%s' % (_backend(server), _base(server), parent_id, include_types)
    hit = _GENRES_MEMO.get(memo_key)
    if hit and time.time() - hit[0] < 600.0:
        return list(hit[1])
    params = {'ParentId': parent_id, 'UserId': server.get('user_id') or '',
              'Recursive': 'true', 'SortBy': 'SortName', 'SortOrder': 'Ascending'}
    if include_types:
        params['IncludeItemTypes'] = str(include_types)
    data = _api(server, '/Genres', params)
    out = []
    for node in (data.get('Items') or []) if isinstance(data, dict) else []:
        gid = str(node.get('Id') or '').strip()
        name = str(node.get('Name') or '').strip()
        if gid and name:
            out.append((gid, name))
    if len(_GENRES_MEMO) > 40:
        _GENRES_MEMO.clear()
    _GENRES_MEMO[memo_key] = (time.time(), list(out))
    return out


def resume(server, start=0, size=PAGE_SIZE):
    """Emby's Continue Watching — the direct equivalent of Plex's On Deck.

    Emby exposes partially-played items on /Users/{id}/Items/Resume. Dex Hub
    never called it, which is why Emby had no "متابعة المشاهدة" while Plex did.
    """
    params = {
        'StartIndex': int(start or 0),
        'Limit': int(size or PAGE_SIZE),
        'Fields': BROWSE_FIELDS,
        'MediaTypes': 'Video',
        'Recursive': 'true',
        'EnableTotalRecordCount': 'true',
    }
    data = _api(server, '/Users/%s/Items/Resume' % server.get('user_id'), params)
    rows = []
    for node in (data.get('Items') or []):
        row = item_from_node(node, server)
        row['resume_ms'] = _resume_ms(node)
        rows.append(row)
    return rows, int(data.get('TotalRecordCount') or len(rows))


def next_up(server, limit=PAGE_SIZE):
    """Next unwatched episode per series — completes Continue Watching.

    Resume only returns items that were *started*. A finished episode should
    surface the NEXT one, exactly as Plex's On Deck does.
    """
    params = {
        'UserId': server.get('user_id'),
        'Limit': int(limit or PAGE_SIZE),
        'Fields': BROWSE_FIELDS,
    }
    try:
        data = _api(server, '/Shows/NextUp', params)
    except Exception:
        return []
    rows = []
    for node in (data.get('Items') or []):
        row = item_from_node(node, server)
        row['resume_ms'] = _resume_ms(node)
        rows.append(row)
    return rows


def _resume_ms(node):
    """Playback position in ms from an Emby node's UserData (ticks -> ms)."""
    try:
        ticks = ((node.get('UserData') or {}).get('PlaybackPositionTicks')) or 0
        return int(ticks) // 10000
    except Exception:
        return 0


def search_server(server, query, media_type='', limit=40):
    types = {'movie': 'Movie', 'show': 'Series', 'series': 'Series',
             'episode': 'Episode'}.get((media_type or '').lower(), 'Movie,Series,Episode')
    data = _api(server, '/Users/%s/Items' % server.get('user_id'), {
        'SearchTerm': query,
        'IncludeItemTypes': types,
        'Recursive': 'true',
        'Limit': int(limit),
        'Fields': BROWSE_FIELDS,
    })
    rows = []
    for item in (data.get('Items') or []):
        row = item_from_node(item, server)
        if row['media_type'] in ('movie', 'show', 'episode'):
            rows.append(row)
    return rows


def metadata(server, item_id):
    data = _api(server, '/Users/%s/Items/%s' % (server.get('user_id'), item_id),
                {'Fields': DETAIL_FIELDS})
    return item_from_node(data, server)


def find_all_by_ids(server, ids, media_type='movie', title='', limit=10):
    """EVERY library item that matches — not just the first one.

    v3.9.209: find_by_ids() returned inside its own loop, so only the FIRST hit
    ever came back. A library that holds the same film several times — a 4K
    rip and a 1080p rip catalogued as separate items, which is exactly what
    Arabic libraries look like — surfaced ONE source in Dex Hub while other
    clients listed three.
    """
    want_type = 'Series' if str(media_type).lower() in ('series', 'show', 'episode') else 'Movie'
    found, seen = [], set()

    def _take(row):
        key = str(row.get('rating_key') or '')
        if key and key not in seen:
            seen.add(key)
            found.append(row)

    if _backend(server) == 'silo':
        # Silo's current Jellyfin compatibility router intentionally supports
        # SearchTerm but not Emby's AnyProviderIdEquals query. Querying that
        # parameter can return an unfiltered page on pre-release builds, so use
        # bounded search and verify every candidate locally by ProviderIds.
        wanted = {k: str((ids or {}).get(k) or '').strip().lower()
                  for k in ('imdb_id', 'tmdb_id', 'tvdb_id')}
        probes = [wanted.get('imdb_id'), wanted.get('tmdb_id'), title]
        used = set()
        for probe in probes:
            probe = str(probe or '').strip()
            if not probe or probe.casefold() in used:
                continue
            used.add(probe.casefold())
            try:
                rows = search_server(server, probe, media_type=media_type,
                                     limit=min(20, int(limit) * 2))
            except EmbyError:
                continue
            for row in rows:
                row_ids = {k: str((row.get('ids') or {}).get(k) or '').strip().lower()
                           for k in wanted}
                same_id = any(wanted[k] and row_ids.get(k) == wanted[k] for k in wanted)
                same_title = (title and str(row.get('title') or '').strip().casefold()
                              == str(title).strip().casefold())
                if same_id or same_title:
                    _take(row)
            if found:
                break
        return _resolve_details(server, found, limit)

    for provider, key in (('Imdb', 'imdb_id'), ('Tmdb', 'tmdb_id'), ('Tvdb', 'tvdb_id')):
        value = str((ids or {}).get(key) or '')
        if not value:
            continue
        try:
            data = _api(server, '/Users/%s/Items' % server.get('user_id'), {
                'AnyProviderIdEquals': '%s.%s' % (provider.lower(), value),
                'IncludeItemTypes': want_type,
                'Recursive': 'true',
                'Limit': int(limit),
                'Fields': DETAIL_FIELDS,
            })
        except EmbyError:
            continue
        for item in (data.get('Items') or []):
            _take(item_from_node(item, server))

    if found:
        # v3.9.226 — the same waste that cost Plex 43 seconds per server.
        #
        # AnyProviderIdEquals is EXACT: Emby resolved the id and handed back the
        # item. Yet the code carried on into a title search because the only
        # gate was `len(found) < limit` — and a film has two or three copies,
        # never ten. Every extra pass is a full recursive library scan.
        xbmc.log('[DexHub] Emby exact id match — skipping the title pass '
                 '(server=%s, %d copies)' % (server.get('name') or '?', len(found)),
                 xbmc.LOGINFO)
        return _resolve_details(server, found, limit)

    # A title pass catches library copies whose provider ids were never set —
    # the unidentified Arabic/Turkish releases the user actually cares about.
    if title and len(found) < int(limit):
        want_ids = {k: str((ids or {}).get(k) or '') for k in ('imdb_id', 'tmdb_id')}
        for row in search_server(server, title, media_type=media_type, limit=int(limit)):
            row_ids = row.get('ids') or {}
            same_id = any(want_ids[k] and row_ids.get(k) == want_ids[k] for k in want_ids)
            same_title = (str(row.get('title') or '').strip().lower()
                          == str(title).strip().lower())
            if same_id or same_title:
                _take(row)

    return _resolve_details(server, found, limit)


def _resolve_details(server, found, limit):
    """Fill in the MediaSources — in parallel, and only when they are missing."""
    rows = found[:int(limit)]
    need = [r for r in rows if not (r.get('versions') or []) and r.get('rating_key')]
    detailed = {}
    if need:
        with ThreadPoolExecutor(max_workers=min(6, len(need))) as pool:
            futures = {pool.submit(metadata, server, r.get('rating_key')): r for r in need}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    detailed[str(row.get('rating_key'))] = future.result()
                except Exception:
                    pass
    return [detailed.get(str(r.get('rating_key')), r) for r in rows]


def find_by_ids(server, ids, media_type='movie', title=''):
    """GUID-first match (imdb/tmdb), title fallback — like the Plex path."""
    want_type = 'Series' if str(media_type).lower() in ('series', 'show', 'episode') else 'Movie'
    for provider, key in (('Imdb', 'imdb_id'), ('Tmdb', 'tmdb_id'), ('Tvdb', 'tvdb_id')):
        value = str((ids or {}).get(key) or '')
        if not value:
            continue
        try:
            data = _api(server, '/Users/%s/Items' % server.get('user_id'), {
                'AnyProviderIdEquals': '%s.%s' % (provider.lower(), value),
                'IncludeItemTypes': want_type,
                'Recursive': 'true',
                'Limit': 5,
                'Fields': BROWSE_FIELDS,
            })
        except EmbyError:
            continue
        for item in (data.get('Items') or []):
            item_id = str(item.get('Id') or '')
            if item_id:
                try:
                    return metadata(server, item_id)
                except EmbyError:
                    return item_from_node(item, server)
    if title:
        normalized_title = str(title or '').strip().casefold()
        first_title = None
        for row in search_server(server, title, media_type=media_type, limit=10):
            row_ids = row.get('ids') or {}
            for key in ('imdb_id', 'tmdb_id'):
                if (ids or {}).get(key) and row_ids.get(key) == str((ids or {}).get(key)):
                    try:
                        return metadata(server, row.get('rating_key'))
                    except EmbyError:
                        return row
            row_title = str(row.get('raw_title') or row.get('title') or '').strip().casefold()
            if row_title == normalized_title and first_title is None:
                first_title = row
        if first_title is not None:
            try:
                return metadata(server, first_title.get('rating_key'))
            except EmbyError:
                return first_title
    return None


def episode_item(server, show_item, season, episode):
    """Walk Series → Episodes and return the exact episode with MediaSources."""
    try:
        data = _api(server, '/Shows/%s/Episodes' % show_item.get('rating_key'), {
            'UserId': server.get('user_id'),
            'Season': int(season),
            'Fields': DETAIL_FIELDS,
        })
    except (EmbyError, TypeError, ValueError):
        return None
    for item in (data.get('Items') or []):
        try:
            if int(item.get('IndexNumber') or -1) == int(episode):
                return item_from_node(item, server)
        except (TypeError, ValueError):
            continue
    return None
