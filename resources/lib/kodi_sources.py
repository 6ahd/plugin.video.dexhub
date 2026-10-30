# -*- coding: utf-8 -*-
"""Sources from other Kodi add-ons (v5.10.447).

Dex Hub asks the Kodi source add-ons the user switched on as more targets of
its own source search; their rows join the same list, tabs, filters and
sorting, and play through the add-on's own resolver and player. Each add-on
searches with its own scrapers and debrid accounts.

No list of add-ons: Dex Hub reads each installed video add-on's code and
recognises its family (the way its source search is built):
  fen       Fen, Red Light, POV, Gears and the like
  umbrella  Umbrella and the like
  otaku     Otaku (anime: Dex Hub's TVDB / TMDb ids go through Otaku's own
            MAL mapping table)
Fen Light answers by itself (its built-in app_list_sources). Every other
add-on of a known family gets Dex Hub's bridge
(resources/kodi_bridge/dexhub_bridge.py) and one fenced line in its router,
added when the user switches it on, removed when it is switched off, and
added again if the add-on's update took it away.
Off by default; the choice lives in "Add-ons searched for sources".
"""
import json
import os
import re
import time
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcaddon
import xbmcvfs

SETTING = 'kodi_source_addons'
PROVIDER_ID = '__kodi__'
_ME = 'plugin.video.dexhub'
_OPEN, _CLOSE = '# >>> Dex Hub bridge (plugin.video.dexhub) >>>', '# <<< Dex Hub bridge <<<'
_FENCE = re.compile(r'[ \t]*%s\r?\n.*?[ \t]*%s\r?\n' % (re.escape(_OPEN), re.escape(_CLOSE)), re.S)
# another tool's fenced lines (they do not count as the add-on's own support)
_OTHER_FENCES = re.compile(r'[ \t]*# >>> [A-Z][^\n]*\r?\n.*?[ \t]*# <<< [A-Z][^\n]*\r?\n', re.S)
_TAGS = re.compile(r'\[/?(?:B|I|UPPERCASE|LOWERCASE|CAPITALIZE|COLOR[^\]]*)\]', re.I)
_SERVICES = (('real', 'RD'), ('premiumize', 'PM'), ('alldebrid', 'AD'), ('all-debrid', 'AD'),
             ('torbox', 'TB'), ('easydebrid', 'ED'), ('offcloud', 'OC'), ('debrid-link', 'DL'))
_FOUND = {}


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] Kodi sources: %s' % msg, level)


def _clean(text):
    return _TAGS.sub('', str(text or '')).strip()


def _home(aid):
    return xbmcvfs.translatePath('special://home/addons/%s/' % aid)


def _read(path):
    with open(path, 'r', encoding='utf-8', errors='replace', newline='') as handle:
        return handle.read()


# ------------------------------------------------------------ recognising

def _family(aid):
    """(family, router file) or None, read from the add-on's own files
    (nothing imported)."""
    lib = os.path.join(_home(aid), 'resources', 'lib')
    mods = os.path.join(lib, 'modules')
    try:
        sources = _read(os.path.join(mods, 'sources.py'))
    except Exception:
        sources = ''
    if sources:
        if all(x in sources for x in ('class Sources', 'def get_sources(', 'def play_source(', 'def play_file(')) and \
                ('def playback_prep(' in sources or 'def source_select(' in sources):
            for name in ('router.py', 'entry.py'):
                try:
                    text = _read(os.path.join(mods, name))
                except Exception:
                    continue
                if 'def routing(' in text:
                    return 'fen', os.path.join(mods, name)
        if all(x in sources for x in ('def sourceSelect(', 'def playItem(', 'def play(self, title, year, imdb')):
            if os.path.isfile(os.path.join(mods, 'router.py')):
                return 'umbrella', os.path.join(mods, 'router.py')
    try:
        browser = _read(os.path.join(lib, 'MetaBrowser.py'))
        main = os.path.join(lib, 'Main.py')
        if 'def get_sources(mal_id, episode, media_type' in browser and 'silent' in browser and \
                "@Route('play/*')" in _read(main):
            return 'otaku', main
    except Exception:
        pass
    return None


