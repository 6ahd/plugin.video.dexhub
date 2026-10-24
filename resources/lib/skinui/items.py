# -*- coding: utf-8 -*-
"""Cached row items to Kodi ListItems (light: no router, no translations).

A cached item is a plain dict made by rows.py / title.py:

  label, label2, path, folder
  art      poster, thumb, landscape, fanart, clearlogo, icon
  info     title, mediatype, plot, year, genres, rating, duration, mpaa,
           tvshowtitle, season, episode, premiered
  ids      imdb, tmdb, tvdb
  progress percent played (0-100); watched: bool
  props    ListItem properties (kind, subtitle, dhs.click, dhs.target, ...)
  menu     [[label, builtin], ...] for Kodi's context menu
"""
import xbmcgui

_STR = (('title', 'setTitle'), ('plot', 'setPlot'), ('mpaa', 'setMpaa'), ('mediatype', 'setMediaType'),
        ('tvshowtitle', 'setTvShowTitle'), ('premiered', 'setPremiered'), ('tagline', 'setTagLine'))
_INT = (('year', 'setYear'), ('season', 'setSeason'), ('episode', 'setEpisode'), ('duration', 'setDuration'))


def _int(value):
    try:
        return int(float(value))
    except Exception:
        return None


def listitem(item, tab='', row=''):
    """A fresh ListItem for one cached item (``row``: '<tab>:<row id>', the
    skin's Home loads its rows one after another by it)."""
    li = xbmcgui.ListItem(label=item.get('label') or '', label2=item.get('label2') or '',
                          path=item.get('path') or '', offscreen=True)
    art = dict((k, v) for k, v in (item.get('art') or {}).items() if v)
    if (item.get('props') or {}).get('kind') == 'work':
        from ..home_art import display_art
        art = display_art(art)
        try:
            from ..art_choice import apply_item    # v5.10.322: Change artwork
            art = apply_item(item, art)
        except Exception:
            pass
    if str(item.get('path') or '').startswith('favourites://'):
        # a widget's click in its favourites form (widgetclick.py): Kodi must
        # not open that address as a video file for its details (it looked
        # its "host" up in DNS and tried to read it, for every card), nor
        # look for pictures beside it (a card without a thumb or a fanart)
        li.setMimeType('playlist')
        for key, sources in (('thumb', ('landscape', 'poster', 'fanart', 'icon')),
                             ('fanart', ('landscape', 'poster', 'thumb'))):
            if not art.get(key):
                found = next((art[s] for s in sources if art.get(s)), '')
                if found:
                    art[key] = found
    if art:
        li.setArt(art)
    info = item.get('info') or {}
    if not info and art and not str(item.get('path') or '').startswith('favourites://'):
        # v5.10.151: a card without facts (a folder, a catalog, See all) still
        # gets a video tag. Kodi's widget loader then takes its video loader,
        # which keeps the card's own art; the program loader it took before
        # queued a texture-cache job for every card at every refresh (a
        # database look-up each, and the path Kodi 22 beta1 crashed in).
        try:
            li.getVideoInfoTag().setMediaType('video')
        except Exception:
            pass
    if info:
        tag = li.getVideoInfoTag()
        for key, setter in _STR:
            value = info.get(key)
            if value not in (None, ''):
                try:
                    getattr(tag, setter)(str(value))
                except Exception:
                    pass
        for key, setter in _INT:
            value = _int(info.get(key))
            if value:
                try:
                    getattr(tag, setter)(value)
                except Exception:
                    pass
        genres = info.get('genres') or []
        if genres:
            try:
                tag.setGenres([str(g) for g in genres])
            except Exception:
                pass
        rating = info.get('rating')
        if rating:
            try:
                tag.setRating(float(rating), 0, '', True)
            except Exception:
                pass
        ids = dict((k, str(v)) for k, v in (item.get('ids') or {}).items() if v)
        if ids:
            try:
                tag.setUniqueIDs(ids, 'tmdb' if 'tmdb' in ids else next(iter(ids)))
            except Exception:
                pass
        progress = _int(item.get('progress'))
        if progress and 0 < progress < 100:
            try:
                tag.setResumePoint(float(progress), 100.0)
            except Exception:
                pass
        if item.get('watched'):
            try:
                tag.setPlaycount(1)
            except Exception:
                pass
    props = dict(item.get('props') or {})
    from .hero_text import cached_meta
    props['dhs.hero_meta'] = cached_meta(item)
    if props.get('kind') == 'work' and art.get('poster'):
        # Carry the already-rendered Home texture through Kodi's Info dialog.
        props['dhs.card.poster'] = art['poster']
    if tab:
        props['dhs.tab'] = tab
    if row:
        props['dhs.row'] = row
    for key, value in props.items():
        if value not in (None, ''):
            li.setProperty(key, str(value))
    menu = item.get('menu') or []
    try:
        from ..context_menu import with_player
        menu = with_player(menu, item.get('path') or '', info, item.get('ids'), props)
    except Exception:
        pass
    if menu:
        try:
            from ..context_menu import clean
            from ..i18n import tr
            li.addContextMenuItems([(tr(label), command) for label, command in clean([(str(m[0]), menu_command(m[1], row)) for m in menu if len(m) > 1])])
        except Exception:
            pass
    return li


