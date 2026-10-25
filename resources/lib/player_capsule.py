# -*- coding: utf-8 -*-
"""v5.10.417: the playback capsule (skin.dexhub 3.28.134); v5.10.418 (skin
3.28.135): badges, the rating services' logos, and with OK.

A glass pill at the top of the picture with Dex Hub's mark and what is
playing: the video (the 4K and Dolby Vision badges, FEL or MEL, and under it
HEVC 10bit · 24.5 Mbps), the audio (Atmos and 7.1, and under it TrueHD · ENG)
and the title's age rating with the logos and scores of the rating services
chosen for the title page (the first three). Or the same in words.

When: once the picture runs (a new playback or another source), for a few
seconds; and while the player's menu is open (OK), for as long as it is.

The skin draws it from Home properties (dhs.cap.*). Kodi can neither size a
background to its text nor place a control from a property, so this module
measures everything (the skin's fonts, the badges' own sizes) and publishes:
each column's width class (dhs.cap.<column>.w, 20 px steps, the skin has a
background for each) and the x of each line as bits (dhs.cap.<line>.<bit>,
spacers of 1, 2, 4 ... 1024 px that the skin shows or hides in front of it).
tools/release/skin_capsule.py writes the skin's side; the sizes below must
match it.

Settings (ui_preferences): player_capsule, player_capsule_when (both, start,
ok), player_capsule_look (badges, text), player_capsule_rating,
player_capsule_services, player_capsule_place (top_left, top_center,
top_right), player_capsule_time.
"""
import re
import threading
import time
import struct
from urllib.parse import unquote, urlsplit

import xbmc
import xbmcgui
import xbmcvfs

PREFIX = 'dhs.cap.'
_SHOWN = {'key': '', 'token': 0}
_LOCK = threading.Lock()
STEP, MIN_W, MAX_W = 20, 40, 600
# the pill (must match tools/release/skin_capsule.py)
SCREEN, SIDE, LOGO, END, PAD, DIV = 1920, 48, 92, 46, 20, 2
BADGE_H, ICON_H, GAP, SERVICE_GAP, VALUE_GAP = 40, 34, 8, 18, 8
BITS = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
LINES = ('bg', 'v1', 'v2', 'a1', 'a2', 's')

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
    return str(_class(widest))


