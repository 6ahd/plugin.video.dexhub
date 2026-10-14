# -*- coding: utf-8 -*-
"""Local mirror of Nuvio's non-secret presentation preferences.

Home metadata behavior depends on both ``tmdb_settings`` and the selected
``layout_settings`` layout. Keeping them together avoids applying TMDb on a
Modern Home where the Nuvio profile explicitly disabled that enrichment.
Credentials such as TMDb API keys remain local to Dex Hub.
"""
from .. import profile_scope as _scope
import os
import re
import time
from datetime import date, datetime

from .common import profile_path
from .safe_io import read_json, write_json

PREFS_FILE = _scope.ScopedPath('nuvio_profile_prefs.json')
SCHEMA = 2
_YEAR_RE = re.compile(r'\b(19|20)\d{2}\b')


def _decode_value(value):
    # Nuvio profile blobs serialize SharedPreferences values as
    # {"type":"boolean|string|int|...", "value": ...}.
    if isinstance(value, dict) and 'value' in value:
        return value.get('value')
    return value


def _decode_map(raw):
    if not isinstance(raw, dict):
        return {}
    return {str(k): _decode_value(v) for k, v in raw.items()}


@_scope.bound
def save_blob(blob, platform='tv', updated_at=''):
    if not isinstance(blob, dict):
        return load()
    features = blob.get('features') if isinstance(blob.get('features'), dict) else {}
    tmdb_raw = features.get('tmdb_settings') if isinstance(features, dict) else {}
    if not isinstance(tmdb_raw, dict):
        tmdb_raw = {}
    layout_raw = features.get('layout_settings') if isinstance(features, dict) else {}
    if not isinstance(layout_raw, dict):
        layout_raw = {}
    row = {
        'schema': SCHEMA,
        'synced': True,
        'saved_at': int(time.time()),
        'updated_at': str(updated_at or ''),
        'platform': str(platform or 'tv'),
        'tmdb': _decode_map(tmdb_raw),
        'layout': _decode_map(layout_raw),
    }
    write_json(PREFS_FILE, row)
    _LOADED['key'] = ()
    _STAT['at'] = 0.0           # looked at again on the next read
    return row


_LOADED = {'key': (), 'row': {}, 'norm': None}


_STAT = {'at': 0.0, 'key': None}


