"""Stable series identities and watched-episode checks, without network IO."""
import re

SERIES_TYPES = ('series', 'anime', 'tv', 'show', 'episode')


def coordinate(row):
    try:
        season, episode = int(row.get('season')), int(row.get('episode'))
        return (season, episode) if season >= 0 and episode > 0 else None
    except (TypeError, ValueError):
        match = re.search(r':(\d+):(\d+)$', str(row.get('video_id') or ''))
        if not match:return None
        point = tuple(map(int, match.groups()))
        return point if point[1] > 0 else None


def show_keys(row):
    """Use SHOW IDs, never a native server episode's external identifiers."""
    keys = set()
    cid = str(row.get('canonical_id') or '').strip().lower()
    native = bool(row.get('native_backend') or row.get('native_server_id'))
    if cid and not native:
        imdb = re.match(r'^(tt\d+)(?::|$)', cid)
        tmdb = re.match(r'^tmdb[:_](\d+)(?::|$)', cid)
        keys.add('imdb:' + imdb.group(1) if imdb else 'tmdb:' + tmdb.group(1) if tmdb else 'id:' + cid)
    imdb = str(row.get('show_imdb_id') or (row.get('imdb_id') if not native else '') or '').lower()
    if re.fullmatch(r'tt\d+', imdb):keys.add('imdb:' + imdb)
    tmdb = str(row.get('show_tmdb_id') or '').strip()
    if tmdb.isdigit():keys.add('tmdb:' + tmdb)
    # An opaque provider canonical remains useful when no show ID is known.
    if not keys and cid:keys.add('id:' + cid)
    return keys


def watched(row):
    try:
        return (str(row.get('event_type') or '').lower() in ('watched', 'finished') or
                float(row.get('percent') or 0) >= (80 if 'simkl' in str(row.get('provider_name') or '').lower() else 90))
    except (TypeError, ValueError):
        return False


class Snapshot:
    def __init__(self, rows):
        self.finished = {}
        for row in rows or []:
            point = coordinate(row)
            if not point or not watched(row):continue
            for key in show_keys(row):self.finished.setdefault(key, set()).add(point)

    def watched(self, row):
        point = coordinate(row)
        return bool(point and any(point in self.finished.get(key, ()) for key in show_keys(row)))

    def furthest(self, row):
        return max((point for key in show_keys(row) for point in self.finished.get(key, ()) if point[0] > 0), default=(0, 0))


def history(store, rows):
    reader = getattr(store, 'nextup_history', None)
    if callable(reader):return reader(rows)
    # Compatibility for lightweight adapters. The production store performs
    # indexed, title-scoped queries without a recent-history truncation.
    return list(rows or [])


def visible(rows, store):
    rows = list(rows or [])
    if not rows:return rows
    state = Snapshot(history(store, rows))
    hidden_reader = getattr(store, 'hidden_nextup_keys', None)
    hidden = hidden_reader() if callable(hidden_reader) else set()
    return [row for row in rows if not state.watched(row) and not show_keys(row).intersection(hidden)]
