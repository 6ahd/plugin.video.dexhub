"""Small Arabic/English track preference dialogs."""
def edit(addon,txt):
    import xbmc,xbmcgui
    from .settings_cache import invalidate
    fields=[('preferred_audio_language','لغة الصوت الأساسية','Primary audio language',('default','original','ar','en','ja','fr','de','es','pt-br','tr'),('افتراضي كودي','الصوت الأصلي','العربية','الإنجليزية','اليابانية','الفرنسية','الألمانية','الإسبانية','البرتغالية البرازيلية','التركية'),('Kodi default','Original audio','Arabic','English','Japanese','French','German','Spanish','Brazilian Portuguese','Turkish')),
    ('secondary_audio_language','لغة الصوت البديلة','Secondary audio language',('','ar','en','ja','fr','de','es','pt-br','tr'),('بدون','العربية','الإنجليزية','اليابانية','الفرنسية','الألمانية','الإسبانية','البرتغالية البرازيلية','التركية'),('None','Arabic','English','Japanese','French','German','Spanish','Brazilian Portuguese','Turkish')),
    ('audio_prefer_original','تفضيل المسار الأصلي عند تساوي اللغة','Prefer original track within the selected language',('true','false'),('مفعّل','معطّل'),('On','Off')),
    ('audio_avoid_commentary','تجنّب التعليق والوصف الصوتي تلقائيًا','Avoid commentary and descriptive audio automatically',('true','false'),('مفعّل','معطّل'),('On','Off')),
    ('embedded_subtitle_mode','اختيار الترجمة المدمجة','Embedded subtitle selection',('preferred','forced','off','kodi'),('اللغة المفضلة','الإجبارية فقط باللغة المفضلة','إيقاف تلقائي','اتبع كودي'),('Preferred language','Forced only in preferred language','Off automatically','Follow Kodi')),
    ('subtitle_sdh_preference','مسارات SDH لضعاف السمع','SDH / hearing-impaired tracks',('any','avoid','prefer'),('أي مسار','تفضيل المسار العادي','تفضيل SDH'),('Any track','Prefer regular','Prefer SDH'))]
    defaults=('default','','true','true','preferred','any')
    while True:
        current=[addon.getSetting(f[0]) or d for f,d in zip(fields,defaults)]
        labels=[]
        for f,v in zip(fields,current):
            i=f[3].index(v) if v in f[3] else -1
            labels.append(txt(f[1],f[2])+': '+(txt(f[4][i],f[5][i]) if i>=0 else v))
        from . import lang_picker as LP
        subs=[c for c in (addon.getSetting('preferred_subtitle_langs') or 'ar,en').split(',') if c]
        labels += [txt('لغات صوت أخرى…','More audio languages…'),txt('لغات الترجمة','Subtitle languages')+': '+LP.names(subs[:3],txt),txt('إعدادات الصوت في كودي','Kodi audio settings'),txt('شكل الترجمة والخط في كودي','Kodi subtitle style and font')]
        pick=xbmcgui.Dialog().select(txt('الصوت والترجمة','Audio & subtitles'),labels)
        if pick<0:return True
        if pick<len(fields):
            f=fields[pick];v=current[pick];pre=f[3].index(v) if v in f[3] else 0
            n=xbmcgui.Dialog().select(txt(f[1],f[2]),[txt(a,b) for a,b in zip(f[4],f[5])],preselect=pre)
            if n>=0:addon.setSetting(f[0],f[3][n])
        elif pick==len(fields):
            # v5.10.427: picked by name from a list (Nuvio's way), not typed as codes
            codes=LP.edit_order(txt,txt('لغة الصوت','Audio language'),current[:2],slots=2,
                                extra=(('default','افتراضي كودي','Kodi default'),('original','الصوت الأصلي','Original audio')))
            if codes is not None:
                addon.setSetting('preferred_audio_language',codes[0] if codes else 'default')
                addon.setSetting('secondary_audio_language',codes[1] if len(codes)>1 else '')
        elif pick==len(fields)+1:
            codes=LP.edit_order(txt,txt('لغات الترجمة','Subtitle languages'),subs[:3])
            if codes:addon.setSetting('preferred_subtitle_langs',','.join(codes+[c for c in subs[3:] if c not in codes]))
        elif pick==len(fields)+2:xbmc.executebuiltin('ActivateWindow(osdaudiosettings)' if xbmc.Player().isPlayingVideo() else 'ActivateWindow(settingssystem)');return True
        else:xbmc.executebuiltin('ActivateWindow(osdsubtitlesettings)' if xbmc.Player().isPlayingVideo() else 'ActivateWindow(settingsplayer)');return True
        invalidate()
