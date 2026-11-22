# -*- coding: utf-8 -*-
"""Shared subtitle sync with DexWorld (SYNC_SHARE_V1, Dex Hub 5.10.601, DexSubtitles 5.9.2).

A subtitle timed once, on any device, is timed for everyone who plays the same release
(specs/dexworld-sync-share.md). Open to every device: no subscription code is needed (a code
only raises the server's trust in what this device reports).

What leaves the device: the release's keys (the OpenSubtitles hash of the file, a hash of its
embedded subtitle's start times, its release-like file name, its size, the title's ids), the
subtitle's keys (a hash of its text, a hash of its first start times) and the fix (offset, speed,
parts, per-line). Never a link, a path, an account or the subtitle's text.

Everything fails quietly: with no server (or no answer within a few seconds) AutoSync works as
before, and the server is not asked again for a while.
"""
import hashlib
import io
import json
import os
import re
import struct
import threading
import time
import uuid
import zlib
import base64

import xbmc
import xbmcgui
import xbmcvfs

from . import host

BASE = 'https://dexworld.cc/subtitles/sync/v1'
TIMEOUT = 4.0
_DOWN = {'until': 0.0}          # the server did not answer: leave it alone for a while
_LOCK = threading.Lock()
_STATE = {}
_OSH_CHUNK = 65536


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log(host.log_prefix('sync share') + msg, level)


def _state_path():
    folder = xbmcvfs.translatePath(host.addon().getAddonInfo('profile'))
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    return os.path.join(folder, 'syncshare.json')


def _state():
    with _LOCK:
        if not _STATE:
            try:
                with io.open(_state_path(), 'r', encoding='utf-8') as f:
                    _STATE.update(json.load(f))
            except Exception:
                pass
            if not _STATE.get('device'):
                _STATE['device'] = str(uuid.uuid4())
                _save()
        return _STATE


def _save():
    try:
        tmp = _state_path() + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_STATE, f)
        os.replace(tmp, _state_path())
    except Exception:
        pass


def enabled():
    try:
        return host.addon().getSetting('sync_share') != 'false'
    except Exception:
        return True


def _code():
    """The DexWorld subscription code when this device has one (raises trust; never required):
    DexSubtitles' own, else Dex Hub's (its AI services keep it)."""
    try:
        import xbmcaddon
        value = (xbmcaddon.Addon('service.subtitles.dexworld').getSetting('api_key') or '').strip()
        if value and value != 'TV-BOX':
            return value
    except Exception:
        pass
    try:
        path = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/ai.json')
        with io.open(path, 'r', encoding='utf-8') as f:
            value = (((json.load(f).get('services') or {}).get('dexworld') or {}).get('key') or '').strip()
        return value if value != 'TV-BOX' else ''
    except Exception:
        return ''


# ------------------------------------------------------------------ the calls
def _post(path, body, auth=True):
    if time.time() < _DOWN['until']:
        return None
    from .autosync import _SESSION
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if auth:
        st = _state()
        if not st.get('token'):
            got = _post('/hello', {'device': st['device']}, auth=False)
            if not got or not got.get('token'):
                return None
            with _LOCK:
                _STATE['token'] = got['token']
                _save()
        headers['X-Dex-Device'] = '%s.%s' % (st['device'], st['token'])
        code = _code()
        if code:
            headers['X-Dex-Code'] = code
    try:
        r = _SESSION.post(BASE + path, data=json.dumps(body), headers=headers, timeout=TIMEOUT)
    except Exception as e:
        _DOWN['until'] = time.time() + 1800
        _log('%s not answered (%s); not asked again for 30 min' % (path, e.__class__.__name__))
        return None
    if r.status_code in (404, 501, 502, 503, 504):
        _DOWN['until'] = time.time() + (6 * 3600 if r.status_code in (404, 501) else 900)
        _log('%s: HTTP %d; not asked again for a while' % (path, r.status_code))
        return None
    if r.status_code in (401, 403) and auth:
        with _LOCK:
            _STATE.pop('token', None)       # a new hello next time
            _save()
        return None
    if r.status_code != 200:
        _log('%s: HTTP %d' % (path, r.status_code))
        return None
    try:
        return r.json()
    except Exception:
        return None


# ------------------------------------------------------------------ the keys
def os_hash(src):
    """The OpenSubtitles hash of a dexcues source (file size + 64-bit sums of its first and last
    64 KB): two reads, the same on every device for the same file."""
    try:
        head = src.read(0, _OSH_CHUNK, partial_ok=False)
        size = getattr(src, 'size', None)
        if not size or size < _OSH_CHUNK * 2 or len(head) < _OSH_CHUNK:
            return '', size
        tail = src.read(size - _OSH_CHUNK, _OSH_CHUNK, partial_ok=False)
        if len(tail) < _OSH_CHUNK:
            return '', size
        h = size
        for buf in (head, tail):
            for (q,) in struct.iter_unpack('<Q', buf):
                h = (h + q) & 0xFFFFFFFFFFFFFFFF
        return '%016x' % h, size
    except Exception:
        return '', None


