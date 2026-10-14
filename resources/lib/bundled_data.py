# -*- coding: utf-8 -*-
"""Transparent reader for the bundled ``resources/data`` payloads.

The three shipped JSON snapshots are ~5.9 MB pretty-printed and ~0.22 MB as
minified gzip.  The release build compresses them; the working tree keeps the
plain ``.json`` so diffs stay readable.  Every reader goes through here so
BOTH layouts work with no behaviour difference and no version check.

Resolution order for ``foo.json``:
    1. ``foo.json``      — uncompressed (development tree)
    2. ``foo.json.gz``   — compressed   (release build)

Dependencies are stdlib only, plus an optional ``xbmcvfs`` fallback for the
Android / SMB / NFS paths where ``open()`` is not enough.  Importing this
module costs nothing measurable, so call sites can import it eagerly.
"""
import gzip
import io
import json
import os

__all__ = ['resolve', 'read_text', 'read_json']


def resolve(path):
    """Return the path that actually exists — ``path`` or ``path + '.gz'``.

    Falls back to the input unchanged so the caller's own error handling still
    produces a sensible message when neither file is present.
    """
    path = str(path or '')
    if not path:
        return path
    try:
        if os.path.exists(path):
            return path
        if not path.endswith('.gz') and os.path.exists(path + '.gz'):
            return path + '.gz'
    except Exception:
        pass
    return path


def _decompress(raw):
    if not raw:
        return ''
    # gzip magic. Checked on the bytes rather than the extension so a
    # mis-named file still reads correctly.
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    return raw.decode('utf-8', 'replace')


def _read_via_xbmcvfs(path):
    """Android / SMB / NFS paths are not always real OS paths."""
    try:
        import xbmcvfs
    except Exception:
        return None
    fh = None
    try:
        fh = xbmcvfs.File(path, 'rb')
        try:
            raw = bytes(fh.readBytes())
        except Exception:
            # Very old builds only expose read() -> str.
            raw = fh.read()
            if isinstance(raw, str):
                return raw
        return _decompress(raw)
    except Exception:
        return None
    finally:
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass


def read_text(path):
    """Return the decoded text of a bundled payload, or '' on any failure."""
    target = resolve(path)
    if not target:
        return ''
    try:
        if target.endswith('.gz'):
            with gzip.open(target, 'rb') as fh:
                return _decompress(fh.read())
        with io.open(target, 'rb') as fh:
            return _decompress(fh.read())
    except Exception:
        pass
    return _read_via_xbmcvfs(target) or ''


def read_json(path):
    """Return the parsed payload, or None on any failure."""
    text = read_text(path)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None
