# -*- coding: utf-8 -*-
"""v5.10.415: syncing the TVs item by item (DexCloud device sync).

Before, a TV sent its whole setup as one block and the newest block replaced
everything on the other TVs, the parts that TV never touched too; and a TV
sent whenever its copy differed, so what an update changed by itself (a
migrated setting, rows it had just seen) went out and undid the layout on
the others.

Now every setting, file, Kodi setting and skin setting is an item of its own
(the "units"), and the account page picks how the TVs agree:

  latest  each item: the last one changed, on any TV, wins. A TV sends only
          the items it changed since it last synced, merged into the
          account's copy, and takes the items the others changed.
  source  one TV is the source: only it sends; the others take what it has
          and send nothing (the hub refuses them too).

Never sent, whatever the mode:
  * what an add-on or skin update changes by itself: the first sync after a
    new version takes the account's copy and counts what this TV has as
    synced, without sending it
  * the add-on's own bookkeeping (INTERNAL: flags, device ids, caches) and
    what belongs to this screen (DEVICE: its size, its light mode...)
  * the rows a TV has seen ('known' in the Home's layout)

The state of the last sync is kept in cloud_sync.json: the version it ran
under, the account's revision, a hash per item and when each was changed.
"""
import base64
import hashlib
import json
import os
import time

import xbmc
import xbmcaddon
import xbmcgui

STATE_FILE = 'cloud_sync.json'
INTERNAL = frozenset(('welcome_seen', 'setup_done', 'homeui_nav_focus_migrated', 'player_badges_colorful_adopted',
                      'player_loading_art_migrated', 'dexhub_defaults_rev', 'jellyfin_device_id', 'emby_device_id',
                      'silo_device_id', 'last_account_sync_at', 'plex_servers_cache_json', 'ui_lang_autodetected',
                      'nuvio_token_keep', 'stremio_token_keep', 'trakt_token_keep', 'plex_client_identifier'))
DEVICE = frozenset(('homeui_debug', 'homeui_autostart', 'homeui_size', 'homeui_light_mode', 'homeui_ss_preload',
                    'player_badges_folder', 'player_badges_json_url'))
LAYOUT = 'homeui/layout.json'


def _addon():
    return xbmcaddon.Addon('plugin.video.dexhub')


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _log(text):
    xbmc.log('[DexHub] device sync: %s' % text, xbmc.LOGINFO)


def _path():
    from . import profile_scope
    return profile_scope.path(STATE_FILE)


def load_state():
    try:
        with open(_path(), encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state):
    path = _path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(state, fh, separators=(',', ':'))
        os.replace(tmp, path)
    except Exception as exc:
        xbmc.log('[DexHub] device sync state not kept: %s' % exc, xbmc.LOGWARNING)


def version():
    """The add-on's and the skin's versions: a change means an update ran."""
    try:
        skin = xbmc.getSkinDir()
        skin_ver = xbmcaddon.Addon(skin).getAddonInfo('version') if skin else ''
    except Exception:
        skin, skin_ver = '', ''
    return '%s|%s|%s' % (_addon().getAddonInfo('version'), skin, skin_ver)


# ------------------------------------------------------------------ units
def _layout_value(b64):
    """The Home's layout without the rows this TV has seen (its own)."""
    try:
        data = json.loads(base64.b64decode(b64).decode('utf-8'))
        if isinstance(data, dict):
            data.pop('known', None)
            return data
    except Exception:
        pass
    return b64


def units(data):
    """{key: value} of a setup (cloud_profile.collect's body)."""
    out = {}
    for part, body in (data.get('parts') or {}).items():
        for sid, value in (body.get('settings') or {}).items():
            if sid in INTERNAL or sid in DEVICE:
                continue
            out['%s:s:%s' % (part, sid)] = value
        for name, b64 in (body.get('files') or {}).items():
            out['%s:f:%s' % (part, name)] = _layout_value(b64) if name == LAYOUT else b64
        if 'favorites' in body:
            out['%s:favorites' % part] = body.get('favorites') or []
        if 'playback' in body:
            out['%s:playback' % part] = body.get('playback') or []
    for sid, value in (data.get('kodi') or {}).items():
        out['kodi:%s' % sid] = value
    skin = data.get('skin') or {}
    for sid, value in (skin.get('strings') or {}).items():
        out['skin:s:%s' % sid] = value
    for sid, value in (skin.get('bools') or {}).items():
        out['skin:b:%s' % sid] = bool(value)
    return out


