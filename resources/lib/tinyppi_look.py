# -*- coding: utf-8 -*-
"""v5.10.418: who shows what plays, Dex Hub or TinyPPI (player_info_source).

Dex Hub: the playback capsule at the top, TinyPPI's own logos at the top
turned off, and TinyPPI's windows (its overlay, the VS10 dialog) drawn in the
skin's design. TinyPPI: its logos at the top (at the start, and with the
player's menu when the capsule would have come with OK), no capsule, and its
windows in its own design.

Kodi takes a script's window file from the running skin first, so the skin
(3.28.135, tools/release/skin_tinyppi.py) ships the redesigned files in xml/
and a copy in extras/tinyppi/. TinyPPI's own design is the skin without
them: they are taken out of xml/ and put back from the copy. Only files the
tool wrote (its mark in them) are ever touched. An update of the skin brings
them back, so this runs at every start, settings change and playback.

TinyPPI's settings are written once per change of the choice; what the user
changes in TinyPPI afterwards stays.
"""
import os

import xbmc

MARK = '<!-- dhs.tinyppi (skin_tinyppi.py) -->'
FILES = ('script-tinyppi-main.xml', 'script-tinyppi-dialog.xml', 'script-tinyppi-dialog-bar.xml',
         'script-tinyppi-dialog-single.xml')
TINYPPI = 'script.tinyppi'
SKINS = ('skin.dexhub', 'skin.dexhub.tvos')


def installed():
    try:
        return bool(xbmc.getCondVisibility('System.HasAddon(%s)' % TINYPPI))
    except Exception:
        return False


def choice():
    try:
        from .ui_preferences import value
        return value('player_info_source')
    except Exception:
        return 'dexhub'


def tinyppi_shows():
    """TinyPPI is chosen and there: the capsule stays away."""
    return choice() == 'tinyppi' and installed()


def _ours(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return MARK in fh.read(512)
    except Exception:
        return False


def windows(own_design):
    """The skin's copy of TinyPPI's windows in xml/ (Dex Hub's design) or not
    (TinyPPI's). Returns what was changed."""
    if xbmc.getSkinDir() not in SKINS:
        return []
    try:
        import xbmcvfs
        skin = xbmcvfs.translatePath('special://skin/')
    except Exception:
        return []
    changed = []
    for name in FILES:
        live = os.path.join(skin, 'xml', name)
        copy = os.path.join(skin, 'extras', 'tinyppi', name)
        try:
            if own_design:
                if not os.path.exists(live) and _ours(copy):
                    with open(copy, 'rb') as src:
                        data = src.read()
                    with open(live + '.tmp', 'wb') as out:
                        out.write(data)
                    os.replace(live + '.tmp', live)
                    changed.append(name)
            elif os.path.exists(live) and _ours(live):
                if not os.path.exists(copy):
                    continue        # no copy to come back from: leave it
                os.remove(live)
                changed.append(name)
        except Exception as exc:
            xbmc.log('[DexHub] TinyPPI windows: %s: %s' % (name, exc), xbmc.LOGWARNING)
    if changed:
        xbmc.log('[DexHub] TinyPPI windows in %s design: %s' % ('Dex Hub' if own_design else "TinyPPI's",
                                                                ', '.join(changed)), xbmc.LOGINFO)
    return changed


def _state_path():
    try:
        import xbmcaddon
        import xbmcvfs
        folder = xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile'))
        return os.path.join(folder, 'tinyppi_look.txt')
    except Exception:
        return ''


def _logos(chosen):
    """TinyPPI's logos at the top: off for Dex Hub, on for TinyPPI (with the
    player's menu too when the capsule would have come with OK)."""
    path = _state_path()
    try:
        with open(path, encoding='utf-8') as fh:
            if fh.read().strip() == chosen:
                return
    except Exception:
        pass
    try:
        import xbmcaddon
        from .ui_preferences import value
        addon = xbmcaddon.Addon(TINYPPI)
        if chosen == 'tinyppi':
            addon.setSetting('splash_enabled', 'true')
            addon.setSetting('splash_show_on_osd', 'true' if value('player_capsule_when') in ('both', 'ok') else 'false')
        else:
            addon.setSetting('splash_enabled', 'false')
            addon.setSetting('splash_show_on_osd', 'false')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(chosen)
        xbmc.log('[DexHub] TinyPPI logos at the top: %s' % ('on' if chosen == 'tinyppi' else 'off'), xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log('[DexHub] TinyPPI settings: %s' % exc, xbmc.LOGWARNING)


def apply():
    """Bring the skin and TinyPPI in line with the choice (cheap when they are)."""
    chosen = choice()
    windows(own_design=chosen != 'tinyppi')
    if installed():
        _logos(chosen)
