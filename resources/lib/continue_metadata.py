"""Local-only metadata for Kodi's standard Continue Watching listing/widget."""
from . import tmdbhelper


def enrich(row, ids, source='auto', is_episode=False):
    out = dict(row)
    # v5.10.286 (GitHub #7): "Like Nuvio" fills only what the row lacks
    fill_only = source == 'native'
    if source not in ('', 'auto', 'tmdb_helper') and not fill_only:
        return out
    tmdb = row.get('show_tmdb_id') or row.get('tmdb_id') or ids.get('tmdb_id') or ''
    imdb = row.get('imdb_id') or ids.get('imdb_id') or ''
    if not (tmdb or imdb):
        return out
    cached = tmdbhelper.get_meta_bundle_from_db(tmdb_id=tmdb, imdb_id=imdb, media_type='tv' if is_episode else 'movie') or {}
    # Deliberately exclude IDs/title/duration/position/percent and routing data.
    fields = ('plot', 'tagline', 'genre', 'studio', 'country', 'cast', 'director',
              'writer', 'rating', 'votes', 'year', 'premiered', 'mpaa',
              'poster', 'background', 'clearlogo')
    for key in fields:
        if cached.get(key) not in (None, '', [], {}) and not (fill_only and row.get(key) not in (None, '', [], {})):
            out[key] = cached[key]
    if is_episode:
        # Helper exposes a TV network as Kodi's studio (skin network logos).
        if cached.get('network') and not (fill_only and row.get('studio')):
            out['studio'] = cached['network']
        details = tmdbhelper.get_episode_details_from_db(tmdb_id=tmdb, imdb_id=imdb, season=row.get('season'), episode=row.get('episode'))
        for key in ('plot', 'rating', 'votes', 'year', 'premiered'):
            if details.get(key) not in (None, '') and not (fill_only and row.get(key) not in (None, '', [], {})):
                out[key] = details[key]
        if details.get('title'):
            out['_episode_display_title'] = details['title']
    return out
