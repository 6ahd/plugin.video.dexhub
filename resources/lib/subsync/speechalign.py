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


# ── v5.10.598: a subtitle made for a version with other cuts (an ad break out, a scene more) ──
# One offset fits no such film. Each piece of sound is asked alone where the subtitle fits it;
# when the pieces fall into a few runs, each run agreeing with itself and the runs together
# clearly beating the one offset, the subtitle moves run by run. The cut itself lies somewhere
# between two pieces that were not listened to: it is put in the longest silence between lines
# there, where an ad break or a scene change most likely was.
PIECE_MIN_FRAMES = 240             # 12 s of subtitle in a piece before it has a say
PIECE_MIN_LIFT = 0.15
PIECE_MIN_MARGIN = 0.06
RUN_TOL_MS = 500
SEG_MIN_GAIN = 0.04                # a single piece after a cut is a sixth of what was heard


def align_pieces(intervals, sref, single=None, max_offset_ms=MAX_OFFSET_MS):
    """{'segments': [(first line ms, offset ms)...], 'scale', 'matched', 'rival', 'identity', 'via'}
    or None when the pieces do not tell of cuts clearly (the single result stands)."""
    single = single or {}
    fm = sref['frame_ms']
    pieces = sref['pieces']
    if len(pieces) < 3 or len(intervals) < 30:
        return None
    scale = single.get('scale', 1.0) if abs(single.get('scale', 1.0) - 1.0) > 0.0005 else 1.0
    R = int(max_offset_ms // fm)
    mask = _mask(intervals, scale, fm)
    wins = _windows(mask, pieces, R)
    votes = []                                   # (piece index, offset frames, excess, frames) of the sure ones
    for n, win in enumerate(wins):
        one = [win]
        scored = sorted(((_score(one, R, off)[0], off) for off in range(-R, R + 1, 2)), reverse=True)
        ex, off = scored[0]
        for o in (off - 1, off + 1):
            if abs(o) <= R:
                e2 = _score(one, R, o)[0]
                if e2 > ex:
                    ex, off = e2, o
        tot = _score(one, R, off)[2]
        if tot < PIECE_MIN_FRAMES:
            continue
        rival = next((e for e, o in scored if abs(o - off) > RIVAL_MS // fm), 0.0)
        if ex / tot >= PIECE_MIN_LIFT and (ex - rival) / tot >= PIECE_MIN_MARGIN:
            votes.append((n, off, ex, tot, (ex - rival) / tot))
    if len(votes) < 3:
        return None
    runs = []                                    # [[votes of one offset]...] in the film's order
    for v in votes:
        if runs and abs(v[1] - runs[-1][-1][1]) * fm <= RUN_TOL_MS:
            runs[-1].append(v)
        else:
            runs.append([v])
    if not 2 <= len(runs) <= 4:
        return None
    for run in runs:
        # a run of one piece must be very sure of itself
        if len(run) < 2 and (run[0][2] / run[0][3] < PIECE_MIN_LIFT * 2 or run[0][4] < PIECE_MIN_MARGIN * 2.5):
            return None
    offs = [sorted(v[1] for v in run)[len(run) // 2] for run in runs]
    if any(abs(offs[k + 1] - offs[k]) * fm < 1500 for k in range(len(offs) - 1)):
        return None
    # every piece scored with its run's offset (a piece with no say goes with the run before it)
    owner, k = [], 0
    for n in range(len(pieces)):
        while k + 1 < len(runs) and n >= runs[k + 1][0][0]:
            k += 1
        owner.append(k)
    excess = tot = 0.0
    for n, win in enumerate(wins):
        ex, _h, t = _score([win], R, offs[owner[n]])
        excess += ex
        tot += t
    _e, _h, own_tot = _score(wins, R, 0)
    norm = float(max(own_tot, tot, 1))
    matched = excess / norm
    if matched < single.get('matched', 0.0) + SEG_MIN_GAIN or matched < PIECE_MIN_LIFT:
        return None
    # where each run starts in the subtitle's own time: the longest silence between the last
    # piece of one run and the first piece of the next
    starts = sorted(a * scale for a, _b in intervals)
    segments = [(int(round(starts[0] / scale)), int(offs[0] * fm))]
    for k in range(1, len(runs)):
        last_piece = pieces[runs[k - 1][-1][0]]
        next_piece = pieces[runs[k][0][0]]
        lo = (last_piece[0] + last_piece[1]) * fm - offs[k - 1] * fm      # film time -> subtitle time
        hi = next_piece[0] * fm - offs[k] * fm
        inside = [x for x in starts if lo <= x <= hi]
        if len(inside) >= 2:
            gap, at = max((inside[i + 1] - inside[i], inside[i + 1]) for i in range(len(inside) - 1))
        elif inside:
            at = inside[0]
        else:
            at = (lo + hi) / 2.0
        segments.append((int(round(at / scale)), int(offs[k] * fm)))
    return {'segments': segments, 'scale': scale, 'matched': matched, 'rival': single.get('matched', 0.0),
            'identity': single.get('identity', 0.0), 'offset_ms': int(offs[0] * fm), 'via': 'sound',
            'pieces_sure': len(votes), 'total': len(intervals)}


FPS_STEPS = (23.976, 24.0, 25.0)


def fps_expected(scale, video_fps):
    """v5.10.598: the speed change is exactly the one between the video's frame rate and another
    usual one (a PAL 25 fps video and a subtitle made for 23.976, the common case on British
    shows): the video itself says so, which a sound match alone cannot."""
    try:
        v = float(video_fps or 0)
    except (TypeError, ValueError):
        return False
    if v < 20 or v > 31 or abs(scale - 1.0) < 1e-6:
        return False
    v = min(FPS_STEPS, key=lambda f: abs(f - v)) if min(abs(f - v) for f in FPS_STEPS) < 0.05 else 0
    return bool(v) and any(abs(scale - src / v) < 2e-4 for src in FPS_STEPS if src != v)


def decide(res, min_lift=0.12, min_margin=0.06, min_gain=0.05, video_fps=0):
    """sync | in_sync | unsure, as dexalign.decide, for a sound reference. The shares are
    of speech beyond chance: 0 is what a random timing gets."""
    if res.get('reason'):
        return 'unsure'
    clear = res['matched'] >= min_lift and (res['matched'] - res['rival']) >= min_margin
    if not clear and fps_expected(res.get('scale', 1.0), video_fps):
        # v5.10.598: the video's own frame rate backs the speed change: a smaller margin over the
        # rival is enough, when the change clearly beats the subtitle's own timing
        clear = (res['matched'] >= min_lift and (res['matched'] - res['rival']) >= 0.02
                 and (res['matched'] - res['identity']) >= 0.10)
        if clear:
            res['fps_prior'] = True
    if not clear:
        return 'unsure'
    if res['max_move_ms'] < NOOP_MS or (res['matched'] - res['identity']) < min_gain:
        # the subtitle's own timing is as good as sound can tell
        return 'in_sync' if res['identity'] >= min_lift else 'unsure'
    return 'sync'
