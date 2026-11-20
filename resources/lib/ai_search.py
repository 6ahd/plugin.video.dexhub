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
    # v5.10.579: Groq (not xAI's Grok): fast open models behind an OpenAI-compatible API
    ('groq', 'Groq', 'https://api.groq.com/openai/v1', 'console.groq.com/keys',
     ('openai/gpt-oss-120b', 'llama-3.3-70b-versatile', 'meta-llama/llama-4-maverick-17b-128e-instruct',
      'llama-3.1-8b-instant')),
    ('openai', 'OpenAI', 'https://api.openai.com/v1', 'platform.openai.com/api-keys',
     ('gpt-5-mini', 'gpt-4.1-mini', 'gpt-4o-mini')),
    ('gemini', 'Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai', 'aistudio.google.com/apikey',
     ('gemini-2.5-flash', 'gemini-flash-latest', 'gemini-2.0-flash')),
    ('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', 'openrouter.ai/keys',
     ('x-ai/grok-4-fast', 'google/gemini-2.5-flash', 'openai/gpt-4o-mini')),
    ('deepseek', 'DeepSeek', 'https://api.deepseek.com/v1', 'platform.deepseek.com/api_keys', ('deepseek-chat',)),
    # v5.10.588: Claude and Mistral through their OpenAI-compatible addresses, as Flix offers them
    ('anthropic', 'Claude', 'https://api.anthropic.com/v1', 'console.anthropic.com/settings/keys',
     ('claude-haiku-5-5', 'claude-sonnet-5-5')),
    ('mistral', 'Mistral', 'https://api.mistral.ai/v1', 'console.mistral.ai/api-keys', ('mistral-small-latest',)),
    # v5.10.588: DexWorld's own OpenAI-compatible address, its key the DexWorld code
    ('dexworld', 'DexWorld', 'https://dexworld.cc/subtitles/api/openai/v1', 'dexworld.cc', ()),
    # any OpenAI-compatible address, http or https; a local one (Ollama, LM Studio) needs no key
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


# v5.10.588: the services and their keys once, then for each feature its own service and model
# (ai.json {"v": 2, "services": {pid: {key, base}}, "features": {fid: {on, service, model, free}}}).
# Subtitle translation also has Google Translate (no key) and DexWorld's own translation.
FEATURES = (
    ('search', ('البحث بالذكاء الاصطناعي', 'AI search'),
     ('تكتب وصف في البحث وتطلع لك أعمال تناسبه', 'Describe what you want in Search and get titles that fit')),
    ('lists', ('المقترحات وقوائم AI', 'AI recommendations and lists'),
     ('«مقترحات لك» من اللي شفته، وصفوف من وصف تكتبه', '"Recommended for you" from what you watched, and rows from a description')),
    ('subtitles', ('ترجمة الترجمة', 'Subtitle translation'),
     ('زر «ترجمة آلية» في المشغّل', 'The player\'s Translate subtitle button')),
    ('metadata', ('ترجمة الميتاداتا', 'AI metadata translation'),
     ('قصة العمل بلغتك لما TMDb ما عنده ترجمتها', 'A title\'s story in your language when TMDb has none in it')),
)
SENT = {
    'search': ('الوصف اللي تكتبه في البحث.', 'The description you type in Search.'),
    'lists': ('أسماء وسنوات اللي شفته مؤخراً، وهل هو فيلم أو مسلسل، والوصف اللي تكتبه للقائمة.',
              'The titles and years of what you watched lately, whether each is a film or a series, and the '
              'description you type for a list.'),
    'metadata': ('قصة العمل اللي فتحت صفحته، واسمه وسنته، واللغة اللي تترجم لها.',
                 'The story of the title whose page you opened, its name and year, and the language to translate into.'),
    'subtitles': ('سطور الترجمة اللي تترجمها، ولغة الترجمة، ومع DexWorld اسم العمل والموسم والحلقة.',
                  'The subtitle lines to translate and the language to translate into; with DexWorld also the '
                  'title with its season and episode.'),
}
SUBTITLE_SERVICES = ('google', 'dexworld_tr', 'silo')     # besides the chat services (silo: v5.10.590)


def _data():
    """ai.json as version 2 (an older one, a single service for everything, is read into it)."""
    data = _load()
    if data.get('v') == 2:
        data.setdefault('services', {})
        data.setdefault('features', {})
        return data
    out = {'v': 2, 'services': {}, 'features': {}, 'search_mode': 'auto'}
    pid = data.get('provider')
    if pid and data.get('base'):
        out['services'][pid] = {'key': data.get('key') or '', 'base': data['base']}
        mode = data.get('search') or 'auto'
        out['search_mode'] = mode if mode != 'off' else 'auto'
        for fid in ('search', 'lists'):
            out['features'][fid] = {'on': not (fid == 'search' and mode == 'off'), 'service': pid,
                                    'model': data.get('model') or '', 'free': bool(data.get('free'))}
    return out


