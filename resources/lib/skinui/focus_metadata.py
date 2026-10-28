# -*- coding: utf-8 -*-
"""Publish fresh focused facts without rebuilding Kodi's focused container."""
import os
import time
from . import common as C
_CACHE = {}
_LAST = None
_AT = 0.0


def publish():
    global _LAST, _AT
    now = time.monotonic()
    if now - _AT < 0.4:
        return
    _AT = now
    import xbmc
    if not xbmc.getCondVisibility('Window.IsActive(home)'):
        return
    tab = C.prop('dhs.tab') or 'all'
    try:
        slot = int(C.prop('dhs.fr') or 0)
    except ValueError:
        return
    if tab not in C.TABS or not 1 <= slot <= C.ROWS:
        return
    cid = C.layout().row_list(slot)
    if not xbmc.getCondVisibility('Control.HasFocus(%d)' % cid):
        return
    ident = xbmc.getInfoLabel('Container(%d).ListItem.Property(dhs.id)' % cid)
    if not ident:
        return
    row = C.prop('dhs.%s.%d.id' % (tab, slot))
    path = C.row_file(tab, row)
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return
    key = (path, stamp)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = C.read_row(path) or {}
    data = _CACHE[key]
    try:
        position = int(xbmc.getInfoLabel('Container(%d).CurrentItem' % cid)) - 1
        item = data['items'][position] if position >= 0 else {}
    except (ValueError, KeyError, IndexError):
        return
    props = item.get('props') or {}
    if props.get('dhs.id') != ident or props.get('kind') != 'work':
        return
    signature = (path, ident, cid, stamp)
    if signature == _LAST and C.prop('dhs.fm.id') == ident:
        return
    C.set_prop('dhs.fm.id', '')
    for field, value in [('plot', (item.get('info') or {}).get('plot') or ''),
                         ('meta', props.get('dhs.meta') or ''),
                         ('compact', props.get('dhs.hero_meta') or '')]:
        C.set_prop('dhs.fm.' + field, value)
    C.set_prop('dhs.fm.cid', str(cid))
    C.set_prop('dhs.fm.id', ident)
    _LAST = signature
