# -*- coding: utf-8 -*-
"""The name of the release being played, for DexWorld's subtitle search (RELEASE_HINT_V1,
Dex Hub 5.10.600, as DexSubtitles' service.py does).

DexWorld orders its subtitles by ``?filename=``: the ones made for the same release first, CAM
and TS last. Only the file's NAME is sent, never its link (debrid and AIOStreams links carry
tokens), and only a name that looks like a release (1080p, WEB-DL, BluRay, S01E02...): anything
else sends nothing, as before.
"""
import re
from urllib.parse import parse_qsl, unquote, unquote_plus, urlsplit

import xbmc

_TAG = re.compile(r'(2160p|1080p|720p|480p|\b4k\b|\buhd\b|bluray|blu-ray|bdrip|brrip|remux|web-?dl|webrip|'
                  r'hdtv|dvdrip|x26[45]|h\.?26[45]|hevc|\bav1\b|s\d{1,2}e\d{1,3})', re.I)
_TOKENISH = re.compile(r'[A-Za-z0-9_-]{32,}')
_LOGGED = {'name': ''}


def _from_link(link):
    """The file's name inside a link: a filename= in its query, else its path's last part."""
    out = []
    link = str(link or '').split('|', 1)[0].strip()
    if not link:
        return out
    try:
        parts = urlsplit(link)
        out += [unquote_plus(v) for k, v in parse_qsl(parts.query)
                if k.lower() in ('filename', 'name', 'file', 'title')]
        out.append(unquote(parts.path).rstrip('/').rsplit('/', 1)[-1])
    except Exception:
        pass
    return out


def _clean(name):
    name = str(name or '').split('|', 1)[0].split('?', 1)[0].split('#', 1)[0]
    name = re.sub(r'\s+', ' ', name).strip()[:200]
    if len(name) < 8 or '://' in name or '/' in name or '\\' in name:
        return ''
    if _TOKENISH.search(name) or not _TAG.search(name):
        return ''
    return name


def hint(ctx=None):
    """The playing release's name, or ''. From the chosen stream when the context has it, else
    from the player, only while a video plays and the context is the player's own (a search made
    before a source is picked has no release yet, and the player may still hold another video)."""
    ctx = ctx or {}
    names = [ctx.get('filename'), ctx.get('stream_filename'),
             ((ctx.get('behaviorHints') or ctx.get('behavior_hints') or {}).get('filename')
              if isinstance(ctx.get('behaviorHints') or ctx.get('behavior_hints'), dict) else '')]
    names += _from_link(ctx.get('stream_url'))
    if ctx.get('subtitle_service') or ctx.get('all_subtitle_addons'):
        try:
            player = xbmc.Player()
            if player.isPlayingVideo():
                names.append(xbmc.getInfoLabel('Player.Filename'))
                names += _from_link(player.getPlayingFile())
                try:
                    names.append(player.getPlayingItem().getLabel())
                except Exception:
                    pass
        except Exception:
            pass
    for name in names:
        name = _clean(name)
        if name:
            if _LOGGED['name'] != name:
                _LOGGED['name'] = name
                xbmc.log('[DexHub] release hint: %s' % name, xbmc.LOGINFO)
            return name
    return ''


def is_dexworld(provider):
    """DexWorld's subtitle add-on (its manifest sits under /subtitles/stremio/<code>/)."""
    provider = provider or {}
    for key in ('base_url', 'transport_url', 'manifest_url', 'url', 'transportUrl'):
        if '/subtitles/stremio/' in str(provider.get(key) or ''):
            return True
    return False
