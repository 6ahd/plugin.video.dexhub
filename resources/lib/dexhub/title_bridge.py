# -*- coding: utf-8 -*-
"""Sources of an add-on with its own ids, for a title known by IMDb or TMDb
(v5.10.482).

An add-on that scrapes one site (Soof-55: "shoof_..." ids) answers nothing for
tt ids even when it has the film: a title opened from TMDb never showed its
sources. When such an add-on answers empty, its catalogs are searched by the
title's names, a catalog entry of the same name and year is taken, and its
streams are asked for by the add-on's own id (a series: the episode's id from
its meta). Only exact names count: a wrong film is worse than none.
"""
import re
import time
import unicodedata

STANDARD = ('tt', 'imdb', 'tmdb', 'tvdb', 'kitsu', 'anilist', 'anidb', 'mal', 'simkl', 'trakt')
BUDGET = 12.0
MAX_MATCHES = 2
MAX_SEARCHES = 3
SEARCH_CATALOGS = 2
_WORDS = ('فيلم', 'مسلسل', 'انمي', 'أنمي', 'برنامج')
_TAGS = r'(?:مترجم(?:ة|ه)?|مدبلج(?:ة|ه)?|كامل(?:ة|ه)?|[اأ]ون ?لاين|hd)'
_SEASON = re.compile(r'(?:الموسم|season)\s*(\d{1,2})', re.I)
_EPISODE = re.compile(r'(?:الحلقة|الحلقه|episode)\s*\d', re.I)
_YEAR = re.compile(r'(?<!\d)((?:19|20)\d{2})(?!\d)')
_LATEST = re.compile(r'latest|recent|new|last', re.I)


def own_prefixes(provider):
    """The id prefixes of the add-on's own library (not IMDb, TMDb and the like)."""
    manifest = (provider or {}).get('manifest') or {}
    found = list(manifest.get('idPrefixes') or [])
    for resource in manifest.get('resources') or []:
        if isinstance(resource, dict):
            found += list(resource.get('idPrefixes') or [])
    return [str(p).lower() for p in found
            if str(p).strip() and not str(p).lower().startswith(STANDARD)]


def _searchable(catalog):
    for extra in (catalog or {}).get('extra') or []:
        if (isinstance(extra, dict) and extra.get('name') == 'search') or extra == 'search':
            return True
    return False


def search_catalogs(provider, media_type):
    """The catalogs worth a search: its site's search is one, each catalog
    only filters it (the general catalogs first, "latest" last)."""
    catalogs = [c for c in ((provider or {}).get('manifest') or {}).get('catalogs') or []
                if isinstance(c, dict) and c.get('id') and c.get('type') == media_type and _searchable(c)]
    return sorted(catalogs, key=lambda c: 1 if _LATEST.search(str(c.get('id'))) else 0)[:SEARCH_CATALOGS]


def eligible(provider, media_type):
    return bool(own_prefixes(provider) and search_catalogs(provider, media_type))


def fold(text):
    """A name to compare: no marks, one spelling of alef and teh marbuta, letters and digits only."""
    text = unicodedata.normalize('NFKC', str(text or '')).lower()
    text = ''.join(c for c in text if not unicodedata.combining(c))
    for old, new in (('أ', 'ا'), ('إ', 'ا'), ('آ', 'ا'), ('ٱ', 'ا'), ('ة', 'ه'), ('ى', 'ي')):
        text = text.replace(old, new)
    return ''.join(c for c in text if c.isalnum())


def parse_name(name):
    """(folded name, year, season) of a catalog entry: "فيلم X 2026 مترجم" is X of 2026."""
    text = str(name or '').strip()
    if _EPISODE.search(text):
        return '', '', 0            # an episode's own entry, not the series
    for word in _WORDS:
        if text.startswith(word + ' '):
            text = text[len(word):].strip()
            break
    text = re.sub(r'\s+%s\s*$' % _TAGS, '', text, flags=re.I)
    text = re.sub(r'\s+%s\s*$' % _TAGS, '', text, flags=re.I)
    season = _SEASON.search(text)
    if season:
        text = text[:season.start()].strip()
    year = ''
    years = _YEAR.findall(text)
    if years and fold(text[:text.rfind(years[-1])]):
        year = years[-1]
        text = text[:text.rfind(year)].strip()
    return fold(text), year, int(season.group(1)) if season else 0


