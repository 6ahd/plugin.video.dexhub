# -*- coding: utf-8 -*-
"""The Dex Hub control centre (v5.10.198): the Home's menu, by section.

skin.dexhub's Menu and the add-on Home's Menu open it (open_centre). The
sections are on the left: Home, Metadata, Accounts and sources, Player,
Look and System; the section's entries are cards on the right, each with
its current value and a line on what it does.

  open    the centre closes and its key goes back to the caller
          (skinui.actions.menu, homeui.window._menu_popup), which opens the
          page as before; keys the caller does not know run their route
          (launch)
  pick    a choice over the centre; the card shows the new value
  toggle  on or off at once (a switch on the card)
  run     a dialog of its own over the centre (the metadata source, the
          TMDb key, the focus colour, clearing the metadata cache)

Values are read from one settings snapshot as the centre opens; the few
that need more (the linked accounts, an add-on's name) are read on a thread
and fill their cards a moment later.

Layout: dexhub_control.xml (gen_control_xml.py).
"""
import os
import threading

import xbmc
import xbmcaddon
import xbmcgui

ADDON_ID = 'plugin.video.dexhub'
SECTIONS, ENTRIES = 100, 200
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
LAST_PROP = 'dexhub.control.section'

# (key, kind, icon, title, line, where); where: '' everywhere, 'skin' in
# skin.dexhub, 'addon' outside it, 'player' in skin.dexhub or Arctic Fuse 3,
# 'coreelec' on CoreELEC
SECTION_LIST = (
    ('home', 'house', 'الرئيسية', 'ترتيب صفحتك وما يظهر فيها', (
        ('phone', 'open', 'mobile', 'إعداد من الجوال', 'امسح الباركود وجهّز كل شي من جوالك', ''),
        ('search', 'open', 'search', 'بحث', 'أفلام ومسلسلات وأشخاص من كل مصادرك', ''),
        ('home_layout_mode', 'pick', 'display', 'وضع الواجهة', 'صف واسع أو صفّان أو Spotlight مع بوسترات', 'skin'),
        ('nav_position', 'pick', 'list-ul', 'مكان تبويبات الأقسام', 'خيار مستقل: شريط علوي أو قائمة جانبية', 'skin'),
        ('nav_icons', 'toggle', 'bars', 'القائمة الجانبية أيقونات فقط', 'ما تنفتح بالكلمات لما تروح لها', 'skin'),  # v5.10.288
        ('nav_autohide', 'toggle', 'eye-slash', 'إخفاء الشريط العلوي عند التمرير', 'يختفي لما تنزل للصفوف ويرجع لما تطلع', 'skin'),
        ('layout', 'open', 'table-cells-large', 'ترتيب الصفحة الرئيسية', 'الصفوف وترتيبها وشكل كل صف', ''),
        ('new_rows', 'toggle', 'circle-xmark', 'صفوف جديدة مخفية',
         'تنتظرك في ترتيب الصفحة', ''),
        ('group_recs', 'toggle', 'layer-group', 'تجميع التوصيات',
         '«لأنك شاهدت» في صف واحد', ''),
        ('community', 'open', 'users', 'Dex Community', 'كتالوجات جاهزة تضيفها بضغطة', ''),
        ('refresh', 'open', 'arrows-rotate', 'تحديث الرئيسية', 'جلب الصفوف من جديد', ''),
        ('size', 'open', 'up-right-and-down-left-from-center', 'حجم الواجهة', 'عادي أو كبير أو كبير جداً', ''),
        ('trailers', 'pick', 'clapperboard', 'التريلرات', 'متى يبدأ تريلر العمل المحدد', ''),
        ('trailer_sound', 'toggle', 'film', 'صوت التريلر', 'التريلر بصوته أو صامت', ''),
        ('trailer_volume', 'pick', 'film', 'مستوى صوت التريلر', 'من 0 إلى 100؛ يرجع صوت المشاهدة بعد التريلر', ''),
        ('screensaver', 'open', 'moon', 'شاشة التوقف', 'الخلفيات ووقت التشغيل', ''),
    )),
    ('following', 'list-check', 'مكتبتي', 'المحفوظات وقوائم المشاهدة والتقدم', (
        ('library', 'open', 'list-check', 'فتح مكتبتي', 'كل المحفوظات وقوائم المشاهدة حسب المصدر', ''),
        ('follow', 'open', 'clock-rotate-left', 'نشاط المشاهدة', 'متابعة المشاهدة والحلقة التالية والقادم', ''),
        ('library_split', 'toggle', 'table-cells-large', 'فصل الأفلام والمسلسلات في المكتبة', 'صف للأفلام وصف للمسلسلات لكل مصدر', ''),
        ('watching', 'open', 'clock-rotate-left', 'متابعة المشاهدة والحلقة التالية',
         'المصدر وحسابات التقدم وشكل البطاقات', ''),
        ('continue_split', 'run', 'table-cells-large', 'متابعة المشاهدة في الرئيسية',
         'صف واحد للكل أو صفين: أفلام ومسلسلات', ''),
        ('follow_order', 'pick', 'sliders', 'ترتيب متابعة المشاهدة', 'في الرئيسية وفي متابعتي', ''),
        ('episode_art', 'pick', 'image', 'صور الحلقات', 'صورة الحلقة نفسها بدل صورة المسلسل', ''),
        ('episode_titles', 'pick', 'tags', 'أسماء الحلقات', 'اسم الحلقة بدل «الحلقة 3»', ''),
        ('follow_merge', 'toggle', 'layer-group', 'دمج الخدمات', 'العمل مرة وحدة ولو كان في أكثر من خدمة', ''),
        ('lib_sort', 'pick', 'list-ul', 'ترتيب المفضلة وقوائم المشاهدة', 'في المكتبة وفي متابعتي', ''),
        ('trakt_smart', 'toggle', 'arrows-rotate', 'مزامنة Trakt الذكية', 'يجلب من Trakt بس لما يتغير شي فيه', ''),
        ('reminders', 'toggle', 'clock-rotate-left', 'تذكير بالحلقات الجديدة', 'إشعار يوم ما تنزل حلقة من مسلسل تتابعه', ''),
        ('credits_panel', 'pick', 'forward-step', 'شاشة نهاية الحلقة', 'الحلقة التالية أو أعمال مشابهة مع بداية التترات', ''),
    )),
    ('metadata', 'database', 'الميتاداتا', 'من وين تجي البيانات والصور وبأي لغة', (
        ('meta_source', 'run', 'database', 'مصدر الميتاداتا والصور', 'للكتالوجات وصفحات الأعمال', ''),
        ('meta_lang', 'pick', 'language', 'لغة الميتاداتا', 'لغة القصص والشعارات', ''),
        ('server_meta', 'pick', 'server', 'ميتاداتا السيرفرات', 'Plex وEmby وJellyfin وSilo', ''),
        ('tmdb_key', 'run', 'key', 'مفتاح TMDb', 'مفتاحك الخاص يسرّع الصفحات', ''),
        ('posters', 'open', 'images', 'مصدر البوسترات', 'بوسترات الكتالوجات كلها', ''),
        ('logos', 'open', 'image', 'الشعارات والخلفيات', 'Fanart.tv وClearLogo', ''),
        ('ratings', 'toggle', 'star', 'تعزيز التقييمات', 'التقييمات وشعار الشبكة', ''),
        ('meta_refresh', 'run', 'arrows-rotate', 'مسح كاش الميتاداتا', 'البيانات والصور من جديد', ''),
    )),
    ('accounts', 'user-gear', 'الحسابات والمصادر', 'Nuvio وTrakt والسيرفرات والإضافات', (
        ('phone', 'open', 'mobile', 'إعداد من الجوال', 'تربط حساباتك وتضيف مصادرك من الجوال', ''),
        ('setup', 'open', 'user-gear', 'الحسابات', 'Nuvio وStremio وTrakt والسيرفرات', ''),
        ('sources', 'open', 'link', 'صفحة المصادر', 'كل مصدر وحالته في صفحة وحدة', ''),
        ('add_source', 'open', 'circle-plus', 'إضافة مصدر', 'رابط إضافة Stremio أو Nuvio', ''),
        ('nuvio', 'open', 'id-card', 'مزامنة Nuvio', 'الهوم والمجموعات ومتابعة المشاهدة', ''),
        ('trakt', 'open', 'clock-rotate-left', 'Trakt', 'سجل المشاهدة والمزامنة', ''),
    )),
    ('player', 'circle-play', 'المشغل', 'التشغيل والرموز والترجمة', (
        ('player', 'open', 'circle-play', 'خيارات المشغل', 'الشعار والتقييمات وسطر المعلومات', 'player'),
        ('badges_visible', 'toggle', 'sliders', 'إظهار رموز الجودة في المشغّل', 'إخفاؤها أو إرجاعها دون تغيير حزمة الرموز', ''),
        ('badges_brightness', 'pick', 'sliders', 'وضوح رموز الجودة', 'واضح بلا تعتيم أو هادئ', ''),
        ('badges', 'open', 'tags', 'رموز الجودة', 'في المشغل والسيرفرات والروابط', ''),
        ('playback', 'open', 'gauge-high', 'طريقة التشغيل', 'أول رابط أو قائمة المصادر', ''),
        ('source_behavior', 'open', 'list-check', 'المصادر وسلوك التشغيل', 'الترتيب والفلترة والاستئناف', ''),
        ('source_addons', 'open', 'list-check', 'إضافات البحث عن المصادر', 'الإضافات اللي يسألها البحث', ''),
        ('subtitles', 'open', 'closed-captioning', 'الترجمة', 'اللغة والمزامنة التلقائية', ''),
        ('episodes', 'open', 'forward-step', 'الحلقات والتخطي', 'الحلقة التالية وتخطي المقدمة', ''),
    )),
    ('look', 'palette', 'المظهر', 'السكين والألوان وشكل البطاقات', (
        ('focus_colour', 'run', 'palette', 'لون المؤشر', 'لون التحديد في السكين', 'skin'),
        ('card_style', 'pick', 'layer-group', 'عناوين البطاقات', 'على الصورة أو تحتها أو بدون', ''),
        # v5.10.286: the Home row's source line, also here beside the other card options
        ('row_source', 'toggle', 'tags', 'إظهار مصدر الصف', 'إظهار أو إخفاء From بجانب عنوان الصف', ''),
        ('focus_motion', 'pick', 'wand-magic-sparkles', 'حركة التحديد', 'كيف تتحرك البطاقة المحددة', ''),
        ('row_dim', 'pick', 'display', 'تعتيم الصفوف', 'الصفوف غير المحددة', ''),
        ('nav_names', 'toggle', 'list-ul', 'أسماء الأقسام', 'أسماء بجانب أيقونات الشريط', ''),
        ('season_names', 'toggle', 'tags', 'أسماء المواسم', 'الموسم 1: Murder House بدل الموسم 1', ''),
        ('skin_pick', 'open', 'brush', 'تغيير السكين', 'أي سكين مثبت على طول', ''),
        ('skin_back', 'open', 'arrow-rotate-left', 'الرجوع للسكين السابق', 'السكين اللي كان قبل Dex Hub', 'skin'),
        ('skin', 'open', 'brush', 'سكين Dex Hub', 'يصير Dex Hub سكين كودي', 'addon'),
        ('classic', 'open', 'list-ul', 'قائمة Dex Hub', 'كل أقسام الإضافة في قائمة', ''),
    )),
    ('customize', 'wand-magic-sparkles', 'تخصيص الواجهة · AF3', 'خيارات العرض والتفاصيل والحركة', (
        ('poster_scale', 'run', 'up-right-and-down-left-from-center', 'حجم البوستر المخصص', 'نسبة من 50% إلى 200% بخطوة 1%', 'skin'),
        ('home_layout_mode', 'pick', 'display', 'وضع الواجهة', 'Spotlight أو صفّان أو بوسترات', 'skin'),
        ('nav_position', 'pick', 'list-ul', 'مكان تبويبات الأقسام', 'القائمة والأدوات في مكان واحد', 'skin'),
        ('title_style', 'pick', 'image', 'عنوان العمل', 'شعار العمل أو نص', ''),
        ('background_style', 'pick', 'display', 'نمط الخلفية', 'سينمائي أو هادئ أو داكن', ''),
        ('row_source', 'toggle', 'tags', 'إظهار مصدر الصف', 'إظهار أو إخفاء From بجانب عنوان الصف', ''),
        ('focus_motion', 'pick', 'wand-magic-sparkles', 'حركة التحديد', 'التكبير والرفع والإضاءة', ''),
        ('row_dim', 'pick', 'display', 'تعتيم الصفوف', 'تحكم بوضوح الصفوف غير المحددة', ''),
        ('carousel', 'toggle', 'clapperboard', 'تدوير Spotlight', 'التنقل التلقائي بين الأعمال', ''),
        ('plot_scroll', 'toggle', 'sliders', 'تحريك النبذة', 'تمرير النبذة الطويلة', ''),
        ('hide_plot', 'toggle', 'sliders', 'إخفاء النبذة', 'واجهة أبسط مع عنوان العمل وتفاصيله', ''),
    )),
    ('system', 'gears', 'النظام', 'الإعدادات والتشخيص والتشغيل', (
        ('settings', 'open', 'sliders', 'إعدادات Dex Hub', 'مركز الإعداد', ''),
        ('kodi', 'open', 'kodi', 'إعدادات كودي', 'النظام والصوت والشاشة', ''),
        ('speed', 'open', 'bolt', 'وضع السرعة', 'إعداد الخفة للبحث والتشغيل بضغطة', ''),
        ('diagnostics', 'open', 'stethoscope', 'التشخيص', 'حالة المصادر والاتصال', ''),
        ('backup', 'open', 'box-archive', 'النسخ الاحتياطي', 'كل الإعداد أو أجزاء منه، وترجّعه متى ما تبي', ''),
        ('clear_cache', 'open', 'broom', 'مسح الكاش', 'كاش الصفحات والطلبات', ''),
        ('power', 'open', 'power-off', 'إيقاف التشغيل', 'إطفاء أو إعادة تشغيل الجهاز', ''),
        ('android', 'open', 'circle-dot', 'إعادة التشغيل إلى أندرويد', 'تشغيل أندرويد الجهاز', 'coreelec'),
    )),
)

