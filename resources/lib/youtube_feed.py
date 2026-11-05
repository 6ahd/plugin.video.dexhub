# -*- coding: utf-8 -*-
"""YouTube in Dex Hub (v5.10.497): rows, series and search.

What youtube.com and the YouTube app ask YouTube for (its InnerTube API: no
key, no account), in the device's country (device_region):

- rows are searches YouTube answers for the country: series as their
  playlists (a Shami or Turkish series is a playlist of its episodes), new
  episodes, music, sport;
- a playlist opens as its episodes, in order;
- a video plays through the official YouTube add-on (plugin.video.youtube):
  its own quality and subtitle settings, and it keeps up with YouTube.
"""
import json
import os
import re
import threading
import time
from urllib.request import Request, urlopen

from . import device_region as R

API = 'https://www.youtube.com/youtubei/v1/%s?prettyPrint=false'
WEB_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
WEB_VERSION = '2.20261030.02.00'
TIMEOUT = 12
KEEP = 1800            # s a row is reused
_lock = threading.Lock()
_mem = {}


def _t(ar, en):
    return en if R.language() == 'en' else ar


def _log(text):
    try:
        import xbmc
        xbmc.log('[DexHub] youtube: %s' % text, xbmc.LOGINFO)
    except Exception:
        pass


# ------------------------------------------------------------ search filters
def params(kind='video', date='', length='', views=False):
    """YouTube's search filter (its protobuf, base64): kind video|playlist, date week|month, length long."""
    import base64
    inner = b''
    if date:
        inner += bytes([0x08, {'today': 2, 'week': 3, 'month': 4, 'year': 5}[date]])
    inner += bytes([0x10, {'video': 1, 'channel': 2, 'playlist': 3}[kind]])
    if length:
        inner += bytes([0x18, {'short': 1, 'long': 2, 'medium': 3}[length]])
    out = (bytes([0x08, 3]) if views else b'') + bytes([0x12, len(inner)]) + inner
    return base64.b64encode(out).decode()


# ------------------------------------------------------------ InnerTube
def network_guard():
    """IPv4 first where IPv6 goes nowhere (net_guard.py): a video waited 78 s there."""
    from . import net_guard
    net_guard.install()


def _web_context():
    return {'client': {'clientName': 'WEB', 'clientVersion': R.seen()[1] or WEB_VERSION,
                       'hl': R.language(), 'gl': R.country()}}


def _ask(endpoint, body):
    network_guard()
    context = _web_context()
    headers = {'User-Agent': WEB_UA, 'X-Youtube-Client-Name': '1', 'X-Youtube-Client-Version': context['client']['clientVersion'],
               'Origin': 'https://www.youtube.com'}
    headers['Content-Type'] = 'application/json'
    data = json.dumps(dict(body, context=context)).encode('utf-8')
    with urlopen(Request(API % endpoint, data=data, headers=headers), timeout=TIMEOUT) as answer:
        return json.loads(answer.read().decode('utf-8'))


def _kept(key, make):
    now = time.time()
    with _lock:
        held = _mem.get(key)
        if held and now - held[0] < KEEP:
            return held[1]
    value = make()
    if value:
        with _lock:
            _mem[key] = (now, value)
            if len(_mem) > 80:
                _mem.pop(next(iter(_mem)))
    return value


def _text(node):
    if not node:
        return ''
    if isinstance(node, str):
        return node
    if node.get('simpleText'):
        return node['simpleText']
    if node.get('content'):
        return node['content']
    return ''.join(run.get('text', '') for run in node.get('runs') or [])


