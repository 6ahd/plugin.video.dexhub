# -*- coding: utf-8 -*-
"""Simkl account sync for Dex Hub (v4.1.0).

Mirrors the trakt.py architecture: device-PIN auth, watched-history sync at
playback end, and the user's Simkl lists (watching / plan-to-watch / completed
/ on-hold / dropped) exposed as browsable catalogs.

Simkl API notes (encoded from Simkl's public apiary docs — endpoints marked
[VERIFY-LIVE] should be confirmed against a live account on first run):
  * Base: https://api.simkl.com
  * Device auth:  GET /oauth/pin?client_id=X
                  -> {user_code, verification_url, expires_in, interval}
                  poll GET /oauth/pin/{user_code}?client_id=X
                  -> {"result":"KO"} until {"result":"OK","access_token":...}
  * Authed calls: headers  Authorization: Bearer <token>
                            simkl-api-key: <client_id>
  * Lists:   GET /sync/all-items/{movies|shows|anime}/{status}?extended=full
  * History: POST /sync/history  {"movies":[{"ids":{...}}],
                                  "shows":[{"ids":{...},"seasons":[
                                      {"number":S,"episodes":[{"number":E}]}]}]}

This integration currently writes watched history at playback end. Simkl also
supports real-time scrobbling and paused playback; those are separate from
the watching-list / next-episode mirror implemented here.
"""
import json
import re
import os
import time
import hashlib
import threading
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
import xbmcgui

from .dexhub.common import profile_path
from . import playback_store
from .i18n import tr

try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()

API = 'https://api.simkl.com'
TOKEN_PATH = os.path.join(profile_path(), 'simkl_token.json')
# Dex Hub's registered Simkl app (simkl.com/settings/developer, redirect
# urn:ietf:wg:oauth:2.0:oob) — same pattern as trakt.DEFAULT_CLIENT_ID.
# The client id is public by design (it identifies the app, it grants
# nothing); the PIN flow never needs the client secret, which is NOT
# embedded. The hidden setting `simkl_client_id` still overrides this.
DEFAULT_CLIENT_ID = '7c5c3e3c74fa28dd639ca8b09da8f5e48d46ee7c3ea86d284c56bee4a0a09643'
PIN_URL_FALLBACK = 'https://simkl.com/pin'

_PUBLIC_CACHE = {}
_PUBLIC_CACHE_TTL = 300
# One history POST per video per Kodi session — a stop at 96% followed by the
# natural "ended" event must not write the same episode twice.
_MARKED_THIS_SESSION = set()
_STATE_LOCK = threading.RLock()
_HISTORY_IMPORTED = {}

# status keys per Simkl kind. Movies have no "watching"/"hold" shelf.
STATUSES_SHOWS = ('watching', 'plantowatch', 'hold', 'completed', 'dropped')
STATUSES_MOVIES = ('plantowatch', 'completed', 'dropped')
KINDS = ('movies', 'shows', 'anime')


def _setting(key, default=''):
    try:
        return ADDON.getSetting(key) or default
    except Exception:
        return default


def enabled():
    return (_setting('enable_simkl', 'true') or 'true').lower() == 'true'


def client_id():
    return (_setting('simkl_client_id', '').strip() or DEFAULT_CLIENT_ID).strip()


def credentials_configured():
    return bool(client_id())


def mark_watched_enabled():
    return (_setting('simkl_mark_watched', 'true') or 'true').lower() == 'true'


def watched_threshold_percent():
    try:
        value = float(_setting('simkl_watched_threshold', '85') or 85)
    except Exception:
        value = 85.0
    return max(50.0, min(99.0, value))


def ensure_enabled():
    try:
        if not enabled():
            ADDON.setSetting('enable_simkl', 'true')
    except Exception:
        pass


def _read_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return default


def _write_json(path, value):
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(value, handle)
        return True
    except Exception:
        return False


def token_data():
    return _read_json(TOKEN_PATH, {})


def save_token(data):
    return _write_json(TOKEN_PATH, data or {})


