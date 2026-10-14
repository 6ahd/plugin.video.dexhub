# -*- coding: utf-8 -*-
"""Release-name analysis — the pure, Kodi-free core.

v4.8.5: extracted from plugin.py by AST span. Scope was chosen by
measuring what is genuinely leaf-pure rather than by theme: these
functions read a stream row and a formatter block and return facts —
resolution, HDR, audio, the labelled fields — with no Kodi imports and no
reach back into the renderer. `_stream_facts` itself deliberately STAYS in
plugin.py: it orchestrates thirteen other helpers, and dragging those
along would have turned a safe move into a rewrite.

This is the code that once hid a real bug: the AIO fast path trusted the
formatter with no fallback, so when a preset omitted the labelled fields
every quality badge disappeared and stayed gone for several releases. As a
standalone module it is exercised directly against real release names.
"""
import html
import re
import unicodedata

from .context import (
    BRACKET_RE, COLOR_TAG_RE, HEX_ENTITIES, MULTI_WS_RE,
    REGIONAL_FLAG_RE, SPECIAL_BLANKS, STREAM_ICON_RE,
)


def _extract_group(values, patterns):
    seen = []
    for value in values:
        text = _normalize_ascii(value).lower()
        for patt, repl in patterns:
            for _ in re.findall(patt, text, re.I):
                if repl not in seen:
                    seen.append(repl)
    return seen
def _iter_stream_values(value):
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        for entry in value:
            for sub in _iter_stream_values(entry):
                yield sub
        return
    if isinstance(value, dict):
        for entry in value.values():
            for sub in _iter_stream_values(entry):
                yield sub
        return
    sval = str(value).strip()
    if sval:
        yield sval
_STREAM_TEXT_FIELDS = (
    'name', 'title', 'description', 'filename', 'fileName', 'folderName',
    'url', 'externalUrl', 'site', 'host', 'tracker', 'sourceSite',
    'indexer', 'indexerName', 'sourceIndexer', 'source', 'sourceType',
    'transport', 'type', 'addon', 'addonName', 'sourceAddon', 'provider',
    'streamName', 'releaseTitle', 'release', 'bingeGroup', 'resolution',
    'quality', 'encode', 'codec', 'releaseGroup', 'network', 'service',
    'serviceName', 'debridService', 'visualTags', 'audioTags',
    'audioChannels', 'languages', 'size', 'videoSize', 'sizeLabel',
)
_STREAM_HINT_TEXT_FIELDS = (
    'filename', 'fileName', 'name', 'title', 'description', 'bingeGroup',
    'videoSize', 'quality', 'source', 'sourceType', 'transport', 'type',
    'provider', 'site', 'indexer', 'indexerName', 'service', 'serviceName',
    'resolution', 'codec', 'audioTags', 'audioChannels', 'visualTags',
    'languages', 'size',
)
def _collect_stream_texts(row):
    """Collect one bounded fact set for a stream row.

    AIOStreams can return hundreds of rows and some rows carry large subtitle or
    behaviour-hint payloads.  The old implementation flattened those payloads,
    JSON-encoded the hints, and then repeated the same work in five different
    extractors.  Keep only fields that can affect source identity/quality and
    cap the scan so one unusual row cannot stall the picker.
    """
    row = row or {}
    hints = row.get('behaviorHints') or {}
    texts = []
    seen = set()

    def _append(value):
        for sval in _iter_stream_values(value):
            text = str(sval or '').strip()
            if not text:
                continue
            # Enough for filenames and formatter descriptions while avoiding
            # embedded manifests/tokens turning into multi-kilobyte regex input.
            if len(text) > 768:
                text = text[:768]
            if text in seen:
                continue
            seen.add(text)
            texts.append(text)
            if len(texts) >= 48:
                return True
        return False

    for key in _STREAM_TEXT_FIELDS:
        if _append(row.get(key)):
            return texts
    if isinstance(hints, dict):
        for key in _STREAM_HINT_TEXT_FIELDS:
            if _append(hints.get(key)):
                break
    return texts