def _walk(node, key, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.append(v)
            else:
                _walk(v, key, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, key, out)
    return out


def _first_url(node):
    """The biggest picture in a node's thumbnail sources."""
    found = _walk(node, 'sources', []) + _walk(node, 'thumbnails', [])
    for sources in found:
        if isinstance(sources, list) and sources and isinstance(sources[0], dict) and sources[0].get('url'):
            url = sources[-1]['url']
            return ('https:' + url) if url.startswith('//') else url
    return ''


def _channel_of(node):
    for endpoint in _walk(node, 'browseEndpoint', []):
        cid = (endpoint or {}).get('browseId') or ''
        if cid.startswith('UC'):
            return cid
    return ''


def _avatar_of(node):
    for url in _walk(node, 'url', []):
        if isinstance(url, str) and ('yt3.' in url or 'ggpht' in url):
            return ('https:' + url) if url.startswith('//') else url
    return ''


def _is_live(node):
    blob = json.dumps(node.get('badges') or []) + json.dumps(node.get('thumbnailOverlays') or [])
    return 'LIVE' in blob and 'UPCOMING' not in blob


def _video(v):
    vid = v.get('videoId')
    if not vid:
        return None
    return {'kind': 'video', 'id': vid, 'title': _text(v.get('title')), 'cid': _channel_of(v.get('ownerText') or v.get('shortBylineText') or v),
            'avatar': _avatar_of(v.get('channelThumbnailSupportedRenderers') or v.get('channelThumbnail') or {}), 'live': _is_live(v),
            'length': _text(v.get('lengthText')), 'channel': _text(v.get('ownerText') or v.get('shortBylineText')),
            'when': _text(v.get('publishedTimeText')), 'views': _text(v.get('shortViewCountText') or v.get('viewCountText')),
            'thumb': 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % vid,
            'big': 'https://i.ytimg.com/vi/%s/maxresdefault.jpg' % vid}


def _lockup(l):
    kind = l.get('contentType') or ''
    pid = l.get('contentId') or ''
    meta = (l.get('metadata') or {}).get('lockupMetadataViewModel') or {}
    title = _text(meta.get('title'))
    badge, live = '', False
    for b in _walk(l, 'thumbnailBadgeViewModel', []):
        badge = b.get('text') or badge
        live = live or 'LIVE' in str(b.get('badgeStyle') or '')
    parts = []
    for row in _walk(meta, 'metadataParts', []):
        for part in row or []:
            text = _text((part or {}).get('text'))
            if text:
                parts.append(text)
    channel = parts[0] if parts else ''
    if 'VIDEO' in kind and pid:
        # a playlist's episode (and newer search answers) come as lockups too
        return {'kind': 'video', 'id': pid, 'title': title, 'length': '' if live else badge, 'channel': channel,
                'cid': _channel_of(meta), 'avatar': _avatar_of(meta), 'live': live,
                'when': parts[-1] if len(parts) > 1 else '', 'views': '',
                'thumb': 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % pid, 'big': 'https://i.ytimg.com/vi/%s/maxresdefault.jpg' % pid}
    if 'PLAYLIST' not in kind and 'SHOW' not in kind and 'PODCAST' not in kind:
        return None
    thumb = _first_url(l.get('contentImage') or {})
    return {'kind': 'playlist', 'id': pid, 'title': title, 'count': badge, 'channel': channel, 'thumb': thumb, 'big': thumb}


def items_of(answer):
    out = []
    for v in _walk(answer, 'videoRenderer', []):
        item = _video(v)
        if item:
            out.append(item)
    for l in _walk(answer, 'lockupViewModel', []):
        item = _lockup(l)
        if item and item['id']:
            out.append(item)
    for c in _walk(answer, 'channelRenderer', []):
        if c.get('channelId'):
            out.append({'kind': 'channel', 'id': c['channelId'], 'title': _text(c.get('title')),
                        'thumb': _avatar_of(c.get('thumbnail') or {}), 'channel': _text(c.get('videoCountText') or c.get('subscriberCountText'))})
    for v in _walk(answer, 'playlistVideoRenderer', []):
        item = _video(v)
        if item:
            item['index'] = _text(v.get('index'))
            out.append(item)
    seen, unique = set(), []
    for item in out:
        if item['id'] not in seen:
            seen.add(item['id'])
            unique.append(item)
    return unique


def search(query, filters=''):
    key = 'search:%s:%s:%s' % (R.country(), query, filters)

    def make():
        body = {'query': query}
        if filters:
            body['params'] = filters
        try:
            return items_of(_ask('search', body))
        except Exception as exc:
            _log('search %r: %s' % (query, exc.__class__.__name__))
            return []
    return _kept(key, make)


def playlist(playlist_id, pages=4):
    """A playlist's videos in order (100 a page)."""
    key = 'playlist:%s' % playlist_id

    def make():
        out = []
        try:
            answer = _ask('browse', {'browseId': 'VL' + playlist_id})
        except Exception as exc:
            _log('playlist %s: %s' % (playlist_id, exc.__class__.__name__))
            return []
        for _page in range(pages):
            out += [i for i in items_of(answer) if i['kind'] == 'video']
            tokens = [c.get('continuationCommand', {}).get('token') for c in _walk(answer, 'continuationEndpoint', [])]
            tokens = [t for t in tokens if t]
            if not tokens:
                break
            try:
                answer = _ask('browse', {'continuation': tokens[-1]})
            except Exception:
                break
        seen, unique = set(), []
        for item in out:
            if item['id'] not in seen:
                seen.add(item['id'])
                unique.append(item)
        return unique
    return _kept(key, make)


# ------------------------------------------------------------ topics
# YouTube's own topic pages: their shelves come back in the device's language
# and country (tested for SA, US and DE), so they suit every user.
TOPICS = {'music': 'UC-9-kyTW8ZkZNDHQJ6FgpwQ', 'sport': 'UCEgdi0XIXXZ-qJOFPf4JSKw',
          'news': 'UCYfdidRxbB8Qhf0Nx7ioOYw', 'live': 'UC4R8DWoMoI7CAwX8_LjQHig',
          'gaming': 'UCOpNcN46UbXVtpKMrmU4Abg'}


def topic(name):
    """[(shelf title, items)] of a topic page, the shelves in YouTube's order."""
    key = 'topic:%s:%s:%s' % (R.country(), R.language(), name)

    def make():
        try:
            answer = _ask('browse', {'browseId': TOPICS[name]})
        except Exception as exc:
            _log('topic %s: %s %s' % (name, exc.__class__.__name__, str(exc)[:160]))
            return []
        shelves = []
        for shelf in _walk(answer, 'richShelfRenderer', []) + _walk(answer, 'shelfRenderer', []):
            items = [i for i in items_of(shelf) if i['kind'] in ('video', 'playlist')]
            title = _text(shelf.get('title'))
            if title and len(items) >= 4:
                shelves.append((title, items))
        if not shelves:
            items = [i for i in items_of(answer) if i['kind'] in ('video', 'playlist')]
            if items:
                shelves.append(('', items))
        return shelves
    return _kept(key, make)


def _mix(*lists, limit=40):
    """The lists taken in turn, no repeats."""
    out, seen = [], set()
    for row in range(max([len(x) for x in lists] or [0])):
        for items in lists:
            if row < len(items) and items[row]['id'] not in seen:
                seen.add(items[row]['id'])
                out.append(items[row])
    return out[:limit]


def trending():
    firsts = [shelves[0][1] for shelves in (topic('music'), topic('sport'), topic('news'), topic('gaming')) if shelves]
    return _mix(*firsts)


def live_now():
    """Live now: what the followed channels stream first, then YouTube's live page."""
    mine = []
    for row in _parallel(lambda f: [i for i in channel(f['id'])['live'] if i.get('live')], follows()[:24]):
        mine += row
    shelves = topic('live')
    public = [i for _t_, items in shelves[:3] for i in items if i.get('live')] or (shelves[0][1] if shelves else [])
    return _mix(mine, public, limit=40) if mine else public[:40]


# ------------------------------------------------------------ channels
CHANNEL_TABS = {'videos': 'EgZ2aWRlb3PyBgQKAjoA', 'live': 'EgdzdHJlYW1z8gYECgJ6AA==',
                'playlists': 'EglwbGF5bGlzdHPyBgQKAkIA'}


def channel(cid):
    """A channel: its name and picture, latest videos, streams (live first) and playlists."""
    key = 'channel:%s' % cid

    def make():
        out = {'id': cid, 'title': '', 'thumb': ''}

        def tab(name):
            try:
                return name, _ask('browse', {'browseId': cid, 'params': CHANNEL_TABS[name]})
            except Exception as exc:
                _log('channel %s %s: %s' % (cid, name, exc.__class__.__name__))
                return name, {}
        for name, answer in _parallel(tab, list(CHANNEL_TABS)):
            items = items_of(answer)
            if name == 'playlists':
                out[name] = [i for i in items if i['kind'] == 'playlist']
            else:
                out[name] = [i for i in items if i['kind'] == 'video']
            if name == 'live':
                out[name].sort(key=lambda i: not i.get('live'))
            meta = (answer.get('metadata') or {}).get('channelMetadataRenderer') or {}
            if meta and not out['title']:
                out['title'] = meta.get('title') or ''
                out['thumb'] = _first_url(meta.get('avatar') or {})
        for name in CHANNEL_TABS:
            out.setdefault(name, [])
            for item in out[name]:
                # a channel's own tabs put the views where the channel's name goes
                item['cid'] = item.get('cid') or cid
                item['when'] = item.get('when') or item.get('channel') or ''
                item['channel'] = out['title']
                item['avatar'] = item.get('avatar') or out['thumb']
        return out if (out['title'] or out['videos']) else None
    return _kept(key, make) or {'id': cid, 'title': '', 'thumb': '', 'videos': [], 'live': [], 'playlists': []}


def _parallel(job, things, workers=6):
    if not things:
        return []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(things)), thread_name_prefix='DexHub-youtube-read') as pool:
        return list(pool.map(job, things))


def new_from_follows():
    """The followed channels' latest videos, newest of each in turn."""
    rows = _parallel(lambda f: channel(f['id'])['videos'][:12], follows()[:30])
    return _mix(*rows, limit=60)


def suggested_channels(items):
    """Channels to follow: the ones behind a page's videos, with their pictures."""
    out, seen = [], set(f['id'] for f in follows())
    for item in items:
        cid = item.get('cid')
        if cid and cid not in seen and item.get('avatar'):
            seen.add(cid)
            out.append({'kind': 'channel', 'id': cid, 'title': item.get('channel') or '', 'thumb': item['avatar'],
                        'channel': ''})
    return out[:24]


# ------------------------------------------------------------ the linked account
# (5.10.499) With YouTube linked, the account's own pages come from YouTube's
# TV client with the add-on's TV sign-in (the one the add-on itself plays
# with): subscriptions' new videos, history (with how far each video got, on
# any device), the account's home, watch later and the subscribed channels.
TV_VERSION = '7.20251029.10.00'
TV_UA = 'Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version'
ACCOUNT_KEEP = 600
_token = {'value': '', 'until': 0.0}


def _tv_token():
    """The add-on's TV access token (renewed by the add-on when it runs out); '' unlinked."""
    if _token['value'] and time.time() < _token['until']:
        return _token['value']
    try:
        _addon_code()
        network_guard()
        from youtube_plugin.youtube.provider import Provider
        from youtube_plugin.kodion.context import XbmcContext
        context = XbmcContext()
        Provider().get_client(context=context)          # renews expired tokens
        tokens, _n, _expiry = context.get_access_manager().get_access_tokens()
        value = tokens[0] if tokens else ''
    except Exception as exc:
        _log('account sign-in: %s' % exc.__class__.__name__)
        value = ''
    _token.update(value=value or '', until=time.time() + (1500 if value else 60))
    return _token['value']


