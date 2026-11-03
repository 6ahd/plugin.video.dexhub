# -*- coding: utf-8 -*-
# Vendored from DexSubtitles 5.7.3 (service.subtitles.dexworld, AUTOSYNC_V573) for
# Dex Hub 5.10.117: Dex Hub runs this AutoSync itself only while DexSubtitles
# is not enabled (subsync/runner.py), so the two never sync the same subtitle.
"""DEX_ALIGN_V570: مطابقة توقيت ترجمة على جدول توقيت مرجعي (بالتوقيت فقط، بدون نص).

المرجع: بدايات سطور الترجمة المدمجة من فهرس Cues. الترجمة: أي SRT أو VTT أو ASS بأي ترميز.
يلقى إزاحة وسرعة (فرق الفريمات 23.976/24/25) ثم يعيد كتابة التوقيتات فقط،
والنص والتنسيق والترميز الأصلي يبقى كما هو بايت بايت.
بايثون صافي بدون أي استيراد من Kodi.
"""
import bisect
import re

SCALES = (1.0, 25.0 / 23.976, 23.976 / 25.0, 25.0 / 24.0, 24.0 / 25.0, 24.0 / 23.976, 23.976 / 24.0)

# ── قراءة وكتابة التوقيتات ──
_PAIR = r'(?P<a>(?:\d{1,3}:)?\d{1,2}:\d{2}[,.]\d{1,3})(?P<arrow>[ \t]*-->[ \t]*)(?P<b>(?:\d{1,3}:)?\d{1,2}:\d{2}[,.]\d{1,3})'
_ASS = r'(?m)^(?P<kind>Dialogue|Comment)(?P<sep>:[ \t]*)(?P<layer>[^,\r\n]*),(?P<a>\d{1,2}:\d{2}:\d{2}[.:]\d{1,3}),(?P<b>\d{1,2}:\d{2}:\d{2}[.:]\d{1,3}),'
_RX = {
    ('pair', 's'): re.compile(_PAIR), ('pair', 'b'): re.compile(_PAIR.encode('ascii')),
    ('ass', 's'): re.compile(_ASS), ('ass', 'b'): re.compile(_ASS.encode('ascii')),
}


def _as_str(x):
    return x.decode('ascii') if isinstance(x, bytes) else x


def _ts_ms(t):
    t = _as_str(t).replace(',', '.')
    main, _, frac = t.rpartition('.')
    if not main:  # ASS قديم H:MM:SS:cc
        parts = t.split(':')
        main, frac = ':'.join(parts[:3]), parts[3] if len(parts) > 3 else '0'
    parts = [int(p) for p in main.split(':')]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3], parts[-2], parts[-1]
    ms = int((frac + '000')[:3]) if frac else 0
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def _fmt_like(ms, orig, kind):
    ms = max(0, int(round(ms)))
    o = _as_str(orig)
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, x = divmod(rem, 1000)
    if kind == 'ass':
        cs = int(round(x / 10.0))
        if cs == 100:  # تقريب 995ms وفوق
            return _fmt_like(ms + 5, orig, kind)
        return '%d:%02d:%02d.%02d' % (h, m, s, cs)
    sep = ',' if ',' in o else '.'
    if o.count(':') >= 2 or h:
        return '%02d:%02d:%02d%s%03d' % (h, m, s, sep, x)
    return '%02d:%02d%s%03d' % (m, s, sep, x)


def _open(raw):
    """يرجّع (نص للعمل عليه، دالة ترجّع البايتات، 's' أو 'b'). UTF-16 نفك ترميزه، وغيره نشتغل على البايتات مباشرة."""
    if raw[:2] == b'\xff\xfe':
        return raw[2:].decode('utf-16-le', 'replace'), (lambda t: b'\xff\xfe' + t.encode('utf-16-le')), 's'
    if raw[:2] == b'\xfe\xff':
        return raw[2:].decode('utf-16-be', 'replace'), (lambda t: b'\xfe\xff' + t.encode('utf-16-be')), 's'
    return raw, (lambda b: b), 'b'


def detect_kind(raw, name=''):
    text, _enc, mode = _open(raw)
    head = text[:6000]
    ev = b'[Events]' if mode == 'b' else '[Events]'
    n = (name or '').lower()
    if n.endswith(('.ass', '.ssa')) or ev in head:
        return 'ass'
    return 'pair'


def parse_starts(raw, name=''):
    """بدايات السطور بالملي ثانية (مرتبة) من SRT/VTT/ASS."""
    kind = detect_kind(raw, name)
    text, _enc, mode = _open(raw)
    out = []
    for m in _RX[(kind, mode)].finditer(text):
        if kind == 'ass' and _as_str(m.group('kind')) != 'Dialogue':
            continue
        try:
            a, b = _ts_ms(m.group('a')), _ts_ms(m.group('b'))
        except Exception:
            continue
        if b >= a:
            out.append(a)
    out.sort()
    return out


