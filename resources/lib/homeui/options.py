# -*- coding: utf-8 -*-
"""The Home's own options menu (v5.10.103).

A centred panel in the Home's design (idea from the Nuvio Hub community
build) in place of Kodi's context menu and select dialogs: the options of a
title, of a row, the Home menu, the trailer speed. The skin draws it from
dexhub_options.xml: list 100 holds the options (label, optional hint on the
right), window properties do.title and do.subtitle the heading.

choose() returns the chosen index or -1, like Dialog().contextmenu(); if the
window cannot open (a skin without the file), Kodi's own dialog is used.
"""
import xbmc
import xbmcaddon
import xbmcgui

ADDON_ID = 'plugin.video.dexhub'
LIST = 100
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
A_CONTEXT = {117, 101}


class OptionsDialog(xbmcgui.WindowXMLDialog):
    def __new__(cls, xml_file, addon_path, skin='Default', res='1080i', **kwargs):
        # Keep title/options out of Kodi's native XML constructor parser.
        return super(OptionsDialog, cls).__new__(cls, xml_file, addon_path, skin, res)

    def __init__(self, *args, **kwargs):
        super(OptionsDialog, self).__init__(*args)
        self.heading = kwargs.get('title') or ''
        self.subtitle = kwargs.get('subtitle') or ''
        self.options = list(kwargs.get('options') or [])
        self.preselect = int(kwargs.get('preselect', -1))
        self.choice = -1

    def onInit(self):
        self.setProperty('do.title', self.heading)
        self.setProperty('do.subtitle', self.subtitle)
        items = []
        for option in self.options:
            icon = desc = ''
            if isinstance(option, (list, tuple)):
                label = option[0] if option else ''
                hint = option[1] if len(option) > 1 else ''
                # v5.10.146: the menu layout's cards: an icon and a line
                icon = option[2] if len(option) > 2 else ''
                desc = option[3] if len(option) > 3 else ''
            else:
                label, hint = option, ''
            li = xbmcgui.ListItem(label=str(label or ''))
            if hint:
                li.setProperty('hint', str(hint))
            if icon:
                li.setArt({'icon': icon})
                # v5.10.147: a service's own logo keeps its colours (the
                # menu's line icons are drawn in the text colour)
                if '/media/menu/' not in icon.replace('\\', '/'):
                    li.setProperty('logo', 'true')
            if desc:
                li.setProperty('desc', str(desc))
            items.append(li)
        try:
            ctrl = self.getControl(LIST)
            ctrl.reset()
            ctrl.addItems(items)
            if 0 <= self.preselect < len(items):
                ctrl.selectItem(self.preselect)
        except Exception:
            pass
        self.setFocusId(LIST)

    def onClick(self, control_id):
        if control_id == LIST:
            try:
                position = int(self.getControl(LIST).getSelectedPosition())
                self.choice = position if 0 <= position < len(self.options) else -1
            except Exception:
                self.choice = -1
            self.close()

    def onAction(self, action):
        if action.getId() in A_BACK or action.getId() in A_CONTEXT:
            self.choice = -1
            self.close()


def _labels(options):
    return [str(o[0] if isinstance(o, (list, tuple)) else o) for o in options]


def choose(title, options, preselect=-1, subtitle='', layout='list'):
    """Index of the chosen option (a label, or (label, hint), or for the
    'menu' layout (label, hint, icon, description)), or -1."""
    options = list(options or [])
    if not options:
        return -1
    try:
        path = xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')
        from . import ui_size
        name = {'menu': 'dexhub_menu.xml', 'gallery': 'dexhub_gallery.xml'}.get(layout, 'dexhub_options.xml')
        dialog = OptionsDialog(ui_size.xml(name), path, 'Default', '1080i', title=title,
                               subtitle=subtitle, options=options, preselect=preselect)
        dialog.doModal()
        choice = dialog.choice
        del dialog
        return choice
    except Exception as exc:
        xbmc.log('[DexHub] homeui: options menu unavailable (%s); using Kodi dialog' % exc,
                 xbmc.LOGWARNING)
    try:
        if title and preselect >= 0:
            return xbmcgui.Dialog().select(title, _labels(options), preselect=preselect)
        return xbmcgui.Dialog().contextmenu(_labels(options))
    except Exception:
        return -1
