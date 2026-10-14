# -*- coding: utf-8 -*-
"""The details of one source, written for people (v5.10.104).

The sources window's Info dialog used to print the raw stream object:
``name:``, ``description:`` and a JSON dump of ``behaviorHints`` (the
bingeGroup included), with the provider description repeated twice and
every decimal point lost to the filename cleaner ("26 2 GB 27 3 Mbps").

This module reads the same row once and writes what a person choosing a
copy wants to know, one labelled fact per line:

    The.Uprising.2026.MULTI.DV.2160p.WEB.H265-LOST

    Quality:        4K (2160p) • WEB-DL
    Video:          Dolby Vision • HEVC (H.265)
    Languages:      Multi
    Size:           24.44 GB
    Bitrate:        27.3 Mbps
    Posted:         15 hours ago
    Release group:  LOST

    Type:           Usenet
    Status:         Ready to play
    Indexer:        altHUB
    Provider:       AIOStreams

The release name is read first (it is the most reliable record of what the
file is), then the structured fields an aggregator sends (AIOStreams'
bingeGroup, Dex Hub's labelled formatter lines), then the provider's own
description, whose numbers keep their decimals here.

Pure Python apart from the language switch: no Kodi calls, so it runs the
same in the plugin and in the player service.
"""
import html
import re

try:
    from .i18n import is_english as _is_english
except Exception:       # a test run without Kodi
    def _is_english():
        return True


# ---------------------------------------------------------------- text ----

# Emoji and pictographs (and what they leave behind: variation selectors,
# joiners, keycaps). Kodi's fonts draw none of them.
_EMOJI_RE = re.compile(
    '[\u200b-\u200f\u2060-\u2064\u20e3\ufe00-\ufe0f'
    '\u2300-\u23ff\u2460-\u24ff\u25a0-\u27bf\u2900-\u297f\u2b00-\u2bff'
    '\ue000-\uf8ff\U0001f000-\U0001faff\U000e0000-\U000e007f]')
_FLAG_RE = re.compile('[\U0001f1e6-\U0001f1ff]{2}')
_MARKUP_RE = re.compile(r'\[/?(?:COLOR|B|I|UPPERCASE|LOWERCASE|LIGHT|CAPITALIZE)\b[^\]]*\]', re.I)
_SPACES_RE = re.compile(r'[ \t\xa0]+')


def plain(value):
    """Provider text without emoji or Kodi markup; punctuation and decimals kept."""
    text = html.unescape(str(value or ''))
    text = _MARKUP_RE.sub(' ', text)
    text = _FLAG_RE.sub(' ', text)
    text = _EMOJI_RE.sub(' ', text)
    lines = []
    for line in text.replace('\r', '').split('\n'):
        line = _SPACES_RE.sub(' ', line).strip(' |\u2022\u00b7-')
        if line:
            lines.append(line)
    return '\n'.join(lines)



# ------------------------------------------------------------- labels ----

_LABELS = {
    'quality': ('الجودة', 'Quality'),
    'video': ('الفيديو', 'Video'),
    'audio': ('الصوت', 'Audio'),
    'languages': ('اللغات', 'Languages'),
    'subtitles': ('الترجمات', 'Subtitles'),
    'edition': ('النسخة', 'Edition'),
    'network': ('منصة البث', 'Streaming service'),
    'size': ('الحجم', 'Size'),
    'bitrate': ('معدل البت', 'Bitrate'),
    'posted': ('تاريخ النشر', 'Posted'),
    'group': ('مجموعة الإصدار', 'Release group'),
    'type': ('النوع', 'Type'),
    'status': ('الحالة', 'Status'),
    'indexer': ('المفهرس', 'Indexer'),
    'service': ('الخدمة', 'Service'),
    'seeders': ('المشاركون', 'Seeders'),
    'site': ('الموقع', 'Site'),
    'addon': ('الإضافة', 'Add-on'),
    'provider': ('المزود', 'Provider'),
}

_TYPES = {
    'usenet': ('Usenet', 'Usenet'),
    'debrid': ('Debrid', 'Debrid'),
    'torrent': ('تورنت (P2P)', 'Torrent (P2P)'),
    'direct': ('رابط مباشر', 'Direct link'),
    'plex': ('خادم Plex', 'Plex server'),
    'emby': ('خادم Emby', 'Emby server'),
    'silo': ('Silo', 'Silo'),
}

_READY = ('جاهز للتشغيل', 'Ready to play')
_NOT_READY = ('غير جاهز بعد', 'Not ready yet')

