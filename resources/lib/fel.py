# -*- coding: utf-8 -*-
"""Dolby Vision Profile 7 FEL (v5.10.612).

resources/data/fel7.json: the titles confirmed to carry a Full Enhancement
Layer on UHD Blu-ray (627, from the FEL Cinema add-on's catalog), by IMDb id.
A title on it gets the "Dolby Vision P7 FEL" badge on its page, the FEL 7
Collection row there and on the Home, and its copies are told apart: FEL when
the release says so, or when the title is on the list and the copy is a 4K
Dolby Vision remux or whole disc that does not say MEL; MEL when it says MEL
(or Profile 8 / hybrid) with Dolby Vision.
"""
import json
import os
import re

_DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'fel7.json')
_CACHE = {}
ART = 'https://images.metahub.space/%s/%s/%s/img'      # kind, size, imdb id

_FEL = re.compile(r'(?<![A-Z0-9])(FEL|P7|PROFILE[ ._-]?7|DUAL[ ._-]?LAYER)(?![A-Z0-9])')
_MEL = re.compile(r'(?<![A-Z0-9])(MEL|P8|P8\.1|PROFILE[ ._-]?8|HYBRID)(?![A-Z0-9])')
_DV = re.compile(r'(?<![A-Z0-9])(DV|DOVI|DOLBY[ ._-]?VISION)(?![A-Z0-9])')
_DISC = re.compile(r'(?<![A-Z0-9])(REMUX|BDMV|ISO|COMPLETE[ ._-]?UHD|BR[ ._-]?DISK|UHD[ ._-]?BLU[ ._-]?RAY[ ._-]?COMPLETE)(?![A-Z0-9])')
_4K = re.compile(r'(?<![0-9])(2160P|4K|UHD)(?![0-9A-Z])')


def titles():
    if 'titles' not in _CACHE:
        try:
            with open(_DATA, encoding='utf-8') as fh:
                _CACHE['titles'] = list((json.load(fh) or {}).get('titles') or [])
        except Exception:
            _CACHE['titles'] = []
        _CACHE['ids'] = set(str(t.get('imdb') or '') for t in _CACHE['titles'])
    return _CACHE['titles']


def is_fel(imdb_id):
    titles()
    return str(imdb_id or '').strip() in _CACHE.get('ids', ())


def art(imdb_id, kind='poster'):
    size = {'poster': 'small', 'background': 'medium', 'logo': 'medium'}.get(kind, 'medium')
    return ART % (kind, size, imdb_id) if imdb_id else ''


def layer(text, title_is_fel=False):
    """'FEL', 'MEL' or '' for a copy, from its release name and lines."""
    blob = str(text or '').upper()
    if not _DV.search(blob) and not _FEL.search(blob):
        return ''
    if _MEL.search(blob):
        return 'MEL' if _DV.search(blob) else ''
    if _FEL.search(blob):
        return 'FEL'
    if title_is_fel and _DV.search(blob) and _DISC.search(blob) and _4K.search(blob):
        return 'FEL'
    return ''
