# -*- coding: utf-8 -*-
"""The film's own speech, heard by Whisper with the viewer's own key (v5.10.607; the user,
23 Nov 2026: "AI Whisper, make it local, with the person's own key").

When the video carries no subtitle to time by and no English subtitle of the release was found,
AutoSync used to fall back to the sound's energy (soundref: where someone speaks, not what is
said). With a Groq key (free tier) or an OpenAI key set up in Dex Hub's AI services, it now asks
Whisper first: short pieces of the film are cut on this device and sent with the viewer's own key;
Whisper answers with every spoken sentence and its exact time. Those start times are a timing
reference as good as an English subtitle of the same release, aligned the same way.

  * Only sound leaves the device, a few minutes of it, to the viewer's own Groq or OpenAI
    account; no link, no file name. Nothing passes through DexWorld.
  * Six pieces of 90 s spread over the film (the opening titles and the end credits left out):
    about 9 minutes of a film's 120. Groq's free tier allows several hours a day; a daily cap
    here keeps it far below.
  * Decoded on this device to 16 kHz mono, the centre channel of a 5.1 mix (where the dialogue
    sits), by the same decoder soundref.py uses; a link is read with ranged requests like a
    player, a file on SMB or NFS through Kodi's own file access.
"""
import json
import os
import subprocess
import threading
import time
import uuid

from . import host

RATE = 16000
PIECES = 6
SHORT_PIECES = 4
PIECE_S = 90
DAILY_CAP_S = 4 * 3600              # far below Groq's free day
MIN_LINES = 30
SERVICES = (
    # (Dex Hub AI service id, its Whisper address, the model that can translate to English)
    ('groq', 'https://api.groq.com/openai/v1/audio/translations', 'whisper-large-v3'),
    ('openai', 'https://api.openai.com/v1/audio/translations', 'whisper-1'),
)


class WhisperError(Exception):
    def __init__(self, reason, detail=''):
        Exception.__init__(self, reason)
        self.reason, self.detail = reason, detail


def _log(text):
    import xbmc
    xbmc.log(host.log_prefix('autosync') + 'whisper: ' + text, xbmc.LOGINFO)


# ------------------------------------------------------------ the viewer's key
def _ai_file():
    import xbmcvfs
    return xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/ai.json')


def account():
    """(service id, key, address, model) of the first Whisper service set up, or None.
    Dex Hub's AI services (ai.json); DexSubtitles reads the same file (both add-ons are ours,
    the key stays on this device)."""
    try:
        with open(_ai_file(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return None
    services = data.get('services') if data.get('v') == 2 else (
        {data.get('provider'): {'key': data.get('key')}} if data.get('provider') else {})
    for pid, url, model in SERVICES:
        key = str(((services or {}).get(pid) or {}).get('key') or '').strip()
        if key:
            return pid, key, url, model
    return None


def available():
    return bool(account())


# ------------------------------------------------------------ the daily cap
def _usage_file():
    import xbmcvfs
    return os.path.join(xbmcvfs.translatePath('special://profile/addon_data/%s' % host.ADDON_ID), 'whisper_usage.json')


def _used_today():
    try:
        with open(_usage_file(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return float(data.get('seconds') or 0) if data.get('day') == time.strftime('%Y-%m-%d') else 0.0
    except Exception:
        return 0.0


def _spend(seconds):
    try:
        path = _usage_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'day': time.strftime('%Y-%m-%d'), 'seconds': _used_today() + seconds}, fh)
    except Exception:
        pass


# ------------------------------------------------------------ sound and Whisper
def _wav(pcm):
    import struct
    n = len(pcm)
    return (b'RIFF' + struct.pack('<I', 36 + n) + b'WAVEfmt ' +
            struct.pack('<IHHIIHH', 16, 1, 1, RATE, RATE * 2, 2, 16) + b'data' + struct.pack('<I', n) + pcm)


def _pcm(tool, src, extra, start_s, length_s, layout, abort):
    from . import soundref
    pan = {'surround': ['-af', 'pan=mono|c0=FC'], 'stereo': ['-ac', '1'], 'mono': []}.get(layout, ['-ac', '1'])
    cmd = [tool, '-nostdin', '-hide_banner', '-loglevel', 'error'] + extra + [
        '-ss', '%.2f' % start_s, '-i', src, '-t', '%d' % length_s, '-vn', '-sn', '-dn'] + pan + [
        '-ar', str(RATE), '-f', 's16le', '-']
    if abort and abort():
        raise WhisperError('cancelled')
    out, err = soundref._run(tool, cmd)
    if len(out or b'') < RATE * 2 * 20:          # under 20 s of sound
        raise WhisperError('unread', (err or b'')[-160:].decode('utf-8', 'replace'))
    return out


def _post(wav, account_row, timeout=120):
    import urllib.request
    import urllib.error
    _pid, key, url, model = account_row
    boundary = uuid.uuid4().hex
    parts = []
    for name, val in (('model', model), ('response_format', 'verbose_json'), ('temperature', '0')):
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, val)).encode())
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                  'Content-Type: audio/wav\r\n\r\n' % boundary).encode() + wav + b'\r\n')
    body = b''.join(parts) + ('--%s--\r\n' % boundary).encode()
    request = urllib.request.Request(url, data=body, headers={
        'Authorization': 'Bearer ' + key, 'User-Agent': host.AGENT,
        'Content-Type': 'multipart/form-data; boundary=' + boundary})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode('utf-8', 'replace') or '{}')
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise WhisperError('key', 'HTTP %d' % e.code)
        if e.code == 429:
            raise WhisperError('limit', 'HTTP 429')
        raise WhisperError('unread', 'HTTP %d' % e.code)


