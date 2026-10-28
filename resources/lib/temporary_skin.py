# -*- coding: utf-8 -*-
"""Recoverable temporary Dex Hub skin session; the user's skin is kept intact."""
import os
import time
import uuid

PROP='dexhub.temporary_skin.active'
BUSY='dexhub.temporary_skin.busy'

def _parts():
    import xbmc,xbmcgui
    from .skinui import common as C, switch
    return xbmc,xbmcgui,C,switch

def _path(C):
    from .profile_scope import root
    folder=root();os.makedirs(folder,exist_ok=True)
    return os.path.join(folder,'temporary-skin.json')

def _state(C):return C.read_json(_path(C),{}) or {}

def active():
    xbmc,gui,C,switch=_parts()
    state=_state(C)
    return bool(state.get('original') and switch.current()==switch.SKIN)

def _clear(C,gui):
    try:os.remove(_path(C))
    except FileNotFoundError:pass
    gui.Window(10000).clearProperty(PROP)

def _apply(target,C,switch,xbmc):
    monitor=xbmc.Monitor()
    if not switch.enable(target,monitor):return False
    if not switch._clear_screen(monitor):return False
    return switch.apply(target,monitor)

def initialize_defaults():
    """Upgrade the old saved-off default once, then preserve every user choice."""
    from .settings_cache import cached_addon
    addon=cached_addon()
    try:
        if addon.getSetting('interface_defaults_v6')=='true':
            return False
        addon.setSetting('temporary_skin_on_launch','true')
        addon.setSetting('homeui_background_style','fullscreen')
        addon.setSetting('homeui_title_page','Dex Hub')
        addon.setSetting('catalog_click_mode','Dex Hub')
        addon.setSetting('interface_defaults_v6','true')
    except Exception:
        return False
    try:
        from .settings_cache import invalidate
        invalidate()
        from .ui_preferences import publish
        publish()
    except Exception:
        pass
    try:
        import xbmcgui
        xbmcgui.Window(10000).clearProperty('dexhub.control.section')
    except Exception:
        pass
    return True


def _frontdoor_url(url):
    """A source, widget, search or helper URL is never an interface launch."""
    from urllib.parse import urlsplit, parse_qsl
    try:
        parsed=urlsplit(str(url or ''))
        params=dict(parse_qsl(parsed.query,keep_blank_values=True))
        return (parsed.scheme=='plugin' and parsed.netloc=='plugin.video.dexhub' and
                parsed.path in ('','/') and not parsed.fragment and
                set(params)<= {'action'} and params.get('action','') in ('','home'))
    except Exception:
        return False


def _interface_launch(xbmc,handle,params):
    if handle<0 or params.get('action','') not in ('','home'):
        return False
    if set(params)-{'action'}:
        return False
    try:
        if xbmc.Player().isPlayingVideo():
            return False
        if not xbmc.getCondVisibility('Window.IsActive(videos) | Window.IsActive(programs)'):
            return False
        # Positive evidence: the focused item is the add-on icon/front door.
        # Busy dialogs alone also occur during source lookups and widget reads.
        paths=[xbmc.getInfoLabel('ListItem.FolderPath'),xbmc.getInfoLabel('ListItem.Path')]
        if not any(_frontdoor_url(path) for path in paths):
            # Kodi can replace the focused icon before dispatching the root.
            # Confirm the foreground container itself belongs to this add-on.
            if not (_frontdoor_url(xbmc.getInfoLabel('Container.FolderPath')) and
                    xbmc.getInfoLabel('Container.PluginName') in ('','plugin.video.dexhub')):
                return False
        from .routes.simple_entry import _foreground_listing
        return bool(_foreground_listing())
    except Exception:
        return False


def enter(handle=-1, params=None):
    """Handle a foreground root launch only. False leaves normal launch alone."""
    from .settings_cache import cached_addon
    initialize_defaults()
    if str(cached_addon().getSetting('temporary_skin_on_launch') or 'true').lower()!='true':return False
    xbmc,gui,C,switch=_parts();home=gui.Window(10000)
    if handle<0 or switch.current()==switch.SKIN or home.getProperty(BUSY) or switch.busy():return False
    if not _interface_launch(xbmc,handle,dict(params or {})):return False
    if not switch.installed(switch.SKIN):
        gui.Dialog().notification('Dex Hub',switch._tr('ثبّت سكين Dex Hub أولًا لتفعيل السكين المؤقت.'))
        return False
    state={'original':switch.current(),'session':uuid.uuid4().hex,'started':time.time()}
    try:
        from .interface_fonts import current_font
        state['font']=current_font()
    except Exception:
        pass
    if not state['original'] or not C.write_json(_path(C),state):return False
    # End the directory before switching to release Kodi's loading dialog.
    import xbmcplugin
    xbmcplugin.endOfDirectory(handle,succeeded=False,updateListing=False,cacheToDisc=False)
    home.setProperty(BUSY,'1')
    try:
        if _apply(switch.SKIN,C,switch,xbmc):
            home.setProperty(PROP,state['session'])
            xbmc.executebuiltin('ActivateWindow(Home)',wait=True)
            return True
        # Preserve recovery information if a partly completed switch took effect.
        if switch.current()!=switch.SKIN:_clear(C,gui)
        return True
    finally:home.clearProperty(BUSY)

def leave():
    xbmc,gui,C,switch=_parts();home=gui.Window(10000);state=_state(C)
    if not state.get('original'):return True
    if home.getProperty(BUSY) or switch.busy():return False
    if switch.current()!=switch.SKIN:
        # A skin chosen explicitly by the user takes precedence.
        _clear(C,gui);return True
    target=state['original']
    if not switch.installed(target):target=switch.DEFAULT_SKIN
    home.setProperty(BUSY,'1')
    try:
        if not _apply(target,C,switch,xbmc):return False
        if state.get('font'):
            try:
                from .interface_fonts import restore_original
                restore_original(state['font'])
            except Exception:
                pass
        _clear(C,gui)
        xbmc.executebuiltin('ActivateWindow(Home)',wait=True)
        return True
    finally:home.clearProperty(BUSY)

def recover():
    """A fresh Kodi service run restores an interrupted temporary session."""
    initialize_defaults()
    xbmc,gui,C,switch=_parts();home=gui.Window(10000)
    if home.getProperty(PROP) or home.getProperty(BUSY):return
    if _state(C):leave()

def open_kodi_addons():
    import xbmc
    if leave():xbmc.executebuiltin('ActivateWindow(AddonBrowser)',wait=True)


def open_kodi_settings():
    import xbmc
    if leave():xbmc.executebuiltin('ActivateWindow(Settings)',wait=True)
