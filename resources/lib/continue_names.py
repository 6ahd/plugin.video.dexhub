# -*- coding: utf-8 -*-
"""Names for Continue Watching and favourite rows that have none (v5.10.103).

Nuvio's watch progress and saved library carry ids only, so rows pulled from
the account had no title and the listings showed "Untitled". The name comes
from TMDb Helper's local database when it knows the title, otherwise from
TMDb once (one short request, in the language of the Nuvio profile or of Dex
Hub), and the caller saves it with the row, so the next listing has it with
no lookup at all. A failed lookup is not repeated for a few hours.
"""
import re
import threading
import time

_LOCK = threading.Lock()
_MEMO = {}
_MISS = {}
_MISS_TTL = 6 * 3600.0
_TIMEOUT = 4.0
# ids that ended up as titles (same test as the Continue Watching listing:
# JSGB4701, 28739), plus prefixed ids and the placeholder names
_CODE_RE = re.compile(r'^(?:(?=[A-Z0-9]*[A-Z])[A-Z0-9]{4,}|\d{5,})$')
_ID_RE = re.compile(r'^(?:tt\d{5,}|(?:tmdb|tvdb|imdb|kitsu|mal|anilist):\S+)$', re.I)
_PLACEHOLDERS = ('untitled', 'بدون عنوان')


def looks_missing(text):
    """No usable name: empty, 'Untitled' or an id shown as a title."""
    value = str(text or '').strip()
    if not value or value.lower() in _PLACEHOLDERS:
        return True
    return bool(_CODE_RE.match(value) or _ID_RE.match(value))


def _digits(value):
    value = str(value or '').strip()
    if value.lower().startswith('tmdb:'):
        value = value.split(':', 1)[1]
    return value if value.isdigit() else ''


def _imdb(value):
    m = re.search(r'(?i)\btt\d{5,12}\b', str(value or ''))
    return m.group(0).lower() if m else ''


def ids_of(row, is_episode=False):
    """(tmdb_id, imdb_id) of the title a row belongs to (the show for episodes)."""
    row = row or {}
    cid = str(row.get('canonical_id') or '').strip()
    tmdb = ''
    if is_episode:
        tmdb = _digits(row.get('show_tmdb_id'))
    tmdb = tmdb or _digits(row.get('tmdb_id')) or _digits(cid)
    imdb = _imdb(row.get('imdb_id')) or _imdb(cid)
    return tmdb, imdb


def _language():
    try:
        from .dexhub import nuvio_profile_prefs
        prefs = nuvio_profile_prefs.normalized_tmdb()
        if prefs.get('synced') and prefs.get('language'):
            return str(prefs['language'])
    except Exception:
        pass
    try:
        from .i18n import current_language
        return 'ar-SA' if current_language() == 'ar' else 'en-US'
    except Exception:
        return 'en-US'


def _from_db(tmdb, imdb, kind):
    try:
        from . import tmdbhelper
        bundle = tmdbhelper.get_meta_bundle_from_db(tmdb_id=tmdb, imdb_id=imdb, media_type=kind) or {}
    except Exception:
        return ''
    for key in ('title', 'name', 'tvshowtitle'):
        value = str(bundle.get(key) or '').strip()
        if value and not looks_missing(value):
            return value
    return ''


def _from_tmdb(tmdb, imdb, kind):
    from . import tmdb_direct
    language = _language()
    key = 'name' if kind == 'tv' else 'title'
    if not tmdb and imdb:
        data = tmdb_direct._request('/find/%s' % imdb, {'external_source': 'imdb_id', 'language': language},
                                    timeout=_TIMEOUT, max_attempts=1, rate_wait=1.0) or {}
        rows = data.get('tv_results' if kind == 'tv' else 'movie_results') or []
        if not rows:
            # an episode row can carry the show's IMDb id under the other type
            rows = data.get('movie_results' if kind == 'tv' else 'tv_results') or []
        if rows:
            return str(rows[0].get('name') or rows[0].get('title') or '').strip()
        return ''
    data = tmdb_direct._request('/%s/%s' % (kind, tmdb), {'language': language},
                                timeout=_TIMEOUT, max_attempts=1, rate_wait=1.0) or {}
    return str(data.get(key) or data.get('original_%s' % key) or '').strip()


def name_for(row, is_episode=False, network=True):
    """The title's name (the show's for an episode), or ''."""
    tmdb, imdb = ids_of(row, is_episode)
    if not (tmdb or imdb):
        return ''
    kind = 'tv' if is_episode else 'movie'
    memo_key = '%s:%s:%s' % (kind, tmdb, imdb)
    with _LOCK:
        if memo_key in _MEMO:
            return _MEMO[memo_key]
        failed_at = _MISS.get(memo_key)
    if failed_at and time.time() - failed_at < _MISS_TTL:
        return ''
    name = _from_db(tmdb, imdb, kind)
    if not name and network:
        try:
            name = _from_tmdb(tmdb, imdb, kind)
        except Exception:
            name = ''
    if name and looks_missing(name):
        name = ''
    with _LOCK:
        if name:
            _MEMO[memo_key] = name
        elif network:
            _MISS[memo_key] = time.time()
    return name
