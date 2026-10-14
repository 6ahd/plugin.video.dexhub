# -*- coding: utf-8 -*-
"""Replay Home widget listings without loading the full router.

v5.10.84. On startup Arctic Fuse fired about fifty Dex Hub invocations in six
seconds (Nuvio collections and TMDb, Trakt and Kitsu catalogs), and every one
went through bootstrap into the 26,000-line router just to rebuild a listing
that had not changed since the last time. The first full render of a stable
widget listing is now recorded item by item, exactly as ui_core.add_item left
it, and the next time a widget asks for the same URL bootstrap replays it from
disk before any router module is imported.

Only stable listings are cached: personal and fast-moving ones (recent,
favourites, watchlist, continue, progress, history, next up, calendar) are
never replayed. Replay happens only outside the Videos window, so a folder the
user opens on purpose is always rendered fresh, and that fresh render also
refreshes the recording. Entries expire after widget_cache_hours (default 6,
0 disables), are dropped on any settings change, and are ignored after an
addon update.

v5.10.104: when the Dex Hub Home closes, the skin reloads every Dex Hub
widget at once (continue, next up, the Emby, Plex and Silo menus, the
providers), each in an interpreter of its own, and one of them hung for 80
seconds before Kodi killed it (a crash). Menus a skin shows as widgets are
now replayed too (for 30 minutes; the providers menu until providers.json
changes), and continue and next up answer a burst of reloads from a
recording under two minutes old, unless playback or another Dex Hub action
has changed them since (mark_changed).
"""
import hashlib
import json
import os
import sys
import time
from urllib.parse import parse_qsl

ACTIONS = frozenset(('catalog_all', 'collection_entry_open'))
MENUS = frozenset(('emby_menu', 'plex_servers', 'plex_menu', 'silo_menu', 'providers'))
BURST = frozenset(('continue', 'nextup', 'recently_watched'))
_MENU_TTL = 30 * 60.0
_BURST_TTL = 120.0
_CHANGED_PROP = 'dexhub.widgets.changed_at'
_VOLATILE = ('recent', 'fav', 'watchlist', 'continue', 'progress', 'history',
             'nextup', 'next_up', 'next-up', 'upnext', 'calendar', 'resume',
             'inprogress', 'in_progress', 'watching', 'mylist', 'my_list',
             'my-list', 'ondeck', 'on_deck', 'unwatched')
_BROWSE_WINDOWS = (10025,)   # Videos: a folder opened on purpose renders fresh
_REC = {'on': False, 'key': '', 'items': []}


def _params():
    try:
        return dict(parse_qsl(sys.argv[2].lstrip('?'))) if len(sys.argv) > 2 else {}
    except Exception:
        return {}


def cacheable(params):
    action = params.get('action')
    if action in MENUS or action in BURST:
        return True
    if action not in ACTIONS:
        return False
    hay = ' '.join(str(params.get(k) or '') for k in
                   ('catalog_id', 'label', 'set_id', 'entry_id', 'title', 'media_filter')).lower()
    return not any(token in hay for token in _VOLATILE)


def _addon():
    import xbmcaddon
    return xbmcaddon.Addon()


def _ttl_seconds(addon):
    try:
        hours = float(addon.getSetting('widget_cache_hours') or 6)
    except Exception:
        hours = 6.0
    return max(0.0, hours) * 3600.0


def _ttl_for(action, addon):
    ttl = _ttl_seconds(addon)
    if ttl <= 0:
        return 0.0
    if action in MENUS:
        return min(ttl, _MENU_TTL)
    if action in BURST:
        return min(ttl, _BURST_TTL)
    return ttl


def mark_changed():
    """Continue and next up changed (playback, a watched mark): no burst replay."""
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(_CHANGED_PROP, '%.3f' % time.time())
    except Exception:
        pass


def _changed_since(moment):
    try:
        import xbmcgui
        return float(xbmcgui.Window(10000).getProperty(_CHANGED_PROP) or 0) >= float(moment or 0)
    except Exception:
        return True


def _providers_changed(addon, moment):
    try:
        import xbmcvfs
        path = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo('profile')), 'providers.json')
        return os.path.getmtime(path) >= float(moment or 0)
    except Exception:
        return False


def _folder(addon):
    import xbmcvfs
    path = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo('profile')), 'widget_cache')
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _key():
    raw = '%s?%s' % (sys.argv[0] if sys.argv else '',
                     sys.argv[2].lstrip('?') if len(sys.argv) > 2 else '')
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _widget_context():
    try:
        import xbmcgui
        return int(xbmcgui.getCurrentWindowId()) not in _BROWSE_WINDOWS
    except Exception:
        return False


_STR = (('title', 'setTitle'), ('originaltitle', 'setOriginalTitle'), ('plot', 'setPlot'),
        ('plotoutline', 'setPlotOutline'), ('tagline', 'setTagLine'), ('mpaa', 'setMpaa'),
        ('mediatype', 'setMediaType'), ('tvshowtitle', 'setTvShowTitle'),
        ('trailer', 'setTrailer'), ('premiered', 'setPremiered'), ('dateadded', 'setDateAdded'),
        ('sorttitle', 'setSortTitle'), ('status', 'setTvShowStatus'),
        ('imdbnumber', 'setIMDBNumber'))
_INT = (('year', 'setYear'), ('season', 'setSeason'), ('episode', 'setEpisode'),
        ('playcount', 'setPlaycount'), ('duration', 'setDuration'), ('top250', 'setTop250'))
_LIST = (('genre', 'setGenres'), ('studio', 'setStudios'), ('country', 'setCountries'),
         ('director', 'setDirectors'), ('writer', 'setWriters'), ('tag', 'setTags'))


