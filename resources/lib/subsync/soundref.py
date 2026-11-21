# -*- coding: utf-8 -*-
"""The film's own sound as a timing reference (Dex Hub 5.10.597).

When the video carries no subtitle to time by (an MP4, a WEB-DL without
subtitles, a stream whose index cannot be read), AutoSync listens to the film
instead: where people speak is where a subtitle line should show.

Only a few minutes are read: two-minute pieces from six places of the film
(the opening titles and the end credits left out), each decoded to 8 kHz mono,
band-limited to the voice (200-3400 Hz), and measured every 50 ms by the
decoder itself. A link is read with ranged requests like any player (a remux
over the internet costs a few tens of megabytes, not the film); a file on SMB
or NFS goes through a tiny local server reading it with Kodi's own file
access, so every place Kodi plays from works.

Speech is told from the rest by the film's own levels (its quiet floor and its
loud parts), not a fixed threshold, then cleaned: short gaps inside a sentence
are filled, clicks shorter than 150 ms dropped. The result is a set of bit
masks, one per piece, that speechalign.py slides the subtitle over.

It needs a sound decoder on the device (the tools add-on of CoreELEC and
LibreELEC, or one on the system on Windows, macOS and Linux). Without one this
step is simply skipped and the subtitle stays as it is.
"""
import os
import re
import shutil
import subprocess
import threading
import time

FRAME_MS = 50
RATE = 8000
SAMPLES = RATE * FRAME_MS // 1000          # 400 samples a frame
PIECE_S = 120
PIECES = 6
SHORT_PIECES = 4                            # under 25 minutes
RUN_TIMEOUT = 75
# Dialogue sits in the centre: the centre channel of a 5.1 mix, what both sides
# share in stereo. Two levels a frame: the centre (or the middle) and the rest
# (the front sides, or what differs between left and right), both in the voice band.
_PAN = {'surround': 'pan=stereo|c0=FC|c1=0.5*FL+0.5*FR',
        'stereo': 'pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0-0.5*c1',
        'mono': 'pan=mono|c0=c0'}
_MEASURE = ('aresample=%d,highpass=f=200,lowpass=f=3400,asetnsamples=n=%d:p=0,'
            'astats=metadata=1:reset=1%%s,ametadata=mode=print:file=-' % (RATE, SAMPLES))
_ONLY_RMS = ':measure_perchannel=RMS_level:measure_overall=none'

_TOOL = [None]
_TOOL_LOCK = threading.Lock()


class SoundError(Exception):
    def __init__(self, reason, detail=''):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail


def _log(text):
    try:
        import xbmc
        from . import host
        xbmc.log(host.log_prefix('autosync') + 'sound: ' + text, xbmc.LOGINFO)
    except Exception:
        pass


# ------------------------------------------------------------------ the decoder
def _candidates():
    out = []
    try:
        import xbmcvfs
        home = xbmcvfs.translatePath('special://home/addons/')
        out.append(os.path.join(home, 'tools.ffmpeg-tools', 'bin', 'ffmpeg'))
    except Exception:
        pass
    for name in ('ffmpeg', 'ffmpeg.exe'):
        found = shutil.which(name)
        if found:
            out.append(found)
    out += ['/storage/.kodi/addons/tools.ffmpeg-tools/bin/ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg',
            '/opt/homebrew/bin/ffmpeg', '/opt/local/bin/ffmpeg']
    seen, unique = set(), []
    for path in out:
        if path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _env(path):
    env = dict(os.environ)
    lib = os.path.join(os.path.dirname(os.path.dirname(path)), 'lib.private')
    if os.path.isdir(lib):
        env['LD_LIBRARY_PATH'] = lib + (os.pathsep + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    return env


def tool():
    """The decoder's path, '' when the device has none that runs (checked once)."""
    with _TOOL_LOCK:
        if _TOOL[0] is not None:
            return _TOOL[0]
        found = ''
        for path in _candidates():
            if not (os.path.isfile(path) and os.access(path, os.X_OK)):
                continue
            try:
                done = subprocess.run([path, '-hide_banner', '-version'], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, timeout=10, env=_env(path))
            except Exception:
                continue
            if done.returncode == 0 and b'ffmpeg' in done.stdout[:200].lower():
                found = path
                break
        _TOOL[0] = found
        _log('decoder %s' % (found or 'none on this device'))
        return found


# ---------------------------------------------------------- SMB / NFS through Kodi
class _VfsServer(object):
    """A one-file HTTP server on 127.0.0.1 that answers ranged reads with Kodi's
    own file access, so the decoder reads smb://, nfs:// and the like."""

    def __init__(self, path):
        import http.server
        import socketserver
        import xbmcvfs
        handle = xbmcvfs.File(path)
        try:
            size = int(handle.size())
        finally:
            handle.close()
        if size <= 0:
            raise SoundError('sound_unread', 'size')
        target = path

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self._answer(False)

            def do_GET(self):
                self._answer(True)

            def _answer(self, body):
                start, end = 0, size - 1
                rng = self.headers.get('Range') or ''
                m = re.match(r'bytes=(\d*)-(\d*)', rng)
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        start = int(m.group(1))
                        end = int(m.group(2)) if m.group(2) else size - 1
                    else:
                        start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header('Content-Range', 'bytes */%d' % size)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send_response(206 if m else 200)
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(end - start + 1))
                if m:
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
                self.end_headers()
                if not body:
                    return
                f = xbmcvfs.File(target)
                try:
                    f.seek(start, 0)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.readBytes(min(left, 1 << 20))
                        if not chunk:
                            break
                        self.wfile.write(bytes(chunk))
                        left -= len(chunk)
                except Exception:
                    pass
                finally:
                    f.close()

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d/media' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, name='DexHubSoundVfs', daemon=True).start()

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass


