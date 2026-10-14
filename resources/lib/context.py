# -*- coding: utf-8 -*-
"""Shared state layer for plugin.video.dexhub.

This module centralises the module-level state that was previously defined
inline at the top of the monolithic ``plugin.py``:

  * the Kodi addon handle (``ADDON``) and per-invocation routing values
    (``HANDLE``, ``BASE_URL``),
  * static lookup tables (``LANG_MAP``, ``FILTER_NAMES``, ``HEX_ENTITIES``,
    ``SPECIAL_BLANKS``),
  * pre-compiled regular expressions used across render/clean paths.

Design notes
------------
* This module must stay import-cheap and side-effect free *except* for the
  two unavoidable module singletons Kodi requires: ``xbmcaddon.Addon()`` and
  reading ``sys.argv``.  In particular it does **not** call
  ``_apply_clean_defaults_once()`` at import time; that one-shot settings
  migration is still triggered explicitly from ``plugin.py`` so it can never
  run twice just because two modules import this one.
* ``plugin.py`` re-exports every name defined here (``from .context import *``)
  so existing call sites inside ``plugin.py`` keep working unchanged, and the
  ``_dispatch`` action table continues to resolve names in its own namespace.
* New extracted modules should import what they need directly from here, e.g.
  ``from .context import ADDON, BASE_URL, build_url``.
"""

import os
import re
import sys
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from urllib.parse import urlencode

from .i18n import tr


# ─────────────────────────────────────────────────────────────────────
# Kodi addon handle + per-invocation routing values.
#
# HANDLE / BASE_URL come from sys.argv, which Kodi populates on every
# plugin invocation (argv[1] = handle, argv[0] = base plugin:// url).
# ADDON is the single Addon() instance shared by the whole process.
# ─────────────────────────────────────────────────────────────────────
# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()

try:
    HANDLE = int(sys.argv[1])
except Exception:
    # RunScript-style / test-harness processes don't carry a plugin handle.
    HANDLE = -1
# v4.6.4: BASE_URL is a CONSTANT derived from the addon id — never argv.
# With reuselanguageinvoker=true this module imports once per warm VM, so a
# value captured from sys.argv here is frozen at whatever the FIRST
# invocation in that VM happened to be. A RunPlugin/settings invocation
# (handle -1) spawning a fresh VM then poisoned every later folder render
# that reused it: directories built on a dead handle → the empty ".."
# screen, and clicks resolving against a broken base ("Unable to find
# plugin / GetDirectory(plugin://) failed" in kodi.log).
BASE_URL = 'plugin://%s/' % (ADDON.getAddonInfo('id') or 'plugin.video.dexhub')


def refresh_invocation():
    """Re-read the per-invocation Kodi handle. MUST run at the start of
    every dispatch (plugin._reset_invocation_state) because module globals
    survive between invocations under reuselanguageinvoker."""
    global HANDLE
    try:
        HANDLE = int(sys.argv[1])
    except Exception:
        HANDLE = -1
    return HANDLE

WINDOW_ID = 10000
PROP = 'dexhub.play_context'
SERIES_PROP_PREFIX = 'dexhub.series.'
SUBS_DIR = xbmcvfs.translatePath('special://temp/dexhub_subs/')


# ─────────────────────────────────────────────────────────────────────
# Static lookup tables.
# ─────────────────────────────────────────────────────────────────────
LANG_MAP = {
    'ar': ('🇸🇦', 'Arabic'), 'ara': ('🇸🇦', 'Arabic'), 'arabic': ('🇸🇦', 'Arabic'),
    'en': ('🇺🇸', 'English'), 'eng': ('🇺🇸', 'English'), 'english': ('🇺🇸', 'English'),
    'fr': ('🇫🇷', 'French'), 'fre': ('🇫🇷', 'French'), 'fra': ('🇫🇷', 'French'), 'french': ('🇫🇷', 'French'),
    'es': ('🇪🇸', 'Spanish'), 'spa': ('🇪🇸', 'Spanish'), 'spanish': ('🇪🇸', 'Spanish'),
    'de': ('🇩🇪', 'German'), 'deu': ('🇩🇪', 'German'), 'ger': ('🇩🇪', 'German'), 'german': ('🇩🇪', 'German'),
    'it': ('🇮🇹', 'Italian'), 'ita': ('🇮🇹', 'Italian'), 'italian': ('🇮🇹', 'Italian'),
    'pt': ('🇵🇹', 'Portuguese'), 'por': ('🇵🇹', 'Portuguese'), 'portuguese': ('🇵🇹', 'Portuguese'),
    'pt-br': ('🇧🇷', 'Portuguese BR'), 'pob': ('🇧🇷', 'Portuguese BR'), 'pb': ('🇧🇷', 'Portuguese BR'), 'brazilian': ('🇧🇷', 'Portuguese BR'),
    'tr': ('🇹🇷', 'Turkish'), 'tur': ('🇹🇷', 'Turkish'), 'turkish': ('🇹🇷', 'Turkish'),
    'ru': ('🇷🇺', 'Russian'), 'rus': ('🇷🇺', 'Russian'), 'russian': ('🇷🇺', 'Russian'),
    'ja': ('🇯🇵', 'Japanese'), 'jpn': ('🇯🇵', 'Japanese'), 'japanese': ('🇯🇵', 'Japanese'),
    'ko': ('🇰🇷', 'Korean'), 'kor': ('🇰🇷', 'Korean'), 'korean': ('🇰🇷', 'Korean'),
    'zh': ('🇨🇳', 'Chinese'), 'chi': ('🇨🇳', 'Chinese'), 'zho': ('🇨🇳', 'Chinese'), 'chinese': ('🇨🇳', 'Chinese'),
    'hi': ('🇮🇳', 'Hindi'), 'hin': ('🇮🇳', 'Hindi'), 'hindi': ('🇮🇳', 'Hindi'),
    'fa': ('🇮🇷', 'Persian'), 'fas': ('🇮🇷', 'Persian'), 'per': ('🇮🇷', 'Persian'), 'persian': ('🇮🇷', 'Persian'),
    'und': ('🏳️', 'Unknown'),
}

