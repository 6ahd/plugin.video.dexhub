# -*- coding: utf-8 -*-
"""Native Silo auth/profile and source provider for Dex Hub."""
from __future__ import absolute_import

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from .. import emby_client as _shared


class SiloError(Exception):
    pass


_refresh_lock = threading.Lock()
RENEWED_RECENTLY = 60     # seconds a token renewed by another reader is trusted
_state_lock = threading.RLock()


def _base(url):
    value = str(url or '').strip().rstrip('/')
    if value and not value.startswith(('http://', 'https://')):
        value = 'http://' + value
    return value


def account():
    return _shared.account('silo')


def _save(value):
    with _state_lock:
        _shared._save_json_setting('silo_auth_json', value or {})


def is_signed_in():
    auth = account()
    return bool(auth.get('url') and auth.get('token') and auth.get('profile_id'))


def sign_out():
    with _state_lock:
        return _shared.sign_out('silo')


def _headers(auth=None, profile=True):
    auth = auth or account()
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    token = str(auth.get('token') or auth.get('access_token') or '')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if profile:
        if auth.get('profile_id'):
            headers['X-Profile-Id'] = str(auth.get('profile_id'))
        if auth.get('profile_token'):
            headers['X-Profile-Token'] = str(auth.get('profile_token'))
    headers['X-Device-Id'] = _shared._device_id('silo')
    headers['X-Device-Name'] = 'Dex Hub on Kodi'
    headers['X-Device-Platform'] = 'kodi'
    return headers


def _decode(raw):
    try:
        return json.loads((raw or b'{}').decode('utf-8'))
    except Exception as exc:
        raise SiloError('رد Silo غير مقروء (%s)' % exc)


def _pooled_get(url, auth=None, profile=True):
    """Return (available, status, body) using the shared keep-alive pool."""
    try:
        from ..dexhub import client as _http
        pool = _http._ensure_pool()
        if pool is None:
            return False, 0, b''
        urllib3 = _http._URLLIB3
    except Exception:
        return False, 0, b''

    timeout = float(_shared._timeout())
    last_exc = None
    for attempt in (0, 1):
        try:
            resp = pool.request(
                'GET', url, headers=_headers(auth, profile),
                timeout=urllib3.Timeout(connect=min(5, timeout), read=timeout),
                redirect=True, retries=False, decode_content=True,
                preload_content=True,
            )
            status = int(getattr(resp, 'status', 0) or 0)
            if 500 <= status < 600 and attempt == 0:
                time.sleep(0.15)
                continue
            return True, status, bytes(getattr(resp, 'data', b'') or b'')
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(0.15)
                continue
            raise SiloError('%s' % exc)
    if last_exc is not None:
        raise SiloError('%s' % last_exc)
    return True, 0, b''


def _verify_key(url, auth):
    """v5.10.241: a Silo server asking its profile to be verified answers so
    to every read until the user does it: remembered for half an hour (the
    log: 42 refusals a night), per server, profile and session."""
    import hashlib
    try:
        from urllib.parse import urlsplit
        host = urlsplit(url).netloc
    except Exception:
        host = ''
    seed = '%s|%s|%s' % (host, (auth or {}).get('profile_id') or '', str((auth or {}).get('access_token') or '')[:24])
    return 'dexhub.silo.verify.' + hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]


def _verify_held(key):
    try:
        import xbmcgui
        raw = xbmcgui.Window(10000).getProperty(key)
        if raw:
            stamp, _sep, message = raw.partition('|')
            if float(stamp) > time.time():
                return message or 'Silo: Profile verification required'
    except Exception:
        pass
    return ''


def _verify_remember(key, message):
    if 'verif' not in str(message or '').lower():
        return
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(key, '%.0f|%s' % (time.time() + 1800, str(message)[:200]))
    except Exception:
        pass


def _raw(url, method='GET', data=None, auth=None, profile=True, retry=True):
    auth = dict(auth or account())
    body = json.dumps(data).encode('utf-8') if data is not None else None
    verify_key = _verify_key(url, auth) if profile else ''
    if verify_key:
        held = _verify_held(verify_key)
        if held:
            raise SiloError(held)

    # Library/detail/search/source reads dominate Silo traffic. Reuse the same
    # keep-alive pool as Stremio/Emby instead of paying a fresh TCP/TLS
    # handshake per row. Mutating/auth POSTs deliberately stay on urllib.
    if str(method or 'GET').upper() == 'GET' and body is None:
        available, status, payload_raw = _pooled_get(url, auth=auth, profile=profile)
        if available:
            if (status == 401 and retry and (auth is None or auth.get('profile_id'))
                    and account().get('refresh_token')):
                renewed = refresh(auth)
                return _raw(url, method, data, renewed, profile, retry=False)
            if status >= 400:
                try:
                    payload = _decode(payload_raw)
                    message = payload.get('message') or payload.get('error')
                except Exception:
                    message = ''
                if verify_key and status in (401, 403):
                    _verify_remember(verify_key, message)
                raise SiloError(message or 'Silo HTTP %s' % status)
            return payload_raw

    req = Request(url, data=body, headers=_headers(auth, profile), method=method)
    try:
        with urlopen(req, timeout=_shared._timeout()) as response:
            return response.read()
    except HTTPError as exc:
        # Normal media calls carry a small server-scoped copy of the selected
        # account, so ``auth`` is not literally None. It is still safe to
        # refresh when that copy has a selected profile; pending login/QR
        # sessions (no profile yet) are deliberately never replaced.
        if (exc.code == 401 and retry and (auth is None or auth.get('profile_id'))
                and account().get('refresh_token')):
            renewed = refresh(auth)
            return _raw(url, method, data, renewed, profile, retry=False)
        try:
            payload = _decode(exc.read())
            message = payload.get('message') or payload.get('error')
        except Exception:
            message = ''
        if verify_key and exc.code in (401, 403):
            _verify_remember(verify_key, message)
        raise SiloError(message or 'Silo HTTP %s' % exc.code)
    except SiloError:
        raise
    except Exception as exc:
        raise SiloError('%s' % exc)


def _api(path, method='GET', data=None, params=None, auth=None, profile=True, retry=True):
    auth = dict(auth or account())
    root = _base(auth.get('url'))
    if not root:
        raise SiloError('أدخل عنوان سيرفر Silo')
    url = root + '/api/v1/' + str(path or '').lstrip('/')
    pairs = [(str(k), str(v)) for k, v in (params or {}).items() if v not in (None, '')]
    if pairs:
        url += '?' + urlencode(pairs)
    return _decode(_raw(url, method=method, data=data, auth=auth, profile=profile, retry=retry))