_LANGS = {
    'multi': ('متعددة', 'Multi'),
    'dual': ('صوت مزدوج', 'Dual audio'),
    'arabic': ('العربية', 'Arabic'),
    'english': ('الإنجليزية', 'English'),
    'french': ('الفرنسية', 'French'),
    'spanish': ('الإسبانية', 'Spanish'),
    'german': ('الألمانية', 'German'),
    'italian': ('الإيطالية', 'Italian'),
    'russian': ('الروسية', 'Russian'),
    'hindi': ('الهندية', 'Hindi'),
    'japanese': ('اليابانية', 'Japanese'),
    'korean': ('الكورية', 'Korean'),
    'turkish': ('التركية', 'Turkish'),
    'portuguese': ('البرتغالية', 'Portuguese'),
    'chinese': ('الصينية', 'Chinese'),
    'persian': ('الفارسية', 'Persian'),
    'urdu': ('الأردية', 'Urdu'),
    'hebrew': ('العبرية', 'Hebrew'),
    'polish': ('البولندية', 'Polish'),
    'dutch': ('الهولندية', 'Dutch'),
    'thai': ('التايلاندية', 'Thai'),
    'indonesian': ('الإندونيسية', 'Indonesian'),
    'malay': ('الملايوية', 'Malay'),
    'vietnamese': ('الفيتنامية', 'Vietnamese'),
    'tamil': ('التاميلية', 'Tamil'),
    'telugu': ('التيلوغوية', 'Telugu'),
}
_LANG_WORDS = (
    (r'multi(?:[ -]?(?:audio|lang(?:uages?)?))?', 'multi'),
    (r'dual(?:[ -]?audio)?', 'dual'),
    (r'arabic|arab', 'arabic'),
    (r'english', 'english'),
    (r'french|truefrench|vff|vfq|vf2', 'french'),
    (r'spanish|castellano|latino|espanol', 'spanish'),
    (r'german|deutsch', 'german'),
    (r'italian|italiano', 'italian'),
    (r'russian', 'russian'),
    (r'hindi', 'hindi'),
    (r'japanese', 'japanese'),
    (r'korean', 'korean'),
    (r'turkish', 'turkish'),
    (r'portuguese|portugues', 'portuguese'),
    (r'chinese|mandarin|cantonese', 'chinese'),
    (r'persian|farsi', 'persian'),
    (r'urdu', 'urdu'),
    (r'hebrew', 'hebrew'),
    (r'polish', 'polish'),
    (r'dutch', 'dutch'),
    (r'thai', 'thai'),
    (r'indonesian', 'indonesian'),
    (r'malay', 'malay'),
    (r'vietnamese', 'vietnamese'),
    (r'tamil', 'tamil'),
    (r'telugu', 'telugu'),
)
_LANG_RES = tuple((re.compile(r'(?<![a-z])(?:%s)(?![a-z])' % patt), key) for patt, key in _LANG_WORDS)
# country flags an aggregator prints for its languages
_FLAG_LANGS = {
    'GB': 'english', 'US': 'english', 'SA': 'arabic', 'AE': 'arabic', 'EG': 'arabic',
    'FR': 'french', 'ES': 'spanish', 'MX': 'spanish', 'DE': 'german', 'IT': 'italian',
    'RU': 'russian', 'IN': 'hindi', 'JP': 'japanese', 'KR': 'korean', 'TR': 'turkish',
    'PT': 'portuguese', 'BR': 'portuguese', 'CN': 'chinese', 'TW': 'chinese',
    'HK': 'chinese', 'IR': 'persian', 'IL': 'hebrew', 'PL': 'polish', 'NL': 'dutch',
    'TH': 'thai', 'ID': 'indonesian', 'VN': 'vietnamese',
}

_NETWORKS = {
    'NF': 'Netflix', 'AMZN': 'Amazon Prime Video', 'ATVP': 'Apple TV+', 'DSNP': 'Disney+',
    'HMAX': 'HBO Max', 'MAX': 'Max', 'HULU': 'Hulu', 'PCOK': 'Peacock', 'PMTP': 'Paramount+',
    'CRAV': 'Crave', 'STAN': 'Stan', 'SHO': 'Showtime', 'STARZ': 'Starz', 'iT': 'iTunes',
    'ROKU': 'Roku', 'SHAHID': 'Shahid', 'OSN': 'OSN+', 'CR': 'Crunchyroll', 'VIU': 'Viu',
}

_SERVICES = {
    'RD': 'Real-Debrid', 'REALDEBRID': 'Real-Debrid', 'REAL-DEBRID': 'Real-Debrid',
    'AD': 'AllDebrid', 'ALLDEBRID': 'AllDebrid', 'PM': 'Premiumize', 'PREMIUMIZE': 'Premiumize',
    'TB': 'TorBox', 'TORBOX': 'TorBox', 'ED': 'EasyDebrid', 'EASYDEBRID': 'EasyDebrid',
    'OC': 'Offcloud', 'OFFCLOUD': 'Offcloud', 'DL': 'Debrid-Link', 'DEBRIDLINK': 'Debrid-Link',
    'PKP': 'PikPak', 'PIKPAK': 'PikPak', 'SEEDR': 'Seedr', 'EN': 'Easynews',
    'EASYNEWS': 'Easynews', 'NZBDAV': 'NzbDAV', 'ALTMOUNT': 'AltMount',
    'STREMTHRU': 'StremThru', 'PUTIO': 'put.io',
}

_RESOLUTIONS = {
    '4320p': '8K (4320p)', '2160p': '4K (2160p)', '1440p': 'QHD (1440p)',
    '1080p': 'Full HD (1080p)', '1080i': 'Full HD (1080i)', '720p': 'HD (720p)',
    '576p': 'SD (576p)', '540p': 'SD (540p)', '480p': 'SD (480p)', '360p': 'SD (360p)',
}

_HDR_NAMES = {'DV': 'Dolby Vision', 'HDR10+': 'HDR10+', 'HDR10': 'HDR10', 'HDR': 'HDR',
              'HLG': 'HLG', 'SDR': 'SDR'}
_CODEC_NAMES = {'HEVC': 'HEVC (H.265)', 'AVC': 'AVC (H.264)', 'AV1': 'AV1', 'VP9': 'VP9',
                'MPEG-2': 'MPEG-2', 'XVID': 'XviD', 'DIVX': 'DivX'}
_AUDIO_NAMES = {'TRUEHD': 'Dolby TrueHD', 'DD+': 'Dolby Digital Plus', 'DD': 'Dolby Digital',
                'DTS-HD MA': 'DTS-HD MA', 'DTS:X': 'DTS:X', 'DTS-HD': 'DTS-HD', 'DTS': 'DTS',
                'AAC': 'AAC', 'FLAC': 'FLAC', 'OPUS': 'Opus', 'LPCM': 'LPCM', 'MP3': 'MP3'}


def _ui(pair):
    return pair[1] if _is_english() else pair[0]


