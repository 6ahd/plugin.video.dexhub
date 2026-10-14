# -*- coding: utf-8 -*-
"""Reboot to Android from CoreELEC (v5.10.131), as Arctic Fuse 3's shortcut does.

CoreELEC's rebootfromnand marks the next start for the box's internal system
(Android), and Kodi's Reboot starts it. Offered only where CoreELEC runs.
"""
import xbmc
import xbmcgui

REBOOT_FROM_NAND = '/usr/sbin/rebootfromnand'


def coreelec():
    """Is this Kodi CoreELEC's? (its settings add-on is part of the system)"""
    try:
        return bool(xbmc.getCondVisibility('System.HasAddon(service.coreelec.settings)'))
    except Exception:
        return False


def reboot_to_android(tr=None, confirm=True):
    """Restart the box into Android; False when the user changed their mind."""
    tr = tr or (lambda text: text)
    if confirm and not xbmcgui.Dialog().yesno('Dex Hub', tr('يعيد الجهاز التشغيل إلى أندرويد. متابعة؟')):
        return False
    xbmc.log('[DexHub] power: reboot to Android', xbmc.LOGINFO)
    xbmc.executebuiltin('System.ExecWait("%s")' % REBOOT_FROM_NAND, True)
    xbmc.executebuiltin('Reboot')
    return True
