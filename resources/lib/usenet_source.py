# -*- coding: utf-8 -*-
"""Usenet as a source of its own (v5.10.558).

With an NNTP provider and a Newznab indexer of the viewer's own (Settings > Sources > Usenet),
every title's source search also asks the indexers, and their releases join the same list,
tabs, filter and TRaSH ranking as every other source. A picked release plays straight from the
provider through the engine on this device (usenet_engine): no server in between.

- search: each indexer's Newznab API, by IMDb id (films) or IMDb/TVDB id with season and
  episode (series); by name when an indexer finds nothing by id. Indexers are asked together.
- a row's address is the release's NZB link; nothing is fetched until it is picked.
- playing: resolve() hands the NZB link and the providers to the engine and gets the film's
  address on 127.0.0.1.

The providers and the indexer keys are kept in addon_data/plugin.video.dexhub/usenet.json on
this device only. Searches and downloads go from this device with the viewer's own accounts:
the indexer and the provider see this device's address, and every search uses API calls on
each indexer.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode

import xbmc
import xbmcgui
import xbmcvfs

PROVIDER_ID = '__usenet__'
COLOR = 'FF5AC8FA'
NAME = 'Usenet'
_FILE = 'special://profile/addon_data/plugin.video.dexhub/usenet.json'
_AGENT = 'Dex Hub (Kodi)'
_RES = re.compile(r'(?<![a-z0-9])(2160p|4k|uhd|1080p|720p|576p|480p)(?![a-z0-9])', re.I)
_CONFIG = {}


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] Usenet: %s' % msg, level)


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


# ------------------------------------------------------------ what the viewer set up

def config():
    """{'providers': [{host, port, ssl, user, password, connections}], 'indexers': [{name, url, key}]}"""
    if 'value' not in _CONFIG:
        try:
            with open(xbmcvfs.translatePath(_FILE), 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except Exception:
            data = {}
        _CONFIG['value'] = {'providers': list(data.get('providers') or []),
                            'indexers': list(data.get('indexers') or []),
                            'private': bool(data.get('private'))}
    return _CONFIG['value']


def _save(data):
    path = xbmcvfs.translatePath(_FILE)
    try:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
    except Exception as exc:
        _log('not saved: %s' % exc, xbmc.LOGWARNING)
    _CONFIG.pop('value', None)


def is_set_up():
    data = config()
    return any(not p.get('off') for p in data['providers']) and any(not i.get('off') for i in data['indexers'])


def servers():
    """The providers as the engine takes them: nntps://user:password@host:port/connections,
    with ?priority=1 on a backup provider: the engine reads from the main ones and turns to a
    backup only for articles the main ones do not have."""
    out = []
    for p in config()['providers']:
        if p.get('off'):
            continue
        login = ''
        if p.get('user'):
            login = '%s:%s@' % (quote(str(p.get('user')), safe=''), quote(str(p.get('password') or ''), safe=''))
        out.append('%s://%s%s:%d/%d' % ('nntps' if p.get('ssl', True) else 'nntp', login, p.get('host'),
                                        int(p.get('port') or (563 if p.get('ssl', True) else 119)),
                                        max(1, min(100, int(p.get('connections') or 20))))
                   + ('?priority=1' if p.get('backup') else ''))
    return out


# ------------------------------------------------------------ the search

def _api(indexer, params, seconds=20):
    base = str(indexer.get('url') or '').rstrip('/')
    if not base.endswith('/api'):
        base += '/api'
    request = urllib.request.Request(base + '?' + urlencode(dict(params, apikey=indexer.get('key') or '')),
                                     headers={'User-Agent': _AGENT})
    with urllib.request.urlopen(request, timeout=seconds) as answer:
        return answer.read()


def _releases(raw):
    """A Newznab answer as [{title, link, size, age, password}]."""
    out = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out
    for item in root.iter('item'):
        title = (item.findtext('title') or '').strip()
        enclosure = item.find('enclosure')
        link = (enclosure.get('url') if enclosure is not None else '') or (item.findtext('link') or '')
        attrs = {}
        for child in item:
            if child.tag.endswith('attr') and child.get('name'):
                attrs[child.get('name')] = child.get('value') or ''
        try:
            size = int((enclosure.get('length') if enclosure is not None else 0) or attrs.get('size') or 0)
        except (TypeError, ValueError):
            size = 0
        if not title or not link.startswith('http'):
            continue
        age = ''
        stamp = attrs.get('usenetdate') or item.findtext('pubDate') or ''
        try:
            from email.utils import parsedate_to_datetime
            days = int((time.time() - parsedate_to_datetime(stamp).timestamp()) / 86400)
            age = '%dd' % days if days < 365 else '%dy' % (days // 365)
        except Exception:
            pass
        out.append({'title': title, 'link': link.strip(), 'size': size, 'age': age,
                    'password': attrs.get('password') not in (None, '', '0')})
    return out


def _quality(title):
    found = _RES.search(re.sub(r'[._]', ' ', title or ''))
    if not found:
        return 'SD'
    text = found.group(1).upper()
    return '2160p' if text in ('4K', 'UHD', '2160P') else text.lower()


def _size_label(size):
    return '%.2f GB' % (size / 1073741824.0) if size >= 1073741824 else '%d MB' % (size // 1048576) if size else ''


def _search_one(indexer, provider, out):
    ids = provider.get('_ids') or {}
    imdb = re.sub(r'\D', '', str(ids.get('imdb_id') or ''))
    season, episode = provider.get('_season'), provider.get('_episode')
    title = str(provider.get('_title') or '')
    calls = []
    if season is not None:
        if ids.get('tvdb_id'):
            calls.append({'t': 'tvsearch', 'tvdbid': str(ids.get('tvdb_id')), 'season': season, 'ep': episode})
        if imdb:
            calls.append({'t': 'tvsearch', 'imdbid': imdb, 'season': season, 'ep': episode})
        if title:
            calls.append({'t': 'search', 'cat': '5000', 'q': '%s S%02dE%02d' % (title, int(season), int(episode))})
    else:
        if imdb:
            calls.append({'t': 'movie', 'imdbid': imdb})
        if title:
            calls.append({'t': 'search', 'cat': '2000', 'q': ('%s %s' % (title, provider.get('_year') or '')).strip()})
    name = str(indexer.get('name') or indexer.get('url') or 'indexer')
    started = time.time()
    for params in calls:
        try:
            found = _releases(_api(indexer, dict(params, limit=100)))
        except urllib.error.HTTPError as exc:
            _log('%s refused the search (%s)' % (name, exc.code), xbmc.LOGWARNING)
            return
        except Exception as exc:
            _log('%s did not answer: %s' % (name, exc), xbmc.LOGWARNING)
            return
        if found:
            _log('%s: %d releases (%s), %.1fs' % (name, len(found), params['t'], time.time() - started))
            out.append((name, found))
            return
    _log('%s: nothing found, %.1fs' % (name, time.time() - started))


def targets(meta, canonical_id, media_type, ids=None, season=None, episode=None):
    """One search target when a provider, an indexer and the engine are all here."""
    if not is_set_up():
        return []
    try:
        from . import usenet_engine
        if not usenet_engine.available():
            return []
        from .source_addons import skipped
        if PROVIDER_ID in skipped():
            return []
    except Exception:
        return []
    from .search.native_titles import candidates
    meta = meta or {}
    year = str(meta.get('year') or meta.get('releaseInfo') or meta.get('release_date')
               or meta.get('first_air_date') or '')[:4]
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    names = [n for n in candidates(meta, media_type) if re.search(r'[A-Za-z0-9]', str(n))]
    return [({'id': PROVIDER_ID, 'name': NAME, '_title': (names[0] if names else meta.get('name') or meta.get('title') or ''),
              '_titles': names, '_year': year, '_ids': dict(ids or {}), '_media_type': media_type,
              '_enrich_titles': True, '_season': int(season) if episodic else None,
              '_episode': int(episode) if episodic else None}, str(canonical_id))]


def payload(provider):
    found, workers = [], []
    for indexer in config()['indexers']:
        if indexer.get('off'):
            continue
        worker = threading.Thread(target=_search_one, args=(indexer, provider, found), name='DexHubUsenetIndexer')
        workers.append(worker)
        worker.start()
    for worker in workers:
        worker.join(25)
    streams, seen = [], set()
    for name, releases in found:
        for release in releases:
            if release['password']:
                continue                        # a release that needs a password cannot be opened
            key = (release['title'].lower(), release['size'] // 10485760)
            if key in seen:
                continue
            seen.add(key)
            facts = u' \u00b7 '.join(x for x in (_size_label(release['size']), release['age'], name) if x)
            stream = {'name': '[COLOR %s]%s[/COLOR] %s\n%s' % (COLOR, NAME, name, _quality(release['title'])),
                      'title': '%s\n%s' % (release['title'], facts),
                      'url': release['link'],
                      'behaviorHints': {'bingeGroup': 'dexhub-usenet', 'filename': release['title']}}
            if release['size']:
                stream['behaviorHints']['videoSize'] = release['size']
            streams.append(stream)
    return {'streams': streams}


# ------------------------------------------------------------ playing

def is_row(ctx):
    return str((ctx or {}).get('source_provider_id') or '') == PROVIDER_ID


def resolve(ctx):
    """The film's address on this device for a picked release ('' when it did not open; what
    went wrong is said in a notification and in the log)."""
    from . import usenet_engine
    link = str((ctx or {}).get('stream_url') or '').split('|', 1)[0]
    if re.match(r'^http://127\.0\.0\.1:\d+/stream/', link):
        return link                             # the engine's own address already
    if not link.startswith('http'):
        return ''
    season, episode = (ctx or {}).get('season'), (ctx or {}).get('episode')
    try:
        season = int(season) if season not in (None, '') else None
        episode = int(episode) if episode not in (None, '') else None
    except (TypeError, ValueError):
        season = episode = None
    try:
        opened = usenet_engine.open_release(link, servers(), season, episode, private=config().get('private'))
    except usenet_engine.UsenetError as exc:
        _log('the release did not open (%s): %s' % (exc.kind or '-', exc), xbmc.LOGWARNING)
        reason = {'provider': _t('المزود رفض الدخول أو خلصت الحصة', 'The provider refused the sign-in or its quota is used up'),
                  'engine': _t('محرك Usenet مو مركّب على هذا الجهاز', 'The Usenet engine is not on this device')
                  }.get(exc.kind) or _t('هذي النسخة ما انفتحت (ناقصة أو محمية)', 'This release did not open (incomplete or protected)')
        try:
            xbmcgui.Dialog().notification('Usenet', reason, xbmcgui.NOTIFICATION_WARNING, 5000)
        except Exception:
            pass
        return ''
    return opened['url']


# ------------------------------------------------------------ setting up

def _ask(heading, default='', hidden=False):
    return xbmcgui.Dialog().input(heading, default, type=xbmcgui.INPUT_ALPHANUM,
                                  option=xbmcgui.ALPHANUM_HIDE_INPUT if hidden else 0)


def _number(heading, default):
    text = xbmcgui.Dialog().input(heading, str(default), type=xbmcgui.INPUT_NUMERIC)
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _test_provider(p, seconds=12):
    """'' when the provider lets this sign-in in, else what it said."""
    import socket
    import ssl as _ssl
    try:
        raw = socket.create_connection((p.get('host'), int(p.get('port') or 563)), seconds)
        sock = _ssl.create_default_context().wrap_socket(raw, server_hostname=p.get('host')) if p.get('ssl', True) else raw
        sock.settimeout(seconds)
        reader = sock.makefile('rb')

        def line():
            return reader.readline().decode('utf-8', 'replace').strip()

        greeting = line()
        if not greeting.startswith(('200', '201')):
            return greeting[:120] or 'no greeting'
        if p.get('user'):
            sock.sendall(('AUTHINFO USER %s\r\n' % p.get('user')).encode('utf-8'))
            answer = line()
            if answer.startswith('381'):
                sock.sendall(('AUTHINFO PASS %s\r\n' % (p.get('password') or '')).encode('utf-8'))
                answer = line()
            if not answer.startswith('281'):
                return answer[:120] or 'sign-in refused'
        try:
            sock.sendall(b'QUIT\r\n')
            sock.close()
        except Exception:
            pass
        return ''
    except Exception as exc:
        return str(exc)[:120]


def _test_indexer(indexer):
    """'' when the indexer answers this key, else what went wrong."""
    try:
        raw = _api(indexer, {'t': 'caps'}, seconds=15)
        if b'<error' in raw and b'<caps' not in raw:
            return re.sub(r'\s+', ' ', raw.decode('utf-8', 'replace'))[:120]
        raw = _api(indexer, {'t': 'movie', 'imdbid': '0133093', 'limit': 5}, seconds=20)
        if b'<error' in raw:
            return re.sub(r'\s+', ' ', raw.decode('utf-8', 'replace'))[:120]
        return ''
    except Exception as exc:
        return str(exc)[:120]


def _indexer_name(url):
    host = url.split('://', 1)[-1].split('/', 1)[0]
    parts = [x for x in host.split('.') if x not in ('api', 'www')]
    return (parts[0] if parts else host) or 'indexer'


def _edit_provider(p, new=False, others=False):
    """Change a provider field by field; True when something changed. new: asks every field
    (and, when there are other providers already, whether this one is a backup)."""
    dialog = xbmcgui.Dialog()
    if new:
        host = _ask(_t('عنوان سيرفر المزود (مثل news.example.com)', 'The provider\'s server (like news.example.com)')).strip()
        if not host:
            return False
        p['host'] = host
        p['ssl'] = bool(dialog.yesno('Usenet', _t('اتصال مشفّر SSL؟ (الأفضل نعم، منفذ 563)', 'Use SSL? (yes is better, port 563)')))
        p['port'] = _number(_t('المنفذ', 'Port'), 563 if p['ssl'] else 119) or (563 if p['ssl'] else 119)
        p['user'] = _ask(_t('اسم المستخدم', 'User name')).strip()
        p['password'] = _ask(_t('كلمة المرور', 'Password'), hidden=True) if p['user'] else ''
        p['connections'] = max(1, min(100, _number(_t('عدد الاتصالات (20 مناسب)', 'Connections (20 suits most)'), 20) or 20))
        if others:
            # yes = backup: used only for what the main providers do not have
            p['backup'] = bool(dialog.yesno('Usenet', _t(
                'هذا المزود احتياطي؟\nالاحتياطي ينستخدم فقط للأجزاء الناقصة عند المزود الأساسي. «لا» يخليه أساسي مع الباقين.',
                'Is this provider a backup?\nA backup is used only for the parts the main provider lacks. "No" makes it a main one beside the others.')))
        wrong = _test_provider(p)
        if wrong and not dialog.yesno('Usenet', _t('المزود ما قبل الدخول: %s\nأحفظه على أي حال؟',
                                                   'The provider did not accept the sign-in: %s\nKeep it anyway?') % wrong):
            return False
        return True
    changed = False
    while True:
        rows = [(_t('السيرفر: %s', 'Server: %s') % p.get('host'), 'host'),
                (_t('المنفذ: %s', 'Port: %s') % p.get('port'), 'port'),
                (_t('SSL: %s', 'SSL: %s') % (_t('نعم', 'yes') if p.get('ssl', True) else _t('لا', 'no')), 'ssl'),
                (_t('اسم المستخدم: %s', 'User name: %s') % (p.get('user') or '-'), 'user'),
                (_t('كلمة المرور: %s', 'Password: %s') % ('••••••' if p.get('password') else '-'), 'password'),
                (_t('عدد الاتصالات: %s', 'Connections: %s') % p.get('connections', 20), 'connections'),
                (_t('الدور: %s', 'Role: %s') % (_t('احتياطي (للأجزاء الناقصة فقط)', 'backup (only for missing parts)')
                                                 if p.get('backup') else _t('أساسي', 'main')), 'backup'),
                (_t('الحالة: %s', 'State: %s') % (_t('موقّف', 'switched off') if p.get('off') else _t('شغال', 'on')), 'off'),
                (_t('فحص الاتصال', 'Test the sign-in'), 'test'),
                (_t('حذف هذا المزود', 'Remove this provider'), 'remove')]
        choice = dialog.select(_t('مزود: %s', 'Provider: %s') % p.get('host'), [r[0] for r in rows])
        if choice < 0:
            return changed
        what = rows[choice][1]
        if what == 'host':
            value = _ask(_t('عنوان السيرفر', 'Server'), str(p.get('host') or '')).strip()
            if value and value != p.get('host'):
                p['host'], changed = value, True
        elif what == 'port':
            value = _number(_t('المنفذ', 'Port'), p.get('port') or 563)
            if value and value != p.get('port'):
                p['port'], changed = value, True
        elif what == 'ssl':
            p['ssl'], changed = not p.get('ssl', True), True
            if p.get('port') in (563, 119):
                p['port'] = 563 if p['ssl'] else 119
        elif what == 'user':
            value = _ask(_t('اسم المستخدم', 'User name'), str(p.get('user') or '')).strip()
            if value != (p.get('user') or ''):
                p['user'], changed = value, True
        elif what == 'password':
            value = _ask(_t('كلمة المرور الجديدة', 'The new password'), hidden=True)
            if value:
                p['password'], changed = value, True
        elif what == 'connections':
            value = _number(_t('عدد الاتصالات (1 إلى 100)', 'Connections (1 to 100)'), p.get('connections') or 20)
            if value:
                p['connections'], changed = max(1, min(100, value)), True
        elif what == 'backup':
            p['backup'], changed = not p.get('backup'), True
        elif what == 'off':
            p['off'], changed = not p.get('off'), True
        elif what == 'test':
            wrong = _test_provider(p)
            dialog.ok('Usenet', _t('المزود شغال والدخول صحيح.', 'The provider answers and the sign-in is right.') if not wrong
                      else _t('ما نجح الفحص: %s', 'The test failed: %s') % wrong)
        elif what == 'remove' and dialog.yesno('Usenet', _t('أحذف المزود %s؟', 'Remove the provider %s?') % p.get('host')):
            p['_remove'] = True
            return True


def _edit_indexer(i, new=False):
    dialog = xbmcgui.Dialog()
    if new:
        url = _ask(_t('عنوان المفهرس (مثل https://api.example.com)', 'The indexer\'s address (like https://api.example.com)')).strip()
        if not url:
            return False
        i['url'] = url if url.startswith('http') else 'https://' + url
        i['key'] = _ask(_t('مفتاح API', 'API key'), hidden=True).strip()
        if not i['key']:
            return False
        i['name'] = _indexer_name(i['url'])
        wrong = _test_indexer(i)
        if wrong and not dialog.yesno('Usenet', _t('المفهرس ما رد صح: %s\nأحفظه على أي حال؟',
                                                   'The indexer did not answer right: %s\nKeep it anyway?') % wrong):
            return False
        return True
    changed = False
    while True:
        rows = [(_t('الاسم: %s', 'Name: %s') % i.get('name'), 'name'),
                (_t('العنوان: %s', 'Address: %s') % i.get('url'), 'url'),
                (_t('مفتاح API: %s', 'API key: %s') % ('••••••' if i.get('key') else '-'), 'key'),
                (_t('الحالة: %s', 'State: %s') % (_t('موقّف', 'switched off') if i.get('off') else _t('شغال', 'on')), 'off'),
                (_t('فحص المفهرس', 'Test the indexer'), 'test'),
                (_t('حذف هذا المفهرس', 'Remove this indexer'), 'remove')]
        choice = dialog.select(_t('مفهرس: %s', 'Indexer: %s') % i.get('name'), [r[0] for r in rows])
        if choice < 0:
            return changed
        what = rows[choice][1]
        if what == 'name':
            value = _ask(_t('اسم المفهرس (يظهر على المصدر)', 'The indexer\'s name (shown on the source)'), str(i.get('name') or '')).strip()
            if value and value != i.get('name'):
                i['name'], changed = value, True
        elif what == 'url':
            value = _ask(_t('عنوان المفهرس', 'Address'), str(i.get('url') or '')).strip()
            if value and value != i.get('url'):
                i['url'], changed = (value if value.startswith('http') else 'https://' + value), True
        elif what == 'key':
            value = _ask(_t('مفتاح API الجديد', 'The new API key'), hidden=True).strip()
            if value:
                i['key'], changed = value, True
        elif what == 'off':
            i['off'], changed = not i.get('off'), True
        elif what == 'test':
            wrong = _test_indexer(i)
            dialog.ok('Usenet', _t('المفهرس شغال والمفتاح صحيح.', 'The indexer answers and the key is right.') if not wrong
                      else _t('ما نجح الفحص: %s', 'The test failed: %s') % wrong)
        elif what == 'remove' and dialog.yesno('Usenet', _t('أحذف المفهرس %s؟', 'Remove the indexer %s?') % i.get('name')):
            i['_remove'] = True
            return True


def _engine_note():
    from . import usenet_engine
    xbmcgui.Dialog().ok('Usenet', _t(
        'المحرك إضافة مستقلة اسمها «Usenet engine (for Dex Hub)» (من NuvioTV، رخصة GPL-3.0) ومو جزء من Dex Hub. ثبّتها من ملف zip أو من المستودع.\nأو حط البرنامج بيدك في:\n%s\nباسم: %s',
        'The engine is an add-on of its own, "Usenet engine (for Dex Hub)" (from NuvioTV, GPL-3.0), not part of Dex Hub. Install it from a zip or the repository.\nOr put the program by hand in:\n%s\nnamed: %s')
        % (xbmcvfs.translatePath(usenet_engine.FOLDER), usenet_engine.binary_name() or '-'))


class SetupWindow(xbmcgui.WindowXMLDialog):
    """Usenet's setup as a card window (dexhub_usenet.xml, tools/s558/make_usenet_window.py): the
    engine's state on top, the providers on the left, the indexers on the right. OK on an entry
    opens its choices; the last row of a column adds one."""
    PROVIDERS, INDEXERS, ENGINE = 100, 110, 120
    BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}

    def __init__(self, *args, **kwargs):
        super(SetupWindow, self).__init__(*args)
        self._inited = False

    def onInit(self):
        if self._inited:
            return
        self._inited = True
        for key, text in (('title', 'Usenet'),
                          ('desc', _t('النسخ تجي من مفهرساتك وتشتغل من مزودك مباشرة على هذا الجهاز',
                                      'Releases from your indexers, played straight from your provider')),
                          ('h.providers', _t('المزودين (NNTP)', 'Providers (NNTP)')),
                          ('h.indexers', _t('المفهرسات (Newznab)', 'Indexers (Newznab)')),
                          ('on', _t('شغال', 'On')), ('off', _t('موقّف', 'Off')),
                          ('hint', _t('OK: تعديل، فحص، إيقاف أو حذف    ·    رجوع: إغلاق',
                                      'OK: change, test, switch off or remove    ·    Back: close'))):
            self.setProperty('un.' + key, text)
        self.fill()
        self.setFocusId(self.PROVIDERS)

    def fill(self, keep=None):
        from . import usenet_engine
        data = config()
        ready = usenet_engine.available()
        self.setProperty('un.engine.ok', '1' if ready else '')
        self.setProperty('un.engine', _t('المحرك مركّب وجاهز', 'The engine is on this device') if ready
                         else _t('المحرك مو مركّب على هذا الجهاز', 'The engine is not on this device'))
        for cid, rows, key, add in ((self.PROVIDERS, data['providers'], 'providers', _t('إضافة مزود', 'Add a provider')),
                                    (self.INDEXERS, data['indexers'], 'indexers', _t('إضافة مفهرس', 'Add an indexer'))):
            items = []
            for row in rows:
                if key == 'providers':
                    name = str(row.get('host') or '')
                    facts = u' \u00b7 '.join(x for x in (
                        _t('منفذ %s', 'port %s') % row.get('port'), 'SSL' if row.get('ssl', True) else '',
                        _t('%d اتصال', '%d connections') % int(row.get('connections') or 20),
                        _t('احتياطي', 'backup') if row.get('backup') else '') if x)
                else:
                    name = str(row.get('name') or '')
                    facts = str(row.get('url') or '').split('://', 1)[-1]
                item = xbmcgui.ListItem(name, facts, offscreen=True)
                item.setProperty('kind', 'entry')
                item.setProperty('off', '1' if row.get('off') else '')
                item.setProperty('state', _t('موقّف', 'Off') if row.get('off') else _t('شغال', 'On'))
                items.append(item)
            last = xbmcgui.ListItem(add, offscreen=True)
            last.setProperty('kind', 'add')
            items.append(last)
            on = sum(1 for row in rows if not row.get('off'))
            self.setProperty('un.n.%s' % key, _t('%d شغال من %d', '%d on of %d') % (on, len(rows)) if rows
                             else _t('ما فيه شي بعد', 'none yet'))
            try:
                control = self.getControl(cid)
                at = control.getSelectedPosition()
                control.reset()
                control.addItems(items)
                control.selectItem(max(0, min(at if at >= 0 else 0, len(items) - 1)))
            except Exception as exc:
                _log('setup window: %s' % exc, xbmc.LOGWARNING)

    def onClick(self, cid):
        if cid == self.ENGINE:
            return _engine_note()
        if cid not in (self.PROVIDERS, self.INDEXERS):
            return
        key = 'providers' if cid == self.PROVIDERS else 'indexers'
        edit = _edit_provider if cid == self.PROVIDERS else _edit_indexer
        data = {'providers': [dict(p) for p in config()['providers']], 'indexers': [dict(i) for i in config()['indexers']],
                'private': config().get('private')}
        try:
            at = self.getControl(cid).getSelectedPosition()
        except Exception:
            return
        if at >= len(data[key]):
            entry = {}
            changed = (_edit_provider(entry, new=True, others=bool(data[key])) if key == 'providers'
                       else edit(entry, new=True))
            if changed:
                data[key].append(entry)
        else:
            changed = edit(data[key][at])
        if changed:
            data[key] = [row for row in data[key] if not row.pop('_remove', False)]
            _save(data)
            self.fill()

    def onAction(self, action):
        if action.getId() in self.BACK:
            self.close()


def setup():
    """Usenet's setup: the card window; the plain list when the window cannot open."""
    try:
        import xbmcaddon
        path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
        window = SetupWindow('dexhub_usenet.xml', path, 'Default', '1080i')
        try:
            window.doModal()
        finally:
            del window
        return True
    except Exception as exc:
        _log('setup window failed, the list opens instead: %s' % exc, xbmc.LOGWARNING)
        return _setup_list()


