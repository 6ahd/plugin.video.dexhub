# -*- coding: utf-8 -*-
"""TMDb person search and filmography.

Lets the user search for "Tom Hardy" and get back his entire filmography
with posters, just like POV's people menu. Uses the TMDb v3 API directly
so the user only needs a TMDb API key (not a separate Stremio addon).
"""
import json
import urllib.error
import urllib.parse
import urllib.request

import xbmcaddon

from .log import log
from .ratelimit import limiter, host_of

# --- dexhub-401-patch ---
try:
    from .settings_cache import cached_addon as _dh_cached_addon
except Exception:
    try:
        from settings_cache import cached_addon as _dh_cached_addon
    except Exception:
        _dh_cached_addon = None
ADDON = _dh_cached_addon() if _dh_cached_addon else xbmcaddon.Addon()

_BASE = 'https://api.themoviedb.org/3'
_IMG_BASE = 'https://image.tmdb.org/t/p'


def _api_key():
    # Use the same editable profile/default key as the rest of TMDb.
    from .tmdb_direct import _api_key as current_key
    return current_key()


def _get(url, timeout=8):
    host = host_of(url)
    limiter.acquire(host, max_wait=2.0)
    req = urllib.request.Request(url, headers={'User-Agent': 'DexHub/3.9'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return None
            return json.loads(resp.read().decode('utf-8', errors='replace'))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # TMDb told us to slow down — tighten the bucket for next time.
            try:
                limiter.set_policy(host, 8, 2.0)
            except Exception:
                pass
        log.warn('PEOPLE', 'http %s for %s', e.code, url)
        return None
    except Exception as exc:
        log.warn('PEOPLE', 'fetch failed %s: %s', url, exc)
        return None


def search_people(query, language='en-US'):
    """Search for actors/directors by name. Returns list of {id, name,
    profile_path, known_for_dept, popularity}."""
    api_key = _api_key()
    query = (query or '').strip()
    if not (api_key and query):
        return []
    url = '%s/search/person?api_key=%s&query=%s&language=%s' % (
        _BASE, urllib.parse.quote(api_key),
        urllib.parse.quote(query), urllib.parse.quote(language),
    )
    data = _get(url)
    if not isinstance(data, dict):
        return []
    out = []
    for r in (data.get('results') or [])[:30]:
        if not isinstance(r, dict):
            continue
        out.append({
            'id': r.get('id'),
            'name': r.get('name') or '',
            'profile_path': r.get('profile_path') or '',
            'known_for_department': r.get('known_for_department') or '',
            'popularity': float(r.get('popularity') or 0),
            'known_for': [
                {
                    'id': k.get('id'),
                    'media_type': k.get('media_type') or 'movie',
                    'title': k.get('title') or k.get('name') or '',
                }
                for k in (r.get('known_for') or [])[:5]
                if isinstance(k, dict)
            ],
        })
    return out


def person_filmography(person_id, language='en-US'):
    """Return {as_actor: [items], as_director: [items], as_writer: [items]}.

    Each item is {id, media_type, title, year, poster_path, vote_average,
    character_or_job}.
    """
    api_key = _api_key()
    if not (api_key and person_id):
        return {'as_actor': [], 'as_director': [], 'as_writer': []}
    url = '%s/person/%s/combined_credits?api_key=%s&language=%s' % (
        _BASE, int(person_id),
        urllib.parse.quote(api_key), urllib.parse.quote(language),
    )
    data = _get(url)
    if not isinstance(data, dict):
        return {'as_actor': [], 'as_director': [], 'as_writer': []}

    def _norm(entry, role_key):
        if not isinstance(entry, dict):
            return None
        title = entry.get('title') or entry.get('name') or ''
        if not title:
            return None
        date = entry.get('release_date') or entry.get('first_air_date') or ''
        try:
            year = int(str(date)[:4]) if date else 0
        except Exception:
            year = 0
        return {
            'id': entry.get('id'),
            'media_type': entry.get('media_type') or 'movie',
            'title': title,
            'year': year,
            'poster_path': entry.get('poster_path') or '',
            'vote_average': float(entry.get('vote_average') or 0),
            'role': str(entry.get(role_key) or '').strip(),
        }

    cast_raw = data.get('cast') or []
    crew_raw = data.get('crew') or []
    as_actor = [x for x in (_norm(e, 'character') for e in cast_raw) if x]
    as_director = [x for x in (_norm(e, 'job') for e in crew_raw) if x and x['role'] == 'Director']
    as_writer = [x for x in (_norm(e, 'job') for e in crew_raw) if x and x['role'] in ('Writer', 'Screenplay', 'Story')]

    # Sort each by year desc — most recent first
    for lst in (as_actor, as_director, as_writer):
        lst.sort(key=lambda x: (x['year'] or 0, x['vote_average']), reverse=True)
    return {'as_actor': as_actor, 'as_director': as_director, 'as_writer': as_writer}


def poster_url(path, size='w342'):
    """Build a full poster URL from a TMDb relative path."""
    if not path:
        return ''
    if str(path).startswith('http'):
        return path
    return '%s/%s%s' % (_IMG_BASE, size, path)


def profile_url(path, size='w185'):
    return poster_url(path, size)