def _tv_ask(browse_id):
    token = _tv_token()
    if not token:
        return None
    network_guard()
    body = {'browseId': browse_id,
            'context': {'client': {'clientName': 'TVHTML5', 'clientVersion': TV_VERSION,
                                   'hl': R.language(), 'gl': R.country()}}}
    request = Request(API % 'browse', data=json.dumps(body).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
                               'User-Agent': TV_UA, 'X-Youtube-Client-Name': '7',
                               'X-Youtube-Client-Version': TV_VERSION})
    with urlopen(request, timeout=TIMEOUT) as answer:
        return json.loads(answer.read().decode('utf-8'))


def account_info():
    """{'name', 'premium'} of the linked account (YouTube's account list: the
    selected account's name, and shouldShowPremiumLogo, which is what YouTube
    itself shows its Premium logo by). {} when not linked."""
    if not linked():
        return {}
    with _lock:
        held = _mem.get('account:info')
        if held and time.time() - held[0] < 3600:
            return held[1]
    info = {}
    try:
        token = _tv_token()
        if token:
            network_guard()
            body = {'context': {'client': {'clientName': 'TVHTML5', 'clientVersion': TV_VERSION,
                                           'hl': R.language(), 'gl': R.country()}}}
            request = Request(API % 'account/accounts_list', data=json.dumps(body).encode('utf-8'),
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
                                       'User-Agent': TV_UA, 'X-Youtube-Client-Name': '7',
                                       'X-Youtube-Client-Version': TV_VERSION})
            with urlopen(request, timeout=TIMEOUT) as answer:
                found = json.loads(answer.read().decode('utf-8'))
            accounts = _walk(found, 'accountItem', [])
            chosen = next((a for a in accounts if a.get('isSelected')), accounts[0] if accounts else {})
            info = {'name': _text(chosen.get('accountName')), 'premium': bool(chosen.get('shouldShowPremiumLogo'))}
    except Exception as exc:
        _log('account info: %s' % exc.__class__.__name__)
    if info:
        with _lock:
            _mem['account:info'] = (time.time(), info)
    return info


def _seconds(length):
    total = 0
    for part in str(length or '').split(':'):
        if not part.strip().isdigit():
            return 0
        total = total * 60 + int(part)
    return total


def _tile(t):
    """A TV card: a video (with how far it was watched) or a channel."""
    kind = t.get('contentType') or ''
    cid = t.get('contentId') or ''
    meta = (t.get('metadata') or {}).get('tileMetadataRenderer') or {}
    title = _text(meta.get('title'))
    lines = []
    for line in meta.get('lines') or []:
        parts = [_text((i.get('lineItemRenderer') or {}).get('text')) for i in (line.get('lineRenderer') or {}).get('items') or []]
        text = ' '.join(p for p in parts if p and p.strip() not in ('•', '·')).strip()
        if text:
            lines.append(text)
    header = (t.get('header') or {}).get('tileHeaderRenderer') or {}
    thumb = _first_url(header.get('thumbnail') or {})
    if 'CHANNEL' in kind and cid:
        return {'kind': 'channel', 'id': cid, 'title': title, 'thumb': thumb, 'channel': lines[0] if lines else ''}
    if 'PLAYLIST' in kind and cid:
        return {'kind': 'playlist', 'id': cid, 'title': title, 'thumb': thumb, 'big': thumb,
                'channel': lines[0] if lines else '', 'count': ''}
    if 'VIDEO' not in kind or not cid:
        return None
    length, live, pct = '', False, 0
    for overlay in header.get('thumbnailOverlays') or []:
        status = overlay.get('thumbnailOverlayTimeStatusRenderer')
        if status:
            if 'LIVE' in str(status.get('style') or ''):
                live = True
            else:
                length = _text(status.get('text'))
        resume = overlay.get('thumbnailOverlayResumePlaybackRenderer')
        if resume:
            pct = int(resume.get('percentDurationWatched') or 0)
    item = {'kind': 'video', 'id': cid, 'title': title, 'length': length, 'live': live,
            'channel': lines[0] if lines else '', 'when': lines[1] if len(lines) > 1 else '',
            'cid': _channel_of(t.get('onLongPressCommand') or {}), 'avatar': '',
            'thumb': 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % cid,
            'big': 'https://i.ytimg.com/vi/%s/maxresdefault.jpg' % cid}
    total = _seconds(length)
    if pct and total and not live:
        item.update(total=total, pos=int(total * pct / 100.0))
    return item


ACCOUNT_PAGES = {'subscriptions': 'FEsubscriptions', 'history': 'FEhistory', 'home': 'FEwhat_to_watch',
                 'later': 'VLWL', 'channels': 'FEchannels'}


def account(page):
    """A page of the linked account as cards ([] when not linked or it fails)."""
    if not linked():
        return []
    key = 'account:%s' % page
    now = time.time()
    with _lock:
        held = _mem.get(key)
        if held and now - held[0] < ACCOUNT_KEEP:
            return held[1]
    try:
        answer = _tv_ask(ACCOUNT_PAGES[page]) or {}
    except Exception as exc:
        _log('account %s: %s' % (page, exc.__class__.__name__))
        answer = {}
    items, seen = [], set()
    for t in _walk(answer, 'tileRenderer', []):
        item = _tile(t)
        if item and item['id'] not in seen:
            seen.add(item['id'])
            items.append(item)
    if items:
        with _lock:
            _mem[key] = (now, items)
    return items


def account_continue():
    """Started on any device and not finished (YouTube's own record), then this device's."""
    mine = [i for i in account('history') if i.get('total') and 0.03 < float(i.get('pos') or 0) / i['total'] < 0.95]
    seen = set(i['id'] for i in mine)
    return mine + [h for h in continuing() if h['id'] not in seen]


# ------------------------------------------------------------ what this device keeps
# Follows, watch history and my list live in Dex Hub's profile (the official
# add-on's link carries no YouTube Data API key, so YouTube's own lists are not
# readable through it).
STORE = 'youtube_library.json'
_store_lock = threading.Lock()


def _library():
    from . import profile_scope
    data = profile_scope._read(profile_scope.path(STORE), {})
    return data if isinstance(data, dict) else {}


def _save(change):
    from . import profile_scope
    with _store_lock:
        data = _library()
        change(data)
        profile_scope._write(profile_scope.path(STORE), data)


def follows():
    return [f for f in _library().get('follows') or [] if f.get('id')]


def is_followed(cid):
    return any(f['id'] == cid for f in follows())


def follow(cid, title='', thumb=''):
    if not cid or not (title and thumb):
        info = channel(cid) if cid else {}
        title, thumb = title or info.get('title', ''), thumb or info.get('thumb', '')

    def change(data):
        rows = [f for f in data.get('follows') or [] if f.get('id') != cid]
        data['follows'] = [{'id': cid, 'title': title, 'thumb': thumb}] + rows
    _save(change)


def unfollow(cid):
    _save(lambda data: data.__setitem__('follows', [f for f in data.get('follows') or [] if f.get('id') != cid]))


def _slim(item):
    return dict((k, item.get(k)) for k in ('kind', 'id', 'title', 'channel', 'cid', 'avatar', 'length', 'count', 'thumb', 'big')
                if item.get(k))


