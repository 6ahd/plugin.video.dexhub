# -*- coding: utf-8 -*-
"""Dex Community (v5.10.195): ready catalogs, picked one by one.

The catalogs are the bundled Kaptain collection's (Dex Collection): groups
(Streaming services, Genres, Moods...), their folders (Netflix, Action...)
and each folder's catalogs (New movies, Popular series...). A catalog picked
here becomes an added row of the Home, Movies or Series page (layout.json,
the layout editor's own rows), so it can be moved, restyled or taken out
there like any other row.

Where a catalog's titles come from, in this order (nuvio_collection_ui):
  1. the user's own AIOMetadata when it lists that catalog (found among the
     user's add-ons, or the link given here: setting community_aio_url);
  2. TMDb directly, with the user's own TMDb key (asked for here);
  3. TMDb with Dex Hub's shared key, until the user gives one.
Nothing is served by DexWorld.

Plain data and layout work; the window is community_window.py.
"""
import json
import os
import threading
import time

PAGES = (('all', 'الرئيسية'), ('movie', 'أفلام'), ('series', 'مسلسلات'))
LABEL = 'Dex Community'
_PREVIEW_TTL = 24 * 3600
_lock = threading.Lock()
_groups = None


def _cs():
    from .. import collection_sets
    return collection_sets


def _media(source):
    raw = str((source or {}).get('catalogType') or (source or {}).get('type') or
              (source or {}).get('mediaType') or 'movie').strip().lower()
    return 'series' if raw in ('series', 'tv', 'show', 'tvshow', 'anime') else 'movie'


def _folder_art(folder):
    try:
        from . import rows as R
        cover = R._usable_art(folder.get('poster')) or R._usable_art(folder.get('background'))
        shape = R.folder_shape(folder)
        logo = R._usable_art(folder.get('clearlogo'))
    except Exception:
        cover, shape, logo = '', 'poster', ''
    return cover, shape, logo


def _groups_of(row, imported=False):
    """The nuvioGroup entries of one collection as Community groups."""
    set_id = str(row.get('id') or '')
    set_name = str(row.get('name') or '').strip()
    out = []
    loose = []
    for entry in row.get('entries') or []:
        if not isinstance(entry, dict):
            continue
        if entry.get('kind') != 'nuvioGroup':
            if imported:
                loose.append(entry)
            continue
        folders = []
        for folder in (entry.get('payload') or {}).get('folders') or []:
            sources = (folder.get('payload') or {}).get('sources') or []
            catalogs = []
            for idx, source in enumerate(sources):
                if not isinstance(source, dict):
                    continue
                name = str(source.get('title') or source.get('name') or source.get('genre') or '').strip()
                catalogs.append({'idx': idx, 'name': name or folder.get('name') or '', 'media': _media(source),
                                 'nkey': _nuvio_key(source.get('addonId'), _media(source), source.get('catalogId'))})
            if catalogs:
                # v5.10.197: the folder's own cover (Kaptain's artwork)
                cover, shape, logo = _folder_art(folder)
                folders.append({'id': str(folder.get('id') or ''), 'name': str(folder.get('name') or '').strip(),
                                'catalogs': catalogs, 'cover': cover, 'shape': shape, 'logo': logo})
        if folders:
            entry_id = str(entry.get('id') or '')
            group = {'id': entry_id, 'name': str(entry.get('name') or '').strip(), 'folders': folders,
                     'set_id': set_id, 'entry_id': entry_id}
            if imported:
                group['id'] = 'set:%s:%s' % (set_id, entry_id)
                group['set_name'] = set_name
            out.append(group)
    if loose:
        # v5.10.211: a collection's own catalogs (Fusion widgets, single
        # catalogs): one group, each catalog a folder of its own
        folders = []
        for entry in loose:
            payload = entry.get('payload') or {}
            media = _media(payload)
            cover, shape, logo = _folder_art(entry)
            folders.append({'id': str(entry.get('id') or ''), 'name': str(entry.get('name') or '').strip(),
                            'catalogs': [{'idx': 0, 'name': str(entry.get('name') or '').strip(), 'media': media,
                                          'params': {'action': 'collection_entry_open', 'set_id': set_id,
                                                     'entry_id': str(entry.get('id') or '')}}],
                            'cover': cover, 'shape': shape, 'logo': logo})
        out.append({'id': 'set:%s:*' % set_id, 'name': set_name or 'Collection', 'folders': folders,
                    'set_id': set_id, 'entry_id': '', 'set_name': set_name, 'loose': True})
    return out


_INDEX = {}


