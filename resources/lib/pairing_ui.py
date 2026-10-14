# -*- coding: utf-8 -*-
"""Shared pairing surface: preblurred backdrop, opaque card, crisp QR.

No screenshot capture, blur worker or dependency on the current skin.
"""
import os
import xbmcaddon
import xbmcgui


def surface(window, width, height):
    media = os.path.join(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path'),
                         'resources', 'media')
    white = os.path.join(media, 'white.png')
    blur = os.path.join(media, 'homeui', 'pairing_blur.jpg')
    x, y = (1280 - width) // 2, (720 - height) // 2
    # An empty ControlImage texture draws nothing, regardless of colorDiffuse.
    window.addControl(xbmcgui.ControlImage(0, 0, 1280, 720, blur))
    window.addControl(xbmcgui.ControlImage(0, 0, 1280, 720, white,
                                         colorDiffuse='AA080B12'))
    window.addControl(xbmcgui.ControlImage(x - 2, y - 2, width + 4, height + 4,
                                         white, colorDiffuse='FF394359'))
    window.addControl(xbmcgui.ControlImage(x, y, width, height, white,
                                         colorDiffuse='FF101622'))
    window.addControl(xbmcgui.ControlImage(x + 26, y + 28, 4, height - 56,
                                         white, colorDiffuse='FFAA9DFF'))
    return x, y


def qr(window, path, x, y, size):
    if not path:
        return
    media = os.path.join(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path'),
                         'resources', 'media')
    window.addControl(xbmcgui.ControlImage(x - 10, y - 10, size + 20, size + 20,
                                         os.path.join(media, 'white.png')))
    window.addControl(xbmcgui.ControlImage(x, y, size, size, path))
