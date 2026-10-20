"""Kodi projection of Silo Apple's owner-bound playback transport.

Adapted behavior: source/player timelines, captured authority, sequenced v2
progress and one atomic final stop. Apple codec claims are deliberately not
copied: they describe Aether/Apple output, not Kodi's decoder or HDMI sink.
"""
import math
import threading
import uuid
from urllib.parse import urlencode


def _seconds(value):
    value = float(value or 0)
    if not math.isfinite(value) or value < 0:
        raise ValueError('توقيت خطة Silo غير صالح')
    return value


def early_end(position_ms, duration_ms):
    """Kodi can turn an incomplete direct HTTP response into a normal EOF."""
    position, duration = float(position_ms or 0), float(duration_ms or 0)
    return (math.isfinite(position) and math.isfinite(duration) and duration > 0
            and position >= 0 and duration - position > 8000)


def session_context(auth, major, installation, request, decision):
    plan = decision.get('playback_plan') or {}
    sid = str(decision.get('session_id') or '')
    if not sid or (plan.get('session_id') and str(plan['session_id']) != sid):
        raise ValueError('هوية خطة Silo لا تطابق الجلسة')
    if major == 2:
        if not plan or str(uuid.UUID(sid)) != sid:
            raise ValueError('هوية جلسة Silo غير صالحة')
    if (plan.get('stream') or {}).get('header_refresh') not in (None, '', 'none'):
        raise ValueError('خطة Silo تحتاج تجديد ترويسات أثناء البث؛ هذه الآلية غير متاحة في مشغّل Kodi')
    timeline = dict(plan.get('timeline') or {})
    for key in ('source_start_seconds', 'stream_origin_seconds', 'player_start_seconds',
                'timeline_offset_seconds', 'seek_window_start_seconds', 'seek_window_end_seconds'):
        if key in timeline and timeline[key] is not None:
            _seconds(timeline[key])
    if (timeline.get('seek_window_start_seconds') is not None and
            timeline.get('seek_window_end_seconds') is not None and
            timeline['seek_window_start_seconds'] > timeline['seek_window_end_seconds']):
        raise ValueError('نافذة تقديم Silo غير صالحة')
    # A playback context needs scoped access proof, never the rotating refresh
    # secret. Renewal is owned by the account store and its single-flight lock.
    scoped = {k: auth[k] for k in ('url', 'account_id', 'profile_id', 'profile_token', 'token',
                                 'access_token') if k in auth}
    return {'api_major': major, 'installation_id': installation, 'auth': scoped,
            'session_id': str(decision.get('session_id') or ''),
            'plan_id': str(plan.get('plan_id') or ''),
            'plan_attempt_key': str(plan.get('plan_attempt_key') or ''),
            'timeline': timeline, 'delivery': str(plan.get('delivery') or ''),
            'source_duration': _seconds((plan.get('source') or {}).get('duration_seconds')),
            'sequence': 0, 'stop_id': str(uuid.uuid4()),
            'playback_attempt_id': str(request.get('playback_attempt_id') or '')}


def stream_headers(auth, stream):
    values = {'Authorization': 'Bearer ' + str(auth.get('token') or auth.get('access_token') or '')}
    for field, header in (('profile_id', 'X-Profile-Id'), ('profile_token', 'X-Profile-Token')):
        if auth.get(field):
            values[header] = str(auth[field])
    # The plan can supply transport-specific headers. Never flatten a map
    # into query parameters or silently discard it.
    for key, value in (stream.get('headers') or {}).items():
        if any(c in str(key) + str(value) for c in ('\r', '\n')):
            raise ValueError('ترويسة خطة Silo غير صالحة')
        values[str(key)] = str(value)
    return urlencode(values)