# ------------------------------------------------- what Nuvio's Home holds
def _addon_family(addon_id):
    low = str(addon_id or '').strip().lower()
    if not low:
        return ''
    # every AIOMetadata (the collection's 'aio-metadata', the user's own
    # manifest id) is the same family: their catalog ids are TMDb's
    if 'aio' in low and 'meta' in low:
        return 'aiometadata'
    return low


def _nuvio_key(addon_id, media, catalog_id):
    family, cid = _addon_family(addon_id), str(catalog_id or '').strip()
    if not family or not cid:
        return ''
    return '%s|%s|%s' % (family, 'series' if media == 'series' else 'movie', cid)


def nuvio_home():
    """(catalog keys, collection ids) on the user's Nuvio Home (v5.10.213):
    a catalog of the Community already there says so instead of "not added"."""
    try:
        from ..dexhub import nuvio_home_mirror as M
        data = M.load() or {}
    except Exception:
        return frozenset(), frozenset()
    keys, collections = set(), set()
    for item in data.get('items') or []:
        if not item.get('enabled', True):
            continue
        if item.get('is_collection'):
            if item.get('collection_id'):
                collections.add(str(item['collection_id']))
            continue
        media = 'series' if str(item.get('type') or '').lower() in ('series', 'tv', 'show', 'anime') else 'movie'
        key = _nuvio_key(item.get('addon_id'), media, item.get('catalog_id'))
        if key:
            keys.add(key)
    return frozenset(keys), frozenset(collections)


def on_nuvio_home(group, catalog, home=None):
    keys, collections = home if home is not None else nuvio_home()
    if (group or {}).get('entry_id') in collections or (group or {}).get('id') in collections:
        return True
    return bool(catalog.get('nkey')) and catalog['nkey'] in keys


def groups():
    """[{id, name, folders: [{id, name, catalogs: [{idx, name, media}]}]}]:
    the bundled Dex Collection's groups, then those of the collections the
    user imported (v5.10.211, from a link or a JSON file: Nuvio, Kaptain,
    Fusion)."""
    global _groups
    with _lock:
        if _groups is not None:
            return _groups
        out = []
        cs = _cs()
        try:
            out.extend(_groups_of(cs._builtin_kaptain_set() or {}))
        except Exception:
            pass
        for row in imported_sets():
            try:
                out.extend(_groups_of(row, imported=True))
            except Exception:
                continue
        _groups = out
        _INDEX.clear()
        _INDEX.update((g['id'], g) for g in out)
        return out


def reset():
    """Read the groups again (a collection was imported or removed)."""
    global _groups
    with _lock:
        _groups = None


def imported_sets():
    """The user's own collections (not the bundled one, not Nuvio's cloud copy)."""
    cs = _cs()
    out = []
    for row in cs._read() or []:
        set_id = str((row or {}).get('id') or '')
        if not set_id or set_id == cs.BUILTIN_KAPTAIN_ID:
            continue        # (Nuvio's cloud copy stays: the user's own collections there)
        out.append(row)
    return out


def import_url(url, resolver=None):
    """(ok, message): a collection from a link (Nuvio share, Kaptain, Fusion, a JSON URL)."""
    url = str(url or '').strip()
    if not url:
        return False, 'رابط فارغ'
    try:
        row = _cs().add_set_from_url(url, manifest_resolver=resolver)
    except Exception as exc:
        return False, str(exc) or 'تعذر قراءة المجموعة'
    reset()
    return True, _summary(row)


SHARE_FORMAT = 'dexhub_community'


def _shared_spec(spec):
    return bool(spec.get('community')) or spec.get('source') in ('community', 'collection')


def share_data(layout):
    """v5.10.212: what the user built with Dex Community, as one file another
    Dex Hub opens with Import (the rows added to each page and the
    collections they come from)."""
    cs = _cs()
    pages, used = {}, set()
    for page, _name in PAGES:
        specs = [dict(spec) for spec in layout.page(page)['custom'] if _shared_spec(spec)]
        if specs:
            pages[page] = specs
            for spec in specs:
                set_id = spec.get('set_id') or (spec.get('params') or {}).get('set_id')
                if set_id:
                    used.add(str(set_id))
    collections = []
    for row in cs._read() or []:
        set_id = str(row.get('id') or '')
        if set_id == cs.BUILTIN_KAPTAIN_ID:
            continue
        if set_id == getattr(cs, 'NUVIO_CLOUD_ID', '') and set_id not in used:
            continue        # the account's own copy goes only when a row needs it
        collections.append({'id': set_id, 'name': row.get('name') or '', 'source_url': row.get('source_url') or '',
                            'entries': row.get('entries') or []})
    return {'format': SHARE_FORMAT, 'version': 1, 'created': int(time.time()), 'pages': pages,
            'collections': collections}


