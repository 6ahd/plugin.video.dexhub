# -*- coding: utf-8 -*-
"""IPTV VOD on demand. Source IDs stay exact; no channel URL guessing."""
import hashlib
import os
import re
import time
from urllib.parse import urlencode, quote
from . import live_sources as LS
from . import live_stremio as ST
from . import live_iptv as XT
from . import rows as R
from . import stremio_catalogs as SC


def _name(text):
    """A panel's name as Kodi can draw it (v5.10.199: look-alike letters made plain)."""
    from .live import clean
    return clean(text)


TYPES = ('movie', 'series')
PAGE = 40
TTL = 600
_recent = {}  # Two open source pages; pagination reuses their parsed metadata.


def url(route_action, **params):
    return R.BASE + '?' + urlencode(dict(params, action=route_action))


def available(source):
    from ..ui_preferences import iptv_available
    allowed = [m for m in TYPES if iptv_available(m)]
    if not allowed:
        return []
    if source.get('kind') == 'xtream':
        return allowed
    if source.get('kind') != 'stremio':
        return []
    provider = ST.provider_for(source['key']) or {}
    return [m for m in allowed if catalogs(provider, m)]


def catalogs(provider, media):
    return [c for c in ((provider or {}).get('manifest') or {}).get('catalogs') or []
            if isinstance(c, dict) and SC.browsable(c) and c.get('type') == media
            and not SC.is_live(c)]


def cached(app, source, key, read):
    if source.startswith('xt:'):
        conf = XT._conf(app, source)
        identity = '|'.join(str(conf.get(k) or '') for k in ('server', 'username', 'password'))
    else:
        provider = ST.provider_for(source) or {}
        identity = str(provider.get('manifest_url') or provider.get('base_url') or '')
    mark = hashlib.sha256((source + '|' + identity + '|' + key).encode('utf-8')).hexdigest()[:24]
    prefix = hashlib.sha256(source.encode('utf-8')).hexdigest()[:12]
    path = os.path.join(LS.cache_dir(app.profile), 'vod_%s_%s.json' % (prefix, mark))
    hit = _recent.get(path) or LS.read_json(path, {}) or {}
    if isinstance(hit, dict) and time.time() - float(hit.get('t') or 0) < TTL:
        _remember(path, hit)
        return hit.get('data')
    data = read()  # Failure is not cached as an empty successful response.
    hit = {'t': time.time(), 'data': data}
    LS.write_json(path, hit)
    _remember(path, hit)
    return data


def _remember(path, hit):
    if path not in _recent and len(_recent) >= 2:
        _recent.pop(next(iter(_recent)), None)
    _recent[path] = hit