def _as_list(value):
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, '')]
    return [v.strip() for v in str(value).split('/') if v.strip()] if value else []


def _apply_info(item, info, ids, cast):
    if not hasattr(item, 'getVideoInfoTag'):
        try:
            item.setInfo('video', info)
        except Exception:
            pass
        return
    tag = item.getVideoInfoTag()
    for key, setter in _STR:
        if info.get(key) not in (None, ''):
            try:
                getattr(tag, setter)(str(info[key]))
            except Exception:
                pass
    for key, setter in _INT:
        if info.get(key) not in (None, ''):
            try:
                getattr(tag, setter)(int(float(info[key])))
            except Exception:
                pass
    for key, setter in _LIST:
        values = _as_list(info.get(key))
        if values:
            try:
                getattr(tag, setter)(values)
            except Exception:
                pass
    if info.get('rating') not in (None, ''):
        try:
            tag.setRating(float(info['rating']), int(float(info.get('votes') or 0)), '', True)
        except Exception:
            pass
    unique = {}
    for name in ('tmdb', 'imdb', 'tvdb'):
        value = str((ids or {}).get(name + '_id') or '')
        if value:
            unique[name] = value
    if unique:
        try:
            tag.setUniqueIDs(unique, 'tmdb' if 'tmdb' in unique else next(iter(unique)))
        except Exception:
            pass
    if cast:
        try:
            import xbmc
            actors = []
            for i, member in enumerate(cast):
                if isinstance(member, dict):
                    actors.append(xbmc.Actor(str(member.get('name') or ''), str(member.get('role') or ''),
                                             int(member.get('order') or i), str(member.get('thumbnail') or '')))
                elif member:
                    actors.append(xbmc.Actor(str(member), '', i, ''))
            if actors:
                tag.setCast(actors)
        except Exception:
            pass


def _listitem(entry):
    import xbmcgui
    try:
        item = xbmcgui.ListItem(label=entry.get('label') or '', label2=entry.get('label2') or '',
                                offscreen=True)
    except TypeError:
        item = xbmcgui.ListItem(label=entry.get('label') or '', label2=entry.get('label2') or '')
    if entry.get('info'):
        _apply_info(item, entry['info'], entry.get('ids') or {}, entry.get('cast'))
    art = entry.get('art') or {}
    if art:
        try:
            item.setArt(art)
        except Exception:
            pass
    props = entry.get('props') or {}
    if props:
        try:
            item.setProperties(props)
        except Exception:
            pass
    menu = entry.get('menu') or []
    if menu:
        try:
            item.addContextMenuItems([tuple(m) for m in menu], replaceItems=True)
        except Exception:
            pass
    return item


def try_replay():
    """Serve this invocation from the recording. True when it did."""
    params = _params()
    if not cacheable(params) or not _widget_context():
        return False
    addon = _addon()
    action = params.get('action')
    ttl = _ttl_for(action, addon)
    if ttl <= 0:
        return False
    started = time.monotonic()
    try:
        with open(os.path.join(_folder(addon), _key() + '.json'), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except Exception:
        return False
    if str(data.get('version') or '') != str(addon.getAddonInfo('version') or ''):
        return False
    age = time.time() - float(data.get('at') or 0)
    items = data.get('items') or []
    if age > ttl or not items:
        return False
    if action in BURST and _changed_since(data.get('at')):
        return False
    if action == 'providers' and _providers_changed(addon, data.get('at')):
        return False
    import xbmcplugin
    handle = int(sys.argv[1])
    rows = [(e.get('path') or '', _listitem(e), bool(e.get('folder'))) for e in items]
    # v5.10.120: series open the title page in Arctic Fuse 3 (skinui/widgetclick.py)
    try:
        from .skinui import widgetclick as _widgetclick
        rows = _widgetclick.adapt(rows)
    except Exception:
        pass
    if data.get('content'):
        xbmcplugin.setContent(handle, data['content'])
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)
    xbmcplugin.addDirectoryItems(handle, rows, len(rows))
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)
    try:
        import xbmc
        xbmc.log('[DexHub] widget replay: %s %d items age=%dmin in %.0fms (router not loaded)' % (
            params.get('action'), len(rows), age // 60, (time.monotonic() - started) * 1000), xbmc.LOGINFO)
    except Exception:
        pass
    return True


def begin():
    if cacheable(_params()):
        # stamped when the render starts: a change marked while it ran
        # (mark_changed) is newer than the data it read
        _REC.update(on=True, key=_key(), items=[], at=time.time())


def recording():
    return bool(_REC['on'])


def record(path, is_folder, label, label2, info, art, ids, cast, props, menu):
    if not _REC['on']:
        return
    _REC['items'].append({'path': path, 'folder': bool(is_folder), 'label': label,
                          'label2': label2 or '', 'info': info or {}, 'art': art or {},
                          'ids': ids or {}, 'cast': cast or None, 'props': props or {},
                          'menu': [list(m) for m in (menu or [])]})


def finish(content):
    if not _REC['on']:
        return
    _REC['on'] = False
    items, _REC['items'] = _REC['items'], []
    if not items:
        return      # an empty listing is usually a failed fetch: never cache it
    try:
        addon = _addon()
        path = os.path.join(_folder(addon), _REC['key'] + '.json')
        tmp = path + '.tmp'
        text = json.dumps({'version': addon.getAddonInfo('version'), 'at': _REC.get('at') or time.time(),
                           'content': content or '', 'items': items}, ensure_ascii=False, default=str,
                          separators=(',', ':'))
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        pass


def clear():
    try:
        folder = _folder(_addon())
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except Exception:
                pass
    except Exception:
        pass
