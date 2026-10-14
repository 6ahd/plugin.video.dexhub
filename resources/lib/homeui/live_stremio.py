# -*- coding: utf-8 -*-
"""TV channels from Stremio add-ons, and the DexWorld subscription (v5.10.104).

Any add-on installed in Dex Hub whose manifest lists catalogs of TV channels
(type tv, channel, channels, live or iptv) becomes a tab of the Live TV page.
Each such catalog is a row, and so is each of its genres (the playlist's
groups, for an IPTV add-on), the genres first and the catalog as a whole
last, like "All channels" on the PVR tab. A channel's stream is asked for
when the cursor rests on it (/stream/<type>/<id>.json).

DexWorld (the user's subscription): its IPTV add-on (manifest id
org.dexworld.v50.alpha, {base}/{key}/manifest.json) is such an add-on. When
it is not installed as a source but its key is saved in Dex Hub's settings
(dexworld_api_key, the same key Dex Hub's DexWorld link uses), the page
reads it directly as the "dw" source without adding anything to the Home.
Its channels also get the programme on now and next from DexWorld's guide
(POST {base}/api/live-browser/epg-bulk with the key and up to 24 stream ids,
times in milliseconds) and the day's programmes on demand
(GET {base}/api/live-browser/epg/<id>?key=...).

The key is part of every DexWorld address, so none is logged.
"""
import os
import json
import hashlib
import threading
import time
from urllib.parse import quote, urlsplit

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from . import live
from . import live_sources as LS
from . import stremio_catalogs as SC

DEXWORLD_IDS = ('org.dexworld.v50.alpha',)
DEXWORLD_HOSTS = ('dexworld.cc', 'www.dexworld.cc')
DEXWORLD_ADDON = 'plugin.video.dexworld'
EPG_CHUNK = 24
EPG_TTL = 600
STREAM_TTL = 60

_epg = {}               # (base, sid) -> (time, {'now', 'next'})
_epg_lock = threading.Lock()
_streams = {}           # tile path -> (time, stream)


def _client():
    from ..dexhub import client
    return client


def _store():
    from ..dexhub import store
    return store


def _setting(name):
    try:
        return (xbmcaddon.Addon('plugin.video.dexhub').getSetting(name) or '').strip()
    except Exception:
        return ''


# --------------------------------------------------------------- DexWorld
def saved_key():
    return _setting('dexworld_api_key')


# v5.10.110: "unlink from Live TV" leaves the key where Dex Hub keeps it (the
# DexWorld link of the settings and the subtitle feedback use it too) and
# leaves this marker in the Home's profile; linking again removes it
_OFF = 'live_dexworld_off'
_off_path_memo = []


def _off_path():
    if not _off_path_memo:
        try:
            profile = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile')
            _off_path_memo.append(os.path.join(xbmcvfs.translatePath(profile), 'homeui', _OFF))
        except Exception:
            return ''
    return _off_path_memo[0]


def live_off():
    """Was the DexWorld subscription unlinked from Live TV?"""
    path = _off_path()
    return bool(path) and os.path.exists(path)


def _set_off(off):
    path = _off_path()
    if not path:
        return False
    try:
        if off:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as handle:
                handle.write('1')
        elif os.path.exists(path):
            os.remove(path)
        return True
    except Exception:
        return False


_ADDON_KEY = {'t': 0.0, 'v': ''}


def live_key():
    """The DexWorld key the Live TV page uses: the saved one, unless it was
    unlinked from Live TV.

    v5.10.185: with no key saved in Dex Hub, the key of the Dex IPTV add-on
    (plugin.video.dexworld, the same subscription) is used: a subscriber who
    entered it there saw no DexWorld in Dex Hub's IPTV page at all."""
    if live_off():
        return ''
    key = saved_key()
    if key:
        return key
    now = time.time()
    if now - _ADDON_KEY['t'] > 30.0:
        _ADDON_KEY['t'], _ADDON_KEY['v'] = now, addon_key()
    return _ADDON_KEY['v']