def invalidate(profile, source):
    """The Refresh command drops just this source's VOD pages."""
    prefix = 'vod_%s_' % hashlib.sha256(str(source).encode('utf-8')).hexdigest()[:12]
    folder = LS.cache_dir(profile)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for path in list(_recent):
        if os.path.dirname(path) == folder and os.path.basename(path).startswith(prefix):
            _recent.pop(path, None)
    for name in names:
        if name.startswith(prefix) and name.endswith('.json'):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def rows(source, app, media):
    if media not in available(source):
        return []
    src = source['key']
    out = []
    if source['kind'] == 'xtream':
        conf = XT._conf(app, src)
        action = 'get_vod_categories' if media == 'movie' else 'get_series_categories'
        cats = cached(app, src, action, lambda: XT.xt_api(conf, action=action)) or []
        if not isinstance(cats, list):raise LS.SourceError('تعذر قراءة أقسام VOD')
        cats = [c for c in cats if isinstance(c, dict) and c.get('category_id') is not None]
        all_title = app.tr('كل الأفلام' if media == 'movie' else 'كل المسلسلات')
        art = app.media_path('collection_movies.png' if media == 'movie' else 'collection_tv.png')
        # v5.10.133: "every title" reads the categories in turn, never the
        # whole library at once
        cards = [{'kind': 'nav', 'title': all_title, 'label': all_title,
                  'shape': 'landscape', 'poster': art, 'landscape': art, 'folder': True,
                  'path': url('home_vod_list', live_src=src, vod_type=media, all='1', ci=0, offset=0)}]
        for c in cats:
            params = {'live_src': src, 'vod_type': media, 'category': str(c['category_id']), 'offset': 0}
            cards.append({'kind': 'nav', 'title': _name(c.get('category_name')) or str(c['category_id']),
                          'label': _name(c.get('category_name')) or str(c['category_id']), 'shape': 'landscape',
                          'poster': art, 'landscape': art, 'folder': True,
                          'path': url('home_vod_list', **params)})
        out.append(R.Row('vod:%s:%s:categories' % (src, media),
                         app.tr('كتالوجات الأفلام' if media == 'movie' else 'كتالوجات المسلسلات'),
                         shape='landscape', kind='catalog', tiles=cards))
        # First category opens quickly; the others load only when chosen.
        cats = cats[:1]
    else:
        provider = ST.provider_for(src) or {}
        cats = catalogs(provider, media)
    for cat in cats:
        params = {'action': 'home_vod_list', 'live_src': src, 'vod_type': media, 'type': media,
                  'catalog': str(cat.get('id') or ''), 'category': str(cat.get('category_id') or ''),
                  'offset': 0, 'skip': 0}
        title = _name(cat.get('name')) or _name(cat.get('category_name')) or app.tr(
            'الأفلام' if media == 'movie' else 'المسلسلات')
        options, required = ST.genre_options(cat)
        if source['kind'] == 'stremio' and options:
            # Categories are navigation cards: don't download every category
            # merely to draw the page. Preserve the exact safe manifest label.
            art = app.media_path('collection_movies.png' if media == 'movie' else 'collection_tv.png')
            cards = [{'kind': 'nav', 'title': _name(genre), 'label': _name(genre), 'shape': 'landscape',
                      'poster': art, 'landscape': art, 'folder': True,
                      'path': url('home_vod_list', **dict(params, genre=genre))}
                     for genre in options]
            out.append(R.Row('vod:%s:%s:%s:groups' % (src, media, params['catalog']),
                             title + ' • ' + app.tr('الأقسام'), shape='landscape', kind='catalog', tiles=cards))
        choices = options if required or (source.get('dexworld') and options) else ['']
        for genre in choices:
            route = dict(params, genre=genre)
            row = R.Row('vod:%s:%s:%s:%s' % (src, media, route['catalog'] or route['category'], genre),
                        _name(genre) or title, shape='poster', kind='catalog', subtitle=source['label'],
                        meta={'params': route, 'vod': True})
            row.loader = lambda patience=0.0, q=route: load_catalog(q, app)
            out.append(row)
    return out


def load_catalog(route, app):
    from . import catalog_controls as CC
    return CC.load(route, app, lambda effective: page_tiles(effective, app))[0]


def categories(source, app, media):
    from ..ui_preferences import iptv_available
    if not iptv_available(media):
        return []
    """v5.10.133: the source's film or series categories for the Live guide's
    panel, as its channel categories are: [{'key', 'title', 'params',
    'count'}]. Only the list of categories is read here; a category's titles
    are read when it is opened, a page at a time (page_tiles)."""
    if media not in available(source):
        return []
    src = source['key']
    out = []
    if source['kind'] == 'xtream':
        conf = XT._conf(app, src)
        action = 'get_vod_categories' if media == 'movie' else 'get_series_categories'
        cats = cached(app, src, action, lambda: XT.xt_api(conf, action=action)) or []
        if not isinstance(cats, list):
            raise LS.SourceError('تعذر قراءة أقسام VOD')
        for c in cats:
            if not isinstance(c, dict) or c.get('category_id') is None:
                continue
            cid = str(c['category_id'])
            out.append({'key': 'vod:%s:%s' % (media, cid), 'title': _name(c.get('category_name')) or cid,
                        'params': {'live_src': src, 'vod_type': media, 'category': cid, 'offset': 0},
                        'count': ''})
        if out:
            # every title, one category after another (the panel's whole
            # library is never asked for at once: it can be tens of thousands)
            out.append({'key': 'vod:%s:all' % media,
                        'title': app.tr('كل الأفلام' if media == 'movie' else 'كل المسلسلات'),
                        'params': {'live_src': src, 'vod_type': media, 'all': '1', 'ci': 0, 'offset': 0},
                        'count': ''})
        return out
    provider = ST.provider_for(src) or {}
    for cat in catalogs(provider, media):
        options, required = ST.genre_options(cat)
        choices = options if required or (source.get('dexworld') and options) else ['']
        title = _name(cat.get('name')) or app.tr('الأفلام' if media == 'movie' else 'المسلسلات')
        for genre in choices:
            params = {'live_src': src, 'vod_type': media, 'type': media, 'catalog': str(cat.get('id') or ''),
                      'category': '', 'offset': 0, 'skip': 0, 'genre': genre}
            label = '%s  •  %s' % (title, _name(genre)) if genre else title
            out.append({'key': 'vod:%s:%s:%s' % (media, params['catalog'], genre), 'title': label,
                        'params': params, 'count': ''})
    return out


