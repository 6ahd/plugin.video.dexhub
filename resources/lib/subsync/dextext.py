# -*- coding: utf-8 -*-
"""The text of an embedded subtitle track in an MKV (Dex Hub 5.10.588, for translating it).

The index (Cues) says where every line of a text subtitle sits: its cluster and its
place inside the cluster (CueRelativePosition, written by mkvmerge and FFmpeg). So
each line is one small read, a few kilobytes, and the video itself is never read:
about 1,500 small reads for a film, done several at a time over HTTP.

Pure Python with no Kodi import, like dexcues, so it is tested outside Kodi.
"""
import threading
import time
import zlib

from . import dexcues
from .dexcues import CuesError, children, read_header, _uint, _get_element

ID_CUECLUSTERPOS = 0xF1
ID_CUERELPOS = 0xF0
ID_BLOCKGROUP = 0xA0
ID_BLOCK = 0xA1
ID_SIMPLEBLOCK = 0xA3
ID_BLOCKDURATION = 0x9B
ID_CODECPRIVATE = 0x63A2
ID_CONTENTENCODINGS = 0x6D80
ID_CONTENTENCODING = 0x6240
ID_CONTENTCOMPRESSION = 0x5034
ID_COMPALGO = 0x4254
ID_COMPSETTINGS = 0x4255

WINDOW = 8 * 1024          # one line of a subtitle, its block and some slack
MAX_LINES = 6000


def _encodings(buf, entry):
    """[(algo, settings)] of a TrackEntry's ContentEncodings (zlib = 0, header stripping = 3)."""
    out = []
    for c in children(buf, entry[1], entry[2]):
        if c[0] != ID_CONTENTENCODINGS:
            continue
        for enc in children(buf, c[1], c[2]):
            if enc[0] != ID_CONTENTENCODING:
                continue
            for comp in children(buf, enc[1], enc[2]):
                if comp[0] != ID_CONTENTCOMPRESSION:
                    continue
                algo, settings = 0, b''
                for x in children(buf, comp[1], comp[2]):
                    if x[0] == ID_COMPALGO:
                        algo = _uint(buf, x[1], x[2])
                    elif x[0] == ID_COMPSETTINGS:
                        settings = bytes(buf[x[1]:x[2]])
                out.append((algo, settings))
    return out


def _track_extras(src, meta, number, o):
    """(encodings, codec private) of one track, read from the Tracks element again."""
    el = _get_element(src, meta['head'], meta['pos'][dexcues.ID_TRACKS], dexcues.ID_TRACKS, o['max_tracks_bytes'], o)
    if not el:
        return [], b''
    buf, h = el
    for e in children(buf, h[1], h[2]):
        if e[0] != dexcues.ID_TRACKENTRY:
            continue
        num, private = None, b''
        for c in children(buf, e[1], e[2]):
            if c[0] == dexcues.ID_TRACKNUMBER:
                num = _uint(buf, c[1], c[2])
            elif c[0] == ID_CODECPRIVATE:
                private = bytes(buf[c[1]:c[2]])
        if num == number:
            return _encodings(buf, e), private
    return [], b''


def _cue_points(buf, h, number):
    """[(time, cluster position, relative position, duration)] of one track's cue points."""
    out = []
    for cp in children(buf, h[1], h[2]):
        if cp[0] != dexcues.ID_CUEPOINT:
            continue
        t = None
        hits = []
        for c in children(buf, cp[1], cp[2]):
            if c[0] == dexcues.ID_CUETIME:
                t = _uint(buf, c[1], c[2])
            elif c[0] == dexcues.ID_CUETRACKPOS:
                tn = cl = rel = d = None
                for x in children(buf, c[1], c[2]):
                    if x[0] == dexcues.ID_CUETRACK:
                        tn = _uint(buf, x[1], x[2])
                    elif x[0] == ID_CUECLUSTERPOS:
                        cl = _uint(buf, x[1], x[2])
                    elif x[0] == ID_CUERELPOS:
                        rel = _uint(buf, x[1], x[2])
                    elif x[0] == dexcues.ID_CUEDURATION:
                        d = _uint(buf, x[1], x[2])
                if tn == number:
                    hits.append((cl, rel, d))
        if t is None:
            continue
        for cl, rel, d in hits:
            out.append((t, cl, rel, d))
    return out