def apply_transform(raw, scale, offset_ms, name=''):
    """يعيد كتابة التوقيتات فقط: جديد = قديم × scale + offset. كل شي ثاني يبقى كما هو."""
    kind = detect_kind(raw, name)
    text, enc, mode = _open(raw)

    def conv(t):
        return max(0, int(round(_ts_ms(t) * scale + offset_ms)))

    def rep(m):
        a0, b0 = m.group('a'), m.group('b')
        na = conv(a0)
        nb = max(na + 1, conv(b0))
        fa, fb = _fmt_like(na, a0, kind), _fmt_like(nb, b0, kind)
        if mode == 'b':
            fa, fb = fa.encode('ascii'), fb.encode('ascii')
        if kind == 'ass':
            g = m.group
            return g('kind') + g('sep') + g('layer') + (b',' if mode == 'b' else ',') + fa + (b',' if mode == 'b' else ',') + fb + (b',' if mode == 'b' else ',')
        return fa + m.group('arrow') + fb
    return enc(_RX[(kind, mode)].sub(rep, text))


# ── المطابقة ──
def _dedupe(xs, gap=40):
    out = []
    for x in sorted(xs):
        if not out or x - out[-1] > gap:
            out.append(x)
    return out


def _match(ref, sub, scale, off, tol):
    """يطابق كل بداية بعد التحويل مع أقرب بداية في المرجع (مؤشرين). يرجّع قائمة (s, r, residual)."""
    pairs = []
    j = 0
    n = len(ref)
    for s in sub:
        x = s * scale + off
        while j + 1 < n and ref[j + 1] <= x:
            j += 1
        best = None
        for k in (j, j + 1):
            if 0 <= k < n:
                d = ref[k] - x
                if best is None or abs(d) < abs(best[1]):
                    best = (ref[k], d)
        if best is not None and abs(best[1]) <= tol:
            pairs.append((s, best[0], best[1]))
    return pairs


def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else 0.0


def _fit(pairs):
    n = float(len(pairs))
    if n < 20:
        return None
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    mx, my = sx / n, sy / n
    vxx = sum((p[0] - mx) ** 2 for p in pairs)
    if vxx <= 0:
        return None
    a = sum((p[0] - mx) * (p[1] - my) for p in pairs) / vxx
    return a, my - a * mx


def _refine(ref, sub, scale, off, tol):
    pairs = _match(ref, sub, scale, off, tol)
    for _ in range(2):
        if not pairs:
            break
        off += _median([p[2] for p in pairs])
        pairs = _match(ref, sub, scale, off, tol)
    fit = _fit(pairs)
    if fit and 0.94 <= fit[0] <= 1.07:
        a, b = fit
        p2 = _match(ref, sub, a, b, tol)
        if len(p2) >= len(pairs):
            scale, off, pairs = a, b, p2
            if pairs:
                off += _median([p[2] for p in pairs])
                pairs = _match(ref, sub, scale, off, tol)
    return scale, off, pairs


