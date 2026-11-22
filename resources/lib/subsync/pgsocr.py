# -*- coding: utf-8 -*-
"""v5.10.598: an embedded image subtitle (PGS, Blu-ray's) made text on this device, so the
Translate subtitle button can translate it.

The display sets are read from the file's own index (dextext, the same way as a text track),
each one drawn as a black-on-white picture of its text in a temp folder, read by an AI service
that reads pictures (the user's own, Control Centre › AI › Reading image subtitles), and the
pictures deleted as soon as their text is out. Only those small pictures of the text leave the
device; the text read is kept (by the file and track) so a second translation reads none again.
"""
import base64
import hashlib
import json
import os
import shutil
import struct
import threading
import time
import zlib

from . import dexcues
from . import dextext

PGS = 'S_HDMV/PGS'
WINDOW = 24 * 1024                 # most display sets fit (a clear one is tiny); a longer one is read again whole
BATCH = 20                         # pictures a request
PARALLEL = 3                       # requests at a time
LONGEST_MS = 7000                  # a line with no end in what was read stays this long at most
CAP_MS = 60000                     # a line never longer than this (DexWorld's contract)
MARGIN = 10                        # pixels around the text
_SEG_PDS, _SEG_ODS, _SEG_PCS, _SEG_END = 0x14, 0x15, 0x16, 0x80


class OcrError(Exception):
    def __init__(self, reason, detail=''):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail


def is_pgs(codec):
    return (codec or '').upper() == PGS


# ------------------------------------------------------------------ PGS -> a picture of its text
def _segments(data):
    p, n = 0, len(data)
    while p + 3 <= n:
        kind = data[p]
        size = (data[p + 1] << 8) | data[p + 2]
        yield kind, data[p + 3:p + 3 + size]
        p += 3 + size


def _rle(data, w, h):
    """PGS run-length object data -> h rows of w palette indexes (bytearrays)."""
    rows, row, i, n = [], bytearray(), 0, len(data)
    while i < n and len(rows) < h:
        b = data[i]
        i += 1
        if b:
            row.append(b)
            continue
        if i >= n:
            break
        f = data[i]
        i += 1
        if f == 0:                      # end of the line
            rows.append(bytes(row[:w].ljust(w, b'\x00')))
            row = bytearray()
            continue
        if f & 0x40:
            if i >= n:
                break
            length = ((f & 0x3F) << 8) | data[i]
            i += 1
        else:
            length = f & 0x3F
        colour = 0
        if f & 0x80:
            if i >= n:
                break
            colour = data[i]
            i += 1
        row.extend(bytes((colour,)) * length)
    if row and len(rows) < h:
        rows.append(bytes(row[:w].ljust(w, b'\x00')))
    return rows


_DARK = bytes(0 if v < 128 else 1 for v in range(256))


