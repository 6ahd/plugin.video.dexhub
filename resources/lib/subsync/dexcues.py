# -*- coding: utf-8 -*-
# Vendored from DexSubtitles 5.7.3 (service.subtitles.dexworld, AUTOSYNC_V573) for
# Dex Hub 5.10.117: Dex Hub runs this AutoSync itself only while DexSubtitles
# is not enabled (subsync/runner.py), so the two never sync the same subtitle.
"""DEX_CUES_V570: جدول توقيت الترجمة المدمجة من فهرس Cues في ملفات MKV.

بايثون صافي بدون ffmpeg وبدون أي استيراد من Kodi (قابل للاختبار خارج Kodi).
يقرأ رأس الملف و SeekHead و Info و Tracks و Cues فقط، ولا يقرأ الكلسترات أبداً،
فالقراءة كلها غالباً أقل من 2 ميغا بطلبين أو ثلاثة حتى لو الملف عشرات الجيجات.
"""
import time

ID_EBML = 0x1A45DFA3
ID_DOCTYPE = 0x4282
ID_SEGMENT = 0x18538067
ID_SEEKHEAD = 0x114D9B74
ID_SEEK = 0x4DBB
ID_SEEKID = 0x53AB
ID_SEEKPOS = 0x53AC
ID_INFO = 0x1549A966
ID_TIMESCALE = 0x2AD7B1
ID_DURATION = 0x4489
ID_TRACKS = 0x1654AE6B
ID_TRACKENTRY = 0xAE
ID_TRACKNUMBER = 0xD7
ID_TRACKTYPE = 0x83
ID_CODECID = 0x86
ID_NAME = 0x536E
ID_LANGUAGE = 0x22B59C
ID_LANGUAGE_BCP47 = 0x22B59D
ID_FLAG_DEFAULT = 0x88
ID_FLAG_FORCED = 0x55AA
ID_FLAG_HI = 0x55AB
ID_FLAG_COMMENTARY = 0x55AF
ID_CUES = 0x1C53BB6B
ID_CUEPOINT = 0xBB
ID_CUETIME = 0xB3
ID_CUETRACKPOS = 0xB7
ID_CUETRACK = 0xF7
ID_CUEDURATION = 0xB2
ID_CLUSTER = 0x1F43B675

TYPE_VIDEO, TYPE_AUDIO, TYPE_SUBTITLE, TYPE_METADATA = 1, 2, 17, 33
MB = 1024 * 1024

DEFAULTS = {
    'max_bytes': 24 * MB,      # سقف كل ما يُقرأ في المحاولة
    'max_requests': 16,
    'timeout': 25.0,           # ثواني للمحاولة كلها
    'head_bytes': 512 * 1024,
    'element_chunk': 1 * MB,
    'max_cues_bytes': 16 * MB,
    'max_tracks_bytes': 4 * MB,
    'max_seekhead_bytes': 2 * MB,
    'max_info_bytes': 512 * 1024,
    'tail_bytes': 4 * MB,
    'max_seek_hops': 4,
    'est_max_gap_ms': 4000,
    'est_last_ms': 2000,
    'max_cue_ms': 30000,
}
BUDGET_REASONS = ('budget_bytes', 'budget_requests', 'timeout')


def _fatal(e):
    # أخطاء ما لها علاج بمحاولة موضع ثاني في نفس الملف
    r = e.reason or ''
    return r in BUDGET_REASONS or r in ('no_range', 'http_io', 'io_error') or r.startswith('http_')


class CuesError(Exception):
    def __init__(self, reason, detail=''):
        Exception.__init__(self, '%s: %s' % (reason, detail) if detail else reason)
        self.reason = reason
        self.detail = detail or ''


