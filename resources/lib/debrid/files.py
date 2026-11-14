# -*- coding: utf-8 -*-
"""The right file of a torrent: the film, or the episode of a season pack.

Every video file gets points; the best one wins:
  the exact episode +800 (+250 when the file holds several episodes),
  the right season +500, the wrong season -800, the wrong episode -900,
  the index or file name the source named +600, the title's words +200,
  the year +100 (films), up to +50 for its size;
  samples, trailers, extras and openings/endings -500.
A series asked for an episode never gets a file of another episode: when no
file names the episode, only a torrent with a single video file is played.
"""
import re

VIDEO = ('.mkv', '.mp4', '.avi', '.m4v', '.ts', '.m2ts', '.mov', '.wmv', '.webm', '.mpg', '.mpeg', '.flv', '.iso')
_EXTRA = re.compile(r'(?<![a-z])(sample|trailer|teaser|featurettes?|extras?|bonus|behind[ ._-]the[ ._-]scenes|'
                    r'deleted[ ._-]scenes?|interviews?|making[ ._-]of|nc(?:op|ed)|creditless|opening|ending|'
                    r'promo|recap)(?![a-z])', re.I)
_DISC = re.compile(r'(^|/)(bdmv|video_ts|certificate)(/|$)', re.I)
_SXE = [
    re.compile(r'(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,4})(?:[ ._-]*(?:e|-)[ ._-]*(\d{1,4}))?(?![0-9])', re.I),
    re.compile(r'(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?:[ ._-]*(?:x|-)(\d{2,3}))?(?![0-9])', re.I),
    re.compile(r'season[ ._-]*(\d{1,2})[ ._-]*(?:episode|ep)[ ._-]*(\d{1,4})(?![0-9])', re.I),
]
_SEASON_ONLY = re.compile(r'(?<![a-z0-9])(?:s|season[ ._-]*)(\d{1,2})(?![0-9])', re.I)
_EP_ONLY = re.compile(r'(?:(?<![a-z0-9])(?:e|ep|episode)[ ._-]*(\d{1,4})(?![0-9]))|(?:[ ._]-[ ._](\d{2,4})(?![0-9p]))', re.I)
_WORD = re.compile(r'[a-z0-9]+')


def is_video(name):
    low = str(name or '').lower()
    return low.endswith(VIDEO) and not _DISC.search(low.replace('\\', '/'))


def episodes_in(name):
    """[(season, first episode, last episode)] named in one file's path."""
    low = str(name or '').replace('\\', '/')
    base = low.rsplit('/', 1)[-1]
    out = []
    for pattern in _SXE:
        for match in pattern.finditer(base):
            season, first = int(match.group(1)), int(match.group(2))
            last = int(match.group(3)) if match.group(3) else first
            if last < first or last - first > 30:
                last = first
            out.append((season, first, last))
        if out:
            return out
    # "Show/Season 2/Show - E05.mkv" or "Show S02/05 - Name.mkv"
    season = None
    for part in low.split('/')[:-1][::-1] + [base]:
        found = _SEASON_ONLY.search(part)
        if found:
            season = int(found.group(1))
            break
    found = _EP_ONLY.search(base)
    if found:
        number = int(found.group(1) or found.group(2))
        out.append((season, number, number))
    return out


def _words(text):
    return _WORD.findall(str(text or '').lower())


def score(item, season=None, episode=None, title='', year='', file_idx=None, filename=''):
    name = str(item.get('name') or '')
    low = name.lower()
    points = 0
    if _EXTRA.search(low.replace('\\', '/').rsplit('/', 1)[-1]) or '/extras/' in low or '/featurettes/' in low:
        points -= 500
    if file_idx not in (None, '') and str(item.get('index')) == str(file_idx):
        points += 600
    if filename and low.replace('\\', '/').rsplit('/', 1)[-1] == str(filename).lower():
        points += 600
    words = [w for w in _words(title) if len(w) > 1]
    if words and all(w in _words(low) for w in words):
        points += 200
    if season is not None and episode is not None:
        found = episodes_in(name)
        if not found:
            points -= 300
        else:
            best = -900
            for s, first, last in found:
                if s is not None and s != int(season):
                    best = max(best, -800)
                    continue
                if first <= int(episode) <= last:
                    best = max(best, (800 if first == last else 250) + (500 if s is not None else 0))
                else:
                    best = max(best, -900)
            points += best
    elif year and str(year) in low:
        points += 100
    size = float(item.get('size') or 0)
    points += min(50, int(size / 1e9 * 5))
    return points


def pick(files, season=None, episode=None, title='', year='', file_idx=None, filename=''):
    """The chosen file (one of `files`, each {'name', 'size', 'index'?}), or None."""
    videos = [f for f in files or [] if is_video(f.get('name'))]
    if not videos:
        return None
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    season = int(season) if episodic else None
    episode = int(episode) if episodic else None
    ranked = sorted(videos, key=lambda f: (score(f, season, episode, title, year, file_idx, filename),
                                          float(f.get('size') or 0)), reverse=True)
    best = ranked[0]
    if episodic:
        named = any(first <= episode <= last and (s is None or s == season)
                    for s, first, last in episodes_in(best.get('name')))
        if not named:
            hinted = (file_idx not in (None, '') and str(best.get('index')) == str(file_idx)) or (
                filename and str(best.get('name') or '').lower().endswith(str(filename).lower()))
            real = [f for f in videos if not _EXTRA.search(str(f.get('name') or '').lower())]
            if not hinted and len(real) != 1:
                return None
    return best