def _extract_audio_bits(*values):
    patterns = [
        (r'\batmos\b', 'ATMOS'),
        (r'\btruehd\b|\btrue-hd\b', 'TRUEHD'),
        (r'\bdolby\s*digital\s*plus\b|\bddp(?:\+|\b)|\beac-?3\b|\bdd\+\b', 'DD+'),
        (r'\bdolby\s*digital\s*ex\b|\bdd-?ex\b', 'DD-EX'),
        (r'\bdolby\s*digital\b|\bdd\b', 'DD'),
        (r'\bac-?3\b', 'AC3'),
        (r'\bdts\s*x\b|\bdtsx\b', 'DTS-X'),
        (r'\bdts[- ]?hd\s*master\s*audio\b|\bdts[- ]?hd\s*ma\b', 'DTS-HD MA'),
        (r'\bdts[- ]?hd\b', 'DTS-HD'),
        (r'\bdts\b', 'DTS'),
        (r'\bflac\b', 'FLAC'),
        (r'\baac\b', 'AAC'),
        (r'\bopus\b', 'OPUS'),
        (r'\bmp3\b', 'MP3'),
        (r'\bpcm\b', 'PCM'),
        (r'\b8ch(?:\s*audio)?\b', '8CH'),
        (r'\b7\.1\b|\b7ch(?:\s*audio)?\b', '7.1'),
        (r'\b6ch(?:\s*audio)?\b|\b5\.1\b', '5.1'),
        (r'\b2ch(?:\s*audio)?\b|\b2\.0\b', '2.0'),
        (r'\bmulti(?:[- ]?audio|[- ]?lang(?:uages?)?)?\b', 'MULTI'),
        (r'\bdual(?:[- ]?audio)?\b', 'DUAL'),
        (r'\bdubbed?\b|\bdub\b', 'DUBBED'),
        (r'\bsubs?\b|\bsubbed\b|\bsubtitles\b', 'SUBS'),
        (r'\barabic\b', 'ARABIC'),
        (r'\benglish\b', 'ENGLISH'),
        (r'\brussian\b', 'RUSSIAN'),
    ]
    return _extract_group(values, patterns)
def _extract_video_bits(*values):
    patterns = [
        (r'\b2160p\b|\b4k\b|\buhd\b', '2160P'),
        (r'\b1080p\b|\bfhd\b', '1080P'),
        (r'\b720p\b', '720P'),
        (r'\b480p\b|\bsd\b', '480P'),
        (r'\bremux\b', 'REMUX'),
        (r'\bblu[ -]?ray\b', 'BLURAY'),
        (r'\bweb[ -]?dl\b', 'WEB-DL'),
        (r'\bweb[ -]?rip\b', 'WEBRIP'),
        (r'\bhdr10\+\b', 'HDR10+'),
        (r'\bhdr10\b', 'HDR10'),
        (r'\bhigh\s+dynamic\s+range\s*\(hdr\)\b|\bhdr\b', 'HDR'),
        (r'\bdolby\s*vision\b|\bdovi\b|\bdv\b', 'DV'),
        (r'\bhevc\b|\bx265\b|\bh265\b', 'HEVC'),
        (r'\bx264\b|\bh264\b', 'H264'),
        (r'\bav1\b', 'AV1'),
        (r'\b10bit\b|\b10-bit\b', '10BIT'),
        (r'\bhybrid\b', 'HYBRID'),
        (r'\bsdr\b', 'SDR'),
        (r'\b3d\b', '3D'),
        (r'\bnf\b|\bnetflix\b', 'NF'),
        (r'\bamzn\b|\bamazon\b', 'AMZN'),
        (r'\bdsnp\b|\bdisney\+?\b', 'DSNP'),
        (r'\bhulu\b', 'HULU'),
        (r'\batvp\b|\bapple\s?tv\b', 'ATVP'),
        (r'\bdvd\s*source\b|\bdvd\b', 'DVD'),
        (r'\bweb\s*source\b', 'WEB'),
        (r'\bpack\b', 'PACK'),
    ]
    return _extract_group(values, patterns)