# -------------------------------------------------------- release name ----

_SOURCES = (
    (r'remux', 'REMUX'),
    (r'blu[ -]?ray|bdrip|brrip|bd(?:25|50|66|100)|uhd[ -]?bd', 'BluRay'),
    (r'web[ -]?dl|webdl', 'WEB-DL'),
    (r'web[ -]?rip|webrip', 'WEBRip'),
    (r'web', 'WEB'),
    (r'hdtv|pdtv|dsr', 'HDTV'),
    (r'dvd[ -]?rip', 'DVDRip'),
    (r'dvd(?:5|9)?|dvdr', 'DVD'),
    (r'hd[ -]?rip', 'HDRip'),
    (r'(?:hd)?cam(?:rip)?', 'CAM'),
    (r'(?:hd)?ts|telesync', 'TS'),
    (r'(?:hd)?tc|telecine', 'TC'),
    (r'(?:dvd)?scr|screener', 'SCR'),
)
_SOURCE_RES = tuple((re.compile(r'(?<![a-z0-9])(?:%s)(?![a-z0-9])' % p), v) for p, v in _SOURCES)
_RES_RE = re.compile(r'(?<![a-z0-9])(4320p|2160p|1440p|1080p|1080i|720p|576p|540p|480p|360p|8k|4k|uhd)(?![a-z0-9])')
_HDR_RES = (
    (re.compile(r'(?<![a-z0-9])(?:dv|dovi|dolby ?vision)(?![a-z0-9])'), 'DV'),
    (re.compile(r'(?<![a-z0-9])hdr ?10 ?(?:\+|plus)'), 'HDR10+'),
    (re.compile(r'(?<![a-z0-9])hdr ?10(?![0-9+]| ?plus)'), 'HDR10'),
    (re.compile(r'(?<![a-z0-9])hdr(?![a-z0-9])'), 'HDR'),
    (re.compile(r'(?<![a-z0-9])hlg(?![a-z0-9])'), 'HLG'),
    (re.compile(r'(?<![a-z0-9])sdr(?![a-z0-9])'), 'SDR'),
)
_CODEC_RES = (
    (re.compile(r'(?<![a-z0-9])(?:hevc|[hx] ?265)(?![0-9])'), 'HEVC'),
    (re.compile(r'(?<![a-z0-9])(?:avc|[hx] ?264)(?![0-9])'), 'AVC'),
    (re.compile(r'(?<![a-z0-9])av1(?![a-z0-9])'), 'AV1'),
    (re.compile(r'(?<![a-z0-9])vp9(?![a-z0-9])'), 'VP9'),
    (re.compile(r'(?<![a-z0-9])mpeg ?2(?![a-z0-9])'), 'MPEG-2'),
    (re.compile(r'(?<![a-z0-9])xvid(?![a-z0-9])'), 'XVID'),
    (re.compile(r'(?<![a-z0-9])divx(?![a-z0-9])'), 'DIVX'),
)
_BITDEPTH_RE = re.compile(r'(?<![a-z0-9])(8|10|12) ?bits?(?![a-z0-9])')
_AUDIO_RES = (
    (re.compile(r'(?<![a-z0-9])true ?hd(?![a-z])'), 'TRUEHD'),
    (re.compile(r'(?<![a-z0-9])dts ?x(?![a-z0-9])'), 'DTS:X'),
    (re.compile(r'(?<![a-z0-9])dts ?hd ?ma(?![a-z])|(?<![a-z0-9])dts ?hd ?master(?![a-z])'), 'DTS-HD MA'),
    (re.compile(r'(?<![a-z0-9])dts ?hd(?![a-z])'), 'DTS-HD'),
    (re.compile(r'(?<![a-z0-9])dts(?![a-z])'), 'DTS'),
    (re.compile(r'(?<![a-z0-9])(?:ddp|dd\+|e ?ac ?3|dolby digital plus)(?![a-z])'), 'DD+'),
    (re.compile(r'(?<![a-z0-9])(?:dd|ac ?3|dolby digital)(?![a-z+])'), 'DD'),
    (re.compile(r'(?<![a-z0-9])aac(?![a-z])'), 'AAC'),
    (re.compile(r'(?<![a-z0-9])flac(?![a-z])'), 'FLAC'),
    (re.compile(r'(?<![a-z0-9])opus(?![a-z])'), 'OPUS'),
    (re.compile(r'(?<![a-z0-9])l?pcm(?![a-z])'), 'LPCM'),
    (re.compile(r'(?<![a-z0-9])mp3(?![a-z0-9])'), 'MP3'),
)
_ATMOS_RE = re.compile(r'(?<![a-z0-9])atmos(?![a-z])')
_CHANNELS_AFTER_RE = re.compile(
    r'(?:ddp|dd\+?|e ?ac ?3|ac ?3|aac|dts(?: ?hd)?(?: ?ma)?|dts ?x|true ?hd|atmos|flac|opus|l?pcm)'
    r' ?([1-9]) ?[ .]? ?([0-2])(?![0-9])')
