# -*- coding: utf-8 -*-
"""Read ordinary external Kodi directories without importing provider code."""
import html
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def clean_path(raw):
    value = html.unescape(str(raw or '')).strip()
    if not value.startswith(('plugin://', 'videodb://', 'library://')):
        return ''
    parsed = urlsplit(value)
    # Remove skin-specific refresh expressions, preserving public list filters.
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k not in ('reload', 'widget', 'cacheonly', 'nextpage', 'length') and not any(mark in v for mark in ('$INFO[', '$VAR[', '$LOCALIZE['))]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), ''))


def _from_rpc(row):
    from .homeui.rows import tile_from_entry
    kind = str(row.get('type') or '')
    route = dict(parse_qsl(urlsplit(row.get('file') or '').query))
    if kind not in ('movie','tvshow','episode','season') and route.get('tmdb_type') in ('movie','tv'):
        kind = ('episode' if route.get('episode') else 'season' if route.get('season') else 'tvshow') if route['tmdb_type']=='tv' else 'movie'
    info = {key: row.get(key) for key in ('title', 'plot', 'year', 'rating', 'genre', 'studio', 'runtime',
                                       'mpaa', 'season', 'episode', 'tvshowtitle', 'premiered')}
    info['mediatype'] = {'movie': 'movie', 'tvshow': 'tvshow', 'episode': 'episode', 'season': 'season'}.get(kind, 'video')
    info['tvshowtitle'] = row.get('showtitle') or row.get('tvshowtitle') or ''
    info['duration'] = row.get('runtime') or 0
    ids = row.get('uniqueid') or {}
    if not isinstance(ids, dict):
        ids = {}
    ids = {name + '_id': str(ids.get(name + '_id') or ids.get(name) or '') for name in ('imdb','tmdb','tvdb')}
    if not ids['imdb_id'] and str(row.get('imdbnumber') or '').startswith('tt'):
        ids['imdb_id'] = str(row['imdbnumber'])
    tile = tile_from_entry({'path': row.get('file') or '', 'label': row.get('label') or row.get('title') or '',
                            'folder': row.get('filetype') == 'directory' or kind in ('tvshow', 'season'),
                            'art': row.get('art') or {'thumb': row.get('thumbnail') or ''},
                            'info': info, 'ids': ids})
    # Helper details already resolve to the Dex Hub title page elsewhere.
    # Apply that same route here; episodes and other add-ons retain theirs.
    source = urlsplit(tile.get('path') or '')
    if (source.netloc == 'plugin.video.themoviedb.helper' and tile.get('kind') == 'work'
            and tile.get('media_type') in ('movie', 'series') and (ids['tmdb_id'] or ids['imdb_id'])):
        from .skinui.common import url
        canonical = ids['imdb_id'] or 'tmdb:' + ids['tmdb_id']
        tile['path'] = url('item_open', canonical_id=canonical, media_type=tile['media_type'], tmdb_id=ids['tmdb_id'])
        tile['folder'] = True
    return tile


def browse(path):
    """Kodi owns external listings; do not import another add-on's Python."""
    import xbmc
    path = clean_path(path)
    if not path:
        return []
    fields = ['title', 'plot', 'year', 'rating', 'genre', 'studio', 'runtime', 'mpaa', 'season', 'episode',
              'showtitle', 'premiered', 'art', 'thumbnail', 'uniqueid', 'imdbnumber']
    reply = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Files.GetDirectory',
                                'params': {'directory': path, 'media': 'video', 'properties': fields}})))
    if reply.get('error'):
        raise RuntimeError('External catalog directory failed: %s' % reply['error'].get('code'))
    return [_from_rpc(row) for row in (reply.get('result') or {}).get('files') or []][:80]
