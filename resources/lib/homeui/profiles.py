# -*- coding: utf-8 -*-
"""Nuvio's "Who's watching?" screen (v5.10.162).

The profiles of the linked Nuvio account as Nuvio shows them: each one's
avatar in a circle (the avatar it chose in Nuvio, from Nuvio's own avatar
catalog, else its colour with its initial), its name under it, the one Dex
Hub follows ticked. Choosing one switches Dex Hub to it (Continue Watching,
library and collections follow the profile).

The avatar catalog is read once a week (get_avatar_catalog, the same call
the Nuvio app makes) and kept in nuvio_avatars.json; the pictures are
public files of Nuvio's storage that Kodi caches like any artwork.
"""
import os
import time

import xbmcgui

from ..i18n import is_english

LIST = 100
CATALOG_TTL = 7 * 24 * 3600
STORAGE = '/storage/v1/object/public/avatars/'
DEFAULT_COLOR = '#1E88E5'


def _t(ar, en):
    return en if is_english() else ar


def _kodi_color(hex_color, alpha='FF'):
    text = str(hex_color or '').strip().lstrip('#')
    if len(text) == 3:
        text = ''.join(c * 2 for c in text)
    if len(text) == 8:
        text = text[2:]
    if len(text) != 6 or any(c not in '0123456789abcdefABCDEF' for c in text):
        text = DEFAULT_COLOR.lstrip('#')
    return (alpha + text).upper()


def _catalog_path():
    from ..dexhub.common import profile_path
    return os.path.join(profile_path(), 'nuvio_avatars.json')


def avatar_catalog(sync):
    """{avatar id: image url} from Nuvio's catalog (kept a week; the last
    copy stands in when Nuvio does not answer)."""
    from ..dexhub.safe_io import read_json, write_json
    saved = read_json(_catalog_path(), {}) or {}
    rows = saved.get('rows') if isinstance(saved, dict) else None
    fresh = isinstance(rows, list) and time.time() - float(saved.get('t') or 0) < CATALOG_TTL
    if not fresh:
        try:
            got = sync.Nuvio._rpc('get_avatar_catalog', {})
            if isinstance(got, list) and got:
                rows = [r for r in got if isinstance(r, dict)]
                write_json(_catalog_path(), {'t': time.time(), 'rows': rows})
        except Exception:
            pass
    out = {}
    for row in rows or []:
        path = str(row.get('storage_path') or '')
        if not row.get('id') or not path:
            continue
        url = path if path.startswith(('http://', 'https://')) else sync.NUVIO_URL + STORAGE + path.lstrip('/')
        out[str(row['id'])] = url
    return out


def avatar_of(row, catalog):
    url = str(row.get('avatar_url') or row.get('avatar') or row.get('image') or '')
    if url.startswith(('http://', 'https://')):
        return url
    return catalog.get(str(row.get('avatar_id') or ''), '')


def _index(row, fallback):
    try:
        return int(row.get('profile_index', row.get('profileIndex', row.get('index'))))
    except (TypeError, ValueError):
        return fallback


class ProfilePicker(xbmcgui.WindowXMLDialog):
    ITEM = 300

    def __init__(self, *args, **kwargs):
        super(ProfilePicker, self).__init__(*args)
        self.rows = kwargs.get('rows') or []
        self.avatars = kwargs.get('avatars') or {}
        self.current = kwargs.get('current')
        self.picked = -1

    def onInit(self):
        self.setProperty('kicker', 'NUVIO')
        self.setProperty('title', _t('مين يشاهد؟', "Who's watching?"))
        self.setProperty('sub', _t('Dex Hub يتبع البروفايل في المتابعة والمكتبة والكتالوجات',
                                   'Dex Hub follows the profile for Continue Watching, library and catalogs'))
        self.setProperty('hint', _t('رجوع للخروج بدون تغيير', 'Back to leave without a change'))
        control = self.getControl(LIST)
        items, focus = [], 0
        for n, row in enumerate(self.rows):
            name = str(row.get('name') or row.get('title') or (_t('بروفايل %d', 'Profile %d') % (n + 1)))
            li = xbmcgui.ListItem(name)
            current = self.current is not None and _index(row, -1) == self.current
            if current:
                focus = n
                li.setLabel2(_t('البروفايل الحالي', 'In use'))
            li.setProperty('current', '1' if current else '')
            color = _kodi_color(row.get('avatar_color_hex'))
            li.setProperty('color', color)
            li.setProperty('initial', (name.strip()[:1] or '?').upper())
            art = {}
            image = self.avatars.get(n) or ''
            if image:
                art['thumb'] = image
            background = str(row.get('profile_background_url') or '')
            if background.startswith(('http://', 'https://')):
                art['fanart'] = background
            if art:
                li.setArt(art)
            items.append(li)
        control.addItems(items)
        width = min(1800, self.ITEM * max(1, len(items)))
        try:
            control.setWidth(width)
            control.setPosition((1920 - width) // 2, 450)
        except Exception:
            pass
        control.selectItem(focus)
        self.setFocusId(LIST)

    def onClick(self, control_id):
        if control_id == LIST:
            self.picked = self.getControl(LIST).getSelectedPosition()
            self.close()


def choose(rows, current):
    """The position of the chosen profile in ``rows`` (-1: Back)."""
    from ..dexhub import nuvio_stremio_sync as sync
    try:
        catalog = avatar_catalog(sync)
    except Exception:
        catalog = {}
    avatars = {}
    for n, row in enumerate(rows):
        url = avatar_of(row, catalog)
        if url:
            avatars[n] = url
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    window = ProfilePicker('dexhub_profiles.xml', path, 'Default', '1080i',
                           rows=rows, avatars=avatars, current=current)
    window.doModal()
    picked = window.picked
    del window
    return picked
