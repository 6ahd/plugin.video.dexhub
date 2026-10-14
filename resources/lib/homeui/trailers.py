# -*- coding: utf-8 -*-
"""Direct IMDb trailers for the Dex Hub Home hero.

No YouTube, no extra addon and no account: IMDb's public GraphQL endpoint
lists a title's trailers together with signed MP4/HLS playback URLs that
Kodi's own player opens directly. The request shape follows the one used by
Kodi trailer addons in production (title -> trailer video id -> playback
URLs). A single combined query is tried first; if the service rejects it the
proven two-step form is used.

Caching:
  * trailer video ids per title live on disk for a week (one small JSON),
    so a repeat visit costs one request instead of two;
  * titles without a trailer are remembered for a day;
  * signed playback URLs are kept in memory only, until shortly before the
    expiry they carry (20 minutes when they carry none, 6 hours at most),
    for every Home opened in this Kodi session.

A title is looked up once at a time (v5.10.103): the Home looks a focused
title's trailer up while it waits to start it, and a start that comes while
that lookup still runs waits for its answer instead of asking again.
"""
import json
import os
import re
import threading
import time
from urllib.request import Request, urlopen

ENDPOINT = 'https://api.graphql.imdb.com/'
_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'),
    'Referer': 'https://www.imdb.com/',
    'Origin': 'https://www.imdb.com',
    'Content-Type': 'application/json',
    'Accept': 'application/json',
}
_IMDB_RE = re.compile(r'^tt\d{5,10}$')
_VIDEO_RE = re.compile(r'^vi\d{5,12}$')
_HEIGHTS = {'DEF_2160p': 2160, 'DEF_1080p': 1080, 'DEF_720p': 720,
            'DEF_480p': 480, 'DEF_360p': 360, 'DEF_SD': 272}

_URL_TTL = 20 * 60
_VIDEO_TTL = 7 * 24 * 3600
_MISS_TTL = 24 * 3600

_Q_COMBINED = ('query($id: ID!){title(id:$id){latestTrailer{id name{value} '
               'playbackURLs{videoDefinition videoMimeType url}}}}')
_Q_STRIP = ('query($id: ID!){title(id:$id){'
            'videoStrip(first:40,filter:{types:[TRAILER]},sort:{by:DATE,order:DESC}){'
            'edges{node{id name{value}}}} latestTrailer{id name{value}}}}')
_Q_VIDEO = ('query($id: ID!){video(id:$id){id name{value} '
            'playbackURLs{videoDefinition videoMimeType url}}}')


def clean_imdb(value):
    text = str(value or '').strip().lower()
    if text.startswith('imdb:'):
        text = text.split(':', 1)[1]
    text = text.split(':', 1)[0]
    return text if _IMDB_RE.match(text) else ''


def choose_stream(streams, max_height=720):
    """Best MP4 at or under ``max_height``; the smallest MP4 otherwise; HLS last."""
    valid = [s for s in (streams or []) if isinstance(s, dict)
             and str(s.get('url') or '').startswith('https://')]
    mp4 = [s for s in valid if str(s.get('videoMimeType') or '').upper() == 'MP4']
    under = [s for s in mp4 if 0 < _HEIGHTS.get(s.get('videoDefinition'), 0) <= max_height]
    if under:
        return max(under, key=lambda s: _HEIGHTS.get(s.get('videoDefinition'), 0))
    if mp4:
        return min(mp4, key=lambda s: _HEIGHTS.get(s.get('videoDefinition'), 9999))
    for s in valid:
        if str(s.get('videoMimeType') or '').upper() in ('M3U8', 'HLS'):
            return s
    return None


def _score_trailer(node, season=None):
    title = str(((node or {}).get('name') or {}).get('value') or '').lower()
    match = re.search(r'(?:season|series|الموسم)\s*(\d+)', title)
    number = int(match.group(1)) if match else None
    if season:
        season_score = 2 if number == int(season) else (0 if number else 1)
    else:
        season_score = 1 if number is None else 0
    return (season_score, 'official' in title, 'trailer' in title,
            'teaser' not in title, 'clip' not in title)


