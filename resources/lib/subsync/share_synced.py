# -*- coding: utf-8 -*-
"""DEVICE_SYNCED_V1: after AutoSync timed a subtitle the viewer picked, send that synced text once to DexWorld.

The server (POST /subtitles/stremio/device_synced) translates it to Arabic on the account's AI when it is not
Arabic, or keeps it as it is when it is Arabic, and saves it for this title, named after the release it was timed
on, so the next viewer of the same release gets it first.

Never blocks playback (its own thread), never sends the video's link (the file name only), skipped for:
the setting off, no code or TV-BOX, AI subtitles, a subtitle that came from DexWorld itself, or one sent before.
Shared by Dex Hub and DexSubtitles (the engine's files are the same; host.py tells them apart).

v5.10.609: upload() sends one now, from the player's buttons (the Sync subtitle list's "Upload the
synced subtitle", the Translate subtitle list's "Upload for a full translation"), and says how it went;
a subtitle sent once (by either way) is not sent again (share_sent.json in the add-on's profile).
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


def _code():
    return _setting('api_key', 'dexworld_code', 'dexworld_api_key') or _dexhub_code()


def _server():
    return (_setting('server_url', 'dexworld_base_url') or 'https://dexworld.cc').rstrip('/')


def _key(ids, text):
    return hashlib.sha1((''.join(ids) + text).encode('utf-8', 'replace')).hexdigest()


def _sent_path():
    import xbmcvfs
    return os.path.join(xbmcvfs.translatePath(host.addon().getAddonInfo('profile')), 'share_sent.json')


def _sent_keys():
    try:
        with io.open(_sent_path(), 'r', encoding='utf-8') as f:
            keys = json.load(f)
        return keys if isinstance(keys, list) else []
    except Exception:
        return []


def _was_sent(key):
    return key in _SENT or key in _sent_keys()


def _remember(key):
    """The automatic upload (the service) and the buttons (a plugin call each) share it."""
    _SENT.add(key)
    try:
        keys = _sent_keys()
        if key in keys:
            return
        path = _sent_path()
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with io.open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump((keys + [key])[-500:], f)
        os.replace(path + '.tmp', path)
    except Exception:
        pass


def _body(code, text, lang, ids, media_url, sub_name, method):
    imdb, season, episode = ids
    return {'apiKey': code, 'srt': text, 'lang': lang or 'en', 'imdbId': imdb, 'season': season, 'episode': episode,
            'filename': _filename(media_url), 'subName': str(sub_name or '')[:120],
            'title': xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or xbmc.getInfoLabel('VideoPlayer.Title') or '',
            'method': method}


def _request(server, body):
    """One upload: (the server's status, its error)."""
    import urllib.error
    req = urllib.request.Request(server + '/subtitles/stremio/device_synced', data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json', 'User-Agent': host.AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read().decode('utf-8', 'replace') or '{}')
    except urllib.error.HTTPError as e:
        try:
            res = json.loads(e.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            res = {}
        return '', str(res.get('error') or res.get('message') or 'HTTP %s' % e.code)[:160]
    return str(res.get('status') or ''), str(res.get('error') or '')[:160]


def ready():
    """'' when an upload can go now, or why not: 'code' (no DexWorld code, or TV-BOX's), 'imdb' (the
    title's IMDb id is not known)."""
    code = _code()
    if not code or code.upper() == 'TV-BOX':
        return 'code'
    if not re.match(r'^tt\d{5,12}$', _ids()[0] or ''):
        return 'imdb'
    return ''


def upload(text, sub_name, media_url, method, lang='', extra=None):
    """v5.10.609: send ``text`` now, from a button. (the server's status, '') when it went, ('again', '')
    when this subtitle went before, ('', why) when it did not ('code', 'imdb', 'network' or the
    server's error)."""
    why = ready()
    if why:
        return '', why
    ids = _ids()
    key = _key(ids, text)
    if _was_sent(key):
        return 'again', ''
    body = _body(_code(), text, lang or _lang(text), ids, media_url, sub_name, method)
    body.update(extra or {})
    try:
        status, error = _request(_server(), body)
    except Exception as e:
        _log('%s %s: send failed: %s' % (ids[0], method, str(e)[:120]))
        return '', 'network'
    _log('%s %s %s (%s) → %s' % (ids[0], body['lang'], method, body['subName'][:60], status or error))
    if error or not status:
        return '', error or 'network'
    _remember(key)
    return status, ''


def send(synced_path, job, media_url):
    """Called once AutoSync applied a synced subtitle. Returns at once; the upload runs in its own thread."""
    try:
        if not _enabled():
            return
        code = _code()
        if not code or code.upper() == 'TV-BOX':
            return
        if job.get('is_ai') or re.search(r'\bAI\b|🤖', str(job.get('display_name') or '')):
            return
        src = str(job.get('source_url') or job.get('provider') or '')
        ids = _ids()
        if not re.match(r'^tt\d{5,12}$', ids[0] or ''):
            return
        with io.open(synced_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
        lang = _lang(text)
        if lang == 'ar' and re.search(r'dexworld\.cc/subtitles|/subtitles/(download|uploaded|ai_public)/', src):
            return                        # an Arabic subtitle DexWorld already has
        key = _key(ids, text)
        if _was_sent(key):
            return
        _SENT.add(key)
        body = _body(code, text, lang, ids, media_url, job.get('display_name'), 'autosync')
        threading.Thread(target=_post, args=(_server(), body, key), name='DexShareSynced', daemon=True).start()
    except Exception as e:
        _log('skipped: %s' % e)


def _post(server, body, key=''):
    try:
        status, error = _request(server, body)
        _log('%s %s → %s' % (body['imdbId'], body['lang'], status or error))
        if status and not error and key:
            _remember(key)
        if status == 'queued':
            host_msg = 'الترجمة المتزامنة تُترجم الحين وتنحفظ لنسختك' if not host.IS_DEXHUB else 'Synced subtitle sent: it is being translated and saved for your release'
            xbmcgui.Dialog().notification(host.NAME, host.tr(host_msg), xbmcgui.NOTIFICATION_INFO, 3500)
    except Exception as e:
        _log('send failed: %s' % str(e)[:120])
