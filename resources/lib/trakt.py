# -*- coding: utf-8 -*-
import datetime
import json
import os
import time
import urllib.error
import urllib.request

import xbmc
import xbmcaddon
import xbmcgui

from .dexhub.common import profile_path
from . import playback_store
from .log import log
from . import tmdbhelper as _tmdbhelper_art
from .art import posters_prefer_local
from . import tmdb_direct as _tmdb_direct_art
from .i18n import tr

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()
API = 'https://api.trakt.tv'
TOKEN_PATH = os.path.join(profile_path(), 'trakt_token.json')
DEFAULT_CLIENT_ID = '19abc9f8275ec0d9a8099f6edf809714184222778aa83ac50f997f3b02165001'
DEFAULT_CLIENT_SECRET = '039b60d14d91c7b948479f463e4c4bff7bd09035fc24d51a92e376cf39c47ae1'
_PUBLIC_CACHE = {}
_PUBLIC_CACHE_TTL = 300


def _setting(key, default=''):
    try:
        return ADDON.getSetting(key) or default
    except Exception:
        return default


def enabled():
    # v5.4.4: the fallback said 'false' while settings.xml defaults to
    # 'true', so a profile that never touched the key had Trakt silently
    # disabled while the settings screen showed it enabled.
    return (_setting('enable_trakt', 'true') or 'true').lower() == 'true'


def client_id():
    return (_setting('trakt_client_id', '').strip() or DEFAULT_CLIENT_ID).strip()


def client_secret():
    return (_setting('trakt_client_secret', '').strip() or DEFAULT_CLIENT_SECRET).strip()


def credentials_configured():
    return bool(client_id() and client_secret())


def ensure_enabled():
    try:
        if not enabled():
            ADDON.setSetting('enable_trakt', 'true')
    except Exception:
        pass


def authorization_status():
    if authorized():
        return 'connected'
    return 'ready' if credentials_configured() else 'needs_api'


def scrobble_enabled():
    return (_setting('trakt_scrobble', 'true') or 'true').lower() == 'true'


def sync_enabled():
    return (_setting('trakt_sync_progress', 'true') or 'true').lower() == 'true'


def _read_json(path, default):
    # v3.9.17: delegated to safe_io for crash-safe read with .bak recovery.
    from .dexhub.safe_io import read_json as _safe_read_json
    return _safe_read_json(path, default)


def _write_json(path, value):
    # v3.9.17: delegated to safe_io for atomic write + .bak snapshot —
    # avoids losing your Trakt tokens to a half-written file on power loss.
    from .dexhub.safe_io import write_json as _safe_write_json
    _safe_write_json(path, value)


def token_data():
    return _read_json(TOKEN_PATH, {})


def save_token(data):
    data = dict(data or {})
    data.setdefault('created_at', int(time.time()))
    _write_json(TOKEN_PATH, data)


def clear_token():
    try:
        os.remove(TOKEN_PATH)
    except FileNotFoundError:
        pass


def authorized():
    data = token_data()
    return bool(enabled() and credentials_configured() and data.get('access_token'))


def _headers(auth=False):
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'trakt-api-version': '2',
        'User-Agent': 'DexHub/%s (Kodi)' % (ADDON.getAddonInfo('version') or '3.7'),
    }
    cid = client_id()
    if cid:
        headers['trakt-api-key'] = cid
    if auth:
        data = token_data()
        token = data.get('access_token') or ''
        if token:
            headers['Authorization'] = 'Bearer %s' % token
    return headers


def _request(path, payload=None, method='GET', auth=False, timeout=20):
    url = API + path
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=_headers(auth=auth), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode('utf-8', 'ignore')
        return json.loads(body) if body else {}


def _refresh_token_if_needed(force=False):
    data = token_data()
    if not data.get('refresh_token'):
        return data
    created = int(data.get('created_at') or 0)
    expires_in = int(data.get('expires_in') or 0)
    if not force and data.get('access_token') and expires_in and (created + expires_in - 300) > int(time.time()):
        return data
    payload = {
        'refresh_token': data.get('refresh_token'),
        'client_id': client_id(),
        'client_secret': client_secret(),
        'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
        'grant_type': 'refresh_token',
    }
    try:
        refreshed = _request('/oauth/token', payload=payload, method='POST', auth=False)
    except Exception:
        refreshed = None
    if isinstance(refreshed, dict) and refreshed.get('access_token'):
        refreshed['created_at'] = int(time.time())
        save_token(refreshed)
        return refreshed
    return data


def _ensure_auth():
    ensure_enabled()
    if not credentials_configured():
        raise RuntimeError('بيانات Trakt غير مدمجة في هذه النسخة')
    data = _refresh_token_if_needed(force=False)
    if not data.get('access_token'):
        raise RuntimeError('Trakt غير مربوط بعد')
    return data


def _build_pin_message(verify_url, user_code, remaining=None):
    """Single-string message compatible with Kodi 20+ (Nexus/Omega) dialogs."""
    lines = [
        '[B]١) افتح الرابط:[/B]',
        '[COLOR orange]%s[/COLOR]' % verify_url,
        '',
        '[B]٢) أدخل الرمز:[/B]',
        '[COLOR yellow][B]   %s   [/B][/COLOR]' % user_code,
    ]
    if remaining is not None:
        lines.append('')
        lines.append(tr('المتبقي: %ss') % int(remaining))
    return '\n'.join(lines)