def _all_page(params, app):
    """A page of "every title": the categories in turn (v5.10.133)."""
    src = str(params.get('live_src') or '')
    media = params.get('vod_type')
    source = {'key': src, 'kind': 'xtream'}
    cats = [c for c in categories(source, app, media) if not c['params'].get('all')]
    ci = max(0, int(params.get('ci') or 0))
    while ci < len(cats):
        q = dict(cats[ci]['params'], offset=int(params.get('offset') or 0))
        tiles, more = page_tiles(q, app)
        if more:
            return tiles, dict(params, ci=ci, offset=more.get('offset') or 0)
        nxt = dict(params, ci=ci + 1, offset=0) if ci + 1 < len(cats) else None
        if tiles:
            return tiles, nxt
        # an empty category: on to the next one
        ci += 1
        params = dict(params, offset=0)
    return [], None


def _season_number(value):
    """One malformed provider video must not hide all valid seasons."""
    try:
        return int(value if value not in (None, '') else 0)
    except (TypeError, ValueError, OverflowError):
        return None


def _tile(meta, params, app, episode=False):
    media = params['vod_type'];src = params['live_src']
    mid = str(meta.get('id') or meta.get('stream_id') or meta.get('series_id') or '')
    title = _name(meta.get('name')) or _name(meta.get('title'))
    if not title:
        title = _name(params.get('title')) if episode else ''
        if title:
            number = meta.get('episode') if meta.get('episode') is not None else meta.get('episode_num')
            if number is not None:
                title += ' — S%s:E%s' % (meta.get('season') if meta.get('season') is not None
                                        else params.get('season', ''), number)
        title = title or str(mid)
    poster = meta.get('poster') or meta.get('stream_icon') or meta.get('cover') or params.get('poster') or ''
    background = meta.get('background') or meta.get('backdrop_path') or ''
    if isinstance(background, list):background = background[0] if background else ''
    # v5.10.133: a card a few hundred pixels wide does not need a 600x900
    # poster (TMDb links of the panels: w600_and_h900_bestv2)
    from ..image_policy import cap_url
    poster, background = cap_url(str(poster or ''), 'w342'), cap_url(str(background or ''), 'w1280')
    tmdb = str(meta.get('tmdb_id') or meta.get('tmdb') or params.get('tmdb_id') or '')
    imdb = str(meta.get('imdb_id') or params.get('imdb_id') or (mid if re.fullmatch(r'tt\d+', mid) else ''))
    ext = str(meta.get('container_extension') or 'mp4')
    q = {'live_src': src, 'vod_type': media, 'id': mid, 'title': title,
         'ext': ext, 'poster': poster, 'canonical_id': params.get('id') or mid,
         'tmdb_id': tmdb, 'imdb_id': imdb}
    if background:
        q['fanart'] = background
    if episode:
        season = meta.get('season') if meta.get('season') is not None else params.get('season')
        number = meta.get('episode') if meta.get('episode') is not None else meta.get('episode_num')
        q.update({'season': str(season) if season is not None else '',
                  'episode': str(number) if number is not None else '',
                  # v5.10.239: the series it belongs to (Continue Watching
                  # plays it again and opens its season)
                  'series': str(params.get('vod_series') or params.get('id') or ''),
                  'show_title': _name(params.get('title')) or '',
                  'show_tmdb_id': str(params.get('show_tmdb_id') or params.get('tmdb_id') or ''),
                  'show_imdb_id': str(params.get('show_imdb_id') or params.get('imdb_id') or '')})
        if params.get('poster'):
            q['poster'] = params.get('poster')
    folder = media == 'series' and not episode
    if folder:q['vod_series'] = mid
    path = url('home_vod_list' if folder else 'home_vod_play', **q)
    info = {'title': title, 'mediatype': 'episode' if episode else ('tvshow' if folder else 'movie'),
            'plot': meta.get('description') or meta.get('plot') or '',
            'season': q.get('season') or '', 'episode': q.get('episode') or '',
            'year': str(meta.get('year') or meta.get('releaseDate') or '')[:4],
            'rating': meta.get('imdbRating') or meta.get('rating') or 0}
    tile = R.tile_from_entry({'label': title, 'path': path, 'folder': folder, 'info': info,
                              'art': {'poster': poster, 'thumb': meta.get('thumbnail') or poster,
                                      'fanart': background, 'clearlogo': meta.get('logo') or ''},
                              'ids': {'tmdb_id': tmdb, 'imdb_id': imdb}}, 'landscape' if episode else 'poster')
    # IPTV grids must identify every film/show/episode, including titles
    # whose source has no poster or subtitle. The shared grid otherwise
    # names navigation/season cards only in its plain poster layout.
    tile['show_title'] = True
    return tile


