# -*- coding: utf-8 -*-
"""The player's info page (v5.10.535, three full tabs since v5.10.536).

dexhub_playerinfo.xml (tools/make_player_info.py) over the running video:

  100       the tabs: Overview, Video, Audio (Dolby Vision modes with TinyPPI)
  110       badges: 4K, Dolby Vision, HEVC, Atmos, 5.1
  Overview  201 six tiles; 301 source, 302 video, 303 audio, 304 subtitles,
            305 network; 306 the CPU cores; 307 the system's meters
  Video     311 the picture's facts; 312 file to screen; 313 bit rate history;
            the resolution as boxes, the brightness, the colour space
  Audio     321 file to speakers; 322 output; 323 channels; 324 audio tracks;
            the room with the speakers in use

A thread reads Kodi's Player and System labels once a second and changes only
what moved. It reads no ListItem or Container label: those race the drawing
thread on Kodi 22. A source shows as its server's name and a file by its name
without any query: an address can hold a login or a key.

Kodi gives no sound levels, so the channel bars only show which channels are
in use, moving for the eye.
"""
import json
import random
import re
import threading
from urllib.parse import unquote, urlsplit

import xbmc
import xbmcgui

from . import common as C

A_LEFT, A_RIGHT = 1, 2
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
A_CLOSE = {11, 122}         # Info again closes it
TABS, BADGES = 100, 110
HISTORY = 30

AUDIO_NAMES = {'aac': 'AAC', 'ac3': 'Dolby Digital', 'eac3': 'Dolby Digital+', 'truehd': 'Dolby TrueHD',
               'dts': 'DTS', 'dca': 'DTS', 'dtshd_ma': 'DTS-HD MA', 'dtshd_hra': 'DTS-HD HRA', 'flac': 'FLAC',
               'mp3': 'MP3', 'opus': 'Opus', 'vorbis': 'Vorbis', 'pcm': 'PCM', 'pcm_s16le': 'PCM',
               'pcm_s24le': 'PCM'}
VIDEO_NAMES = {'hevc': 'HEVC', 'h265': 'HEVC', 'h264': 'H.264', 'avc1': 'H.264', 'av1': 'AV1', 'vp9': 'VP9',
               'mpeg2video': 'MPEG-2', 'vc1': 'VC-1', 'mpeg4': 'MPEG-4'}
HDR_NAMES = {'dolbyvision': 'Dolby Vision', 'hdr10': 'HDR10', 'hdr10plus': 'HDR10+', 'hlg': 'HLG'}
CHANNELS = {'1': '1.0', '2': '2.0', '3': '2.1', '6': '5.1', '7': '6.1', '8': '7.1'}
SPEAKERS = {'1': ('C',), '2': ('L', 'R'), '3': ('L', 'R', 'LFE'), '6': ('L', 'C', 'R', 'SL', 'SR', 'LFE'),
            '7': ('L', 'C', 'R', 'SL', 'SR', 'SBL', 'LFE'), '8': ('L', 'C', 'R', 'SL', 'SR', 'SBL', 'SBR', 'LFE')}
METERS = ('L', 'C', 'R', 'SL', 'SR', 'SBL', 'SBR', 'LFE', 'TFL', 'TFR', 'TRL', 'TRR')
CONTAINERS = {'mkv': 'Matroska (MKV)', 'mp4': 'MP4', 'm4v': 'MP4', 'ts': 'MPEG-TS', 'm2ts': 'Blu-ray (M2TS)',
              'avi': 'AVI', 'webm': 'WebM', 'mov': 'QuickTime', 'iso': 'Disc image', 'm3u8': 'HLS', 'mpd': 'DASH'}


def _english():
    try:
        from ..settings_cache import cached_addon
        return not (cached_addon().getSetting('ui_language') or '').strip().lower().startswith('ar')
    except Exception:
        return True


def _number(text):
    match = re.search(r'-?\d+(?:\.\d+)?', str(text or '').replace(',', ''))
    return float(match.group(0)) if match else 0.0