def clear_token():
    try:
        if os.path.exists(TOKEN_PATH):
            os.remove(TOKEN_PATH)
    except Exception:
        pass


def authorized():
    return bool((token_data() or {}).get('access_token'))


def authorization_status():
    if authorized():
        return 'connected'
    return 'ready' if credentials_configured() else 'needs_api'


def _headers(auth=False):
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'DexHub/%s (Kodi)' % (ADDON.getAddonInfo('version') or '4.1.0'),
        'simkl-api-key': client_id(),
    }
    if auth:
        token = (token_data() or {}).get('access_token') or ''
        if not token:
            raise RuntimeError(tr('حساب Simkl غير مرتبط'))
        headers['Authorization'] = 'Bearer %s' % token
    return headers


def _request(path, payload=None, method='GET', auth=False, timeout=20):
    # Current API requires app identification in the query, as well as the
    # legacy simkl-api-key header used by existing PIN accounts.
    query = urllib.parse.urlencode({
        **({} if 'client_id=' in path else {'client_id': client_id()}), 'app-name': 'dexhub',
        'app-version': ADDON.getAddonInfo('version') or '5.10.137',
    })
    url = API + path + ('&' if '?' in path else '?') + query
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(auth=auth), method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode('utf-8', 'ignore')
        return json.loads(body) if body else {}


def _cached_request(path, auth=True, timeout=20):
    now = time.time()
    key = (_account_key() if auth else '', path)
    hit = _PUBLIC_CACHE.get(key)
    if hit and (now - hit[0]) <= _PUBLIC_CACHE_TTL:
        return hit[1]
    data = _request(path, method='GET', auth=auth, timeout=timeout)
    _PUBLIC_CACHE[key] = (now, data)
    return data


def invalidate_cache(prefix=''):
    for key in list(_PUBLIC_CACHE.keys()):
        if not prefix or key[1].startswith(prefix):
            _PUBLIC_CACHE.pop(key, None)


def _account_key():
    value = client_id() + ':' + str((token_data() or {}).get('access_token') or '')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def _state_path(kind):
    return os.path.join(profile_path(), 'simkl_sync_%s_%s.json' % (_account_key(), kind))


def _save_state(path, state):
    # Atomic replacement keeps the previous snapshot usable if a fetch or a
    # write fails. Never save the access token in this cache.
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='simkl-sync-', dir=os.path.dirname(path))
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(state, handle, separators=(',', ':'))
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def _remote_rows(data, kind, delta=False):
    # v5.10.140: Simkl can answer a literal null (a date_from read with no
    # change, an empty list). That is "nothing", not a failure: as an error
    # it kept the snapshot from saving, so the same read failed every time
    # and one empty kind stopped the whole history import.
    if data is None:
        return []
    if not isinstance(data, dict) or data.get('error'):
        raise RuntimeError('Invalid Simkl sync response')
    bucket = data.get(kind)
    if bucket is None and (not data or (delta and any(key in KINDS for key in data))):
        return []
    if not isinstance(bucket, list):
        raise RuntimeError('Missing Simkl %s list' % kind)
    return normalize_all_items({kind: bucket}, kind)


def _row_key(row):
    return str(row.get('_simkl_id') or _canonical_for(row.get('ids') or {}))