def _account_tokens(seed, value):
    user = value.get('user') or {}
    access = str(value.get('access_token') or '')
    if not access:
        raise SiloError('لم يرجع Silo جلسة صالحة')
    out = dict(seed or {})
    out.update({
        'url': _base(out.get('url')), 'backend': 'silo',
        'token': access, 'access_token': access,
        'refresh_token': str(value.get('refresh_token') or ''),
        'expires_in': int(value.get('expires_in') or 0),
        'token_received_at': int(time.time()),
        'account_id': str(user.get('id') or ''),
        'account_name': str(user.get('username') or ''),
        'server_name': str(out.get('server_name') or 'Silo'),
    })
    return out


def login_account(url, username, password=''):
    root = _base(url)
    if not root:
        raise SiloError('أدخل عنوان سيرفر Silo')
    seed = {'url': root, 'backend': 'silo'}
    value = _api('auth/login', method='POST', data={
        'username': str(username or '').strip(), 'password': str(password or '')
    }, auth=seed, profile=False)
    return _account_tokens(seed, value)


def sign_in(url, username, password=''):
    """Legacy helper; interactive UI uses login_account + profile selection."""
    pending = login_account(url, username, password)
    profiles = list_profiles(pending)
    unlocked = next((p for p in profiles if not p.get('has_pin')), None)
    if not unlocked:
        raise SiloError('اختر بروفايل Silo وأدخل PIN من صفحة الربط')
    return select_profile(pending, unlocked)


def _same_account(first, second):
    if not _base(first.get('url')) or _base(first.get('url')) != _base(second.get('url')):
        return False
    left, right = first.get('account_id'), second.get('account_id')
    return not (left and right and str(left) != str(right))


def _renewed_auth(requested, current):
    # Account credentials rotate together; a request keeps the profile/PIN
    # scope in which it began, rather than inheriting a different profile.
    value = dict(requested)
    for key in ('token', 'access_token', 'refresh_token', 'expires_in', 'token_received_at'):
        if key in current:
            value[key] = current[key]
    return value


def refresh(auth=None):
    with _refresh_lock:
        current = dict(account())
        requested = dict(auth or current)
        if not _same_account(requested, current):
            raise SiloError('تغير حساب Silo؛ أعد تحميل المصدر')
        old_token = str(requested.get('token') or requested.get('access_token') or '')
        current_token = str(current.get('token') or current.get('access_token') or '')
        try:
            renewed_age = time.time() - float(current.get('token_received_at') or 0)
        except (TypeError, ValueError):
            renewed_age = RENEWED_RECENTLY + 1
        # v5.10.202: another reader renewed it a moment ago, so its token is
        # used as it is. A saved token renewed earlier may have expired too:
        # a request that began with an older copy renews it again, or every
        # such request would fail until the servers were read again.
        if (auth is not None and old_token and current_token and old_token != current_token
                and 0 <= renewed_age < RENEWED_RECENTLY):
            return _renewed_auth(requested, current)
        refresh_token = str(current.get('refresh_token') or '')
        if not refresh_token:
            raise SiloError('انتهت جلسة Silo؛ أعد الربط')
        # A refused refresh must surface once, never recursively refresh
        # itself through the ordinary 401 handler.
        value = _api('auth/refresh', method='POST', data={'refresh_token': refresh_token},
                     auth=current, profile=False, retry=False)
        updates = {'token': str(value.get('access_token') or ''),
                   'access_token': str(value.get('access_token') or ''),
                   'refresh_token': str(value.get('refresh_token') or refresh_token),
                   'expires_in': int(value.get('expires_in') or 0),
                   'token_received_at': int(time.time())}
        if not updates['token']:
            raise SiloError('تعذر تجديد جلسة Silo')
        with _state_lock:
            latest = dict(account())
            if not _same_account(current, latest):
                raise SiloError('تغير حساب Silo؛ أعد تحميل المصدر')
            # Preserve a profile selected (or a new login) while the server
            # answered; network waits never hold the short state-write lock.
            latest_token = str(latest.get('token') or latest.get('access_token') or '')
            if latest_token and latest_token != current_token:
                return _renewed_auth(requested, latest)
            latest.update(updates)
            _save(latest)
            return _renewed_auth(requested, latest)


def start_device_login(url):
    root = _base(url)
    seed = {'url': root, 'backend': 'silo'}
    value = _api('auth/device/start', method='POST', data={
        'device_name': 'Dex Hub on Kodi', 'device_platform': 'kodi'
    }, auth=seed, profile=False)
    value['_server_url'] = root
    return value


def poll_device_login(pairing):
    seed = {'url': pairing.get('_server_url') or '', 'backend': 'silo'}
    value = _api('auth/device/poll', method='POST', data={
        'device_code': str(pairing.get('device_code') or '')
    }, auth=seed, profile=False)
    status = str(value.get('status') or '').lower()
    if status == 'approved' and value.get('access_token'):
        return status, _account_tokens(seed, value)
    return status, value


def list_profiles(auth=None):
    value = _api('profiles/', auth=auth or account(), profile=False)
    return list(value.get('profiles') or [])


def verify_pin(auth, profile_id, pin):
    value = _api('profiles/%s/verify-pin' % quote(str(profile_id), safe=''),
                 method='POST', data={'pin': str(pin or '')}, auth=auth,
                 profile=False)
    token = str(value.get('profile_token') or '')
    if not value.get('valid') or not token:
        raise SiloError('PIN غير صحيح')
    return token


def select_profile(auth, profile, profile_token=''):
    value = dict(auth or {})
    value.update({
        'profile_id': str(profile.get('id') or ''),
        'profile_name': str(profile.get('name') or 'Profile'),
        'profile_token': str(profile_token or ''),
        'user_id': str(profile.get('id') or ''),
        'username': str(profile.get('name') or ''),
        'signed_in_at': int(time.time()), 'auth_mode': 'native',
    })
    if not value.get('profile_id'):
        raise SiloError('بروفايل Silo غير صالح')
    _save(value)
    return value


def servers(*_args, **_kwargs):
    auth = account()
    if not is_signed_in():
        return []
    return [{
        'id': auth.get('profile_id') or 'silo',
        'name': auth.get('server_name') or 'Silo',
        'url': auth.get('url') or '', 'token': auth.get('token') or '',
        'account_id': auth.get('account_id') or '',
        'user_id': auth.get('profile_id') or '', 'profile_id': auth.get('profile_id') or '',
        'profile_token': auth.get('profile_token') or '', 'backend': 'silo',
    }]


