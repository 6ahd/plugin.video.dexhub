# -*- coding: utf-8 -*-
"""Vendored from DexSubtitles 5.7.6 for Dex Hub 5.10.290 (subsync/osdflow.py).
DEX_PICK_V576: أدوات زر «أفضل ترجمة» بدون أي استيراد من Kodi.

- release_info / name_score: قرب اسم نسخة الترجمة من اسم ملف الفيديو (الدقة، المصدر، المنصة،
  الفريق، النسخة الخاصة). نستخدمه لترتيب المرشحين قبل التحميل، ولحاله لما الفيديو ما فيه مرجع توقيت.
- looks_arabic: الملف عربي فعلاً؟ (ملفات جنب الفيديو والمرفقة بالمصدر ما لها حقل لغة موثوق).
"""
import re
from urllib.parse import unquote

_RES = {'2160p': '2160', '4k': '2160', 'uhd': '2160', '1080p': '1080', '1080i': '1080',
        '720p': '720', '576p': '576', '480p': '480', '360p': '360'}
# عائلة المصدر: نفس العائلة غالباً نفس التوقيت، وعائلة ثانية غالباً فرق فريمات أو مقدمة
_SRC = {'bluray': 'bluray', 'bdrip': 'bluray', 'brrip': 'bluray', 'bdremux': 'bluray', 'remux': 'bluray',
        'bd': 'bluray', 'bdr': 'bluray', 'bd25': 'bluray', 'bd50': 'bluray',
        'webdl': 'webdl', 'web': 'webdl', 'webrip': 'webrip', 'webcap': 'webrip',
        'hdtv': 'hdtv', 'pdtv': 'hdtv', 'sdtv': 'hdtv', 'dsr': 'hdtv', 'tvrip': 'hdtv',
        'dvdrip': 'dvd', 'dvd': 'dvd', 'dvdr': 'dvd', 'dvd5': 'dvd', 'dvd9': 'dvd', 'dvdscr': 'dvd',
        'hdrip': 'hdrip', 'hdcam': 'cam', 'cam': 'cam', 'camrip': 'cam', 'telesync': 'cam', 'hdts': 'cam'}
_SERVICE = {'amzn': 'amzn', 'amazon': 'amzn', 'nf': 'nf', 'netflix': 'nf', 'dsnp': 'dsnp', 'dsny': 'dsnp',
            'hmax': 'max', 'max': 'max', 'atvp': 'atvp', 'hulu': 'hulu', 'pcok': 'pcok', 'pmtp': 'pmtp',
            'stan': 'stan', 'crav': 'crav', 'shahid': 'shahid', 'osn': 'osn', 'osnplus': 'osn',
            'itunes': 'itunes', 'it': 'itunes', 'roku': 'roku', 'ip': 'iplayer', 'iplayer': 'iplayer'}
_CODEC = {'x264': '264', 'h264': '264', 'avc': '264', 'x265': '265', 'h265': '265', 'hevc': '265', 'av1': 'av1'}
_EDITION = {'extended': 'extended', 'unrated': 'unrated', 'uncut': 'unrated', 'directors': 'directors',
            'dc': 'directors', 'imax': 'imax', 'theatrical': 'theatrical', 'remastered': 'remastered',
            'criterion': 'criterion', 'final': 'final'}
_NOISE = {'ar', 'ara', 'arabic', 'en', 'eng', 'english', 'srt', 'ass', 'ssa', 'vtt', 'sub', 'subs',
          'mkv', 'mp4', 'avi', 'm2ts', 'ts', 'proper', 'repack', 'real', 'internal', 'dl', 'hdr', 'hdr10',
          'dv', 'sdr', 'aac', 'ac3', 'eac3', 'ddp', 'dd', 'dts', 'truehd', 'atmos', '10bit', '8bit',
          'h', 'x', 'the', 'and', 'of', 'a', 'dexsubtitles', 'external', 'forced', 'default', 'sdh', 'hi'}
_ARABIC = re.compile('[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]')
_LATIN = re.compile('[A-Za-z]')
_ASS_TAG = re.compile(r'\{[^}]*\}|<[^>]*>')


def _clean(text):
    s = unquote(str(text or '')).lower()
    s = s.split('|', 1)[0].split('?', 1)[0]
    s = s.replace('\\', '/').rsplit('/', 1)[-1]
    s = re.sub(r'\.(srt|ass|ssa|vtt|sub|zip|mkv|mp4|avi|m2ts|ts|webm|mov)$', '', s)
    s = s.replace('web-dl', 'webdl').replace('web dl', 'webdl').replace('blu-ray', 'bluray')
    s = s.replace('dd+', 'ddp').replace('h.264', 'h264').replace('h.265', 'h265')
    s = re.sub(r'\bdirector\'?s\b', 'directors', s)
    return s


