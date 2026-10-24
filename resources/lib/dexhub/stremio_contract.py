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


def changes(rows, existing, videos_loader=None):
    """Chronological local events per work, preserving all valid cloud fields.

    Times are milliseconds; dates are RFC3339. Completion uses Stremio's
    counters and watched bitmap, never a timestamp in `watched`.
    """
    from .stremio_watched import mark
    working, changed = copy.deepcopy(existing), {}
    def sort_stamp(row):
        try:return number(row.get('updated_at'))
        except (ValueError,TypeError,OverflowError):return 0
    for row in sorted((r for r in rows or [] if isinstance(r, dict)), key=sort_stamp):
        if row.get('event_type') in ('sync','cloud','import','account_sync','nextup'):
            continue
        identifier = str(row.get('imdb_id') or row.get('canonical_id') or '')
        previous = working.get(identifier)
        if not previous and not re.fullmatch(r'tt[0-9]+', identifier):
            continue
        try:
            stamp, duration, position = (number(row.get(k)) for k in ('updated_at','duration','position'))
            if stamp <= 0 or duration <= 0 or (previous and epoch(previous.get('_mtime')) >= stamp):
                continue
            # A removed temporary item still belongs to Continue Watching.
            series = row.get('media_type') in ('series','tv','show','anime','episode')
            if series:
                season, episode = number(row.get('season')), number(row.get('episode'))
                if row.get('season') is None or row.get('episode') is None or not season.is_integer() or not episode.is_integer():
                    continue
                video = '%s:%d:%d' % (identifier, season, episode)
                if row.get('video_id') and row['video_id'] != video:
                    continue
            else:
                video = identifier
            item = copy.deepcopy(previous) if previous else {
                '_id':identifier,'type':'series' if series else 'movie',
                'poster':str(row.get('poster') or ''),'removed':True,'temp':True,
                '_ctime':iso(stamp),'state':{}}
            if item.get('removed'):
                item['temp'] = True  # newer local play revives CW, without adding a library favourite
            item['name'] = item.get('name') or str(row.get('title') or identifier)
            state = item.get('state')
            if not isinstance(state, dict) or not valid_watched(state.get('watched')):
                continue
            for field in ('timeWatched','overallTimeWatched','timesWatched','flaggedWatched'):
                state[field] = int(number(state.get(field)))
            same_video = state.get('video_id') == video
            if not same_video:
                state['flaggedWatched'] = 0
                state['timeWatched'] = 0
            finished = row.get('finished') is True or row.get('event_type') in ('watched','finished','ended') or position >= duration * .90
            if finished:
                newly_watched = not state['flaggedWatched']
                if series:
                    videos = videos_loader(identifier) if videos_loader else []
                    if not videos:
                        raise RuntimeError('Stremio episode metadata unavailable; completion will retry')
                    try:state['watched'], newly_watched = mark(state.get('watched'), videos, video)
                    except ValueError as exc:raise RuntimeError(str(exc))
                if newly_watched:
                    state['timesWatched'] = min(2**32-1, state['timesWatched'] + 1)
                state['flaggedWatched'] = 1
                state['timeWatched'] = max(state['timeWatched'], int(round(duration*1000)))
                state['overallTimeWatched'] = max(state['overallTimeWatched'],state['timeWatched'])
            state.update(timeOffset=0 if finished else int(round(min(position,duration)*1000)),
                         duration=int(round(duration*1000)),video_id=video,lastWatched=iso(stamp))
            item['_mtime'] = iso(stamp)
            if not epoch(item.get('_ctime')):
                item['_ctime'] = iso(stamp)
            if validate(item):
                working[identifier] = item
                changed[identifier] = item
        except (ValueError,TypeError,OverflowError,OSError):
            continue
    return list(changed.values())
