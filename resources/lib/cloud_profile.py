# -*- coding: utf-8 -*-
"""v5.10.371: the whole setup in the account (DexCloud).

Everything the backup holds goes to the account, so a TV linked later takes
it all at once: the sources and add-ons, the Home (rows, order, shapes, look),
collections, favourites, watching, the accounts of Plex, Emby, Jellyfin, Silo
and Stremio, every setting (language, player, interface), Kodi's languages
for audio and subtitles and its navigation sounds, and skin.dexhub's own
settings. The hub keeps it encrypted and never opens it.

What never travels (each TV keeps its own):
  * the identity of the TV on servers (Plex client id, Emby, Jellyfin and
    Silo device ids): two TVs with one id knock each other off
  * the Silo sign in (v5.10.451): each TV signs in with its own device code
  * Trakt and Nuvio sign ins: their refresh tokens are used once, two TVs
    with one token log each other out (Nuvio comes from the hub, Trakt is
    linked once on each TV)
  * Dex Hub's link to the hub (hub_link.json)

When it moves:
  * up: a few minutes after Kodi starts and then every six hours, only when
    something changed (not under a video), or on request
  * down: when the TV is linked (asked once), or on request

v5.10.415: syncing the TVs is item by item (cloud_sync.py): each setting
and file on its own, the last one changed wins, or one TV is the source;
what an update changes never goes up. Before (v5.10.375, below) the newest
whole setup replaced the others'.

v5.10.375, syncing the TVs (switched on from the account's page, with the
parts to take): a change here goes up once it settled (45 s after the last
one, and a look every five minutes for anything else), the hub tells the
profile's other TVs at once (the relay line, remote_setup) and they take the
chosen parts without asking (never under a video: right after it). A TV that
was off takes the newest when it starts and every half hour.
"""
import base64
import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET
import zlib

import xbmc
import xbmcgui
import xbmcvfs

PARTS = ('settings', 'sources', 'home', 'collections', 'library', 'progress', 'accounts')
SKIP_FILES = ('trakt_token.json', 'nuvio_token.json', 'plex_client_id.txt')
# v5.10.451: the Silo session too: one sign-in per device (Silo's maintainers
# asked for it; two TVs renewing one refresh token knock each other out)
SKIP_SETTINGS = ('trakt_token_keep', 'nuvio_token_keep', 'plex_client_identifier', 'silo_auth_json', 'silo_tg_hook')
KODI_SETTINGS = ('locale.audiolanguage', 'locale.subtitlelanguage', 'subtitles.languages', 'subtitles.tv',
                 'subtitles.movie', 'subtitles.charset', 'subtitles.style', 'subtitles.color', 'subtitles.fontsize',
                 'subtitles.bgcolor', 'subtitles.bgopacity', 'subtitles.align', 'subtitles.overridestyles',
                 'lookandfeel.soundskin', 'audiooutput.guisoundmode', 'videoplayer.preferdefaultflag')
PROGRESS_ROWS = 2000
PUSH_EVERY = 6 * 3600
_STATE = {'at': 0.0, 'dirty': 0.0, 'pulled': 0.0, 'pending': False}
# v5.10.375: device sync, the account page's parts and the backup parts they take
USER_PARTS = {'addons': ('sources',), 'home': ('home',), 'collections': ('collections',), 'library': ('library',),
              'progress': ('progress',), 'accounts': ('accounts',), 'settings': ('settings',)}
SETTLE = 45.0
PULL_EVERY = 30 * 60
import threading as _threading
_LOCK = _threading.Lock()


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _skip_setting(sid):
    return sid in SKIP_SETTINGS or sid.endswith('_device_id')


def _rpc(method, params):
    try:
        out = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
        return out.get('result')
    except Exception:
        return None


def _skin_id():
    try:
        from .skin_theme import DEXHUB_SKIN
    except Exception:
        DEXHUB_SKIN = 'skin.dexhub'
    return DEXHUB_SKIN if xbmc.getSkinDir() == DEXHUB_SKIN else ''


def _skin_settings(skin):
    out = {'strings': {}, 'bools': {}}
    try:
        root = ET.parse(xbmcvfs.translatePath('special://profile/addon_data/%s/settings.xml' % skin)).getroot()
    except Exception:
        return out
    for node in root.iter('setting'):
        sid = node.get('id') or ''
        if not sid:
            continue
        if node.get('type') == 'bool':
            out['bools'][sid] = (node.text or '').strip().lower() == 'true'
        else:
            out['strings'][sid] = node.text or ''
    return out


