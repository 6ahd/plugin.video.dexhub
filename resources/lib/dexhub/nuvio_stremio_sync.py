# -*- coding: utf-8 -*-
"""Native Nuvio + Stremio account sync — no external Hub server required.

This replaces the whole web-Hub architecture with a small, self-contained
module that talks to Nuvio (api.nuvio.tv) and Stremio (api.strem.io) directly
from the addon. Nuvio uses its TV pairing flow by default: Dex Hub shows a Nuvio website QR
code, the user signs in and approves on nuvio.tv, and only the resulting session
tokens are stored locally.  The legacy password method remains internal for
backward compatibility but is not exposed in the UI.

Design mirrors resources/lib/trakt.py:
  * tokens live in profile_path()/nuvio_token.json and stremio_token.json
  * save/clear/refresh helpers, all failures degrade gracefully
  * stdlib urllib only

Everything here was reverse-engineered from Nuvio's open source + APK and
Stremio's official API, and was validated end-to-end in the previous Hub
implementation before being ported to Python.
"""
from .. import profile_scope as _scope
import base64
import json
import os
import time
import secrets
import urllib.request
import urllib.error

try:
    import xbmc
except Exception:
    xbmc = None

from .common import profile_path, addon
from .safe_io import read_json, write_json

# ── endpoints / constants ──────────────────────────────────────────────────
NUVIO_URL = 'https://api.nuvio.tv'
# Public anon JWT (role=anon, exp 2031) shipped in Nuvio's own APK — safe to
# embed; it only permits what Nuvio's row-level-security allows a logged-in
# user to do.
NUVIO_ANON = ('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
              'eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiaWF0IjoxNzgxNTIxMzQ2LCJleHAiOjE5MzkyMDEzNDZ9.'
              'tmQaj682pwzehpqlgCDMnySOqiUvpgRbrE43T4VJpDI')
STREMIO_URL = 'https://api.strem.io'

_NUVIO_TOKEN = os.path.join(profile_path(), 'nuvio_token.json')
_STREMIO_TOKEN = os.path.join(profile_path(), 'stremio_token.json')
_NUVIO_CLIENT_ID_FILE = os.path.join(profile_path(), 'nuvio_sync_client_identity.json')
_NUVIO_RATE_STATE_FILE = os.path.join(profile_path(), 'nuvio_rate_state.json')
# v5.10.120: the account's profiles, kept: a profile list asked again within
# ten minutes comes from here, and while Nuvio answers 429 (or not at all)
# the last list read is used, so the profile switch still works
_NUVIO_PROFILES_FILE = os.path.join(profile_path(), 'nuvio_profiles.json')
_PROFILES_FRESH = 600
_NUVIO_ADDON_URLS_FILE = _scope.ScopedPath('nuvio_enabled_addons.json')

# Be an honest TV-compatible client. NuvioTV's official SyncClientIdentity
# uses a persistent per-install origin id so cloud writes are not echoed back
# to the same client. Dex Hub follows that protocol but does not impersonate
# the official Android application or spoof its package/User-Agent.
_UA = 'DexHub/%s Kodi TV Client' % (addon().getAddonInfo('version') or 'unknown')
_CLIENT_ID_PREFIX = 'dexhub-tv-'
_CLIENT_ID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'


def _log(msg):
    if xbmc is None:
        return
    try:
        xbmc.log('[DexHub][sync] ' + str(msg), xbmc.LOGINFO)
    except Exception:
        pass


def _setting(key, default=''):
    try:
        return (addon().getSetting(key) or default).strip()
    except Exception:
        return default


def _save_nuvio_enabled_addon_urls(rows):
    """Persist Nuvio's exact enabled transport URLs for provider selection.

    Multiple historical URLs can expose the same Stremio manifest.id.  Older
    Dex Hub builds kept all of them and whichever row happened to be first won,
    so a dead legacy hostname could be selected even after Nuvio had moved the
    addon.  The account list is authoritative for Nuvio-backed Home/Collections.
    """
    urls, seen = [], set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw = row.get('manifest_url') or row.get('url') or row.get('transportUrl') or row.get('manifestUrl') or ''
        url = _ensure_manifest_suffix(str(raw or '').strip())
        key = url.lower().rstrip('/')
        if not key.startswith(('http://', 'https://')) or key in seen:
            continue
        seen.add(key)
        urls.append(url)
    try:
        write_json(_NUVIO_ADDON_URLS_FILE, {
            'urls': urls,
            'authoritative': True,
            'saved_at': int(time.time()),
        })
    except Exception:
        pass
    return urls


def _setting_bool(key, default=False):
    v = _setting(key, '')
    if v == '':
        return default
    return v.lower() in ('true', '1', 'yes', 'on')


def _valid_sync_client_id(value):
    value = str(value or '').strip()
    return (16 <= len(value) <= 96 and
            all(ch.isalnum() or ch in '-_' for ch in value))


def _nuvio_client_id():
    """Persistent per-install sync origin identity.

    Mirrors NuvioTV's current SyncClientIdentity contract (stable id,
    32 random lowercase/digit suffix, p_origin_client_id on supported write
    RPCs) while keeping an explicit Dex Hub prefix.
    """
    row = read_json(_NUVIO_CLIENT_ID_FILE, {}) or {}
    stored = str((row or {}).get('client_instance_id') or '').strip()
    if _valid_sync_client_id(stored):
        return stored
    suffix = ''.join(secrets.choice(_CLIENT_ID_ALPHABET) for _ in range(32))
    value = _CLIENT_ID_PREFIX + suffix
    try:
        write_json(_NUVIO_CLIENT_ID_FILE, {
            'client_instance_id': value,
            'client_kind': 'tv',
            'client_name': 'Dex Hub',
            'created_at': int(time.time()),
        })
    except Exception:
        pass
    return value


def _nuvio_rate_state():
    row = read_json(_NUVIO_RATE_STATE_FILE, {}) or {}
    return row if isinstance(row, dict) else {}


def _nuvio_cooldown_remaining():
    try:
        return max(0, int(_nuvio_rate_state().get('blocked_until') or 0) - int(time.time()))
    except Exception:
        return 0


def _set_nuvio_cooldown(code, retry_after=0, reason=''):
    """Persist an account-sync cooldown across Kodi invocations.

    A 429 (and Nuvio/Supabase 503 Too Many Requests/SlowDown) must not be
    retried by every widget/service invocation. Repeated hits escalate up to
    30 minutes; Retry-After always wins when it asks for longer.
    """
    now = int(time.time())
    prev = _nuvio_rate_state()
    last = int(prev.get('last_limited_at') or 0)
    strikes = int(prev.get('strikes') or 0) + 1 if now - last < 3600 else 1
    if int(code or 0) == 429:
        base = 600
    else:
        base = 300
    delay = max(int(float(retry_after or 0)), min(1800, base * (2 ** min(2, strikes - 1))))
    row = {
        'blocked_until': now + delay,
        'last_limited_at': now,
        'strikes': strikes,
        'code': int(code or 0),
        'reason': str(reason or '')[:240],
    }
    try:
        write_json(_NUVIO_RATE_STATE_FILE, row)
    except Exception:
        pass
    _log('Nuvio cooldown armed for %ss after HTTP %s (%s)' % (delay, code, reason or 'rate limit'))
    return delay


def _clear_expired_nuvio_cooldown():
    row = _nuvio_rate_state()
    if not row:
        return
    try:
        if int(row.get('blocked_until') or 0) <= int(time.time()):
            write_json(_NUVIO_RATE_STATE_FILE, {})
    except Exception:
        pass