_FORMATTER_LINE_RE = re.compile(
    r'^(FILE|TITLE|VIDEO|AUDIO|SIZE|LANG|SUBS|SOURCE)\s+(.+)$', re.I)
_FORMATTER_SOURCE_PART_RE = re.compile(
    r'^(SOURCE|ADDON|SERVICE|INDEXER|TYPE|URL)\s+(.+)$', re.I)
def _parse_formatter_fields(row):
    """Read labelled AIOStreams formatter output without re-analysing it.

    AIOStreams has already parsed filename, quality, codecs, service, stream
    type and indexer.  Dex Hub's preset emits those values on labelled lines;
    this reader only splits the final text that the Stremio endpoint returns.
    It also accepts the v4.2.1 preset so existing configurations keep working.
    """
    out = {}
    description = str((row or {}).get('description') or '')
    if not description:
        return out
    for raw_line in description.splitlines()[:16]:
        line = MULTI_WS_RE.sub(' ', str(raw_line or '')).strip()
        match = _FORMATTER_LINE_RE.match(line)
        if not match:
            continue
        key = match.group(1).lower()
        value = match.group(2).strip()
        if not value:
            continue
        if key != 'source':
            out[key] = value
            continue
        # SOURCE Addon | SERVICE RD | INDEXER NZBGeek | TYPE usenet
        for idx, raw_part in enumerate(re.split(r'\s*[|\u2022]\s*', value)):
            part = str(raw_part or '').strip()
            if not part:
                continue
            labelled = _FORMATTER_SOURCE_PART_RE.match(part)
            if labelled:
                out[labelled.group(1).lower()] = labelled.group(2).strip()
            elif idx == 0:
                out['source'] = part
    return out
def _formatter_fact_list(value, prefix=''):
    text = str(value or '').strip()
    if prefix and text.upper().startswith(prefix.upper() + ' '):
        text = text[len(prefix) + 1:].strip()
    out = []
    for part in re.split(r'\s*(?:\||\u2022|\u00b7)\s*', text):
        clean = MULTI_WS_RE.sub(' ', str(part or '')).strip(' -_.')
        if clean and clean != '--':
            upper = clean.upper()
            if upper not in out:
                out.append(upper)
    return out


def _batch_replace(text, replacements):
    for old, new in replacements:
        text = text.replace(old, new)
    return text
def _regional_flag_to_code(match):
    chars = match.group(0)
    try:
        return '[%s%s]' % (chr(ord(chars[0]) - 127397), chr(ord(chars[1]) - 127397))
    except Exception:
        return ' '
def _clean_stream_text(value):
    text = html.unescape(str(value or ''))
    text = _batch_replace(text, HEX_ENTITIES)
    text = COLOR_TAG_RE.sub(' ', text)
    text = REGIONAL_FLAG_RE.sub(_regional_flag_to_code, text)
    text = STREAM_ICON_RE.sub(' ', text)
    text = text.replace('\u200f', ' ').replace('\u200e', ' ').replace('\xa0', ' ')
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = ''.join(ch for ch in text if ch == '\n' or ch == '\t' or ord(ch) >= 32)
    text = _batch_replace(text, SPECIAL_BLANKS)
    text = text.replace('•', ' | ').replace('·', ' | ').replace('—', '-').replace('–', '-')
    text = BRACKET_RE.sub(' ', text)
    text = MULTI_WS_RE.sub(' ', text).strip(' -|_')
    return text.strip()
def _normalize_ascii(text):
    text = _clean_stream_text(text)
    text = re.sub(r'[^\x00-\x7F]', '', text)
    text = MULTI_WS_RE.sub(' ', text).strip(' -|_')
    return text