def _native_auth(server):
    auth = dict(account())
    auth.update({k: server.get(k) for k in ('url', 'token', 'account_id', 'profile_id', 'profile_token')
                 if server.get(k)})
    return auth


def _media_url(server, value):
    """Turn Silo's client-relative artwork URLs into Kodi-loadable URLs.

    The native catalog intentionally returns paths such as
    ``/api/v1/items/<id>/logo``. Web/Android resolve those against the server
    origin; Kodi does not, so passing the path through made posters and
    clearlogos look missing even though Silo supplied them.
    """
    value = str(value or '').strip()
    if not value or value.startswith(('http://', 'https://', 'special://')):
        return value
    return _base((server or {}).get('url')) + '/' + value.lstrip('/')


def _version(row):
    audio_tracks = list(row.get('audio_tracks') or [])
    audio = audio_tracks[0] if audio_tracks else {}
    video_tracks = list(row.get('video_tracks') or [])
    video = video_tracks[0] if video_tracks else {}
    channels = int(audio.get('channels') or row.get('audio_channels') or 0)
    resolution = str(row.get('resolution') or '')
    if not resolution and video.get('height'):
        height = int(video.get('height') or 0)
        resolution = ('4K' if height >= 2000 else
                      '1080p' if height >= 1000 else
                      '720p' if height >= 700 else ('%dp' % height if height else ''))
    # FileVersion only promises a HDR boolean. The video-track inventory is
    # richer and is what the official apps use for DV / HDR10+ badges.
    dv = str(video.get('dolby_vision') or '').strip()
    range_type = str(video.get('video_range_type') or video.get('video_range') or '').strip()
    if dv or int(video.get('dv_profile') or 0) > 0:
        hdr = 'Dolby Vision'
    elif video.get('hdr10_plus') or 'hdr10+' in range_type.lower():
        hdr = 'HDR10+'
    else:
        hdr = row.get('hdr_format') or range_type or ('HDR' if row.get('hdr') else '')
    size = int(row.get('file_size') or 0)
    video_codec = str(row.get('codec_video') or video.get('codec') or '')
    audio_codec = str(row.get('codec_audio') or audio.get('codec') or '')
    edition = str(row.get('edition_raw') or row.get('edition_key') or '').strip()
    # Detail/version responses expose the complete track inventory, including
    # external sidecars. Their playable URLs are session-scoped and are
    # attached later from playback_plan.subtitle.inventory.
    subtitle_tracks = []
    for ordinal, track in enumerate(row.get('subtitle_tracks') or []):
        subtitle_tracks.append({
            'id': 'silo-catalog-%s-%s' % (row.get('file_id') or '', ordinal),
            'lang': str(track.get('language') or ''),
            'language': str(track.get('language') or ''),
            'name': str(track.get('title') or track.get('embedded_title') or
                        track.get('file_name') or track.get('language') or 'Subtitle'),
            'codec': str(track.get('codec') or ''),
            'source': 'external' if track.get('external') else 'embedded',
            'external': bool(track.get('external')),
            'forced': bool(track.get('forced')),
            'default': bool(track.get('default')),
            'hearing_impaired': bool(track.get('hearing_impaired')),
            'url': '',
        })
    return {
        'media_source_id': str(row.get('file_id') or ''),
        'file_id': str(row.get('file_id') or ''),
        'file': row.get('file_path') or row.get('file_name') or '',
        'resolution': resolution, 'hdr': str(hdr or ''),
        'video_codec': video_codec,
        'audio_codec': audio_codec,
        'audio': ('%s %s' % (audio_codec,
                             _shared._CH_MAP.get(channels, str(channels) if channels else ''))).strip(),
        'container': str(row.get('container') or ''),
        'edition': edition,
        'bitrate': int(row.get('bitrate') or video.get('bitrate') or 0),
        'size_bytes': size, 'size_label': _shared._size_label(size),
        'duration_ms': int(float(row.get('duration') or 0) * 1000),
        'subtitles': subtitle_tracks,
        'info_line': ' • '.join(x for x in (edition, resolution, str(hdr or ''),
                                             video_codec, audio_codec,
                                             _shared._size_label(size)) if x),
    }



def _external_ids(row):
    """Normalize Silo external identity across native and compat-era shapes.

    Native metadata providers expose a ``provider_ids`` map, while some older
    catalog/detail builds flatten the same values as ``imdb_id`` / ``tmdb_id``
    / ``tvdb_id``.  The Infuse compatibility patch maps those values into
    Jellyfin ``ProviderIds``; Dex reads them directly here so an unpatched Silo
    server can resolve the same media-source identity.
    """
    row = row or {}
    pools = []
    for key in ('provider_ids', 'providerIds', 'ProviderIds', 'external_ids',
                'externalIds', 'ExternalIds'):
        value = row.get(key)
        if isinstance(value, dict):
            pools.append(value)
    metadata = row.get('metadata') or {}
    if isinstance(metadata, dict):
        for key in ('provider_ids', 'providerIds', 'ProviderIds',
                    'external_ids', 'externalIds', 'ExternalIds'):
            value = metadata.get(key)
            if isinstance(value, dict):
                pools.append(value)

    aliases = {
        'imdb_id': ('imdb_id', 'imdbId', 'imdb', 'Imdb', 'IMDb', 'IMDB'),
        'tmdb_id': ('tmdb_id', 'tmdbId', 'tmdb', 'Tmdb', 'TMDb', 'TMDB'),
        'tvdb_id': ('tvdb_id', 'tvdbId', 'tvdb', 'Tvdb', 'TVDb', 'TVDB'),
    }
    out = {}
    for target, names in aliases.items():
        value = ''
        for name in names:
            if row.get(name) not in (None, ''):
                value = row.get(name)
                break
        if value in (None, ''):
            for pool in pools:
                for name in names:
                    if pool.get(name) not in (None, ''):
                        value = pool.get(name)
                        break
                if value not in (None, ''):
                    break
        out[target] = str(value or '').strip()
    return out

