# -*- coding: utf-8 -*-
"""A title's own backdrop and logo, chosen on its page (v5.10.322), as Nuvio
Legacy's Change artwork: TMDb's backdrops and logos of the title, by
language (the interface's, English, without text, or all).

Kept in the profile (art_choice.json) by TMDb and IMDb id; every card of the
title (items.listitem) and its page take them. Restore originals forgets
the title's choice.
"""
import json
import os
import threading

import xbmc

_LOCK = threading.Lock()
_MEMO = {'mtime': -1.0, 'data': {}}
_FILE = 'art_choice.json'


_PATH = []


def _path():
    # v5.10.323: worked out once a process (every card asks; an Addon()
    # costs far more than the file's stat)
    if not _PATH:
        try:
            import xbmcaddon
            import xbmcvfs
            folder = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
        except Exception:
            return ''
        _PATH.append(os.path.join(folder, _FILE))
    return _PATH[0]


def _load():
    path = _path()
    if not path:
        return {}
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _MEMO.update(mtime=-1.0, data={})
        return {}
    with _LOCK:
        if mtime != _MEMO['mtime']:
            try:
                with open(path, encoding='utf-8') as handle:
                    data = json.load(handle)
                _MEMO.update(mtime=mtime, data=data if isinstance(data, dict) else {})
            except Exception:
                _MEMO.update(mtime=mtime, data={})
        return _MEMO['data']


def _save(data):
    path = _path()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)
    except Exception as exc:
        xbmc.log('[DexHub] artwork choice not saved: %s' % exc, xbmc.LOGWARNING)


def _keys(tmdb='', imdb='', media=''):
    kind = 'tv' if str(media or '') in ('series', 'tv', 'tvshow', 'show', 'anime', 'episode', 'season') else 'movie'
    out = []
    if str(tmdb or '').isdigit():
        out.append('%s:tmdb:%s' % (kind, tmdb))
    imdb = str(imdb or '').split(':')[0]
    if imdb.startswith('tt'):
        out.append('imdb:%s' % imdb)
    return out


def chosen(tmdb='', imdb='', media=''):
    """{'fanart': url, 'clearlogo': url} the user chose for a title, or {}."""
    data = _load()
    if not data:
        return {}
    for key in _keys(tmdb, imdb, media):
        if data.get(key):
            return dict(data[key])
    return {}


def apply_item(item, art):
    """A card's art with the title's choice (items.listitem)."""
    if not _load():
        return art
    ids = item.get('ids') or {}
    info = item.get('info') or {}
    media = info.get('mediatype') or ''
    if media == 'episode':
        return art          # an episode keeps its still
    choice = chosen(ids.get('tmdb') or '', ids.get('imdb') or '', media)
    if not choice:
        return art
    art = dict(art)
    if choice.get('fanart'):
        art['fanart'] = choice['fanart']
        if art.get('landscape') and media in ('movie', 'tvshow'):
            art['landscape'] = choice['fanart']
    if choice.get('clearlogo'):
        art['clearlogo'] = choice['clearlogo']
    return art


def remember(tmdb='', imdb='', media='', fanart=None, clearlogo=None, forget=False):
    keys = _keys(tmdb, imdb, media)
    if not keys:
        return False
    data = dict(_load())
    entry = {}
    for key in keys:
        entry.update(data.get(key) or {})
    if forget:
        for key in keys:
            data.pop(key, None)
    else:
        if fanart is not None:
            entry['fanart'] = fanart
        if clearlogo is not None:
            entry['clearlogo'] = clearlogo
        entry = dict((k, v) for k, v in entry.items() if v)
        for key in keys:
            if entry:
                data[key] = entry
            else:
                data.pop(key, None)
    _save(data)
    _MEMO['mtime'] = -1.0
    return True


# ---------------------------------------------------------------- the picker

def _images(tmdb, media):
    from . import tmdb_direct as T
    kind = 'tv' if media in ('series', 'tv', 'tvshow') else 'movie'
    key = 'dh-images-v1:%s:%s' % (kind, tmdb)
    data = T._cache_get(key)
    if data is None:
        data = T._request('/%s/%s/images' % (kind, tmdb), {}, timeout=8, max_attempts=2) or {}
        T._cache_set(key, data, 7 * 86400 if data else 300)
    return data if isinstance(data, dict) else {}