FRAME_MS = 50


def spans_of(answer, offset_ms=0):
    """(start, end) in ms of every sentence Whisper heard in one piece (offset_ms: the piece's place)."""
    out = []
    for seg in (answer or {}).get('segments') or []:
        try:
            text = str(seg.get('text') or '').strip()
            if not text or float(seg.get('no_speech_prob') or 0) > 0.6:
                continue
            a, b = float(seg['start']) * 1000, float(seg['end']) * 1000
            if b > a:
                out.append((int(offset_ms + a), int(offset_ms + b)))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def starts_of(answer, offset_ms):
    """The start (ms, on the film's clock) of every sentence Whisper heard in one piece."""
    return [a for a, _b in spans_of(answer, offset_ms)]


def _mask(spans, length_s):
    """A piece's speech as bits of 50 ms frames, as soundref gives it (speechalign slides over these)."""
    frames = int(length_s * 1000 // FRAME_MS)
    bits = 0
    for a, b in spans:
        x, y = max(0, int(a // FRAME_MS)), min(frames, int(-(-b // FRAME_MS)))
        if y > x:
            bits |= ((1 << (y - x)) - 1) << x
    return frames, bits


def reference(media, duration_s=0.0, abort=None):
    """{'ok': True, 'starts': [ms...], 'via': 'whisper', 'service', 'pieces', 'seconds'} or raises
    WhisperError. media: as soundref.reference takes it."""
    from . import soundref
    row = account()
    if not row:
        raise WhisperError('no_key')
    tool = soundref.tool()
    if not tool:
        raise WhisperError('no_tool')
    t0 = time.time()
    server = None
    try:
        extra = []
        if media['kind'] == 'http':
            src = media['path']
            extra = soundref._http_args(media.get('headers'))
        elif media['kind'] == 'file':
            src = media['path']
        else:
            server = soundref._VfsServer(media['path'])
            src = server.url
        length_probe, layout = soundref._probe(tool, src, extra)
        duration_s = duration_s if duration_s and duration_s >= 60 else length_probe
        if not duration_s or duration_s < 300:
            raise WhisperError('short')
        count = PIECES if duration_s >= 1500 else SHORT_PIECES
        length = min(PIECE_S, int(duration_s * 0.1))
        if _used_today() + count * length > DAILY_CAP_S:
            raise WhisperError('cap')
        lo, hi = duration_s * 0.05, duration_s * 0.86 - length
        starts = [lo + (hi - lo) * i / float(max(1, count - 1)) for i in range(count)]
        found, errors, heard = [None] * count, [], [0]
        spans = [None] * count

        def one(i):
            try:
                pcm = _pcm(tool, src, extra, starts[i], length, layout, abort)
                heard[0] += len(pcm) / float(RATE * 2)
                answer = _post(_wav(pcm), row)
                spans[i] = spans_of(answer)                 # on the piece's own clock
                found[i] = [int(starts[i] * 1000) + a for a, _b in spans[i]]
            except WhisperError as e:
                errors.append(e)
            except Exception as e:
                errors.append(WhisperError('unread', str(e)[:120]))

        for k in range(0, count, 2):                   # two at a time, like soundref
            batch = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(k, min(count, k + 2))]
            for w in batch:
                w.start()
            for w in batch:
                w.join()
            if abort and abort():
                raise WhisperError('cancelled')
            if any(e.reason in ('key', 'limit') for e in errors):
                break
        _spend(heard[0])
        lines = sorted(s for piece in found if piece for s in piece)
        if len(lines) < MIN_LINES:
            bad = [e for e in errors if e.reason in ('key', 'limit')]
            raise bad[0] if bad else (errors[0] if errors else WhisperError('few', '%d lines' % len(lines)))
        _log('%s heard %d lines in %d pieces (%.0f s of sound, %.1f s)' % (
            row[0], len(lines), sum(1 for p in found if p), heard[0], time.time() - t0))
        # v5.10.608: the pieces as speech masks, like soundref: Whisper hears ~9 minutes of a film,
        # so the subtitle is slid over those pieces only (speechalign), never matched line by line
        # against the whole film (most lines would find nothing and the match rate would fail)
        pieces, on, total = [], 0, 0
        for i in range(count):
            if spans[i] is None:
                continue
            frames, bits = _mask(spans[i], length)
            pieces.append((int(round(starts[i] * 1000 / FRAME_MS)), frames, bits))
            on += bin(bits).count('1')
            total += frames
        return {'ok': True, 'starts': lines, 'via': 'whisper', 'service': row[0], 'frame_ms': FRAME_MS,
                'pieces': pieces, 'speech': (on / float(total)) if total else 0.0,
                'seconds': time.time() - t0, 'duration_s': duration_s, 'layout': layout}
    finally:
        if server is not None:
            server.close()
