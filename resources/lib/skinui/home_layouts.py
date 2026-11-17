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

v5.10.532: a real AM8 reloaded the skin 9 times in 8 minutes (about 8 s
each with the Home gone), a row's style counting as new whenever it moved
to another slot or a hidden tab filled in. What the skin really loaded is
kept (dhs.rows.loaded). A style for a row nobody sees is written for the next
start and costs nothing now; the Home, Movies, TV Shows and the tab on screen
may reload, once in _RELOAD_GAP; the user's own style or layout always does.
A row whose style is not loaded yet shows as posters, never blank. One list
shared by every row was measured too: 3 s more at every start, so no.
"""
import json
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
_LOADED = 'dhs.rows.loaded'         # Home property: {row: the styles the skin took}
_RELOADED = 'dhs.rows.reloaded'     # Home property: when the styles last reloaded it
_FELL_BACK = 'dhs.rows.fellback'    # Home property: a row is showing posters for now
_FORCE = 'dhs.rows.force'           # Home property: the user changed a style
_RELOAD_GAP = 600.0
_ALWAYS = ('all', 'movie', 'series')    # lists of their own, loaded while hidden


def wanted(tabs=None):
    """{row: set of tokens}: every shape the row shows on any tab, the
    default poster, and the Home layout."""
    layout = C.prop('dhs.home.layout') or ''
    rows = {n: {'poster'} for n in range(1, ROWS + 1)}
    for tab in tabs or C.TABS:
        for n, shape in enumerate((C.prop('dhs.%s.shapes' % tab) or '').split('|'), 1):
            if shape and n <= ROWS:
                rows[n].add(shape)
    if layout:
        for tokens in rows.values():
            tokens.add('L:' + layout)
    return rows


def _encode(tokens):
    return '/%s/' % '/'.join(sorted(tokens))


def _tokens(text):
    return {t for t in str(text or '').split('/') if t}


def _strings():
    return dict((n, xbmc.getInfoLabel('Skin.String(DexHub.Row.%d)' % n)) for n in range(1, ROWS + 1))


def _loaded(current=None):
    """{row: its string when the skin loaded}. Nothing has written them yet
    the first time in a Kodi start, so they are what the skin took."""
    try:
        known = json.loads(C.prop(_LOADED) or 'null')
    except ValueError:
        known = None
    if isinstance(known, dict):
        return dict((int(n), value) for n, value in known.items())
    known = current if current is not None else _strings()
    C.set_prop(_LOADED, json.dumps(known))
    return dict(known)


def shown(n, shape):
    """The shape row n can draw now: its own, or posters until the skin has
    its layouts (a row with no layout for its shape would be blank)."""
    token = shape or 'poster'
    if token == 'poster' or n > ROWS:
        return shape
    have = _loaded().get(n)
    if not have or token in _tokens(have):
        return shape
    C.set_prop(_FELL_BACK, '1')
    return 'poster'


def force_next():
    """The user chose a style or a layout: the next sync shows it at once."""
    C.set_prop(_FORCE, '1')


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
        current = _strings()
        loaded = _loaded(current)
        have_of = lambda n: _tokens(current[n])
        seen = _seen(want, have_of, now)
        trim = C.prop(_TRIMMED) != '1'
        visible = C.prop('dhs.tab') or 'all'
        need = wanted(_ALWAYS + (() if visible in _ALWAYS else (visible,)))
        missing = [(n, sorted(tokens - _tokens(loaded.get(n)))) for n, tokens in need.items()
                   if loaded.get(n) and not tokens <= _tokens(loaded.get(n))]
        writes, final = [], dict(current)
        for n, tokens in want.items():
            have = have_of(n)
            recent = {t for t, at in (seen.get(str(n)) or {}).items() if now - float(at or 0) < KEEP}
            # within a start only ever grows: rows move between slots as they
            # empty and fill, and a shrinking list would reload the skin back
            # and forth; at a start, what no tab showed for a week goes
            value = _encode(tokens | recent if trim else tokens | recent | have)
            if value != current[n]:
                writes.append((n, value))
                final[n] = value
        if missing and xbmc.getCondVisibility('Player.HasVideo'):
            return False                # not over a video: the next sync after it
        for n, value in writes:
            xbmc.executebuiltin('Skin.SetString(DexHub.Row.%d,%s)' % (n, value))
        if trim:
            C.set_prop(_TRIMMED, '1')
        forced = C.prop(_FORCE) == '1'
        if forced:
            C.set_prop(_FORCE, '')
        layout = any(token.startswith('L:') for _n, tokens in missing for token in tokens)
        try:
            recently = now - float(C.prop(_RELOADED) or 0) < _RELOAD_GAP
        except ValueError:
            recently = False
        if missing and recently and not forced and not layout:
            C.log('home rows: new styles %s wait for the next start' % missing)
            missing = []
        fell_back = C.prop(_FELL_BACK) == '1'
        if missing:
            C.set_prop(_LOADED, json.dumps(final))
            C.set_prop(_RELOADED, '%.0f' % now)
            C.set_prop(_FELL_BACK, '')
    if missing:
        C.log('home rows: new styles %s, the skin is reloaded' % missing)
        xbmc.executebuiltin('ReloadSkin()')
        if fell_back:
            C.ask_republish('styles')   # rows on posters take their own style now
    return bool(missing)
