# -*- coding: utf-8 -*-
"""The rating card when a film starts (v5.10.421; v5.10.423 the card alone).

As a streaming service shows it: the age rating large (R), what earned it
(IMDb's Parents Guide: the categories rated severe or moderate), and a quiet
line with the video, the audio and the first of the ratings chosen for the
title page (4K HDR10 · DTS 5.1 · IMDb 6.5). It comes once the picture runs,
for a new playback or another source, stays a few seconds and leaves; with
the player's menu too when asked. It takes the Parents Guide corner's place.
When TinyPPI's own logos sit on the same side it goes to the other.

v5.10.423: the pill (badges or words) and Dex Hub's design of TinyPPI's
windows are gone; TinyPPI is its own. The skin (skin.dexhub 3.28.138,
tools/release/skin_capsule.py) draws the card from Home properties
dhs.cap.card.* and shows it while dhs.cap.on is set.

Settings (ui_preferences): player_capsule, player_capsule_when (start, both,
ok), player_capsule_place (top_left, top_center, top_right),
player_capsule_time; parents_guide for the words.
"""
import os
import re
import threading
import time
from urllib.parse import unquote, urlsplit

import xbmc
import xbmcgui

PREFIX = 'dhs.cap.'
TINYPPI = 'script.tinyppi'
_SHOWN = {'key': '', 'token': 0}
_LOCK = threading.Lock()

# ------------------------------------------------------------------ what plays
_HDR = {'dolbyvision': 'DV', 'hdr10plus': 'HDR10+', 'hdr10': 'HDR10', 'hlg': 'HLG'}
# Kodi's audio codec (its first letters) -> its name
_AUDIO = (('truehd_atmos', 'TrueHD'), ('eac3_ddp_atmos', 'DD+'), ('truehd', 'TrueHD'), ('eac3', 'DD+'),
          ('ac3', 'DD'), ('dtshd_ma_x_imax', 'DTS:X IMAX'), ('dtshd_ma_x', 'DTS:X'), ('dtshd_ma', 'DTS-HD MA'),
          ('dtshd_hra', 'DTS-HD HRA'), ('dts_express', 'DTS Express'), ('dts_es', 'DTS-ES'),
          ('dts_96', 'DTS 96/24'), ('dca', 'DTS'), ('dts', 'DTS'), ('aac', 'AAC'), ('flac', 'FLAC'),
          ('alac', 'ALAC'), ('opus', 'Opus'), ('vorbis', 'Vorbis'), ('pcm', 'PCM'), ('lpcm', 'PCM'),
          ('mp3', 'MP3'), ('mp2', 'MP2'), ('mp1', 'MP1'))
# what Kodi names only by its core, told apart by the stream's own words
_AUDIO_WORDS = (('DTS', r'dts[ ._-]?es\b', 'DTS-ES'), ('DTS', r'dts[ ._-]?96[ /._-]?24', 'DTS 96/24'),
                ('DTS', r'dts[ ._-]?express', 'DTS Express'), ('AAC', r'he[ ._-]?aac[ ._-]?v2', 'HE-AAC v2'),
                ('AAC', r'he[ ._-]?aac', 'HE-AAC'))
_CHANNELS = {'1': '1.0', '2': '2.0', '3': '2.1', '4': '4.0', '5': '5.0', '6': '5.1', '7': '6.1', '8': '7.1',
             '10': '7.1.2', '12': '7.1.4'}
_SERVICE_NAMES = {'imdb': 'IMDb', 'tmdb': 'TMDb', 'rt': 'RT', 'metacritic': 'Metacritic', 'trakt': 'Trakt',
                  'letterboxd': 'Letterboxd', 'mdblist': 'MDBList', 'mal': 'MAL'}


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


def _video(text):
    """'4K HDR10' (SDR is not said)."""
    res = (xbmc.getInfoLabel('VideoPlayer.VideoResolution') or '').lower()
    size = {'4k': '4K', '2160': '4K', '8k': '8K', '4320': '8K'}.get(res, (res.upper() + 'P') if res.isdigit() else res.upper())
    hdr = (xbmc.getInfoLabel('VideoPlayer.HdrType') or '').lower()
    if not hdr and re.search(r'(?:^|[ ._-])(?:dv|dovi|dolby[ ._-]?vision)(?:[ ._-]|$)', text, re.I):
        hdr = 'dolbyvision'
    return ' '.join(x for x in (size, _HDR.get(hdr, '')) if x)