def base_url():
    return (_setting('dexworld_base_url') or 'https://dexworld.cc').rstrip('/')


def addon_key():
    """The key saved in the Dex IPTV add-on (plugin.video.dexworld), when it is installed."""
    try:
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % DEXWORLD_ADDON):
            return ''
        return (xbmcaddon.Addon(DEXWORLD_ADDON).getSetting('api_key') or '').strip()
    except Exception:
        return ''


def addon_icon():
    try:
        if xbmc.getCondVisibility('System.HasAddon(%s)' % DEXWORLD_ADDON):
            path = xbmcvfs.translatePath('special://home/addons/%s/icon.png' % DEXWORLD_ADDON)
            if xbmcvfs.exists(path):
                return path
    except Exception:
        pass
    return ''


def dexworld_parts(provider):
    """(base, key) when ``provider`` is the DexWorld IPTV add-on, else None."""
    provider = provider or {}
    manifest = provider.get('manifest') or {}
    url = str(provider.get('manifest_url') or provider.get('base_url') or '')
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    mid = str(manifest.get('id') or '')
    if (mid not in DEXWORLD_IDS and not mid.startswith(('org.dexworld.v50.user.', 'org.dexworld.v50.source.'))
            and host not in DEXWORLD_HOSTS):
        return None
    segments = [s for s in parts.path.split('/') if s]
    if not segments or segments[0] in ('subtitles', 'manifest.json'):
        return None
    return '%s://%s' % (parts.scheme or 'https', parts.netloc), segments[0]


# a manifest that could not be read is not asked for again for two minutes:
# the Live page builds its tabs often, and each attempt waited its timeout
_MANIFEST_FAILED = {}
_MANIFEST_RETRY = 120.0
_MANIFEST_REFRESH = set()


_MANIFEST_ERROR = {}


def _last_good(url):
    """The manifest DexWorld gave last time (any age), or None."""
    try:
        stale, _age = _client()._read_stale_cache(url, 400 * 86400)
        return stale if isinstance(stale, dict) and stale.get('catalogs') else None
    except Exception:
        return None


def virtual_provider(key, base):
    """The DexWorld IPTV add-on read with the saved key, not installed as a source.

    v5.10.185: DexWorld builds a subscriber's manifest from his IPTV accounts
    and can take longer than the 12 s it had (the page then said it could not
    connect): 25 s and one more try, and when it still does not answer, the
    manifest it gave last time is used (the channels play from the same
    addresses). The reason of a failure is kept for the page."""
    url = '%s/%s/manifest.json' % (base, key)
    force = url in _MANIFEST_REFRESH
    _MANIFEST_REFRESH.discard(url)
    failed_at = None if force else _MANIFEST_FAILED.get(url)
    if failed_at and time.time() - failed_at < _MANIFEST_RETRY:
        manifest = _last_good(url) or {}
    else:
        started = time.time()
        try:
            manifest = _client().get_json(url, ttl_seconds=0 if force else 6 * 3600, timeout_override=25,
                                          retry=True) or {}
            _MANIFEST_FAILED.pop(url, None)
            _MANIFEST_ERROR.pop(url, None)
            if isinstance(manifest, dict) and (time.time() - started) > 0.05:
                LS.log('DexWorld manifest read in %.0f ms: %d catalogs, %d of TV channels' % (
                    (time.time() - started) * 1000, len(manifest.get('catalogs') or []),
                    len(live_catalogs({'manifest': manifest}))))
        except Exception as exc:
            _MANIFEST_FAILED[url] = time.time()
            _MANIFEST_ERROR[url] = LS.safe(exc)
            LS.log('DexWorld manifest not read after %.0f ms: %s' % ((time.time() - started) * 1000,
                                                                   LS.failure(exc)), xbmc.LOGWARNING)
            manifest = _last_good(url) or {}
            if manifest:
                LS.log('DexWorld: the manifest read last time is used')
    if not isinstance(manifest, dict):
        manifest = {}
    return {'id': 'dexworld', 'name': 'DexWorld IPTV', 'manifest_url': url,
            'base_url': url.rsplit('/manifest.json', 1)[0], 'manifest': manifest,
            '_virtual': True, '_error': _MANIFEST_ERROR.get(url, '')}


