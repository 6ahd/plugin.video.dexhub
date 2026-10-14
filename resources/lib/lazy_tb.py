# -*- coding: utf-8 -*-
"""traceback, loaded the first time an error is written (v5.10.235).

Python 3.14's traceback module pulls in _colorize, dataclasses, inspect,
textwrap and tokenize: 82 modules, about 26 ms on a desktop and over half a
second while Kodi starts (every plugin call is a fresh interpreter and they
start together). Dex Hub only needs it inside except blocks, so modules
import this one in its place:

    from .. import lazy_tb as traceback
    ...
    except Exception:
        log(traceback.format_exc())
"""


def format_exc(limit=None, chain=True):
    import traceback
    return traceback.format_exc(limit=limit, chain=chain)


def format_exception(*args, **kwargs):
    import traceback
    return traceback.format_exception(*args, **kwargs)


def print_exc(limit=None, file=None, chain=True):
    import traceback
    return traceback.print_exc(limit=limit, file=file, chain=chain)


def __getattr__(name):
    import traceback
    return getattr(traceback, name)