def _usable(pid, svc):
    return bool(svc and svc.get('base') and (svc.get('key') or pid == 'custom'))


def services():
    """{pid: {key, base}} of the services set up with their key."""
    return dict((pid, svc) for pid, svc in _data()['services'].items() if _usable(pid, svc))


_UNSAVED = {}      # v5.10.592: a choice that could not be written (a full disk), for this run


def feature(fid):
    """{on, service, model, free} of a feature (subtitles: Google Translate unless chosen)."""
    f = dict(_UNSAVED.get(fid) or _data()['features'].get(fid) or {})
    if fid == 'subtitles':
        f.setdefault('service', 'google')
        f['on'] = True
    return f


def config(fid='search'):
    """{provider, key, base, model} the feature asks, or None when it is off or has no service set."""
    f = feature(fid)
    pid = f.get('service')
    if not f.get('on') or pid in SUBTITLE_SERVICES or pid == 'google':
        return None
    svc = services().get(pid)
    if not svc or not f.get('model'):
        return None
    return {'provider': pid, 'key': svc.get('key') or '', 'base': svc['base'], 'model': f['model'],
            'free': bool(f.get('free')), 'search': _data().get('search_mode') or 'auto'}


def _no_chat(pid):
    return pid in SUBTITLE_SERVICES or pid == 'google'


def service_name(pid):
    if pid == 'google':
        return 'Google Translate'
    if pid == 'dexworld_tr':
        return _t('ترجمة DexWorld', 'DexWorld translation')
    if pid == 'silo':
        return 'Silo'
    p = _provider(pid)
    return p[1] if p else str(pid or 'AI')


def feature_label(fid):
    f = feature(fid)
    if not f.get('on'):
        return _t('مطفي', 'Off')
    pid = f.get('service')
    if _no_chat(pid):
        return service_name(pid)
    if not pid or not f.get('model'):
        return _t('اختر الخدمة', 'Choose the service')
    return '%s · %s' % (service_name(pid), f.get('model'))


def label():
    n = len(services())
    return _t('%d خدمة' % n, '%d service(s)' % n) if n else _t('غير مضاف', 'Not set')


# ---------------------------------------------------------------- the AI
def _post(cfg, path, body=None, timeout=30):
    import urllib.request
    import urllib.error
    url = cfg['base'].rstrip('/') + path
    headers = {'Content-Type': 'application/json', 'User-Agent': 'DexHub'}
    if cfg.get('key'):              # v5.10.588: a local service (Ollama, LM Studio) takes none
        headers['Authorization'] = 'Bearer ' + cfg['key']
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
    cfg = config('search' if kind == 'search' else 'lists')
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
    cfg = config('search')
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
    """Control Centre › AI: each feature with its service and model, the services and keys, and
    what each sends. Any change saves at once."""
    dialog = xbmcgui.Dialog()
    changed = False
    while True:
        rows = ['%s:  [B]%s[/B]' % (_t(*names), feature_label(fid)) for fid, names, _d in FEATURES]
        rows.append('%s:  [B]%s[/B]' % (_t('الخدمات والمفاتيح', 'Services and keys'), label()))
        rows.append(_t('وش ينرسل لكل خدمة', 'What each sends'))
        pick = dialog.select(_t('الذكاء الاصطناعي', 'AI'), rows)
        if pick < 0:
            return changed
        if pick < len(FEATURES):
            changed = edit_feature(FEATURES[pick][0]) or changed
        elif pick == len(FEATURES):
            changed = edit_services() or changed
        else:
            show_sent()


def show_sent():
    lines = [_t('هذي الميزات ترسل البيانات اللي تحت من جهازك للخدمة اللي اخترتها لها، بمفتاحك. تطلع من جهازك '
                'والخدمة تتعامل معها حسب شروطها.',
                'These features send the data below from this device to the service you chose for each, with '
                'your own key. It leaves your device, and the service handles it under its own terms.'), '']
    for fid, names, _d in FEATURES:
        lines.append('[B]%s[/B] (%s)' % (_t(*names), feature_label(fid)))
        lines.append(_t(*SENT[fid]))
        lines.append('')
    lines.append('[B]%s[/B]' % _t('ما ينرسل أبداً', 'Never sent'))
    lines.append(_t('ملفاتك والصوت والفيديو، ومفاتيحك لأي خدمة غير خدمتها.',
                    'Your media files, any audio or video, and a key to any service but its own.'))
    xbmcgui.Dialog().textviewer(_t('وش ينرسل', 'What is sent'), '\n'.join(lines))