# ----------------------------------------------------------------- reading sound
def _http_args(headers):
    args = []
    agent = ''
    lines = []
    for key, value in (headers or {}).items():
        if key.lower() == 'user-agent':
            agent = value
        else:
            lines.append('%s: %s\r\n' % (key, value))
    if agent:
        args += ['-user_agent', agent]
    if lines:
        args += ['-headers', ''.join(lines)]
    return args + ['-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '4', '-rw_timeout', '20000000']


def _probe(path, src, extra):
    """(length in seconds, layout) from the decoder's own reading of the header."""
    try:
        done = subprocess.run([path, '-nostdin', '-hide_banner'] + extra + ['-i', src], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=40, env=_env(path))
    except subprocess.TimeoutExpired:
        raise SoundError('sound_unread', 'timeout')
    err = done.stderr or b''
    m = re.search(rb'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', err)
    # v5.10.598: a stream whose header gives no length (Duration: N/A, some Usenet and CDN
    # links) is still read when the player knows the length; reference() decides
    length = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    if not m and not re.search(rb'Stream #0:\d+[^\n]*Audio:', err):
        raise SoundError('sound_unread', 'no duration')
    audio = re.search(rb'Stream #0:\d+[^\n]*Audio:[^\n]*', err)
    line = audio.group(0).lower() if audio else b''
    if re.search(rb'5\.1|7\.1|6 channels|8 channels|6\.1', line):
        layout = 'surround'
    elif b'mono' in line or b'1 channels' in line:
        layout = 'mono'
    else:
        layout = 'stereo'
    return length, layout


def _run(path, cmd):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(path))
    timer = threading.Timer(RUN_TIMEOUT, proc.kill)
    timer.start()
    try:
        return proc.communicate()
    finally:
        timer.cancel()


def _db(raw):
    try:
        return max(-120.0, float(raw))
    except ValueError:
        return -120.0


def _levels(path, src, extra, start_s, length_s, layout, abort):
    """[(centre dB, rest dB or None)] every 50 ms of one piece, in the voice band."""
    for only in (_ONLY_RMS, ''):                 # an older decoder has no measure_* options
        cmd = ([path, '-nostdin', '-hide_banner', '-loglevel', 'error'] + extra +
               ['-ss', '%.3f' % start_s, '-t', '%d' % length_s, '-i', src, '-vn', '-sn', '-dn', '-map', '0:a:0',
                '-af', _PAN[layout] + ',' + _MEASURE % only, '-f', 'null', '-'])
        out, err = _run(path, cmd)
        if abort and abort():
            raise SoundError('sound_cancelled')
        frames, centre = [], None
        for line in (out or b'').splitlines():
            if line.startswith(b'lavfi.astats.1.RMS_level='):
                centre = _db(line.split(b'=', 1)[1].strip())
                if layout == 'mono':
                    frames.append((centre, None))
            elif line.startswith(b'lavfi.astats.2.RMS_level=') and centre is not None:
                frames.append((centre, _db(line.split(b'=', 1)[1].strip())))
                centre = None
        if len(frames) >= length_s * 1000 // FRAME_MS // 2:
            return frames
        if only and b'measure_' not in (err or b''):
            break
    raise SoundError('sound_unread', (err or b'')[-160:].decode('utf-8', 'replace').strip())


def _percentile(sorted_values, q):
    if not sorted_values:
        return -120.0
    return sorted_values[min(len(sorted_values) - 1, int(q * (len(sorted_values) - 1)))]


