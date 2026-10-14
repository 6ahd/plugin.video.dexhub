# -*- coding: utf-8 -*-
"""DexHub <-> service.upnext bridge.

Ported idea: the "Up Next" popup that appears *near the end* of an episode
so the viewer can jump to the next one before the credits finish — the same
behaviour Umbrella (and DPlex) rely on. Rather than reimplement a bespoke
countdown window, DexHub speaks the standard service.upnext protocol: it
sends a hex/base64-encoded ``upnext_data`` signal over AddonSignals. When
service.upnext is not installed, callers fall back to DexHub's own built-in
end-of-episode prompt (see companion._maybe_play_next).

The signal is a fire-and-forget notification. service.upnext owns the popup,
the countdown and the "still watching" logic; DexHub only supplies the
current/next episode metadata plus a play URL for the next episode.
"""

import binascii
import json
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui

ADDON_ID = 'plugin.video.dexhub'
UPNEXT_ID = 'service.upnext'
WINDOW_ID = 10000
SERIES_PROP_PREFIX = 'dexhub.series.'


def _text(value):
    return str(value or '').strip()


def upnext_installed():
    """True when service.upnext is installed AND enabled.

    Asked through the skin condition first: xbmcaddon.Addon() on a missing
    add-on logs an EXCEPTION line in kodi.log every time (v5.10.103).
    """
    try:
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % UPNEXT_ID):
            return False
    except Exception:
        return False
    try:
        xbmcaddon.Addon(UPNEXT_ID)
        return True
    except Exception:
        return False


def _encode(payload, encoding):
    raw = json.dumps(payload).encode('utf-8')
    if _text(encoding).lower() == 'base64':
        import base64
        return base64.b64encode(raw).decode('ascii')
    return binascii.hexlify(raw).decode('ascii')


def _notify_all(sender, message, data, encoding):
    """Minimal AddonSignals-compatible emitter.

    AddonSignals wraps the payload as ``["<encoded>"]`` and delivers it on
    the JSON-RPC ``NotifyAll`` channel under ``<sender>.SIGNAL.<message>``.
    We avoid a hard dependency on the script.module.addon.signals import and
    talk to the same channel directly, matching how DPlex/Umbrella consumers
    expect the data.
    """
    encoded = _encode(data, encoding)
    try:
        import AddonSignals  # noqa: N814 — provided by script.module.addon.signals
        AddonSignals.sendSignal(message, data, source_id=sender)
        return True
    except Exception:
        pass
    # Fallback: raw NotifyAll with the AddonSignals envelope shape.
    try:
        params = {
            'sender': '%s.SIGNAL' % sender,
            'message': message,
            'data': [encoded],
        }
        rpc = {
            'jsonrpc': '2.0',
            'method': 'JSONRPC.NotifyAll',
            'params': params,
            'id': 1,
        }
        xbmc.executeJSONRPC(json.dumps(rpc))
        return True
    except Exception as exc:
        xbmc.log('[DexHub] upnext notify failed: %s' % exc, xbmc.LOGWARNING)
        return False


def _series_videos(canonical):
    try:
        raw = xbmcgui.Window(WINDOW_ID).getProperty(
            SERIES_PROP_PREFIX + _text(canonical)) or ''
        videos = json.loads(raw) if raw else []
        return videos if isinstance(videos, list) else []
    except Exception:
        return []


def _find_video(videos, season, episode):
    for v in videos:
        try:
            if int(v.get('season') or 0) == season and int(v.get('episode') or 0) == episode:
                return v
        except Exception:
            continue
    return None


def _play_url(next_ep, same_source):
    s = int(next_ep.get('season') or 0)
    e = int(next_ep.get('episode') or 0)
    strict = bool(same_source and next_ep.get('source_provider_id'))
    params = {
        'action': 'play_next_same_source' if strict else 'play_item',
        'media_type': 'series',
        'canonical_id': _text(next_ep.get('canonical_id')),
        'video_id': _text(next_ep.get('video_id')) or _text(next_ep.get('canonical_id')),
        'season': str(s),
        'episode': str(e),
        'title': _text(next_ep.get('title')),
    }
    if strict:
        for src_key, route_key in (
                ('source_provider_id', 'source_provider_id'),
                ('server_id', 'source_server_id'),
                ('provider_name', 'preferred_provider_name'),
                ('binge_group', 'preferred_binge_group'),
                ('source_addon', 'preferred_source_addon'),
                ('source_indexer', 'preferred_source_indexer'),
                ('source_service', 'preferred_source_service'),
                ('source_type', 'preferred_source_type')):
            if next_ep.get(src_key):
                params[route_key] = next_ep.get(src_key)
    return 'plugin://%s/?%s' % (ADDON_ID, urllib.parse.urlencode(params))


def _episode_payload(canonical, title, season, episode, video, art_ctx):
    art_ctx = art_ctx or {}
    poster = _text(art_ctx.get('poster'))
    fanart = _text(art_ctx.get('background')) or poster
    clearlogo = _text(art_ctx.get('clearlogo'))
    thumb = _text((video or {}).get('thumbnail')) or _text((video or {}).get('thumb')) or fanart
    ep = {
        'episodeid': _text((video or {}).get('id')) or ('%s.s%se%s' % (canonical, season, episode)),
        'tvshowid': _text(canonical),
        'title': _text((video or {}).get('title')),
        'art': {
            'tvshow.poster': poster,
            'thumb': thumb,
            'tvshow.fanart': fanart,
            'tvshow.landscape': thumb or fanart,
            'tvshow.clearart': '',
            'tvshow.clearlogo': clearlogo,
        },
        'plot': _text((video or {}).get('overview')),
        'showtitle': _text(title),
        'season': season,
        'episode': episode,
        'firstaired': _text((video or {}).get('released') or (video or {}).get('firstaired')),
    }
    return ep


def notify(next_ep, ctx, *, same_source=True, encoding='hex'):
    """Send the service.upnext signal for the upcoming episode.

    :param next_ep: the next-episode hint (companion ctx['next_episode'])
    :param ctx: the active playback context (for artwork + current ep info)
    :returns: True when a signal was emitted
    """
    try:
        if not next_ep:
            return False
        if not upnext_installed():
            return False
        canonical = _text(next_ep.get('canonical_id'))
        title = _text(next_ep.get('title')) or _text(ctx.get('show_title')) or _text(ctx.get('title'))
        n_s = int(next_ep.get('season') or 0)
        n_e = int(next_ep.get('episode') or 0)
        if not canonical or n_s <= 0 or n_e <= 0:
            return False
        videos = _series_videos(canonical)
        next_video = _find_video(videos, n_s, n_e)

        cur_s = int(ctx.get('season') or 0)
        cur_e = int(ctx.get('episode') or 0)
        cur_video = _find_video(videos, cur_s, cur_e) if cur_s and cur_e else None

        payload = {
            'current_episode': _episode_payload(canonical, title, cur_s, cur_e, cur_video, ctx),
            'next_episode': _episode_payload(canonical, title, n_s, n_e, next_video, ctx),
            'play_url': _play_url(next_ep, same_source),
        }
        ok = _notify_all(ADDON_ID, 'upnext_data', payload, encoding)
        if ok:
            xbmc.log('[DexHub] upnext_data sent for %s S%02dE%02d' % (title, n_s, n_e), xbmc.LOGINFO)
        return ok
    except Exception as exc:
        xbmc.log('[DexHub] upnext notify error: %s' % exc, xbmc.LOGWARNING)
        return False
