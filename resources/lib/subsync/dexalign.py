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