FILTER_NAMES = {
    'search': 'Search', 'sort': 'Sort', 'sortBy': 'Sort', 'sortby': 'Sort', 'orderBy': 'Sort', 'orderby': 'Sort', 'order': 'Order',
    'genre': 'Genre', 'availability': 'Availability', 'year': 'Year', 'language': 'Language', 'country': 'Country',
    'contentRating': 'Rating', 'studio': 'Studio', 'network': 'Network', 'collection': 'Collection', 'unwatched': 'Unwatched',
    'lastRelease': 'Last Release', 'last_release': 'Last Release', 'lastAdded': 'Last Added', 'last_added': 'Last Added',
}

HEX_ENTITIES = [
    ('&#x26;', '&'), ('&#x27;', "'"), ('&#xC6;', 'AE'), ('&#xC7;', 'C'), ('&#xF4;', 'o'),
    ('&#xE9;', 'e'), ('&#xEB;', 'e'), ('&#xED;', 'i'), ('&#xEE;', 'i'), ('&#xA2;', 'c'),
    ('&#xE2;', 'a'), ('&#xEF;', 'i'), ('&#xE1;', 'a'), ('&#xE8;', 'e'), ('%2E', '.'),
    ('&frac12;', '%BD'), ('&#xBD;', '%BD'), ('&#xB3;', '%B3'), ('&#xB0;', '%B0'),
    ('&amp;', '&'), ('&#xB7;', '.'), ('&#xE4;', 'A'), ('\xe2\x80\x99', '')
]
SPECIAL_BLANKS = [
    ('"', ' '), ('/', ' '), (':', ' '), ('<', ' '), ('>', ' '), ('?', ' '), ('\\', ' '), ('|', ' '),
    ('%BD;', ' '), ('%B3;', ' '), ('%B0;', ' '), ("'", ''), (' - ', ' '), ('.', ' '), ('!', ''), (';', ''), (',', ' ')
]


# ─────────────────────────────────────────────────────────────────────
# Pre-compiled regexes (shared across clean/render/parse paths).
# ─────────────────────────────────────────────────────────────────────
COLOR_TAG_RE = re.compile(r'\[/?(?:COLOR|B|I|UPPERCASE|LOWERCASE|LIGHT)\b[^\]]*\]', re.I)
STREAM_ICON_RE = re.compile(r'[\u2500-\u257F\u2580-\u259F\u25A0-\u25FF\u2600-\u27BF\uE000-\uF8FF\U0001F300-\U0001FAFF]')
REGIONAL_FLAG_RE = re.compile(r'[\U0001F1E6-\U0001F1FF]{2}')
BRACKET_RE = re.compile(r'\[[^\]]*\]')
MULTI_WS_RE = re.compile(r'\s+')
QUALITY_RE = re.compile(r'\b(?:2160p|4k|1080p|720p|480p|sd|hdr10\+|hdr10|hdr|dv|dovi|dolby\s*vision|remux|bluray|blu\s*ray|web[- ]?dl|webrip|x265|x264|hevc|av1|aac|dts(?:-?hd)?|truehd|atmos|ddp(?:5\.1)?|5\.1|7\.1)\b', re.I)
SIZE_RE = re.compile(r'(\d+(?:\.\d+)?\s*(?:GB|MB|GiB|MiB))', re.I)
YEAR_RE = re.compile(r'^(\d{4})')


# ─────────────────────────────────────────────────────────────────────
# Small URL / notification / settings primitives.
#
# These are pure helpers over the shared state above.  They live here so
# extracted modules can import them without pulling in plugin.py.
# ─────────────────────────────────────────────────────────────────────
def _home_flagged():
    try:
        return 'dh_home=1' in (sys.argv[2] if len(sys.argv) > 2 else '')
    except Exception:
        return False


def build_url(**query):
    # v5.10.105: a page opened from the Dex Hub Home passes the Home's flag
    # on to every route it builds (its seasons, episodes, next page), so the
    # classic "open in TMDb Helper" click mode never takes a later page away.
    if query and 'dh_home' not in query and _home_flagged():
        query['dh_home'] = '1'
    return BASE_URL + '?' + urlencode(query)


