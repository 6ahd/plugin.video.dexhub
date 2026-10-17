# -*- coding: utf-8 -*-
"""What each subtitle file Dex Hub hands the player was called by its add-on
(v5.10.319).

Kodi names an external subtitle after its file: it splits the file name on
spaces, dots and dashes, takes one word it reads as a language (from the
end) and joins the rest with spaces, plus "(External)". The add-on's own
name loses its punctuation on the way and Kodi's language guess can be a
word of the name. Dex Hub notes, for every file it writes, the add-on's
name, the add-on (source) and the language, in a Home window property
(shared by Dex Hub's processes), and the player's lists read them back by
matching Kodi's name to the file's words.
"""
import json
import os
import re
import time

PROP = 'dexhub.subnames.v1'
_CAP = 240
_WORD = re.compile(r'[^\W_]+', re.UNICODE)
_EXTERNAL = ('external', 'خارجي', 'خارجية')


def _window():
    import xbmcgui
    return xbmcgui.Window(10000)


def _load():
    try:
        data = json.loads(_window().getProperty(PROP) or '{}')
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def remember(path, name='', source='', lang=''):
    """path: the file given to the player; name: the add-on's name of it."""
    base = os.path.basename(str(path or '').split('|', 1)[0])
    if not base or not (name or source):
        return
    data = _load()
    data[base] = {'n': str(name or '')[:160], 's': str(source or '')[:60], 'l': str(lang or ''), 't': int(time.time())}
    if len(data) > _CAP:
        for key in sorted(data, key=lambda k: data[k].get('t', 0))[:len(data) - _CAP]:
            data.pop(key, None)
    try:
        _window().setProperty(PROP, json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    except Exception:
        pass


def _words(text):
    return [w.lower() for w in _WORD.findall(str(text or ''))]


def lookup(stream_name):
    """{'n': name, 's': source, 'l': lang} for a track Kodi calls
    stream_name, or None."""
    words = [w for w in _words(stream_name) if w not in _EXTERNAL]
    if not words:
        return None
    data = _load()
    if not data:
        return None
    want = set(words)
    best, best_score = None, 0.0
    for base, info in data.items():
        stem = set(_words(os.path.splitext(base)[0]))
        if not stem:
            continue
        hit = len(want & stem)
        score = hit / float(len(want))
        # Kodi keeps all but one word (the language) and the flags
        if score >= 0.9 and len(stem) - hit <= 3:
            score += min(info.get('t', 0), 4e9) / 1e12      # the newest when equal
            if score > best_score:
                best, best_score = info, score
    return best
