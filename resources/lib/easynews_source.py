# -*- coding: utf-8 -*-
"""EasyNews as a source of its own (v5.10.557).

A viewer with an EasyNews account signs in once (Settings > Sources > EasyNews); after that
every title's source search also asks EasyNews itself, over the viewer's own connection, and
its files join the same list, tabs, filter and ranking as every other source. No other
add-on and no server in between.

- search: members.easynews.com's own search (the request its site makes), by the title's
  English name and year, or name and SxxEyy;
- only files whose name starts with the title and then carries the year (or the episode) are
  kept; samples, trailers and extras are left out, and a file once per name and size;
- playing: the members link answers with the file's address on EasyNews' file servers; that
  address is asked for when the source is picked (resolve) and Kodi plays it.

The sign-in is kept in addon_data/plugin.video.dexhub/easynews.json on this device only.
Written from EasyNews' public member API as other Kodi add-ons use it; not yet tried against
a live account when 5.10.557 was packed (the Mac used for testing has none).
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urljoin

import xbmc
import xbmcgui
import xbmcvfs

PROVIDER_ID = '__easynews__'
COLOR = 'FFF0A54E'
NAME = 'EasyNews'
_FILE = 'special://profile/addon_data/plugin.video.dexhub/easynews.json'
_HOST = 'https://members.easynews.com'
_SEARCH = _HOST + '/2.0/search/solr-search/advanced'
_EXT = ('mkv', 'mp4', 'avi', 'm4v', 'ts', 'm2ts', 'mts', 'mov', 'wmv', 'webm', 'mpg', 'mpeg', 'vob', 'iso')
_PARAMS = {'st': 'adv', 'sb': 1, 'fex': ', '.join(_EXT), 'fty[]': 'VIDEO', 'spamf': 1, 'u': 1, 'gx': 1,
           'pno': 1, 'sS': 3, 's1': 'relevance', 's1d': '-', 's2': 'dsize', 's2d': '-', 'pby': 250, 'safeO': 0}
_JUNK = re.compile(r'(?<![a-z])(sample|trailer|teaser|featurette|extras?|behind[ ._-]the[ ._-]scenes)(?![a-z])', re.I)
_WORD = re.compile(r'[a-z0-9]+')
_RES = re.compile(r'(?<![a-z0-9])(2160p|4k|uhd|1080p|720p|576p|480p)(?![a-z0-9])', re.I)
_AGENT = 'Dex Hub (Kodi)'
_LOGIN = {}


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] EasyNews: %s' % msg, level)


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


# ------------------------------------------------------------ the sign-in

def login():
    """(user, password), ('', '') when not signed in."""
    if 'value' not in _LOGIN:
        try:
            with open(xbmcvfs.translatePath(_FILE), 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            _LOGIN['value'] = (str(data.get('user') or ''), str(data.get('password') or ''))
        except Exception:
            _LOGIN['value'] = ('', '')
    return _LOGIN['value']


def is_set_up():
    return all(login())


def _save(user, password):
    path = xbmcvfs.translatePath(_FILE)
    try:
        if not user:
            if os.path.isfile(path):
                os.remove(path)
        else:
            folder = os.path.dirname(path)
            if not os.path.isdir(folder):
                os.makedirs(folder)
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({'user': user, 'password': password, 't': int(time.time())}, handle)
    except Exception as exc:
        _log('sign-in not saved: %s' % exc, xbmc.LOGWARNING)
    _LOGIN.pop('value', None)


def _auth(user, password):
    return 'Basic ' + base64.b64encode(('%s:%s' % (user, password)).encode('utf-8')).decode('ascii')


# ------------------------------------------------------------ the search

def _words(text):
    text = str(text or '').lower().replace('&', ' and ').replace("'", '').replace(u'’', '')
    return _WORD.findall(text)


def _fits(name, words, year, season, episode):
    """The file's name starts with the title, then carries the year (a film) or SxxEyy."""
    have = _words(name)
    if have[:1] == ['the'] and words[:1] != ['the']:
        have = have[1:]
    if words[:1] == ['the'] and have[:1] != ['the']:
        words = words[1:]
    if not words or have[:len(words)] != words:
        return False
    rest = have[len(words):]
    if season is None:
        if not str(year).isdigit():
            return True
        near = {str(int(year) + step) for step in (-1, 0, 1)}
        return any(word in near for word in rest[:3])
    if rest[:1] and rest[0].isdigit() and len(rest[0]) == 4:
        rest = rest[1:]                                 # "Show 2019 S01E02"
    for word in rest[:3]:
        found = re.match(r'^s(\d{1,2})e(\d{1,3})(?:e\d{1,3})*$', word) or re.match(r'^(\d{1,2})x(\d{1,3})$', word)
        if found and int(found.group(1)) == season and int(found.group(2)) == episode:
            return True
    return False


