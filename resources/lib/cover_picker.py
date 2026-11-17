# -*- coding: utf-8 -*-
"""v5.10.426: choose a cover on the TV (Collections+ artwork).

The folder view's cards have "Choose a cover" in their menu: the covers of
Collections+ (collections.wixct.cc, used with its maker's permission) whose
names match the folder or catalog come first, then a search and the
groups. The list comes through DexCloud's mirror when the TV is linked (the
pictures stay when the site is away), else from the site itself.
"""
import re
import time
import unicodedata

import xbmc
import xbmcgui

SITE = 'https://collections.wixct.cc'
TTL = 24 * 3600
_memo = {'t': 0, 'data': None}


def _tr(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def catalogue():
    """{'base': picture base url, 'presets': [...], 'ar': {english: arabic}}."""
    if _memo['data'] and time.time() - _memo['t'] < TTL:
        return _memo['data']
    from .dexhub.client import get_json
    data = None
    try:
        from . import hub_client
        if hub_client.linked():
            data = get_json(hub_client.base() + '/api/covers/presets', ttl_seconds=TTL, retry=False)
    except Exception:
        data = None
    if not isinstance(data, dict) or not data.get('presets'):
        raw = get_json(SITE + '/api/presets', ttl_seconds=TTL, retry=False) or {}
        data = {'base': SITE, 'presets': raw.get('presets') or [], 'ar': raw.get('arNames') or {},
                'groups': raw.get('groups') or []}
    _memo.update(t=time.time(), data=data)
    return data


# ------------------------------------------------------------ matching
_DROP = {'the', 'movies', 'movie', 'films', 'film', 'series', 'shows', 'show', 'tv', 'collection', 'all',
         'افلام', 'أفلام', 'مسلسلات', 'مسلسل', 'فيلم', 'كوليكشن', 'الكل'}


def norm(text):
    text = unicodedata.normalize('NFKC', str(text or '')).lower()
    text = text.replace('&', ' and ').replace('+', ' plus ')
    text = re.sub(r'[ً-ْـ]', '', text)            # Arabic marks and tatweel
    text = text.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا').replace('ة', 'ه').replace('ى', 'ي')
    return ' '.join(re.findall(r'[\w]+', text))


def _tokens(text):
    return set(t for t in norm(text).split() if t not in _DROP)


def score(name, preset, arabic=None):
    """How well ``name`` names ``preset`` (0-100)."""
    best = 0
    for label in (preset.get('name'), (arabic or {}).get(preset.get('name') or ''), preset.get('slug', '').replace('-', ' ')):
        if not label:
            continue
        a, b = norm(name), norm(label)
        if not a or not b:
            continue
        if a == b:
            return 100
        ta, tb = _tokens(name), _tokens(label)
        if ta and ta == tb:
            best = max(best, 95)
        elif ta and tb and (ta <= tb or tb <= ta):
            best = max(best, 75)
        elif ta and tb:
            jac = len(ta & tb) / float(len(ta | tb))
            best = max(best, int(60 * jac))
    return best


def matches(name, presets, arabic=None, limit=8):
    ranked = [(score(name, p, arabic), p) for p in presets if p.get('covers')]
    ranked = [r for r in ranked if r[0] >= 40]
    ranked.sort(key=lambda r: -r[0])
    return [p for _s, p in ranked[:limit]]


def _url(base, cover):
    return '%s/%s' % (base.rstrip('/'), str(cover.get('file') or '').lstrip('/'))


def _label(preset, arabic):
    name = preset.get('name') or ''
    ar = (arabic or {}).get(name)
    return ('%s  ·  %s' % (ar, name)) if ar and _tr(True, False) else name


# ------------------------------------------------------------ the dialog
def pick(params, handle=-1):
    """params: kind (folder|catalog), key, name. Saves the chosen cover."""
    from . import cover_overrides as co
    kind = 'catalog' if params.get('kind') == 'catalog' else 'folder'
    key = params.get('key') or ''
    name = params.get('name') or ''
    if not key:
        return None
    dialog = xbmcgui.Dialog()
    try:
        data = catalogue()
    except Exception as exc:
        xbmc.log('[DexHub] covers not read: %s' % exc, xbmc.LOGWARNING)
        dialog.notification('Dex Hub', _tr('تعذر تحميل الأغلفة', 'Covers could not be loaded'), xbmcgui.NOTIFICATION_WARNING)
        return None
    presets, arabic, base = data.get('presets') or [], data.get('ar') or {}, data.get('base') or SITE
    chosen = key in (co.load().get('catalogs' if kind == 'catalog' else 'folders') or {})
    shown = matches(name, presets, arabic)
    while True:
        entries = []
        for preset in shown:
            li = xbmcgui.ListItem(_label(preset, arabic), '%d %s' % (len(preset['covers']), _tr('غلاف', 'covers')))
            li.setArt({'thumb': _url(base, preset['covers'][0]), 'icon': _url(base, preset['covers'][0])})
            entries.append(('preset', preset, li))
        for action, text in (('search', _tr('بحث…', 'Search…')), ('groups', _tr('تصفح المجموعات', 'Browse groups'))):
            entries.append((action, None, xbmcgui.ListItem('[B]%s[/B]' % text)))
        if chosen:
            entries.append(('clear', None, xbmcgui.ListItem(_tr('[B]رجّع الغلاف الأصلي[/B]', '[B]Restore the original cover[/B]'))))
        heading = _tr('غلاف «%s»', 'Cover for "%s"') % name
        index = dialog.select(heading, [e[2] for e in entries], useDetails=True)
        if index < 0:
            return None
        action, preset, _li = entries[index]
        if action == 'search':
            text = dialog.input(_tr('ابحث في الأغلفة', 'Search covers'), name)
            if text:
                shown = matches(text, presets, arabic, limit=30) or shown
            continue
        if action == 'groups':
            groups = data.get('groups') or sorted(set(p.get('group') for p in presets if p.get('group')))
            g = dialog.select(_tr('المجموعات', 'Groups'), [str(x) for x in groups])
            if g >= 0:
                shown = [p for p in presets if p.get('group') == groups[g] and p.get('covers')]
            continue
        if action == 'clear':
            co.apply_changes(clear=[{'key': key, 'kind': kind}])
            _done()
            return None
        cover = _choose_cover(dialog, preset, base, arabic)
        if cover:
            item = {'key': key, 'kind': kind, 'preset': preset.get('key') or ''}
            item['poster' if cover.get('shape') == 'poster' else 'landscape'] = _url(base, cover)
            if cover.get('shape') != 'poster':
                poster = next((c for c in preset['covers'] if c.get('shape') == 'poster'), None)
                if poster:
                    item['poster'] = _url(base, poster)
            co.apply_changes(items=[item])
            _done()
            return None


def _choose_cover(dialog, preset, base, arabic):
    covers = sorted(preset.get('covers') or [], key=lambda c: {'landscape': 0, 'square': 1, 'poster': 2}.get(c.get('shape'), 3))
    if len(covers) == 1:
        return covers[0]
    shapes = {'landscape': _tr('عريض', 'Wide'), 'square': _tr('مربع', 'Square'), 'poster': _tr('بوستر', 'Poster')}
    items = []
    for n, cover in enumerate(covers):
        li = xbmcgui.ListItem('%s %d' % (shapes.get(cover.get('shape'), ''), n + 1), _label(preset, arabic))
        li.setArt({'thumb': _url(base, cover), 'icon': _url(base, cover)})
        items.append(li)
    index = dialog.select(_label(preset, arabic), items, useDetails=True)
    return covers[index] if index >= 0 else None


def _done():
    try:
        from .homeui import community
        community.reset()
    except Exception:
        pass
    try:
        from . import cloud_look
        cloud_look.mark()
    except Exception:
        pass
    try:
        from .folder_view import publish_cards
        from .skinui import common as C
        data = C.read_json(C.spec_file('fold'), {}) or {}
        if data.get('ref'):
            publish_cards(data.get('title') or '', data['ref'], stamp='%d' % int(time.time()))
        C.ask_republish('collections')
    except Exception:
        pass
    xbmcgui.Dialog().notification('Dex Hub', _tr('تم تغيير الغلاف', 'Cover changed'), xbmcgui.NOTIFICATION_INFO, 2500)
