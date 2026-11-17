# -*- coding: utf-8 -*-
"""Small, task-oriented setup front door for Dex Hub.

Kodi's native settings window remains available for advanced users, but it is
not a friendly first stop on a television.  This module groups the handful of
choices that materially change day-to-day use into short remote-friendly
dialogs.  It deliberately never enables automatic source playback without a
separate confirmation.
"""


def _setting(addon, key, default=''):
    try:
        value = addon.getSetting(key)
    except Exception:
        value = ''
    return str(value if value not in (None, '') else default)


def _set_many(addon, values):
    for key, value in values.items():
        addon.setSetting(str(key), str(value))


def _provider_count(api):
    try:
        return len(api.store.list_providers() or [])
    except Exception:
        return 0


def _manual_source_mode(addon):
    return _setting(
        addon, 'playback_open_mode', 'Choose from sources'
    ).strip().lower() != 'best quality automatically'


def status_text(api):
    """Return a compact, privacy-safe configuration summary."""
    addon = api.ADDON
    source_mode = (
        api.tr('اختيار المصدر يدوياً') if _manual_source_mode(addon)
        else api.tr('تشغيل أفضل جودة تلقائياً')
    )
    subtitle_mode = _setting(addon, 'subtitle_search_mode', 'Play with subtitles')
    subtitle_label = (
        api.tr('بدون ترجمة') if subtitle_mode.strip().lower() == 'play only'
        else api.tr('قبل التشغيل') if 'attach' in subtitle_mode.strip().lower()
        else api.tr('مع التشغيل')
    )
    try:
        timeout = max(10, min(20, int(float(
            _setting(addon, 'subtitle_timeout', '15')))))
    except Exception:
        timeout = 15
    return api.tr('%d مصدر • %s • الترجمة %s (%d ثانية)') % (
        _provider_count(api), source_mode, subtitle_label, timeout)


def render(api):
    """The same organized categories as every add-on settings entry point."""
    from ..settings_sections import SECTIONS, DESCRIPTIONS
    from ..i18n import is_english
    english=is_english()
    for key,ar,en,action,folder,icon,asset,skin_id in SECTIONS:
        title=en if english else ar
        description=DESCRIPTIONS[key][1 if english else 0]
        api.add_item(title,api.build_url(action=action),is_folder=folder,
                     art=api.root_art('settings'),info={'title':title,'plot':description})
    return api.end_dir(content='files',cache=False)


def connections(api):
    add = api.add_item
    url = api.build_url
    art = api.root_art
    add(api.tr('مصادر Stremio'), url(action='providers'), art=art('providers'),
        info={'title': api.tr('مصادر Stremio'),
              'plot': api.tr('إضافة وتعديل واختبار وترتيب مصادر الستريم.')})
    add(api.tr('Plex / Emby / Jellyfin'), url(action='media_libraries_menu'), art=art('plex'),
        info={'title': api.tr('مكتبات الوسائط'),
              'plot': api.tr('ربط وإدارة مكتبات Plex وEmby وJellyfin.')})
    return api.end_dir(content='files', cache=False)


def playback_quality(api):
    add = api.add_item
    url = api.build_url
    art = api.root_art
    add(api.tr('طريقة التشغيل'), url(action='setup_playback'), is_folder=False, art=art('play'))
    add(api.tr('الجودة'), url(action='setup_quality'), is_folder=False, art=art('catalogs'))
    add(api.tr('شارات الجودة'), url(action='badges_url'), is_folder=False, art=art('settings'),
        info={'title': api.tr('شارات الجودة'),
              'plot': api.tr('اختيار مجموعة الشارات وضبط عرض 4K وDV وHDR وAtmos.')})
    return api.end_dir(content='files', cache=False)


def performance(api):
    add = api.add_item
    url = api.build_url
    art = api.root_art
    add(api.tr('تفعيل إعداد الخفة الموصى به'), url(action='setup_stability'),
        is_folder=False, art=art('settings'))
    add(api.tr('مسح الكاش'), url(action='clear_cache'), is_folder=False, art=art('refresh'))
    add(api.tr('حالة وتشخيص Dex Hub'), url(action='setup_diagnostics'),
        is_folder=False, art=art('settings'))
    add(api.tr('نسخ احتياطي / استعادة'), url(action='config_backup_menu'), art=art('settings'))
    return api.end_dir(content='files', cache=False)


