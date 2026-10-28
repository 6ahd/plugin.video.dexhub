"""Account snapshot reconciliation without treating a failed pull as deletion."""
import math


def _number(value):
    if value in (None, '') or isinstance(value, bool):
        return ''
    try:
        number = float(value)
        return str(int(number)) if math.isfinite(number) and number >= 0 and number.is_integer() else ''
    except (TypeError, ValueError, OverflowError):
        return ''


def identity(row):
    media = str(row.get('media_type') or 'movie').lower()
    series = media in ('series', 'show', 'tv', 'anime', 'episode')
    cid = str(row.get('imdb_id') or row.get('canonical_id') or row.get('tmdb_id') or '')
    # Nuvio and local adapters can use different video IDs for one episode.
    episode = (_number(row.get('season')), _number(row.get('episode'))) if series else ('', '')
    return ('series' if series else 'movie', cid) + episode


def stamp(row):
    value = float(row.get('updated_at') or 0)
    return int(value / 1000 if value >= 100000000000 else value)


def work_keys(row):
    """The ID aliases consumed by Continue Watching's hidden-title filter."""
    kind, cid, _season, _episode = identity(row)
    prefix = 's:' if kind == 'series' else 'm:'
    keys = {prefix + cid.lower()} if cid else set()
    canonical = str(row.get('canonical_id') or '').lower()
    if canonical:
        keys.add(prefix + canonical)
    tmdb = str(row.get('show_tmdb_id') or row.get('tmdb_id') or '')
    if canonical.startswith(('tmdb:', 'tmdb_')):
        tmdb = canonical[5:].split(':')[0]
    if tmdb:
        keys.add(prefix + 'tmdb' + tmdb)
    return keys


def missing(previous, current, local):
    """Rows once observed remotely, now absent, unless locally played later.

    Previous is account/profile scoped by the caller. Merely absent local
    records never imply a cloud delete. No title-only or name matching.
    """
    present = {identity(row) for row in current}
    removed = {identity(row): stamp(row) for row in previous
               if identity(row) not in present}
    return [row for row in local if identity(row) in removed
            and stamp(row) <= removed[identity(row)]]