def device_auth():
    """Link Trakt with a branded QR/device-code window.

    v5.10.22 removes the old two-dialog PIN flow.  The QR is generated locally,
    polling remains on the dispatch thread only for the short pairing session,
    and BACK cancels immediately.  No external QR service is used.
    """
    ensure_enabled()
    if not credentials_configured():
        raise RuntimeError(tr('أدخل Trakt Client ID و Client Secret في الإعدادات أولاً'))

    try:
        code = _request('/oauth/device/code', payload={'client_id': client_id()}, method='POST', auth=False)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode('utf-8', 'ignore')
        except Exception:
            body = ''
        raise RuntimeError(tr('فشل طلب رمز Trakt (%s): %s') % (exc.code, body[:200]))
    except Exception as exc:
        raise RuntimeError(tr('فشل الاتصال بـ Trakt: %s') % exc)

    user_code = (code.get('user_code') or '').strip()
    device_code = (code.get('device_code') or '').strip()
    verify = code.get('verification_url') or 'https://trakt.tv/activate'
    interval = max(3, int(code.get('interval') or 5))
    expires_in = max(60, int(code.get('expires_in') or 600))
    if not user_code or not device_code:
        raise RuntimeError(tr('لم يرجع Trakt رمز ربط صالح'))

    from .plex_qr import qr_png, QRLinkWindow
    qr_path = qr_png(verify, box_size=8, border=4) or ''
    window = QRLinkWindow(
        qr_path=qr_path,
        code=user_code,
        title=tr('ربط Trakt'),
        verification_url=verify,
        body_text=tr(
            'امسح الباركود بالجوال لفتح Trakt.\n\n'
            'ثم أدخل رمز الربط:\n\n[B][COLOR yellow]%s[/COLOR][/B]\n\n'
            'أو افتح:\n[COLOR cyan]%s[/COLOR]\n\n'
            'بانتظار الموافقة…  (رجوع للإلغاء)'
        ) % (user_code, verify),
    )
    window.show()
    monitor = xbmc.Monitor()
    start = time.time()
    try:
        while (time.time() - start) < expires_in and not monitor.abortRequested():
            if getattr(window, 'cancelled', False):
                return False
            try:
                token = _request('/oauth/device/token', payload={
                    'code': device_code,
                    'client_id': client_id(),
                    'client_secret': client_secret(),
                }, method='POST', auth=False)
                if isinstance(token, dict) and token.get('access_token'):
                    save_token(token)
                    window.close()
                    xbmcgui.Dialog().notification('Dex Hub', tr('تم ربط Trakt بنجاح'), xbmcgui.NOTIFICATION_INFO, 3000)
                    invalidate_cache()
                    return True
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    interval = min(30, max(interval + 1, interval * 2))
                elif exc.code not in (400, 404):
                    xbmc.log('[DexHub] trakt poll http %s' % exc.code, xbmc.LOGWARNING)
            except Exception as exc:
                xbmc.log('[DexHub] trakt poll transient error: %s' % exc, xbmc.LOGWARNING)
            remaining = max(0, int(expires_in - (time.time() - start)))
            try:
                window.set_status(tr(
                    'امسح الباركود بالجوال لفتح Trakt.\n\n'
                    'رمز الربط: [B][COLOR yellow]%s[/COLOR][/B]\n\n'
                    'بانتظار الموافقة…\nالمتبقي: %s ثانية\n\n(رجوع للإلغاء)'
                ) % (user_code, remaining))
            except Exception:
                pass
            if monitor.waitForAbort(interval):
                break
    finally:
        try:
            window.close()
        except Exception:
            pass
    return False

def logout():
    data = token_data()
    token = data.get('access_token')
    if token and client_id() and client_secret():
        try:
            _request('/oauth/revoke', payload={
                'token': token,
                'client_id': client_id(),
                'client_secret': client_secret(),
            }, method='POST', auth=False)
        except Exception:
            pass
    clear_token()
    xbmcgui.Dialog().notification('Dex Hub', tr('تم تسجيل الخروج من Trakt'), xbmcgui.NOTIFICATION_INFO, 2500)


def _ids_payload(ctx):
    ids = {}
    imdb = str(ctx.get('imdb_id') or '').strip()
    tmdb = str(ctx.get('tmdb_id') or '').strip()
    tvdb = str(ctx.get('tvdb_id') or '').strip()
    if imdb:
        ids['imdb'] = imdb if imdb.startswith('tt') else 'tt%s' % imdb
    if tmdb and tmdb.isdigit():
        ids['tmdb'] = int(tmdb)
    elif tmdb:
        try:
            ids['tmdb'] = int(float(tmdb))
        except Exception:
            pass
    if tvdb and tvdb.isdigit():
        ids['tvdb'] = int(tvdb)
    elif tvdb:
        try:
            ids['tvdb'] = int(float(tvdb))
        except Exception:
            pass
    return ids


def _progress(ctx, position_ms):
    duration = float(ctx.get('duration_ms') or 0)
    if duration <= 0:
        return 0.0
    return max(0.0, min(100.0, (float(position_ms or 0) / duration) * 100.0))


def _movie_payload(ctx, position_ms):
    payload = {
        'progress': round(_progress(ctx, position_ms), 2),
        'app_version': ADDON.getAddonInfo('version') or '3.7',
        'app_date': datetime.date.today().isoformat(),
        'movie': {
            'title': ctx.get('title') or '',
            'year': int(ctx.get('year') or 0) or None,
            'ids': _ids_payload(ctx),
        }
    }
    if payload['movie']['year'] is None:
        payload['movie'].pop('year', None)
    return payload


def _episode_payload(ctx, position_ms):
    payload = {
        'progress': round(_progress(ctx, position_ms), 2),
        'app_version': ADDON.getAddonInfo('version') or '3.7',
        'app_date': datetime.date.today().isoformat(),
        'show': {
            'title': ctx.get('show_title') or ctx.get('title') or '',
            'year': int(ctx.get('year') or 0) or None,
            'ids': _ids_payload(ctx),
        },
        'episode': {
            'season': int(ctx.get('season') or 0),
            'number': int(ctx.get('episode') or 0),
        }
    }
    if payload['show']['year'] is None:
        payload['show'].pop('year', None)
    return payload


