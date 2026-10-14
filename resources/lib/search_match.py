# -*- coding: utf-8 -*-
"""Search relevance matching — pure, dependency-free, unit-testable.

v4.8.4: lifted out of plugin.py (25.8k lines) unchanged. Two reasons this
module exists rather than another block in there:

  * these are the functions that decide whether a search result is shown
    at all, and they were only ever covered by tests that grep the source
    for a string. Here they can be called directly;
  * the relevance FILTER used to be inline in two different render
    functions, so a fix had to be applied twice — one of the ways a patch
    silently missed a site earlier in this cycle.

Ported from the DexWorld Pro server's arabicNorm/titleMatchScore
(server.js), which is tuned against this catalogue. Two of its rules are
load-bearing: a digit in the query must appear verbatim ("الموسم 5" must
never match "الموسم 50"), and a single-word query must hit a WHOLE word
rather than a fragment.
"""

# ── v4.8.2: Arabic-first search matching ─────────────────────────────────
# Ported from the DexWorld Pro server's own titleMatchScore/arabicNorm
# (server.js), which is already tuned against this catalogue: hamza forms,
# ta-marbuta and alef-maqsura are folded, tashkeel dropped, and a
# Damerau/OSA edit distance forgives one or two typos. Two rules from that
# implementation matter as much as the folding itself:
#   * NUMERIC STRICTNESS — a query digit must appear verbatim, so
#     "الموسم 5" never scores against "الموسم 50".
#   * a single-word query must match a whole word, never a fragment.
import unicodedata
from functools import lru_cache


AR_FOLD = {
    '\u0623': '\u0627', '\u0625': '\u0627', '\u0622': '\u0627',   # أ إ آ  → ا
    '\u0649': '\u064a',                                         # ى      → ي
    '\u0629': '\u0647',                                         # ة      → ه
    '\u0624': '\u0648', '\u0626': '\u064a',                     # ؤ ئ    → و ي
    '\u0640': '',                                                # tatweel
    '\u0671': '\u0627',                                         # ٱ (alef wasla) → ا
}
AR_DIGITS = {'\u0660': '0', '\u0661': '1', '\u0662': '2', '\u0663': '3',
              '\u0664': '4', '\u0665': '5', '\u0666': '6', '\u0667': '7',
              '\u0668': '8', '\u0669': '9'}
# v5.10.28: Extended Arabic-Indic digits (Persian/Urdu keyboards and some
# Android Arabic layouts). '۲'.isdigit() is True, so without this the strict
# numeric rule demanded the literal glyph in the title and scored 0.
AR_DIGITS.update({chr(0x06F0 + _i): str(_i) for _i in range(10)})


@lru_cache(maxsize=16384)
def _norm_text(text):
    out = []
    for ch in unicodedata.normalize('NFKD', text.casefold()):
        if '\u064b' <= ch <= '\u065f' or ch == '\u0670':      # tashkeel
            continue
        if unicodedata.combining(ch):                          # é → e
            continue
        ch = AR_DIGITS.get(ch, AR_FOLD.get(ch, ch))
        if not ch:
            continue
        out.append(ch if ch.isalnum() else ' ')
    tokens = []
    for tok in ''.join(out).split():
        # the definite article is noise: "الطيار" must reach "طيار"
        if tok.startswith('\u0627\u0644') and len(tok) > 4:
            tok = tok[2:]
        tokens.append(tok)
    return ' '.join(tokens)


def norm(text):
    """Fold a title to its comparable form with a bounded hot-path cache."""
    return _norm_text(str(text or ''))


def edit_distance_le(a, b, max_dist):
    """Damerau/OSA distance with early exit — a straight port."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return False
    if not la or not lb:
        return max(la, lb) <= max_dist
    prev_prev = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        row_min = i
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cb = b[j - 1]
            v = prev[j - 1] + (0 if ca == cb else 1)
            v = min(v, prev[j] + 1, cur[j - 1] + 1)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                v = min(v, prev_prev[j - 2] + 1)
            cur[j] = v
            row_min = min(row_min, v)
        if row_min > max_dist:
            return False
        prev_prev, prev = prev, cur
    return prev[lb] <= max_dist


def fuzzy_token_eq(qt, it, min_len=4):
    """Word-level match with typo tolerance. Anything containing a digit
    must match exactly — the server's numeric-strictness rule."""
    if qt == it:
        return True
    if any(c.isdigit() for c in qt) or any(c.isdigit() for c in it):
        return False
    if len(qt) < min_len or len(it) < 4:
        return False
    return edit_distance_le(qt, it, 2 if max(len(qt), len(it)) >= 8 else 1)


