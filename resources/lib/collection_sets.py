# -*- coding: utf-8 -*-
"""Collection sets — external JSON shortcuts à la Fusion Starter Kit.

A "collection set" is a JSON array where each entry defines a named shortcut
that points to either:
  - an addonCatalog (a specific catalog from a Stremio-compatible manifest)
  - a traktList    (a Trakt user list)

Shape (one entry):
{
  "name": "Paul Thomas Anderson",
  "id": "...",
  "layout": "Poster" | "Wide",
  "backgroundImageURL": "https://...",
  "hideTitle": false,
  "dataSource": {
    "kind": "traktList" | "addonCatalog",
    "payload": {...}
  }
}

We persist:
  - collection_sets.json       → [{id, name, source_url, entries:[...]}]
The entries are frozen at add-time and can be refreshed later.
"""
from . import profile_scope as _scope
import hashlib
import json
import os
import uuid
from urllib.parse import urlparse

from .dexhub.common import profile_path
from . import store as _store

SETS_FILE = _scope.ScopedPath('collection_sets.json')
ACTIVE_FILE = _scope.ScopedPath('active_collection.json')
BUILTIN_PREFS_FILE = _scope.ScopedPath('dex_collection_prefs.json')
NUVIO_ADDON_URLS_FILE = _scope.ScopedPath('nuvio_enabled_addons.json')

# Known public Stremio manifests used by popular Nuvio/Fusion collection
# exports. Collection JSONs usually reference addons by manifest `id` only
# (addonId), so Dex Hub cannot discover the transport URL unless we keep a
# small resolver table. User-configured/provider-installed manifests always
# win over this table.
# Private Plexio/StreamBridge manifests must be added by the user from the
# public setup sites. Never bundle per-user instance URLs or tokens here.
DEXWORLD_PLEXIO_SITE = 'https://plexio.dexworld.cc/'
DEXWORLD_STREAMBRIDGE_SITE = 'https://sb.dexworld.cc/'

KNOWN_ADDON_MANIFESTS = {
    # Streaming Catalogs — used by Nuvio profiles for Netflix/HBO/Disney/etc.
    'pw.ers.netflix-catalog': 'https://7a82163c306e-stremio-netflix-catalog-addon.baby-beamup.club/manifest.json',
}

# Addons that are known to require a user-configured manifest. Do not silently
# register their public shell manifest because it may expose no catalogs until
# configured.
CONFIG_REQUIRED_ADDONS = {
    'com.stremio.plexio',
    'com.stremio.plexbridge',
    'plexio',
    'plexbridge',
    'streambridge',
    'dexbridge',
    'com.stremio.streambridge',
    'com.stremio.dexbridge',
}

_CACHE_ROWS = None
_CACHE_MTIME = None

# v5.4.16: a curated Nuvio/Kaptain collection ships with Dex Hub itself.
# It is virtual/read-only: no profile write is needed on first run, so a fresh
# install (or a wiped profile) always has useful Movies/TV collection widgets.
BUILTIN_KAPTAIN_ID = 'builtin-kaptain'
BUILTIN_KAPTAIN_URL = 'https://imkaptain.github.io/Kaptain-Collection/'
_BUILTIN_SET_CACHE = None

# v5.4.29: Nuvio account collections live in one stable, user-renamable set.
# Keeping the ID stable means Home/AF3 shortcuts survive every cloud refresh.
NUVIO_CLOUD_ID = 'nuvio-cloud'
NUVIO_CLOUD_SOURCE = 'nuvio://cloud'


def _is_builtin_id(set_id):
    return str(set_id or '').strip() == BUILTIN_KAPTAIN_ID


@_scope.bound
def builtin_collection_enabled():
    try:
        from .dexhub.safe_io import read_json as _safe_read_json
        row = _safe_read_json(BUILTIN_PREFS_FILE, {}) or {}
        # Nuvio-linked builds must not inject Dex's bundled Kaptain set into
        # Movies/TV or Collections. It remains available through the explicit
        # Restore action for users who want it, but fresh/upgraded profiles
        # default to the Nuvio/custom source-of-truth only.
        return not bool((row if isinstance(row, dict) else {}).get('hidden', True))
    except Exception:
        return False


@_scope.bound
def set_builtin_collection_enabled(enabled=True):
    from .dexhub.safe_io import write_json as _safe_write_json
    _safe_write_json(BUILTIN_PREFS_FILE, {'hidden': not bool(enabled)})
    return bool(enabled)


@_scope.bound
def get_active_set_id():
    """Return the Collection used by Home/AF3 widgets.

    The bundled Dex Collection is a permanent fallback, not a forced overlay.
    Once a user imports/chooses a custom Kaptain/Nuvio JSON, that collection
    becomes the single active source for Movies Collections / TV Collections.
    """
    try:
        with open(ACTIVE_FILE, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        value = str((data or {}).get('set_id') or '').strip() if isinstance(data, dict) else str(data or '').strip()
        if value and not (value == BUILTIN_KAPTAIN_ID and not builtin_collection_enabled()):
            return value
    except Exception:
        pass
    if builtin_collection_enabled():
        return BUILTIN_KAPTAIN_ID
    rows = list(_read() or [])
    for row in rows:
        if str((row or {}).get('id') or '') == NUVIO_CLOUD_ID:
            return NUVIO_CLOUD_ID
    return str((rows[0] or {}).get('id') or '') if rows else ''


@_scope.bound
def set_active_set(set_id):
    set_id = str(set_id or '').strip()
    if not set_id:
        set_id = BUILTIN_KAPTAIN_ID if builtin_collection_enabled() else get_active_set_id()
    if set_id == BUILTIN_KAPTAIN_ID and not builtin_collection_enabled():
        raise ValueError('Dex Collection معطلة')
    if set_id != BUILTIN_KAPTAIN_ID and not any(str(r.get('id') or '') == set_id for r in _read()):
        raise ValueError('المجموعة غير موجودة')
    from .dexhub.safe_io import write_json as _safe_write_json
    _safe_write_json(ACTIVE_FILE, {'set_id': set_id})
    return set_id


@_scope.bound
def active_sets():
    """Return exactly one active collection for Home/AF3, with fallback."""
    active_id = get_active_set_id()
    row = get_set(active_id)
    if row:
        return [row]
    # A stale profile can reference a deleted custom file. Heal silently.
    if builtin_collection_enabled():
        try:
            set_active_set(BUILTIN_KAPTAIN_ID)
        except Exception:
            pass
        builtin = _builtin_kaptain_set()
        return [builtin] if builtin else list(_read()[:1])
    rows = list(_read() or [])
    return rows[:1]


def _builtin_kaptain_set():
    global _BUILTIN_SET_CACHE
    if _BUILTIN_SET_CACHE is not None:
        return _BUILTIN_SET_CACHE
    text = _bundled_kaptain_snapshot()
    if not text:
        return None
    try:
        entries = parse_collection_json(text)
    except Exception:
        return None
    if not entries:
        return None
    _BUILTIN_SET_CACHE = {
        'id': BUILTIN_KAPTAIN_ID,
        'name': 'Dex Collection',
        'source_url': BUILTIN_KAPTAIN_URL,
        'entries': entries,
        'builtin': True,
        'readonly': True,
    }
    return _BUILTIN_SET_CACHE


def _normalized_media_filter(value):
    value = str(value or '').strip().lower()
    if value in ('movie', 'movies', 'film', 'films'):
        return 'movie'
    if value in ('series', 'show', 'shows', 'tv', 'tvshow', 'anime'):
        return 'series'
    return ''


@_scope.bound
def source_matches_media(source, media_filter=''):
    """Cheap local media split used by Home/AF3 collection widgets.

    No provider/network lookup happens here; the source's normalized metadata
    decides whether it belongs to Movies or TV.
    """
    wanted = _normalized_media_filter(media_filter)
    if not wanted:
        return True
    src = source if isinstance(source, dict) else {}
    raw_mt = src.get('mediaType') or src.get('catalogType') or src.get('type') or ''
    # Nuvio Home order is authoritative. Some collection sources do not carry
    # an explicit media type; treating those as movie used to hide the card
    # from TV entirely. Unknown means compatible with both split views.
    if not str(raw_mt or '').strip():
        return True
    mt = _normalize_nuvio_media_type(raw_mt)
    actual = 'series' if mt in ('series', 'anime') else 'movie'
    return actual == wanted


@_scope.bound
def folder_supports_media(folder, media_filter=''):
    wanted = _normalized_media_filter(media_filter)
    if not wanted:
        return True
    if not isinstance(folder, dict):
        return False
    payload = folder.get('payload') or {}
    sources = payload.get('sources') or []
    return any(source_matches_media(src, wanted) for src in sources if isinstance(src, dict))


@_scope.bound
def entry_supports_media(entry, media_filter=''):
    wanted = _normalized_media_filter(media_filter)
    if not wanted:
        return True
    if not isinstance(entry, dict):
        return False
    kind = str(entry.get('kind') or '')
    payload = entry.get('payload') or {}
    if kind == 'nuvioGroup':
        return any(folder_supports_media(folder, wanted) for folder in payload.get('folders') or [])
    if kind == 'nuvioFolder':
        return folder_supports_media(entry, wanted)
    if kind == 'addonCatalog':
        return source_matches_media({'catalogType': payload.get('catalogType') or payload.get('type') or 'movie'}, wanted)
    if kind == 'multiCatalog':
        return any(source_matches_media(src, wanted) for src in payload.get('sources') or [] if isinstance(src, dict))
    if kind in ('traktList', 'mdblistList', 'localJsonCatalog'):
        mf = str(payload.get('mediaFilter') or payload.get('media_type') or '').strip().lower()
        if not mf or mf == 'auto':
            return True
        return _normalized_media_filter(mf) == wanted
    # Kodi routes can be arbitrary. Keep them visible in both views rather than
    # guessing and hiding a user's custom shortcut.
    return True


def _file_mtime():
    try:
        return (_scope.key(), os.path.getmtime(SETS_FILE))
    except Exception:
        return (_scope.key(), None)


def _read():
    global _CACHE_ROWS, _CACHE_MTIME
    mtime = _file_mtime()
    if _CACHE_ROWS is not None and mtime == _CACHE_MTIME:
        return list(_CACHE_ROWS)
    try:
        with open(SETS_FILE, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
            if isinstance(data, list):
                _CACHE_ROWS = data
                _CACHE_MTIME = mtime
                return list(data)
    except Exception:
        pass
    _CACHE_ROWS = []
    _CACHE_MTIME = mtime
    return []


def _write(rows):
    # v3.9.17: atomic write so a crash mid-save doesn't lose all custom
    # collection sets. Caller invariant unchanged — rows are persisted
    # then memory cache is updated.
    from .dexhub.safe_io import write_json as _safe_write_json
    global _CACHE_ROWS, _CACHE_MTIME
    _safe_write_json(SETS_FILE, rows)
    _CACHE_ROWS = list(rows)
    _CACHE_MTIME = _file_mtime()


def _write_fast(rows):
    """Fast atomic save for tiny collection edits such as delete.

    The normal writer is intentionally very defensive: it copies a .bak file,
    pretty-prints the whole JSON and fsyncs the temp file.  On Android/TV boxes
    that can make a simple context-menu delete feel like opening the whole
    collection again.  For confirmed local edits we still keep the important
    guarantee (write to temp then atomic replace), but write compact JSON and do
    not block on fsync/backup snapshot.
    """
    global _CACHE_ROWS, _CACHE_MTIME
    os.makedirs(os.path.dirname(SETS_FILE), exist_ok=True)
    tmp_path = '%s.tmp.%d.fast' % (SETS_FILE, os.getpid())
    try:
        with open(tmp_path, 'w', encoding='utf-8') as fh:
            json.dump(rows, fh, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp_path, SETS_FILE)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise
    _CACHE_ROWS = list(rows)
    _CACHE_MTIME = _file_mtime()


# ── parsing ──────────────────────────────────────────────────────────────────


@_scope.bound
def manifest_url_from_collections_url(url):
    """Return the sibling manifest.json URL for a Plexio/collection JSON URL.

    Plexio exposes both URLs side-by-side:
      /<installation>/<key>/collections.json
      /<installation>/<key>/manifest.json

    Dex Hub accepts collections.json for import, but provider registration and
    playback must use manifest.json. Query strings/fragments are preserved.
    """
    url = str(url or '').strip()
    if not url.lower().startswith(('http://', 'https://')):
        return ''
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        path = parsed.path or ''
        if path.lower().endswith('/collections.json'):
            new_path = path[:-len('collections.json')] + 'manifest.json'
            return urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment))
    except Exception:
        pass
    lower = url.lower()
    marker = '/collections.json'
    idx = lower.find(marker)
    if idx >= 0:
        return url[:idx] + '/manifest.json' + url[idx + len(marker):]
    return ''