def _scrobble_payload(ctx, position_ms):
    media_type = str(ctx.get('media_type') or 'movie').lower()
    if media_type in ('series', 'show', 'tv', 'anime') or ctx.get('season') or ctx.get('episode'):
        return _episode_payload(ctx, position_ms)
    return _movie_payload(ctx, position_ms)


# v5.10.103: a refused link (HTTP 401) or Trakt's rate limit (HTTP 429) used
# to be retried on every progress report, every few seconds for a whole
# playback, which also got the account rate limited. Now a refused link gets
# one forced token refresh; if that does not help, scrobbling pauses until
# Trakt is linked again (the token file changes) and the user is told once.
# A 429 waits for the time Trakt asks for. The pause is kept on Kodi's Home
# window, so the service and every player invocation see it.
_BLOCK_PROP = 'dexhub.trakt.scrobble_block'
_NOTICE_PROP = 'dexhub.trakt.auth_notice'
_AUTH_BLOCK = 6 * 3600
_SCROBBLE_TIMEOUT = 10


def _home_window():
    return xbmcgui.Window(10000)


def _token_stamp():
    try:
        return int(os.path.getmtime(TOKEN_PATH))
    except Exception:
        return 0


def scrobble_paused_reason():
    """'auth' or 'rate' while scrobbling waits, else ''."""
    try:
        raw = _home_window().getProperty(_BLOCK_PROP) or ''
    except Exception:
        return ''
    if not raw:
        return ''
    try:
        until, reason, stamp = raw.split('|', 2)
        until, stamp = float(until), int(stamp)
    except Exception:
        until, reason, stamp = 0.0, '', 0
    if time.time() >= until or (reason == 'auth' and _token_stamp() != stamp):
        try:
            _home_window().clearProperty(_BLOCK_PROP)
        except Exception:
            pass
        return ''
    return reason


def _pause_scrobbling(seconds, reason):
    try:
        _home_window().setProperty(_BLOCK_PROP, '%d|%s|%d' % (time.time() + seconds, reason, _token_stamp()))
    except Exception:
        pass