_ARABIC_REPLACEMENTS = {
    'ا': 'a', 'ب': 'b', 'ت': 't', 'ث': 't', 'ج': 'g', 'ح': 'h',
    'خ': 'k', 'د': 'd', 'ذ': 'z', 'ر': 'r', 'ز': 'z', 'س': 's',
    'ش': 's', 'ص': 's', 'ض': 'd', 'ط': 't', 'ظ': 'z', 'ع': '',
    'غ': 'g', 'ف': 'f', 'ق': 'k', 'ك': 'k', 'ل': 'l', 'م': 'm',
    'ن': 'n', 'ه': 'h', 'و': 'w', 'ي': 'y', 'ء': '',
}
_TITLE_KEYS = (
    'name', 'title', 'original_title', 'original_name', 'originalTitle',
    'english_title', 'arabic_title', 'ar_title', 'aliases', 'alias', 'aka',
    'alternate_titles', 'alternative_titles', 'translations',
)


@lru_cache(maxsize=8192)
def _has_arabic(text):
    return any('\u0600' <= ch <= '\u06ff' for ch in str(text or ''))


@lru_cache(maxsize=8192)
def _phonetic_skeleton(token):
    """Small Arabic/Latin phonetic key, ported from the DexWorld server."""
    raw = str(token or '').casefold()
    if _has_arabic(raw):
        mapped = ''.join(_ARABIC_REPLACEMENTS.get(ch, ch if ch.isdigit() else '')
                         for ch in raw)
    else:
        mapped = raw
        for old, new in (('sh', 's'), ('ch', 's'), ('ph', 'f'), ('th', 't'),
                         ('kh', 'k'), ('ck', 'k'), ('gh', 'g')):
            mapped = mapped.replace(old, new)
        mapped = ''.join('s' if ch == 'c' and idx + 1 < len(mapped)
                         and mapped[idx + 1] in 'eiy' else ch
                         for idx, ch in enumerate(mapped))
        mapped = mapped.replace('q', 'k').replace('c', 'k').replace('j', 'g')
        mapped = mapped.replace('p', 'b').replace('v', 'f').replace('x', 'ks')
    out = []
    for ch in mapped:
        if ch.isdigit():
            out.append(ch)
        elif ch in 'wy':
            if not out:
                out.append(ch)
        elif ch not in 'aeiou' and ch.isalpha():
            out.append(ch)
    deduped = []
    for ch in out or list(mapped):
        if not deduped or deduped[-1] != ch:
            deduped.append(ch)
    return ''.join(deduped)


def _cross_script_score(q_tokens, t_tokens):
    """Score Arabic titles against common Latin transliterations and back."""
    if not q_tokens or not t_tokens:
        return 0
    q_is_ar = [_has_arabic(tok) for tok in q_tokens]
    t_is_ar = [_has_arabic(tok) for tok in t_tokens]
    hits = 0.0
    for q_idx, qtok in enumerate(q_tokens):
        qsk = _phonetic_skeleton(qtok)
        if not qsk or qtok.isdigit():
            continue
        best = 0.0
        for t_idx, ttok in enumerate(t_tokens):
            if q_is_ar[q_idx] == t_is_ar[t_idx] or ttok.isdigit():
                continue
            tsk = _phonetic_skeleton(ttok)
            if qsk == tsk and len(qtok) >= 4 and len(ttok) >= 4:
                best = 1.0
                break
            if (len(qsk) >= 3 and len(tsk) >= 3 and qsk[:1] == tsk[:1]
                    and edit_distance_le(qsk, tsk, 1)):
                best = max(best, 0.85)
        hits += best
    coverage = hits / float(len(set(q_tokens)))
    if coverage >= 0.999:
        return 70
    if len(set(q_tokens)) >= 2 and coverage >= 0.75:
        return 58
    return 0


