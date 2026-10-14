# -*- coding: utf-8 -*-
"""Loading window shown while Dex Hub gathers sources from providers.

Provides a cancellable, POV/SALTS-style "Searching..." overlay with live
provider progress. The window is a `WindowXMLDialog` so it renders above
Kodi's main window (including over the catalog listing).

Usage:

    from .sources_loading import SourcesLoadingDialog

    with SourcesLoadingDialog(title='Killers of the Flower Moon (2023)',
                              provider_count=5) as loader:
        for idx, provider in enumerate(providers, 1):
            if loader.is_cancelled():
                break
            loader.update(provider_name=provider['name'], index=idx,
                          results_so_far=len(found))
            # fetch...
            loader.add_results(n=len(new_results))

The dialog auto-closes when the `with` block exits. If `is_cancelled()`
returns True, the caller should abort its fetch loop.
"""
import os
import time
from .log import log

import xbmc
import xbmcgui
import xbmcaddon

from .i18n import tr

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()

_SKIN_FILE = 'sources_loading.xml'
_SKIN_RES = 'Default'
_SKIN_DIMS = '1080i'

_CTRL_FANART = 5101
_CTRL_POSTER = 5102
_CTRL_CLEARLOGO = 5103


_TRACE_T0 = [0.0]


def _trace(msg):
    """v5.10.30: INFO-level scan trace. Kodi 22 beta on CoreELEC has frozen
    the GUI mid-scan with nothing in the log; these lines show the last
    window operation before a freeze without needing debug logging."""
    try:
        import time as _t
        now = _t.monotonic()
        if not _TRACE_T0[0]:
            _TRACE_T0[0] = now
        xbmc.log('[DexHub] scan-ui +%.1fs %s' % (now - _TRACE_T0[0], msg), xbmc.LOGINFO)
    except Exception:
        pass


def _addon_path():
    return ADDON.getAddonInfo('path')


