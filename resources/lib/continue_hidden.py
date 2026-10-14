# -*- coding: utf-8 -*-
# v5.10.286: new module (Remove from Continue Watching that stays removed)
"""Titles the user removed from Continue Watching (v5.10.286).

"Remove from Continue Watching" used to delete one playback row. A Continue
Watching card stands for a whole title (every episode of a show, and the
same title from Trakt, Nuvio, Floppy, Simkl and the servers, merged since
v5.10.214), so the next row of that title took the card's place at once, and
the next account sync wrote the deleted row back.

A removal now leaves a mark here: every name the title goes by (the keys of
plugin._work_keys) with the time it was removed. Continue Watching leaves out
any row of those names that is not newer than that time, whatever source it
comes from. Progress newer than the removal (the title played again, here or
on another device) shows the title again and drops its mark.
"""
import json
import os
import re
import threading
import time

from . import profile_scope as _scope

PATH = _scope.ScopedPath('continue_hidden.json')
GRACE = 60          # seconds: a sync rewriting the same pause point is not new progress
KEEP_DAYS = 365     # marks older than this are forgotten
_LOCK = threading.RLock()
_MEMO = {'path': '', 'mtime': None, 'keys': {}}


def _stamp(row):
    value = (row or {}).get('updated_at')
    try:
        at = float(value or 0)
    except (TypeError, ValueError):
        try:
            from .native_continue import _epoch
            at = float(_epoch(value) or 0)
        except Exception:
            at = 0.0
    return at / 1000.0 if at > 1e12 else at


def _load():
    path = os.fspath(PATH)
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    if _MEMO['path'] == path and _MEMO['mtime'] == mtime:
        return dict(_MEMO['keys'])
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        keys = data.get('keys') if isinstance(data, dict) else None
        keys = dict((str(k), float(v)) for k, v in (keys or {}).items())
    except Exception:
        keys = {}
    _MEMO.update(path=path, mtime=mtime, keys=dict(keys))
    return keys


def _save(keys):
    path = os.fspath(PATH)
    floor = time.time() - KEEP_DAYS * 86400
    keys = dict((k, v) for k, v in keys.items() if v >= floor)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump({'schema': 1, 'keys': keys}, handle, ensure_ascii=False)
        os.replace(tmp, path)
        _MEMO.update(path='', mtime=None, keys={})
        return True
    except Exception:
        return False


def hide(keys, at=None):
    """Remember ``keys`` (every name of one title) as removed now."""
    keys = [str(k) for k in keys or [] if k]
    if not keys:
        return False
    at = float(at or time.time())
    with _LOCK:
        data = _load()
        for key in keys:
            data[key] = max(at, data.get(key, 0.0))
        return _save(data)


def forget(keys):
    with _LOCK:
        data = _load()
        changed = False
        for key in keys or []:
            if data.pop(str(key), None) is not None:
                changed = True
        if changed:
            _save(data)
        return changed


def visible(rows, keys_fn):
    """``rows`` without the removed titles' rows; a row newer than its
    title's removal stays and clears the mark (the title was played again)."""
    rows = list(rows or [])
    with _LOCK:
        marks = _load()
    if not marks or not rows:
        return rows
    out, revived = [], set()
    for row in rows:
        try:
            names = set(keys_fn(row))
        except Exception:
            names = set()
        hit = [marks[k] for k in names if k in marks]
        if not hit:
            out.append(row)
            continue
        if _stamp(row) > max(hit) + GRACE:
            revived |= names
            out.append(row)
    if revived:
        forget([k for k in revived if k in marks])
    return out


def group_keys(rows, target, keys_fn):
    """Every name of ``target``'s Continue Watching card: the keys of all the
    rows the card merges (continue_merge.merge joins rows sharing any key, and
    a server episode with no show id by its show's name)."""
    try:
        want = set(keys_fn(target))
    except Exception:
        want = set()
    aliases = []
    for row in rows or []:
        try:
            aliases.append((row, set(keys_fn(row))))
        except Exception:
            continue
    grew = True
    while grew:
        grew = False
        for _row, names in aliases:
            if names & want and not names <= want:
                want |= names
                grew = True
    # the show's name, for a server's episode carrying only episode ids
    name = _show_name(target)
    if name and any(k.startswith(('s:tt', 's:tmdb')) for k in want):
        for row, names in aliases:
            if _show_name(row) == name and not any(k.startswith(('s:tt', 's:tmdb')) for k in names):
                want |= names
    return want


def _show_name(row):
    row = row or {}
    episode = bool(row.get('season') or row.get('episode')) or \
        str(row.get('media_type') or '').lower() in ('series', 'episode', 'anime', 'tv')
    if not episode:
        return ''
    text = str(row.get('show_title') or '').strip()
    if not text or re.match(r'^(?:tt\d+|\d+|episode\s*\d+|s\d+\s*e\d+)$', text, re.I):
        return ''
    return re.sub(r'[^\w]+', ' ', text.lower()).strip()