def restore_share(data, layout):
    """(rows added, collections added) from a shared file."""
    from .layout import new_custom
    cs = _cs()
    rows_now = cs._read() or []
    have = set(str(r.get('id') or '') for r in rows_now)
    added_sets = 0
    for row in data.get('collections') or []:
        set_id = str((row or {}).get('id') or '')
        if not set_id or set_id in have or not isinstance(row.get('entries'), list):
            continue
        rows_now.append({'id': set_id, 'name': row.get('name') or 'Collection',
                         'source_url': row.get('source_url') or '', 'entries': row['entries']})
        have.add(set_id)
        added_sets += 1
    if added_sets:
        cs._write(rows_now)
    added_rows = 0
    for page, specs in (data.get('pages') or {}).items():
        if page not in dict(PAGES):
            continue
        current = layout.page(page)
        keys = set(s.get('community') for s in current['custom'] if s.get('community'))
        extra = []
        for spec in specs or []:
            if not isinstance(spec, dict) or (spec.get('community') and spec['community'] in keys):
                continue
            fresh = new_custom(dict((k, v) for k, v in spec.items() if k not in ('id', 'added')))
            extra.append(fresh)
            added_rows += 1
        if extra:
            layout.save_page(page, custom=current['custom'] + extra)
    reset()
    return added_rows, added_sets


def import_file(path, resolver=None, layout=None):
    """(ok, message): a collection from a JSON file on the device, or a
    Dex Community share (v5.10.212)."""
    try:
        import xbmcvfs
        handle = xbmcvfs.File(path)
        try:
            raw = handle.readBytes()
        finally:
            handle.close()
        text = bytes(raw).decode('utf-8-sig', 'replace')
    except Exception as exc:
        return False, str(exc) or 'تعذر فتح الملف'
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, dict) and data.get('format') == SHARE_FORMAT:
        if layout is None:
            return False, 'تعذر الاستيراد'
        rows_n, sets_n = restore_share(data, layout)
        return True, 'Dex Community  •  %d / %d' % (rows_n, sets_n)
    name = os.path.splitext(os.path.basename(str(path).rstrip('/\\')))[0] or 'Collection'
    try:
        row = _cs().add_set_from_text(text, source_label=name, manifest_resolver=resolver)
    except Exception as exc:
        return False, str(exc) or 'الملف ليس مجموعة'
    reset()
    return True, _summary(row)


def _summary(row):
    groups_n = len(_groups_of(row or {}, imported=True))
    return '%s  •  %d' % (row.get('name') or 'Collection', groups_n)


def remove_imported(set_id):
    _cs().remove_set(set_id)
    reset()


def _group(group_id):
    groups()
    return _INDEX.get(group_id)


def fits(catalog, page):
    return page == 'all' or catalog['media'] == page


def folders_for(group, page):
    return [f for f in group['folders'] if any(fits(c, page) for c in f['catalogs'])]


def key_of(group_id, folder_id, idx):
    return '%s/%s/%s' % (group_id, folder_id, idx)


def params_of(group_id, folder_id, idx):
    group = _group(group_id)
    if group and group.get('loose'):
        folder = next((f for f in group['folders'] if f['id'] == folder_id), None)
        return dict(folder['catalogs'][0]['params']) if folder else {}
    set_id = (group or {}).get('set_id') or _cs().BUILTIN_KAPTAIN_ID
    entry_id = (group or {}).get('entry_id') or group_id
    return {'action': 'collection_nuvio_source_open', 'set_id': set_id,
            'group_id': entry_id, 'folder_id': folder_id, 'source_index': str(idx), 'page': '1'}


def row_label(folder, catalog):
    name, title = folder['name'], catalog['name']
    if not title or title == name:
        return name
    return '%s  •  %s' % (name, title)


# ---------------------------------------------------------------- layout
def added(layout, page):
    """{community key: custom row id} of a page."""
    out = {}
    for spec in layout.page(page)['custom']:
        key = spec.get('community')
        if key:
            out[key] = spec.get('id') or ''
    return out


def add(layout, page, group, folder, catalog):
    from .layout import new_custom
    key = key_of(group['id'], folder['id'], catalog['idx'])
    current = layout.page(page)
    if any(s.get('community') == key for s in current['custom']):
        return False
    spec = new_custom({'type': 'catalog', 'label': row_label(folder, catalog),
                       'params': params_of(group['id'], folder['id'], catalog['idx']),
                       'source': 'community', 'source_label': LABEL, 'community': key})
    layout.save_page(page, custom=current['custom'] + [spec])
    return True