def _cues_element(src, meta, o):
    if dexcues.ID_CUES not in meta['pos']:
        raise CuesError('no_cues')
    el = _get_element(src, meta['head'], meta['pos'][dexcues.ID_CUES], dexcues.ID_CUES, o['max_cues_bytes'], o)
    if not el:
        raise CuesError('no_cues')
    return el


def _block_payload(buf, p, end, number):
    """(payload, duration ticks or None) of a BlockGroup or SimpleBlock at p for the track, else None."""
    h = read_header(buf, p, end)
    if not h or h[2] is None or h[2] > end:
        return None
    if h[0] == ID_SIMPLEBLOCK:
        block, duration = (h[1], h[2]), None
    elif h[0] == ID_BLOCKGROUP:
        block, duration = None, None
        for c in children(buf, h[1], h[2]):
            if c[0] == ID_BLOCK:
                block = (c[1], c[2])
            elif c[0] == ID_BLOCKDURATION:
                duration = _uint(buf, c[1], c[2])
        if not block:
            return None
    else:
        return None
    s, e = block
    tl = dexcues._vint_len(buf[s]) if s < e else 0
    if not tl or s + tl + 3 > e:
        return None
    track = buf[s] & (0xFF >> tl)
    for i in range(1, tl):
        track = (track << 8) | buf[s + i]
    if track != number:
        return None
    flags = buf[s + tl + 2]
    if flags & 0x06:            # laced: never used for subtitles
        return None
    return bytes(buf[s + tl + 3:e]), duration


def _decode(payload, encodings):
    for algo, settings in reversed(encodings):
        if algo == 0:
            payload = zlib.decompress(payload)
        elif algo == 3:
            payload = settings + payload
        else:
            raise CuesError('compressed_track', str(algo))
    return payload


def _line_text(payload, codec):
    text = payload.decode('utf-8', 'replace').replace('\r\n', '\n').strip('\x00')
    c = (codec or '').upper()
    if c in ('S_TEXT/ASS', 'S_TEXT/SSA', 'S_ASS', 'S_SSA'):
        # ReadOrder, Layer, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        parts = text.split(',', 8)
        text = parts[-1] if len(parts) == 9 else text
        text = text.replace('\\N', '\n').replace('\\n', '\n').replace('\\h', ' ')
    return text.strip()


# v5.10.590: the file's index once per translation, not once per part (565 KB of Cues took about 5 s
# on box .54's Emby stream, read three times before the first line showed)
_MEMO = {}


def _key(src):
    return getattr(src, 'url', None) or getattr(src, 'path', None)


def _meta(src, o):
    k = (_key(src), 'meta')
    if k[0] and k in _MEMO:
        return _MEMO[k]
    meta = dexcues.read_meta(src, o)
    if k[0]:
        if len(_MEMO) > 12:
            _MEMO.clear()
        _MEMO[k] = meta
    return meta


def _index(src, meta, number, o):
    """(encodings, cue points) of one track, kept for the next part."""
    k = (_key(src), 'track', number)
    if k[0] and k in _MEMO:
        return _MEMO[k]
    encodings, _private = _track_extras(src, meta, number, o)
    buf, h = _cues_element(src, meta, o)
    got = (encodings, _cue_points(buf, h, number))
    if k[0]:
        _MEMO[k] = got
    return got


RETRY_WAIT = (0.5, 1.5, 3.0)        # v5.10.590: seconds before asking a refused line again
RETRY_REASONS = ('http_io', 'timeout', 'http_429', 'http_500', 'http_502', 'http_503', 'http_504')


def line_starts(src, number, options=None):
    """v5.10.590: when each line of a track starts (ms), from the index alone."""
    o = dict(dexcues.DEFAULTS)
    o.update(options or {})
    meta = _meta(src, o)
    _encodings, points = _index(src, meta, number, o)
    return sorted(int(round(p[0] * meta['scale'] / 1e6)) for p in points)


