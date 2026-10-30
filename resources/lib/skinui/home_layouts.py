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
"""
import threading

import xbmc

from . import common as C

ROWS = 20
_LOCK = threading.Lock()


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


def sync():
    """Write each row's styles; reload the skin when a row lacks one it now shows."""
    if not xbmc.getSkinDir().startswith('skin.dexhub'):
        return False
    with _LOCK:
        writes, missing = [], []
        for n, tokens in wanted().items():
            current = xbmc.getInfoLabel('Skin.String(DexHub.Row.%d)' % n)
            have = {t for t in current.split('/') if t}
            if current and not tokens <= have:
                missing.append((n, sorted(tokens - have)))
            # only ever grows: rows move between slots as they empty and
            # fill, and a shrinking list would reload the skin back and forth
            value = _encode(tokens | have)
            if value != current:
                writes.append((n, value))
        if missing and xbmc.getCondVisibility('Player.HasVideo'):
            return False                # not over a video: the next sync after it
        for n, value in writes:
            xbmc.executebuiltin('Skin.SetString(DexHub.Row.%d,%s)' % (n, value))
    if missing:
        C.log('home rows: new styles %s, the skin is reloaded' % missing)
        xbmc.executebuiltin('ReloadSkin()')
    return bool(missing)