def playback(api):
    labels = [
        api.tr('اختيار المصدر في كل مرة (موصى به)'),
        api.tr('استخدام نفس المصدر فقط'),
        api.tr('الاختيار عبر TMDb Helper'),
        api.tr('تشغيل أفضل جودة تلقائياً'),
    ]
    idx = api.xbmcgui.Dialog().select(api.tr('طريقة التشغيل'), labels)
    if idx < 0:
        return False
    if idx == 3:
        confirmed = api.xbmcgui.Dialog().yesno(
            api.tr('تأكيد التشغيل التلقائي'),
            api.tr('سيبدأ أفضل مصدر مباشرةً من دون نافذة اختيار. هل تريد تفعيله؟'),
        )
        if not confirmed:
            return False
        _set_many(api.ADDON, {
            'source_resolution_mode': 'DexHub picker',
            'playback_open_mode': 'Best quality automatically',
            'remember_last_source': 'false',
        })
    else:
        resolution = ('DexHub picker', 'Same source', 'TMDb Helper')[idx]
        _set_many(api.ADDON, {
            'source_resolution_mode': resolution,
            'playback_open_mode': 'Choose from sources',
            'remember_last_source': 'false',
        })
    api.notify(api.tr('تم حفظ طريقة التشغيل'))
    return True


def subtitles(api):
    mode_labels = [
        api.tr('تشغيل بدون ترجمة'),
        api.tr('تشغيل مع ترجمة'),
        api.tr('إرفاق ترجمة قبل التشغيل'),
    ]
    mode_idx = api.xbmcgui.Dialog().select(api.tr('وضع الترجمة'), mode_labels)
    if mode_idx < 0:
        return False
    timeout_labels = [
        api.tr('10 ثوانٍ'), api.tr('15 ثانية (موصى به)'), api.tr('20 ثانية')
    ]
    timeout_idx = api.xbmcgui.Dialog().select(
        api.tr('مدة البحث المشتركة'), timeout_labels)
    if timeout_idx < 0:
        return False
    _set_many(api.ADDON, {
        'subtitle_search_mode': ('Play only', 'Play with subtitles', 'Attach before playing')[mode_idx],
        'subtitle_timeout': ('10', '15', '20')[timeout_idx],
    })
    api.notify(api.tr('تم حفظ إعداد الترجمة'))
    return True


def quality(api):
    values = (
        'Balanced (recommended)', 'Best', 'Data saver', 'Custom', 'Like Nuvio (add-on order)'
    )
    labels = [
        api.tr('متوازن (موصى به)'), api.tr('أفضل جودة'),
        api.tr('توفير البيانات'), api.tr('مخصص'),
        api.tr('مثل Nuvio: ترتيب الإضافة (AIOStreams كما رتبها)'),
    ]
    idx = api.xbmcgui.Dialog().select(api.tr('ملف الجودة'), labels)
    if idx < 0:
        return False
    api.ADDON.setSetting('quality_profile', values[idx])
    api.notify(api.tr('تم حفظ ملف الجودة'))
    if idx == 3:
        __import__('xbmc').executebuiltin('RunPlugin(plugin://plugin.video.dexhub/?action=preferences_sources)')
    return True


def stability(api):
    confirmed = api.xbmcgui.Dialog().yesno(
        api.tr('ثبات الأجهزة الضعيفة'),
        api.tr('تفعيل الوضع الخفيف وإيقاف نافذة الانتظار والكاش المسبق للحلقة التالية والعمل المتوازي الثقيل؟ شارات الجودة تبقى على اختيارك ولا يتم تعطيلها.'),
    )
    if not confirmed:
        return False
    _set_many(api.ADDON, {
        'lightweight_mode': 'true',
        'show_playback_waiter': 'false',
        'pre_cache_next_episode': 'false',
        'streams_full_parallel_scan': 'false',
        'verbose_logging': 'false',
    })
    api.notify(api.tr('تم تفعيل إعداد الثبات'))
    return True


def diagnostics(api):
    try:
        from ..runtime_tasks import active_count
        optional_jobs = active_count()
    except Exception:
        optional_jobs = 0
    details = [
        api.tr('الإصدار: %s') % api.ADDON.getAddonInfo('version'),
        api.tr('المصادر: %d') % _provider_count(api),
        api.tr('المهام الخلفية الاختيارية: %d') % int(optional_jobs),
        api.tr('الوضع: اختيار يدوي') if _manual_source_mode(api.ADDON)
        else api.tr('الوضع: تشغيل تلقائي'),
        api.tr('الكاش المسبق للحلقة التالية: متوقف')
        if _setting(api.ADDON, 'pre_cache_next_episode', 'false').lower() != 'true'
        else api.tr('الكاش المسبق للحلقة التالية: مفعّل'),
    ]
    api.xbmcgui.Dialog().ok(api.tr('حالة Dex Hub'), '\n'.join(details))
    return True