def menu_command(command, row=''):
    """v5.10.249: a card's menu command as the skin's Home can run it.

    Kodi runs Container.Update only in a media window; on the Home (and its
    rows, grids and title pages) "Show sources", "Open seasons" and "Open
    details" did nothing. A route that plays (the source list) runs as a
    plugin call, a title's page opens as the skin's title page, and any
    other add-on folder opens in the Videos window."""
    import re
    from . import common as C
    text = str(command or '').strip()
    match = re.match(r'^Container\.Update\((.*?)(?:,\s*replace)?\)$', text, re.I)
    if not match:
        return text
    url = match.group(1).strip().strip('"')
    if not url.startswith(C.BASE):
        return text if row is None else 'ActivateWindow(Videos,%s,return)' % url
    try:
        from urllib.parse import parse_qsl, urlsplit
        from ..homeui import rows as R
        params = dict(parse_qsl(urlsplit(url).query))
        action = params.get('action', '')
        if action in R.PLAYER_ACTIONS:
            return 'RunPlugin(%s)' % url
        if action == 'filters_menu' and row is not None and title_here():
            # the catalog's filters as the skin shows them: its row's
            # choices on the Home, the grid's own filter on a grid page
            if row and ':' in str(row):
                tab, row_id = str(row).split(':', 1)
                return 'RunPlugin(%s)' % C.url('skin_row_facet', m=tab, id=row_id)
            return 'RunPlugin(%s)' % C.url('skin_facet', which='filter')
        if action in ('item_open', 'series_meta') and title_here():
            from .widgetclick import title_query
            query = title_query(params)
            if query:
                return 'RunPlugin(%s)' % C.url('skin_title', u=url, **dict(query))
    except Exception:
        pass
    if row is None:
        return text     # the add-on's own list in the Videos window
    return 'ActivateWindow(Videos,%s,return)' % url


# what a Kodi list does itself with a click (v5.10.114)
GO_CLICKS = ('info', 'run')
_FOLD = {'t': 0.0, 'ok': False}
_TITLE = {'t': 0.0, 'ok': False}


def _fold_page():
    """Arctic Fuse 3 has Dex Hub's folder page (asked once per listing)."""
    import time
    now = time.time()
    if now - _FOLD['t'] > 2.0:
        try:
            from . import common as C
            ok = False
            if C.served() == 'af3':
                from . import af3pages
                ok = af3pages.fold_ready()
        except Exception:
            ok = False
        _FOLD['t'], _FOLD['ok'] = now, ok
    return _FOLD['ok']


def title_here():
    """Dex Hub's title page is the skin's own information dialog
    (skin.dexhub, or Arctic Fuse 3 with Dex Hub's pages)."""
    import time
    now = time.time()
    if now - _TITLE['t'] > 2.0:
        ok = False
        try:
            from . import common as C
            served = C.served()
            if served == 'dexhub':
                ok = True
            elif served == 'af3':
                from . import af3pages
                ok = af3pages.title_ready()
        except Exception:
            ok = False
        _TITLE['t'], _TITLE['ok'] = now, ok
    return _TITLE['ok']


def title_target(q):
    """The add-on address that opens a title's page (``q``: its dhs.q):
    the skin's page where it has Dex Hub's, else the add-on's own over it."""
    from urllib.parse import parse_qsl
    from . import common as C
    query = dict(parse_qsl(q)) if isinstance(q, str) else dict(q or {})
    if not query:
        return ''
    return C.url('skin_open' if title_here() else 'home_details', **query)