def history():
    return [h for h in _library().get('history') or [] if h.get('id')]


def remember(item, position=0, total=0):
    """A video watched: first in the history, with how far it got."""
    def change(data):
        rows = [h for h in data.get('history') or [] if h.get('id') != item['id']]
        entry = _slim(item)
        entry.update(kind='video', pos=int(position), total=int(total), at=int(time.time()))
        data['history'] = ([entry] + rows)[:200]
    _save(change)


def forget(video_id=None):
    _save(lambda data: data.__setitem__('history', [h for h in data.get('history') or [] if video_id and h.get('id') != video_id]))


def continuing():
    """Started and not finished (5 % to 92 %)."""
    return [h for h in history() if h.get('total') and 0.05 < float(h.get('pos') or 0) / h['total'] < 0.92]


def my_list():
    return [m for m in _library().get('list') or [] if m.get('id')]


def in_list(item_id):
    return any(m['id'] == item_id for m in my_list())


def toggle_list(item):
    """Add a video or a playlist to my list, or take it off; True when it is on now."""
    on = not in_list(item['id'])

    def change(data):
        rows = [m for m in data.get('list') or [] if m.get('id') != item['id']]
        data['list'] = ([_slim(item)] + rows) if on else rows
    _save(change)
    return on


# ------------------------------------------------------------ the pages' rows
# A row is {'title', 'kind', ...}: kind search (query, filters), topic (name:
# one section a shelf, up to 'slots'), or one of the page's own: continue,
# history, list, follows, new, live, trending, suggest.
def _rows(*specs):
    return [{'title': _t(ar, en), 'kind': 'search', 'query': q, 'filters': f} for ar, en, q, f in specs]


def _own(kind, ar, en, **more):
    return dict({'title': _t(ar, en), 'kind': kind}, **more)


ARAB = ('SA', 'AE', 'KW', 'QA', 'BH', 'OM', 'YE', 'IQ', 'SY', 'JO', 'LB', 'PS', 'EG', 'SD', 'LY', 'TN', 'DZ', 'MA', 'MR')


def arabic():
    """The Arabic pages (Syrian, Turkish, Gulf, Kids) for Arabic, or anyone in an Arab country."""
    return R.language() == 'ar' or R.country() in ARAB


def chips():
    out = [('home', _t('الرئيسية', 'Home')), ('library', _t('مكتبتي', 'Library')), ('live', _t('مباشر', 'Live')),
           ('trending', _t('الرائج', 'Trending')), ('music', _t('موسيقى', 'Music')), ('sport', _t('رياضة', 'Sport')),
           ('news', _t('أخبار', 'News'))]
    if arabic():
        out += [('shami', 'شامي'), ('turkish', 'تركي'), ('gulf', 'خليجي'), ('kids', 'أطفال')]
    else:
        out += [('gaming', _t('ألعاب', 'Gaming'))]
    return out


def rows_of(chip):
    if linked() and chip in ('home', 'library'):
        return _account_rows(chip)
    if chip == 'library':
        return [_own('continue', 'متابعة المشاهدة', 'Continue watching'),
                _own('follows', 'قنواتك', 'Your channels'),
                _own('list', 'قائمتي', 'My list'),
                _own('history', 'سجل المشاهدة', 'Watch history'),
                _own('channels', '', '', slots=16)]
    if chip == 'live':
        return [_own('live', 'بث مباشر الآن', 'Live now'), {'title': '', 'kind': 'topic', 'name': 'live', 'slots': 8, 'skip': 1}]
    if chip == 'trending':
        return [_own('trending', 'رائج الآن', 'Trending now')] + [
            {'title': '', 'kind': 'topic', 'name': n, 'slots': 2} for n in ('music', 'sport', 'news', 'gaming')]
    if chip in ('music', 'sport', 'news', 'gaming'):
        extra = []
        if chip == 'music' and arabic():
            extra = _arabic_rows('music')[:3]
        return [{'title': '', 'kind': 'topic', 'name': chip, 'slots': 10}] + extra
    if chip in ('shami', 'turkish', 'gulf', 'kids'):
        return _arabic_rows(chip)
    # home: what is mine first, then live and trending, then the rest
    mine = [_own('continue', 'متابعة المشاهدة', 'Continue watching'),
            _own('follows', 'قنواتك', 'Your channels'),
            _own('new', 'جديد قنواتك', 'New from your channels'),
            _own('live', 'بث مباشر الآن', 'Live now'),
            _own('trending', 'رائج الآن', 'Trending now'),
            _own('list', 'قائمتي', 'My list')]
    if not follows():
        mine.insert(2, _own('suggest', 'قنوات قد تعجبك', 'Channels to follow'))
    if arabic():
        return mine + _arabic_rows('home')
    return mine + [{'title': '', 'kind': 'topic', 'name': 'music', 'slots': 2},
                   {'title': '', 'kind': 'topic', 'name': 'sport', 'slots': 2}]


def _account_rows(chip):
    """Linked: the account's own sections, as the YouTube app has them."""
    if chip == 'library':
        return [_own('a_continue', 'متابعة المشاهدة', 'Continue watching'),
                _own('a_history', 'سجل المشاهدة', 'History'),
                _own('a_later', 'المشاهدة لاحقاً', 'Watch later'),
                _own('list', 'قائمتي', 'My list'),
                _own('a_channels', 'اشتراكاتك', 'Your subscriptions')]
    rows = [_own('a_continue', 'متابعة المشاهدة', 'Continue watching'),
            _own('a_channels', 'اشتراكاتك', 'Your subscriptions'),
            _own('a_new', 'جديد اشتراكاتك', 'New from your subscriptions'),
            _own('a_home', 'مقترح لك', 'Recommended for you'),
            _own('live', 'بث مباشر الآن', 'Live now'),
            _own('trending', 'رائج الآن', 'Trending now'),
            _own('a_later', 'المشاهدة لاحقاً', 'Watch later'),
            _own('list', 'قائمتي', 'My list')]
    if arabic():
        return rows + _arabic_rows('home')
    return rows + [{'title': '', 'kind': 'topic', 'name': 'music', 'slots': 2}]


def fill(row):
    """[(title, items, shape)] a row answers: shape wide (videos) or round (channels)."""
    kind = row['kind']
    if kind == 'a_continue':
        found = account_continue()
        return [(row['title'], found, 'wide')] if found else []
    if kind in ('a_history', 'a_later', 'a_new', 'a_home'):
        page = {'a_history': 'history', 'a_later': 'later', 'a_new': 'subscriptions', 'a_home': 'home'}[kind]
        found = account(page)
        return [(row['title'], found[:60], 'wide')] if found else []
    if kind == 'a_channels':
        found = account('channels')
        return [(row['title'], found[:80], 'round')] if found else []
    if kind == 'search':
        found = search(row['query'], row['filters'])
        return [(row['title'], found, 'round' if found and found[0]['kind'] == 'channel' else 'wide')]
    if kind == 'topic':
        shelves = topic(row['name'])[row.get('skip', 0):row.get('skip', 0) + row.get('slots', 1)]
        return [(title or row['title'], items, 'wide') for title, items in shelves]
    if kind == 'continue':
        return [(row['title'], continuing(), 'wide')]
    if kind == 'history':
        return [(row['title'], history()[:60], 'wide')]
    if kind == 'list':
        return [(row['title'], my_list(), 'wide')]
    if kind == 'follows':
        rows = [dict(kind='channel', **f) for f in follows()]
        return [(row['title'], rows, 'round')] if rows else []
    if kind == 'channels':
        out = []
        for f in follows()[:row.get('slots', 1)]:
            videos = channel(f['id'])['videos'][:20]
            if videos:
                out.append((f.get('title') or videos[0].get('channel') or '', videos, 'wide'))
        return out
    if kind == 'new':
        return [(row['title'], new_from_follows(), 'wide')] if follows() else []
    if kind == 'live':
        return [(row['title'], live_now(), 'wide')]
    if kind == 'trending':
        return [(row['title'], trending(), 'wide')]
    if kind == 'suggest':
        found = suggested_channels(trending() + live_now())
        return [(row['title'], found, 'round')] if found else []
    return []