@_scope.bound
def normalize_catalog_id_and_type(catalog_id, catalog_type='movie'):
    """Normalize Nuvio/Plexio collection catalog IDs.

    Plexio's collections.json may export catalogId as e.g.
    `movie::plex-col-1-XXXX` while its manifest catalog id is `plex-col-1-XXXX`.
    Kodi/Dex Hub must call /catalog/movie/plex-col-... rather than passing the
    prefixed id verbatim.
    """
    cid = str(catalog_id or '').strip()
    ctype = str(catalog_type or '').strip() or 'movie'
    if '::' in cid:
        head, tail = cid.split('::', 1)
        head_l = head.strip().lower()
        if head_l in ('movie', 'movies', 'series', 'show', 'tv', 'anime') and tail.strip():
            cid = tail.strip()
            if head_l in ('series', 'show', 'tv'):
                ctype = 'series'
            elif head_l == 'anime':
                ctype = 'anime'
            else:
                ctype = 'movie'
    ctype_l = ctype.lower()
    if ctype_l in ('tv', 'show', 'shows'):
        ctype = 'series'
    elif ctype_l in ('movies',):
        ctype = 'movie'
    elif ctype_l not in ('movie', 'series', 'anime'):
        ctype = ctype or 'movie'
    return cid, ctype


def _normalize_nuvio_media_type(value):
    value = str(value or 'movie').strip().lower()
    if value in ('tv', 'series', 'show', 'shows', 'tvshow'):
        return 'series'
    if value in ('anime',):
        return 'anime'
    return 'movie'


def _normalize_nuvio_source(src):
    """Normalize one Nuvio/Kaptain folder ``sources[]`` row.

    Nuvio native exports are deliberately provider-agnostic.  A folder can mix
    addon catalogs, TMDb discover/list/collection sources and numeric Trakt
    lists.  Keep those sources frozen in the imported collection so browsing a
    big pack does not need to rebuild or re-parse the original JSON on every
    click.
    """
    if not isinstance(src, dict):
        return None
    provider = str(src.get('provider') or '').strip().lower()
    addon_id = str(src.get('addonId') or src.get('addon_id') or '').strip()
    catalog_id = str(src.get('catalogId') or src.get('catalog_id') or '').strip()
    media_type = _normalize_nuvio_media_type(src.get('mediaType') or src.get('type') or src.get('catalogType') or 'movie')
    # v5.10.224: the source's title is its tab («New Movies»); Kaptain's
    # native packs name every source of a folder after the folder («Horror»)
    name = str(src.get('title') or src.get('name') or src.get('genre') or catalog_id or provider or 'Source').strip() or 'Source'
    genre = str(src.get('genre') or '').strip()

    # Stremio/AIO Metadata style catalog source.
    if provider == 'addon' or (addon_id and catalog_id):
        if not addon_id or not catalog_id:
            return None
        cid, ctype = normalize_catalog_id_and_type(catalog_id, media_type)
        return {
            'sourceKind': 'addonCatalog',
            'nuvio_raw': dict(src),
            'name': name,
            'addonId': addon_id,
            'catalogId': cid,
            'catalogType': ctype,
            'genre': genre,
            'extra': dict(src.get('extra') or {}) if isinstance(src.get('extra'), dict) else {},
        }

    # Nuvio native TMDb source.  Preserve filters verbatim; the TMDb adapter
    # translates camelCase/snake_case keys only when the folder is opened.
    if provider == 'tmdb':
        filters = dict(src.get('filters') or {}) if isinstance(src.get('filters'), dict) else {}
        return {
            'sourceKind': 'tmdb',
            'nuvio_raw': dict(src),
            'name': name,
            'mediaType': media_type,
            'tmdbSourceType': str(src.get('tmdbSourceType') or src.get('sourceType') or 'DISCOVER').strip().upper(),
            'tmdbId': str(src.get('tmdbId') or src.get('tmdb_id') or '').strip(),
            'sortBy': str(src.get('sortBy') or filters.get('sortBy') or filters.get('sort_by') or '').strip(),
            'sortHow': str(src.get('sortHow') or '').strip(),
            'filters': filters,
            'genre': genre,
        }

    # Kaptain Top Streaming Series and similar packs use Trakt numeric list IDs.
    if provider == 'trakt':
        list_id = str(src.get('traktListId') or src.get('listId') or src.get('list_id') or '').strip()
        if list_id:
            return {
                'sourceKind': 'traktIdList',
                'nuvio_raw': dict(src),
                'name': name,
                'mediaType': media_type,
                'traktListId': list_id,
                'sortBy': str(src.get('sortBy') or '').strip(),
                'sortHow': str(src.get('sortHow') or '').strip(),
                'genre': genre,
            }

    return None


