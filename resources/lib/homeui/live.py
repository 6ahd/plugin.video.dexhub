# -*- coding: utf-8 -*-
"""Live TV for the Dex Hub Home (v5.10.103).

Kodi's own PVR holds the channels. IPTV Simple (or any PVR client the user
has) reads the playlist and the programme guide; this page shows the PVR's
channel groups as rows in the Home's design, the programme on now and next in
the hero, and the channel itself playing behind the page once the cursor rests
on it (idea from the Nuvio Hub community build). OK watches it full screen.

A channel is opened through Kodi's PVR (JSON-RPC Player.Open with its channel
id), so the PVR client's own stream settings, timeshift and guide apply. Kodi
switches to full screen whenever a channel starts, following its setting
"Switch to full screen when starting playback of channels"; while a channel
preview starts that setting is held at "Never", and the user's own value is
put back as soon as the page is left (a marker file restores it after a
crash, see restore_marker).

Setting up: an Xtream Codes account or an M3U playlist (with an optional XMLTV
guide) is written as a dedicated IPTV Simple instance named "Dex Hub IPTV", so
other playlists the user keeps in IPTV Simple stay untouched.

Nothing here touches the Home's windows; the functions run on any thread.
"""
import json
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import xbmc
import xbmcgui
import xbmcvfs

IPTV_SIMPLE = 'pvr.iptvsimple'
INSTANCE_NAME = 'Dex Hub IPTV'
FIRST_INSTANCE = 90200
ROW_LIMIT = 60          # channels in a row before its "See all" card
PAGE_SIZE = 120         # channels per page of the "See all" grid
RECENT_LIMIT = 20

_CHANNEL_PROPS = ['icon', 'channelnumber', 'broadcastnow', 'broadcastnext', 'hidden', 'locked']
# Kodi 20 and later: an integer (0 = never); Kodi 19: a boolean
_FULLSCREEN_SETTINGS = (('pvrplayback.switchtofullscreenchanneltypes', 0),
                        ('pvrplayback.switchtofullscreen', False))
_MARKER = 'live_fullscreen_setting'
_RECENT = 'live_recent.json'
# Match complete formatting tags, not category names such as [BEIN SPORTS]
# or [IPTV]. Source formatting must not override the skin's contrast colours.
_TAGS = re.compile(r'\[(?:/?(?:B|I|UPPERCASE|LOWERCASE|CAPITALIZE|LIGHT)|/COLOR|COLOR\s+[^\]\r\n]+)\]', re.I)
_TIME = re.compile(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?')
_NO_GENRE = ('other / unknown', 'unknown', 'other')

_hold_lock = threading.Lock()
_held = {}              # setting id -> the user's value, while held
_icon_ok = [True]       # False once Kodi refused the 'icon' property (Kodi 19)


class LiveError(Exception):
    pass


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] live: %s' % msg, level)


def rpc(method, params=None):
    raw = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                                          'params': params or {}}))
    try:
        data = json.loads(raw) or {}
    except Exception:
        raise LiveError('%s: unreadable reply' % method)
    if data.get('error'):
        raise LiveError('%s: %s' % (method, (data.get('error') or {}).get('message') or 'failed'))
    return data.get('result')


def _quiet(method, params=None):
    try:
        return rpc(method, params)
    except Exception:
        return None


def _int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


# v5.10.199: letters a panel draws with Unicode look-alikes (𝐒𝐏𝐎𝐑𝐓𝐒, ꜱᴘᴏʀᴛꜱ,
# ＳＰＯＲＴＳ, 🅢🅟🅞🅡🅣) are in no skin's font: Kodi drew them as boxes or as
# nothing, and a category or a channel named so had no readable name. They
# become plain letters; box-drawing rules (━━) and characters past the
# emoji blocks (Kodi keeps 16 bits of a character) are dropped.
_FANCY = re.compile('[\u2100-\u214f\u2460-\u24ff\u2500-\u259f\uff01-\uff5e\u1d00-\u1d2b'
                    '\u0262\u0274\u0280\u0281\u028f\u0299\u029c\u029f\u026a\ua730-\ua7ff'
                    '\U00010000-\U0001efff\U0001f100-\U0001f1e5\U0001fb00-\U0010ffff]')