def cues_key(ref_starts):
    """A hash of the embedded subtitle's first 200 start times: the same release, the same key."""
    starts = sorted(int(x) for x in (ref_starts or []))[:200]
    if len(starts) < 30:
        return ''
    return hashlib.sha1(','.join(str(x) for x in starts).encode('ascii')).hexdigest()[:40]


_REL_TAG = re.compile(r'(2160p|1080p|720p|480p|\b4k\b|\buhd\b|bluray|blu-ray|bdrip|brrip|remux|web-?dl|webrip|'
                      r'hdtv|dvdrip|x26[45]|h\.?26[45]|hevc|\bav1\b|s\d{1,2}e\d{1,3})', re.I)
_TOKENISH = re.compile(r'[A-Za-z0-9_-]{32,}')


def name_key(filename):
    """The release's file name, normalised, when it looks like a release (as RELEASE_HINT_V1)."""
    name = str(filename or '').split('|', 1)[0].split('?', 1)[0].split('#', 1)[0]
    name = name.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1].strip()[:200]
    if len(name) < 8 or not _REL_TAG.search(name) or _TOKENISH.search(name):
        return ''
    name = re.sub(r'\.(mkv|mp4|avi|m2ts|ts|mov|webm)$', '', name.lower())
    return re.sub(r'[\s_\-\[\]\(\)]+', '.', name).strip('.')


_TAGS = re.compile(r'<[^>]*>|\{[^}]*\}')
# the server's own rule (SYNC_SHARE_V1 reply, 22 Nov 2026): bidi marks and every space go
_BIDI = re.compile('[\u200e\u200f\u202a-\u202e\u2066-\u2069\u061c\ufeff]')
_SPACE = re.compile(r'\s+')


def _text_lines(raw):
    """The subtitle's text lines, decoded (any encoding): SRT/VTT lines, or ASS dialogue lines."""
    from .dexpick import _decode
    text = _decode(raw) if isinstance(raw, bytes) else str(raw)
    text = text.lstrip('\ufeff')
    if '[Events]' in text:
        out = []
        for l in text.splitlines():
            if l.startswith('Dialogue:'):
                out.extend(re.split(r'\\[Nn]', l.split(',', 9)[-1]))
        return out
    return [l for l in text.splitlines() if '-->' not in l and not l.lstrip().startswith('WEBVTT')]


def sub_keys(raw, name=''):
    """(sh, tf) exactly as DexWorld makes them: sh = sha1 of the text lines (tags, bidi marks and
    all whitespace removed; empty and number-only lines dropped; joined with \\n); tf = sha1 of the
    first 120 start times in ms, comma-joined, first 16 hex."""
    from . import dexalign
    keep = []
    for l in _text_lines(raw):
        l = _SPACE.sub('', _BIDI.sub('', _TAGS.sub('', l)))
        if l and not l.isdigit() and l != dexalign.INTRO:     # DexWorld's intro line (DEX_INTRO_CUE_V1)
            keep.append(l)
    body = '\n'.join(keep)
    sh = hashlib.sha1(body.encode('utf-8')).hexdigest() if len(keep) >= 20 else ''
    starts = [s for s in dexalign._file_starts(raw, name) if s is not None][:120]
    tf = hashlib.sha1(','.join(str(int(s)) for s in starts).encode('ascii')).hexdigest()[:16] if len(starts) >= 30 else ''
    return sh, tf


def is_arabic(raw):
    """DexWorld shares Arabic fixes only."""
    try:
        from .dexpick import looks_arabic
        return bool(looks_arabic(raw))
    except Exception:
        return False


def video_keys(src=None, ref_starts=None, filename='', ids=None):
    keys = {}
    if src is not None:
        oh, size = os_hash(src)
        if oh:
            keys['oh'] = oh
        if size:
            keys['size'] = int(size)
    cf = cues_key(ref_starts)
    if cf:
        keys['cf'] = cf
    fn = name_key(filename)
    if fn:
        keys['fn'] = fn
    for k, v in (ids or {}).items():
        if v not in (None, '', '0', 0) and k in ('imdb', 's', 'e'):
            keys[k] = str(v)
    return keys


# ------------------------------------------------------------------ the fix
def fix_of(res, method):
    """AutoSync's result as the shared fix."""
    fix = {'method': method, 'scale': round(float(res.get('scale', 1.0)), 7),
           'offset_ms': int(res.get('offset_ms', 0)), 'score': round(float(res.get('matched', 0.0)), 3),
           'lift': round(float(res.get('matched', 0.0)) / max(0.01, float(res.get('rival', 0.0)) or 0.01), 2),
           'app': 'dexhub' if host.IS_DEXHUB else 'dexsubtitles',
           'ver': host.addon().getAddonInfo('version')}
    if res.get('segments'):
        fix['mode'] = 'pieces'
        firsts = [s[0] for s in res['segments']] + [None]
        fix['pieces'] = [{'from_ms': int(a), 'to_ms': firsts[i + 1], 'offset_ms': int(off)}
                         for i, (a, off) in enumerate(res['segments'])]
    else:
        fix['mode'] = 'scale' if abs(fix['scale'] - 1.0) > 0.0005 else 'offset'
    if res.get('lines'):
        fix['mode'] = 'lines'
        deltas = [max(-32768, min(32767, int(round(x / 10.0)))) for x in res['lines']]
        fix['lines'] = base64.b64encode(zlib.compress(struct.pack('<%dh' % len(deltas), *deltas))).decode('ascii')
    return fix