def picture(data):
    """(width, height, grey rows) of a display set's text, black on white and cropped, or None for
    a set that clears the screen (or has nothing drawn).

    Each palette entry becomes 255 - its brightness x opacity: white or yellow letters are dark,
    their black outline and the clear background white, the edges stay smooth."""
    palette = bytearray(b'\xff' * 256)
    objects, places, video_h = {}, [], 1080
    for kind, body in _segments(data):
        if kind == _SEG_PCS and len(body) >= 11:
            video_h = (body[2] << 8) | body[3]
            places = []
            p = 11
            for _k in range(body[10]):
                if p + 8 > len(body):
                    break
                oid = (body[p] << 8) | body[p + 1]
                cropped = body[p + 3] & 0x80
                x, y = struct.unpack('>HH', body[p + 4:p + 8])
                places.append((oid, x, y))
                p += 16 if cropped else 8
        elif kind == _SEG_PDS:
            for k in range(2, len(body) - 4, 5):
                pid, lum, _cr, _cb, alpha = body[k:k + 5]
                palette[pid] = 255 - lum * alpha // 255
        elif kind == _SEG_ODS and len(body) >= 4:
            oid, flag = (body[0] << 8) | body[1], body[3]
            if flag & 0x80 and len(body) >= 11:
                w, h = struct.unpack('>HH', body[7:11])
                objects[oid] = [w, h, bytearray(body[11:])]
            elif oid in objects:
                objects[oid][2] += body[4:]
    if not places or not objects:
        return None
    table = bytes(palette)
    drawn = []
    for oid, x, y in places:
        if oid not in objects:
            continue
        w, h, rle = objects[oid]
        rows = [r.translate(table) for r in _rle(rle, w, h)]
        drawn.append((x, y, w, rows))
    if not drawn:
        return None
    x0 = min(d[0] for d in drawn)
    y0 = min(d[1] for d in drawn)
    x1 = max(d[0] + d[2] for d in drawn)
    y1 = max(d[1] + len(d[3]) for d in drawn)
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0 or width * height > 8 * 1024 * 1024:
        return None
    canvas = [bytearray(b'\xff' * width) for _y in range(height)]
    for x, y, w, rows in drawn:
        for n, r in enumerate(rows):
            line = canvas[y - y0 + n]
            line[x - x0:x - x0 + w] = r
    # the text alone: rows and columns with a dark pixel, and a margin
    marks = [r.translate(_DARK) for r in canvas]
    ys = [n for n, m in enumerate(marks) if 0 in m]
    if not ys:
        return None
    lefts = [marks[n].find(b'\x00') for n in ys]
    rights = [marks[n].rfind(b'\x00') for n in ys]
    top, bottom = max(0, ys[0] - MARGIN), min(height, ys[-1] + MARGIN + 1)
    left, right = max(0, min(lefts) - MARGIN), min(width, max(rights) + MARGIN + 1)
    rows = [bytes(r[left:right]) for r in canvas[top:bottom]]
    w, h = right - left, bottom - top
    if 0 < video_h <= 576:                 # a DVD-sized picture, twice as big for the reading
        rows = [bytes(b for b in r for _k in (0, 1)) for r in rows for _k in (0, 1)]
        w, h = w * 2, h * 2
    return w, h, rows


def png(width, height, rows):
    """An 8-bit grey PNG."""
    raw = b''.join(b'\x00' + r for r in rows)

    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def pictures(events):
    """[(start, end, png bytes)] from the display sets read [(start, end, bytes)], in order.

    A line ends when the next set starts (one that clears the screen, or the next line), or with
    its own duration; the same picture shown again at once (a fade) stays one line."""
    events = sorted(events, key=lambda e: e[0])
    out, last = [], None
    for n, (start, end, data) in enumerate(events):
        nxt = events[n + 1][0] if n + 1 < len(events) else None
        got = picture(data)
        if not got:
            last = None
            continue
        stop = end if end and end > start else (nxt if nxt is not None else start + LONGEST_MS)
        if nxt is not None:
            stop = min(stop, nxt)
        if stop <= start:
            stop = start + 2000
        stop = min(stop, start + CAP_MS)
        digest = hashlib.sha1(b''.join(got[2])).digest()
        if last is not None and last[1] == digest and out and out[-1][1] >= start - 40:
            out[-1][1] = stop
            continue
        out.append([start, stop, png(*got)])
        last = (start, digest)
    return [tuple(x) for x in out]


# ------------------------------------------------------------------ the reading
OCR_SYSTEM = ('You read subtitle images. Each image is one subtitle shown on screen: dark text on white. '
              'Write exactly the text in it, in its own language; keep each line break as a newline, keep '
              'tags like [door closes] or (sighs) and speaker dashes. Do not translate, fix or add anything. '
              'An image with no readable text gives "".')


def _ask(chat, batch):
    parts = [{'type': 'text', 'text': '%d images. Answer only JSON: {"1": "text of image 1", "2": "..."}' % len(batch)}]
    for n, (_s, _e, data) in enumerate(batch):
        parts.append({'type': 'text', 'text': 'Image %d:' % (n + 1)})
        parts.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                                                         base64.b64encode(data).decode('ascii')}})
    answer = chat(OCR_SYSTEM, parts)
    if answer is None:
        raise OcrError('ocr_failed')
    text = str(answer)
    a, b = text.find('{'), text.rfind('}')
    try:
        got = json.loads(text[a:b + 1]) if 0 <= a < b else {}
    except ValueError:
        got = {}
    if not isinstance(got, dict) or not got:
        raise OcrError('ocr_unreadable', text[:80])
    return [str(got.get(str(n + 1)) or '').strip() for n in range(len(batch))]