def _scrobble_post(action, payload):
    try:
        return _request('/scrobble/%s' % action, payload=payload, method='POST', auth=True,
                        timeout=_SCROBBLE_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        # the saved token was refused: one forced refresh, then one more try
        before = token_data().get('access_token') or ''
        fresh = _refresh_token_if_needed(force=True) or {}
        if not fresh.get('access_token') or fresh.get('access_token') == before:
            raise
        return _request('/scrobble/%s' % action, payload=payload, method='POST', auth=True,
                        timeout=_SCROBBLE_TIMEOUT)


def _scrobble_refused(exc):
    code = int(getattr(exc, 'code', 0) or 0)
    if code in (401, 403):
        _pause_scrobbling(_AUTH_BLOCK, 'auth')
        xbmc.log('[DexHub] trakt: the saved link was refused (HTTP %d); scrobbling pauses '
                 'until Trakt is linked again' % code, xbmc.LOGWARNING)
        try:
            home = _home_window()
            if not home.getProperty(_NOTICE_PROP):
                home.setProperty(_NOTICE_PROP, '1')
                xbmcgui.Dialog().notification(
                    'Trakt', tr('انتهى ربط Trakt. اربطه من جديد من إعدادات Dex Hub'),
                    xbmcgui.NOTIFICATION_WARNING, 6000)
        except Exception:
            pass
    elif code == 429:
        try:
            wait = int(float(exc.headers.get('Retry-After') or 0))
        except Exception:
            wait = 0
        wait = max(30, min(wait or 60, 900))
        _pause_scrobbling(wait, 'rate')
        xbmc.log('[DexHub] trakt: rate limited (HTTP 429); next scrobble in %d s' % wait,
                 xbmc.LOGWARNING)
    elif code == 409:
        pass        # Trakt already has this scrobble
    elif code >= 500:
        _pause_scrobbling(120, 'rate')
        xbmc.log('[DexHub] trakt scrobble failed: HTTP %d; trying again in 2 minutes' % code,
                 xbmc.LOGWARNING)
    else:
        xbmc.log('[DexHub] trakt scrobble failed: HTTP %d' % code, xbmc.LOGWARNING)


def scrobble(action, ctx, position_ms=0):
    """Safe scrobble — never raises into the player."""
    if not (enabled() and scrobble_enabled()):
        return None
    if scrobble_paused_reason():
        return None
    try:
        data = _ensure_auth()
    except Exception as exc:
        xbmc.log('[DexHub] trakt scrobble skipped (not auth): %s' % exc, xbmc.LOGDEBUG)
        return None
    if not data.get('access_token'):
        return None
    try:
        progress = _progress(ctx or {}, position_ms)
        # Avoid Trakt 422 noise when playback fails before the first frame or
        # stops near the start: Trakt refuses a pause or stop below 1 percent
        # (v5.10.143, GitHub issue #3). DexHub already stores local progress.
        if str(action or '').lower() in ('stop', 'pause') and progress < 1.0:
            return None
        payload = _scrobble_payload(ctx or {}, position_ms)
        ids = ((payload.get('movie') or payload.get('show') or {}).get('ids') or {})
        if not ids:
            return None
        return _scrobble_post(action, payload)
    except urllib.error.HTTPError as exc:
        _scrobble_refused(exc)
        return None
    except Exception as exc:
        xbmc.log('[DexHub] trakt scrobble failed: %s' % exc, xbmc.LOGWARNING)
        return None


def _canonical_from_ids(ids, fallback_title=''):
    ids = ids or {}
    if ids.get('tmdb'):
        return 'tmdb:%s' % ids.get('tmdb')
    if ids.get('imdb'):
        return ids.get('imdb')
    if ids.get('tvdb'):
        return 'tvdb:%s' % ids.get('tvdb')
    if ids.get('trakt'):
        return 'trakt:%s' % ids.get('trakt')
    return fallback_title or ''


def _fetch_art_setting():
    raw = (_setting('trakt_fetch_art', 'true') or 'true').lower()
    return raw not in ('false', '0', 'no')


def _art_bundle_for_ids(ids, media_type, title='', force_remote=False):
    """Resolve fanart/clearlogo, and only use remote posters when enabled.

    `force_remote=True` overrides the 'Local only' poster preference. Used by
    Next Up rows because they have no local provider context — the row is
    derived from a Trakt sync, so the only available poster source is remote.
    """
    if not _fetch_art_setting():
        return {'poster': '', 'fanart': '', 'clearlogo': ''}
    ids = ids or {}
    tmdb_id = str(ids.get('tmdb') or '').strip()
    imdb_id = str(ids.get('imdb') or '').strip()
    prefer_local_poster = posters_prefer_local() and not force_remote
    try:
        bundle = _tmdbhelper_art.get_art_bundle_from_db(
            tmdb_id=tmdb_id,
            media_type='tv' if media_type == 'series' else 'movie',
            imdb_id=imdb_id,
            title=title or '',
        ) or {}
    except Exception:
        bundle = {}
    need_direct = not (bundle.get('fanart') and bundle.get('clearlogo') and (prefer_local_poster or bundle.get('poster')))
    if need_direct:
        try:
            direct = _tmdb_direct_art.art_for(
                tmdb_id=tmdb_id,
                imdb_id=imdb_id,
                media_type='tv' if media_type == 'series' else 'movie',
                title=title or '',
            ) or {}
        except Exception:
            direct = {}
    else:
        direct = {}
    return {
        'poster': '' if prefer_local_poster else (bundle.get('poster') or direct.get('poster') or ''),
        'fanart': bundle.get('fanart') or bundle.get('landscape') or direct.get('fanart') or direct.get('landscape') or '',
        'clearlogo': bundle.get('clearlogo') or direct.get('clearlogo') or '',
    }



def _cached_public_request(path, timeout=20):
    now = time.time()
    cached = _PUBLIC_CACHE.get(path)
    if cached and (now - cached[0]) <= _PUBLIC_CACHE_TTL:
        return cached[1]
    data = _request(path, method='GET', auth=False, timeout=timeout)
    _PUBLIC_CACHE[path] = (now, data)
    return data


def invalidate_cache(prefix=''):
    """Drop entries from the public-request cache.

    With no prefix → clears ALL cached Trakt responses.
    With a prefix → clears every entry whose key starts with or equals it.

    Service-side sync calls this with 'next_up_v1' and '/sync/playback/' so the
    Next Up widget reflects the freshest data immediately after sync, instead
    of waiting for the natural TTL (10min/6h) to expire.
    """
    if not prefix:
        _PUBLIC_CACHE.clear()
        return
    prefix = str(prefix)
    for key in [k for k in list(_PUBLIC_CACHE.keys()) if str(k).startswith(prefix) or k == prefix]:
        _PUBLIC_CACHE.pop(key, None)


# ---------------------------------------------------------------- smart sync
# v5.10.208: Trakt says when each part of an account last changed
# (/sync/last_activities, one small request). A pull whose part has not
# changed since the last pull is skipped: the playback list, the watchlist,
# and Next Up (one request per show) are read again only when something
# happened on Trakt. Settings, Following and library: Smart Trakt sync.
ACT_PATH = os.path.join(profile_path(), 'trakt_activities.json')
_ACT_PROP = 'dexhub.trakt.activities'
_ACT_TTL = 60.0


def _note(message):
    # (``log`` here is the category logger: log.info('TRAKT', ...))
    xbmc.log('[DexHub] %s' % message, xbmc.LOGINFO)
_SECTIONS = {
    'playback': (('movies', 'paused_at'), ('episodes', 'paused_at'), ('movies', 'watched_at'),
                 ('episodes', 'watched_at')),
    'watchlist': (('movies', 'watchlisted_at'), ('shows', 'watchlisted_at'), ('seasons', 'watchlisted_at'),
                  ('episodes', 'watchlisted_at')),
    'watched': (('movies', 'watched_at'), ('episodes', 'watched_at'), ('shows', 'hidden_at')),
}


def smart_sync():
    return (_setting('trakt_smart_sync', 'true') or 'true').lower() != 'false'


def last_activities():
    """Trakt's /sync/last_activities, read at most once a minute (shared by
    Kodi's processes through a window property); {} when unknown."""
    win = None
    try:
        win = xbmcgui.Window(10000)
        raw = win.getProperty(_ACT_PROP)
        if raw:
            stamp, data = raw.split('|', 1)
            if time.time() - float(stamp) < _ACT_TTL:
                return json.loads(data)
    except Exception:
        pass
    try:
        _ensure_auth()
        data = _request('/sync/last_activities', method='GET', auth=True, timeout=8) or {}
    except Exception as exc:
        _note('trakt last_activities: %s' % exc.__class__.__name__)
        # a failed read is remembered a minute too: no 8 s wait per question
        try:
            if win is not None:
                win.setProperty(_ACT_PROP, '%.0f|{}' % time.time())
        except Exception:
            pass
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        if win is not None:
            win.setProperty(_ACT_PROP, '%.0f|%s' % (time.time(), json.dumps(data)))
    except Exception:
        pass
    return data


def _section_stamp(data, section):
    stamps = []
    for group, key in _SECTIONS.get(section, ()):
        value = ((data or {}).get(group) or {}).get(key)
        if value:
            stamps.append(str(value))
    return max(stamps) if stamps else ''


def unchanged(section):
    """True when smart sync is on and Trakt's ``section`` has not changed
    since it was last pulled (unknown counts as changed)."""
    if not smart_sync():
        return False
    current = _section_stamp(last_activities(), section)
    if not current:
        return False
    saved = (_read_json(ACT_PATH, {}).get('synced') or {}).get(section)
    return saved == current


def mark_synced(section):
    """Remember Trakt's stamp of ``section`` after a pull that worked."""
    current = _section_stamp(last_activities(), section)
    if not current:
        return
    data = _read_json(ACT_PATH, {})
    data.setdefault('synced', {})[section] = current
    _write_json(ACT_PATH, data)


def _saved_next_up():
    data = _read_json(ACT_PATH, {})
    saved = data.get('nextup') or {}
    current = _section_stamp(last_activities(), 'watched') if smart_sync() else ''
    fresh = time.time() - float(saved.get('at') or 0) < 6 * 3600   # a day's shows may air meanwhile
    if current and fresh and saved.get('stamp') == current and isinstance(saved.get('rows'), list):
        return saved['rows']
    return None


def _save_next_up(rows):
    current = _section_stamp(last_activities(), 'watched')
    if not current:
        return
    data = _read_json(ACT_PATH, {})
    data['nextup'] = {'stamp': current, 'rows': rows, 'at': int(time.time())}
    _write_json(ACT_PATH, data)


def watched_episodes(imdb_id='', tmdb_id=''):
    """v5.10.208: the episodes of a show watched on Trakt, as a set of
    (season, episode), so a title page ticks what was watched anywhere.
    Kept per show and read again only when Trakt's watched list changed."""
    try:
        if not (enabled() and authorized() and sync_enabled()):
            return set()
    except Exception:
        return set()
    imdb_id = str(imdb_id or '').split(':')[0]
    imdb_id = imdb_id if imdb_id.startswith('tt') else ''
    tmdb_id = str(tmdb_id or '')
    key = imdb_id or ('tmdb:%s' % tmdb_id if tmdb_id.isdigit() else '')
    if not key:
        return set()
    data = _read_json(ACT_PATH, {})
    shows = data.get('shows') if isinstance(data.get('shows'), dict) else {}
    hit = shows.get(key) or {}
    current = _section_stamp(last_activities(), 'watched')
    age = time.time() - float(hit.get('at') or 0)
    if hit and ((current and hit.get('stamp') == current) or (not current and age < 3600)):
        return set(tuple(x) for x in hit.get('eps') or [])
    if time.time() - float(hit.get('fail_at') or 0) < 600:
        return set(tuple(x) for x in hit.get('eps') or [])     # Trakt failed lately: no new wait
    try:
        show_id = imdb_id or str(hit.get('tid') or '')
        if not show_id:
            found = _request('/search/tmdb/%s?type=show' % tmdb_id, method='GET', auth=True, timeout=6) or []
            show_id = str((((found or [{}])[0].get('show') or {}).get('ids') or {}).get('trakt') or '')
        if not show_id:
            return set(tuple(x) for x in hit.get('eps') or [])
        progress = _request('/shows/%s/progress/watched?hidden=false&specials=false&count_specials=false'
                            % show_id, method='GET', auth=True, timeout=6) or {}
    except Exception as exc:
        _note('trakt watched episodes: %s' % exc.__class__.__name__)
        try:
            shows[key] = dict(hit, fail_at=int(time.time()))
            data['shows'] = shows
            _write_json(ACT_PATH, data)
        except Exception:
            pass
        return set(tuple(x) for x in hit.get('eps') or [])
    eps = []
    for season in progress.get('seasons') or []:
        for episode in season.get('episodes') or []:
            if episode.get('completed'):
                try:
                    eps.append([int(season.get('number') or 0), int(episode.get('number') or 0)])
                except (TypeError, ValueError):
                    continue
    shows[key] = {'stamp': current, 'at': int(time.time()), 'eps': eps, 'tid': show_id}
    if len(shows) > 200:
        for old in sorted(shows, key=lambda k: shows[k].get('at') or 0)[:len(shows) - 200]:
            shows.pop(old, None)
    data['shows'] = shows
    _write_json(ACT_PATH, data)
    return set(tuple(x) for x in eps)


def import_progress(limit=100, force=False):
    if not (enabled() and sync_enabled()):
        raise RuntimeError('فعّل مزامنة Trakt من الإعدادات')
    _ensure_auth()
    if not force and unchanged('playback'):
        _note('trakt progress: nothing changed on Trakt, pull skipped')
        return 0
    pending = []
    failed = False
    for path, media_type in (('/sync/playback/movies?extended=full', 'movie'), ('/sync/playback/episodes?extended=full', 'series')):
        try:
            rows = _request(path, method='GET', auth=True) or []
        except Exception:
            rows = []
            failed = True
        for row in rows[:limit]:
            try:
                # Trakt gives `paused_at` (ISO 8601) — use it as the row's
                # updated_at so Continue Watching ordering stays stable across
                # imports. Falling back to time.time() reshuffled the row on
                # every sync (the original 3.7.5 bug).
                paused_at = _parse_trakt_ts(row.get('paused_at') or row.get('last_watched_at'))
                if media_type == 'movie':
                    movie = row.get('movie') or {}
                    ids = movie.get('ids') or {}
                    title = movie.get('title') or 'Unknown'
                    runtime = float(movie.get('runtime') or 0) * 60.0
                    duration = runtime if runtime > 0 else 0.0
                    progress = float(row.get('progress') or 0.0)
                    position = (duration * progress / 100.0) if duration > 0 else 0.0
                    canonical = _canonical_from_ids(ids, title)
                    art = _art_bundle_for_ids(ids, 'movie', title=title)
                    pending.append({
                        'media_type': 'movie', 'canonical_id': canonical,
                        'video_id': canonical, 'title': title,
                        'provider_name': 'Trakt', 'poster': art['poster'],
                        'background': art['fanart'], 'clearlogo': art['clearlogo'],
                        'season': None, 'episode': None, 'position': position,
                        'duration': duration, 'percent': progress,
                        'stream_url': '', 'event_type': 'progress',
                        'ext_updated_at': paused_at,
                        'tmdb_id': str(ids.get('tmdb') or ''),
                        'imdb_id': str(ids.get('imdb') or ''),
                        'tvdb_id': str(ids.get('tvdb') or ''),
                    })
                else:
                    episode = row.get('episode') or {}
                    show = row.get('show') or {}
                    ids = show.get('ids') or {}
                    title = show.get('title') or 'Unknown'
                    runtime = float(episode.get('runtime') or show.get('runtime') or 0) * 60.0
                    duration = runtime if runtime > 0 else 0.0
                    progress = float(row.get('progress') or 0.0)
                    position = (duration * progress / 100.0) if duration > 0 else 0.0
                    canonical = _canonical_from_ids(ids, title)
                    video_id = '%s:%s:%s' % (canonical, int(episode.get('season') or 0), int(episode.get('number') or 0))
                    art = _art_bundle_for_ids(ids, 'series', title=title)
                    pending.append({
                        'media_type': 'series', 'canonical_id': canonical,
                        'video_id': video_id, 'title': title,
                        'provider_name': 'Trakt', 'poster': art['poster'],
                        'background': art['fanart'], 'clearlogo': art['clearlogo'],
                        'season': int(episode.get('season') or 0),
                        'episode': int(episode.get('number') or 0),
                        'position': position, 'duration': duration,
                        'percent': progress, 'stream_url': '',
                        'event_type': 'progress', 'ext_updated_at': paused_at,
                        'tmdb_id': str(ids.get('tmdb') or ''),
                        'show_tmdb_id': str(ids.get('tmdb') or ''),
                        'imdb_id': str(ids.get('imdb') or ''),
                        'tvdb_id': str(ids.get('tvdb') or ''),
                    })
            except Exception:
                continue
    count = int(playback_store.upsert_entries(pending, mark_dirty=False) or 0) if pending else 0
    if not failed:
        mark_synced('playback')
    return count


def _parse_trakt_ts(value):
    """Parse a Trakt ISO 8601 timestamp into epoch seconds, or None."""
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except Exception:
        try:
            return int(time.mktime(time.strptime(str(value)[:19], '%Y-%m-%dT%H:%M:%S')))
        except Exception:
            return None


def fetch_watchlist(limit=100, strict=False, fetch_art=True):
    """Return movies + shows on the user's Trakt watchlist."""
    if not enabled():
        return []
    try:
        _ensure_auth()
    except Exception:
        if strict:
            raise
        return []
    out = []
    for path, media_type in (('/users/me/watchlist/movies', 'movie'),
                             ('/users/me/watchlist/shows', 'series')):
        try:
            rows = _request(path, method='GET', auth=True) or []
        except Exception:
            if strict:
                raise
            rows = []
        if not isinstance(rows, list):
            raise RuntimeError('Invalid Trakt watchlist response')
        for row in rows[:limit]:
            try:
                node = row.get('movie') if media_type == 'movie' else row.get('show')
                if not node:
                    continue
                ids = node.get('ids') or {}
                title = node.get('title') or ''
                year = int(node.get('year') or 0)
                canonical = _canonical_from_ids(ids, title)
                art = _art_bundle_for_ids(ids, media_type, title=title) if fetch_art and (_setting('trakt_fetch_art', 'true') == 'true') else {'poster':'', 'fanart':'', 'clearlogo':''}
                out.append({
                    'media_type': media_type,
                    'canonical_id': canonical,
                    'title': title,
                    'year': year,
                    'plot': node.get('overview') or '',
                    'poster': art.get('poster') or '',
                    'background': art.get('fanart') or '',
                    'clearlogo': art.get('clearlogo') or '',
                    'added_at': _parse_trakt_ts(row.get('listed_at')) or int(time.time()),
                })
            except Exception:
                continue
    return out


def fetch_next_up(limit=40):
    """Return the next unwatched episode for every show the user has started.

    Uses Trakt's `/sync/watched/shows` to find started shows, then
    `/shows/{id}/progress/watched` to get the next episode for each.
    Cached results are returned from a 10-minute in-memory cache to keep
    the home screen snappy.
    """
    cache_key = 'next_up_v1'
    cached = _PUBLIC_CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]
    if not enabled():
        return []
    try:
        _ensure_auth()
    except Exception:
        return []
    # v5.10.208: nothing watched on Trakt since the last read: the saved list
    saved = _saved_next_up()
    if saved is not None:
        _PUBLIC_CACHE[cache_key] = (time.time() + 600, saved)
        return saved
    out = []
    watched_ok = True
    try:
        watched = _request('/sync/watched/shows', method='GET', auth=True) or []
    except Exception:
        watched = []
        watched_ok = False
    # Sort by last_watched_at descending so the home row leads with the most
    # recently active shows.
    def _key(row):
        return _parse_trakt_ts((row.get('last_watched_at') or '')) or 0
    watched = sorted(watched, key=_key, reverse=True)[:limit]
    for row in watched:
        try:
            show = row.get('show') or {}
            ids = show.get('ids') or {}
            slug = ids.get('slug') or ids.get('trakt')
            if not slug:
                continue
            try:
                progress = _request('/shows/%s/progress/watched?hidden=false&specials=false' % slug,
                                    method='GET', auth=True) or {}
            except Exception:
                continue
            nxt = (progress or {}).get('next_episode') or {}
            if not nxt:
                continue
            title = show.get('title') or 'Unknown'
            canonical = _canonical_from_ids(ids, title)
            art = _art_bundle_for_ids(ids, 'series', title=title, force_remote=True) if (_setting('trakt_fetch_art', 'true') == 'true') else {'poster':'', 'fanart':'', 'clearlogo':''}
            season_n = int(nxt.get('season') or 0)
            episode_n = int(nxt.get('number') or 0)
            out.append({
                'media_type': 'series',
                'canonical_id': canonical,
                'video_id': '%s:%s:%s' % (canonical, season_n, episode_n),
                'title': title,
                'episode_title': nxt.get('title') or '',
                'show_tmdb_id': str(ids.get('tmdb') or ''),
                'imdb_id': str(ids.get('imdb') or ''),
                'season': season_n,
                'episode': episode_n,
                'plot': nxt.get('overview') or show.get('overview') or '',
                'poster': art.get('poster') or '',
                'background': art.get('fanart') or '',
                'clearlogo': art.get('clearlogo') or '',
                'last_watched_at': _parse_trakt_ts(row.get('last_watched_at')) or int(time.time()),
            })
        except Exception:
            continue
    _PUBLIC_CACHE[cache_key] = (time.time() + 600, out)  # 10-minute cache
    if watched_ok:
        try:
            _save_next_up(out)
        except Exception:
            pass
    return out