# ------------------------------------------------------------ a YouTube row on the Home
# (5.10.501) Any of these, as a Home row or a bar section's catalog (action
# youtube_row: plugin.youtube_row_browse).
def row_items(kind, ident='', name=''):
    """The cards of a YouTube row: kind account (ident = a page of the account),
    live, trending, topic (ident = music|sport|news|gaming|live, name = a shelf's
    title or ''), search (ident = the words), channel (ident = its id),
    playlist (ident = its id)."""
    if kind == 'account':
        return account_continue() if ident == 'continue' else account(ident)
    if kind == 'live':
        return live_now()
    if kind == 'trending':
        return trending()
    if kind == 'topic':
        shelves = topic(ident)
        for title, items in shelves:
            if not name or title == name:
                return items
        return shelves[0][1] if shelves else []
    if kind == 'search':
        return search(ident, params('video'))
    if kind == 'channel':
        return channel(ident)['videos']
    if kind == 'playlist':
        return playlist(ident)
    return []


def row_spec(kind, ident='', name='', title=''):
    """A bar section's catalog (bar_sections spec) for a YouTube row."""
    return {'key': 'youtube:%s:%s:%s' % (kind, ident, name), 'title': title or name or ident or 'YouTube',
            'sub': 'YouTube', 'shape': 'landscape', 'style': '',
            'params': {'action': 'youtube_row', 'kind': kind, 'id': ident, 'name': name, 'title': title}}


def row_choices():
    """[(spec, where)]: the YouTube rows the Home's editor offers."""
    out = []
    if linked():
        for page, ar, en in (('continue', 'متابعة المشاهدة على YouTube', 'YouTube: continue watching'),
                             ('subscriptions', 'جديد اشتراكاتك', 'New from your subscriptions'),
                             ('home', 'مقترح لك على YouTube', 'Recommended on YouTube'),
                             ('later', 'المشاهدة لاحقاً', 'Watch later'), ('history', 'سجل YouTube', 'YouTube history')):
            out.append((row_spec('account', page, title=_t(ar, en)), _t('حسابك', 'Your account')))
    out.append((row_spec('live', title=_t('بث مباشر الآن', 'Live now')), 'YouTube'))
    out.append((row_spec('trending', title=_t('رائج على YouTube', 'Trending on YouTube')), 'YouTube'))
    for name in ('music', 'sport', 'news', 'gaming'):
        for title, _items in topic(name)[:4]:
            out.append((row_spec('topic', name, title, title=title), dict(chips()).get(name) or name))
    channels = account('channels') if linked() else []
    channels = channels or [dict(kind='channel', **f) for f in follows()]
    for c in channels[:60]:
        out.append((row_spec('channel', c['id'], title=c.get('title') or ''), _t('قناة', 'Channel')))
    return out


def channel_rows(cid):
    info = channel(cid)
    out = []
    if any(i.get('live') for i in info['live']):
        out.append((_t('بث مباشر الآن', 'Live now'), [i for i in info['live'] if i.get('live')], 'wide'))
    out.append((_t('أحدث الفيديوهات', 'Latest videos'), info['videos'], 'wide'))
    if info['playlists']:
        out.append((_t('قوائم التشغيل', 'Playlists'), info['playlists'], 'wide'))
    past = [i for i in info['live'] if not i.get('live')]
    if past:
        out.append((_t('بثوث سابقة', 'Past streams'), past, 'wide'))
    return info, out


def _arabic_rows(chip):
    """The Arabic pages' searches (Syrian, Turkish and Gulf series, Arabic music, sport, kids)."""
    P, V = params('playlist'), params('video')
    NEW_EPISODES = params('video', date='month', length='long')
    WEEK = params('video', date='week')
    if chip == 'shami':
        return _rows(('مسلسلات شامية كاملة', 'Complete Syrian series', 'مسلسل شامي كامل', P),
                     ('حلقات شامية جديدة', 'New Syrian episodes', 'مسلسل شامي الحلقة', NEW_EPISODES),
                     ('مسلسلات البيئة الشامية', 'Damascus period dramas', 'مسلسلات البيئة الشامية كاملة', P),
                     ('مسلسلات سورية', 'Syrian dramas', 'مسلسل سوري كامل', P),
                     ('كوميديا سورية', 'Syrian comedy', 'مسلسل كوميدي سوري كامل', P),
                     ('أفلام سورية', 'Syrian films', 'فيلم سوري كامل', params('video', length='long')))
    if chip == 'turkish':
        return _rows(('حلقات تركية جديدة مترجمة', 'New Turkish episodes, subtitled', 'مسلسل تركي الحلقة مترجمة', NEW_EPISODES),
                     ('حلقات تركية جديدة مدبلجة', 'New Turkish episodes, dubbed', 'مسلسل تركي الحلقة مدبلج', NEW_EPISODES),
                     ('مسلسلات تركية مترجمة', 'Turkish series, subtitled', 'مسلسل تركي مترجم كامل', P),
                     ('مسلسلات تركية مدبلجة', 'Turkish series, dubbed', 'مسلسل تركي مدبلج كامل', P),
                     ('مسلسلات تركية تاريخية', 'Turkish historical series', 'مسلسل تركي تاريخي مترجم', P),
                     ('أفلام تركية مترجمة', 'Turkish films, subtitled', 'فيلم تركي مترجم كامل', params('video', length='long')))
    if chip == 'gulf':
        return _rows(('مسلسلات خليجية كاملة', 'Complete Gulf series', 'مسلسل خليجي كامل', P),
                     ('حلقات خليجية جديدة', 'New Gulf episodes', 'مسلسل خليجي الحلقة', NEW_EPISODES),
                     ('مسلسلات سعودية', 'Saudi series', 'مسلسل سعودي كامل', P),
                     ('مسلسلات كويتية', 'Kuwaiti series', 'مسلسل كويتي كامل', P),
                     ('مسرحيات خليجية', 'Gulf plays', 'مسرحية خليجية كاملة', params('video', length='long')))
    if chip == 'music':
        return _rows(('أغاني عربية جديدة', 'New Arabic songs', 'أغنية جديدة 2026', WEEK),
                     ('أغاني خليجية', 'Gulf songs', 'أغاني خليجية', V),
                     ('شيلات', 'Sheilat', 'شيلات جديدة', V),
                     ('أناشيد', 'Nasheeds', 'أناشيد', V),
                     ('أغاني تركية', 'Turkish songs', 'Türkçe pop şarkılar', V),
                     ('قوائم أغاني', 'Song playlists', 'أغاني عربية', P))
    if chip == 'sport':
        return _rows(('ملخصات هذا الأسبوع', "This week's highlights", 'ملخص مباراة', WEEK),
                     ('دوري روشن', 'Saudi Pro League', 'ملخص مباراة دوري روشن', WEEK),
                     ('الدوري الإنجليزي', 'Premier League', 'ملخص الدوري الإنجليزي', WEEK),
                     ('الدوري الإسباني', 'La Liga', 'ملخص الدوري الإسباني', WEEK),
                     ('أهداف', 'Goals', 'أجمل الأهداف', V))
    if chip == 'kids':
        return _rows(('رسوم متحركة عربية', 'Arabic cartoons', 'رسوم متحركة عربية كاملة', P),
                     ('أناشيد أطفال', 'Kids songs', 'أناشيد أطفال', V),
                     ('كرتون مدبلج', 'Dubbed cartoons', 'كرتون مدبلج عربي الحلقة', V))
    # home: a little of each, the series first
    return _rows(('حلقات تركية جديدة', 'New Turkish episodes', 'مسلسل تركي الحلقة مترجمة', NEW_EPISODES),
                 ('مسلسلات شامية كاملة', 'Complete Syrian series', 'مسلسل شامي كامل', P),
                 ('مسلسلات تركية مترجمة', 'Turkish series, subtitled', 'مسلسل تركي مترجم كامل', P),
                 ('مسلسلات تركية مدبلجة', 'Turkish series, dubbed', 'مسلسل تركي مدبلج كامل', P),
                 ('مسلسلات خليجية', 'Gulf series', 'مسلسل خليجي كامل', P),
                 ('أغاني جديدة', 'New songs', 'أغنية جديدة 2026', WEEK),
                 ('ملخصات المباريات', 'Match highlights', 'ملخص مباراة', WEEK))


