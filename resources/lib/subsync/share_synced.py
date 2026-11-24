# -*- coding: utf-8 -*-
"""DEVICE_SYNCED_V1: after AutoSync timed a subtitle the viewer picked, send that synced text once to DexWorld.

The server (POST /subtitles/stremio/device_synced) translates it to Arabic on the account's AI when it is not
Arabic, or keeps it as it is when it is Arabic, and saves it for this title, named after the release it was timed
on, so the next viewer of the same release gets it first.

Never blocks playback (its own thread), never sends the video's link (the file name only), skipped for:
the setting off, no code or TV-BOX, AI subtitles, a subtitle that came from DexWorld itself, or one sent before.
Shared by Dex Hub and DexSubtitles (the engine's files are the same; host.py tells them apart).
"""
import hashlib
import io
import json
import os
import re
import threading
import urllib.request

import xbmc
import xbmcgui

from . import host

_SENT = set()
_REL_EXT = re.compile(r'\.(mkv|mp4|avi|m4v|ts|m2ts|webm)$', re.I)


def _log(msg):
    xbmc.log(host.log_prefix('share') + msg, xbmc.LOGINFO)


def _setting(*keys):
    a = host.addon()
    for k in keys:
        try:
            v = (a.getSetting(k) or '').strip()
            if v:
                return v
        except Exception:
            pass
    return ''


def _dexhub_code():
    """Dex Hub keeps the DexWorld code as its 'dexworld' AI service (ai.json, set_dexworld_code);
    DexSubtitles reads the same file when its own setting is empty (both add-ons are ours)."""
    try:
        import xbmcvfs
        path = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/ai.json')
        with io.open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return str((((data.get('services') or {}).get('dexworld')) or {}).get('key') or '').strip()
    except Exception:
        return ''


def _enabled():
    v = _setting('share_synced')
    return v.lower() != 'false'          # on unless turned off


def _ids():
    imdb = xbmc.getInfoLabel('VideoPlayer.IMDBNumber') or ''
    season = xbmc.getInfoLabel('VideoPlayer.Season') or ''
    episode = xbmc.getInfoLabel('VideoPlayer.Episode') or ''
    try:
        home = xbmcgui.Window(10000)
        imdb = imdb or home.getProperty('dexhub.sub.imdb_id') or ''
        season = season or home.getProperty('dexhub.sub.season') or ''
        episode = episode or home.getProperty('dexhub.sub.episode') or ''
    except Exception:
        pass
    return imdb, (season if str(season).isdigit() and int(season) > 0 else ''), (episode if str(episode).isdigit() and int(episode) > 0 else '')


def _filename(media_url):
    name = xbmc.getInfoLabel('Player.Filename') or ''
    if not name:
        base = str(media_url or '').split('|', 1)[0].split('?', 1)[0].rstrip('/')
        name = base.rsplit('/', 1)[-1]
    try:
        from urllib.parse import unquote
        name = unquote(name)
    except Exception:
        pass
    return name[:200] if _REL_EXT.search(name) or re.search(r'(1080p|2160p|720p|web|bluray|s\d{1,2}e\d{1,3})', name, re.I) else ''


def _lang(text):
    letters = re.findall(r'[A-Za-z؀-ۿ]', text[:20000])
    if not letters:
        return ''
    ar = sum(1 for c in letters if '؀' <= c <= 'ۿ')
    return 'ar' if ar / float(len(letters)) > 0.3 else 'en'


def send(synced_path, job, media_url):
    """Called once AutoSync applied a synced subtitle. Returns at once; the upload runs in its own thread."""
    try:
        if not _enabled():
            return
        code = _setting('api_key', 'dexworld_code', 'dexworld_api_key') or _dexhub_code()
        if not code or code.upper() == 'TV-BOX':
            return
        if job.get('is_ai') or re.search(r'\bAI\b|🤖', str(job.get('display_name') or '')):
            return
        src = str(job.get('source_url') or job.get('provider') or '')
        imdb, season, episode = _ids()
        if not re.match(r'^tt\d{5,12}$', imdb or ''):
            return
        with io.open(synced_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        lang = _lang(text)
        if lang == 'ar' and re.search(r'dexworld\.cc/subtitles|/subtitles/(download|uploaded|ai_public)/', src):
            return                        # an Arabic subtitle DexWorld already has
        key = hashlib.sha1((imdb + season + episode + text).encode('utf-8', 'replace')).hexdigest()
        if key in _SENT:
            return
        _SENT.add(key)
        server = (_setting('server_url', 'dexworld_base_url') or 'https://dexworld.cc').rstrip('/')
        body = {'apiKey': code, 'srt': text, 'lang': lang or 'en', 'imdbId': imdb, 'season': season, 'episode': episode,
                'filename': _filename(media_url), 'subName': str(job.get('display_name') or '')[:120],
                'title': xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or xbmc.getInfoLabel('VideoPlayer.Title') or '',
                'method': 'autosync'}
        threading.Thread(target=_post, args=(server, body), name='DexShareSynced', daemon=True).start()
    except Exception as e:
        _log('skipped: %s' % e)


def _post(server, body):
    try:
        req = urllib.request.Request(server + '/subtitles/stremio/device_synced', data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json', 'User-Agent': host.AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read().decode('utf-8', 'replace') or '{}')
        _log('%s %s → %s' % (body['imdbId'], body['lang'], res.get('status') or res.get('error')))
        if res.get('status') == 'queued':
            host_msg = 'الترجمة المتزامنة تُترجم الحين وتنحفظ لنسختك' if not host.IS_DEXHUB else 'Synced subtitle sent: it is being translated and saved for your release'
            xbmcgui.Dialog().notification(host.NAME, host.tr(host_msg), xbmcgui.NOTIFICATION_INFO, 3500)
    except Exception as e:
        _log('send failed: %s' % str(e)[:120])