def _group(clean):
    """فريق الإصدار: آخر كلمة بعد «-» (Movie.2020.1080p.BluRay.x264-SPARKS)."""
    s = re.sub(r'([ ._-](ar|ara|arabic|en|eng|forced|sdh|hi))+$', '', clean)
    s = re.sub(r'\s*[\[(][^\])]*[\])]\s*$', '', s).strip()
    m = re.search(r'-([a-z0-9]{2,20})$', s)
    if not m:
        return ''
    g = m.group(1)
    if g in _RES or g in _SRC or g in _CODEC or g in _NOISE or g.isdigit():
        return ''
    return g


def release_info(text):
    clean = _clean(text)
    tokens = [t for t in re.split(r'[\s._\[\]()\-+,{}]+', clean) if t]
    info = {'res': '', 'src': '', 'service': '', 'codec': '', 'edition': set(), 'group': _group(clean),
            'words': set(), 'raw': tokens}
    for t in tokens:
        if t in _RES and not info['res']:
            info['res'] = _RES[t]
        elif t in _SRC:
            fam = _SRC[t]
            # webrip أدق من web: لو الاثنين موجودين نخلي webrip
            if not info['src'] or (info['src'] == 'webdl' and fam == 'webrip'):
                info['src'] = fam
        elif t in _SERVICE and not info['service']:
            info['service'] = _SERVICE[t]
        elif t in _CODEC and not info['codec']:
            info['codec'] = _CODEC[t]
        elif t in _EDITION:
            info['edition'].add(_EDITION[t])
        elif t not in _NOISE and not re.match(r'^(19|20)\d\d$', t) and not re.match(r'^s\d+e\d+$', t):
            info['words'].add(t)
    if info['group']:
        info['words'].discard(info['group'])
    return info


def has_release_hints(info):
    return bool(info['res'] or info['src'] or info['service'] or info['group'])


def name_score(video, sub):
    """0..100 تقريباً: قرب نسخة الترجمة من نسخة الفيديو. video و sub: نص أو ناتج release_info."""
    v = video if isinstance(video, dict) else release_info(video)
    s = sub if isinstance(sub, dict) else release_info(sub)
    score = 0.0
    if v['group'] and s['group']:
        score += 40 if v['group'] == s['group'] else 0
    if v['src'] and s['src']:
        if v['src'] == s['src']:
            score += 25
        elif {v['src'], s['src']} == {'webdl', 'webrip'}:
            score += 12
        else:
            score -= 10
    if v['service'] and s['service']:
        score += 12 if v['service'] == s['service'] else -4
    if v['res'] and s['res'] and v['res'] == s['res']:
        score += 8
    if v['codec'] and s['codec'] and v['codec'] == s['codec']:
        score += 3
    if v['edition'] or s['edition']:
        score += 10 if (v['edition'] & s['edition']) else -5
    if v['words'] and s['words']:
        inter = len(v['words'] & s['words'])
        union = len(v['words'] | s['words'])
        score += 15.0 * inter / union if union else 0
    return max(0.0, score)


def best_video_info(texts):
    """من عدة نصوص (اسم الملف، الرابط، عنوان العنصر) نختار اللي فيه أكثر معلومات نسخة."""
    best, best_n = release_info(''), -1
    for t in texts:
        if not t:
            continue
        info = release_info(t)
        n = sum(1 for k in ('res', 'src', 'service', 'codec', 'group') if info[k]) * 3 + len(info['words'])
        if n > best_n:
            best, best_n = info, n
    return best


def _decode(raw):
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw[2:].decode('utf-16-le' if raw[:2] == b'\xff\xfe' else 'utf-16-be', 'ignore')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as e:
        if e.start >= len(raw) - 3:   # العينة قطعت حرف في آخرها
            return raw.decode('utf-8', 'ignore')
        return raw.decode('cp1256', 'ignore')


def looks_arabic(raw, sample=60000):
    """نسبة الحروف العربية من الحروف (بعد شيل وسوم ASS و HTML والتوقيتات)."""
    if not raw:
        return False
    text = _ASS_TAG.sub(' ', _decode(raw[:sample]))
    if '[Events]' in text:
        text = text.split('[Events]', 1)[1]
    ar = len(_ARABIC.findall(text))
    la = len(_LATIN.findall(text))
    return ar >= 200 and ar >= 0.25 * (ar + la)
