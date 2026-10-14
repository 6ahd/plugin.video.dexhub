# -*- coding: utf-8 -*-
"""Quality badge settings front door."""


def render(api):
    from resources.lib import source_browser as sb

    current = sb._elite_badge_setting_url()
    enabled = sb._elite_badges_enabled()
    toggle_label = (api.tr('تعطيل شارات الجودة') if enabled else
                    api.tr('تفعيل شارات الجودة'))
    choice = api.xbmcgui.Dialog().select(api.tr('شارات الجودة'), [
        toggle_label,
        api.tr('اختيار مجموعة جاهزة'),
        api.tr('تعيين رابط JSON للشارات'),
        api.tr('استخدام المجموعة المدمجة'),
        api.tr('عرض الحالة الحالية'),
    ])
    if choice < 0:
        return

    if choice == 0:
        new_enabled = not enabled
        try:
            api.ADDON.setSetting('elite_badges_enabled', 'true' if new_enabled else 'false')
            api.xbmcgui.Window(10000).setProperty(
                'dexhub.badges.enabled', 'true' if new_enabled else 'false')
            sb._SETTINGS_FILE_MEMO.update({'sig': None, 'values': {}})
        except Exception:
            pass
        api.notify(api.tr('تم تفعيل شارات الجودة') if new_enabled else
                   api.tr('تم تعطيل شارات الجودة'))
        return

    if choice == 1:
        items = []
        for entry in sb.ELITE_BADGE_PRESETS:
            name, url = entry[0], entry[1]
            preview = sb.elite_preset_preview(url, entry[2] if len(entry) > 2 else '')
            li = api.xbmcgui.ListItem(label=name)
            li.setLabel2(api.tr('(الحالي)') if url == current else api.tr('اضغط للتطبيق'))
            if preview:
                li.setArt({'icon': preview, 'thumb': preview})
            items.append(li)
        try:
            pick = api.xbmcgui.Dialog().select(api.tr('اختيار مجموعة جاهزة'), items,
                                               useDetails=True)
        except Exception:
            pick = api.xbmcgui.Dialog().select(
                api.tr('اختيار مجموعة جاهزة'),
                [e[0] for e in sb.ELITE_BADGE_PRESETS])
        if pick < 0:
            return
        url = sb.ELITE_BADGE_PRESETS[pick][1]
        if not sb._elite_url_override_write(url):
            return api.error(api.tr('تعذر حفظ الرابط'))
        try:
            api.ADDON.setSetting('elite_badges_json_url', url)
            api.ADDON.setSetting('elite_badges_enabled', 'true')
            # v5.10.162: this menu's choice is the sources page's own set
            api.ADDON.setSetting('sources_badges_json_url', '')
            api.ADDON.setSetting('badges_sources_follow', 'false')
            api.xbmcgui.Window(10000).setProperty('dexhub.badges.enabled', 'true')
        except Exception:
            pass
        rules = sb._elite_badge_rules()
        if rules:
            return api.notify(api.tr('تم تطبيق %d قاعدة شارات من الرابط المخصص') % len(rules))
        return api.error(api.tr('تعذر تحميل badges.json المخصص — سيتم استخدام الافتراضي'))

    if choice == 2:
        keyboard = api.xbmc.Keyboard(current, api.tr('رابط JSON للشارات'))
        from .. import kb_private
        with kb_private.private():  # v5.10.110: no keyboard suggestions for it
            keyboard.doModal()
        if not keyboard.isConfirmed():
            return
        url = (keyboard.getText() or '').strip()
        if not sb._elite_url_override_write(url):
            return api.error(api.tr('تعذر حفظ الرابط'))
        try:
            api.ADDON.setSetting('elite_badges_json_url', url)
            api.ADDON.setSetting('elite_badges_enabled', 'true')
            # v5.10.162: this menu's choice is the sources page's own set
            api.ADDON.setSetting('sources_badges_json_url', '')
            api.ADDON.setSetting('badges_sources_follow', 'false')
            api.xbmcgui.Window(10000).setProperty('dexhub.badges.enabled', 'true')
        except Exception:
            pass
        rules = sb._elite_badge_rules()
        if rules and url:
            api.notify(api.tr('تم تطبيق %d قاعدة شارات من الرابط المخصص') % len(rules))
        elif url:
            api.error(api.tr('تعذر تحميل badges.json المخصص — سيتم استخدام الافتراضي'))
        else:
            api.notify(api.tr('تم التبديل إلى المجموعة المدمجة'))
        return

    if choice == 3:
        sb._elite_url_override_write('')
        try:
            api.ADDON.setSetting('elite_badges_enabled', 'true')
            # v5.10.162: this menu's choice is the sources page's own set
            api.ADDON.setSetting('sources_badges_json_url', '')
            api.ADDON.setSetting('badges_sources_follow', 'false')
            api.xbmcgui.Window(10000).setProperty('dexhub.badges.enabled', 'true')
        except Exception:
            pass
        api.notify(api.tr('تم التبديل إلى المجموعة المدمجة'))
        return

    rules = sb._elite_badge_rules()
    enabled_now = sb._elite_badges_enabled()
    status = api.tr('مفعلة') if enabled_now else api.tr('معطلة')
    source = current or api.tr('المجموعة المدمجة')
    provenance = sb._elite_url_provenance()
    api.xbmcgui.Dialog().ok(
        'Dex Hub',
        '%s: %s\n%s: %s\n%s: %s\n%s: %d' % (
            api.tr('شارات الجودة'), status,
            api.tr('المصدر'), source,
            api.tr('طبقة الإعداد'), provenance,
            api.tr('القواعد'), len(rules)))