def group_key(group):
    return 'group:%s' % group['id']


def add_group(layout, page, group, label=''):
    """A whole group as one collection row (its folders as cards)."""
    from .layout import new_custom
    key = group_key(group)
    current = layout.page(page)
    if any(s.get('community') == key for s in current['custom']):
        return False
    if group.get('loose'):
        return False        # its catalogs are rows of their own
    spec = new_custom({'type': 'collection', 'label': label or group['name'], 'source': 'collection',
                       'source_label': LABEL, 'set_id': group.get('set_id') or _cs().BUILTIN_KAPTAIN_ID,
                       'entry_id': group.get('entry_id') or group['id'], 'community': key})
    layout.save_page(page, custom=current['custom'] + [spec])
    return True


def add_folders(layout, page, label, picks):
    """Chosen folders (of any groups) as one collection row: [(group, folder)]."""
    from .layout import new_custom
    picks = [(g, f) for g, f in picks if not g.get('loose')]
    refs = [{'group_id': g.get('entry_id') or g['id'], 'folder_id': f['id']} for g, f in picks]
    if not refs:
        return False
    sets = set(g.get('set_id') or _cs().BUILTIN_KAPTAIN_ID for g, _f in picks)
    if len(sets) > 1:
        raise ValueError('one collection per row')
    current = layout.page(page)
    spec = new_custom({'type': 'folders', 'label': label, 'source': 'collection', 'source_label': LABEL,
                       'set_id': sets.pop(), 'folders': refs})
    spec['community'] = 'folders:%s' % spec['id']
    layout.save_page(page, custom=current['custom'] + [spec])
    return True


def folder_row_tiles(spec, wrap=None, gif_enabled=True, media_filter=''):
    """The cards of a 'folders' row (v5.10.197): each chosen folder as its
    own collection would show it, in the order chosen."""
    from . import rows as R
    cs = _cs()
    row = cs.get_set(spec.get('set_id') or cs.BUILTIN_KAPTAIN_ID) or {}
    groups_by_id = dict((str(e.get('id') or ''), e) for e in row.get('entries') or [] if isinstance(e, dict))
    tiles = []
    for ref in spec.get('folders') or []:
        entry = groups_by_id.get(str(ref.get('group_id') or ''))
        if not entry:
            continue
        folder = next((f for f in (entry.get('payload') or {}).get('folders') or []
                       if str(f.get('id') or '') == str(ref.get('folder_id') or '')), None)
        if folder is None:
            continue
        one = dict(entry)
        one['payload'] = dict(entry.get('payload') or {}, folders=[folder])
        tiles.extend(R.folder_tiles(one, row.get('id') or cs.BUILTIN_KAPTAIN_ID, wrap=wrap,
                                    gif_enabled=gif_enabled, media_filter=media_filter))
    return tiles


def remove(layout, page, key):
    current = layout.page(page)
    keep = [s for s in current['custom'] if s.get('community') != key]
    if len(keep) == len(current['custom']):
        return False
    gone = ['my:%s' % s.get('id') for s in current['custom'] if s.get('community') == key]
    order = [k for k in current.get('order') or [] if k not in gone]
    layout.save_page(page, order=order, custom=keep)
    return True


# ------------------------------------------------------------- the source
def personal_key():
    """True when the user gave a TMDb key of their own."""
    try:
        from ..tmdb_direct import DEFAULT_API_KEY
        from ..live_settings import live_setting
        key = (live_setting('tmdb_api_key', default='') or '').strip()
    except Exception:
        return False
    return bool(key) and key != DEFAULT_API_KEY


def aio_url():
    try:
        import xbmcaddon
        url = (xbmcaddon.Addon('plugin.video.dexhub').getSetting('community_aio_url') or '').strip()
    except Exception:
        url = ''
    if url and not url.endswith('/manifest.json'):
        url = url.rstrip('/') + '/manifest.json'
    return url


def _is_aio_manifest(manifest, url=''):
    mid = str((manifest or {}).get('id') or '').lower()
    name = str((manifest or {}).get('name') or '').lower()
    return ('aio' in mid and 'meta' in mid) or 'aiometadata' in name.replace(' ', '').replace('-', '') or \
        'aio-metadata' in mid