def check_key(key, base=None):
    """Does DexWorld answer for ``key`` with TV channels? (the setup's check)"""
    base = base or base_url()
    url = '%s/%s/manifest.json' % (base, key)
    try:
        manifest = _client().get_json(url, ttl_seconds=0, timeout_override=15) or {}
    except Exception as exc:
        raise LS.SourceError(LS.safe(exc)) from None
    if not isinstance(manifest, dict) or not any(
            SC.browsable(c) for c in manifest.get('catalogs') or [] if isinstance(c, dict)):
        raise LS.SourceError('لا توجد كتالوجات في هذا الاشتراك')
    return manifest


def installed_dexworld():
    """The installed DexWorld IPTV add-on (a Dex Hub source), or None."""
    try:
        for provider in _store().list_providers() or []:
            if provider.get('enabled') is not False and dexworld_parts(provider):
                return provider
    except Exception:
        pass
    return None


# -------------------------------------------------------------- catalogs
def live_catalogs(provider):
    out = []
    for cat in ((provider or {}).get('manifest') or {}).get('catalogs') or []:
        if not isinstance(cat, dict) or not cat.get('id'):
            continue
        if not SC.browsable(cat) or not SC.is_live(cat):
            continue
        out.append(cat)
    return out


def genre_options(cat):
    """(genres, genre required) of a catalog."""
    options, required = [], False
    extra = SC.extras(cat).get('genre') or {}
    options = [str(o) for o in extra.get('options') or [] if str(o or '').strip()]
    required = bool(extra.get('isRequired'))
    if not options and isinstance(cat.get('genres'), list):
        options = [str(o) for o in cat['genres'] if str(o or '').strip()]
    return options, required


def provider_for(src):
    if src == 'dw':
        key = live_key()
        return virtual_provider(key, base_url()) if key else None
    if str(src).startswith('st:'):
        try:
            return _store().get_provider(src[3:])
        except Exception:
            return None
    return None


def _dexworld_of(src):
    if src == 'dw':
        key = live_key()
        return (base_url(), key) if key else None
    return dexworld_parts(provider_for(src))


def sources(app):
    out, keys = [], set()
    for provider in _store().list_providers() or []:
        if not isinstance(provider, dict) or provider.get('enabled') is False:
            continue
        refresh_key = 'st:%s' % provider.get('id')
        if refresh_key in _MANIFEST_REFRESH:
            _MANIFEST_REFRESH.discard(refresh_key)
            try:
                manifest = _client().get_json(provider['manifest_url'], ttl_seconds=0,
                                              timeout_override=12, retry=False)
                if isinstance(manifest, dict) and isinstance(manifest.get('catalogs'), list):
                    provider = _store().refresh_provider_manifest(provider['id'], manifest) or provider
            except Exception as exc:
                LS.log('IPTV manifest refresh failed: %s' % LS.failure(exc), xbmc.LOGWARNING)
        if not live_catalogs(provider) and not dexworld_parts(provider):
            continue
        dw = dexworld_parts(provider)
        if dw:
            # A standalone /source/<id> install does not cover the root subscription.
            if '/source/' not in urlsplit(str(provider.get('manifest_url') or provider.get('base_url') or '')).path:
                keys.add((dw[0].lower(), dw[1]))
        out.append(_source(app, provider, 'st:%s' % provider.get('id'), dw))
    key = live_key()
    if key and (base_url().lower(), key) not in keys:
        provider = virtual_provider(key, base_url())
        out.append(_source(app, provider, 'dw', (base_url(), key)))
    return out