_SMALL_CAPS = dict(zip(map(ord, 'ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡʏᴢ'), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'))


def _plain_letter(ch):
    code = ord(ch)
    if code in _SMALL_CAPS:
        return _SMALL_CAPS[code]
    if 0x1f150 <= code <= 0x1f169 or 0x1f170 <= code <= 0x1f189:
        # negative circled and squared capitals (no compatibility form)
        return chr(ord('A') + (code - 0x1f150) % 0x20)
    if 0x2500 <= code <= 0x259f:
        return ' '
    plain = unicodedata.normalize('NFKC', ch)
    if plain != ch:
        return plain
    if code > 0xffff:
        return ''
    return ch


def readable(text):
    """Text with look-alike letters made plain (see _FANCY)."""
    if text.isascii() or not _FANCY.search(text):
        return text
    return ''.join(_plain_letter(ch) if _FANCY.match(ch) else ch for ch in text)


def clean(text):
    text = ' '.join(readable(_TAGS.sub('', str(text or ''))).split())
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        # v5.10.110: a lone surrogate (a name cut inside an emoji by its
        # server) can be neither saved nor shown: it becomes U+FFFD
        text = ''.join('�' if '\ud800' <= ch <= '\udfff' else ch for ch in text)
    return text


# ----------------------------------------------------------------- the PVR
def has_client():
    """Is any PVR client add-on enabled?"""
    try:
        return bool(xbmc.getCondVisibility('System.HasPVRAddon'))
    except Exception:
        return False


def simple_installed():
    try:
        return bool(xbmc.getCondVisibility('System.HasAddon(%s)' % IPTV_SIMPLE))
    except Exception:
        return False


def groups():
    """The TV channel groups in the PVR's order: [{'id', 'label', 'all'}].

    Raises LiveError while the PVR is not running.
    """
    result = rpc('PVR.GetChannelGroups', {'channeltype': 'tv'}) or {}
    details = _quiet('PVR.GetChannelGroupDetails', {'channelgroupid': 'alltv'}) or {}
    all_id = _int((details.get('channelgroupdetails') or {}).get('channelgroupid'), -1)
    out = []
    for group in result.get('channelgroups') or []:
        gid = _int(group.get('channelgroupid'), -1)
        if gid < 0:
            continue
        out.append({'id': gid, 'label': clean(group.get('label')), 'all': gid == all_id})
    return out


def _channels(group_id, start=0, end=0, props=None):
    """(channels, total) of a group, in channel number order."""
    props = list(props or _CHANNEL_PROPS)
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        props = [p for p in props if p not in ('broadcastnow', 'broadcastnext')]
    if not _icon_ok[0]:
        props = ['thumbnail' if p == 'icon' else p for p in props]
    params = {'channelgroupid': group_id, 'properties': props}
    if end:
        params['limits'] = {'start': int(start), 'end': int(end)}
    try:
        result = rpc('PVR.GetChannels', params) or {}
    except LiveError:
        if 'icon' not in props:
            raise
        # Kodi 19 has no 'icon' property: its channel logo is the thumbnail
        _icon_ok[0] = False
        params['properties'] = ['thumbnail' if p == 'icon' else p for p in props]
        result = rpc('PVR.GetChannels', params) or {}
    rows = [c for c in (result.get('channels') or []) if not c.get('hidden')]
    total = _int((result.get('limits') or {}).get('total'), len(rows))
    return rows, total


def _epoch(value):
    """Kodi's guide times are UTC, written without a zone."""
    match = _TIME.match(str(value or '').strip())
    if not match:
        return 0
    y, mo, d, h, mi, s = (int(x or 0) for x in match.groups())
    try:
        return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())
    except Exception:
        return 0


def clock(epoch):
    try:
        return time.strftime('%H:%M', time.localtime(int(epoch)))
    except Exception:
        return ''


def _programme(broadcast):
    if not isinstance(broadcast, dict):
        return {}
    title = clean(broadcast.get('title') or broadcast.get('label'))
    if not title:
        return {}
    genres = broadcast.get('genre') or []
    if isinstance(genres, str):
        genres = [genres]
    genres = [clean(g) for g in genres if clean(g) and clean(g).lower() not in _NO_GENRE]
    thumb = str(broadcast.get('thumbnail') or '')
    return {
        'title': title,
        'plot': clean(broadcast.get('plot') or broadcast.get('plotoutline')),
        'start': _epoch(broadcast.get('starttime')),
        'end': _epoch(broadcast.get('endtime')),
        'episode': clean(broadcast.get('episodename')),
        'genre': ' / '.join(genres[:2]),
        'thumb': '' if thumb.startswith('image://pvr') else thumb,
    }


def channel_key(channel_id):
    # the trailing slash keeps channel 4 from matching channel 45
    return 'pvr://channel/%d/' % int(channel_id)


def update_times(tile, now=None):
    """Progress and programme lines of a channel tile, from its now/next pair.

    Returns True when the programme on now is known. A programme that has
    ended gives way to the next one without asking the PVR again.
    """
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        tile.update(now={}, next={}, progress=0, total=0, resume=0, hint='', plot='')
        return False
    now = time.time() if now is None else now
    cur = tile.get('now') or {}
    nxt = tile.get('next') or {}
    if cur.get('end') and now >= cur['end'] and nxt.get('start') and nxt['start'] <= now:
        cur, nxt = nxt, {}
        tile['now'], tile['next'] = cur, nxt
    on = bool(cur.get('start') and cur.get('end') and cur['start'] <= now < cur['end'])
    if on:
        total = max(1, cur['end'] - cur['start'])
        done = now - cur['start']
        tile['progress'] = max(1, min(99, int(done * 100 / total)))
        tile['total'], tile['resume'] = total, done
        tile['hint'] = cur.get('title') or ''
    else:
        tile['progress'], tile['total'], tile['resume'] = 0, 0, 0
        tile['hint'] = ''
    tile['plot'] = (cur.get('plot') or '') if on else ''
    return on


def channel_tile(channel, group='', card=''):
    cid = _int(channel.get('channelid'))
    name = clean(channel.get('label') or channel.get('channel'))
    logo = str(channel.get('icon') or channel.get('thumbnail') or '')
    if logo.lower().split('?', 1)[0].endswith('.svg'):
        logo = ''       # Kodi cannot draw SVG
    number = _int(channel.get('channelnumber'))
    tile = {
        'kind': 'channel', 'shape': 'landscape', 'title': name, 'label': name,
        'channelid': cid, 'number': str(number) if number > 0 else '', 'group': group,
        'path': channel_key(cid), 'folder': False,
        'logo': logo, 'clearlogo': logo, 'poster': card, 'landscape': card, 'fanart': '',
        'now': _programme(channel.get('broadcastnow')),
        'next': _programme(channel.get('broadcastnext')),
        'locked': bool(channel.get('locked')),
    }
    update_times(tile)
    return tile


def group_tiles(group_id, label='', card='', start=0, count=ROW_LIMIT):
    """(tiles, more, total) of one group; ``more`` continues the list (the grid pages).

    Only one page is asked for, guide included: a playlist of thousands of
    channels costs no more than the channels on screen.
    """
    channels, total = _channels(group_id, start=start, end=start + count)
    tiles = [channel_tile(c, label, card) for c in channels]
    more = None
    if start + count < total:
        more = {'live_group': group_id, 'label': label, 'start': start + count}
    return tiles, more, total


def page_tiles(params, card=''):
    """A page of the "See all" grid (GridWindow params with 'live_group')."""
    tiles, more, _total = group_tiles(_int(params.get('live_group')), params.get('label') or '', card,
                                      start=_int(params.get('start')), count=PAGE_SIZE)
    return tiles, more


def _channel_details(channel_id):
    props = list(_CHANNEL_PROPS)
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        props = [p for p in props if p not in ('broadcastnow', 'broadcastnext')]
    if not _icon_ok[0]:
        props = ['thumbnail' if p == 'icon' else p for p in props]
    result = _quiet('PVR.GetChannelDetails', {'channelid': int(channel_id), 'properties': props})
    return (result or {}).get('channeldetails') or {}


def _recent_path(profile):
    return os.path.join(profile, _RECENT)


_recent_lock = threading.Lock()


def recent_entries(profile):
    """Every remembered channel, newest first: {'src', 'id'} (PVR) or {'src', 'tile'}.

    v5.10.104: each live source keeps its own; entries written before carry
    no 'src' and are PVR channels.
    """
    try:
        with open(_recent_path(profile), 'r', encoding='utf-8') as handle:
            data = json.load(handle) or []
    except Exception:
        return []
    out = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        entry['src'] = str(entry.get('src') or 'pvr')
        if entry['src'] == 'pvr' and not _int(entry.get('id')):
            continue
        if entry['src'] != 'pvr' and not isinstance(entry.get('tile'), dict):
            continue
        out.append(entry)
    return out


def recent_ids(profile):
    return [_int(e.get('id')) for e in recent_entries(profile) if e['src'] == 'pvr']


def _entry_key(entry):
    if entry.get('src') == 'pvr':
        return 'pvr:%d' % _int(entry.get('id'))
    return str((entry.get('tile') or {}).get('path') or '')


def remember_entry(profile, entry):
    """A channel the user watched (OK on it), newest first.

    Kodi's own "last played" date cannot say this: every preview plays the
    channel too, and it keeps the day only.
    """
    key = _entry_key(entry)
    if not key or key in ('pvr:0',):
        return
    with _recent_lock:
        entries = [entry] + [e for e in recent_entries(profile) if _entry_key(e) != key]
        kept, per_source = [], {}
        for e in entries:
            count = per_source.get(e['src'], 0)
            if count >= RECENT_LIMIT * 2:
                continue
            per_source[e['src']] = count + 1
            kept.append(e)
        _write_recent(profile, kept[:200])


def forget_sources(profile, sources):
    """Drop the remembered channels of sources that were removed (v5.10.110)."""
    sources = set(sources or ())
    with _recent_lock:
        entries = recent_entries(profile)
        kept = [e for e in entries if e.get('src') not in sources]
        if len(kept) != len(entries):
            _write_recent(profile, kept)


def _write_recent(profile, entries):
    try:
        tmp = _recent_path(profile) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(entries, handle)
        os.replace(tmp, _recent_path(profile))
        try:
            os.chmod(_recent_path(profile), 0o600)
        except Exception:
            pass
    except Exception:
        pass


def remember(profile, channel_id):
    """A PVR channel the user watched."""
    channel_id = _int(channel_id)
    if channel_id:
        remember_entry(profile, {'src': 'pvr', 'id': channel_id})


def recent_tiles(profile, card=''):
    """The channels watched last from the Live TV page, newest first."""
    tiles = []
    for cid in recent_ids(profile):
        details = _channel_details(cid)
        if details and not details.get('hidden'):
            tiles.append(channel_tile(details, '', card))
        if len(tiles) >= RECENT_LIMIT:
            break
    return tiles


def channel_count():
    try:
        _rows, total = _channels('alltv', end=1, props=['channelnumber'])
        return total
    except LiveError:
        return 0


def guide(channel_id, limit=40):
    """The programmes of a channel from now on."""
    from ..ui_preferences import enabled
    if not enabled('iptv_epg_enabled'):
        return []
    result = _quiet('PVR.GetBroadcasts', {
        'channelid': int(channel_id),
        'properties': ['title', 'plot', 'plotoutline', 'starttime', 'endtime', 'episodename', 'genre']}) or {}
    now = time.time()
    out = []
    for broadcast in result.get('broadcasts') or []:
        programme = _programme(broadcast)
        if not programme or (programme.get('end') and programme['end'] <= now):
            continue
        out.append(programme)
    out.sort(key=lambda p: p.get('start') or 0)
    return out[:limit]


# -------------------------------------------------------------- playback
def open_channel(channel_id):
    """Start a channel through Kodi's PVR (its stream settings and guide apply)."""
    try:
        rpc('Player.Open', {'item': {'channelid': int(channel_id)}})
        return True
    except Exception as exc:
        _log('channel %s did not open: %s' % (channel_id, exc), xbmc.LOGWARNING)
        return False


def playing_channel():
    """Kodi's id of the TV channel playing now, or 0."""
    item = ((_quiet('Player.GetItem', {'playerid': 1}) or {}).get('item')) or {}
    if item.get('type') != 'channel':
        return 0
    return _int(item.get('id'))


def _marker(profile):
    return os.path.join(profile, _MARKER)


def hold_windowed(profile):
    """Keep Kodi from going full screen when a channel preview starts."""
    with _hold_lock:
        if _held:
            return True
        for setting, never in _FULLSCREEN_SETTINGS:
            found = _quiet('Settings.GetSettingValue', {'setting': setting})
            if not isinstance(found, dict) or 'value' not in found:
                continue
            value = found['value']
            if value == never:
                _held[setting] = None       # already "never": nothing to put back
                return True
            try:
                with open(_marker(profile), 'w') as handle:
                    json.dump({'setting': setting, 'value': value}, handle)
            except Exception:
                pass
            if _quiet('Settings.SetSettingValue', {'setting': setting, 'value': never}) is None:
                try:
                    os.remove(_marker(profile))
                except Exception:
                    pass
                return False
            _held[setting] = value
            return True
    return False


def release_windowed(profile):
    """Give the user's full-screen setting back (no-op when nothing is held)."""
    if not _held:
        return
    with _hold_lock:
        for setting, value in list(_held.items()):
            if value is not None:
                _quiet('Settings.SetSettingValue', {'setting': setting, 'value': value})
        _held.clear()
        try:
            os.remove(_marker(profile))
        except Exception:
            pass


def restore_marker(profile_dir):
    """Service start: put back a full-screen setting held when Kodi stopped."""
    path = os.path.join(profile_dir, 'homeui', _MARKER)
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r') as handle:
            data = json.load(handle) or {}
    except Exception:
        data = {}
    setting, value = data.get('setting'), data.get('value')
    if setting in dict(_FULLSCREEN_SETTINGS) and value is not None:
        _quiet('Settings.SetSettingValue', {'setting': setting, 'value': value})
    try:
        os.remove(path)
    except Exception:
        pass


# ----------------------------------------------------------------- setup
def valid_url(value):
    value = str(value or '').strip()
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.netloc or any(c in value for c in '\r\n'):
        raise ValueError('url')
    return value


def xtream_urls(server, username, password):
    """The playlist and guide addresses of an Xtream Codes account."""
    server = valid_url(server).rstrip('/')
    parts = urlsplit(server)
    if parts.query or parts.fragment:
        raise ValueError('url')
    for suffix in ('/get.php', '/player_api.php', '/xmltv.php'):
        if server.lower().endswith(suffix):
            server = server[:-len(suffix)]
    if not username or not password:
        raise ValueError('account')
    query = urlencode({'username': username, 'password': password})
    return (server + '/get.php?' + query + '&type=m3u_plus&output=ts',
            server + '/xmltv.php?' + query)


def _simple_profile():
    return xbmcvfs.translatePath('special://profile/addon_data/%s/' % IPTV_SIMPLE)


def _kodi_major():
    try:
        return int(str(xbmc.getInfoLabel('System.BuildVersion') or '0').split('.', 1)[0])
    except Exception:
        return 0


def write_instance(m3u, epg=''):
    """Write (or update) the "Dex Hub IPTV" instance of IPTV Simple; its id."""
    m3u = valid_url(m3u)
    epg = valid_url(epg) if epg else ''
    folder = _simple_profile()
    os.makedirs(folder, exist_ok=True)
    values = [('kodi_addon_instance_name', INSTANCE_NAME), ('kodi_addon_instance_enabled', 'true'),
              ('m3uPathType', '1'), ('m3uUrl', m3u), ('m3uCache', 'true'),
              ('epgPathType', '1'), ('epgUrl', epg), ('epgCache', 'true'),
              ('m3uRefreshMode', '2'), ('m3uRefreshHour', '4'), ('numberByOrder', 'false')]
    iid = FIRST_INSTANCE
    while True:
        path = os.path.join(folder, 'instance-settings-%d.xml' % iid)
        if not os.path.exists(path):
            break
        try:
            node = ET.parse(path).getroot().find("setting[@id='kodi_addon_instance_name']")
            if node is not None and (node.text or '') == INSTANCE_NAME:
                break
        except Exception:
            pass
        iid += 1
    root = ET.Element('settings', version='2')
    for key, value in values:
        ET.SubElement(root, 'setting', id=key).text = value
    if os.path.exists(path):
        backup = os.path.join(folder, 'dexhub-backups')
        os.makedirs(backup, exist_ok=True)
        try:
            with open(path, 'rb') as src, open(os.path.join(backup, '%d.xml' % time.time()), 'wb') as dst:
                dst.write(src.read())
        except Exception:
            pass
    tmp = path + '.tmp'
    ET.ElementTree(root).write(tmp, encoding='utf-8', xml_declaration=True)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return iid


def ensure_simple():
    """IPTV Simple installed and enabled (Kodi asks before installing it)."""
    if not simple_installed():
        xbmc.executebuiltin('InstallAddon(%s)' % IPTV_SIMPLE, True)
        deadline = time.monotonic() + 20.0
        while not simple_installed() and time.monotonic() < deadline:
            if xbmc.Monitor().waitForAbort(0.5):
                return False
        if not simple_installed():
            return False
    _quiet('Addons.SetAddonEnabled', {'addonid': IPTV_SIMPLE, 'enabled': True})
    return True


def configure(tr, kind):
    """Ask for an Xtream account ('xtream') or an M3U playlist ('m3u') and save it.

    Runs on the GUI thread of the page that asked (Kodi's own input dialogs).
    True when IPTV Simple was set up and restarted.
    """
    dialog = xbmcgui.Dialog()
    if 0 < _kodi_major() < 20:
        # Kodi 19's IPTV Simple keeps one playlist in its own settings, which
        # a new one would overwrite: it is set up there
        dialog.ok('Dex Hub', tr('في هذا الإصدار من Kodi تُضاف القائمة من إعدادات IPTV Simple.'))
        if ensure_simple():
            xbmc.executebuiltin('Addon.OpenSettings(%s)' % IPTV_SIMPLE, True)
            return True
        return False
    # v5.10.110: account data: the skin asks no keyboard suggestions for it
    from .. import kb_private
    try:
        if kind == 'xtream':
            server = (kb_private.dialog_input(tr('عنوان السيرفر مع المنفذ، مثل http://example.com:8080')) or '').strip()
            if not server:
                return False
            if '://' not in server:
                server = 'http://' + server
            username = (kb_private.dialog_input(tr('اسم المستخدم')) or '').strip()
            if not username:
                return False
            password = kb_private.dialog_input(tr('كلمة المرور'), option=xbmcgui.ALPHANUM_HIDE_INPUT) or ''
            if not password:
                return False
            m3u, epg = xtream_urls(server, username, password)
        else:
            m3u = (kb_private.dialog_input(tr('رابط قائمة M3U')) or '').strip()
            if not m3u:
                return False
            m3u = valid_url(m3u)
            epg = (kb_private.dialog_input(tr('رابط دليل البرامج XMLTV (اختياري)')) or '').strip()
            if epg:
                epg = valid_url(epg)
    except ValueError as exc:
        text = tr('اكتب اسم المستخدم وكلمة المرور') if str(exc) == 'account' else \
            tr('اكتب رابطاً كاملاً يبدأ بـ http:// أو https://')
        dialog.ok('Dex Hub', text)
        return False
    if xbmc.getCondVisibility('PVR.IsPlayingTV'):
        xbmc.Player().stop()
    if not ensure_simple():
        dialog.ok('Dex Hub', tr('تعذر تثبيت IPTV Simple. ثبّته من مستودع Kodi ثم حاول مرة أخرى.'))
        return False
    # the client reads its instances when it starts: restart it around the write
    _quiet('Addons.SetAddonEnabled', {'addonid': IPTV_SIMPLE, 'enabled': False})
    try:
        write_instance(m3u, epg)
    except Exception as exc:
        _log('IPTV Simple settings not written: %s' % exc, xbmc.LOGWARNING)
        dialog.ok('Dex Hub', tr('تعذر حفظ إعدادات القنوات'))
        return False
    finally:
        _quiet('Addons.SetAddonEnabled', {'addonid': IPTV_SIMPLE, 'enabled': True})
    _log('IPTV Simple instance "%s" saved (%s)' % (INSTANCE_NAME, 'Xtream' if kind == 'xtream' else 'M3U'))
    return True
