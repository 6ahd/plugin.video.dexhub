"""UI family, size and weight, independent of subtitle typography."""
import time

# Kept for callers of the original settings module. The menu discovers the
# current skin presets and user media fonts rather than using this list.
FAMILIES = ('Default','Arial','مهند','الجزيرة','منادي','APPLE','امازون','حسن','تونس','RobotoNotoCondensed')
SCALES = (90,95,100,105,110,115,120)
_state={'at':0.0,'stamp':None,'tries':0,'registered':None}


def _rpc(method,params):
    from .kodi_rpc import call
    return call(method,params)


def current_font():
    try:return str((_rpc('Settings.GetSettingValue',{'setting':'lookandfeel.font'}) or {}).get('value') or '')
    except Exception:return ''


def restore_original(font):
    if not font:return False
    try:return bool(_rpc('Settings.SetSettingValue',{'setting':'lookandfeel.font','value':str(font)}))
    except Exception:return False


def fontset(family,scale,weight='normal'):
    try:scale=int(scale)
    except (ValueError,TypeError):scale=100
    if scale not in SCALES:scale=100
    return str(family or 'Default')+(' · '+str(scale)+'%' if scale!=100 else '')+(' · Bold' if weight=='bold' else '')


def reset():
    _state.update(at=0.0,stamp=None,tries=0,registered=None)


# v5.10.427: Kodi's Interface > Skin > Fonts is the one place the interface
# font is chosen. Dex Hub no longer keeps a choice of its own that it put back
# over Kodi's (it waited for every Dex Hub window to close, so it seldom got
# its turn, and a font picked in Kodi was turned back when it did). It only
# remembers the font Kodi has, and puts it back once when the skin comes up
# on Default (a skin switch, a skin update) while another font was in use.
def tick():
    now=time.monotonic()
    if now-_state['at']<2:return False
    _state['at']=now
    import xbmc,xbmcgui
    if xbmc.getSkinDir()!='skin.dexhub':return False
    from .settings_cache import cached_addon
    addon=cached_addon();font=current_font()
    if not font:return False
    kept=addon.getSetting('interface_font_kodi') or ''
    if not kept and addon.getSetting('interface_font'):
        # the choice an older Dex Hub kept, once
        kept=fontset(addon.getSetting('interface_font'),addon.getSetting('interface_font_scale') or '100',
                     addon.getSetting('interface_font_weight') or 'normal')
    if _state['stamp'] is None:
        _state['stamp']='start'
        if font=='Default' and kept and kept!='Default' and _known(kept):
            if xbmc.Player().isPlayingVideo() or xbmcgui.getCurrentWindowDialogId() not in (0,9999):   # 9999: no dialog
                _state['stamp']=None;return False
            try:
                if int(xbmcgui.Window(10000).getProperty('dexhub.homeui.windows') or 0)>0:
                    _state['stamp']=None;return False
            except (ValueError,TypeError):return False
            return restore_original(kept)
    if font!=kept:
        addon.setSetting('interface_font_kodi',font)
        from .settings_cache import invalidate
        invalidate()
    return False


def _known(font):
    try:
        from .font_catalog import skin_font_file
        import xml.etree.ElementTree as E
        return any(fs.get('id')==font for fs in E.parse(skin_font_file()).getroot().findall('fontset'))
    except Exception:
        return False


def choose(addon,txt):
    """Kodi's font setting; a TTF of your own is added to its list first."""
    import xbmc,xbmcgui
    from . import font_catalog
    n=xbmcgui.Dialog().select(txt('خط الواجهة وحجمه','Interface font & size'),[
        txt('اختر الخط والحجم (إعدادات كودي)','Choose font and size (Kodi settings)'),
        txt('أضف خطًا من ملفاتك (TTF)…','Add a font of your own (TTF)…'),
        txt('مجلد خطوط إضافي…','Additional font folder…')])
    if n<0:return True
    if n==0:
        xbmc.executebuiltin('Dialog.Close(all,true)')
        xbmc.executebuiltin('ActivateWindow(interfacesettings)')
        return True
    if n==2:
        path=xbmcgui.Dialog().browseSingle(0,txt('مجلد الخطوط','Font folder'),'files',defaultt=addon.getSetting('interface_font_dir') or 'special://home/media/Fonts/')
        if path:
            addon.setSetting('interface_font_dir',path)
            from .settings_cache import invalidate
            invalidate()
        return True
    choices=font_catalog.media_fonts(addon.getSetting('interface_font_dir') or '')
    if not choices:
        xbmcgui.Dialog().ok(txt('إضافة خطوط للواجهة','Add interface fonts'),txt('ضع ملفات TTF في مجلد كودي media/Fonts أو اختر مجلدًا إضافيًا، ثم ارجع هنا.','Put TTF files in Kodi\'s media/Fonts folder or choose an additional folder, then come back here.'))
        return True
    selected=xbmcgui.Dialog().select(txt('خطوط مجلد الميديا','Media folder fonts'),[c['label'] for c in choices])
    if selected<0:return True
    face=choices[selected]
    try:
        font_catalog.register(face['id'],face['source'],100,'normal')
    except Exception:
        xbmcgui.Dialog().notification('Dex Hub',txt('الخط غير متاح؛ اختر خطًا آخر','Font unavailable; choose another'),sound=False)
        return True
    # Kodi reloads the skin when its font setting changes
    xbmc.executebuiltin('Dialog.Close(all,true)')
    if not restore_original(fontset(face['id'],100)):
        xbmc.executebuiltin('ReloadSkin()')
        xbmc.executebuiltin('ActivateWindow(interfacesettings)')
    return True