def _item(row, server):
    row = row or {}
    kind = str(row.get('type') or '').lower()
    if kind in ('series', 'show'):
        media_type = 'show'
    elif kind == 'season':
        media_type = 'season'
    elif kind == 'episode':
        media_type = 'episode'
    else:
        media_type = 'movie'
    versions = [_version(v) for v in (row.get('versions') or row.get('files') or [])]
    user = row.get('user_data') or row.get('user_state') or {}
    overlay = row.get('overlay_summary') or {}
    crew = list(row.get('crew') or [])
    # ``runtime`` is minutes on every catalog/detail surface. Home section
    # progress rows additionally expose duration_seconds, which must win.
    if row.get('duration_seconds') not in (None, ''):
        duration_seconds = float(row.get('duration_seconds') or 0)
    elif user.get('duration_seconds') not in (None, ''):
        duration_seconds = float(user.get('duration_seconds') or 0)
    else:
        duration_seconds = float(row.get('runtime') or 0) * 60.0
    resume_seconds = float(row.get('position_seconds') or
                           user.get('position_seconds') or 0)
    resolution = str(overlay.get('resolution') or '')
    hdr = str(overlay.get('hdr') or '')
    video_codec = str(overlay.get('video_codec') or '')
    audio = ' '.join(str(x).strip() for x in
                     (overlay.get('audio'), overlay.get('audio_channels')) if x).strip()
    technical = ' • '.join(str(x).strip() for x in
                           (resolution, hdr, video_codec, audio,
                            overlay.get('container'), overlay.get('edition')) if x)
    return {
        'rating_key': str(row.get('content_id') or row.get('id') or ''), 'key': str(row.get('content_id') or row.get('id') or ''),
        'play_content_id': str(row.get('play_content_id') or ''),
        'title': str(row.get('title') or ''), 'raw_title': str(row.get('title') or ''),
        'media_type': media_type, 'year': int(row.get('year') or 0),
        'summary': str(row.get('overview') or ''),
        'duration_ms': int(duration_seconds * 1000),
        'resume_ms': int(resume_seconds * 1000),
        'thumb': _media_url(server, row.get('poster_url') or row.get('still_url') or ''),
        'art': _media_url(server, row.get('backdrop_url') or row.get('poster_url') or ''),
        'logo': _media_url(server, row.get('logo_url') or ''),
        'rating': float(row.get('rating_imdb') or row.get('rating_tmdb') or 0),
        'content_rating': str(row.get('content_rating') or ''),
        'studio': ', '.join((row.get('studios') or row.get('networks') or []))[:80],
        'tagline': str(row.get('tagline') or ''),
        'premiered': str(row.get('release_date') or row.get('air_date') or '')[:10],
        'genres': list(row.get('genres') or [])[:8],
        'directors': [p.get('name') for p in crew if str(p.get('job') or '').lower() == 'director'][:4],
        'writers': [p.get('name') for p in crew if str(p.get('job') or '').lower() in ('writer', 'screenplay')][:4],
        'cast': [p.get('name') for p in (row.get('cast') or []) if p.get('name')][:10],
        'versions': versions,
        'ids': _external_ids(row),
        'resolution': resolution or (versions[0].get('resolution') if versions else ''),
        'hdr': hdr or (versions[0].get('hdr') if versions else ''),
        'video_codec': video_codec or (versions[0].get('video_codec') if versions else ''),
        'audio': audio or (versions[0].get('audio') if versions else ''),
        'info_line': technical or (versions[0].get('info_line') if versions else ''),
        'size_label': versions[0].get('size_label') if versions else '',
        'series_id': str(row.get('series_id') or ''),
        'show_title': str(row.get('series_title') or ''),
        'season': int(row.get('season_number') or 0),
        'index': int(row.get('episode_number') or 0),
        'server_url': server.get('url') or '', 'token': server.get('token') or '',
    }


def _server(server=None):
    """Return the selected native server without making a second login call."""
    if server:
        return server
    rows = servers()
    if not rows:
        raise SiloError('لم يتم ربط Silo بعد')
    return rows[0]


def user_libraries(server=None):
    server = _server(server)
    value = _api('user/libraries', auth=_native_auth(server))
    rows = value if isinstance(value, list) else (value.get('libraries') or [])
    return sorted(list(rows), key=lambda x: (int(x.get('sort_order') or 0), str(x.get('name') or '')))


def _section(row, server):
    row = row or {}
    items = [_item(x, server) for x in (row.get('items') or [])]
    return {
        'id': str(row.get('id') or ''),
        'title': str(row.get('title') or 'Section'),
        'section_type': str(row.get('section_type') or row.get('type') or ''),
        'featured': bool(row.get('featured')),
        'total_count': int(row.get('total_count') or len(items)),
        'items': items,
    }


def home_sections(server=None):
    server = _server(server)
    value = _api('home/sections', auth=_native_auth(server))
    raw = value if isinstance(value, list) else (value.get('sections') or [])
    return [_section(x, server) for x in raw]


def home_section_items(server, section_id):
    server = _server(server)
    value = _api('home/sections/%s/items' % quote(str(section_id), safe=''),
                 auth=_native_auth(server))
    rows = value.get('items') or ((value.get('section') or {}).get('items')) or []
    return [_item(x, server) for x in rows]


def library_sections(server, library_id):
    server = _server(server)
    value = _api('library/%s/sections' % quote(str(library_id), safe=''),
                 auth=_native_auth(server))
    raw = value if isinstance(value, list) else (value.get('sections') or [])
    return [_section(x, server) for x in raw]


def library_section_items(server, library_id, section_id):
    server = _server(server)
    value = _api('library/%s/sections/%s/items' % (
        quote(str(library_id), safe=''), quote(str(section_id), safe='')),
        auth=_native_auth(server))
    rows = value.get('items') or ((value.get('section') or {}).get('items')) or []
    return [_item(x, server) for x in rows]


def library_collections(server, library_id):
    """Flatten Silo's grouped collection response while preserving display order."""
    server = _server(server)
    value = _api('library/%s/collections' % quote(str(library_id), safe=''),
                 auth=_native_auth(server))
    if isinstance(value, list):
        raw = value
    else:
        ordered, buckets = [], []
        for index, group in enumerate(value.get('groups') or []):
            group_rows = []
            for row in group.get('collections') or []:
                copy = dict(row)
                copy['_group_name'] = str(group.get('name') or '')
                copy['_group_kind'] = str(group.get('kind') or '')
                group_rows.append(copy)
            if group_rows:
                buckets.append((int(group.get('sort_order') or 0), index, group_rows))
        ungrouped = value.get('ungrouped') or {}
        if ungrouped.get('collections'):
            buckets.append((int(ungrouped.get('sort_order') or 2147483647),
                            len(buckets), list(ungrouped.get('collections') or [])))
        for _order, _index, bucket in sorted(buckets, key=lambda x: (x[0], x[1])):
            ordered.extend(bucket)
        raw = ordered or (value.get('collections') or [])
    rows = []
    for row in raw:
        rows.append({
            'id': str(row.get('id') or ''),
            'title': str(row.get('title') or row.get('name') or 'Collection'),
            'item_count': int(row.get('item_count') or 0),
            'poster_url': _media_url(server, row.get('poster_url') or ''),
            'group_name': str(row.get('_group_name') or ''),
            'kind': str(row.get('kind') or row.get('_group_kind') or 'regular'),
        })
    return rows