_CHANNELS_RE = re.compile(r'(?<![0-9.])([57])[ .]1(?![0-9])')
_CHANNELS_CH_RE = re.compile(r'(?<![a-z0-9])(2|6|8) ?ch(?![a-z])')
_EDITIONS = (
    (r'imax(?: ?enhanced)?', 'IMAX'),
    (r'extended(?: ?(?:cut|edition))?', 'Extended'),
    (r'directors? ?cut', "Director's Cut"),
    (r'remastered', 'Remastered'),
    (r'unrated', 'Unrated'),
    (r'uncut', 'Uncut'),
    (r'theatrical', 'Theatrical'),
    (r'criterion', 'Criterion'),
    (r'open ?matte', 'Open Matte'),
    (r'hybrid', 'Hybrid'),
    (r'repack\d?|rerip', 'REPACK'),
    (r'proper', 'PROPER'),
)
_EDITION_RES = tuple((re.compile(r'(?<![a-z0-9])(?:%s)(?![a-z])' % p), v) for p, v in _EDITIONS)
_3D_RE = re.compile(r'(?<![A-Za-z0-9])3D(?![A-Za-z0-9])')
_EXT_RE = re.compile(r'\.(?:mkv|mp4|m4v|avi|ts|m2ts|webm|mov|wmv|mpg|mpeg|iso|nzb|torrent)$', re.I)
_GROUP_RE = re.compile(r'-([A-Za-z0-9][A-Za-z0-9]{1,23})(?:\s*\[[^\]]{1,40}\])?\s*$')
_NOT_GROUPS = {
    'DL', 'RIP', 'HD', 'SD', 'MA', 'X', '265', '264', 'X264', 'X265', 'H264', 'H265', 'HEVC',
    'AVC', 'AAC', 'AC3', 'DTS', 'DDP', 'SDR', 'HDR', 'DV', 'WEB', 'WEBDL', 'WEBRIP', 'BLURAY',
    'REMUX', '1080P', '2160P', '720P', '480P', '4K', 'UHD', '51', '71', '20', 'ATMOS', 'TRUEHD',
    'MULTI', 'DUAL', 'PROPER', 'REPACK', 'MKV', 'MP4',
}
_TOKEN_SPLIT_RE = re.compile(r'[\s._\-\[\]()+]+')
_RELEASE_LIKE_RE = re.compile(
    r'(?i)(?:2160p|1080p|720p|480p|web[ .-]?dl|webrip|blu[ .-]?ray|remux|hdtv|[hx]\.?26[45]|hevc)')


def _flat(text):
    """Lower-case words: dots, underscores and dashes read as spaces."""
    return re.sub(r'\s+', ' ', re.sub(r'[._\-/\\|,:;~]', ' ', str(text or '').lower())).strip()


_YEAR_RE = re.compile(r'(?<![0-9])(?:19[0-9]{2}|20[0-3][0-9])(?![0-9])')
_EPISODE_RE = re.compile(r'(?i)(?<![a-z0-9])s[0-9]{1,2}(?:[ ._-]?e[0-9]{1,3})?(?![a-z0-9])')
_RES_MARK_RE = re.compile(r'(?i)(?<![a-z0-9])(?:4320p|2160p|1440p|1080[pi]|720p|576p|540p|480p|360p|4k|uhd)(?![a-z0-9])')


def _after_title(line):
    """A release line without its title: from the year, episode or resolution on.

    Title words must not read as facts ("The.Italian.Job" is not an Italian
    release, "Hybrid (2025)" not a hybrid encode). A line with none of these
    markers (a tag line such as "WEB-DL DV HEVC") is kept whole.
    """
    line = str(line or '')
    cuts = []
    match = _YEAR_RE.search(line)
    if match:
        cuts.append(match.end())
    match = _EPISODE_RE.search(line)
    if match:
        cuts.append(match.end())
    match = _RES_MARK_RE.search(line)
    if match:
        cuts.append(match.start())
    return line[min(cuts):] if cuts else line


def _first(regexes, flat):
    for rx, value in regexes:
        if rx.search(flat):
            return value
    return ''


def parse_release(text, whole=False):
    """The facts a release name (or any provider text) spells out.

    Each line is read from its year, episode or resolution on (see
    ``_after_title``) unless ``whole`` (a list of tags with no title in it).
    """
    lines = str(text or '').split('\n')
    raw = '\n'.join(lines if whole else [_after_title(line) for line in lines])
    flat = _flat(raw)
    out = {}
    match = _RES_RE.search(flat)
    if match:
        res = match.group(1)
        out['res'] = {'4k': '2160p', 'uhd': '2160p', '8k': '4320p'}.get(res, res)
    sources = [value for rx, value in _SOURCE_RES if rx.search(flat)]
    if sources:
        # a REMUX is always of a disc; WEB-DL beats the bare WEB it contains
        if 'REMUX' in sources:
            out['source'] = 'BluRay REMUX' if 'BluRay' in sources else 'REMUX'
        else:
            out['source'] = sources[0]
    hdr = [value for rx, value in _HDR_RES if rx.search(flat)]
    if 'HDR10+' in hdr or 'HDR10' in hdr:
        hdr = [h for h in hdr if h != 'HDR']
    if 'HDR10+' in hdr:
        hdr = [h for h in hdr if h != 'HDR10']
    if len(hdr) > 1:
        hdr = [h for h in hdr if h != 'SDR']
    if hdr:
        out['hdr'] = hdr
    codec = _first(_CODEC_RES, flat)
    if codec:
        out['codec'] = codec
    match = _BITDEPTH_RE.search(flat)
    if match:
        out['bitdepth'] = '%s-bit' % match.group(1)
    audio = [value for rx, value in _AUDIO_RES if rx.search(flat)]
    if 'DTS:X' in audio or 'DTS-HD MA' in audio or 'DTS-HD' in audio:
        audio = [a for a in audio if a != 'DTS']
    if 'DTS-HD MA' in audio:
        audio = [a for a in audio if a != 'DTS-HD']
    if 'DD+' in audio:
        audio = [a for a in audio if a != 'DD']
    if audio:
        out['audio'] = audio
    if _ATMOS_RE.search(flat):
        out['atmos'] = True
    match = _CHANNELS_AFTER_RE.search(flat) or _CHANNELS_RE.search(flat)
    if match:
        out['channels'] = '%s.%s' % (match.group(1), match.group(2))
    else:
        match = _CHANNELS_CH_RE.search(flat)
        if match:
            out['channels'] = {'2': '2.0', '6': '5.1', '8': '7.1'}[match.group(1)]
    langs = []
    for rx, key in _LANG_RES:
        if rx.search(flat) and key not in langs:
            langs.append(key)
    if langs:
        out['langs'] = langs
    tokens = [t for t in _TOKEN_SPLIT_RE.split(raw) if t]
    networks = [_NETWORKS[t] for t in tokens if t in _NETWORKS]
    if networks:
        out['network'] = networks[0]
    editions = [value for rx, value in _EDITION_RES if rx.search(flat)]
    if _3D_RE.search(raw):
        editions.append('3D')
    if editions:
        out['edition'] = editions
    return out


