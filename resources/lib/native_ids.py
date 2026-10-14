# -*- coding: utf-8 -*-
"""Map Plex/Emby/Jellyfin/Silo server ids to ids a metadata source resolves.

v5.10.80. A Continue Watching row without external guids gets a canonical id
such as plex:<server>:<key> or silo:<server>:movie-tmdb-1386315. No TMDb or
Stremio metadata source understands those, yet three places asked with them
anyway: the exact-meta fetch before a source search (kodi.log: 2.1s spent on
a Silo row before the search could start), the Continue Watching warm-up,
and the artwork pass. Each paid a round trip for an answer that could not
exist, and native rows never received the selected source's metadata.

Lives in its own module so the generated player_runtime.py can import it and
keep working after it is regenerated.
"""
import re

NATIVE_PREFIXES = ('plex:', 'emby:', 'jellyfin:', 'silo:')
_TMDB_IN_KEY = re.compile(r'(?:^|[^a-z0-9])tmdb[-_:](\d+)', re.I)
_IMDB_IN_KEY = re.compile(r'(?:^|[^a-z0-9])(tt\d{5,})(?![0-9])', re.I)


def is_native(canonical_id):
    return str(canonical_id or '').strip().lower().startswith(NATIVE_PREFIXES)


def external_canonical(canonical_id, ids=None):
    """A canonical id a metadata source can resolve, or '' if none is known.

    Non-native ids come back unchanged. For native ids the order is: an imdb
    or tmdb id already known for the row, then one written inside the server
    key itself (Silo keys carry movie-tmdb-<n>). Nothing is guessed from
    titles, so an answer is either exact or absent.
    """
    cid = str(canonical_id or '').strip()
    if not is_native(cid):
        return cid
    ids = ids if isinstance(ids, dict) else {}
    imdb = str(ids.get('imdb_id') or '').strip()
    if imdb.lower().startswith('tt') and imdb[2:].isdigit():
        return imdb
    tmdb = str(ids.get('tmdb_id') or '').strip()
    if tmdb.isdigit():
        return 'tmdb:' + tmdb
    match = _IMDB_IN_KEY.search(cid)
    if match:
        return match.group(1).lower()
    match = _TMDB_IN_KEY.search(cid)
    if match:
        return 'tmdb:' + match.group(1)
    return ''
