"""Canonical sections for add-on cards, directories and native skin settings.

The skin XML is generated at packaging time. Opening settings imports only
this small declaration; it never builds skin controls or reloads a skin.
"""
# key, Arabic, English, action, folder, menu icon, asset, skin item id
SECTIONS = (('theme', 'الثيم والمظهر', 'Theme & appearance', 'control_section_theme', False, 'palette', 'settings.png', 42), ('home', 'الرئيسية والصفوف', 'Home & rows', 'control_section_home', False, 'house', 'settings.png', 43), ('trailers', 'التريلر', 'Trailers', 'control_section_trailers', False, 'clapperboard', 'settings.png', 41), ('player', 'المشغّل', 'Player', 'control_section_player', False, 'circle-play', 'settings.png', 40), ('metadata', 'الميتاداتا والصور', 'Metadata & artwork', 'control_section_metadata', False, 'image', 'settings.png', 44), ('sources', 'مصادر التشغيل', 'Stream sources', 'control_section_sources', False, 'server', 'settings.png', 45), ('subtitles', 'الترجمة', 'Subtitles', 'control_section_subtitles', False, 'comment-dots', 'settings.png', 46), ('accounts', 'الحسابات والمزامنة', 'Accounts & sync', 'control_section_accounts', False, 'user-gear', 'settings.png', 47), ('library', 'المكتبة والمتابعة', 'Library & progress', 'control_section_library', False, 'list-check', 'settings.png', 48), ('live', 'IPTV والسيرفرات', 'IPTV & servers', 'control_section_live', False, 'display', 'settings.png', 49), ('skin', 'السكين واللغة', 'Skin & language', 'control_section_skin', False, 'brush', 'settings.png', 50), ('maintenance', 'الصيانة والنسخ الاحتياطي', 'Maintenance & backup', 'control_section_maintenance', False, 'gears', 'settings.png', 51), ('system', 'كودي والخروج', 'Kodi & exit', 'control_section_system', False, 'power-off', 'settings.png', 52))
EXTRA = ()
DESCRIPTIONS = {'player': ('التشغيل وأزرار المشغّل وشكله والحلقة التالية', 'Playback, player controls, appearance and next episode'), 'trailers': ('التشغيل التلقائي والصوت والجودة وملء الشاشة', 'Automatic trailers, sound, quality and fullscreen'), 'theme': ('الثيم والألوان والزجاج والتحديد', 'Theme, colours, glass and focus'), 'home': ('التخطيط والتنقّل والبطاقات والبانر', 'Layout, navigation, cards and featured banner'), 'metadata': ('المصادر واللغة والبوسترات والشعارات والتقييمات', 'Sources, language, posters, logos and ratings'), 'sources': ('إضافات البحث والفلترة وترتيب روابط التشغيل', 'Source add-ons, filters and stream ordering'), 'subtitles': ('البحث واللغة والتصفية والمزامنة', 'Search, language, filters and synchronization'), 'accounts': ('الربط وإدارة الحسابات والمزامنة والمشاهدة الجماعية', 'Connect accounts, sync and watch parties'), 'library': ('المفضلة والقوائم وسجل المشاهدة والحلقات', 'Favourites, lists, watch history and episodes'), 'live': ('القنوات وVOD ودليل البرامج ومكتبات السيرفرات', 'Live TV, VOD, programme guide and server libraries'), 'skin': ('السكين المؤقت والتبديل واللغة والحجم والأصوات', 'Temporary skin, switching, language, size and sounds'), 'maintenance': ('الأداء والكاش والتشخيص والنسخ والاستعادة', 'Performance, cache, diagnostics, backup and restore'), 'system': ('إضافات كودي وإعداداته والخروج وإعادة التشغيل', 'Kodi add-ons, settings, exit and restart')}


def all_sections():
    return SECTIONS + EXTRA


# Opening or editing preferences does not mean viewing progress changed.
# Settings changes themselves invalidate the rows that actually depend on them.
SETTING_ACTIONS = frozenset(row[3] for row in all_sections()) | frozenset((
    'av_settings', 'interface_fonts', 'track_preferences', 'ui_option', 'ui_options_publish', 'playback_mode', 'simple_source_behavior', 'source_addons',
    'setup_playback', 'setup_quality', 'simple_episode_controls', 'player_appearance', 'osd_buttons',
    'player_badges', 'player_badge', 'player_badge_packs', 'badges_url',
    'simple_metadata_language', 'meta_source_picker', 'simple_tmdb_api_key',
    'setup_stability', 'simple_watching', 'simple_trakt'))
