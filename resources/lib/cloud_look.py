# -*- coding: utf-8 -*-
"""v5.10.366: the look and order in the cloud (DexCloud).

Only the look travels: the Home's layout, theme and interface choices, the
player bar's look, the order and shapes of the rows (homeui/layout.json) and
skin.dexhub's own look strings (its accent, glow, AMOLED, tab names). No
account, key, source, favourite or progress goes up: that is the backup's.

When it moves:
  * once when Kodi starts (the service, a few seconds in): the account's look
    is asked with the revision this TV has; an unchanged one costs one line
    (``unchanged``), a newer one is applied
  * after a change made on this TV: the change is marked (one Home property)
    and the service sends it half a minute later, once the choosing is over
Nothing is polled.
"""
import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .profile_scope import ADDON_ID

VERSION = 1
DIRTY = 'dexhub.look.dirty'
SETTLE = 30.0
PREFIXES = ('homeui_', 'player_osd_', 'player_badges_', 'theme_')
EXTRA = ('ui_language', 'player_title_actions', 'sources_row_style', 'show_servers', 'library_split_media',
         'favorites_layout', 'continue_art_style', 'search_style', 'badges_home', 'metadata_badges',
         'home_hide_new_rows', 'home_group_recs', 'continue_episode_art', 'episode_titles', 'season_names')
# this TV's own: its screen, its speed, its paths, Dex Hub's bookkeeping
EXCLUDE = frozenset(('homeui_debug', 'homeui_autostart', 'homeui_size', 'homeui_light_mode', 'homeui_ss_preload',
                     'player_badges_folder', 'player_badges_json_url', 'player_badges_colorful_adopted',
                     'ui_lang_autodetected'))
FILES = ('homeui/layout.json',)
SKIN_PREFIXES = ('dh.', 'HomeSwitcher.')


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _ids():
    ids = set()
    try:
        path = os.path.join(_addon().getAddonInfo('path'), 'resources', 'settings.xml')
        for node in ET.parse(path).getroot().iter('setting'):
            sid = node.get('id')
            if sid and node.get('type') not in ('action', 'sep', 'lsep'):
                ids.add(sid)
    except Exception:
        pass
    try:
        from .ui_preferences import OPTIONS
        ids.update(OPTIONS)
    except Exception:
        pass
    from .backup import _secret
    return sorted(i for i in ids if (i.startswith(PREFIXES) or i in EXTRA) and i not in EXCLUDE and not _secret(i))


def _skin_family():
    try:
        from .skin_theme import DEXHUB_SKIN
    except Exception:
        DEXHUB_SKIN = 'skin.dexhub'
    return DEXHUB_SKIN if xbmc.getSkinDir() == DEXHUB_SKIN else ''


def _skin_settings(skin):
    """skin.dexhub's own look (its settings file): {'strings': {}, 'bools': {}}."""
    out = {'strings': {}, 'bools': {}}
    path = xbmcvfs.translatePath('special://profile/addon_data/%s/settings.xml' % skin)
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return out
    for node in root.iter('setting'):
        sid = node.get('id') or ''
        if not sid.startswith(SKIN_PREFIXES):
            continue
        if node.get('type') == 'bool':
            out['bools'][sid] = (node.text or '').strip().lower() == 'true'
        else:
            out['strings'][sid] = node.text or ''
    return out


def collect():
    addon = _addon()
    look = {'v': VERSION, 'settings': dict((sid, addon.getSetting(sid)) for sid in _ids()), 'files': {}}
    from . import profile_scope
    for name in FILES:
        try:
            with open(profile_scope.path(name), encoding='utf-8') as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.pop('known', None)     # rows this TV has seen: its own
            look['files'][name] = data
        except Exception:
            continue
    skin = _skin_family()
    if skin:
        look['skin'] = dict(_skin_settings(skin), id=skin)
    return look


def signature(look):
    body = dict((k, v) for k, v in (look or {}).items() if k not in ('from', 'at'))
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _usable():
    from . import hub_client
    if not (hub_client.linked() and hub_client.option('look')):
        return False
    try:
        # v5.10.415: with the TVs syncing, the look travels with them (item by
        # item, cloud_sync): this older whole-look copy stands down
        from . import cloud_profile
        if cloud_profile.devsync().get('on'):
            return False
    except Exception:
        pass
    return True


def push(force=False):
    """This TV's look goes up when it changed (or ``force``). True when the
    account holds it now."""
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
    look = collect()
    sig = signature(look)
    mine = hub_client.state().get('look') or {}
    if not force and mine.get('sig') == sig:
        return True
    look['from'] = hub_client.state().get('label') or 'Kodi'
    look['at'] = int(time.time())
    try:
        answer = hub_client.request('PUT', '/api/device/look', {'look': look}, timeout=12)
    except Exception as exc:
        xbmc.log('[DexHub] cloud look not sent: %s' % exc, xbmc.LOGINFO)
        return False
    hub_client.save(look={'sig': sig, 'rev': answer.get('rev') or 0})
    xbmc.log('[DexHub] cloud look sent (rev %s)' % answer.get('rev'), xbmc.LOGINFO)
    return True


