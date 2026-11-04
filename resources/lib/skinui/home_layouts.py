# -*- coding: utf-8 -*-
"""A lighter Home (v5.10.447, skin.dexhub 3.28.167).

skin.dexhub's Home rows take a card style's layouts only when the row uses
that style (skin_home_lite.py): Skin.String(DexHub.Row.<n>) lists the shapes
row n has on any tab and the Home layout, "/poster/wide/L:two_rows/". An
empty string takes every style (the skin's first start, as before).

sync() writes those strings from what the board published (dhs.<tab>.shapes,
dhs.home.layout). Include conditions are read when the skin loads, so:
  - a style a row newly needs (the user gave it one): the skin is reloaded
    once; with a video playing nothing is written yet, so the next sync
    after it does it
  - a style no row needs any more stays listed (the list only grows, so rows
    moving between slots never reload the skin back and forth)
  - the first time (strings still empty): written, no reload; the Home is
    light from the next start

v5.10.512: the lists only grew, so in time every row carried every style it
had ever shown and every Home layout tried once: 504 of the 1,180 layouts
(13,000 controls) on a test Mac against 132 needed. Each shape's last day
on screen is kept now (home_layouts.json), and the first sync of each Kodi
start drops what no tab showed for a week. Dropping never reloads the
skin: the Home is lighter from the next start.
"""
import os
import threading
import time

import xbmc

from . import common as C

ROWS = 20
KEEP = 7 * 86400
_LOCK = threading.Lock()
_STATE = 'home_layouts.json'
_TRIMMED = 'dhs.rows.trimmed'       # Home property: this Kodi start trimmed already


def wanted():
    """{row: set of tokens}: every shape the row shows on any tab, the
    default poster, and the Home layout."""
    layout = C.prop('dhs.home.layout') or ''
    rows = {n: {'poster'} for n in range(1, ROWS + 1)}
    for tab in C.TABS:
        for n, shape in enumerate((C.prop('dhs.%s.shapes' % tab) or '').split('|'), 1):
            if shape and n <= ROWS:
                rows[n].add(shape)
    if layout:
        for tokens in rows.values():
            tokens.add('L:' + layout)
    return rows


def _encode(tokens):
    return '/%s/' % '/'.join(sorted(tokens))


def _seen(wanted, have_of, now):
    """{row: {token: last time on screen}} with *wanted* stamped now. A
    token listed before this record existed counts as seen six days ago:
    kept a day longer, then dropped if nothing showed it."""
    path = os.path.join(C.folder(), _STATE)
    state = C.read_json(path, None)
    fresh = not isinstance(state, dict)
    state = {} if fresh else state
    changed = fresh
    for n, tokens in wanted.items():
        row = state.setdefault(str(n), {})
        if fresh:
            for token in have_of(n):
                row[token] = now - KEEP + 86400
        for token in tokens:
            if now - float(row.get(token) or 0) > 3600:
                row[token] = now
                changed = True
    if changed:
        C.write_json(path, state)
    return state


def sync():
    """Write each row's styles; reload the skin when a row lacks one it now shows."""
    if not xbmc.getSkinDir().startswith('skin.dexhub'):
        return False
    with _LOCK:
        now = time.time()
        want = wanted()
        current = dict((n, xbmc.getInfoLabel('Skin.String(DexHub.Row.%d)' % n)) for n in want)
        have_of = lambda n: {t for t in current[n].split('/') if t}
        seen = _seen(want, have_of, now)
        trim = C.prop(_TRIMMED) != '1'
        writes, missing = [], []
        for n, tokens in want.items():
            have = have_of(n)
            if current[n] and not tokens <= have:
                missing.append((n, sorted(tokens - have)))
            recent = {t for t, at in (seen.get(str(n)) or {}).items() if now - float(at or 0) < KEEP}
            # within a start only ever grows: rows move between slots as they
            # empty and fill, and a shrinking list would reload the skin back
            # and forth; at a start, what no tab showed for a week goes
            value = _encode(tokens | recent if trim else tokens | recent | have)
            if value != current[n]:
                writes.append((n, value))
        if missing and xbmc.getCondVisibility('Player.HasVideo'):
            return False                # not over a video: the next sync after it
        for n, value in writes:
            xbmc.executebuiltin('Skin.SetString(DexHub.Row.%d,%s)' % (n, value))
        if trim:
            C.set_prop(_TRIMMED, '1')
    if missing:
        C.log('home rows: new styles %s, the skin is reloaded' % missing)
        xbmc.executebuiltin('ReloadSkin()')
    return bool(missing)
