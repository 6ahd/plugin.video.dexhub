# -*- coding: utf-8 -*-
"""OK on a Dex Hub item in a widget of the skin (v5.10.120).

A widget's list is a Kodi directory, and Kodi clicks it itself: a folder
opens in the Videos window (the add-on's own listing), a file is played
with PlayMedia. Two things went wrong with that in Arctic Fuse 3:

* a series from one of Dex Hub's catalogs, made a widget in the skin,
  opened the add-on's seasons listing instead of a page in the skin;
* Dex Hub's own rows answered PlayMedia's resolve with a failure to open
  their title page (items.kodi_click, v5.10.114): Kodi logged "Error
  resolving item ... skipping unplayable item" at every click, stopped
  what was playing (the trailer behind the Home, or the user's video in
  the background), and every click ran two add-on calls at once.

Kodi runs the item of a widget whose path is in its favourites form,
``favourites://<a builtin, url-encoded>``, as that builtin (Kodi 21 and
22, CDirectoryProvider::OnClick and CExecString): RunPlugin straight to
the title page, no resolve, nothing stopped, one call. The skin's own
lists with click actions (skin.dexhub, the folder page) never get these
paths: their Info action needs the item itself.
"""
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import xbmc

FAVOURITES = 'favourites://'
# set on an item with such a path: Kodi takes it for a playlist, which it
# never opens for stream details or a thumbnail (CDVDFileInfo::CanExtract);
# without it Kodi looked the path's "host" up in DNS and tried to read it
MIME = 'playlist'
VIDEOS = 10025              # the Videos window: a folder opened on purpose
ADDON_WINDOWS = 13000       # the add-on's own windows (Python WindowXML)
SERIES = ('series', 'anime', 'show', 'tv', 'tvshow')
_BASE = 'plugin://plugin.video.dexhub/'


def builtin_path(builtin):
    """A list item path that a widget's OK runs as ``builtin``."""
    return FAVOURITES + quote(str(builtin), safe='')


def run_path(target):
    """OK runs ``target``: a plugin address as RunPlugin, anything else is played."""
    target = str(target or '').replace('"', '%22')
    if target.startswith('plugin://'):
        return builtin_path('RunPlugin("%s")' % target)
    return builtin_path('PlayMedia("%s")' % target)



def in_widget():
    """This listing is asked for by a widget of the skin: not the Videos
    window (a folder opened on purpose), not one of the add-on's windows."""
    try:
        import xbmcgui
        window = int(xbmcgui.getCurrentWindowId())
    except Exception:
        return False
    return window != VIDEOS and 0 < window < ADDON_WINDOWS


def af3_widgets():
    """Arctic Fuse 3 with Dex Hub in it is the skin in use."""
    try:
        from . import common as C
        return C.served() == 'af3'
    except Exception:
        return False


def title_query(params, year=''):
    """The title page's query (rows.Context.title_props) from a route's parameters."""
    media = str(params.get('media_type') or 'movie').lower()
    series = media in SERIES
    canonical = params.get('canonical_id') or params.get('imdb_id') or ''
    if not canonical and params.get('tmdb_id'):
        canonical = 'tmdb:%s' % params['tmdb_id']
    if not canonical:
        return []
    from . import common as C
    query = [('m', media if media in ('series', 'anime') else ('series' if series else 'movie')),
             ('id', canonical), ('i', C.short_id('%s|%s' % ('series' if series else 'movie', canonical)))]
    source = params.get('source_provider_id') or ''
    if source.startswith('virtual.'):
        source = ''
    for key, value in (('src', source), ('tmdb', params.get('tmdb_id') or ''),
                       ('imdb', params.get('imdb_id') or ''), ('title', params.get('title') or ''),
                       ('year', year or '')):
        if value:
            query.append((key, str(value)))
    return query


def _year(listitem):
    try:
        year = int(listitem.getVideoInfoTag().getYear() or 0)
        return str(year) if year > 0 else ''
    except Exception:
        return ''


