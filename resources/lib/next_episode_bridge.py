# -*- coding: utf-8 -*-
"""Small cross-add-on contract for TIDB's Next Episode button."""
import json
import urllib.parse


PROPERTY = 'dexhub.tidb.next_episode.v1'
WINDOW_ID = 10000


def _text(value):
    return str(value or '').strip()


def build_route(next_episode, same_source=True):
    """Build either strict same-source playback or the full source results."""
    row = dict(next_episode or {})
    canonical = _text(row.get('canonical_id'))
    video_id = _text(row.get('video_id')) or canonical
    try:
        season = int(row.get('season') or 0)
        episode = int(row.get('episode') or 0)
    except (TypeError, ValueError):
        return ''
    if not canonical or season < 0 or episode <= 0:
        return ''

    source_provider_id = _text(
        row.get('source_provider_id') or row.get('provider_id'))
    strict = bool(same_source and source_provider_id)
    params = {
        'action': 'play_next_same_source' if strict else 'episode_streams',
        'media_type': 'series',
        'canonical_id': canonical,
        'video_id': video_id,
        'season': str(season),
        'episode': str(episode),
        'title': _text(row.get('title')) or canonical,
    }
    if strict:
        for source_key, route_key in (
                ('source_provider_id', 'source_provider_id'),
                ('server_id', 'source_server_id'),
                ('provider_name', 'preferred_provider_name'),
                ('binge_group', 'preferred_binge_group'),
                ('source_addon', 'preferred_source_addon'),
                ('source_indexer', 'preferred_source_indexer'),
                ('source_service', 'preferred_source_service'),
                ('source_type', 'preferred_source_type')):
            value = _text(row.get(source_key))
            if source_key == 'source_provider_id' and not value:
                value = _text(row.get('provider_id'))
            if value:
                params[route_key] = value
    return 'plugin://plugin.video.dexhub/?' + urllib.parse.urlencode(params)


def payload(next_episode, same_source=True, playback_uid=''):
    route = build_route(next_episode, same_source=same_source)
    if not route:
        return {}
    row = dict(next_episode or {})
    return {
        'version': 1,
        'url': route,
        'title': _text(row.get('title')),
        'season': int(row.get('season') or 0),
        'episode': int(row.get('episode') or 0),
        'same_source': bool(same_source and _text(
            row.get('source_provider_id') or row.get('provider_id'))),
        'playback_uid': _text(playback_uid),
    }


def publish(next_episode, same_source=True, playback_uid='', window=None):
    data = payload(next_episode, same_source=same_source,
                   playback_uid=playback_uid)
    if window is None:
        import xbmcgui
        window = xbmcgui.Window(WINDOW_ID)
    if data:
        window.setProperty(PROPERTY, json.dumps(
            data, ensure_ascii=False, separators=(',', ':')))
    else:
        window.clearProperty(PROPERTY)
    return data


def clear(playback_uid='', window=None):
    """Clear this playback only, never a newer source-switch replacement."""
    if window is None:
        import xbmcgui
        window = xbmcgui.Window(WINDOW_ID)
    wanted = _text(playback_uid)
    if wanted:
        try:
            current = json.loads(window.getProperty(PROPERTY) or '{}')
        except Exception:
            current = {}
        current_uid = _text((current or {}).get('playback_uid'))
        if current_uid and current_uid != wanted:
            return False
    window.clearProperty(PROPERTY)
    return True