def edit_feature(fid):
    """A feature: on or off, its service, then that service's model (from its list, or typed)."""
    dialog = xbmcgui.Dialog()
    names = next(n for f, n, _d in FEATURES if f == fid)
    data = _data()
    f = dict(data['features'].get(fid) or {})
    if fid == 'subtitles':
        f.setdefault('service', 'google')
    rows, keys = [], []
    if fid != 'subtitles':
        rows.append('%s:  [B]%s[/B]' % (_t('الحالة', 'State'), _t('شغّال', 'On') if f.get('on') else _t('مطفي', 'Off')))
        keys.append('on')
    rows.append('%s:  [B]%s[/B]' % (_t('الخدمة', 'Service'), service_name(f.get('service')) if f.get('service') else '-'))
    keys.append('service')
    if f.get('service') and not _no_chat(f.get('service')):
        rows.append('%s:  [B]%s[/B]' % (_t('النموذج', 'Model'), f.get('model') or '-'))
        keys.append('model')
    if fid == 'search':
        modes = {'auto': _t('للأوصاف فقط', 'Descriptions only'), 'always': _t('كل بحث', 'Every search')}
        rows.append('%s:  [B]%s[/B]' % (_t('متى', 'When'), modes.get(data.get('search_mode') or 'auto', modes['auto'])))
        keys.append('mode')
    if f.get('service') and not _no_chat(f.get('service')):
        rows.append(_t('جرّب', 'Test'))
        keys.append('test')
    pick = dialog.select(_t(*names), rows)
    if pick < 0:
        return False
    what = keys[pick]
    if what == 'on':
        f['on'] = not f.get('on')
        if f['on'] and not (f.get('service') and f.get('model')):
            if not _choose_service(fid, f):
                return False
    elif what == 'service':
        if not _choose_service(fid, f):
            return False
        f['on'] = True
    elif what == 'model':
        svc = services().get(f.get('service'))
        if not svc:
            return False
        prov = _provider(f.get('service'))
        model = _choose_model(dict(svc, provider=f['service'], model=f.get('model') or ''), prov[4] if prov else ())
        if not model:
            return False
        f['model'], f['free'] = model, bool(_LISTED.get(model))
    elif what == 'test':
        cfg = config(fid) if f.get('on') else None
        if cfg:
            _test(cfg, quiet=False)
        return False
    fresh = _data()                    # read again: a service added on the way is in it
    if what == 'mode':
        fresh['search_mode'] = 'always' if (fresh.get('search_mode') or 'auto') == 'auto' else 'auto'
    if fid == 'subtitles' and f.get('service') and f.get('model'):
        f.setdefault('models', {})[f['service']] = f['model']     # the player's list offers it again
    fresh['features'][fid] = f
    _save(fresh)
    return True


def subtitle_choices():
    """v5.10.590: the subtitle translation's services ready to use, for the player's list:
    Google Translate, DexWorld's translation with its code, and each AI service set up with
    its model (the one last used for subtitles, else for another feature, else its first
    suggested one). [(sid, name, model, note)]"""
    have = services()
    data = _data()
    f = feature('subtitles')
    used = {}
    for fid in ('search', 'lists', 'metadata'):
        g = data['features'].get(fid) or {}
        if g.get('service') and g.get('model'):
            used.setdefault(g['service'], g['model'])
    used.update(f.get('models') or {})
    if f.get('service') and f.get('model'):
        used[f['service']] = f['model']
    rows = [('google', 'Google Translate', '', _t('مجاني', 'free'))]
    if 'dexworld' in have:
        rows.append(('dexworld_tr', service_name('dexworld_tr'), '', ''))
    for pid, name, _b, _w, prefer in PROVIDERS:
        if pid not in have or (pid == 'dexworld' and pid not in used):
            continue
        model = used.get(pid) or (prefer[0] if prefer else '')
        if model:
            same = f.get('service') == pid and f.get('model') == model
            rows.append((pid, name, model, free_note(pid, model, bool(f.get('free')) and same)))
    # v5.10.590: while a Silo title plays, the server's own AI translation, under Google and AI
    try:
        from . import silo_subsync
        silo = silo_subsync.playing()
    except Exception:
        silo = False
    if silo or f.get('service') == 'silo':
        rows.append(('silo', 'Silo', '', _t('ترجمة AI على سيرفر Silo', "AI translation on Silo's server")))
    return rows


