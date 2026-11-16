# -*- coding: utf-8 -*-
"""AI in Dex Hub (v5.10.576): smart search, AI lists and Recommended for you.

The viewer's own key of an OpenAI-compatible service (Grok, OpenAI, Gemini,
OpenRouter, DeepSeek, or any address that speaks /chat/completions), the same
providers as DexCloud's Telegram bot (hub lib/aisearch.js). The AI names
titles; TMDb finds each one, so every card opens Dex Hub's title page.

* Smart search: a search that reads like a description ("Korean romance,
  short", "رعب عن بيت مسكون في الثمانينات") also asks the AI; its picks come
  as their own row next to the usual results (plugin.hub_search_collect).
* AI lists: Edit Home > Add rows > With AI: a description becomes a Home row
  (action lib_ai_list), kept a day.
* Recommended for you: the titles last watched go to the AI, which picks what
  to watch next (action lib_ai_picks), kept a day. This sends those titles
  to the viewer's AI service, and says so where it is turned on.

The key is kept in the add-on profile's ai.json, never written to the log.
A Stremio AI search add-on needs nothing here: Dex Hub's search already asks
every add-on's search catalog.
"""
import hashlib
import json
import os
import re
import threading
import time

import xbmc
import xbmcgui

PROVIDERS = (
    ('grok', 'Grok (xAI)', 'https://api.x.ai/v1', 'console.x.ai',
     ('grok-4-1-fast-non-reasoning', 'grok-4-fast-non-reasoning', 'grok-4-1-fast', 'grok-4-fast', 'grok-3-mini')),
    ('openai', 'OpenAI', 'https://api.openai.com/v1', 'platform.openai.com/api-keys',
     ('gpt-5-mini', 'gpt-4.1-mini', 'gpt-4o-mini')),
    ('gemini', 'Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai', 'aistudio.google.com/apikey',
     ('gemini-2.5-flash', 'gemini-flash-latest', 'gemini-2.0-flash')),
    ('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', 'openrouter.ai/keys',
     ('x-ai/grok-4-fast', 'google/gemini-2.5-flash', 'openai/gpt-4o-mini')),
    ('deepseek', 'DeepSeek', 'https://api.deepseek.com/v1', 'platform.deepseek.com/api_keys', ('deepseek-chat',)),
    ('custom', 'OpenAI-compatible', '', '', ()),
)
SEARCH_MODES = ('auto', 'always', 'off')
MAX_SEARCH, MAX_LIST, MAX_PICKS = 16, 30, 20
DAY = 86400
_HINT = re.compile(r'(^|[^a-z])(ai|gpt|recommend|suggest|similar|like)([^a-z]|$)|اقترح|مثل|يشبه|شبيه', re.I)


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return en


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] AI: %s' % msg, level)


# ---------------------------------------------------------------- the key
def _path():
    import xbmcaddon
    import xbmcvfs
    folder = xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile'))
    return os.path.join(folder, 'ai.json')


def _load():
    try:
        with open(_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)
    os.replace(temp, path)


def _provider(pid):
    return next((p for p in PROVIDERS if p[0] == pid), None)


def config():
    """{provider, key, base, model, search} or None when no key is set."""
    data = _load()
    if not data.get('key') or not data.get('base') or not data.get('model'):
        return None
    return data


def label():
    cfg = config()
    if not cfg:
        return _t('غير مضاف', 'Not set')
    p = _provider(cfg.get('provider'))
    return '%s ✓' % (p[1] if p else 'AI')


# ---------------------------------------------------------------- the AI
def _post(cfg, path, body=None, timeout=30):
    import urllib.request
    import urllib.error
    url = cfg['base'].rstrip('/') + path
    headers = {'Authorization': 'Bearer ' + cfg['key'], 'Content-Type': 'application/json', 'User-Agent': 'DexHub'}
    if 'openrouter.ai' in url:
        headers['X-Title'] = 'Dex Hub'
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method='POST' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return 200, json.loads(answer.read().decode('utf-8', 'replace') or '{}')
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            detail = {}
        return exc.code, detail
    except Exception as exc:
        return 0, {'error': str(exc)[:160]}