def read_track(src, number, options=None, workers=6, progress=None, cancelled=None, want=None, raw=False,
               window=None):
    """{'ok', 'lines': [(start ms, end ms, text)], 'codec', 'lang', 'name'} of one embedded text track.

    ``src`` is a dexcues source; HTTP sources are read by ``workers`` threads, each with its
    own source from ``src.clone()`` when it has one. ``progress(done, total)`` and
    ``cancelled()`` are optional; ``want(start ms)`` reads only the lines it says yes to
    (5.10.588: the minutes around the viewer first).

    v5.10.598: ``raw`` gives each block's bytes instead of its text (an image track too: PGS,
    read by pgsocr.py), ``window`` the bytes first read for a block (bigger for images); a block
    longer than that is read again whole."""
    o = dict(dexcues.DEFAULTS)
    o.update(options or {})
    try:
        meta = _meta(src, o)
        track = next((t for t in meta['tracks'] if t['number'] == number), None)
        if not track or track['type'] != dexcues.TYPE_SUBTITLE:
            raise CuesError('no_such_track', str(number))
        if not raw and not dexcues.is_text_codec(track['codec']):
            raise CuesError('image_track', track['codec'])
        win = int(window or WINDOW)
        encodings, points = _index(src, meta, number, o)
        if not points:
            raise CuesError('no_cues_for_track')
        if any(rel is None or cl is None for _t, cl, rel, _d in points):
            raise CuesError('no_positions')
        if len(points) > MAX_LINES:
            raise CuesError('too_many_lines', str(len(points)))
        if want is not None:
            points = [p for p in points if want(int(round(p[0] * meta['scale'] / 1e6)))]
            if not points:
                return {'ok': True, 'lines': [], 'codec': track['codec'], 'lang': dexcues.track_lang(track),
                        'name': track['name'], 'stats': src.budget.stats()}
        b = src.budget             # one small read per line, on top of the index, and room for retries
        # an image's block may need a second, whole read
        b.max_requests = b.requests + len(points) * (2 if raw else 1) + len(points) // 3 + 16
        b.max_bytes = b.bytes + len(points) * (win + 16 + (64 * 1024 if raw else 0)) + dexcues.MB
        b.deadline = time.time() + (max(120.0, len(points) * 0.6) if raw else max(90.0, len(points) * 0.25))
        seg_start = _segment_start(meta['head'])
        scale = meta['scale']
        lines = [None] * len(points)
        state = {'done': 0, 'error': None, 'missed': 0}
        allowed = max(3, len(points) // 20)       # lines a server refuses to give, skipped, at most 5 %
        lock = threading.Lock()
        order = list(range(len(points)))

        def fetch(source, at, size=None):
            # v5.10.590: a server that drops a read now and then (box .54's Emby stream refused one
            # of a few hundred, which stopped the whole translation) gets it asked again
            for attempt in range(len(RETRY_WAIT) + 1):
                try:
                    return source.read(at, (size or win) + 8, True)
                except CuesError as e:
                    if e.reason not in RETRY_REASONS or attempt == len(RETRY_WAIT) or \
                            (cancelled and cancelled()) or b.left() <= RETRY_WAIT[attempt]:
                        raise
                    time.sleep(RETRY_WAIT[attempt])

        def one(source, i):
            t, cl, rel, d = points[i]
            at = seg_start + cl + rel + 4      # past the cluster's ID; the size field is 1 to 8 bytes
            try:
                data = fetch(source, at)
            except CuesError as e:
                if e.reason not in RETRY_REASONS:
                    raise
                with lock:
                    state['missed'] += 1
                    if state['missed'] > allowed:
                        raise
                return
            got = None
            for size_len in range(1, 9):
                got = _block_payload(data, size_len, len(data), number)
                if got:
                    break
            if not got and raw:
                # an image's block can be longer than the first read: its size, then the whole of it
                size = _block_size(data, number)
                if size and size + 64 > len(data):
                    data = fetch(source, at, min(size + 64, 4 * dexcues.MB))
                    for size_len in range(1, 9):
                        got = _block_payload(data, size_len, len(data), number)
                        if got:
                            break
            if not got:
                raise CuesError('block_not_found', str(t))
            payload, dur = got
            text = _decode(payload, encodings) if raw else _line_text(_decode(payload, encodings), track['codec'])
            ticks = dur if dur else d
            start = int(round(t * scale / 1e6))
            end = start + (int(round(ticks * scale / 1e6)) if ticks else 0)
            lines[i] = (start, end, text)

        def worker(source):
            while True:
                with lock:
                    if state['error'] or not order or (cancelled and cancelled()):
                        return
                    i = order.pop(0)
                try:
                    one(source, i)
                except CuesError as e:
                    with lock:
                        state['error'] = state['error'] or e
                    return
                except Exception as e:
                    with lock:
                        state['error'] = state['error'] or CuesError('error', '%s: %s' % (e.__class__.__name__, e))
                    return
                with lock:
                    state['done'] += 1
                    done = state['done']
                if progress:
                    try:
                        progress(done, len(points))
                    except Exception:
                        pass

        clone = getattr(src, 'clone', None)
        if clone and workers > 1:
            threads = [threading.Thread(target=worker, args=(clone(),), name='dexhub-subtext-%d' % n)
                       for n in range(workers)]
            for th in threads:
                th.daemon = True
                th.start()
            for th in threads:
                th.join()
        else:
            worker(src)
        if state['error']:
            raise state['error']
        if cancelled and cancelled():
            raise CuesError('cancelled')
        out = sorted((x for x in lines if x and x[2]), key=lambda x: x[0])
        for i, (s, e, text) in enumerate(out):
            if e <= s:          # no duration: up to the next line, at most 6 s
                nxt = out[i + 1][0] if i + 1 < len(out) else s + 4000
                out[i] = (s, max(s + 800, min(nxt, s + 6000)), text)
        return {'ok': True, 'lines': out, 'codec': track['codec'], 'lang': dexcues.track_lang(track),
                'name': track['name'], 'stats': src.budget.stats(), 'missed': state['missed']}
    except CuesError as e:
        return {'ok': False, 'reason': e.reason, 'detail': e.detail, 'stats': src.budget.stats()}
    except Exception as e:
        return {'ok': False, 'reason': 'error', 'detail': '%s: %s' % (e.__class__.__name__, e),
                'stats': src.budget.stats()}


def _block_size(data, number):
    """Where the track's block (group) that starts ``data`` past the cluster ID ends, when it
    ends past the bytes read: 0 when there is none. Each offset for the cluster's size field is
    tried; a SimpleBlock must be the track's own (a stray byte can look like one)."""
    for size_len in range(1, 9):
        h = read_header(data, size_len, len(data) + 64 * dexcues.MB)
        if not h or h[2] is None or h[2] <= len(data) or h[2] > 16 * dexcues.MB:
            continue
        if h[0] == ID_SIMPLEBLOCK:
            s = h[1]
            tl = dexcues._vint_len(data[s]) if s < len(data) else 0
            if not tl or s + tl > len(data):
                continue
            track = data[s] & (0xFF >> tl)
            for i in range(1, tl):
                track = (track << 8) | data[s + i]
            if track == number:
                return h[2]
        elif h[0] == ID_BLOCKGROUP:
            return h[2]
    return 0


def _segment_start(head):
    ebml = read_header(head, 0, len(head))
    p = ebml[2]
    while p < len(head):
        h = read_header(head, p, len(head))
        if not h:
            break
        if h[0] == dexcues.ID_SEGMENT:
            return h[1]
        if h[2] is None:
            break
        p = h[2]
    raise CuesError('no_segment')


def text_tracks(src, options=None):
    """The embedded subtitle tracks in file order: [{number, codec, lang, name, forced, text}]."""
    o = dict(dexcues.DEFAULTS)
    o.update(options or {})
    try:
        meta = _meta(src, o)
    except CuesError as e:
        return {'ok': False, 'reason': e.reason, 'detail': e.detail}
    except Exception as e:
        return {'ok': False, 'reason': 'error', 'detail': '%s: %s' % (e.__class__.__name__, e)}
    out = []
    for t in meta['tracks']:
        if t['type'] == dexcues.TYPE_SUBTITLE:
            out.append({'number': t['number'], 'codec': t['codec'], 'lang': dexcues.track_lang(t),
                        'name': t['name'], 'forced': t['forced'], 'text': dexcues.is_text_codec(t['codec'])})
    return {'ok': True, 'tracks': out}