def _audio(text):
    """'DTS 5.1', 'TrueHD Atmos 7.1.4'."""
    codec = (xbmc.getInfoLabel('VideoPlayer.AudioCodec') or '').lower()
    family = next((name for key, name in _AUDIO if codec.startswith(key)), codec.upper()[:10])
    for core, words, name in _AUDIO_WORDS:
        if family == core and re.search(words, text, re.I):
            family = name
            break
    atmos = 'atmos' in codec or (family in ('TrueHD', 'DD+') and bool(re.search(r'atmos', text, re.I)))
    channels = _CHANNELS.get(xbmc.getInfoLabel('VideoPlayer.AudioChannels') or '', '')
    heights = re.search(r'(?<!\d)([57])[ ._]1[ ._]([24])(?!\d)', text) if (atmos or 'DTS:X' in family) else None
    if heights:
        channels = '%s.1.%s' % heights.groups()
    return ' '.join(x for x in (family, 'Atmos' if atmos else '', channels) if x)


def _cert(ctx):
    info = ctx.get('kodi_info') if isinstance(ctx.get('kodi_info'), dict) else {}
    cert = str(info.get('mpaa') or xbmc.getInfoLabel('VideoPlayer.mpaa') or '').strip()
    return re.sub(r'^(?:Rated|US:)\s*', '', cert)[:10]


def _first_rating(ctx):
    """'IMDb 6.5': the first of the ratings chosen for the title page."""
    try:
        from .homeui import ratings
        ids = ctx.get('external_ids') or {}
        kind = str(ctx.get('media_type') or '').lower()
        series = kind in ('series', 'tv', 'episode') or (kind not in ('movie', 'movies') and bool(ctx.get('season')))
        imdb = str(ctx.get('imdb_id') or ids.get('imdb_id') or ids.get('imdb') or '')
        tmdb = str(ctx.get('show_tmdb_id') or ctx.get('tmdb_id') or ids.get('tmdb_id') or ids.get('tmdb') or '')
        values = ratings.collect(imdb_id=imdb, tmdb_id=tmdb, media_type='series' if series else 'movie',
                                 network=True)
        shown = ratings.props(values, prefix='x.', limit=1)
        for key in ratings.ORDER:
            value = shown.get('x.' + key) or ''
            if value:
                return '%s %s' % (_SERVICE_NAMES.get(key.split('_')[0], key.upper()), value)
    except Exception as exc:
        xbmc.log('[DexHub] rating card ratings: %s' % exc, xbmc.LOGDEBUG)
    return ''


def _imdb(ctx):
    for raw in (ctx.get('show_imdb_id'), ctx.get('imdb_id'), ctx.get('canonical_id')):
        raw = str(raw or '')
        if raw.startswith('tt'):
            return raw.split(':')[0]
    return ''


def _guide(ctx):
    imdb = _imdb(ctx)
    if not imdb:
        return []
    try:
        from . import parents_guide
        return parents_guide.guide(imdb, timeout=5) or []
    except Exception as exc:
        xbmc.log('[DexHub] rating card parents guide: %s' % exc, xbmc.LOGINFO)
        return []


def _words(rows):
    """What earned the rating: the categories rated severe, then moderate."""
    try:
        from . import parents_guide as PG
        en = 1 if PG._english() else 0
    except Exception:
        return ''
    rank = {'SEVERE': 0, 'MODERATE': 1}
    picked = sorted((rank[sid], i, cid) for i, (cid, sid) in enumerate(rows or []) if sid in rank and cid in PG.SHORT)
    return ' · '.join(PG.SHORT[cid][en] for _r, _i, cid in picked)


def card(ctx, text, rating='', guide=None, place='top_left'):
    """The Home properties the skin draws the card from ({} for nothing to say)."""
    info = '  ·  '.join(x for x in (_video(text), _audio(text), rating) if x)
    props = {'place': place, 'card.cert': _cert(ctx), 'card.words': _words(guide), 'card.info': info}
    if not any(props[k] for k in ('card.cert', 'card.words', 'card.info')):
        return {}
    # the bar beside it is as tall as the lines (tools/release/skin_capsule.py CARD_LINES)
    props['card.h'] = str(72 * bool(props['card.cert']) + 40 * bool(props['card.words'])
                          + 34 * bool(props['card.info']))
    return props