def page_tiles(params, app):
    from ..ui_preferences import iptv_available
    if not iptv_available(params.get('vod_type') or ''):
        return [], None
    src = str(params.get('live_src') or '');media = params.get('vod_type')
    if media not in TYPES:raise LS.SourceError('نوع VOD غير مدعوم')
    if params.get('all') and src.startswith('xt:') and not params.get('vod_series'):
        return _all_page(params, app)
    if src.startswith('xt:'):
        conf = XT._conf(app, src)
        series = str(params.get('vod_series') or '')
        if series:
            data = cached(app, src, 'series:'+series, lambda: XT.xt_api(conf, action='get_series_info', series_id=series)) or {}
            episodes = data.get('episodes') or {}
            season = str(params.get('season') or '')
            if not season:
                tiles = []
                for number in sorted(episodes, key=lambda s: int(s) if str(s).isdigit() else 999):
                    q = dict(params, season=str(number))
                    tiles.append({'kind':'nav','title':app.tr('الموسم %s') % number,
                                  'label':app.tr('الموسم %s') % number,'folder':True,
                                  'poster':(data.get('info') or {}).get('cover') or params.get('poster') or '',
                                  'path':url('home_vod_list', **q)})
                return tiles, None
            metas = []
            for e in episodes.get(season) or []:
                meta = dict(e);meta.update({'thumbnail':(e.get('info') or {}).get('movie_image') or '', 'season':season})
                metas.append(meta)
            return [_tile(m,params,app,episode=True) for m in metas], None
        action = 'get_vod_streams' if media == 'movie' else 'get_series'
        category = str(params.get('category') or '')
        metas = cached(app, src, action+':'+category, lambda: XT.xt_api(conf, action=action, category_id=category)) or []
        if not isinstance(metas, list):raise LS.SourceError('تعذر قراءة VOD من المصدر')
    else:
        provider = ST.provider_for(src)
        if not provider:raise LS.SourceError('المصدر غير موجود')
        series = str(params.get('vod_series') or '')
        if series:
            data = cached(app, src, 'series:'+series,
                          lambda: ST._client().fetch_meta(provider, 'series', series, timeout_override=12)) or {}
            meta = data.get('meta') or {};videos = meta.get('videos') or []
            season = str(params.get('season') or '')
            if not season:
                numbers = sorted({number for number in (
                    _season_number(v.get('season')) for v in videos if isinstance(v,dict))
                    if number is not None})
                return [{'kind':'nav','title':app.tr('الموسم %s') % n,'label':app.tr('الموسم %s') % n,
                         'poster':meta.get('poster') or '', 'folder':True,
                         'path':url('home_vod_list', **dict(params,season=str(n)))} for n in numbers], None
            return [_tile(v,params,app,episode=True) for v in videos if isinstance(v,dict)
                    and _season_number(v.get('season')) is not None
                    and str(_season_number(v.get('season')))==season], None
        _ids, metas, raw = ST._add_on_page(provider, params, int(params.get('skip') or 0), 12)
        if int(params.get('skip') or 0) > 0 and not int(params.get('offset') or 0) and _ids:
            _first_ids, _, _ = ST._add_on_page(provider, params, 0, 12)
            if _ids[0] == str(params.get('after') or '') or set(_ids) <= set(_first_ids):
                return [], None
    offset = max(0, int(params.get('offset') or 0));page = metas[offset:offset+PAGE]
    more = None
    if offset+PAGE < len(metas):more = dict(params, offset=offset+PAGE)
    elif not src.startswith('xt:') and raw:
        # Only catalogs declaring skip get another network page.
        cat = next((c for c in catalogs(provider,media) if str(c['id'])==str(params.get('catalog'))), {})
        if SC.supports(cat, 'skip'):
            more = dict(params,offset=0,skip=int(params.get('skip') or 0)+raw,after=_ids[0] if _ids else '')
    return [_tile(m,params,app) for m in page if isinstance(m,dict)
            and (m.get('id') or m.get('stream_id') or m.get('series_id'))], more