def remote(rev=None):
    """The account's look: {rev, look, updated_at} or {unchanged, rev}."""
    from . import hub_client
    path = '/api/device/look' + ('?rev=%d' % int(rev) if rev is not None else '')
    try:
        return hub_client.request('GET', path, timeout=8)
    except Exception as exc:
        xbmc.log('[DexHub] cloud look not read: %s' % exc, xbmc.LOGINFO)
        return {}


def apply(cloud):
    """The account's look on this TV. Returns how many things changed."""
    from . import hub_client
    look = (cloud or {}).get('look') or {}
    if not isinstance(look, dict) or not look.get('v'):
        return 0
    changed = 0
    addon = _addon()
    allowed = set(_ids())
    for sid, value in (look.get('settings') or {}).items():
        if sid not in allowed or value is None:
            continue
        value = str(value)
        try:
            if addon.getSetting(sid) != value:
                addon.setSetting(sid, value)
                changed += 1
        except Exception:
            continue
    from . import profile_scope
    for name, data in (look.get('files') or {}).items():
        if name not in FILES or not isinstance(data, dict):
            continue
        path = profile_scope.path(name)
        try:
            with open(path, encoding='utf-8') as fh:
                old = json.load(fh) or {}
        except Exception:
            old = {}
        new = dict(data)
        if isinstance(old, dict) and old.get('known'):
            new['known'] = old['known']
        if new == old:
            continue
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.cloud'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(new, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            changed += 1
        except Exception as exc:
            xbmc.log('[DexHub] cloud look %s: %s' % (name, exc), xbmc.LOGWARNING)
    skin = look.get('skin') or {}
    if skin and skin.get('id') and skin.get('id') == _skin_family():
        have = _skin_settings(skin['id'])
        for sid, value in (skin.get('strings') or {}).items():
            if sid.startswith(SKIN_PREFIXES) and have['strings'].get(sid, '') != (value or ''):
                if value:
                    xbmc.executebuiltin('Skin.SetString(%s,%s)' % (sid, str(value).replace(',', '\\,')))
                else:
                    xbmc.executebuiltin('Skin.Reset(%s)' % sid)
                changed += 1
        for sid, value in (skin.get('bools') or {}).items():
            if sid.startswith(SKIN_PREFIXES) and bool(have['bools'].get(sid, False)) != bool(value):
                xbmc.executebuiltin(('Skin.SetBool(%s)' if value else 'Skin.Reset(%s)') % sid)
                changed += 1
    if changed:
        _after_apply()
    hub_client.save(look={'sig': signature(collect()), 'rev': (cloud or {}).get('rev') or 0})
    xbmc.log('[DexHub] cloud look applied (rev %s, %d changes)' % ((cloud or {}).get('rev'), changed), xbmc.LOGINFO)
    return changed


def _after_apply():
    try:
        from . import settings_cache
        settings_cache.invalidate()
    except Exception:
        pass
    try:
        from . import ui_preferences
        ui_preferences.publish()
    except Exception:
        pass
    try:
        from . import home_preferences
        home_preferences.publish()
    except Exception:
        pass
    try:
        from . import skin_theme
        skin_theme.publish_theme()
    except Exception:
        pass
    try:
        from .skinui import common
        common.ask_republish('cloud look')
    except Exception:
        pass
    try:
        xbmcgui.Window(10000).clearProperty(DIRTY)
    except Exception:
        pass


# ------------------------------------------------------------------ marks
def mark():
    """Something of the look changed on this TV: sent once the choosing is over."""
    try:
        from . import cloud_profile          # v5.10.375: and the whole setup, when the TVs sync
        cloud_profile.mark()
    except Exception:
        pass
    try:
        if _usable():
            xbmcgui.Window(10000).setProperty(DIRTY, '%.1f' % time.time())
    except Exception:
        pass


def settings_changed(keys):
    """The service's onSettingsChanged: a look setting among ``keys``."""
    try:
        if keys:
            from . import cloud_profile      # v5.10.375: any setting, when the TVs sync
            cloud_profile.mark()
        if keys and set(keys).intersection(_ids()):
            mark()
    except Exception:
        pass


def at_start():
    """Kodi started (the service's hub thread, once)."""
    from . import hub_client
    if not _usable() or not hub_client.active(network=True):
        return
    mine = hub_client.state().get('look') or {}
    cloud = remote(mine.get('rev') if mine.get('rev') else None)
    if not cloud:
        return
    if cloud.get('unchanged'):
        if signature(collect()) != mine.get('sig'):
            push()
        return
    if cloud.get('look') and int(cloud.get('rev') or 0) > int(mine.get('rev') or 0):
        apply(cloud)
        return
    push(force=not cloud.get('rev'))


def tick():
    """The service's hub thread, every few seconds: a marked change is sent
    after it settled (not under a playing video)."""
    try:
        home = xbmcgui.Window(10000)
        mark_at = float(home.getProperty(DIRTY) or 0)
    except (ValueError, RuntimeError):
        return
    if not mark_at or time.time() - mark_at < SETTLE or xbmc.Player().isPlayingVideo():
        return
    home.clearProperty(DIRTY)
    if _usable():
        push()
