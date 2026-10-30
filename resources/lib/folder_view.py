# -*- coding: utf-8 -*-
"""Explicit collection clicks keep the original poster rows and branding."""


def rows_enabled():
    from .ui_preferences import value
    return value('homeui_folder_view') == 'rows'


def choose():
    from .ui_preferences import edit
    from .i18n import is_english
    return edit('homeui_folder_view', lambda ar, en: en if is_english() else ar)


def native_options(params, handle):
    # The setting is shared with the simple menu. set_value publishes the
    # selected view in place; changing a view must never push another window.
    # skin.18: the collection page's own Catalogs / Folders pills name the view
    mode = (params or {}).get('mode') or ''
    if mode in ('rows', 'catalogs'):
        from .ui_preferences import set_value
        if mode == ('rows' if rows_enabled() else 'catalogs'):
            return apply_current()
        return set_value('homeui_folder_view', mode)
    return choose()


def apply_current():
    from .skinui import common as C
    mode = 'rows' if rows_enabled() else 'catalogs'
    C.set_prop('dhs.fold.view', mode)
    data = C.read_json(C.spec_file('fold'), {}) or {}
    ref = data.get('ref') or {}
    if not ref.get('folder_id'):
        return 0
    C.set_prop('dhs.fold.cards', C.url('skin_folder', inline='1', t=data.get('title') or '', **ref))
    publish_cards(data.get('title') or '', ref)
    if mode == 'rows':
        from .i18n import tr
        from .skinui.folderpage import _publish
        shown = _publish(list(data.get('rows') or []), ref, data.get('title') or '', tr)
    else:
        # v5.10.448: the folder view's titles, its first catalog's to begin with
        from .skinui.folderpage import publish_pick
        publish_pick(data, 0)
        shown = len(data.get('rows') or [])
    C.set_prop('dhs.fold.focus_pending', '7001' if mode == 'rows' else '7100')
    return shown


def publish_cards(title, ref, stamp=''):
    """The folder view's catalog tabs (v5.10.448; the page adds the kind
    filter, &k=). A new stamp makes Kodi read them again."""
    from .skinui import common as C
    C.set_prop('dhs.fold.cards.tabs', C.url('skin_folder', inline='1', part='tabs', s=stamp, t=title, **ref))


def initialize_defaults():
    """Apply the requested default once, then keep user choices. v5.10.448: a
    collection opens on its catalog tabs (All, Top 10…) over one poster row,
    as the streaming services do; poster rows stay a choice."""
    from .settings_cache import cached_addon, invalidate
    addon = cached_addon()
    if addon.getSetting('catalog_view_default_v17') == 'true':
        return False
    addon.setSetting('homeui_folder_view', 'catalogs')
    addon.setSetting('catalog_view_default_v16', 'true')
    addon.setSetting('catalog_view_default_v17', 'true')
    invalidate()
    return True