def _answers_itself(router):
    """The add-on has app_list_sources of its own (Fen Light)."""
    try:
        return 'app_list_sources' in _OTHER_FENCES.sub('', _FENCE.sub('', _read(router)))
    except Exception:
        return False


def installed():
    """[(id, name, how)] for the video add-ons Dex Hub can ask; how is
    'native' (answers itself) or the family that takes Dex Hub's bridge."""
    if 'list' in _FOUND and time.time() - _FOUND['at'] < 300:
        return _FOUND['list']
    out = []
    try:
        names = sorted(os.listdir(xbmcvfs.translatePath('special://home/addons/')))
    except OSError:
        names = []
    for aid in names:
        if not aid.startswith('plugin.video.') or aid == _ME:
            continue
        found = _family(aid)
        if not found or not xbmc.getCondVisibility('System.HasAddon(%s)' % aid):
            continue
        family, router = found
        try:
            name = xbmcaddon.Addon(aid).getAddonInfo('name')
        except Exception:
            name = aid.split('.')[-1].title()
        native = family != 'otaku' and _answers_itself(router)
        out.append((aid, _clean(name), 'native' if native else family))
    _FOUND.update({'list': out, 'at': time.time()})
    return out


def enabled_ids():
    try:
        value = json.loads(xbmcaddon.Addon().getSetting(SETTING) or '[]')
    except Exception:
        return []
    return [str(x) for x in value if x] if isinstance(value, list) else []


def save(ids):
    xbmcaddon.Addon().setSetting(SETTING, json.dumps(sorted(set(ids))))


# --------------------------------------------------------------- the bridge

def _router_line(family, indent):
    """Dex Hub's line in the add-on's router, in the router's indentation."""
    unit = '\t' if '\t' in indent else '    '
    if family == 'fen':
        return ["if mode in ('dexhub_list', 'dexhub_play'):",
                unit + 'from modules.dexhub_bridge import answer',
                unit + "return answer(mode, params, 'fen')"]
    return ["if action in ('dexhub_list', 'dexhub_play'):",
            unit + 'from resources.lib.modules.dexhub_bridge import answer',
            unit + "return answer(action, params, 'umbrella')"]


def _fenced(lines, indent, nl):
    return ''.join(indent + x + nl for x in [_OPEN] + lines + [_CLOSE])


