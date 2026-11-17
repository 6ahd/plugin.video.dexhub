"""One Audio & video entry for playback settings and default track policy."""
def show(addon, txt):
    import xbmc,xbmcgui
    from . import track_settings
    playing=bool(xbmc.Player().isPlayingVideo())
    rows=[txt('الترجمة والصوت — المسارات والتأخير والمظهر ومستوى الصوت','Subtitles & audio — tracks, delay, style and volume'),
          txt('الفيديو — نسبة العرض والصورة','Video — aspect ratio and picture'),
          txt('اللغة الأساسية والبديلة والاختيار التلقائي','Primary/fallback languages and automatic selection'),
          txt('إعدادات المشغّل العامة في كودي','Kodi player preferences'),
          txt('إخراج الصوت والشاشة في كودي','Kodi audio output and display')]
    n=xbmcgui.Dialog().select(txt('الصوت والفيديو','Audio & video'),rows)
    if n<0:return True
    n=(0,1,3,4,5)[n]    # rows 0 and 2 (audio, subtitles) are one page since 5.10.427
    if n==3:return track_settings.edit(addon,txt)
    if n in (0,1) and not playing:
        if n==1:xbmc.executebuiltin('ActivateWindow(settingsplayer)')
        else:track_settings.edit(addon,txt)
        return True
    commands={0:'RunPlugin(plugin://plugin.video.dexhub/?action=skin_osd_subtitles)',
              1:'ActivateWindow(osdvideosettings)',
              4:'ActivateWindow(settingsplayer)',5:'ActivateWindow(settingssystem)'}
    xbmc.executebuiltin('Dialog.Close(1101,true)')
    xbmc.executebuiltin(commands[n])
    return True
