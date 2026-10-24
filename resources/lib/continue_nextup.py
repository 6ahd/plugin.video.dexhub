"""Project next episodes into Continue Watching without saving fake progress."""
from datetime import datetime, timezone


def released(value, now=None):
    if not value:
        return True
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date <= (now or datetime.now(timezone.utc))
    except (TypeError, ValueError):
        return False


def merge(rows, candidates, finished, keys):
    rows = list(rows)
    occupied = {k for row in rows for k in keys(row)}
    completed = {}
    for row in finished:
        for k in keys(row):
            completed[k] = max(completed.get(k, 0), int(row.get('updated_at') or 0))
    for candidate in candidates:
        aliases = set(keys(candidate))
        stamp = int(candidate.get('last_watched_at') or 0)
        # A stale Next Up cache must not revive a series deleted in Nuvio.
        if (aliases & occupied or not released(candidate.get('aired')) or not stamp
                or not any(completed.get(k) == stamp for k in aliases)):
            continue
        row = dict(candidate, updated_at=stamp, position=0.0, duration=0.0,
                   percent=0.0, event_type='nextup', stream_url='')
        rows.append(row)
        occupied.update(aliases)
    return rows