def _file_key():
    # v5.10.213: the file is looked at once every two seconds at most (a Home
    # publish asked hundreds of times: 0.2 to 0.4 s of stat calls on a box)
    now = time.monotonic()
    if _STAT.get('scope') == _scope.key() and now - _STAT['at'] < 2.0:
        return _STAT['key']
    try:
        st = os.stat(PREFS_FILE)
        key = (_scope.key(), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (_scope.key(), None)
    _STAT.update(at=now, key=key, scope=_scope.key())
    return key


@_scope.bound
def load():
    """The saved preferences (a copy). v5.10.188: read once per change of the
    file: a Home publish asked for them about 500 times, each a file read and
    a JSON parse (most of a box's second per Home tab)."""
    key = _file_key()
    if key != _LOADED['key']:
        row = (read_json(PREFS_FILE, {}) or {}) if key is not None else {}
        _LOADED.update(key=key, row=row if isinstance(row, dict) else {}, norm=None)
    return dict(_LOADED['row'])


@_scope.bound
def clear():
    _STAT['at'] = 0.0
    for path in (PREFS_FILE, PREFS_FILE + '.bak'):
        try:
            os.remove(path)
        except Exception:
            pass


@_scope.bound
def is_stale(max_age=1800):
    row = load()
    if not row.get('synced'):
        return True
    try:
        return (time.time() - int(row.get('saved_at') or 0)) >= max(60, int(max_age))
    except Exception:
        return True


@_scope.bound
def tmdb_settings():
    row = load()
    prefs = row.get('tmdb') if isinstance(row.get('tmdb'), dict) else {}
    return dict(prefs), bool(row.get('synced'))


@_scope.bound
def layout_settings():
    row = load()
    prefs = row.get('layout') if isinstance(row.get('layout'), dict) else {}
    return dict(prefs), bool(row.get('synced'))


def _bool(prefs, key, default=False):
    value = (prefs or {}).get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


@_scope.bound
def normalized_tmdb():
    """v5.10.188: worked out once per change of the preferences file."""
    if _file_key() != _LOADED['key'] or _LOADED.get('row') is None:
        load()
    if _LOADED['norm'] is None:
        _LOADED['norm'] = _normalized_tmdb()
    return dict(_LOADED['norm'])


def _normalized_tmdb():
    prefs, synced = tmdb_settings()
    layout, _layout_synced = layout_settings()
    selected_layout = str(layout.get('selected_layout') or 'MODERN').strip().upper() or 'MODERN'
    enabled = _bool(prefs, 'tmdb_enabled', False)
    modern_home_enabled = _bool(prefs, 'tmdb_modern_home_enabled', False)
    home_enabled = enabled and (selected_layout != 'MODERN' or modern_home_enabled)
    return {
        'synced': synced,
        'enabled': enabled,
        'home_enabled': home_enabled,
        'modern_home_enabled': modern_home_enabled,
        'home_layout': selected_layout,
        # Nuvio enables this by default and renders Home rows as
        # ``Catalog name - Movie/Series`` using the app's current language.
        # It is a layout preference, not a Dex-specific naming option.
        'catalog_type_suffix_enabled': _bool(
            layout, 'catalog_type_suffix_enabled', True),
        'catalog_addon_name_enabled': _bool(
            layout, 'catalog_addon_name_enabled', True),
        'follow_addons_order': _bool(
            layout, 'follow_addons_order', False),
        # Nuvio localizes a catalog title only in Modern Home. Classic and
        # Compact keep the add-on's native title even when TMDb is enabled.
        'localized_title_on_home': bool(home_enabled and selected_layout == 'MODERN'),
        'enrich_continue_watching': _bool(prefs, 'tmdb_enrich_continue_watching', True),
        'language': str(prefs.get('tmdb_language') or 'en').strip() or 'en',
        'use_artwork': _bool(prefs, 'tmdb_use_artwork', True),
        'use_basic_info': _bool(prefs, 'tmdb_use_basic_info', True),
        'use_details': _bool(prefs, 'tmdb_use_details', True),
        'use_credits': _bool(prefs, 'tmdb_use_credits', True),
        'use_productions': _bool(prefs, 'tmdb_use_productions', True),
        'use_networks': _bool(prefs, 'tmdb_use_networks', True),
        'use_release_dates': _bool(prefs, 'tmdb_use_release_dates', False),
        'use_episodes': _bool(prefs, 'tmdb_use_episodes', True),
        'use_trailers': _bool(prefs, 'tmdb_use_trailers', True),
        'use_more_like_this': _bool(prefs, 'tmdb_use_more_like_this', True),
        'use_collections': _bool(prefs, 'tmdb_use_collections', True),
    }


@_scope.bound
def format_catalog_row_title(name, media_type, prefs=None, type_labels=None):
    """Return the visible catalog title exactly as Nuvio Home formats it.

    Nuvio uses the custom title when present (the caller resolves that),
    otherwise the live manifest catalog name. It uppercases only the first
    character, then appends the localized singular content type when the
    synced layout toggle is enabled. ``type_labels`` lets the Kodi adapter
    supply Nuvio's Arabic/English resource strings without importing Kodi here.
    """
    prefs = normalized_tmdb() if prefs is None else dict(prefs or {})
    title = str(name or '').strip()
    if title:
        title = title[:1].upper() + title[1:]
    media_type = str(media_type or '').strip().lower()
    is_series = media_type in ('series', 'tv', 'show', 'shows', 'tvshow', 'anime')
    labels = type_labels if isinstance(type_labels, dict) else {}
    type_label = str(labels.get('series' if is_series else 'movie') or
                     ('Series' if is_series else 'Movie')).strip()
    if not title:
        return type_label
    if not prefs.get('catalog_type_suffix_enabled', True) or not type_label:
        return title
    return '%s - %s' % (title, type_label)


@_scope.bound
def merge_home_preview(base, tmdb_meta, prefs=None):
    """Pure implementation of Nuvio TV's catalog-preview enrichment.

    The source poster is immutable at this stage. TMDb may supply the focus
    backdrop/logo and selected preview metadata; it localizes the visible title
    only for Modern Home with ``tmdb_modern_home_enabled``.
    """
    prefs = dict(prefs or normalized_tmdb())
    out = dict(base or {})
    tm = tmdb_meta if isinstance(tmdb_meta, dict) else {}

    for key in ('imdb_id', 'tmdb_id', 'tvdb_id'):
        if not out.get(key) and tm.get(key):
            out[key] = tm.get(key)

    if prefs.get('use_basic_info', True):
        for key in ('description', 'overview', 'genres'):
            if tm.get(key) not in (None, '', [], {}):
                out[key] = tm.get(key)
        if prefs.get('localized_title_on_home'):
            localized = tm.get('name') or tm.get('title')
            if localized not in (None, '', [], {}):
                out['name'] = localized
                out['title'] = localized

    if prefs.get('use_details', True):
        for key in ('runtime', 'ageRating', 'certification', 'status'):
            if tm.get(key) not in (None, '', [], {}):
                out[key] = tm.get(key)

    # Nuvio exposes production companies and TV networks as independent TMDb
    # preference groups.  Carry the native TMDb-shaped fields instead of
    # flattening them here; Kodi's shared metadata adapter then publishes exact
    # studio names, which Arctic Fuse can resolve through its studio resource
    # icon pack.  Older builds omitted these fields entirely from Home rows.
    if prefs.get('use_productions', True) or prefs.get('use_networks', True):
        for key in ('studio', 'studios'):
            if tm.get(key) not in (None, '', [], {}):
                out[key] = tm.get(key)

    if prefs.get('use_productions', True):
        for key in ('production_companies', 'productionCompanies'):
            if tm.get(key) not in (None, '', [], {}):
                out[key] = tm.get(key)

    if prefs.get('use_networks', True):
        for key in ('network', 'networks'):
            if tm.get(key) not in (None, '', [], {}):
                out[key] = tm.get(key)

    if prefs.get('use_release_dates', False):
        release = tm.get('releaseInfo') or tm.get('year')
        if release not in (None, '', [], {}):
            out['releaseInfo'] = release

    if prefs.get('use_artwork', True):
        for key in ('fanart', 'landscape', 'clearlogo'):
            if tm.get(key):
                out[key] = tm.get(key)
        if tm.get('fanart'):
            out['background'] = tm.get('fanart')

    out['_dexhub_art_strict'] = '1'
    out['_dexhub_meta_source_id'] = (
        'nuvio-tmdb' if prefs.get('home_enabled') else 'nuvio-native')
    if tm.get('_nuvio_exact_art'):
        out['_nuvio_exact_art'] = '1'
        out['_nuvio_tmdb_language'] = (
            tm.get('_nuvio_tmdb_language') or prefs.get('language') or '')
    return out


@_scope.bound
def is_unreleased(meta, today=None, treat_missing_as_unreleased=False):
    """Mirror Nuvio's release filter for a MetaPreview-like dictionary."""
    meta = meta if isinstance(meta, dict) else {}
    today = today or date.today()
    values = [meta.get('released'), meta.get('releaseInfo'),
              meta.get('release_info'), meta.get('premiered')]
    values = [str(value).strip() for value in values if str(value or '').strip()]
    if not values:
        year = str(meta.get('year') or '').strip()
        if year:
            values.append(year)
    if not values:
        return bool(treat_missing_as_unreleased)

    for raw in values:
        candidate = raw
        try:
            parsed = datetime.fromisoformat(candidate.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                now = datetime.now(parsed.tzinfo)
                return parsed > now
            return parsed.date() > today
        except Exception:
            pass
        try:
            return date.fromisoformat(candidate[:10]) > today
        except Exception:
            pass
        match = _YEAR_RE.search(candidate)
        if match:
            try:
                return int(match.group(0)) > int(today.year)
            except Exception:
                pass
    return False