def server_collections(server=None):
    """v5.10.198: the server's collections of every library (Silo's own
    /collections/server: featured first, twenty per library), one list."""
    server = _server(server)
    value = _api('collections/server', auth=_native_auth(server))
    rows = []
    for library in (value.get('libraries') or []) if isinstance(value, dict) else []:
        for row in library.get('collections') or []:
            rows.append({
                'id': str(row.get('id') or ''),
                'title': str(row.get('title') or 'Collection'),
                'item_count': int(row.get('item_count') or 0),
                'poster_url': _media_url(server, row.get('poster_url') or ''),
                'library_id': str(library.get('library_id') or ''),
                'library_name': str(library.get('library_name') or ''),
                'featured': bool(row.get('featured')),
            })
    return [r for r in rows if r['id']]


def personal_collections(server=None):
    """Profile collections in the same group/order contract as Silo apps."""
    server = _server(server)
    value = _api('collections', auth=_native_auth(server))
    if isinstance(value, list):
        raw, groups = value, {}
    else:
        raw = value.get('collections') or []
        groups = {str(row.get('id') or ''): row for row in (value.get('groups') or [])}
    rows = []
    for row in raw:
        group = groups.get(str(row.get('group_id') or '')) or {}
        rows.append({
            'id': str(row.get('id') or ''),
            'title': str(row.get('name') or 'Collection'),
            'description': str(row.get('description') or ''),
            'item_count': int(row.get('item_count') or 0),
            'poster_url': _media_url(server, row.get('poster_url') or ''),
            'group_name': str(group.get('name') or ''),
            'group_order': int(group.get('sort_order') or 2147483647),
            'sort_order': int(row.get('sort_order') or 0),
        })
    return sorted(rows, key=lambda x: (x['group_order'], x['sort_order'], x['title'].casefold()))


def catalog_filters(genre='', unwatched='', decade='', rating=''):
    """Native catalog overlays, as parsed by Silo's catalog_parser.go."""
    params = {}
    if str(genre or '').strip():
        params['genre'] = str(genre).strip()
    if str(decade or '').isdigit() and len(str(decade)) == 4:
        params['year_min'] = int(decade)
        params['year_max'] = int(decade) + 9
    rules = []
    if str(unwatched or '') in ('1', 'true'):
        rules.append(('watched', 'is', 'false'))
    try:
        if 0 < float(rating or 0) <= 10:
            rules.append(('rating_imdb', 'gte', str(float(rating))))
    except (ValueError, TypeError):
        pass
    if rules:
        params['match'] = params['groups[0][match]'] = 'all'
        for i, (field, op, value) in enumerate(rules):
            for key, val in (('field', field), ('op', op), ('value', value)):
                params['groups[0][rules][%d][%s]' % (i, key)] = val
    return params


def genres(server=None, library_id=''):
    params = {'library_id': str(library_id)} if library_id else {}
    data = _api('catalog/filters', params=params, auth=_native_auth(_server(server)))
    rows = data.get('genres') or [] if isinstance(data, dict) else []
    return [(str(g), str(g)) for g in rows if isinstance(g, str) and g.strip()]


def catalog_page(server=None, library_id='', media_type='', offset=0, limit=60,
                 query='', source='', collection_id='', section_id='', scope='',
                 enrich_details=False, sort='', genre='', unwatched='', decade='', rating=''):
    server = _server(server)
    native_type = {'show': 'series', 'tvshow': 'series'}.get(
        str(media_type or '').lower(), str(media_type or '').lower())
    params = {'limit': min(max(int(limit or 60), 1), 100),
              'offset': max(int(offset or 0), 0), 'include_total': 'true'}
    if library_id:
        params['library_id'] = library_id
    if native_type in ('movie', 'series', 'episode', 'video', 'audiobook', 'ebook', 'manga'):
        params['type'] = native_type
    if query:
        params['q'] = str(query).strip()
    if source:
        params['source'] = source
    if collection_id:
        params['collection_id'] = collection_id
    if section_id:
        params['section_id'] = section_id
    if scope:
        params['scope'] = scope
    if sort:
        field, _, order = str(sort).partition(':')
        if field not in ('added_at', 'release_date', 'title', 'year', 'rating_imdb',
                         'latest_episode_added', 'last_air_date'):
            raise SiloError('ترتيب Silo غير مدعوم')
        params['sort'], params['order'] = field, 'asc' if order == 'asc' else 'desc'
    params.update(catalog_filters(genre, unwatched, decade, rating))
    value = _api('catalog', params=params, auth=_native_auth(server))
    raw = value if isinstance(value, list) else (value.get('items') or
                                                 value.get('results') or [])
    rows = [_item(x, server) for x in raw]
    if enrich_details and rows:
        # Search used to detail-enrich all 40 results, and metadata() could make
        # a second /versions request for every card: up to ~80 HTTP calls before
        # the result grid opened. Enrich only the first visible batch. Lower
        # rows remain fully clickable and fetch fresh detail when opened.
        detail_count = min(12, len(rows))
        head, tail = rows[:detail_count], rows[detail_count:]
        workers = min(4, detail_count)
        def _detail_or_row(row):
            try:
                return metadata(server, row.get('rating_key'), include_versions=False)
            except Exception:
                return row
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(_detail_or_row, head)) + tail
    total = int((value.get('total') if isinstance(value, dict) else 0) or len(rows))
    has_more = (bool(value.get('has_more')) if isinstance(value, dict) else False)
    has_more = has_more or (int(offset or 0) + len(rows) < total)
    return rows, total, has_more


def seasons(server, series_id):
    server = _server(server)
    value = _api('catalog/series/%s/seasons' % quote(str(series_id), safe=''),
                 auth=_native_auth(server))
    return list(value.get('seasons') or [])


def episodes(server, series_id, season_number):
    server = _server(server)
    value = _api('catalog/series/%s/seasons/%s/episodes' % (
        quote(str(series_id), safe=''), int(season_number or 0)),
        auth=_native_auth(server))
    return [_item(x, server) for x in (value.get('episodes') or value.get('items') or [])]


def metadata(server, item_id, include_versions=True):
    auth = _native_auth(server)
    path_id = quote(str(item_id), safe='')
    value = _api('catalog/items/%s' % path_id, auth=auth)
    row = (value.get('item') or value.get('detail') or value
           if isinstance(value, dict) else {})
    if include_versions and not row.get('versions'):
        try:
            extra = _api('catalog/items/%s/versions' % path_id, auth=auth)
            row['versions'] = (extra.get('versions') or []) if isinstance(extra, dict) else extra
        except Exception:
            pass
    return _item(row, server)