def align(ref_starts, sub_starts, max_offset_ms=300000, tol_ms=350, sample=400):
    """يرجّع dict: scale و offset_ms و matched (نسبة السطور المتطابقة) و rival (أفضل بديل مختلف)
    و hits و total و max_move_ms. القرار نفسه في decide()."""
    ref = _dedupe(ref_starts)
    sub = _dedupe(sub_starts)
    res = {'scale': 1.0, 'offset_ms': 0, 'matched': 0.0, 'rival': 0.0, 'hits': 0, 'total': len(sub)}
    if len(ref) < 30 or len(sub) < 30:
        res['reason'] = 'too_few_lines'
        return res
    step = max(1, len(sub) // sample)
    votes_from = sub[::step]
    R = int(max_offset_ms // 100)
    cands = [(1.0, 0.0)]
    for sc in SCALES:
        hist = [0] * (2 * R + 3)
        for s in votes_from:
            x = s * sc
            lo = bisect.bisect_left(ref, x - max_offset_ms)
            hi = bisect.bisect_right(ref, x + max_offset_ms)
            for r in ref[lo:hi]:
                hist[int((r - x) / 100.0 + R + 1.5)] += 1
        sm = [hist[k - 1] + hist[k] + hist[k + 1] for k in range(1, len(hist) - 1)]
        order = sorted(range(len(sm)), key=sm.__getitem__, reverse=True)
        taken = []
        for k in order:
            if len(taken) >= 4 or sm[k] <= 0:
                break
            if all(abs(k - t) > 10 for t in taken):
                taken.append(k)
                cands.append((sc, (k - R) * 100.0))
    scored = []
    for sc, off in cands:
        s2, o2, pairs = _refine(ref, sub, sc, off, tol_ms)
        scored.append((len(pairs), s2, o2))
    scored.sort(key=lambda x: -x[0])
    hits, scale, off = scored[0]
    probes = (sub[0], sub[len(sub) // 2], sub[-1])

    def moved(sc, o, sc2, o2):
        return max(abs((p * sc + o) - (p * sc2 + o2)) for p in probes)
    rival = 0
    for h, sc, o in scored[1:]:
        if moved(sc, o, scale, off) > 1500:
            rival = h
            break
    total = float(len(sub))
    res.update({'scale': scale, 'offset_ms': int(round(off)), 'hits': hits,
                'matched': hits / total, 'rival': rival / total})
    identity = moved(scale, off, 1.0, 0.0)
    res['max_move_ms'] = int(round(identity))
    return res


def decide(res, min_match=0.5, min_margin=0.2, min_hits=20, noop_ms=250):
    """قرار آمن: طبّق فقط لو التطابق عالي وواضح أفضل من أي بديل."""
    if res.get('reason'):
        return 'unsure'
    strong = res['matched'] >= min_match and (res['matched'] - res['rival']) >= min_margin and res['hits'] >= min_hits
    if not strong:
        return 'unsure'
    if res.get('max_move_ms', 0) < noop_ms:
        return 'in_sync'
    return 'sync'


# ── DEX_SEGMENTS_V578: ترجمة لنسخة ثانية من العمل ──
# نسخة فيها فواصل زيادة (أو ناقصة) ما تنضبط بإزاحة وحدة: الفرق يقفز عند كل فاصل.
# نقسمها أجزاء، لكل جزء إزاحته، وما نطبق إلا لو التقسيم نفسه واضح فوق الصدفة:
# نفس الحساب على الترجمة معكوسة زمنياً هو مقياس الصدفة (rival).
_SEG_WINDOW = 300000
_SEG_MAX_OFFSET = 180000
_SEG_PEAKS = 3
_SEG_PENALTY = 8
_SEG_MIN_CUES = 12
_SEG_MAX = 8
_SEG_MIN_GAIN = 0.12
_SEG_MIN_MARGIN = 0.20
_SEG_MIN_HITS = 30
_SEG_MIN_RATE = 0.30


def _hit(ref, x, tol):
    k = bisect.bisect_left(ref, x)
    return (k < len(ref) and ref[k] - x <= tol) or (k > 0 and x - ref[k - 1] <= tol)


def _seg_candidates(ref, sub):
    """أقوى الإزاحات في كل نافذة من الترجمة."""
    R = _SEG_MAX_OFFSET // 100
    found = []
    i = 0
    while i < len(sub):
        j = i
        while j < len(sub) and sub[j] - sub[i] < _SEG_WINDOW:
            j += 1
        if j - i >= _SEG_MIN_CUES:
            hist = {}
            for s in sub[i:j]:
                lo = bisect.bisect_left(ref, s - _SEG_MAX_OFFSET)
                hi = bisect.bisect_right(ref, s + _SEG_MAX_OFFSET)
                for r in ref[lo:hi]:
                    b = int(round((r - s) / 100.0))
                    hist[b] = hist.get(b, 0) + 1
            taken = []
            for b in sorted(hist, key=lambda k: -(hist.get(k - 1, 0) + hist[k] + hist.get(k + 1, 0))):
                if len(taken) >= _SEG_PEAKS:
                    break
                if abs(b) <= R and all(abs(b - t) > 3 for t in taken):
                    taken.append(b)
            found += [t * 100 for t in taken]
        i = max(j, i + 1)
    out = []
    for off in sorted(found):
        if not out or off - out[-1] > 200:
            out.append(off)
    return out


def _seg_path(ref, sub, offs, tol):
    """لكل سطر إزاحته: أكثر تطابق بأقل عدد قفزات."""
    n = len(offs)
    score = [0] * n
    back = []
    for s in sub:
        top = max(range(n), key=score.__getitem__)
        limit = score[top] - _SEG_PENALTY
        row = [0] * n
        new = [0] * n
        for k in range(n):
            if score[k] >= limit:
                row[k], v = k, score[k]
            else:
                row[k], v = top, limit
            new[k] = v + (1 if _hit(ref, s + offs[k], tol) else 0)
        score = new
        back.append(row)
    k = max(range(n), key=score.__getitem__)
    path = [0] * len(sub)
    for i in range(len(sub) - 1, -1, -1):
        path[i] = offs[k]
        k = back[i][k]
    return path


def _seg_build(ref, sub, tol):
    """[[أول سطر، الإزاحة، عدد السطور، المتطابق]] بعد ضم الأجزاء الصغيرة وضبط كل جزء."""
    offs = _seg_candidates(ref, sub)
    if not offs:
        return []
    segs = []
    for s, off in zip(sub, _seg_path(ref, sub, offs, tol)):
        if not segs or segs[-1][1] != off:
            segs.append([s, off, []])
        segs[-1][2].append(s)
    merged = []
    for seg in segs:
        if merged and len(seg[2]) < _SEG_MIN_CUES:
            merged[-1][2] += seg[2]
        elif merged and len(merged[-1][2]) < _SEG_MIN_CUES:
            seg[0] = merged[-1][0]
            seg[2] = merged[-1][2] + seg[2]
            merged[-1] = seg
        else:
            merged.append(seg)
    out = []
    for start, off, cues in merged:
        pairs = _match(ref, cues, 1.0, off, tol)
        if pairs:
            off += _median([p[2] for p in pairs])
        hits = sum(1 for s in cues if _hit(ref, s + off, tol))
        if out and abs(out[-1][1] - off) <= 300:
            out[-1][2] += len(cues)
            out[-1][3] += hits
        else:
            out.append([start, off, len(cues), hits])
    return out


def align_segments(ref_starts, sub_starts, single=None, min_match=0.5, tol_ms=350):
    """لما الإزاحة الوحدة ما تكفي: {'segments': [(أول سطر بالملي ثانية، الإزاحة)...], 'scale',
    'matched', 'rival', 'hits', 'total'} أو None لو التقسيم مو واضح (الأصل يبقى)."""
    single = single or {}
    ref = _dedupe(ref_starts)
    raw = _dedupe(sub_starts)
    if len(ref) < 30 or len(raw) < 30:
        return None
    best = None
    scales = [1.0]
    if abs(single.get('scale', 1.0) - 1.0) > 0.0005:
        scales.append(single['scale'])
    for sc in scales:
        sub = [x * sc for x in raw]
        segs = _seg_build(ref, sub, tol_ms)
        hits = sum(x[3] for x in segs)
        if segs and (best is None or hits > best[0]):
            best = (hits, sc, segs, sub)
    if best is None:
        return None
    hits, sc, segs, sub = best
    total = float(len(sub))
    matched = hits / total
    if not 2 <= len(segs) <= _SEG_MAX or hits < _SEG_MIN_HITS:
        return None
    if matched < max(min_match, single.get('matched', 0.0) + _SEG_MIN_GAIN):
        return None
    if any(x[2] < _SEG_MIN_CUES or x[3] / float(x[2]) < _SEG_MIN_RATE or abs(x[1]) > _SEG_MAX_OFFSET for x in segs):
        return None
    end = sub[-1] + sub[0]
    mirror = sorted(end - x for x in sub)
    rival = sum(x[3] for x in _seg_build(ref, mirror, tol_ms)) / total
    if matched - rival < _SEG_MIN_MARGIN:
        return None
    return {'segments': [(int(round(x[0] / sc)), int(round(x[1]))) for x in segs], 'scale': sc,
            'matched': matched, 'rival': rival, 'hits': hits, 'total': len(sub)}


def apply_segments(raw, scale, segments, name=''):
    """مثل apply_transform بس لكل جزء إزاحته. السطور تبقى بترتيبها: عند القفزة السطر
    السابق ينتهي قبل اللي بعده."""
    kind = detect_kind(raw, name)
    text, enc, mode = _open(raw)
    firsts = [s[0] for s in segments]
    offs = [s[1] for s in segments]
    rx = _RX[(kind, mode)]
    times = []
    for m in rx.finditer(text):
        a = _ts_ms(m.group('a'))
        off = offs[max(0, bisect.bisect_right(firsts, a) - 1)]
        na = max(0, int(round(a * scale + off)))
        times.append([na, max(na + 1, int(round(_ts_ms(m.group('b')) * scale + off)))])
    if kind != 'ass':
        for i in range(1, len(times)):
            if times[i][0] <= times[i - 1][0]:
                times[i][0] = times[i - 1][0] + 1
                times[i][1] = max(times[i][1], times[i][0] + 1)
            if times[i - 1][1] > times[i][0] and times[i][0] < times[i - 1][1] - 2000:
                times[i - 1][1] = max(times[i - 1][0] + 1, times[i][0] - 1)
    it = iter(times)

    def rep(m):
        na, nb = next(it)
        a0, b0 = m.group('a'), m.group('b')
        fa, fb = _fmt_like(na, a0, kind), _fmt_like(nb, b0, kind)
        if mode == 'b':
            fa, fb = fa.encode('ascii'), fb.encode('ascii')
        if kind == 'ass':
            g = m.group
            return g('kind') + g('sep') + g('layer') + (b',' if mode == 'b' else ',') + fa + (b',' if mode == 'b' else ',') + fb + (b',' if mode == 'b' else ',')
        return fa + m.group('arrow') + fb
    return enc(rx.sub(rep, text))