def matches(metas, titles, year='', season=0):
    """The catalog entries that are this title, by exact name; years more than one apart differ."""
    wanted = set(w for w in (fold(t) for t in titles or []) if w)
    out = []
    for meta in metas or []:
        name, its_year, its_season = parse_name((meta or {}).get('name'))
        if not name or name not in wanted or not meta.get('id'):
            continue
        if year and its_year and abs(int(str(year)[:4]) - int(its_year)) > 1:
            continue
        if season and its_season and season != its_season:
            continue
        out.append(meta)
    return out[:MAX_MATCHES]


def _titles(hints, media_type):
    """The title's names: what is known here, then TMDb's English and Arabic ones."""
    names = [str(n) for n in (hints.get('titles') or []) if n]
    ids = hints.get('ids') or {}
    try:
        from .. import tmdb_direct as T
        path = 'tv' if media_type == 'series' else 'movie'
        tmdb = str(ids.get('tmdb_id') or '')
        if not tmdb and ids.get('imdb_id'):
            found = T._request('/find/%s' % ids['imdb_id'], {'external_source': 'imdb_id'}, timeout=5, max_attempts=1) or {}
            rows = found.get('tv_results' if media_type == 'series' else 'movie_results') or []
            tmdb = str((rows[0] or {}).get('id') or '') if rows else ''
        if tmdb:
            key = 'title_bridge1:%s:%s' % (path, tmdb)
            known = T._cache_get(key)
            if known is None:
                known = []
                for language in ('en-US', 'ar-SA'):
                    raw = T._request('/%s/%s' % (path, tmdb), {'language': language}, timeout=5, max_attempts=1) or {}
                    known += [raw.get('title') or raw.get('name') or '',
                              raw.get('original_title') or raw.get('original_name') or '']
                T._cache_set(key, {'names': known}, 7 * 86400)
            else:
                known = known.get('names') or []
            names += [n for n in known if n]
    except Exception:
        pass
    out, seen = [], set()
    for name in names:
        key = fold(name)
        if key and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _episode_id(client, provider, meta_id, season, episode, timeout):
    data = client.fetch_meta(provider, 'series', meta_id, timeout_override=timeout) or {}
    for video in (data.get('meta') or {}).get('videos') or []:
        try:
            if int(video.get('season')) == int(season) and int(video.get('episode')) == int(episode):
                return video.get('id') or ''
        except (TypeError, ValueError):
            continue
    return ''


def find_ids(provider, media_type, hints, timeout=8.0):
    """The add-on's own stream ids for the title ([] when it has no such title)."""
    from . import client
    started = time.monotonic()
    timeout = min(float(timeout or 8.0), 8.0)
    season, episode = hints.get('season'), hints.get('episode')
    series = media_type == 'series'
    if series and not (season and episode):
        return []
    names = _titles(hints, media_type)
    picked = []
    for name in names[:MAX_SEARCHES]:
        for catalog in search_catalogs(provider, media_type):
            if time.monotonic() - started > BUDGET:
                return []
            try:
                data = client.fetch_catalog(provider, media_type, catalog['id'], extra={'search': name},
                                            timeout_override=timeout, retry=False)
            except Exception:
                continue
            picked = matches((data or {}).get('metas') or [], names, hints.get('year') or '',
                             int(season or 0) if series else 0)
            if picked:
                break
        if picked:
            break
    if not series:
        return [m['id'] for m in picked]
    out = []
    for meta in picked:
        if time.monotonic() - started > BUDGET:
            break
        try:
            video = _episode_id(client, provider, meta['id'], season, episode, timeout)
        except Exception:
            video = ''
        if video:
            out.append(video)
    return out


def payload(provider, media_type, fetch, timeout=8.0):
    """(streams payload, the id that answered) for an add-on that knows the
    title by its own id, or (None, ''). ``fetch(id)`` asks it for the streams."""
    hints = (provider or {}).get('_bridge') or {}
    if not hints:
        return None, ''
    rows, first = [], ''
    for own_id in find_ids(provider, media_type, hints, timeout):
        data = fetch(own_id) or {}
        found = data.get('streams') if isinstance(data, dict) else None
        if isinstance(found, dict):
            found = [found]
        if found:
            rows += list(found)
            first = first or own_id
    return ({'streams': rows}, first) if rows else (None, '')
