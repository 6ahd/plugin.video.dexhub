"""Jellyfin's public API adapter: no Emby-only exact-provider filter.

References reviewed: Jellyfin ItemsController/OpenAPI, JellyCon's image owners,
Jellio+'s internal provider-ID queries (which are not a public REST parameter).
No network is performed here except through the caller's bounded callbacks.
"""
import hashlib
import json
import re
import threading
import time
import unicodedata
from urllib.parse import quote, urlencode
from .identity.media import normalize_ids

_LOCK = threading.Lock()
_KNOWN = {}
_TTL = 300


def request_route(path, params=None):
    path = '/' + str(path).lstrip('/')
    params = dict(params or {})
    match = re.fullmatch(r'/Users/([^/]+)/(Items(?:/[^/]+)?|Views)', path)
    if match:
        params.setdefault('UserId', match.group(1))
        tail = match.group(2)
        path = '/UserViews' if tail == 'Views' else '/UserItems/Resume' if tail == 'Items/Resume' else '/' + tail
    if (path in ('/Items', '/UserItems/Resume', '/UserViews', '/Shows/NextUp')
            or re.fullmatch(r'/Items/[^/]+', path)
            or re.fullmatch(r'/Shows/[^/]+/(Episodes|Seasons)', path)):
        params.setdefault('EnableImages', 'true')
        params.setdefault('ImageTypeLimit', 1)
        fields = [f for f in str(params.get('Fields') or '').split(',') if f]
        if fields:
            for f in ('PrimaryImageAspectRatio', 'OriginalTitle'):
                if f not in fields: fields.append(f)
            params['Fields'] = ','.join(fields)
    return path, params


def artwork(item, server):
    tags = item.get('ImageTags') or {}
    tags = {str(k).lower(): v for k, v in tags.items()} if isinstance(tags, dict) else {}
    iid, series = str(item.get('Id') or ''), str(item.get('SeriesId') or '')
    kind = str(item.get('Type') or '').lower()
    def url(owner, image, tag='', width=600):
        if not owner:return ''
        query = {'maxWidth': width, 'quality': 90, 'api_key': server.get('token') or ''}
        if tag:query['tag'] = tag
        return '%s/Items/%s/Images/%s?%s' % (str(server.get('url') or '').rstrip('/'), quote(str(owner),safe=''), image, urlencode(query))
    # A missing ImageTags block in a thin DTO does not prove an absent image.
    primary_tag = tags.get('primary') or item.get('PrimaryImageTag') or ''
    thin = 'ImageTags' not in item or item.get('ImageTags') is None
    own = url(iid, 'Primary', primary_tag) if primary_tag or thin else ''
    parent = str(item.get('ParentPrimaryImageItemId') or '')
    inherited = url(parent, 'Primary', item.get('ParentPrimaryImageTag') or '') if parent else ''
    show = url(series, 'Primary', item.get('SeriesPrimaryImageTag') or '') if series else ''
    poster = (show or inherited or own) if kind == 'episode' else (own or inherited or show)
    # A movie/series may omit tags even though its direct image route works.
    if not poster and kind in ('movie', 'series'):poster = url(iid, 'Primary')
    backs = item.get('BackdropImageTags') or []
    inherited_backs = item.get('ParentBackdropImageTags') or []
    fanart = url(iid, 'Backdrop/0', backs[0], 1280) if backs else ''
    if not fanart and inherited_backs:fanart = url(item.get('ParentBackdropItemId') or series, 'Backdrop/0', inherited_backs[0], 1280)
    logo = url(iid,'Logo',tags['logo'],800) if tags.get('logo') else ''
    if not logo and item.get('ParentLogoImageTag'):logo = url(item.get('ParentLogoItemId') or series,'Logo',item['ParentLogoImageTag'],800)
    thumb = url(iid,'Thumb',tags['thumb'],800) if tags.get('thumb') else ''
    if not thumb and item.get('ParentThumbImageTag'):thumb = url(item.get('ParentThumbItemId') or series,'Thumb',item['ParentThumbImageTag'],800)
    return {k:v for k,v in {'poster':poster,'thumb':own or poster,'icon':poster,'fanart':fanart,
                            'landscape':thumb or fanart or own,'clearlogo':logo,
                            'tvshow.poster':show or inherited or poster,'tvshow.clearlogo':logo}.items() if v}


def _scope(server):
    token = hashlib.sha256(str(server.get('token') or '').encode()).hexdigest()[:12]
    return str(server.get('url') or '').rstrip('/'), str(server.get('user_id') or ''), token


def remember(server, row):
    ids = normalize_ids(row.get('ids'))
    if not row.get('rating_key') or not any(ids.values()):return
    saved = {k:row.get(k) for k in ('rating_key','media_type','title','raw_title','original_title','year')}
    saved['ids'] = ids
    with _LOCK:
        if len(_KNOWN) > 8:_KNOWN.clear()
        entries = _KNOWN.setdefault(_scope(server), {})
        if len(entries) >= 600:
            oldest = min(entries, key=lambda k:entries[k][0]);entries.pop(oldest,None)
        entries[str(row['rating_key'])] = (time.monotonic(), saved)