def _chat(cfg, system, user, timeout=30):
    status, answer = _post(cfg, '/chat/completions', {
        'model': cfg['model'], 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]},
        timeout=timeout)
    if status != 200:
        error = answer.get('error') if isinstance(answer, dict) else ''
        if isinstance(error, dict):
            error = error.get('message') or ''
        _log('the service answered %s: %s' % (status, str(error)[:120]), xbmc.LOGWARNING)
        return None, status
    try:
        text = answer['choices'][0]['message']['content']
        if isinstance(text, list):
            text = ''.join(part.get('text', '') for part in text if isinstance(part, dict))
        return str(text or ''), 200
    except Exception:
        return None, 0


def _parse(text):
    s = str(text or '')
    start, end = s.find('{'), s.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(s[start:end + 1])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_FORMAT = ('Answer with JSON only, no other text: {"note": "...", "titles": [{"title": "...", "year": 2014, "type": "movie"}]}\n'
           '"type" is "movie" or "series". "title" is the original or English release title exactly as TMDb lists it; '
           '"year" is the release or first air year. Only real, released titles. "note" is one short sentence in the '
           'person\'s language.')
SEARCH_SYSTEM = ('You find films and TV series for a person who describes what they want to watch, in any language '
                 '(often Arabic). Up to %d titles, the best match first. If the person names a title, actor, director '
                 'or franchise, honour it.\n' % MAX_SEARCH) + _FORMAT
LIST_SYSTEM = ('You build a watch list from a person\'s description of it, in any language (often Arabic). Up to %d '
               'titles that fit it well, in a sensible order (the best or the defining ones first).\n' % MAX_LIST) + _FORMAT
PICKS_SYSTEM = ('You recommend what a person should watch next, from the titles they watched lately (newest first). '
                'Up to %d %%s they have not watched, matching their taste; vary them, and avoid the titles listed.\n'
                % MAX_PICKS) + _FORMAT


def _ask(system, text, kind):
    """[{title, year, type}] from the AI, or None when it could not be asked."""
    cfg = config()
    if not cfg:
        return None
    started = time.monotonic()
    answer, status = _chat(cfg, system, text[:1500])
    if answer is None:
        return None
    data = _parse(answer)
    titles = [t for t in data.get('titles') or [] if isinstance(t, dict) and str(t.get('title') or '').strip()]
    _log('%s: %d title(s) in %.1f s' % (kind, len(titles), time.monotonic() - started))
    return titles


# ---------------------------------------------------------------- TMDb
def _lang():
    try:
        from .i18n import is_english
        return 'en-US' if is_english() else 'ar-SA'
    except Exception:
        return 'en-US'


def _find(entry, lang):
    from . import tmdb_direct as T
    name = str(entry.get('title') or '').strip()[:120]
    series = str(entry.get('type') or '').lower() in ('series', 'tv', 'show', 'tvshow')
    kind = 'tv' if series else 'movie'
    year = str(entry.get('year') or '').strip()
    params = {'query': name, 'include_adult': 'false', 'language': lang}
    if year.isdigit() and len(year) == 4:
        params['first_air_date_year' if series else 'year'] = year
    try:
        data = T._request('/search/%s' % kind, params, timeout=8, max_attempts=1) or {}
        rows = data.get('results') or []
        if not rows and len(params) > 3:
            params.pop('first_air_date_year', None)
            params.pop('year', None)
            rows = (T._request('/search/%s' % kind, params, timeout=8, max_attempts=1) or {}).get('results') or []
    except Exception:
        rows = []
    if not rows:
        return None
    item = T._row_from_tmdb(rows[0], 'series' if series else 'movie')
    return item if item.get('tmdb_id') else None