class _LoadingWindow(xbmcgui.WindowXMLDialog):
    """Internal dialog. Prefer SourcesLoadingDialog context manager."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cancelled = False
        # v3.9.32: distinguish "abort the whole flow" (cancel) from
        # "stop scanning more sources but keep the ones already found"
        # (sufficient). Both close the dialog; the caller chooses how
        # to react via is_cancelled() vs is_sufficient().
        self._sufficient = False
        self._continue_search = False
        # v3.9.52: initial art values captured by the wrapper before
        # show(); applied in onInit() so the dialog never displays
        # stale artwork from a previous item.
        self._init_poster = ''
        self._init_fanart = ''
        self._init_clearlogo = ''

    def _push_art_controls(self, poster='', fanart='', clearlogo=''):
        """Push artwork directly into named image controls.

        Kodi's $INFO[Window.Property(...)] bindings can be late/empty on some
        WindowXMLDialog openings, especially with URL/proxy images.  Direct
        setImage mirrors source_browser/playback_wait and makes the loading
        sheet show the same poster/clearlogo as the results page.

        v3.9.108: any value that points to DexHub's own packaged fanart/icon
        is treated as 'no art' here too — defense in depth in case a caller
        forgot to clean its art dict.
        """
        def _self_art(v):
            if not v:
                return False
            try:
                s = str(v).lower()
            except Exception:
                return False
            if 'plugin.video.dexhub' not in s:
                return False
            return ('/resources/media/' in s or '/resources/icon' in s
                    or 'fanart.jpg' in s or 'icon.png' in s)

        if _self_art(fanart):
            fanart = ''
        if _self_art(poster):
            poster = ''
        if _self_art(clearlogo):
            clearlogo = ''
        try:
            if fanart:
                self.getControl(_CTRL_FANART).setImage(fanart, useCache=False)
                self.getControl(_CTRL_FANART).setVisible(True)
            else:
                self.getControl(_CTRL_FANART).setVisible(False)
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
        try:
            if poster:
                self.getControl(_CTRL_POSTER).setImage(poster, useCache=False)
                self.getControl(_CTRL_POSTER).setVisible(True)
            else:
                self.getControl(_CTRL_POSTER).setVisible(False)
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
        try:
            if clearlogo:
                self.getControl(_CTRL_CLEARLOGO).setImage(clearlogo, useCache=False)
                self.getControl(_CTRL_CLEARLOGO).setVisible(True)
            else:
                self.getControl(_CTRL_CLEARLOGO).setVisible(False)
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
    def onInit(self):
        try:
            from . import skin_theme
            skin_theme.publish_theme(self)
        except Exception:
            pass
        # v3.9.55: namespaced property names. The previous v3.9.52 fix
        # of clearing properties on init was insufficient because the
        # global Window(10000) home window keeps its own 'poster',
        # 'fanart', and 'clearlogo' properties — set by many code paths
        # throughout plugin.py — and Kodi's $INFO[Window.Property(key)]
        # resolver was reading from there for our dialog, not from the
        # dialog's own properties. By switching to dexhub.loading.*
        # names we guarantee no conflict with any other code, and the
        # dialog only sees what THIS specific call set.
        for prop in ('dexhub.loading.poster',
                     'dexhub.loading.fanart',
                     'dexhub.loading.clearlogo'):
            try:
                self.setProperty(prop, '')
            except Exception as _silent_exc:
                log.silent('LOADING_DLG', _silent_exc)
        try:
            if self._init_poster:
                self.setProperty('dexhub.loading.poster', self._init_poster)
            if self._init_fanart:
                self.setProperty('dexhub.loading.fanart', self._init_fanart)
            if self._init_clearlogo:
                self.setProperty('dexhub.loading.clearlogo', self._init_clearlogo)
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
        self._push_art_controls(self._init_poster, self._init_fanart, self._init_clearlogo)
        # No rows exist on first paint, so focus the always-available Cancel
        # action.  The wrapper moves focus to "Show results now" as soon as the
        # first usable, de-duplicated row arrives.
        try:
            self.setFocusId(9000)
        except Exception:
            try:
                self.setFocusId(9001)
            except Exception as _silent_exc:
                log.silent('LOADING_DLG', _silent_exc)
    def onAction(self, action):
        action_id = action.getId()
        # Remote/keyboard support: explicitly cycle the three actions because
        # a few CoreELEC/Kodi 22 builds do not honour WindowXML navigation on a
        # modal dialog consistently.
        if action_id in (1, 2, 3, 4):
            try:
                current = self.getFocusId()
            except Exception:
                current = 9001
            order = (9001, 9002, 9000)
            try:
                pos = order.index(current)
            except ValueError:
                pos = 0
            delta = -1 if action_id in (1, 3) else 1
            try:
                self.setFocusId(order[(pos + delta) % len(order)])
            except Exception as _silent_exc:
                log.silent('LOADING_DLG', _silent_exc)
            return
        if action_id in (10, 92, 216, 247, 257, 275):  # ACTION_PREVIOUS_MENU / BACK
            self._cancelled = True
            self.close()
        else:
            super().onAction(action)

    def onClick(self, control_id):
        if control_id == 9001:
            # Keep the button focusable from the first frame (Kodi otherwise
            # logs a focus error when the first result arrives), but ignore an
            # early click until there is a real partial result to show.
            if int(getattr(self, '_results_so_far', 0) or 0) <= 0:
                return
            # Show now — stop scanning and keep the useful partial result set.
            self._sufficient = True
            self.close()
        elif control_id == 9002:
            # Keep waiting even after the automatic sufficiency threshold.
            self._continue_search = True
            try:
                self.setProperty('enough_state', '')
                self.setProperty('hint_line', tr('سيستمر البحث حتى اكتمال المصادر أو انتهاء المهلة'))
            except Exception as _silent_exc:
                log.silent('LOADING_DLG', _silent_exc)
        elif control_id == 9000:
            # "Cancel" — abort the whole flow, drop results.
            self._cancelled = True
            self.close()

    def is_cancelled(self):
        return self._cancelled

    def is_sufficient(self):
        return self._sufficient

    def is_stopped(self):
        """True when either button was pressed. Useful for breaking
        out of the parallel loop without caring which intent applied."""
        return self._cancelled or self._sufficient

    def consume_continue_search(self):
        value = bool(self._continue_search)
        self._continue_search = False
        return value


class SourcesLoadingDialog(object):
    """Context-managed wrapper around the loading WindowXMLDialog.
    Safe to use even if the XML is missing — falls back to a no-op so the
    caller never crashes. All setter methods silently no-op when the
    window isn't actually showing.
    """

    def __init__(self, title='', subtitle='', fanart='', poster='', clearlogo='',
                 media_type='', imdb_id='', tmdb_id='', provider_count=0,
                 provider_names=None):
        self._title = title
        self._subtitle = subtitle
        self._fanart = fanart
        # v3.9.89: route poster sourcing through art.clean_poster so the
        # loading dialog uses the SAME TMDb-first policy as the player
        # overlay and the results window. Order: (1) MetaHub/TMDb URL
        # when an IMDb id is known — wins over any addon URL; (2) the
        # addon-provided URL when it passes the logo-suspect check;
        # (3) the local portrait placeholder.
        #
        # When neither poster nor IDs nor media_type are given (e.g. the
        # unified search context where there is no current item yet),
        # leave the slot empty so the XML hides the control via its
        # <visible> rule.
        if poster or imdb_id or tmdb_id or media_type:
            try:
                from .art import clean_poster
                self._poster = clean_poster(
                    {'poster': poster, 'imdb_id': imdb_id, 'tmdb_id': tmdb_id},
                    media_type=media_type or 'movie',
                )
            except Exception:
                self._poster = poster or ''
        else:
            self._poster = ''
        # v3.9.52: clearlogo is the transparent-background brand mark of
        # the show. When present, the dialog displays it in place of the
        # serif text title for a magazine/cinema feel. When absent the
        # text title shows instead, controlled by visibility conditions
        # in the skin XML.
        self._clearlogo = clearlogo or ''
        self._provider_count = max(0, int(provider_count or 0))
        self._results_so_far = 0
        self._started_at = time.monotonic()
        self._provider_results = {}
        self._provider_order = []
        # v4.6.0: provider dashboard cards. The caller passes the full ordered
        # provider name list up-front so every provider is visible from the
        # first paint as "waiting", flips to "done" the moment it reports, and
        # shows its own result count — instead of the old anonymous numbered
        # dots plus a top-4 chip lottery. Names keep first-seen order; the XML
        # renders the first _CARD_SLOTS of them.
        self._card_order = []
        self._card_state = {}   # name -> 'wait' | 'done' | 'zero'
        for name in (provider_names or []):
            name = str(name or '').strip()
            if name and name not in self._card_state:
                self._card_order.append(name)
                self._card_state[name] = 'wait'
        if not self._provider_count and self._card_order:
            self._provider_count = len(self._card_order)
        self._win = None
        self._opened = False
        self._cached_cancelled = False
        self._cached_sufficient = False

    def __enter__(self):
        _TRACE_T0[0] = 0.0
        _trace('opening window')
        try:
            self._win = _LoadingWindow(_SKIN_FILE, _addon_path(), _SKIN_RES, _SKIN_DIMS)
            # v3.9.52: feed the initial art values to the window so its
            # onInit() can clear and re-set the corresponding properties,
            # protecting against stale artwork from a previous instance.
            self._win._init_poster = self._poster or ''
            self._win._init_fanart = self._fanart or ''
            self._win._init_clearlogo = self._clearlogo or ''
        except Exception:
            self._win = None
            return self
        self._apply_initial_props()
        try:
            self._win.show()
            self._opened = True
            _trace('window shown')
        except Exception as exc:
            _trace('window show failed: %s' % exc)
            self._win = None
        # v4.6.4: the fill control exists only after show() — pushing the
        # width earlier made Kodi log "Non-Existent Control 5110".
        self._set_progress_width(0)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        _trace('closing window (results=%d, exc=%s)' % (int(self._results_so_far or 0), exc_type.__name__ if exc_type else 'none'))
        try:
            if self._win is not None:
                self._win.close()
                _trace('window closed')
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
        self._win = None
        self._opened = False
        return False  # don't suppress exceptions

    def _apply_initial_props(self):
        if self._win is None:
            return
        self._set('title', self._title)
        self._set('subtitle', self._subtitle)
        # v3.9.55: namespaced art property names to prevent collision
        # with the global Window(10000) properties set elsewhere in
        # plugin.py. The XML reads from these unique names.
        self._set('dexhub.loading.fanart', self._fanart)
        self._set('dexhub.loading.poster', self._poster)
        self._set('dexhub.loading.clearlogo', self._clearlogo)
        # v3.9.52: editorial properties.
        self._set('eyebrow', tr('بحث المصادر — Dex Hub'))
        self._set('caption_line', tr('A DEX HUB SOURCE SEARCH'))
        self._set('counter_number', '0')
        self._set('counter_label', tr('نتيجة'))
        self._set('providers_short_label', tr('مصادر'))
        self._set('results_line', tr('نتيجة صالحة بعد حذف المكرر'))
        self._set('status', tr('جار البحث عن المصادر...'))
        self._set('sub_status', '')
        self._set('progress_pct', '0')
        self._set('current_provider', '')
        self._set('hint_line', tr('اضغط رجوع لإلغاء البحث'))
        self._set('cancel_label', tr('إلغاء البحث') or 'Cancel search')
        self._set('sufficient_label', tr('عرض النتائج الآن') or 'Show results now')
        self._set('continue_label', tr('متابعة البحث') or 'Keep searching')
        self._set('enough_state', '')
        self._set('enough_label', '')
        self._set('elapsed_label', '0.0 %s' % tr('ث'))
        self._set('provider_count', str(self._provider_count))
        self._set('completed_count', '0')
        self._set('progress_label', '0%')
        self._set('has_results', '')
        for idx in range(1, 13):
            self._set('step%d_visible' % idx, '1' if idx <= self._provider_count else '')
            self._set('step%d_color' % idx, 'FF606673')
        for idx in range(1, 5):
            self._set('provider_%d_label' % idx, '')
            self._set('provider_%d_color' % idx, 'FF38BDF8')
        # v4.6.0: state labels the XML card chips bind to (translated once).
        self._set('pv_state_wait_label', tr('بالانتظار'))
        self._set('pv_state_done_label', tr('اكتمل'))
        self._set('pv_state_zero_label', tr('بدون نتائج'))
        self._refresh_provider_cards()
        if self._provider_count:
            self._set('providers_line',
                      '%s 0 / %d' % (tr('المصادر'), self._provider_count))
        else:
            self._set('providers_line', '')

    def set_art(self, poster='', fanart='', clearlogo=''):
        # v3.9.88: don't poison the poster slot with fanart. Keep the
        # existing poster (or the default poster placeholder set in
        # __init__) when no new poster is supplied.
        if poster:
            self._poster = poster
        self._fanart = fanart or self._fanart
        self._clearlogo = clearlogo or self._clearlogo
        if self._win is None:
            return
        self._set('dexhub.loading.poster', self._poster)
        self._set('dexhub.loading.fanart', self._fanart)
        self._set('dexhub.loading.clearlogo', self._clearlogo)
        try:
            self._win._push_art_controls(self._poster, self._fanart, self._clearlogo)
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
    # v4.6.2: the XML no longer uses Kodi's <progress> control at all — its
    # left/right cap textures render at the RAW FILE SIZE and ignore the
    # transparent colordiffuse, painting a giant white block past the bar
    # (both 4.6.0's full-width bar and 4.6.1's white remainder). The bar is
    # now a plain rail image plus a fill image (control 5110) whose width
    # this helper drives directly.
    _BAR_WIDTH = 1000

    def _set_progress_width(self, pct):
        try:
            pct = max(0, min(100, int(pct)))
        except Exception:
            pct = 0
        try:
            self._win.getControl(5110).setWidth(
                max(10, int(self._BAR_WIDTH * pct / 100.0)))
        except Exception:
            pass

    def _set(self, key, value):
        if self._win is None:
            return
        try:
            self._win.setProperty(key, str(value or ''))
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
    def is_cancelled(self):
        # v3.9.33: sticky — once True, stay True forever (defends against
        # reading from a window object that's already been closed and
        # would silently return False on getter calls).
        if self._cached_cancelled:
            return True
        if self._win is None:
            return False
        try:
            if bool(self._win.is_cancelled()):
                self._cached_cancelled = True
                return True
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
        return False

    def is_sufficient(self):
        if self._cached_sufficient:
            return True
        if self._win is None:
            return False
        try:
            if bool(self._win.is_sufficient()):
                self._cached_sufficient = True
                return True
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
        return False

    def is_stopped(self):
        """Either button was pressed — caller should break out of the
        scan loop. Distinguish abort vs use-partial via the two methods
        above when post-processing."""
        # v3.9.33: short-circuit via cache for speed and stickiness.
        if self._cached_cancelled or self._cached_sufficient:
            return True
        if self._win is None:
            return False
        try:
            if bool(self._win.is_stopped()):
                # Refresh cached state on the way out so subsequent
                # is_cancelled() / is_sufficient() calls don't miss it.
                try:
                    self._cached_cancelled = bool(self._win.is_cancelled())
                    self._cached_sufficient = bool(self._win.is_sufficient())
                except Exception as _silent_exc:
                    log.silent('LOADING_DLG', _silent_exc)
                return True
        except Exception as _silent_exc:
            log.silent('LOADING_DLG', _silent_exc)
        return False

    def update(self, provider_name='', index=0, status=None, sub_status=None):
        """Update the window while the caller makes progress."""
        if self._win is None:
            return
        if status is not None:
            self._set('status', status)
        # v3.9.52: current_provider styled for the editorial layout.
        # "Now checking: <name>" — caption + name composed inline so it
        # matches the magazine subhead pattern.
        if provider_name:
            _trace('provider done: %s (#%s, results so far %d)' % (provider_name, index, int(self._results_so_far or 0)))
            self._set('current_provider',
                      '%s  %s' % (tr('يفحص الآن:'), provider_name))
            # v4.6.0: iter_parallel yields a provider when it has finished
            # answering, so an update() naming a provider means that provider
            # is DONE — flip its dashboard card (count may still arrive via
            # add_results() immediately after; _touch_card re-evaluates).
            self._touch_card(provider_name, done=True)
            self._refresh_provider_cards()
        if sub_status is not None:
            self._set('sub_status', sub_status)
        if index and self._provider_count:
            pct = max(0, min(100, int(round(100.0 * index / self._provider_count))))
            self._set('progress_pct', str(pct))
            self._set_progress_width(pct)
            self._set('progress_label', '%d%%' % pct)
            self._set('completed_count', str(index))
            line = '%s %d / %d' % (tr('المصادر'), index, self._provider_count)
            self._set('providers_line', line)
            for step in range(1, min(self._provider_count, 12) + 1):
                if step < index:
                    color = 'FF20C7C9'
                elif step == index:
                    color = 'FFFFA500'
                else:
                    color = 'FF606673'
                self._set('step%d_color' % step, color)
            # sub_status in editorial layout shows "X of Y providers checked"
            if sub_status is None:
                self._set('sub_status',
                          tr('%d من %d مزوّد تم فحصها') % (index, self._provider_count))

        self._set('elapsed_label', '%.1f %s' % (max(0.0, time.monotonic() - self._started_at), tr('ث')))

    # v4.6.0 ------------------------------------------------------------- #
    #  Provider dashboard cards (up to 8 visible slots).                    #
    #  Window properties per slot i in 1..8:                                #
    #    pv{i}_name       provider display name ('' hides the card)         #
    #    pv{i}_count      result count text ('' until first result)         #
    #    pv{i}_state      'wait' | 'done' | 'zero'                          #
    #    pv{i}_coloridx   '1'..'8' — stable identity colour slot            #
    #  plus pv_more: '+N' overflow note when more than 8 providers ran.     #
    # ------------------------------------------------------------------- #
    # v5.4.6: six cards, not eight. Each card costs ~15 skin expressions
    # that Kodi re-evaluates while providers are still answering, and a
    # real setup runs four to six providers — the last two slots were
    # paying render cost to stay empty. pv_more still reports any
    # provider beyond the sixth.
    _CARD_SLOTS = 6

    def _touch_card(self, provider_name, done=False):
        """Register/refresh one provider in the card model."""
        name = str(provider_name or '').strip()
        if not name:
            return
        if name not in self._card_state:
            self._card_order.append(name)
            self._card_state[name] = 'wait'
        if done:
            count = int(self._provider_results.get(name) or 0)
            self._card_state[name] = 'done' if count > 0 else 'zero'

    def _refresh_provider_cards(self):
        try:
            from . import skin_theme
            _color_idx = skin_theme.provider_color_index
        except Exception:  # pragma: no cover - stub environments
            _color_idx = lambda _n: 1  # noqa: E731
        shown = self._card_order[:self._CARD_SLOTS]
        for slot in range(1, self._CARD_SLOTS + 1):
            if slot <= len(shown):
                name = shown[slot - 1]
                count = int(self._provider_results.get(name) or 0)
                state = self._card_state.get(name) or 'wait'
                self._set('pv%d_name' % slot, name)
                self._set('pv%d_count' % slot, str(count) if (count or state != 'wait') else '')
                self._set('pv%d_state' % slot, state)
                self._set('pv%d_coloridx' % slot, str(_color_idx(name)))
            else:
                self._set('pv%d_name' % slot, '')
                self._set('pv%d_count' % slot, '')
                self._set('pv%d_state' % slot, '')
                self._set('pv%d_coloridx' % slot, '')
        extra = len(self._card_order) - len(shown)
        self._set('pv_more', ('+%d' % extra) if extra > 0 else '')

    def _refresh_provider_chips(self):
        palette = ('FF22D3EE', 'FFFFC928', 'FFC084FC', 'FF4ADE80')
        ranked = sorted(
            ((name, int(self._provider_results.get(name) or 0)) for name in self._provider_order),
            key=lambda pair: (-pair[1], self._provider_order.index(pair[0])),
        )
        for idx in range(1, 5):
            if idx <= len(ranked):
                name, count = ranked[idx - 1]
                self._set('provider_%d_label' % idx, '%s  %d' % (name, count))
                self._set('provider_%d_color' % idx, palette[idx - 1])
            else:
                self._set('provider_%d_label' % idx, '')

    def add_results(self, n=0, provider_name='', unique_total=None):
        was_empty = self._results_so_far <= 0
        added = max(0, int(n or 0))
        self._results_so_far += added
        provider_name = str(provider_name or '').strip()
        if provider_name:
            if provider_name not in self._provider_results:
                self._provider_order.append(provider_name)
                self._provider_results[provider_name] = 0
            self._provider_results[provider_name] += added
            self._refresh_provider_chips()
            # v4.6.0: a provider that delivered results is by definition done.
            self._touch_card(provider_name, done=True)
            self._refresh_provider_cards()
        if unique_total is not None:
            try:
                self._results_so_far = max(0, int(unique_total))
            except Exception:
                pass
        if self._results_so_far:
            # v3.9.59/v3.9.60: bottom-sheet layout — only update the
            # big number, leave counter_label/results_line static so
            # they don't reflow on every result update.
            self._set('counter_number', str(self._results_so_far))
            self._set('has_results', '1')
            if was_empty and self._win is not None:
                try:
                    self._win.setFocusId(9001)
                except Exception:
                    pass
        self._set('elapsed_label', '%.1f %s' % (max(0.0, time.monotonic() - self._started_at), tr('ث')))

    def mark_sufficient(self, auto_seconds=2):
        self._set('enough_state', '1')
        self._set('enough_label', tr('تم الوصول إلى نتائج كافية'))
        self._set('status', tr('تم الوصول إلى نتائج كافية'))
        self._set('hint_line', tr('عرض النتائج تلقائياً خلال %d ثانية') % int(auto_seconds or 0))

    def clear_sufficient(self):
        self._set('enough_state', '')
        self._set('enough_label', '')
        self._set('status', tr('جار البحث عن المصادر...'))

    def consume_continue_search(self):
        if self._win is None:
            return False
        try:
            return bool(self._win.consume_continue_search())
        except Exception:
            return False

    def set_finalizing(self, msg=None):
        """Switch to a final "organizing results..." state."""
        _trace('finalizing (results=%d)' % int(self._results_so_far or 0))
        self._set('status', msg or (tr('جار جلب نتائج إضافية...')))
        self._set('progress_pct', '100')
        self._set_progress_width(100)
        self._set('progress_label', '100%')
