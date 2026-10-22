# -*- coding: utf-8 -*-
"""v5.10.373: the host's own link in a watch party, as an option (DexCloud).

Off unless the host turns it on (the party's menu). With it on, the title the
host plays goes to the hub with the link it plays from:

  servers  only a link from the host's own servers: Plex, Emby, Jellyfin, Silo
  all      any direct link (an add-on's too)

The hub keeps the link in memory and never shows it: a guest's source search
asks the hub for it and gets a ticket of its own (hub/pv/<ticket>), shown as
the source «Dex Hub+» at the top of the list. The ticket sends the guest's
player on to the link while the party lives, the guest is in it and the host
still shares that title. Guests need no account or subscription of their own
for it.

What the guest's Kodi gets in the end is the link itself, so before it goes:
  * a token sent as a header is put in the address (the hub drops secret
    headers anyway)
  * Plex: the server's public address in place of a home one, and a transient
    token in place of the owner's (it dies when the server restarts)
  * a home address (192.168…, .local) is never shared: guests outside the
    home cannot reach it
"""
import ipaddress
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import xbmc
import xbmcgui

NAME = 'Dex Hub+'
PID = '__party__'
P_MODE = 'dhs.party.sharemode'          # host: '', 'servers', 'all'
P_SHARED = 'dhs.party.shared'           # guest: {"label", "quality", "v"} while the host shares
NATIVE = {'plex_native': 'plex', 'emby_native': 'emby', 'jellyfin_native': 'jellyfin', 'silo_native': 'silo',
          '__plex__': 'plex', '__emby__': 'emby', '__jellyfin__': 'jellyfin', '__silo__': 'silo'}
TOKEN_HEADERS = {'x-plex-token': 'X-Plex-Token', 'x-emby-token': 'api_key', 'x-mediabrowser-token': 'api_key'}
SECRET_HEADERS = ('authorization', 'cookie', 'x-emby-authorization', 'proxy-authorization')


def _home():
    return xbmcgui.Window(10000)


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def mode():
    value = _home().getProperty(P_MODE)
    return value if value in ('servers', 'all') else ''


def mode_name(value=None):
    value = mode() if value is None else value
    return {'servers': _t('من سيرفراتي', 'From my servers'), 'all': _t('كل الروابط المباشرة', 'Every direct link')}.get(
        value, _t('طافي', 'Off'))


# ------------------------------------------------------------------ host
def choose():
    """The host picks (the party's menu). True when it changed."""
    from .homeui.options import choose as _choose
    rows = [(_t('طافي', 'Off'), _t('كل واحد يشغّل من مصادره', 'Everyone plays from their own sources')),
            (_t('من سيرفراتي', 'From my servers'), _t('Plex وEmby وJellyfin وSilo: ضيوفك يشغّلون من رابطك',
                                                     'Plex, Emby, Jellyfin, Silo: your guests play from your link')),
            (_t('كل الروابط المباشرة', 'Every direct link'), _t('وروابط الإضافات بعد (روابط الديبريد ممكن تتقفل لو تشاركت)',
                                                                'add-on links too (a shared debrid link can get the account locked)'))]
    pick = _choose(_t('شارك تشغيلي مع الضيوف', 'Share my playback with guests'), rows,
                   subtitle=_t('الحين: %s', 'Now: %s') % mode_name())
    if pick < 0:
        return False
    value = ('', 'servers', 'all')[pick]
    if value == mode():
        return False
    if value and not xbmcgui.Dialog().yesno(_t('شارك تشغيلي', 'Share my playback'), _t(
            'ضيوفك يشغّلون من رابطك أنت، ويطلع لهم مصدر «Dex Hub+» أول القائمة.\n\n'
            'الرابط يمر مغلّف من Dex Hub ويموت لما تغيّر العمل أو تنتهي المشاهدة، لكن تلفزيون الضيف يوصل للرابط نفسه في النهاية: '
            'شاركه مع ناس تثق فيهم بس. وكل واحد يسحب من سيرفرك، فالرفع عندك يتضاعف.',
            'Your guests play from your own link, shown as the source «Dex Hub+» at the top of their list.\n\n'
            'The link goes wrapped through Dex Hub and dies when you change the title or the party ends, but in the end '
            'the guest\'s TV reaches the link itself: share it only with people you trust. Everyone pulls from your server, '
            'so your upload multiplies.'), nolabel=_t('لا', 'No'), yeslabel=_t('شارك', 'Share')):
        return False
    if value:
        _home().setProperty(P_MODE, value)
    else:
        _home().clearProperty(P_MODE)
    return True