def release_group(name):
    base = _EXT_RE.sub('', str(name or '').strip())
    match = _GROUP_RE.search(base)
    if not match:
        return ''
    group = match.group(1)
    if group.upper() in _NOT_GROUPS or group.isdigit():
        return ''
    return group


def _binge_fields(hints):
    """AIOStreams writes its parsed facts into bingeGroup, '|' separated."""
    value = str((hints or {}).get('bingeGroup') or '')
    if value.count('|') < 3:
        return {}
    fields = [f.strip() for f in value.split('|')]
    out = {}
    facts = parse_release(' '.join(fields[1:]), whole=True)
    for key in ('res', 'source', 'hdr', 'codec', 'langs'):
        if facts.get(key):
            out[key] = facts[key]
    if value.lower().startswith('com.aiostreams') and fields:
        last = fields[-1]
        if (re.match(r'^[A-Za-z0-9]{2,24}$', last) and last.upper() not in _NOT_GROUPS
                and last.lower() not in ('true', 'false', 'none', 'null', 'undefined')
                and not parse_release(last, whole=True)):
            out['group'] = last
    return out


def _release_name(row, hints, formatted, text):
    for value in (hints.get('filename'), row.get('filename'), row.get('fileName'),
                  formatted.get('file')):
        value = plain(value).split('\n')[0].strip() if value else ''
        if value:
            return value.rsplit('/', 1)[-1]
    # Torrentio style: the release name is the first line of the title
    for line in str(text or '').split('\n'):
        line = line.strip()
        if ' ' not in line and len(line) > 12 and _RELEASE_LIKE_RE.search(line):
            return line
    return ''


# ------------------------------------------------------- provider text ----

_BITRATE_RE = re.compile(r'(?i)(?<![\w.])(\d+(?:[.,]\d+)?)\s*(gbps|mbps|mb/s|kbps|kb/s)(?![\w])')
_AGE_TOKEN_RE = re.compile(r'^(\d{1,4})(h|d|w|mo|y)$')
_AGE_UNITS = {
    # one, two (after قبل), 3 to 10, 11 to 99, 100 and more
    'h': (('ساعة', 'ساعتين', 'ساعات', 'ساعة', 'ساعة'), ('hour', 'hours')),
    'd': (('يوم', 'يومين', 'أيام', 'يوماً', 'يوم'), ('day', 'days')),
    'w': (('أسبوع', 'أسبوعين', 'أسابيع', 'أسبوعاً', 'أسبوع'), ('week', 'weeks')),
    'mo': (('شهر', 'شهرين', 'أشهر', 'شهراً', 'شهر'), ('month', 'months')),
    'y': (('سنة', 'سنتين', 'سنوات', 'سنة', 'سنة'), ('year', 'years')),
}
_DURATION_ICONS = ('\u23f1', '\u231a', '\u23f0', '\u23f2')      # ⏱ ⌚ ⏰ ⏲
_SEEDERS_RE = re.compile('(?:\U0001f464|\U0001f465)\ufe0f?\\s*(\\d{1,6})|(?i:\\b(\\d{1,6})\\s*(?:seeders|seeds)\\b)')
_READY_RE = re.compile(r'(?i)(?<![a-z])(?:ready|cached|instant(?:ly)?|available)(?![a-z])')
_NOT_READY_RE = re.compile(r'(?i)(?<![a-z])(?:uncached|not[ -]cached|not[ -]ready|downloading)(?![a-z])')
_CACHED_BADGE_RE = re.compile(r'\[[A-Z]{2,4}\+\]|\u26a1')
_UNCACHED_BADGE_RE = re.compile(r'\[[A-Z]{2,4}\s*(?:download|\u23f3)\]|\u23f3', re.I)


def _count(unit, number):
    ar, en = _AGE_UNITS[unit]
    if _is_english():
        return '%d %s ago' % (number, en[0] if number == 1 else en[1])
    if number == 1:
        words = ar[0]
    elif number == 2:
        words = ar[1]
    elif 3 <= number <= 10:
        words = '%d %s' % (number, ar[2])
    elif number < 100:
        words = '%d %s' % (number, ar[3])
    else:
        words = '%d %s' % (number, ar[4])
    return 'قبل %s' % words


def _posted(raw):
    """'15h', '3d', '2mo' as an aggregator prints the age of a release."""
    tokens = str(raw or '').split()
    for index, token in enumerate(tokens):
        icon = ''
        core = token
        while core and not core[0].isdigit():
            icon += core[0]
            core = core[1:]
        match = _AGE_TOKEN_RE.match(core.rstrip(',;|'))
        if not match:
            continue
        number, unit = int(match.group(1)), match.group(2)
        following = tokens[index + 1] if index + 1 < len(tokens) else ''
        if re.match(r'^:?\d{1,2}m', following) or re.match(r'^:\d', core[len(match.group(0)):]):
            continue            # "2h 8m": a running time
        previous = icon or (tokens[index - 1] if index else '')
        if unit == 'h' and number < 6 and any(i in previous for i in _DURATION_ICONS):
            continue            # "⏱ 2h": also a running time
        if number <= 0:
            continue
        return _count(unit, number)
    return ''