def add_bridge(aid, family, router):
    """Copy the bridge next to the router and fence Dex Hub's line into it.
    Nothing is written when the line has no place (an update that rebuilt
    the router is left as it is)."""
    try:
        text = _FENCE.sub('', _read(router))
    except Exception as exc:
        _log('%s: router unreadable (%s)' % (aid, exc), xbmc.LOGWARNING)
        return False
    nl = '\r\n' if '\r\n' in text else '\n'
    if family == 'otaku':
        # Otaku dispatches by route: one more route at the end of Main.py
        stripped = text.rstrip('\r\n')
        text = stripped + nl + _fenced(
            ["@Route('dexhub_sources/*')", 'def DEXHUB_SOURCES(payload, params):',
             '    from resources.lib.dexhub_bridge import otaku_answer',
             '    return otaku_answer(payload, params)'], '', nl) + text[len(stripped):]
    else:
        word = 'mode' if family == 'fen' else 'action'
        found = re.search(r'^([ \t]+)%s\s*=\s*[^\r\n]*[\'"]%s[\'"][^\r\n]*\r?\n' % (word, word), text, re.M)
        if not found:
            _log('%s: no place for the bridge in %s' % (aid, os.path.basename(router)), xbmc.LOGWARNING)
            return False
        indent = found.group(1)
        text = text[:found.end()] + _fenced(_router_line(family, indent), indent, nl) + text[found.end():]
    try:
        with open(os.path.join(_home(_ME), 'resources', 'kodi_bridge', 'dexhub_bridge.py'), 'rb') as src:
            body = src.read()
        with open(os.path.join(os.path.dirname(router), 'dexhub_bridge.py'), 'wb') as dst:
            dst.write(body)
        with open(router, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
    except Exception as exc:
        _log('%s: bridge not written (%s)' % (aid, exc), xbmc.LOGWARNING)
        return False
    _log('bridge added to %s (%s)' % (aid, family))
    return True


def remove_bridge(aid):
    """Take Dex Hub's line and file out again (another tool's are left)."""
    found = _family(aid)
    if not found:
        return
    router = found[1]
    try:
        text = _read(router)
        clean = _FENCE.sub('', text) if found[0] != 'otaku' else _strip_tail(text)
        if clean != text:
            with open(router, 'w', encoding='utf-8', newline='') as handle:
                handle.write(clean)
        os.remove(os.path.join(os.path.dirname(router), 'dexhub_bridge.py'))
        _log('bridge removed from %s' % aid)
    except OSError:
        pass
    except Exception as exc:
        _log('%s: bridge not removed (%s)' % (aid, exc), xbmc.LOGWARNING)


def _strip_tail(text):
    """Otaku: the route was added after the file's last line and its line end."""
    at = text.find(_OPEN)
    if at < 0:
        return text
    end = text.find(_CLOSE, at)
    end = text.find('\n', end) + 1 if end >= 0 else len(text)
    head = text[:at]
    nl = '\r\n' if head.endswith('\r\n') else '\n'
    return head[:len(head) - len(nl)] + text[end:] if head.endswith(nl) else head + text[end:]


def _bridged(router):
    try:
        return _OPEN in _read(router)
    except Exception:
        return False


def _refresh_bridge(aid, router):
    """v5.10.448: the add-on gets Dex Hub's bridge again when Dex Hub's copy
    is newer (a bridge copied once stayed at its first version)."""
    mine = os.path.join(_home(_ME), 'resources', 'kodi_bridge', 'dexhub_bridge.py')
    theirs = os.path.join(os.path.dirname(router), 'dexhub_bridge.py')
    try:
        with open(mine, 'rb') as src:
            body = src.read()
        try:
            with open(theirs, 'rb') as dst:
                if dst.read() == body:
                    return
        except OSError:
            pass
        with open(theirs, 'wb') as dst:
            dst.write(body)
        _log('bridge renewed in %s' % aid)
    except Exception as exc:
        _log('%s: bridge not renewed (%s)' % (aid, exc), xbmc.LOGWARNING)


def ready(aid):
    """'native', 'fen', 'umbrella' or 'otaku' when the add-on can be asked
    now (bridging it if needed), else ''."""
    found = _family(aid)
    if not found:
        return ''
    family, router = found
    if family != 'otaku' and _answers_itself(router):
        return 'native'
    if _bridged(router):
        _refresh_bridge(aid, router)
        return family
    if add_bridge(aid, family, router):
        return family
    return ''


def apply_choice(ids):
    """Save the switched-on add-ons, bridge them, unbridge the others.
    Returns the names that could not be bridged."""
    save(ids)
    failed = []
    for aid, name, _how in installed():
        if aid in ids:
            if not ready(aid):
                failed.append(name)
        else:
            remove_bridge(aid)
    return failed


# ---------------------------------------------------------------- searching

_SEARCHABLE = {}


def can_search(aid):
    """v5.10.448: False when the add-on has every scraper switched off (its
    own settings.db, as Fen Light and Red Light keep them): such a search
    finds nothing, and Fen Light's ends with Dialog.Close(all), which closed
    the title page under Dex Hub's sources list. Asked again as soon as one
    scraper is on. True when its settings cannot be read (POV keeps an XML)."""
    hit = _SEARCHABLE.get(aid)
    if hit and time.time() - hit[1] < 60:
        return hit[0]
    path = xbmcvfs.translatePath('special://profile/addon_data/%s/databases/settings.db' % aid)
    result = True
    if os.path.isfile(path):
        try:
            import sqlite3
            con = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=2)
            try:
                rows = con.execute("SELECT setting_id, setting_value FROM settings WHERE setting_id LIKE 'provider.%'"
                                   " AND setting_value IN ('true', 'false')").fetchall()
            finally:
                con.close()
            switches = [value for key, value in rows if not key.split('.', 1)[1].startswith('prefer')]
            result = not switches or 'true' in switches
        except Exception:
            result = True
    if not result and (hit is None or hit[0]):
        _log('%s has every scraper switched off: not asked (a search would find nothing)' % aid)
    _SEARCHABLE[aid] = (result, time.time())
    return result


