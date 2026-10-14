# -*- coding: utf-8 -*-
"""How Dex Hub's widgets in Arctic Fuse 3 are asked for (v5.10.112).

Kodi reuses one Python interpreter for Dex Hub's plugin calls
(reuselanguageinvoker). When a hub opens, Kodi asks every widget of it at
once (three at a time). When one of those calls is handed the interpreter a
previous call left, another call starting in the same instant can stop it
before it ran ("GetDirectory - Error getting": the row stays empty) or tear
it down under it (a crash). In the test harness twelve Dex Hub rows asked at
once lost a row every second opening and crashed Kodi after a few dozen.

Two things keep that from happening:

  * every widget call ends without an interpreter to reuse (serve.widget):
    each one gets its own, none is stopped or torn down (thirty openings of
    twelve rows, not one error);
  * a row whose titles changed is asked for again one at a time: its path
    ends in its own version, a Home property the service (af3.Versions)
    sets from the row's real version one row after another, never while a
    Dex Hub widget call runs:

        ...?action=skin_row&m=<tab>&id=<row>&af3=<menu>&v=$INFO[Window(Home).Property(dhs.af3v.<tab>.<row>)]

The path itself never changes otherwise, so opening a hub again reads
nothing (Kodi keeps the hub's lists), and the lists are sorted "as given"
(playlist), so starting or stopping a video does not make Kodi read them
all again either.
"""
import time

from . import common as C

P_BUSY = 'dhs.af3.busy'         # a Dex Hub widget call runs now (time it started)


def p_version(tab, row_id):
    return 'dhs.af3v.%s.%s' % (tab, row_id)


def widget_path(tab, row_id, menu):
    return C.url('skin_row', m=tab, id=row_id, af3=menu) + '&v=$INFO[Window(Home).Property(%s)]' % p_version(tab, row_id)


def begin():
    C.set_prop(P_BUSY, '%.2f' % time.time())


def end():
    C.set_prop(P_BUSY, '')


def busy(now=None):
    try:
        since = float(C.prop(P_BUSY) or 0)
    except ValueError:
        return False
    return bool(since) and (now or time.time()) - since < 10.0
