# -*- coding: utf-8 -*-
"""The viewer's linked Debrid accounts, kept on this device only.

addon_data/plugin.video.dexhub/debrid.json:
  {"accounts": {"realdebrid": {"token": ..., "refresh": ..., "client_id": ...,
                               "client_secret": ..., "expires": 1700000000,
                               "user": "name", "linked": 1700000000}, ...},
   "order": ["torbox", "realdebrid"],
   "options": {"stremthru": true, "auto_sources": true, "cache_and_play": false}}

Tokens are never written to the log.
"""
import json
import os
import threading
import time

try:
    import xbmc
    import xbmcvfs
except Exception:          # tests run without Kodi
    xbmc = None
    xbmcvfs = None

_FILE = 'special://profile/addon_data/plugin.video.dexhub/debrid.json'
_LOCK = threading.RLock()
_CACHE = {}

# Real-Debrid, AllDebrid, Premiumize, TorBox, in the order a new viewer meets them
SERVICES = ('realdebrid', 'alldebrid', 'torbox', 'premiumize')
NAMES = {'realdebrid': 'Real-Debrid', 'alldebrid': 'AllDebrid', 'torbox': 'TorBox', 'premiumize': 'Premiumize'}
BADGES = {'realdebrid': 'RD', 'alldebrid': 'AD', 'torbox': 'TB', 'premiumize': 'PM'}
DEFAULT_OPTIONS = {
    # what Real-Debrid and AllDebrid have cached, asked of StremThru with the account's key
    'stremthru': True,
    # Torrentio and Comet added as sources after the first account is linked
    'auto_sources': True,
    # a torrent the service does not have yet is added and waited for (off: only cached ones play)
    'cache_and_play': False,
    # a whole Blu-ray disc (BDMV folders) opens in its own menus (discserver); off: its main film plays
    'disc_menus': True,
}

# Replaced by tests: the folder that holds debrid.json.
PATH_OVERRIDE = None


def path(name='debrid.json'):
    if PATH_OVERRIDE:
        return os.path.join(PATH_OVERRIDE, name)
    base = _FILE.rsplit('/', 1)[0] + '/' + name
    return xbmcvfs.translatePath(base) if xbmcvfs else base


def log(msg, warning=False):
    if xbmc is None:
        return
    xbmc.log('[DexHub] Debrid: %s' % msg, xbmc.LOGWARNING if warning else xbmc.LOGINFO)


def _load():
    with _LOCK:
        if 'data' in _CACHE:
            return _CACHE['data']
        data = {}
        try:
            with open(path(), 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
        except Exception:
            data = {}
        if not isinstance(data.get('accounts'), dict):
            data['accounts'] = {}
        if not isinstance(data.get('order'), list):
            data['order'] = []
        options = dict(DEFAULT_OPTIONS)
        options.update(data.get('options') if isinstance(data.get('options'), dict) else {})
        data['options'] = options
        _CACHE['data'] = data
        return data


def _save(data):
    with _LOCK:
        target = path()
        folder = os.path.dirname(target)
        try:
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            temp = target + '.tmp'
            with open(temp, 'w', encoding='utf-8') as handle:
                json.dump(data, handle)
            os.replace(temp, target)
        except Exception as exc:
            log('accounts not saved: %s' % exc, True)
        _CACHE['data'] = data


def reset_cache():
    with _LOCK:
        _CACHE.clear()


def get(service):
    """The stored account (a copy), {} when it is not linked."""
    return dict(_load()['accounts'].get(service) or {})


def save(service, account):
    with _LOCK:
        data = _load()
        account = dict(account or {})
        account.setdefault('linked', int(time.time()))
        data['accounts'][service] = account
        if service not in data['order']:
            data['order'].append(service)
        _save(data)


def update(service, **fields):
    with _LOCK:
        data = _load()
        if service in data['accounts']:
            data['accounts'][service].update(fields)
            _save(data)


def remove(service):
    with _LOCK:
        data = _load()
        data['accounts'].pop(service, None)
        data['order'] = [s for s in data['order'] if s != service]
        _save(data)


def linked():
    """The linked services, in the viewer's order."""
    data = _load()
    have = [s for s in data['order'] if s in data['accounts'] and data['accounts'][s].get('token')]
    for service in SERVICES:
        if service not in have and (data['accounts'].get(service) or {}).get('token'):
            have.append(service)
    return have


def any_linked():
    return bool(linked())


def option(name):
    return _load()['options'].get(name, DEFAULT_OPTIONS.get(name))


def set_option(name, value):
    with _LOCK:
        data = _load()
        data['options'][name] = value
        _save(data)


def token(service):
    return str((_load()['accounts'].get(service) or {}).get('token') or '')
