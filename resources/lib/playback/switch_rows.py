# -*- coding: utf-8 -*-
"""v5.4.8: mark and lift the currently-playing source in the switch dialog.

Root cause of the "stuck red cursor" report: Dialog().select always opens
with focus on row 0 and the cached index preserves the original search
order, so the row under the cursor had no relation to what was actually
playing — after a switch, reopening the dialog looked identical and the
user could not tell which press maps to the live source.

Lives outside plugin.py to honour the line-count ratchet
(test_plugin_py_is_shrinking_not_growing): extract, do not append.
"""
from ..i18n import tr

_TAG = '[B][COLOR FFE53935]\u25b6 %s[/COLOR][/B]  %s'


def rows_with_current_first(rows, current_key):
    """Bubble the row whose stream_key matches *current_key* to index 0
    and tag it in red as the live source.

    Pure function: shallow-copies the tagged row so the cached index
    (Window property / disk) is never mutated. Returns (rows, has_current);
    when has_current is True the live source is row 0.
    """
    key = str(current_key or '')
    out = list(rows or [])
    if not key:
        return out, False
    for i, row in enumerate(out):
        if str((row or {}).get('k') or '') != key:
            continue
        tagged = dict(row)
        tagged['l'] = _TAG % (tr('يشتغل الآن'), row.get('l') or '?')
        return [tagged] + out[:i] + out[i + 1:], True
    return out, False