def use_subtitle(sid):
    """v5.10.590: the player's list chose a service: subtitle translation uses it from now on."""
    row = next((r for r in subtitle_choices() if r[0] == sid), None)
    if not row:
        return False
    data = _data()
    f = dict(data['features'].get('subtitles') or {})
    models = dict(f.get('models') or {})
    if f.get('service') and f.get('model'):
        models[f['service']] = f['model']
    was_free = bool(f.get('free')) and f.get('service') == sid and f.get('model') == row[2]
    f.update(on=True, service=sid, model=row[2], free=was_free or row[2].endswith(':free'), models=models)
    data['features']['subtitles'] = f
    try:
        _save(data)
    except Exception as exc:
        # not kept (a full disk on the Mac, 20 Nov): the translation asked for now still uses it
        _UNSAVED['subtitles'] = f
        _log('subtitle translation choice not saved (%s): %s used this time' % (exc, service_name(sid)),
             xbmc.LOGWARNING)
        return True
    _log('subtitle translation now uses %s%s' % (service_name(sid), (' · ' + row[2]) if row[2] else ''))
    return True


def _choose_service(fid, f):
    """The service for a feature (and then its model). A service with no key yet is set up here."""
    dialog = xbmcgui.Dialog()
    have = services()
    rows = []
    if fid in ('subtitles', 'metadata'):
        rows.append(('google', 'Google Translate'))
    for pid, name, _b, _w, _p in PROVIDERS:
        if pid in have:
            rows.append((pid, name))
    if fid == 'subtitles' and 'dexworld' in have:
        rows.append(('dexworld_tr', service_name('dexworld_tr')))
    rows.append(('add', _t('خدمة ثانية…', 'Another service…')))
    now = f.get('service')
    pick = dialog.select(_t('اختر الخدمة', 'Choose the service'), [r[1] for r in rows],
                         preselect=next((n for n, r in enumerate(rows) if r[0] == now), 0))
    if pick < 0:
        return False
    pid = rows[pick][0]
    if pid == 'add':
        pid = add_service()
        if not pid:
            return False
        if pid == 'dexworld' and fid == 'subtitles':
            pid = 'dexworld_tr'               # DexWorld's own subtitle translation, its code just added
    f['service'] = pid
    if _no_chat(pid):
        f['model'] = ''
        return True
    svc = services().get(pid)
    prov = _provider(pid)
    model = _choose_model(dict(svc, provider=pid, model=f.get('model') or ''), prov[4] if prov else ())
    if not model:
        return False
    f['model'], f['free'] = model, bool(_LISTED.get(model))
    return True


def edit_services():
    """The services set up: add one, change a key, or remove one."""
    dialog = xbmcgui.Dialog()
    have = services()
    rows = [(pid, '%s  ✓' % name) for pid, name, _b, _w, _p in PROVIDERS if pid in have]
    rows.append(('add', _t('إضافة خدمة…', 'Add a service…')))
    pick = dialog.select(_t('الخدمات والمفاتيح', 'Services and keys'), [r[1] for r in rows])
    if pick < 0:
        return False
    pid = rows[pick][0]
    if pid == 'add':
        return bool(add_service())
    act = dialog.select(service_name(pid), [_t('تغيير المفتاح', 'Change the key'), _t('حذف الخدمة', 'Remove the service')])
    if act == 0:
        return bool(add_service(pid))
    if act == 1 and dialog.yesno(service_name(pid), _t('أحذف هذي الخدمة ومفتاحها من الجهاز؟', 'Remove this service and its key from this device?')):
        data = _data()
        data['services'].pop(pid, None)
        for fid, f in data['features'].items():
            if f.get('service') == pid or (pid == 'dexworld' and f.get('service') == 'dexworld_tr'):
                data['features'][fid] = {'on': False} if fid != 'subtitles' else {'service': 'google'}
        _save(data)
        return True
    return False