# ------------------------------------------------------------ playback
ADDON = 'plugin.video.youtube'


def addon_installable():
    """v5.10.515: False on Apple TV. The add-on needs inputstream.adaptive,
    which no repository has for Kodi on tvOS ("The dependency on
    inputstream.adaptive version 19.0.0 could not be satisfied"); there the
    YouTube app plays the videos and holds the account."""
    import xbmc
    return not xbmc.getCondVisibility('System.Platform.TVOS')


def addon_ready(install=True):
    """True when the official YouTube add-on is installed and on. With ``install``
    (only from a page, never while Kodi waits on a plugin call: that would
    deadlock it) Kodi is asked to install it, and the answer is waited for."""
    import xbmc
    if xbmc.getCondVisibility('System.HasAddon(%s)' % ADDON) and xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % ADDON):
        return True
    if not install or not addon_installable():
        return False
    xbmc.executebuiltin('InstallAddon(%s)' % ADDON, True)
    return bool(xbmc.getCondVisibility('System.HasAddon(%s)' % ADDON))


def app_link():
    """Where the official YouTube app takes the video, by device: Apple TV and
    Android open YouTube's own app (4K, its subtitles, the person's account);
    elsewhere (Mac, Windows, Xbox) the official Kodi add-on plays it.
    Returns the Kodi builtin with %s for the video id, or '' for the add-on."""
    import xbmc
    if xbmc.getCondVisibility('System.Platform.TVOS'):
        return 'System.OpenURL("youtube://www.youtube.com/watch?v=%s")'
    if xbmc.getCondVisibility('System.Platform.Android'):
        return 'StartAndroidActivity("",android.intent.action.VIEW,"",https://www.youtube.com/watch?v=%s)'
    return ''


def open_in_app(video_id):
    """True when the device's YouTube app was asked to play ``video_id``."""
    import xbmc
    link = app_link()
    if not link or not re.match(r'^[A-Za-z0-9_-]{6,20}$', video_id or ''):
        return False
    xbmc.executebuiltin(link % video_id)
    return True


def play_url(video_id):
    return 'plugin://%s/play/?video_id=%s' % (ADDON, video_id)


# ------------------------------------------------------------ the add-on, out of sight
# (5.10.497) Dex Hub sets the official add-on up itself, so its setup wizard and
# settings never show: the person only sees Dex Hub's page, a quality choice and
# "Link YouTube". The add-on keeps doing the signing in and the playing.
QUALITIES = ((3, '720p'), (4, '1080p'), (5, '1440p'), (6, '4K'))


def _addon():
    import xbmcaddon
    fix_settings_version()          # before any of its settings is read or written
    return xbmcaddon.Addon(ADDON)


_SETTINGS_FIXED = []


def fix_settings_version():
    """v5.10.513: the add-on's saved settings readable again. A settings.xml
    written as <settings version="4"> (by a newer Kodi or an older add-on) is
    refused whole by Kodi 22's beta ("unable to read setting values from
    version 4"): every YouTube setting fell back to its default, the quality
    chosen in Dex Hub never held and playback failed (a CoreELEC box; a Mac
    the same, mended by hand). The values are the same in version 2."""
    if _SETTINGS_FIXED:
        return
    _SETTINGS_FIXED.append(True)
    try:
        import xbmcvfs
        path = xbmcvfs.translatePath('special://profile/addon_data/%s/settings.xml' % ADDON)
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        head = text[:200]
        if '<settings version="4">' not in head:
            return
        with open(path + '.v4', 'w', encoding='utf-8') as handle:
            handle.write(text)              # kept once, as found
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            handle.write(text.replace('<settings version="4">', '<settings version="2">', 1))
        os.replace(tmp, path)
        _log('settings.xml was version 4 (unreadable by this Kodi): now version 2')
    except Exception:
        return
    try:
        # its service read the settings when it started: off and on again,
        # so it serves videos with them (no Kodi restart)
        import xbmc
        for on in (False, True):
            xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Addons.SetAddonEnabled',
                                            'params': {'addonid': ADDON, 'enabled': on}}))
            xbmc.sleep(500)
    except Exception:
        pass


def quiet_setup():
    """No first-run wizard, subtitles in the person's language (else the video's own,
    else English), the device's country. Cheap: only what differs is written."""
    if not addon_ready(install=False):
        return False
    fix_settings_version()
    try:
        addon = _addon()
        mode, language = subtitle_choice()
        wanted = {'kodion.setup_wizard': 'false',
                  'kodion.video.quality.ask': 'false',
                  # 0 none, 2 the language (YouTube translates when the video has none), 5 every language
                  'kodion.subtitle.languages.num': {'off': '0', 'all': '5'}.get(mode, '2'),
                  'youtube.language': language,
                  'youtube.region': R.country(),
                  # its local server (the video's quality list) answers at once
                  # instead of sleeping between videos
                  'youtube.http.idle_sleep': 'false',
                  # the player fetches the video from YouTube itself (Kodi's own
                  # downloader) instead of through the add-on's Python server,
                  # which waited on IPv6 for every piece where IPv6 goes nowhere
                  'youtube.http.stream_redirect': 'true',
                  # subtitles downloaded while the video is looked up (in Dex Hub's
                  # process, IPv4 first) instead of through that server: the
                  # player waited 20 s on each subtitle track there
                  'kodion.subtitle.download': 'true'}
        import xbmc
        if xbmc.getCondVisibility('System.Platform.OSX'):
            # On macOS the add-on finds its own address by connecting a UDP socket
            # to port 0 of its listen address, which macOS refuses (errno 49); it
            # then hands out the network address while its server only listens on
            # 127.0.0.1, so every video fails. Listening on all addresses (its
            # server still only answers this network) makes the two agree.
            wanted['kodion.http.listen'] = '0.0.0.0'
        for key, value in wanted.items():
            if addon.getSetting(key) != value:
                addon.setSetting(key, value)
        return True
    except Exception as exc:
        _log('quiet setup: %s' % exc)
        return False


