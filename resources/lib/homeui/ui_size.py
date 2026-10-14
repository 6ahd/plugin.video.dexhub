# -*- coding: utf-8 -*-
"""The size of the Dex Hub windows (v5.10.110).

gen_xml.py lays every window out three times, each on its own canvas that
Kodi zooms to fill the screen: normal (the 1080 design as it was), large
(about Arctic Fuse 3's size, the default) and extra large. The setting
homeui_size picks the file a window opens with: dexhub_browse.xml,
dexhub_browse_l.xml or dexhub_browse_xl.xml.
"""
import xbmcaddon

ADDON_ID = 'plugin.video.dexhub'
SETTING = 'homeui_size'
# the setting's stored values (labelenum values) and their files
SUFFIX = {'normal': '', 'large': '_l', 'extra large': '_xl'}
DEFAULT = 'large'
ORDER = ('Normal', 'Large', 'Extra large')


def current():
    """The chosen size: 'normal', 'large' or 'extra large'."""
    try:
        value = (xbmcaddon.Addon(ADDON_ID).getSetting(SETTING) or '').strip().lower()
    except Exception:
        value = ''
    return value if value in SUFFIX else DEFAULT


def xml(name):
    """The window file for the chosen size ('dexhub_browse.xml' ->
    'dexhub_browse_l.xml' on the large size)."""
    suffix = SUFFIX[current()]
    if not suffix or not name.endswith('.xml'):
        return name
    return name[:-4] + suffix + '.xml'


def choose(tr):
    """Pick the size in Dex Hub's options menu; True when it changed."""
    from .options import choose as menu
    options = [(tr('عادي'), '100%'), (tr('كبير'), '120%'), (tr('كبير جداً'), '130%')]
    index = [v.lower() for v in ORDER].index(current())
    choice = menu(tr('حجم الواجهة'), options, preselect=index)
    if choice < 0 or choice == index:
        return False
    try:
        xbmcaddon.Addon(ADDON_ID).setSetting(SETTING, ORDER[choice])
    except Exception:
        return False
    return True
