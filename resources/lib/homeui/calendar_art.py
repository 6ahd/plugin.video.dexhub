# -*- coding: utf-8 -*-
"""Complete calendar artwork off the UI thread, retaining each show's poster."""
import re
from concurrent.futures import ThreadPoolExecutor


def poster(raw, image):
    value = raw.strip() if isinstance(raw, str) else ''
    # TMDb paths are filenames at its image root; server/local paths stay intact.
    if re.fullmatch(r'/[A-Za-z0-9_-]+\.(?:jpg|png|webp)', value, re.I):
        return image(value, 'poster')
    return value


def key(row):
    media = 'movie' if row.get('media') in ('movie', 'movies') or row.get('kind') == 'movie' else 'tv'
    return media, str(row.get('tmdb_id') or row.get('tmdb') or '')


def enrich(entries, features, image, lookup=None, cancelled=None):
    """Reuse known art first. Fetch each missing title once, at most 4 at a time.

    A lookup is optional so cached calendar labels render before any network.
    Cancellation stops pending lookups and prevents updates after window exit.
    """
    rows = [dict(row) for row in entries]
    known = {}
    for row in list(features) + rows:
        art = poster(row.get('poster') or row.get('poster_path'), image)
        if art and key(row)[1]:
            known[key(row)] = art
    missing = {}
    for row in rows:
        row['poster'] = poster(row.get('poster'), image) or known.get(key(row), '')
        media, tid = key(row)
        if not row['poster'] and tid.isdigit():
            missing[(media, tid)] = None
    if lookup and missing:
        def fetch(item):
            if cancelled and cancelled.is_set():
                return item, ''
            media, tid = item
            try:
                data = lookup('/%s/%s' % (media, tid)) or {}
                return item, poster(data.get('poster_path'), image)
            except Exception:
                return item, ''
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='DexHubCalendarArt') as pool:
            known.update(pool.map(fetch, list(missing)[:30]))
        for row in rows:
            if not row['poster']:
                row['poster'] = known.get(key(row), '')
    return rows
