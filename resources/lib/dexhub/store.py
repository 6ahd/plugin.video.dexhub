from .. import profile_scope as _scope
# -*- coding: utf-8 -*-
import json
import os
import shutil
import uuid

import xbmc

from .common import profile_path
from .links import manifest_link
from .safe_io import write_json as _atomic_write_json, read_json as _safe_read_json

PROVIDERS_PATH = _scope.ScopedPath('providers.json')
CATALOG_STATE_PATH = _scope.ScopedPath('catalog_state.json')

_PROVIDERS_CACHE = None
_PROVIDERS_MTIME = None
_STATE_CACHE = None
_STATE_MTIME = None


def _file_mtime(path):
    try:
        return (os.fspath(path), os.path.getmtime(path))
    except Exception:
        return (os.fspath(path), None)


# v3.9.17: state I/O delegated to safe_io for atomic writes + .bak recovery.
# These wrappers exist so the rest of store.py stays unchanged in shape.
def _read_json(path, default):
    return _safe_read_json(path, default)


def _write_json(path, value):
    _atomic_write_json(path, value)


def _invalidate_providers_cache(rows=None):
    global _PROVIDERS_CACHE, _PROVIDERS_MTIME
    _PROVIDERS_CACHE = rows
    _PROVIDERS_MTIME = _file_mtime(PROVIDERS_PATH)


def _invalidate_state_cache(state=None):
    global _STATE_CACHE, _STATE_MTIME
    _STATE_CACHE = state
    _STATE_MTIME = _file_mtime(CATALOG_STATE_PATH)


@_scope.bound
def list_providers():
    global _PROVIDERS_CACHE, _PROVIDERS_MTIME
    mtime = _file_mtime(PROVIDERS_PATH)
    if _PROVIDERS_CACHE is not None and mtime == _PROVIDERS_MTIME:
        return list(_PROVIDERS_CACHE)
    rows = _read_json(PROVIDERS_PATH, [])
    _PROVIDERS_CACHE = rows
    _PROVIDERS_MTIME = mtime
    return list(rows)


@_scope.bound
def write_providers_raw(rows):
    """v3.9.45: bulk-write the providers list, used by the import path
    when restoring a previously-exported configuration snapshot. Skips
    the per-provider validation flow because the snapshot already
    captured validated manifests at export time."""
    if not isinstance(rows, list):
        return
    _write_json(PROVIDERS_PATH, rows)
    _invalidate_providers_cache(rows)


@_scope.bound
def add_provider(name, manifest_url, manifest):
    # v5.10.143: saved as the https manifest even when a caller passes the
    # stremio:// or Stremio Web link it was given (GitHub issue #1).
    manifest_url = manifest_link(manifest_url)
    rows = list_providers()
    base_url = manifest_url.rsplit('/manifest.json', 1)[0]
    for row in rows:
        if row.get('manifest_url') == manifest_url:
            row.update({'name': name, 'base_url': base_url, 'manifest': manifest})
            _write_json(PROVIDERS_PATH, rows)
            _invalidate_providers_cache(rows)
            return row
    row = {
        'id': uuid.uuid4().hex[:10],
        'name': name,
        'manifest_url': manifest_url,
        'base_url': base_url,
        'manifest': manifest,
    }
    rows.append(row)
    _write_json(PROVIDERS_PATH, rows)
    _invalidate_providers_cache(rows)
    return row


@_scope.bound
def refresh_provider_manifest(provider_id, new_manifest):
    """Update a provider's manifest snapshot without touching its id/name."""
    rows = list_providers()
    for row in rows:
        if row.get('id') == provider_id:
            row['manifest'] = new_manifest
            if new_manifest and new_manifest.get('name') and not row.get('name'):
                row['name'] = new_manifest.get('name')
            _write_json(PROVIDERS_PATH, rows)
            _invalidate_providers_cache(rows)
            return row
    return None


@_scope.bound
def remove_provider(provider_id):
    rows = [row for row in list_providers() if row.get('id') != provider_id]
    _write_json(PROVIDERS_PATH, rows)
    _invalidate_providers_cache(rows)
    state = _catalog_state()
    state = {k: v for k, v in state.items() if not k.startswith(provider_id + ':')}
    _write_json(CATALOG_STATE_PATH, state)
    _invalidate_state_cache(state)



@_scope.bound
def move_provider_to(provider_id, new_idx):
    """Move a provider to an absolute index. Used for "Move to top" or
    "Move to bottom" shortcuts where the user knows exactly where they
    want the provider."""
    rows = list_providers()
    src_idx = None
    for i, row in enumerate(rows):
        if row.get('id') == provider_id:
            src_idx = i
            break
    if src_idx is None:
        return None
    new_idx = max(0, min(int(new_idx), len(rows) - 1))
    if new_idx == src_idx:
        return src_idx
    rows.insert(new_idx, rows.pop(src_idx))
    _write_json(PROVIDERS_PATH, rows)
    _invalidate_providers_cache(rows)
    return new_idx


@_scope.bound
def get_provider(provider_id):
    for row in list_providers():
        if row.get('id') == provider_id:
            return row
    return None


def _catalog_state():
    global _STATE_CACHE, _STATE_MTIME
    mtime = _file_mtime(CATALOG_STATE_PATH)
    if _STATE_CACHE is not None and mtime == _STATE_MTIME:
        return dict(_STATE_CACHE)
    state = _read_json(CATALOG_STATE_PATH, {})
    _STATE_CACHE = state
    _STATE_MTIME = mtime
    return dict(state)


def _catalog_key(provider_id, catalog_id):
    return '%s:%s' % (provider_id or '', catalog_id or '')


@_scope.bound
def get_catalog_state(provider_id, catalog_id):
    return _catalog_state().get(_catalog_key(provider_id, catalog_id), {})


@_scope.bound
def set_catalog_filter(provider_id, catalog_id, name, value):
    state = _catalog_state()
    key = _catalog_key(provider_id, catalog_id)
    current = dict(state.get(key, {}))
    if value in (None, '', '__clear__'):
        current.pop(name, None)
    else:
        current[name] = value
    state[key] = current
    _write_json(CATALOG_STATE_PATH, state)
    _invalidate_state_cache(state)
    return current


@_scope.bound
def clear_catalog_filters(provider_id, catalog_id):
    state = _catalog_state()
    key = _catalog_key(provider_id, catalog_id)
    if key in state:
        del state[key]
        _write_json(CATALOG_STATE_PATH, state)
        _invalidate_state_cache(state)