class NativeReporter:
    def __init__(self):
        self._lock = threading.RLock()
        self._paused = False
        self._closed = set()

    def _send(self, ctx, position_ms, stop=False):
        from . import silo_provider as provider
        sid = str(ctx.get('session_id') or '')
        if not sid or sid in self._closed:
            return
        session = ctx.get('silo_session') or {}
        auth = dict(session.get('auth') or {})
        if not auth:
            auth = dict(provider.account() or {})
            if provider._base(auth.get('url')) != provider._base(ctx.get('server_url')):
                raise provider.SiloError('تغير سيرفر Silo؛ أعد فتح المصدر')
            session = dict(session, auth=auth)
            ctx['silo_session'] = session
        current = dict(provider.account() or {})
        if not provider._same_account(auth, current):
            raise provider.SiloError('تغير حساب Silo؛ تجاهل تحديث الجلسة السابقة')
        # Rotate access proof only within the captured account. Keep its
        # original profile/PIN proof even when the UI selects another profile.
        auth = provider._renewed_auth(auth, current)
        major = session.get('api_major') or provider.api_major(auth)
        path = 'playback/' + sid
        position = _seconds(float(position_ms or 0) / 1000.0)
        if major == 2:
            if not session.get('installation_id'):
                raise provider.SiloError('هوية جلسة Silo مفقودة؛ أعد تشغيل المصدر')
            session['sequence'] = int(session.get('sequence') or 0) + 1
            sample = {'installation_id': session['installation_id'], 'sequence': session['sequence'],
                      'position': position, 'is_paused': self._paused}
            if stop:
                session.setdefault('stop_id', str(uuid.uuid4()))
                sample['stop_id'] = session['stop_id']
            if stop:
                self._closed.add(sid)
            try:
                value = self._request(provider, path if stop else path + '/progress',
                                      method='DELETE' if stop else 'POST', data=sample, auth=auth, major=2)
            except provider.SiloError as exc:
                if not stop and self._terminal(ctx, sid, exc):
                    return
                raise
            allowed = ('stopped', 'replayed') if stop else ('applied', 'replayed', 'stale_sample')
            if not isinstance(value, dict) or value.get('outcome') not in allowed:
                raise provider.SiloError('رد تحديث جلسة Silo غير صالح')
            if stop and value.get('outcome') == 'stopped' and value.get('stop_id') != sample['stop_id']:
                raise provider.SiloError('رد إنهاء جلسة Silo لا يطابق الطلب')
        else:
            if stop:
                self._closed.add(sid)
            try:
                try:
                    provider._api(path + '/progress', method='POST', auth=auth, major=1,
                                  data={'position': position, 'is_paused': self._paused})
                except provider.SiloError as exc:
                    if not stop and self._terminal(ctx, sid, exc):
                        return
                    raise
            finally:
                if stop:
                    provider._api(path, method='DELETE', auth=auth, major=1)
                    self._closed.add(sid)
        if stop:
            self._closed.add(sid)

    @staticmethod
    def _request(provider, path, **kwargs):
        # Only an unanswered transport request is resent, once. In v2 the
        # sequence and stop_id make this exact resend idempotent.
        try:
            return provider._api(path, **kwargs)
        except provider.SiloError as exc:
            if exc.status != 0:
                raise
            return provider._api(path, **kwargs)

    def _terminal(self, ctx, sid, exc):
        problem = str(exc.problem or '').rstrip('/').rsplit('/', 1)[-1]
        if exc.status == 410 and problem == 'playback_session_ended':
            ctx['silo_session_ended'] = True
        elif ((exc.status == 404 and problem == 'playback_session_not_found') or
              (exc.status == 409 and problem == 'installation_changed')):
            ctx['silo_session_missing'] = True
        else:
            return False
        self._closed.add(sid)
        return True

    def started(self, ctx, position_ms=0):
        with self._lock:
            self._paused = False
            return self._send(ctx, position_ms)

    def progress(self, ctx, position_ms):
        with self._lock:
            self._paused = False
            return self._send(ctx, position_ms)

    def paused(self, ctx, position_ms):
        with self._lock:
            self._paused = True
            return self._send(ctx, position_ms)

    def paused_tick(self, ctx, position_ms):
        return self.paused(ctx, position_ms)

    def stopped(self, ctx, position_ms):
        with self._lock:
            return self._send(ctx, position_ms, stop=True)

    def ended(self, ctx, position_ms=0):
        return self.stopped(ctx, position_ms or int(ctx.get('duration_ms') or 0))