# open entries the callers leave to their route
ROUTES = {
    'phone': 'phone_setup',
    'add_source': 'add_provider',
    'library': 'home_library',
    'follow': 'home_follow',
    'watching': 'simple_watching',
    'nuvio': 'simple_open_page&target=nuvio_sync_menu',
    'trakt': 'simple_trakt',
    'playback': 'playback_mode',
    'source_behavior': 'simple_source_behavior',
    'source_addons': 'source_addons',
    'subtitles': 'setup_subtitles',
    'episodes': 'simple_episode_controls',
    'posters': 'poster_pick_global',
    'logos': 'simple_fanarttv',
    'speed': 'setup_stability',
    'diagnostics': 'setup_diagnostics',
    'backup': 'backup_menu',
    'clear_cache': 'clear_cache',
}

TRAILER_STEPS = ((0, 'فوري'), (1, 'بعد ثانية'), (2, 'بعد ثانيتين'), (3, 'بعد 3 ثوانٍ'),
                 (5, 'بعد 5 ثوانٍ'), (8, 'بعد 8 ثوانٍ'))
# (setting, [(value, label)]) of the pick entries kept in one setting
PICKS = {
    'badges_brightness': ('player_badges_brightness', (('solid', 'واضح بلا تعتيم'), ('soft', 'هادئ'))),
    'title_style': ('homeui_title_style', (('logo', 'شعار العمل'), ('text', 'نص'))),
    'background_style': ('homeui_background_style', (('cinematic', 'سينمائي'), ('soft', 'هادئ'), ('dark', 'داكن'))),
    # v5.10.286: the two new Home layouts first (cinematic keeps the default: options[0]
    # is only the fallback label, the setting's default stays cinematic)
    'home_layout_mode': ('homeui_layout_mode', (('cinematic', 'هيرو وصفوف'), ('cinema', 'سينمائي ملء الشاشة'), ('shelf', 'رف بطاقات عريضة'),
                                                ('netflix', 'هيرو وصف واحد'), ('two_rows', 'هيرو مختصر وصفين'),
                                                ('spotlight_posters', 'Spotlight وبوسترات'))),
    'nav_position': ('homeui_nav_position', (('top', 'أعلى الشاشة'), ('side', 'قائمة جانبية'))),
    'trailer_volume': ('homeui_trailer_volume', tuple((str(v), str(v) + '%') for v in range(0,101,5))),
    'meta_lang': ('homeui_metadata_language', (('Auto', 'تلقائي (لغة الواجهة)'), ('Arabic', 'العربية'),
                                                ('English', 'English'))),
    'server_meta': ('server_metadata', (('0', 'من السيرفر أولاً'), ('1', 'من المصدر المختار'))),
    'card_style': ('homeui_card_style', (('Overlay', 'على الصورة'), ('Below', 'تحت الصورة'),
                                         ('None', 'بدون عناوين'))),
    'focus_motion': ('homeui_focus_motion', (('zoom', 'تكبير ناعم'), ('lift', 'رفع للأعلى'), ('pop', 'قفزة'),
                                             ('glow', 'إضاءة فقط'))),
    'row_dim': ('homeui_row_dim', (('light', 'خفيف'), ('off', 'بدون'), ('strong', 'قوي'))),
    # v5.10.208: the Following and library section
    'follow_order': ('follow_order', (('recent', 'آخر مشاهدة أولاً'), ('almost', 'الأقرب للنهاية أولاً'),
                                      ('oldest', 'الأقدم أولاً'))),
    'lib_sort': ('library_sort', (('added', 'وقت الإضافة'), ('title', 'الاسم'), ('year', 'السنة'))),
    'episode_art': ('continue_episode_art', (('off', 'صورة المسلسل'), ('started', 'الحلقات اللي بدأتها'),
                                             ('all', 'كل الحلقات'))),
    # v5.10.217: when the end-of-episode panel comes up
    'credits_panel': ('credits_panel', (('auto', 'مع التترات'), ('end', 'آخر الحلقة'), ('off', 'بدون'))),
    # v5.10.214
    'episode_titles': ('episode_titles', (('all', 'كل الحلقات'), ('started', 'الحلقات اللي بدأتها'),
                                          ('off', 'الرقم فقط'))),
}
PICK_LINES = {
    'server_meta': 'السيرفر يعطي العنوان والقصة والصور، والناقص مثل الشعار من مصدرك',
    'meta_lang': 'القصص والشعارات بهذه اللغة إذا كانت متوفرة',
    'follow_order': 'صف متابعة المشاهدة في الرئيسية وأول قسم في متابعتي',
    'lib_sort': 'المفضلة وقوائم المشاهدة في المكتبة وفي متابعتي',
    'episode_art': 'في متابعة المشاهدة والحلقة التالية. «الحلقات اللي بدأتها» يخلي الحلقة القادمة بصورة المسلسل حتى ما تنكشف',
    'credits_panel': 'الفيديو يصغر في الزاوية وتطلع الحلقة التالية بعد عد تنازلي، وللأفلام أعمال مشابهة. «مع التترات» يبدأ مع فصل التترات أو قبل النهاية بقليل',
    'episode_titles': 'في البطاقات وقائمة حلقات الموسم. «الحلقات اللي بدأتها» يخلي اللي ما شفتها بالرقم حتى ما ينكشف شي',
}
TOGGLES = {'library_split': ('library_split_media', 'true'), 'badges_visible': ('player_badges_visible', 'true'),
           'row_source': ('homeui_show_row_source', 'true'), 'plot_scroll': ('homeui_plot_scroll', 'true'),
           'hide_plot': ('homeui_hide_plot', 'false'), 'nav_icons': ('homeui_nav_icons_only', 'false'), 'nav_autohide': ('homeui_nav_autohide', 'false'), 'carousel': ('homeui_carousel', 'true'),'trailer_sound': ('homeui_trailer_sound', 'true'), 'ratings': ('metadata_badges', 'true'),
           'nav_names': ('homeui_nav_names', 'false'), 'follow_merge': ('follow_merge', 'true'),
           'trakt_smart': ('trakt_smart_sync', 'true'), 'season_names': ('season_names', 'true'),
           'reminders': ('episode_reminders', 'true'),
           'new_rows': ('home_hide_new_rows', 'false'), 'group_recs': ('home_group_recs', 'false')}
