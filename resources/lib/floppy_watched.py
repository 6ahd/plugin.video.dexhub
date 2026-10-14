"""Floppy hears what is marked watched by hand in Dex Hub (v5.10.171).

Playback reaches Floppy through the Dex Hub Floppy service add-on, which
scrobbles what Kodi plays. A film or an episode marked watched on the title
page never played, so Floppy never heard of it. This sends the same event
the service sends at the end of a playback: a "stop" scrobble with
completed=true (POST /api/v1/scrobble/, the token the service keeps).

Runs on a thread of its own; nothing here waits on the network.
"""
import threading
import time
import uuid

import xbmc


def _iso(at=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(at or time.time()))


def _imdb(value):
    value = str(value or '').strip().lower()
    return value if value.startswith('tt') and value[2:].isdigit() else ''


def push_watched(media, title='', imdb='', tmdb='', tvdb='', series_title='', season=0, episode=0,
                 duration=0):
    """media: 'movie' or 'series' (an episode: series ids, season, episode)."""
    try:
        from . import floppy_api
        if not floppy_api.configured():
            return False
    except Exception:
        return False
    ids = {}
    if _imdb(imdb):
        ids['imdb'] = _imdb(imdb)
    if str(tmdb or '').strip().isdigit():
        ids['tmdb'] = str(tmdb).strip()
    if str(tvdb or '').strip().isdigit():
        ids['tvdb'] = str(tvdb).strip()
    if not ids:
        return False
    episode_kind = media in ('series', 'tv', 'show', 'episode')
    if episode_kind and not (int(season or 0) >= 0 and int(episode or 0) > 0):
        return False
    body = {'action': 'stop', 'media_type': 'episode' if episode_kind else 'movie', 'ids': ids,
            'title': title or None, 'completed': True, 'client_event_id': uuid.uuid4().hex,
            'played_at': _iso()}
    if duration and float(duration) > 0:
        body['duration_seconds'] = int(float(duration))
        body['position_seconds'] = int(float(duration))
    if episode_kind:
        body.update({'series_title': series_title or None, 'season_number': int(season),
                     'episode_number': int(episode)})

    def run():
        try:
            from . import floppy_api
            status, _ = floppy_api.request('POST', 'scrobble/', body=body)
            what = ('S%02dE%02d' % (int(season), int(episode))) if episode_kind else 'movie'
            xbmc.log('[DexHub] Floppy: marked watched (%s %s): HTTP %s' % (what, sorted(ids), status),
                     xbmc.LOGINFO)
        except Exception as exc:
            xbmc.log('[DexHub] Floppy: mark watched failed: %s' % exc.__class__.__name__, xbmc.LOGINFO)

    thread = threading.Thread(target=run, name='dexhub-floppy-watched')
    thread.daemon = True
    thread.start()
    return True