def _speech(levels_by_piece):
    """Bit masks of speech, one per piece, from the film's own levels: the centre loud
    enough (its floor and peaks), and standing out of the rest (music and effects spread
    to the sides; a voice is centred)."""
    every = sorted(c for piece in levels_by_piece for c, _r in piece if c > -110)
    if len(every) < 200:
        raise SoundError('sound_quiet')
    floor = _percentile(every, 0.15)
    loud = _percentile(every, 0.95)
    if loud - floor < 9:
        raise SoundError('sound_quiet', 'flat %.1f dB' % (loud - floor))
    threshold = max(floor + 0.35 * (loud - floor), floor + 6.0)
    margins = sorted(c - r for piece in levels_by_piece for c, r in piece if r is not None and c >= threshold)
    # how far a voice stands out of the rest in THIS mix: above the typical loud frame
    stand = max(2.0, _percentile(margins, 0.45)) if len(margins) > 100 else None
    masks, voiced, total = [], 0, 0
    for piece in levels_by_piece:
        on = [c >= threshold and (stand is None or r is None or c - r >= stand) for c, r in piece]
        n = len(on)
        # fill gaps under 300 ms inside speech, then drop blips under 150 ms
        i = 0
        while i < n:
            if not on[i]:
                j = i
                while j < n and not on[j]:
                    j += 1
                if 0 < i and j < n and j - i <= 6:
                    for k in range(i, j):
                        on[k] = True
                i = j
            else:
                i += 1
        i = 0
        while i < n:
            if on[i]:
                j = i
                while j < n and on[j]:
                    j += 1
                if j - i < 3:
                    for k in range(i, j):
                        on[k] = False
                i = j
            else:
                i += 1
        bits = 0
        for k in range(n - 1, -1, -1):
            bits = (bits << 1) | (1 if on[k] else 0)
        masks.append((n, bits))
        voiced += sum(on)
        total += n
    return masks, voiced / float(total or 1), threshold


def reference(media, duration_s=0.0, abort=None):
    """{'ok': True, 'via': 'sound', 'frame_ms': 50, 'pieces': [(start_frame, frames, bits)],
    'speech': share of frames with speech, 'seconds': time taken} or raises SoundError.

    media: {'kind': 'http' | 'file' | 'vfs', 'path': ..., 'headers': {...}}"""
    path = tool()
    if not path:
        raise SoundError('sound_no_tool')
    t0 = time.time()
    server = None
    try:
        extra = []
        if media['kind'] == 'http':
            src = media['path']
            extra = _http_args(media.get('headers'))
        elif media['kind'] == 'file':
            src = media['path']
        else:
            server = _VfsServer(media['path'])
            src = server.url
        length_probe, layout = _probe(path, src, extra)
        if not duration_s or duration_s < 60:
            duration_s = length_probe
        if not duration_s:
            raise SoundError('sound_unread', 'no duration')
        if duration_s < 300:
            raise SoundError('sound_short')
        count = PIECES if duration_s >= 1500 else SHORT_PIECES
        length = min(PIECE_S, int(duration_s * (0.12 if count == PIECES else 0.2)))
        usable_from, usable_to = duration_s * 0.05, duration_s * 0.86 - length
        starts = [usable_from + (usable_to - usable_from) * i / float(max(1, count - 1)) for i in range(count)]
        levels = [None] * count
        errors = []

        def one(i):
            try:
                try:
                    levels[i] = _levels(path, src, extra, starts[i], length, layout, abort)
                except SoundError as e:
                    if e.reason != 'sound_unread':
                        raise
                    levels[i] = _levels(path, src, extra, starts[i], length, layout, abort)   # once more
            except SoundError as e:
                errors.append(e)
            except Exception as e:
                errors.append(SoundError('sound_unread', str(e)[:120]))

        workers = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(count)]
        for k in range(0, count, 2):                   # two at a time
            batch = workers[k:k + 2]
            for w in batch:
                w.start()
            for w in batch:
                w.join()
            if abort and abort():
                raise SoundError('sound_cancelled')
        got = [(starts[i], levels[i]) for i in range(count) if levels[i]]
        if len(got) < max(2, count // 2):
            raise errors[0] if errors else SoundError('sound_unread')
        masks, share, threshold = _speech([lv for _s, lv in got])
        pieces = [(int(round(s * 1000 / FRAME_MS)), n, bits) for (s, _lv), (n, bits) in zip(got, masks)]
        return {'ok': True, 'via': 'sound', 'frame_ms': FRAME_MS, 'pieces': pieces, 'speech': share,
                'threshold': threshold, 'seconds': time.time() - t0, 'duration_s': duration_s, 'layout': layout}
    finally:
        if server is not None:
            server.close()