def search_server(server, query, media_type='', limit=40):
    native_type = {'movie': 'movie', 'show': 'series', 'series': 'series',
                   'episode': 'episode'}.get(str(media_type or '').lower(), '')
    rows, _total, _more = catalog_page(server, media_type=native_type,
                                       query=query, limit=limit,
                                       enrich_details=True)
    return rows


def media_source_lookup(server, ids, media_type='movie'):
    """Resolve Dex media-source identity through Silo's external-ID presence path.

    The requests detail contract returns ``library_content_id`` after Silo's
    presence resolver matches TMDB/IMDb/TVDB against the local media source.
    It is exact and avoids pretending an external ID is a free-text title.
    """
    tmdb_id = str((ids or {}).get('tmdb_id') or '').strip()
    if not tmdb_id or not tmdb_id.isdigit():
        return None
    native_type = 'series' if str(media_type or '').lower() in ('show', 'series', 'tv') else 'movie'
    try:
        value = _api('requests/detail/%s/%s' % (
            native_type, quote(tmdb_id, safe='')), auth=_native_auth(server))
        content_id = str(value.get('library_content_id') or '')
        if content_id:
            return metadata(server, content_id)
    except Exception:
        # Requests/TMDB can be disabled on a valid Silo server. The normal
        # catalog-title path below remains available in that configuration.
        return None
    return None


def find_all_by_ids(server, ids, media_type='movie', title='', limit=10):
    wanted = {k: str((ids or {}).get(k) or '').strip().casefold()
              for k in ('imdb_id', 'tmdb_id', 'tvdb_id')}
    # The source pipeline calls once with no title for the exact external-ID
    # lookup, then supplies title variants only if that misses. Do not repeat
    # the optional Requests/TMDB call for every localized title fallback.
    exact = media_source_lookup(server, ids, media_type) if not title else None
    if exact:
        return [exact]
    # Some servers disable the optional Requests/TMDB feature. In that case,
    # use catalog search but accept only returned IDs (or an exact title when
    # the caller has no IDs) so a similarly named movie is never substituted.
    probes = [title]
    found, seen = [], set()
    # v5.10.26: the per-row detail fallback below is serial and each call
    # pays the full request timeout; bound it so one cold server cannot
    # turn a search into a 10-20s stall.
    detail_budget = 4
    for probe in probes:
        if not str(probe or '').strip():
            continue
        try:
            rows = search_server(server, probe, media_type, max(int(limit) * 2, 12))
        except Exception:
            continue
        for row in rows:
            key = row.get('rating_key')
            if not key or key in seen:
                continue
            # search_server already enriches current catalog rows. Retain the
            # explicit detail fallback for older Silo list shapes so IDs are
            # always verified before the candidate becomes a playable source.
            detailed = row
            if not (any((row.get('ids') or {}).values()) or row.get('versions')):
                if detail_budget <= 0:
                    continue
                detail_budget -= 1
                try:
                    detailed = metadata(server, key)
                except Exception:
                    detailed = row
            row_ids = {k: str((detailed.get('ids') or {}).get(k) or '').casefold()
                       for k in wanted}
            same_id = any(wanted[k] and row_ids.get(k) == wanted[k] for k in wanted)
            # A locally scanned item may have no external ids yet. Exact title
            # is safe only for that ID-less candidate; conflicting ids never
            # pass merely because a title happens to match.
            candidate_has_ids = any(row_ids.values())
            same_title = bool(title and not candidate_has_ids and
                              str(detailed.get('title') or '').strip().casefold() ==
                              str(title).strip().casefold())
            if (same_id or same_title) and key and key not in seen:
                seen.add(key)
                found.append(detailed)
            if len(found) >= int(limit):
                return found
        if found:
            break
    return found


def find_by_ids(server, ids, media_type='movie', title=''):
    rows = find_all_by_ids(server, ids, media_type, title, 1)
    return rows[0] if rows else None


def episode_item(server, show_item, season, episode):
    series_id = str((show_item or {}).get('rating_key') or '')
    if not series_id:
        return None
    value = _api('catalog/series/%s/seasons/%s/episodes' % (
        quote(series_id, safe=''), int(season or 0)), auth=_native_auth(server))
    row = next((x for x in (value.get('episodes') or [])
                if int(x.get('episode_number') or 0) == int(episode or 0)), None)
    return _item(row, server) if row else None


def _stream_headers(auth):
    values = {'Authorization': 'Bearer ' + str(auth.get('token') or '')}
    if auth.get('profile_id'):
        values['X-Profile-Id'] = str(auth.get('profile_id'))
    if auth.get('profile_token'):
        values['X-Profile-Token'] = str(auth.get('profile_token'))
    values['X-Device-Id'] = _shared._device_id('silo')
    return urlencode(values)


def playback_url(server, item_id, media_source_id=''):
    auth = _native_auth(server)
    file_id = str(media_source_id or '')
    if not file_id:
        versions = metadata(server, item_id).get('versions') or []
        file_id = str((versions[0] if versions else {}).get('file_id') or '')
    if not file_id:
        raise SiloError('لا توجد نسخة قابلة للتشغيل في Silo')
    url = '%s/api/v1/direct-download?%s' % (
        _base(auth.get('url')), urlencode({'file_id': file_id, 'format': 'original'}))
    return url + '|' + _stream_headers(auth)


def _subtitle_absolute_url(auth, value):
    """Resolve Silo/Jellycompat subtitle delivery paths for Kodi.

    Silo builds in the wild expose subtitle URLs as native v3 ``url`` fields,
    Jellyfin-style ``DeliveryUrl`` fields, or relative delivery routes.  Kodi
    needs a concrete URL plus the same auth headers as video playback.
    """
    value = str(value or '').strip()
    if not value:
        return ''
    if not value.startswith(('http://', 'https://')):
        relative = '/' + value.lstrip('/')
        # Native playback v3 may emit API-relative subtitle routes, while the
        # Jellyfin compatibility layer emits /Videos/... routes. Prefix only
        # native routes; Jellyfin routes must stay rooted at the server origin.
        if not relative.startswith(('/api/v1/', '/api/v2/', '/Videos/')):
            relative = '/api/v1' + relative
        value = _base(auth.get('url')) + relative
    return value + '|' + _stream_headers(auth)


