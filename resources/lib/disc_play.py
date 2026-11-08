# -*- coding: utf-8 -*-
"""Blu-ray disc images over the internet (v5.10.543).

A debrid or usenet link to an untouched Blu-ray (AIOStreams marks them
BR-DISK) is often a whole .iso behind an address with no extension. Kodi
only treats a file as a disc image by its extension (CURL::IsDiscImage), so
it handed such a link to FFmpeg, which cannot read a disc image, and the
source failed. Wrapped as udf://<link>/BDMV/index.bdmv, Kodi opens it with
its Blu-ray reader instead and reads the image in ranges over HTTP: the
film plays, with its menus where the device and Kodi's Blu-ray setting
allow (CoreELEC).
"""
import re
from urllib.parse import quote, urlsplit

import xbmc

_DISC_TEXT = re.compile(r'(?<![a-z0-9])(?:iso|bdmv|br[ ._-]?disk|full[ ._-]?disc|bd(?:25|50|66|100)|'
                        r'complete[ ._-]?(?:uhd[ ._-]?)?blu[ ._-]?ray)(?![a-z0-9])', re.I)
_PLAIN_VIDEO = ('.mkv', '.mp4', '.m4v', '.avi', '.m2ts', '.mts', '.ts', '.mov', '.webm', '.wmv', '.mpg')


def _log(msg):
    xbmc.log('[DexHub] disc: %s' % msg, xbmc.LOGINFO)


def _text(ctx):
    bits = []
    for key, value in (ctx or {}).items():
        if isinstance(value, str) and any(w in key.lower() for w in ('title', 'name', 'file', 'label', 'release', 'desc')):
            bits.append(value)
    return ' '.join(bits)


def _probe(url):
    """Asks for the disc's volume descriptor (sector 16). Returns the address
    the file is served from (after redirects) when it is a disc image, else ''.
    Kodi sizes the image with a HEAD request, which some resolver links refuse
    (AIOStreams answers 405) while the address they redirect to answers it."""
    import time
    import urllib.request
    # v5.10.546: an AIOStreams usenet link answers only once the file is
    # ready, ~35 s after the first ask (The Batman, 8 Nov 2026); the old 10 s
    # wait gave up, so the disc went to FFmpeg without its Blu-ray menu.
    # Asking again after a timeout reaches the same, by then warmer, file.
    started = time.time()
    named, head, final = '', b'', ''
    for timeout in (45, 30):
        request = urllib.request.Request(url, headers={'Range': 'bytes=32768-34815', 'User-Agent': 'Kodi'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                named = answer.headers.get('Content-Disposition') or ''
                head = answer.read(2048)
                final = answer.geturl() or url
            break
        except Exception as exc:
            _log('probe failed after %.1f s: %s' % (time.time() - started, exc))
            if 'timed out' not in str(exc).lower() or xbmc.Monitor().abortRequested():
                return ''
    if not final:
        return ''
    _log('probe answered in %.1f s' % (time.time() - started))
    # a file named .iso, or ISO 9660 "CD001" / UDF "BEA01", "NSR0x" descriptors
    if re.search(r'\.(?:iso|img)\b', named, re.I) or any(
            mark in head[:16] for mark in (b'CD001', b'BEA01', b'NSR02', b'NSR03')):
        return final
    return ''


def on_coreelec():
    """CoreELEC and LibreELEC open a streamed disc image this way: Kodi on the
    Mac sat on its busy window reading one (7 Nov 2026), so elsewhere the link
    plays as it always did. v5.10.548: LibreELEC too (an x86 box handed a
    74 GB UHD .iso straight to FFmpeg: 'error probing input format')."""
    try:
        with open('/etc/os-release', encoding='utf-8') as handle:
            text = handle.read().lower()
        return 'coreelec' in text or 'libreelec' in text
    except Exception:
        return False


def wrap(url, ctx=None):
    """The address Kodi should open: a disc image as udf://…/BDMV/index.bdmv,
    anything else unchanged."""
    raw = str(url or '')
    if not raw.lower().startswith(('http://', 'https://')) or not on_coreelec():
        return raw
    link = raw.split('|', 1)[0]
    path = urlsplit(link).path.lower()
    if path.endswith(_PLAIN_VIDEO):
        return raw
    if not path.endswith(('.iso', '.img')) and not _DISC_TEXT.search(_text(ctx)):
        return raw
    final = _probe(link)
    if not final:
        return raw
    link = final
    _log('opening a Blu-ray image through udf')
    return 'udf://%s/BDMV/index.bdmv' % quote(link, safe='')
