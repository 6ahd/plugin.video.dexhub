# -*- coding: utf-8 -*-
"""The IPTV provider's own file as a source row (v5.10.560).

The user, 12 Nov 2026: an IPTV film or series page plays its provider's file
straight away; Sources searches as for any title, with the IPTV file as one of
the sources. The page (skinui/actions.open_title) leaves its IPTV binding in a
Home property while it is open; a source search for the same title adds one
row: the film's file, or the episode's when the provider has that episode.
"""
import json
import re

import xbmc
import xbmcgui

PROVIDER_ID = '__iptv__'
COLOR = 'FFF59E0B'
PROP = 'dhs.tp.iptv.bind'


def _log(text, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] IPTV VOD source: %s' % text, level)


def bind(data):
    """What the open IPTV page plays (None clears it)."""
    home = xbmcgui.Window(10000)
    if data:
        home.setProperty(PROP, json.dumps(data))
    else:
        home.clearProperty(PROP)


def _bound():
    try:
        return json.loads(xbmcgui.Window(10000).getProperty(PROP) or '{}') or {}
    except ValueError:
        return {}


def _same(bound, canonical_id, ids):
    ids = dict(ids or {})
    canonical = str(canonical_id or '').split(':')[0] if str(canonical_id or '').startswith('tt') \
        else str(canonical_id or '')
    mine = {str(bound.get('canonical') or ''), 'tmdb:%s' % bound.get('tmdb') if bound.get('tmdb') else '',
            str(bound.get('imdb') or '')} - {''}
    theirs = {canonical, 'tmdb:%s' % ids.get('tmdb_id') if ids.get('tmdb_id') else '',
              str(ids.get('imdb_id') or '')} - {''}
    return bool(mine & theirs)


def targets(meta, canonical_id, media_type, ids=None, season=None, episode=None):
    bound = _bound()
    if not bound or not _same(bound, canonical_id, ids):
        return []
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    if episodic != bool(bound.get('vs')):
        return []
    return [({'id': PROVIDER_ID, 'name': 'IPTV VOD', '_bound': bound, '_season': season,
              '_episode': episode}, str(canonical_id))]


def _quality(name):
    text = str(name or '').upper()
    if re.search(r'(?<![0-9])(?:4K|2160P?|UHD)(?![0-9])', text):
        return '2160p'
    if re.search(r'1080', text) or re.search(r'(?<![0-9])FHD(?![0-9])', text):
        return '1080p'
    if re.search(r'720', text):
        return '720p'
    return ''


def payload(provider):
    from .homeui import rows as R
    from .homeui import vod
    bound = provider.get('_bound') or {}
    route = bound.get('vplay') or ''
    if bound.get('vs'):
        try:
            route = vod.episode_routes(bound['vs'], int(provider.get('_season') or 0), bound.get('title') or '').get(
                int(provider.get('_episode') or 0)) or ''
        except Exception:
            route = ''
    if not route:
        _log('no file at the provider for this one')
        return {'streams': []}
    try:
        stream = vod.direct_stream(R.params_of(route))
    except Exception as exc:
        _log('the address was not found: %s' % type(exc).__name__, xbmc.LOGWARNING)
        return {'streams': []}
    if not stream:
        return {'streams': []}
    params = R.params_of(route)
    raw = params.get('title') or bound.get('title') or ''
    quality = _quality(raw) or _quality(bound.get('raw') or '') or 'SD'
    ext = str(params.get('ext') or '').upper()
    where = bound.get('provider') or 'IPTV'
    row = {'name': '[COLOR %s]IPTV VOD[/COLOR] %s\n%s' % (COLOR, where, quality),
           'title': '%s\n%s' % (raw, ' · '.join(x for x in (ext, where) if x)),
           'url': stream['url'],
           'behaviorHints': {'bingeGroup': 'dexhub-iptv-%s' % (bound.get('src') or ''),
                             'filename': '%s.%s' % (raw, (params.get('ext') or 'mp4'))}}
    _log('1 file for %s' % (bound.get('title') or '?'))
    return {'streams': [row]}
