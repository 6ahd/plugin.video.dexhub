# -*- coding: utf-8 -*-
"""Subtitle sync for a Blu-ray disc (v5.10.581).

AutoSync times a subtitle file from the timing index (Cues) inside the playing MKV.
A disc has none: its own subtitles are pictures inside the m2ts streams, and their
times show only by reading the whole film. A REMUX of the same disc is its main movie
in an MKV, on the same clock, so the index of a REMUX in this title's sources list
serves as the disc's timing reference. Only that index is read (a few requests,
under 2 MB); the REMUX is never played.

Picked from the sources list Dex Hub keeps for the playing title (the player bar's
Sources rows): a REMUX in an MKV, not a disc itself, of the disc's own resolution
(UHD with UHD, 1080p with 1080p: a UHD release may differ from the 1080p by a few
seconds, a studio logo). A library server's row (Emby, Jellyfin, Plex) comes first,
then a direct link, then a Debrid row resolved to its link. A Usenet row (it would
start a download) and a Kodi add-on's row (it plays itself) are never used.
"""
import re

import xbmc

_DISC_TEXT = re.compile(r'\b(iso|bdmv|complete\.?(uhd\.?)?blu-?ray|full\s*disc|disc\s*image|bd(25|50|66|100))\b', re.I)
_UHD = re.compile(r'\b(2160p?|uhd|4k)\b', re.I)
_HD = re.compile(r'\b1080p?\b', re.I)
_LIBRARY = ('emby', 'jellyfin', 'plex')


def _log(msg):
    xbmc.log('[DexHub] subtitle sync, disc: %s' % msg, xbmc.LOGINFO)


def is_disc(url):
    """A disc playing in Kodi: bluray:// (its menus or a playlist), udf://, an image or a
    BDMV folder."""
    path = str(url or '').split('|', 1)[0].split('?', 1)[0].lower()
    if not path:
        return False
    return (path.startswith(('bluray://', 'udf://')) or path.endswith(('.iso', '.img', '/index.bdmv', '/bdmv'))
            or '/bdmv/' in path)


def _text(row):
    return ' '.join(str(row.get(k) or '') for k in ('l', 'n', 'd', 'l2')).replace('.', ' ')


def _uhd_disc(current):
    text = _text(current or {})
    if _UHD.search(text):
        return True
    if _HD.search(text):
        return False
    try:
        return xbmc.getInfoLabel('VideoPlayer.VideoResolution') == '4K'
    except Exception:
        return False


def _rows():
    try:
        from ..skinui import osd_sources
        rows, _has = osd_sources.rows()
        return rows or []
    except Exception as exc:
        _log('sources list not read (%s)' % exc)
        return []


def _remuxes(rows):
    """The REMUX rows that fit the playing disc, best first."""
    current = next((r for r in rows if r.get('current')), {})
    uhd = _uhd_disc(current)
    picked = []
    for row in rows:
        if row.get('current') or not row.get('k'):
            continue
        text = _text(row)
        if not re.search(r'\bremux\b', text, re.I) or _DISC_TEXT.search(text):
            continue
        if re.search(r'\b(mp4|m2ts|ts)\b', text, re.I) and not re.search(r'\bmkv\b', text, re.I):
            continue
        if bool(_UHD.search(text)) != uhd:
            continue
        picked.append(row)
    return picked, uhd


def _link(row):
    """The row's own address (a library or direct link, or a Debrid row resolved), or ''."""
    try:
        from .. import cache_store
        ctx = cache_store.get('stream', str(row.get('k') or '')) or {}
    except Exception:
        ctx = {}
    if not isinstance(ctx, dict) or not ctx:
        return '', 9
    try:
        from .. import usenet_source, kodi_sources
        if usenet_source.is_row(ctx) or kodi_sources.is_kodi_row(ctx):
            return '', 9
    except Exception:
        pass
    try:
        from .. import easynews_source
        if easynews_source.is_row(ctx):
            return '', 9
    except Exception:
        pass
    try:
        from .. import debrid_source
        if debrid_source.is_row(ctx):
            return (debrid_source.resolve(ctx) or ''), 3
    except Exception as exc:
        _log('a Debrid row not resolved (%s)' % exc)
        return '', 9
    url = str(ctx.get('stream_url') or '')
    if not url.lower().startswith(('http://', 'https://')) or is_disc(url):
        return '', 9
    kind = str(ctx.get('server_type') or '').lower()
    return url, (1 if kind in _LIBRARY else 2)


def candidates(media_url, limit=4):
    """[(link, label)] of REMUX MKVs of this disc to read the timing index from, best first."""
    if not is_disc(media_url):
        return []
    picked, uhd = _remuxes(_rows())
    if not picked:
        _log('no %s REMUX in the sources list' % ('UHD' if uhd else '1080p'))
        return []
    ranked = []
    for row in picked:
        # the order of a library row, a direct link, a Debrid row: links asked only as needed
        ctx_rank = 1 if any(name in _text(row).lower() or name in str(row.get('p') or '').lower()
                            or name in str(row.get('id') or '').lower() for name in _LIBRARY) else 2
        ranked.append((ctx_rank, row))
    ranked.sort(key=lambda pair: pair[0])
    out = []
    for _rank, row in ranked:
        if len(out) >= limit:
            break
        url, _kind = _link(row)
        if url:
            out.append((url, str(row.get('l') or row.get('n') or 'REMUX')[:80]))
    if not out:
        _log('%d REMUX row(s), none with a link to read' % len(picked))
    return out