def _lang_name(code, tr):
    if not code:
        return tr('بدون نص')
    names = {'ar': 'العربية', 'en': 'English', 'fr': 'Français', 'es': 'Español', 'de': 'Deutsch',
             'it': 'Italiano', 'ja': '日本語', 'ko': '한국어', 'zh': '中文', 'pt': 'Português', 'ru': 'Русский',
             'tr': 'Türkçe', 'hi': 'हिन्दी'}
    return names.get(code, code.upper())


def pick(tmdb, imdb, media, title, tr, current=None):
    """Nuvio's Change artwork: Backdrops or Logos, a language, then the
    picture. True when the choice changed."""
    from .homeui.options import choose
    if not str(tmdb or '').isdigit():
        xbmc.executebuiltin('Notification(Dex Hub,%s,3000)' % tr('لا توجد صور لهذا العمل في TMDb'))
        return False
    current = current or {}
    mine = chosen(tmdb, imdb, media)
    while True:
        menu = [(tr('الخلفيات'), 'current' if mine.get('fanart') else ''),
                (tr('الشعارات'), 'current' if mine.get('clearlogo') else '')]
        if mine:
            menu.append((tr('استرجاع الصور الأصلية'), ''))
        what = choose(title, menu, subtitle=tr('تغيير الصور'))
        if what < 0:
            return False
        if what == 2:
            remember(tmdb, imdb, media, forget=True)
            return True
        field, group = (('fanart', 'backdrops') if what == 0 else ('clearlogo', 'logos'))
        xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
        try:
            rows = list((_images(tmdb, media).get(group) or []))
        finally:
            xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
        if not rows:
            xbmc.executebuiltin('Notification(Dex Hub,%s,3000)' % tr('لا توجد صور'))
            continue
        # the language filter, as Nuvio: the languages TMDb has, most first
        counts = {}
        for row in rows:
            code = str(row.get('iso_639_1') or '')
            counts[code] = counts.get(code, 0) + 1
        try:
            from .i18n import is_english
            mine_lang = 'en' if is_english() else 'ar'
        except Exception:
            mine_lang = 'ar'
        order = sorted(counts, key=lambda c: (c != mine_lang, c != 'en', c != '', -counts[c]))
        langs = [('', tr('الكل'), len(rows))] + [(c, _lang_name(c, tr), counts[c]) for c in order]
        lang_pick = 0
        if len(langs) > 2:
            lang_pick = choose(tr('اللغة'), ['%s  (%d)' % (name, n) for _c, name, n in langs],
                               subtitle=title)
            if lang_pick < 0:
                continue
        want = langs[lang_pick][0] if lang_pick > 0 else None
        shown = [r for r in rows if want is None or str(r.get('iso_639_1') or '') == want]
        shown.sort(key=lambda r: (-(float(r.get('vote_average') or 0)), -(int(r.get('width') or 0))))
        shown = shown[:80]
        size = 'w780' if field == 'fanart' else 'w500'
        have = mine.get(field) or current.get(field) or ''
        options = []
        for row in shown:
            path = str(row.get('file_path') or '')
            if path.lower().endswith('.svg'):
                path = path[:-4] + '.png'
            thumb = 'https://image.tmdb.org/t/p/%s%s' % (size, path)
            full = 'https://image.tmdb.org/t/p/%s%s' % ('w1280' if field == 'fanart' else 'w500', path)
            line = '%s  ·  %dx%d' % (_lang_name(str(row.get('iso_639_1') or ''), tr),
                                     int(row.get('width') or 0), int(row.get('height') or 0))
            options.append((full, line, thumb, path))
        labels = [(o[1], 'current' if have and have.endswith(o[3]) else '', o[2], '') for o in options]
        chosen_index = choose(tr('الخلفيات') if field == 'fanart' else tr('الشعارات'), labels,
                              subtitle=title, layout='gallery')
        if chosen_index < 0:
            continue
        remember(tmdb, imdb, media, **{field: options[chosen_index][0]})
        return True
