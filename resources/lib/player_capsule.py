# -*- coding: utf-8 -*-
"""v5.10.417: the playback capsule (skin.dexhub 3.28.134).

A frosted pill at the top of the picture with Dex Hub's mark and what is
playing: the video (4K DV, and under it HEVC 10bit · 24.5 Mbps), the audio
(Atmos, and under it TrueHD 7.1) and, if wanted, the title's age rating
(PG-13, and under it the star rating). It comes once the picture runs, stays
a few seconds and fades, and comes again only for a new playback or another
source; never for a pause or the player's bar.

The skin draws it from Home properties (dhs.cap.*). Kodi cannot size a
background to text that changes, so each column's width is measured here
(the skin's fonts, widths per character below) and published as a class
(dhs.cap.<column>.w, 20 px steps) that the skin has a column for.

Settings (ui_preferences): player_capsule (on), player_capsule_rating,
player_capsule_place (top_left, top_right, top_center), player_capsule_time.
"""
import re
import threading
import time
from urllib.parse import unquote, urlsplit

import xbmc
import xbmcgui

PREFIX = 'dhs.cap.'
_SHOWN = {'key': '', 'token': 0}
_LOCK = threading.Lock()
STEP, MIN_W, MAX_W = 20, 100, 420

# advance widths (px) of the skin's InterArabic Bold at 30 px (font30_title)
# and Regular at 22 px (dh_small), the capsule's two lines, measured from the
# fonts; a character not listed counts as an 'M'
_CHARS = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~·★'
_BOLD30 = dict(zip(_CHARS, (7, 10, 17, 19, 20, 30, 20, 10, 11, 11, 17, 20, 10, 14, 10, 12, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 10, 10, 20, 20, 20, 17, 30, 22, 20, 22, 22, 18, 18, 23, 22, 8, 18, 22, 17, 28, 23, 23, 19, 23, 20, 20, 20, 22, 22, 31, 22, 22, 20, 11, 12, 11, 15, 14, 11, 17, 19, 18, 19, 18, 12, 19, 19, 8, 8, 17, 8, 27, 19, 18, 19, 19, 12, 17, 11, 19, 18, 26, 17, 18, 17, 14, 11, 14, 20, 10, 31)))
_REG22 = dict(zip(_CHARS, (6, 6, 10, 14, 14, 22, 14, 7, 8, 8, 11, 15, 6, 10, 6, 8, 14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 6, 7, 15, 15, 15, 11, 21, 15, 14, 16, 16, 13, 13, 16, 16, 6, 13, 15, 12, 20, 17, 17, 14, 17, 14, 14, 14, 16, 15, 22, 15, 15, 14, 8, 8, 8, 10, 10, 7, 12, 13, 13, 13, 13, 8, 14, 13, 5, 5, 12, 5, 19, 13, 13, 13, 13, 8, 12, 7, 13, 12, 18, 12, 12, 12, 9, 7, 9, 15, 6, 23)))


def _measure(text, table):
    return sum(table.get(ch, table['M']) for ch in str(text or ''))


