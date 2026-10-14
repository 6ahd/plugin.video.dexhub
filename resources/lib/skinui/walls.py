# -*- coding: utf-8 -*-
"""Collection walls (v5.10.183).

A collection row (Networks, Studios, Genres...) drawn in a collection style
shows each folder as a card: the folder's logo on the left and the posters of
its first catalog as a tilted wall on the right (the Nuvio and Apple TV look).
A folder with no picture of its own (a cover only Nuvio's app can show) gets
its catalog's posters too.

The posters are read in the background, four catalogs at a time, and kept a
day (walls.json); the row is written again when they are in. A row is never
held back for them: until they come, a card shows its backdrop.
"""
import os
import threading
import time
from .. import lazy_tb as traceback

import xbmc

from . import common as C

KEEP = 24 * 3600.0
RETRY = 3 * 3600.0          # a catalog that gave nothing is asked again later
MAX_POSTERS = 20
MAX_FOLDERS = 80
AT_ONCE = 3
PAUSE = 0.2           # between batches: the walls never crowd out what the user opens

_lock = threading.Lock()
_cache = {'data': None}
_running = set()
_inflight = {}         # folder key -> Event while one fill reads it (v5.10.185)


def _path():
    return os.path.join(C.folder(), 'walls.json')


def _load():
    with _lock:
        if _cache['data'] is None:
            data = C.read_json(_path(), {}) or {}
            _cache['data'] = data if isinstance(data, dict) else {}
        return _cache['data']


def _save():
    with _lock:
        data = dict(_cache['data'] or {})
    now = time.time()
    data = {k: v for k, v in data.items() if now - float((v or {}).get('t') or 0) < 3 * KEEP}
    C.write_json(_path(), data)


def _key(tile):
    ref = tile.get('folder_ref') or {}
    return '%s|%s|%s' % (ref.get('set_id') or '', ref.get('group_id') or '', ref.get('folder_id') or '')


def _params(tile):
    ref = tile.get('folder_ref') or {}
    indexes = tile.get('source_indexes') or []
    if not indexes:
        return None
    return {'action': 'collection_nuvio_source_open', 'set_id': ref.get('set_id') or '',
            'group_id': ref.get('group_id') or '', 'folder_id': ref.get('folder_id') or '',
            'source_index': str(indexes[0]), 'page': '1'}


def _fresh(entry):
    if not entry or 'preview' not in entry:
        return False
    age = time.time() - float(entry.get('t') or 0)
    return age < (KEEP if entry.get('p') else RETRY)


def dress(tab, spec, tiles, on_done=None):
    """The folder cards with the posters known so far; the rest are read in
    the background and the row is written again when they are in (or
    ``on_done()`` runs: the add-on's own Home window, v5.10.184)."""
    data = _load()
    missing = []
    out = []
    for index, tile in enumerate(tiles or []):
        if (tile or {}).get('kind') != 'folder' or not tile.get('folder_ref'):
            out.append(tile)
            continue
        tile = dict(tile)
        entry = data.get(_key(tile))
        posters = list((entry or {}).get('p') or [])
        if posters:
            posters = (posters * (MAX_POSTERS // len(posters) + 1))[:MAX_POSTERS]
        tile['collage'] = posters
        tile['collage_line'] = ''
        tile['collection_preview'] = dict((entry or {}).get('preview') or {})
        if not tile.get('fanart') and (entry or {}).get('f'):
            tile['fanart'] = entry['f']
        if not _fresh(entry) and index < MAX_FOLDERS:
            missing.append(tile)
        out.append(tile)
    if missing:
        _start(tab, spec, tiles, missing, on_done)
    return out


def _start(tab, spec, tiles, missing, on_done=None):
    mark = '%s|%s' % (tab, spec.get('id'))
    with _lock:
        if mark in _running:
            return
        _running.add(mark)
    thread = threading.Thread(target=_fill, args=(tab, dict(spec), list(tiles), missing, mark, on_done),
                              name='DexHub-walls')
    thread.daemon = False
    thread.start()


def _read_one(tile, out):
    params = _params(tile)
    entry = {'t': time.time(), 'p': [], 'f': '', 'preview': {}}
    if params:
        try:
            from ..homeui import rows as R
            from .rows import api
            found, _more = R.capture_tiles(params, api(), limit=MAX_POSTERS + 6)
            posters = []
            for t in found or []:
                art = t.get('poster') or t.get('landscape') or t.get('fanart') or ''
                if art and art not in posters:
                    posters.append(art)
                if not entry['f'] and t.get('fanart'):
                    entry['f'] = t['fanart']
            entry['p'] = posters[:MAX_POSTERS]
            from ..homeui.row_styles import preview_fields
            entry['preview'] = preview_fields(found)
        except Exception:
            C.log('wall of "%s" failed: %s' % (tile.get('title') or '', traceback.format_exc().splitlines()[-1]),
                  xbmc.LOGDEBUG)
    out[_key(tile)] = entry


def _fill(tab, spec, tiles, missing, mark, on_done=None):
    started = time.monotonic()
    try:
        results = {}
        # v5.10.185: a row on two tabs (All and Movies) asked for the same
        # folders twice at once (a Mac log: 28 and 80 catalogs together, TMDb
        # rate limited for seconds): a folder another fill reads is waited for
        mine, others = [], []
        with _lock:
            for tile in missing:
                key = _key(tile)
                if key in _inflight:
                    others.append(_inflight[key])
                else:
                    _inflight[key] = threading.Event()
                    mine.append(tile)
        queue = list(mine)
        monitor = xbmc.Monitor()
        try:
            while queue and not monitor.abortRequested():
                batch, queue = queue[:AT_ONCE], queue[AT_ONCE:]
                threads = [threading.Thread(target=_read_one, args=(t, results), name='DexHub-wall') for t in batch]
                for th in threads:
                    th.daemon = True
                    th.start()
                for th in threads:
                    th.join(25)
                with _lock:
                    (_cache['data'] if _cache['data'] is not None else {}).update(results)
                    for tile in batch:
                        event = _inflight.pop(_key(tile), None)
                        if event is not None:
                            event.set()
                if queue and monitor.waitForAbort(PAUSE):
                    break
        finally:
            with _lock:
                for tile in mine:
                    event = _inflight.pop(_key(tile), None)
                    if event is not None:
                        event.set()
        for event in others:
            event.wait(30)
        _save()
        data = _load()
        got = sum(1 for v in results.values() if v.get('p')) + sum(
            1 for t in missing if t not in mine and (data.get(_key(t)) or {}).get('p'))
        C.log('collection walls of "%s": %d of %d in %.1f s' % (spec.get('title') or '', got, len(missing),
                                                                  time.monotonic() - started))
        if got and on_done is not None:
            on_done()
        elif got:
            from . import rows as SR
            from .board import bump, is_published
            SR._store(tab, spec, tiles, None, enriched=True, src=SR._tiles_digest(tiles))
            if is_published(tab, spec['id']):
                bump(tab, spec['id'])
    except Exception:
        C.log('collection walls failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    finally:
        with _lock:
            _running.discard(mark)
