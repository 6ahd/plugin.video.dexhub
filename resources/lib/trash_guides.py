# -*- coding: utf-8 -*-
"""TRaSH Guides scores for source rows (v5.10.447).

resources/data/trash_formats.json holds TRaSH Guides' custom formats
(https://trash-guides.info, MIT licence; rebuilt by tools/make_trash.py):
Radarr's for movies, Sonarr's for episodes, each with TRaSH's own score. A
row's score is the sum of the formats its release matches, judged the way
Radarr and Sonarr judge them: the specifications are grouped by kind, and a
group passes when none of its required ones fails and not all of them fail;
the format matches when every group passes.

What is judged is the release: the file name when the add-on gives one, and
the add-on's own lines with it (they carry the audio and HDR facts many
names leave out). Source, resolution and remux are read from that text.

v5.10.557 (measured on 1,635 sources of one film: 124 had a release group tier):
- the release group is read from more names: a library file's tags ({edition-Extended},
  {tmdb-19995}) are set aside first, and a name that lost its dash (POV and Red Light hand
  "... x265 QTZ mkv") gives its last word when TRaSH knows that word as a group;
- tier(): the group's tier, by the group alone when the name carries no source word (a
  library file renamed "[1080p AVC DTS]-FraMeSToR");
- rejects(): the formats TRaSH scores below zero that a row matches (x265 at HD, AV1, Dolby
  Vision with no HDR fallback, 3D, upscaled, low quality groups): the filter's "No rejects".
"""
import json
import os
import re

_TABLE = {}
_KIND_ORDER = {'S': 0, 'R': 1, 'M': 2, 'G': 3, 'T': 4}
_EPISODE = re.compile(r'\bS\d{1,3}[ ._-]?E\d{1,4}\b|\b\d{1,2}x\d{2,3}\b|\s-\s\d{1,4}(?:v\d)?\b', re.I)
_LEADING_GROUP = re.compile(r'^\s*\[([^\]]+)\]')        # anime: "[SubsPlease] Title - 05" 
_GROUP = re.compile(r'-[ ]?([A-Za-z0-9][A-Za-z0-9_.@]*?)(?:\.(?:mkv|mp4|avi|ts|m2ts|webm))?(?:\s*\[[^\]]*\])*\s*$', re.I)
# what Radarr / Sonarr would call the release's source (their numbers)
_SOURCES = (
    (re.compile(r'\b(?:remux|bdremux)\b', re.I), 9, 7, 5),                    # Bluray + Remux
    (re.compile(r'\b(?:blu[ ._-]?ray|bdrip|brrip|bd25|bd50|uhd[ ._-]?bd)\b', re.I), 9, 6, 0),
    (re.compile(r'\bweb[ ._-]?rip\b', re.I), 8, 4, 0),
    (re.compile(r'\b(?:web[ ._-]?dl|webdl|web)\b', re.I), 7, 3, 0),
    (re.compile(r'\b(?:hdtv|pdtv|sdtv|tvrip|dsr)\b', re.I), 6, 1, 0),
    (re.compile(r'\b(?:dvd|dvdrip|dvd5|dvd9)\b', re.I), 5, 5, 0),
    (re.compile(r'\b(?:telecine|tc)\b', re.I), 3, 0, 0),
    (re.compile(r'\b(?:telesync|ts|hdts)\b', re.I), 2, 0, 0),
    (re.compile(r'\b(?:cam|camrip|hdcam)\b', re.I), 1, 0, 0),
)
_RESOLUTIONS = ((re.compile(r'(?<!\d)(?:2160p?|4k|uhd)(?!\d)', re.I), 2160),
                (re.compile(r'(?<!\d)1080[pi]?(?!\d)', re.I), 1080),
                (re.compile(r'(?<!\d)720p?(?!\d)', re.I), 720),
                (re.compile(r'(?<!\d)(?:576p?|480p?|360p?)(?!\d)', re.I), 480))
# what a media server adds to a file's name: {edition-Extended}, {imdb-tt0499549}, [tmdb-19995]
_TAGS = re.compile(r'\s*\{[^}]*\}|\s*\[(?:imdb|tmdb|tvdb)[^\]]*\]', re.I)
_EXT_WORD = re.compile(r'[ .](?:mkv|mp4|avi|ts|m2ts|webm)\s*$', re.I)
_LAST_WORD = re.compile(r'([A-Za-z0-9][A-Za-z0-9@_]{1,20})\s*$')
_TIER = re.compile(r'\bTier 0?(\d)\b')
_KNOWN = {}
_BRDISK = re.compile(r'\b(?:bdmv|complete[ ._-]?(?:uhd[ ._-]?)?blu[ ._-]?ray|iso)\b', re.I)


