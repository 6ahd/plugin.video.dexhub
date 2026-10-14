# -*- coding: utf-8 -*-
"""The title page's season list as one window (v5.10.224): OK on a season of
a show with many seasons lists them all at once; the one picked takes the
focus on the page (its episodes follow, as when it is reached by hand)."""
import xbmc
import xbmcgui

LIST = 8200


def _tr(text):
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


def run(params=None):
    try:
        count = int(xbmc.getInfoLabel('Container(%d).NumItems' % LIST) or 0)
    except ValueError:
        count = 0
    if count <= 0:
        return None
    items, art = [], False
    for i in range(count):
        label = xbmc.getInfoLabel('Container(%d).ListItemAbsolute(%d).Label' % (LIST, i))
        poster = (xbmc.getInfoLabel('Container(%d).ListItemAbsolute(%d).Art(poster)' % (LIST, i))
                  or xbmc.getInfoLabel('Container(%d).ListItemAbsolute(%d).Art(thumb)' % (LIST, i)))
        li = xbmcgui.ListItem(label)
        if poster:
            li.setArt({'thumb': poster, 'icon': poster})
            art = True
        items.append(li)
    try:
        current = max(0, int(xbmc.getInfoLabel('Container(%d).CurrentItem' % LIST) or 1) - 1)
    except ValueError:
        current = 0
    pick = xbmcgui.Dialog().select(_tr('كل المواسم'), items, preselect=current, useDetails=art)
    if pick is None or pick < 0:
        return None
    xbmc.executebuiltin('Control.SetFocus(%d,%d,absolute)' % (LIST, pick))
    return None