def read_text(shots, chat, folder, progress=None, cancelled=None):
    """[(start, end, text)] of the pictures [(start, end, png)]: each written to ``folder`` (a temp
    folder of its own), read by ``chat(system, parts)`` BATCH at a time, and the folder deleted."""
    os.makedirs(folder, exist_ok=True)
    try:
        files = []
        for n, (start, end, data) in enumerate(shots):
            path = os.path.join(folder, '%05d_%08d.png' % (n, start))
            with open(path, 'wb') as f:
                f.write(data)
            files.append((start, end, path))
        batches = [files[k:k + BATCH] for k in range(0, len(files), BATCH)]
        texts = [None] * len(batches)
        errors = []
        lock = threading.Lock()
        state = {'next': 0, 'done': 0}

        def work():
            while True:
                with lock:
                    k = state['next']
                    state['next'] += 1
                if k >= len(batches) or errors or (cancelled and cancelled()):
                    return
                batch = []
                for s, e, p in batches[k]:
                    with open(p, 'rb') as f:
                        batch.append((s, e, f.read()))
                try:
                    try:
                        texts[k] = _ask(chat, batch)
                    except OcrError as e:
                        if e.reason != 'ocr_unreadable':
                            raise
                        texts[k] = _ask(chat, batch)          # once more: a reply cut short
                except Exception as e:
                    errors.append(e)
                    return
                with lock:
                    state['done'] += len(batch)
                    if progress:
                        progress(state['done'], len(files))
        threads = [threading.Thread(target=work) for _n in range(min(PARALLEL, len(batches)))]
        for t in threads:
            t.daemon = True
            t.start()
        for t in threads:
            t.join()
        if cancelled and cancelled():
            raise OcrError('cancelled')
        if errors:
            e = errors[0]
            raise e if isinstance(e, OcrError) else OcrError('ocr_failed', '%s: %s' % (e.__class__.__name__, e))
        out = []
        for batch, got in zip(batches, texts):
            for (start, end, _p), text in zip(batch, got or []):
                text = '\n'.join(x.strip() for x in text.replace('\r', '').split('\n') if x.strip())
                if text:
                    out.append((start, end, text))
        return out
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ------------------------------------------------------------------ what was read before
def _cache_path(cache_dir, key):
    return os.path.join(cache_dir, 'pgs-%s.json' % key)