def _normalize_entry(raw):
    """Return a clean minimal dict for one collection entry, or None if unusable.

    In addition to the historical Fusion/Plexio shapes, v5.4.14 understands
    Nuvio/Kaptain native folders whose real catalog definitions live in
    ``sources[]``.  These become ``nuvioFolder`` entries and stay lazy: only the
    chosen folder/source is resolved when the user opens it.
    """
    if not isinstance(raw, dict):
        return None

    # Nuvio/Kaptain native shape: sources[] may mix addon/TMDb/Trakt rows.
    native_sources = raw.get('sources') or []
    if isinstance(native_sources, list) and native_sources:
        cleaned_native = []
        for src in native_sources:
            row = _normalize_nuvio_source(src)
            if row:
                cleaned_native.append(row)
        if cleaned_native:
            tile_shape = str(raw.get('tileShape') or raw.get('tile_shape') or '').strip().lower()
            layout = 'Wide' if tile_shape in ('landscape', 'wide', 'banner') else 'Poster'
            cover = str(raw.get('coverImageUrl') or raw.get('backgroundImageURL') or raw.get('imageURL') or raw.get('imageUrl') or '').strip()
            hero = str(raw.get('heroBackdropUrl') or raw.get('backdropImageUrl') or '').strip()
            return {
                'id': str(raw.get('id') or uuid.uuid4().hex[:12]),
                'name': str(raw.get('title') or raw.get('name') or 'Folder').strip() or 'Folder',
                'layout': layout,
                'background': hero or cover,
                'poster': cover,
                'clearlogo': str(raw.get('titleLogoUrl') or raw.get('titleLogoURL') or raw.get('clearlogo') or raw.get('clearLogoUrl') or raw.get('logo') or '').strip(),
                'hide_title': bool(raw.get('hideTitle') or False),
                'kind': 'nuvioFolder',
                'payload': {
                    'sources': cleaned_native,
                    'coverEmoji': str(raw.get('coverEmoji') or '').strip(),
                    'focusGifUrl': str(raw.get('focusGifUrl') or '').strip(),
                    'focusGifEnabled': bool(raw.get('focusGifEnabled', True)),
                    'heroVideoUrl': str(raw.get('heroVideoUrl') or '').strip(),
                    'tileShape': str(raw.get('tileShape') or 'SQUARE').strip().upper(),
                },
                'open_mode': str(raw.get('open_mode') or raw.get('openMode') or 'normal').strip() or 'normal',
            }

    # Existing shape: catalogSources[] (list of {addonId, type, catalogId})
    cs = raw.get('catalogSources') or raw.get('catalog_sources') or []
    if isinstance(cs, list) and cs:
        cleaned = []
        for src in cs:
            if not isinstance(src, dict):
                continue
            addon_id = (src.get('addonId') or src.get('addon_id') or '').strip()
            catalog_id = (src.get('catalogId') or src.get('catalog_id') or '').strip()
            ctype = (src.get('type') or src.get('catalogType') or 'movie').strip()
            catalog_id, ctype = normalize_catalog_id_and_type(catalog_id, ctype)
            if addon_id and catalog_id:
                cleaned.append({'addonId': addon_id, 'catalogId': catalog_id, 'catalogType': ctype, 'genre': src.get('genre') or '', 'extra': src.get('extra') or {}})
        if cleaned:
            entry = {
                'id': str(raw.get('id') or uuid.uuid4().hex[:12]),
                'name': str(raw.get('title') or raw.get('name') or 'Folder').strip() or 'Folder',
                'layout': 'Wide' if (raw.get('tileShape') or '').upper() == 'LANDSCAPE' else 'Poster',
                'background': str(raw.get('heroBackdropUrl') or raw.get('coverImageUrl') or raw.get('focusGifUrl') or raw.get('backgroundImageURL') or '').strip(),
                'poster': str(raw.get('coverImageUrl') or '').strip(),
                'clearlogo': str(raw.get('clearlogo') or raw.get('clearLogoUrl') or raw.get('clearLogoURL') or raw.get('titleLogoUrl') or raw.get('titleLogoURL') or raw.get('titleLogo') or raw.get('logo') or raw.get('logoURL') or raw.get('logoUrl') or '').strip(),
                'hide_title': bool(raw.get('hideTitle') or False),
                'kind': 'multiCatalog',
                'payload': {
                    'sources': cleaned,
                    'coverEmoji': str(raw.get('coverEmoji') or '').strip(),
                    'focusGifUrl': str(raw.get('focusGifUrl') or '').strip(),
                    'focusGifEnabled': bool(raw.get('focusGifEnabled', True)),
                    'heroVideoUrl': str(raw.get('heroVideoUrl') or '').strip(),
                    'tileShape': str(raw.get('tileShape') or 'SQUARE').strip().upper(),
                },
                'open_mode': str(raw.get('open_mode') or raw.get('openMode') or 'skip').strip() or 'skip',
            }
            return entry

    # Old shape: dataSource / dataSources
    ds = raw.get('dataSource') or {}
    if not ds or not isinstance(ds, dict):
        sources = raw.get('dataSources') or []
        ds = sources[0] if sources and isinstance(sources[0], dict) else {}
    kind = (ds.get('kind') or '').strip()
    payload = ds.get('payload') or {}
    if not kind or not isinstance(payload, dict):
        return None
    payload = dict(payload)
    if kind == 'addonCatalog':
        cid, ctype = normalize_catalog_id_and_type(
            payload.get('catalogId') or payload.get('catalog_id') or '',
            payload.get('catalogType') or payload.get('type') or 'movie',
        )
        if cid:
            payload['catalogId'] = cid
        if ctype:
            payload['catalogType'] = ctype

    image_aspect = str(raw.get('imageAspect') or raw.get('image_aspect') or '').strip().lower()
    tile_shape = str(raw.get('tileShape') or raw.get('tile_shape') or '').strip().lower()
    raw_layout = str(raw.get('layout') or '').strip()
    if raw_layout:
        layout = raw_layout
    elif image_aspect in ('wide', 'landscape', 'banner', '16:9', '16x9') or tile_shape in ('landscape', 'wide', 'banner'):
        layout = 'Wide'
    else:
        layout = 'Poster'

    image_url = str(raw.get('backgroundImageURL') or raw.get('heroBackdropUrl') or raw.get('coverImageUrl') or raw.get('imageURL') or raw.get('imageUrl') or '').strip()
    poster_url = str(raw.get('poster') or raw.get('posterURL') or raw.get('posterUrl') or raw.get('coverImageUrl') or '').strip()
    background_url = image_url or poster_url

    return {
        'id': str(raw.get('id') or uuid.uuid4().hex[:12]),
        'name': str(raw.get('name') or raw.get('title') or 'Collection').strip() or 'Collection',
        'layout': layout,
        'background': background_url,
        'image_aspect': image_aspect,
        'poster': poster_url,
        'clearlogo': str(raw.get('clearlogo') or raw.get('clearLogoUrl') or raw.get('clearLogoURL') or raw.get('titleLogoUrl') or raw.get('titleLogoURL') or raw.get('titleLogo') or raw.get('logo') or raw.get('logoURL') or raw.get('logoUrl') or '').strip(),
        'hide_title': bool(raw.get('hideTitle') or False),
        'kind': kind,
        'payload': payload,
    }


def _normalize_nuvio_group(raw):
    """Preserve Nuvio's top-level collection -> folders hierarchy."""
    if not isinstance(raw, dict) or not isinstance(raw.get('folders'), list):
        return None
    folders = []
    for folder in raw.get('folders') or []:
        entry = _normalize_entry(folder)
        if entry:
            folders.append(entry)
    if not folders:
        return None
    tile_shape = str(raw.get('tileShape') or raw.get('tile_shape') or '').strip().lower()
    cover = str(raw.get('coverImageUrl') or raw.get('backgroundImageURL') or raw.get('imageURL') or raw.get('imageUrl') or '').strip()
    hero = str(raw.get('backdropImageUrl') or raw.get('backdropImageURL') or raw.get('heroBackdropUrl') or '').strip()
    return {
        'id': str(raw.get('id') or uuid.uuid4().hex[:12]),
        'name': str(raw.get('title') or raw.get('name') or 'Collection').strip() or 'Collection',
        'layout': 'Wide' if tile_shape in ('landscape', 'wide', 'banner') or cover else 'Square',
        'background': hero or cover,
        'poster': cover,
        'clearlogo': str(raw.get('titleLogoUrl') or raw.get('titleLogoURL') or raw.get('clearlogo') or raw.get('logo') or '').strip(),
        'hide_title': bool(raw.get('hideTitle') or False),
        'kind': 'nuvioGroup',
        'payload': {
            'folders': folders,
            # Preserve Nuvio presentation settings even when Kodi cannot map
            # every Android layout mode 1:1. They remain available to the UI
            # and prevent a cloud refresh from throwing away the source-of-
            # truth presentation state.
            'viewMode': str(raw.get('viewMode') or raw.get('view_mode') or 'FOLLOW_HOME').strip() or 'FOLLOW_HOME',
            'showAllTab': bool(raw.get('showAllTab', raw.get('show_all_tab', True))),
            'pinToTop': bool(raw.get('pinToTop', raw.get('pin_to_top', False))),
            'focusGlowEnabled': bool(raw.get('focusGlowEnabled', raw.get('focus_glow_enabled', True))),
        },
    }