def _tinyppi():
    try:
        return bool(xbmc.getCondVisibility('System.HasAddon(%s)' % TINYPPI))
    except Exception:
        return False


def _place(place, when):
    """Away from TinyPPI's own logos when they sit on that side (its settings:
    on at the start or in the player's menu, and how far across, 0 % at the
    left)."""
    try:
        if not _tinyppi():
            return place
        import xbmcaddon
        addon = xbmcaddon.Addon(TINYPPI)
        sides = set()
        for mode, on in (('start', 'splash_enabled'), ('osd', 'splash_show_on_osd')):
            if mode == 'osd' and when not in ('both', 'ok'):
                continue
            if mode == 'start' and when == 'ok':
                continue
            if str(addon.getSetting(on)).lower() != 'true':
                continue
            try:
                across = float(addon.getSetting('splash_%s_offset_x' % mode) or 0)
            except ValueError:
                across = 0.0
            sides.add('left' if across < 34 else ('right' if across > 66 else 'center'))
        side = {'top_left': 'left', 'top_right': 'right', 'top_center': 'center'}.get(place, 'left')
        if side in sides:
            for other in ('top_right', 'top_left', 'top_center'):
                if other.split('_')[1] not in sides:
                    return other
    except Exception as exc:
        xbmc.log('[DexHub] rating card place: %s' % exc, xbmc.LOGDEBUG)
    return place


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
    """A showing is waiting for the picture."""
    return _SHOWN.get('running', 0) == _SHOWN['token'] and _SHOWN['token'] > 0


def _why_not():
    """Why the card stays away for this video ('' when it comes)."""
    from .ui_preferences import value
    if value('player_capsule') == 'false':
        return 'turned off in the player options'
    skin = xbmc.getSkinDir()
    if skin not in ('skin.dexhub', 'skin.dexhub.tvos'):
        return 'the skin is %s, it needs skin.dexhub' % skin
    from .trailer_mark import playing_is_trailer
    if playing_is_trailer():
        return 'a trailer'
    return ''


def _wanted():
    return not _why_not()


def takes_guide():
    """The card shows IMDb's Parents Guide; its own corner stays away."""
    try:
        return _wanted()
    except Exception:
        return False


def _note(key, text):
    """Said once per video, in the normal log."""
    if _SHOWN.get('noted') != (key, text):
        _SHOWN['noted'] = (key, text)
        xbmc.log('[DexHub] rating card: %s' % text, xbmc.LOGINFO)


def start(foreign=False):
    """Player.OnAVStarted / OnAVChange: once for this file; the same file
    again (another audio track) only brings what it says up to date.
    foreign: a video Dex Hub did not start (its play context is another's)."""
    try:
        key = _key()
        why = _why_not()
        if why:
            _note(key, 'not shown (%s)' % why)
            return
        with _LOCK:
            if not key:
                return
            if key == _SHOWN['key']:
                token = _SHOWN['token']
                target = _refresh
            else:
                _SHOWN.update(key=key, token=_SHOWN['token'] + 1, rating='', guide=None, foreign=foreign)
                token = _SHOWN['token']
                target = _run
    except Exception as exc:
        xbmc.log('[DexHub] rating card: %s' % exc, xbmc.LOGWARNING)
        return
    threading.Thread(target=target, args=(token,), name='DexHubRatingCard', daemon=True).start()


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
    props = card(ctx, _text_of(ctx), rating=_SHOWN.get('rating') or '', guide=_SHOWN.get('guide') or [],
                 place=_place(value('player_capsule_place') or 'top_left', value('player_capsule_when')))
    if token != _SHOWN['token'] or not props:
        return False
    for name, val in props.items():
        if win.getProperty(PREFIX + name) != val:
            if val:
                win.setProperty(PREFIX + name, val)
            else:
                win.clearProperty(PREFIX + name)
    _SHOWN['names'] = set(_SHOWN.get('names') or ()) | set(props)
    if value('player_capsule_when') in ('both', 'ok'):
        win.setProperty(PREFIX + 'osd', '1')
    else:
        win.clearProperty(PREFIX + 'osd')
    win.setProperty(PREFIX + 'ready', '1')
    return True


