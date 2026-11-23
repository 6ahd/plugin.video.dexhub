# -*- coding: utf-8 -*-
"""v5.10.598: a title's theme music on its page, as Flix plays it.

A series: its theme from tvthemes.plexapp.com/<tvdb>.mp3. A film (or a series with none there):
the main title of its soundtrack album on the iTunes Search API (no key), its 30 s preview.
It plays on the title page only (Kodi's video info window, where every skin shows Dex Hub's
titles), quietly, after the page rested a moment, and never over anything that plays. Leaving
the page fades it out; a trailer or the film itself simply takes the player.

Kodi has one volume for everything, so the theme lowers it to its own level and puts it back
when it stops (a marker file puts it back after a crash too). Off by default:
Control Centre › Trailers › Theme music.
"""
import hashlib
import json
import os
import re
import threading
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.dexhub'
REST_S = 0.35                   # the page shown this long before the theme sounds (it is looked for at once)
FADE_STEPS, FADE_MS = 6, 180    # short: leaving the page must sound like leaving it
PAGE = 'Window.IsVisible(movieinformation)'
_FOUND = {}                     # key -> url ('' = none), for this run
_STARTED = set()                # every theme address this run started
_AT = {}                        # key -> when it was found
_BAD = re.compile(r'karaoke|tribute|piano|cover|lullab|8-bit|music box|inspired by|made famous', re.I)
_OST = re.compile(r'soundtrack|\bost\b|score|motion picture|original series|television', re.I)
_MAIN = re.compile(r'main title|main theme|\btheme\b|opening|overture|prologue', re.I)


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] theme music: %s' % msg, level)


def _setting(key, default=''):
    try:
        return xbmcaddon.Addon(ADDON_ID).getSetting(key) or default
    except Exception:
        return default


def enabled():
    return _setting('theme_music', 'true').lower() == 'true'


def level():
    try:
        return max(5, min(100, int(float(_setting('theme_music_volume', '60')))))
    except Exception:
        return 60


def _get(url, timeout=6, method='GET'):
    req = Request(url, headers={'User-Agent': 'DexHub'}, method=method)
    with urlopen(req, timeout=timeout) as r:
        return r.status, (r.read() if method == 'GET' else b'')


def _words(text):
    return [w for w in re.sub(r'[^a-z0-9 ]+', ' ', (text or '').lower()).split() if len(w) > 1 and w not in ('the', 'of', 'and')]


def _itunes(title, year, series):
    """The preview of a soundtrack album's main title, or ''."""
    want = _words(title)
    if not want:
        return ''
    _s, raw = _get('https://itunes.apple.com/search?term=%s&entity=album&limit=25' % quote('%s soundtrack' % title))
    albums = []
    for a in (json.loads(raw.decode('utf-8', 'replace')) or {}).get('results') or []:
        name = a.get('collectionName') or ''
        if _BAD.search(name) or not _OST.search(name):
            continue
        have = _words(name)
        if not all(w in have for w in want):
            continue
        try:
            gap = int(str(a.get('releaseDate') or '')[:4]) - int(year)
        except Exception:
            gap = 0
        if (series and gap < -1) or (not series and abs(gap) > 2):
            continue
        albums.append((abs(gap), len(have), a))
    if not albums:
        return ''
    album = sorted(albums, key=lambda x: x[:2])[0][2]
    _s, raw = _get('https://itunes.apple.com/lookup?id=%s&entity=song' % album.get('collectionId'))
    songs = [x for x in (json.loads(raw.decode('utf-8', 'replace')) or {}).get('results') or []
             if x.get('wrapperType') == 'track' and x.get('previewUrl')]
    if not songs:
        return ''
    title_l = (title or '').lower()
    pick = next((x for x in songs if _MAIN.search(x.get('trackName') or '')), None) or \
        next((x for x in songs if title_l and title_l in (x.get('trackName') or '').lower()), None) or songs[0]
    return pick['previewUrl']