def _is_nuvio_native_group(raw):
    """Recognize every current/legacy Nuvio Collection group shape.

    NuvioTV still supports legacy ``catalogSources[]`` alongside the newer
    provider-agnostic ``sources[]``. Treating catalogSources-only groups as a
    generic share export flattened away the top-level Collection ID; Nuvio Home
    then referenced that missing ID and the Collection appeared empty.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('folders'), list):
        return False
    for folder in raw.get('folders') or []:
        if not isinstance(folder, dict):
            continue
        if isinstance(folder.get('sources'), list) and folder.get('sources'):
            return True
        if isinstance(folder.get('catalogSources'), list) and folder.get('catalogSources'):
            return True
        if isinstance(folder.get('catalog_sources'), list) and folder.get('catalog_sources'):
            return True
    return False

def _expand_groups(data):
    """Walk top-level groups with `folders[]` and emit each folder as an entry.

    Supports the share-URL export format:
        [
          {"id": "...", "title": "خدمات البث", "folders": [
              {"id": "...", "title": "Netflix", "catalogSources": [...]},
              ...
          ]},
          ...
        ]
    """
    if not isinstance(data, list):
        return data
    expanded = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get('folders'), list):
            for folder in item['folders']:
                if isinstance(folder, dict):
                    # Carry the group title forward as a name prefix when the
                    # folder's own name is generic.
                    if folder.get('title') in (None, '', 'Folder', 'Collection'):
                        folder = dict(folder)
                        folder['title'] = item.get('title') or folder.get('title') or 'Folder'
                    expanded.append(folder)
        else:
            expanded.append(item)
    return expanded


def _flatten_fusion_widgets(data):
    """Flatten Fusion Widgets exports into normal collection entries.

    Fusion config files often look like:
        {
          "exportType": "fusionWidgets",
          "widgets": [
            {
              "title": "Latest Shows",
              "dataSource": {
                "kind": "collection",
                "payload": {"items": [ ... real entries ... ]}
              }
            }
          ]
        }

    Dex Hub expects the real entries directly. This helper unwraps the
    intermediate widget shell and preserves the widget title as a fallback
    label for generic child items.
    """
    def _copy_with_defaults(child, parent):
        if not isinstance(child, dict):
            return child
        row = dict(child)
        for key in ('backgroundImageURL', 'coverImageUrl', 'imageURL', 'imageUrl', 'poster', 'posterURL', 'posterUrl'):
            if not row.get(key) and parent.get(key):
                row[key] = parent.get(key)
                break
        if row.get('title') in (None, '', 'Folder', 'Collection') and parent.get('title'):
            row['title'] = parent.get('title')
        if row.get('name') in (None, '', 'Folder', 'Collection') and parent.get('name'):
            row['name'] = parent.get('name')
        return row

    def _unwrap(item):
        if not isinstance(item, dict):
            return [item]
        ds = item.get('dataSource') or {}
        payload = ds.get('payload') or {}
        kind = str(ds.get('kind') or '').strip().lower()
        items = payload.get('items') if isinstance(payload, dict) else None
        if kind == 'collection' and isinstance(items, list) and items:
            out = []
            for child in items:
                if isinstance(child, dict):
                    out.append(_copy_with_defaults(child, item))
            if out:
                return out
        return [item]

    if isinstance(data, dict):
        export_type = str(data.get('exportType') or data.get('type') or '').strip().lower()
        widgets = data.get('widgets') if isinstance(data.get('widgets'), list) else None
        if widgets and export_type in ('fusionwidgets', 'fusion_widgets', 'fusion-widget-export'):
            flattened = []
            for widget in widgets:
                flattened.extend(_unwrap(widget))
            return flattened
        return data

    if isinstance(data, list):
        flattened = []
        for item in data:
            flattened.extend(_unwrap(item))
        return flattened

    return data


@_scope.bound
def parse_collection_json(text):
    """Parse the raw text of a collection JSON into a list of normalized entries.

    Accepts:
      - Top-level array of entries (old format)
      - Top-level array of groups, each with `folders[]` (new share-URL format)
      - Top-level object with 'collections'/'widgets'/'entries' array
      - Fusion Widgets export files where real entries live under
        widgets[].dataSource.payload.items
    """
    try:
        data = json.loads(text)
    except Exception:
        try:
            data = json.loads(text.strip().lstrip('\ufeff'))
        except Exception:
            raise ValueError('JSON غير صالح')

    data = _flatten_fusion_widgets(data)

    if isinstance(data, dict):
        for key in ('collections', 'widgets', 'entries', 'items', 'folders'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]

    data = _flatten_fusion_widgets(data)

    if not isinstance(data, list):
        raise ValueError('الملف لا يحتوي على قائمة عناصر')

    out = []
    for raw in data:
        if _is_nuvio_native_group(raw):
            entry = _normalize_nuvio_group(raw)
            if entry:
                out.append(entry)
            continue
        # Historical Fusion/Plexio group exports stay flattened for backwards
        # compatibility; only native Nuvio sources[] packs keep hierarchy.
        expanded = _expand_groups([raw])
        for child in expanded:
            entry = _normalize_entry(child)
            if entry:
                out.append(entry)
    if not out:
        raise ValueError('لم يتم العثور على عناصر صالحة في الملف')
    return out


# ── addon auto-registration ──────────────────────────────────────────────────


def _provider_for_manifest(manifest_url):
    wanted = _normalize_manifest_url(manifest_url)
    for row in _store.list_providers():
        if _normalize_manifest_url(row.get('manifest_url')) == wanted:
            return row
    return None


def _transport_url_valid(url):
    """Syntactic transport validation only; never performs a DNS request."""
    try:
        raw = str(url or '').strip()
        if not raw or any(ch.isspace() for ch in raw):
            return False
        parsed = urlparse(raw)
        return parsed.scheme.lower() in ('http', 'https') and bool(parsed.hostname)
    except Exception:
        return False


_ADDON_SNAPSHOT = {'key': (), 'value': None}


def _stamp_of(path):
    try:
        st = os.stat(path)
        return (_scope.key(), st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _nuvio_addon_snapshot():
    """Return the canonical v2 Home add-on snapshot, then legacy fallback.

    v5.10.188: read again only when one of its two files changed (every
    provider lookup of a Home publish read and parsed the Home mirror)."""
    try:
        from .dexhub import nuvio_home_mirror as _home_mirror
        key = (_stamp_of(_home_mirror.MIRROR_PATH), _stamp_of(NUVIO_ADDON_URLS_FILE))
    except Exception:
        key = None
    if key is not None and key == _ADDON_SNAPSHOT['key'] and _ADDON_SNAPSHOT['value'] is not None:
        value = _ADDON_SNAPSHOT['value']
        return {'urls': list(value.get('urls') or []), 'authoritative': bool(value.get('authoritative'))} \
            if 'urls' in value else dict(value)
    value = _nuvio_addon_snapshot_read()
    if key is not None:
        _ADDON_SNAPSHOT.update(key=key, value=value)
    return value


def _nuvio_addon_snapshot_read():
    try:
        from .dexhub import nuvio_home_mirror as _home_mirror
        from .dexhub.safe_io import read_json as _read_json
        raw = _read_json(_home_mirror.MIRROR_PATH, {}) or {}
        if (isinstance(raw, dict) and int(raw.get('schema') or 0) >= 2 and
                raw.get('complete') is True and isinstance(raw.get('addons'), list)):
            return {
                'urls': [str((row or {}).get('manifest_url') or '')
                         for row in raw.get('addons') or [] if isinstance(row, dict)],
                'authoritative': True,
            }
    except Exception:
        pass
    try:
        from .dexhub.safe_io import read_json as _read_json
        data = _read_json(NUVIO_ADDON_URLS_FILE, {}) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _nuvio_preferred_manifest_urls():
    return set(_nuvio_preferred_manifest_rank())


_RANK_MEMO = {'key': None, 'rank': None}


def _nuvio_preferred_manifest_rank():
    """Map each enabled Nuvio transport to its exact account order.

    v5.10.213: worked out once per change of the snapshot (a Home publish
    asked it once per catalog: 0.8 s of a 1.1 s publish on an AM8 Pro)."""
    data = _nuvio_addon_snapshot()
    key = _ADDON_SNAPSHOT.get('key')
    if key and _RANK_MEMO['key'] == key and _RANK_MEMO['rank'] is not None:
        return _RANK_MEMO['rank']
    rank = _nuvio_preferred_manifest_rank_of(data)
    if key:
        _RANK_MEMO.update(key=key, rank=rank)
    return rank


def _nuvio_preferred_manifest_rank_of(data):
    rows = data.get('urls') if isinstance(data, dict) else []
    out = {}
    for raw in rows if isinstance(rows, list) else []:
        url = _normalize_manifest_url(raw).lower().rstrip('/')
        if _transport_url_valid(url) and url not in out:
            out[url] = len(out)
    return out


def _nuvio_addon_snapshot_authoritative():
    data = _nuvio_addon_snapshot()
    return bool(data.get('authoritative')) if isinstance(data, dict) else False


@_scope.bound
def provider_transport_valid(provider):
    if not isinstance(provider, dict):
        return False
    return _transport_url_valid(provider.get('manifest_url') or provider.get('base_url') or '')


_CANDIDATES_MEMO = {'key': None, 'rows': {}}


@_scope.bound
def find_provider_candidates_by_addon_id(addon_id, exclude_provider_id=''):
    """The matching providers (v5.10.213: remembered until the providers or
    the Nuvio snapshot change; a publish looked each one up per catalog)."""
    try:
        from .dexhub import store as _st
        key = (_stamp_of(_st.PROVIDERS_PATH), _ADDON_SNAPSHOT.get('key'))
        _nuvio_addon_snapshot()             # refreshes the snapshot key
        key = (_stamp_of(_st.PROVIDERS_PATH), _ADDON_SNAPSHOT.get('key'))
    except Exception:
        key = None
    memo_key = (str(addon_id or '').strip().lower(), str(exclude_provider_id or '').strip())
    if key is not None and key[0] is not None and _CANDIDATES_MEMO['key'] == key:
        hit = _CANDIDATES_MEMO['rows'].get(memo_key)
        if hit is not None:
            return list(hit)
    rows = _find_provider_candidates_by_addon_id(addon_id, exclude_provider_id)
    if key is not None and key[0] is not None:
        if _CANDIDATES_MEMO['key'] != key:
            _CANDIDATES_MEMO.update(key=key, rows={})
        _CANDIDATES_MEMO['rows'][memo_key] = list(rows)
    return rows


def _find_provider_candidates_by_addon_id(addon_id, exclude_provider_id=''):
    """Return matching installed providers, preferring Nuvio's current URL.

    A profile can contain multiple historical transports for the same
    manifest.id.  Nuvio's current enabled add-on URL wins, then any healthy
    syntactic HTTP(S) transport.  Invalid rows are excluded entirely.
    """
    if not addon_id:
        return []
    wanted = str(addon_id).strip().lower()
    exclude_provider_id = str(exclude_provider_id or '').strip()
    wants_plexio = 'plexio' in wanted or wanted in ('com.stremio.plexio', 'com.stremio.plexbridge', 'plexbridge') or 'plexbridge' in wanted
    wants_streambridge = wanted.startswith('streambridge') or wanted in ('com.stremio.streambridge', 'com.stremio.dexbridge', 'dexbridge') or 'streambridge' in wanted or 'dexbridge' in wanted or wanted.startswith('sb.')
    preferred_rank = _nuvio_preferred_manifest_rank()
    matches = []
    for idx, row in enumerate(_store.list_providers() or []):
        if str(row.get('id') or '') == exclude_provider_id:
            continue
        manifest = row.get('manifest') or {}
        mid = str(manifest.get('id') or '').strip().lower()
        manifest_url = str(row.get('manifest_url') or '').strip()
        manifest_url_l = _normalize_manifest_url(manifest_url).lower().rstrip('/')
        name = str(row.get('name') or manifest.get('name') or '').strip().lower()
        matched = bool(mid and mid == wanted)
        if not matched and wants_plexio:
            matched = (mid in ('com.stremio.plexbridge', 'com.stremio.plexio') or 'plexio' in manifest_url_l or 'plexio' in name or 'plexbridge' in name)
        if not matched and wants_streambridge:
            matched = (mid in ('com.stremio.streambridge', 'com.stremio.dexbridge') or 'sb.dexworld.cc' in manifest_url_l or 'streambridge' in manifest_url_l or 'streambridge' in name or 'dexbridge' in name or 'emby' in name)
        if not matched or not provider_transport_valid(row):
            continue
        # Lower score wins. Current Nuvio transport is authoritative.
        # Nuvio de-duplicates matching catalog keys in enabled-addon order.
        # Preserve that order when two configured transports expose the same
        # manifest.id; store insertion order is unrelated and can be stale.
        score = ((0, preferred_rank[manifest_url_l], idx)
                 if manifest_url_l in preferred_rank else (1, idx, idx))
        matches.append((score, row))
    matches.sort(key=lambda x: x[0])
    return [row for _score, row in matches]


@_scope.bound
def find_provider_by_addon_id(addon_id, exclude_provider_id=''):
    rows = find_provider_candidates_by_addon_id(addon_id, exclude_provider_id=exclude_provider_id)
    return rows[0] if rows else None


@_scope.bound
def find_nuvio_provider_by_addon_id(addon_id, exclude_provider_id=''):
    """Resolve a provider for a Nuvio source without legacy guessed URLs.

    If Nuvio supplied an enabled URL list, only a provider whose transport is
    in that list is authoritative.  This prevents a stale hard-coded/previous
    hostname from winning merely because it shares the same manifest.id.
    """
    rows = find_provider_candidates_by_addon_id(addon_id, exclude_provider_id=exclude_provider_id)
    if not rows:
        return None
    preferred = _nuvio_preferred_manifest_urls()
    if not preferred:
        # An explicitly empty current account list must not fall back to a
        # stale historical Dex provider. Older cache files did not carry the
        # authority marker, so they retain the backward-compatible fallback.
        return None if _nuvio_addon_snapshot_authoritative() else rows[0]
    for row in rows:
        url = _normalize_manifest_url(row.get('manifest_url') or '').lower().rstrip('/')
        if url in preferred:
            return row
    # The account knows its current enabled transports and none of the matching
    # installed rows is one of them: treat this as stale instead of calling it.
    return None


def _normalize_manifest_url(url):
    from .dexhub.links import manifest_link     # v5.10.143: stremio:// links
    url = manifest_link(url)
    if url.lower().endswith('/manifest.json/'):
        url = url[:-1]
    return url


@_scope.bound
def known_manifest_for_addon_id(addon_id):
    addon_id = str(addon_id or '').strip().lower()
    if not addon_id:
        return ''
    if addon_id in KNOWN_ADDON_MANIFESTS:
        return _normalize_manifest_url(KNOWN_ADDON_MANIFESTS.get(addon_id, ''))
    # Some Nuvio/Fusion exports use generated Plexio/StreamBridge ids or
    # URL-ish ids. They require the user's configured manifest, so do not map
    # them to a bundled private URL.
    return ''


@_scope.bound
def addon_requires_configuration(addon_id):
    addon_id = str(addon_id or '').strip().lower()
    if addon_id in CONFIG_REQUIRED_ADDONS:
        return True
    return (
        'plexio' in addon_id or
        'plexbridge' in addon_id or
        'streambridge' in addon_id or
        'dexbridge' in addon_id or
        addon_id.startswith('sb.')
    )


@_scope.bound
def resolve_or_register_addon(addon_id, manifest_resolver=None, share_url=''):
    """Return a provider row for a Stremio addon id/manifest URL when possible.

    Priority:
      1. already registered provider whose manifest.id matches addon_id
      2. addon_id itself if it is a manifest URL
      3. built-in known manifest map for public catalog addons
      4. optional UI resolver callback (for Plexio/configured/private addons)
    """
    addon_id = str(addon_id or '').strip()
    if not addon_id:
        return None
    existing = find_provider_by_addon_id(addon_id)
    if existing:
        return existing
    if addon_id.startswith(('http://', 'https://')):
        try:
            return ensure_addon_registered(addon_id)
        except Exception:
            return None
    manifest_url = known_manifest_for_addon_id(addon_id)
    if manifest_url:
        try:
            return ensure_addon_registered(manifest_url)
        except Exception:
            pass
    # If no configured/known URL exists, configured-only addons should not fall
    # through to a public empty shell manifest. Let the caller ask the user.
    if addon_requires_configuration(addon_id) and not manifest_resolver:
        return None
    if manifest_resolver:
        try:
            resolved = manifest_resolver(addon_id, share_url)
            if resolved:
                return ensure_addon_registered(resolved)
        except Exception:
            return None
    return None


@_scope.bound
def ensure_addon_registered(manifest_url, refresh=False, display_name=''):
    """Make sure the given manifest is in the provider list. Returns the provider row.

    If a matching provider already exists, returns it unless ``refresh`` is
    requested. Nuvio refreshes enabled manifests before rebuilding Home; its
    exact pull path uses the same behavior so catalog names cannot remain from
    an older Dex snapshot.
    """
    manifest_url = _normalize_manifest_url(manifest_url)
    existing = _provider_for_manifest(manifest_url)
    if existing and not refresh:
        return existing
    from .client import validate_manifest
    manifest = validate_manifest(manifest_url, force_refresh=bool(refresh))
    name = str(display_name or manifest.get('name') or
               (existing or {}).get('name') or 'Addon').strip() or 'Addon'
    return _store.add_provider(name, manifest_url, manifest)


# ── CRUD for saved sets ──────────────────────────────────────────────────────


@_scope.bound
def list_sets():
    """Return user/cloud collections plus the optional bundled Dex set.

    Older 5.4.14/5.4.15 profiles may already contain a manually imported copy
    of the Kaptain builder URL. Suppress that duplicate so the Home/AF3 widgets
    never show the same collection twice.
    """
    rows = list(_read() or [])
    if not builtin_collection_enabled():
        return rows
    builtin = _builtin_kaptain_set()
    if not builtin:
        return rows
    def _is_duplicate(row):
        src = str((row or {}).get('source_url') or '').strip().lower().rstrip('/')
        return src == BUILTIN_KAPTAIN_URL.lower().rstrip('/') or str((row or {}).get('id') or '') == BUILTIN_KAPTAIN_ID
    return [builtin] + [row for row in rows if not _is_duplicate(row)]


@_scope.bound
def get_set(set_id):
    if _is_builtin_id(set_id):
        # v5.10.195: asked for by its id (a Dex Community row) the bundled
        # collection is always there; whether it leads the Home is
        # active_sets()' business
        return _builtin_kaptain_set()
    for row in _read():
        if row.get('id') == set_id:
            return row
    return None


@_scope.bound
def collect_referenced_addon_ids(entries):
    """Return unique Stremio addon IDs referenced anywhere in a collection.

    v5.4.14 walks Nuvio groups/folders recursively so AIO Metadata catalogs are
    pre-resolved once during import instead of prompting later while browsing.
    """
    seen = []
    seen_set = set()

    def _add(aid):
        aid = str(aid or '').strip()
        if aid and aid not in seen_set:
            seen_set.add(aid)
            seen.append(aid)

    def _walk(entry):
        if not isinstance(entry, dict):
            return
        kind = entry.get('kind')
        payload = entry.get('payload') or {}
        if kind == 'addonCatalog':
            _add(payload.get('addonId'))
        elif kind == 'multiCatalog':
            for src in payload.get('sources') or []:
                if isinstance(src, dict):
                    _add(src.get('addonId'))
        elif kind == 'nuvioFolder':
            for src in payload.get('sources') or []:
                if isinstance(src, dict) and src.get('sourceKind') == 'addonCatalog':
                    _add(src.get('addonId'))
        elif kind == 'nuvioGroup':
            for child in payload.get('folders') or []:
                _walk(child)

    for entry in entries or []:
        _walk(entry)
    return seen


@_scope.bound
def derive_addon_manifest_url(share_url, addon_id):
    """Return a useful manifest hint for collection imports.

    Plexio/DexWorld collection URLs live next to the configured manifest, so
    /collections.json can be promoted to /manifest.json automatically. For
    Stremiolab/Nuvio collection hosts we still return the host prefix as a
    manual hint because their share token does not expose a manifest URL.
    """
    sibling = manifest_url_from_collections_url(share_url)
    if sibling:
        return sibling
    if not share_url:
        return ''
    try:
        from urllib.parse import urlparse
        parsed = urlparse(share_url)
        if parsed.netloc and 'collections' in parsed.netloc.lower():
            return '%s://%s/' % (parsed.scheme or 'https', parsed.netloc)
    except Exception:
        pass
    return ''


@_scope.bound
def add_set_from_url(url, name=None, manifest_resolver=None):
    """Fetch a collection JSON, parse it, and persist it.

    For each unique addonId referenced by the entries, attempt to register
    the addon. Plexio/DexWorld collections.json URLs are also registered as a
    normal provider by swapping /collections.json -> /manifest.json, so opening
    the imported collection can play immediately without asking for the same
    manifest URL again.

    v3.9.60: defensive — addon-resolution failures no longer abort the
    whole import. Entries are saved regardless; addon resolution is a
    convenience for catalog entries that need a provider, but trakt/
    mdblist/kodi-route entries don't depend on it, and saving them
    even when some resolution step fails gives the user something
    usable instead of nothing.
    """
    url = (url or '').strip()
    if not url:
        raise ValueError('رابط فارغ')
    if url.lower().rstrip('/') == BUILTIN_KAPTAIN_URL.lower().rstrip('/'):
        builtin = _builtin_kaptain_set()
        if builtin:
            return builtin
    text = fetch_collection_text(url)
    entries = parse_collection_json(text)

    # Log a summary so kodi.log shows exactly what got parsed and saved.
    try:
        import xbmc as _xbmc
        kinds = {}
        for e in entries:
            k = e.get('kind') or '?'
            kinds[k] = kinds.get(k, 0) + 1
        _xbmc.log('[DexHub] add_set_from_url parsed %d entries: %s' % (
            len(entries), ', '.join('%s=%d' % (k, v) for k, v in kinds.items())),
            _xbmc.LOGINFO)
    except Exception:
        pass

    sibling_manifest = manifest_url_from_collections_url(url)
    if sibling_manifest:
        try:
            ensure_addon_registered(sibling_manifest)
        except Exception:
            # Keep collection import usable even if the source is temporarily
            # unreachable; individual entries can still resolve through their
            # explicit addonId or a later refresh.
            pass

    def _resolver(addon_id, share_url):
        if sibling_manifest:
            try:
                if addon_requires_configuration(addon_id) or 'plex' in str(addon_id or '').lower():
                    return sibling_manifest
            except Exception:
                return sibling_manifest
        if manifest_resolver:
            return manifest_resolver(addon_id, share_url)
        return ''

    # Collect all referenced addonIds from any kind of entry. Trakt/
    # MDBList/Kodi-route entries don't reference any addonId so this
    # returns an empty list for the user's Fusion config — no resolver
    # prompts and no auxiliary failures can block the save.
    try:
        referenced = collect_referenced_addon_ids(entries)
    except Exception:
        referenced = []

    seen_addons = {}
    for addon_id in referenced:
        if addon_id in seen_addons:
            continue
        try:
            seen_addons[addon_id] = resolve_or_register_addon(
                addon_id,
                manifest_resolver=_resolver,
                share_url=url,
            )
        except Exception as exc:
            # v3.9.60: a failed addon registration must NOT abort the
            # whole import. Log it and continue — the entry is still
            # persisted and will work if the addon gets installed later.
            try:
                import xbmc as _xbmc
                _xbmc.log('[DexHub] addon resolve failed for %s: %s — continuing' % (addon_id, exc), _xbmc.LOGWARNING)
            except Exception:
                pass

    set_row = {
        'id': hashlib.sha1(url.encode('utf-8')).hexdigest()[:12],
        'name': (name or _derive_set_name(url, entries)).strip(),
        'source_url': url,
        'entries': entries,
    }

    rows = _read()
    rows = [r for r in rows if r.get('id') != set_row['id']]
    rows.append(set_row)
    _write(rows)
    return set_row

@_scope.bound
def rename_set(set_id, new_name):
    if _is_builtin_id(set_id):
        raise ValueError('المجموعة الأساسية جزء من Dex Hub ولا يمكن إعادة تسميتها')
    new_name = (new_name or '').strip()
    if not new_name:
        raise ValueError('الاسم فارغ')
    rows = _read()
    changed = False
    for r in rows:
        if r.get('id') == set_id:
            r['name'] = new_name
            changed = True
            break
    if not changed:
        raise ValueError('المجموعة غير موجودة')
    _write(rows)


@_scope.bound
def update_entry(set_id, entry_id, updates):
    if _is_builtin_id(set_id):
        raise ValueError('المجموعة الأساسية للقراءة فقط')
    """Update fields of a single entry inside a set.

    `updates` is a dict that may include any of: name, background, layout,
    hide_title, or keys inside payload (prefixed with 'payload.') like
    'payload.username', 'payload.listSlug', 'payload.addonId'.
    """
    rows = _read()
    target_set = None
    for r in rows:
        if r.get('id') == set_id:
            target_set = r
            break
    if not target_set:
        raise ValueError('المجموعة غير موجودة')
    target_entry = None
    for e in (target_set.get('entries') or []):
        if e.get('id') == entry_id:
            target_entry = e
            break
    if not target_entry:
        raise ValueError('العنصر غير موجود')
    for k, v in (updates or {}).items():
        if k.startswith('payload.'):
            payload = target_entry.setdefault('payload', {})
            payload[k.split('.', 1)[1]] = v
        else:
            target_entry[k] = v
    _write(rows)
    return target_entry


@_scope.bound
def remove_entry(set_id, entry_id):
    if _is_builtin_id(set_id):
        raise ValueError('لا يمكن حذف عناصر المجموعة الأساسية')
    rows = _read()
    for r in rows:
        if r.get('id') == set_id:
            before = len(r.get('entries') or [])
            r['entries'] = [e for e in (r.get('entries') or []) if e.get('id') != entry_id]
            if len(r['entries']) == before:
                raise ValueError('العنصر غير موجود')
            # Deleting one entry should be instant from the context menu.  Use
            # the compact atomic writer instead of the full backup+fsync path.
            _write_fast(rows)
            return True
    raise ValueError('المجموعة غير موجودة')


@_scope.bound
def add_custom_entry(set_id, name, kind, payload, background='', open_mode='normal'):
    if _is_builtin_id(set_id):
        raise ValueError('أضف العناصر إلى مجموعة مخصصة؛ المجموعة الأساسية للقراءة فقط')
    """Manually add a new entry to a set (for users who want to build
    collections by hand instead of importing JSON)."""
    import uuid as _uuid
    rows = _read()
    for r in rows:
        if r.get('id') == set_id:
            entry = {
                'id': _uuid.uuid4().hex[:12],
                'name': (name or 'Collection').strip(),
                'layout': 'Poster',
                'background': (background or '').strip(),
                'hide_title': False,
                'open_mode': (open_mode or 'normal').strip() or 'normal',
                'kind': kind,
                'payload': dict(payload or {}),
            }
            r.setdefault('entries', []).append(entry)
            _write(rows)
            return entry
    raise ValueError('المجموعة غير موجودة')


@_scope.bound
def create_empty_set(name):
    """Create a new empty set the user can populate manually."""
    import uuid as _uuid, time as _t
    rows = _read()
    new_id = _uuid.uuid4().hex[:12]
    while any(r.get('id') == new_id for r in rows):
        new_id = _uuid.uuid4().hex[:12]
    set_row = {
        'id': new_id,
        'name': (name or 'مجموعة جديدة').strip() or 'مجموعة جديدة',
        'source_url': '',
        'entries': [],
        'created_at': int(_t.time()),
    }
    rows.append(set_row)
    _write(rows)
    return set_row


@_scope.bound
def add_set_from_text(text, source_label='مُدخل يدويًا', manifest_resolver=None):
    """Parse raw JSON text (paste) and persist as a new set.

    Mirrors `add_set_from_url`: collects unique addonIds across all entry
    kinds, auto-registers what we can, and falls back to `manifest_resolver`
    (caller-provided UI prompt) for missing ones.
    """
    entries = parse_collection_json(text)

    referenced = collect_referenced_addon_ids(entries)
    seen_addons = {}
    for addon_id in referenced:
        if addon_id in seen_addons:
            continue
        seen_addons[addon_id] = resolve_or_register_addon(
            addon_id,
            manifest_resolver=manifest_resolver,
            share_url='',
        )

    set_row = {
        'id': hashlib.sha1(text.encode('utf-8')).hexdigest()[:12],
        'name': source_label or 'Collection Set',
        'source_url': '',
        'entries': entries,
    }
    rows = _read()
    rows = [r for r in rows if r.get('id') != set_row['id']]
    rows.append(set_row)
    _write(rows)
    return set_row


@_scope.bound
def remove_set(set_id):
    if _is_builtin_id(set_id):
        set_builtin_collection_enabled(False)
        return
    was_active = (get_active_set_id() == str(set_id or ''))
    rows = [r for r in _read() if r.get('id') != set_id]
    _write(rows)
    if was_active:
        try:
            fallback = get_active_set_id()
            if fallback:
                set_active_set(fallback)
        except Exception:
            pass


@_scope.bound
def refresh_set(set_id, manifest_resolver=None):
    if _is_builtin_id(set_id):
        return _builtin_kaptain_set()
    row = get_set(set_id)
    if not row or not row.get('source_url'):
        raise ValueError('لا يمكن تحديث مجموعة بدون رابط مصدر')
    return add_set_from_url(row['source_url'], name=row.get('name') or None, manifest_resolver=manifest_resolver)


# ── helpers ──────────────────────────────────────────────────────────────────


def _decode_share_token(token):
    """Decode a stremiolabar.com /api/share/<token> payload.

    Token is URL-safe base64 of gzip-compressed JSON. Returns the decoded
    JSON text on success, or None on any decoding failure (caller falls
    back to a normal HTTP fetch).
    """
    if not token:
        return None
    import base64 as _b64, gzip as _gzip
    try:
        # URL-safe base64 (uses - and _ instead of + and /). Pad to multiple of 4.
        padded = token + ('=' * ((4 - len(token) % 4) % 4))
        try:
            raw = _b64.urlsafe_b64decode(padded)
        except Exception:
            # Fallback: standard b64 with manual char swap.
            fixed = token.replace('-', '+').replace('_', '/')
            fixed += '=' * ((4 - len(fixed) % 4) % 4)
            raw = _b64.b64decode(fixed)
        # Try gzip decompress (most common case).
        try:
            decompressed = _gzip.decompress(raw)
        except Exception:
            # Token may be plain base64 of JSON without gzip.
            decompressed = raw
        return decompressed.decode('utf-8', 'replace')
    except Exception as exc:
        import xbmc
        xbmc.log('[DexHub] share token decode failed: %s' % exc, xbmc.LOGDEBUG)
        return None


def _bundled_kaptain_snapshot():
    """Current known-good Kaptain/Nuvio snapshot bundled for the builder URL."""
    try:
        from . import bundled_data as _bundled_data
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, 'resources', 'data', 'kaptain_native_2026-10-09.json')
        # Reads the plain .json in a source tree and the .json.gz produced by
        # the release build, with no caller-visible difference.
        return _bundled_data.read_text(path)
    except Exception:
        return ''


def _discover_json_links(page_url, html_text):
    """Return a tiny, ordered list of JSON-like links found in an HTML page."""
    import re as _re
    try:
        from urllib.parse import urljoin as _urljoin
    except Exception:
        return []
    html_text = str(html_text or '')
    raw = []
    patterns = (
        r"(?:href|src|data-[\w-]+)\s*=\s*['\"]([^'\"]+)['\"]",
        r"https?://[^\s'\"<>]+",
    )
    for pattern in patterns:
        for value in _re.findall(pattern, html_text, _re.I):
            value = str(value or '').strip().replace('&amp;', '&')
            low = value.lower().split('?', 1)[0]
            if low.endswith('.json') or '/raw/' in low or 'gist.githubusercontent.com/' in low:
                raw.append(_urljoin(page_url, value))
    uniq = []
    for value in sorted(raw, key=lambda x: (0 if any(k in x.lower() for k in ('collection','nuvio','profile')) else 1, len(x))):
        if value not in uniq:
            uniq.append(value)
        if len(uniq) >= 5:
            break
    return uniq


@_scope.bound
def fetch_collection_text(url):
    """Fetch JSON/share links and compatible collection web pages.

    Kaptain's public page is a client-side builder rather than a deterministic
    JSON endpoint. Pasting that exact URL therefore opens the current known-good
    snapshot bundled with Dex Hub 5.4.14; direct exported JSON URLs remain live.
    """
    url = str(url or '').strip()
    low_url = url.lower().rstrip('/')
    # The public Kaptain URL is a builder, so do not waste a network request or
    # parse megabytes of HTML on Kodi. The direct page shortcut is instant.
    if low_url == 'https://imkaptain.github.io/kaptain-collection':
        bundled = _bundled_kaptain_snapshot()
        if bundled:
            return bundled

    text = _fetch_raw_text(url)
    try:
        parse_collection_json(text)
        return text
    except Exception:
        pass

    head = str(text or '')[:1000].lower()
    if '<html' in head or '<!doctype' in head or '<script' in head:
        for candidate in _discover_json_links(url, text):
            try:
                payload = _fetch_raw_text(candidate)
                parse_collection_json(payload)
                return payload
            except Exception:
                continue
    raise ValueError('الرابط ليس JSON Collection صالحاً ولا يحتوي على ملف JSON قابل للاكتشاف')


@_scope.bound
def fetch_raw_text(url):
    """Public collection fetcher: direct JSON/share URL or compatible page."""
    return fetch_collection_text(url)


def _fetch_raw_text(url):
    """Fetch raw text; supports inline share-token decoding and normal HTTP.

    v3.9.60: hardened with multiple fallbacks. If the standard urllib
    request fails for network or SSL reasons, we retry with:
      1. SSL verification disabled (some CoreELEC builds ship with a
         truncated CA store that rejects valid Let's Encrypt and
         Cloudflare R2 certs)
      2. urllib3 if available
      3. requests if available
    Each fallback is logged so kodi.log shows exactly which path
    succeeded — useful for diagnosing future issues.
    """
    import gzip as _gzip, io as _io, ssl as _ssl
    from urllib.request import Request, urlopen

    # Inline share token: most stremiolabar.com share URLs encode the
    # entire collection in the URL itself — no server call needed.
    if '/api/share/' in (url or ''):
        token = url.rsplit('/api/share/', 1)[-1].split('?', 1)[0].split('#', 1)[0]
        decoded = _decode_share_token(token)
        if decoded:
            return decoded
        # Fall through to HTTP if decode fails (server may still return JSON).

    # v3.9.62: bug fix — ADDON was never imported in this module, so
    # the User-Agent line raised NameError as soon as a URL was fetched.
    # The bug existed in the v3.9.58 baseline too but was masked by the
    # generic error handling. Resolve it now via a local defensive lookup
    # that survives if xbmcaddon isn't available.
    try:
        import xbmcaddon as _xa
        _ver = _xa.Addon().getAddonInfo('version') or '3.8.9'
    except Exception:
        _ver = '3.8.9'

    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip',
        'User-Agent': 'DexHub/%s (Kodi)' % _ver,
    }

    def _read_body(resp):
        body = resp.read()
        enc = (resp.headers.get('Content-Encoding') or '').lower()
        if 'gzip' in enc:
            try:
                body = _gzip.GzipFile(fileobj=_io.BytesIO(body)).read()
            except Exception:
                pass
        return body.decode('utf-8', 'replace')

    last_err = None
    # Attempt 1: standard urlopen with default SSL verification.
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=20) as resp:
            return _read_body(resp)
    except Exception as exc:
        last_err = exc
        try:
            import xbmc as _xbmc
            _xbmc.log('[DexHub] fetch_raw_text default fetch failed: %s — trying unverified SSL' % exc, _xbmc.LOGINFO)
        except Exception:
            pass

    # Attempt 2: unverified SSL context — covers stale CA stores.
    try:
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        req = Request(url, headers=headers)
        with urlopen(req, timeout=20, context=ctx) as resp:
            try:
                import xbmc as _xbmc
                _xbmc.log('[DexHub] fetch_raw_text succeeded with unverified SSL', _xbmc.LOGINFO)
            except Exception:
                pass
            return _read_body(resp)
    except Exception as exc:
        last_err = exc
        try:
            import xbmc as _xbmc
            _xbmc.log('[DexHub] fetch_raw_text unverified SSL failed: %s' % exc, _xbmc.LOGINFO)
        except Exception:
            pass

    # Attempt 3: third-party requests library if available.
    try:
        import requests as _requests  # type: ignore
        # --- dexhub-401-patch --- verify TLS; this is a public https endpoint
        resp = _requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        try:
            import xbmc as _xbmc
            _xbmc.log('[DexHub] fetch_raw_text succeeded via requests library', _xbmc.LOGINFO)
        except Exception:
            pass
        return resp.text
    except ImportError:
        pass
    except Exception as exc:
        last_err = exc

    # All attempts failed — raise the most recent error with context.
    raise last_err if last_err else IOError('Unknown fetch failure')


def _derive_set_name(url, entries):
    try:
        tail = url.rsplit('/', 1)[-1]
        if tail:
            base = tail.rsplit('.', 1)[0]
            if base:
                return base.replace('-', ' ').replace('_', ' ').title()
    except Exception:
        pass
    if entries:
        return '%d collection%s' % (len(entries), 's' if len(entries) > 1 else '')
    return 'Collection Set'


def _native_nuvio_groups(rows):
    """Return raw Nuvio collection groups (the account/API format).

    Nuvio stores a list of collection objects with ``folders[]``.  Dex Hub's
    portable backup format instead stores sets with ``entries[]``.  Keep the
    two shapes separate so a cloud pull is never silently discarded by the
    backup importer.
    """
    out = []
    for row in (rows or []):
        if isinstance(row, dict) and isinstance(row.get('folders'), list):
            out.append(row)
    return out


@_scope.bound
def upsert_nuvio_cloud(rows, activate=False, replace_empty=False):
    """Persist raw Nuvio account collections under one stable set ID.

    Returns the number of top-level Nuvio collections applied. A malformed
    pull is a no-op. A caller that completed an authoritative RPC may pass
    ``replace_empty=True`` to remove a stale ``nuvio-cloud`` projection when
    the user intentionally has no Collections. User-imported sets are never
    touched. A user rename of a populated cloud set survives refreshes.
    """
    native = _native_nuvio_groups(rows)
    if not native:
        if replace_empty and isinstance(rows, list) and not rows:
            current = _read()
            merged = [r for r in current if str((r or {}).get('id') or '') != NUVIO_CLOUD_ID]
            if len(merged) != len(current):
                _write(merged)
        return 0
    # Parse through the exact same Nuvio/Kaptain normalizer used by file import
    # so browsing behaviour remains identical regardless of where data came from.
    entries = parse_collection_json(json.dumps(native, ensure_ascii=False))
    if not entries:
        return 0
    current = _read()
    existing = next((r for r in current if str(r.get('id') or '') == NUVIO_CLOUD_ID), None)
    name = str((existing or {}).get('name') or 'Nuvio Cloud').strip() or 'Nuvio Cloud'
    set_row = {
        'id': NUVIO_CLOUD_ID,
        'name': name,
        'source_url': NUVIO_CLOUD_SOURCE,
        'entries': entries,
        # Preserve the authoritative account shape for safe explicit upload.
        # Normalized ``entries`` are for Kodi rendering only and are not pushed.
        'nuvio_raw': native,
        'cloud': 'nuvio',
    }
    merged = [r for r in current if str(r.get('id') or '') != NUVIO_CLOUD_ID]
    # Keep the cloud row in the same relative position when it already exists.
    if existing:
        pos = next((i for i, r in enumerate(current) if str(r.get('id') or '') == NUVIO_CLOUD_ID), len(merged))
        merged.insert(min(pos, len(merged)), set_row)
    else:
        merged.append(set_row)
    _write(merged)
    if activate:
        set_active_set(NUVIO_CLOUD_ID)
    return len(native)


@_scope.bound
def import_synced_collections(rows, activate_cloud=False):
    """Route mixed sync payloads by their actual schema.

    * Nuvio-native ``folders[]`` groups update the stable ``nuvio-cloud`` set.
    * Dex Hub backup rows with ``entries[]`` continue through ``import_sets``.
    This preserves backwards compatibility while fixing the old silent +0 pull.
    """
    if not isinstance(rows, list):
        return {'cloud': 0, 'backups': 0, 'total': 0}
    native = _native_nuvio_groups(rows)
    backups = [r for r in rows if isinstance(r, dict) and isinstance(r.get('entries'), list)]
    cloud_count = upsert_nuvio_cloud(native, activate=activate_cloud) if native else 0
    backup_count = import_sets(backups) if backups else 0
    return {
        'cloud': int(cloud_count or 0),
        'backups': int(backup_count or 0),
        'total': int(cloud_count or 0) + int(backup_count or 0),
    }


@_scope.bound
def import_sets(rows):
    """Merge backup rows into collection_sets.json and return count imported.

    The backup format is intentionally plain JSON so users can share it. Existing
    IDs are replaced, which lets a shared backup update the same collection cleanly.
    """
    incoming = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        if _is_builtin_id(row.get('id')) or bool(row.get('builtin')):
            # The mandatory collection ships with the addon and is never part
            # of portable profile backups.
            continue
        entries = row.get('entries') or []
        if not isinstance(entries, list):
            continue
        rid = str(row.get('id') or '').strip() or hashlib.sha1(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:12]
        incoming.append({
            'id': rid,
            'name': str(row.get('name') or row.get('title') or 'Shared Collection').strip(),
            'source_url': str(row.get('source_url') or row.get('source') or '').strip(),
            'entries': entries,
        })
    if not incoming:
        return 0
    current = _read()
    incoming_ids = {r.get('id') for r in incoming}
    merged = [r for r in current if r.get('id') not in incoming_ids]
    merged.extend(incoming)
    _write(merged)
    return len(incoming)