def _capture_quiet(msg, level):
    # v5.10.97: the Dex Hub Home window loads its rows by running the normal
    # routes in the background. A slow or broken catalog must not throw a
    # toast over the Home screen for every row; it is logged, and an error
    # marks the row as failed so the window keeps what it already shows.
    try:
        from . import capture as _capture
        if not _capture.quiet():
            return False
        _capture.note(tr(msg), level)
        return True
    except Exception:
        return False


def notify(msg):
    if _capture_quiet(msg, 'info'):
        return
    xbmcgui.Dialog().notification('Dex Hub', tr(msg), xbmcgui.NOTIFICATION_INFO, 2500)


def error(msg):
    if _capture_quiet(msg, 'error'):
        return
    xbmcgui.Dialog().notification('Dex Hub', tr(msg), xbmcgui.NOTIFICATION_ERROR, 3500)


def _get_int_setting(key, default, minimum=None, maximum=None):
    try:
        value = int(ADDON.getSetting(key) or default)
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _get_bool_setting(key, default=False):
    try:
        raw = (ADDON.getSetting(key) or ('true' if default else 'false')).strip().lower()
    except Exception:
        raw = 'true' if default else 'false'
    return raw in ('true', '1', 'yes', 'on')


def _pagination_hidden():
    # Defensive pagination visibility helper.  It is intentionally kept in the
    # shared layer because several catalogue/collection routes use it while
    # building folders.  Some previous builds had the helper below/removed,
    # which made collection_entry_open crash with:
    # name '_pagination_hidden' is not defined.
    # v5.10.96: the Dex Hub Home window reads the "More" item to page its
    # rows and grids, so a captured route always keeps it.
    try:
        from . import capture as _capture
        if _capture.active():
            return False
    except Exception:
        pass
    try:
        raw = (ADDON.getSetting('hide_pagination_more') or 'true').strip().lower()
    except Exception:
        return False
    return raw in ('true', '1', 'yes', 'on')


def apply_clean_defaults_once():
    """Apply the v3.9.139 clean baseline once for existing installs.

    settings.xml defaults only affect brand-new profiles.  Existing Kodi
    profiles keep old stored values, so the clean baseline would not take
    effect after updating unless we migrate it once.  After this revision is
    marked applied, user changes are respected and never overwritten again.

    NOTE: this is *not* invoked at import time.  ``plugin.py`` calls it once
    during module load so the one-shot migration can never run twice.
    """
    # v4.7.6: read through a FRESH instance — a warm process's proxy can
    # hold a revision from before the user's last settings change and
    # re-trigger a migration that has already run.
    try:
        _fresh = xbmcaddon.Addon()
    except Exception:
        _fresh = ADDON
    try:
        rev = (_fresh.getSetting('dexhub_defaults_rev') or '').strip()
    except Exception:
        rev = ''
    if rev == '470-release-defaults':
        return
    # v5.1 briefly reused this legacy string sentinel for a numeric service
    # migration. Treat those shipped revisions as already clean; otherwise a
    # plugin invocation after service startup could reapply old UI defaults
    # and overwrite later user choices.
    try:
        if int(rev) >= 510:
            return
    except Exception:
        pass
    # v4.7.6: never write while the addon's settings dialog is on screen.
    # Kodi's dialog holds its own copy and the two saves race — whichever
    # lands last wins, which is how a migration write could erase the URL
    # the user had just typed.
    try:
        if xbmc.getCondVisibility('Window.IsVisible(addonsettings)'):
            return
    except Exception:
        pass
    if rev == '139-clean-defaults':
        # v4.7.0 corrective pass: the 139 migration WROTE
        # clean_catalog_view='true' into every existing install — the exact
        # v3.9.82 regression mechanism (breaks the "+ Add to home"
        # workflow). One-time reset back to the PERMANENT 'false', then
        # stamp the new revision so user choices are respected afterwards.
        try:
            if (_fresh.getSetting('clean_catalog_view') or '').strip().lower() != 'false':
                ADDON.setSetting('clean_catalog_view', 'false')
        except Exception:
            pass
        try:
            ADDON.setSetting('dexhub_defaults_rev', '470-release-defaults')
        except Exception:
            pass
        return
    defaults = {
        'default_meta_source': 'Native (addon)',
        # PERMANENT: must stay 'false' — 'true' broke the "+ Add to
        # home" workflow (v3.9.82 regression, fixed since).
        'clean_catalog_view': 'false',
        'hide_pagination_more': 'true',
        'poster_reliability_mode': 'Always clean (no badges)',
    }
    for key, value in defaults.items():
        try:
            ADDON.setSetting(key, value)
        except Exception as exc:
            try:
                xbmc.log('[DexHub] clean defaults setSetting(%s) failed: %s' % (key, exc), xbmc.LOGWARNING)
            except Exception:
                pass
    try:
        ADDON.setSetting('dexhub_defaults_rev', '470-release-defaults')
    except Exception:
        pass