# One cache for every Home opened in this Kodi session (v5.10.103). The
# add-on's Python stays loaded between Home openings (reuselanguageinvoker),
# so a trailer found once plays again at once on the next visit, until its
# signed address expires. Lookups of the same title never run twice at once,
# and the disk copy is written at most every half minute while browsing (and
# when the Home closes) rather than after every lookup.
_LOCK = threading.Lock()
_MEM = {}           # title key|quality -> (monotonic expiry, stream or None)
_INFLIGHT = {}      # title key|quality -> Event of the lookup running now
_DISK = {}          # cache file -> {title key: row}
_DIRTY = set()
_SAVED_AT = {}
_SAVE_EVERY = 30.0
_URL_TTL_MAX = 6 * 3600
_EXPIRES_RE = re.compile(r'[?&]Expires=(\d{9,11})(?:&|$)')


def _url_ttl(url):
    """How long a signed playback address stays usable (10 minutes of margin)."""
    match = _EXPIRES_RE.search(str(url or ''))
    if not match:
        return _URL_TTL
    left = int(match.group(1)) - time.time() - 600
    return max(0.0, min(left, _URL_TTL_MAX))


class TrailerResolver(object):
    def __init__(self, cache_dir, quality=720, request=None, log=None):
        self._path = os.path.join(cache_dir, 'trailers.json')
        self._quality = int(quality or 720)
        self._request = request or self._post
        self._log = log or (lambda msg: None)
        self._lock = _LOCK
        self._mem = _MEM
        self._inflight = _INFLIGHT

    # ------------------------------------------------------------------ io
    def _post(self, query, variables, timeout=8.0):
        body = json.dumps({'query': query, 'variables': variables}).encode('utf-8')
        req = Request(ENDPOINT, data=body, headers=_HEADERS)
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode('utf-8', 'replace'))
        if payload.get('errors') and not payload.get('data'):
            raise ValueError('imdb graphql error')
        return payload.get('data') or {}

    def _load(self):
        data = _DISK.get(self._path)
        if data is not None:
            return data
        try:
            with open(self._path, 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
        except Exception:
            data = {}
        data = data if isinstance(data, dict) else {}
        _DISK[self._path] = data
        return data

    def _changed_locked(self):
        _DIRTY.add(self._path)
        if time.monotonic() - _SAVED_AT.get(self._path, -_SAVE_EVERY) >= _SAVE_EVERY:
            self._save_locked()

    def flush(self):
        """Write the disk copy if it changed (the app calls this when it closes)."""
        with self._lock:
            if self._path in _DIRTY:
                self._save_locked()

    def _save_locked(self):
        _DIRTY.discard(self._path)
        _SAVED_AT[self._path] = time.monotonic()
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            data = self._load()
            if len(data) > 1500:
                newest = sorted(data.items(), key=lambda kv: float((kv[1] or {}).get('t') or 0),
                                reverse=True)[:1000]
                data = dict(newest)
                _DISK[self._path] = data
            tmp = self._path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, separators=(',', ':'))
            os.replace(tmp, self._path)
        except Exception:
            pass


    @staticmethod
    def _key(imdb_id, season=None):
        return '%s:%s' % (imdb_id, season or '')

    def _mem_key(self, key):
        return '%s|%d' % (key, self._quality)

    def cached(self, imdb_id, season=None):
        """(known, stream): what is already known about the title, no network."""
        imdb_id = clean_imdb(imdb_id)
        if not imdb_id:
            return True, None
        key = self._key(imdb_id, season)
        with self._lock:
            hit = self._mem.get(self._mem_key(key))
            if hit and hit[0] > time.monotonic():
                return True, (dict(hit[1]) if hit[1] else None)
            row = self._load().get(key) or {}
        if row.get('none') and time.time() - float(row.get('t') or 0) < _MISS_TTL:
            return True, None
        return False, None

    def forget_url(self, url):
        """A cached address that failed to play is looked up again next time."""
        if not url:
            return
        with self._lock:
            for mkey, (_expiry, stream) in list(self._mem.items()):
                if stream and stream.get('url') == url:
                    self._mem.pop(mkey, None)

    def resolve(self, imdb_id, season=None, timeout=8.0):
        """Return {'url','mime','title','video_id'} or None. Never raises."""
        imdb_id = clean_imdb(imdb_id)
        if not imdb_id:
            return None
        key = self._key(imdb_id, season)
        mkey = self._mem_key(key)
        with self._lock:
            hit = self._mem.get(mkey)
            if hit and hit[0] > time.monotonic():
                return dict(hit[1]) if hit[1] else None
            running = self._inflight.get(mkey)
            if running is None:
                self._inflight[mkey] = threading.Event()
        if running is not None:
            # the same title is being looked up right now: share its answer
            running.wait(timeout * 2 + 1.0)
            with self._lock:
                hit = self._mem.get(mkey)
                if hit and hit[0] > time.monotonic():
                    return dict(hit[1]) if hit[1] else None
            return None
        try:
            return self._resolve(imdb_id, season, key, mkey, timeout)
        finally:
            with self._lock:
                done = self._inflight.pop(mkey, None)
            if done is not None:
                done.set()

    def _resolve(self, imdb_id, season, key, mkey, timeout):
        now = time.time()
        with self._lock:
            row = dict(self._load().get(key) or {})
        if row.get('none') and now - float(row.get('t') or 0) < _MISS_TTL:
            with self._lock:
                self._mem[mkey] = (time.monotonic() + 600, None)
            return None
        result = None
        try:
            video_id = row.get('vid') if now - float(row.get('t') or 0) < _VIDEO_TTL else ''
            if video_id and _VIDEO_RE.match(str(video_id)):
                result = self._from_video(video_id, row.get('name') or '', timeout)
            if not result:
                result = self._discover(imdb_id, season, timeout)
        except Exception as exc:
            self._log('trailer lookup failed for %s: %s' % (imdb_id, exc))
            # Network trouble is not "no trailer": keep no negative entry.
            with self._lock:
                self._mem[mkey] = (time.monotonic() + 60, None)
            return None
        with self._lock:
            disk = self._load()
            if result:
                fresh = {'vid': result.get('video_id') or '', 'name': result.get('title') or ''}
                old = disk.get(key) or {}
                if (old.get('vid'), old.get('name')) != (fresh['vid'], fresh['name']) or \
                        now - float(old.get('t') or 0) > _VIDEO_TTL / 2:
                    fresh['t'] = now
                    disk[key] = fresh
                    self._changed_locked()
                self._mem[mkey] = (time.monotonic() + _url_ttl(result.get('url')), dict(result))
            else:
                disk[key] = {'none': 1, 't': now}
                self._mem[mkey] = (time.monotonic() + 600, None)
                self._changed_locked()
        return dict(result) if result else None

    # ------------------------------------------------------------ helpers
    def _pack(self, video, fallback_name=''):
        stream = choose_stream((video or {}).get('playbackURLs') or [], self._quality)
        if not stream:
            return None
        mime = str(stream.get('videoMimeType') or '').upper()
        return {
            'url': stream.get('url'),
            'mime': 'video/mp4' if mime == 'MP4' else 'application/vnd.apple.mpegurl',
            'title': ((video or {}).get('name') or {}).get('value') or fallback_name or 'Trailer',
            'video_id': (video or {}).get('id') or '',
        }

    def _from_video(self, video_id, name, timeout):
        data = self._request(_Q_VIDEO, {'id': video_id}, timeout=timeout)
        return self._pack(data.get('video') or {}, name)

    def _discover(self, imdb_id, season, timeout):
        if not season:
            try:
                data = self._request(_Q_COMBINED, {'id': imdb_id}, timeout=timeout)
                trailer = ((data.get('title') or {}).get('latestTrailer') or {})
                packed = self._pack(trailer) if trailer.get('playbackURLs') else None
                if packed:
                    return packed
            except Exception:
                pass
        data = self._request(_Q_STRIP, {'id': imdb_id}, timeout=timeout)
        title = data.get('title') or {}
        nodes = [edge.get('node') or {} for edge in
                 ((title.get('videoStrip') or {}).get('edges') or []) if isinstance(edge, dict)]
        nodes = [n for n in nodes if _VIDEO_RE.match(str(n.get('id') or ''))]
        best = max(nodes, key=lambda n: _score_trailer(n, season)) if nodes else None
        best = best or title.get('latestTrailer')
        if not best or not _VIDEO_RE.match(str(best.get('id') or '')):
            return None
        return self._from_video(best['id'], (best.get('name') or {}).get('value') or '', timeout)
