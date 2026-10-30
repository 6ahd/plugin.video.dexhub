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
"""
import json
import os
import re

_TABLE = {}
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
_BRDISK = re.compile(r'\b(?:bdmv|complete[ ._-]?(?:uhd[ ._-]?)?blu[ ._-]?ray|iso)\b', re.I)


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
                        try:
                            value = re.compile(value, re.I)
                        except re.error:
                            continue
                    specs.append((kind, negate, required, value))
                if specs:
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
    first = text.split('\n', 1)[0].strip()
    leading = _LEADING_GROUP.match(first)
    if leading and not source:
        source = 3 if episode else 7      # "[Group] Title - 05": a fansub/web release, as Sonarr reads it
    found = leading or _GROUP.search(first)
    return source, modifier, resolution, (found.group(1).strip() if found else '')


def _matches(specs, text, facts):
    source, modifier, resolution, group = facts
    groups = {}
    for kind, negate, required, value in specs:
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
        groups.setdefault(kind, []).append((required, hit != negate))
    return all(not any(req and not ok for req, ok in results) and any(ok for _req, ok in results)
               for results in groups.values())


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
        source, modifier, _res, _group = release_facts(release or extra)
        cached = cached if isinstance(cached, dict) else {}
        cached[key] = [value, names, bool(_DISC.search(release or extra))]
        entry['_trash'] = cached
    value, _names, disc = cached[key]
    if prefer == 'web' and disc:
        value -= _WEB_FIRST_PENALTY
    return value


def reason(entry, anime=False):
    """'TRaSH 8450 · Remux Tier 01 · TrueHD ATMOS · DV Boost' for the row."""
    entry_score(entry, anime=anime)
    value, names, _disc = entry['_trash']['anime' if anime else 'default']
    if not names:
        return ''
    return 'TRaSH %d  ·  %s' % (value, '  ·  '.join(names[:4]))