def installed_aio():
    """(name, manifest url, manifest) of an AIOMetadata among the user's
    add-ons, or None (v5.10.198: with the manifest saved when it was added)."""
    try:
        from ..dexhub import store
        rows = store.list_providers() or []
    except Exception:
        rows = []
    for row in rows:
        manifest = row.get('manifest') or {}
        url = str(row.get('manifest_url') or '')
        if url and (_is_aio_manifest(manifest) or _is_aio_url(url)):
            return (row.get('name') or manifest.get('name') or 'AIOMetadata', url,
                    manifest if manifest.get('catalogs') else None)
    # v5.10.212: the AIOMetadata of the user's Nuvio account (its add-ons are
    # mirrored, not always among Dex Hub's own): the Community said there was
    # none while Nuvio's own catalogs showed on the Home
    try:
        from .. import collection_sets as cs
        data = cs._nuvio_addon_snapshot()
        for url in (data.get('urls') if isinstance(data, dict) else None) or []:
            url = str(url or '')
            if _is_aio_url(url):
                if not url.endswith('/manifest.json'):
                    url = url.rstrip('/') + '/manifest.json'
                return ('AIOMetadata (Nuvio)', url, None)
    except Exception:
        pass
    return None


def _is_aio_url(url):
    low = str(url or '').lower()
    return 'aiometadata' in low or 'aio-metadata' in low or 'aiometa' in low


def source_state(tr):
    """(state, line): 'aio', 'key' or 'shared', and what the page says of it."""
    url = aio_url()
    if url:
        host = url.split('://', 1)[-1].split('/', 1)[0]
        return 'aio', tr('AIOMetadata الخاص بك') + '  •  ' + host
    found = installed_aio()
    if found and found[0] == 'AIOMetadata (Nuvio)':
        return 'aio', tr('AIOMetadata من حساب Nuvio')
    if found:
        return 'aio', tr('AIOMetadata من إضافاتك') + '  •  ' + found[0]
    if personal_key():
        return 'key', tr('مفتاح TMDb الخاص بك')
    return 'shared', tr('مفتاح TMDb المشترك (أضف مفتاحك)')


def link_aio(url):
    """Check an AIOMetadata manifest link and keep it; returns (ok, message)."""
    url = (url or '').strip()
    if url.startswith('stremio://'):
        url = 'https://' + url[len('stremio://'):]
    if not url.startswith(('http://', 'https://')):
        return False, 'رابط غير صحيح'
    if not url.endswith('/manifest.json'):
        url = url.rstrip('/') + '/manifest.json'
    try:
        from ..dexhub.client import validate_manifest
        manifest = validate_manifest(url, force_refresh=True) or {}
    except Exception:
        return False, 'تعذر قراءة الرابط'
    if not (manifest.get('catalogs') or []):
        return False, 'الرابط لا يحتوي على كتالوجات'
    import xbmcaddon
    xbmcaddon.Addon('plugin.video.dexhub').setSetting('community_aio_url', url)
    return True, manifest.get('name') or 'AIOMetadata'


def unlink_aio():
    import xbmcaddon
    xbmcaddon.Addon('plugin.video.dexhub').setSetting('community_aio_url', '')


# --------------------------------------------------------------- previews
class Previews(object):
    """First posters of a catalog, read once a day (profile/community_previews.json)."""

    def __init__(self, profile):
        self.path = os.path.join(profile, 'community_previews_v2.json')
        self.data = {}
        self.dirty = False
        try:
            with open(self.path, 'r', encoding='utf-8') as handle:
                raw = json.load(handle)
            now = time.time()
            self.data = dict((k, v) for k, v in (raw or {}).items()
                             if isinstance(v, dict) and now - float(v.get('t') or 0) < _PREVIEW_TTL)
        except Exception:
            self.data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self.data.get(key)
        return list(hit.get('p') or []) if hit else None

    def read(self, key, params, api, count=6):
        hit = self.get(key)
        if hit is not None:
            return hit
        from . import rows as R
        posters = []
        try:
            tiles, _more = R.capture_tiles(dict(params), api, limit=count * 2)
            for tile in tiles:
                art = tile.get('poster') or tile.get('landscape') or ''
                if art:
                    posters.append(art)
                if len(posters) >= count:
                    break
        except Exception:
            posters = []
        with self._lock:
            self.data[key] = {'t': time.time(), 'p': posters}
            self.dirty = True
        return posters

    def save(self):
        with self._lock:
            if not self.dirty:
                return
            data = dict(self.data)
            self.dirty = False
        try:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(data, handle)
            os.replace(tmp, self.path)
        except Exception:
            pass