# ------------------------------------------------------------ subtitles
# (5.10.499) The add-on picks the subtitle of its language and, when the video
# has none, asks YouTube to translate one (auto-translate, as on YouTube).
SUB_LANGS = (('ar', 'العربية', 'Arabic'), ('en', 'الإنجليزية', 'English'), ('tr', 'التركية', 'Turkish'),
             ('fr', 'الفرنسية', 'French'), ('ur', 'الأوردية', 'Urdu'), ('fa', 'الفارسية', 'Persian'),
             ('id', 'الإندونيسية', 'Indonesian'), ('hi', 'الهندية', 'Hindi'), ('es', 'الإسبانية', 'Spanish'),
             ('de', 'الألمانية', 'German'), ('ru', 'الروسية', 'Russian'), ('it', 'الإيطالية', 'Italian'),
             ('pt', 'البرتغالية', 'Portuguese'), ('ja', 'اليابانية', 'Japanese'), ('ko', 'الكورية', 'Korean'),
             ('zh-Hans', 'الصينية', 'Chinese'))


def subtitle_choices():
    """[(key, label)]: off, the device's language, each language, every language."""
    out = [('off', _t('بدون ترجمة', 'Off')),
           ('auto', _t('لغة الجهاز (ترجمة تلقائية)', "Device language (auto-translated)"))]
    out += [(code, _t('%s (ترجمة تلقائية لو ما فيه)', '%s (auto-translated if missing)') % _t(ar, en)) for code, ar, en in SUB_LANGS]
    return out + [('all', _t('كل اللغات (تختار من المشغّل)', 'Every language (pick in the player)'))]


def subtitle_choice():
    """(mode, the add-on's language): mode off, all, or the language chosen."""
    key = (_library().get('subtitles') or 'auto')
    ui = 'en-US' if R.language() == 'en' else 'ar'
    if key in ('off', 'all', 'auto'):
        return key, ui
    return key, key


def set_subtitles(key):
    _save(lambda data: data.__setitem__('subtitles', key))
    quiet_setup()


def apply_subtitles(player):
    """Once a video plays: the chosen language's subtitle on (its translation
    when the video has none), or subtitles off. 'Every language' leaves the
    player's own choice. True when done; False while the player has not
    listed its subtitles yet (the caller asks again)."""
    import xbmc
    mode, language = subtitle_choice()
    try:
        if mode == 'off':
            player.showSubtitles(False)
            return True
        if mode == 'all':
            return True
        streams = [str(s or '').lower() for s in player.getAvailableSubtitleStreams()]
        if not streams:
            return False
        base = language.split('-')[0].lower()
        wanted = (xbmc.convertLanguage(base, xbmc.ISO_639_2) or base).lower()
        # downloaded files carry the language in their name ("<id> ar (External)"),
        # and the player reports odd codes for them (asr): the names are read too
        try:
            names = [str(t.get('name') or '').lower() for t in
                     (_rpc('Player.GetProperties', {'playerid': 1, 'properties': ['subtitles']}).get('subtitles') or [])]
        except Exception:
            names = []
        for index, stream in enumerate(streams):
            name = names[index] if index < len(names) else ''
            if stream.startswith(wanted) or stream.startswith(base) or (' %s ' % base) in (' %s ' % re.sub(r'[^a-z]+', ' ', name)):
                player.setSubtitleStream(index)
                player.showSubtitles(True)
                return True
        return True
    except Exception as exc:
        _log('subtitles: %s' % exc.__class__.__name__)
        return True


def subtitle_label():
    return dict(subtitle_choices()).get(_library().get('subtitles') or 'auto', '')


def quality():
    """The height the add-on plays at, as one of QUALITIES' labels."""
    try:
        return dict(QUALITIES).get(int(_addon().getSetting('kodion.mpd.quality.selection') or 4), '1080p')
    except Exception:
        return '1080p'


def set_quality(index):
    try:
        _addon().setSetting('kodion.mpd.quality.selection', str(QUALITIES[index][0]))
    except Exception as exc:
        _log('quality: %s' % exc)


def linked():
    """True when the add-on holds a YouTube sign-in (its saved refresh token)."""
    try:
        import xbmcvfs
        path = xbmcvfs.translatePath('special://profile/addon_data/%s/access_manager.json' % ADDON)
        with open(path, encoding='utf-8') as handle:
            manager = json.load(handle).get('access_manager') or {}
        user = (manager.get('users') or {}).get(str(manager.get('current_user', 0))) or {}
        # one token per kind (tv, user, vr, dev), joined with '|': '|||' is none
        return any(part.strip() for part in str(user.get('refresh_token') or '').split('|'))
    except Exception:
        return False


ACTIVATE = 'https://www.youtube.com/activate'


def _module_paths(addon_id, seen):
    """The library folders of *addon_id*'s script.module dependencies, and of
    theirs (requests needs urllib3, idna, certifi, chardet)."""
    import xml.etree.ElementTree as ET
    import xbmcvfs
    out = []
    for base in ('special://home/addons/', 'special://xbmc/addons/'):
        root = xbmcvfs.translatePath(base + addon_id)
        if os.path.isfile(os.path.join(root, 'addon.xml')):
            break
    else:
        return out
    try:
        tree = ET.parse(os.path.join(root, 'addon.xml')).getroot()
    except Exception:
        return out
    if addon_id.startswith('script.module.'):
        for ext in tree.iter('extension'):
            if ext.get('point') == 'xbmc.python.module':
                out.append(os.path.join(root, ext.get('library') or 'lib'))
    for imp in tree.iter('import'):
        dep = imp.get('addon') or ''
        if dep.startswith('script.module.') and dep not in seen:
            seen.add(dep)
            out += _module_paths(dep, seen)
    return out


def _addon_code():
    """The official add-on's own modules (youtube_plugin, youtube_resolver) importable.

    v5.10.513: with the modules it needs. Kodi puts only Dex Hub's own
    dependencies on Dex Hub's path, so on a CoreELEC box the add-on's code
    stopped at 'No module named requests' (Link YouTube showed no code); a
    Mac had requests on the path by chance."""
    import sys
    import xbmcvfs
    fix_settings_version()
    path = xbmcvfs.translatePath('special://home/addons/%s/resources/lib' % ADDON)
    for folder in [path] + _module_paths(ADDON, set()):
        if folder not in sys.path and os.path.isdir(folder):
            sys.path.append(folder)


# v5.10.513: why the last link gave no code, for the page to show (a CoreELEC
# box failed within a second with nothing on screen and no way to see why)
LINK_ERROR = ['']


def _link_failed(kind, exc):
    import traceback
    text = str(getattr(exc, 'json_data', '') or exc or '')
    LINK_ERROR[0] = ('%s: %s' % (exc.__class__.__name__, text))[:160]
    _log('link %s: %s\n%s' % (kind, LINK_ERROR[0], traceback.format_exc()))