def play(params):
    import xbmc, xbmcaddon, xbmcgui, xbmcvfs
    addon = xbmcaddon.Addon('plugin.video.dexhub')
    app = type('PlaybackSource', (), {'profile':os.path.join(xbmcvfs.translatePath(addon.getAddonInfo('profile')), 'homeui')})()
    src = str(params.get('live_src') or '');mid = str(params.get('id') or '')
    if params.get('vod_type') not in TYPES:raise LS.SourceError('نوع VOD غير مدعوم')
    if not mid:return
    if src.startswith('xt:'):
        conf = XT._conf(app,src);ext = str(params.get('ext') or 'mp4')
        if not re.fullmatch(r'[A-Za-z0-9]{1,8}',ext):ext='mp4'
        if not mid.isdigit():raise LS.SourceError('معرّف VOD غير صالح')
        kind = 'series' if params.get('vod_type')=='series' else 'movie'
        address = '%s/%s/%s/%s/%s.%s' % (XT._xt_base(conf),kind,quote(str(conf.get('username') or ''),safe=''),quote(str(conf.get('password') or ''),safe=''),mid,ext)
        stream = LS.stream_for(address)
    else:
        provider = ST.provider_for(src)
        if not provider:raise LS.SourceError('المصدر غير موجود')
        found = ST._client().fetch_streams(provider,params.get('vod_type') or 'movie',mid,timeout_override=12) or {}
        streams = [s for s in found.get('streams') or [] if isinstance(s,dict) and s.get('url')]
        if not streams:raise LS.SourceError('لا توجد روابط تشغيل لهذا العمل')
        at = xbmcgui.Dialog().select(params.get('title') or 'VOD',[s.get('title') or s.get('name') or 'Stream %d'%(i+1) for i,s in enumerate(streams)]) if len(streams)>1 else 0
        if at<0:return
        chosen=streams[at];hints=chosen.get('behaviorHints') or {}
        headers=(hints.get('proxyHeaders') or {}).get('request') or {}
        stream=LS.stream_for(chosen['url'],headers,props=chosen.get('properties') or {})
    if not stream:raise LS.SourceError('تعذر تشغيل هذا العمل')
    resume = _resume_point(params)
    if resume is None:
        return
    _arm_session(params, stream['url'], resume)
    item=xbmcgui.ListItem(label=params.get('title') or 'VOD',path=stream['url'])
    item.setContentLookup(False)
    if stream.get('mime'):item.setMimeType(stream['mime'])
    for k,v in (stream.get('props') or {}).items():item.setProperty(k,str(v))
    item.setArt({'poster':params.get('poster') or ''});tag=item.getVideoInfoTag()
    tag.setTitle(params.get('title') or 'VOD');tag.setMediaType('episode' if params.get('episode') else 'movie')
    if str(params.get('season') or '').isdigit():tag.setSeason(int(params['season']))
    if str(params.get('episode') or '').isdigit():tag.setEpisode(int(params['episode']))
    ids={k:params[v] for k,v in [('tmdb','tmdb_id'),('imdb','imdb_id')] if params.get(v)}
    if ids:tag.setUniqueIDs(ids)
    home=xbmcgui.Window(10000)
    for key in ('dexhub.trailer.active','dexhub.trailer.url','dhs.trailer.visible','dhs.immersive'):home.clearProperty(key)
    from .. import refresh_guard
    refresh_guard.restore()
    from .player_handoff import play_video
    play_video(stream['url'], item)


