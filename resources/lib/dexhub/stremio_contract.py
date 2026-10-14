"""Conservative Stremio progress writer; no Kodi ids or invented watched fields.

Contract: Stremio/stremio-core, src/types/library/library_item.rs.
The watched field is an encoded per-video bitfield, NEVER a timestamp.
"""
import copy
import datetime
import math
import re


def epoch(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})', value):
        return 0
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, OverflowError):
        return 0


def iso(value):
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not playback time')
    n = float(value or 0)
    if not math.isfinite(n) or n < 0 or n > 2 ** 53:
        raise ValueError('Invalid playback time')
    return n


def valid_watched(value):
    if value in (None, ''):
        return True
    if not isinstance(value, str) or len(value) > 1000000:
        return False
    try:
        import base64, zlib
        anchor, count, payload = value.rsplit(':', 2)
        if not anchor or not count.isascii() or not count.isdigit():
            return False
        decoder = zlib.decompressobj()
        decoder.decompress(base64.b64decode(payload, validate=True), 1000000)
        return decoder.eof and not decoder.unused_data
    except Exception:
        return False


def validate(item):
    if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k] for k in ('_id', 'name', 'type')):
        return False
    if not all(isinstance(item.get(k), bool) for k in ('removed', 'temp')) or not epoch(item.get('_mtime')):
        return False
    state = item.get('state')
    if not isinstance(state, dict) or not valid_watched(state.get('watched')):
        return False
    for k in ('timeWatched', 'timeOffset', 'overallTimeWatched', 'timesWatched', 'flaggedWatched', 'duration'):
        value = state.get(k)
        maximum = 2 ** 32 - 1 if k in ('timesWatched', 'flaggedWatched') else 2 ** 64 - 1
        if type(value) is not int or not 0 <= value <= maximum:
            return False
    for container, k in ((item, '_ctime'), (state, 'lastWatched')):
        if container.get(k) not in (None, '') and not epoch(container[k]):
            return False
    if state.get('video_id') is not None and not isinstance(state['video_id'], str):
        return False
    if 'noNotif' in state and not isinstance(state['noNotif'], bool):
        return False
    return True


def changes(rows, existing):
    # Latest event per work, not one conflicting write per episode.
    newest = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        identifier = str(row.get('imdb_id') or row.get('canonical_id') or '')
        previous = existing.get(identifier)
        if not previous and not re.fullmatch(r'tt[0-9]+', identifier):
            continue
        # Cloud-imported rows are mirrors, not new local playback events.
        if row.get('event_type') in ('sync', 'cloud', 'import', 'account_sync'):
            continue
        try:
            stamp = number(row.get('updated_at'))
            duration = number(row.get('duration'))
            position = number(row.get('position'))
            if stamp <= 0 or duration <= 0:
                continue
            if previous and (previous.get('removed') or epoch(previous.get('_mtime')) >= stamp):
                continue
            if identifier in newest and newest[identifier][0] >= stamp:
                continue
            series = row.get('media_type') in ('series', 'tv', 'show', 'anime')
            video = str(row.get('video_id') or '')
            if series:
                season, episode = row.get('season'), row.get('episode')
                if season is None or episode is None:
                    continue
                season, episode = int(number(season)), int(number(episode))
                video = video or '%s:%d:%d' % (identifier, season, episode)
                if video != '%s:%d:%d' % (identifier, season, episode):
                    continue
            else:
                video = identifier
            item = copy.deepcopy(previous) if previous else {
                '_id': identifier, 'name': str(row.get('title') or identifier),
                'type': 'series' if series else 'movie', 'poster': str(row.get('poster') or ''),
                'removed': False, 'temp': True, '_ctime': iso(stamp), 'state': {}}
            state = item.get('state')
            if not isinstance(state, dict):
                continue
            # Do not mutate or perpetuate legacy corrupt watched payloads.
            watched = state.get('watched')
            if not valid_watched(watched):
                continue
            for field in ('timeWatched', 'overallTimeWatched', 'timesWatched', 'flaggedWatched'):
                state[field] = int(number(state.get(field)))
            state.update(timeOffset=int(round(min(position, duration) * 1000)),
                         duration=int(round(duration * 1000)), video_id=video, lastWatched=iso(stamp))
            # Completion bitfields/counters require full episode history; leave
            # them intact instead of guessing from a percentage threshold.
            item['_mtime'] = iso(stamp)
            if not epoch(item.get('_ctime')):
                item['_ctime'] = iso(stamp)
            item['state'] = state
            if validate(item):
                newest[identifier] = (stamp, item)
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    return [value[1] for value in newest.values()]
