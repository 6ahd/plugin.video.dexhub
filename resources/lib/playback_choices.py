"""A single playback policy and reuse of the exact last successful stream.

Local cache reads only before handoff. A stale link is resolved again from
its own provider; changing providers remains an explicit picker choice.
"""
import hashlib
import re
import time
from urllib.parse import parse_qs, urlsplit

MODES = ('choose', 'best', 'same', 'tmdbh')
_MAX_LINK_AGE = 1800


def mode(addon):
    routing = (addon.getSetting('source_resolution_mode') or '').casefold()
    if 'tmdb' in routing or 'helper' in routing:
        return 'tmdbh'
    if addon.getSetting('quick_same_source_play') == 'true' or 'same' in routing or 'نفس' in routing:
        return 'same'
    playback = (addon.getSetting('playback_open_mode') or '').casefold()
    return 'best' if playback in ('1', 'auto', 'autoplay') or 'best' in playback or 'أفضل' in playback else 'choose'


def set_mode(addon, value):
    if value not in MODES:
        return False
    addon.setSetting('source_resolution_mode', {'same':'Same source', 'tmdbh':'TMDb Helper'}.get(value, 'DexHub picker'))
    addon.setSetting('playback_open_mode', 'Best quality automatically' if value == 'best' else 'Choose from sources')
    addon.setSetting('quick_same_source_play', 'false')
    return True


def edit_mode(addon, txt):
    import xbmcgui
    labels = [txt('اختيار المصدر من النتائج','Choose a source from results'),
              txt('أفضل نتيجة تلقائيًا','Best result automatically'),
              txt('مصدر العمل أو السيرفر نفسه','The item source or server'),
              txt('تشغيل عبر TMDb Helper','Play through TMDb Helper')]
    pick=xbmcgui.Dialog().select(txt('طريقة التشغيل','Playback method'),labels,preselect=MODES.index(mode(addon)))
    if pick<0:
        return True
    set_mode(addon,MODES[pick])
    from . import settings_cache, ui_preferences
    settings_cache.invalidate()
    ui_preferences.publish()
    return True
def _scope(media, canonical, video='', season='', episode=''):
    canonical = str(canonical or '').strip()
    if not canonical:
        return ''
    episodic = (season not in (None, '', '0', 0) and episode not in (None, '', '0', 0))
    if episodic:
        canonical = re.sub(r'^((?:tt\d+|tmdb:\d+)):\d+:\d+$', r'\1', canonical)
        try:
            identity = 'series|%s|%s|%s' % (canonical, int(season), int(episode))
        except (ValueError, TypeError):
            return ''
    elif str(media or '').lower() in ('series', 'episode', 'tv', 'show', 'anime'):
        if not video:
            return ''  # Never reuse an episode link for a show root.
        identity = 'series|%s|%s' % (canonical, video)
    else:
        identity = 'movie|%s' % canonical
    return 'last-played-' + hashlib.sha256(identity.encode()).hexdigest()


def saved(api, media, canonical, video='', season='', episode=''):
    key = _scope(media, canonical, video, season, episode)
    record = api.cache_store.get('last_played', key) if key else None
    return record if isinstance(record, dict) and isinstance(record.get('ctx'), dict) else {}


def save_started(ctx):
    """Called once by the service's existing real-media start probe."""
    from .settings_cache import cached_addon
    addon = cached_addon()
    if addon.getSetting('remember_last_source') != 'true' and addon.getSetting('continue_known_source_direct') == 'false':
        return
    if not ctx.get('stream_url') or not ctx.get('canonical_id'):
        return
    from . import cache_store
    clean = {k:v for k,v in ctx.items() if not k.startswith(('playback_', 'source_switch_', 'switch_subtitle_'))
             and k not in ('resume_seconds', 'resume_percent', 'selected_subtitle', 'source_picker_url',
                           'fallback_stream_keys', 'next_episode', 'selected_subtitle_index', 'subtitles')}
    record = {'at':time.time(), 'ctx':clean}
    ids = [ctx.get('canonical_id')]
    if ctx.get('imdb_id'):
        ids.append(ctx['imdb_id'])
    if ctx.get('tmdb_id'):
        ids.append('tmdb:%s' % ctx['tmdb_id'])
    for cid in dict.fromkeys(ids):
        key = _scope(ctx.get('media_type'), cid, ctx.get('video_id'), ctx.get('season'), ctx.get('episode'))
        if key:
            cache_store.update('last_played', key, record)


def _fresh(record, now):
    age = now - float(record.get('at') or 0)
    if age < 0 or age > _MAX_LINK_AGE:
        return False
    url = str(record['ctx'].get('stream_url') or '').split('|', 1)[0]
    if urlsplit(url).scheme not in ('http', 'https', 'plugin'):
        return False
    query = parse_qs(urlsplit(url).query)
    for name in ('expires', 'exp', 'expiry'):
        for value in query.get(name, []):
            try:
                expiry = float(value)
                if expiry > 100000000000:
                    expiry /= 1000
                if expiry > 1000000000 and expiry <= now + 30:
                    return False
            except (ValueError, TypeError):
                pass
    return True


def native_resume_seconds(value):
    try:
        return value if float(value or 0) > 1 else None
    except (ValueError, TypeError):
        return None


