# -*- coding: utf-8 -*-
"""Click-only catalog navigation, shared by the original Kodi and portable UI."""
from urllib.parse import parse_qsl, urlsplit


def unwrap(params):
    """Recover raw catalog routes from older cached skin_list wrappers."""
    params = dict(params or {})
    for _ in range(8):
        if params.get('action') == 'skin_catalog_hub':
            target = urlsplit(params.get('target') or '')
            if target.scheme == 'plugin' and target.netloc == 'plugin.video.dexhub':
                params = dict(parse_qsl(target.query, keep_blank_values=True))
            elif params.get('folder_id'):
                params['action'] = 'skin_folder'
            else:
                return {}
        if params.get('action') != 'skin_list':
            action = params.get('action') or ''
            if action.startswith('skin_') and action not in ('skin_folder', 'skin_fold', 'skin_follow'):
                return {}
            return params
        nested = dict(parse_qsl(params.get('r') or '', keep_blank_values=True))
        if not nested or nested == params:
            return {}
        params = nested
    return {} if params.get('action') == 'skin_list' else params


def folder_tiles(params, api):
    """List catalog choices without fetching any catalog contents."""
    from types import SimpleNamespace
    from .homeui.window import BrowseWindow
    from .i18n import tr
    from .skinui import common as C
    folder, specs = BrowseWindow._folder_sources(SimpleNamespace(tr=tr), params)
    wrap = getattr(api, '_wrap_tokenized_url', None) or (lambda url: url)
    folder = folder or {}
    cover = wrap(str(folder.get('poster') or folder.get('background') or ''))
    background = wrap(str(folder.get('background') or folder.get('poster') or ''))
    logo = wrap(str(folder.get('clearlogo') or (folder.get('poster') if folder.get('hide_title') else '') or ''))
    return [{'kind': 'nav', 'folder': True, 'title': title, 'label': title,
             'path': C.url(**route), 'hint': subtitle, 'plot': subtitle,
             'poster': cover, 'landscape': cover, 'fanart': background, 'clearlogo': logo,
             'shape': 'landscape'} for key, title, subtitle, route in specs]


def open_selector(params, handle):
    """Accept legacy RunPlugin links as well as normal directory requests."""
    from .skinui import common as C, actions
    if handle >= 0:
        return actions.folder(params, handle)
    import xbmc
    route = {key: value for key, value in params.items()
             if key in ('set_id', 'group_id', 'folder_id', 'media_filter', 't')}
    path = C.url('skin_folder', **route)
    xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % actions._arg(path))


def folder_ref(params):
    route = unwrap(params)
    if route.get('action') not in ('skin_folder', 'skin_fold', 'collection_group_folder_open') or not route.get('folder_id'):
        return None
    return {key: route.get(key) or '' for key in ('set_id','group_id','folder_id','media_filter')}
