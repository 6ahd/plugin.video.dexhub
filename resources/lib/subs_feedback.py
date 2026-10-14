# -*- coding: utf-8 -*-
"""AI subtitles quality feedback.

Lets the user thumb-down a bad AI-translated subtitle and send the
sub_id + stream context back to the DexWorld backend, so prompts can
be tuned for the categories that produce poor output.

Best-effort: if the network call fails, the user still gets a "thanks
for the feedback" toast — the data point is just lost. Never blocks
the player.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon

from .log import log

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()

# Default endpoint; user can override in settings (advanced) if you stand up
# a different bucket.
_FEEDBACK_URL = 'https://dexworld.cc/subtitles/api/feedback'
_TIMEOUT = 6


def _api_key():
    try:
        return (ADDON.getSetting('dexworld_api_key') or '').strip()
    except Exception:
        return ''


def _post_async(payload):
    def _send():
        try:
            data = json.dumps(payload).encode('utf-8')
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'DexHub/3.9',
            }
            api_key = _api_key()
            if api_key:
                headers['Authorization'] = 'Bearer %s' % api_key
            req = urllib.request.Request(_FEEDBACK_URL, data=data, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                if resp.status >= 400:
                    log.warn('SUBSFB', 'feedback http %s', resp.status)
        except urllib.error.HTTPError as e:
            log.warn('SUBSFB', 'feedback http %s', e.code)
        except Exception as exc:
            log.warn('SUBSFB', 'feedback failed: %s', exc)

    try:
        from .runtime_tasks import submit_optional
        submit_optional(
            _send,
            key='subtitle-feedback:%s' % abs(hash(json.dumps(
                payload, sort_keys=True, ensure_ascii=False))),
        )
    except Exception:
        pass


def report_bad(sub_id, stream_key, lang='', source='', reason='quality', notes=''):
    """Fire-and-forget bad-quality report. Always returns True so the UI
    can confirm "thanks" without waiting on the network."""
    if not sub_id:
        return False
    payload = {
        'kind': 'subtitle_quality',
        'sub_id': str(sub_id),
        'stream_key': str(stream_key or ''),
        'lang': str(lang or ''),
        'source': str(source or ''),
        'reason': str(reason or 'quality'),
        'notes': str(notes or '')[:500],
        'addon_version': str(xbmcaddon.Addon().getAddonInfo('version') or ''),
    }
    _post_async(payload)
    return True


