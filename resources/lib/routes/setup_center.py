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
    """Single, compact Settings home. Detailed controls live one level down."""
    add = api.add_item
    url = api.build_url
    art = api.root_art

    # Nuvio is first-class: it can mirror the exact Home order/catalogs plus progress/library.
    try:
        from resources.lib.dexhub import nuvio_stremio_sync as _sync
        linked = _sync.Nuvio.is_linked()
        profile = _sync.Nuvio.profile_name()
    except Exception:
        linked, profile = False, ''
    status = (api.tr('متصل ✓') + ((' • ' + profile) if profile else '')) if linked else api.tr('غير متصل')
    add('Nuvio • %s' % status, url(action='nuvio_sync_menu'),
        art=art('sync'),
        info={'title': 'Nuvio',
              'plot': api.tr('ربط الحساب من موقع Nuvio ثم مزامنة نفس الهوم والكتالوجات والترتيب.')})

    add('Nuvio Home / Fallback', url(action='collection_settings_hub'),
        art=art('movie_collections'),
        info={'title': 'Nuvio Home / Fallback',
              'plot': api.tr('Nuvio هو المصدر الأساسي؛ Self‑Hosted والملفات المحلية احتياطية فقط.')})

    add(api.tr('المصادر والمكتبات'), url(action='setup_connections'),
        art=art('providers'),
        info={'title': api.tr('المصادر والمكتبات'),
              'plot': api.tr('إضافات Stremio ومكتبات Plex وEmby وJellyfin في صفحة واحدة.')})

    add(api.tr('التشغيل والجودة'), url(action='setup_playback_quality'),
        art=art('play'),
        info={'title': api.tr('التشغيل والجودة'),
              'plot': api.tr('طريقة اختيار الستريم، ملف الجودة وشارات DV/HDR/Atmos.')})

    add(api.tr('الميتاداتا والصور'), url(action='meta_sources_menu'),
        art=art('tmdb'),
        info={'title': api.tr('الميتاداتا والصور'),
              'plot': api.tr('TMDb Helper، البوسترات، Fanart، ClearLogo والاستوديو.')})

    add(api.tr('الترجمة'), url(action='setup_subtitles'),
        is_folder=False, art=art('subtitles'),
        info={'title': api.tr('الترجمة'),
              'plot': api.tr('وضع الترجمة ومدة البحث واللغات.')})

    add(api.tr('المظهر والواجهة'), url(action='theme_select'),
        is_folder=False, art=art('settings'),
        info={'title': api.tr('المظهر والواجهة'),
              'plot': api.tr('الثيم وطريقة العرض بما فيها Arctic Fuse 3.')})

    add(api.tr('الأداء والصيانة'), url(action='setup_performance'),
        art=art('settings'),
        info={'title': api.tr('الأداء والصيانة'),
              'plot': api.tr('الوضع الخفيف، الكاش، التشخيص والنسخ الاحتياطي.')})

    add(api.tr('حسابات أخرى'), url(action='sync_accounts_menu'),
        art=art('sync'),
        info={'title': api.tr('حسابات أخرى'),
              'plot': api.tr('Stremio وTrakt وSimkl وMDBList. Nuvio له صفحة مستقلة أعلاه.')})

    add(api.tr('متقدم'), url(action='open_settings'),
        is_folder=False, art=art('settings'),
        info={'title': api.tr('متقدم'),
              'plot': api.tr('الخيارات النادرة والتقنية في نافذة Kodi الأصلية.')})
    return api.end_dir(content='files', cache=False)


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
        api.open_settings_action()
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
