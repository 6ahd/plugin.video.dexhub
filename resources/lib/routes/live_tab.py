# -*- coding: utf-8 -*-
"""Live TV as a tab of skin.dexhub's Home (v5.10.466).

Hovering Live TV on the menu bar shows the channels on the Home itself:
the channels watched last and the favourites of every source, then the
categories of the source Live TV opens on, each a row of channel cards
loaded on its own (skin_live, homeui.rows.capture_tiles). OK on a channel
opens Live TV on its category and starts it there (home_live with ch=),
so its panel in the player and the guide work as on the page itself.
"""
import xbmc

MAX_CATS = 12


def _t(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _app():
    from ..skinui import rows as SR
    return SR.app()


def _sources(app):
    from ..homeui import live_sources as LS
    return [s for s in LS.sources(app) if s.get('kind') != 'setup']


def _main_source(app, sources):
    from ..homeui import live_sources as LS
    wanted = LS.remembered_tab(app.profile, 'live')
    return ([s for s in sources if s['key'] == wanted] or sources or [None])[0]


def skin_rows():
    """The Live TV tab's rows (homeui Row objects)."""
    from ..homeui import rows as R
    from ..ui_preferences import iptv_available
    if not iptv_available('channels'):
        return []
    app = _app()
    try:
        sources = _sources(app)
    except Exception as exc:
        xbmc.log('[DexHub] live tab: sources not read: %s' % exc, xbmc.LOGINFO)
        sources = []
    if not sources:
        tile = {'kind': 'action', 'command': 'live_setup', 'title': _t('أضف مصدر قنوات', 'Add a channel source'),
                'label': _t('أضف مصدر قنوات', 'Add a channel source'), 'shape': 'landscape',
                'hint': 'M3U  ·  Xtream  ·  Stremio  ·  Kodi PVR',
                'poster': app.media_path('live_stremio.jpg'), 'landscape': app.media_path('live_stremio.jpg')}
        return [R.Row('live:setup', _t('البث المباشر', 'Live TV'), shape='landscape', kind='collection',
                      tiles=[tile], meta={})]
    rows = [R.Row('live:recent', _t('شاهدتها مؤخراً', 'Watched lately'), shape='landscape', kind='catalog',
                  meta={'params': {'action': 'skin_live', 'what': 'recent'}}),
            R.Row('live:fav', _t('قنواتك المفضلة', 'Your favourite channels'), shape='landscape', kind='catalog',
                  meta={'params': {'action': 'skin_live', 'what': 'fav'}})]
    source = _main_source(app, sources)
    for key, title, params in _categories(app, source)[:MAX_CATS]:
        rows.append(R.Row('live:cat:%s' % key, title, shape='landscape', kind='catalog',
                          subtitle=source.get('label') or '',
                          meta={'params': dict(params, action='skin_live', what='cat', cat=key)}))
    return rows


def _categories(app, source):
    """[(key, title, params)] of a source's categories, as the guide lists them."""
    from ..homeui import live, live_sources as LS
    if source is None:
        return []
    if source['kind'] == 'pvr':
        out = []
        for group in [g for g in live.groups() if not g['all']]:
            out.append(('g:%d' % group['id'], group['label'] or _t('قنوات', 'Channels'),
                        {'src': 'pvr', 'group': str(group['id']), 'label': group['label'] or ''}))
        return out
    out = []
    for spec in LS.rows(source, app, changed=lambda _key: None):
        params = dict((k, str(v)) for k, v in (spec.get('params') or {}).items())
        out.append((spec['key'], spec['title'], dict(params, src=source['key'])))
    return out


def _mark(tiles, src, cat):
    for tile in tiles:
        tile['live_cat'] = cat
        tile['src'] = tile.get('src') or src
        tile['landscape'] = tile.get('landscape') or tile.get('poster') or ''
        now = tile.get('now') or {}
        if now.get('title'):
            # the programme on now names the card and fills the hero
            tile['hint'] = now['title']
            tile['plot'] = now.get('plot') or tile.get('desc') or ''
        elif tile.get('desc'):
            tile['plot'] = tile['desc']
    return tiles


def tiles(params):
    """A Live TV row's channels (capture_tiles' skin_live)."""
    from ..homeui import live, live_sources as LS, live_favorites as LF
    app = _app()
    card = app.media_path('channel_card.jpg')
    what = params.get('what') or ''
    if what in ('recent', 'fav'):
        out = []
        for source in _sources(app):
            key = source['key']
            try:
                if what == 'fav':
                    found = LF.tiles(app.profile, key, card)
                elif key == 'pvr':
                    found = live.recent_tiles(app.profile, card)
                else:
                    found = LS.recent_tiles(app.profile, key, card)
            except Exception:
                found = []
            out.extend(_mark(found, key, 'fav' if what == 'fav' else 'recent'))
        return out[:30]
    if what == 'cat':
        src = params.get('src') or ''
        if src == 'pvr':
            found, _more, _total = live.group_tiles(int(params.get('group') or 0), params.get('label') or '', card)
        else:
            page = dict(params)
            for name in ('action', 'what', 'cat', 'src'):
                page.pop(name, None)
            page['live_src'] = src
            found, _more = LS.page_tiles(page, app)
        found = list(found or [])[:40]
        try:
            LS.fill_epg(found, app, limit=12)
        except Exception:
            pass
        return _mark(found, src, params.get('cat') or '')
    return []