class Budget(object):
    def __init__(self, o):
        self.bytes = 0
        self.requests = 0
        self.started = time.time()
        self.deadline = self.started + float(o['timeout'])
        self.max_bytes = int(o['max_bytes'])
        self.max_requests = int(o['max_requests'])

    def take(self, length, partial_ok):
        if self.requests + 1 > self.max_requests:
            raise CuesError('budget_requests', str(self.max_requests))
        room = self.max_bytes - self.bytes
        if room <= 0 or (not partial_ok and length > room):
            raise CuesError('budget_bytes', '%dMB' % (self.max_bytes // MB))
        if self.left() <= 0:
            raise CuesError('timeout')
        self.requests += 1
        return min(length, room)

    def add(self, n):
        self.bytes += n

    def left(self):
        return self.deadline - time.time()

    def stats(self):
        return {'bytes': self.bytes, 'requests': self.requests, 'ms': int((time.time() - self.started) * 1000)}


def fmt_stats(s):
    if not s:
        return ''
    b = s.get('bytes', 0)
    size = ('%.1fMB' % (b / float(MB))) if b >= MB else ('%dKB' % max(1, int(round(b / 1024.0))) if b else '0KB')
    return '(%s, %d req, %dms)' % (size, s.get('requests', 0), s.get('ms', 0))


# ── مصادر القراءة ──
class FileSource(object):
    """ملف محلي عادي (للاختبار وللمسارات المحلية)."""
    kind = 'file'

    def __init__(self, path, budget):
        self.budget = budget
        try:
            self.f = open(path, 'rb')
            self.f.seek(0, 2)
            self.size = self.f.tell()
        except (IOError, OSError) as e:
            raise CuesError('io_error', getattr(e, 'strerror', None) or str(e))

    def read(self, pos, length, partial_ok=True):
        if self.size is not None:
            if pos >= self.size:
                return b''
            length = min(length, self.size - pos)
        if length <= 0:
            return b''
        length = self.budget.take(length, partial_ok)
        self.f.seek(pos)
        data = self.f.read(length)
        self.budget.add(len(data))
        return data

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


class VfsSource(object):
    """ملف عبر opener يرجّع كائن فيه size/seek/readBytes (xbmcvfs.File في Kodi)."""
    kind = 'vfs'

    def __init__(self, path, budget, opener):
        self.budget = budget
        try:
            self.f = opener(path)
            self.size = int(self.f.size() or 0) or None
        except Exception as e:
            raise CuesError('io_error', str(e))

    def read(self, pos, length, partial_ok=True):
        if self.size is not None:
            if pos >= self.size:
                return b''
            length = min(length, self.size - pos)
        if length <= 0:
            return b''
        length = self.budget.take(length, partial_ok)
        out = bytearray()
        try:
            self.f.seek(pos, 0)
            while len(out) < length:
                chunk = self.f.readBytes(length - len(out))
                if not chunk:
                    break
                out.extend(chunk)
                if self.budget.left() <= 0:
                    raise CuesError('timeout', 'read')
        except CuesError:
            raise
        except Exception as e:
            raise CuesError('io_error', str(e))
        self.budget.add(len(out))
        return bytes(out)

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


class HttpSource(object):
    """رابط http(s) بطلبات Range. أي خادم يتجاهل Range نوقف معه فوراً بدل ما ننزّل الملف."""
    kind = 'http'

    def __init__(self, url, budget, session, headers=None, verify=False):
        self.url = url
        self.budget = budget
        self.session = session
        self.headers = dict(headers or {})
        self.verify = verify
        self.size = None
        self.no_range = False

    def read(self, pos, length, partial_ok=True):
        if self.size is not None:
            if pos >= self.size:
                return b''
            length = min(length, self.size - pos)
        if length <= 0:
            return b''
        if self.no_range:
            raise CuesError('no_range')
        length = self.budget.take(length, partial_ok)
        h = dict(self.headers)
        h['Range'] = 'bytes=%d-%d' % (pos, pos + length - 1)
        h.setdefault('Accept-Encoding', 'identity')
        left = self.budget.left()
        if left <= 0:
            raise CuesError('timeout', 'http')
        r = None
        out = bytearray()
        try:
            r = self.session.get(self.url, headers=h, stream=True, allow_redirects=True,
                                 verify=self.verify, timeout=(min(8.0, left), min(12.0, left)))
            if r.status_code == 200:
                self.no_range = True
                if pos != 0:
                    raise CuesError('no_range')
            elif r.status_code == 206:
                cr = r.headers.get('Content-Range') or ''
                total = cr.rsplit('/', 1)[-1].strip() if '/' in cr else ''
                if total.isdigit():
                    self.size = int(total)
            elif r.status_code == 416:
                return b''
            else:
                raise CuesError('http_%d' % r.status_code)
            if r.url and r.url != self.url:
                self.url = r.url  # نثبّت الرابط بعد التحويل
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                out.extend(chunk)
                if len(out) >= length:
                    break
                if self.budget.left() <= 0:
                    raise CuesError('timeout', 'http')
        except CuesError:
            raise
        except Exception as e:
            raise CuesError('http_io', e.__class__.__name__)
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
        data = bytes(out[:length])
        self.budget.add(len(data))
        return data

    def close(self):
        pass


# ── EBML ──
def _vint_len(first):
    if not first:
        return 0
    n, mask = 1, 0x80
    while not (first & mask):
        mask >>= 1
        n += 1
    return n


def read_header(buf, pos, end):
    """يرجّع (id, data_start, data_end أو None لو الحجم مجهول, pos) أو None."""
    if pos >= end:
        return None
    il = _vint_len(buf[pos])
    if il == 0 or il > 4 or pos + il > end:
        return None
    eid = 0
    for i in range(il):
        eid = (eid << 8) | buf[pos + i]
    p = pos + il
    if p >= end:
        return None
    sl = _vint_len(buf[p])
    if sl == 0 or sl > 8 or p + sl > end:
        return None
    size = buf[p] & (0xFF >> sl)
    unknown = size == (0xFF >> sl)
    for i in range(1, sl):
        b = buf[p + i]
        size = (size << 8) | b
        if b != 0xFF:
            unknown = False
    ds = p + sl
    return (eid, ds, None if unknown else ds + size, pos)


def children(buf, start, end):
    p = start
    while p < end:
        h = read_header(buf, p, end)
        if h is None or h[2] is None or h[2] > end:
            return
        yield h
        p = h[2]


def _uint(buf, s, e):
    v = 0
    for i in range(s, e):
        v = (v << 8) | buf[i]
    return v


def _str(buf, s, e):
    return bytes(buf[s:e]).decode('utf-8', 'replace').rstrip('\x00')


def _parse_seekhead(buf, h):
    out = []
    for e in children(buf, h[1], h[2]):
        if e[0] != ID_SEEK:
            continue
        sid = spos = None
        for c in children(buf, e[1], e[2]):
            if c[0] == ID_SEEKID:
                sid = _uint(buf, c[1], c[2])
            elif c[0] == ID_SEEKPOS:
                spos = _uint(buf, c[1], c[2])
        if sid is not None and spos is not None:
            out.append((sid, spos))
    return out


def _parse_info(buf, h):
    scale = 1000000
    for c in children(buf, h[1], h[2]):
        if c[0] == ID_TIMESCALE:
            scale = _uint(buf, c[1], c[2]) or 1000000
    return scale


def _parse_tracks(buf, h):
    tracks = []
    for e in children(buf, h[1], h[2]):
        if e[0] != ID_TRACKENTRY:
            continue
        t = {'number': None, 'type': None, 'codec': '', 'name': '', 'language': 'eng', 'bcp47': '',
             'default': True, 'forced': False, 'hi': False, 'commentary': False}
        for c in children(buf, e[1], e[2]):
            cid = c[0]
            if cid == ID_TRACKNUMBER:
                t['number'] = _uint(buf, c[1], c[2])
            elif cid == ID_TRACKTYPE:
                t['type'] = _uint(buf, c[1], c[2])
            elif cid == ID_CODECID:
                t['codec'] = _str(buf, c[1], c[2])
            elif cid == ID_NAME:
                t['name'] = _str(buf, c[1], c[2])
            elif cid == ID_LANGUAGE:
                t['language'] = _str(buf, c[1], c[2]) or 'eng'
            elif cid == ID_LANGUAGE_BCP47:
                t['bcp47'] = _str(buf, c[1], c[2])
            elif cid == ID_FLAG_DEFAULT:
                t['default'] = _uint(buf, c[1], c[2]) != 0
            elif cid == ID_FLAG_FORCED:
                t['forced'] = _uint(buf, c[1], c[2]) == 1
            elif cid == ID_FLAG_HI:
                t['hi'] = _uint(buf, c[1], c[2]) == 1
            elif cid == ID_FLAG_COMMENTARY:
                t['commentary'] = _uint(buf, c[1], c[2]) == 1
        if t['number'] is not None:
            tracks.append(t)
    return tracks


def _parse_cues(buf, h, want):
    out = dict((n, []) for n in want)
    for cp in children(buf, h[1], h[2]):
        if cp[0] != ID_CUEPOINT:
            continue
        t = None
        pos = []
        for c in children(buf, cp[1], cp[2]):
            if c[0] == ID_CUETIME:
                t = _uint(buf, c[1], c[2])
            elif c[0] == ID_CUETRACKPOS:
                tn = d = None
                for x in children(buf, c[1], c[2]):
                    if x[0] == ID_CUETRACK:
                        tn = _uint(buf, x[1], x[2])
                    elif x[0] == ID_CUEDURATION:
                        d = _uint(buf, x[1], x[2])
                if tn in out:
                    pos.append((tn, d))
        if t is None:
            continue
        for tn, d in pos:
            out[tn].append((t, d))
    return out


def is_text_codec(codec):
    c = (codec or '').upper()
    return c.startswith('S_TEXT/') or c in ('S_ASS', 'S_SSA') or c.startswith('D_WEBVTT')


def is_image_codec(codec):
    # DEX_CUES_PGS_V571: ترجمات الصور. فهرس PGS يسجل لحظة ظهور السطر ولحظة مسحه،
    # و VobSub و DVB لحظة الظهور فقط. التوقيت يكفي مرجعاً بدون ما نقرأ الصور نفسها.
    c = (codec or '').upper()
    return c.startswith('S_HDMV/PGS') or c.startswith('S_VOBSUB') or c.startswith('S_DVBSUB')


def norm_lang(x):
    s = (x or '').strip().lower().replace('_', '-').split('-')[0]
    if s in ('', 'und', 'unknown', 'mis', 'zxx'):
        return ''
    two = {'en': 'eng', 'ar': 'ara', 'fr': 'fra', 'de': 'deu', 'es': 'spa', 'it': 'ita', 'pt': 'por', 'tr': 'tur', 'ru': 'rus', 'ja': 'jpn', 'ko': 'kor', 'zh': 'zho', 'nl': 'nld'}
    b2t = {'fre': 'fra', 'ger': 'deu', 'dut': 'nld', 'chi': 'zho', 'per': 'fas', 'gre': 'ell', 'cze': 'ces', 'rum': 'ron'}
    if len(s) == 2:
        return two.get(s, s)
    return b2t.get(s, s)


def track_lang(t):
    return norm_lang(t.get('bcp47')) or norm_lang(t.get('language'))


# ── قراءة البنية ──
def _get_element(src, head, at, expect, cap, o):
    if at + 12 <= len(head):
        h = read_header(head, at, len(head))
        if h and h[0] == expect and h[2] is not None and h[2] <= len(head):
            return head, (h[0], h[1], h[2], h[3])
    if src.size is not None and at >= src.size:
        return None
    first = src.read(at, min(o['element_chunk'], cap + 16), True)
    h = read_header(first, 0, len(first))
    if not h or h[0] != expect:
        return None
    if h[2] is None:
        raise CuesError('unknown_size', '%x' % expect)
    if h[2] > cap + 16:
        raise CuesError('cues_too_large' if expect == ID_CUES else 'element_too_large', '%.1fMB' % (h[2] / float(MB)))
    if h[2] <= len(first):
        return first, h
    rest = src.read(at + len(first), h[2] - len(first), False)
    buf = first + rest
    if h[2] > len(buf):
        raise CuesError('truncated', '%x' % expect)
    return buf, h


def read_meta(src, o):
    head = src.read(0, o['head_bytes'], True)
    if len(head) < 32:
        raise CuesError('short_file')
    ebml = read_header(head, 0, len(head))
    if not ebml or ebml[0] != ID_EBML or ebml[2] is None or ebml[2] > len(head):
        raise CuesError('not_mkv')
    doctype = 'matroska'
    for c in children(head, ebml[1], ebml[2]):
        if c[0] == ID_DOCTYPE:
            doctype = _str(head, c[1], c[2])
    if doctype.lower() not in ('matroska', 'webm'):
        raise CuesError('not_mkv', doctype)
    p, seg = ebml[2], None
    while p < len(head):
        h = read_header(head, p, len(head))
        if not h:
            break
        if h[0] == ID_SEGMENT:
            seg = h
            break
        if h[2] is None:
            break
        p = h[2]
    if not seg:
        raise CuesError('no_segment')
    seg_start = seg[1]
    pos = {}
    p = seg_start
    while p < len(head):
        h = read_header(head, p, len(head))
        if not h:
            break
        if h[0] in (ID_SEEKHEAD, ID_INFO, ID_TRACKS, ID_CUES) and h[0] not in pos:
            pos[h[0]] = h[3]
        if h[0] == ID_CLUSTER or h[2] is None:
            break
        p = h[2]
    queue = [pos[ID_SEEKHEAD]] if ID_SEEKHEAD in pos else []
    seen = set()
    hops = 0
    while queue and hops < o['max_seek_hops']:
        at = queue.pop(0)
        if at in seen:
            continue
        seen.add(at)
        hops += 1
        try:
            el = _get_element(src, head, at, ID_SEEKHEAD, o['max_seekhead_bytes'], o)
        except CuesError as e:
            if _fatal(e):
                raise
            el = None
        if not el:
            continue
        for sid, rel in _parse_seekhead(el[0], el[1]):
            ab = seg_start + rel
            if sid == ID_SEEKHEAD:
                if ab not in seen:
                    queue.append(ab)
                continue
            if sid not in pos:
                pos[sid] = ab
    scale = 1000000
    if ID_INFO in pos:
        el = _get_element(src, head, pos[ID_INFO], ID_INFO, o['max_info_bytes'], o)
        if el:
            scale = _parse_info(el[0], el[1])
    if ID_TRACKS not in pos:
        raise CuesError('no_tracks')
    el = _get_element(src, head, pos[ID_TRACKS], ID_TRACKS, o['max_tracks_bytes'], o)
    if not el:
        raise CuesError('no_tracks')
    return {'head': head, 'pos': pos, 'scale': scale, 'tracks': _parse_tracks(el[0], el[1])}


def read_cues(src, meta, o, want):
    if ID_CUES in meta['pos']:
        el = None
        try:
            el = _get_element(src, meta['head'], meta['pos'][ID_CUES], ID_CUES, o['max_cues_bytes'], o)
        except CuesError as e:
            if _fatal(e) or e.reason == 'cues_too_large':
                raise
        if el:
            return _parse_cues(el[0], el[1], want), 'seekhead'
    # احتياط: Cues بدون مؤشر في SeekHead، ندوّره في آخر الملف
    if not src.size or src.size <= len(meta['head']):
        raise CuesError('no_cues')
    start = max(0, src.size - o['tail_bytes'])
    tail = src.read(start, src.size - start, True)
    i = tail.rfind(b'\x1c\x53\xbb\x6b')
    while i >= 0:
        h = read_header(tail, i, len(tail))
        if h and h[2] is not None and h[2] <= len(tail):
            c = read_header(tail, h[1], h[2])
            if c and c[0] == ID_CUEPOINT:
                m = _parse_cues(tail, h, want)
                if sum(len(v) for v in m.values()):
                    return m, 'tail'
        i = tail.rfind(b'\x1c\x53\xbb\x6b', 0, i)
    raise CuesError('no_cues')


def build_timeline(entries, scale_ns, o):
    to_ms = lambda ticks: int(round(ticks * scale_ns / 1e6))
    uniq = []
    for t, d in sorted(entries, key=lambda x: x[0]):
        if uniq and uniq[-1][0] == t:
            if (d or 0) > (uniq[-1][1] or 0):
                uniq[-1] = (t, d)
            continue
        uniq.append((t, d))
    cues = []
    estimated = 0
    for i, (t, d) in enumerate(uniq):
        start = to_ms(t)
        dur = to_ms(d) if d else 0
        if dur <= 0:
            estimated += 1
            if i + 1 < len(uniq):
                dur = max(1, min(o['est_max_gap_ms'], to_ms(uniq[i + 1][0]) - start))
            else:
                dur = o['est_last_ms']
        cues.append((start, start + min(dur, o['max_cue_ms'])))
    return cues, estimated


def load(src, options=None):
    """يقرأ كل مسارات الترجمة النصية وتوقيتاتها. يرجّع dict فيه ok و tracks و stats."""
    o = dict(DEFAULTS)
    o.update(options or {})
    budget = src.budget
    try:
        meta = read_meta(src, o)
        subs = [t for t in meta['tracks'] if t['type'] == TYPE_SUBTITLE]
        usable = [t for t in subs if is_text_codec(t['codec']) or is_image_codec(t['codec'])]
        if not subs:
            raise CuesError('no_subtitle_tracks')
        if not usable:
            raise CuesError('no_usable_subtitles', ','.join(sorted(set(t['codec'] for t in subs))))
        cmap, via = read_cues(src, meta, o, set(t['number'] for t in usable))
        out = []
        for t in usable:
            cues, est = build_timeline(cmap.get(t['number']) or [], meta['scale'], o)
            item = dict(t)
            item['lang'] = track_lang(t)
            item['kind'] = 'text' if is_text_codec(t['codec']) else 'image'
            item['cues'] = cues
            item['estimated'] = est
            out.append(item)
        return {'ok': True, 'tracks': out, 'via': via, 'size': src.size, 'stats': budget.stats()}
    except CuesError as e:
        return {'ok': False, 'reason': e.reason, 'detail': e.detail, 'stats': budget.stats()}
    except Exception as e:  # لا نطيّح الخدمة أبداً
        return {'ok': False, 'reason': 'error', 'detail': '%s: %s' % (e.__class__.__name__, e), 'stats': budget.stats()}
    finally:
        try:
            src.close()
        except Exception:
            pass


def pick_reference(tracks, min_cues=30, min_span_ms=120000):
    """أفضل مرجع توقيت: النص أولاً (إنجليزي، مو forced ولا تعليق، SRT قبل ASS، وأكثر سطور).
    إذا ما فيه نص يصلح، ترجمة صور بنفس الترتيب، وتحتاج ضعف الأحداث لأن كل سطر PGS له حدثين."""
    def usable(t):
        c = t['cues']
        need = min_cues * (2 if t.get('kind') == 'image' else 1)
        return len(c) >= need and (c[-1][0] - c[0][0]) >= min_span_ms

    def score(t):
        s = 0.0 if t['lang'] == 'eng' else 5.0
        s += 50.0 if t['forced'] else 0.0
        s += 50.0 if t['commentary'] else 0.0
        s += 2.0 if ('ASS' in t['codec'].upper() or 'SSA' in t['codec'].upper()) else 0.0
        s += 1.0 if t['hi'] else 0.0
        return s - min(len(t['cues']), 3000) / 1000.0
    for kind in ('text', 'image'):
        pool = [t for t in tracks if t.get('kind', 'text') == kind and usable(t)]
        if pool:
            pool.sort(key=score)
            return pool[0]
    return None
