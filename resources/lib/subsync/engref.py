# -*- coding: utf-8 -*-
"""An English subtitle of the same release as the timing reference (Dex Hub 5.10.601,
DexSubtitles 5.9.2).

When the video carries no subtitle to time by (an MP4, a WEB-DL without subtitles, a link whose
index cannot be read) and DexWorld keeps no reference for the release, an English subtitle made
for the very same release is the next best clock: its lines start where people speak, on this
release's own timing. OpenSubtitles' public Stremio add-on (the one Dex Hub already asks by
default, no key) lists English subtitles with their release name, group and frame rate.

Only a subtitle that is surely of this release is used:
  * its release name matches the playing file's closely (the same group and source), or
  * two English subtitles of different uploads agree with each other within a few hundred ms
    (two people timed the same release the same way).
Otherwise none is used, and AutoSync goes on to the film's sound as before.

What leaves the device: the title's ids (and, when the file can be read, its OpenSubtitles hash
and size, which the add-on uses to put the very same file's subtitles first). Never a link.
"""
import re
import time
from urllib.parse import quote

import xbmc

from . import dexalign, dexpick, host

BASE = 'https://opensubtitles-v3.strem.io'
MAX_DOWNLOADS = 3
SAME_RELEASE = 60.0        # name_score from which a subtitle counts as this release's (group + source)
AGREE_MS = 400
_MEMO = {}                 # media -> (until, result)
_TTL = 3600


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log(host.log_prefix('autosync') + 'english reference: ' + msg, level)


def _session():
    from .autosync import _SESSION
    return _SESSION


def _hearing_impaired(item):
    name = '%s %s' % (item.get('subtitleFileName') or '', item.get('movieReleaseName') or '')
    return bool(re.search(r'(?i)(^|[._\-\s\[(])(hi|sdh|cc)([._\-\s\])]|$)', name)) or str(item.get('hi') or '') == '1'


def _list(ids, oh='', size=None):
    imdb = str(ids.get('imdb') or '')
    if not imdb.startswith('tt'):
        return []
    s, e = str(ids.get('s') or ''), str(ids.get('e') or '')
    if s.isdigit() and e.isdigit() and int(e) > 0:
        kind, vid = 'series', '%s:%d:%d' % (imdb, int(s), int(e))
    else:
        kind, vid = 'movie', imdb
    extra = ''
    if oh and size:
        extra = '/videoHash=%s&videoSize=%d' % (quote(oh), int(size))
    url = '%s/subtitles/%s/%s%s.json' % (BASE, kind, quote(vid, safe=':'), extra)
    try:
        r = _session().get(url, timeout=12)
        if r.status_code != 200:
            _log('list: HTTP %d' % r.status_code)
            return []
        items = r.json().get('subtitles') or []
    except Exception as e:
        _log('list failed: %s' % e.__class__.__name__)
        return []
    return [x for x in items if isinstance(x, dict) and str(x.get('lang') or '').lower() in ('eng', 'en') and x.get('url')]


def _starts(url):
    try:
        r = _session().get(url, timeout=15)
        if r.status_code != 200 or not r.content or len(r.content) > 2 * 1024 * 1024:
            _log('download: HTTP %d, %d bytes' % (r.status_code, len(r.content or b'')))
            return []
        starts = dexalign.parse_starts(r.content)
        return starts if len(starts) >= 150 else []
    except Exception:
        return []


def _agree(a, b):
    """Two English subtitles timed the same release the same way?"""
    if not a or not b:
        return False
    res = dexalign.align(a, b, max_offset_ms=30000)
    return (abs(res.get('scale', 1.0) - 1.0) < 0.0005 and abs(res.get('offset_ms', 0)) <= AGREE_MS
            and res.get('matched', 0) >= 0.6)


def reference(media_url, ids, filename='', oh='', size=None, fps=0.0):
    """{'ok': True, 'starts': [...], 'name': release name, 'why': 'name'|'agree'} or {'ok': False, 'reason'}."""
    hit = _MEMO.get(media_url)
    if hit and time.time() < hit[0]:
        return hit[1]
    out = {'ok': False, 'reason': 'english_none'}
    items = _list(ids, oh, size)
    if items:
        video = dexpick.best_video_info([filename])
        has_hints = dexpick.has_release_hints(video)
        ranked = []
        for i, x in enumerate(items):
            name = x.get('movieReleaseName') or x.get('subtitleFileName') or ''
            score = dexpick.name_score(video, name) if has_hints else 0.0
            info = dexpick.release_info(name)
            if video.get('service') and info.get('service') and video['service'] != info['service']:
                # another streaming service's master (NF vs AMZN) may differ: not "this release" alone
                score = min(score, SAME_RELEASE - 1)
            if _hearing_impaired(x):
                score -= 8
            if fps and x.get('fpsMilli'):
                try:
                    if abs(float(x['fpsMilli']) / 1000.0 - fps) < 0.05:
                        score += 5
                except Exception:
                    pass
            # the add-on puts the very same file's subtitles first when it got the hash
            ranked.append((score - i * 0.01, name, x))
        ranked.sort(key=lambda t: -t[0])
        got = []
        for score, name, x in ranked[:MAX_DOWNLOADS]:
            starts = _starts(x['url'])
            if starts:
                got.append((score, name, starts))
        if got and got[0][0] >= SAME_RELEASE:
            out = {'ok': True, 'starts': got[0][2], 'name': got[0][1], 'why': 'name'}
        else:
            # two uploads agreeing could both be of another release (an HDTV pair for a WEB file):
            # with a known source, only candidates of the same family count
            if has_hints and video.get('src'):
                got = [g for g in got if g[0] >= 20]
            for i in range(len(got)):
                for j in range(i + 1, len(got)):
                    if got[i][1] != got[j][1] and _agree(got[i][2], got[j][2]):
                        out = {'ok': True, 'starts': got[i][2], 'name': got[i][1], 'why': 'agree'}
                        break
                if out['ok']:
                    break
        if not out['ok']:
            out['reason'] = 'english_unsure' if got else 'english_unread'
        _log('%d English listed, %d read -> %s' % (len(items), len(got),
                                                  ('%s (%s)' % (out['name'], out['why'])) if out['ok'] else out['reason']))
    _MEMO.clear()
    _MEMO[media_url] = (time.time() + _TTL, out)
    return out