def fetch_my_lists(limit=100):
    """The signed-in user's own Trakt lists.

    v5.10.24: nothing in Dex Hub could reach /users/me/lists, so the only way
    to open a list was trakt_list_browse with a username and slug typed by
    hand. Rows are returned as Trakt delivers them, with the fields a menu
    needs pulled to the top level so callers do not re-walk the payload.
    """
    if not authorized():
        return []
    try:
        rows = _request('/users/me/lists', method='GET', auth=True) or []
    except Exception as exc:
        log.warn('TRAKT', 'my lists failed: %s' % exc)
        return []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        ids = row.get('ids') or {}
        out.append({
            'name': row.get('name') or 'List',
            'slug': str(ids.get('slug') or ''),
            'list_id': str(ids.get('trakt') or ''),
            'username': str(((row.get('user') or {}).get('ids') or {}).get('slug') or 'me'),
            'item_count': int(row.get('item_count') or 0),
            'description': row.get('description') or '',
            'privacy': row.get('privacy') or '',
        })
        if limit and len(out) >= int(limit):
            break
    return out


def fetch_collection(media_type='movies'):
    """The user's Trakt collection (what they own), movies or shows.

    Returned in the same row shape as fetch_watchlist so existing renderers
    accept it without a special case.
    """
    if not authorized():
        return []
    kind = 'shows' if str(media_type).lower().startswith(('show', 'series', 'tv')) else 'movies'
    try:
        rows = _request('/sync/collection/%s?extended=full' % kind,
                        method='GET', auth=True) or []
    except Exception as exc:
        log.warn('TRAKT', 'collection failed: %s' % exc)
        return []
    single = 'show' if kind == 'shows' else 'movie'
    out = []
    for row in rows if isinstance(rows, list) else []:
        meta = (row or {}).get(single)
        if isinstance(meta, dict):
            out.append({'type': single, single: meta,
                        'collected_at': row.get('collected_at') or ''})
    return out


