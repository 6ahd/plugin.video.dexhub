# -*- coding: utf-8 -*-
"""The player's quick lists (v5.10.317): what a row of the Subtitles and
Audio panels shows (skin.dexhub Custom_1146 / Custom_1147, the list that
opens when the button gets the focus).

Each track gets the language's flag, the language's name, the track's full
name, a line of details (source, provider, default), and up to three badges
drawn like the skin's codec flags: for audio the channels (5.1, 7.1) and the
codec (DD+, Atmos, TrueHD, DTS-HD MA), for subtitles the format (SRT, ASS,
PGS), SDH and FORCED.

Properties: dh_flag, dh_lang, dh_title, dh_tags, dh_b1, dh_b2, dh_b3.
"""
import re

FLAG_DIR = 'af3/flags/color/language/'
FLAGS = frozenset((
    'alb', 'ar', 'ara', 'arm', 'baq', 'bg', 'bos', 'bs', 'bul', 'cat', 'chi', 'cro', 'cs', 'cze', 'da', 'dan', 'de',
    'deu', 'dut', 'el', 'ell', 'en', 'eng', 'es', 'est', 'et', 'fa', 'fas', 'fi', 'fil', 'fin', 'fo', 'fr', 'fra',
    'fre', 'ger', 'gre', 'he', 'heb', 'hi', 'hin', 'hr', 'hrv', 'hu', 'hun', 'hy', 'hye', 'ice', 'id', 'ind', 'is',
    'isl', 'it', 'ita', 'ja', 'jpn', 'kaz', 'kk', 'ko', 'kor', 'lav', 'lb', 'lit', 'lt', 'ltz', 'lv', 'mac', 'mal',
    'may', 'mk', 'mkd', 'ms', 'msa', 'nl', 'nld', 'no', 'nob', 'nor', 'per', 'pl', 'pol', 'por', 'pt', 'pt-br', 'ro',
    'ron', 'ru', 'rum', 'rus', 'sk', 'sl', 'slk', 'slo', 'slv', 'spa', 'sq', 'sqi', 'sr', 'srp', 'sv', 'swe', 'tam',
    'tel', 'th', 'tha', 'tr', 'tur', 'uk', 'ukr', 'vi', 'vie', 'zh', 'zho'))
AUDIO_DIR = 'af3/flags/colorful/audio/'
AUDIO = frozenset((
    'aac', 'ac3', 'alac', 'ape', 'atmos', 'dca', 'dts', 'dts_x', 'dtshd_hra', 'dtshd_ma', 'dtshd_ma_x', 'eac3',
    'eac3_ddp_atmos', 'flac', 'mp2', 'mp3', 'mlp', 'ogg', 'opus', 'pcm', 'truehd', 'truehd_atmos', 'vorbis', 'wav',
    'wma', 'wmapro'))
CHANNELS_DIR = 'af3/flags/colorful/channels/'
BADGES = 'dexhub/badges/'
_SUB_FORMATS = (('subrip', 'srt'), ('srt', 'srt'), ('ass', 'ass'), ('ssa', 'ssa'), ('webvtt', 'vtt'), ('vtt', 'vtt'),
                ('pgs', 'pgs'), ('hdmv', 'pgs'), ('sup', 'sup'), ('dvd_sub', 'vob'), ('dvdsub', 'vob'),
                ('vobsub', 'vob'), ('dvb', 'dvb'), ('ttml', 'ttml'), ('mov_text', 'txt'), ('text', 'txt'))
_PROVIDER = re.compile(r'[\[(]([^\[\]()]{2,40})[\])]\s*(?:\.[a-z0-9]{2,4})?\s*$', re.I)


