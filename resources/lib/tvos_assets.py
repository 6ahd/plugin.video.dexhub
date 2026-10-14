"""Resolve original UI textures from the actual installed addon location."""
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.dexhub'
PREFIX = 'special://home/addons/' + ADDON_ID + '/'
PROPERTY = 'dexhub.assetroot'


def asset_root():
    return xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')).rstrip('/\\')


def resolve_assets(value):
    return str(value).replace(PREFIX, asset_root() + '/')


def publish_asset_root():
    xbmcgui.Window(10000).setProperty(PROPERTY, asset_root())