def _source(app, provider, key, dw):
    manifest = provider.get('manifest') or {}
    icon = str(manifest.get('logo') or '')
    if dw:
        icon = addon_icon() or icon or app.media_path('tab_dexworld.png')
    label = live.clean(provider.get('name') or manifest.get('name')) or 'Stremio'
    if dw and label.lower() in ('dexworld iptv', 'dexworld', 'dex iptv'):
        label = 'DexWorld'
    return {'key': key, 'kind': 'stremio', 'label': label,
            'hint': app.tr('اشتراكك') if dw else 'Stremio',
            'icon': icon or app.media_path('tab_stremio.png'),
            'card': 'live_dexworld.jpg' if dw else 'live_stremio.jpg',
            'dexworld': bool(dw), 'virtual': key == 'dw',
            'description': live.clean(manifest.get('description'))}


def rows(source, app, changed=None):
    provider = provider_for(source['key'])
    if not provider:
        return []
    if provider.get('_virtual') and not provider.get('manifest'):
        reason = provider.get('_error') or ''
        raise LS.SourceError(app.tr('تعذر الاتصال باشتراك DexWorld') + ('\n' + app.tr(reason) if reason else ''))
    card = app.media_path('channel_card.jpg')
    cats = live_catalogs(provider)
    if not cats and source.get('dexworld'):
        # v5.10.185: a subscription whose manifest has no TV channels (a Light
        # configuration: films and series only) says so
        raise LS.SourceError(app.tr('اشتراك DexWorld هذا ما فيه قنوات مباشرة (أفلام ومسلسلات فقط)'))
    multi = len(cats) > 1
    out = []
    for cat in cats:
        ctype = str(cat.get('type')).strip().lower()
        cid = str(cat.get('id'))
        cname = live.clean(cat.get('name')) or app.tr('قنوات')
        options, required = genre_options(cat)
        for genre in options:
            out.append(_row(source, app, ctype, cid, genre, live.clean(genre) or genre,
                            cname if multi else '', card))
        if not required and not (source.get('dexworld') and options):
            if options:
                title = '%s  •  %s' % (cname, app.tr('كل القنوات')) if multi else app.tr('كل القنوات')
            else:
                title = cname
            out.append(_row(source, app, ctype, cid, '', title, '', card))
    return out


def _row(source, app, ctype, cid, genre, title, subtitle, card):
    params = {'live_src': source['key'], 'type': ctype, 'catalog': cid, 'genre': genre,
              'skip': 0, 'label': title}

    def load(patience=0.0):
        return catalog_tiles(params, card, timeout=20 if patience else 12)
    return {'key': 'live:%s:%s:%s:%s' % (source['key'], ctype, cid, genre), 'title': title,
            'subtitle': subtitle, 'params': params, 'loader': load, 'epg': source.get('dexworld')}


# v5.10.106: an IPTV add-on often answers one request with a whole playlist
# group (DexWorld: every channel of a genre, thousands for "All channels").
# The answer is kept here for a few minutes and only the channels on screen
# become tiles: a row shows ROW_LIMIT, each page of its grid PAGE_SIZE more,
# taken from this copy without asking the add-on again.
PAGE_TTL = 300
_pages = {}             # (src, type, catalog, genre, skip) -> (time, [ids], [metas], raw count)
_pages_lock = threading.Lock()


