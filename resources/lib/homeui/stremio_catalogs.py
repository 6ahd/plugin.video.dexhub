# -*- coding: utf-8 -*-
"""Manifest contract shared by IPTV discovery, facets and pagination."""
import json
from urllib.parse import urlsplit


def dexworld_sortable(provider, catalog):
    mid = str((provider.get('manifest') or {}).get('id') or '')
    host = (urlsplit(str(provider.get('manifest_url') or provider.get('base_url') or '')).hostname or '').lower()
    known = mid == 'org.dexworld.v50.alpha' or mid.startswith(
        ('org.dexworld.v50.user.', 'org.dexworld.v50.source.')) or host in ('dexworld.cc', 'www.dexworld.cc')
    cid = str(catalog.get('id') or '').split('~', 1)[0]
    return known and cid.startswith(('dex_vod_', 'dex_series_', 'dexcat_movie_', 'dexcat_series_'))


def extras(catalog):
    out = {}
    for entry in catalog.get('extra') or []:
        entry = entry if isinstance(entry, dict) else {'name': entry}
        name = str(entry.get('name') or '').strip().lower()
        if name:
            entry = dict(entry, name=name)
            required = entry.get('isRequired')
            entry['isRequired'] = (required.lower() == 'true' if isinstance(required, str)
                                   else bool(required))
            entry['options'] = [str(o) for o in entry.get('options') or [] if o is not None]
            out[name] = entry
    for field in ('extraSupported', 'extraRequired'):
        for name in catalog.get(field) or []:
            if isinstance(name, str) and name:
                name = name.strip().lower()
                entry = out.setdefault(name, {'name': name})
                if field == 'extraRequired':
                    entry['isRequired'] = True
    return out


def supports(catalog, name):
    return str(name).strip().lower() in extras(catalog)


def is_live(catalog):
    media = str(catalog.get('type') or '').lower()
    cid = str(catalog.get('id') or '').split('~', 1)[0]
    return media in ('tv', 'channel', 'channels', 'live', 'iptv') or (
        media == 'movie' and (cid.startswith(('dex_live_', 'dexcat_channel_')) or
                              cid in ('dex_favs_live', 'dex_search_channel')))


def browsable(catalog):
    return bool(catalog.get('id') and catalog.get('enabled') is not False and
                not catalog.get('hidden') and not any(
                    e.get('isRequired') for name, e in extras(catalog).items() if name == 'search'))


def definition(provider, params):
    return next((c for c in (provider.get('manifest') or {}).get('catalogs') or []
                 if isinstance(c, dict) and str(c.get('id')) == str(params.get('catalog')) and
                 str(c.get('type')) == str(params.get('type') or params.get('vod_type'))), {})


def request_extras(catalog, params):
    declared = extras(catalog)
    result = {}
    for name, entry in declared.items():
        if name in ('skip', 'search'):
            continue
        value = entry.get('default')
        if value in (None, '') and entry.get('isRequired') and entry.get('options'):
            value = entry['options'][0]
        if value not in (None, ''):
            result[name] = str(value)
    try:
        chosen = json.loads(params.get('extras') or '{}')
        if isinstance(chosen, dict):
            result.update({str(k).strip().lower(): str(v) for k, v in chosen.items()
                           if v not in (None, '') and str(k).strip().lower() in declared
                           and str(k).strip().lower() not in ('skip', 'search')})
    except (ValueError, TypeError):
        pass
    for name in declared:
        if name not in ('skip', 'search') and params.get(name) not in (None, ''):
            result[name] = str(params[name])
    return result