def _snapshot_for_kind(kind, need_episodes=False):
    """One baseline, then activity-gated deltas; status moves replace rows.

    Episode history is seeded once, only when an import needs it. Subsequent
    pulls include episode data only for changed shows, not the whole library.
    """
    with _STATE_LOCK:
        activities = _cached_request('/sync/activities', auth=True)
        if not isinstance(activities, dict) or not activities.get('all'):
            raise RuntimeError('Invalid Simkl activities response')
        section = activities.get('tv_shows' if kind == 'shows' else kind) or {}
        stamp = str(section.get('all') or activities['all'])
        removed = str(section.get('removed_from_list') or '')
        path = _state_path(kind)
        state = _read_json(path, {})
        valid = (isinstance(state, dict) and state.get('schema') == 1
                 and isinstance(state.get('rows'), list) and state.get('stamp'))
        episodes = kind != 'movies' and bool(need_episodes or (state.get('episodes') if valid else False))
        baseline = not valid or (episodes and not state.get('episodes'))
        if not baseline and state['stamp'] == stamp:
            return state
        params = {}
        if episodes:
            params.update(extended='full', episode_watched_at='yes', include_all_episodes='yes')
        if not baseline:
            params['date_from'] = state['stamp']
        url = '/sync/all-items/%s' % kind
        if params:
            url += '?' + urllib.parse.urlencode(params)
        rows = _remote_rows(_request(url, auth=True), kind, delta=not baseline)
        merged = {} if baseline else {_row_key(row): row for row in state['rows']}
        for row in rows:
            merged[_row_key(row)] = row
        if not baseline and removed != str(state.get('removed') or ''):
            # Deltas contain no deletion tombstones. Reconcile IDs only when
            # Simkl announces a removal, preserving all other cached titles.
            ids_data = _request('/sync/all-items/%s?extended=ids_only' % kind, auth=True)
            current = {_row_key(row) for row in _remote_rows(ids_data, kind)}
            merged = {key: row for key, row in merged.items() if key in current}
        state = {'schema': 1, 'stamp': stamp, 'removed': removed,
                 'episodes': episodes, 'rows': list(merged.values())}
        _save_state(path, state)
        return state


