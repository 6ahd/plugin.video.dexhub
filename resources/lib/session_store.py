# -*- coding: utf-8 -*-
"""Session store for active playback context.

Uses Kodi's Home window properties as the primary store (RAM, ~100x faster
than disk on every read/write). Falls back to a profile JSON file only when
xbmcgui isn't available (rare — happens during very early service startup
before the GUI is initialized).

The session is consulted on every progress tick by CompanionPlayer, so the
property-vs-file difference compounds quickly.
"""
from . import profile_scope as _scope
import json
import os

from .dexhub.common import profile_path

SESSION_FILE = os.path.join(profile_path(), "current_session.json")
WINDOW_ID = 10000  # Home window — survives across plugin invocations
PROP_KEY = 'dexhub.session_v1'


def _window():
    try:
        import xbmcgui
        return xbmcgui.Window(WINDOW_ID)
    except Exception:
        return None


def save_session(data):
    data = dict(data or {})
    data.setdefault('profile_scope', _scope.key())
    payload = json.dumps(data, ensure_ascii=False)
    win = _window()
    if win is not None:
        try:
            win.setProperty(PROP_KEY, payload)
            return
        except Exception:
            pass
    # Fallback: disk. v3.9.17: atomic write so a crash mid-save doesn't
    # leave a corrupt session file that wipes the user's Plex/Emby login.
    try:
        # We're writing a JSON-shaped string; reuse the atomic primitive
        # directly so behavior matches the rest of the addon.
        from .dexhub.safe_io import write_json as _safe_write_json
        try:
            _safe_write_json(SESSION_FILE, json.loads(payload))
        except Exception:
            # Payload wasn't strict JSON (shouldn't happen, but be defensive)
            # — fall back to an atomic raw write.
            os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
            tmp = SESSION_FILE + '.tmp.%d' % os.getpid()
            with open(tmp, 'w', encoding='utf-8') as fh:
                fh.write(payload)
            os.replace(tmp, SESSION_FILE)
    except Exception:
        pass


def load_session():
    win = _window()
    if win is not None:
        try:
            raw = win.getProperty(PROP_KEY) or ''
            if raw:
                return _session_for_profile(json.loads(raw))
        except Exception:
            pass
    # Fallback: disk
    try:
        with open(SESSION_FILE, 'r', encoding='utf-8') as fh:
            return _session_for_profile(json.load(fh))
    except Exception:
        return {}


def clear_session():
    win = _window()
    if win is not None:
        try:
            win.clearProperty(PROP_KEY)
        except Exception:
            pass
    try:
        os.remove(SESSION_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _session_for_profile(data):
    if not isinstance(data, dict) or data.get('profile_scope') != _scope.key():
        return {}
    return data