def digest(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()[:20]


def part_of(key):
    """The account page's part an item belongs to."""
    head = key.split(':', 1)[0]
    return {'sources': 'addons', 'kodi': 'settings', 'skin': 'home'}.get(head, head)


def _put(data, key, value):
    """Write one item into a setup body (the merged copy that goes up)."""
    head, _sep, rest = key.partition(':')
    if head == 'kodi':
        data.setdefault('kodi', {})[rest] = value
        return
    if head == 'skin':
        kind, _sep, sid = rest.partition(':')
        skin = data.setdefault('skin', {})
        skin.setdefault('strings' if kind == 's' else 'bools', {})[sid] = value
        return
    body = data.setdefault('parts', {}).setdefault(head, {})
    kind, _sep, name = rest.partition(':')
    if rest in ('favorites', 'playback'):
        body[rest] = value
    elif kind == 's':
        body.setdefault('settings', {})[name] = value
    elif kind == 'f':
        if name == LAYOUT and isinstance(value, dict):
            value = base64.b64encode(json.dumps(value, ensure_ascii=False).encode('utf-8')).decode('ascii')
        body.setdefault('files', {})[name] = value


def apply_units(items):
    """Put the account's items on this TV: {key: value}. Returns how many."""
    from . import backup, cloud_profile
    addon = _addon()
    count = 0
    for key, value in items.items():
        head, _sep, rest = key.partition(':')
        try:
            if head == 'kodi':
                if cloud_profile._rpc('Settings.SetSettingValue', {'setting': rest, 'value': value}) is not None:
                    count += 1
            elif head == 'skin':
                kind, _sep, sid = rest.partition(':')
                if kind == 's':
                    xbmc.executebuiltin(('Skin.SetString(%s,%s)' % (sid, str(value).replace(',', '\\,'))) if value
                                        else ('Skin.Reset(%s)' % sid))
                else:
                    xbmc.executebuiltin(('Skin.SetBool(%s)' if value else 'Skin.Reset(%s)') % sid)
                count += 1
            elif rest == 'favorites' or rest == 'playback':
                backup.restore({'parts': {head: {rest: value}}}, [head])
                count += 1
            else:
                kind, _sep, name = rest.partition(':')
                if kind == 's':
                    addon.setSetting(name, '' if value is None else str(value))
                    count += 1
                elif kind == 'f' and name in backup.FILES.get(head, ()):
                    if name == LAYOUT and isinstance(value, dict):
                        value = dict(value)
                        known = _local_known()
                        if known is not None:
                            value['known'] = known      # the rows this TV has seen stay
                        value = base64.b64encode(json.dumps(value, ensure_ascii=False, indent=1).encode('utf-8')).decode('ascii')
                    backup._write_file(name, value)
                    count += 1
        except Exception as exc:
            xbmc.log('[DexHub] device sync: %s not applied: %s' % (key, exc), xbmc.LOGWARNING)
    if count:
        try:
            from . import settings_cache
            settings_cache.invalidate()
        except Exception:
            pass
    return count


def _local_known():
    from . import profile_scope
    try:
        with open(profile_scope.path(LAYOUT), encoding='utf-8') as fh:
            data = json.load(fh)
        return data.get('known') if isinstance(data, dict) else None
    except Exception:
        return None


# ------------------------------------------------------------------ sync
def sync(notify=True, edited_at=0.0):
    """One round: take what the others changed, send what this TV changed
    (``edited_at``: when this TV's change was made, if known). True when
    something changed here."""
    from . import hub_client, cloud_profile
    devsync = cloud_profile.devsync()
    if not devsync.get('on') or not hub_client.linked():
        return False
    chosen = set(devsync.get('parts') or [])
    mode = devsync.get('mode') or 'latest'
    me = hub_client.state().get('device_id') or ''
    source = devsync.get('source') or ''
    if mode == 'source' and not source:
        return False                    # no source TV picked yet: nothing moves
    sender = mode == 'latest' or source == me
    taker = mode == 'latest' or source != me

    state = load_state()
    fresh = state.get('ver') != version()      # an update ran since the last sync
    base = state.get('items') or {}
    edits = state.get('edits') or {}
    now = time.time()

    local_data = cloud_profile.collect()
    local = dict((k, v) for k, v in units(local_data).items() if part_of(k) in chosen)
    local_hash = dict((k, digest(v)) for k, v in local.items())

    answer = cloud_profile.remote(state.get('rev') or None)
    if not answer:
        return False
    cloud_profile._remember(answer)
    if answer.get('z'):
        cloud_data = cloud_profile._unpack(answer['z'])
        cloud_rev = answer.get('rev') or 0
    elif answer.get('unchanged'):
        cloud_data, cloud_rev = None, answer.get('rev') or 0
    else:
        cloud_data, cloud_rev = {}, 0                       # nothing in the account yet
    if cloud_data is not None:
        cloud = dict((k, v) for k, v in units(cloud_data).items() if part_of(k) in chosen)
        cloud_hash = dict((k, digest(v)) for k, v in cloud.items())
        cloud_when = dict((cloud_data.get('units') or {}))
    else:
        # the account has not moved since this TV last synced: it holds this
        # TV's synced copy, the base
        cloud, cloud_hash, cloud_when = None, dict(base), {}

    take, send = {}, []
    if not base or fresh:
        # first sync (or after an update): the account's copy wins, what this
        # TV has is counted as synced, nothing of it is sent
        if cloud and taker:
            take = dict((k, v) for k, v in cloud.items() if local_hash.get(k) != cloud_hash[k])
        elif not cloud and sender and not cloud_rev:
            send = list(local)                              # the account is empty: this TV starts it
    else:
        for key in set(local_hash) | set(cloud_hash):
            mine_changed = key in local_hash and local_hash[key] != base.get(key)
            theirs_changed = key in cloud_hash and cloud_hash[key] != base.get(key)
            if mine_changed and key not in edits:
                edits[key] = edited_at or now
            if not mine_changed:
                edits.pop(key, None)
            if theirs_changed and taker and (not mine_changed or not sender
                                             or float(cloud_when.get(key) or 0) > float(edits.get(key) or 0)):
                if cloud is not None and key in cloud and local_hash.get(key) != cloud_hash[key]:
                    take[key] = cloud[key]
                edits.pop(key, None)
            elif mine_changed and sender:
                send.append(key)

    applied = apply_units(take) if take else 0

    sent = False
    if send:
        if cloud_data is None:
            # the account has not moved: its whole copy, to merge into (the
            # parts not synced must stay as they are there)
            full = cloud_profile.remote(None) or {}
            cloud_data = cloud_profile._unpack(full['z']) if full.get('z') else {}
        merged = cloud_data if cloud_data else local_data   # an empty account: this TV starts it
        merged = json.loads(json.dumps(merged))             # a copy
        when = dict((merged.get('units') or {}))
        for key in send:
            _put(merged, key, local[key])
            when[key] = edits.get(key) or now
        merged['units'] = when
        try:
            reply = hub_client.request('PUT', '/api/device/profile',
                                       {'z': cloud_profile._pack(merged), 'sig': cloud_profile.signature(merged),
                                        'label': hub_client.state().get('label') or ''}, timeout=60)
            cloud_profile._remember(reply)
            if reply.get('refused'):
                _log('not sent: "%s" is the source' % (devsync.get('source') or ''))
            else:
                cloud_rev = reply.get('rev') or cloud_rev
                sent = True
                for key in send:
                    edits.pop(key, None)
        except Exception as exc:
            _log('not sent: %s' % exc)

    # the new synced copy: what each item is now on this TV
    synced = dict(local_hash)
    for key, value in take.items():
        synced[key] = digest(value)
    if send and not sent:
        # not sent: these stay changes of this TV for the next round
        for key in send:
            synced[key] = base.get(key, '')
    state.update(ver=version(), rev=cloud_rev, items=synced, edits=edits, at=int(now))
    save_state(state)
    if applied:
        _after_apply()
        if notify:
            xbmcgui.Dialog().notification('Dex Hub', _t('تحدّث من أجهزتك (%d) ✓', 'Updated from your TVs (%d) ✓') % applied,
                                          xbmcgui.NOTIFICATION_INFO, 3500, sound=False)
    _log('mode %s%s: took %d, sent %d%s' % (mode, ' (after an update)' if fresh else '', applied,
                                            len(send) if sent else 0, '' if sent or not send else ' (not sent)'))
    return bool(applied)


def _after_apply():
    try:
        from . import cloud_look
        cloud_look._after_apply()
    except Exception:
        pass
    try:
        from .phone_setup import _republish
        _republish('sync')
    except Exception:
        pass
