# -*- coding: utf-8 -*-
"""Subtitle automation policy.

AI rows remain discoverable for manual selection, but are never downloaded,
prepared, attached or activated automatically. This prevents DexWorld AI token
usage unless the user explicitly chooses that row.
"""
from __future__ import absolute_import


import re

# Fields that describe the subtitle row itself. The provider/addon label is
# deliberately excluded: DexSubtitles is registered as "DexWorld AI Subtitles"
# and serves ordinary SubSource/SubDL/OpenSubtitles rows under that name.
_ROW_KEYS = ('title', 'displayName', 'name', 'url', 'key', 'id', 'type',
             'category', 'model', 'status', 'kind', 'origin')

_AI_WORD_RE = re.compile(
    r'(?<![a-z0-9])(ai|ai[-_ ]?generated|ai[-_ ]?public|ai[-_ ]?request|'
    r'machine[-_ ]?translat\w*|auto[-_ ]?translat\w*)(?![a-z0-9])', re.I)
_AI_MARKERS = ('/ai/', 'ai_request', 'ai-request', 'ai_public',
               'artificial intelligence', 'ترجمة ai', 'ذكاء اصطناعي',
               'ترجمة آلية', '🤖', '[ai]')


def _text(row):
    row = row or {}
    return ' '.join(str(row.get(k) or '') for k in _ROW_KEYS).lower()


def _flag(value):
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else '').strip().lower()
    if text in ('1', 'true', 'yes', 'on'):
        return True
    if text in ('0', 'false', 'no', 'off'):
        return False
    return None


def is_ai_subtitle(row):
    """True for rows that would cost an AI translation if attached.

    v5.10.94: the server flags (``is_ai`` / ``ai`` / ``generated``, sent by
    DexSubtitles since V166) are authoritative in both directions. Only rows
    with no flag at all fall back to the name/url heuristic, which now
    matches "ai" as a whole word (so "Main", "Rain" and "Spain" no longer
    count) and ignores the provider label.
    """
    row = row or {}
    for key in ('is_ai', 'ai', 'generated', 'ai_generated', 'isAi'):
        if key in row:
            flag = _flag(row.get(key))
            if flag is not None:
                return flag
    status = str(row.get('status') or '').strip().lower()
    if status in ('pending', 'queued', 'translating', 'processing', 'request'):
        return True
    text = _text(row)
    if any(x in text for x in _AI_MARKERS):
        return True
    return bool(_AI_WORD_RE.search(text))


def is_manual_choice(row):
    row = row or {}
    return bool(row.get('user_selected') or row.get('manual_selected') or
                str(row.get('sourceType') or '').lower() == 'manual')


def allow_automatic(row):
    return bool(row) and (not is_ai_subtitle(row) or is_manual_choice(row))


def automatic_rows(rows):
    return [r for r in (rows or []) if allow_automatic(r)]