def _bitrate(text):
    for match in _BITRATE_RE.finditer(str(text or '')):
        value = match.group(1).replace(',', '.')
        try:
            if float(value) <= 0:
                continue
        except ValueError:
            continue
        unit = match.group(2).lower().replace('/s', 'ps')
        unit = {'gbps': 'Gbps', 'mbps': 'Mbps', 'kbps': 'Kbps'}.get(unit, unit)
        return '%s %s' % (value, unit)
    return ''


def _seeders(raw):
    match = _SEEDERS_RE.search(str(raw or ''))
    if not match:
        return ''
    return match.group(1) or match.group(2) or ''


_LANG_LINE_RE = re.compile('[\U0001f30d\U0001f30e\U0001f30f\U0001f310\U0001f5e3]|(?i:\\b(?:lang(?:uages?)?|audio)\\b)')


def _described_langs(raw):
    """Languages a provider lists on a line of their own (a globe icon, 'Lang')."""
    langs = []
    for line in str(raw or '').split('\n'):
        if not _LANG_LINE_RE.search(line):
            continue
        for key in parse_release(plain(line), whole=True).get('langs') or []:
            if key not in langs:
                langs.append(key)
    return langs


def _flag_langs(raw):
    langs = []
    for pair in _FLAG_RE.findall(str(raw or '')):
        try:
            code = chr(ord(pair[0]) - 127397) + chr(ord(pair[1]) - 127397)
        except Exception:
            continue
        key = _FLAG_LANGS.get(code)
        if key and key not in langs:
            langs.append(key)
    return langs


_GENERIC_ADDONS = {'addon', 'addons', 'source', 'sources', 'provider', 'unknown', 'stremio',
                   'stream', 'streams', 'direct', 'usenet', 'torrent', 'debrid'}
# Torrentio and its kin name the tracker after a gear: "⚙️ ThePirateBay"
_TRACKER_RE = re.compile('\u2699\ufe0f?\\s*([^\\n|\u2022\U0001f000-\U0001faff]{2,40})')


def _cased(name, *texts):
    """``name`` as the provider itself spells it (ALTHUB -> altHUB)."""
    name = str(name or '').strip()
    if not name:
        return ''
    rx = re.compile(r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % re.escape(name), re.I)
    for text in texts:
        match = rx.search(str(text or ''))
        if match:
            return match.group(0)
    return name


def _same(a, b):
    squash = lambda v: re.sub(r'[^a-z0-9]', '', str(v or '').lower())
    return bool(squash(a)) and squash(a) == squash(b)


# --------------------------------------------------------------- build ----

LABEL_COLOR = 'FF9AA4B2'
_TYPE_COLORS = {'usenet': 'FFF59E0B', 'debrid': 'FF10B981', 'torrent': 'FFEF4444',
                'direct': 'FF3B82F6', 'plex': 'FFE5A00D', 'emby': 'FF52B54B', 'silo': 'FF8B5CF6'}
_READY_COLOR = 'FF22C55E'
_NOT_READY_COLOR = 'FFF59E0B'


def _merge(base, extra):
    for key, value in (extra or {}).items():
        if not value:
            continue
        if key not in base or not base[key]:
            base[key] = value
        elif key == 'source' and base[key] == 'WEB' and value in ('WEB-DL', 'WEBRip'):
            base[key] = value       # the release says WEB, the aggregator knows which
        elif isinstance(base[key], list) and isinstance(value, list):
            base[key] = base[key] + [v for v in value if v not in base[key]]
    return base


_BIT_KEYS = ('res', 'source', 'hdr', 'codec', 'bitdepth', 'audio', 'atmos', 'channels')


def _from_bits(bits):
    """Dex Hub's own upper-case fact lists (video_bits / audio_bits).

    Only the technical facts: those lists are read from every text of a row
    at once, so their language, edition and 3D guesses can come from a title
    or from an age ("3d") and are left out here.
    """
    found = parse_release(' '.join(str(b) for b in (bits or [])), whole=True)
    return dict((key, found[key]) for key in _BIT_KEYS if key in found)


def _tidy(info):
    """One name per fact once every source of facts has been read."""
    hdr = list(info.get('hdr') or [])
    if 'HDR10+' in hdr:
        hdr = [h for h in hdr if h not in ('HDR10', 'HDR')]
    elif 'HDR10' in hdr:
        hdr = [h for h in hdr if h != 'HDR']
    if len(hdr) > 1:
        hdr = [h for h in hdr if h != 'SDR']
    order = ('DV', 'HDR10+', 'HDR10', 'HDR', 'HLG', 'SDR')
    info['hdr'] = sorted(hdr, key=lambda h: order.index(h) if h in order else 99)
    audio = list(info.get('audio') or [])
    if 'DD+' in audio:
        audio = [a for a in audio if a != 'DD']
    if 'DTS-HD MA' in audio or 'DTS:X' in audio:
        audio = [a for a in audio if a not in ('DTS-HD', 'DTS')]
    elif 'DTS-HD' in audio:
        audio = [a for a in audio if a != 'DTS']
    info['audio'] = audio
    return info


_LIBRARY_RE = re.compile(u'(?i)\U0001f4cc\ufe0f?\\s*library|\\bin[ -]library\\b|\\[library\\]')


def library_state(row):
    """v5.10.204: is the source already in the user's debrid or usenet
    library (AIOStreams' streamData.library, or its formatter's 📌 Library)?"""
    row = row or {}
    data = row.get('streamData') if isinstance(row.get('streamData'), dict) else {}
    if isinstance(data.get('library'), bool):
        return data['library']
    raw = '\n'.join(str(row.get(k) or '') for k in ('name', 'description', 'title') if row.get(k))
    return bool(_LIBRARY_RE.search(raw))


