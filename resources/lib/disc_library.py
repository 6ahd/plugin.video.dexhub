# -*- coding: utf-8 -*-
"""The Full Disc collection (v5.10.612): films a search found a whole UHD/Blu-ray
disc of (an ISO or a BDMV folder), learned as you browse.

Kept in the add-on's data folder (disc_titles.json, {imdb: {title, year, seen}});
title_sources (the title page's search) and source_browser (the sources page)
add to it; fel.py's collections list it, the newest first.
"""
import json
import os
import re
import threading
import time

import xbmcvfs

_PATH = 'special://profile/addon_data/plugin.video.dexhub/disc_titles.json'
_LOCK = threading.Lock()
DISC_RE = re.compile(r'(?<![a-z0-9])(?:iso|bdmv|br[ ._-]?disk|full[ ._-]?disc|bd(?:25|50|66|100)|'
                     r'complete[ ._-]?(?:uhd[ ._-]?)?blu[ ._-]?ray)(?![a-z0-9])|\.iso(?:$|[?|])|/index\.bdmv', re.I)


def _file():
    return xbmcvfs.translatePath(_PATH)


def load():
    try:
        with open(_file(), encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def add(imdb, title='', year=''):
    imdb = str(imdb or '').strip()
    if not imdb.startswith('tt'):
        return
    with _LOCK:
        data = load()
        known = data.get(imdb) or {}
        if known and time.time() - float(known.get('seen') or 0) < 86400:
            return
        data[imdb] = {'title': title or known.get('title') or '', 'year': str(year or known.get('year') or ''),
                      'seen': time.time()}
        try:
            os.makedirs(os.path.dirname(_file()), exist_ok=True)
            tmp = _file() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, _file())
        except Exception:
            pass


def titles():
    """[{imdb, title, year}], the newest first."""
    data = load()
    rows = [dict(v, imdb=k) for k, v in data.items()]
    rows.sort(key=lambda r: -float(r.get('seen') or 0))
    return rows