def _tvdb_of(tmdb):
    """A series' TVDb id from TMDb (the title page gives its TMDb and IMDb ids only)."""
    try:
        from . import tmdb_direct
        data = tmdb_direct._request('/tv/%s/external_ids' % tmdb, timeout=6) or {}
        return str(data.get('tvdb_id') or '')
    except Exception as e:
        _log('TVDb id of %s: %s' % (tmdb, e))
        return ''


KEEP_S = 30 * 86400


def _store():
    return os.path.join(xbmcvfs.translatePath('special://profile/addon_data/%s/' % ADDON_ID), 'theme_found.json')


def _load():
    if _FOUND.get('__loaded'):
        return
    _FOUND['__loaded'] = True
    try:
        with open(_store(), 'r', encoding='utf-8') as f:
            for k, (url, at) in json.load(f).items():
                if time.time() - at < (KEEP_S if url else 86400):
                    _FOUND.setdefault(k, url)
                    _AT[k] = at
    except Exception:
        pass


def _keep(key, url):
    _AT[key] = time.time()
    try:
        data = dict((k, [_FOUND[k], _AT.get(k, time.time())]) for k in _FOUND if k != '__loaded')
        tmp = _store() + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(dict(list(data.items())[-600:]), f)
        os.replace(tmp, _store())
    except Exception:
        pass


def _is_latin(text):
    return all(ord(c) < 0x250 for c in text or '')


def _latin_title(title, series, tmdb, imdb=''):
    """The title's English or original name when the page shows it in another script (an Arabic
    name finds no soundtrack album), from TMDb by its TMDb id, else by its IMDb id. '' when it
    could not be had."""
    if not title or _is_latin(title):
        return title
    try:
        from . import tmdb_direct
        rows = []
        if str(tmdb).isdigit():
            rows.append(tmdb_direct._request('/%s/%s' % ('tv' if series else 'movie', tmdb), {'language': 'en-US'},
                                             timeout=6) or {})
        elif str(imdb).startswith('tt'):
            found = tmdb_direct._request('/find/%s' % imdb, {'external_source': 'imdb_id', 'language': 'en-US'},
                                         timeout=6) or {}
            rows.extend((found.get('tv_results') if series else found.get('movie_results')) or [])
            rows.extend((found.get('movie_results') if series else found.get('tv_results')) or [])
        for data in rows:
            for name in (data.get('title'), data.get('name'), data.get('original_title'), data.get('original_name')):
                if name and _is_latin(name):
                    return name
    except Exception as e:
        _log('name of "%s": %s' % (title, e))
    _log('no English name for "%s" (tmdb %r, imdb %r)' % (title, tmdb, imdb))
    return ''


def find(title, year='', series=False, tvdb='', tmdb='', imdb=''):
    """The address of the title's theme, or ''. Kept (a miss too: for a day)."""
    _load()
    key = 't3|%s|%s|%s' % ((title or '').strip().lower(), year, 1 if series else 0)
    if key in _FOUND:
        return _FOUND[key]
    url = ''
    try:
        if series and not str(tvdb).isdigit() and str(tmdb).isdigit():
            tvdb = _tvdb_of(tmdb)
        if series and str(tvdb).isdigit():
            candidate = 'https://tvthemes.plexapp.com/%s.mp3' % tvdb
            try:
                if _get(candidate, 4, 'HEAD')[0] == 200:
                    url = candidate
            except Exception:
                pass
        if not url:
            name = _latin_title(title, series, tmdb, imdb)
            if not name:
                return ''                           # not kept: asked again next time
            url = _itunes(name, year, series)
    except Exception as e:
        _log('lookup for "%s": %s' % (title, e.__class__.__name__))
    _FOUND[key] = url
    _keep(key, url)
    return url


# ------------------------------------------------------------------ the theme as a file
FILES_KEPT = 40
_FETCH = threading.Lock()