SIZES = {'': 'عادي', 'large': 'كبير', 'xlarge': 'كبير جداً',
         'normal': 'عادي', 'extra large': 'كبير جداً'}
_SETTINGS = ('homeui_nav_icons_only', 'homeui_nav_autohide', 'library_split_media', 'player_badges_visible', 'player_badges_brightness', 'homeui_poster_scale', 'homeui_show_row_source', 'homeui_title_style', 'homeui_background_style', 'homeui_plot_scroll', 'homeui_hide_plot', 'homeui_carousel', 'homeui_layout_mode', 'homeui_nav_position', 'homeui_metadata_language', 'server_metadata', 'homeui_card_style', 'homeui_focus_motion',
             'homeui_row_dim', 'homeui_trailer_sound', 'metadata_badges', 'homeui_nav_names', 'homeui_trailers',
             'homeui_trailer_delay', 'homeui_trailer_volume', 'homeui_size', 'global_meta_source_id', 'global_meta_source_all',
             'tmdb_api_key', 'global_poster_source_id', 'follow_order', 'library_sort', 'follow_merge',
             'trakt_smart_sync', 'continue_episode_art', 'season_names', 'episode_titles', 'episode_reminders', 'credits_panel',
             'home_hide_new_rows', 'home_group_recs')


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _where():
    """What this place offers: skin.dexhub, Arctic Fuse 3, CoreELEC."""
    skin = xbmc.getSkinDir()
    try:
        from .. import power
        coreelec = bool(power.coreelec())
    except Exception:
        coreelec = False
    return {'skin': skin == 'skin.dexhub', 'addon': skin != 'skin.dexhub',
            'player': skin in ('skin.dexhub', 'skin.arctic.fuse.3'), 'coreelec': coreelec, '': True}


