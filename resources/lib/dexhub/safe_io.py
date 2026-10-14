# -*- coding: utf-8 -*-
"""Atomic, crash-safe state I/O helpers.

Every JSON state file in DexHub (providers, sessions, Trakt tokens,
collection sets, source preferences, next-up cache, …) goes through
this module so the same crash-safety guarantees apply everywhere:

  * Writes are atomic — a power loss or process kill never leaves a
    half-written file behind.
  * Each successful write also leaves a `.bak` snapshot of the previous
    contents, so the most recent known-good version is recoverable
    even if disk corruption hits the primary file later.
  * Reads automatically recover from `.bak` when the primary is
    unreadable, log a clear warning, and restore the backup as the
    new primary so the next call is clean.

Introduced in v3.9.17 to fix "all my sources disappeared after a
power loss" — the addon's previous `with open('w')` writes were not
crash-safe.
"""
import json
import os
import shutil
import uuid

import xbmc


def write_json(path, value):
    """Atomic JSON write with automatic .bak snapshot.

    Steps:
      1. Snapshot existing file to ``path + '.bak'`` (if any).
      2. Write to ``path + '.tmp.<pid>'``, fsync, close.
      3. Atomically rename the temp onto the destination.

    If the process is killed at any step, ``path`` is either the old
    valid contents or the new valid contents — never partially written.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + '.bak')
        except Exception as exc:
            xbmc.log('[DexHub] backup snapshot failed for %s: %s' % (path, exc),
                     xbmc.LOGDEBUG)
    path = os.fspath(path)
    tmp_path = '%s.tmp.%d.%s' % (path, os.getpid(), uuid.uuid4().hex)
    try:
        with open(tmp_path, 'w', encoding='utf-8') as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except Exception:
                # Some platforms (Android scoped storage, certain SMB
                # mounts) reject fsync. The atomic rename is the more
                # important guarantee.
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise


def read_json(path, default):
    """Read a JSON file with `.bak` recovery on corruption.

    If the primary file is missing the default is returned silently —
    that's the normal "first run" case. If the primary is *present but
    unreadable* (truncated write, manual edit gone wrong, disk error),
    the backup is tried; if it works, it's restored as the new primary.
    """
    path = os.fspath(path)
    primary_err = None
    for candidate, is_backup in ((path, False), (path + '.bak', True)):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            if not isinstance(data, type(default)):
                raise ValueError('expected %s, got %s' %
                                  (type(default).__name__, type(data).__name__))
            if is_backup:
                xbmc.log('[DexHub] Recovered %s from .bak after primary corruption' % path,
                         xbmc.LOGWARNING)
                try:
                    shutil.copy2(candidate, path)
                except Exception:
                    pass
            return data
        except Exception as exc:
            if not is_backup:
                primary_err = exc
                xbmc.log('[DexHub] Corrupt %s (%s) — trying backup' %
                         (path, exc), xbmc.LOGWARNING)
            else:
                xbmc.log('[DexHub] Backup also unusable for %s: %s' %
                         (path, exc), xbmc.LOGERROR)
    if primary_err is not None:
        xbmc.log('[DexHub] Falling back to default for %s — primary error: %s' %
                 (path, primary_err), xbmc.LOGERROR)
    return default
