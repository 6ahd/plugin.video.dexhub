# -*- coding: utf-8 -*-
"""v5.10.426: covers chosen for collections, folders and catalogs.

The account page (DexCloud's cover studio, Collections+ artwork) or the phone
page picks a picture for a collection, a folder of it or one catalog of a
folder. The choice lives in its own file (collection_covers.json, with the
collections in backups, device sync and the look), never in the collection:
a collection read again from its link keeps the chosen covers, and clearing
one gives back the collection's own artwork.

Keys:
  folders   '<set id>|<entry id>|<folder id>'  (folder id '' for the entry itself)
  catalogs  '<set id>|<folder id>#<source index>' ('all' for the folder's All card)
Each value: {landscape, poster, logo, hide_title, preset, at}.

collection_sets dresses what it reads (dress) and undresses what it writes
(undress): an edit saved while a cover is on never stores the cover.
"""
import json
import os
import threading
import time

from . import profile_scope as _scope

FILE = 'collection_covers.json'
ORIG = '_cover_orig'
FIELDS = ('background', 'poster', 'clearlogo', 'hide_title')
_lock = threading.RLock()
_cache = {'key': None, 'data': None}


def _path():
    return _scope.path(FILE)


def _empty():
    return {'v': 1, 'folders': {}, 'catalogs': {}}


def load():
    path = _path()
    try:
        stamp = (_scope.key(), os.path.getmtime(path))
    except OSError:
        stamp = (_scope.key(), None)
    with _lock:
        if _cache['key'] == stamp and _cache['data'] is not None:
            return _cache['data']
        data = _empty()
        if stamp[1] is not None:
            try:
                with open(path, encoding='utf-8') as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    for part in ('folders', 'catalogs'):
                        if isinstance(raw.get(part), dict):
                            data[part] = raw[part]
            except Exception:
                pass
        _cache.update(key=stamp, data=data)
        return data


def _save(data):
    from .dexhub.safe_io import write_json
    with _lock:
        write_json(_path(), data)
        _cache.update(key=None, data=None)


def folder_key(set_id, entry_id, folder_id=''):
    return '%s|%s|%s' % (set_id or '', entry_id or '', folder_id or '')


def catalog_key(set_id, folder_id, index):
    return '%s|%s#%s' % (set_id or '', folder_id or '', index)


def _clean(value):
    """Only picture links and two flags are kept (from the page: never trusted)."""
    if not isinstance(value, dict):
        return None
    out = {}
    for field in ('landscape', 'poster', 'logo'):
        url = str(value.get(field) or '').strip()
        if url.startswith(('https://', 'http://')) and len(url) <= 1000:
            out[field] = url
    if not out:
        return None
    out['hide_title'] = bool(value.get('hide_title')) and bool(out.get('logo'))
    preset = str(value.get('preset') or '').strip()[:120]
    if preset:
        out['preset'] = preset
    out['at'] = int(time.time())
    return out


def apply_changes(items=None, clear=None):
    """Set (``items``: [{key, kind, landscape, poster, logo, hide_title,
    preset}]) and clear (``clear``: [{key, kind}]) covers. Returns the count."""
    data = json.loads(json.dumps(load()))
    count = 0
    for item in items or []:
        kind = 'catalogs' if (item or {}).get('kind') == 'catalog' else 'folders'
        key = str((item or {}).get('key') or '')[:400]
        value = _clean(item)
        if key and value:
            data[kind][key] = value
            count += 1
    for item in clear or []:
        kind = 'catalogs' if (item or {}).get('kind') == 'catalog' else 'folders'
        key = str((item or {}).get('key') or '')
        if data[kind].pop(key, None) is not None:
            count += 1
    if count:
        _save(data)
    return count


# ------------------------------------------------------------ dressing
def _dress_entry(entry, cover):
    if not cover or not isinstance(entry, dict):
        return entry
    entry = dict(entry)
    entry[ORIG] = dict((f, entry.get(f)) for f in FIELDS)
    if cover.get('landscape'):
        entry['background'] = cover['landscape']
    if cover.get('poster') or cover.get('landscape'):
        entry['poster'] = cover.get('poster') or cover['landscape']
    if cover.get('logo'):
        entry['clearlogo'] = cover['logo']
        entry['hide_title'] = bool(cover.get('hide_title'))
    entry['_cover'] = dict(cover)
    return entry


