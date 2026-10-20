"""Silo's mandatory session lease is separate from optional cloud scrobbling.

Protocol behavior adapted from Silo Apple's captured session authority,
sequenced progress, missing-session recovery and terminal-stop distinction.
No source search, player/codec assumptions or permission fallback is added.
"""
import json
import math
import time
import uuid
import xbmc
import xbmcgui
from .session_store import load_session, save_session
from .i18n import tr
from .providers import silo_provider as provider
from .providers.silo_playback import NativeReporter


def send_report(reporter, ctx, method, position_ms, sync_enabled):
    if sync_enabled:
        return getattr(reporter, method)(ctx, position_ms)
    # A disabled cloud-sync toggle cannot revoke the stream's control lease.
    # Filter a MultiReporter too: Trakt/Simkl/Nuvio remain disabled.
    reporters = getattr(reporter, 'reporters', None)
    if isinstance(reporters, (list, tuple)):
        return [send_report(r, ctx, method, position_ms, False) for r in reporters]
    if isinstance(reporter, NativeReporter) and ctx.get('server_type') == 'silo':
        return getattr(reporter, method)(ctx, position_ms)
    return None


def _owns(player, ctx):
    if getattr(player, '_foreign_active', False) or not player.isPlayingVideo():
        return False
    uid = str(ctx.get('playback_uid') or '')
    stored = load_session() or {}
    if not uid or stored.get('playback_uid') != uid:
        return False
    current = str(player.getPlayingFile() or '').split('|', 1)[0]
    expected = str(ctx.get('stream_url') or '').split('|', 1)[0]
    return bool(current and expected and current == expected)


def _captured(ctx):
    auth = dict((ctx.get('silo_session') or {}).get('auth') or {})
    current = dict(provider.account() or {})
    if not auth or not provider._same_account(auth, current):
        return None
    # Progress can finish for a captured profile, but recovery must not start
    # playback after the user selected a different household profile.
    if str(auth.get('profile_id') or '') != str(current.get('profile_id') or ''):
        return None
    return provider._renewed_auth(auth, current)


def _discard(resolved, original):
    try:
        session = resolved.get('silo_session') or {}
        ctx = dict(original, session_id=resolved.get('play_session_id') or '', silo_session=session)
        NativeReporter().stopped(ctx, 0)
    except Exception:
        pass  # Never let a late cleanup play, or affect a replacement session.


def _recover(player, ctx):
    if xbmc.getCondVisibility('Player.Paused') or not _owns(player, ctx):
        return
    attempts = int(ctx.get('silo_recovery_attempts') or 0)
    if attempts >= 2:
        if not ctx.get('silo_recovery_notified'):
            ctx['silo_recovery_notified'] = True
            xbmcgui.Dialog().notification('Silo', tr('تعذر تجديد جلسة المصدر؛ أعد فتحه'), time=5000)
        return
    now = time.monotonic()
    if now < float(ctx.get('silo_recovery_after') or 0):
        return
    auth = _captured(ctx)
    item_id, version = str(ctx.get('item_id') or ''), str(ctx.get('media_source_id') or '')
    if not auth or not item_id or not version:
        return
    position = float(player._position_ms()) / 1000.0
    if not math.isfinite(position) or position < 0:
        return
    ctx['silo_recovery_attempts'] = attempts + 1
    ctx['silo_recovery_after'] = now + 15
    resolved = None
    try:
        resolved = provider.resolve_playback(auth, item_id, version,
                                             start_position=position, strict_version=True)
        if not _owns(player, ctx) or not _captured(ctx) or xbmc.getCondVisibility('Player.Paused'):
            _discard(resolved, ctx)
            return
        from . import player_runtime as api
        if not _owns(player, ctx):
            _discard(resolved, ctx)
            return
        updated = dict(ctx)
        updated.update(stream_url=resolved['url'], session_id=resolved['play_session_id'],
                       silo_session=resolved['silo_session'], native_playback_resolved=True,
                       resume_seconds=position, token=auth.get('token') or auth.get('access_token') or '')
        updated.pop('silo_session_missing', None)
        updated.pop('silo_session_ended', None)
        # Same file/cut: seconds are authoritative, not an old percentage.
        updated.pop('resume_percent', None)
        subs = (resolved.get('version') or {}).get('subtitles')
        if subs is not None:
            updated['subtitles'] = list(subs)
        duration = float(player._duration_ms() or 0) / 1000.0
        handoff = {'token': uuid.uuid4().hex, 'old_uid': ctx['playback_uid'],
                   'position': position, 'duration': duration, 'resume_percent': 0,
                   'created_at': time.time(), 'expires_at': time.time() + 8, 'ack': False}
        xbmcgui.Window(10000).setProperty('dexhub.source_switch_handoff.v2', json.dumps(handoff))
        xbmcgui.Window(10000).setProperty('dexhub.switch.inplace', str(int(time.time())))
        xbmc.log('[DexHub] Silo session renewed on the same selected file at %.1fs' % position, xbmc.LOGINFO)
        api._play_with_context(updated, stream_key=ctx.get('stream_key') or '', use_resolved_url=False)
    except Exception as exc:
        if resolved:
            _discard(resolved, ctx)
        xbmc.log('[DexHub] Silo session renewal failed: %s' % type(exc).__name__, xbmc.LOGWARNING)
        # A new start that was explicitly refused is not retried forever.
        if isinstance(exc, provider.SiloError) and exc.status and exc.status not in (408, 429) and exc.status < 500:
            ctx['silo_recovery_attempts'] = 2


def maintain(player):
    ctx = getattr(player, 'ctx', None) or {}
    if not ctx:
        # Service startup/update can happen after the Started callback. Adopt
        # only a persisted Silo session whose exact stream is currently playing.
        pending = load_session() or {}
        if pending.get('server_type') != 'silo' or not _owns(player, pending):
            return
        player._refresh_context()
        player._start_progress_heartbeat()
        ctx = player.ctx
    if ctx.get('server_type') != 'silo' or not _owns(player, ctx):
        return
    if ctx.get('silo_session_ended'):
        # Explicit administrator stop is terminal, just as in Silo Apple.
        player.stop()
    elif ctx.get('silo_session_missing'):
        _recover(player, ctx)
