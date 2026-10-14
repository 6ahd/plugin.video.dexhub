# -*- coding: utf-8 -*-
"""Stable media identity shared by Plex, Emby/Jellyfin and Stremio.

The object intentionally contains no Kodi/UI state.  Providers receive the
same identifiers and episode coordinates, which makes exact-ID lookup the
primary path and translated-title lookup only a fallback.
"""
from __future__ import absolute_import


def _text(value):
    return str(value or '').strip()


def normalize_ids(value):
    src = value or {}
    imdb = _text(src.get('imdb_id') or src.get('imdb') or src.get('imdbId'))
    if imdb and not imdb.lower().startswith('tt') and imdb.isdigit():
        imdb = 'tt' + imdb
    return {
        'imdb_id': imdb.lower(),
        'tmdb_id': _text(src.get('tmdb_id') or src.get('tmdb') or src.get('tmdbId')),
        'tvdb_id': _text(src.get('tvdb_id') or src.get('tvdb') or src.get('tvdbId')),
    }


class MediaIdentity(object):
    __slots__ = ('media_type', 'ids', 'title', 'titles', 'year', 'season', 'episode')

    def __init__(self, media_type='movie', ids=None, title='', titles=None,
                 year='', season=None, episode=None):
        kind = _text(media_type).lower()
        self.media_type = 'series' if kind in ('series', 'show', 'tv', 'episode') else 'movie'
        self.ids = normalize_ids(ids)
        self.title = _text(title)
        seen = set()
        self.titles = []
        for item in [self.title] + list(titles or []):
            clean = _text(item)
            key = clean.casefold()
            if clean and key not in seen:
                seen.add(key)
                self.titles.append(clean)
        self.year = _text(year)
        try:
            self.season = int(season) if season not in (None, '') else None
        except Exception:
            self.season = None
        try:
            self.episode = int(episode) if episode not in (None, '') else None
        except Exception:
            self.episode = None

    @property
    def is_episode(self):
        return self.media_type == 'series' and self.season is not None and self.episode is not None

    @property
    def has_ids(self):
        return any(self.ids.values())

    def cache_key(self):
        return '|'.join([
            self.media_type,
            self.ids.get('imdb_id') or '', self.ids.get('tmdb_id') or '',
            self.ids.get('tvdb_id') or '', str(self.season or ''),
            str(self.episode or ''), self.title.casefold(),
        ])
