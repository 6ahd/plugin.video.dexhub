# -*- coding: utf-8 -*-
"""A subtitle's timing against where people speak in the film (Dex Hub 5.10.597).

soundref.py gives bit masks of speech (50 ms frames) for a few pieces of the
film. The subtitle's lines become one long mask on the same frames; sliding
it over the pieces, the right timing is the one where the most subtitle time
falls on speech. Python's big integers do the sliding: one AND and one
bit count a piece and a position, so every offset of three minutes either
way, for the seven frame-rate differences, is a fraction of a second.

The decision is as careful as the one on a subtitle reference: the best
timing must stand clearly above the film's speech density and above the best
alternative more than a second and a half away, and it must beat the
subtitle's own timing by a margin; otherwise the subtitle stays as it is.
"""
from .dexalign import SCALES

MAX_OFFSET_MS = 180000
RIVAL_MS = 1500
NOOP_MS = 300


def _mask(intervals, scale, frame_ms):
    bits = 0
    for a, b in intervals:
        x = int(round(a * scale / frame_ms))
        y = int(round(b * scale / frame_ms))
        if y > x >= 0:
            bits |= ((1 << (y - x)) - 1) << x
    return bits


try:
    _ones = int.bit_count                       # Python 3.10+
except AttributeError:                          # pragma: no cover
    def _ones(x):
        return bin(x).count('1')


def _windows(mask, pieces, R):
    """Each piece's part of the subtitle mask, R frames either side, cut once."""
    out = []
    for start, n, speech in pieces:
        a = start - R
        width = n + 2 * R
        part = (mask >> a) if a >= 0 else (mask << -a)
        out.append((part & ((1 << width) - 1), n, speech, _ones(speech) / float(n or 1)))
    return out


def _score(windows, R, off):
    """(excess, subtitle frames on speech, subtitle frames) with the subtitle moved by off
    frames. excess: the frames on speech beyond what chance gives in each piece (its
    speech density), so a timing that pushes the subtitle out of the pieces or onto
    the busiest one does not win by percentage alone."""
    hit = tot = 0
    excess = 0.0
    for part, n, speech, density in windows:
        seg = (part >> (R - off)) & ((1 << n) - 1)
        if seg:
            t = _ones(seg)
            h = _ones(seg & speech)
            tot += t
            hit += h
            excess += h - density * t
    return excess, hit, tot


def align(intervals, sref, max_offset_ms=MAX_OFFSET_MS):
    """{'scale', 'offset_ms', 'matched', 'rival', 'identity', 'density', 'hits', 'total', 'max_move_ms'}
    matched, rival, identity: the subtitle frames on speech beyond chance, as a share of the
    subtitle frames in the pieces, at the best timing, the best alternative and the subtitle's
    own timing; density: the film's share of speech."""
    fm = sref['frame_ms']
    pieces = sref['pieces']
    res = {'scale': 1.0, 'offset_ms': 0, 'matched': 0.0, 'rival': 0.0, 'identity': 0.0,
           'density': float(sref.get('speech') or 0), 'hits': 0, 'total': len(intervals), 'max_move_ms': 0}
    if len(intervals) < 30:
        res['reason'] = 'too_few_lines'
        return res
    R = int(max_offset_ms // fm)
    base = _windows(_mask(intervals, 1.0, fm), pieces, R)
    _e, _h, own_tot = _score(base, R, 0)
    if own_tot < 200:
        res['reason'] = 'no_overlap'
        return res
    norm = float(own_tot)

    def share(excess):
        return excess / norm
    found = []                     # (excess, scale, offset frames, hits)
    for sc in SCALES:
        wins = base if sc == 1.0 else _windows(_mask(intervals, sc, fm), pieces, R)
        best = []
        for off in range(-R, R + 1, 2):            # every 100 ms first
            ex, hit, tot = _score(wins, R, off)
            best.append((ex, off))
        best.sort(reverse=True)
        taken = []
        for ex, off in best:
            if len(taken) >= 3:
                break
            if all(abs(off - o) > RIVAL_MS // fm for _v, o in taken):
                taken.append((ex, off))
        for ex, off in taken:                       # then to the frame
            top = (ex, off)
            for o in (off - 1, off + 1):
                if abs(o) <= R:
                    e2 = _score(wins, R, o)[0]
                    if e2 > top[0]:
                        top = (e2, o)
            found.append((top[0], sc, top[1]))
    found.sort(reverse=True)
    excess, scale, off = found[0]
    if scale != 1.0:
        # a speed change only when it clearly does better than the plain timing: a wrong
        # one drifts seconds over a long film
        plain = [f for f in found if f[1] == 1.0]
        if plain and plain[0][0] >= excess * 0.94:
            excess, scale, off = plain[0]
    probes = (intervals[0][0], intervals[len(intervals) // 2][0], intervals[-1][0])

    def moved(sc, o, sc2, o2):
        return max(abs((p * sc + o * fm) - (p * sc2 + o2 * fm)) for p in probes)
    rival = 0.0
    for e, sc, o in found[1:]:
        if moved(sc, o, scale, off) > RIVAL_MS:
            rival = e
            break
    identity = _score(base, R, 0)[0]
    res.update({'scale': scale, 'offset_ms': int(off * fm), 'matched': share(excess), 'rival': share(rival),
                'identity': share(identity), 'hits': int(excess),
                'max_move_ms': int(round(moved(scale, off, 1.0, 0)))})
    return res


def decide(res, min_lift=0.12, min_margin=0.06, min_gain=0.05):
    """sync | in_sync | unsure, as dexalign.decide, for a sound reference. The shares are
    of speech beyond chance: 0 is what a random timing gets."""
    if res.get('reason'):
        return 'unsure'
    clear = res['matched'] >= min_lift and (res['matched'] - res['rival']) >= min_margin
    if not clear:
        return 'unsure'
    if res['max_move_ms'] < NOOP_MS or (res['matched'] - res['identity']) < min_gain:
        # the subtitle's own timing is as good as sound can tell
        return 'in_sync' if res['identity'] >= min_lift else 'unsure'
    return 'sync'