def fetch_favorites(limit=100):
    """Trakt's own Favorites, which are a separate list from the watchlist.

    Dex Hub already folds the watchlist into its Favorites screen; this is the
    other one, the list Trakt itself calls Favorites, which had no reader at
    all. An older account may not have the endpoint, so a 4xx returns empty
    rather than surfacing as an error.
    """
    if not authorized():
        return []
    out = []
    for kind, single in (('movies', 'movie'), ('shows', 'show')):
        try:
            rows = _request('/users/me/favorites/%s?extended=full' % kind,
                            method='GET', auth=True) or []
        except Exception as exc:
            log.warn('TRAKT', 'favorites %s failed: %s' % (kind, exc))
            continue
        for row in rows if isinstance(rows, list) else []:
            meta = (row or {}).get(single)
            if isinstance(meta, dict):
                out.append({'type': single, single: meta,
                            'listed_at': row.get('listed_at') or ''})
    if limit:
        out = out[:int(limit)]
    return out



def fetch_user_list(username, slug, list_type='movies,shows', limit=None):
    """Fetch a public Trakt list's items.

    Does NOT require auth for public lists. Returns the raw list rows from
    Trakt (each row has either a 'movie' or 'show' sub-object with ids).
    """
    username = (username or '').strip()
    slug = (slug or '').strip()
    if not username or not slug:
        return []
    path = '/users/%s/lists/%s/items?type=%s&extended=full' % (username, slug, list_type)
    if limit:
        try:
            path += '&limit=%d' % max(1, int(limit))
        except Exception:
            pass
    try:
        data = _cached_public_request(path)
    except urllib.error.HTTPError as exc:
        # 401 means the list is private; try with auth.
        if exc.code in (401, 403):
            try:
                data = _request(path, method='GET', auth=True)
            except Exception:
                return []
        else:
            return []
    except Exception:
        return []
    return data if isinstance(data, list) else []