def ready_state(row, facts=None):
    """Is a debrid or usenet source cached (True), not cached (False) or unknown (None)?

    v5.10.203: the source list shows it on every row. AIOStreams' own data
    (streamData.service.cached, or a plain 'cached' field) is read first;
    otherwise the text its formatter wrote (⚡, Ready, [TB+] / Not Ready,
    ⏳, [TB download]), as the details panel already read it.
    """
    row = row or {}
    facts = facts or {}
    data = row.get('streamData') if isinstance(row.get('streamData'), dict) else {}
    service = data.get('service') if isinstance(data.get('service'), dict) else {}
    if isinstance(service.get('cached'), bool):
        return service['cached']        # AIOStreams' own answer, any formatter
    kind = facts.get('source_type') or ''
    if isinstance(kind, (list, tuple)):
        kind = kind[0] if kind else ''
    if str(kind or '').lower() not in ('usenet', 'debrid'):
        return None
    hints = row.get('behaviorHints') if isinstance(row.get('behaviorHints'), dict) else {}
    for value in (row.get('cached'), hints.get('cached')):
        if isinstance(value, bool):
            return value
    raw = '\n'.join(str(row.get(k) or '') for k in ('name', 'title', 'description') if row.get(k))
    text = plain(raw)
    if _UNCACHED_BADGE_RE.search(raw) or _NOT_READY_RE.search(text):
        return False
    if _CACHED_BADGE_RE.search(raw) or _READY_RE.search(raw + '\n' + text):
        return True
    if str(facts.get('provider_badge') or '').endswith('+'):
        return True
    return None


def facts_for(row, provider_name='', facts=None):
    """Everything known about one source row, as display values."""
    row = row or {}
    facts = facts or {}
    hints = row.get('behaviorHints') if isinstance(row.get('behaviorHints'), dict) else {}
    formatted = facts.get('_formatter') if isinstance(facts.get('_formatter'), dict) else {}
    raw_parts = [str(row.get(k) or '') for k in ('name', 'title', 'description')]
    raw = '\n'.join(p for p in raw_parts if p)
    text = plain(raw)
    release = _release_name(row, hints, formatted, plain(row.get('title') or row.get('description') or ''))
    info = parse_release(_EXT_RE.sub('', release))
    if release:
        group = release_group(release)
        if group:
            info['group'] = group
    _merge(info, _binge_fields(hints))
    if formatted:
        _merge(info, parse_release(' '.join(str(formatted.get(k) or '')
                                            for k in ('video', 'audio', 'lang', 'title'))))
    described = parse_release(text)
    described.pop('langs', None)        # see _described_langs
    _merge(info, described)
    _merge(info, _from_bits(list(facts.get('video_bits') or []) + list(facts.get('audio_bits') or [])))
    langs = list(info.get('langs') or [])
    for key in _described_langs(raw) + _flag_langs(raw):
        if key not in langs:
            langs.append(key)
    tags = facts.get('tags') if isinstance(facts.get('tags'), dict) else {}
    for lang in (tags.get('languages') or []):
        key = str(lang or '').strip().lower()
        if key in _LANGS and key not in langs:
            langs.append(key)
    info['langs'] = langs
    info['release'] = release
    info['size'] = str(facts.get('size') or '').strip()
    info['bitrate'] = _bitrate(text)
    info['posted'] = _posted(raw)
    info['seeders'] = _seeders(raw)
    kind = facts.get('source_type') or ''
    if isinstance(kind, (list, tuple)):
        kind = kind[0] if kind else ''
    kind = str(kind or '').lower()
    info['kind'] = kind
    ready = None
    status_text = '%s\n%s' % (raw, text)
    if kind in ('usenet', 'debrid'):
        if _UNCACHED_BADGE_RE.search(raw) or _NOT_READY_RE.search(text):
            ready = False
        elif _CACHED_BADGE_RE.search(raw) or _READY_RE.search(status_text):
            ready = True
        elif str(facts.get('provider_badge') or '').endswith('+'):
            ready = True
    info['ready'] = ready
    info['indexer'] = _cased(facts.get('indexer'), text)
    service = str(facts.get('service') or '').strip()
    info['service'] = _SERVICES.get(service.upper().replace(' ', ''), _cased(service, text)) if service else ''
    provider = str(provider_name or '').strip()
    info['provider'] = provider
    addon = str(facts.get('addon') or '').strip()
    squashed = re.sub(r'[^a-z0-9]', '', addon.lower())
    if (addon and squashed not in _GENERIC_ADDONS
            and not _RES_MARK_RE.search(addon)
            and not (provider and re.sub(r'[^a-z0-9]', '', provider.lower()) in squashed)
            and not any(_same(addon, other) for other in
                        (provider, service, facts.get('indexer'), info['service']))):
        info['addon'] = _cased(addon, text, row.get('name'))
    site = ''
    for value in (row.get('site'), row.get('tracker'), row.get('sourceSite'), hints.get('site')):
        site = plain(value).split('\n')[0].strip() if value else ''
        if site:
            break
    if not site:
        match = _TRACKER_RE.search(raw)
        site = match.group(1).strip() if match else ''
    if site and not any(_same(site, other) for other in
                        (provider, addon, service, facts.get('indexer'), info['service'])):
        info['site'] = site
    return _tidy(info)


_ARABIC_RE = re.compile('[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]')