def try_saved(api, media, canonical, video='', season='', episode='',
              resume_seconds='0', resume_percent='', provider_id='', provider_name='', server_id=''):
    record = saved(api, media, canonical, video, season, episode)
    ctx = dict(record.get('ctx') or {})
    if not ctx:
        return False, None
    wanted = str(provider_id or '')
    sid = str(ctx.get('source_provider_id') or ctx.get('provider_id') or '')
    if sid in ('plex_native', 'emby_native', 'jellyfin_native', 'silo_native'):
        sid = '__%s__' % sid.split('_', 1)[0]
    if wanted and not wanted.startswith('virtual.') and wanted != sid:
        return False, None
    if provider_name and str(provider_name).strip().casefold() != str(ctx.get('provider_name') or '').strip().casefold():
        return False, None
    if server_id and str(server_id) != str(ctx.get('server_id') or ''):
        return False, None
    # Native sessions must obtain current authentication/session data directly
    # from the exact server object. This does not run a source search.
    backend = str(ctx.get('server_type') or '').lower()
    if backend in ('plex', 'emby', 'jellyfin', 'silo'):
        client = getattr(api, backend + '_client')
        try:
            server = next((s for s in client.servers() if str(s.get('id')) == str(ctx.get('server_id'))), None)
        except Exception:
            return False, None
        if server is None:
            return False, None
        iid = ctx.get('rating_key') if backend == 'plex' else ctx.get('item_id')
        if not iid:
            return False, None
        if backend == 'plex':
            return True, api.plex_play(server['id'], iid, force_native=True,
                preferred_version=ctx.get('part_key') or '', resume_seconds=native_resume_seconds(resume_seconds))
        if backend == 'silo':
            return True, api.silo_play(iid, server=server, force_native=True, resume_seconds=resume_seconds,
                                      preferred_version=ctx.get('media_source_id') or '')
        return True, api.emby_play(iid, server=server, backend=backend, force_native=True,
            preferred_version=ctx.get('media_source_id') or '', resume_seconds=native_resume_seconds(resume_seconds))
    if not _fresh(record, time.time()):
        return False, None
    provider = api.store.get_provider(sid) if sid else None
    if not provider or provider.get('enabled') in (False, 'false', 0):
        return False, None
    base = str(ctx.get('provider_base_url') or '')
    if base and base != str(provider.get('base_url') or ''):
        return False, None
    ctx['resume_seconds'], ctx['resume_percent'] = resume_seconds, resume_percent
    key = api.cache_store.put('stream', ctx, ttl_hours=24)
    return True, api._play_with_context(ctx, stream_key=key)


def saved_version(versions, preferred, fields):
    if not preferred:
        return {}
    return next((v for v in versions if any(str(v.get(k) or '')==str(preferred) for k in fields)), {})


def native_origin(api, source_provider_id, seed, resume_seconds='0'):
    backend = str(source_provider_id or '').strip('_').replace('_native','')
    sid, iid = (seed or {}).get('native_server_id'), (seed or {}).get('native_item_id')
    if backend not in ('plex','emby','jellyfin','silo') or not (sid and iid):
        return False, None
    client = getattr(api, backend+'_client')
    try:
        server=next((s for s in client.servers() if str(s.get('id'))==str(sid)),None)
    except Exception:
        return False,None
    if not server:
        return False,None
    if backend=='plex':
        return True,api.plex_play(sid,iid,force_native=True,resume_seconds=native_resume_seconds(resume_seconds))
    if backend=='silo':
        return True,api.silo_play(iid,server=server,force_native=True,resume_seconds=resume_seconds)
    return True,api.emby_play(iid,server=server,backend=backend,force_native=True,resume_seconds=native_resume_seconds(resume_seconds))


def route(api, params, episodic=False):
    """Apply Playback settings to catalog stream routes too."""
    media = params.get('media_type') or ('series' if episodic else 'movie')
    canonical = params.get('canonical_id', '')
    seed = api._ui_seed_from_route(**{k:params.get(k,'') for k in
                                    ('ui_poster', 'ui_fanart', 'ui_clearlogo', 'ui_seed_key')})
    common = dict(title=params.get('title',''), resume_seconds=params.get('resume_seconds','0'),
                  resume_percent=params.get('resume_percent',''),
                  preferred_provider_name=params.get('preferred_provider_name',''),
                  source_provider_id=params.get('source_provider_id',''),
                  source_mode=params.get('source_mode',''), ui_seed=seed)
    explicit = params.get('force_picker') == '1' or params.get('fresh_search') == '1'
    if not explicit and (mode(api.ADDON) != 'choose' or api.ADDON.getSetting('remember_last_source') == 'true'):
        return api._dispatch_dexhub_play(media_type=media, canonical_id=canonical,
            video_id=params.get('video_id',''), season=params.get('season',''), episode=params.get('episode',''),
            tmdb_id=params.get('tmdb_id',''), imdb_id=params.get('imdb_id',''), tvdb_id=params.get('tvdb_id',''), **common)
    common['seed_ids'] = api._seed_ids_from_values(tmdb_id=params.get('tmdb_id',''),
        imdb_id=params.get('imdb_id',''), tvdb_id=params.get('tvdb_id',''), canonical_id=canonical)
    if episodic:
        return api.episode_streams(canonical, params.get('video_id',''), params.get('season',1),
                                   params.get('episode',1), media_type=media, **common)
    return api.streams(media, canonical, **common)