def _add_on_page(provider, params, skip, timeout):
    """(ids, metas, raw count) of one answer of the add-on, an id once, from
    the copy above when fresh. The raw count (duplicates included) is how far
    the add-on's own paging moved."""
    catalog = SC.definition(provider, params)
    if SC.dexworld_sortable(provider, catalog):
        catalog = dict(catalog, extraSupported=list(catalog.get('extraSupported') or []) + ['sort'])
    extra = SC.request_extras(catalog, params)
    key = _page_key(provider, params, extra, skip)
    with _pages_lock:
        hit = _pages.get(key)
    fresh = bool(params.get('_refresh')) or bool(getattr(getattr(_client(), '_FRESH', None), 'on', False))
    if hit and not fresh and time.time() - hit[0] < PAGE_TTL:
        return hit[1], hit[2], hit[3]
    if skip and SC.supports(catalog, 'skip'):
        extra['skip'] = skip
    try:
        kwargs = {'timeout_override': timeout, 'retry': False}
        if params.get('_refresh'):
            kwargs['force_refresh'] = True
        data = _client().fetch_catalog(provider, params['type'], params['catalog'], extra=extra,
                                       **kwargs) or {}
    except Exception as exc:
        raise LS.SourceError(LS.safe(exc)) from None
    failure = data.get('dexworldError')
    if isinstance(failure, dict) and failure.get('code') == 'IPTV_UPSTREAM':
        status = str(failure.get('status') or '')
        source = str(failure.get('source') or '')
        raise LS.SourceError('تعذر تحميل القسم من مزود IPTV%s%s؛ حدّث المصدر أو تحقق من الاشتراك' %
                             (' (' + source + ')' if source else '', ' — HTTP ' + status if status else ''))
    batch = data.get('metas') or []
    batch = batch if isinstance(batch, list) else []
    raw_count = len(batch)
    raw = [m for m in batch if isinstance(m, dict) and m.get('id')]
    ids, metas, seen = [], [], set()
    for meta in raw:
        mid = str(meta.get('id'))
        if mid in seen:
            continue
        seen.add(mid)
        ids.append(mid)
        metas.append(meta)
    with _pages_lock:
        if len(_pages) > 60:
            now = time.time()
            for old in [k for k, v in _pages.items() if now - v[0] >= PAGE_TTL] or list(_pages)[:30]:
                _pages.pop(old, None)
        _pages[key] = (time.time(), ids, metas, raw_count)
    return ids, metas, raw_count


def _page_key(provider, params, extra, skip):
    identity = hashlib.sha256(str(provider.get('manifest_url') or provider.get('base_url') or '')
                              .encode('utf-8')).hexdigest()[:20]
    return (str(params.get('live_src')), identity, str(params.get('type')), str(params.get('catalog')),
            json.dumps(extra, sort_keys=True, ensure_ascii=False), int(skip))


def refresh(src):
    """Invalidate only this subscription; refresh its manifest on the worker."""
    with _pages_lock:
        for key in list(_pages):
            if key[0] == str(src):
                _pages.pop(key, None)
    if src == 'dw' and live_key():
        _MANIFEST_REFRESH.add('%s/%s/manifest.json' % (base_url(), live_key()))
    elif str(src).startswith('st:'):
        _MANIFEST_REFRESH.add(str(src))


def catalog_tiles(params, card, timeout=12, count=None):
    """Tiles of the channels from ``offset`` on in the add-on's answer for ``skip``.

    ``more`` continues inside the same answer while it has channels left, then
    asks the add-on for its next page (skip), unless the answer was short or
    the add-on ignored skip and sent the channels already shown.
    """
    src = params.get('live_src')
    provider = provider_for(src)
    if not provider:
        raise LS.SourceError('المصدر غير موجود')
    count = max(1, int(count or LS.ROW_LIMIT))
    skip = max(0, int(params.get('skip') or 0))
    offset = max(0, int(params.get('offset') or 0))
    ids, metas, raw = _add_on_page(provider, params, skip, timeout)
    if skip and ids and offset == 0:
        # an add-on that ignores skip answers with a page already shown: the
        # one before (whose first id came along as ``after``) or the first
        first_ids, _, _ = _add_on_page(provider, params, 0, timeout)
        if ids[0] == str(params.get('after') or '') or (first_ids and set(ids) <= set(first_ids)):
            return [], None
    dw = bool(_dexworld_of(src))
    group = params.get('label') or params.get('genre') or ''
    guide_sources = _guide_sources(provider) if dw else set()
    tiles = [meta_tile(src, meta, card, group, dw, guide_sources)
             for meta in metas[offset:offset + count]]
    more = None
    if offset + count < len(metas):
        more = dict(params, skip=skip, offset=offset + count)
    elif raw and SC.supports(SC.definition(provider, params), 'skip'):
        more = dict(params, skip=skip + raw, offset=0, after=ids[0] if ids else '')
    return tiles, more