def fetch_list_by_id(list_id, page=1, limit=100, list_type='movies,shows',
                     timeout=4):
    """Fetch public Trakt list items using the numeric global list id.

    Kaptain/Nuvio native exports store ``traktListId`` rather than the owner's
    username/slug. Trakt supports ``/lists/{id}/items`` directly. Explicit
    pagination is used because Trakt made list pagination mandatory in 2026.
    """
    list_id = str(list_id or '').strip()
    if not list_id:
        return []
    try:
        page = max(1, int(page or 1))
    except Exception:
        page = 1
    try:
        limit = min(250, max(1, int(limit or 100)))
    except Exception:
        limit = 100
    type_part = str(list_type or 'movies,shows').strip() or 'movies,shows'
    path = '/lists/%s/items/%s?extended=full&page=%d&limit=%d' % (list_id, type_part, page, limit)
    try:
        data = _cached_public_request(path, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            try:
                data = _request(
                    path, method='GET', auth=True, timeout=timeout)
            except Exception:
                return []
        else:
            return []
    except Exception:
        return []
    return data if isinstance(data, list) else []



_PROGRESS_LOOKUP_CACHE = {}
_PROGRESS_LOOKUP_TTL = 90

def find_playback_progress(media_type='movie', tmdb_id='', imdb_id='', tvdb_id='', season=None, episode=None):
    """Return the matching unfinished Trakt playback item on demand.

    Background sync remains the normal path. This targeted fallback is used
    only when a TMDb Helper handoff has no usable local/live resume point, so a
    recent Trakt pause is available immediately instead of after the next
    service interval. Results are cached briefly to avoid repeated API calls.
    """
    if not (enabled() and sync_enabled() and authorized()):
        return {}
    mt = str(media_type or 'movie').lower()
    is_ep = mt in ('series','show','tv','episode','anime') or (season not in (None,'',0,'0') and episode not in (None,'',0,'0'))
    try:
        wanted_s, wanted_e = int(season or 0), int(episode or 0)
    except Exception:
        wanted_s, wanted_e = 0, 0
    imdb = str(imdb_id or '').lower().replace('imdb:', '').strip()
    if imdb.isdigit(): imdb = 'tt'+imdb
    key = ('ep' if is_ep else 'movie', str(tmdb_id or ''), imdb, str(tvdb_id or ''), wanted_s, wanted_e)
    now = time.time()
    cached = _PROGRESS_LOOKUP_CACHE.get(key)
    if cached and now-cached[0] <= _PROGRESS_LOOKUP_TTL:
        return dict(cached[1])
    path = '/sync/playback/episodes?extended=full' if is_ep else '/sync/playback/movies?extended=full'
    try:
        rows = _request(path, method='GET', auth=True, timeout=12) or []
    except Exception as exc:
        xbmc.log('[DexHub] trakt direct progress lookup failed: %s' % exc, xbmc.LOGWARNING)
        return {}
    best = {}
    for row in rows:
        try:
            item = (row.get('show') or {}) if is_ep else (row.get('movie') or {})
            ids = item.get('ids') or {}
            matched = False
            if tmdb_id and str(ids.get('tmdb') or '') == str(tmdb_id): matched = True
            if not matched and imdb and str(ids.get('imdb') or '').lower() == imdb: matched = True
            if not matched and tvdb_id and str(ids.get('tvdb') or '') == str(tvdb_id): matched = True
            if not matched: continue
            ep = row.get('episode') or {}
            if is_ep and (int(ep.get('season') or 0) != wanted_s or int(ep.get('number') or 0) != wanted_e):
                continue
            progress = float(row.get('progress') or 0.0)
            if not (1.0 < progress < 95.0): continue
            runtime_min = float((ep.get('runtime') if is_ep else item.get('runtime')) or item.get('runtime') or 0.0)
            duration = runtime_min * 60.0 if runtime_min > 0 else 0.0
            position = duration * progress / 100.0 if duration > 0 else 0.0
            updated = _parse_trakt_ts(row.get('paused_at') or row.get('last_watched_at')) or 0
            candidate = {'resume_seconds': position, 'resume_percent': progress, 'duration': duration, 'updated_at': updated, 'source': 'trakt'}
            if not best or updated > int(best.get('updated_at') or 0): best = candidate
        except Exception:
            continue
    _PROGRESS_LOOKUP_CACHE[key] = (now, dict(best))
    return best


# v5.10.286: Remove from Continue Watching removes the title's paused
# playback on Trakt too (DELETE /sync/playback/{id}), or the next progress
# pull (import_progress) brings the card back.
def remove_playback(media_type='movie', imdb_ids=(), tmdb_ids=(), tvdb_ids=()):
    """Delete every Trakt playback entry of one title (every episode of a
    show). Returns how many were deleted; 0 when Trakt is not linked."""
    if not authorized():
        return 0
    _ensure_auth()
    imdb = set()
    for value in imdb_ids or ():
        text = str(value or '').strip().lower().replace('imdb:', '')
        if text.isdigit():
            text = 'tt' + text
        if text.startswith('tt'):
            imdb.add(text)
    tmdb = set(str(v).strip() for v in tmdb_ids or () if str(v or '').strip().isdigit())
    tvdb = set(str(v).strip() for v in tvdb_ids or () if str(v or '').strip().isdigit())
    if not (imdb or tmdb or tvdb):
        return 0
    is_ep = str(media_type or '').lower() in ('series', 'show', 'tv', 'episode', 'anime')
    path = '/sync/playback/episodes' if is_ep else '/sync/playback/movies'
    rows = _request(path, method='GET', auth=True, timeout=12) or []
    deleted = 0
    for row in rows if isinstance(rows, list) else []:
        try:
            item = (row.get('show') or {}) if is_ep else (row.get('movie') or {})
            ids = item.get('ids') or {}
            if not (str(ids.get('imdb') or '').lower() in imdb
                    or str(ids.get('tmdb') or '') in tmdb
                    or str(ids.get('tvdb') or '') in tvdb):
                continue
            if not row.get('id'):
                continue
            try:
                _request('/sync/playback/%s' % int(row['id']), method='DELETE', auth=True, timeout=10)
            except urllib.error.HTTPError as exc:
                if getattr(exc, 'code', 0) != 404:
                    raise
            deleted += 1
        except (TypeError, ValueError):
            continue
    if deleted:
        invalidate_cache('/sync/playback/')
        _PROGRESS_LOOKUP_CACHE.clear()
    return deleted