def _tmdb_rows(titles):
    lang = _lang()
    found = [None] * len(titles)

    def one(index, entry):
        found[index] = _find(entry, lang)

    threads = [threading.Thread(target=one, args=(i, e)) for i, e in enumerate(titles)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    out, seen = [], set()
    for item in found:
        if not item:
            continue
        key = (item['media_type'], item['tmdb_id'])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _cached(key, ttl, make):
    from . import tmdb_direct as T
    ckey = 'ai:' + hashlib.sha1(key.encode('utf-8')).hexdigest()
    try:
        data = T._cache_get(ckey)
    except Exception:
        data = None
    if isinstance(data, dict) and isinstance(data.get('rows'), list):
        return data['rows']
    titles = make()
    if titles is None:
        return None
    rows = _tmdb_rows(titles)
    try:
        T._cache_set(ckey, {'rows': rows}, ttl if rows else 600)
    except Exception:
        pass
    return rows


# ---------------------------------------------------------------- the three uses
def wants(query):
    """Whether a search also asks the AI: a key, and a query that reads like a description."""
    cfg = config()
    if not cfg:
        return False
    mode = cfg.get('search') or 'auto'
    if mode == 'off':
        return False
    if mode == 'always':
        return True
    words = [w for w in re.split(r'\s+', str(query or '').strip()) if w]
    return len(words) >= 3 or bool(_HINT.search(str(query or '')))


def search_entries(query):
    """Smart search: unified-search entries (plugin.hub_search_collect) flagged 'ai'."""
    rows = _cached('search:%s:%s' % (_lang(), str(query).strip().casefold()), DAY,
                   lambda: _ask(SEARCH_SYSTEM, str(query), 'search'))
    if not rows:
        return []
    from . import plugin as P
    out = []
    for n, item in enumerate(rows):
        media = item['media_type']
        path = P._content_click_path(media_type=media, canonical_id='tmdb:%s' % item['tmdb_id'],
                                     title=item['title'], tmdb_id=item['tmdb_id'])
        folder = bool(path[1]) if isinstance(path, tuple) else True
        path = path[0] if isinstance(path, tuple) else path
        out.append({'ai': True, 'path': path, 'is_folder': folder, 'title': item['title'], 'media_type': media,
                    'year': item.get('year') or '', 'poster': item.get('poster') or '',
                    'fanart': item.get('backdrop') or '', 'plot': item.get('overview') or '',
                    'rating': item.get('rating') or 0, 'tmdb_id': item['tmdb_id'],
                    'source_label': 'AI', 'score': 1.0 - n * 0.01})
    return out


def list_rows(description, fresh=False):
    """An AI list: [tmdb row] for a description, kept a day."""
    key = 'list:%s:%s' % (_lang(), str(description).strip().casefold())
    if fresh:
        _forget(key)
    return _cached(key, DAY, lambda: _ask(LIST_SYSTEM, str(description), 'list'))


def _history(media, limit=25):
    from . import playback_store
    out, seen = [], set()
    for row in playback_store.list_recent_items(limit=150):
        series = str(row.get('media_type') or '').lower() in ('series', 'episode', 'tv', 'show') or bool(row.get('season'))
        if media == 'movie' and series or media == 'series' and not series:
            continue
        title = (row.get('show_title') if series else row.get('title')) or ''
        # the same title is kept under its English and its Arabic name, with one id or
        # another: a title is one if any of its ids (a series: its show's) was seen
        ids = set(str(x).strip() for x in ((row.get('show_tmdb_id'),) if series
                                           else (row.get('tmdb_id'), row.get('imdb_id'))) if str(x or '').strip())
        if not title or title.startswith('tt') or title.casefold() in seen or ids & seen:
            continue
        seen.add(title.casefold())
        seen |= ids
        year = str(row.get('year') or '')[:4]
        out.append('%s%s (%s)' % (title, ' %s' % year if year.isdigit() else '', 'series' if series else 'movie'))
        if len(out) >= limit:
            break
    return out


def picks_rows(media):
    """Recommended for you: [tmdb row] from the last titles watched, kept a day."""
    history = _history(media)
    if len(history) < 2:
        return []
    what = {'movie': 'films', 'series': 'TV series'}.get(media, 'films and TV series')
    key = 'picks:%s:%s:%s' % (_lang(), media, hashlib.sha1('\n'.join(history[:10]).encode('utf-8')).hexdigest())
    rows = _cached(key, DAY, lambda: _ask(PICKS_SYSTEM % what, 'Watched lately:\n' + '\n'.join(history), 'picks'))
    if not rows:
        return rows
    watched = set(h.split(' (')[0].rsplit(' ', 1)[0].casefold() for h in history) | set(
        h.split(' (')[0].casefold() for h in history)
    keep = [r for r in rows if str(r.get('title') or '').casefold() not in watched]
    if media in ('movie', 'series'):
        keep = [r for r in keep if r.get('media_type') == media]
    return keep


def _forget(key):
    try:
        from . import tmdb_direct as T
        T._cache_set('ai:' + hashlib.sha1(key.encode('utf-8')).hexdigest(), {}, 1)
    except Exception:
        pass


# ---------------------------------------------------------------- setting it up
def setup():
    """Control Centre's AI entry: the service and its key, the search mode, or removal."""
    dialog = xbmcgui.Dialog()
    cfg = config()
    title = _t('الذكاء الاصطناعي', 'AI')
    if cfg:
        p = _provider(cfg.get('provider'))
        modes = {'auto': _t('للأوصاف فقط', 'Descriptions only'), 'always': _t('كل بحث', 'Every search'),
                 'off': _t('مطفي', 'Off')}
        choice = dialog.select('%s · %s' % (title, p[1] if p else 'AI'), [
            _t('تغيير الخدمة أو المفتاح', 'Change the service or key'),
            '%s: %s' % (_t('البحث الذكي', 'Smart search'), modes.get(cfg.get('search') or 'auto')),
            _t('تجربة المفتاح', 'Test the key'),
            _t('حذف المفتاح', 'Remove the key')])
        if choice < 0:
            return False
        if choice == 1:
            pick = dialog.select(_t('البحث الذكي', 'Smart search'), [modes[m] for m in SEARCH_MODES],
                                 preselect=SEARCH_MODES.index(cfg.get('search') or 'auto'))
            if pick >= 0:
                cfg['search'] = SEARCH_MODES[pick]
                _save(cfg)
            return pick >= 0
        if choice == 2:
            _test(cfg, quiet=False)
            return False
        if choice == 3:
            if dialog.yesno(title, _t('أحذف مفتاح الذكاء الاصطناعي من هذا الجهاز؟', 'Remove the AI key from this device?')):
                _save({})
                return True
            return False
    names = [p[1] for p in PROVIDERS]
    pick = dialog.select(_t('اختر خدمة الذكاء الاصطناعي', 'Pick the AI service'), names)
    if pick < 0:
        return False
    pid, name, base, where, prefer = PROVIDERS[pick]
    if pid == 'custom':
        base = (dialog.input(_t('العنوان (ينتهي بـ /v1)', 'Address (ends in /v1)'), 'https://') or '').strip().rstrip('/')
        if not base.startswith('https://'):
            return False
    key = (dialog.input('%s  (%s)' % (_t('مفتاح %s', '%s key') % name, where) if where else _t('المفتاح', 'Key'),
                        option=xbmcgui.ALPHANUM_HIDE_INPUT) or '').strip()
    if not key:
        return False
    new = {'provider': pid, 'key': key, 'base': base, 'model': prefer[0] if prefer else '',
           'search': (cfg or {}).get('search') or 'auto'}
    model = _pick_model(new, prefer)
    if not model:
        model = (dialog.input(_t('اسم النموذج', 'Model name')) or '').strip()
        if not model:
            return False
    new['model'] = model
    if not _test(new, quiet=False):
        return False
    _save(new)
    return True


def _pick_model(cfg, prefer):
    """The first model of ``prefer`` the key can use (from /models), else the first of them."""
    status, answer = _post(cfg, '/models', None, timeout=15)
    if status != 200:
        return prefer[0] if prefer else ''
    have = set(str(m.get('id') or '').split('/')[-1] if cfg['provider'] == 'gemini' else str(m.get('id') or '')
               for m in (answer.get('data') or []) if isinstance(m, dict))
    for model in prefer:
        if model in have:
            return model
    return prefer[0] if prefer else (sorted(have)[0] if have else '')


def _test(cfg, quiet=True):
    titles_ok = False
    answer, status = _chat(cfg, SEARCH_SYSTEM, 'a heist film', timeout=40)
    if answer is not None:
        titles_ok = bool(_parse(answer).get('titles'))
    if not quiet:
        if titles_ok:
            xbmcgui.Dialog().notification('Dex Hub', _t('المفتاح يشتغل ✓', 'The key works ✓'), xbmcgui.NOTIFICATION_INFO, 3500)
        else:
            why = {401: _t('المفتاح مرفوض', 'The key was refused'), 403: _t('المفتاح مرفوض', 'The key was refused'),
                   402: _t('الرصيد خلص', 'No credit left'), 429: _t('الخدمة مشغولة، جرّب بعدين', 'The service is busy, try later'),
                   404: _t('النموذج غير موجود', 'The model was not found')}.get(status, _t('ما رد بقائمة', 'No list came back'))
            xbmcgui.Dialog().ok(_t('الذكاء الاصطناعي', 'AI'), why)
    return titles_ok
