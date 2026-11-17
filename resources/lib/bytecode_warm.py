# -*- coding: utf-8 -*-
"""v5.4.7: background bytecode pre-compiler.

Why this exists
---------------
plugin.py alone costs ~373ms to byte-compile on x86 (measured on the
5.4.6 tree) — roughly 1.5-2s on CoreELEC ARM32. The full addon tree is
~1.2s x86 / ~4-6s ARM32 cold. That cost lands on the FIRST click after
every install, update, or wiped __pycache__, exactly when the user is
forming an impression of the addon.

CPython writes __pycache__ automatically on first import, so this cost
was always a one-off — but it was a one-off paid interactively. This
module lets service.py pay it in the background right after Kodi boots,
so the user's first click only ever sees warm-import cost.

Design constraints
------------------
- Never raises: every per-file failure is swallowed (a syntax error in
  one file must not stop the rest, and must not kill the service).
- Abort-aware: checks monitor.abortRequested() between files and uses
  monitor.waitForAbort() as the throttle, so Kodi shutdown is instant.
- Throttled: default 50ms pause after each *actual* compile keeps ARM32
  CPU flat during boot. Freshness checks (the common case on every boot
  after the first) are stat()-only and run unthrottled — the whole tree
  re-check costs single-digit milliseconds.
- Skips tests/, __pycache__/, .git/ — nothing there runs inside Kodi.
"""
import importlib.util
import os
import py_compile
import sys

# v5.10.512: Kodi starts Python with dont_write_bytecode and optimize=1, so an
# import never writes a .pyc and looks only for .opt-1.pyc. Nothing called
# warm() any more and the zips stopped carrying .pyc files (tools/pack.py),
# so every plugin call compiled what it imported again: 0.3-1.1 s a call on a
# Mac, measured 2026-11-04, and several seconds on an Android box. Written
# here at the level Kodi loads, hash-checked: a file changed after this (a
# patch) is still read from its source.
_OPT = sys.flags.optimize or ''
_MODE = py_compile.PycInvalidationMode.CHECKED_HASH

_SKIP_DIRS = ('tests', '__pycache__', '.git')


def _stale(src):
    """True when the cached .pyc is missing or older than the source.

    v5.10.235: the release zip carries hash-checked .pyc files for Python
    3.14 (what Kodi 22 runs; .opt-1.pyc, as Kodi runs Python with
    optimize=1), so the first open after an update compiles nothing. Their
    times say nothing (Kodi writes every file as it unpacks): one is taken
    as fresh, and Python checks its source hash when it loads it."""
    try:
        cached = importlib.util.cache_from_source(src, optimization=_OPT)
        with open(cached, 'rb') as handle:
            head = handle.read(16)
        if len(head) < 16 or head[:4] != importlib.util.MAGIC_NUMBER:
            return True
        if int.from_bytes(head[4:8], 'little') & 1:
            return False        # the import checks the hash itself
        # v5.10.512: as the import judges it, the source's time and size kept
        # in the file (a file newer than its source can still be refused)
        st = os.stat(src)
        return int.from_bytes(head[8:12], 'little') != int(st.st_mtime) & 0xFFFFFFFF \
            or int.from_bytes(head[12:16], 'little') != st.st_size & 0xFFFFFFFF
    except (OSError, ValueError):
        return True


def warm(root, monitor=None, throttle=0.05):
    """Compile every stale .py under *root* to __pycache__.

    Returns (checked, compiled) counts. Safe to call repeatedly; after
    the first successful pass, subsequent calls are stat-only no-ops.
    """
    checked = 0
    compiled = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in sorted(filenames):
            if not name.endswith('.py'):
                continue
            if monitor is not None and monitor.abortRequested():
                return checked, compiled
            src = os.path.join(dirpath, name)
            checked += 1
            if not _stale(src):
                continue
            try:
                py_compile.compile(src, cfile=importlib.util.cache_from_source(src, optimization=_OPT),
                                   doraise=False, quiet=2, optimize=sys.flags.optimize,
                                   invalidation_mode=_MODE)
                compiled += 1
            except OSError:
                # v5.10.512: a folder Kodi cannot write (an app's own
                # copy): the rest would only be compiled and thrown away
                return checked, compiled
            except Exception:
                # doraise=False already swallows compile errors
                continue
            if monitor is not None and throttle > 0:
                if monitor.waitForAbort(throttle):
                    return checked, compiled
    return checked, compiled