def speed(kbps):
    """'18.4 Mb/s' from Kodi's live bit rate (kilobits a second)."""
    value = _number(kbps)
    if value <= 0:
        return ''
    if value >= 1000:
        return '%.1f Mb/s' % (value / 1000.0)
    return '%d kb/s' % value


def size_class(width, height):
    width, height = _number(width), _number(height)
    if width >= 3000 or height >= 1700:
        return '4K'
    if width >= 1800 or height >= 1000:
        return '1080p'
    if width >= 1200 or height >= 700:
        return '720p'
    return 'SD' if width else ''


def video_name(code):
    code = str(code or '').strip().lower()
    return VIDEO_NAMES.get(code, code.upper())


def audio_name(code):
    code = str(code or '').strip().lower()
    return AUDIO_NAMES.get(code, code.upper())


def channels(count):
    count = str(count or '').strip()
    return CHANNELS.get(count, count)


def server_name(path):
    """Where it plays from, without the address: a server's name or ''."""
    path = str(path or '')
    if '://' not in path:
        return ''
    try:
        return urlsplit(path).hostname or ''
    except ValueError:
        return ''


def file_name(path):
    """The file's own name, never a query or a token folder."""
    path = str(path or '').split('|')[0].split('?')[0].rstrip('/')
    return unquote(path.rsplit('/', 1)[-1])[:60]


def container(path):
    name = file_name(path)
    return CONTAINERS.get(name.rsplit('.', 1)[-1].lower(), '') if '.' in name else ''


def language_name(code):
    code = str(code or '').strip()
    try:
        return xbmc.convertLanguage(code, xbmc.ENGLISH_NAME) or code
    except Exception:
        return code


def cores(text):
    """[(name, percent)] from System.CpuUsage ('CPU0: 12% CPU1: 40%') or one value."""
    rows = re.findall(r'CPU\s*(\d+)\s*:\s*(\d+(?:\.\d+)?)', str(text or ''))
    if rows:
        return [('#%s' % name, float(value)) for name, value in rows]
    value = _number(text)
    return [('CPU', value)] if str(text or '').strip() else []


def step(percent, top=100.0):
    return max(0, min(20, int(round(float(percent or 0) / (top or 1.0) * 20))))


