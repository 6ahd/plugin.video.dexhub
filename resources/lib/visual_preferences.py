"""Keep visual appearance selectors in the simple settings menu."""
import os

def edit(key):
    import xbmc,xbmcaddon
    if key=='theme_preset':
        xbmc.executebuiltin('ActivateWindow(1191)' if xbmc.getSkinDir()=='skin.dexhub' else 'RunPlugin(plugin://plugin.video.dexhub/?action=theme_select)')
        return 'launched'
    if key!='homeui_layout_mode':
        return False
    from .homeui.control import PICKS,PICK_LINES,apply_entry
    from .homeui.options import choose
    from .ui_preferences import value
    from .i18n import tr
    choices=PICKS['home_layout_mode'][1]
    current=value(key)
    folder=os.path.join(xbmcaddon.Addon().getAddonInfo('path'),'resources','media','homeui')
    cards=[(tr(label),'current' if v==current else '',os.path.join(folder,'homelayout_%s.jpg'%v),'') for v,label in choices]
    selected=choose(tr('وضع الواجهة'),cards,preselect=next((i for i,(v,_) in enumerate(choices) if v==current),0),layout='gallery')
    if 0<=selected<len(choices):
        apply_entry('home_layout_mode',choices[selected][0])
    return True