def _year(value):
    match = re.search(r'\b(18\d{2}|19\d{2}|20\d{2})\b',str(value or ''))
    return match.group(1) if match else ''


def _title(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c) and c != '\u0640')
    text = re.sub(r'\s*\((?:18|19|20)\d{2}\)\s*$', '', text)
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text).split())


def _selection(rows, ids, media_type, title='', year=''):
    wanted = normalize_ids(ids)
    want_type = 'show' if str(media_type).lower() in ('series','show','episode','tv') else 'movie'
    exact, named, seen = [], [], set()
    wanted_title = _title(title)
    wanted_year = _year(year) or (_year(title) if re.search(r'\((?:18|19|20)\d{2}\)\s*$', str(title or '')) else '')
    for row in rows:
        key = str(row.get('rating_key') or '')
        if not key or key in seen or row.get('media_type') != want_type:continue
        seen.add(key)
        actual = normalize_ids(row.get('ids'))
        shared = [k for k in wanted if wanted[k] and actual[k]]
        if any(wanted[k] != actual[k] for k in shared):continue
        if any(wanted[k] == actual[k] for k in shared):exact.append(row);continue
        if not wanted_title:continue
        names = [_title(row.get(k)) for k in ('title','raw_title','original_title')]
        if wanted_title not in names:continue
        if wanted_year and _year(row.get('year')) != wanted_year:continue
        named.append(row)
    if exact:return exact
    if not wanted_year and len({_year(r.get('year')) for r in named}) > 1:return []
    return named



def _bridge_key(server, provider, value):
    # Positive identity hints cross Kodi Python invokers. Never store tokens
    # or playback URLs in the shared window properties.
    identity = json.dumps([_scope(server), provider, value], ensure_ascii=True)
    return 'dexhub.jellyfin.match.' + hashlib.sha256(identity.encode()).hexdigest()


def _bridge_read(server, ids):
    try:
        import xbmcgui
        win = xbmcgui.Window(10000)
        rows, seen = [], set()
        for provider, value in normalize_ids(ids).items():
            if not value: continue
            key = _bridge_key(server, provider, value)
            raw = win.getProperty(key)
            if not raw: continue
            record = json.loads(raw)
            if not 0 <= time.time() - float(record.get('at') or 0) < _TTL:
                win.clearProperty(key)
                continue
            for row in record.get('rows') or []:
                item = str(row.get('rating_key') or '')
                if item and item not in seen:
                    seen.add(item); rows.append(row)
        return rows
    except Exception:
        return []


def _bridge_write(server, rows):
    try:
        import xbmcgui
        win = xbmcgui.Window(10000)
        fields = ('rating_key', 'media_type', 'title', 'raw_title', 'original_title', 'year', 'ids')
        slim = [{k: row.get(k) for k in fields} for row in rows[:24]]
        groups = {}
        for row in slim:
            for provider, value in normalize_ids(row.get('ids')).items():
                if value: groups.setdefault((provider, value), []).append(row)
        # A short ring bounds shared hints, even during a very long session.
        ring_key = 'dexhub.jellyfin.match.keys'
        ring = json.loads(win.getProperty(ring_key) or '[]')
        for (provider, value), items in groups.items():
            key = _bridge_key(server, provider, value)
            win.setProperty(key, json.dumps({'at':time.time(), 'rows':items}, separators=(',',':')))
            if key in ring: ring.remove(key)
            ring.append(key)
        while len(ring) > 120:
            win.clearProperty(ring.pop(0))
        win.setProperty(ring_key, json.dumps(ring, separators=(',',':')))
    except Exception:
        pass


def find_matches(server, ids, media_type, title, limit, year, search, resolve):
    # Public GetItems does not accept AnyProviderIdEquals. Passing it through
    # returns an unfiltered page on some versions: never trust that response.
    with _LOCK:
        known = [dict(row) for at,row in _KNOWN.get(_scope(server),{}).values() if time.monotonic()-at < _TTL]
    cached = _selection(known + _bridge_read(server, ids), ids, media_type) if any(normalize_ids(ids).values()) else []
    if cached:
        fresh = resolve(server, cached, limit)
        verified = _selection(fresh, ids, media_type)
        if verified:
            _bridge_write(server, verified)
            return verified[:int(limit)]
    if not str(title or '').strip():return []
    kind = 'show' if str(media_type).lower() in ('series','show','episode','tv') else 'movie'
    query = re.sub(r'\s*\((?:18|19|20)\d{2}\)\s*$', '', str(title)).strip()
    rows = search(server, query, media_type=kind, limit=min(80,max(40,int(limit)*2)))
    selected = _selection(rows, ids, media_type, title, year)
    if not selected:return []
    verified = _selection(resolve(server,selected,limit),ids,media_type,title,year)[:int(limit)]
    if verified: _bridge_write(server, verified)
    return verified