@lru_cache(maxsize=16384)
def _match_score_text(query, title):
    q_norm, t_norm = norm(query), norm(title)
    if not q_norm or not t_norm:
        return 0
    if q_norm == t_norm:
        return 100
    q_tokens, t_tokens = q_norm.split(), t_norm.split()
    q_set, t_set = set(q_tokens), set(t_tokens)

    # numeric strictness: every digit-word in the query must appear verbatim
    q_nums = [w for w in q_set if w.isdigit()]
    if q_nums:
        t_nums = set(w for w in t_set if w.isdigit())
        for n in q_nums:
            if n not in t_nums:
                return 0

    # A prefix only counts on a WORD boundary: 'ابن' must not score against
    # 'ابناء الشوارع' just because the letters lead the title.
    if t_norm.startswith(q_norm + ' '):
        return 88
    hits = 0.0
    for w in q_set:
        if w in t_set:
            hits += 1
            continue
        # Character edit distance across two scripts is guaranteed wasted
        # work; the phonetic layer below handles Arabic/Latin pairs.
        w_ar = _has_arabic(w)
        if any(w_ar == _has_arabic(cand) and fuzzy_token_eq(w, cand)
               for cand in t_set):
            hits += 0.9          # a typo costs a little, never everything
    coverage = hits / len(q_set)
    if coverage >= 0.999:
        return 80
    if coverage >= 0.8:
        return 74
    # Substring credit is for MULTI-WORD queries only. For a single word the
    # server requires a whole-word hit, otherwise 'ابن' would score against
    # every title merely containing those letters ('ابناء الشوارع').
    if len(q_set) >= 2 and q_norm in t_norm:
        return 66
    if coverage >= 0.6 and len(q_set) >= 2:
        return 58
    if len(q_set) == 1:
        only = next(iter(q_set))
        # a lone short word must match a WHOLE word, never a fragment
        if len(only) >= 5 and any(fuzzy_token_eq(only, c, 5) for c in t_set):
            return 52
        return _cross_script_score(q_tokens, t_tokens)
    return max(int(50 * coverage), _cross_script_score(q_tokens, t_tokens))


def match_score(query, title):
    """Relevance 0-100 for one query/title pair."""
    return _match_score_text(str(query or ''), str(title or ''))


def candidate_titles(value):
    """Return bounded title aliases/translations from common metadata shapes."""
    if not isinstance(value, dict):
        if isinstance(value, (list, tuple, set)):
            return tuple(str(v).strip() for v in value if str(v or '').strip())[:24]
        text = str(value or '').strip()
        return (text,) if text else ()
    out, seen = [], set()

    def _add(raw, depth=0):
        if len(out) >= 24 or raw is None or depth > 2:
            return
        if isinstance(raw, str):
            text = raw.strip()[:300]
            folded = text.casefold()
            if text and folded not in seen:
                seen.add(folded)
                out.append(text)
            return
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                _add(item, depth + 1)
            return
        if isinstance(raw, dict):
            matched = False
            for key in _TITLE_KEYS:
                if key in raw:
                    matched = True
                    _add(raw.get(key), depth + 1)
            if not matched:
                for item in raw.values():
                    _add(item, depth + 1)

    for key in _TITLE_KEYS:
        _add(value.get(key), 0)
    return tuple(out)


def best_match_score(query, value):
    """Best relevance across a title and all known aliases/translations."""
    return max((match_score(query, title) for title in candidate_titles(value)), default=0)



def filter_relevant(items, query, title_of=lambda x: x, fallback_when_empty=True):
    """Drop rows that do not match the query at all.

    Ranking alone was never enough: an addon that ignores the search extra
    answers with its plain catalogue, and those alphabetical titles still
    rendered below the real hits. The server drops them outright
    (`.filter(x => x.score > 0)`) with one safety valve, kept here: when
    NOTHING scores, return the unfiltered set rather than an empty page.

    Returns (kept_with_scores, dropped_count) where each kept entry is
    (item, score).
    """
    scored = [(item, best_match_score(query, title_of(item))) for item in (items or [])]
    kept = [row for row in scored if row[1] > 0]
    if not kept and fallback_when_empty:
        return scored, 0
    return kept, len(scored) - len(kept)