def _clock(seconds):
    seconds = int(seconds or 0)
    hours, rest = divmod(seconds, 3600)
    return ('%d:%02d:%02d' % (hours, rest // 60, rest % 60)) if hours else ('%d:%02d' % (rest // 60, rest % 60))


def _resume_point(params):
    """v5.10.239: where to start: Continue Watching resumes (resume=1);
    a title stopped half way asks Resume from or From the start. None when
    the choice was cancelled."""
    try:
        from .. import iptv_progress as IP
        _canonical, video = IP.ids_of(params)
        row = IP.find(video)
    except Exception:
        row = None
    if not row:
        return 0.0
    position = float(row.get('position') or 0)
    if position < 30 or float(row.get('percent') or 0) >= IP.FINISHED:
        return 0.0
    if str(params.get('resume') or '') == '1':
        return position
    import xbmcgui
    from ..i18n import tr
    pick = xbmcgui.Dialog().contextmenu([tr('استكمال من %s') % _clock(position), tr('من البداية')])
    if pick < 0:
        return None
    return position if pick == 0 else 0.0


def _arm_session(params, stream_url, resume):
    """v5.10.239: Dex Hub's player companion follows this play as its own:
    where it stops is saved (iptv_progress) and it resumes there. Nothing of
    it goes to the account syncs (its ids are the subscription's)."""
    try:
        import uuid
        from .. import session_store, iptv_progress as IP
        canonical, video = IP.ids_of(params)
        episode = params.get('vod_type') == 'series'
        title = _name(params.get('title')) or 'VOD'
        ctx = {
            'playback_uid': uuid.uuid4().hex, 'stream_url': stream_url, 'stream_key': video,
            'media_type': 'series' if episode else 'movie', 'canonical_id': canonical, 'video_id': video,
            'title': title, 'show_title': _name(params.get('show_title')) if episode else '',
            'provider_name': 'IPTV', 'poster': params.get('poster') or '',
            'background': params.get('fanart') or '',
            'season': params.get('season') or '', 'episode': params.get('episode') or '',
            'tmdb_id': params.get('tmdb_id') or '', 'imdb_id': params.get('imdb_id') or '',
            'show_tmdb_id': params.get('show_tmdb_id') or '',
            'show_imdb_id': params.get('show_imdb_id') or '',
            'resume_seconds': float(resume or 0.0), 'iptv': {
                'live_src': params.get('live_src') or '', 'vod_type': params.get('vod_type') or 'movie',
                'id': params.get('id') or '', 'ext': params.get('ext') or '',
                'series': params.get('series') or '', 'title': title,
                'show_title': params.get('show_title') or '', 'poster': params.get('poster') or '',
                'fanart': params.get('fanart') or ''},
        }
        session_store.save_session(ctx)
    except Exception:
        pass