class _LazyPattern(object):
    __slots__ = ('source', 'compiled')

    def __init__(self, source):
        self.source = source
        self.compiled = None

    def search(self, text):
        compiled = self.compiled
        if compiled is None:
            try:
                compiled = re.compile(self.source, re.I)
            except re.error:
                compiled = re.compile(r'(?!)')      # a broken pattern matches nothing
            self.compiled = compiled
        return compiled.search(text)


def _table():
    if not _TABLE:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            'resources', 'data', 'trash_formats.json')
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except Exception:
            data = {}
        for app in ('radarr', 'sonarr'):
            compiled = []
            for fmt in data.get(app) or []:
                specs = []
                for kind, negate, required, value in fmt.get('specs') or []:
                    if kind in ('T', 'G'):
                        # v5.10.547: compiled on first use: building both
                        # tables compiled 1,841 patterns (~0.9 s of a source
                        # search) though a movie never reads Sonarr's and most
                        # formats are settled before their text patterns
                        value = _LazyPattern(value)
                    specs.append((kind, negate, required, value))
                if specs:
                    # v5.10.547: cheap kinds first, required ones first in a
                    # kind, so _matches can stop at the first deciding answer
                    specs.sort(key=lambda spec: (_KIND_ORDER.get(spec[0], 9), not spec[2]))
                    compiled.append((fmt.get('name'), int(fmt.get('score') or 0), int(fmt.get('anime') or 0),
                                     bool(fmt.get('anime_only')), specs))
            _TABLE[app] = compiled
    return _TABLE


def release_facts(text, episode=False):
    """(source, modifier, resolution, group) as Radarr (or Sonarr) numbers them."""
    source = modifier = 0
    for pattern, radarr, sonarr, mod in _SOURCES:
        if pattern.search(text):
            source, modifier = (sonarr if episode else radarr), mod
            break
    if not modifier and _BRDISK.search(text):
        modifier = 4
    resolution = 0
    for pattern, value in _RESOLUTIONS:
        if pattern.search(text):
            resolution = value
            break
    first = _TAGS.sub('', text.split('\n', 1)[0]).strip()
    leading = _LEADING_GROUP.match(first)
    if leading and not source:
        source = 3 if episode else 7      # "[Group] Title - 05": a fansub/web release, as Sonarr reads it
    found = leading or _GROUP.search(first)
    group = found.group(1).strip() if found else ''
    if not group:
        # a name that lost its dash: its last word, when TRaSH knows it as a group
        word = _LAST_WORD.search(_EXT_WORD.sub('', first))
        if word and group_tier(word.group(1), episode, any_format=True):
            group = word.group(1)
    return source, modifier, resolution, group


def group_tier(group, episode=False, anime=False, any_format=False):
    """The lowest release group tier TRaSH puts this group in (1, 2, 3...), 0 when in none.
    any_format: 1 when any of TRaSH's formats names the group (a tier or a low quality list)."""
    group = str(group or '').strip()
    if not group:
        return 0
    key = (group.lower(), bool(episode), bool(anime), bool(any_format))
    if key in _KNOWN:
        return _KNOWN[key]
    best = 0
    for name, _default, _anime, anime_only, specs in _table().get('sonarr' if episode else 'radarr') or ():
        tier = _TIER.search(str(name))
        if (anime_only and not anime) or (not tier and not any_format):
            continue
        if any(kind == 'G' and not negate and value.search(group) for kind, negate, _req, value in specs):
            number = int(tier.group(1)) if tier else 9
            if any_format:
                best = 1
                break
            best = number if not best else min(best, number)
    if len(_KNOWN) > 4000:
        _KNOWN.clear()
    _KNOWN[key] = best
    return best


def _matches(specs, text, facts):
    """Radarr's rule: every kind of specification passes when none of its
    required ones fails and at least one of them holds. v5.10.547: answered
    with as few regex searches as possible (the specs come sorted: cheap kinds
    first, required ones first): a failed required spec ends the format, and
    once a kind holds its other optional specs are not searched. 435 AIOStreams
    rows cost ~200,000 regex searches before; same results."""
    source, modifier, resolution, group = facts
    passed = {}
    for kind, negate, required, value in specs:
        if not required and passed.get(kind):
            continue
        if kind == 'T':
            hit = bool(value.search(text))
        elif kind == 'G':
            hit = bool(group) and bool(value.search(group))
        elif kind == 'S':
            hit = source == int(value)
        elif kind == 'R':
            hit = resolution == int(value)
        else:
            hit = modifier == int(value)
        ok = hit != negate
        if required and not ok:
            return False
        if ok:
            passed[kind] = True
        else:
            passed.setdefault(kind, False)
    return all(passed.values())


