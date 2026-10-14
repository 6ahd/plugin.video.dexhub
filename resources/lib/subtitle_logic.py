# -*- coding: utf-8 -*-
"""Pure subtitle selection logic for plugin.video.dexhub.

Round 4a of the refactor map: the two decision blocks extracted verbatim
from ``_play_with_context`` (v3.9.148).  Both are pure — no Kodi UI calls,
no window properties, no threads — which is what makes them equivalence-
testable (verified via a 145-case before/after snapshot, zero mismatches).

The playback-timing pieces around them (``item.setSubtitles``, the deferred
background-thread attach, ``_post_start_chores``) intentionally stay in
``plugin.py``; see ROUND4_PLAN.md phase 4b.
"""

from .subtitle_policy import allow_automatic


def _partition_subtitle_rows(subtitle_rows, force_subtitles, selected_sub, ctx, pick_index_fn):
    """Split subtitle rows into (selected_first, other_subs).

    POV-style fast path: only the SELECTED/forced subtitle is prepared
    synchronously; the rest are downloaded in a background thread AFTER
    playback has already started, so they don't delay first-frame.

    ``pick_index_fn`` is injected (``_pick_default_subtitle_index`` from
    plugin.py) so this module never imports from plugin.  Failure and
    out-of-range behaviour of the picker is clamped to index 0, exactly as
    the original inline block did.
    """
    selected_first = []
    other_subs = []
    if subtitle_rows:
        if force_subtitles or selected_sub:
            # Pre-pick the index that will be auto-applied. Promote that one
            # subtitle to "prepare now"; defer the rest.
            try:
                pick_idx = pick_index_fn(subtitle_rows, ctx)
            except Exception:
                pick_idx = 0
            if pick_idx < 0 or pick_idx >= len(subtitle_rows):
                selected_first = []
                other_subs = list(subtitle_rows)
            else:
                selected_first = [subtitle_rows[pick_idx]]
                other_subs = [s for i, s in enumerate(subtitle_rows) if i != pick_idx]
        else:
            # Not playing-with-subs and nothing pre-selected. We can defer
            # ALL of them — Kodi's subtitle menu will populate as they arrive.
            selected_first = []
            other_subs = list(subtitle_rows)
    return selected_first, other_subs


def _elect_default_subtitle(prepared_first, subs):
    """Pick the auto-selected subtitle → (path, index into ``subs``).

    v3.9.105 behaviour, verbatim: pick the BEST Arabic subtitle as the
    auto-selected one, not just whatever sits at index 0.  Walks the user's
    preferred languages (ar first, then en, then any fallback) over
    ``prepared_first`` and elects the first match; last resort is the first
    row that has a path.  Returns ('', -1) when ``subs`` is empty.
    """
    selected_subtitle_path = ''
    selected_subtitle_index = -1
    if subs:
        try:
            from .subtitle_broker import _preferred_languages as _prefs_fn
            preferred = [str(p).lower() for p in (_prefs_fn() or [])] or ['ar', 'en']
        except Exception:
            preferred = ['ar', 'en']

        def _lang_short(row):
            lk = str((row or {}).get('lang_key') or '').lower()
            # lang_key shapes seen in DexHub: 'ar', 'ar-sa', 'en-us', etc.
            return lk.split('-')[0] if lk else ''

        chosen_idx = -1
        # Walk preferred languages in priority order; first match wins.
        for want in preferred:
            for idx, row in enumerate(prepared_first):
                if not row.get('path') or not allow_automatic(row):
                    continue
                if _lang_short(row) == want:
                    chosen_idx = idx
                    break
            if chosen_idx >= 0:
                break
        # Last-resort: first available — better than nothing if no preferred
        # language is present in the bundle.
        if chosen_idx < 0:
            for idx, row in enumerate(prepared_first):
                if row.get('path') and allow_automatic(row):
                    chosen_idx = idx
                    break
        if chosen_idx >= 0:
            selected_subtitle_path = prepared_first[chosen_idx].get('path') or ''
            # subs[] indexing matches prepared_first[] indexing only for rows
            # that had a path — recompute the index inside subs[] to be safe.
            try:
                selected_subtitle_index = subs.index(selected_subtitle_path)
            except ValueError:
                selected_subtitle_index = 0
    return selected_subtitle_path, selected_subtitle_index