def _guide_sources(provider):
    """Scoped guide IDs are safe only for a single-source main installation.

    The uploaded legacy guide endpoint cannot select an independent source.
    """
    if '/source/' in str(provider.get('manifest_url') or ''):
        return set()
    scopes = {str(cat.get('id') or '').split('~', 1)[1]
              for cat in (provider.get('manifest') or {}).get('catalogs') or []
              if isinstance(cat, dict) and '~' in str(cat.get('id') or '')}
    return scopes if len(scopes) == 1 and 'all' not in scopes else set()


def dexworld_sid(item_id, guide_sources=None):
    """DexWorld's stream id of a channel: dex_channel_<id>_<ext> (a '~source' tag aside)."""
    # This legacy guide API always uses the account's first Xtream source,
    # and cannot take a source selector. Don't show another source's guide.
    base, _, scope = str(item_id or '').partition('~')
    if scope and scope not in (guide_sources or set()):
        return ''
    parts = base.split('_')
    if base.startswith('dex_') and len(parts) >= 4 and parts[1] in ('channel', 'tv', 'live'):
        return parts[-2] if parts[-2].isdigit() else ''
    return ''


def meta_tile(src, meta, card, group='', dexworld=False, guide_sources=None):
    mid = str(meta.get('id'))
    mtype = str(meta.get('type') or 'tv').strip().lower()
    logo = meta.get('logo') or meta.get('poster') or meta.get('thumbnail') or ''
    genres = [live.clean(g) for g in (meta.get('genres') or []) if live.clean(g)]
    epg = {}
    if dexworld:
        sid = dexworld_sid(mid, guide_sources)
        if sid:
            epg = {'dw': sid}
    return LS.channel_tile(src, '%s/%s' % (mtype, mid), meta.get('name') or mid, card, logo=logo,
                           group=group, stream={'type': mtype, 'id': mid},
                           desc=meta.get('description') or '', fanart=meta.get('background') or '',
                           epg=epg, genre=' / '.join(genres[:2]))


def page_tiles(params, app):
    return catalog_tiles(params, app.media_path('channel_card.jpg'), timeout=15, count=LS.PAGE_SIZE)


# ---------------------------------------------------------------- streams
def _kind(stream):
    url = str(stream.get('url') or '').split('|', 1)[0].lower()
    blob = ('%s %s' % (stream.get('name') or '', stream.get('title') or '')).lower()
    if '.m3u8' in url or 'hls' in blob:
        return 'hls'
    if '.ts' in url or '/live/' in url:
        return 'ts'
    return 'other'


def pick(streams):
    """The stream to play: TS first (Kodi plays IPTV's TS more reliably than its HLS)."""
    usable = [s for s in streams or [] if isinstance(s, dict)
              and str(s.get('url') or '').startswith(('http://', 'https://'))]
    if not usable:
        return None
    for stream in usable:
        if _kind(stream) == 'ts':
            return stream
    return usable[0]


def resolve(tile, app):
    path = tile.get('path') or ''
    hit = _streams.get(path)
    if hit and time.time() - hit[0] < STREAM_TTL:
        return hit[1]
    provider = provider_for(tile.get('src'))
    if not provider:
        return None
    provider = dict(provider, _force_stream_refresh=True)    # a live address may expire
    info = tile.get('stream') or {}
    ctype, cid = info.get('type') or 'tv', info.get('id') or ''
    try:
        data = _client().fetch_streams(provider, ctype, cid, timeout_override=12) or {}
        chosen = pick(data.get('streams'))
        if chosen is None and ctype == 'channel':
            # a Stremio "channel" may hold videos: the first one is its live stream
            meta = (_client().fetch_meta(provider, ctype, cid, timeout_override=10) or {}).get('meta') or {}
            videos = [v for v in meta.get('videos') or [] if isinstance(v, dict) and v.get('id')]
            if videos:
                data = _client().fetch_streams(provider, ctype, str(videos[0]['id']),
                                               timeout_override=12) or {}
                chosen = pick(data.get('streams'))
    except Exception as exc:
        LS.log('channel stream not found: %s' % LS.failure(exc), xbmc.LOGWARNING)
        return None
    if chosen is None:
        return None
    hints = chosen.get('behaviorHints') or {}
    headers = ((hints.get('proxyHeaders') or {}).get('request') or {}) if isinstance(hints, dict) else {}
    stream = LS.stream_for(chosen['url'], headers if isinstance(headers, dict) else {},
                           key=path, logo=tile.get('logo') or '')
    if stream:
        if len(_streams) > 300:
            _streams.clear()
        _streams[path] = (time.time(), stream)
    return stream