def dress(rows):
    """The collections with their chosen covers (copies: ``rows`` is not changed)."""
    folders = load().get('folders') or {}
    if not folders or not rows:
        return rows
    out = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        set_id = str(row.get('id') or '')
        entries = row.get('entries') or []
        if not any(k.startswith(set_id + '|') for k in folders):
            out.append(row)
            continue
        new_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                new_entries.append(entry)
                continue
            entry_id = str(entry.get('id') or '')
            entry = _dress_entry(entry, folders.get(folder_key(set_id, entry_id)))
            payload = entry.get('payload') if isinstance(entry.get('payload'), dict) else None
            inner = (payload or {}).get('folders')
            if isinstance(inner, list) and any(folder_key(set_id, entry_id, str((f or {}).get('id') or '')) in folders
                                               for f in inner if isinstance(f, dict)):
                payload = dict(payload)
                payload['folders'] = [_dress_entry(f, folders.get(folder_key(set_id, entry_id, str(f.get('id') or ''))))
                                      if isinstance(f, dict) else f for f in inner]
                entry = dict(entry, payload=payload)
            new_entries.append(entry)
        out.append(dict(row, entries=new_entries))
    return out


def _undress_entry(entry):
    if not isinstance(entry, dict):
        return entry
    entry = dict(entry)
    orig = entry.pop(ORIG, None)
    cover = entry.pop('_cover', None) or {}
    if isinstance(orig, dict):
        worn = {'background': cover.get('landscape'), 'poster': cover.get('poster') or cover.get('landscape'),
                'clearlogo': cover.get('logo'), 'hide_title': bool(cover.get('hide_title')) if cover.get('logo') else None}
        for field in FIELDS:
            # a field the cover set and nobody changed since: the collection's own
            if worn.get(field) is not None and entry.get(field) == worn[field]:
                entry[field] = orig.get(field)
    payload = entry.get('payload')
    if isinstance(payload, dict) and isinstance(payload.get('folders'), list):
        entry['payload'] = dict(payload, folders=[_undress_entry(f) for f in payload['folders']])
    return entry


def undress(rows):
    """What collection_sets writes: the collections without the chosen covers."""
    out = []
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get('entries'), list):
            row = dict(row, entries=[_undress_entry(e) for e in row['entries']])
        out.append(row)
    return out


def catalog_cover(set_id, folder_id, index):
    """The picture chosen for one catalog of a folder (its folder view card)."""
    value = (load().get('catalogs') or {}).get(catalog_key(set_id, folder_id, index)) or {}
    return value.get('landscape') or value.get('poster') or ''


# ------------------------------------------------------------ the pages
def targets(builtin=False):
    """What can take a cover, for the account page: every collection with
    its entries, their folders and the folders' catalogs, each with its
    current picture and key. The bundled Dex Collection (756 folders) only
    when asked: the page shows it as Dex Community, folder by folder."""
    from . import collection_sets as cs
    data = load()
    out = []
    for row in cs.list_sets() or []:
        if row.get('builtin') and not builtin:
            continue
        set_id = str(row.get('id') or '')
        entries = []
        for entry in row.get('entries') or []:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get('id') or '')
            item = _target(set_id, entry_id, '', entry, data)
            inner = (entry.get('payload') or {}).get('folders') if isinstance(entry.get('payload'), dict) else None
            if isinstance(inner, list):
                item['folders'] = [_target(set_id, entry_id, str(f.get('id') or ''), f, data)
                                   for f in inner if isinstance(f, dict)]
            else:
                item['catalogs'] = _catalogs(set_id, entry_id, entry, data)
            entries.append(item)
        out.append({'id': set_id, 'name': str(row.get('name') or ''), 'builtin': bool(row.get('builtin')),
                    'entries': entries})
    return out


def _target(set_id, entry_id, folder_id, entry, data):
    key = folder_key(set_id, entry_id, folder_id)
    own = entry.get(ORIG) or {}
    item = {'key': key, 'kind': 'folder', 'name': str(entry.get('name') or ''),
            'landscape': entry.get('background') or '', 'poster': entry.get('poster') or '',
            'logo': entry.get('clearlogo') or '', 'chosen': key in (data.get('folders') or {}),
            'own': {'landscape': own.get('background', entry.get('background')) or '',
                    'poster': own.get('poster', entry.get('poster')) or ''}}
    if folder_id:
        item['catalogs'] = _catalogs(set_id, folder_id, entry, data)
    return item


def _catalogs(set_id, folder_id, entry, data):
    sources = (entry.get('payload') or {}).get('sources') if isinstance(entry.get('payload'), dict) else None
    out = []
    chosen = data.get('catalogs') or {}
    if len(sources or []) > 1:
        key = catalog_key(set_id, folder_id, 'all')
        value = chosen.get(key) or {}
        out.append({'key': key, 'kind': 'catalog', 'name': 'الكل / All', 'chosen': bool(value),
                    'landscape': value.get('landscape') or ''})
    for index, source in enumerate(sources or []):
        if not isinstance(source, dict):
            continue
        name = str(source.get('name') or source.get('title') or '').strip() or '#%d' % (index + 1)
        key = catalog_key(set_id, folder_id, index)
        value = chosen.get(key) or {}
        out.append({'key': key, 'kind': 'catalog', 'name': name, 'chosen': bool(value),
                    'landscape': value.get('landscape') or ''})
    return out