def _merge_playback_subtitles(auth, catalog_rows, inventory_rows):
    """Merge catalog sidecars with Silo v3/Jellycompat playback inventory.

    Do not replace the metadata list: downloaded/external subtitles can be
    present on the selected FileVersion while the playback plan exposes only
    session-routable sidecars. Matching by track id/index/language preserves
    labels and flags, then overlays the authoritative DeliveryUrl.
    """
    base_rows = [dict(x) for x in (catalog_rows or []) if isinstance(x, dict)]
    out, seen = [], set()

    def _key(row, ordinal=0):
        return (str(row.get('track_id') or row.get('id') or row.get('combined_index') or
                    row.get('index') or ordinal),
                str(row.get('language') or row.get('lang') or '').lower(),
                str(row.get('codec') or row.get('format') or '').lower())

    def _append(row, ordinal=0):
        url = (row.get('url') or row.get('delivery_url') or row.get('deliveryUrl') or
               row.get('DeliveryUrl') or row.get('externalUrl') or '')
        delivery = str(row.get('delivery') or row.get('delivery_method') or
                       row.get('DeliveryMethod') or '').lower()
        external = bool(row.get('external') or row.get('is_external') or
                        row.get('IsExternal') or delivery in ('sidecar', 'external'))
        # Embedded streams intentionally stay in the media file. Everything
        # carrying a delivery URL is attachable as an external Kodi subtitle.
        if not url and not external:
            return
        norm = dict(row)
        norm.update({
            'id': str(row.get('track_id') or row.get('id') or
                      'silo-sub-%s' % (row.get('combined_index') if row.get('combined_index') is not None else ordinal)),
            'lang': str(row.get('language') or row.get('lang') or ''),
            'language': str(row.get('language') or row.get('lang') or ''),
            'name': str(row.get('label') or row.get('display_title') or
                        row.get('DisplayTitle') or row.get('title') or row.get('name') or
                        row.get('file_name') or row.get('language') or 'Silo subtitle'),
            'codec': str(row.get('codec') or row.get('format') or row.get('Codec') or ''),
            'source': str(row.get('source') or ('external' if external else 'silo')),
            'external': external,
            'forced': bool(row.get('forced') or row.get('IsForced')),
            'default': bool(row.get('default') or row.get('IsDefault')),
            'hearing_impaired': bool(row.get('hearing_impaired') or row.get('IsHearingImpaired')),
            'url': _subtitle_absolute_url(auth, url),
        })
        key = _key(norm, ordinal)
        if key in seen:
            # Prefer the row that actually has a playable route.
            if norm.get('url'):
                for idx, old in enumerate(out):
                    if _key(old, idx) == key and not old.get('url'):
                        merged = dict(old); merged.update(norm); out[idx] = merged
                        break
            return
        seen.add(key)
        out.append(norm)

    for i, row in enumerate(base_rows):
        _append(row, i)
    for i, row in enumerate(inventory_rows or []):
        if isinstance(row, dict):
            _append(row, i + len(base_rows))
    return out