# ------------------------------------------------------------------ guide
def _programme(item):
    item = item or {}
    try:
        start = int(float(item.get('start') or 0)) // 1000
        end = int(float(item.get('stop') or item.get('end') or 0)) // 1000
    except Exception:
        start = end = 0
    return LS.programme(item.get('title'), start, end,
                        plot=item.get('desc') or item.get('description') or '')


def _headers():
    try:
        version = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('version')
    except Exception:
        version = ''
    return {'X-DexWorld-Client': 'kodi', 'User-Agent': 'DexHub/%s (Kodi)' % (version or '5')}


def fill_epg(tiles, app):
    """DexWorld's now and next for channels that have none yet."""
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    changed = []
    by_src = {}
    for tile in tiles:
        sid = (tile.get('epg') or {}).get('dw')
        if sid:
            by_src.setdefault(tile.get('src'), []).append((sid, tile))
    now = time.time()
    for src, pairs in by_src.items():
        parts = _dexworld_of(src)
        if not parts:
            continue
        base, key = parts
        missing = []
        with _epg_lock:
            for sid, _tile in pairs:
                hit = _epg.get((base, key, sid))
                if not hit or now - hit[0] > EPG_TTL:
                    missing.append(sid)
        missing = list(dict.fromkeys(missing))
        failed = set()
        for start in range(0, len(missing), EPG_CHUNK):
            chunk = missing[start:start + EPG_CHUNK]
            try:
                reply = LS.http_json(base + '/api/live-browser/epg-bulk', headers=_headers(),
                                     timeout=8, method='POST', payload={'key': key, 'ids': chunk}) or {}
            except Exception as exc:
                LS.log('DexWorld guide not read: %s' % LS.failure(exc), xbmc.LOGWARNING)
                failed.update(missing[start:])
                break
            found = reply.get('map') if isinstance(reply, dict) else None
            found = found if isinstance(found, dict) else {}
            with _epg_lock:
                for sid in chunk:
                    _epg[(base, key, sid)] = (now, found.get(str(sid)) or {})
        for sid, tile in pairs:
            if sid in failed:
                continue        # asked again when the cursor reaches it
            tile['_epg_done'] = True
            with _epg_lock:
                data = (_epg.get((base, key, sid)) or (0, {}))[1] or {}
            cur, nxt = _programme(data.get('now')), _programme(data.get('next'))
            if (cur or nxt) and (cur != tile.get('now') or nxt != tile.get('next')):
                tile['now'], tile['next'] = cur, nxt
                live.update_times(tile)
                changed.append(tile)
    if len(_epg) > 5000:
        with _epg_lock:
            _epg.clear()
    return changed


def guide(tile, app):
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    sid = (tile.get('epg') or {}).get('dw')
    parts = _dexworld_of(tile.get('src'))
    if not sid or not parts:
        return []
    base, key = parts
    url = '%s/api/live-browser/epg/%s?key=%s' % (base, quote(str(sid)), quote(key))
    reply = LS.http_json(url, headers=_headers(), timeout=10) or {}
    schedule = reply.get('schedule') if isinstance(reply, dict) else None
    now = time.time()
    out = []
    for item in schedule or []:
        programme = _programme(item)
        if programme and programme.get('end', 0) > now:
            out.append(programme)
    out.sort(key=lambda p: p.get('start') or 0)
    return out[:60]


