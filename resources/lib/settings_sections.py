"""Canonical sections for add-on cards, directories and native skin settings.

The skin XML is generated at packaging time. Opening settings imports only
this small declaration; it never builds skin controls or reloads a skin.
"""
# key, Arabic, English, action, folder, menu icon, asset, skin item id
SECTIONS = (
    ('appearance','المظهر والثيم','Appearance & Theme','simple_appearance',True,'brush','dexhub_infinity.png',1),
    ('home','الرئيسية والويدجت','Home & Widgets','simple_home_options',False,'table-cells-large','collection_movies.png',2),
    ('playback','المشغّل وطريقة التشغيل','Player & Playback','setup_playback_quality',True,'circle-play','play.png',4),
    ('badges','رموز الجودة','Quality Badges','badges_section',False,'sliders','settings.png',11),
    ('metadata','الميتاداتا والصور','Metadata & Artwork','simple_metadata',True,'image','tmdb.png',7),
    ('accounts','الحسابات والمصادر','Accounts & Sources','home_setup',False,'user-gear','sync_accounts.png',5),
    ('library','المكتبة واللستات','Library & Lists','simple_favorites',False,'list-check','root_favorites.png',6),
    ('iptv','IPTV وVOD والسيرفرات','IPTV, VOD & Servers','simple_sources_libraries',True,'livetv','root_sources.png',8),
    ('subtitles','الترجمة','Subtitles','setup_subtitles',False,'comment-dots','settings.png',9),
    ('performance','الأداء والصيانة','Performance & Maintenance','setup_performance',True,'gears','settings.png',10),
)
EXTRA = (
    ('quick','الإعداد السريع','Quick Setup','simple_quick_setup',False,'update','settings.png',0),
    ('advanced','الإعدادات المتقدمة','Advanced Settings','open_settings',False,'sliders','settings.png',3),
)
DESCRIPTIONS = {
    'appearance':('الثيم والألوان والمؤشر','Theme, colours and focus'),
    'home':('الويدجت والتريلر والبانر والحركة','Widgets, trailers, banners and motion'),
    'playback':('طريقة التشغيل وآخر نسخة والجودة','Playback method, last version and quality'),
    'badges':('معاينة الرموز وحجمها ومكانها','Badge preview, size and placement'),
    'metadata':('مصدر البيانات والبوسترات واللغة','Metadata source, posters and language'),
    'accounts':('الربط وإدارة المصادر','Connect accounts and manage sources'),
    'library':('اللستات حسب المصدر والتتبع والمتابعة','Lists by source, tracking and progress'),
    'iptv':('الأقسام والقنوات وVOD ودليل البرامج','Sections, channels, VOD and programme guide'),
    'subtitles':('البحث والاختيار والمزامنة','Search, selection and synchronisation'),
    'performance':('الخفة والكاش والتشخيص والنسخ الاحتياطي','Light mode, cache, diagnostics and backup'),
    'quick':('الربط والإعداد المبدئي','Connect accounts and set up'),
    'advanced':('خيارات كودي والتكاملات الإضافية','Kodi options and additional integrations'),
}


def all_sections():
    return SECTIONS + EXTRA


# Opening or editing preferences does not mean viewing progress changed.
# Settings changes themselves invalidate the rows that actually depend on them.
SETTING_ACTIONS = frozenset(row[3] for row in all_sections()) | frozenset((
    'ui_option', 'ui_options_publish', 'playback_mode', 'simple_source_behavior', 'source_addons',
    'setup_playback', 'setup_quality', 'simple_episode_controls', 'player_appearance', 'osd_buttons',
    'player_badges', 'player_badge', 'player_badge_packs', 'badges_url',
    'simple_metadata_language', 'meta_source_picker', 'simple_tmdb_api_key',
    'setup_stability', 'simple_watching', 'simple_trakt'))