def width_class(*lines):
    """The column's width class: the widest of its lines (bold, small), with room."""
    widest = max([_measure(lines[0], _BOLD30)] + [_measure(t, _REG22) for t in lines[1:]] + [0])
    w = int((widest * 1.08 + STEP - 1) // STEP * STEP)
    return str(max(MIN_W, min(MAX_W, w)))


# ------------------------------------------------------------------ what plays
_CODECS = {'hevc': 'HEVC', 'h265': 'HEVC', 'h264': 'H.264', 'avc': 'H.264', 'avc1': 'H.264', 'av1': 'AV1',
           'vp9': 'VP9', 'vc1': 'VC-1', 'mpeg2video': 'MPEG-2', 'mpeg4': 'MPEG-4'}
_HDR = {'dolbyvision': 'DV', 'hdr10plus': 'HDR10+', 'hdr10': 'HDR10', 'hlg': 'HLG'}
_AUDIO = (('truehd_atmos', 'TrueHD'), ('eac3_ddp_atmos', 'DD+'), ('truehd', 'TrueHD'), ('eac3', 'DD+'),
          ('ac3', 'DD'), ('dtshd_ma_x_imax', 'DTS:X'), ('dtshd_ma_x', 'DTS:X'), ('dtshd_ma', 'DTS-HD MA'),
          ('dtshd_hra', 'DTS-HD'), ('dca', 'DTS'), ('dts', 'DTS'), ('aac', 'AAC'), ('flac', 'FLAC'),
          ('opus', 'Opus'), ('pcm', 'PCM'), ('mp3', 'MP3'))
_CHANNELS = {'1': '1.0', '2': '2.0', '3': '2.1', '6': '5.1', '7': '6.1', '8': '7.1'}


def _context():
    try:
        import json
        raw = xbmcgui.Window(10000).getProperty('dexhub.play_context') or '{}'
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _text_of(ctx):
    """The stream's own words: its name, title and file name."""
    try:
        name = unquote(urlsplit(xbmc.getInfoLabel('Player.FileNameAndPath') or '').path).rsplit('/', 1)[-1]
    except Exception:
        name = ''
    return ' '.join(str(x or '') for x in (ctx.get('stream_name'), ctx.get('stream_title'), name))


def _size_bytes(ctx, text):
    try:
        size = int(float(ctx.get('size_bytes') or 0))
    except (TypeError, ValueError):
        size = 0
    if size:
        return size
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(TB|GB|MB)\b', text, re.I)
    if not m:
        return 0
    return int(float(m.group(1).replace(',', '.')) * {'tb': 1e12, 'gb': 1e9, 'mb': 1e6}[m.group(2).lower()])


def video_column(ctx, text):
    res = (xbmc.getInfoLabel('VideoPlayer.VideoResolution') or '').lower()
    bold = {'4k': '4K', '2160': '4K', '8k': '8K', '4320': '8K'}.get(res, (res.upper() + 'P') if res.isdigit() else res.upper())
    hdr = (xbmc.getInfoLabel('VideoPlayer.HdrType') or '').lower()
    if not hdr and re.search(r'(?:^|[ ._-])(?:dv|dovi|dolby[ ._-]?vision)(?:[ ._-]|$)', text, re.I):
        hdr = 'dolbyvision'
    light = _HDR.get(hdr, 'SDR' if bold else '')
    small = []
    codec = _CODECS.get((xbmc.getInfoLabel('VideoPlayer.VideoCodec') or '').lower(), '')
    if codec:
        small.append(codec)
    pixfmt = (xbmc.getInfoLabel('Player.Process(pixformat)') or '').lower()
    if pixfmt:
        small[-1:] = ['%s %s' % (small[-1] if small else '', '10bit' if ('10' in pixfmt or 'p010' in pixfmt) else
                      ('12bit' if '12' in pixfmt else '8bit'))]
    try:
        total = xbmc.Player().getTotalTime()
    except Exception:
        total = 0
    size = _size_bytes(ctx, text)
    if size and total > 60:
        small.append('%.1f Mbps' % (size * 8.0 / total / 1e6))
    return bold, light, ' · '.join(s.strip() for s in small if s.strip())


def audio_column(text):
    codec = (xbmc.getInfoLabel('VideoPlayer.AudioCodec') or '').lower()
    family = next((name for key, name in _AUDIO if codec.startswith(key)), codec.upper()[:10])
    atmos = 'atmos' in codec or bool(re.search(r'atmos', text, re.I))
    channels = _CHANNELS.get(xbmc.getInfoLabel('VideoPlayer.AudioChannels') or '', '')
    if atmos:
        return 'Atmos', '', ' '.join(x for x in (family, channels) if x)
    language = (xbmc.getInfoLabel('VideoPlayer.AudioLanguage') or '').upper()[:3]
    return family, channels, language


def rating_column(ctx):
    info = ctx.get('kodi_info') if isinstance(ctx.get('kodi_info'), dict) else {}
    cert = str(info.get('mpaa') or xbmc.getInfoLabel('VideoPlayer.mpaa') or '').strip()
    cert = re.sub(r'^(?:Rated|US:)\s*', '', cert)[:10]
    try:
        stars = float(info.get('rating') or ctx.get('rating') or 0)
    except (TypeError, ValueError):
        stars = 0.0
    return cert, '', ('★ %.1f' % stars) if stars else ''


# ------------------------------------------------------------------ showing
def _key():
    """One showing per playback and source (a new file is another key)."""
    try:
        return xbmc.Player().getPlayingFile() or ''
    except Exception:
        return ''


def clear(win=None):
    win = win or xbmcgui.Window(10000)
    for name in ('on', 'place', 'v1', 'v1b', 'v2', 'v.w', 'a1', 'a1b', 'a2', 'a.w', 'r1', 'r2', 'r.w'):
        win.clearProperty(PREFIX + name)


def busy():
    return bool(xbmcgui.Window(10000).getProperty(PREFIX + 'on'))


def starting():
    """A showing is waiting for the picture (the Parents Guide waits for it)."""
    return _SHOWN.get('running', 0) == _SHOWN['token'] and _SHOWN['token'] > 0


def start():
    """Player.OnAVStarted / OnAVChange: show it once for this file."""
    try:
        from .ui_preferences import value
        if value('player_capsule') == 'false':
            return
        if xbmc.getSkinDir() not in ('skin.dexhub', 'skin.dexhub.tvos'):
            return
        from .trailer_mark import playing_is_trailer
        if playing_is_trailer():
            return
        key = _key()
        with _LOCK:
            if not key or key == _SHOWN['key']:
                return
            _SHOWN['key'] = key
            _SHOWN['token'] += 1
            token = _SHOWN['token']
    except Exception:
        return
    threading.Thread(target=_run, args=(token,), name='DexHubCapsule', daemon=True).start()


def _run(token):
    _SHOWN['running'] = token
    try:
        _show(token)
    finally:
        if _SHOWN.get('running') == token:
            _SHOWN['running'] = 0


def _show(token):
    from .ui_preferences import value
    win = xbmcgui.Window(10000)
    player = xbmc.Player()
    monitor = xbmc.Monitor()
    try:
        # once the picture runs: with refresh rate matching the screen is
        # black for the first seconds (the Parents Guide waits the same way)
        first, waited = None, time.time()
        while time.time() - waited < 25:
            if token != _SHOWN['token'] or not player.isPlayingVideo() or monitor.abortRequested():
                return
            try:
                at = player.getTime()
            except Exception:
                at = 0.0
            first = at if first is None else first
            if at - first >= 1.5 and not xbmc.getCondVisibility('Player.Paused'):
                break
            if monitor.waitForAbort(0.4):
                return
        ctx = _context()
        text = _text_of(ctx)
        v1, v1b, v2 = video_column(ctx, text)
        a1, a1b, a2 = audio_column(text)
        r1, _r1b, r2 = rating_column(ctx) if value('player_capsule_rating') != 'false' else ('', '', '')
        if not (v1 or a1):
            return
        props = {'v1': v1, 'v1b': v1b, 'v2': v2, 'v.w': width_class(v1 + (' ' + v1b if v1b else ''), v2) if v1 else '',
                 'a1': a1, 'a1b': a1b, 'a2': a2, 'a.w': width_class(a1 + (' ' + a1b if a1b else ''), a2) if a1 else '',
                 'r1': r1, 'r2': r2, 'r.w': width_class(r1, r2) if (r1 or r2) else '',
                 'place': value('player_capsule_place') or 'top_left'}
        if token != _SHOWN['token']:
            return
        for name, val in props.items():
            if val:
                win.setProperty(PREFIX + name, val)
            else:
                win.clearProperty(PREFIX + name)
        win.setProperty(PREFIX + 'on', '1')         # last: the skin shows it whole
        try:
            seconds = float(value('player_capsule_time') or 6)
        except ValueError:
            seconds = 6.0
        end = time.time() + seconds
        while time.time() < end:
            if token != _SHOWN['token'] or not player.isPlayingVideo() or monitor.waitForAbort(0.3):
                break
    except Exception as exc:
        xbmc.log('[DexHub] capsule: %s' % exc, xbmc.LOGDEBUG)
    finally:
        if token == _SHOWN['token']:
            win.clearProperty(PREFIX + 'on')        # the skin fades it out
            monitor.waitForAbort(0.7)
            if token == _SHOWN['token'] and not win.getProperty(PREFIX + 'on'):
                clear(win)


def stopped():
    """Playback ended: the next one shows it again, the same file too."""
    with _LOCK:
        _SHOWN['key'] = ''
        _SHOWN['token'] += 1
    clear()