def _line(key, value, color=''):
    if not value:
        return ''
    label = _ui(_LABELS[key])
    if not _is_english() and not _ARABIC_RE.search(value):
        # after an Arabic label "24.44 GB" would read "GB 24.44": a left to
        # right mark keeps a Latin value in its own order
        value = '\u200e' + value
    if color:
        value = '[COLOR %s]%s[/COLOR]' % (color, value)
    return '[COLOR %s]%s:[/COLOR]  %s' % (LABEL_COLOR, label, value)


def _wrap_name(name, width=50):
    """A long release name over several lines, broken after its dots.

    Kodi wraps a line only at spaces, and a release name has none: the end of
    a long one was cut off at the edge of the dialog.
    """
    name = str(name or '')
    if len(name) <= width:
        return name
    parts = re.split(r'(?<=[._ ])', name)
    lines, line = [], ''
    for part in parts:
        if line and len(line) + len(part) > width:
            lines.append(line)
            line = ''
        while len(part) > width:         # one very long piece: cut it
            lines.append(part[:width])
            part = part[width:]
        line += part
    if line:
        lines.append(line)
    return '\n'.join(l.rstrip() for l in lines)


def build(row, provider_name='', facts=None, subtitles=''):
    """The Info dialog text for one source row (Kodi label markup)."""
    row = row if isinstance(row, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    info = facts_for(row, provider_name, facts)
    dot = '  \u2022  '
    quality = []
    if info.get('res'):
        quality.append(_RESOLUTIONS.get(info['res'], info['res']))
    if info.get('source'):
        quality.append(info['source'])
    video = [_HDR_NAMES.get(h, h) for h in (info.get('hdr') or [])]
    if info.get('codec'):
        video.append(_CODEC_NAMES.get(info['codec'], info['codec']))
    if info.get('bitdepth'):
        video.append(info['bitdepth'])
    audio = []
    codecs = info.get('audio') or []
    if codecs:
        audio.append(_AUDIO_NAMES.get(codecs[0], codecs[0]))
    if info.get('atmos'):
        audio.append('Dolby Atmos')
    if info.get('channels'):
        audio.append(info['channels'])
    langs = [_ui(_LANGS[key]) for key in info.get('langs') or [] if key in _LANGS]
    comma = ', ' if _is_english() else '\u060c '

    head = []
    title = info.get('release') or plain((facts or {}).get('display_name') or row.get('name') or '')
    if title:
        head.append('[B]%s[/B]' % _wrap_name(title.split('\n')[0]))
    media = [
        _line('quality', dot.join(quality)),
        _line('video', dot.join(video)),
        _line('audio', dot.join(audio)),
        _line('languages', comma.join(langs)),
        _line('subtitles', subtitles),
        _line('edition', dot.join(info.get('edition') or [])),
        _line('network', info.get('network')),
        _line('size', info.get('size')),
        _line('bitrate', info.get('bitrate')),
        _line('posted', info.get('posted')),
        _line('group', info.get('group')),
    ]
    kind = info.get('kind') or ''
    status = ''
    if info.get('ready') is True:
        status = _line('status', _ui(_READY), _READY_COLOR)
    elif info.get('ready') is False:
        status = _line('status', _ui(_NOT_READY), _NOT_READY_COLOR)
    origin = [
        _line('type', _ui(_TYPES[kind]) if kind in _TYPES else '', _TYPE_COLORS.get(kind, '')),
        status,
        _line('indexer', info.get('indexer')),
        _line('service', info.get('service')),
        _line('seeders', info.get('seeders')),
        _line('site', info.get('site')),
        _line('addon', info.get('addon')),
        _line('provider', info.get('provider')),
    ]
    blocks = ['\n'.join(head), '\n'.join(l for l in media if l), '\n'.join(l for l in origin if l)]
    return '\n\n'.join(b for b in blocks if b)


def for_entry(entry, payload=None):
    """The details of a row of the sources window, written when Info is pressed.

    Nothing here runs during a source scan: the window keeps each row's
    parsed facts and the stream cache keeps the provider's own texts, so the
    details are composed only for the row the user asks about.
    """
    entry = entry if isinstance(entry, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    hints = payload.get('behaviorHints')
    row = {
        'name': payload.get('stream_name') or '',
        'title': payload.get('stream_title') or '',
        'description': payload.get('stream_description') or '',
        'behaviorHints': hints if isinstance(hints, dict) else {},
        'subtitles': payload.get('subtitles') or [],
    }
    if not row['behaviorHints'].get('filename') and entry.get('filename'):
        row['behaviorHints'] = dict(row['behaviorHints'], filename=entry.get('filename'))
    try:
        from .stream_facts import _parse_formatter_fields
        formatted = _parse_formatter_fields(row)
    except Exception:
        formatted = {}
    size = str(entry.get('size_label') or '').strip()
    facts = {
        'size': '' if size in ('--', '-') else size,
        'video_bits': entry.get('video_bits') or payload.get('video_bits') or [],
        'audio_bits': entry.get('audio_bits') or payload.get('audio_bits') or [],
        'tags': entry.get('tags') or {},
        'source_type': (str(entry.get('source_type') or '').lower(),),
        'indexer': entry.get('indexer') or entry.get('source_indexer') or '',
        'service': entry.get('service_name') or entry.get('source_service') or '',
        'addon': entry.get('addon') or entry.get('source_addon') or '',
        'provider_badge': entry.get('provider') or '',
        'display_name': entry.get('name') or '',
        '_formatter': formatted,
    }
    subtitles = ''
    if row['subtitles']:
        try:
            from .playback.subtitle_files import _subtitles_summary
            subtitles = _subtitles_summary(row['subtitles'])
        except Exception:
            subtitles = ''
    provider = entry.get('provider_name_raw') or payload.get('provider_name') or ''
    return build(row, provider, facts, subtitles=subtitles)