def local(url):
    """The theme as a file on this device, '' when it could not be fetched. Kodi opens an address
    behind its loading dialog, which takes the remote for seconds while the sound buffers (the
    page hung as it opened, the user, 22 Nov 2026); a file opens at once. Fetched off the page's
    thread (about 1 MB), the last 40 kept."""
    if not url:
        return ''
    folder = os.path.join(xbmcvfs.translatePath('special://profile/addon_data/%s/' % ADDON_ID), 'themes')
    ext = '.m4a' if '.m4a' in url.lower() else '.mp3'
    path = os.path.join(folder, hashlib.sha1(url.encode('utf-8')).hexdigest()[:20] + ext)
    with _FETCH:
        try:
            if os.path.getsize(path) > 20000:
                os.utime(path, None)
                return path
        except OSError:
            pass
        try:
            os.makedirs(folder, exist_ok=True)
            req = Request(url, headers={'User-Agent': 'DexHub (Kodi)'})
            size = 0
            with urlopen(req, timeout=10) as r, open(path + '.part', 'wb') as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 12 * 1024 * 1024:
                        raise ValueError('too long')
                    f.write(chunk)
            if size < 20000:
                raise ValueError('too short')
            os.replace(path + '.part', path)
            kept = sorted((os.path.getmtime(os.path.join(folder, n)), n) for n in os.listdir(folder))
            for _at, name in kept[:-FILES_KEPT]:
                os.remove(os.path.join(folder, name))
            return path
        except Exception as e:
            _log('theme not fetched: %s' % e.__class__.__name__)
            try:
                os.remove(path + '.part')
            except OSError:
                pass
            return ''


def find_ready(*args):
    """find(), and the theme fetched: (address, file)."""
    url = find(*args)
    return url, local(url)


# ------------------------------------------------------------------ Kodi's volume, borrowed
def _rpc(method, params=None):
    try:
        raw = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}))
        return (json.loads(raw) or {}).get('result')
    except Exception:
        return None


def _volume():
    try:
        return int((_rpc('Application.GetProperties', {'properties': ['volume']}) or {}).get('volume'))
    except Exception:
        return None


_QUIET = {'until': 0.0}


def _set_volume(percent):
    # the skin keeps its volume bar away while this is set (skin.dexhub 3.28.234): the theme
    # moves Kodi's volume by itself, and the bar showed before every theme
    try:
        xbmcgui.Window(10000).setProperty('dexhub.theme.volume', '1')
        _QUIET['until'] = time.time() + 2.0
    except Exception:
        pass
    xbmc.executebuiltin('SetVolume(%d)' % max(0, min(100, int(percent))), True)


def _quiet_tick():
    if _QUIET['until'] and time.time() > _QUIET['until']:
        _QUIET['until'] = 0.0
        try:
            xbmcgui.Window(10000).clearProperty('dexhub.theme.volume')
        except Exception:
            pass


def _marker():
    folder = xbmcvfs.translatePath('special://profile/addon_data/%s/' % ADDON_ID)
    return os.path.join(folder, 'theme_volume.json')


def restore_after_crash():
    """Kodi's volume put back when a theme's lowered level was left behind."""
    try:
        with open(_marker(), 'r') as f:
            saved = json.load(f)
        if _volume() == int(saved['applied']):
            _set_volume(int(saved['saved']))
        os.remove(_marker())
    except Exception:
        pass