def collect(label, extra=None, history=None):
    """Every value of the page from ``label(info)``: pure, so it can be tested.
    ``extra``: Kodi's audio settings and the player's tracks over JSON-RPC.
    ``history``: the last bit rates, kilobits a second."""
    extra = extra or {}
    history = history if history is not None else []
    p = lambda name: label('Player.Process(%s)' % name)
    v = lambda name: label('VideoPlayer.%s' % name)
    english = _english()
    t = lambda ar, en: en if english else ar
    path = label('Player.Filenameandpath')
    host = server_name(path)
    width, height = p('videowidth'), p('videoheight')
    size = '%s×%s' % (width, height) if width and height else ''
    klass = size_class(width, height)
    hdr_code = v('HdrType').strip().lower()
    hdr = HDR_NAMES.get(hdr_code, hdr_code.upper()) or 'SDR'
    vcodec, acodec = video_name(v('VideoCodec')), audio_name(v('AudioCodec'))
    count = str(v('AudioChannels')).strip()
    chan = channels(count)
    hardware = p('videohwdecoder').strip().lower() in ('1', 'true', 'yes')
    decoder = p('videodecoder')
    mode = t('عتادي', 'hardware') if hardware else t('برمجي', 'software')
    fps = _number(p('videofps'))
    fps_text = ('%.3f' % fps) if fps else ''
    vkbps, akbps = _number(p('videolivebitrate')), _number(p('audiolivebitrate'))
    vrate, arate = speed(vkbps), speed(akbps)
    total = speed(vkbps + akbps)
    language = v('AudioLanguage')
    cache = _number(label('Player.CacheLevel'))
    vqueue, aqueue = _number(p('videoqueuelevel')), _number(p('audioqueuelevel'))
    rate = _number(p('audiosamplerate'))
    bits = p('audiobitspersample')
    pix = p('pixformat')
    ten = bool(re.search(r'1[02]', pix or ''))
    atmos = 'atmos' in v('AudioCodec').lower() or 'atmos' in v('AudioName').lower()
    dts_x = 'dts:x' in v('AudioName').lower() or 'dtsx' in v('AudioName').lower()
    scan = p('videoscantype') or t('تدريجي', 'progressive')
    deint = p('deintmethod')
    dar = p('videodar')
    quality = '  ·  '.join(x for x in (rate and '%.1f kHz' % (rate / 1000.0), bits and '%s bit' % bits) if x)
    ahead = ''
    duration, played, cached = _clock(label('Player.Duration')), _number(label('Player.Progress')), _number(label('Player.ProgressCache'))
    if duration and cached > played:
        ahead = t('%d ث قدام', '%d s ahead') % int(duration * (cached - played) / 100.0)
    buffer_level = max(cache, vqueue)
    stream_type = t('ملف محلي', 'Local file') if not host else ('HLS' if 'm3u8' in path else t('بث عبر الشبكة', 'Network stream'))
    streams = extra.get('audiostreams') or []
    current = (extra.get('currentaudiostream') or {}).get('index', -1)
    position = next((i + 1 for i, row in enumerate(streams) if row.get('index') == current), 0)
    track = '  ·  '.join(x for x in ('%d / %d' % (position, len(streams)) if streams else '', language, acodec, chan) if x)
    subtitle = extra.get('currentsubtitle') or {}
    subs = t('مطفية', 'Off')
    if extra.get('subtitleenabled') and subtitle:
        subs = '  ·  '.join(x for x in (subtitle.get('language'), subtitle.get('name')) if x)[:44] or t('شغالة', 'On')
    usage = cores(label('System.CpuUsage'))
    average = sum(value for _name, value in usage) / len(usage) if usage else 0.0
    memory = _number(label('System.Memory(used.percent)'))
    render = _number(label('System.FPS'))
    passthrough = str(extra.get('audiooutput.passthrough')).lower() == 'true' and acodec not in ('AAC', 'MP3', 'FLAC', 'Opus', 'PCM')
    device = str(extra.get('audiooutput.audiodevice') or '').split(':')[-1].split('|')[0][:28] or t('الافتراضي', 'Default')
    lit = list(SPEAKERS.get(count, ()))
    if atmos or dts_x:
        lit += ['TFL', 'TFR', 'TRL', 'TRR']
    tiles = [(t('الصورة', 'PICTURE'), klass or '–', '  ·  '.join(x for x in (size, fps_text) if x), None),
             ('HDR', hdr, '10-bit' if ten else '8-bit', None),
             (t('الصوت', 'SOUND'), chan or '–', '  ·  '.join(x for x in (acodec, rate and '%.1f kHz' % (rate / 1000.0)) if x), None),
             (t('السرعة', 'BITRATE'), total or '–', t('الفيديو والصوت الحين', 'video and audio now'), None),
             (t('التخزين المؤقت', 'BUFFER'), '%d%%' % buffer_level, ahead, buffer_level),
             (t('الجهاز', 'SYSTEM'), 'CPU %d%%' % average, t('الذاكرة %d%%', 'memory %d%%') % memory, average)]
    source = [(t('الملف', 'File'), file_name(path)), (t('الموقع', 'Location'), host or t('هذا الجهاز', 'this device')),
              (t('الحاوية', 'Container'), container(path)), (t('السرعة', 'Bitrate'), total)]
    video = [(t('الدقة', 'Resolution'), '  ·  '.join(x for x in (size, fps_text and '%s fps' % fps_text, scan) if x)),
             ('HDR', hdr), (t('الصيغة', 'Format'), '  ·  '.join(x for x in (pix, dar) if x)),
             (t('فك الترميز', 'Decoder'), '  ·  '.join(x for x in (decoder, mode) if x)),
             (t('الشاشة', 'Display'), label('System.ScreenResolution')), (t('الطابور', 'Queue'), '%d%%' % vqueue)]
    audio = [(t('المسار', 'Track'), track), (t('الصيغة', 'Format'), '  ·  '.join(x for x in (acodec, chan) if x)),
             (t('الجودة', 'Quality'), quality), (t('فك الترميز', 'Decoder'), p('audiodecoder')),
             (t('الطابور', 'Queue'), '%d%%' % aqueue)]
    network = [(t('التخزين', 'Buffer'), '  ·  '.join(x for x in ('%d%%' % buffer_level, ahead) if x)),
               (t('النوع', 'Type'), stream_type)]
    meters = [(t('حرارة المعالج', 'CPU temperature'), label('System.CPUTemperature') or '–', _number(label('System.CPUTemperature'))),
              (t('الذاكرة', 'Memory'), '%d%%' % memory, memory),
              (t('الرسم', 'Render'), render and '%.2f fps' % render or '–', min(100.0, render / 60.0 * 100)),
              (t('حرارة الرسوميات', 'GPU temperature'), label('System.GPUTemperature') or '–', _number(label('System.GPUTemperature'))),
              (t('تردد المعالج', 'CPU clock'), label('System.CpuFrequency') or '–', 0),
              (t('طابور الفيديو', 'Video queue'), '%d%%' % vqueue, vqueue)]
    facts = [(t('النسبة', 'Aspect ratio'), dar), (t('الإطارات', 'Frame rate'), fps_text and '%s fps' % fps_text),
             (t('المسح', 'Scan'), scan), (t('إزالة التشابك', 'Deinterlace'), deint or t('ما يحتاج', 'not needed'))]
    screen = label('System.ScreenResolution').split(' ')[0]
    picture_chain = [(t('الملف', 'FILE'), vcodec, pix), (t('فك الترميز', 'DECODER'), decoder, mode),
                     (t('الصورة', 'PICTURE'), render and '%.2f fps' % render, t('رسم في الثانية', 'drawn per second')),
                     (t('التلفزيون', 'TV'), screen, '')]
    sound_chain = [(t('الملف', 'FILE'), acodec, chan and t('%s قناة', '%s channels') % count),
                   ('KODI', t('يمرر الصوت', 'passes it on') if passthrough else t('يفك الترميز', 'decodes itself'), p('audiodecoder')),
                   (t('الخرج', 'OUT'), acodec if passthrough else 'PCM', quality),
                   (t('الجهاز', 'DEVICE'), device, t('يشغّل', 'plays')),
                   (t('السماعات', 'SPEAKERS'), chan, chan and t('%s قناة', '%s channels') % count)]
    config = {'1': t('الأفضل', 'Best match'), '2': t('محدد', 'Fixed'), '3': t('الأنسب', 'Optimized')}.get(str(extra.get('audiooutput.config')), '')
    delay = label('Player.AudioDelay')
    output = [(t('الجهاز', 'Device'), device), (t('الإعداد', 'Configuration'), config),
              (t('الصوت', 'Volume'), '  ·  '.join(x for x in (label('Player.Volume'), delay) if x)), (t('طابور الصوت', 'Audio queue'), '%d%%' % aqueue)]
    tracks = []
    for row in streams:
        name = '  ·  '.join(x for x in (channels(row.get('channels')), row.get('name')) if x)[:44]
        tracks.append((language_name(row.get('language')) or t('غير محددة', 'Unknown'), audio_name(row.get('codec')), name, row.get('index') == current))
    peak = max(history) if history else 0.0
    title = v('TVShowTitle')
    if title:
        code = 'S%s E%s' % (v('Season'), v('Episode')) if v('Season') and v('Episode') else ''
        title = '  ·  '.join(x for x in (title, code, v('Title')) if x)
    else:
        title = v('Title') or file_name(path)
    time_line = ' / '.join(x for x in (label('Player.Time'), label('Player.Duration')) if x)
    chapter, chapters = int(_number(label('Player.Chapter'))), int(_number(label('Player.ChapterCount')))
    if chapters > 1 and chapter:
        time_line += t('  ·  فصل %d من %d', '  ·  chapter %d of %d') % (chapter, chapters)
    remux = 'REMUX' if 'remux' in file_name(path).lower() else ''
    badges = [klass, hdr if hdr != 'SDR' else '', vcodec, 'Atmos' if atmos else 'DTS:X' if dts_x else acodec, chan, remux]
    return {'title': title, 'time': time_line, 'badges': [b for b in badges if b], 'tiles': tiles,
            'source': source, 'video': video, 'audio': audio, 'subs': [(t('المسار', 'Track'), subs)], 'network': network,
            'cores': usage, 'core_line': t('المعالج لكل نواة · المعدل %d%%', 'CPU per core · avg. %d%%') % average, 'meters': meters,
            'facts': facts, 'picture_chain': picture_chain, 'sound_chain': sound_chain, 'output': output, 'tracks': tracks,
            'bars': [step(value, peak) for value in history], 'rate': vrate or '–', 'peak': speed(peak), 'vqueue': vqueue, 'aqueue': aqueue,
            'res': klass, 'hdr': hdr, 'hdr_kind': 'dv' if hdr_code == 'dolbyvision' else 'hdr' if hdr != 'SDR' else 'sdr',
            'gamut': 'BT.2020' if hdr != 'SDR' else 'BT.709', 'transfer': {'dolbyvision': 'PQ', 'hdr10': 'PQ', 'hdr10plus': 'PQ', 'hlg': 'HLG'}.get(hdr_code, 'SDR (gamma)'),
            'depth': ('10-bit  ·  ' + t('مليار لون', '1.07 billion colours')) if ten else ('8-bit  ·  ' + t('16.7 مليون لون', '16.7 million colours')),
            'lit': lit, 'hear': acodec or '–', 'hear_sub': '  ·  '.join(x for x in (chan, quality) if x), 'room': chan}


