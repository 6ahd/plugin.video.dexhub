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
ENTRY_PROP = 'dexhub.control.entry'     # v5.10.358: the entry last opened

# (key, kind, icon, title, line, where); where: '' everywhere, 'skin' in
# skin.dexhub, 'addon' outside it, 'player' in skin.dexhub or Arctic Fuse 3,
# 'coreelec' on CoreELEC
SECTION_LIST = (
    ('home', 'house', 'الرئيسية', 'ترتيب صفحتك وما يظهر فيها', (
        ('phone', 'open', 'mobile', 'إعداد من الجوال', 'امسح الباركود وجهّز كل شي من جوالك', ''),
        ('search', 'open', 'search', 'بحث', 'أفلام ومسلسلات وأشخاص من كل مصادرك', ''),
        ('layout', 'open', 'table-cells-large', 'ترتيب الصفحة الرئيسية', 'الصفوف وترتيبها وشكل كل صف', ''),
        ('new_rows', 'toggle', 'circle-xmark', 'صفوف جديدة مخفية',
         'تنتظرك في ترتيب الصفحة', ''),
        ('group_recs', 'toggle', 'layer-group', 'تجميع التوصيات',
         '«لأنك شاهدت» في صف واحد', ''),
        ('community', 'open', 'users', 'Dex Community', 'كتالوجات جاهزة تضيفها بضغطة', ''),
        ('refresh', 'open', 'arrows-rotate', 'تحديث الرئيسية', 'جلب الصفوف من جديد', ''),
        ('screensaver', 'open', 'moon', 'شاشة التوقف', 'الخلفيات ووقت التشغيل', ''),
        ('ss_preload', 'toggle', 'moon', 'تجهيز الرئيسية وقت شاشة التوقف', 'الصفوف والصور تتجهز والجهاز فاضي، فترجع للرئيسية وهي جاهزة', 'skin'),
    )),
    # v5.10.299: one Appearance place, in three steps (colours, the Home's
    # shape, motion); every choice shows at once, the skin's settings page
    # opens here too
    ('look_theme', 'palette', 'المظهر: الثيم والألوان', 'كل تغيير يظهر فورًا في السكين ونوافذ كودي', (
        ('theme', 'open', 'palette', 'الثيم', 'ألوان السكين كلها بضغطة', 'skin'),
        ('focus_colour', 'run', 'palette', 'لون التحديد', 'لون المؤشر في السكين ونوافذ كودي', 'skin'),
        ('focus_text', 'pick', 'tags', 'الكتابة على التحديد', 'تلقائي حسب اللون أو أبيض أو داكن', 'skin'),
        ('kodi_focus', 'pick', 'sliders', 'شكل التحديد', 'بلون التحديد أو أبيض مثل Nuvio، في الواجهة ونوافذ كودي', 'skin'),
        ('amoled', 'toggle', 'moon', 'أسود كامل AMOLED', 'خلفية الصفحات سوداء تمامًا', 'skin'),
        ('glow', 'pick', 'wand-magic-sparkles', 'إضاءة الصفحات', 'هادئة أو قوية أو بدون', 'skin'),
        ('focus_glow', 'toggle', 'wand-magic-sparkles', 'إضاءة حول البطاقة المحددة', 'هالة خفيفة بلون التحديد', 'skin'),
    )),
    ('look_layout', 'display', 'المظهر: الواجهة والبطاقات', 'شكل الرئيسية والقوائم والبطاقات', (
        ('home_layout_mode', 'pick', 'display', 'وضع الواجهة', 'هيرو وصفوف أو سينمائي أو رف بطاقات أو غيرها', 'skin'),
        ('nav_position', 'pick', 'list-ul', 'مكان تبويبات الأقسام', 'شريط علوي أو قائمة جانبية', 'skin'),
        ('nav_icons', 'toggle', 'bars', 'القائمة الجانبية أيقونات فقط', 'ما تنفتح بالكلمات لما تروح لها', 'skin'),
        ('nav_autohide', 'toggle', 'eye-slash', 'إخفاء القائمة عند التمرير', 'الشريط العلوي أو القائمة الجانبية تختفي لما تنزل للصفوف وترجع لما تطلع أو تروح يسار', 'skin'),
        ('nav_focus_open', 'toggle', 'circle-dot', 'فتح الأقسام بالتمرير', 'القسم ينفتح لما توقف على أيقونته ثانية، بدون ضغط', 'skin'),
        ('back_exit', 'toggle', 'power-off', 'الرجوع من القائمة يسأل عن الخروج', 'زر الرجوع على الشريط العلوي أو القائمة الجانبية يسألك تطلع من كودي', 'skin_exit'),
        ('bg_settle', 'toggle', 'image', 'الخلفية تتبع التحديد بعد التوقف', 'خلفية الرئيسية تتغير لما توقف على العمل، فالتنقل أخف', 'skin'),
        ('nav_names', 'toggle', 'list-ul', 'أسماء الأقسام', 'أسماء بجانب أيقونات الشريط', ''),
        ('size', 'open', 'up-right-and-down-left-from-center', 'حجم الواجهة والبطاقات', 'من 50% إلى 200%', ''),
        ('card_style', 'pick', 'layer-group', 'عناوين البطاقات', 'على الصورة أو تحتها أو بدون', ''),
        ('title_poster', 'toggle', 'image', 'إظهار البوستر في صفحة العمل', 'مع بوستر بجانب المعلومات، أو بدون بوستر مثل التخطيط السابق', ''),
        ('title_style', 'pick', 'image', 'عنوان العمل', 'شعار العمل أو نص', ''),
        ('background_style', 'pick', 'display', 'نمط الخلفية', 'سينمائي أو هادئ أو داكن', ''),
        ('clean_look', 'toggle', 'wand-magic-sparkles', 'مظهر نظيف', 'تحديد أبيض، سطر المعلومات كلام بدون كبسولات ملونة، وأسماء الصفوف مختصرة', 'skin'),
        # v5.10.379: the spotlight as in Arctic Fuse 3: its row, its size, the section
        ('spotlight', 'run', 'clapperboard', 'أعمال السبوت لايت', 'الصف اللي تجي منه أعمال السبوت لايت في هذا القسم، أو بدون', 'skin'),
        ('spot_count', 'run', 'clapperboard', 'عدد أعمال السبوت لايت', 'من 5 إلى 20 عمل', 'skin'),
        ('spot_section', 'toggle', 'display', 'سبوت لايت كامل', 'لما تطلع للسبوت لايت تنزل الصفوف تحت ويصير العمل قدامك مثل Arctic Fuse 3', 'skin'),
        ('row_dim', 'pick', 'display', 'تعتيم الصفوف', 'الصفوف غير المحددة', ''),
        ('row_source', 'toggle', 'tags', 'إظهار مصدر الصف', 'From بجانب عنوان الصف', ''),
        ('season_names', 'toggle', 'tags', 'أسماء المواسم', 'الموسم 1: Murder House بدل الموسم 1', ''),
        ('rating_inline', 'toggle', 'star', 'التقييم في سطر التفاصيل', 'بعد التصنيفات والسنة والمدة بدل سطر لحاله', ''),
        ('compact_hero', 'toggle', 'display', 'هيرو الصفين أصغر', 'أزرار وأسماء صفوف أصغر ونبذة أعرض في وضع الصفين', ''),
        ('servers', 'toggle', 'server', 'قسم السيرفرات', 'Plex وEmby وJellyfin وSilo في الرئيسية', ''),
    )),
    ('look_motion', 'wand-magic-sparkles', 'المظهر: الحركة والتريلرات', 'التحديد والخلفيات والتريلر', (
        ('focus_motion', 'pick', 'wand-magic-sparkles', 'حركة التحديد', 'تكبير أو رفع أو قفزة أو إضاءة', ''),
        ('ui_sounds', 'run', 'sliders', 'أصوات التنقل', 'نقرة ناعمة مثل Apple TV، أو أصوات كودي، أو بدون', ''),
        # v5.10.376: sharper backdrops and posters (Kodi's 720 cache, TMDb sizes)
        ('image_quality', 'run', 'image', 'جودة الصور', 'الخلفيات والبوسترات: عادية، أو عالية 1080 للأجهزة القوية', ''),
        ('kenburns', 'toggle', 'image', 'تحريك الخلفيات', 'حركة بطيئة للصورة خلف العمل', ''),
        ('focus_gif', 'toggle', 'film', 'الصور المتحركة', 'صور GIF للأعمال اللي فيها', ''),
        ('carousel', 'toggle', 'clapperboard', 'تدوير البانر', 'التنقل التلقائي بين الأعمال', ''),
        ('plot_scroll', 'toggle', 'sliders', 'تحريك النبذة', 'تمرير النبذة الطويلة', ''),
        ('hide_plot', 'toggle', 'eye-slash', 'إخفاء النبذة', 'واجهة أبسط مع عنوان العمل وتفاصيله', ''),
        ('trailers', 'pick', 'clapperboard', 'التريلرات', 'متى يبدأ تريلر العمل المحدد', ''),
        ('trailer_sound', 'toggle', 'film', 'صوت التريلر', 'التريلر بصوته أو صامت', ''),
        ('trailer_volume', 'pick', 'film', 'مستوى صوت التريلر', 'من 0 إلى 100؛ يرجع صوت المشاهدة بعد التريلر', ''),
        ('trailer_place', 'pick', 'clapperboard', 'مكان التريلر', 'خلف الصفحة، أو داخل البطاقة المحددة وهي مفتوحة في صفها', ''),
        ('trailer_clean', 'toggle', 'eye-slash', 'تريلر بدون كتابة', 'الشعار والتصنيفات والتقييمات تختفي وقت التريلر وترجع مع أي زر', ''),
        ('immersive_after', 'pick', 'clapperboard', 'ملء الشاشة بعد', 'بعد كم ثانية يملأ التريلر الشاشة وتختفي الصفوف', ''),
        ('hero_shuffle', 'toggle', 'clapperboard', 'خلط أعمال البانر', 'ترتيب جديد كل يوم من نفس الكتالوج', ''),
    )),
    ('player', 'circle-play', 'المشغل', 'التشغيل والرموز والترجمة', (
        ('player', 'open', 'circle-play', 'خيارات المشغل', 'الشعار والتقييمات وسطر المعلومات', 'player'),
        ('sources_style', 'pick', 'list-ul', 'صفحة المصادر', 'مثل Nuvio TV أو صفوف مختصرة', ''),
        ('osd_buttons', 'open', 'circle-play', 'أزرار المشغل وشكله', 'الأزرار وترتيبها مع معاينة النمط واللون', 'player'),
        ('osd_preset', 'pick', 'wand-magic-sparkles', 'شكل جاهز للمشغل', 'Nuvio وApple TV وNetflix وغيرها بضغطة', 'player'),
        ('osd_style', 'pick', 'display', 'نمط شريط المشغل', 'كلاسيكي أو زجاجي أو سينمائي أو بسيط', 'player'),
        ('osd_dim', 'pick', 'sliders', 'تعتيم خلفية المشغل', 'من شفاف بدون ظل إلى 95%', 'player'),
        ('osd_area', 'pick', 'sliders', 'مكان ظل المشغل', 'تحت الشريط أو فوق وتحت أو بدون', 'player'),
        ('osd_scrim', 'pick', 'palette', 'لون ظل المشغل', 'أسود أو لون الثيم أو العمل أو لون تختاره', 'player'),
        ('osd_buttons_bg', 'pick', 'circle-dot', 'خلفية أزرار المشغل', 'بدون أو شكل خفيف أو إطار', 'player'),
        ('osd_shape', 'pick', 'circle-dot', 'شكل المؤشر في المشغل', 'دائرة أو مربع أو حلقة أو خط أو نقطة أو شفاف', 'player'),
        ('osd_accent', 'pick', 'palette', 'لون المشغل', 'أبيض أو لون الثيم أو يتبع العمل المشغّل', 'player'),
        ('badges_visible', 'toggle', 'sliders', 'إظهار رموز الجودة في المشغّل', 'إخفاؤها أو إرجاعها دون تغيير حزمة الرموز', ''),
        ('badges_brightness', 'pick', 'sliders', 'وضوح رموز الجودة', 'واضح بلا تعتيم أو هادئ', ''),
        ('badges', 'open', 'tags', 'رموز الجودة', 'في المشغل والسيرفرات والروابط', ''),
        ('playback', 'open', 'gauge-high', 'طريقة التشغيل', 'أول رابط أو قائمة المصادر', ''),
        ('source_behavior', 'open', 'list-check', 'المصادر وسلوك التشغيل', 'الترتيب والفلترة والاستئناف', ''),
        ('source_addons', 'open', 'list-check', 'إضافات البحث عن المصادر', 'الإضافات اللي يسألها البحث', ''),
        ('subtitles', 'open', 'closed-captioning', 'الترجمة', 'اللغة والمزامنة التلقائية', ''),
        ('episodes', 'open', 'forward-step', 'الحلقات والتخطي', 'الحلقة التالية وتخطي المقدمة', ''),
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
        # v5.10.374 (Reddit): an episode not out yet in Next episode, with its day, or hidden
        ('nextup_unaired', 'toggle', 'clock-rotate-left', 'الحلقات اللي ما نزلت', 'في الحلقة التالية مع يوم نزولها، أو تختفي لين تنزل', ''),
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
        ('ratings_pick', 'run', 'star', 'التقييمات الظاهرة', 'اختار اللي يظهر: IMDb وRotten Tomatoes وMetacritic وغيرها', ''),
        ('meta_refresh', 'run', 'arrows-rotate', 'مسح كاش الميتاداتا', 'البيانات والصور من جديد', ''),
    )),
    ('accounts', 'user-gear', 'الحسابات والمصادر', 'Nuvio وTrakt والسيرفرات والإضافات', (
        ('phone', 'open', 'mobile', 'إعداد من الجوال', 'تربط حساباتك وتضيف مصادرك من الجوال', ''),
        ('setup', 'open', 'user-gear', 'الحسابات', 'Nuvio وStremio وTrakt والسيرفرات', ''),
        ('sources', 'open', 'link', 'صفحة المصادر', 'كل مصدر وحالته في صفحة وحدة', ''),
        ('add_source', 'open', 'circle-plus', 'إضافة مصدر', 'رابط إضافة Stremio أو Nuvio', ''),
        ('nuvio', 'open', 'id-card', 'مزامنة Nuvio', 'الهوم والمجموعات ومتابعة المشاهدة', ''),
        ('trakt', 'open', 'clock-rotate-left', 'Trakt', 'سجل المشاهدة والمزامنة', ''),
        ('hub_extra', 'open', 'star', 'DexCloud', 'شكلك في حسابك، صفوف أسرع، ومشاهدة جماعية', ''),
        ('party', 'open', 'users', 'مشاهدة جماعية', 'تتفرجون مع بعض، كل واحد على تلفزيونه', ''),
    )),
    ('system', 'gears', 'النظام', 'الإعدادات والسكين والتشخيص', (
        ('settings', 'open', 'sliders', 'إعدادات Dex Hub', 'مركز الإعداد', ''),
        ('skin_pick', 'open', 'brush', 'تغيير السكين', 'أي سكين مثبت على طول', ''),
        ('skin_back', 'open', 'arrow-rotate-left', 'الرجوع للسكين السابق', 'السكين اللي كان قبل Dex Hub', 'skin'),
        ('skin', 'open', 'brush', 'سكين Dex Hub', 'يصير Dex Hub سكين كودي', 'addon'),
        ('classic', 'open', 'list-ul', 'قائمة Dex Hub', 'كل أقسام الإضافة في قائمة', ''),
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
    'hub_extra': 'hub_extra',         # v5.10.366
    'party': 'party',
    'clear_cache': 'clear_cache',
    'osd_buttons': 'osd_buttons',     # v5.10.299
}

TRAILER_STEPS = ((0, 'فوري'), (1, 'بعد ثانية'), (2, 'بعد ثانيتين'), (3, 'بعد 3 ثوانٍ'),
                 (5, 'بعد 5 ثوانٍ'), (8, 'بعد 8 ثوانٍ'))
# (setting, [(value, label)]) of the pick entries kept in one setting
PICKS = {
    'badges_brightness': ('player_badges_brightness', (('solid', 'واضح بلا تعتيم'), ('soft', 'هادئ'))),
    'title_style': ('homeui_title_style', (('logo', 'شعار العمل'), ('text', 'نص'))),
    'background_style': ('homeui_background_style', (('cinematic', 'سينمائي'), ('soft', 'هادئ'), ('dark', 'داكن'), ('fullscreen', 'ملء الشاشة'))),
    # v5.10.286: the two new Home layouts first (cinematic keeps the default: options[0]
    # is only the fallback label, the setting's default stays cinematic)
    'home_layout_mode': ('homeui_layout_mode', (('cinematic', 'هيرو وصفوف'), ('legacy', 'ليقسي'), ('cinema', 'سينمائي ملء الشاشة'), ('shelf', 'رف بطاقات عريضة'),
                                                ('netflix', 'هيرو وصف واحد'), ('two_rows', 'هيرو مختصر وصفين'),
                                                ('spotlight_posters', 'Spotlight وبوسترات'))),
    'nav_position': ('homeui_nav_position', (('top', 'أعلى الشاشة'), ('side', 'قائمة جانبية'))),
    'trailer_volume': ('homeui_trailer_volume', tuple((str(v), str(v) + '%') for v in range(0,101,5))),
    'trailer_place': ('homeui_trailer_mode', (('background', 'خلفية الشاشة'), ('card', 'داخل البطاقة'))),
    'immersive_after': ('homeui_immersive_delay', tuple((v, 'بعد %s ثوانٍ' % v) for v in ('4', '6', '8', '10')) + (('12', 'بعد 12 ثانية'), ('16', 'بعد 16 ثانية'), ('20', 'بعد 20 ثانية'))),
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
    # v5.10.301: the sources window's rows
    'sources_style': ('sources_row_style', (('full', 'مثل Nuvio TV'), ('compact', 'مختصرة'))),
    # v5.10.299: the player bar's look (osd_look.py)
    'osd_style': ('player_osd_style', (('classic', 'كلاسيكي'), ('glass', 'زجاجي'), ('cinematic', 'سينمائي'),
                                       ('minimal', 'بسيط'))),
    'osd_shape': ('player_osd_shape', (('circle', 'دائرة'), ('square', 'مربع مثل Apple TV'), ('ring', 'حلقة'),
                                       ('line', 'خط تحت الأيقونة'), ('dot', 'نقطة تحت الأيقونة'),
                                       ('soft', 'دائرة شفافة'))),
    'osd_dim': ('player_osd_dim', (('0', 'شفاف بدون ظل'), ('15', '15%'), ('30', '30%'), ('50', '50%'), ('60', '60%'),
                                   ('70', '70%'), ('80', '80% مثل Nuvio'), ('85', '85%'), ('95', '95%'))),
    # v5.10.302: the shades and the buttons' backing; ready looks set all of it
    'osd_area': ('player_osd_area', (('bottom', 'تحت الشريط فقط'), ('both', 'فوق وتحت مثل Nuvio'), ('none', 'بدون ظل · شفاف'))),
    'osd_scrim': ('player_osd_scrim', (('auto', 'أسود بلمسة من لون المشغل'), ('black', 'أسود'), ('theme', 'لون خلفية الثيم'),
                                       ('artwork', 'يتبع ألوان العمل المشغّل'), ('navy', 'كحلي'), ('plum', 'بنفسجي غامق'),
                                       ('wine', 'عنابي'), ('forest', 'أخضر غامق'), ('slate', 'رمادي أزرق'), ('warm', 'بني دافئ'))),
    'osd_buttons_bg': ('player_osd_buttons', (('none', 'بدون خلفية'), ('soft', 'شكل خفيف خلف كل زر'), ('outline', 'إطار حول كل زر'))),
    'osd_preset': ('player_osd_preset', (('custom', 'مخصص (اختياراتك)'), ('nuvio', 'Nuvio'), ('appletv', 'Apple TV'),
                                         ('netflix', 'على طريقة Netflix'), ('infuse', 'على طريقة Infuse'), ('plex', 'على طريقة Plex'),
                                         ('disney', 'على طريقة Disney+'), ('artwork', 'ألوان العمل'), ('clean', 'شفاف ونظيف'),
                                         ('theme', 'ألوان الثيم'))),
    'osd_accent': ('player_osd_accent', (('white', 'أبيض'), ('theme', 'لون الثيم'), ('artwork', 'يتبع العمل المشغّل'),
                                         ('violet', 'بنفسجي'), ('blue', 'أزرق'), ('cyan', 'سماوي'), ('green', 'أخضر'),
                                         ('gold', 'ذهبي'), ('orange', 'برتقالي'), ('red', 'أحمر'), ('pink', 'وردي'))),
}
# v5.10.299: the skin's own looks (skin strings; the theme is published again at once)
#   key: (skin string, ((value, label), ...)); '' is the skin's default (Skin.Reset)
SKIN_PICKS = {
    'focus_text': ('dh.ontext', (('', 'تلقائي حسب اللون'), ('white', 'أبيض'), ('dark', 'داكن'))),
    'glow': ('dh.glow', (('', 'هادئة'), ('vivid', 'قوية'), ('off', 'بدون'))),
    'kodi_focus': ('dh.kodifocus', (('', 'بلون التحديد'), ('white', 'أبيض مثل Nuvio'))),
}
#   key: (skin string, its value when on, on by default)
SKIN_TOGGLES = {
    'amoled': ('dh.amoled', 'on', False),
    'focus_glow': ('dh.focusglow', '', True),     # off is the string 'off'
}
PICK_LINES = {
    'osd_accent': '«يتبع العمل» ياخذ لون من صورة العمل اللي تشغله',
    'osd_dim': 'خلفية شريط المشغل: كل ما زادت النسبة صارت أغمق، وشفاف بدون ظل أبدًا',
    'osd_preset': 'يغير النمط والظل واللون والمؤشر والأزرار مرة وحدة، وتقدر تعدل كل واحد بعدها',
    'osd_scrim': 'الظل اللي فوق وتحت الشريط',
    'server_meta': 'السيرفر يعطي العنوان والقصة والصور، والناقص مثل الشعار من مصدرك',
    'meta_lang': 'القصص والشعارات بهذه اللغة إذا كانت متوفرة',
    'follow_order': 'صف متابعة المشاهدة في الرئيسية وأول قسم في متابعتي',
    'lib_sort': 'المفضلة وقوائم المشاهدة في المكتبة وفي متابعتي',
    'episode_art': 'في متابعة المشاهدة والحلقة التالية. «الحلقات اللي بدأتها» يخلي الحلقة القادمة بصورة المسلسل حتى ما تنكشف',
    'credits_panel': 'الفيديو يصغر في الزاوية وتطلع الحلقة التالية بعد عد تنازلي، وللأفلام أعمال مشابهة. «مع التترات» يبدأ مع فصل التترات أو قبل النهاية بقليل',
    'episode_titles': 'في البطاقات وقائمة حلقات الموسم. «الحلقات اللي بدأتها» يخلي اللي ما شفتها بالرقم حتى ما ينكشف شي',
}
TOGGLES = {'title_poster': ('homeui_title_poster', 'true'), 'library_split': ('library_split_media', 'true'), 'badges_visible': ('player_badges_visible', 'true'),
           'row_source': ('homeui_show_row_source', 'true'), 'plot_scroll': ('homeui_plot_scroll', 'true'),
           'hide_plot': ('homeui_hide_plot', 'false'), 'rating_inline': ('homeui_rating_inline', 'true'),
           'compact_hero': ('homeui_compact_hero', 'true'), 'nav_icons': ('homeui_nav_icons_only', 'false'), 'nav_autohide': ('homeui_nav_autohide', 'false'), 'nav_focus_open': ('homeui_nav_focus_open', 'false'), 'back_exit': ('homeui_back_exit', 'true'), 'hero_shuffle': ('homeui_hero_shuffle', 'false'), 'clean_look': ('homeui_clean_look', 'true'), 'spot_section': ('homeui_spot_section', 'true'), 'ss_preload': ('homeui_ss_preload', 'true'), 'servers': ('show_servers', 'true'), 'bg_settle': ('homeui_bg_settle', 'true'), 'trailer_clean': ('homeui_trailer_clean', 'true'), 'carousel': ('homeui_carousel', 'true'),'trailer_sound': ('homeui_trailer_sound', 'true'), 'ratings': ('metadata_badges', 'true'),
           'nav_names': ('homeui_nav_names', 'false'), 'follow_merge': ('follow_merge', 'true'),
           'nextup_unaired': ('nextup_show_unaired', 'true'),
           'trakt_smart': ('trakt_smart_sync', 'true'), 'season_names': ('season_names', 'true'),
           'reminders': ('episode_reminders', 'true'),
           'new_rows': ('home_hide_new_rows', 'false'), 'group_recs': ('home_group_recs', 'false'),
           'kenburns': ('homeui_kenburns', 'true'), 'focus_gif': ('homeui_focus_gif', 'true')}
SIZES = {'': 'عادي', 'large': 'كبير', 'xlarge': 'كبير جداً',
         'normal': 'عادي', 'extra large': 'كبير جداً'}
_SETTINGS = ('homeui_title_poster', 'homeui_nav_icons_only', 'homeui_nav_autohide', 'homeui_nav_focus_open', 'homeui_back_exit', 'homeui_immersive_delay', 'homeui_hero_shuffle', 'homeui_clean_look', 'homeui_spot_section', 'homeui_spot_count', 'homeui_spot_source', 'homeui_ss_preload', 'show_servers', 'homeui_bg_settle', 'homeui_trailer_clean', 'ratings_shown', 'library_split_media', 'player_badges_visible', 'player_badges_brightness', 'homeui_poster_scale', 'homeui_show_row_source', 'homeui_title_style', 'homeui_background_style', 'homeui_plot_scroll', 'homeui_hide_plot', 'homeui_rating_inline', 'homeui_compact_hero', 'homeui_carousel', 'homeui_layout_mode', 'homeui_nav_position', 'homeui_metadata_language', 'server_metadata', 'homeui_card_style', 'homeui_focus_motion',
             'homeui_row_dim', 'homeui_trailer_sound', 'metadata_badges', 'homeui_nav_names', 'homeui_trailers',
             'homeui_trailer_delay', 'homeui_trailer_volume', 'homeui_trailer_mode', 'homeui_size', 'global_meta_source_id', 'global_meta_source_all',
             'tmdb_api_key', 'global_poster_source_id', 'follow_order', 'library_sort', 'follow_merge',
             'trakt_smart_sync', 'continue_episode_art', 'season_names', 'episode_titles', 'episode_reminders', 'credits_panel',
             'home_hide_new_rows', 'home_group_recs', 'homeui_kenburns', 'homeui_focus_gif',
             'player_osd_style', 'player_osd_dim', 'player_osd_accent', 'player_osd_shape', 'sources_row_style',
             'player_osd_scrim', 'player_osd_area', 'player_osd_buttons', 'player_osd_preset')


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _where():
    """What this place offers: skin.dexhub, Arctic Fuse 3, CoreELEC."""
    skin = xbmc.getSkinDir()
    try:
        from .. import power
        coreelec = bool(power.coreelec())
        appliance = bool(power.appliance())
    except Exception:
        coreelec = appliance = False
    return {'skin': skin == 'skin.dexhub', 'addon': skin != 'skin.dexhub',
            'player': skin in ('skin.dexhub', 'skin.arctic.fuse.3'), 'coreelec': coreelec, '': True,
            'skin_exit': skin == 'skin.dexhub' and not appliance}     # v5.10.367


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


def _skin_on(key):
    string, on_value, default = SKIN_TOGGLES[key]
    value = (xbmc.getInfoLabel('Skin.String(%s)' % string) or '').strip().lower()
    if key == 'focus_glow':
        return value != 'off'
    return value == on_value if value else default


def set_skin_string(string, value):
    """v5.10.299: one of the skin's looks, shown at once: the string, then
    the theme published again (the skin and Dex Hub's windows read it)."""
    if value:
        xbmc.executebuiltin('Skin.SetString(%s,%s)' % (string, value), True)
    else:
        xbmc.executebuiltin('Skin.Reset(%s)' % string, True)
    try:
        from .. import skin_theme
        skin_theme.publish_theme()
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
        if setting.startswith('player_osd_'):
            # v5.10.302: a ready look writes its parts; a part makes it custom
            try:
                from .. import osd_look
                osd_look.after_write(setting, value)
            except Exception:
                pass
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
        if key == 'nextup_unaired':
            try:
                from ..nextup_logic import invalidate_nextup_cache
                invalidate_nextup_cache(xbmcgui.Window(10000))
            except Exception:
                pass
        return key in ('library_split', 'nextup_unaired')
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
        self._painted_values = {}
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
        if key == 'ratings_pick':
            from . import ratings as RB
            keys = RB.chosen(snap.get('ratings_shown'))
            if len(keys) == len(RB.ORDER):
                return tr('الكل'), ''
            return ' · '.join(RB.NAMES[k] for k in keys)[:60], ''
        if key == 'continue_split':
            try:
                from .layout import continue_is_split
                split = continue_is_split()
            except Exception:
                split = False
            return (tr('صفين: أفلام ومسلسلات') if split else tr('صف واحد')), ''
        if key == 'spotlight':
            from ..skinui import spot_source
            return spot_source.summary(tr), ''
        if key == 'spot_count':
            from ..skinui import spot_source
            return tr('%d أعمال') % spot_source.count(), ''
        if key == 'image_quality':
            from ..image_quality import current as _iq
            return (tr('عالية 1080') if _iq() == 'high' else tr('عادية')), ''
        if key == 'ui_sounds':
            from ..ui_sounds import current
            return tr({'dexhub': 'ناعمة مثل Apple TV', 'kodi': 'أصوات كودي', 'off': 'بدون',
                       'other': 'حزمة أخرى'}[current()]), ''
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
        if key in SKIN_PICKS:
            string, options = SKIN_PICKS[key]
            current = (xbmc.getInfoLabel('Skin.String(%s)' % string) or '').strip().lower()
            return tr(next((label for value, label in options if value == current), options[0][1])), ''
        if key in SKIN_TOGGLES:
            on = _skin_on(key)
            return (tr('شغال') if on else tr('مطفأ')), ('on' if on else 'off')
        if key == 'theme':
            return xbmc.getInfoLabel('Skin.CurrentColourTheme') or '', ''
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
        # v5.10.358: back from an entry's page: the focus on that entry
        entry = xbmcgui.Window(10000).getProperty(ENTRY_PROP)
        xbmcgui.Window(10000).clearProperty(ENTRY_PROP)
        keys = [li.getProperty('key') for li in self.items]
        if entry and entry in keys:
            try:
                self.getControl(ENTRIES).selectItem(keys.index(entry))
                self.setFocusId(ENTRIES)
            except Exception:
                pass
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
            self._painted_values = {}
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
            key = li.getProperty('key')
            try:
                value, switch = self._value(key)
            except Exception as exc:
                # One unreadable preference must not erase every card in
                # the section. Keep the entry available and log its key.
                xbmc.log('[DexHub] control value %s: %s' % (key, type(exc).__name__), xbmc.LOGWARNING)
                value, switch = self.tr('غير متاح الآن'), ''
            previous = self._painted_values.get(key)
            if previous == (li, value, switch):
                continue
            li.setProperty('value', value)
            li.setProperty('switch', switch)
            self._painted_values[key] = (li, value, switch)

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
            # v5.10.358: Back from what it opens finds this card again
            xbmcgui.Window(10000).setProperty(ENTRY_PROP, key)
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
            if key in ('trailer_volume', 'trailer_sound', 'trailers', 'trailer_place'):
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
        if key in SKIN_PICKS:
            string, options = SKIN_PICKS[key]
            current = (xbmc.getInfoLabel('Skin.String(%s)' % string) or '').strip().lower()
            preselect = next((i for i, (value, _l) in enumerate(options) if value == current), 0)
            title = next((e[3] for sec in self.sections for e in sec[4] if e[0] == key), '')
            choice = choose(tr(title), [tr(label) for _v, label in options], preselect=preselect)
            if choice < 0 or choice == preselect:
                return False
            set_skin_string(string, options[choice][0])
            return True
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
                     'legacy': 'شعار ومعلومات العمل فوق، والصفوف تحتها بإطار أبيض',
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
        if key in SKIN_TOGGLES:
            string, on_value, _default = SKIN_TOGGLES[key]
            if key == 'focus_glow':
                set_skin_string(string, 'off' if _skin_on(key) else '')
            else:
                set_skin_string(string, '' if _skin_on(key) else on_value)
            return True
        setting, default = TOGGLES[key]
        on = (self.snap.get(setting) or default).lower() == 'true'
        # v5.10.286: the write and its follow-ups live in apply_entry
        if apply_entry(key, not on):
            self.refresh_after = True
        return True

    def _run(self, key):
        tr = self.tr
        if key == 'ratings_pick':
            # v5.10.334: which ratings show (the Home, the title pages, the player)
            from . import ratings as RB
            return RB.pick(tr)
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
        if key == 'ui_sounds':
            # v5.10.365: Kodi's navigation sounds (no skin has its own)
            from .options import choose
            from .. import ui_sounds
            names = [tr('ناعمة مثل Apple TV'), tr('أصوات كودي'), tr('بدون')]
            now = ui_sounds.current()
            pick = choose(tr('أصوات التنقل'), names, preselect=ui_sounds.CHOICES.index(now) if now in ui_sounds.CHOICES else 0,
                          subtitle=tr('تنسمع لما يتحرك التحديد وOK والرجوع'))
            if pick < 0:
                return False
            return ui_sounds.apply(ui_sounds.CHOICES[pick], notify=lambda: xbmcgui.Dialog().notification(
                'Dex Hub', tr('ثبّت Dex Hub UI Sounds من مستودع Dex Hub'), sound=False))
        if key == 'spotlight':
            from ..skinui import spot_source
            return spot_source.pick(tr)
        if key == 'spot_count':
            from ..skinui import spot_source
            return spot_source.pick_count(tr)
        if key == 'image_quality':
            from .options import choose
            from .. import image_quality
            now = image_quality.current()
            rows = [(tr('عادية'), tr('خلفيات 720 وبوسترات w500: الأخف للأجهزة الصغيرة')),
                    (tr('عالية 1080'), tr('خلفيات 1080 من الأصل وبوسترات أوضح: Shield وApple TV 4K والماك'))]
            pick = choose(tr('جودة الصور'), rows, preselect=image_quality.CHOICES.index(now), subtitle=tr('الخلفيات والبوسترات'))
            if pick < 0 or image_quality.CHOICES[pick] == now:
                return False
            done = image_quality.apply(image_quality.CHOICES[pick])
            if done.get('own'):
                xbmcgui.Dialog().ok('Dex Hub', tr('ملف advancedsettings.xml فيه imageres من قبل، فما غيرناه. الصور من TMDb تغيرت.'))
            if done.get('changed') and xbmcgui.Dialog().yesno('Dex Hub', tr('كودي يقرأ جودة الصور لما يشتغل. نعيد تشغيله الحين؟'),
                                                            nolabel=tr('لاحقًا'), yeslabel=tr('أعد التشغيل')):
                xbmc.executebuiltin('RestartApp')
            self.refresh_after = True
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