def _class(width):
    w = int((width * 1.04 + STEP - 1) // STEP * STEP)
    return max(MIN_W, min(MAX_W, w))


_SIZES = {}


def _png_width(rel, height):
    """The width a skin image is drawn at, at this height (its own aspect)."""
    if rel not in _SIZES:
        size = None
        try:
            with open(xbmcvfs.translatePath('special://skin/media/' + rel), 'rb') as fh:
                head = fh.read(24)
            if head[:8] == b'\x89PNG\r\n\x1a\n':
                size = struct.unpack('>II', head[16:24])
        except Exception:
            size = None
        _SIZES[rel] = size
    size = _SIZES[rel]
    if not size or not size[1]:
        return 0
    return int(round(size[0] * float(height) / size[1]))


# ------------------------------------------------------------------ what plays
_CODECS = {'hevc': 'HEVC', 'h265': 'HEVC', 'h264': 'H.264', 'avc': 'H.264', 'avc1': 'H.264', 'av1': 'AV1',
           'vp9': 'VP9', 'vc1': 'VC-1', 'mpeg2video': 'MPEG-2', 'mpeg4': 'MPEG-4'}
_HDR = {'dolbyvision': 'DV', 'hdr10plus': 'HDR10+', 'hdr10': 'HDR10', 'hlg': 'HLG'}
_HDR_BADGE = {'dolbyvision': 'hdr_dv', 'hdr10plus': 'hdr_10plus', 'hdr10': 'hdr_10', 'hlg': 'hdr_hlg'}
_RES_BADGE = {'4K': 'res_4k', '8K': 'res_8k', '1080P': 'res_1080', '720P': 'res_720', '576P': 'res_576',
              '540P': 'res_540', '480P': 'res_480'}
# Kodi's audio codec (its first letters) -> the name, the badge
_AUDIO = (('truehd_atmos', 'TrueHD', 'audio_atmos'), ('eac3_ddp_atmos', 'DD+', 'audio_atmos'),
          ('truehd', 'TrueHD', 'audio_truehd'), ('eac3', 'DD+', 'audio_ddplus'), ('ac3', 'DD', 'audio_dd'),
          ('dtshd_ma_x_imax', 'DTS:X', 'audio_dtsx_imax'), ('dtshd_ma_x', 'DTS:X', 'audio_dtsx'),
          ('dtshd_ma', 'DTS-HD MA', 'audio_dtshd_ma'), ('dtshd_hra', 'DTS-HD HRA', 'audio_dtshd_hra'),
          ('dts_express', 'DTS Express', 'audio_dts_express'), ('dts_es', 'DTS-ES', 'audio_dts_es'),
          ('dts_96', 'DTS 96/24', 'audio_dts_9624'), ('dca', 'DTS', 'audio_dts'), ('dts', 'DTS', 'audio_dts'),
          ('aac_latm', 'AAC LATM', 'audio_aac'), ('aac', 'AAC', 'audio_aac'), ('flac', 'FLAC', 'audio_flac'),
          ('alac', 'ALAC', 'audio_alac'), ('opus', 'Opus', 'audio_opus'), ('vorbis', 'Vorbis', 'audio_vorbis'),
          ('pcm', 'PCM', 'audio_pcm'), ('lpcm', 'PCM', 'audio_pcm'), ('mp3', 'MP3', 'audio_mp3'),
          ('mp2', 'MP2', 'audio_mp2'), ('mp1', 'MP1', 'audio_mp1'))
# what Kodi names only by its core, told apart by the stream's own words
_AUDIO_WORDS = (('dts', r'dts[ ._-]?es\b', 'DTS-ES', 'audio_dts_es'),
                ('dts', r'dts[ ._-]?96[ /._-]?24', 'DTS 96/24', 'audio_dts_9624'),
                ('dts', r'dts[ ._-]?express', 'DTS Express', 'audio_dts_express'),
                ('aac', r'he[ ._-]?aac[ ._-]?v2', 'HE-AAC v2', 'audio_aac_hev2'),
                ('aac', r'he[ ._-]?aac', 'HE-AAC', 'audio_aac_he'),
                ('aac', r'aac[ ._-]?lc', 'AAC-LC', 'audio_aac_lc'))
_CHANNELS = {'1': '1.0', '2': '2.0', '3': '2.1', '4': '4.0', '5': '5.0', '6': '5.1', '7': '6.1', '8': '7.1',
             '10': '7.1.2', '12': '7.1.4'}
_HEIGHTS = {'5.1.2': 'ch_512', '5.1.4': 'ch_514', '7.1.2': 'ch_712', '7.1.4': 'ch_714'}


def _context():
    try:
        import json
        raw = xbmcgui.Window(10000).getProperty('dexhub.play_context') or '{}'
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _text_of(ctx):
    """The stream's own words: its name, title, description and file name."""
    try:
        name = unquote(urlsplit(xbmc.getInfoLabel('Player.FileNameAndPath') or '').path).rsplit('/', 1)[-1]
    except Exception:
        name = ''
    return ' '.join(str(x or '') for x in (ctx.get('stream_name'), ctx.get('stream_title'),
                                           ctx.get('stream_description'), name))


def _size_bytes(ctx, text):
    hints = ctx.get('behaviorHints') if isinstance(ctx.get('behaviorHints'), dict) else {}
    for raw in (ctx.get('size_bytes'), hints.get('videoSize')):
        try:
            size = int(float(raw or 0))
        except (TypeError, ValueError):
            size = 0
        if size:
            return size
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(TB|GB|MB)\b', text, re.I)
    if not m:
        return 0
    return int(float(m.group(1).replace(',', '.')) * {'tb': 1e12, 'gb': 1e9, 'mb': 1e6}[m.group(2).lower()])


def _dv_layer(text):
    """Dolby Vision's enhancement layer and profile, when the stream says them."""
    layer = 'fel' if re.search(r'(?:^|[^a-z])fel(?:[^a-z]|$)', text, re.I) else (
        'mel' if re.search(r'(?:^|[^a-z])mel(?:[^a-z]|$)', text, re.I) else '')
    m = re.search(r'(?:(?:^|[ ._\[(-])p|profile[ ._-]?)([4-9])(?:\.\d)?(?=[ ._\])-]|$)', text, re.I)
    return layer, ('P' + m.group(1)) if m else ''


def video_column(ctx, text):
    """(bold, light, small): 4K, DV, HEVC 10bit · 24.5 Mbps."""
    return _video(ctx, text)[:3]


def _video(ctx, text):
    res = (xbmc.getInfoLabel('VideoPlayer.VideoResolution') or '').lower()
    bold = {'4k': '4K', '2160': '4K', '8k': '8K', '4320': '8K'}.get(res, (res.upper() + 'P') if res.isdigit() else res.upper())
    hdr = (xbmc.getInfoLabel('VideoPlayer.HdrType') or '').lower()
    if not hdr and re.search(r'(?:^|[ ._-])(?:dv|dovi|dolby[ ._-]?vision)(?:[ ._-]|$)', text, re.I):
        hdr = 'dolbyvision'
    light = _HDR.get(hdr, 'SDR' if bold else '')
    layer, profile = _dv_layer(text) if hdr == 'dolbyvision' else ('', '')
    small = [profile] if profile else []
    codec = _CODECS.get((xbmc.getInfoLabel('VideoPlayer.VideoCodec') or '').lower(), '')
    if codec:
        small.append(codec)
    pixfmt = (xbmc.getInfoLabel('Player.Process(pixformat)') or '').lower()
    if pixfmt:
        bits = '10bit' if ('10' in pixfmt or 'p010' in pixfmt) else ('12bit' if '12' in pixfmt else '8bit')
        if codec:
            small[-1] = '%s %s' % (codec, bits)
        else:
            small.append(bits)
    try:
        total = xbmc.Player().getTotalTime()
    except Exception:
        total = 0
    size = _size_bytes(ctx, text)
    if size and total > 60:
        small.append('%.1f Mbps' % (size * 8.0 / total / 1e6))
    badges = [b for b in (_RES_BADGE.get(bold), _HDR_BADGE.get(hdr)) if b]
    if re.search(r'(?:^|[^a-z])imax(?:[^a-z]|$)', text, re.I):
        badges.append('other_imax')
    return bold, light, ' · '.join(s.strip() for s in small if s.strip()), badges, layer


def audio_column(text):
    """(bold, light, small): Atmos, '', TrueHD 7.1."""
    return _audio(text, 'text')[:3]


def _audio(text, look):
    codec = (xbmc.getInfoLabel('VideoPlayer.AudioCodec') or '').lower()
    family, badge = next(((name, b) for key, name, b in _AUDIO if codec.startswith(key)),
                         (codec.upper()[:10], ''))
    for core, words, name, b in _AUDIO_WORDS:
        if badge == 'audio_' + core and re.search(words, text, re.I):
            family, badge = name, b
            break
    atmos = 'atmos' in codec or (badge in ('audio_truehd', 'audio_ddplus') and bool(re.search(r'atmos', text, re.I)))
    if atmos:
        badge = 'audio_atmos'
    channels = _CHANNELS.get(xbmc.getInfoLabel('VideoPlayer.AudioChannels') or '', '')
    heights = re.search(r'(?<!\d)([57])[ ._]1[ ._]([24])(?!\d)', text) if (atmos or 'dtsx' in badge) else None
    if heights:
        channels = '%s.1.%s' % heights.groups()
    channel_badge = _HEIGHTS.get(channels) or ('ch_%s' % xbmc.getInfoLabel('VideoPlayer.AudioChannels')
                                               if channels and '.' in channels and channels.count('.') == 1 else '')
    language = (xbmc.getInfoLabel('VideoPlayer.AudioLanguage') or '').upper()[:3]
    if look == 'badges':
        named = family if badge in ('audio_atmos', 'audio_dtsx_imax') or not badge else ''
        return '', '', ' · '.join(x for x in (named, language) if x), [b for b in (badge, channel_badge) if b]
    if atmos:
        return 'Atmos', '', ' '.join(x for x in (family, channels) if x), []
    return family, channels, language, []


def _cert(ctx):
    info = ctx.get('kodi_info') if isinstance(ctx.get('kodi_info'), dict) else {}
    cert = str(info.get('mpaa') or xbmc.getInfoLabel('VideoPlayer.mpaa') or '').strip()
    return re.sub(r'^(?:Rated|US:)\s*', '', cert)[:10]


def rating_column(ctx):
    """(age rating, '', ''): the old third column, now the services' one."""
    return _cert(ctx), '', ''


def _services(ctx):
    """[(icon, value)]: the first three of the ratings chosen for the title
    page, with the same logos (Rotten Tomatoes fresh or rotten)."""
    try:
        from .homeui import ratings
        ids = ctx.get('external_ids') or {}
        kind = str(ctx.get('media_type') or '').lower()
        series = kind in ('series', 'tv', 'episode') or (kind not in ('movie', 'movies') and bool(ctx.get('season')))
        imdb = str(ctx.get('imdb_id') or ids.get('imdb_id') or ids.get('imdb') or '')
        tmdb = str(ctx.get('show_tmdb_id') or ctx.get('tmdb_id') or ids.get('tmdb_id') or ids.get('tmdb') or '')
        values = ratings.collect(imdb_id=imdb, tmdb_id=tmdb, media_type='series' if series else 'movie',
                                 network=True)
        shown = ratings.props(values, prefix='x.', limit=3)
    except Exception as exc:
        xbmc.log('[DexHub] capsule ratings: %s' % exc, xbmc.LOGDEBUG)
        return []
    out = []
    for key in ratings.ORDER:
        value = shown.get('x.' + key) or ''
        if not value:
            continue
        icon = shown.get('x.%s.icon' % key) or {'metacritic': 'metacritic.png'}.get(key, key + '.png')
        out.append((icon, value))
    return out[:3]


# ------------------------------------------------------------------ layout
def _bits(name, x, out):
    x = max(0, min(sum(BITS), int(x)))
    for bit in BITS:
        out['%s.%d' % (name, bit)] = '1' if x & bit else ''


def layout(ctx, text, look='badges', cert=True, services=None, place='top_left'):
    """The Home properties the skin draws the pill from (empty values clear)."""
    props = {'look': look, 'place': place}
    b30 = lambda s: _measure(s, _BOLD30)
    r22 = lambda s: _measure(s, _REG22)
    bold, light, small, vbadges, layer = _video(ctx, text)
    a1, a1b, a2, abadges = _audio(text, look)
    columns = []                                   # (name, content width)
    tag = ('cap_%s.png' % layer) if layer else ''
    tag_w = _png_width('dexhub/' + tag, BADGE_H) if tag else 0
    layer = layer if tag_w else ''
    props['v2'], props['a2'] = small, a2
    props['vtag'], props['vtag.w'] = layer, str(tag_w) if tag else ''
    if look == 'badges':
        widths = [_png_width('dexhub/badges/%s.png' % b, BADGE_H) for b in vbadges]
        vbadges = [b for b, w in zip(vbadges, widths) if w]
        widths = [w for w in widths if w]
        line = sum(widths) + GAP * max(0, len(widths) - 1) + ((GAP + tag_w) if tag_w else 0)
        for i in range(3):
            props['vb%d' % (i + 1)] = vbadges[i] if i < len(vbadges) else ''
            props['vb%d.w' % (i + 1)] = str(widths[i]) if i < len(vbadges) else ''
        props['v1'] = props['v1b'] = ''
        if vbadges or small:
            columns.append(('v', max(line, r22(small))))
        widths = [_png_width('dexhub/badges/%s.png' % b, BADGE_H) for b in abadges]
        abadges = [b for b, w in zip(abadges, widths) if w]
        widths = [w for w in widths if w]
        for i in range(2):
            props['ab%d' % (i + 1)] = abadges[i] if i < len(abadges) else ''
            props['ab%d.w' % (i + 1)] = str(widths[i]) if i < len(abadges) else ''
        props['a1'] = props['a1b'] = ''
        if abadges or a2:
            columns.append(('a', max(sum(widths) + GAP * max(0, len(widths) - 1), r22(a2))))
    else:
        props.update({'v1': bold, 'v1b': light, 'a1': a1, 'a1b': a1b})
        for name in ('vb1', 'vb2', 'vb3', 'ab1', 'ab2'):
            props[name] = props[name + '.w'] = ''
        if bold:
            columns.append(('v', max(b30(bold + (' ' + light if light else '')) + ((GAP + tag_w) if tag_w else 0),
                                     r22(small))))
        if a1:
            columns.append(('a', max(b30(a1 + (' ' + a1b if a1b else '')), r22(a2))))
    cert = _cert(ctx) if cert else ''
    services = list(services or [])
    props['s.cert'] = cert
    width = b30(cert)
    for i in range(3):
        icon, value = services[i] if i < len(services) else ('', '')
        icon_w = _png_width('dexhub/ratings/' + icon, ICON_H) if icon else 0
        if not icon_w:
            icon = value = ''
        props['s%d.icon' % (i + 1)], props['s%d.v' % (i + 1)] = icon, value
        props['s%d.w' % (i + 1)] = str(icon_w) if icon else ''
        if icon:
            width += (SERVICE_GAP if width else 0) + icon_w + VALUE_GAP + b30(value)
    if width:
        columns.append(('s', width))
    for name in ('v', 'a', 's'):
        props[name + '.w'] = ''
    if not any(name in ('v', 'a') for name, _w in columns):
        return {}
    classes = [(name, _class(w)) for name, w in columns]
    total = LOGO + sum(c + 2 * PAD for _n, c in classes) + DIV * (len(classes) - 1) + END
    x0 = {'top_center': (SCREEN - total) // 2, 'top_right': SCREEN - SIDE - total}.get(place, SIDE)
    _bits('bg', x0, props)
    x = x0 + LOGO
    for name, c in classes:
        props[name + '.w'] = str(c)
        for line in ((name + '1', name + '2') if name != 's' else ('s',)):
            _bits(line, x + PAD, props)
        x += c + 2 * PAD + DIV
    for name in ('v', 'a', 's'):
        if not props[name + '.w']:
            for line in ((name + '1', name + '2') if name != 's' else ('s',)):
                _bits(line, 0, props)
    props['total'] = str(total)
    return props


# ------------------------------------------------------------------ showing
def _key():
    """One showing per playback and source (a new file is another key)."""
    try:
        return xbmc.Player().getPlayingFile() or ''
    except Exception:
        return ''


def clear(win=None):
    win = win or xbmcgui.Window(10000)
    for name in _SHOWN.pop('names', ()):
        win.clearProperty(PREFIX + name)
    for name in ('on', 'osd', 'ready'):
        win.clearProperty(PREFIX + name)


def busy():
    return bool(xbmcgui.Window(10000).getProperty(PREFIX + 'on'))


def starting():
    """A showing is waiting for the picture (the Parents Guide waits for it)."""
    return _SHOWN.get('running', 0) == _SHOWN['token'] and _SHOWN['token'] > 0


def _wanted():
    from .ui_preferences import value
    if value('player_capsule') == 'false':
        return False
    if xbmc.getSkinDir() not in ('skin.dexhub', 'skin.dexhub.tvos'):
        return False
    from . import tinyppi_look
    if tinyppi_look.tinyppi_shows():          # v5.10.418: TinyPPI's logos instead
        return False
    from .trailer_mark import playing_is_trailer
    return not playing_is_trailer()


def start():
    """Player.OnAVStarted / OnAVChange: show it once for this file; the same
    file again (another audio track) only brings what it says up to date."""
    try:
        if not _wanted():
            return
        key = _key()
        with _LOCK:
            if not key:
                return
            if key == _SHOWN['key']:
                token = _SHOWN['token']
                target = _refresh
            else:
                _SHOWN['key'] = key
                _SHOWN['token'] += 1
                _SHOWN['services'] = None
                token = _SHOWN['token']
                target = _run
    except Exception:
        return
    threading.Thread(target=target, args=(token,), name='DexHubCapsule', daemon=True).start()


def _run(token):
    _SHOWN['running'] = token
    try:
        _show(token)
    finally:
        if _SHOWN.get('running') == token:
            _SHOWN['running'] = 0


def _publish(token, ctx):
    from .ui_preferences import value
    win = xbmcgui.Window(10000)
    services = _SHOWN.get('services') or []
    props = layout(ctx, _text_of(ctx), look='text' if value('player_capsule_look') == 'text' else 'badges',
                   cert=value('player_capsule_rating') != 'false', services=services,
                   place=value('player_capsule_place') or 'top_left')
    if token != _SHOWN['token'] or not props:
        return False
    for name, val in props.items():
        if win.getProperty(PREFIX + name) != val:
            if val:
                win.setProperty(PREFIX + name, val)
            else:
                win.clearProperty(PREFIX + name)
    _SHOWN['names'] = set(_SHOWN.get('names') or ()) | set(props)
    if value('player_capsule_when') in ('both', 'ok', ''):
        win.setProperty(PREFIX + 'osd', '1')
    else:
        win.clearProperty(PREFIX + 'osd')
    win.setProperty(PREFIX + 'ready', '1')
    return True


def _refresh(token):
    try:
        if xbmcgui.Window(10000).getProperty(PREFIX + 'ready'):
            _publish(token, _context())
    except Exception as exc:
        xbmc.log('[DexHub] capsule: %s' % exc, xbmc.LOGDEBUG)


def _late(token, lookup, ctx):
    """The ratings came after the pill: they join it."""
    lookup.join(10.0)
    if token == _SHOWN['token'] and _SHOWN.get('services'):
        _publish(token, ctx)


def _show(token):
    from .ui_preferences import value
    win = xbmcgui.Window(10000)
    player = xbmc.Player()
    monitor = xbmc.Monitor()
    shown = False
    try:
        ctx = _context()
        lookup = None
        if value('player_capsule_services') != 'false':
            # beside the wait for the picture (it may ask MDbList)
            def fetch():
                found = _services(ctx)
                if token == _SHOWN['token']:
                    _SHOWN['services'] = found
            lookup = threading.Thread(target=fetch, name='DexHubCapsuleRatings', daemon=True)
            lookup.start()
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
        if lookup is not None:
            lookup.join(2.0)
        if not _publish(token, ctx):
            return
        if lookup is not None and lookup.is_alive():
            threading.Thread(target=_late, args=(token, lookup, ctx), daemon=True).start()
        if value('player_capsule_when') == 'ok':
            return
        win.setProperty(PREFIX + 'on', '1')         # last: the skin shows it whole
        shown = True
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
        if shown and token == _SHOWN['token']:
            win.clearProperty(PREFIX + 'on')        # the skin fades it out; the rest stays for OK


def stopped():
    """Playback ended: the next one shows it again, the same file too."""
    with _LOCK:
        _SHOWN['key'] = ''
        _SHOWN['token'] += 1
    clear()