def kodi_click(item, widget=False):
    """The item as a Kodi list (the Videos window, Arctic Fuse 3's widgets,
    the OSD's panels) must hold it: a click there is Kodi's own, not the
    skin's onclick. Kodi 22 opens a file item of a widget with PlayMedia,
    a resolve that Dex Hub's title page and player hand-off never answered
    (the user saw "is not playable", and the video it had started stopped);
    the Videos window runs it as a script. A Dex Hub title or play route
    becomes skin_go, which answers a waiting resolve at once and then runs
    the click. (A folder that fails to list was tried: from a widget Kodi
    then showed the Videos window's root.)

    widget (v5.10.120): a widget of Arctic Fuse 3, whose OK runs the
    favourites form of a path as a builtin (widgetclick.py): the click goes
    straight to the title page or the play route, with no resolve to fail
    (that stopped the video playing behind the Home) and one add-on call."""
    from . import common as C
    props = item.get('props') or {}
    click = props.get('dhs.click') or ''
    # Series/movie widgets use the live preference, even when their row is cached.
    if widget and props.get('dhs.q') and (props.get('dhs.open_title') == '1' or click == 'info'):
        from . import widgetclick as W
        from urllib.parse import parse_qsl
        q = dict(parse_qsl(props['dhs.q']))
        target = C.url('skin_title', u=props.get('dhs.raw') or '', **q)
        out = dict(item)
        out.update(path=W.run_path(target), folder=False)
        return out
    if click == 'grid' and props.get('dhs.fold') and _fold_page():
        # v5.10.119: Arctic Fuse 3 with Dex Hub's pages opens a collection
        # folder as its page of rows (folderpage.py), as skin.dexhub does
        out = dict(item)
        if widget:
            from . import widgetclick as W
            out['path'] = W.run_path(props['dhs.fold'])
        else:
            out['path'] = C.url('skin_go', do='run', u=props['dhs.fold'])
        out['folder'] = False
        return out
    if click not in GO_CLICKS:
        return item
    out = dict(item)
    if click == 'info':
        if widget:
            from . import widgetclick as W
            target = title_target(props.get('dhs.q') or '')
            out['path'] = W.run_path(target) if target else C.url(
                'skin_go', do='info', q=props.get('dhs.q') or '', u=item.get('path') or '')
        else:
            out['path'] = C.url('skin_go', do='info', q=props.get('dhs.q') or '', u=item.get('path') or '')
    else:
        target = props.get('dhs.target') or item.get('path') or ''
        if not target:
            return item
        if widget:
            from . import widgetclick as W
            out['path'] = W.run_path(target)
        else:
            out['path'] = C.url('skin_go', do='run', u=target)
    out['folder'] = False
    return out


# v5.10.132: the service works grids out for the plugin call (listing.py):
# on its thread the listing is kept instead of handed to Kodi
CAPTURE_HANDLE = -7


class _Box(object):
    def __enter__(self):
        import threading
        global _LOCAL
        if _LOCAL is None:
            _LOCAL = threading.local()
        self.previous = getattr(_LOCAL, 'box', None)
        _LOCAL.box = {}
        return _LOCAL.box

    def __exit__(self, exc_type, exc, tb):
        _LOCAL.box = self.previous
        return False


_LOCAL = None


def capture():
    """``with capture() as box``: this thread's listing goes into ``box``
    (items, kw, done; failed when the route ended in an error)."""
    return _Box()


def captured():
    """This thread's capture box, or None."""
    return getattr(_LOCAL, 'box', None) if _LOCAL is not None else None


def directory(handle, items, tab='', content='videos', props=None, row='', kodi_clicks=False, cache_hash=''):
    """Hand ``items`` to Kodi as this invocation's listing (the last thing a
    call does: the interpreter is free again the moment Kodi has the list).
    kodi_clicks: a list Kodi itself clicks (kodi_click); 'widget': a widget
    of the skin, clicked through the favourites form in Arctic Fuse 3."""
    box = captured()
    if box is not None:
        box.update(done=True, items=list(items or []),
                   kw={'tab': tab, 'content': content, 'props': dict(props or {}), 'row': row,
                       'kodi_clicks': kodi_clicks, 'cache_hash': cache_hash})
        return len(box['items'])
    import xbmcplugin
    widget = False
    if kodi_clicks == 'widget':
        from . import widgetclick as W
        widget = W.af3_widgets() and W.in_widget()
    entries = []
    for item in items or []:
        try:
            if kodi_clicks:
                item = kodi_click(item, widget=widget)
            li = listitem(item, tab, row)
            if cache_hash:
                li.setProperty('dhs.cache.h', cache_hash)
            entries.append((item.get('path') or '', li, bool(item.get('folder'))))
        except Exception:
            continue
    try:
        xbmcplugin.setContent(handle, content)
    except Exception:
        pass
    for key, value in (props or {}).items():
        try:
            xbmcplugin.setProperty(handle, key, str(value or ''))
        except Exception:
            pass
    if entries:
        xbmcplugin.addDirectoryItems(handle, entries, len(entries))
    xbmcplugin.endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=False)
    return len(entries)