class _Theme(object):
    def __init__(self):
        self.url = ''
        self.saved = None
        self.applied = None
        self.began = 0.0            # when play was asked
        self.heard = False          # the player took it

    def playing(self):
        try:
            return bool(self.url) and xbmc.Player().isPlayingAudio() and xbmc.Player().getPlayingFile() == self.url
        except Exception:
            return False

    def start(self, url, title, source=''):
        now = _volume()
        want = level()
        source = source or url
        self.url = url
        self.began, self.heard = time.time(), False
        _STARTED.add(url)
        if now is not None and now > want:
            self.saved, self.applied = now, want
            try:
                with open(_marker(), 'w') as f:
                    json.dump({'saved': now, 'applied': want}, f)
            except Exception:
                pass
            _set_volume(want)
        _opening(True)
        item = xbmcgui.ListItem(label=title, offscreen=True)
        item.setProperty('dexhub_theme', '1')
        try:
            item.getMusicInfoTag().setTitle(title)
        except Exception:
            pass
        xbmc.Player().play(url, item, True)
        _log('"%s" playing (%s)' % (title, 'Plex themes' if 'plexapp' in source else 'soundtrack preview'))

    def _restore(self):
        if self.saved is not None:
            now = _volume()
            if now is None:
                return                              # Kodi is closing: the marker puts it back next time
            if now in (self.applied, 0) or (self.applied is not None and now < self.applied):
                _set_volume(self.saved)             # a level the viewer set meanwhile is left alone
                xbmc.sleep(60)
                if _volume() != self.saved:
                    return                          # not taken (Kodi closing): the marker stays
            try:
                os.remove(_marker())
            except Exception:
                pass
        self.saved = self.applied = None

    def stop(self, fade=True):
        if not self.playing() and self.url and not self.heard:
            xbmc.sleep(400)                 # asked a moment ago: let the player take it, then stop it
        if self.playing():
            if fade:
                start = _volume() or 0
                for n in range(FADE_STEPS - 1, -1, -1):
                    _set_volume(start * n // FADE_STEPS)
                    xbmc.sleep(FADE_MS // FADE_STEPS)
            xbmc.Player().stop()
            xbmc.sleep(120)
            if fade and self.saved is None:
                _set_volume(start)
        self._restore()
        self.url = ''
        _opening(False)

    def taken(self):
        """Something else took the player (a trailer, the film): the volume goes back at once."""
        if not self.url:
            return
        if self.playing():
            if not self.heard:
                self.heard = True
                _log('sounding %.1f s after it was asked' % (time.time() - self.began))
            # Kodi keeps its loading dialog up while the theme's address buffers (3.7 s on the Mac,
            # 21 Nov: the mark showed from the moment a fixed 2.5 s ran out): the skin keeps it
            # away until Kodi itself closes it
            if self.began:
                spent = time.time() - self.began
                busy = xbmc.getCondVisibility('Window.IsActive(busydialog) | Window.IsActive(busydialognocancel)')
                if (spent > 1.0 and not busy) or spent > 20:
                    _opening(False)
                    self.began = 0.0
            return
        # the player needs a moment to take the address: not "taken" before it was heard,
        # unless it never started at all
        if self.heard or time.time() - self.began > 8:
            self._restore()
            self.url = ''
            _opening(False)


def _opening(on):
    """The skin keeps its loading card away while this is set (it does the same for a trailer)."""
    try:
        home = xbmcgui.Window(10000)
        if on:
            home.setProperty('dhs.preview.opening', '1')
            home.setProperty('dexhub.theme.opening', '1')
        elif home.getProperty('dexhub.theme.opening'):
            home.clearProperty('dexhub.theme.opening')
            home.clearProperty('dhs.preview.opening')
    except Exception:
        pass


def _ahead(rest):
    """The cursor rests on a title outside its page: its theme is looked for now, so the page
    sounds at once when it opens."""
    t = _page_title()
    key = '%s|%s' % (t['title'], t['year'])
    if not t['title'] or not (t['year'] or t['tmdb']):
        rest['key'] = ''
        return
    if key != rest['key']:
        rest['key'], rest['at'] = key, time.time()
        return
    if key == rest['done'] or time.time() - rest['at'] < 0.5:
        return
    rest['done'] = key
    threading.Thread(target=find_ready, args=(t['title'], t['year'], t['series'], t['tvdb'], t['tmdb'], t['imdb']),
                     name='DexHub-theme-ahead', daemon=True).start()


def _orphan():
    try:
        p = xbmc.Player()
        return p.isPlayingAudio() and p.getPlayingFile() in _STARTED
    except Exception:
        return False


def _page_title():
    info = xbmc.getInfoLabel
    series = bool(info('ListItem.TVShowTitle')) or info('ListItem.DBTYPE') in ('tvshow', 'season', 'episode')
    title = info('ListItem.TVShowTitle') or info('ListItem.OriginalTitle') or info('ListItem.Title') or info('ListItem.Label')
    return {'title': title, 'year': info('ListItem.Year'), 'series': series,
            'tvdb': info('ListItem.UniqueID(tvdb)') or info('ListItem.Property(tvdb_id)'),
            'tmdb': info('ListItem.UniqueID(tmdb)') or info('ListItem.Property(tmdb_id)'),
            'imdb': info('ListItem.UniqueID(imdb)') or info('ListItem.IMDBNumber') or info('ListItem.Property(imdb_id)')}


def service_loop(monitor):
    """Watch for the title page; play its theme while it shows and nothing else plays."""
    restore_after_crash()
    theme = _Theme()
    since, page, tried, page_id = 0.0, None, False, ''
    rest = {'key': '', 'at': 0.0, 'done': ''}
    home = xbmcgui.Window(10000)
    while not monitor.abortRequested():
        try:
            _quiet_tick()
            on_page = xbmc.getCondVisibility(PAGE)
            if not on_page or not enabled():
                if theme.url:
                    if theme.playing() or not theme.heard:
                        theme.stop(fade=True)
                    else:
                        theme.taken()
                elif _orphan():
                    # a theme still sounding with nobody holding it (the page left in the moment
                    # it started): off at once, it never plays outside the title page
                    xbmc.Player().stop()
                since, page, tried, page_id = 0.0, None, False, ''
                if enabled() and not xbmc.getCondVisibility('Player.HasMedia'):
                    _ahead(rest)
            else:
                # another title opened from this page (a related one): Dex Hub names each page
                now_id = home.getProperty('dhs.tp.id')
                if page is not None and now_id and now_id != page_id:
                    # the page's name changed. Right after a page opens it still holds the page
                    # before (or none yet), so the title itself says whether this is another one
                    # (21 Nov: the first name read was empty, and a related title opened from the
                    # page kept the old theme playing)
                    t = _page_title()
                    same = (not t['title']) or (t['title'], t['year']) == (page['title'], page['year'])
                    page_id = now_id
                    if not same:
                        _log('"%s" opened from the page of "%s"' % (t['title'], page['title']))
                        if theme.url:
                            theme.stop(fade=True)
                        page, tried = None, False
                # the page's title, read once when it opens: inside the page the cursor moves over
                # its cast and episodes, and "the item under the cursor" is no longer the title
                if page is None:
                    t = _page_title()
                    if t['title']:
                        page, since, tried, page_id = t, time.time(), False, now_id
                        answer = {}
                        page['answer'] = answer

                        def look(p=t, out=answer):
                            if not _is_latin(p['title']) and not (p['tmdb'] or p['imdb']):
                                # the page's ids come a moment after it opens
                                for _n in range(8):
                                    xbmc.sleep(250)
                                    again = _page_title()
                                    if again['title'] == p['title'] and (again['tmdb'] or again['imdb']):
                                        p.update(tmdb=again['tmdb'], imdb=again['imdb'], tvdb=again['tvdb'])
                                        break
                            url, out['file'] = find_ready(p['title'], p['year'], p['series'], p['tvdb'], p['tmdb'], p['imdb'])
                            out['url'] = url
                        threading.Thread(target=look, name='DexHub-theme-find', daemon=True).start()
                theme.taken()
                if (page and not tried and 'url' in page['answer'] and time.time() - since >= REST_S
                        and not xbmc.getCondVisibility('Player.HasMedia')):
                    tried = True
                    url = page['answer']['url']
                    if not url:
                        _log('none found for "%s"' % page['title'])
                    else:
                        theme.start(page['answer'].get('file') or url, page['title'], url)
                        _log('started %.1f s after the page opened' % (time.time() - since))
        except Exception as e:
            _log('watch: %s' % e, xbmc.LOGWARNING)
        if monitor.waitForAbort(0.2):
            break
    # Kodi is closing: nothing is touched now (a volume set while it closes is not kept, and the
    # lowered level was saved as Kodi's own, 21 Nov); the marker puts the level back at the next start


def start_service():
    threading.Thread(target=lambda: service_loop(xbmc.Monitor()), name='DexHub-theme-music', daemon=True).start()