def _setup_list():
    """Usenet's settings: the engine's state, the providers and the indexers. Any number of
    each; an entry opens to be changed field by field, tested, switched off or removed."""
    from . import usenet_engine
    dialog = xbmcgui.Dialog()
    while True:
        data = {'providers': [dict(p) for p in config()['providers']], 'indexers': [dict(i) for i in config()['indexers']],
                'private': config().get('private')}
        engine = usenet_engine.available()
        off = _t(' · موقّف', ' · off')
        rows = [(_t('المحرك: مركّب على هذا الجهاز', 'Engine: on this device') if engine
                 else _t('المحرك: مو مركّب (%s)', 'Engine: not on this device (%s)')
                 % (usenet_engine.binary_name() or _t('هذا النظام ما يدعمه', 'this system cannot run it')), 'engine')]
        for index, p in enumerate(data['providers']):
            rows.append((_t('مزود: %s · %d اتصال%s', 'Provider: %s · %d connections%s')
                         % (p.get('host'), int(p.get('connections') or 20), off if p.get('off') else ''), 'p%d' % index))
        rows.append((_t('+ إضافة مزود NNTP', '+ Add an NNTP provider'), 'add_p'))
        for index, i in enumerate(data['indexers']):
            rows.append((_t('مفهرس: %s%s', 'Indexer: %s%s') % (i.get('name'), off if i.get('off') else ''), 'i%d' % index))
        rows.append((_t('+ إضافة مفهرس Newznab', '+ Add a Newznab indexer'), 'add_i'))
        choice = dialog.select(_t('Usenet: المزودين والمفهرسات', 'Usenet: providers and indexers'), [r[0] for r in rows])
        if choice < 0:
            return True
        what = rows[choice][1]
        changed = False
        if what == 'engine':
            dialog.ok('Usenet', _t(
                'المحرك إضافة مستقلة اسمها «Usenet engine (for Dex Hub)» (من NuvioTV، رخصة GPL-3.0) ومو جزء من Dex Hub. ثبّتها من ملف zip أو من المستودع.\nأو حط البرنامج بيدك في:\n%s\nباسم: %s',
                'The engine is an add-on of its own, "Usenet engine (for Dex Hub)" (from NuvioTV, GPL-3.0), not part of Dex Hub. Install it from a zip or the repository.\nOr put the program by hand in:\n%s\nnamed: %s')
                      % (xbmcvfs.translatePath(usenet_engine.FOLDER), usenet_engine.binary_name() or '-'))
        elif what == 'add_p':
            entry = {}
            if _edit_provider(entry, new=True):
                data['providers'].append(entry)
                changed = True
        elif what == 'add_i':
            entry = {}
            if _edit_indexer(entry, new=True):
                data['indexers'].append(entry)
                changed = True
        elif what[0] == 'p':
            changed = _edit_provider(data['providers'][int(what[1:])])
        elif what[0] == 'i':
            changed = _edit_indexer(data['indexers'][int(what[1:])])
        if changed:
            data['providers'] = [p for p in data['providers'] if not p.pop('_remove', False)]
            data['indexers'] = [i for i in data['indexers'] if not i.pop('_remove', False)]
            _save(data)
