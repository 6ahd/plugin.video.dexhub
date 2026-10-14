# -*- coding: utf-8 -*-
"""Native Kodi progress UI for source and catalogue searches.

The old implementation opened a large WindowXMLDialog, populated dozens of
properties, and pushed remote artwork while provider workers were already
busy.  This wrapper keeps the same small API used by plugin.py, but delegates
painting, focus, Back and cancellation to Kodi's own skin.
"""
import time

import xbmcgui

from .i18n import tr
from .log import log


class SourcesLoadingDialog(object):
    """Cancellable native progress dialog with partial-result semantics.

    Back/Cancel before any result is a true abort.  Once at least one useful
    row exists, Back means "use current results".  That is the least
    surprising one-button mapping for Kodi's native DialogProgress and keeps
    the source/search call sites unchanged.
    """

    def __init__(self, title='', subtitle='', fanart='', poster='', clearlogo='',
                 media_type='', imdb_id='', tmdb_id='', provider_count=0,
                 provider_names=None):
        del fanart, poster, clearlogo, media_type, imdb_id, tmdb_id
        self._title = str(title or tr('جار البحث عن المصادر...'))
        self._subtitle = str(subtitle or '')
        self._provider_names = []
        for name in provider_names or []:
            name = str(name or '').strip()
            if name and name not in self._provider_names:
                self._provider_names.append(name)
        self._provider_count = max(0, int(provider_count or 0))
        if not self._provider_count:
            self._provider_count = len(self._provider_names)
        self._dialog = None
        # _win is retained as a compatibility alias for callers/tests that
        # only check whether a progress surface exists.
        self._win = None
        self._opened = False
        self._started_at = time.monotonic()
        self._current_provider = ''
        self._completed = 0
        self._results_so_far = 0
        self._status = tr('جار البحث عن المصادر...')
        self._sub_status = ''
        self._cached_cancelled = False
        self._cached_sufficient = False
        self._last_render_signature = None

    def __enter__(self):
        try:
            self._dialog = xbmcgui.DialogProgress()
            self._win = self._dialog
            try:
                self._dialog.create('Dex Hub', self._message())
            except TypeError:
                self._dialog.create('Dex Hub')
            self._opened = True
            self._render(force=True)
        except Exception as exc:
            log.silent('NATIVE_LOADING', exc)
            self._dialog = None
            self._win = None
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._poll_cancel()
        except Exception:
            pass
        try:
            if self._dialog is not None:
                self._dialog.close()
        except Exception as exc:
            log.silent('NATIVE_LOADING', exc)
        self._dialog = None
        self._win = None
        self._opened = False
        return False

    def _percent(self):
        if self._provider_count <= 0:
            return 0
        return max(0, min(100, int(round(
            100.0 * min(self._completed, self._provider_count) /
            float(self._provider_count)))))

    def _message(self):
        lines = [self._status]
        if self._current_provider:
            lines.append('%s %s' % (tr('يفحص الآن:'), self._current_provider))
        elif self._subtitle:
            lines.append(self._subtitle)
        progress = []
        if self._provider_count:
            progress.append('%s %d/%d' % (
                tr('المصادر'), min(self._completed, self._provider_count),
                self._provider_count))
        if self._results_so_far:
            progress.append('%d %s' % (self._results_so_far, tr('نتيجة')))
        elapsed = max(0.0, time.monotonic() - self._started_at)
        progress.append('%.1f %s' % (elapsed, tr('ث')))
        if self._sub_status:
            progress.insert(0, self._sub_status)
        lines.append('  •  '.join(progress))
        return '\n'.join(line for line in lines[:3] if line)

    def _render(self, force=False):
        if self._dialog is None:
            return
        pct = self._percent()
        message = self._message()
        signature = (pct, message)
        if not force and signature == self._last_render_signature:
            return
        self._last_render_signature = signature
        try:
            self._dialog.update(pct, message)
        except TypeError:
            # Compatibility with old Kodi Python signatures.
            lines = message.split('\n')
            lines += ['', '', '']
            try:
                self._dialog.update(pct, lines[0], lines[1], lines[2])
            except Exception as exc:
                log.silent('NATIVE_LOADING', exc)
        except Exception as exc:
            log.silent('NATIVE_LOADING', exc)

    def _poll_cancel(self):
        if self._cached_cancelled or self._cached_sufficient:
            return True
        if self._dialog is None:
            return False
        try:
            stopped = bool(self._dialog.iscanceled())
        except Exception:
            stopped = False
        if not stopped:
            return False
        if self._results_so_far > 0:
            self._cached_sufficient = True
        else:
            self._cached_cancelled = True
        try:
            self._dialog.close()
        except Exception:
            pass
        return True

    def set_art(self, **_kwargs):
        """Compatibility no-op: native progress deliberately loads no art."""

    def is_cancelled(self):
        self._poll_cancel()
        return bool(self._cached_cancelled)

    def is_sufficient(self):
        self._poll_cancel()
        return bool(self._cached_sufficient)

    def is_stopped(self):
        return bool(self._poll_cancel())

    def update(self, provider_name='', index=0, status=None, sub_status=None):
        if provider_name:
            self._current_provider = str(provider_name)
        if index:
            try:
                self._completed = max(self._completed, int(index))
            except Exception:
                pass
        if status is not None:
            self._status = str(status or '')
        if sub_status is not None:
            self._sub_status = str(sub_status or '')
        self._poll_cancel()
        self._render()

    def add_results(self, n=0, provider_name='', unique_total=None):
        if provider_name:
            self._current_provider = str(provider_name)
        try:
            self._results_so_far += max(0, int(n or 0))
        except Exception:
            pass
        if unique_total is not None:
            try:
                self._results_so_far = max(0, int(unique_total))
            except Exception:
                pass
        self._poll_cancel()
        self._render()

    def mark_sufficient(self, auto_seconds=0):
        del auto_seconds
        self._status = tr('تم الوصول إلى نتائج كافية')
        self._render(force=True)

    def clear_sufficient(self):
        self._status = tr('جار البحث عن المصادر...')
        self._render(force=True)

    def consume_continue_search(self):
        # Kodi's native progress dialog has one cancel action. Patient mode is
        # still available through the source-search policy setting.
        return False

    def set_finalizing(self, msg=None):
        self._completed = max(self._completed, self._provider_count)
        self._status = str(msg or tr('جار تنظيم النتائج...'))
        self._render(force=True)