def _kind(session, url):
    """'plex', 'emby', 'jellyfin', 'silo' when what plays is from the host's own server."""
    kind = NATIVE.get(str(session.get('provider_id') or '')) or NATIVE.get(str(session.get('source_provider_id') or ''))
    if not kind:
        return ''
    # the session is this link's (not an older one with something else playing now)
    played = urlsplit(str(session.get('stream_url') or '').split('|', 1)[0]).netloc.lower()
    return kind if played and played == urlsplit(url).netloc.lower() else ''


def _private(url):
    host = (urlsplit(url).hostname or '').lower()
    if not host or host == 'localhost' or host.endswith(('.local', '.lan', '.home', '.internal', '.localdomain')):
        return True
    m = re.match(r'^(\d{1,3}(?:-\d{1,3}){3})\.[0-9a-f]+\.plex\.direct$', host)
    if m:
        host = m.group(1).replace('-', '.')
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _with_query(url, add):
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in add]
    pairs.extend(add.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _headers(url, raw):
    """Kodi's |headers: a token goes in the address, other secrets go nowhere."""
    keep, add = {}, {}
    for key, value in parse_qsl(raw or '', keep_blank_values=True):
        low = key.strip().lower()
        if low in TOKEN_HEADERS:
            name = TOKEN_HEADERS[low]
            if name.lower() not in [k.lower() for k, _v in parse_qsl(urlsplit(url).query)]:
                add[name] = value
        elif low in SECRET_HEADERS:
            continue
        elif key.strip():
            keep[key.strip()] = value
    return (_with_query(url, add) if add else url), keep


def _plex_server(url):
    try:
        from . import plex_client
        servers = plex_client.servers(cached_only=True) or []
    except Exception:
        return None, None
    base = '%s://%s' % (urlsplit(url).scheme, urlsplit(url).netloc)
    for server in servers:
        for conn in server.get('connections') or []:
            if str(conn.get('uri') or '').rstrip('/').lower() == base.lower():
                return server, conn
    return None, None


def _plex(url):
    """The server's public address and a transient token."""
    server, conn = _plex_server(url)
    if server and conn and conn.get('local'):
        public = [c for c in server.get('connections') or [] if not c.get('local') and not c.get('relay')]
        public.sort(key=lambda c: not str(c.get('uri') or '').startswith('https://'))
        if public:
            new = urlsplit(public[0]['uri'])
            old = urlsplit(url)
            url = urlunsplit((new.scheme, new.netloc, old.path, old.query, old.fragment))
    token = dict(parse_qsl(urlsplit(url).query)).get('X-Plex-Token') or ''
    if token and not token.startswith('transient-') and not _private(url):
        try:
            import urllib.request
            parts = urlsplit(url)
            req = urllib.request.Request('%s://%s/security/token?type=delegation&scope=all' % (parts.scheme, parts.netloc),
                                         headers={'X-Plex-Token': token, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=6) as fh:
                transient = (json.loads(fh.read().decode('utf-8')).get('MediaContainer') or {}).get('token') or ''
            if transient:
                url = _with_query(url, {'X-Plex-Token': transient})
        except Exception as exc:
            xbmc.log('[DexHub] party: no Plex transient token (%s), the link keeps its own' % type(exc).__name__, xbmc.LOGINFO)
    return url


def host_share(player):
    """(share, why) for what the host plays now: share is None when nothing goes."""
    want = mode()
    if not want:
        return None, ''
    try:
        playing = player.getPlayingFile() or ''
    except Exception:
        playing = ''
    url, _sep, raw = playing.partition('|')
    if not url.lower().startswith(('http://', 'https://')):
        return None, _t('هذا التشغيل ما هو رابط ينشارك', 'This playback is not a link to share')
    try:
        from .session_store import load_session
        session = load_session() or {}
    except Exception:
        session = {}
    kind = _kind(session, url)
    if want == 'servers' and not kind:
        return None, _t('هذا المصدر مو من سيرفراتك، كل واحد يشغّل من مصادره', 'Not from your servers: each guest plays from their own sources')
    if kind == 'plex' and '/transcode/' in urlsplit(url).path:
        # a Plex transcode belongs to this TV's session: two players on it break both
        return None, _t('تشغيل Plex هنا محوّل (Transcode) وما ينشارك', 'Plex transcodes this playback: it cannot be shared')
    url, headers = _headers(url, raw)
    if kind == 'plex':
        url = _plex(url)
    if _private(url):
        return None, _t('رابطك على شبكة البيت، ربعك برا البيت ما يوصلونه', 'Your link is on your home network: guests outside cannot reach it')
    label = str(session.get('provider_name') or '').strip() if kind or session.get('stream_url') else ''
    quality = xbmc.getInfoLabel('VideoPlayer.VideoResolution') or ''
    return {'url': url, 'headers': headers, 'label': label[:80], 'quality': quality}, ''


# ------------------------------------------------------------------ guest
def _shared():
    try:
        return json.loads(_home().getProperty(P_SHARED) or '{}') or {}
    except ValueError:
        return {}


def target(ids, canonical_id, season=None, episode=None):
    """The «Dex Hub+» source for this title when this TV is a guest and the
    host shares it (None otherwise)."""
    home = _home()
    room = home.getProperty('dhs.party.room')
    if not room or home.getProperty('dhs.party.host') == '1':
        return None
    shared = _shared()
    if not shared:
        return None
    try:
        media = json.loads(home.getProperty('dhs.party.media') or '{}') or {}
    except ValueError:
        media = {}
    from .watch_party import same
    imdb = str((ids or {}).get('imdb_id') or '')
    if not imdb and str(canonical_id or '').startswith('tt'):
        imdb = str(canonical_id).split(':', 1)[0]
    here = {'imdb': imdb.split(':', 1)[0], 'tmdb': str((ids or {}).get('tmdb_id') or ''), 'canonical': str(canonical_id or ''),
            'season': int(season or 0), 'episode': int(episode or 0)}
    if not same(media, here):
        return None
    logo = ''
    try:
        import xbmcaddon
        logo = xbmcaddon.Addon().getAddonInfo('icon')
    except Exception:
        pass
    return ({'id': PID, 'name': NAME, '_room': room, '_shared': shared,
             'manifest': {'name': NAME, 'logo': logo}}, str(canonical_id or ''))


def _quality(text):
    text = str(text or '').lower()
    return {'2160': '4K', '4k': '4K', '1080': '1080p', '720': '720p', '480': '480p', '576': '576p'}.get(text, text)


def payload(provider):
    """The hub's ticket as one Stremio-like row (no row when the host stopped sharing)."""
    from . import hub_client
    try:
        answer = hub_client.request('GET', '/api/device/party/source?room=%s' % provider.get('_room'), timeout=8)
    except Exception as exc:
        xbmc.log('[DexHub] party: no shared link (%s)' % exc, xbmc.LOGINFO)
        return {'streams': []}
    host = ''
    try:
        host = [m.get('name') for m in json.loads(_home().getProperty('dhs.party.members') or '[]') if m.get('host')][0]
    except Exception:
        pass
    quality = _quality(answer.get('quality'))
    label = answer.get('label') or _t('رابط المضيف', "The host's link")
    row = {'name': '%s\n%s' % (NAME, quality) if quality else NAME,
           'title': '%s%s' % (label, (' · ' + host) if host else ''),
           'description': _t('تشغيل المضيف نفسه، تتابع معه', "The host's own playback, in step with it"),
           # the hub's address this TV already uses, the ticket's path on it
           'url': (hub_client.base().rstrip('/') + answer['path']) if answer.get('path') else answer['url'],
           'behaviorHints': {'bingeGroup': 'dexhub-party', 'notWebReady': False}}
    if answer.get('headers'):
        row['behaviorHints']['proxyHeaders'] = {'request': dict(answer['headers'])}
    xbmc.log('[DexHub] party: the host\'s link offered as %s (%s)' % (NAME, quality or '?'), xbmc.LOGINFO)
    return {'streams': [row]}


def is_party_row(entry):
    return str((entry or {}).get('provider_name_raw') or '') == NAME