def add_service(pid=None):
    """A service and its key (DexWorld: its code; any address: the address, the key if it asks
    one), checked with its model list. Returns its pid, or None."""
    dialog = xbmcgui.Dialog()
    if pid is None:
        names = [p[1] for p in PROVIDERS]
        pick = dialog.select(_t('اختر الخدمة', 'Choose the service'), names)
        if pick < 0:
            return None
        pid = PROVIDERS[pick][0]
    prov = _provider(pid)
    name, base, where = prov[1], prov[2], prov[3]
    old = _data()['services'].get(pid) or {}
    if pid == 'custom':
        # any address, http too (a service on the home network)
        base = (dialog.input(_t('العنوان (ينتهي غالباً بـ /v1)', 'Address (usually ends in /v1)'),
                             old.get('base') or 'https://') or '').strip().rstrip('/')
        if not base.startswith(('https://', 'http://')) or len(base) < 12:
            return None
        if base.endswith('/chat/completions'):
            base = base[:-len('/chat/completions')]
    if pid == 'dexworld':
        ask = _t('كود DexWorld', 'DexWorld code')
    elif where:
        ask = '%s  (%s)' % (_t('مفتاح %s', '%s key') % name, where)
    else:
        ask = _t('المفتاح (اتركه فاضي لو الخدمة ما تطلبه)', 'Key (leave it empty if the service needs none)')
    key = (dialog.input(ask, option=xbmcgui.ALPHANUM_HIDE_INPUT) or '').strip() or (old.get('key') or '')
    if not key and pid != 'custom':
        return None
    status, _answer = _post({'base': base, 'key': key, 'provider': pid}, '/models', None, timeout=15)
    if status == 0:
        dialog.ok(name, _t('ما وصلت للخدمة، تأكد من العنوان', "Couldn't reach the service; check the address"))
        return None
    if status not in (200, 404, 405):      # a service without a model list still answers 404 / 405
        dialog.ok(name, _t('المفتاح مرفوض', 'The key was refused') + ' (%s)' % status)
        return None
    data = _data()
    data['services'][pid] = {'key': key, 'base': base}
    _save(data)
    xbmcgui.Dialog().notification('Dex Hub', _t('انضافت %s ✓', '%s added ✓') % name, xbmcgui.NOTIFICATION_INFO, 3000)
    return pid


def _models(cfg):
    """{model id: free} the key can use (the service's /models), or {} when it does not list them.
    free: True when the service says the model costs nothing (OpenRouter's zero price or ":free")."""
    status, answer = _post(cfg, '/models', None, timeout=15)
    if status != 200 or not isinstance(answer, dict):
        return {}
    out = {}
    for m in answer.get('data') or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get('id') or '')
        if cfg.get('provider') == 'gemini':
            mid = mid.split('/')[-1]
        if not mid or mid in out:
            continue
        price = m.get('pricing') if isinstance(m.get('pricing'), dict) else {}
        zero = bool(price) and all(str(price.get(k, '0')).strip() in ('0', '0.0', '') for k in ('prompt', 'completion'))
        out[mid] = zero or mid.endswith(':free') or bool(m.get('free'))
    return out


_LISTED = {}      # the last model list read: {id: free}


def free_note(provider, model, listed_free=False):
    """v5.10.588: what a model costs, in a word for the lists: free, free up to a daily limit, or ''."""
    model = str(model or '').lower()
    if listed_free or model.endswith(':free'):
        return _t('مجاني', 'free')
    if provider == 'groq' or (provider == 'gemini' and 'flash' in model):
        return _t('مجاني بحد يومي', 'free up to a daily limit')
    return ''


def _choose_model(cfg, prefer):
    """v5.10.588: the viewer picks the model from the service's list (the suggested ones first,
    then the free ones), each marked free when it is, or types one; a service that lists none
    asks for its name."""
    dialog = xbmcgui.Dialog()
    have = _models(cfg)
    _LISTED.clear()
    _LISTED.update(have)
    rest = sorted((m for m in have if m not in prefer), key=lambda m: (not have[m], m))
    ordered = [m for m in prefer if not have or m in have] + rest
    if not ordered:
        return (dialog.input(_t('اسم النموذج', 'Model name'), cfg.get('model') or '') or '').strip()
    rows = []
    for m in ordered:
        bits = [x for x in (free_note(cfg.get('provider'), m, have.get(m)), _t('مقترح', 'suggested') if m in prefer else '') if x]
        rows.append('%s%s' % (m, ('   · ' + ' · '.join(bits)) if bits else ''))
    rows.append(_t('اكتب اسم نموذج ثاني…', 'Type another model…'))
    now = cfg.get('model') or ''
    pick = dialog.select(_t('اختر النموذج', 'Choose the model'), rows,
                         preselect=ordered.index(now) if now in ordered else 0)
    if pick < 0:
        return ''
    if pick == len(ordered):
        return (dialog.input(_t('اسم النموذج', 'Model name'), now) or '').strip()
    return ordered[pick]


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