def res_of(fix):
    """The shared fix as AutoSync's result (what _transform applies)."""
    res = {'scale': float(fix.get('scale') or 1.0), 'offset_ms': int(fix.get('offset_ms') or 0),
           'matched': float(fix.get('score') or 0.0), 'rival': 0.0, 'hits': 0, 'shared': True}
    if fix.get('pieces'):
        res['segments'] = [(int(p['from_ms']), int(p['offset_ms'])) for p in fix['pieces']]
    if fix.get('lines'):
        try:
            data = zlib.decompress(base64.b64decode(fix['lines']))
            res['lines'] = [x * 10 for x in struct.unpack('<%dh' % (len(data) // 2), data)]
        except Exception:
            pass
    return res


# ------------------------------------------------------------------ what AutoSync calls
def lookup(video, subs):
    """{'fixes': {(sh, tf): fix} the server trusts, 'release': its id, 'timeline': whether it keeps
    this release's reference times}; empty when nothing is known."""
    out = {'fixes': {}, 'release': '', 'timeline': False}
    if not enabled() or not video or not subs:
        return out
    want = [{'sh': sh, 'tf': tf} for sh, tf in subs if sh and tf][:12]
    if not want:
        return out
    got = _post('/lookup', {'video': video, 'subs': want}) or {}
    out['release'] = str(got.get('release') or '')
    out['timeline'] = bool(got.get('timeline'))
    for key, item in (got.get('fixes') or {}).items():
        if not isinstance(item, dict) or item.get('trust') not in ('consensus', 'strong'):
            continue
        sh, _, tf = key.partition(':')
        fix = item.get('fix') if isinstance(item.get('fix'), dict) else item
        out['fixes'][(sh, tf)] = fix
    if out['fixes']:
        _log('%d shared fix(es) for release %s' % (len(out['fixes']), out['release'] or '?'))
    elif got:
        _log('asked: nothing shared yet for this subtitle (release %s, timeline %s)' % (
            out['release'] or 'new', 'yes' if out['timeline'] else 'no'))
    return out


_SENT_TIMELINE = set()


def send_timeline(video, starts):
    """This release's reference times (the embedded subtitle's starts, never its text), once a run,
    for devices that cannot read the file themselves."""
    starts = sorted(int(x) for x in (starts or []))[:6000]
    key = video.get('oh') or video.get('cf') or ''
    if not enabled() or not key or key in _SENT_TIMELINE or len(starts) < 10:
        return
    _SENT_TIMELINE.add(key)
    threading.Thread(target=lambda: _post('/timeline', {'video': video, 'kind': 'cues', 'starts': starts}),
                     name='DexSyncShare', daemon=True).start()


def get_timeline(release):
    if not enabled() or not release or time.time() < _DOWN['until']:
        return []
    from .autosync import _SESSION
    st = _state()
    if not st.get('token'):
        got = _post('/hello', {'device': st['device']}, auth=False)
        if not got or not got.get('token'):
            return []
        with _LOCK:
            _STATE['token'] = got['token']
            _save()
    try:
        r = _SESSION.get('%s/timeline/%s' % (BASE, release), timeout=TIMEOUT,
                         headers={'X-Dex-Device': '%s.%s' % (st['device'], st.get('token', '')), 'Accept': 'application/json'})
        if r.status_code != 200:
            return []
        starts = [int(x) for x in (r.json().get('starts') or [])]
        if starts:
            _log('release %s: shared reference of %d lines' % (release, len(starts)))
        return starts
    except Exception:
        return []


def report(video, sh, tf, lang, fix, name=''):
    if not enabled() or not video or not sh or not tf:
        return
    body = {'video': video, 'sub': {'sh': sh, 'tf': tf, 'lang': lang or ''}, 'fix': fix}
    if name:
        body['sub']['name'] = os.path.basename(str(name))[:120]

    def go():
        got = _post('/report', body)
        if got:
            _log('reported (%s, %s): %s' % (fix.get('method'), fix.get('mode'), got.get('status')))
    threading.Thread(target=go, name='DexSyncShare', daemon=True).start()


def feedback(video, sh, tf, verdict):
    if enabled() and video and sh and tf and verdict in ('good', 'early', 'late', 'off'):
        threading.Thread(target=lambda: _post('/feedback', {'video': video, 'sub': {'sh': sh, 'tf': tf},
                                                             'verdict': verdict}),
                         name='DexSyncShare', daemon=True).start()