def _clock(text):
    parts = [int(x) for x in re.findall(r'\d+', str(text or ''))]
    seconds = 0
    for part in parts[-3:]:
        seconds = seconds * 60 + part
    return seconds


class PlayerInfoWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(PlayerInfoWindow, self).__init__(*args)
        self.closing = False
        self.thread = None
        self.tabs = []
        self.tab = 'overview'
        self.next_action = ''
        self.shown = {}
        self.props = {}
        self.history = []
        self.extra = {}
        self.tick = 0
        self.lock = threading.Lock()

    def onInit(self):
        english = _english()
        t = lambda ar, en: en if english else ar
        self.tabs = [('overview', t('نظرة عامة', 'Overview')), ('video', t('الفيديو', 'Video')), ('audio', t('الصوت', 'Audio'))]
        if xbmc.getCondVisibility('System.HasAddon(script.tinyppi)'):
            self.tabs.append(('dv', 'Dolby Vision'))
        words = {'head': t('معلومات التشغيل', 'PLAYER INFO'), 'source': t('المصدر', 'SOURCE'), 'video': t('الفيديو', 'VIDEO'),
                 'audio': t('الصوت', 'AUDIO'), 'subs': t('الترجمة', 'SUBTITLES'), 'network': t('الشبكة', 'NETWORK'), 'system': t('الجهاز', 'SYSTEM'),
                 'resolution': t('الدقة', 'RESOLUTION'), 'bright': t('السطوع', 'BRIGHTNESS'), 'colour': t('الألوان', 'COLOUR'),
                 'fromfile': t('من الملف إلى الشاشة', 'FROM FILE TO SCREEN'), 'rate': t('سرعة الصورة', 'PICTURE BITRATE'),
                 'tospeakers': t('من الملف إلى السماعات', 'FROM FILE TO YOUR SPEAKERS'), 'hear': t('اللي تسمعه', 'WHAT YOU HEAR'),
                 'output': t('الخرج', 'OUTPUT'), 'room': t('غرفتك', 'YOUR ROOM'), 'channels': t('القنوات', 'CHANNELS'),
                 'tracks': t('مسارات الصوت', 'AUDIO TRACKS'), 'playing': t('شغال', 'PLAYING'), 'sofa': t('الكنبة', 'sofa'),
                 'switch': t('يمين ويسار للتبديل', 'left / right to switch'), 'queue': t('طابور الفيديو', 'Video queue'),
                 'colours': t('الألوان:', 'Colours:'), 'transfer': t('النقل:', 'Transfer:')}
        for key, text in words.items():
            self.setProperty('pi.t.' + key, text)
        self._read_extra()
        self._fill_tabs()
        self.setFocusId(TABS)
        self.setProperty('pi.tab', self.tab)
        self._refresh()
        self.thread = threading.Thread(target=self._loop, name='DexHub-playerinfo')
        self.thread.daemon = True
        self.thread.start()

    def _fill_tabs(self):
        items = []
        for key, name in self.tabs:
            item = xbmcgui.ListItem(name, offscreen=True)
            item.setProperty('sel', '1' if key == self.tab else '')
            items.append(item)
        control = self.getControl(TABS)
        control.reset()
        control.addItems(items)

    def _rpc(self, method, params):
        try:
            answer = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
            return answer.get('result') or {}
        except (TypeError, ValueError):
            return {}

    def _read_extra(self):
        """The tracks and Kodi's audio settings: every few seconds, not every tick."""
        extra = dict(self.extra)
        for key in ('audiooutput.passthrough', 'audiooutput.config', 'audiooutput.audiodevice'):
            if key not in extra:
                extra[key] = self._rpc('Settings.GetSettingValue', {'setting': key}).get('value')
        extra.update(self._rpc('Player.GetProperties', {'playerid': 1, 'properties': [
            'audiostreams', 'currentaudiostream', 'subtitleenabled', 'currentsubtitle']}))
        self.extra = extra

    def _loop(self):
        monitor = xbmc.Monitor()
        while not self.closing and not monitor.abortRequested():
            for _tick in range(4):
                if self.closing or monitor.waitForAbort(0.25):
                    return
            if not xbmc.getCondVisibility('Player.HasVideo'):
                self.close_window()
                return
            self.tick += 1
            if self.tick % 5 == 0:
                self._read_extra()
            self._refresh()

    def _refresh(self):
        try:
            rate = _number(xbmc.getInfoLabel('Player.Process(videolivebitrate)'))
            if rate:
                self.history = (self.history + [rate])[-HISTORY:]
            data = collect(xbmc.getInfoLabel, self.extra, self.history)
        except Exception as exc:
            C.log('player info: %s' % type(exc).__name__, xbmc.LOGWARNING)
            return
        with self.lock:
            if self.closing:
                return
            self._paint(data)

    def _prop(self, key, value):
        value = '' if value is None else str(value)
        if self.props.get(key) != value:
            self.props[key] = value
            self.setProperty(key, value)

    def _paint(self, data):
        self._prop('pi.title', data['title'])
        self._prop('pi.time', data['time'])
        self._fill(BADGES, [(b, '', {}) for b in data['badges']])
        if self.tab == 'overview':
            self._fill(201, [(cap, big, {'sub': sub, 'step': '' if level is None else max(1, step(level))}) for cap, big, sub, level in data['tiles']])
            for cid, key in ((301, 'source'), (302, 'video'), (303, 'audio'), (304, 'subs'), (305, 'network')):
                self._fill(cid, [(name, value or '–', {}) for name, value in data[key]])
            self._fill(306, [(name, '', {'step': step(value)}) for name, value in data['cores']])
            self._prop('pi.cores', data['core_line'])
            self._fill(307, [(name, value, {'step': max(1, step(level)) if level else ''}) for name, value, level in data['meters']])
        elif self.tab == 'video':
            self._fill(311, [(name, value or '–', {}) for name, value in data['facts']])
            self._fill(312, self._chain(data['picture_chain']))
            self._fill(313, [(str(n), '', {'step': value}) for n, value in enumerate(data['bars'])])
            for key in ('res', 'hdr', 'hdr_kind', 'gamut', 'transfer', 'depth', 'rate', 'peak'):
                self._prop('pi.' + key, data[key])
            self._prop('pi.vq', '%d%%' % data['vqueue'])
            self._prop('pi.vq.step', max(1, step(data['vqueue'])))
        else:
            self._fill(321, self._chain(data['sound_chain']))
            self._fill(322, [(name, value or '–', {}) for name, value in data['output']])
            lit = data['lit']
            self._fill(323, [(name, '', {'step': random.randint(5, 19) if name in lit else 0, 'top': '1' if name[0] == 'T' else ''}) for name in METERS])
            self._fill(324, [(lang, codec, {'sub': sub, 'now': '1' if now else ''}) for lang, codec, sub, now in data['tracks']])
            self._prop('pi.lit', '/%s/' % '/'.join(lit))      # a | would be an OR in the skin's condition
            for key in ('hear', 'hear_sub', 'room'):
                self._prop('pi.' + key, data[key])

    @staticmethod
    def _chain(steps):
        return [(cap, big or '–', {'sub': sub, 'more': '1' if index + 1 < len(steps) else ''})
                for index, (cap, big, sub) in enumerate(steps)]

    def _fill(self, cid, rows):
        """The same names: only the values change. Else the list is rebuilt."""
        control = self.getControl(cid)
        names = [row[0] for row in rows]
        if self.shown.get(cid) == names:
            for index, row in enumerate(rows):
                item = control.getListItem(index)
                if item.getLabel2() != row[1]:
                    item.setLabel2(row[1])
                for key, value in row[2].items():
                    item.setProperty(key, str(value))
            return
        items = []
        for name, value, props in rows:
            item = xbmcgui.ListItem(name, value, offscreen=True)
            for key, text in props.items():
                item.setProperty(key, str(text))
            items.append(item)
        control.reset()
        control.addItems(items)
        self.shown[cid] = names

    def _pick(self, pos):
        if not 0 <= pos < len(self.tabs) or self.tabs[pos][0] == 'dv' or self.tabs[pos][0] == self.tab:
            return
        self.tab = self.tabs[pos][0]
        with self.lock:
            # in place: a list rebuilt under the cursor lost its position
            control = self.getControl(TABS)
            for index, (key, _name) in enumerate(self.tabs):
                control.getListItem(index).setProperty('sel', '1' if key == self.tab else '')
            self.setProperty('pi.tab', self.tab)
        self._refresh()

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK or aid in A_CLOSE:
            self.close_window()
        elif aid in (A_LEFT, A_RIGHT) and self.getFocusId() == TABS:
            self._pick(self.getControl(TABS).getSelectedPosition())

    def onClick(self, cid):
        if cid != TABS:
            return
        pos = self.getControl(TABS).getSelectedPosition()
        if 0 <= pos < len(self.tabs) and self.tabs[pos][0] == 'dv':
            self.next_action = 'RunScript(script.tinyppi,dialog)'
            self.close_window()
        else:
            self._pick(pos)

    def close_window(self):
        self.closing = True
        self.close()


def open_window(params=None):
    import xbmcaddon
    try:
        from .. import skin_theme
        skin_theme.ensure_theme()
    except Exception:
        pass
    addon = xbmcaddon.Addon(C.ADDON_ID)
    window = PlayerInfoWindow('dexhub_playerinfo.xml', addon.getAddonInfo('path'), 'Default', '1080i')
    window.doModal()
    window.closing = True
    action = window.next_action
    if window.thread is not None:
        window.thread.join(2)
    del window
    if action:
        xbmc.executebuiltin(action)
    return None