def _t(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


# v5.10.318: Arabic shows the Saudi flag (AF3's set has the UAE's)
OWN_FLAGS = {'ar': 'dexhub/flags/ar.png'}

# a track's language as its name says it (Dex Hub names the files it adds
# "<name> - <flag> <Language> - [<source>]"; Kodi's own guess from such a
# name can be another language)
_COUNTRY = {'sa': 'ar', 'ae': 'ar', 'eg': 'ar', 'kw': 'ar', 'qa': 'ar', 'bh': 'ar', 'om': 'ar', 'jo': 'ar', 'lb': 'ar',
            'sy': 'ar', 'iq': 'ar', 'ma': 'ar', 'dz': 'ar', 'tn': 'ar', 'ly': 'ar', 'ye': 'ar', 'sd': 'ar', 'ps': 'ar',
            'gb': 'en', 'us': 'en', 'uk': 'en', 'au': 'en', 'ca': 'en', 'fr': 'fr', 'es': 'es', 'mx': 'es', 'de': 'de',
            'it': 'it', 'pt': 'pt', 'br': 'pt-br', 'tr': 'tr', 'ru': 'ru', 'jp': 'ja', 'kr': 'ko', 'cn': 'zh', 'tw': 'zh',
            'in': 'hi', 'ir': 'fa', 'nl': 'nl', 'se': 'sv', 'no': 'no', 'dk': 'da', 'fi': 'fi', 'pl': 'pl', 'gr': 'el',
            'il': 'he', 'id': 'id', 'my': 'ms', 'th': 'th', 'vn': 'vi', 'ro': 'ro', 'hu': 'hu', 'cz': 'cs', 'ua': 'uk'}
_NAMES = {'arabic': 'ar', 'العربية': 'ar', 'عربي': 'ar', 'عربية': 'ar', 'english': 'en', 'french': 'fr',
          'spanish': 'es', 'german': 'de', 'italian': 'it', 'portuguese': 'pt', 'brazilian': 'pt-br', 'turkish': 'tr',
          'russian': 'ru', 'japanese': 'ja', 'korean': 'ko', 'chinese': 'zh', 'hindi': 'hi', 'persian': 'fa',
          'farsi': 'fa', 'dutch': 'nl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da', 'finnish': 'fi',
          'polish': 'pl', 'greek': 'el', 'hebrew': 'he', 'indonesian': 'id', 'malay': 'ms', 'thai': 'th',
          'vietnamese': 'vi', 'romanian': 'ro', 'hungarian': 'hu', 'czech': 'cs', 'ukrainian': 'uk', 'urdu': 'ur'}
_FLAG_PAIR = re.compile(u'([\U0001F1E6-\U0001F1FF])([\U0001F1E6-\U0001F1FF])')
_WORD = re.compile(r'(?i)(?<![a-z\u0600-\u06ff])(%s)(?![a-z\u0600-\u06ff])' % '|'.join(
    re.escape(k) for k in sorted(_NAMES, key=len, reverse=True)))


def name_lang(text):
    """The language a track's name gives, the last one said ('' if none)."""
    text = str(text or '')
    found = ''
    at = -1
    for m in _FLAG_PAIR.finditer(text):
        country = ''.join(chr(ord(c) - 0x1F1E6 + 97) for c in m.groups())
        if country in _COUNTRY and m.start() > at:
            found, at = _COUNTRY[country], m.start()
    for m in _WORD.finditer(text):
        if m.start() > at:
            found, at = _NAMES[m.group(1).lower()], m.start()
    return found


def _known(stream):
    """The add-on's name, source and language of a file Dex Hub added."""
    try:
        from ..playback import subtitle_names
        return subtitle_names.lookup(stream.get('name'))
    except Exception:
        return None


def track_code(stream):
    """A subtitle's language: what its add-on said for a file Dex Hub added,
    an external file by its name, then Kodi's."""
    from . import subwindow as W
    raw = str(stream.get('name') or '')
    known = _known(stream)
    if known and known.get('l') and known['l'] != 'und':
        return W._code(known['l'])
    external = 'External' in raw or 'خارجية' in raw or bool(re.search(r'\.(srt|ass|ssa|vtt|sub|sup)\b', raw, re.I))
    if external:
        hint = name_lang(raw)
        if hint:
            return hint
    code = W._code(stream.get('language'))
    if code == 'und':
        code = name_lang(raw) or 'und'
    return code


def _flag(stream, code):
    if code in OWN_FLAGS:
        return OWN_FLAGS[code]
    raw = str(stream.get('language') or '').strip().lower()
    for key in (code, raw, raw[:3]):
        if key and key in FLAGS:
            return FLAG_DIR + key + '.png'
    return FLAG_DIR + 'unk.png'


def _english_name(code):
    try:
        import xbmc
        return xbmc.convertLanguage(code, xbmc.ENGLISH_NAME) or ''
    except Exception:
        return ''


def _badge(key):
    return BADGES + key + '.png' if key else ''


def subtitle(stream):
    from . import subwindow as W
    raw = str(stream.get('name') or '')
    code = track_code(stream)
    known = _known(stream)
    if known:
        return _known_row(stream, code, known)
    bare = re.sub(r'\s*\((?:External|خارجية)\)\s*$', '', raw)
    external = bare != raw or bool(re.search(r'\.(srt|ass|ssa|vtt|sub|sup)\b', raw, re.I))
    provider = _PROVIDER.search(bare) if external else None
    title = W._clean_name(bare[:provider.start()] if provider else bare)
    lang = W._lang_label(code) if code != 'und' else ''
    # the language is the row's first word already
    for word in {lang, _english_name(code)}:
        if word:
            title = re.sub(r'(?i)(^|[\s\-\u00b7|]+)%s(?=$|[\s\-\u00b7|]+)' % re.escape(word), ' ', title)
    title = re.sub(r'\s*[-\u00b7|]\s*(?=[-\u00b7|]|$)', '', title).strip(' -\u00b7|')
    if title and title.strip().lower() in (lang.lower(), code, str(stream.get('language') or '').lower()):
        title = ''
    external = 'External' in raw or 'خارجية' in raw or bool(re.search(r'\.(srt|ass|ssa|vtt|sub|sup)\b', raw, re.I))
    fmt = ''
    hint = ('%s %s' % (stream.get('codec') or '', raw)).lower()
    for mark, key in _SUB_FORMATS:
        if mark in hint:
            fmt = key
            break
    if not fmt and external:
        fmt = 'srt'
    tags = []
    if provider and external:
        tags.append(provider.group(1).strip())
    tags.append(_t('خارجية', 'External') if external else _t('مضمّنة', 'Embedded'))
    if stream.get('isdefault'):
        tags.append(_t('افتراضية', 'Default'))
    return {'dh_flag': _flag(stream, code), 'dh_lang': lang or _t('لغة غير معروفة', 'Unknown language'),
            'dh_title': title, 'dh_tags': '  ·  '.join(tags), 'dh_b1': _badge(fmt),
            'dh_b2': _badge('sdh') if stream.get('isimpaired') else '',
            'dh_b3': _badge('forced') if stream.get('isforced') else ''}


def _known_row(stream, code, known):
    from . import subwindow as W
    lang = W._lang_label(code) if code != 'und' else _t('لغة غير معروفة', 'Unknown language')
    name = re.sub(u'[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]', '', known.get('n') or '')
    name = re.sub(r'\s+', ' ', name).strip(' -\u00b7|')
    source = (known.get('s') or '').strip()
    title = name or source
    raw = str(stream.get('name') or '')
    fmt = ''
    for mark, key in _SUB_FORMATS:
        if mark in raw.lower():
            fmt = key
            break
    tags = [x for x in (source if source and source != title else '', _t('خارجية', 'External')) if x]
    if stream.get('isdefault'):
        tags.append(_t('افتراضية', 'Default'))
    return {'dh_flag': _flag(stream, code), 'dh_lang': lang, 'dh_title': title,
            'dh_tags': '  \u00b7  '.join(tags), 'dh_b1': _badge(fmt or 'srt'),
            'dh_b2': _badge('sdh') if stream.get('isimpaired') else '',
            'dh_b3': _badge('forced') if stream.get('isforced') else ''}


def _codec_key(codec, name):
    codec = str(codec or '').lower().replace('-', '_')
    text = str(name or '').lower()
    atmos = 'atmos' in text or 'atmos' in codec
    if codec in AUDIO:
        return codec
    if codec in ('eac3', 'e_ac3', 'ec_3', 'ddp'):
        key = 'eac3_ddp_atmos' if atmos else 'eac3'
    elif codec in ('truehd', 'mlp'):
        key = 'truehd_atmos' if atmos else 'truehd'
    elif codec in ('dtshd_ma', 'dts_hd_ma', 'dtsma', 'dtshdma'):
        key = 'dtshd_ma_x' if ('dts:x' in text or 'dts-x' in text) else 'dtshd_ma'
    elif codec in ('dca', 'dts'):
        key = 'dts_x' if ('dts:x' in text or 'dts-x' in text) else 'dts'
    elif codec.startswith('pcm'):
        key = 'pcm'
    elif codec.startswith('aac'):
        key = 'aac'
    else:
        key = codec
    return key if key in AUDIO else ''


_CODEC_NAMES = {'eac3': 'Dolby Digital Plus', 'ac3': 'Dolby Digital', 'truehd': 'Dolby TrueHD', 'dca': 'DTS',
                'dts': 'DTS', 'dtshd_ma': 'DTS-HD MA', 'dtshd_hra': 'DTS-HD HRA', 'aac': 'AAC', 'flac': 'FLAC',
                'opus': 'Opus', 'mp3': 'MP3', 'pcm': 'PCM', 'vorbis': 'Vorbis'}


def audio(stream):
    from . import subwindow as W
    code = W._code(stream.get('language'))
    name = str(stream.get('name') or '').strip()
    lang = W._lang_label(code) if code != 'und' else _t('لغة غير معروفة', 'Unknown language')
    codec = str(stream.get('codec') or '').lower()
    try:
        channels = int(stream.get('channels') or 0)
    except (TypeError, ValueError):
        channels = 0
    tags = []
    pretty = _CODEC_NAMES.get(codec) or _CODEC_NAMES.get(codec.split('_')[0]) or codec.upper()
    if pretty:
        tags.append(pretty + (' Atmos' if ('atmos' in name.lower() or 'atmos' in codec) and 'atmos' not in pretty.lower() else ''))
    if channels:
        tags.append({1: 'Mono', 2: _t('ستيريو', 'Stereo'), 6: '5.1', 8: '7.1'}.get(channels, '%d ch' % channels))
    try:
        bitrate = int(stream.get('bitrate') or 0)
    except (TypeError, ValueError):
        bitrate = 0
    if bitrate > 1000:
        tags.append('%d kbps' % (bitrate // 1000))
    if stream.get('isdefault'):
        tags.append(_t('افتراضي', 'Default'))
    if stream.get('isoriginal'):
        tags.append(_t('الأصلي', 'Original'))
    ch = str(channels) if 0 < channels <= 10 else ''
    return {'dh_flag': _flag(stream, code), 'dh_lang': lang, 'dh_title': name if name.lower() != lang.lower() else '',
            'dh_tags': '  ·  '.join(tags), 'dh_b1': CHANNELS_DIR + ch + '.png' if ch else '',
            'dh_b2': AUDIO_DIR + _codec_key(codec, name) + '.png' if _codec_key(codec, name) else '', 'dh_b3': ''}


def props(kind, stream):
    try:
        return audio(stream) if kind == 'audio' else subtitle(stream)
    except Exception:
        return {}


_SHORT = {'eac3': 'DD+', 'ac3': 'DD', 'truehd': 'TrueHD', 'mlp': 'TrueHD', 'dtshd': 'DTS-HD', 'dca': 'DTS', 'dts': 'DTS',
          'aac': 'AAC', 'flac': 'FLAC', 'opus': 'Opus', 'mp3': 'MP3', 'pcm': 'PCM', 'vorbis': 'Vorbis'}


def short_audio(stream):
    """'English · DD+ Atmos 5.1' for the audio page's track row."""
    from . import subwindow as W
    codec = str(stream.get('codec') or '').lower()
    name = str(stream.get('name') or '').lower()
    head = codec.split('_')[0]
    short = _SHORT.get(head) or head.upper()
    if codec.startswith('dtshd_ma'):
        short = 'DTS-HD MA'
    if 'atmos' in codec or 'atmos' in name:
        short += ' Atmos'
    try:
        channels = int(stream.get('channels') or 0)
    except (TypeError, ValueError):
        channels = 0
    ch = {1: '1.0', 2: '2.0', 6: '5.1', 8: '7.1'}.get(channels, '')
    lang = W._lang_label(W._code(stream.get('language')))
    return ' \u00b7 '.join(x for x in (lang, ' '.join(x for x in (short, ch) if x)) if x)
