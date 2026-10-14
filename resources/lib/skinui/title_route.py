# -*- coding: utf-8 -*-
"""Open an AF3 widget title according to the live title-page preference."""
from urllib.parse import parse_qsl, urlencode
import xbmc
from . import common as C

HELPER = 'plugin.video.themoviedb.helper'
SERIES = ('series', 'anime', 'tv', 'show', 'tvshow')


def mode():
    try:
        from ..live_settings import live_setting
        value = live_setting('homeui_title_page', default='Dex Hub')
    except Exception:
        import xbmcaddon
        value = xbmcaddon.Addon(C.ADDON_ID).getSetting('homeui_title_page') or 'Dex Hub'
    value = str(value).strip().lower()
    return 'tmdbhelper' if value.startswith('tmdb') else ('classic' if value.startswith(('classic', 'كلاسيك')) else 'dexhub')


def classic_path(q, raw=''):
    if raw and raw.startswith(C.BASE):
        params = dict(parse_qsl(raw.partition('?')[2]))
    else:
        params = {'action': 'item_open', 'media_type': q.get('m') or 'movie',
                  'canonical_id': q.get('id') or q.get('imdb') or '',
                  'title': q.get('title') or '', 'tmdb_id': q.get('tmdb') or '',
                  'imdb_id': q.get('imdb') or '', 'source_provider_id': q.get('src') or ''}
    params['dh_home'] = '1'  # prevent the legacy click mode from redirecting it again
    return C.BASE + '?' + urlencode(params)


def helper_path(q):
    series = str(q.get('m') or '').lower() in SERIES
    tmdb, imdb = str(q.get('tmdb') or ''), str(q.get('imdb') or '')
    canonical = str(q.get('id') or '')
    if not tmdb and canonical.startswith('tmdb:'):
        tmdb = canonical.split(':', 1)[1]
    if not imdb and canonical.startswith('tt'):
        imdb = canonical.split(':', 1)[0]
    params = {'info': 'seasons' if series else 'details', 'tmdb_type': 'tv' if series else 'movie'}
    if tmdb.isdigit():
        params['tmdb_id'] = tmdb
    elif imdb.startswith('tt'):
        params['imdb_id'] = imdb
    else:
        return ''
    return 'plugin://%s/?%s' % (HELPER, urlencode(params))


def open_title(params, handle=-1):
    q = dict((k, v) for k, v in params.items() if k not in ('action', 'u', 'page'))
    choice = mode()
    target = helper_path(q) if choice == 'tmdbhelper' else ''
    if target and xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % HELPER):
        xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % target.replace('"', '%22'))
        return
    if choice == 'classic':
        target = classic_path(q, params.get('u') or '')
        series = str(q.get('m') or '').lower() in SERIES
        command = 'ActivateWindow(Videos,"%s",return)' if series else 'RunPlugin("%s")'
        xbmc.executebuiltin(command % target.replace('"', '%22'))
        return
    if choice == 'tmdbhelper':
        import xbmcgui
        from ..i18n import tr
        xbmcgui.Dialog().notification('Dex Hub', tr('تعذر فتح TMDb Helper؛ عرض صفحة Dex Hub'), sound=False)
    from . import items as I
    if I.title_here():
        from .actions import open_title as native_title
        return native_title(q, handle)
    xbmc.executebuiltin('RunPlugin("%s")' % C.url('home_details', **q).replace('"', '%22'))
