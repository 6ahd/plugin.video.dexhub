# -*- coding: utf-8 -*-
"""Self-hosted AIO Metadata bridge for Dex Hub.

The bridge has two jobs:
  * map Kaptain/Nuvio ``aio-metadata`` catalog ids to native TMDb/Trakt
    sources so leaf rows are real works (Poster + metadata), not generic addon
    thumbnails;
  * optionally redirect unresolved AIO catalogs to a user supplied self-hosted
    Stremio manifest.

A user-imported AIO config in the addon profile wins over the bundled safe
catalog map. The bundled map contains catalog definitions only, no credentials.
"""
import json
import os
import re

import xbmcaddon
import xbmcvfs

from . import bundled_data as _bundled_data

ADDON = xbmcaddon.Addon()
_CONFIG_CACHE = None
_MAP_CACHE = None


def _profile_dir():
    try:
        path = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    except Exception:
        path = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/')
    try:
        xbmcvfs.mkdirs(path)
    except Exception:
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
    return path


def imported_config_path():
    return os.path.join(_profile_dir(), 'selfhost_aiometadata.json')


def bundled_config_path():
    # bundled_data.resolve() returns the .gz variant when the release build
    # compressed it, so this stays correct for both reading and display.
    return _bundled_data.resolve(os.path.join(
        ADDON.getAddonInfo('path'), 'resources', 'data',
        'aiometadata_config_default.json'))


def bundled_collection_path():
    return _bundled_data.resolve(os.path.join(
        ADDON.getAddonInfo('path'), 'resources', 'data',
        'nuvio_collection_selfhost_default.json'))


def enabled():
    try:
        return (ADDON.getSetting('selfhost_enabled') or 'false').strip().lower() in ('1','true','yes','on')
    except Exception:
        return False


def manifest_url():
    try:
        raw = (ADDON.getSetting('selfhost_aio_manifest_url') or '').strip()
    except Exception:
        raw = ''
    if raw and not raw.endswith('/manifest.json'):
        raw = raw.rstrip('/') + '/manifest.json'
    return raw


def _read_json(path):
    # Handles plain .json, gzipped .json.gz and the Android/Samba/file-manager
    # paths that plain open() cannot reach. User-imported configs in the addon
    # profile are always uncompressed and go down the same path unchanged.
    return _bundled_data.read_json(path)


def load_config():
    global _CONFIG_CACHE
    if isinstance(_CONFIG_CACHE, dict):
        return _CONFIG_CACHE
    data = _read_json(imported_config_path())
    if not isinstance(data, dict) or not isinstance(data.get('catalogs'), list):
        data = _read_json(bundled_config_path())
    _CONFIG_CACHE = data if isinstance(data, dict) else {'version': 1, 'catalogs': []}
    return _CONFIG_CACHE


def save_imported_config(path):
    global _CONFIG_CACHE, _MAP_CACHE
    data = _read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get('catalogs'), list):
        raise ValueError('AIO Metadata config must contain catalogs[]')
    target = imported_config_path()
    with open(target, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(',', ':'))
    _CONFIG_CACHE = data
    _MAP_CACHE = None
    return target, len(data.get('catalogs') or [])


def config_status():
    custom = os.path.exists(imported_config_path())
    data = load_config()
    return {
        'custom': custom,
        'path': imported_config_path() if custom else bundled_config_path(),
        'catalogs': len(data.get('catalogs') or []),
    }


def _catalog_map():
    global _MAP_CACHE
    if isinstance(_MAP_CACHE, dict):
        return _MAP_CACHE
    rows = load_config().get('catalogs') or []
    _MAP_CACHE = {str(row.get('id') or '').strip(): row for row in rows if isinstance(row, dict) and row.get('id')}
    return _MAP_CACHE


def _media_type(value):
    v = str(value or '').strip().lower()
    return 'series' if v in ('series','tv','show','shows','tvshow') else 'movie'


def native_source_for_catalog(source):
    """Return a native Nuvio source for an aio-metadata catalog when possible."""
    source = source if isinstance(source, dict) else {}
    addon_id = str(source.get('addonId') or '').strip().lower()
    if addon_id not in ('aio-metadata', 'aiometadata') and 'aio-metadata' not in addon_id and 'aiometadata' not in addon_id:
        return None
    cid = str(source.get('catalogId') or '').strip()
    if not cid:
        return None
    name = str(source.get('name') or source.get('title') or source.get('genre') or cid).strip()
    mt = _media_type(source.get('catalogType') or source.get('mediaType') or source.get('type'))

    # Fast deterministic mappings do not require a config file.
    m = re.match(r'^tmdb\.list\.(\d+)$', cid, re.I)
    if m:
        return {'sourceKind':'tmdb','name':name,'mediaType':mt,'tmdbSourceType':'LIST','tmdbId':m.group(1),'filters':{}}
    m = re.match(r'^trakt\.list\.(\d+)$', cid, re.I)
    if m:
        return {'sourceKind':'traktIdList','name':name,'mediaType':mt,'traktListId':m.group(1)}

    row = _catalog_map().get(cid)
    if not isinstance(row, dict):
        return None
    row_mt = _media_type(row.get('type') or row.get('displayType') or mt)
    src = str(row.get('source') or '').strip().lower()
    meta = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    if src == 'tmdb':
        discover = meta.get('discover') if isinstance(meta.get('discover'), dict) else {}
        params = discover.get('params') if isinstance(discover.get('params'), dict) else {}
        if params:
            return {
                'sourceKind':'tmdb','name':name,'mediaType':row_mt,
                'tmdbSourceType':'DISCOVER','tmdbId':'','filters':{},
                'tmdbParams':dict(params),
            }
        list_id = str(meta.get('listId') or '').strip()
        if list_id:
            return {'sourceKind':'tmdb','name':name,'mediaType':row_mt,'tmdbSourceType':'LIST','tmdbId':list_id,'filters':{}}
    if src == 'trakt':
        list_id = ''
        url = str(meta.get('url') or '')
        m = re.search(r'/lists/(\d+)', url)
        if m:
            list_id = m.group(1)
        if not list_id:
            m = re.match(r'^trakt\.list\.(\d+)$', cid, re.I)
            if m:
                list_id = m.group(1)
        if list_id:
            return {'sourceKind':'traktIdList','name':name,'mediaType':row_mt,'traktListId':list_id}
    return None
