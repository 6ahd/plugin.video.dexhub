# -*- coding: utf-8 -*-
"""Anime ID mapping — the missing half of Dex Hub's stream-request policy.

A Stremio stream addon only answers the ID SHAPE it declares in `idPrefixes`.
Dex Hub already translates imdb <-> tmdb <-> tvdb, so those addons are served.
Anime addons declare `kitsu:` / `mal:` / `anilist:` / `anidb:` — and Dex Hub
had no way to produce those ids, so it queried them with `tt...` and they
returned NOTHING. That is why anime sources looked empty or "bad".

AIOStreams solves this with a merged anime mapping database. This module does
the same with one source (Fribb's anime-lists, which merges MAL/AniList/Kitsu/
AniDB/IMDb/TMDB/TVDB), downloaded ONCE, indexed into SQLite, and then queried
locally in microseconds.

Everything here is best-effort: if the download or the DB fails, the caller
simply gets no extra ids and the addon behaves exactly as before.
"""
from __future__ import absolute_import

import json
import os
import sqlite3
import threading
import time
from urllib.request import Request, urlopen

import xbmc
import xbmcaddon
import xbmcvfs

SOURCE_URL = ('https://raw.githubusercontent.com/Fribb/anime-lists/'
              'master/anime-list-full.json')
REFRESH_DAYS = 14
_LOCK = threading.Lock()
_MEM = {}

# Fribb key -> our key
_FIELDS = {
    'imdb_id': 'imdb_id',
    'themoviedb_id': 'tmdb_id',
    'thetvdb_id': 'tvdb_id',
    'kitsu_id': 'kitsu_id',
    'mal_id': 'mal_id',
    'anilist_id': 'anilist_id',
    'anidb_id': 'anidb_id',
}
KEYS = tuple(_FIELDS.values())


def _addon():
    return xbmcaddon.Addon()


def enabled():
    try:
        raw = (_addon().getSetting('anime_id_mapping') or 'true').strip().lower()
    except Exception:
        return True
    return raw in ('true', '1', 'yes', 'on')


def _db_path():
    try:
        base = xbmcvfs.translatePath(_addon().getAddonInfo('profile'))
    except Exception:
        base = xbmc.translatePath('special://profile/addon_data/plugin.video.dexhub')
    if not os.path.isdir(base):
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, 'anime_ids.db')


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=5)
    conn.execute('CREATE TABLE IF NOT EXISTS map ('
                 'imdb_id TEXT, tmdb_id TEXT, tvdb_id TEXT, kitsu_id TEXT, '
                 'mal_id TEXT, anilist_id TEXT, anidb_id TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)')
    for key in KEYS:
        conn.execute('CREATE INDEX IF NOT EXISTS idx_%s ON map(%s)' % (key, key))
    return conn


def _built_at(conn):
    try:
        row = conn.execute("SELECT v FROM meta WHERE k='built_at'").fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def _is_fresh(conn):
    return _built_at(conn) + REFRESH_DAYS * 86400 > time.time()


def _download():
    request = Request(SOURCE_URL, headers={'User-Agent': 'DexHub/1.0',
                                           'Accept': 'application/json'})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def build(force=False):
    """Download and index the mapping. Safe to call from a worker thread."""
    with _LOCK:
        conn = _connect()
        try:
            if not force and _is_fresh(conn):
                return True
            rows = _download()
            if not isinstance(rows, list) or not rows:
                return False
            conn.execute('DELETE FROM map')
            batch = []
            for entry in rows:
                if not isinstance(entry, dict):
                    continue
                values = tuple(str(entry.get(src) or '').strip().lower() or None
                               for src in _FIELDS)
                if not any(values):
                    continue
                batch.append(values)
            conn.executemany(
                'INSERT INTO map (%s) VALUES (%s)'
                % (','.join(KEYS), ','.join('?' * len(KEYS))), batch)
            conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('built_at', ?)",
                         (str(time.time()),))
            conn.commit()
            xbmc.log('[DexHub] anime id map built: %d entries' % len(batch), xbmc.LOGINFO)
            return True
        except Exception as exc:
            xbmc.log('[DexHub] anime id map build failed: %s' % exc, xbmc.LOGWARNING)
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass


_BUILDING = [False]


def ensure_async():
    """Kick off a one-time build in the background; never blocks the caller."""
    if _BUILDING[0]:
        return
    _BUILDING[0] = True

    def _work():
        try:
            build()
        finally:
            _BUILDING[0] = False

    try:
        from .runtime_tasks import submit_optional
        accepted = submit_optional(_work, key='anime-id-map-build')
    except Exception:
        accepted = False
    if not accepted:
        _BUILDING[0] = False


def _normalise(key, value):
    value = str(value or '').strip().lower()
    if not value:
        return ''
    if key == 'imdb_id':
        return value if value.startswith('tt') else ''
    return value.split(':')[-1]


def lookup(ids):
    """Given any known ids, return every anime-namespace id we can add.

    Returns {} when nothing new is found, the DB is missing, or the feature is
    off — the caller is never harmed by a failure here.
    """
    if not enabled() or not isinstance(ids, dict):
        return {}
    known = {key: _normalise(key, ids.get(key)) for key in KEYS}
    known = {k: v for k, v in known.items() if v}
    if not known:
        return {}

    cache_key = tuple(sorted(known.items()))
    if cache_key in _MEM:
        return dict(_MEM[cache_key])

    if not os.path.isfile(_db_path()):
        ensure_async()          # first ever use: fetch it for next time
        return {}

    out = {}
    try:
        conn = _connect()
        try:
            if not _is_fresh(conn):
                ensure_async()   # stale: refresh in the background, use it anyway
            for key, value in known.items():
                row = conn.execute(
                    'SELECT %s FROM map WHERE %s = ? LIMIT 1' % (','.join(KEYS), key),
                    (value,)).fetchone()
                if not row:
                    continue
                for index, field in enumerate(KEYS):
                    if row[index] and not known.get(field):
                        out[field] = str(row[index])
                break
        finally:
            conn.close()
    except Exception as exc:
        xbmc.log('[DexHub] anime id lookup failed: %s' % exc, xbmc.LOGDEBUG)
        return {}

    _MEM[cache_key] = dict(out)
    return out