def adapt(entries):
    """A classic Dex Hub listing (catalogs, collections) going to a widget
    of Arctic Fuse 3: its series open Dex Hub's title page in the skin
    (the series' seasons and episodes are there) instead of the add-on's
    seasons listing. ``entries``: (path, ListItem, is_folder) as Kodi gets
    them; anything else, or any other place, is left as it is."""
    try:
        if not entries or not any(('item_open' in str(path or '')) or ('action=streams' in str(path or ''))
                                   or ('action=play_item' in str(path or ''))
                                   for path, _li, _folder in entries):
            return entries
        from . import common as C
        if C.served() == 'dexhub':
            # v5.10.249: in skin.dexhub every title of a catalog (Stremio
            # add-ons, collections, search) opens the skin's title page, in
            # the Videos window too, not the add-on's classic listing
            return _all_to_title_page(entries, widget=in_widget())
        if not any(folder and 'item_open' in str(path or '') for path, _li, folder in entries):
            return entries
        if not in_widget() or not af3_widgets():
            return entries
        from . import items as I
    except Exception:
        return entries
    out = []
    changed = 0
    for path, li, folder in entries:
        try:
            text = str(path or '')
            if folder and text.startswith(_BASE) and 'item_open' in text:
                params = dict(parse_qsl(urlsplit(text).query))
                if params.get('action') == 'item_open' and str(params.get('media_type') or '').lower() in SERIES:
                    query = title_query(params, _year(li))
                    if query:
                        path = run_path(C.url('skin_title', u=text, **dict(query)))
                        folder = False
                        li.setMimeType(MIME)
                        # the context menu's Information opens the same page
                        li.setProperty('dhs.q', urlencode(query))
                        li.setProperty('dhs.id', dict(query).get('i') or '')
                        li.setProperty('dhs.media', 'series')
                        changed += 1
        except Exception:
            pass
        out.append((path, li, folder))
    if changed:
        try:
            xbmc.log('[DexHub] widget: %d series open the title page in the skin' % changed, xbmc.LOGDEBUG)
        except Exception:
            pass
    return out


def _all_to_title_page(entries, widget=False):
    """skin.dexhub (v5.10.249): a work of a classic listing opens the skin's
    title page: a film or a series (item_open), or a film whose OK listed
    its sources (streams, no season). A widget runs the page at once; the
    Videos window runs skin_go as a script (no resolve waits on it)."""
    from . import common as C
    out = []
    changed = 0
    for path, li, folder in entries:
        try:
            text = str(path or '')
            if text.startswith(_BASE):
                params = dict(parse_qsl(urlsplit(text).query))
                action = params.get('action', '')
                work = action == 'item_open' or (
                    action in ('streams', 'play_item') and not folder and not params.get('season')
                    and not params.get('episode') and not params.get('resume_seconds')
                    and not params.get('source_mode')
                    and not params.get('force_picker') and not params.get('stream_key')
                    and str(params.get('media_type') or 'movie').lower() not in SERIES)
                query = title_query(params, _year(li)) if work else []
                if query:
                    if widget:
                        path = run_path(C.url('skin_title', u=text, **dict(query)))
                        li.setMimeType(MIME)
                    else:
                        path = C.url('skin_go', do='info', q=urlencode(query), u=text)
                    folder = False
                    try:
                        li.setProperty('IsPlayable', 'false')
                    except Exception:
                        pass
                    li.setProperty('dhs.q', urlencode(query))
                    li.setProperty('dhs.id', dict(query).get('i') or '')
                    li.setProperty('dhs.media', dict(query).get('m') or '')
                    changed += 1
        except Exception:
            pass
        out.append((path, li, folder))
    if changed:
        try:
            xbmc.log('[DexHub] skin: %d title(s) of this listing open the title page' % changed, xbmc.LOGDEBUG)
        except Exception:
            pass
    return out