def targets(ids, canonical_id, media_type, meta=None, season=None, episode=None):
    """One search target per switched-on add-on that can answer."""
    on = enabled_ids()
    tmdb_id = str((ids or {}).get('tmdb_id') or '').strip()
    if not on or not tmdb_id:
        return []
    meta = meta or {}
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    title = str(meta.get('name') or meta.get('title') or '')
    media = {'media_type': 'episode' if episodic else 'movie', 'tmdb_id': tmdb_id,
             'imdb': str((ids or {}).get('imdb_id') or ''), 'tvdb': str((ids or {}).get('tvdb_id') or ''),
             'year': str(meta.get('year') or str(meta.get('releaseInfo') or meta.get('release_date')
                                                     or meta.get('first_air_date') or '')[:4]),
             'title': title, 'tvshowtitle': title if episodic else ''}
    if episodic:
        media.update({'season': str(int(season)), 'episode': str(int(episode))})
    names = {aid: name for aid, name, _how in installed()}
    out = []
    for aid in on:
        if aid not in names or not can_search(aid):
            continue
        how = ready(aid)
        if how:
            out.append(({'id': PROVIDER_ID, 'name': names[aid], '_aid': aid, '_how': how, '_media': media},
                        str(canonical_id)))
    return out


def _url(provider, rid, kind, index=None):
    """The add-on's list or play address for its kind of answer."""
    m, how, aid = provider['_media'], provider['_how'], provider['_aid']
    tail = {'request_id': rid}
    if index is not None:
        tail['selected_index'] = index
    if how == 'otaku':
        params = {'tmdb_id': m['tmdb_id'], 'tvdb_id': m['tvdb'], 'season': m.get('season', ''),
                  'episode': m.get('episode', '')}
        params.update(tail)
        return 'plugin://%s/dexhub_sources/%s?%s' % (aid, kind, urlencode(params))
    if how == 'umbrella':
        params = {'action': 'dexhub_%s' % kind, 'title': m['title'], 'year': m['year'], 'imdb': m['imdb'],
                  'tmdb': m['tmdb_id'], 'tvdb': m['tvdb']}
        if m['media_type'] == 'episode':
            params.update({'tvshowtitle': m['tvshowtitle'], 'season': m['season'], 'episode': m['episode']})
    else:
        if how == 'native':
            mode = 'app_list_sources' if kind == 'list' else 'app_play_source'
        else:
            mode = 'dexhub_%s' % kind
        params = {'mode': mode, 'media_type': m['media_type'], 'tmdb_id': m['tmdb_id']}
        if m['media_type'] == 'episode':
            params.update({'season': m['season'], 'episode': m['episode']})
        if kind == 'list' and how == 'native':
            params.update({'autoplay': 'false', 'prescrape': 'false'})
    params.update(tail)
    return 'plugin://%s/?%s' % (aid, urlencode(params))


def _native_row(item):
    """A result from Fen Light's own saved list (the bridge writes this
    summary itself)."""
    get = item.get
    service = str(get('cache_provider') or get('debrid') or '')
    site = (get('source') if get('scrape_provider') == 'external' else get('scrape_provider')) or get('source')
    try:
        size_gb = float(get('size') or 0)
    except (TypeError, ValueError):
        size_gb = 0.0
    return {'name': _clean(get('display_name') or get('name')), 'quality': _clean(get('quality')).upper(),
            'size': _clean(get('size_label')), 'size_gb': size_gb,
            'info': [x.strip() for x in _clean(get('extraInfo')).split('|') if x.strip()],
            'service': service.replace('Uncached', '').strip(), 'site': _clean(site),
            'cached': 'uncached' not in service.lower()}


def _service(text):
    text = str(text or '').lower().replace(' ', '')
    for word, short in _SERVICES:
        if word in text:
            return short
    return ''


def _quality(text):
    q = _clean(text).upper()
    if q in ('2160P', 'UHD', '8K', '4K'):
        return '4K'
    return q if q in ('1080P', '720P') else 'SD'