def score(release, extra='', episode=None, anime=False):
    """(score, [format names]) for one release. release: the file name (or
    the add-on's title line); extra: the add-on's other lines."""
    release = str(release or '').strip()
    text = ' '.join(x for x in (release, str(extra or '')) if x)
    if not text:
        return 0, []
    if episode is None:
        episode = bool(_EPISODE.search(text))
    facts = release_facts(release or text, episode)
    total, names = 0, []
    for name, default, anime_score, anime_only, specs in _table().get('sonarr' if episode else 'radarr') or ():
        if anime_only and not anime:
            continue
        value = anime_score if anime else default
        if value and _matches(specs, text, facts):
            total += value
            names.append(name)
    return total, names


# v5.10.447: "WEB first" (TRaSH's WEB profiles take no Remux or Blu-ray): those
# copies drop below every WEB copy of the same resolution
_WEB_FIRST_PENALTY = 4000
_DISC = re.compile(r'\b(?:remux|bdremux|blu[ ._-]?ray|bdrip|brrip|bd25|bd50)\b', re.I)


def entry_score(entry, anime=False, prefer=''):
    """The score of a Dex Hub source row (kept on the row): TRaSH's anime
    scores for an anime title; prefer='web' puts WEB copies first."""
    key = 'anime' if anime else 'default'
    cached = entry.get('_trash')
    if not (isinstance(cached, dict) and key in cached):
        release = entry.get('filename') or str(entry.get('stream_desc_raw') or '').split('\n', 1)[0]
        extra = ' '.join(str(entry.get(k) or '') for k in ('stream_name_raw', 'stream_desc_raw', 'name', 'label2'))
        value, names = score(release, extra, anime=anime)
        episode = bool(_EPISODE.search(' '.join((str(release), extra))))
        _source, _modifier, _res, group = release_facts(release or extra, episode)
        cached = cached if isinstance(cached, dict) else {}
        cached[key] = [value, names, bool(_DISC.search(release or extra)), group, episode]
        entry['_trash'] = cached
    value, disc = cached[key][0], cached[key][2]
    if prefer == 'web' and disc:
        value -= _WEB_FIRST_PENALTY
    return value


def _kept(entry, anime=False):
    entry_score(entry, anime=anime)
    kept = entry['_trash']['anime' if anime else 'default']
    return kept if len(kept) >= 5 else list(kept) + ['', False]


def tier(entry, anime=False):
    """The release group's tier for a source row (1, 2, 3...; 0 when its group is in none):
    the lowest of the tier formats the release matches, else the group's own tier (a library
    file whose name has no source word matches no format though its group is known)."""
    _value, names, _disc, group, episode = _kept(entry, anime)[:5]
    tiers = [int(m.group(1)) for m in (_TIER.search(str(name)) for name in names or ()) if m]
    if tiers:
        return min(tiers)
    return group_tier(group, episode, anime)


def rejects(entry, anime=False):
    """The formats TRaSH scores below zero that the row matches (['x265 (HD)', 'Upscaled'])."""
    _value, names, _disc, _group, episode = _kept(entry, anime)[:5]
    if not names:
        return []
    low = _LOW.get((episode, anime))
    if low is None:
        low = _LOW[(episode, anime)] = set(
            name for name, default, anime_score, _only, _specs in _table().get('sonarr' if episode else 'radarr') or ()
            if (anime_score if anime else default) < -100)
    return [name for name in names if name in low]


_LOW = {}


def reason(entry, anime=False):
    """'TRaSH 8450 · Remux Tier 01 · TrueHD ATMOS · DV Boost' for the row; what TRaSH rejects
    it for comes first ('TRaSH -9500 · rejected: x265 (HD) · ...'), then its tier."""
    value, names = _kept(entry, anime)[:2]
    if not names:
        return ''
    low = rejects(entry, anime)
    rest = [name for name in names if name not in low]
    rest.sort(key=lambda name: 0 if _TIER.search(str(name)) else 1)
    if not any(_TIER.search(str(name)) for name in rest):
        number = tier(entry, anime)
        if number:
            rest.insert(0, 'Tier %02d' % number)
    parts = (['rejected: %s' % ', '.join(low[:2])] if low else []) + rest[:4 - (1 if low else 0)]
    return 'TRaSH %d  ·  %s' % (value, '  ·  '.join(parts))
