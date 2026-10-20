# -*- coding: utf-8 -*-
"""v5.10.363 (GitHub #27): Back on the Home's menu asks to exit Kodi.

skin.dexhub 3.28.92 calls skin_exit when Back is pressed on the top bar or
the side menu (Back climbs there from the rows, so a few presses of Back end
here). OK quits Kodi, Cancel (or Back) stays. One question at a time.
"""
import xbmc
import xbmcgui

_ASKING = 'dhs.exit.asking'


def ask():
    home = xbmcgui.Window(10000)
    if home.getProperty(_ASKING):
        return None
    if xbmc.getCondVisibility('System.Platform.TVOS | Player.HasVideo | System.HasModalDialog'):
        return None
    from ..i18n import tr
    home.setProperty(_ASKING, '1')
    try:
        dialog = xbmcgui.Dialog()
        args = (tr('الخروج من كودي'), tr('تبي تطلع من كودي؟'))
        try:
            yes = dialog.yesno(*args, nolabel=tr('إلغاء'), yeslabel=tr('موافق'),
                               defaultbutton=xbmcgui.DLG_YESNO_YES_BTN)
        except (AttributeError, TypeError):
            yes = dialog.yesno(*args, nolabel=tr('إلغاء'), yeslabel=tr('موافق'))
    finally:
        home.clearProperty(_ASKING)
    if yes:
        xbmc.executebuiltin('Quit')
    return None
