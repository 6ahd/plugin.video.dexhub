# -*- coding: utf-8 -*-
"""Centralized logging for Dex Hub.

Replaces ad-hoc `xbmc.log('[DexHub] ...', xbmc.LOGDEBUG)` calls scattered
across the codebase. Provides:

* Category prefixes (PLAYBACK, STREAMS, CW, CATALOG, META, TRAKT, etc.)
* Lazy formatting — args are only formatted when the level is actually printed
* Automatic redaction of API keys, tokens, and passwords in any log payload
* Optional exc_info=True for full traceback on errors

Usage:
    from .log import log
    log.debug('META', 'cache hit %s', cache_key)
    log.info('PLAYBACK', 'started %s via %s', title, provider)
    log.warn('STREAMS', 'addon %s timed out', name)
    log.error('CW', 'sync failed: %s', exc, exc_info=True)
"""
import re
import time
from . import lazy_tb as _tb

import xbmc

_PREFIX = '[DexHub]'

# Patterns that match common secret shapes. Anything matching gets replaced
# with '***' so the cleartext never reaches the Kodi log file or any user
# screenshot of it.
_REDACT_PATTERNS = [
    # query-string and key=value forms
    re.compile(r'(api[_-]?key=)[A-Za-z0-9._\-]+', re.IGNORECASE),
    re.compile(r'(token=)[A-Za-z0-9._\-]+', re.IGNORECASE),
    re.compile(r'(access[_-]?token=)[A-Za-z0-9._\-]+', re.IGNORECASE),
    re.compile(r'(refresh[_-]?token=)[A-Za-z0-9._\-]+', re.IGNORECASE),
    re.compile(r'(password=)[^\s&]+', re.IGNORECASE),
    re.compile(r'(secret=)[A-Za-z0-9._\-]+', re.IGNORECASE),
    # Plex token in URL: ?X-Plex-Token=...
    re.compile(r'(X-Plex-Token=)[A-Za-z0-9._\-]+', re.IGNORECASE),
    # Bearer headers
    re.compile(r'(Bearer\s+)[A-Za-z0-9._\-]+'),
    # JSON-style "key": "value" for known sensitive keys
    re.compile(r'("(?:api_key|token|access_token|refresh_token|password|secret|client_secret)"\s*:\s*")[^"]+(")', re.IGNORECASE),
]


def _redact(text):
    """Replace any matched secret with the same key prefix + '***'.

    Idempotent: running twice gives the same output. Safe on non-string input.
    """
    if text is None:
        return text
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return '<unprintable>'
    for pat in _REDACT_PATTERNS:
        try:
            text = pat.sub(lambda m: m.group(1) + '***' + (m.group(2) if m.lastindex and m.lastindex >= 2 else ''), text)
        except Exception:
            pass
    return text


_LEVEL_MAP = {
    'debug':   xbmc.LOGDEBUG,
    'info':    xbmc.LOGINFO,
    'notice':  xbmc.LOGINFO,
    'warning': xbmc.LOGWARNING,
    'warn':    xbmc.LOGWARNING,
    'error':   xbmc.LOGERROR,
    'fatal':   xbmc.LOGFATAL,
}


def _emit(level_name, category, msg, args, exc_info=False):
    level = _LEVEL_MAP.get(level_name, xbmc.LOGINFO)
    try:
        if args:
            try:
                rendered = msg % args
            except Exception:
                # Fallback if format args don't match — log raw + repr.
                rendered = '%s | args=%r' % (msg, args)
        else:
            rendered = str(msg)
        rendered = _redact(rendered)
        cat = ('[%s]' % category.upper()) if category else ''
        xbmc.log('%s%s %s' % (_PREFIX, cat, rendered), level)
        if exc_info:
            xbmc.log('%s%s traceback:\n%s' % (_PREFIX, cat, _redact(_tb.format_exc())), level)
    except Exception:
        # Logging must never raise.
        try:
            xbmc.log('%s logging failure' % _PREFIX, xbmc.LOGERROR)
        except Exception:
            pass


