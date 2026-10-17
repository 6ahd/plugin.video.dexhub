# -*- coding: utf-8 -*-
"""IMDb's Parents Guide (v5.10.322, GitHub issue #21): how much sex and
nudity, violence and gore, profanity, alcohol, drugs and smoking, and
frightening and intense scenes a title has (None, Mild, Moderate, Severe),
as Nuvio shows it when a video starts.

Read from IMDb's GraphQL API (the one imdb.com uses), kept 30 days per title
(a title IMDb has no guide for, or a failed read, is asked again after a
day). Shown on the title page (one line under the ratings) and for a few
seconds in the player's top left corner when a video starts.
"""
import json
import os
import threading
import time

import xbmc

_URLS = ('https://api.graphql.imdb.com/', 'https://caching.graphql.imdb.com/')
_QUERY = ('query ParentsGuide($id: ID!) { title(id: $id) { parentsGuide { categories { '
          'category { id text } severity { id text } } } } }')
_HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'Origin': 'https://www.imdb.com',
    'Referer': 'https://www.imdb.com/',
    'x-imdb-client-name': 'imdb-web-next',
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/126.0 Safari/537.36'),
}
KEEP = 30 * 86400
KEEP_EMPTY = 86400
_LOCK = threading.Lock()
_MEMO = {}

# IMDb's category ids, in the order Nuvio lists them
ORDER = ('NUDITY', 'VIOLENCE', 'PROFANITY', 'ALCOHOL', 'FRIGHTENING')
NAMES = {
    'NUDITY': ('الجنس والعري', 'Sex & Nudity'),
    'VIOLENCE': ('العنف والدموية', 'Violence & Gore'),
    'PROFANITY': ('الألفاظ', 'Profanity'),
    'ALCOHOL': ('الكحول والمخدرات والتدخين', 'Alcohol, Drugs & Smoking'),
    'FRIGHTENING': ('المشاهد المخيفة', 'Frightening & Intense Scenes'),
}
SHORT = {
    'NUDITY': ('العري', 'Nudity'), 'VIOLENCE': ('العنف', 'Violence'), 'PROFANITY': ('الألفاظ', 'Profanity'),
    'ALCOHOL': ('الكحول والمخدرات', 'Drugs'), 'FRIGHTENING': ('الرعب', 'Intense'),
}
SEVERITY = {
    'NONE': ('لا يوجد', 'None', 'FF7FD08A'),
    'MILD': ('خفيف', 'Mild', 'FFFFD54F'),
    'MODERATE': ('متوسط', 'Moderate', 'FFFFA040'),
    'SEVERE': ('شديد', 'Severe', 'FFFF5A5A'),
}


def _english():
    try:
        from .i18n import is_english
        return bool(is_english())
    except Exception:
        return False


_PATH = []


def _path():
    if not _PATH:
        try:
            import xbmcaddon
            import xbmcvfs
            folder = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
        except Exception:
            return ''
        _PATH.append(os.path.join(folder, 'parents_guide.json'))
    return _PATH[0]


def _load():
    path = _path()
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    path = _path()
    if not path:
        return
    try:
        if len(data) > 1500:
            for key in sorted(data, key=lambda k: data[k].get('t', 0))[:len(data) - 1500]:
                data.pop(key, None)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)
    except Exception:
        pass


def _category(cid, text):
    """IMDb's category, by its id or its name (NUDITY, "Sex & Nudity")."""
    words = (cid + ' ' + text).upper()
    for key, marks in (('NUDITY', ('NUDITY', 'SEX')), ('VIOLENCE', ('VIOLENCE', 'GORE')),
                       ('PROFANITY', ('PROFANITY',)), ('ALCOHOL', ('ALCOHOL', 'DRUG', 'SMOK')),
                       ('FRIGHTENING', ('FRIGHT', 'INTENSE'))):
        if any(mark in words for mark in marks):
            return key
    return ''


def _severity(sid, text):
    """None, Mild, Moderate or Severe, by its id or its name."""
    words = (sid + ' ' + text).upper()
    for key in ('SEVERE', 'MODERATE', 'MILD', 'NONE'):
        if key in words:
            return key
    return ''


def _ask(imdb, timeout):
    import urllib.request
    body = json.dumps({'query': _QUERY, 'operationName': 'ParentsGuide', 'variables': {'id': imdb}}).encode('utf-8')
    last = None
    for url in _URLS:
        try:
            request = urllib.request.Request(url, data=body, headers=_HEADERS, method='POST')
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                data = json.loads(answer.read().decode('utf-8', 'replace'))
            title = (data.get('data') or {}).get('title') or {}
            cats = ((title.get('parentsGuide') or {}).get('categories')) or []
            out = []
            for cat in cats:
                category = (cat or {}).get('category') or {}
                severity = (cat or {}).get('severity') or {}
                cid = _category(str(category.get('id') or ''), str(category.get('text') or ''))
                sid = _severity(str(severity.get('id') or ''), str(severity.get('text') or ''))
                if cid and sid and cid not in [r[0] for r in out]:
                    out.append([cid, sid])
            out.sort(key=lambda r: ORDER.index(r[0]))
            return out
        except Exception as exc:
            last = exc
    raise last or RuntimeError('no answer')


def guide(imdb, timeout=6.0, network=True):
    """[[category id, severity id], ...] for an IMDb id ('tt...'), [] when
    IMDb has none, None when it cannot be read now."""
    imdb = str(imdb or '').strip()
    if not imdb.startswith('tt'):
        return []
    now = time.time()
    with _LOCK:
        hit = _MEMO.get(imdb)
        if hit is None:
            hit = _load().get(imdb)
        if hit and now - hit.get('t', 0) < (KEEP if hit.get('g') else KEEP_EMPTY):
            _MEMO[imdb] = hit
            return list(hit.get('g') or [])
    if not network:
        return None
    try:
        found = _ask(imdb, timeout)
    except Exception as exc:
        xbmc.log('[DexHub] parents guide: %s not read: %s' % (imdb, exc), xbmc.LOGINFO)
        found = None
    with _LOCK:
        data = _load()
        data[imdb] = {'t': now, 'g': found or []}
        _MEMO[imdb] = data[imdb]
        _save(data)
    return found


def line(rows, short=False):
    """'Sex & Nudity: Mild   Violence & Gore: Moderate ...' with each
    severity in its colour (Kodi label markup)."""
    en = 1 if _english() else 0
    parts = []
    for cid, sid in rows or []:
        name = (SHORT if short else NAMES)[cid][en]
        sev = SEVERITY[sid]
        parts.append('%s: [COLOR %s][B]%s[/B][/COLOR]' % (name, sev[2], sev[en]))
    return '     '.join(parts)


def lines(rows):
    """One category a line, for the player's corner."""
    en = 1 if _english() else 0
    out = []
    for cid, sid in rows or []:
        sev = SEVERITY[sid]
        out.append('%s:  [COLOR %s][B]%s[/B][/COLOR]' % (NAMES[cid][en], sev[2], sev[en]))
    return '[CR]'.join(out)


def pills(rows):
    """One label a category for the title page's pills (v5.10.323): the
    short name, then the severity in its colour."""
    en = 1 if _english() else 0
    out = []
    for cid, sid in rows or []:
        sev = SEVERITY[sid]
        out.append('[COLOR B3FFFFFF]%s[/COLOR]  [COLOR %s][B]%s[/B][/COLOR]' % (SHORT[cid][en], sev[2], sev[en]))
    return out