def _ctx():
    return {} if _SHOWN.get('foreign') else _context()


def _refresh(token):
    try:
        if xbmcgui.Window(10000).getProperty(PREFIX + 'ready'):
            _publish(token, _ctx())
    except Exception as exc:
        xbmc.log('[DexHub] rating card: %s' % exc, xbmc.LOGDEBUG)


def _late(token, lookup, ctx):
    """The words or the rating came after the card: they join it."""
    lookup.join(10.0)
    if token == _SHOWN['token']:
        _publish(token, ctx)


def _show(token):
    from .ui_preferences import value
    win = xbmcgui.Window(10000)
    player = xbmc.Player()
    monitor = xbmc.Monitor()
    shown = False
    try:
        ctx = _ctx()
        if _SHOWN.get('foreign') and monitor.waitForAbort(3):
            return
        lookup = None
        if ctx:
            # beside the wait for the picture (it may ask IMDb and MDbList)
            want_guide = str(value('parents_guide') or 'true').lower() != 'false'

            def fetch():
                if want_guide:
                    found = _guide(ctx)
                    if token == _SHOWN['token']:
                        _SHOWN['guide'] = found
                found = _first_rating(ctx)
                if token == _SHOWN['token']:
                    _SHOWN['rating'] = found
            lookup = threading.Thread(target=fetch, name='DexHubRatingCardData', daemon=True)
            lookup.start()
        # once the picture runs: with refresh rate matching the screen is
        # black for the first seconds
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
        else:
            _note(_SHOWN['key'], 'not shown (the playing time did not move for 25 s)')
            return
        if lookup is not None:
            lookup.join(2.5)
        if not _publish(token, ctx):
            if token == _SHOWN['token']:
                _note(_SHOWN['key'], 'not shown (nothing to say: no age rating, guide or video details)')
            return
        _note(_SHOWN['key'], 'shown (%r, %r, %r, %s, %s)' % (
            win.getProperty(PREFIX + 'card.cert'), win.getProperty(PREFIX + 'card.words'),
            win.getProperty(PREFIX + 'card.info'), win.getProperty(PREFIX + 'place'),
            value('player_capsule_when') or 'start'))
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
        xbmc.log('[DexHub] rating card: %s' % exc, xbmc.LOGWARNING)
    finally:
        if shown and token == _SHOWN['token']:
            win.clearProperty(PREFIX + 'on')        # the skin fades it out; the rest stays for OK


def stopped():
    """Playback ended: the next one shows it again, the same file too."""
    with _LOCK:
        _SHOWN['key'] = ''
        _SHOWN['token'] += 1
    clear()


# ------------------------------------------------------------------ leftovers
_OLD_MARK = '<!-- dhs.tinyppi (skin_tinyppi.py) -->'
_OLD_TINYPPI = ('script-tinyppi-main.xml', 'script-tinyppi-dialog.xml', 'script-tinyppi-dialog-bar.xml',
                'script-tinyppi-dialog-single.xml')


def leftovers():
    """v5.10.423: TinyPPI is its own again. Skins 3.28.135-3.28.137 put Dex
    Hub's design of its windows in xml/ (and a copy in extras/tinyppi/); a
    skin update that keeps old files would keep them, so the ones that carry
    the old tool's mark are taken out. Nothing else is touched."""
    if xbmc.getSkinDir() not in ('skin.dexhub', 'skin.dexhub.tvos'):
        return
    try:
        import xbmcvfs
        skin = xbmcvfs.translatePath('special://skin/')
    except Exception:
        return
    for folder in ('xml', os.path.join('extras', 'tinyppi')):
        for name in _OLD_TINYPPI:
            path = os.path.join(skin, folder, name)
            try:
                if not os.path.exists(path):
                    continue
                with open(path, encoding='utf-8') as fh:
                    ours = _OLD_MARK in fh.read(512)
                if ours:
                    os.remove(path)
                    xbmc.log('[DexHub] TinyPPI is its own again: %s taken out' % os.path.join(folder, name),
                             xbmc.LOGINFO)
            except Exception as exc:
                xbmc.log('[DexHub] TinyPPI leftovers: %s: %s' % (name, exc), xbmc.LOGWARNING)