def sections():
    """[(id, icon, title, line, [entries])] that apply here."""
    place = _where()
    out = []
    for sid, icon, title, line, entries in SECTION_LIST:
        shown = [e for e in entries if place.get(e[5], False)]
        if shown:
            out.append((sid, icon, title, line, shown))
    return out


def launch(key):
    """Run an open entry the caller does not handle itself (its route)."""
    route = ROUTES.get(key)
    if route:
        xbmc.executebuiltin('RunPlugin(plugin://%s/?action=%s)' % (ADDON_ID, route))
        return True
    return False


def write(setting, value):
    """One setting written the way the centre writes it (v5.10.286: shared
    with the phone setup page)."""
    if setting in ('homeui_layout_mode', 'homeui_nav_position'):
        from ..home_preferences import write_setting
        write_setting(setting, value)
    elif setting in ('homeui_trailer_mode', 'homeui_trailer_volume', 'homeui_trailers', 'homeui_trailer_sound', 'homeui_trailer_delay'):
        from ..trailer_preferences import write_setting
        write_setting(setting, value)
    else:
        _addon().setSetting(setting, value)
    try:
        from ..settings_cache import invalidate
        invalidate()
        from ..ui_preferences import publish
        publish()
    except Exception:
        pass


def phone_values():
    """v5.10.286: the current value of every pick and toggle entry, for the
    phone setup page ({entry key: value})."""
    addon = _addon()
    out = {}
    for key, (setting, options) in PICKS.items():
        current = (addon.getSetting(setting) or options[0][0]).lower()
        out[key] = next((v for v, _l in options if v.lower() == current), options[0][0])
    for key, (setting, default) in TOGGLES.items():
        out[key] = (addon.getSetting(setting) or default).lower() == 'true'
    try:
        from .layout import continue_is_split
        out['continue_split'] = bool(continue_is_split())
    except Exception:
        out['continue_split'] = False
    return out