def _compat_playback_subtitles(server, item_id, media_source_id=''):
    """Fetch Silo external/downloaded subtitles from both compat surfaces.

    The Infuse compatibility patch exposes downloaded subtitles in two places:
    PlaybackInfo (session-scoped DeliveryUrl) and the item-detail MediaSources.
    Some Silo/profile combinations authorize only one of those surfaces, so
    probe both at playback handoff and merge their authenticated URLs.
    """
    auth = _native_auth(server)
    compat_server = {
        'id': str((server or {}).get('id') or auth.get('profile_id') or 'silo'),
        'name': str((server or {}).get('name') or auth.get('server_name') or 'Silo'),
        'url': str((server or {}).get('url') or auth.get('url') or ''),
        'token': str((server or {}).get('token') or auth.get('token') or ''),
        'user_id': str((server or {}).get('user_id') or auth.get('profile_id') or ''),
        'backend': 'silo',
    }
    candidates = []
    for token in (compat_server.get('token'), auth.get('profile_token')):
        token = str(token or '').strip()
        if token and token not in candidates:
            candidates.append(token)
    out, seen = [], set()

    def _take(rows):
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            url = str(row.get('url') or row.get('key') or '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(dict(row))

    last_error = None
    for token in candidates:
        probe = dict(compat_server)
        probe['token'] = token
        # 1) PlaybackInfo — preferred because DeliveryUrl carries the
        # PlaySessionId/ApiKey minted by Silo for the subtitle stream.
        try:
            resolved = _shared.resolve_playback(probe, item_id, media_source_id)
            version = resolved.get('version') or {}
            _take(version.get('subtitles') or [])
        except Exception as exc:
            last_error = exc

        # 2) Item detail — the Infuse patch also appends downloaded subtitles
        # to MediaSources here. This catches servers where PlaybackInfo omits
        # them for the selected profile/source.
        try:
            detail = _shared.metadata(probe, item_id)
            versions = list(detail.get('versions') or [])
            wanted = str(media_source_id or '')
            chosen = next((v for v in versions
                           if str(v.get('media_source_id') or '') == wanted), None)
            chosen = chosen or (versions[0] if versions else {})
            _take(chosen.get('subtitles') or [])
        except Exception as exc:
            last_error = exc

        if out:
            break

    try:
        if out:
            _shared.xbmc.log('[DexHub] Silo compat subtitles: %d external track(s)' % len(out),
                             _shared.xbmc.LOGINFO)
        elif last_error is not None:
            _shared.xbmc.log('[DexHub] Silo compat subtitle inventory unavailable: %s' % last_error,
                             _shared.xbmc.LOGDEBUG)
        else:
            _shared.xbmc.log('[DexHub] Silo compat subtitles: 0 external track(s)',
                             _shared.xbmc.LOGINFO)
    except Exception:
        pass
    return out


def resolve_playback(server, item_id, media_source_id=''):
    item = metadata(server, item_id)
    versions = item.get('versions') or []
    selected = next((v for v in versions if str(v.get('file_id') or '') == str(media_source_id or '')), None)
    selected = selected or (versions[0] if versions else {})
    file_id = str(selected.get('file_id') or media_source_id or '')
    auth = _native_auth(server)
    # Negotiate through Silo's native v3 playback contract at the actual
    # handoff. This works for accounts without download permission and lets
    # the server choose direct/remux/transcode. The original-file route below
    # is retained only as a compatibility fallback for older Silo builds.
    try:
        capabilities = {
            'video_evidence': 'declared', 'audio_evidence': 'declared',
            'codecs_video': ['h264', 'hevc', 'vp9', 'av1', 'mpeg4', 'mpeg2video'],
            'codecs_video_hardware': [],
            'codecs_audio': ['aac', 'ac3', 'eac3', 'truehd', 'dts', 'flac', 'opus', 'mp3'],
            'containers': ['mkv', 'mp4', 'webm', 'ts', 'm2ts', 'avi', 'mov'],
            'hdr': True,
        }
        delivery = {
            'enabled': True, 'supported_on_device': True,
            'containers': capabilities['containers'],
            'video_codecs': capabilities['codecs_video'],
            'audio_decode_codecs': capabilities['codecs_audio'],
            'audio_passthrough_codecs': [],
            'max_channels': 8,
            'subtitles': {'embedded_text': True, 'sidecar_text': True,
                          'ass_styling': True, 'embedded_bitmap': True,
                          'sidecar_bitmap': True, 'font_attachments': True},
            'features': [], 'auth_header_refresh': False,
            'validated_claims': [], 'transformations': [],
        }
        capabilities['codecs_video_hardware'] = list(capabilities['codecs_video'])
        decision = _api('playback/start', method='POST', auth=auth, data={
            'protocol_version': 3,
            # Only advertise the feature Dex actually executes. Newer Silo
            # treats these tokens as behavioural promises, not decoration.
            'client_features': ['playback_plan_v3'],
            'file_id': int(file_id), 'profile_id': str(auth.get('profile_id') or ''),
            'playback_attempt_id': uuid.uuid4().hex,
            'quality_preference': 'original',
            'subtitle_fidelity_preference': 'preserve',
            'progress_persistence': 'server',
            'metered': False,
            'client_capabilities': capabilities,
            'client_playback_context': {
                'protocol_version': 3, 'form_factor': 'tv', 'app_version': '5.10.14',
                'device': {'platform': 'kodi'},
                'output': {},
                # v5.10.172: Kodi plays the original file itself: it maps HDR and
                # Dolby Vision onto the screen and picks the audio track. Without
                # these claims Silo wanted a server transcode for HDR/4K files,
                # refused it (4K transcoding off: "no_alternate_version"), and
                # Dex Hub fell back to the bare file with no session, so Silo's
                # subtitles never came.
                'deliveries': {'original_http': dict(delivery, validated_claims=[
                                   'client_managed_dynamic_range_v1', 'client_selected_audio_track_v1']),
                               'progressive': delivery, 'hls': delivery},
            },
        })
        plan = decision.get('playback_plan') or {}
        candidate = ((plan.get('stream') or {}).get('url') or decision.get('stream_url') or '')
        if candidate:
            if not str(candidate).startswith(('http://', 'https://')):
                candidate = _base(auth.get('url')) + '/' + str(candidate).lstrip('/')
            # Silo v3 publishes the authoritative track inventory after the
            # playback session exists. Only real sidecar rows are attached to
            # Kodi; embedded bitmap/burn-in rows stay inside the video.
            compat_inventory = decision.get('subtitle_inventory') or []
            inventory = ((plan.get('subtitle') or {}).get('inventory') or compat_inventory or
                         decision.get('media_streams') or [])
            # Native v3 rows identify external delivery as sidecar. Historical guard: str(track.get('delivery') or '').lower() != 'sidecar' means skip it. Keep the
            # explicit test here as a compatibility guard for older contracts.
            native_inventory = [track for track in inventory if isinstance(track, dict) and
                                (not track.get('delivery') or
                                 str(track.get('delivery') or '').lower() == 'sidecar')]
            if native_inventory:
                inventory = native_inventory
            # Jellycompat builds may return a mixed MediaStreams list. Keep
            # subtitle rows only, while native v3 inventory is already clean.
            if inventory and any(isinstance(x, dict) and (x.get('Type') or x.get('type'))
                                 for x in inventory):
                inventory = [x for x in inventory if isinstance(x, dict) and
                             str(x.get('Type') or x.get('type') or '').lower() == 'subtitle']
            selected = dict(selected)
            subtitles = _merge_playback_subtitles(
                auth, selected.get('subtitles') or [], inventory)
            # v5.10.12: downloaded/external subtitles can live only on Silo's
            # Jellyfin PlaybackInfo surface. Native playback/start may return
            # the correct stream but no sidecar inventory. Merge both surfaces
            # at handoff; never delay source discovery for subtitle enrichment.
            compat_subtitles = _compat_playback_subtitles(server, item_id, file_id)
            if compat_subtitles:
                _seen_urls = {str(x.get('url') or x.get('key') or '') for x in subtitles}
                for _sub in compat_subtitles:
                    _u = str(_sub.get('url') or _sub.get('key') or '')
                    if _u and _u not in _seen_urls:
                        subtitles.append(_sub)
                        _seen_urls.add(_u)
            selected['subtitles'] = subtitles
            try:
                _shared.xbmc.log('[DexHub] Silo playback v3: %d subtitle track(s) in the plan, %d with a URL '
                                 '(%d attached in all)' % (
                                     len(inventory or []), len([x for x in inventory or [] if isinstance(x, dict)
                                                                and x.get('url')]),
                                     len([x for x in subtitles if x.get('url')])), _shared.xbmc.LOGINFO)
            except Exception:
                pass
            return {'url': str(candidate) + '|' + _stream_headers(auth),
                    'media_source_id': file_id,
                    'play_session_id': str(decision.get('session_id') or ''),
                    'version': selected}
        # v5.10.170: say why the native plan was not used (no stream in it)
        _shared.xbmc.log('[DexHub] Silo playback v3: no stream in the plan (outcome %s, %s)' % (
            decision.get('outcome') or '', ((decision.get('terminal') or {}).get('reason') or '')),
            _shared.xbmc.LOGWARNING)
    except Exception as exc:
        try:
            _shared.xbmc.log('[DexHub] Silo playback v3 failed, direct file used: %s' % (
                str(exc)[:200]), _shared.xbmc.LOGWARNING)
        except Exception:
            pass
    # Older/native-v3-incompatible builds: retain direct video fallback, but
    # still ask Jellycompat for authenticated external subtitle DeliveryUrls.
    try:
        selected = dict(selected)
        compat_subtitles = _compat_playback_subtitles(server, item_id, file_id)
        if compat_subtitles:
            selected['subtitles'] = compat_subtitles
    except Exception:
        pass
    return {'url': playback_url(server, item_id, file_id),
            'media_source_id': file_id, 'play_session_id': '', 'version': selected}


def artwork_url(server, item_id, kind='Primary'):
    value = str(item_id or '')
    return value if value.startswith(('http://', 'https://')) else ''


def logo_url(server, item_id):
    return artwork_url(server, item_id, 'Logo')
