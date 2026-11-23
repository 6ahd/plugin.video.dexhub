# -*- coding: utf-8 -*-
"""AI metadata translation (Dex Hub 5.10.588): a title's story in your language when TMDb has
none in it.

Control Centre › AI › Metadata translation picks the service: Google Translate (no key) or a
chat service with its model (ai_search's 'metadata' feature, off until turned on). The title
page shows its story at once as it came; when that story is not in the metadata language
(Arabic), the translation replaces it a moment later. Each translation is kept, so the same
story opens translated next time, and only the one story of the page opened is sent.
"""
import hashlib
import json
import os
import re
import threading

import xbmc

_SCRIPTS = {'ar': '؀-ۿ', 'fa': '؀-ۿ', 'ur': '؀-ۿ', 'he': '֐-׿',
            'ru': 'Ѐ-ӿ', 'uk': 'Ѐ-ӿ', 'el': 'Ͱ-Ͽ', 'hi': 'ऀ-ॿ',
            'th': '฀-๿', 'ja': '぀-ヿ一-鿿', 'zh': '一-鿿', 'ko': '가-힯'}
_NAMES = {'ar': 'Arabic', 'fa': 'Persian', 'ur': 'Urdu', 'he': 'Hebrew', 'ru': 'Russian', 'uk': 'Ukrainian',
          'el': 'Greek', 'hi': 'Hindi', 'th': 'Thai', 'ja': 'Japanese', 'zh': 'Chinese', 'ko': 'Korean'}
_LOCK = threading.Lock()
MAX_KEEP = 3000


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] AI metadata: %s' % msg, level)


def target():
    """The metadata language's code ('ar') when its script tells a translated story apart, else ''."""
    try:
        from .continue_names import _language
        code = str(_language() or '').lower().split('-')[0]
    except Exception:
        code = ''
    return code if code in _SCRIPTS else ''


def needs(text, code):
    """True when a story has hardly any letters of the language's script."""
    text = str(text or '').strip()
    if not text or not code:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 12:
        return False
    mine = len(re.findall('[%s]' % _SCRIPTS[code], text))
    return mine < len(letters) * 0.3


def enabled():
    try:
        from . import ai_search
        f = ai_search.feature('metadata')
        return bool(f.get('on') and f.get('service'))
    except Exception:
        return False


def _path():
    import xbmcvfs
    return xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/meta_ai.json')


def _load():
    try:
        with open(_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _keep(key, text):
    with _LOCK:
        data = _load()
        data[key] = text
        if len(data) > MAX_KEEP:
            for old in list(data)[:len(data) - MAX_KEEP]:
                data.pop(old, None)
        try:
            os.makedirs(os.path.dirname(_path()), exist_ok=True)
            tmp = _path() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, _path())
        except Exception as exc:
            _log('not kept: %s' % exc, xbmc.LOGWARNING)


def _key(text, code, service, model):
    return hashlib.sha1(('%s|%s|%s|%s' % (code, service, model, text)).encode('utf-8')).hexdigest()[:24]


def cached(text, code=None):
    """The kept translation of a story, or ''."""
    code = code or target()
    try:
        from . import ai_search
        f = ai_search.feature('metadata')
    except Exception:
        return ''
    text = re.sub(r'\s+', ' ', str(text or '')).strip()      # the same key translate() keeps it by
    return _load().get(_key(text, code, f.get('service'), f.get('model') or '')) or ''


def translate(text, title='', year='', kind='plot', code=None):
    """The story in the metadata language, or '' when it could not be translated."""
    from . import ai_search
    code = code or target()
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not needs(text, code) or not enabled():
        return ''
    f = ai_search.feature('metadata')
    service, model = f.get('service'), f.get('model') or ''
    key = _key(text, code, service, model)
    hit = _load().get(key)
    if hit:
        return hit
    out = ''
    try:
        if service == 'google':
            from .subsync import subtrans
            from .subsync.transflow import _http_post
            got = subtrans._google_batch([text], code, _http_post)
            out = (got or [''])[0]
        else:
            cfg = ai_search.config('metadata')
            if not cfg:
                return ''
            what = {'plot': 'story (plot summary)', 'episode': 'episode summary',
                    'biography': 'biography of the person'}.get(kind, 'text')
            system = ('You translate film and TV metadata into %s. Translate the %s of "%s"%s naturally, as a '
                      'streaming service would show it; keep names as they are usually written in %s. Answer with '
                      'the translation only.' % (_NAMES.get(code, code), what, title or 'the title',
                                                 (' (%s)' % year) if year else '', _NAMES.get(code, code)))
            answer, _status = ai_search._chat(cfg, system, text, timeout=40)
            out = str(answer or '').strip().strip('"').strip()
    except Exception as exc:
        _log('%s' % exc, xbmc.LOGWARNING)
        return ''
    if not out or needs(out, code):
        return ''
    _keep(key, out)
    _log('%s translated with %s (%d characters)' % (kind, service, len(out)))
    return out