def apply_entry(key, value):
    """v5.10.286: a pick or toggle entry set to value, with the same follow-ups
    as on the TV. Returns True when the rows should be read again once the
    caller's dialogs close (the centre's refresh_after)."""
    if key in PICKS:
        setting, options = PICKS[key]
        value = str(value)
        if value not in [v for v, _l in options]:
            raise ValueError('bad value')
        write(setting, value)
        if key == 'episode_titles':
            try:
                from .. import episode_titles
                episode_titles.forget()
                import time as _time
                xbmcgui.Window(10000).setProperty('dhs.tp.rev.episodes', '%d' % int(_time.time() * 1000))
            except Exception:
                pass
        if key == 'server_meta':
            try:
                from ..meta_policy import refresh_server_rows
                refresh_server_rows()
            except Exception:
                pass
        return key in ('follow_order', 'lib_sort', 'episode_art', 'episode_titles')
    if key in TOGGLES:
        setting, default = TOGGLES[key]
        if isinstance(value, str):
            value = value.strip().lower() == 'true'
        was_on = (_addon().getSetting(setting) or default).lower() == 'true'
        write(setting, 'true' if value else 'false')
        if key in ('new_rows', 'group_recs', 'row_source'):
            # v5.10.236: the Home's rows again with the new choice
            # (v5.10.286: and with or without their source line)
            from .layout import _skin_follows
            if key == 'new_rows' and was_on and not value:
                # turned off: turned on again later, it starts from the rows then shown
                try:
                    import xbmcvfs
                    from .layout import Layout
                    Layout(os.path.join(xbmcvfs.translatePath(_addon().getAddonInfo('profile')),
                                        'homeui')).forget_known()
                except Exception:
                    pass
            _skin_follows()
        return key == 'library_split'
    if key == 'continue_split':
        from .layout import continue_split
        if isinstance(value, str):
            value = value.strip().lower() == 'true'
        continue_split(bool(value))
        return True
    raise ValueError('unknown entry')