def collect():
    from . import backup
    data = backup.collect(PARTS)
    for body in (data.get('parts') or {}).values():
        settings = body.get('settings')
        if isinstance(settings, dict):
            for sid in [s for s in settings if _skip_setting(s)]:
                settings.pop(sid, None)
        files = body.get('files')
        if isinstance(files, dict):
            for name in SKIP_FILES:
                files.pop(name, None)
    progress = (data.get('parts') or {}).get('progress') or {}
    if isinstance(progress.get('playback'), list):
        progress['playback'] = progress['playback'][:PROGRESS_ROWS]
    kodi = {}
    for sid in KODI_SETTINGS:
        result = _rpc('Settings.GetSettingValue', {'setting': sid})
        if isinstance(result, dict) and 'value' in result:
            kodi[sid] = result['value']
    data['kodi'] = kodi
    skin = _skin_id()
    if skin:
        data['skin'] = dict(_skin_settings(skin), id=skin)
    return data


def signature(data):
    body = dict((k, v) for k, v in (data or {}).items() if k not in ('created', 'plugin_version'))
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def _pack(data):
    return base64.b64encode(zlib.compress(json.dumps(data, ensure_ascii=False).encode('utf-8'), 6)).decode('ascii')


def _unpack(z):
    return json.loads(zlib.decompress(base64.b64decode(z)).decode('utf-8'))


def push(force=False):
    """This TV's whole setup to the account (when it changed, or ``force``)."""
    from . import hub_client
    if not hub_client.linked() or not hub_client.active(network=force):
        return False
    try:
        # v5.10.378: settings Kodi cannot read are defaults: never sent to the account
        from .settings_version import kodi_reads
        from .profile_scope import ADDON_ID as _own_id
        if not kodi_reads(_own_id):
            xbmc.log('[DexHub] DexCloud: saved settings unreadable by Kodi on this TV, nothing sent', xbmc.LOGWARNING)
            return False
    except Exception:
        pass
    data = collect()
    sig = signature(data)
    mine = hub_client.state().get('profile') or {}
    if not force and mine.get('sig') == sig:
        return True
    try:
        answer = hub_client.request('PUT', '/api/device/profile', {'z': _pack(data), 'sig': sig,
                                                                   'label': hub_client.state().get('label') or ''}, timeout=60)
    except Exception as exc:
        xbmc.log('[DexHub] whole setup not sent: %s' % exc, xbmc.LOGINFO)
        return False
    hub_client.save(profile={'sig': sig, 'rev': answer.get('rev') or 0, 'at': time.time()})
    _remember(answer)
    xbmc.log('[DexHub] whole setup sent (rev %s%s)' % (answer.get('rev'), ', unchanged' if answer.get('unchanged') else ''), xbmc.LOGINFO)
    return True


def remote(rev=None):
    from . import hub_client
    try:
        answer = hub_client.request('GET', '/api/device/profile' + ('?rev=%d' % int(rev) if rev else ''), timeout=60)
        _remember(answer)
        return answer
    except Exception as exc:
        xbmc.log('[DexHub] whole setup not read: %s' % exc, xbmc.LOGINFO)
        return {}


def apply(answer, only=None):
    """The account's setup on this TV (``only``: these backup parts, device
    sync). Returns {part: count}."""
    from . import backup, hub_client
    data = _unpack(answer['z'])
    parts = [p for p in (data.get('parts') or {}) if p in PARTS and (only is None or p in only)]
    done = backup.restore(data, parts)
    kodi = (data.get('kodi') or {}) if only is None or 'settings' in only else {}
    if kodi.get('lookandfeel.soundskin') and kodi['lookandfeel.soundskin'] != 'resource.uisounds.kodi':
        pack = kodi['lookandfeel.soundskin']
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % pack):
            xbmc.executebuiltin('InstallAddon(%s)' % pack, True)
    count = 0
    for sid, value in kodi.items():
        if sid in KODI_SETTINGS and _rpc('Settings.SetSettingValue', {'setting': sid, 'value': value}) is not None:
            count += 1
    done['kodi'] = count
    skin = (data.get('skin') or {}) if only is None or 'home' in only else {}
    if skin.get('id') and skin['id'] == _skin_id():
        n = 0
        for sid, value in (skin.get('strings') or {}).items():
            xbmc.executebuiltin(('Skin.SetString(%s,%s)' % (sid, str(value).replace(',', '\\,'))) if value else ('Skin.Reset(%s)' % sid))
            n += 1
        for sid, value in (skin.get('bools') or {}).items():
            xbmc.executebuiltin(('Skin.SetBool(%s)' if value else 'Skin.Reset(%s)') % sid)
            n += 1
        done['skin'] = n
    hub_client.save(profile={'sig': signature(collect()), 'rev': answer.get('rev') or 0, 'at': time.time()})
    try:
        from . import cloud_look
        cloud_look.mark()
    except Exception:
        pass
    xbmc.log('[DexHub] whole setup applied (rev %s): %s' % (answer.get('rev'), done), xbmc.LOGINFO)
    return done