def cache_load(cache_dir, key):
    try:
        with open(_cache_path(cache_dir, key), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return dict((int(k), (int(v[0]), str(v[1]))) for k, v in data.items())
    except Exception:
        return {}


def cache_save(cache_dir, key, known):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = _cache_path(cache_dir, key) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(dict((str(k), [v[0], v[1]]) for k, v in known.items()), f, ensure_ascii=False)
        os.replace(tmp, _cache_path(cache_dir, key))
    except Exception:
        pass


def lines(src, number, chat, temp, cache_dir=None, key=None, want=None, workers=12, progress=None,
          cancelled=None, log=None):
    """[(start ms, end ms, text)] of a PGS track's lines that ``want(start)`` says yes to.

    ``progress(stage, done, total)``: stage 'read' (the display sets) then 'ocr'. Lines read
    before (``cache_dir``/``key``) are not read again."""
    known = cache_load(cache_dir, key) if cache_dir and key else {}
    t0 = time.time()

    def step(done, total):
        if progress:
            progress('read', done, total)
    r = dextext.read_track(src, number, workers=workers, progress=step, cancelled=cancelled, want=want, raw=True,
                           window=WINDOW)
    if not r.get('ok'):
        raise dexcues.CuesError(r.get('reason') or 'error', r.get('detail') or '')
    t1 = time.time()
    shots = pictures(r['lines'])
    fresh = [x for x in shots if x[0] not in known]
    if log:
        log('pgs: %d display sets, %d lines (%d read before) in %.1fs %s' % (
            len(r['lines']), len(shots), len(shots) - len(fresh), t1 - t0, dexcues.fmt_stats(r.get('stats'))))
    if fresh:
        got = read_text(fresh, chat, temp, (lambda d, n: progress('ocr', d, n)) if progress else None, cancelled)
        for start, end, text in got:
            known[start] = (end, text)
        for start, end, _png in fresh:              # a picture with no text is not asked again
            known.setdefault(start, (end, ''))
        if cache_dir and key:
            cache_save(cache_dir, key, known)
        if log:
            log('pgs: %d pictures read as text in %.1fs (%d KB sent)' % (
                len(fresh), time.time() - t1, sum(len(x[2]) for x in fresh) * 4 // 3 // 1024))
    out = []
    for start, end, _png in shots:
        e, text = known.get(start, (end, ''))
        if text:
            out.append((start, end, text))
    return out


# ------------------------------------------------------------------ DexWorld reads it (spec 1d)
# The device takes the pictures out of the file once a release; DexWorld reads them (Tesseract),
# translates them with the account's AI and keeps them for every subscriber with that release.
# Nothing of the viewer and no link is sent: the fingerprint is made of the display sets' times
# and sizes alone.
LATIN = ('en', 'fr', 'es', 'de', 'it', 'pt', 'nl', 'sv', 'da', 'no', 'fi', 'pl', 'cs', 'ro', 'hu', 'tr',
         'id', 'ms', 'hr', 'sk', 'sl', 'et', 'lv', 'lt', 'ca', 'eu', 'gl', 'vi')
POLL_S = 5
POLL_LIMIT_S = 300


def server_lang(lang2):
    """'en' or 'ar' for DexWorld's reading, or '' when its models do not read that script."""
    lang2 = (lang2 or '').lower()
    if lang2 == 'ar':
        return 'ar'
    return 'en' if lang2 in LATIN else ''


def fingerprint(sets, lang):
    """The release's fingerprint: the same on every device with that release (spec 1d)."""
    sets = sorted(sets, key=lambda e: e[0])
    text = 'pgs1|%s|%d|%s' % (lang, len(sets), ','.join('%d:%d' % (start, len(data)) for start, _e, data in sets))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:40]


def index_fingerprint(starts, lang):
    """v5.10.598: the release's fingerprint from the file's index alone (when each display set
    starts), so asking DexWorld costs a second instead of reading the whole track. Sent as
    ``ifp`` beside ``fp``; a server that does not know it yet answers by ``fp`` only."""
    starts = sorted(int(x) for x in starts)
    text = 'pgsi1|%s|%d|%s' % (lang, len(starts), ','.join(str(x) for x in starts))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:40]


class DexWorld(object):
    """The three calls of /subtitles/stremio/<code>/ocr. ``title``: {imdbId, season?, episode?}."""

    def __init__(self, origin, code, title, timeout=60):
        self.base = '%s/subtitles/stremio/%s' % (origin.rstrip('/'), code)
        self.title = dict((k, v) for k, v in title.items() if v not in (None, ''))
        self.timeout = timeout

    def _call(self, path, body=None, timeout=None):
        import urllib.error
        import urllib.parse
        import urllib.request
        url = self.base + path
        data = None
        headers = {'User-Agent': 'DexHub', 'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        else:
            url += '?' + urllib.parse.urlencode(self.title)
        request = urllib.request.Request(url, data=data, headers=headers, method='POST' if body is not None else 'GET')
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as answer:
                return answer.status, json.loads(answer.read().decode('utf-8', 'replace') or '{}')
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode('utf-8', 'replace') or '{}')
            except Exception:
                return exc.code, {}
        except Exception as exc:
            return 0, {'error': '%s: %s' % (exc.__class__.__name__, str(exc)[:120])}

    def lookup(self, fp='', ifp=''):
        body = dict(self.title)
        if fp:
            body['fp'] = fp
        if ifp:
            body['ifp'] = ifp
        return self._call('/ocr/lookup', body)

    def submit(self, fp, lang, shots, name='', ifp=''):
        events = [{'start_ms': int(start), 'end_ms': int(end), 'png': base64.b64encode(data).decode('ascii')}
                  for start, end, data in shots]
        body = dict(self.title, fp=fp, lang=lang, events=events)
        if ifp:
            body['ifp'] = ifp
        if name:
            body['title'] = name
        return self._call('/ocr', body, timeout=180)

    def status(self, fp):
        import urllib.parse
        return self._call('/ocr/' + urllib.parse.quote(fp))

    @staticmethod
    def known(answer):
        """The answer names this release (any status but missing), and its fp when it gives one."""
        return isinstance(answer, dict) and answer.get('success') is not False and \
            answer.get('status') not in (None, '', 'missing')


def fit_for_server(shots):
    """The pictures DexWorld takes: 10-4000 of them, each PNG under 256 KB, under 40 MB in all;
    None when this subtitle cannot go (the device reads it itself then)."""
    shots = [x for x in shots if len(x[2]) <= 256 * 1024]
    if not 10 <= len(shots) <= 4000 or sum(len(x[2]) for x in shots) > 40 * 1024 * 1024:
        return None
    return shots