# ------------------------------------------------------------------ setup
def _busy(on):
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)' if on else 'Dialog.Close(busydialognocancel)')


def link(app):
    """Link the DexWorld subscription to Live TV; the key of its tab ('' when not linked).

    The key goes where Dex Hub keeps it (the dexworld_api_key setting, as the
    DexWorld link of the settings does). Nothing is added to the Home: the
    Live TV page reads the subscription's channels directly.
    """
    tr, dialog = app.tr, xbmcgui.Dialog()
    installed = installed_dexworld()
    if installed:
        return 'st:%s' % installed.get('id')
    if saved_key():
        # v5.10.110: linked again after "unlink from Live TV": the saved key's
        # channels show again
        if not _set_off(False):
            return ''
        return 'dw'
    key = ''
    found = addon_key()
    if found and dialog.yesno('DexWorld', tr('مفتاح اشتراكك محفوظ في إضافة Dex IPTV. تستخدمه هنا؟')):
        key = found
    if not key:
        key = (dialog.input(tr('مفتاح اشتراك DexWorld (API Key)'),
                            option=xbmcgui.ALPHANUM_HIDE_INPUT) or '').strip()
    if not key:
        return ''
    _busy(True)
    try:
        check_key(key)
    except Exception as exc:
        _busy(False)
        dialog.ok('DexWorld', '%s\n%s' % (tr('تعذر ربط الاشتراك'), tr(LS.safe(exc))))
        return ''
    _busy(False)
    try:
        xbmcaddon.Addon('plugin.video.dexhub').setSetting('dexworld_api_key', key)
    except Exception:
        return ''
    _set_off(False)
    LS.log('DexWorld subscription linked to Live TV')
    dialog.notification('Dex Hub', tr('رُبط اشتراك DexWorld'), xbmcgui.NOTIFICATION_INFO, 2500, sound=False)
    return 'dw'


def unlink():
    """Hide the DexWorld subscription from Live TV; True when done (v5.10.110).

    The key itself stays saved: clearing dexworld_api_key also unlinked the
    DexWorld link of the settings and the subtitle feedback.
    """
    _streams.clear()
    return _set_off(True)


def add_addon(app):
    """Add a Stremio add-on by its manifest address; its tab key when it has TV channels."""
    tr, dialog = app.tr, xbmcgui.Dialog()
    from .. import kb_private
    url = (kb_private.dialog_input(tr('رابط manifest.json لإضافة Stremio فيها قنوات')) or '').strip()
    if not url:
        return ''
    if url.lower().startswith('stremio://'):
        url = 'https://' + url[len('stremio://'):]
    if not url.lower().startswith(('http://', 'https://')):
        dialog.ok('Dex Hub', tr('اكتب رابطاً كاملاً يبدأ بـ http:// أو https://'))
        return ''
    if '/manifest.json' not in url:
        url = url.split('?', 1)[0].rstrip('/') + '/manifest.json'
    _busy(True)
    try:
        from ..dexhub.api import add_provider_from_url
        provider = add_provider_from_url(url)
    except Exception as exc:
        _busy(False)
        dialog.ok('Dex Hub', '%s\n%s' % (tr('تعذر إضافة الإضافة'), tr(LS.safe(exc))))
        return ''
    _busy(False)
    if not live_catalogs(provider) and not (dexworld_parts(provider) and any(
            SC.browsable(c) for c in (provider.get('manifest') or {}).get('catalogs') or []
            if isinstance(c, dict))):
        dialog.ok('Dex Hub', tr('أُضيفت الإضافة، لكن ما فيها كتالوجات قنوات. تظهر أعمالها في الصفحة الرئيسية.'))
        return ''
    LS.log('Stremio add-on with TV channels added')
    return 'st:%s' % provider.get('id')