def _when(ms):
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(float(ms) / 1000.0))
    except Exception:
        return ''


def offer(first=False):
    """The account's setup, asked once (just linked) or on request. True when applied."""
    answer = remote()
    if not answer or not answer.get('z'):
        if not first:
            xbmcgui.Dialog().ok('DexCloud', _t('ما فيه إعداد محفوظ في حسابك للحين. ارفعه من التلفزيون اللي فيه كل شي.',
                                                    'Nothing is kept in your account yet. Upload it from the TV that has everything.'))
        return False
    if answer.get('mine') and first:
        return False
    text = _t('حسابك فيه إعداد كامل من «%s» (%s):\nالمصادر والإضافات، الرئيسية وشكلها، الكوليكشن، المفضلة، المتابعة، حسابات Plex وEmby وJellyfin وSilo وStremio، اللغة والمشغل وإعدادات الواجهة.\n\nتطبقه على هذا التلفزيون؟ (Trakt تربطه هنا مرة وحدة)',
              'Your account holds a whole setup from “%s” (%s):\nsources and add-ons, the Home and its look, collections, favourites, watching, Plex, Emby, Jellyfin, Silo and Stremio, the language, player and interface settings.\n\nApply it to this TV? (Trakt is linked once on each TV)') % (
        answer.get('from') or 'Kodi', _when(answer.get('updated_at')))
    if not xbmcgui.Dialog().yesno('DexCloud', text, nolabel=_t('لا', 'No'), yeslabel=_t('طبّقه', 'Apply')):
        return False
    progress = xbmcgui.DialogProgressBG()
    progress.create('DexCloud', _t('يطبق إعدادك…', 'Applying your setup…'))
    try:
        apply(answer)
    finally:
        progress.close()
    if xbmcgui.Dialog().yesno('DexCloud', _t('انطبق إعدادك ✓\nنعيد تشغيل كودي الحين عشان يقرأه كله؟',
                                                  'Your setup is applied ✓\nRestart Kodi now so it reads all of it?')):
        xbmc.executebuiltin('RestartApp')
    return True


# ------------------------------------------------------------------ device sync (v5.10.375)
def _remember(answer):
    """The account's device sync choice, as the hub said it."""
    if isinstance(answer, dict) and isinstance(answer.get('sync'), dict):
        from . import hub_client
        if (hub_client.state().get('devsync') or {}) != answer['sync']:
            hub_client.save(devsync=answer['sync'])


def devsync():
    from . import hub_client
    return hub_client.state().get('devsync') or {}


def mark():
    """Something of the setup changed here (a setting, the Home, the look)."""
    _STATE['dirty'] = time.time()


def on_notice(notice):
    """The hub's word through the relay: another TV changed the setup, the
    account page turned syncing on or asked to sync now."""
    kind = (notice or {}).get('kind')
    if isinstance(notice.get('sync'), dict):
        _remember({'sync': notice['sync']})
    if kind == 'profile' or kind == 'sync-now' or (kind == 'settings' and notice.get('pull')):
        if xbmc.Player().isPlayingVideo():
            _STATE['pending'] = True
        else:
            _round()


def _round(edited_at=0.0):
    """v5.10.415: one item-by-item round (cloud_sync), one at a time."""
    if not _LOCK.acquire(False):
        return False
    try:
        from . import cloud_sync
        return cloud_sync.sync(edited_at=edited_at)
    except Exception as exc:
        xbmc.log('[DexHub] device sync failed: %s' % exc, xbmc.LOGWARNING)
        return False
    finally:
        _LOCK.release()


def tick():
    """The service's hub thread: up a few minutes in, then every six hours
    (syncing the TVs: soon after a change, and the newest taken)."""
    now = time.time()
    if not _STATE['at']:
        _STATE['at'] = now - PUSH_EVERY + 5 * 60
        return
    from . import hub_client
    if not (hub_client.linked() and hub_client.option('profile')):
        return
    playing = xbmc.Player().isPlayingVideo()
    if devsync().get('on'):
        # v5.10.415: item by item (cloud_sync): soon after a change here, when
        # the hub says another TV changed something, and every half hour
        dirty = _STATE['dirty']
        if not playing and (_STATE['pending'] or (dirty and now - dirty > SETTLE)
                            or now - _STATE['pulled'] > PULL_EVERY):
            _STATE['pulled'], _STATE['pending'], _STATE['dirty'], _STATE['at'] = now, False, 0.0, now
            _round(edited_at=dirty)
        return
    if now - _STATE['at'] < PUSH_EVERY or playing:
        return
    _STATE['at'] = now
    push()
