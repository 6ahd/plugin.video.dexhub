# -*- coding: utf-8 -*-
"""Collection rows the user makes (v5.10.179).

The user picks a few catalogs (a source's, a Plex, Emby, Jellyfin or Silo
library); the row shows one collection card for each, several to a row, as
Apple TV shows its collections: the catalog's posters fill the card (a wall,
a mosaic or a fanned stack, the row's style) with its name at the foot. OK on
a card opens that catalog.

Saved as a custom row of the layout (layout.json):
  {'type': 'shelf', 'id', 'label', 'members': [{'label', 'params',
   'source_label'}...]}

The row reads the route ``home_shelf`` (id, m): each member's first page is
read (side by side, the catalogs' own caches answer most of them) and its
first posters go on its card (properties p1 to p20).
"""
import threading

MAX_POSTERS = 20
LOOKS = ('collage', 'mosaic', 'stack', 'collection_spotlight', 'collection_compact', 'collection_ranking')


def _tr(text):
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


def find(shelf_id):
    """The saved spec of a collection row, on whichever page it is."""
    from .layout import Layout, PAGES
    import os
    import xbmcaddon
    import xbmcvfs
    profile = os.path.join(xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile')),
                           'homeui')
    layout = Layout(profile)
    for media in PAGES:
        for spec in layout.page(media)['custom']:
            if spec.get('id') == shelf_id and spec.get('type') == 'shelf':
                return spec
    return None


def _member_tiles(member, api, out, index):
    from . import rows as R
    try:
        tiles, _more = R.capture_tiles(dict(member.get('params') or {}), api, limit=MAX_POSTERS + 4)
    except Exception:
        tiles = []
    out[index] = tiles


def route(params, api):
    """The cards of a collection row (one per catalog)."""
    from .. import ui_core
    shelf_id = params.get('id') or ''
    if shelf_id == 'auto:recs':
        # v5.10.236: the add-ons' recommendation catalogs (Group recommendation rows)
        from . import rows as R
        spec = {'members': [{'label': label, 'params': p}
                            for label, p in R.recommendation_tabs(api, params.get('m') or 'all')][:24]}
    else:
        spec = find(shelf_id)
    members = list((spec or {}).get('members') or [])
    found = [None] * len(members)
    threads = []
    for index, member in enumerate(members):
        thread = threading.Thread(target=_member_tiles, args=(member, api, found, index),
                                  name='DexHub-shelf-%d' % index)
        thread.daemon = True
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(30)
    from urllib.parse import urlencode
    for index, member in enumerate(members):
        tiles = [t for t in (found[index] or []) if t.get('poster') or t.get('fanart')]
        posters = [p for p in (t.get('poster') or t.get('landscape') or t.get('fanart') for t in tiles) if p]
        # a short catalog fills its card again from its first posters
        posters = (posters * (MAX_POSTERS // max(1, len(posters)) + 1))[:MAX_POSTERS] if posters else []
        lead = next((t for t in tiles if t.get('fanart')), tiles[0] if tiles else {})
        count = len(found[index] or [])
        line = '  ·  '.join(p for p in (member.get('source_label') or '',
                                          (_tr('%d عمل') % count) if count else '') if p)
        props = {'dexhub.shelf': '1', 'collage_line': line}
        from .row_styles import preview_fields
        props.update(preview_fields(tiles))
        for n, poster in enumerate(posters):
            props['p%d' % (n + 1)] = poster or ''
        path = 'plugin://plugin.video.dexhub/?' + urlencode(sorted((member.get('params') or {}).items()))
        ui_core.add_item(member.get('label') or '', path, is_folder=True,
                         info={'title': member.get('label') or '', 'plot': line},
                         art={'poster': lead.get('poster') or (posters[0] if posters else ''),
                              'fanart': lead.get('fanart') or '', 'landscape': lead.get('fanart') or ''},
                         properties=props)
    ui_core.end_dir(cache=False, sortable=False)


def pick(app, media='all'):
    """Make a collection row: its name, then its catalogs one by one."""
    import xbmcgui
    tr = app.tr
    name = xbmcgui.Dialog().input(tr('اسم الكوليكشن'), defaultt=tr('كوليكشن'))
    if not name:
        return None
    from .layout_pick import pick_member
    members = []
    while True:
        heading = tr('أضف كتالوج للكوليكشن (%d)') % len(members) if members else tr('أضف أول كتالوج للكوليكشن')
        member = pick_member(app, media, heading)
        if not member:
            break
        members.append({'label': member.get('label') or '', 'params': dict(member.get('params') or {}),
                        'source_label': member.get('source_label') or ''})
        if not xbmcgui.Dialog().yesno(name, tr('أُضيف «%s». تضيف كتالوج ثاني؟') % (member.get('label') or ''),
                                      nolabel=tr('خلاص'), yeslabel=tr('أضف')):
            break
    if not members:
        return None
    return {'type': 'shelf', 'label': name, 'members': members,
            'source_label': tr('كوليكشن  ·  %d كتالوج') % len(members)}
