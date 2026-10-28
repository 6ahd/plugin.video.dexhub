"""Turn a browsed title into one details request; explicit play stays play."""
import hashlib
from urllib.parse import urlsplit,parse_qsl,urlencode
BASE='plugin://plugin.video.dexhub/'
SERVER_PLAY={'plex_play','emby_play','jellyfin_play','silo_play'}
SERVER_SHOW={'plex_children','emby_children','jellyfin_children','silo_series'}


def query_for(path,li,folder=False):
    from .widgetclick import title_query,_year
    parsed=urlsplit(path);params=dict(parse_qsl(parsed.query))
    action=params.get('action','')
    def tag(name,*args):
        try:return getattr(li.getVideoInfoTag(),name)(*args) or ''
        except Exception:return ''
    media=str(tag('getMediaType') or params.get('media_type') or '').lower()
    # Continue Watching, episode and source/version requests remain direct.
    if any(params.get(k) for k in ('resume_seconds','force_picker','stream_key','preferred_version')):
        return []
    if media in ('episode','season') or params.get('episode') or params.get('season'):
        return []
    server=False
    if parsed.netloc=='plugin.video.dexhub' and parsed.scheme=='plugin':
        server=(action in SERVER_PLAY and media=='movie') or (action in SERVER_SHOW and media in ('tvshow','show','series'))
        if server and action=='plex_children' and not params.get('key','').startswith('/library/metadata/'):
            return []
        work=server or action in ('item_open','series_meta') or (
            action in ('streams','play_item') and not folder and
            str(params.get('media_type') or media or 'movie').lower() not in ('tvshow','show','series','anime'))
        if not work:return []
    elif parsed.netloc=='plugin.video.themoviedb.helper' and params.get('info') in ('details','seasons'):
        params['media_type']='series' if params.get('tmdb_type')=='tv' else 'movie'
    else:return []
    seed=dict(params)
    if server:seed['media_type']='series' if media in ('tvshow','show','series') else 'movie'
    seed.setdefault('title',tag('getTitle') or li.getLabel())
    seed.setdefault('imdb_id',tag('getUniqueID','imdb'))
    seed.setdefault('tmdb_id',tag('getUniqueID','tmdb'))
    if server and not any(seed.get(k) for k in ('canonical_id','imdb_id','tmdb_id')):
        seed['canonical_id']='srv:'+hashlib.sha1(path.encode()).hexdigest()[:16]
    query=title_query(seed,_year(li))
    if server and query:
        query.append(('srv',urlencode(sorted(params.items()))))
    return query
