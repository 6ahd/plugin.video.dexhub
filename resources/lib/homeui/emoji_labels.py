# -*- coding: utf-8 -*-
"""Small offline emoji textures for IPTV labels; source IDs stay untouched.

At most two textures are shown per category, only in visible list rows.
This is a bounded image pack, not a colour-font renderer for all of Kodi.
"""
import json
import os
import re
from functools import lru_cache

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..',
                                     'media', 'homeui', 'emoji'))
try:
    with open(os.path.join(_ROOT, 'index.json'), encoding='utf-8') as handle:
        _ICONS = json.load(handle)
except (OSError, ValueError):
    _ICONS = {}

# Longest first, and keep unrecognised joined sequences intact.
_KEYS = '|'.join(re.escape(key) for key in sorted(_ICONS, key=len, reverse=True))
_MATCH = re.compile(r'(?:[\U0001f1e6-\U0001f1ff]{2}' +
                    ('|' + _KEYS if _KEYS else r'|(?!x)x') + r')\ufe0f?')


_EMO = '[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff]'
_ASTRAL = re.compile(u'[\U00010000-\U0010ffff][\ufe0f\u200d]*|\ufe0f')
_JOINED = re.compile('(' + _EMO + '\ufe0f?)(?:[\U0001f3fb-\U0001f3ff]?\u200d' + _EMO + '\ufe0f?)+')


@lru_cache(maxsize=512)
def presentation(text, fallback):
    """Return a readable category title and up to two local icon paths."""
    # v5.10.202: Kodi draws a joined emoji (👨‍👩‍👧, 🏳️‍🌈) as its separate parts;
    # its first part alone stands for it.
    text = _JOINED.sub(lambda m: m.group(1), str(text or ''))
    icons = []

    def replace(match):
        token = match.group(0).rstrip('\ufe0f')
        # A recognised base inside a compound emoji must not be split.
        if text[match.end():match.end() + 2].lstrip('\ufe0f').startswith('\u200d'):
            return match.group(0)
        if match.start() and text[match.start() - 1] == '\u200d':
            return match.group(0)
        name = _ICONS.get(token)
        if name:
            path = os.path.join(_ROOT, name)
            if path not in icons and len(icons) < 2:
                icons.append(path)
            return ' '
        if len(token) == 2 and all(0x1f1e6 <= ord(ch) <= 0x1f1ff for ch in token):
            # An unpackaged country flag stays readable on skins whose
            # fonts do not shape regional-indicator pairs.
            return ' ' + ''.join(chr(ord('A') + ord(ch) - 0x1f1e6) for ch in token) + ' '
        return token

    label = _MATCH.sub(replace, text)
    # v5.10.205: an emoji with no texture here (🍿, 🕓, 🔍 of DexWorld's
    # series and search catalogs) is left out of the text: on Kodi for macOS
    # a label holding one was not drawn at all, the whole name blank
    label = _ASTRAL.sub(' ', label)
    label = ' '.join(label.split()) or fallback
    return label, tuple(icons)
