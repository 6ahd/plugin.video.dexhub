# -*- coding: utf-8 -*-
"""Keyboard prompts for account data: addresses, usernames, keys (v5.10.110).

skin.dexhub 2 (Arctic Fuse 3 inside) can show keyboard suggestions from
plugin.program.autocompletion, a web search service, for what is typed.
While Dex Hub asks for a server address, a username, an API key or a
playlist address it sets the DexHub.KeyboardPrivate Home property, and the
skin asks for no suggestions at all (it never does for a hidden entry such
as a password, nor for text holding an address).
"""
import contextlib
import threading

import xbmcgui

PROPERTY = 'DexHub.KeyboardPrivate'
_lock = threading.Lock()
_depth = [0]


@contextlib.contextmanager
def private():
    """The keyboard opened inside this block suggests nothing."""
    with _lock:
        _depth[0] += 1
        first = _depth[0] == 1
    if first:
        try:
            xbmcgui.Window(10000).setProperty(PROPERTY, '1')
        except Exception:
            pass
    try:
        yield
    finally:
        with _lock:
            _depth[0] = max(0, _depth[0] - 1)
            last = _depth[0] == 0
        if last:
            try:
                xbmcgui.Window(10000).clearProperty(PROPERTY)
            except Exception:
                pass


def dialog_input(heading, defaultt='', type=xbmcgui.INPUT_ALPHANUM, option=0, autoclose=0):
    """Dialog().input() for account data (the same answer: '' on Cancel)."""
    with private():
        return xbmcgui.Dialog().input(heading, defaultt=defaultt, type=type, option=option,
                                      autoclose=autoclose)


def keyboard(default='', heading='', hidden=False):
    """(confirmed, text) of Kodi's keyboard asked for account data."""
    import xbmc
    kb = xbmc.Keyboard(default or '', heading, bool(hidden))
    with private():
        kb.doModal()
    if not kb.isConfirmed():
        return False, ''
    return True, kb.getText() or ''