class ControlCentre(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(ControlCentre, self).__init__(*args)
        self.tr = kwargs.get('tr') or (lambda text: text)
        self.media = kwargs.get('media') or ''
        self.version = kwargs.get('version') or ''
        self.menu_dir = kwargs.get('menu_dir') or ''
        self.sections = sections()
        self.choice = ''
        self.section = 0
        self.items = []
        self.snap = {}
        self.slow = {}
        self._lock = threading.Lock()
        self._closing = False
        self._shown = -1
        self.refresh_after = False

    # ------------------------------------------------------------ values
    def _read(self):
        addon = _addon()
        snap = {}
        for key in _SETTINGS:
            try:
                snap[key] = addon.getSetting(key) or ''
            except Exception:
                snap[key] = ''
        self.snap = snap

    def _value(self, key):
        """(value, switch) of an entry: switch 'on', 'off' or ''."""
        tr, snap = self.tr, self.snap
        if key in PICKS:
            setting, options = PICKS[key]
            current = snap.get(setting) or options[0][0]
            for value, label in options:
                if value.lower() == current.lower():
                    return tr(label), ''
            return tr(options[0][1]), ''
        if key in TOGGLES:
            setting, default = TOGGLES[key]
            on = (snap.get(setting) or default).lower() == 'true'
            return (tr('شغال') if on else tr('مطفأ')), ('on' if on else 'off')
        if key == 'continue_split':
            try:
                from .layout import continue_is_split
                split = continue_is_split()
            except Exception:
                split = False
            return (tr('صفين: أفلام ومسلسلات') if split else tr('صف واحد')), ''
        if key == 'trailers':
            if (snap.get('homeui_trailers') or 'true').lower() != 'true':
                return tr('متوقفة'), ''
            try:
                seconds = int(float(snap.get('homeui_trailer_delay') or 3))
            except ValueError:
                seconds = 3
            for value, label in TRAILER_STEPS:
                if value == seconds:
                    return tr(label), ''
            return tr('بعد %d ثوانٍ') % seconds, ''
        if key == 'poster_scale':
            # v5.10.288: the same reading as the size entry (clamped, the old skin size)
            from ..home_preferences import current_scale, scale_label
            return scale_label(tr, current_scale(setting=snap.get('homeui_poster_scale') or '')), ''
        if key == 'size':
            if xbmc.getSkinDir() == 'skin.dexhub':
                # v5.10.288: skin.dexhub's size is homeui_poster_scale (50-200 %).
                # This read Skin.String(DexHub.CardSize), which set_poster_scale
                # resets on every change, so the card always said Normal
                from ..home_preferences import current_scale, scale_label
                return scale_label(tr, current_scale(setting=snap.get('homeui_poster_scale') or '')), ''
            # v5.10.288: with its percentage, like the picker (ui_size.choose);
            # an empty setting is ui_size's default, Large
            from ..home_preferences import scale_label, UI_SIZE_PERCENT
            current = (snap.get('homeui_size') or 'Large').strip().lower()
            return scale_label(tr, UI_SIZE_PERCENT.get(current, 120)), ''
        if key == 'tmdb_key':
            return self.slow.get('tmdb_key', ''), ''
        if key == 'meta_source':
            return self.slow.get('meta_source', ''), ''
        if key == 'setup':
            return self.slow.get('setup', ''), ''
        if key == 'posters':
            return self.slow.get('posters', ''), ''
        if key == 'focus_colour':
            name = xbmc.getInfoLabel('Skin.String(dh.accent.name)') or ''
            return name or tr('لون الثيم'), ''
        return '', ''

    def _slow_values(self):
        """The values that need more than a setting (on a thread)."""
        tr = self.tr
        found = {}
        snap = dict(self.snap)
        try:
            from ..tmdb_direct import DEFAULT_API_KEY
            key = (snap.get('tmdb_api_key') or '').strip()
            found['tmdb_key'] = tr('مفتاحك ✓') if key and key != DEFAULT_API_KEY else tr('المفتاح المشترك')
        except Exception:
            pass
        found['meta_source'] = self._source_name(snap.get('global_meta_source_id') or '',
                                                 snap.get('global_meta_source_all') or 'true')
        poster = (snap.get('global_poster_source_id') or 'same').strip()
        found['posters'] = tr('مثل مصدر الميتاداتا') if poster in ('', 'same') else self._source_name(poster, 'true')
        found['setup'] = self._linked_line()
        if self._closing:
            return
        with self._lock:
            self.slow.update(found)
        self._paint(only_values=True)

    def _source_name(self, selected, apply_all):
        tr = self.tr
        selected = (selected or '').strip()
        if not selected or selected == 'auto' or apply_all != 'true':
            return tr('تلقائي (ذكي)')
        if selected == 'tmdb_helper':
            return 'TMDb Helper'
        if selected == 'native':
            return tr('مثل Nuvio (بيانات المصدر)')
        try:
            from ..dexhub import store
            row = store.get_provider(selected) or {}
        except Exception:
            row = {}
        return str(row.get('name') or selected)

    def _linked_line(self):
        """The linked accounts, by name (local reads: settings, window copies)."""
        names = []
        try:
            from .setup import _linked
        except Exception:
            return ''
        for key, name in (('nuvio', 'Nuvio'), ('stremio', 'Stremio'), ('trakt', 'Trakt'), ('plex', 'Plex'),
                          ('emby', 'Emby'), ('jellyfin', 'Jellyfin'), ('silo', 'Silo')):
            if self._closing:
                return ''
            try:
                if _linked(key):
                    names.append(name)
            except Exception:
                pass
        return u' \u2022 '.join(names) if names else self.tr('لا يوجد حساب مربوط')

    # ------------------------------------------------------------- drawing
    def onInit(self):
        tr = self.tr
        self.setProperty('cc.caption', tr('مركز التحكم'))
        self.setProperty('cc.version', 'Dex Hub %s' % self.version if self.version else 'Dex Hub')
        self.setProperty('cc.hint', tr('اضغط OK للفتح أو التغيير  \u2022  يسار للأقسام  \u2022  رجوع للإغلاق'))
        self._read()
        items = []
        for sid, icon, title, line, entries in self.sections:
            li = xbmcgui.ListItem(label=tr(title), label2=tr(line))
            li.setArt({'icon': self._icon(icon)})
            li.setProperty('sid', sid)
            items.append(li)
        try:
            control = self.getControl(SECTIONS)
            control.reset()
            control.addItems(items)
        except Exception:
            return
        want = xbmcgui.Window(10000).getProperty(LAST_PROP) or ''
        start = next((i for i, s in enumerate(self.sections) if s[0] == want), 0)
        control.selectItem(start)
        self._show_section(start)
        self.setFocusId(SECTIONS)
        threading.Thread(target=self._slow_values, name='dexhub-control-values', daemon=True).start()

    def _icon(self, name):
        return os.path.join(self.menu_dir, name + '.png')

    def _show_section(self, index):
        if not (0 <= index < len(self.sections)) or index == self._shown:
            return
        self._shown = index
        self.section = index
        sid, _icon, title, line, entries = self.sections[index]
        self.setProperty('cc.title', self.tr(title))
        self.setProperty('cc.desc', self.tr(line))
        xbmcgui.Window(10000).setProperty(LAST_PROP, sid)
        items = []
        for key, kind, icon, name, desc, _where_ in entries:
            li = xbmcgui.ListItem(label=self.tr(name), label2=self.tr(desc))
            li.setArt({'icon': self._icon(icon)})
            li.setProperty('key', key)
            li.setProperty('kind', kind)
            items.append(li)
        with self._lock:
            self.items = items
        self._paint(only_values=True)
        try:
            control = self.getControl(ENTRIES)
            control.reset()
            control.addItems(items)
        except Exception:
            pass

    def _paint(self, only_values=False):
        with self._lock:
            items = list(self.items)
        for li in items:
            value, switch = self._value(li.getProperty('key'))
            li.setProperty('value', value)
            li.setProperty('switch', switch)

    # ------------------------------------------------------------ actions
    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self._closing = True
            self.close()
            return
        try:
            if self.getFocusId() == SECTIONS:
                # the section under the cursor (keys, a mouse) fills the cards
                self._show_section(self.getControl(SECTIONS).getSelectedPosition())
        except Exception:
            pass

    def onClick(self, control_id):
        if control_id == SECTIONS:
            self.setFocusId(ENTRIES)
            return
        if control_id != ENTRIES:
            return
        try:
            li = self.getControl(ENTRIES).getSelectedItem()
        except Exception:
            return
        if li is None:
            return
        key, kind = li.getProperty('key'), li.getProperty('kind')
        if kind == 'open':
            self.choice = key
            self._closing = True
            self.close()
            return
        changed = False
        try:
            if kind == 'pick':
                changed = self._pick(key)
            elif kind == 'toggle':
                changed = self._toggle(key)
            elif kind == 'run':
                changed = self._run(key)
        except Exception:
            import traceback
            xbmc.log('[DexHub] control centre %s:\n%s' % (key, traceback.format_exc()), xbmc.LOGWARNING)
            if key in ('home_layout_mode', 'nav_position'):
                xbmcgui.Dialog().notification('Dex Hub', self.tr('تعذر حفظ وضع الواجهة؛ حدّث الإضافة وأعد تشغيل Kodi'), sound=False)
            if key in ('trailer_volume', 'trailer_sound', 'trailers'):
                xbmcgui.Dialog().notification('Dex Hub', self.tr('تعذر حفظ إعداد التريلر؛ حدّث الإضافة وأعد تشغيل Kodi'), sound=False)
        if changed:
            self._read()
            if key in ('meta_source', 'tmdb_key', 'posters'):
                threading.Thread(target=self._slow_values, name='dexhub-control-values', daemon=True).start()
            self._paint()

    def _write(self, setting, value):
        write(setting, value)  # v5.10.286: shared with the phone page

    def _pick(self, key):
        from .options import choose
        tr = self.tr
        if key == 'trailers':
            labels = [tr(label) for _seconds, label in TRAILER_STEPS] + [tr('إيقاف التريلرات')]
            values = [seconds for seconds, _label in TRAILER_STEPS] + [None]
            on = (self.snap.get('homeui_trailers') or 'true').lower() == 'true'
            try:
                current = int(float(self.snap.get('homeui_trailer_delay') or 3))
            except ValueError:
                current = 3
            preselect = values.index(current) if on and current in values else (len(values) - 1 if not on else -1)
            choice = choose(tr('التريلرات'), labels, preselect=preselect, subtitle=tr('متى يبدأ تريلر العمل المحدد'))
            if choice < 0:
                return False
            if values[choice] is None:
                self._write('homeui_trailers', 'false')
            else:
                self._write('homeui_trailer_delay', str(values[choice]))
                if not on:
                    self._write('homeui_trailers', 'true')
            return True
        setting, options = PICKS[key]
        current = (self.snap.get(setting) or options[0][0]).lower()
        preselect = next((i for i, (value, _label) in enumerate(options) if value.lower() == current), 0)
        title = next((e[3] for s in self.sections for e in s[4] if e[0] == key), '')
        if key == 'home_layout_mode':
            # v5.10.289: the layouts as pictures of the Home (homelayout_<key>.jpg)
            import os
            import xbmcaddon
            folder = os.path.join(xbmcaddon.Addon().getAddonInfo('path'), 'resources', 'media', 'homeui')
            lines = {'cinema': 'الخلفية بملء الشاشة، والصفوف تطلع لما تنزل',
                     'netflix': 'عمل بارز كبير وتحته الصف اللي أنت عليه',
                     'two_rows': 'شريط بارز صغير وصفين كاملين',
                     'cinematic': 'عمل بارز وتحته الصفوف',
                     'shelf': 'بدون هيرو: بطاقات عريضة كبيرة ثم الصفوف',
                     'spotlight_posters': 'عمل بارز وصفوف بوسترات'}
            gallery = [(tr(label), 'current' if i == preselect else '',
                        os.path.join(folder, 'homelayout_%s.jpg' % value), tr(lines.get(value, '')))
                       for i, (value, label) in enumerate(options)]
            choice = choose(tr(title), gallery, preselect=preselect,
                            subtitle=tr(PICK_LINES.get(key, '')), layout='gallery')
        else:
            choice = choose(tr(title), [tr(label) for _value, label in options], preselect=preselect,
                            subtitle=tr(PICK_LINES.get(key, '')))
        if choice < 0 or choice == preselect:
            return False
        # v5.10.286: the write and its follow-ups live in apply_entry (the phone
        # page uses it too). v5.10.210: the rows are read again once the centre
        # closes, not under it (a republish of every tab while two dialogs were
        # open came right before a crash on a CoreELEC box)
        if apply_entry(key, options[choice][0]):
            self.refresh_after = True
        return True

    def _toggle(self, key):
        setting, default = TOGGLES[key]
        on = (self.snap.get(setting) or default).lower() == 'true'
        # v5.10.286: the write and its follow-ups live in apply_entry
        if apply_entry(key, not on):
            self.refresh_after = True
        return True

    def _run(self, key):
        tr = self.tr
        if key == 'poster_scale':
            from ..home_preferences import choose_poster_scale
            return choose_poster_scale(tr)
        if key == 'meta_source':
            from .. import meta_mode
            return bool(meta_mode.pick())
        if key == 'tmdb_key':
            from ..routes.simple_entry import tmdb_api_key_dialog
            tmdb_api_key_dialog()
            return True
        if key == 'focus_colour':
            from ..skinui import actions
            actions.focus_colour({}, -1)
            return True
        if key == 'continue_split':
            from .options import choose
            from .layout import continue_split, continue_is_split
            current = 1 if continue_is_split() else 0
            choice = choose(tr('متابعة المشاهدة في الرئيسية'), [tr('صف واحد'), tr('صفين: أفلام ومسلسلات')],
                            preselect=current, subtitle=tr('في صفحة الكل؛ الأفلام والمسلسلات لها صفها دائماً'))
            if choice < 0 or choice == current:
                return False
            continue_split(choice == 1)
            self.refresh_after = True
            return True
        if key == 'meta_refresh':
            if not xbmcgui.Dialog().yesno('Dex Hub', tr('نمسح كاش الميتاداتا ونحمل البيانات والصور من جديد؟')):
                return False
            from .. import meta_mode
            meta_mode.clear_metadata_caches(notify=True, reload_ui=True)
            return False
        return False


def fallback_choose(tr):
    """Every entry in one plain list, for a skin the window cannot open in."""
    from .options import choose
    keys, labels = [], []
    for _sid, _icon, title, _line, entries in sections():
        for key, _kind, _icon2, name, _desc, _w in entries:
            keys.append(key)
            labels.append('%s  \u2022  %s' % (tr(title), tr(name)))
    choice = choose('Dex Hub', labels, subtitle=tr('القائمة'))
    return keys[choice] if choice >= 0 else ''


def open_centre(tr=None, media=''):
    """Show the control centre; the key of the open entry chosen, or ''."""
    addon = _addon()
    if tr is None:
        try:
            from ..i18n import tr
        except Exception:
            tr = lambda text: text
    try:
        from . import ui_size
        path = addon.getAddonInfo('path')
        window = ControlCentre(ui_size.xml('dexhub_control.xml'), path, 'Default', '1080i', tr=tr, media=media,
                               version=addon.getAddonInfo('version'),
                               menu_dir=os.path.join(path, 'resources', 'media', 'menu'))
        window.doModal()
        choice = window.choice
        window._closing = True
        if window.refresh_after:
            try:
                from ..routes.simple_entry import _refresh_continue
                _refresh_continue()
            except Exception:
                pass
        del window
        return choice
    except Exception:
        import traceback
        xbmc.log('[DexHub] control centre unavailable:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    return None