# ── low-level HTTP ─────────────────────────────────────────────────────────
def _http(url, payload=None, headers=None, method='GET', timeout=10):
    """HTTP helper with pacing + persistent Nuvio account cooldown.

    v5.4.40 deliberately does *not* spoof Nuvio Android TV. Instead it follows
    NuvioTV's sync-origin contract and, once the backend says Too Many
    Requests, stops cloud-sync traffic long enough for the account/IP bucket
    to recover. This also protects nuvio.tv/account in the user's browser from
    Dex Hub continuously consuming the same backend allowance.
    """
    data = None
    hdrs = {'Accept': 'application/json', 'User-Agent': _UA}
    if headers:
        hdrs.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        hdrs['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)

    is_nuvio = str(url).startswith(NUVIO_URL + '/')
    is_nuvio_sync = is_nuvio and '/rest/v1/' in str(url)
    if is_nuvio_sync:
        remaining = _nuvio_cooldown_remaining()
        if remaining > 0:
            raise RuntimeError('Nuvio rate-limit cooldown active (%d min remaining)' % max(1, (remaining + 59) // 60))
        _clear_expired_nuvio_cooldown()

    try:
        from ..ratelimit import limiter, host_of
    except Exception:
        limiter = None
        host_of = None

    last_exc = None
    # Nuvio rate-limit responses are not retried immediately. A generic 502/
    # 504, or a non-rate 503, gets at most one bounded retry.
    max_attempts = 2 if is_nuvio else 3
    for attempt in range(max_attempts):
        try:
            if limiter is not None and host_of is not None:
                limiter.acquire(host_of(url), max_wait=8.0 if is_nuvio else 6.0)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode('utf-8', 'replace')
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            last_exc = e
            try:
                body = e.read().decode('utf-8', 'replace')
                parsed = json.loads(body) if body else {}
            except Exception:
                body = ''
                parsed = {}
            code = int(getattr(e, 'code', 0) or 0)
            msg = (parsed.get('error_description') or parsed.get('msg')
                   or parsed.get('message') or parsed.get('error') or body
                   or ('HTTP %s' % code))
            low = str(msg or '').lower()
            try:
                retry_after = float(e.headers.get('Retry-After') or 0)
            except Exception:
                retry_after = 0.0
            rate_limited = (code == 429 or
                            (code == 503 and any(x in low for x in
                             ('too many requests', 'slowdown', 'rate limit', 'rate-limit'))))
            if is_nuvio_sync and rate_limited:
                delay = _set_nuvio_cooldown(code, retry_after, msg)
                raise RuntimeError('Nuvio busy/rate-limited (HTTP %s). Cloud sync paused for %d min.' %
                                   (code, max(1, (delay + 59) // 60)))
            if code in (502, 503, 504) and attempt < (max_attempts - 1):
                wait = retry_after if retry_after > 0 else (2.0 * (attempt + 1))
                _log('HTTP %s from %s; one bounded retry in %.1fs' %
                     (code, url.split('/')[2], min(5.0, max(1.0, wait))))
                time.sleep(min(5.0, max(1.0, wait)))
                continue
            if is_nuvio_sync and code == 503:
                _set_nuvio_cooldown(code, retry_after or 180, msg)
            raise RuntimeError(str(msg))
        except RuntimeError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < (max_attempts - 1):
                time.sleep(1.0 if is_nuvio else 0.75)
                continue
            raise
    if last_exc:
        raise last_exc
    return {}


def _jwt_sub(access):
    """v5.10.291: the Supabase account id (sub) carried inside the access token."""
    try:
        part = str(access or '').split('.')[1]
        part += '=' * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(part.encode('ascii')).decode('utf-8'))
        return str(claims.get('sub') or '')
    except Exception:
        return ''


# ═══════════════════════════════════════════════════════════════════════════
# NUVIO  (self-hosted Supabase: GoTrue auth + PostgREST RPCs)
# ═══════════════════════════════════════════════════════════════════════════
class Nuvio:
    @staticmethod
    def token():
        return read_json(_NUVIO_TOKEN, {}) or {}

    @staticmethod
    def save_token(data):
        d = dict(data or {})
        d.setdefault('created_at', int(time.time()))
        if not d.get('user_id') and d.get('access_token'):
            d['user_id'] = _jwt_sub(d.get('access_token')) or None
        write_json(_NUVIO_TOKEN, d)

    @staticmethod
    def ensure_identity():
        """v5.10.291: a sign-in whose reply had no user object left user_id
        empty, so choosing a profile asked to sign in again. The id is read
        back from the access token, or from Nuvio's /auth/v1/user."""
        data = Nuvio.token()
        if data.get('user_id') or not data.get('access_token'):
            return data
        uid = _jwt_sub(data.get('access_token'))
        if not uid:
            try:
                fresh = Nuvio._ensure_token()
                body = _http(NUVIO_URL + '/auth/v1/user',
                             headers={'apikey': NUVIO_ANON,
                                      'Authorization': 'Bearer %s' % fresh.get('access_token')},
                             timeout=10)
                uid = str((body or {}).get('id') or '')
                if not data.get('email'):
                    data['email'] = str((body or {}).get('email') or '')
            except Exception as exc:
                _log('nuvio identity lookup failed: %s' % exc)
        if uid:
            latest = Nuvio.token()
            if latest.get('access_token') == data.get('access_token') or not latest.get('user_id'):
                before = _scope.token_key(latest)
                latest['user_id'] = uid
                if data.get('email') and not latest.get('email'):
                    latest['email'] = data['email']
                write_json(_NUVIO_TOKEN, latest)
                # v5.10.292: the files kept while the id was missing ('local')
                # follow the account; the settings stay as they are
                _scope.seed_first_use(before, _scope.token_key(latest), full=True)
                _log('nuvio identity restored from the session')
                return latest
        return Nuvio.token()

    @staticmethod
    def clear():
        # safe_io keeps a .bak snapshot; remove both or logout would silently
        # resurrect the previous session on the next read.
        for path in (_NUVIO_TOKEN, _NUVIO_TOKEN + '.bak'):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except Exception:
                pass
        try:
            from . import nuvio_home_mirror
            nuvio_home_mirror.clear()
        except Exception:
            pass
        try:
            from . import nuvio_profile_prefs
            nuvio_profile_prefs.clear()
        except Exception:
            pass

    @staticmethod
    def is_linked():
        return bool(Nuvio.token().get('access_token'))

    @staticmethod
    def _rpc_with_token(fn, params, token, timeout=12):
        return _http(
            NUVIO_URL + '/rest/v1/rpc/' + fn,
            payload=params or {},
            headers={'apikey': NUVIO_ANON,
                     'Authorization': 'Bearer %s' % token},
            method='POST', timeout=timeout)

    @staticmethod
    def _anonymous_session():
        headers = {
            'apikey': NUVIO_ANON,
            'Authorization': 'Bearer %s' % NUVIO_ANON,
        }
        body = {}
        try:
            body = _http(
                NUVIO_URL + '/auth/v1/signup',
                payload={'data': {'tv_client': 'dexhub-tv', 'sync_client_id': _nuvio_client_id()}},
                headers=headers, method='POST', timeout=12)
        except Exception:
            body = {}
        if not isinstance(body, dict) or not body.get('access_token'):
            body = _http(
                NUVIO_URL + '/auth/v1/token?grant_type=anonymous',
                payload={}, headers=headers, method='POST', timeout=12)
        token = (body or {}).get('access_token')
        if not token:
            raise RuntimeError('Nuvio anonymous TV session failed')
        return str(token)

    @staticmethod
    def start_tv_login(device_name='Dex Hub • TV (Kodi)'):
        """Start Nuvio's TV pairing flow; no user password enters Dex Hub."""
        anon = Nuvio._anonymous_session()
        nonce = base64.urlsafe_b64encode(os.urandom(24)).decode('ascii').rstrip('=')
        rows = Nuvio._rpc_with_token(
            'start_tv_login_session',
            {
                'p_device_nonce': nonce,
                'p_redirect_base_url': 'https://nuvio.tv/tv-login',
                'p_device_name': device_name,
            }, anon, timeout=15)
        row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
        code = str((row or {}).get('code') or '').strip()
        url = str((row or {}).get('qr_content') or (row or {}).get('web_url') or '').strip()
        if not url and code:
            url = 'https://nuvio.tv/tv-login?code=' + code
        if not code or not url:
            raise RuntimeError('Nuvio did not return a TV login code')
        try:
            poll = float((row or {}).get('poll_interval_seconds') or 3)
        except Exception:
            poll = 3.0
        return {
            'code': code, 'url': url, 'nonce': nonce, 'anon_token': anon,
            'poll_interval': max(2.0, min(5.0, poll)),
        }

    @staticmethod
    def poll_tv_login(state):
        state = dict(state or {})
        code = str(state.get('code') or '')
        nonce = str(state.get('nonce') or '')
        anon = str(state.get('anon_token') or '')
        if not code or not nonce or not anon:
            return {'status': 'none'}
        rows = Nuvio._rpc_with_token(
            'poll_tv_login_session',
            {'p_code': code, 'p_device_nonce': nonce}, anon, timeout=12)
        row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
        status = str((row or {}).get('status') or 'pending').strip().lower()
        if status not in ('approved', 'authorized', 'completed'):
            return {'status': status}
        body = _http(
            NUVIO_URL + '/functions/v1/tv-logins-exchange',
            payload={'code': code, 'device_nonce': nonce},
            headers={'apikey': NUVIO_ANON, 'Authorization': 'Bearer %s' % anon},
            method='POST', timeout=15)
        session = (body or {}).get('session') or {}
        access = (body or {}).get('access_token') or session.get('access_token')
        refresh = (body or {}).get('refresh_token') or session.get('refresh_token')
        user = (body or {}).get('user') or session.get('user') or {}
        if not access:
            return {'status': 'exchange_failed', 'error': 'no access_token'}
        Nuvio.save_token({
            'access_token': access,
            'refresh_token': refresh,
            'expires_in': int((body or {}).get('expires_in') or session.get('expires_in') or 3600),
            'created_at': int(time.time()),
            'user_id': (user or {}).get('id'),
            'email': (user or {}).get('email') or '',
        })
        # Resolve the default profile immediately so subsequent sync is ready.
        try:
            Nuvio._profile_id()
        except Exception:
            pass
        return {'status': 'ok'}

    @staticmethod
    def profiles(fresh=False):
        """The account's profiles (v5.10.120: kept ten minutes, and the last
        list read stands in while Nuvio is rate limited or unreachable)."""
        owner = str(Nuvio.ensure_identity().get('user_id') or '')
        saved = read_json(_NUVIO_PROFILES_FILE, {}) or {}
        kept = saved.get('rows') if isinstance(saved, dict) and str(saved.get('user') or '') == owner else None
        kept = [r for r in kept if isinstance(r, dict)] if isinstance(kept, list) else None
        if kept and not fresh:
            try:
                if time.time() - float(saved.get('t') or 0) < _PROFILES_FRESH:
                    return kept
            except Exception:
                pass
        try:
            rows = Nuvio._rpc('sync_pull_profiles', {})
        except Exception as exc:
            if kept:
                _log('profiles: Nuvio did not answer (%s); the saved list is used' % exc)
                return kept
            raise
        rows = [r for r in (rows or []) if isinstance(r, dict)] if isinstance(rows, list) else []
        if rows:
            try:
                write_json(_NUVIO_PROFILES_FILE, {'t': time.time(), 'user': owner, 'rows': rows})
            except Exception:
                pass
        return rows

    @staticmethod
    def select_profile(row):
        if not isinstance(row, dict):
            return False
        try:
            idx = int(row.get('profile_index', row.get('profileIndex', row.get('index'))))
            if idx <= 0:
                return False
        except (ValueError, TypeError):
            return False
        if xbmc is not None and xbmc.Player().isPlaying():
            raise RuntimeError('Stop playback before switching profiles')
        data = Nuvio.ensure_identity()
        if not data.get('user_id'):
            raise RuntimeError('Nuvio account identity is missing; sign in again')
        _scope.initialize()
        data.update(profile_index=idx, profile_name=str(row.get('name') or row.get('title') or ('Profile %s' % idx)),
                    profile_id=row.get('id') or '',
                    profile_avatar=str(row.get('avatar_url') or row.get('avatar') or row.get('image') or ''))
        previous = Nuvio.token()
        if _scope.token_key(previous) == _scope.token_key(data):
            Nuvio.save_token(data)
            return True
        marker = os.path.join(_scope.root(), 'profile_transition.json')
        with open(marker, 'x', encoding='utf-8') as handle:
            json.dump({'previous': _scope.token_key(previous), 'next': _scope.token_key(data),
                       'pid': os.getpid(), 'at': time.time()}, handle)
        # v5.10.286 (issue #12): a profile's first visit starts from the current Home rows
        # v5.10.292: coming from the account before any profile was chosen
        # (no profile index), the profile takes everything: same person
        try:
            came_unselected = int(previous.get('profile_index') or 0) <= 0
        except (TypeError, ValueError):
            came_unselected = True
        _scope.seed_first_use(_scope.token_key(previous), _scope.token_key(data), full=came_unselected)
        try:
            _scope.switch_settings(_scope.token_key(data))
            Nuvio.save_token(data)
        except Exception:
            saved = _scope._read(_scope.path('preferences.json', scope=_scope.token_key(previous)), {})
            for sid, value in saved.items():
                addon().setSetting(sid, str(value))
            raise
        finally:
            os.unlink(marker)
        try:
            from .. import settings_cache
            settings_cache.invalidate()
            import xbmcgui
            home = xbmcgui.Window(10000)
            home.setProperty('dhs.ready', '')
            home.setProperty('dexhub.sync.want_full', '1')
            home.setProperty('dhs.republish', '%s profile' % time.time())
        except Exception:
            pass
        return True

    @staticmethod
    def profile_name():
        return str(Nuvio.token().get('profile_name') or '')

    @staticmethod
    def login(email, password):
        """Exchange email+password for a Supabase session. Stores the token."""
        body = _http(
            NUVIO_URL + '/auth/v1/token?grant_type=password',
            payload={'email': email, 'password': password},
            headers={'apikey': NUVIO_ANON}, method='POST')
        if not body.get('access_token'):
            raise RuntimeError('login failed: no access_token')
        Nuvio.save_token({
            'access_token': body.get('access_token'),
            'refresh_token': body.get('refresh_token'),
            'expires_in': int(body.get('expires_in') or 3600),
            'created_at': int(time.time()),
            'user_id': (body.get('user') or {}).get('id'),
            'email': email,
        })
        return True

    @staticmethod
    def _ensure_token():
        data = Nuvio.token()
        if not data.get('access_token'):
            raise RuntimeError('not linked to Nuvio')
        created = int(data.get('created_at') or 0)
        exp = int(data.get('expires_in') or 3600)
        if (created + exp - 60) > int(time.time()):
            return data
        # refresh
        if not data.get('refresh_token'):
            return data
        try:
            body = _http(
                NUVIO_URL + '/auth/v1/token?grant_type=refresh_token',
                payload={'refresh_token': data.get('refresh_token')},
                headers={'apikey': NUVIO_ANON}, method='POST')
            if body.get('access_token'):
                latest = Nuvio.token()
                if not latest.get('access_token') or latest.get('user_id') != data.get('user_id'):
                    raise RuntimeError('Account changed while refreshing Nuvio')
                if latest.get('refresh_token') != data.get('refresh_token'):
                    return latest
                # Refresh credentials only. Profile selection/name/avatar must
                # survive token renewal and a concurrent profile switch.
                latest.update(access_token=body['access_token'],
                    refresh_token=body.get('refresh_token') or latest.get('refresh_token'),
                    expires_in=int(body.get('expires_in') or 3600), created_at=int(time.time()))
                Nuvio.save_token(latest)
                return latest

        except Exception as e:
            _log('nuvio refresh failed: %s' % e)
        return data

    @staticmethod
    def _rpc(fn, params, timeout=9):
        _scope.require_current()
        data = Nuvio._ensure_token()
        _scope.require_current()
        result = _http(
            NUVIO_URL + '/rest/v1/rpc/' + fn,
            payload=params or {},
            headers={'apikey': NUVIO_ANON,
                     'Authorization': 'Bearer %s' % data.get('access_token')},
            method='POST', timeout=timeout)
        _scope.require_current()
        return result

    @staticmethod
    def _profile_id():
        data = Nuvio.token()
        try:
            index = int(data.get('profile_index'))
            if index > 0:
                return index
        except (ValueError, TypeError):
            pass
        raise RuntimeError('Select a Nuvio profile before synchronizing')

    # ── mappers: DexHub ⇄ Nuvio ──
    @staticmethod
    def _addons_to_nuvio(providers):
        out, seen = [], set()
        for p in providers or []:
            url = (p.get('manifest_url') or p.get('transportUrl') or p.get('url') or '').strip()
            if not url.lower().startswith(('http://', 'https://')):
                continue
            k = url.lower()
            if k in seen:
                continue
            seen.add(k)
            # Exact AddonPushItem shape from Nuvio's client: url/name/enabled/sort_order.
            out.append({
                'url': _ensure_manifest_suffix(url),
                'name': str(p.get('name') or ''),
                'enabled': p.get('enabled') is not False,
                'sort_order': len(out),
            })
        return out

    @staticmethod
    def _addons_to_dex(addons):
        out, seen = [], set()
        for a in addons or []:
            url = (a.get('url') or a.get('transportUrl') or a.get('manifestUrl')
                   or a.get('manifest_url') or '').strip()
            if not url.lower().startswith(('http://', 'https://')):
                continue
            k = url.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append({'manifest_url': url, 'name': str(a.get('name') or '')})
        return out

    @staticmethod
    def _progress_to_nuvio(rows):
        out = []
        for r in rows or []:
            cid = r.get('imdb_id') or r.get('tmdb_id') or r.get('canonical_id') or ''
            if not cid:
                continue
            is_series = (r.get('media_type') in ('tv', 'series') or r.get('season') is not None)
            position = float(r.get('position') or 0)
            duration = float(r.get('duration') or 0)
            if r.get('finished'):
                # done: a full position, so Nuvio drops it from Continue Watching
                duration = duration if duration > 0 else max(position, 1.0)
                position = duration
            out.append({
                'content_id': str(cid),
                'content_type': 'series' if is_series else 'movie',
                'video_id': str(r.get('video_id') or cid),
                'season': int(r['season']) if r.get('season') is not None else None,
                'episode': int(r['episode']) if r.get('episode') is not None else None,
                'position': int(round(position * 1000)),
                'duration': int(round(duration * 1000)),
                'last_watched': int(r.get('updated_at') or time.time()),
                'progress_key': r.get('progress_key') or ('%s:%s' % (cid, r.get('video_id') or '')),
            })
        return out

    @staticmethod
    def _progress_to_dex(entries):
        from .stremio_contract import number, epoch
        out = []
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            try:
                cid = str(entry.get('content_id') or '')
                if not cid:
                    continue
                position, duration = number(entry.get('position')) / 1000, number(entry.get('duration')) / 1000
                stamp = entry.get('last_watched') or 0
                stamp = epoch(stamp) if isinstance(stamp, str) and 'T' in stamp else number(stamp)
                if stamp > 100000000000:
                    stamp /= 1000
                kind = 'series' if entry.get('content_type') in ('series', 'tv', 'show', 'anime') else 'movie'
                out.append({'imdb_id': cid if cid.startswith('tt') else '',
                    'tmdb_id': cid.split(':', 1)[1] if cid.startswith('tmdb:') else '',
                    'canonical_id': cid, 'media_type': kind,
                    'video_id': str(entry.get('video_id') or cid),
                    'season': entry.get('season'), 'episode': entry.get('episode'),
                    'position': position, 'duration': duration,
                    'percent': _percent_of(position, duration), 'updated_at': int(stamp),
                    'progress_key': entry.get('progress_key') or '',
                    'title': str(entry.get('title') or entry.get('name') or ''),
                    'poster': str(entry.get('poster') or ''),
                    'background': str(entry.get('background') or ''),
                    'event_type': 'account_sync'})
            except (TypeError, ValueError, OverflowError):
                continue
        if entries and not out:
            raise RuntimeError('Nuvio progress contained no valid entries; previous progress retained')
        return out


    @staticmethod
    def _rest_get(path, timeout=8):
        """Direct PostgREST table read — same access path Nuvio's own client uses."""
        data = Nuvio._ensure_token()
        return _http(
            NUVIO_URL + path,
            headers={'apikey': NUVIO_ANON,
                     'Authorization': 'Bearer %s' % data.get('access_token')},
            method='GET', timeout=timeout)

    @staticmethod
    def pull_addons_exact():
        """Return Nuvio's enabled add-ons in the exact account order.

        Nuvio's own clients read the addons table ordered by sort_order.  Keep
        this separate from sync_addons(), whose merge semantics intentionally
        include Dex Hub-only providers.
        """
        pid = Nuvio._profile_id()
        rows = Nuvio._rest_get(
            '/rest/v1/addons?profile_id=eq.%d'
            '&select=url,name,enabled,sort_order&order=sort_order.asc' % pid)
        out = []
        for idx, r in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(r, dict) or r.get('enabled') is False:
                continue
            url = _ensure_manifest_suffix(str(r.get('url') or '').strip())
            if not url.lower().startswith(('http://', 'https://')):
                continue
            try:
                order = int(r.get('sort_order'))
            except Exception:
                order = idx
            out.append({
                'manifest_url': url,
                'name': str(r.get('name') or ''),
                'enabled': True,
                'sort_order': order,
            })
        out.sort(key=lambda row: int(row.get('sort_order') or 0))
        return out

    @staticmethod
    def pull_profile_settings_blob(platform='tv'):
        """Pull Nuvio's non-secret profile settings (TMDb display prefs etc.)."""
        pid = Nuvio._profile_id()
        rows = Nuvio._rpc('sync_pull_profile_settings_blob', {
            'p_profile_id': pid, 'p_platform': str(platform or 'tv'),
        }, timeout=9)
        row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
        if not isinstance(row, dict):
            return {}
        raw = (row.get('settings_json') or row.get('settingsJson') or
               row.get('settings') or row.get('payload') or {})
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        try:
            from . import nuvio_profile_prefs
            nuvio_profile_prefs.save_blob(
                raw, platform=platform,
                updated_at=row.get('updated_at') or row.get('updatedAt') or '',
            )
        except Exception as exc:
            _log('nuvio profile settings cache failed: %s' % exc)
        return raw

    @staticmethod
    def pull_home_catalog_settings():
        """Pull Home using Nuvio TV's current three-surface selection rule.

        Home order and the standalone hide-unreleased flag do not necessarily
        come from the same row during migration. Nuvio checks shared, TV and
        mobile, lets a populated shared row win, otherwise uses the newest
        populated legacy row, and takes hide-unreleased from the newest row
        that explicitly contains it. Empty Home payloads preserve local Home.
        """
        pid = Nuvio._profile_id()

        def fetch(platform):
            try:
                rows = Nuvio._rpc('sync_pull_home_catalog_settings', {
                    'p_profile_id': pid, 'p_platform': platform,
                }, timeout=9)
            except Exception as exc:
                _log('nuvio home settings pull %s failed: %s' % (platform, exc))
                return None
            row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})
            if not isinstance(row, dict):
                return None
            raw = (row.get('settings_json') or row.get('settingsJson') or
                   row.get('settings') or row.get('payload') or {})
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}
            if isinstance(raw, list):
                raw = {'items': raw}
            if not isinstance(raw, dict):
                raw = {}
            items = raw.get('items')
            if not isinstance(items, list):
                items = raw.get('catalogs') if isinstance(raw.get('catalogs'), list) else []
            return {
                'platform': platform, 'payload': raw,
                'updated_at': str(row.get('updated_at') or row.get('updatedAt') or ''),
                'has_items': bool(isinstance(items, list) and items),
                'has_hide_unreleased': bool(
                    'hide_unreleased_content' in raw or
                    'hideUnreleasedContent' in raw),
            }

        rows = []
        for platform in ('home_catalog_shared', 'tv', 'mobile'):
            remote_row = fetch(platform)
            if remote_row:
                rows.append(remote_row)
        try:
            from . import nuvio_home_mirror
            current_hide = bool((nuvio_home_mirror.load() or {}).get(
                'hide_unreleased_content', False))
        except Exception:
            current_hide = False
        from . import nuvio_contract
        return nuvio_contract.select_home_snapshot(
            rows, current_hide_unreleased=current_hide)

    @staticmethod
    def sync_addons(providers, direction='both'):
        """Sync installed addons with the Nuvio account.

        Verified against Nuvio's open source (NuvioMobile AddonRepository.kt):
          * PULL: there is NO ``sync_pull_addons`` RPC on api.nuvio.tv — the
            old probe 404'd every time. Nuvio's app reads the ``addons`` table
            directly:  GET /rest/v1/addons?profile_id=eq.N&order=sort_order.asc
            Rows may store the base URL, so ``/manifest.json`` is appended when
            missing (mirrors Nuvio's ensureManifestSuffix).
          * PUSH: RPC ``sync_push_addons(p_profile_id, p_addons,
            p_origin_client_id)`` with rows shaped {url, name, enabled,
            sort_order}. Nuvio always pushes the full list (replace-set
            semantics), so we MUST pull + merge before pushing — the old
            push-first order would have wiped Nuvio-only addons.
        """
        pid = Nuvio._profile_id()
        merged = list(providers or [])
        if direction != 'push':
            rows = Nuvio._rest_get(
                '/rest/v1/addons?profile_id=eq.%d'
                '&select=url,name,enabled,sort_order&order=sort_order.asc' % pid)
            remote_dex, disabled = [], 0
            for r in (rows if isinstance(rows, list) else []):
                if not isinstance(r, dict):
                    continue
                url = _ensure_manifest_suffix(str(r.get('url') or '').strip())
                if not url.lower().startswith(('http://', 'https://')):
                    continue
                if r.get('enabled') is False:
                    disabled += 1
                    continue
                remote_dex.append({'manifest_url': url, 'name': str(r.get('name') or '')})
            _save_nuvio_enabled_addon_urls(remote_dex)
            _log('nuvio addons pull: %d enabled, %d disabled skipped (profile %s)'
                 % (len(remote_dex), disabled, pid))
            merged = _merge_by_url(merged, remote_dex)
        if direction != 'pull':
            addons = Nuvio._addons_to_nuvio(merged)
            if addons:
                Nuvio._rpc('sync_push_addons', {
                    'p_profile_id': pid, 'p_addons': addons,
                    'p_origin_client_id': _nuvio_client_id()})
                _log('nuvio addons push: %d rows' % len(addons))
        return merged

    @staticmethod
    def _decode_collections_pull(rows):
        """Decode ``sync_pull_collections`` without altering Nuvio JSON.

        Nuvio clients currently decode the first blob's ``collections_json``
        as a JSON array, but Supabase/client layers may expose that field as
        snake_case, camelCase, an already-decoded list, or a JSON string.
        Older test/profile exports can also return the collection array
        directly. Accept all of those shapes and reject unrelated wrapper rows.
        """
        def decode_jsonish(value):
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    return parsed if isinstance(parsed, list) else []
                except Exception:
                    return []
            return []

        # Direct native collection array: [{id,title,folders:[...]}]
        if isinstance(rows, list) and rows and all(
                isinstance(r, dict) and isinstance(r.get('folders'), list) for r in rows):
            return list(rows)

        wrappers = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
        for wrapper in wrappers:
            if not isinstance(wrapper, dict):
                continue
            for key in ('collections_json', 'collectionsJson', 'collections', 'items', 'data'):
                if key not in wrapper:
                    continue
                value = wrapper.get(key)
                # Some generic wrappers carry {data:{collections_json:[...]}}.
                if isinstance(value, dict):
                    for nested_key in ('collections_json', 'collectionsJson', 'collections', 'items'):
                        if nested_key in value:
                            decoded = decode_jsonish(value.get(nested_key))
                            if decoded:
                                return [r for r in decoded if isinstance(r, dict)]
                decoded = decode_jsonish(value)
                if decoded:
                    return [r for r in decoded if isinstance(r, dict)]
        return []

    @staticmethod
    def pull_collections():
        """Pull the authoritative native collection list from Nuvio only.

        Empty remains a valid result and never deletes the last local cloud
        snapshot; ``upsert_nuvio_cloud`` is deliberately no-op on empty input.
        """
        pid = Nuvio._profile_id()
        rows = Nuvio._rpc('sync_pull_collections', {'p_profile_id': pid})
        remote = Nuvio._decode_collections_pull(rows)
        if not remote and rows:
            try:
                shape = type(rows).__name__
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    shape += ':' + ','.join(sorted(str(k) for k in rows[0].keys())[:8])
                elif isinstance(rows, dict):
                    shape += ':' + ','.join(sorted(str(k) for k in rows.keys())[:8])
                _log('nuvio collections pull decoded empty from non-empty response (%s)' % shape)
            except Exception:
                pass
        _log('nuvio collections pull: %d top-level collection(s)' % len(remote))
        return remote

    @staticmethod
    def _collections_to_nuvio(collections):
        """Return only data that is safe for sync_push_collections.

        Dex Hub set rows use ``entries[]`` and are NOT Nuvio's schema.  The
        old code pushed those rows verbatim during Two-way sync, which could
        overwrite a valid Nuvio blob with data Nuvio cannot decode.  Only raw
        native groups or the preserved ``nuvio_raw`` cloud snapshot qualify.
        """
        out, seen = [], set()
        for row in collections or []:
            candidates = []
            if isinstance(row, dict) and isinstance(row.get('folders'), list):
                candidates = [row]
            elif isinstance(row, dict) and isinstance(row.get('nuvio_raw'), list):
                candidates = [x for x in row.get('nuvio_raw') if isinstance(x, dict) and isinstance(x.get('folders'), list)]
            for item in candidates:
                key = str(item.get('id') or item.get('title') or item.get('name') or '')
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                out.append(item)
        return out

    @staticmethod
    def sync_collections(collections, direction='both'):
        """Nuvio collection sync with cloud-as-source-of-truth semantics.

        Pull and Two-way modes always pull first and never echo Dex Hub's
        normalized set schema back to Nuvio.  Explicit Upload-only remains
        available, but it sends only a preserved/native Nuvio collection JSON.
        """
        pid = Nuvio._profile_id()
        if direction != 'push':
            # Remote is authoritative.  Do not merge Dex Hub backup rows into
            # this payload; writeback preserves local sets independently.
            return Nuvio.pull_collections()
        native = Nuvio._collections_to_nuvio(collections)
        if not native:
            _log('nuvio collections push skipped: no native Nuvio collection shape available')
            return list(collections or [])
        Nuvio._rpc('sync_push_collections',
                   {'p_profile_id': pid, 'p_collections_json': native,
                    'p_origin_client_id': _nuvio_client_id()})
        _log('nuvio collections push: %d top-level collection(s)' % len(native))
        return native

    # ── mappers: DexHub favourites ⇄ Nuvio saved library ──
    # Contract verified against NuvioWeb (js/core/profile/
    # savedLibrarySyncService.js): RPCs sync_pull_library(p_profile_id,
    # p_limit, p_offset) and sync_push_library(p_profile_id, p_items), with
    # rows shaped content_id / content_type / name / poster / poster_shape /
    # background / description / release_info / imdb_rating / genres /
    # addon_base_url / added_at (epoch ms).
    _LIB_PAGE = 500

    @staticmethod
    def _library_to_nuvio(rows):
        out, seen = [], set()
        for r in rows or []:
            cid = str((r or {}).get('canonical_id') or '').strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            mt = str(r.get('media_type') or 'movie').strip().lower()
            try:
                added = int(float(r.get('added_at') or 0)) * 1000
            except Exception:
                added = 0
            name = str(r.get('title') or '').strip()
            try:
                # v5.10.103: never push a placeholder or an id as the name to
                # the account; a row without one gets its real name first
                from .. import continue_names as _names
                if _names.looks_missing(name) or name == cid:
                    name = _names.name_for(
                        {'canonical_id': cid}, is_episode=mt in ('series', 'show', 'tv', 'anime')) or ''
            except Exception:
                pass
            out.append({
                'content_id': cid,
                'content_type': 'series' if mt in ('series', 'show', 'tv', 'anime') else 'movie',
                'name': name,
                'poster': r.get('poster') or None,
                'poster_shape': 'POSTER',
                'background': r.get('background') or None,
                'description': r.get('plot') or '',
                'release_info': str(r.get('year') or '') or '',
                'imdb_rating': None,
                'genres': [],
                'addon_base_url': None,
                'added_at': added or int(time.time() * 1000),
            })
        return out

    @staticmethod
    def _library_to_dex(rows):
        out = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            cid = r.get('content_id') or r.get('contentId') or r.get('id') or ''
            if not cid:
                continue
            ctype = str(r.get('content_type') or r.get('contentType') or 'movie').lower()
            year = str(r.get('release_info') or r.get('releaseInfo') or '')[:4]
            added = r.get('added_at') or r.get('addedAt') or 0
            try:
                added = float(added)
                added = int(added / 1000 if added > 10 ** 11 else added)
            except (ValueError, TypeError):
                added = int(_parse_ts(added))
            out.append({
                'canonical_id': str(cid),
                'media_type': 'series' if ctype in ('series', 'show', 'tv') else 'movie',
                # v5.10.103: no 'Untitled' placeholder; the listing finds the name
                'title': r.get('name') or r.get('title') or '',
                'poster': r.get('poster') or '',
                'background': r.get('background') or '',
                'plot': r.get('description') or '',
                'year': int(year) if year.isdigit() else 0,
                'added_at': added,
            })
        return out

    @staticmethod
    def sync_library(rows, direction='both'):
        """Sync the saved library (DexHub favourites) with Nuvio.

        v4.8.1: Nuvio's own clients keep the saved library in this RPC pair,
        so a title added on the phone shows up in DexHub's favourites and
        the reverse. Pull is paged exactly like NuvioWeb does.
        """
        pid = Nuvio._profile_id()
        # Account watchlists stay independent. Only locally saved favorites
        # are eligible for a two-way Nuvio push. Nuvio's own cached rows are
        # included on explicit push-only, and otherwise come from this pull.
        merged = [r for r in (rows or [])
                  if 'local' in (r.get('sources') or [r.get('source') or 'local'])
                  or (direction == 'push' and 'nuvio' in
                      (r.get('sources') or [r.get('source') or 'local']))]
        local_for_push = list(merged)
        if direction != 'push':
            remote_rows, offset = [], 0
            for _page in range(20):          # 10k items ceiling, same as web
                page = Nuvio._rpc('sync_pull_library', {
                    'p_profile_id': pid, 'p_limit': Nuvio._LIB_PAGE,
                    'p_offset': offset})
                if not isinstance(page, list):
                    raise RuntimeError('Invalid Nuvio library snapshot')
                batch = page
                remote_rows.extend(batch)
                if len(batch) < Nuvio._LIB_PAGE:
                    break
                offset += Nuvio._LIB_PAGE
            else:
                raise RuntimeError('Nuvio library is incomplete; previous library retained')
            _scope.require_current()
            remote_dex = Nuvio._library_to_dex(remote_rows)
            # v5.10.241: the same library as last time is not written again
            # (every sync deleted and wrote back every title, and pushed them
            # all: "library +36" every half hour all night)
            state = _load_sync_state()
            fp_remote = _fingerprint(remote_rows)
            if not fp_remote or state.get('fp_nuvio_lib_remote') != fp_remote:
                from .. import favorites_store
                favorites_store.replace_source_mirror('nuvio', remote_dex)
                _log('nuvio library pull: %d item(s)' % len(remote_dex))
            else:
                _log('nuvio library pull: %d item(s), unchanged' % len(remote_dex))
            merged = _merge_library(merged, remote_dex)
        if direction != 'pull':
            items = Nuvio._library_to_nuvio(local_for_push)
            if items:
                state = _load_sync_state()
                fp_push = _fingerprint(items)
                if not fp_push or state.get('fp_nuvio_lib_push') != fp_push:
                    Nuvio._rpc('sync_push_library', {'p_profile_id': pid, 'p_items': items})
                    _log('nuvio library push: %d item(s)' % len(items))
                    state['fp_nuvio_lib_push'] = fp_push
                    _save_sync_state(state)
        if direction != 'push':
            state = _load_sync_state()
            state['fp_nuvio_lib_remote'] = fp_remote
            _save_sync_state(state)
        return merged

    @staticmethod
    def sync_progress(rows, direction='both'):
        pid = Nuvio._profile_id()
        merged = rows or []
        if direction != 'pull':
            entries = Nuvio._progress_to_nuvio(rows)
            if entries:
                Nuvio._rpc('sync_push_watch_progress', {
                    'p_profile_id': pid, 'p_entries': entries,
                    'p_origin_client_id': _nuvio_client_id()})
        if direction != 'push':
            remote = Nuvio._rpc('sync_pull_watch_progress', {'p_profile_id': pid})
            if not isinstance(remote, list):
                raise RuntimeError('Invalid Nuvio progress snapshot')
            remote_dex = Nuvio._progress_to_dex(remote)
            merged = _merge_progress(rows, remote_dex)
        return merged


# ═══════════════════════════════════════════════════════════════════════════
# STREMIO  (official API: POST /api/<method>)
# ═══════════════════════════════════════════════════════════════════════════
class Stremio:
    LIB = 'libraryItem'

    @staticmethod
    def token():
        return read_json(_STREMIO_TOKEN, {}) or {}

    @staticmethod
    def save_token(data):
        write_json(_STREMIO_TOKEN, dict(data or {}))

    @staticmethod
    def clear():
        for path in (_STREMIO_TOKEN, _STREMIO_TOKEN + '.bak'):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except Exception:
                pass

    @staticmethod
    def is_linked():
        return bool(Stremio.token().get('authKey'))

    @staticmethod
    def _api(method, params):
        _scope.require_current()
        if method == 'addonCollectionSet':
            raise RuntimeError('Stremio add-on sync is read-only')
        if method == 'datastorePut' and not _setting_bool('stremio_progress_write_opt_in', False):
            raise RuntimeError('Enable Stremio progress uploads explicitly before writing')
        if method == 'datastorePut':
            from .stremio_contract import validate
            changes = (params or {}).get('changes')
            if not isinstance(changes, list) or not all(validate(item) for item in changes):
                raise RuntimeError('Invalid Stremio library write refused')
        body = _http(STREMIO_URL + '/api/' + method, payload=params or {}, method='POST')
        if isinstance(body, dict) and body.get('error'):
            err = body['error']
            msg = err.get('message') if isinstance(err, dict) else str(err)
            raise RuntimeError('Stremio %s: %s' % (method, msg))
        return body.get('result') if isinstance(body, dict) else None

    @staticmethod
    def login(email, password):
        result = Stremio._api('login', {'email': email, 'password': password})
        if not result or not result.get('authKey'):
            raise RuntimeError('login failed: no authKey')
        Stremio.save_token({
            'authKey': result.get('authKey'),
            'user_id': (result.get('user') or {}).get('_id'),
            'email': email,
            'created_at': int(time.time()),
        })
        return True

    @staticmethod
    def _auth_key():
        data = Stremio.token()
        if not data.get('authKey'):
            raise RuntimeError('not linked to Stremio')
        return data['authKey']

    # ── mappers ──
    @staticmethod
    def _addons_to_dex(addons):
        out = []
        for a in addons or []:
            manifest = a.get('manifest') or {}
            url = a.get('transportUrl') or manifest.get('url') or ''
            if url:
                out.append({'manifest_url': url, 'name': manifest.get('name') or a.get('transportName') or ''})
        return out

    @staticmethod
    def _fetch_manifest(url):
        try:
            return _http(url, method='GET', timeout=12)
        except Exception:
            return None

    @staticmethod
    def _library_to_dex(items):
        from .stremio_contract import number, epoch
        out = []
        for item in items or []:
            if not isinstance(item, dict) or item.get('removed'):
                continue
            try:
                state = item.get('state') or {}
                if not isinstance(state, dict):
                    continue
                duration, position = number(state.get('duration')), number(state.get('timeOffset'))
                cid = str(item.get('_id') or '')
                if not cid or not (duration or position):
                    continue
                series = item.get('type') == 'series'
                video = str(state.get('video_id') or cid)
                season, episode = state.get('season'), state.get('episode')
                if series and (season is None or episode is None):
                    parts = video.rsplit(':', 2)
                    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                        season, episode = int(parts[1]), int(parts[2])
                out.append({'imdb_id': cid if cid.startswith('tt') else '',
                    'tmdb_id': cid.split(':', 1)[1] if cid.startswith('tmdb:') else '',
                    'canonical_id': cid, 'media_type': 'series' if series else 'movie',
                    'title': str(item.get('name') or ''), 'poster': str(item.get('poster') or ''),
                    'background': str(item.get('background') or ''), 'video_id': video,
                    'season': season if series else None, 'episode': episode if series else None,
                    'position': position / 1000, 'duration': duration / 1000,
                    'percent': _percent_of(position, duration),
                    'updated_at': int(epoch(item.get('_mtime'))), 'event_type': 'account_sync'})
            except (ValueError, TypeError, OverflowError):
                continue
        if items and not out and any(isinstance(i, dict) and not i.get('removed') and isinstance(i.get('state'), dict) and i['state'].get('timeOffset') for i in items):
            raise RuntimeError('Stremio progress is invalid; previous progress retained')
        return out


    @staticmethod
    def _progress_to_stremio(rows, existing_by_id):
        from .stremio_contract import changes
        return changes(rows, existing_by_id)

    # ── high-level ops ──
    @staticmethod
    def sync_addons(providers, direction='both'):
        key = Stremio._auth_key()
        remote = Stremio._api('addonCollectionGet', {'authKey': key, 'update': True})
        if not isinstance(remote, dict) or not isinstance(remote.get('addons'), list):
            raise RuntimeError('Invalid Stremio add-on snapshot')
        return _merge_by_url(providers, Stremio._addons_to_dex(remote['addons']))

    @staticmethod
    def sync_progress(rows, direction='both'):
        if not _setting_bool('stremio_progress_write_opt_in', False):
            direction = 'pull'
        key = Stremio._auth_key()
        merged = rows or []
        items = Stremio._api('datastoreGet', {'authKey': key, 'collection': Stremio.LIB, 'all': True}) or []
        by_id = {}
        for it in items:
            if it and it.get('_id'):
                by_id[it['_id']] = it
        if direction != 'push':
            merged = _merge_progress(rows, Stremio._library_to_dex(items))
        if direction != 'pull':
            changes = Stremio._progress_to_stremio(rows, by_id)
            # v4.4.2: never push a datetime stremio-core would choke on —
            # this also stops old poisoned _ctime values from re-propagating
            # through the dict(prev) clone path.
            changes = [_sanitize_stremio_item(c)[0] for c in (changes or [])]
            if changes:
                Stremio._api('datastorePut', {'authKey': key, 'collection': Stremio.LIB, 'changes': changes})
        return merged

    @staticmethod
    def repair_library():
        """v4.4.2: scan the entire Stremio cloud library and rewrite every
        item whose datetime fields stremio-core cannot parse. Heals libraries
        poisoned by older writers regardless of which app wrote the value.
        Returns (scanned, repaired)."""
        key = Stremio._auth_key()
        items = Stremio._api('datastoreGet', {'authKey': key, 'collection': Stremio.LIB, 'all': True}) or []
        fixes = []
        for it in items:
            fixed, changed = _sanitize_stremio_item(it)
            if changed:
                fixes.append(fixed)
        if fixes:
            Stremio._api('datastorePut', {'authKey': key, 'collection': Stremio.LIB, 'changes': fixes})
        return len(items), len(fixes)


# ── merge helpers (shared) ─────────────────────────────────────────────────
def _ensure_manifest_suffix(url):
    """Mirror Nuvio's ensureManifestSuffix: `addons` table rows may store the
    base addon URL without the trailing /manifest.json."""
    if not url:
        return url
    path, sep, query = url.partition('?')
    path = path.rstrip('/')
    if not path.endswith('/manifest.json'):
        path += '/manifest.json'
    return path + (('?' + query) if sep else '')


def _merge_by_url(local, remote):
    seen = {}
    for x in local or []:
        u = (x.get('manifest_url') or x.get('transportUrl') or x.get('url') or '').lower()
        if u:
            seen[u] = x
    for x in remote or []:
        u = (x.get('manifest_url') or x.get('transportUrl') or x.get('url') or '').lower()
        if u and u not in seen:
            seen[u] = x
    return list(seen.values())



def _percent_of(position, duration):
    """Watched share (0 to 100) from a position and a duration in seconds.

    v5.10.143 (GitHub issue #3): Nuvio's progress rows carry no percent, so
    pulled rows sat at 0 percent and finished ones stayed in Continue Watching.
    """
    try:
        position, duration = float(position or 0.0), float(duration or 0.0)
    except Exception:
        return 0.0
    if position <= 0.0 or duration <= 0.0:
        return 0.0
    return round(min(100.0, position / duration * 100.0), 2)


def _merge_progress(local, remote):
    def key(e):
        return '%s:%s' % (e.get('canonical_id') or e.get('imdb_id') or e.get('tmdb_id'), e.get('video_id') or '')
    m = {}
    for e in local or []:
        m[key(e)] = e
    for e in remote or []:
        k = key(e)
        cur = m.get(k)
        if not cur or (int(e.get('updated_at') or 0) > int(cur.get('updated_at') or 0)):
            m[k] = e
    return list(m.values())


_ISO_STRICT_RE = None


def _iso_is_valid(value):
    """True when stremio-core's chrono parser will accept this string."""
    global _ISO_STRICT_RE
    if _ISO_STRICT_RE is None:
        import re as _re
        # re.ASCII is essential: Python's \d matches Arabic-Indic digits,
        # but stremio-core's chrono parser rejects them as invalid characters.
        _ISO_STRICT_RE = _re.compile(
            r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$', _re.ASCII)
    return bool(value) and bool(_ISO_STRICT_RE.match(str(value)))


def _coerce_iso(value, fallback_epoch=None):
    """Best-effort: any timestampish input → strict UTC ISO with Z."""
    if _iso_is_valid(value):
        return str(value)
    text = str(value or '').strip().replace(' ', 'T')
    if text:
        try:
            import calendar
            t = time.strptime(text[:19], '%Y-%m-%dT%H:%M:%S')
            return _to_iso(calendar.timegm(t))
        except Exception:
            pass
    return _to_iso(fallback_epoch if fallback_epoch is not None else time.time())


def _sanitize_stremio_item(item):
    """v4.4.2: heal every datetime field stremio-core parses strictly.

    A single library item with a malformed `_ctime` (a space instead of the
    'T', localized digits, an empty string…) makes stremio-core reject the
    WHOLE LibraryItemsPulledFromAPI response — the exact
    'result[N]._ctime: input contains invalid characters' failure seen on
    web.strem.io. Poisoned values also self-perpetuate because the sync
    clones existing items via dict(prev). Sanitize on every write AND on
    demand via repair_library().
    Returns (item, changed).
    """
    if not isinstance(item, dict):
        return item, False
    changed = False
    out = dict(item)
    mtime_epoch = _parse_ts(out.get('_mtime')) if out.get('_mtime') else time.time()
    for key in ('_mtime', '_ctime'):
        if not _iso_is_valid(out.get(key)):
            out[key] = _coerce_iso(out.get(key), fallback_epoch=mtime_epoch)
            changed = True
    state = out.get('state')
    if isinstance(state, dict):
        state = dict(state)
        if state.get('lastWatched') and not _iso_is_valid(state.get('lastWatched')):
            state['lastWatched'] = _coerce_iso(state.get('lastWatched'), fallback_epoch=mtime_epoch)
            changed = True
        if state.get('lastVidReleased') and not _iso_is_valid(state.get('lastVidReleased')):
            state.pop('lastVidReleased', None)
            changed = True
        if 'watched' in state and not isinstance(state.get('watched'), str):
            state['watched'] = str(state.get('watched') or '')
            changed = True
        if changed:
            out['state'] = state
    return out, changed


def _parse_ts(iso):
    if not iso:
        return time.time()
    try:
        import calendar
        t = time.strptime(str(iso)[:19], '%Y-%m-%dT%H:%M:%S')
        return calendar.timegm(t)
    except Exception:
        return time.time()


def _to_iso(epoch):
    try:
        return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(int(epoch)))
    except Exception:
        return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATION — gather local data, sync enabled targets, write back
# ═══════════════════════════════════════════════════════════════════════════
def _gather_providers():
    try:
        from . import store
        rows = store.list_providers() or []
    except Exception:
        return []
    return [{'manifest_url': r.get('manifest_url', ''), 'name': r.get('name', '')}
            for r in rows if r.get('manifest_url')]


def _merge_library(local, remote):
    """Union by canonical id; the newer 'added_at' wins on a collision."""
    by_id = {}
    for row in list(local or []) + list(remote or []):
        cid = str((row or {}).get('canonical_id') or '').strip()
        if not cid:
            continue
        prev = by_id.get(cid)
        if not prev:
            by_id[cid] = row
            continue
        try:
            if float(row.get('added_at') or 0) > float(prev.get('added_at') or 0):
                by_id[cid] = row
        except Exception:
            pass
    return list(by_id.values())


def _gather_library(limit=1000):
    try:
        from .. import favorites_store
        return favorites_store.list_favorites(limit=limit) or []
    except Exception:
        return []



def _gather_collections():
    try:
        from .. import collection_sets
        return collection_sets.list_sets() or []
    except Exception:
        return []


_FINISHED_DAYS = 45


def _gather_progress(limit=400):
    """Continue Watching rows, plus what was finished lately (v5.10.171).

    Finished titles (90 % and more, or marked watched) were left out, so Nuvio
    never heard that an episode or a film was done and kept it in its own
    Continue Watching. They go as a full position (position = duration)."""
    try:
        from . import playback_store
        rows = list(playback_store.list_continue_items(limit=limit) or [])
    except Exception:
        return []
    try:
        seen = set((r.get('media_type'), r.get('canonical_id'), r.get('video_id')) for r in rows)
        since = time.time() - _FINISHED_DAYS * 86400
        for r in playback_store.list_recent_items(limit=200, include_watched=True) or []:
            key = (r.get('media_type'), r.get('canonical_id'), r.get('video_id'))
            if key in seen or int(r.get('updated_at') or 0) < since:
                continue
            pct = float(r.get('percent') or 0)
            limit_pct = 80.0 if 'simkl' in str(r.get('provider_name') or '').lower() else 90.0
            if pct < limit_pct:
                continue
            row = dict(r)
            row['finished'] = True
            seen.add(key)
            rows.append(row)
    except Exception:
        pass
    return rows


def _writeback_providers(providers, refresh_existing=False):
    if not isinstance(providers, list):
        return 0
    try:
        from . import store
        from .. import collection_sets
    except Exception:
        return 0
    have = set((r.get('manifest_url') or '').lower() for r in (store.list_providers() or []))
    added, refreshed, failed = 0, 0, []
    # v5.10.241: an add-on whose manifest could not be read is tried again
    # after six hours, not on every sync (5 s each time in the log)
    state = _load_sync_state()
    gone = dict(state.get('failed_manifests') or {})
    now = time.time()
    gone = dict((k, v) for k, v in gone.items() if now - float(v or 0) < 6 * 3600)
    for p in providers:
        raw_url = str((p or {}).get('manifest_url') or '').strip()
        url = raw_url.lower()
        if not url:
            continue
        existed = url in have
        if existed and not refresh_existing:
            continue
        if url in gone and not refresh_existing:
            continue
        try:
            collection_sets.ensure_addon_registered(
                raw_url,
                refresh=bool(refresh_existing),
                display_name=(p or {}).get('name') or '',
            )
            if existed:
                refreshed += 1
            else:
                added += 1
            have.add(url)
        except Exception as exc:
            # v4.7.9: this used to be a bare `continue`. A pulled addon whose
            # manifest was briefly unreachable vanished without a trace, and
            # the sync still reported success with "+0 addons".
            failed.append('%s (%s)' % (p.get('manifest_url') or '?', str(exc)[:60]))
            gone[url] = now
    if gone != dict(state.get('failed_manifests') or {}):
        state['failed_manifests'] = gone
        _save_sync_state(state)
    if failed:
        _log('addon writeback: %d added, %d refreshed, %d FAILED -> %s'
             % (added, refreshed, len(failed), ' | '.join(failed[:5])))
    else:
        _log('addon writeback: %d added, %d refreshed' % (added, refreshed))
    return added


def _writeback_collections(rows):
    if not isinstance(rows, list):
        return 0
    try:
        from .. import collection_sets
        result = collection_sets.import_synced_collections(rows, activate_cloud=False)
        count = int((result or {}).get('total') or 0)
        _log('collection writeback: cloud=%d backups=%d total=%d' % (
            int((result or {}).get('cloud') or 0),
            int((result or {}).get('backups') or 0), count))
        return count
    except Exception as exc:
        _log('collection writeback failed: %s' % exc)
        return 0


def _save_nuvio_home_settings(snapshot, addons=None, collections=None):
    try:
        from . import nuvio_home_mirror
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        if addons is None:
            cached_addons = read_json(_NUVIO_ADDON_URLS_FILE, {}) or {}
            if (isinstance(cached_addons, dict) and
                    cached_addons.get('authoritative') is True and
                    isinstance(cached_addons.get('urls'), list)):
                addons = [
                    {'manifest_url': url, 'sort_order': idx, 'enabled': True}
                    for idx, url in enumerate(cached_addons.get('urls') or [])
                ]
        return nuvio_home_mirror.save(
            snapshot.get('payload') or {},
            platform=snapshot.get('platform') or '',
            updated_at=snapshot.get('updated_at') or '',
            addons=addons,
            collections=collections,
            complete=bool(snapshot.get('complete', bool(
                (snapshot.get('payload') or {}).get('items') if
                isinstance(snapshot.get('payload'), dict) else False))),
        )
    except Exception as exc:
        _log('nuvio home mirror save failed: %s' % exc)
        return {}


@_scope.bound
def pull_nuvio_home_to_local():
    """Pull the whole Nuvio Home source-of-truth without pushing anything.

    This is the preferred manual refresh path from v5.4.33 onward:
      * native Collections -> stable nuvio-cloud set (never Active-dependent)
      * enabled account add-ons -> register missing manifests
      * unified Home catalog order/visibility/custom titles -> cached mirror
    """
    if not Nuvio.is_linked():
        raise RuntimeError('Nuvio is not linked')
    remote_collections = Nuvio.pull_collections()
    exact_addons = Nuvio.pull_addons_exact()
    home = Nuvio.pull_home_catalog_settings()

    # Nuvio's TMDb artwork/language/metadata toggles are profile preferences.
    # Pull them at most every 30 minutes so Home refresh stays light and does
    # not recreate the 429/503 burst fixed in v5.4.37.
    try:
        from . import nuvio_profile_prefs
        if nuvio_profile_prefs.is_stale(1800):
            Nuvio.pull_profile_settings_blob('tv')
    except Exception as exc:
        _log('nuvio profile settings pull skipped: %s' % exc)

    collection_count = 0
    if remote_collections:
        from .. import collection_sets
        collection_count = int(collection_sets.upsert_nuvio_cloud(
            remote_collections, activate=False, replace_empty=True) or 0)
    elif isinstance(remote_collections, list):
        # Match Nuvio TV CollectionSyncService: an empty remote payload never
        # deletes an existing local Collection set. This is especially
        # important when Home references a Collection during a partial sync.
        try:
            from .. import collection_sets
            existing = collection_sets.get_set(collection_sets.NUVIO_CLOUD_ID) or {}
            collection_count = len(existing.get('entries') or [])
        except Exception:
            collection_count = 0
    provider_count = _writeback_providers(
        exact_addons, refresh_existing=True) if exact_addons else 0
    mirror = _save_nuvio_home_settings(
        home, addons=exact_addons,
        collections=remote_collections if remote_collections else None)
    # Backward-compatible projection for older modules. v5.5 resolves from
    # the atomic Home snapshot first, so this file is no longer authoritative.
    _save_nuvio_enabled_addon_urls(exact_addons)
    return {
        'ok': bool(mirror.get('complete') or mirror.get('items') or collection_count or exact_addons),
        'empty': not bool(mirror.get('items') or remote_collections or exact_addons),
        'collections': collection_count,
        'providers': int(provider_count or 0),
        'addons_total': len(exact_addons),
        'home_rows': len(mirror.get('items') or []),
        'platform': mirror.get('platform') or '',
        'set_id': 'nuvio-cloud' if collection_count else '',
    }



def _writeback_progress(rows):
    if not isinstance(rows, list):
        return 0
    try:
        from . import playback_store
    except Exception:
        return 0
    pending = []
    for r in rows:
        try:
            cid = r.get('canonical_id') or r.get('imdb_id') or r.get('tmdb_id') or ''
            if not cid:
                continue
            pending.append({
                'media_type': r.get('media_type') or 'movie',
                'canonical_id': cid, 'video_id': r.get('video_id') or cid,
                'title': r.get('title', ''),
                'provider_name': r.get('provider_name', ''),
                'poster': r.get('poster', ''),
                'background': r.get('background', ''),
                'clearlogo': r.get('clearlogo', ''),
                'season': r.get('season'), 'episode': r.get('episode'),
                'position': float(r.get('position') or 0.0),
                'duration': float(r.get('duration') or 0.0),
                'percent': (float(r.get('percent') or 0.0)
                            or _percent_of(r.get('position'), r.get('duration'))),
                'stream_url': '', 'event_type': 'account_sync',
                'ext_updated_at': r.get('updated_at'),
                'tmdb_id': r.get('tmdb_id', ''),
                'imdb_id': r.get('imdb_id', ''),
                'tvdb_id': r.get('tvdb_id', ''),
                'show_tmdb_id': r.get('show_tmdb_id', ''),
                'native_server_id': r.get('native_server_id', ''),
                'native_item_id': r.get('native_item_id', ''),
            })
        except Exception:
            continue
    if not pending:
        return 0
    if hasattr(playback_store, 'upsert_entries'):
        # Pulled cloud rows must not mark the database dirty again; doing so
        # creates a pull -> write -> dirty -> sync feedback loop.
        return int(playback_store.upsert_entries(pending, mark_dirty=False) or 0)
    # Compatibility for older embedded test/runtime modules.
    written = 0
    for row in pending:
        try:
            args = (
                row['media_type'], row['canonical_id'], row['video_id'],
                row['title'], row['provider_name'], row['poster'],
                row['background'], row['clearlogo'], row['season'],
                row['episode'], row['position'], row['duration'],
                row['percent'], row['stream_url'], row['event_type'])
            kwargs = {
                'ext_updated_at': row['ext_updated_at'],
                'tmdb_id': row['tmdb_id'], 'imdb_id': row['imdb_id'],
                'tvdb_id': row['tvdb_id'],
                'show_tmdb_id': row['show_tmdb_id'],
                'mark_dirty': False,
            }
            try:
                playback_store.upsert_entry(*args, **kwargs)
            except TypeError:
                # Only for an older embedded store without the new flag.
                kwargs.pop('mark_dirty', None)
                playback_store.upsert_entry(*args, **kwargs)
            written += 1
        except Exception:
            continue
    return written


def enabled_targets():
    """Which services are both linked and enabled in settings.

    v4.7.9: a LINKED account whose master toggle was never written is
    treated as enabled, and the toggle is repaired on the spot. Accounts
    paired by QR never got that toggle (only the password path wrote it),
    so they sat linked-but-disabled forever and every sync run reported
    'no linked/enabled accounts' — silently, because nothing surfaced it.
    An explicit 'false' is still respected: only the unwritten state heals.
    """
    out = []
    for svc, linked in (('nuvio', Nuvio.is_linked()), ('stremio', Stremio.is_linked())):
        if not linked:
            continue
        raw = str(_setting('%s_sync_enabled' % svc, '') or '').strip().lower()
        if raw in ('true', '1', 'yes', 'on'):
            out.append(svc)
            continue
        if raw in ('false', '0', 'no', 'off'):
            _log('%s is linked but its sync toggle is OFF — skipping' % svc)
            continue
        # never written: linking implied consent to sync
        try:
            addon().setSetting('%s_sync_enabled' % svc, 'true')
        except Exception:
            pass
        _log('%s was linked with no sync toggle — enabling it now' % svc)
        out.append(svc)
    return out


def _direction_for(svc):
    """v4.0.0: per-service direction. Falls back to the global setting when
    the service is set to 'Follow global' (or on legacy installs where the
    per-service setting does not exist yet)."""
    raw = _setting('%s_sync_direction' % svc, '')
    if raw in ('', 'Follow global', 'اتباع الإعداد العام'):
        raw = _setting('cloud_sync_direction', 'Two-way') or 'Two-way'
    if raw in ('Upload only', 'رفع فقط'):
        return 'push'
    if raw in ('Download only', 'سحب فقط'):
        return 'pull'
    return 'both'


def _sections_for(svc):
    """Per-service section toggles.

    v4.8.0: 'collections' was HARD-CODED to False, so the
    nuvio_sync_collections setting was dead — turning it on changed
    nothing and the section never ran. It now reads its setting like the
    others. Nuvio defaults to on (its sync_push/pull_collections RPCs are
    verified and it is the account users expect to carry everything);
    Stremio defaults to off because its API stores add-ons and library
    progress, not folder layouts — run_sync already reports that plainly.
    """
    return {
        'addons': _setting_bool('%s_sync_addons' % svc, True),
        'collections': _setting_bool('%s_sync_collections' % svc,
                                     svc == 'nuvio'),
        'progress': _setting_bool('%s_sync_progress' % svc, True),
        # v4.8.1: the saved library. Nuvio only — Stremio's own library is
        # already carried by its progress/library API in sync_progress.
        'library': (svc == 'nuvio') and _setting_bool('nuvio_sync_library', True),
    }


def _fingerprint(rows):
    """Stable short hash of a list (order-independent). '' on any failure."""
    try:
        import hashlib
        norm = sorted(
            json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            for r in (rows or []))
        return hashlib.sha1('\u0001'.join(norm).encode('utf-8', 'replace')).hexdigest()[:16]
    except Exception:
        return ''


_FP_FILE = _scope.ScopedPath('account_sync_fp.json')


def _load_sync_state():
    return read_json(_FP_FILE, {}) or {}


def _save_sync_state(state):
    try:
        write_json(_FP_FILE, state)
    except Exception:
        pass


@_scope.bound
def run_sync(direction=None, targets=None, force_full=False, sections=None, only=None):
    """Synchronize selected sections independently.

    v4.0.0: `direction` and `sections` are now optional *overrides*. When
    None (the normal case), each service resolves its own direction and
    section toggles from its per-service settings, so Nuvio and Stremio are
    fully independent. A failure in one section never blocks the others.

    v5.10.120: `only` keeps the user's toggles but limits them to these
    sections (the service's sync after a local change: progress and the
    library, without the add-ons, collections and Home settings requests
    that made Nuvio answer 429 every few minutes).
    """
    targets = targets or enabled_targets()
    if not targets:
        return {'ok': False, 'error': 'no linked/enabled accounts'}

    svc_dir = dict((svc, direction or _direction_for(svc)) for svc in targets)
    if 'stremio' in svc_dir and not _setting_bool('stremio_progress_write_opt_in', False):
        svc_dir['stremio'] = 'pull'
    svc_sections = dict((svc, dict(sections) if sections else _sections_for(svc))
                        for svc in targets)
    if only:
        for svc in targets:
            svc_sections[svc] = dict((key, bool(value) and key in only)
                                     for key, value in svc_sections[svc].items())
    # Gather local data once for every section any service wants.
    want = {
        'addons': any(s.get('addons') for s in svc_sections.values()),
        'collections': any(s.get('collections') for s in svc_sections.values()),
        'progress': any(s.get('progress') for s in svc_sections.values()),
        'library': any(s.get('library') for s in svc_sections.values()),
    }
    addons = _gather_providers() if want['addons'] else []
    collections = _gather_collections() if want['collections'] else []
    progress = _gather_progress() if want['progress'] else []
    merged_library = _gather_library() if want['library'] else []

    state = {} if force_full else _load_sync_state()
    last_ok = int(state.get('last_ok_at') or 0)
    fp_addons = _fingerprint(addons)
    fp_collections = _fingerprint(collections)
    addons_changed = want['addons'] and (force_full or not fp_addons or state.get('fp_addons') != fp_addons)
    collections_changed = want['collections'] and (force_full or not fp_collections or state.get('fp_collections') != fp_collections)
    progress_delta_push = [r for r in progress
        if r.get('event_type') != 'account_sync' and
        (force_full or not last_ok or int(r.get('updated_at') or 0) > last_ok)]

    _log('sync plan: %s' % ' '.join(
        '%s[%s:%s]' % (svc, svc_dir[svc],
                       ','.join(k for k in ('addons', 'collections', 'progress')
                                if svc_sections[svc].get(k)) or 'none')
        for svc in targets))

    report=[]
    merged_addons=list(addons)
    merged_collections=list(collections)
    merged_progress=list(progress)

    for svc in targets:
        d = svc_dir[svc]
        sec = svc_sections[svc]
        impl = Nuvio if svc == 'nuvio' else Stremio
        section_report = {'service': svc, 'ok': False, 'direction': d, 'sections': {}}
        any_ok = False
        if svc == 'stremio' and sec.get('collections'):
            section_report['sections']['collections'] = {
                'ok': False,
                'error': 'Stremio account stores add-ons and library progress, not Kaptain folder layouts; use JSON import for collections.'
            }
        if svc == 'nuvio' and sec.get('collections'):
            # v5.4.9: the missing link. v4.8.0 fixed the dead setting read
            # but never added this call — the section was gathered, gated,
            # fingerprinted and counted in totals without a single RPC
            # firing. Same skip contract as addons: unchanged local data
            # pulls only, and a push-only direction with nothing new skips.
            try:
                c_dir = d if collections_changed else ('pull' if d != 'push' else 'skip')
                if c_dir != 'skip':
                    merged_collections = impl.sync_collections(collections, c_dir)
                section_report['sections']['collections'] = {'ok': True, 'count': len(merged_collections)}
                any_ok = True
            except Exception as e:
                section_report['sections']['collections'] = {'ok': False, 'error': str(e)}
                _log('%s collections sync failed: %s' % (svc, e))
        if sec.get('addons'):
            try:
                # Nuvio Home is a mirror, not a provider merge target. During
                # normal pull/two-way sync Nuvio's account add-ons are
                # authoritative, so never push Dex Hub-only providers merely
                # because Home mirroring is enabled. Explicit push-only stays
                # available in Advanced for users who intentionally request it.
                if svc == 'nuvio' and d != 'push':
                    a_dir = 'pull'
                else:
                    a_dir = d if addons_changed else ('pull' if d != 'push' else 'skip')
                if a_dir != 'skip':
                    merged_addons = _merge_by_url(merged_addons, impl.sync_addons(addons, a_dir))
                section_report['sections']['addons'] = {'ok': True, 'count': len(merged_addons)}
                any_ok = True
            except Exception as e:
                section_report['sections']['addons'] = {'ok': False, 'error': str(e)}
                _log('%s addons sync failed: %s' % (svc, e))
        # Nuvio keeps the visible Home order/disabled state/custom titles in
        # a separate sync payload. Pull it whenever Home data (addons or
        # Collections) participates so Dex Hub mirrors Nuvio instead of
        # rebuilding a local order. This path is pull-only by design.
        if svc == 'nuvio' and d != 'push' and (sec.get('addons') or sec.get('collections')):
            try:
                home_snapshot = impl.pull_home_catalog_settings()
                mirror = _save_nuvio_home_settings(
                    home_snapshot,
                    collections=merged_collections if sec.get('collections') else None)
                section_report['sections']['home'] = {
                    'ok': True, 'count': len((mirror or {}).get('items') or [])
                }
                any_ok = True
            except Exception as e:
                section_report['sections']['home'] = {'ok': False, 'error': str(e)}
                _log('nuvio home settings sync failed: %s' % e)

        if sec.get('progress'):
            try:
                seed = progress_delta_push if d in ('both', 'push') else progress
                merged_progress = _merge_progress(merged_progress, impl.sync_progress(seed, d))
                section_report['sections']['progress'] = {'ok': True, 'count': len(merged_progress)}
                any_ok = True
            except Exception as e:
                section_report['sections']['progress'] = {'ok': False, 'error': str(e)}
                _log('%s progress sync failed: %s' % (svc, e))
        if sec.get('library') and hasattr(impl, 'sync_library'):
            # v4.8.1: the saved library (DexHub favourites) — Nuvio's own
            # clients keep it in sync_pull_library / sync_push_library.
            try:
                merged_library = impl.sync_library(merged_library, d)
                section_report['sections']['library'] = {'ok': True, 'count': len(merged_library)}
                any_ok = True
            except Exception as e:
                section_report['sections']['library'] = {'ok': False, 'error': str(e)}
                _log('%s library sync failed: %s' % (svc, e))
        section_report['ok'] = any_ok
        report.append(section_report)

    wb = {'providers': 0, 'collections': 0, 'progress': 0, 'library': 0}

    def _pulled(section):
        return any(svc_dir[s] != 'push' and svc_sections[s].get(section) for s in targets)

    _scope.require_current()
    if _pulled('addons'):
        wb['providers'] = _writeback_providers(merged_addons)
    if _pulled('collections'):
        wb['collections'] = _writeback_collections(merged_collections)
    if _pulled('progress'):
        wb['progress'] = _writeback_progress(merged_progress)
    if _pulled('library'):
        # Each library provider persists its own successful snapshot.
        # Writing the merged union as Nuvio would mislabel local/Trakt rows.
        wb['library'] = sum(int(r.get('sections', {}).get('library', {}).get('count') or 0)
                            for r in report if r.get('sections', {}).get('library', {}).get('ok'))

    ok=any(r.get('ok') for r in report)
    if ok:
        all_ok = all(section.get('ok') for result in report for section in result.get('sections', {}).values())
        new_state = {'last_ok_at': int(time.time()) if all_ok else last_ok,
                     'fp_addons': fp_addons if all_ok else state.get('fp_addons'),
                     'fp_collections': fp_collections if all_ok else state.get('fp_collections')}
        # v5.10.241: the library fingerprints and failed manifests written during this sync stay
        _current = _load_sync_state()
        for _key in ('fp_nuvio_lib_remote', 'fp_nuvio_lib_push', 'failed_manifests'):
            if _key in _current:
                new_state[_key] = _current[_key]
        if only:
            # sections left out keep their fingerprints: the next full sync
            # must not take them for changed (and push them)
            previous = _load_sync_state()
            for section, key in (('addons', 'fp_addons'), ('collections', 'fp_collections')):
                if section not in only:
                    new_state[key] = previous.get(key) or ''
        _save_sync_state(new_state)
    try: addon().setSetting('last_account_sync_at', str(int(time.time())))
    except Exception: pass
    _log('sync result: providers +%d collections +%d progress %d library %d'
         % (wb['providers'], wb['collections'], wb['progress'], wb['library']))
    return {'ok':ok,'report':report,'writeback':wb,
            'totals':{'addons':len(merged_addons),'collections':len(merged_collections),
                      'progress':len(merged_progress),'library':len(merged_library)}}


# v5.10.286: Remove from Continue Watching removes the title's watch progress
# on the Nuvio account too (the RPC Nuvio's own apps call for it,
# sync_delete_watch_progress), or the next pull brought the card back. Keys
# are Nuvio's progress keys: '<content id>' for a film,
# '<content id>_s<S>e<E>' for an episode (the ones its snapshot lists).
def remove_progress(content_ids):
    """Delete every Nuvio watch progress entry of one title. Returns how many
    keys were sent; 0 when Nuvio is not linked, not synced or download-only."""
    wanted = set(str(c).strip() for c in content_ids or () if str(c or '').strip())
    if not wanted or 'nuvio' not in enabled_targets() or _direction_for('nuvio') == 'pull':
        return 0
    pid = Nuvio._profile_id()
    remote = Nuvio._rpc('sync_pull_watch_progress', {'p_profile_id': pid})
    keys = set()
    for entry in remote if isinstance(remote, list) else []:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get('content_id') or '').strip()
        if cid not in wanted:
            continue
        key = str(entry.get('progress_key') or '').strip()
        if key:
            keys.add(key)
        season, episode = entry.get('season'), entry.get('episode')
        keys.add('%s_s%se%s' % (cid, season, episode) if season is not None and episode is not None else cid)
    if not keys:
        return 0
    Nuvio._rpc('sync_delete_watch_progress', {
        'p_keys': sorted(keys), 'p_profile_id': pid,
        'p_origin_client_id': _nuvio_client_id()})
    _log('nuvio: removed %d watch progress entr(ies) from Continue Watching' % len(keys))
    return len(keys)