def _stream(provider, rid, index, row):
    """One result as a Stremio-like row, so Dex Hub ranks, badges and
    filters it like any other."""
    release = _clean(row.get('name'))
    service = _service(row.get('service'))
    size = _clean(row.get('size'))
    if size.upper() in ('N/A', 'NA'):
        size = ''
    facts = ' · '.join(x for x in [size, _clean(row.get('site')).upper()] +
                       [_clean(i) for i in row.get('info') or []] + [_clean(row.get('lang'))] if x)
    out = {'name': '%s%s\n%s' % ('[%s+] ' % service if service else '', provider['name'],
                                  _quality(row.get('quality')).replace('P', 'p')),
           'title': '%s\n%s' % (release, facts) if facts else release,
           'url': _url(provider, rid, 'play', index),
           'behaviorHints': {'bingeGroup': 'dexhub-kodi-%s' % provider['_aid'], 'filename': release}}
    try:
        if float(row.get('size_gb') or 0) > 0:
            out['behaviorHints']['videoSize'] = int(float(row['size_gb']) * 1024 ** 3)
    except (TypeError, ValueError):
        pass
    return out


def payload(provider):
    """Ask one add-on (blocks until it has searched; Dex Hub's own search
    timeout lets the list open meanwhile)."""
    rid = 'dexhub%d' % int(time.time() * 1000)
    folder = 'device_select' if provider['_how'] == 'native' else 'dexhub'
    path = xbmcvfs.translatePath('special://profile/addon_data/%s/%s/%s.json' % (provider['_aid'], folder, rid))
    started = time.time()
    answer = ''
    try:
        answer = xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'Files.GetDirectory',
            'params': {'directory': _url(provider, rid, 'list'), 'media': 'files'}}))
    except Exception as exc:
        answer = str(exc)
    data = None
    for _attempt in range(10):          # written just before the listing ends
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
                break
            except Exception:
                pass
        xbmc.sleep(200)
    if data is None:
        # an error answer means the add-on itself stopped (its own settings:
        # TMDb key, debrid account); its traceback is in the log just before
        _log('%s did not search (%.1fs): %s' % (provider['name'], time.time() - started,
                                               'it stopped with an error, see its own log lines'
                                               if '"error"' in (answer or '') else (answer or '')[:200]),
             xbmc.LOGWARNING)
        return {'streams': []}
    rows = data.get('rows') if isinstance(data, dict) else [_native_row(i) for i in data if isinstance(i, dict)]
    # an uncached pick only adds the torrent to the debrid cloud: not a source to play
    streams = [_stream(provider, rid, index, row) for index, row in enumerate(rows or [])
               if isinstance(row, dict) and row.get('cached', True)]
    _log('%s: %d playable of %d in %.1fs' % (provider['name'], len(streams), len(rows or []), time.time() - started))
    return {'streams': streams}


def is_kodi_row(ctx):
    return str((ctx or {}).get('source_provider_id') or '') == PROVIDER_ID


def resolve(ctx, seconds=90):
    """v5.10.448: the add-on's pick resolved by the add-on (its resolver, its
    debrid account) but played by Dex Hub, so its rows get Dex Hub's waiting
    screen and player like every other source. The link, or '' when the
    add-on found none (or played the pick itself after all), or None when the
    add-on does not resolve for Dex Hub (play() hands it the whole pick)."""
    url = str((ctx or {}).get('stream_url') or '').split('|', 1)[0]
    if not url.startswith('plugin://') or 'mode=dexhub_play' not in url:
        return None
    aid = url.split('://', 1)[1].split('/', 1)[0]
    rid = dict(parse_qsl(url.split('?', 1)[-1])).get('request_id') or ''
    folder = xbmcvfs.translatePath('special://profile/addon_data/%s/dexhub/' % aid)
    try:
        with open(os.path.join(folder, '%s.json' % rid), 'r', encoding='utf-8') as handle:
            if not json.load(handle).get('resolve'):
                return None
    except Exception:
        return None
    path = os.path.join(folder, '%s.resolved.json' % rid)
    try:
        os.remove(path)
    except OSError:
        pass
    started = time.time()
    _log('resolving through %s' % aid)
    xbmc.executebuiltin('RunPlugin(%s&resolve_only=1)' % url)
    monitor, player = xbmc.Monitor(), xbmc.Player()
    while time.time() - started < seconds and not monitor.abortRequested():
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
            except Exception:
                data = None
            if data is not None:
                _log('%s: link %s in %.1fs' % (aid, 'ready' if data.get('url') else 'not found',
                                               time.time() - started))
                return '' if data.get('played') else str(data.get('url') or '')
        if player.isPlaying():                # an older bridge played it by itself
            return ''
        monitor.waitForAbort(0.25)
    _log('%s: no link after %ds' % (aid, seconds), xbmc.LOGWARNING)
    return ''