def link(show_code, show_state, stopped):
    """Link YouTube the way the add-on's own sign-in does (yt_login._do_login),
    with Dex Hub's page showing each code instead of the add-on's dialog.

    For every kind of sign-in the add-on keeps (tv, user, vr, dev) that it can
    ask for and does not hold yet: ask YouTube for a code, ``show_code(url,
    code, n)``, then wait for the person to allow it (10 minutes at most).
    The new sign-ins are saved where and how the add-on saves them.
    Returns 'linked', 'stopped' or 'failed'."""
    import xbmc
    LINK_ERROR[0] = ''
    try:
        _addon_code()
        network_guard()
        from youtube_plugin.youtube.provider import Provider
        from youtube_plugin.kodion.context import XbmcContext
        context = XbmcContext()
        provider = Provider()
        client = provider.get_client(context)
        manager = context.get_access_manager()
        access_tokens, _n, expiry_timestamp = manager.get_access_tokens()
        refresh_tokens, _n = manager.get_refresh_tokens()
    except Exception as exc:
        _link_failed('setup', exc)
        return 'failed'
    kinds = ['tv', 'user', 'vr', 'dev']
    new_access = dict.fromkeys(kinds, None)
    tokens = list(kinds)
    shown = 0
    monitor = xbmc.Monitor()
    for index, kind in enumerate(kinds):
        try:
            if access_tokens[index] and refresh_tokens[index]:
                new_access[kind] = access_tokens[index]
                tokens[index] = (access_tokens[index], expiry_timestamp, refresh_tokens[index])
                continue
        except IndexError:
            pass
        token = ('', expiry_timestamp, '')
        try:
            answer = client.request_device_and_user_code(index)
            if not answer:
                continue
            interval = int(answer.get('interval', 5))
            interval = interval if 0 < interval <= 60 else 5
            url = answer.get('verification_url') or 'https://www.youtube.com/activate'
            shown += 1
            show_code(url, answer['user_code'], shown)
            for _step in range((10 * 60) // interval):
                if stopped() or monitor.waitForAbort(interval):
                    return 'stopped'
                answer2 = client.request_access_token(index, answer['device_code'])
                if not answer2:
                    break
                if 'error' not in answer2:
                    access, refresh = answer2.get('access_token', ''), answer2.get('refresh_token', '')
                    token = (access, int(answer2.get('expires_in', 3600)) if (access or refresh) else 0, refresh)
                    show_state('ok', shown)
                    break
                if answer2['error'] != 'authorization_pending':
                    _log('link: %s' % answer2['error'])
                    show_state(answer2['error'], shown)
                    break
        except Exception as exc:
            # v5.10.513: the next kind is still asked (one refused kind
            # left the page with no code), and the reason is shown
            _link_failed(kind, exc)
            continue
        finally:
            new_access[kind] = token[0]
            tokens[index] = token
    if not shown and not any(new_access.values()):
        return 'failed'
    provider.reset_client(context=context, access_tokens=new_access)
    manager.update_access_token(context.get_param('addon_id', None), *zip(*tokens))
    return 'linked' if any(new_access.values()) else 'failed'


def sign_in():
    """The add-on's own sign-in: it shows the code to type at youtube.com/activate."""
    import xbmc
    xbmc.executebuiltin('RunPlugin(plugin://%s/sign/in/)' % ADDON)


def sign_out():
    import xbmc
    xbmc.executebuiltin('RunPlugin(plugin://%s/sign/out/)' % ADDON)


# ------------------------------------------------------------ quality in the player
# (5.10.497) Dex Hub asks the add-on's public resolver (youtube_resolver, its
# xbmc.python.module for other add-ons) for the video's stream and gives it to
# the player itself, with every quality as its own video stream
# (inputstream.adaptive's manual-osd), so the player's Sources button lists the
# qualities and switches between them in place, as YouTube's own does.
ISA = 'inputstream.adaptive'


def dexhub_url(video_id, title=''):
    from urllib.parse import urlencode
    query = {'action': 'youtube_play', 'v': video_id}
    if title:
        query['title'] = title
    return 'plugin://plugin.video.dexhub/?' + urlencode(query)


def player_item(video_id, title=''):
    """A ListItem for ``video_id`` with all its qualities, or None (then the add-on plays it)."""
    import sys
    import xbmcaddon
    import xbmcgui
    try:
        lib = os.path.join(xbmcaddon.Addon(ADDON).getAddonInfo('path'), 'resources', 'lib')
        if lib not in sys.path:
            sys.path.append(lib)
        _addon_code()
        network_guard()
        import youtube_resolver
        try:
            # its local server (which serves the video list) sleeps when idle;
            # the add-on wakes it the same way before it plays
            from youtube_plugin.kodion.context import XbmcContext
            from youtube_plugin.kodion.constants import SERVER_WAKEUP
            XbmcContext().ipc_exec(SERVER_WAKEUP, timeout=5)
        except Exception as exc:
            _log('wake: %s' % exc)
        streams = youtube_resolver.resolve(video_id) or []
    except Exception as exc:
        _log('resolver: %s' % exc)
        return None
    stream = next((s for s in streams if s.get('container') == 'mpd' and s.get('url')), None)
    if not stream:
        return None
    meta = stream.get('meta') or {}
    thumbs = meta.get('thumbnails') or {}
    thumb = ((thumbs.get('maxres') or thumbs.get('720') or thumbs.get('high') or {}).get('url')) or ''
    item = xbmcgui.ListItem(title or meta.get('title') or video_id, offscreen=True)
    item.setPath(stream['url'])
    item.setMimeType('application/dash+xml')
    item.setContentLookup(False)
    if thumb:
        item.setArt({'thumb': thumb, 'fanart': thumb})
    subtitles = [path for path in (meta.get('subtitles') or []) if path]
    if subtitles:
        item.setSubtitles(subtitles)
    item.setProperty('inputstream', ISA)
    item.setProperty('%s.stream_selection_type' % ISA, 'manual-osd')
    headers = stream.get('headers')
    if isinstance(headers, dict) and headers:
        from urllib.parse import urlencode
        headers = urlencode(headers)
    if headers:
        item.setProperty('%s.manifest_headers' % ISA, headers)
        item.setProperty('%s.stream_headers' % ISA, headers)
    try:
        tag = item.getVideoInfoTag()
        tag.setTitle(title or meta.get('title') or '')
        tag.setMediaType('video')
    except Exception:
        pass
    return item


def playing():
    """True while the player plays a YouTube video (Dex Hub's or the add-on's)."""
    import xbmc
    path = xbmc.getInfoLabel('Player.FilenameAndPath') or ''
    return 'action=youtube_play' in path or 'youtube/manifest' in path


def _rpc(method, params):
    import xbmc
    answer = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
    return answer.get('result') or {}


def qualities():
    """[(index, label, detail, current)] of the playing video: Automatic first,
    then one row a quality (the add-on lists the better codec first), highest first."""
    try:
        props = _rpc('Player.GetProperties', {'playerid': 1, 'properties': ['videostreams', 'currentvideostream']})
    except Exception:
        return []
    current = props.get('currentvideostream') or {}
    streams = props.get('videostreams') or []

    def name(stream):
        return re.sub(r'\[/?B\]', '', str(stream.get('name') or '')).strip()

    auto = [s for s in streams if name(s).lower().startswith('automatic')]
    fixed = [s for s in streams if s not in auto and int(s.get('height') or 0)]
    on_auto = name(current).lower().startswith('automatic')
    rows = []
    if auto:
        top = max(auto, key=lambda s: int(s.get('height') or 0))
        playing = current if on_auto else top
        label = _t('تلقائي', 'Automatic')
        rows.append((playing.get('index', top['index']), label,
                     _t('أعلى جودة يتحملها اتصالك', 'The best your connection takes'), on_auto))
    chosen = {}
    for stream in sorted(fixed, key=lambda s: -int(s.get('height') or 0)):
        label = re.sub(r'\s*\(.*?\)', '', name(stream)) or '%dp' % int(stream['height'])
        if label not in chosen or (not on_auto and stream.get('index') == current.get('index')):
            chosen[label] = stream
    for label, stream in sorted(chosen.items(), key=lambda kv: -int(kv[1].get('height') or 0)):
        rows.append((stream['index'], label, str(stream.get('codec') or '').upper(),
                     not on_auto and stream.get('index') == current.get('index')))
    return rows


def set_quality_now(index):
    import xbmc
    xbmc.Player().setVideoStream(int(index))