class _Log:
    """Tiny facade so callers write log.debug(...) instead of log_debug(...)."""

    def debug(self, category, msg, *args, exc_info=False):
        _emit('debug', category, msg, args, exc_info)

    def info(self, category, msg, *args, exc_info=False):
        _emit('info', category, msg, args, exc_info)

    def warn(self, category, msg, *args, exc_info=False):
        _emit('warn', category, msg, args, exc_info)

    warning = warn

    def error(self, category, msg, *args, exc_info=False):
        _emit('error', category, msg, args, exc_info)

    def fatal(self, category, msg, *args, exc_info=False):
        _emit('fatal', category, msg, args, exc_info)

    # ──────────────────────────────────────────────────────────────
    # v3.9.90: silent-exception API
    #
    # The codebase has ~426 `except Exception: pass` blocks. They were
    # added defensively (Kodi GUI calls can fail on different platforms,
    # Stremio addons return malformed JSON, etc.) but the side effect
    # is that genuine bugs are swallowed without a trace. Every future
    # bug report becomes a guessing game.
    #
    # log.silent() is the drop-in replacement: same defensive behavior,
    # but the exception is recorded for diagnostics. The Kodi-log
    # output is LOGDEBUG by default so existing log files stay quiet;
    # turning on the "Verbose logging" setting bumps it to LOGINFO so
    # silent errors become visible in the default log.
    #
    # The last error is also kept in _last_silent_error so the user
    # can show it via the settings action "Copy last silent error" —
    # no need to dig through kodi.log just to file a bug report.
    # ──────────────────────────────────────────────────────────────

    def silent(self, category, exc, context_msg=''):
        """Record an exception that was deliberately swallowed.

        Usage:
            try:
                window.setProperty('dexhub.results.poster', url)
            except Exception as exc:
                log.silent('RESULTS_WIN', exc, 'setProperty poster')

        Behavior:
          * Always updates the internal last-error tracker.
          * Logs at LOGDEBUG unless verbose_logging is on, then LOGINFO.
          * Never raises (logging must never break the caller).
        """
        global _last_silent_error
        try:
            if isinstance(exc, BaseException):
                msg = '%s: %s' % (exc.__class__.__name__, exc)
            else:
                msg = str(exc)
            full = ('%s — %s' % (context_msg, msg)) if context_msg else msg
            _last_silent_error = {
                'when': time.time(),
                'category': category or '?',
                'message': _redact(full),
                'traceback': _redact(_tb.format_exc()) if isinstance(exc, BaseException) else '',
            }
            level_name = 'info' if _verbose_logging_enabled() else 'debug'
            _emit(level_name, category, '[silent] %s', (full,))
        except Exception:
            try:
                xbmc.log('%s log.silent failure' % _PREFIX, xbmc.LOGERROR)
            except Exception:
                pass


log = _Log()


# ──────────────────────────────────────────────────────────────────
# Silent-error state (module-level singletons).
# These are READ by:
#   * the "Copy last silent error" action in plugin.py
#   * developer tooling — `get_last_silent_error()` from anywhere
# ──────────────────────────────────────────────────────────────────

_last_silent_error = {
    'when': None,
    'category': '',
    'message': '',
    'traceback': '',
}


def _verbose_logging_enabled():
    """Read the verbose_logging setting on demand. Cached for one
    invocation only — Kodi reuses the Python interpreter between
    plugin calls under reuselanguageinvoker, but settings can change
    between calls, so we accept the small cost of one getSetting()
    call per silent() invocation."""
    try:
        import xbmcaddon
        return (xbmcaddon.Addon().getSetting('verbose_logging') or 'false').strip().lower() == 'true'
    except Exception:
        return False


def get_last_silent_error():
    """Return a copy of the most recent silent error record. Used by
    the settings action to surface the error in a dialog/clipboard."""
    return dict(_last_silent_error)