def _quality(width, name):
    found = _RES.search(re.sub(r'[._]', ' ', name or ''))
    if found:
        text = found.group(1).upper()
        return '2160p' if text in ('4K', 'UHD', '2160P') else text.lower()
    try:
        width = int(width or 0)
    except (TypeError, ValueError):
        width = 0
    return '2160p' if width > 1920 else '1080p' if width > 1280 else '720p' if width > 720 else 'SD'


def _size_label(size):
    return '%.2f GB' % (size / 1073741824.0) if size >= 1073741824 else '%d MB' % (size // 1048576) if size else ''


def rows(data, words, year, season, episode):
    """EasyNews' answer as source rows (Stremio-like streams)."""
    farm, port = data.get('dlFarm'), data.get('dlPort')
    out, seen = [], set()
    shortest = 300 if season is not None else 1200      # leaves samples and clips out
    for item in data.get('data') or []:
        try:
            if not isinstance(item, dict) or item.get('virus') or item.get('passwd'):
                continue
            if str(item.get('type') or 'VIDEO').upper() != 'VIDEO':
                continue
            post, name, ext = str(item['0']), str(item['10']), str(item['11'])
            if ext.lstrip('.').lower() not in _EXT or _JUNK.search(name):
                continue
            if not _fits(name, words, year, season, episode):
                continue
            try:
                seconds = int(float(item.get('runtime') or 0))
            except (TypeError, ValueError):
                seconds = 0
            if 0 < seconds < shortest:
                continue
            try:
                size = int(float(item.get('rawSize') or 0))
            except (TypeError, ValueError):
                size = 0
            key = (name.lower(), size // 10485760)
            if key in seen:
                continue
            seen.add(key)
            path = quote('%s%s/%s%s' % (post, ext, name, ext))
            link = '%s/dl/%s/%s/%s' % (_HOST, farm, port, path) if farm and port else '%s/dl/%s' % (_HOST, path)
            langs = item.get('alangs') or []
            langs = [langs] if isinstance(langs, str) else list(langs)
            facts = u' · '.join(x for x in [_size_label(size)] + [str(l).upper() for l in langs[:3] if l] if x)
            stream = {'name': '[COLOR %s]%s[/COLOR]\n%s' % (COLOR, NAME, _quality(item.get('width'), name)),
                      'title': '%s%s\n%s' % (name, ext, facts) if facts else name + ext,
                      'url': link,
                      'behaviorHints': {'bingeGroup': 'dexhub-easynews', 'filename': name + ext}}
            if size:
                stream['behaviorHints']['videoSize'] = size
            out.append(stream)
        except Exception as exc:
            _log('a file skipped: %s' % exc, xbmc.LOGDEBUG)
    return out


def search(text, user='', password='', seconds=20):
    """EasyNews' answer (a dict) for a search text; raises on a wrong sign-in or no answer."""
    user, password = (user, password) if user else login()
    if not (user and password):
        raise ValueError('not signed in')
    request = urllib.request.Request(_SEARCH + '?' + urlencode(dict(_PARAMS, gps=text)), headers={
        'Authorization': _auth(user, password), 'Accept': 'application/json', 'User-Agent': _AGENT})
    with urllib.request.urlopen(request, timeout=seconds) as answer:
        data = json.loads(answer.read().decode('utf-8', 'replace'))
    if not isinstance(data, dict):
        raise ValueError('unexpected answer')
    return data


def targets(meta, canonical_id, media_type, ids=None, season=None, episode=None):
    """One search target when the viewer is signed in."""
    if not is_set_up():
        return []
    try:
        from .source_addons import skipped
        if PROVIDER_ID in skipped():
            return []
    except Exception:
        pass
    from .search.native_titles import candidates
    meta = meta or {}
    year = str(meta.get('year') or meta.get('releaseInfo') or meta.get('release_date')
               or meta.get('first_air_date') or '')[:4]
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    return [({'id': PROVIDER_ID, 'name': NAME, '_title': meta.get('name') or meta.get('title') or '',
              '_titles': candidates(meta, media_type), '_year': year, '_ids': dict(ids or {}),
              '_media_type': media_type, '_enrich_titles': True,
              '_season': int(season) if episodic else None, '_episode': int(episode) if episodic else None},
             str(canonical_id))]


def _names(provider):
    """The title's names to search by, English first (EasyNews' files carry English names)."""
    names = []
    tmdb_id = str((provider.get('_ids') or {}).get('tmdb_id') or '')
    if tmdb_id:
        try:
            from . import tmdb_direct
            kind = 'tv' if provider.get('_season') is not None or str(provider.get('_media_type') or '').lower() in (
                'series', 'tv', 'show', 'episode', 'anime') else 'movie'
            names.extend(tmdb_direct.english_titles_for(tmdb_id, kind) or [])
        except Exception as exc:
            _log('English name: %s' % exc, xbmc.LOGDEBUG)
    names.extend(list(provider.get('_titles') or []) + [provider.get('_title') or ''])
    out = []
    for name in names:
        name = str(name or '').strip()
        # a name with no Latin letter or digit cannot match a file's name
        if name and _words(name) and ' '.join(_words(name)) not in [' '.join(_words(n)) for n in out]:
            out.append(name)
    return out


def payload(provider):
    season, episode, year = provider.get('_season'), provider.get('_episode'), str(provider.get('_year') or '')
    started = time.time()
    for name in _names(provider)[:2]:
        words = _words(name)
        text = ('%s S%02dE%02d' % (' '.join(words), season, episode) if season is not None
                else ('%s %s' % (' '.join(words), year)).strip())
        try:
            data = search(text)
        except urllib.error.HTTPError as exc:
            _log('search refused (%s): check the sign-in' % exc.code, xbmc.LOGWARNING)
            return {'streams': []}
        except Exception as exc:
            _log('search failed: %s' % exc, xbmc.LOGWARNING)
            return {'streams': []}
        found = rows(data, words, year, season, episode)
        _log('"%s": %d of %d files fit, %.1fs' % (text, len(found), len(data.get('data') or []), time.time() - started))
        if found:
            return {'streams': found}
    return {'streams': []}


# ------------------------------------------------------------ playing

def is_row(ctx):
    return str((ctx or {}).get('source_provider_id') or '') == PROVIDER_ID


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def resolve(ctx, seconds=20):
    """The file's own address for a picked row: the members link answers with a redirect to
    EasyNews' file server. '' when the sign-in is gone or the file is not there any more."""
    link = str((ctx or {}).get('stream_url') or '').split('|', 1)[0]
    user, password = login()
    if not link.startswith(_HOST) or not (user and password):
        return ''
    opener = urllib.request.build_opener(_NoRedirect)
    auth = _auth(user, password)
    for _hop in range(5):
        headers = {'User-Agent': _AGENT, 'Range': 'bytes=0-0'}
        if link.startswith(_HOST):
            headers['Authorization'] = auth             # the sign-in goes to EasyNews' own host only
        try:
            with opener.open(urllib.request.Request(link, headers=headers), timeout=seconds) as answer:
                answer.read(1)
                # no redirect: the members host serves the file itself, Kodi sends the sign-in
                return link + ('|' + urlencode({'Authorization': auth, 'User-Agent': _AGENT})
                               if link.startswith(_HOST) else '')
        except urllib.error.HTTPError as exc:
            where = exc.headers.get('Location') if exc.code in (301, 302, 303, 307, 308) else ''
            if not where:
                _log('the file did not open (%s)' % exc.code, xbmc.LOGWARNING)
                return ''
            link = urljoin(link, where)
            if not link.startswith(_HOST):
                return link
        except Exception as exc:
            _log('the file did not open: %s' % exc, xbmc.LOGWARNING)
            return ''
    return ''


# ------------------------------------------------------------ setting up

def setup():
    """Sign in, check the sign-in, or sign out."""
    dialog = xbmcgui.Dialog()
    user, password = login()
    if user:
        choice = dialog.select('EasyNews', [_t('فحص الحساب (%s)', 'Check the account (%s)') % user,
                                            _t('تغيير الحساب', 'Change the account'),
                                            _t('تسجيل الخروج', 'Sign out')])
        if choice < 0:
            return False
        if choice == 2:
            _save('', '')
            dialog.notification('EasyNews', _t('تم تسجيل الخروج', 'Signed out'), xbmcgui.NOTIFICATION_INFO, 3000)
            return True
        if choice == 0:
            return _check(user, password)
    user = dialog.input(_t('اسم المستخدم في EasyNews', 'EasyNews user name'), type=xbmcgui.INPUT_ALPHANUM)
    if not user:
        return False
    password = dialog.input(_t('كلمة المرور', 'Password'), type=xbmcgui.INPUT_ALPHANUM,
                            option=xbmcgui.ALPHANUM_HIDE_INPUT)
    if not password:
        return False
    if _check(user.strip(), password):
        _save(user.strip(), password)
        return True
    return False


def _check(user, password):
    dialog = xbmcgui.Dialog()
    try:
        data = search('big buck bunny', user, password, seconds=15)
    except urllib.error.HTTPError as exc:
        dialog.ok('EasyNews', _t('EasyNews رفض الدخول (%s). تأكد من الاسم وكلمة المرور.',
                                 'EasyNews refused the sign-in (%s). Check the name and the password.') % exc.code)
        return False
    except Exception as exc:
        dialog.ok('EasyNews', _t('ما وصلنا لـEasyNews: %s', 'EasyNews did not answer: %s') % exc)
        return False
    dialog.ok('EasyNews', _t('الحساب شغال (%d نتيجة في بحث تجريبي). ملفات EasyNews بتطلع مع مصادر كل عمل.',
                             'The account works (%d results in a trial search). EasyNews files will show '
                             'among every title\'s sources.') % len(data.get('data') or []))
    return True