def _build_pin_message(verify_url, user_code, remaining=None):
    """Single-string message compatible with Kodi 20+ dialogs (trakt pattern)."""
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
    """Link Simkl through the same QR-first TV flow used by Trakt."""
    ensure_enabled()
    if not credentials_configured():
        raise RuntimeError(tr('أدخل Simkl Client ID في الإعدادات أولاً (من simkl.com/settings/developer)'))

    try:
        code = _request('/oauth/pin?client_id=%s' % urllib.parse.quote(client_id()), method='GET', auth=False)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode('utf-8', 'ignore')
        except Exception:
            body = ''
        raise RuntimeError(tr('فشل طلب رمز Simkl (%s): %s') % (exc.code, body[:200]))
    except Exception as exc:
        raise RuntimeError(tr('فشل الاتصال بـ Simkl: %s') % exc)

    user_code = str((code or {}).get('user_code') or '').strip()
    verify = str((code or {}).get('verification_url') or PIN_URL_FALLBACK)
    interval = max(5, int((code or {}).get('interval') or 5))
    expires_in = max(60, int((code or {}).get('expires_in') or 900))
    if not user_code:
        raise RuntimeError(tr('لم يرجع Simkl رمز ربط صالح'))

    from .plex_qr import qr_png, QRLinkWindow
    qr_path = qr_png(verify, box_size=8, border=4) or ''
    window = QRLinkWindow(
        qr_path=qr_path,
        code=user_code,
        title=tr('ربط Simkl'),
        verification_url=verify,
        body_text=tr(
            'امسح الباركود بالجوال لفتح Simkl.\n\n'
            'ثم أدخل رمز الربط:\n\n[B][COLOR yellow]%s[/COLOR][/B]\n\n'
            'أو افتح:\n[COLOR cyan]%s[/COLOR]\n\n'
            'بانتظار الموافقة…  (رجوع للإلغاء)'
        ) % (user_code, verify),
    )
    window.show()
    poll_path = '/oauth/pin/%s?client_id=%s' % (urllib.parse.quote(user_code), urllib.parse.quote(client_id()))
    start = time.time()
    monitor = xbmc.Monitor()
    try:
        while (time.time() - start) < expires_in and not monitor.abortRequested():
            if getattr(window, 'cancelled', False):
                return False
            try:
                token = _request(poll_path, method='GET', auth=False)
                if isinstance(token, dict) and token.get('access_token'):
                    save_token({'access_token': token.get('access_token'), 'created_at': int(time.time())})
                    window.close()
                    xbmcgui.Dialog().notification('Dex Hub', tr('تم ربط Simkl بنجاح'), xbmcgui.NOTIFICATION_INFO, 3000)
                    invalidate_cache()
                    return True
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    interval = min(30, max(interval + 1, interval * 2))
                elif exc.code not in (400, 404):
                    xbmc.log('[DexHub] simkl poll http %s' % exc.code, xbmc.LOGWARNING)
            except Exception as exc:
                xbmc.log('[DexHub] simkl poll transient error: %s' % exc, xbmc.LOGWARNING)
            remaining = max(0, int(expires_in - (time.time() - start)))
            try:
                window.set_status(tr(
                    'امسح الباركود بالجوال لفتح Simkl.\n\n'
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
    clear_token()
    invalidate_cache()
    _MARKED_THIS_SESSION.clear()
    return True


# ─── ids / payload helpers ──────────────────────────────────────────────────

def _ids_from_ctx(ctx):
    ids = {}
    imdb = str((ctx or {}).get('imdb_id') or '').strip()
    tmdb = str((ctx or {}).get('tmdb_id') or '').strip()
    tvdb = str((ctx or {}).get('tvdb_id') or '').strip()
    if imdb:
        ids['imdb'] = imdb if imdb.startswith('tt') else 'tt%s' % imdb
    if tmdb:
        ids['tmdb'] = tmdb
    if tvdb:
        ids['tvdb'] = tvdb
    return ids


def history_payload(ctx):
    """Build the POST /sync/history body for a playback ctx (movie or episode)."""
    ids = _ids_from_ctx(ctx)
    if not ids:
        return None
    media_type = str((ctx or {}).get('media_type') or 'movie').lower()
    if media_type in ('series', 'show', 'tv', 'anime', 'episode'):
        try:
            season = int(ctx.get('season'))
            episode = int(ctx.get('episode'))
        except Exception:
            return None
        if season < 0 or episode <= 0:
            return None
        return {'shows': [{
            'title': ctx.get('show_title') or ctx.get('title') or '',
            'ids': ids,
            'seasons': [{'number': season, 'episodes': [{'number': episode}]}],
        }]}
    return {'movies': [{'title': ctx.get('title') or '', 'ids': ids}]}


def _session_key(ctx):
    base = str((ctx or {}).get('video_id') or (ctx or {}).get('canonical_id') or '')
    # A canonical show ID alone is shared by every episode. It must not
    # suppress all later episodes in the same Kodi session.
    return '%s|%s|%s|%s' % (_account_key(), base or json.dumps(_ids_from_ctx(ctx), sort_keys=True),
                           (ctx or {}).get('season', ''), (ctx or {}).get('episode', ''))


def should_mark_watched(position_ms, duration_ms, threshold=None):
    """Pure decision helper (unit-tested): watched iff progress ≥ threshold."""
    try:
        pos = float(position_ms or 0)
        dur = float(duration_ms or 0)
    except Exception:
        return False
    if dur <= 0:
        # Unknown duration cannot prove completion. The natural ended event
        # uses force=True; a stop at position zero must never mark watched.
        return False
    pct = (pos / dur) * 100.0
    return pct >= float(threshold if threshold is not None else watched_threshold_percent())


def mark_watched_from_ctx(ctx, position_ms=0, force=False):
    """Called by SimklReporter at stop/ended. POSTs /sync/history once per item.

    Returns True when a history write happened, False when skipped (below
    threshold, missing ids, disabled, or already written this session).
    """
    if not (enabled() and authorized() and mark_watched_enabled()):
        return False
    duration_ms = 0
    try:
        duration_ms = int(float((ctx or {}).get('duration_ms') or 0))
    except Exception:
        duration_ms = 0
    if not force and not should_mark_watched(position_ms, duration_ms):
        return False
    key = _session_key(ctx)
    if key in _MARKED_THIS_SESSION:
        return False
    payload = history_payload(ctx)
    if not payload:
        return False
    try:
        result = _request('/sync/history', payload=payload, method='POST', auth=True)
        if isinstance(result, dict) and (result.get('error') or any(
                result.get('not_found', {}).get(key) for key in ('movies', 'shows', 'episodes'))):
            raise RuntimeError('Simkl did not resolve this media ID')
        _MARKED_THIS_SESSION.add(key)
        invalidate_cache('/sync/all-items')
        invalidate_cache('/sync/activities')
        xbmc.log('[DexHub] simkl: marked watched %s' % key, xbmc.LOGINFO)
        return True
    except Exception as exc:
        xbmc.log('[DexHub] simkl mark-watched failed for %s: %s' % (key, exc), xbmc.LOGWARNING)
        return False


# ─── lists ──────────────────────────────────────────────────────────────────

def _node_from_row(row, kind):
    """The item node inside an all-items row: movies→'movie', shows/anime→'show'."""
    if not isinstance(row, dict):
        return {}
    if kind == 'movies':
        return row.get('movie') or {}
    return row.get('show') or {}


def normalize_all_items(data, kind):
    """Normalize a /sync/all-items response into mdblist-shaped rows.

    Output rows: {'_type': 'movie'|'show', 'title', 'year'/'release_year',
                  'overview', 'ids': {'imdb','tmdb','tvdb'}} — the exact shape
    plugin._render_idlist_rows() already consumes for MDBList catalogs.
    """
    rows = []
    if not isinstance(data, dict):
        return rows
    bucket = data.get(kind)
    if bucket is None and kind == 'anime':
        bucket = data.get('anime') or data.get('shows')
    for row in (bucket or []):
        node = _node_from_row(row, kind)
        if not node:
            continue
        ids_in = node.get('ids') or {}
        imdb = str(ids_in.get('imdb') or '').strip()
        tmdb = str(ids_in.get('tmdb') or '').strip()
        tvdb = str(ids_in.get('tvdb') or '').strip()
        if not (imdb or tmdb or tvdb):
            continue  # nothing Dex Hub can route on
        rows.append({
            '_type': 'movie' if kind == 'movies' else 'show',
            'title': node.get('title') or '',
            'year': node.get('year') or '',
            'release_year': node.get('year') or '',
            'overview': node.get('overview') or '',
            'ids': {'imdb': imdb, 'tmdb': tmdb, 'tvdb': tvdb},
            '_simkl_id': str(ids_in.get('simkl') or ''),
            '_status': 'dropped' if row.get('status') == 'notinteresting' else str(row.get('status') or ''),
            # v4.2.0: Simkl serves its own poster CDN slug — pass a full URL
            # through so the renderer never shows a bare file icon even when
            # the TMDb art pipeline has nothing for this title.
            'poster': poster_url(node.get('poster')),
            'last_watched': str(row.get('last_watched') or ''),
            'next_to_watch': str(row.get('next_to_watch') or ''),
            'watched_episodes_count': int(row.get('watched_episodes_count') or 0),
            'total_episodes_count': int(row.get('total_episodes_count') or 0),
            'last_watched_at': str(row.get('last_watched_at') or ''),
            'seasons': row.get('seasons') if isinstance(row.get('seasons'), list) else [],
        })
    return rows


_FAIL_LOG_MEMO = {}


def fetch_all_items(kind='shows', status='watching', strict=False):
    """User's Simkl list for one kind/status, normalized (see normalize_all_items)."""
    if not enabled():
        return []
    kind = kind if kind in KINDS else 'shows'
    valid = STATUSES_MOVIES if kind == 'movies' else STATUSES_SHOWS
    status = status if status in valid else valid[0]
    try:
        state = _snapshot_for_kind(kind)
    except Exception as exc:
        # v4.7.5: identical transient network errors (DNS/timeouts) repeated
        # 27 times in one session of the user's log. Report each failure
        # signature at most once an hour; the rest go to DEBUG.
        _sig = '%s|%s|%s' % (kind, status, str(exc)[:60])
        _now = time.time()
        if _now - float(_FAIL_LOG_MEMO.get(_sig) or 0.0) > 3600:
            _FAIL_LOG_MEMO[_sig] = _now
            xbmc.log('[DexHub] simkl fetch_all_items(%s,%s) failed: %s' % (kind, status, exc), xbmc.LOGWARNING)
        else:
            xbmc.log('[DexHub] simkl fetch_all_items(%s,%s) failed (repeat): %s' % (kind, status, exc), xbmc.LOGDEBUG)
        if strict:
            raise
        return []
    return [row for row in state['rows'] if row.get('_status') == status]



# ── v4.2.0: art + full history import + Continue Watching sync ───────────

def poster_url(slug):
    """Simkl poster CDN. [VERIFY-LIVE] `_m` (medium) jpg variant."""
    slug = str(slug or '').strip()
    return ('https://simkl.in/posters/%s_m.jpg' % slug) if slug else ''


_EP_MARKER_RE = re.compile(r's\s*(\d+)\s*e\s*(\d+)', re.I)


def parse_episode_marker(value):
    """'S02E05' / 's2e5' → (2, 5); anything else → None."""
    match = _EP_MARKER_RE.search(str(value or ''))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _canonical_for(ids):
    imdb = str(ids.get('imdb') or '').strip()
    tmdb = str(ids.get('tmdb') or '').strip()
    tvdb = str(ids.get('tvdb') or '').strip()
    if imdb:
        return imdb if imdb.startswith('tt') else 'tt%s' % imdb
    if tmdb:
        return 'tmdb:%s' % tmdb
    if tvdb:
        return 'tvdb:%s' % tvdb
    return ''


def import_watched(limit=1000, force=False):
    """Import only episodes actually returned in Simkl's watched history.

    History is seeded once, then updated through activity-gated deltas. Remote
    watch dates clear stale resume rows without erasing a newer local rewatch.
    """
    if not (enabled() and authorized()):
        raise RuntimeError(tr('حساب Simkl غير مرتبط'))
    from . import trakt as _trakt
    states = {kind: _snapshot_for_kind(kind, need_episodes=(kind != 'movies'))
              for kind in ('shows', 'movies', 'anime')}
    signature = tuple((kind, states[kind]['stamp']) for kind in KINDS)
    account = _account_key()
    if not force and _HISTORY_IMPORTED.get(account) == signature:
        return 0, 0
    events = []
    for kind in KINDS:
        for row in states[kind]['rows'][:max(1, int(limit))]:
            ids = row.get('ids') or {}
            canonical = _canonical_for(ids)
            if not canonical:
                continue
            base = {'canonical_id': canonical, 'imdb_id': ids.get('imdb') or '',
                    'tmdb_id': ids.get('tmdb') or '', 'tvdb_id': ids.get('tvdb') or ''}
            if kind == 'movies':
                if row.get('_status') == 'completed':
                    events.append(dict(base, media_type='movie',
                                       watched_at=_trakt._parse_trakt_ts(row.get('last_watched_at'))))
                continue
            for season_row in row.get('seasons') or []:
                if not isinstance(season_row, dict):
                    continue
                for episode_row in season_row.get('episodes') or []:
                    if not isinstance(episode_row, dict):
                        continue
                    try:
                        season = int(season_row['number'])
                        episode = int(episode_row['number'])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if season < 0 or episode <= 0 or episode_row.get('watched') is False:
                        continue
                    events.append(dict(base, media_type='series', season=season, episode=episode,
                                       watched_at=_trakt._parse_trakt_ts(episode_row.get('watched_at'))))
    result = playback_store.mark_remote_watched(events)
    _HISTORY_IMPORTED[account] = signature
    return result


def sync_continue_watching(limit=60):
    """Mirror Simkl 'watching' shows into Dex Hub's Continue Watching.

    This mirror contains next-episode suggestions, not remote pause points.
    A null/missing next_to_watch means caught up: never guess a later episode.
    Stale suggestions are reconciled only after both lists succeed.
    """
    if not (enabled() and authorized()):
        return 0
    from . import trakt as _trakt
    from . import playback_store
    # Reuse local art; a history refresh must not fetch TMDb once per title.
    local_art = {}
    for row in playback_store.list_recent_items(limit=500):
        local_art.setdefault(row.get('canonical_id'), row)
    pending = []
    # v5.10.147: Simkl keeps every show ever started in "watching", and its
    # next episode turned up as a new title in Continue Watching for shows
    # left long ago (or new episodes of them). Only shows watched within the
    # chosen days come in (Settings, Continue Watching; 0 = every show).
    try:
        days = int(float(xbmcaddon.Addon().getSetting('simkl_continue_days') or '30'))
    except Exception:
        days = 30
    oldest = (time.time() - days * 86400) if days > 0 else 0
    for kind in ('shows', 'anime'):
        added = 0
        watching = fetch_all_items(kind, 'watching', strict=True)
        watching.sort(key=lambda row: _trakt._parse_trakt_ts(row.get('last_watched_at')) or 0, reverse=True)
        for row in watching:
            ids = row.get('ids') or {}
            canonical = _canonical_for(ids)
            if not canonical:
                continue
            marker = parse_episode_marker(row.get('next_to_watch'))
            if not marker or row.get('_status') != 'watching':
                continue
            season, episode = marker
            if season <= 0 or episode <= 0:
                continue
            ts = _trakt._parse_trakt_ts(row.get('last_watched_at'))
            if oldest and not (ts and ts >= oldest):
                continue
            if added >= max(1, int(limit)):
                break
            added += 1
            known = local_art.get(canonical) or {}
            art = {'poster': known.get('poster') or row.get('poster') or '',
                   'fanart': known.get('background') or '', 'clearlogo': known.get('clearlogo') or ''}
            try:
                from . import tmdb_direct
                cached_art = tmdb_direct.art_cached_only(
                    tmdb_id=ids.get('tmdb') or '', imdb_id=ids.get('imdb') or '',
                    media_type='tv', title=row.get('title') or '') or {}
                for key in ('poster', 'fanart', 'clearlogo'):
                    art[key] = art[key] or cached_art.get(key) or ''
            except Exception:
                pass
            if not art.get('poster') and row.get('poster'):
                art['poster'] = row.get('poster')
            pending.append({
                'media_type': 'series', 'canonical_id': canonical,
                'video_id': '%s:%s:%s' % (canonical, season, episode),
                'title': row.get('title') or 'Unknown',
                'provider_name': 'Simkl',
                'poster': art.get('poster') or '',
                'background': art.get('fanart') or '',
                'clearlogo': art.get('clearlogo') or '',
                'season': season, 'episode': episode,
                'position': 0.0, 'duration': 0.0, 'percent': 0.0,
                'stream_url': '', 'event_type': 'progress',
                'ext_updated_at': ts if ts and ts > 0 else 1,
                'imdb_id': str(ids.get('imdb') or ''),
                'tmdb_id': str(ids.get('tmdb') or ''),
                'show_tmdb_id': str(ids.get('tmdb') or ''),
                'tvdb_id': str(ids.get('tvdb') or ''),
            })
    # Empty successful snapshots must remove obsolete suggestions too.
    unique = {}
    for row in pending:
        key = row['canonical_id']
        if key not in unique or row['ext_updated_at'] > unique[key]['ext_updated_at']:
            unique[key] = row
    try:
        return int(playback_store.reconcile_simkl_up_next(list(unique.values())) or 0)
    except Exception as exc:
        xbmc.log('[DexHub] simkl continue batch sync failed: %s' % exc,
                 xbmc.LOGDEBUG)
        return 0


def watchlist_mirror_rows(limit=200, strict=False):
    """'Plan to watch' movies+shows shaped for favorites_store mirror rows."""
    if not (enabled() and authorized()):
        return []
    out = []
    for kind, media_type in (('movies', 'movie'), ('shows', 'series'), ('anime', 'series')):
        for row in fetch_all_items(kind, 'plantowatch', strict=strict)[:limit]:
            canonical = _canonical_for(row.get('ids') or {})
            if not canonical:
                continue
            out.append({
                'media_type': media_type,
                'canonical_id': canonical,
                'title': row.get('title') or '',
                'poster': row.get('poster') or '',
                'background': '',
                'clearlogo': '',
                'year': row.get('year') or 0,
                'plot': row.get('overview') or '',
            })
    return out