def play(ctx):
    """The add-on plays its own result (its resolver, its player)."""
    url = str((ctx or {}).get('stream_url') or '').split('|', 1)[0]
    if not url.startswith('plugin://'):
        return False
    _log('playing through %s' % url.split('/?', 1)[0].split('/dexhub_sources', 1)[0])
    xbmc.executebuiltin('RunPlugin(%s)' % url)
    return True


class LateSearch(object):
    """v5.10.447: the switched-on Kodi add-ons search in their own lane.

    They start with Dex Hub's search but outside its shared race (which stops
    waiting after 25 seconds; Kodi alone can take that long to start an
    add-on). Their rows wait for the sources page's session (attach) and join
    the open page as each add-on answers, up to SECONDS; with no page yet
    (nothing else found anything) collect() waits for them instead."""

    SECONDS = 120

    def __init__(self, targets, fetch):
        import threading
        self.targets = list(targets or [])
        self.fetch = fetch
        self.lock = threading.Lock()
        self.results = []           # (target, data) as each add-on answers
        self.session = None
        self.convert = None
        self.done = threading.Event()
        for target in self.targets:
            threading.Thread(target=self._run, args=(target,), name='DexHubKodiSource', daemon=True).start()

    def __bool__(self):
        return bool(self.targets)

    def names(self):
        return [target[0].get('name') or target[0].get('_aid') for target in self.targets]

    def _run(self, target):
        try:
            data = self.fetch(target)
        except Exception as exc:
            _log('%s: %s' % (target[0].get('name'), exc), xbmc.LOGWARNING)
            data = None
        with self.lock:
            self.results.append((target, data))
            session, convert = self.session, self.convert
            if len(self.results) >= len(self.targets):
                self.done.set()
        if session:
            self._deliver(session, convert, target, data)

    def _deliver(self, session, convert, target, data):
        from . import source_session
        entries = []
        if isinstance(data, dict) and data.get('streams'):
            try:
                entries = convert(target, data) or []
            except Exception as exc:
                _log('%s rows: %s' % (target[0].get('name'), exc), xbmc.LOGWARNING)
        source_session.late_add(session, target[0].get('name') or target[0].get('_aid'), entries)
        if entries:
            _log('%s: %d rows joined the open page' % (target[0].get('name'), len(entries)))

    def attach(self, session_key, convert, base_done):
        """Hand the open page's session over: what came already joins now,
        the rest when it comes."""
        from . import source_session
        source_session.late_begin(session_key, self.names(), self.SECONDS, base_done=base_done)
        import threading
        with self.lock:
            self.session, self.convert = session_key, convert
            ready = list(self.results)
        if not ready:
            return

        # v5.10.448: off the call's thread: 302 POV rows took 1.1 s here,
        # before the sources page could open (the title page showed between
        # the search screen and the page)
        def deliver():
            for target, data in ready:
                self._deliver(session_key, convert, target, data)

        threading.Thread(target=deliver, name='DexHubKodiSource', daemon=True).start()

    def collect(self, convert, timeout=None):
        """No page to join: wait for the add-ons and return their rows."""
        self.done.wait(self.SECONDS if timeout is None else timeout)
        with self.lock:
            ready = list(self.results)
            self.session = None
        entries = []
        for target, data in ready:
            if isinstance(data, dict) and data.get('streams'):
                try:
                    entries.extend(convert(target, data) or [])
                except Exception as exc:
                    _log('%s rows: %s' % (target[0].get('name'), exc), xbmc.LOGWARNING)
        return entries


def split_late(targets, fetch):
    """(the shared race's targets, a LateSearch of the Kodi add-ons)."""
    targets = list(targets or [])
    kodi = [t for t in targets if (t[0] or {}).get('id') == PROVIDER_ID]
    if not kodi:
        return targets, None
    return [t for t in targets if (t[0] or {}).get('id') != PROVIDER_ID], LateSearch(kodi, fetch)
