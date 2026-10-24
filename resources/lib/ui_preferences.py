# -*- coding: utf-8 -*-
"""Shared inline skin preferences; small property updates, no skin reload."""
import xbmc
import xbmcgui

# key: (default, choices, Arabic label, English label)
OPTIONS = {
    'homeui_show_row_source': ('true', None, 'إظهار مصدر الصف', 'Show row source'),
    'homeui_title_style': ('logo', ('logo', 'text'), 'عنوان العمل', 'Title style'),
    'homeui_background_style': ('cinematic', ('cinematic', 'soft', 'dark', 'fullscreen'), 'نمط الخلفية', 'Background style'),
    'library_split_media': ('true', None, 'فصل الأفلام والمسلسلات في المكتبة', 'Separate movie and show library rows'),
    'player_badges_visible': ('true', None, 'إظهار رموز الجودة في المشغّل', 'Show player badges'),
    'player_badges_brightness': ('solid', ('solid', 'soft'), 'وضوح رموز الجودة', 'Badge appearance'),
    # v5.10.286: two new Home layouts (cinema, netflix); cinematic stays the default
    'homeui_layout_mode': ('cinematic', ('legacy', 'cinema', 'netflix', 'two_rows', 'cinematic', 'shelf', 'spotlight_posters'), 'وضع الواجهة', 'Home layout'),
    'homeui_nav_position': ('top', ('top', 'side'), 'مكان تبويبات الأقسام', 'Section tabs position'),
    'remember_last_source': ('false', None, 'إعادة آخر نسخة شغّلتها للعمل', 'Replay the last version played for this title'),
    'continue_known_source_direct': ('true', None, 'متابعة المشاهدة من المصدر المعروف', 'Resume the known source directly'),
    'library_playback_mode': ('dexhub', ('native','dexhub'), 'تشغيل عناصر المكتبات', 'Library playback'),
    # v5.10.156 (issue #6): who uses no Plex, Emby, Jellyfin or Silo hides the Servers section
    'show_servers': ('true', None, 'قسم السيرفرات', 'Servers section'),
    'iptv_epg_enabled': ('true', None, 'دليل البرامج EPG', 'Programme guide EPG'),
    'iptv_show_live': ('true', None, 'قسم القنوات IPTV', 'IPTV channels section'),
    'iptv_show_vod': ('true', None, 'قسم VOD', 'VOD section'),
    'iptv_show_movies': ('true', None, 'أفلام VOD', 'VOD movies'),
    'iptv_show_series': ('true', None, 'مسلسلات VOD', 'VOD series'),
    # v5.10.239: IPTV films and episodes stopped half way in Continue Watching
    'iptv_in_continue': ('true', None, 'أفلام ومسلسلات IPTV في متابعة المشاهدة', 'IPTV films and series in Continue Watching'),
    # v5.10.245: Back in the player: the video keeps playing behind the
    # Home (true) or stops (false); homeui/keymap.py writes the map
    'video_back_background': ('true', None, 'زر رجوع في المشغل يخلي الفيديو شغال في الخلفية',
                              'Back in the player keeps the video playing behind'),
    # v5.10.146: the player's badges above the seek bar, or under the details as before
    'player_badges_size': ('auto', ('auto', 'small', 'original'), 'حجم رموز المشغّل', 'Player badge size'),
    'player_badges_place': ('seekbar', ('seekbar', 'info'), 'مكان الرموز في المشغّل', 'Badges place in the player'),
    # v5.10.286 (issue #18): the badges at full opacity by default; 85 or 70 % on request
    'player_badges_opacity': ('100', ('100', '85', '70'), 'شفافية رموز الجودة', 'Badge opacity'),
    # v5.10.301: the sources window's rows: the add-on's own lines as Nuvio
    # shows them (full) or Dex Hub's compact rows
    'sources_row_style': ('full', ('full', 'compact'), 'شكل صفوف المصادر', 'Source rows'),
    # v5.10.298: the player bar's look (osd_look.py): style, darkness, colour
    'player_osd_style': ('classic', ('classic', 'glass', 'cinematic', 'minimal'), 'نمط شريط المشغّل', 'Player bar style'),
    'player_osd_dim': ('70', ('0', '15', '30', '50', '60', '70', '80', '85', '95'), 'تعتيم خلفية المشغّل', 'Player background darkness'),
    'player_osd_shape': ('circle', ('circle', 'square', 'ring', 'line', 'dot', 'soft'), 'شكل المؤشر في المشغّل', 'Player cursor shape'),
    # v5.10.302: the shades' colour and place, the buttons' backing, ready looks
    'player_osd_scrim': ('auto', ('auto', 'black', 'theme', 'artwork', 'navy', 'plum', 'wine', 'forest', 'slate', 'warm'), 'لون ظل المشغّل', 'Player shade colour'),
    'player_osd_area': ('bottom', ('bottom', 'both', 'none'), 'مكان ظل المشغّل', 'Player shade place'),
    'player_osd_buttons': ('none', ('none', 'soft', 'outline'), 'خلفية أزرار المشغّل', 'Player button backing'),
    'player_osd_preset': ('custom', ('custom', 'nuvio', 'appletv', 'netflix', 'infuse', 'plex', 'disney', 'artwork', 'clean', 'theme'), 'شكل جاهز للمشغّل', 'Ready player look'),
    'player_osd_accent': ('white', ('white', 'theme', 'artwork', 'violet', 'blue', 'cyan', 'green', 'gold', 'orange', 'red', 'pink'),
                          'لون المشغّل', 'Player colour'),
    # v5.10.309: the pill's name, the 3.28.49 bar and title pills as options
    'player_osd_names': ('true', None, 'اسم الزر داخل الكبسولة', 'Button name in the pill'),
    'player_osd_design': ('legacy', ('legacy', 'classic'), 'تصميم أزرار المشغّل', 'Player buttons design'),
    'player_title_actions': ('icons', ('icons', 'pills'), 'أزرار صفحة العمل', 'Title page buttons'),
    # v5.10.311: the player's subtitle list shows only your languages (sync,
    # next subtitle and auto pick still use every track)
    # v5.10.311: the title page asks for the sources while you look at it
    'sources_warm': ('true', None, 'تجهيز المصادر مسبقاً في صفحة العمل', 'Get sources ready on the title page'),
    'player_subtitle_filter': ('off', ('off', 'mine', 'first'), 'تصفية الترجمات في المشغّل', 'Subtitle filter in the player'),
    # v5.10.405: on by default (a plain screen while the source opened);
    # never over a playing video or a source switched in the player
    'player_loading_art': ('true', None, 'خلفية وشعار أثناء التحميل', 'Artwork while loading'),
    'player_show_clearlogo': ('true', None, 'شعار العمل في المشغّل', 'Work logo in player'),
    'player_logo_position': ('above_info', ('above_info', 'top_left', 'top_right'), 'مكان شعار العمل', 'Work logo position'),
    'player_logo_size': ('normal', ('small', 'normal', 'large'), 'حجم شعار العمل (Clear logo)', 'Clear logo size'),
    'player_show_plot': ('true', None, 'النبذة في المشغّل', 'Plot in player'),
    'player_plot_size': ('normal', ('small', 'normal', 'large'), 'حجم خط الوصف (Plot)', 'Plot text size'),
    'player_plot_width': ('normal', ('short', 'normal', 'wide'), 'عرض الوصف', 'Plot width'),
    'player_plot_scroll': ('false', None, 'تمرير الوصف الطويل تلقائيًا', 'Scroll long plots automatically'),
    'player_show_metadata': ('true', None, 'بيانات العمل في المشغّل', 'Metadata in player'),
    'player_show_ratings': ('true', None, 'التقييمات في المشغّل', 'Ratings in player'),
    'player_show_studio': ('true', None, 'شعار الشبكة في المشغّل', 'Network logo in player'),
    'player_studio_size': ('normal', ('small', 'normal', 'large'), 'حجم شعار الاستديو', 'Studio logo size'),
    'player_studio_position': ('bottom_right', ('bottom_right', 'bottom_left', 'top_right', 'top_left'), 'مكان شعار الاستديو', 'Studio logo position'),
    'metadata_badges': ('true', None, 'تعزيز التقييمات وشعار الشبكة', 'Supplement ratings & network logo'),
    # v5.10.322 (GitHub #21): IMDb's Parents Guide on the title page and when a video starts
    'parents_guide': ('true', None, 'دليل الوالدين من IMDb', 'IMDb Parents Guide'),
    'badges_home': ('true', None, 'الرموز في صفحة السيرفرات', 'Badges on the servers page'),
    'homeui_title_poster': ('true', None, 'إظهار البوستر في صفحة العمل', 'Show poster on the title page'),
    'homeui_trailers': ('true', None, 'التريلر التلقائي', 'Automatic trailers'),
    'homeui_trailer_sound': ('true', None, 'صوت التريلر', 'Trailer sound'),
    'homeui_trailer_delay': ('3', tuple(str(n) for n in range(16)), 'انتظار التريلر', 'Trailer delay'),
    'homeui_trailer_quality': ('720p', ('480p', '720p', '1080p'), 'جودة التريلر', 'Trailer quality'),
    'homeui_immersive': ('true', None, 'التريلر بملء الشاشة', 'Fullscreen trailer'),
    # v5.10.364: how long a trailer waits before it fills the screen
    'homeui_immersive_delay': ('16', ('4', '6', '8', '10', '12', '16', '20'), 'ملء الشاشة بعد', 'Full screen after'),
    # v5.10.364: the banner's titles in a new order every day
    'homeui_hero_shuffle': ('false', None, 'خلط أعمال البانر', 'Shuffle banner titles'),
    # v5.10.378: white focus, the hero's line as words, short row names (skin33)
    'homeui_clean_look': ('true', None, 'مظهر نظيف', 'Clean look'),
    # v5.10.379: on the spotlight, the rows go down and the title fills the page (skin34)
    'homeui_spot_section': ('true', None, 'سبوت لايت كامل', 'Full spotlight'),
    # v5.10.379: how many titles the spotlight shows (spot_source)
    'homeui_spot_count': ('8', ('5', '8', '10', '12', '15', '20'), 'عدد أعمال السبوت لايت', 'Spotlight titles'),
    # v5.10.365: the Home's rows and pictures made ready while the screensaver runs
    'homeui_ss_preload': ('true', None, 'تجهيز الرئيسية وقت شاشة التوقف', 'Prepare the Home during the screensaver'),
    # v5.10.326: the trailer behind the page or inside the focused card
    'homeui_trailer_mode': ('background', ('background', 'card'), 'مكان التريلر', 'Trailer place'),
    'homeui_carousel': ('true', None, 'تدوير البانر', 'Banner carousel'),
    'homeui_carousel_interval': ('10', tuple(str(n) for n in range(5, 31)), 'مدة البانر', 'Banner interval'),
    'homeui_kenburns': ('true', None, 'تحريك الخلفيات', 'Animated backgrounds'),
    'homeui_focus_gif': ('true', None, 'الصور المتحركة', 'Animated images'),
    'homeui_plot_scroll': ('false', None, 'تحريك النبذة', 'Scrolling plot'),
    # v5.10.288: side rail as icons only; top bar hidden while scrolling the rows
    'homeui_nav_icons_only': ('false', None, 'القائمة الجانبية أيقونات فقط', 'Sidebar as icons only'),
    'homeui_nav_autohide': ('false', None, 'إخفاء القائمة عند التمرير', 'Hide the menu while scrolling'),
    # v5.10.380: immediate tab focus, with IPTV kept click-only
    'homeui_nav_focus_open': ('true', None, 'فتح الأقسام بالتمرير', 'Open sections on focus'),
    # v5.10.363 (GitHub #27): Back on the Home's menu asks to exit Kodi (skin.dexhub 3.28.92)
    'homeui_back_exit': ('true', None, 'الرجوع من القائمة يسأل عن الخروج', 'Back on the menu asks to exit Kodi'),
    # v5.10.334: the Home's backdrop changes once the cursor rests (lighter scrolling)
    'homeui_bg_settle': ('true', None, 'الخلفية تتبع التحديد بعد التوقف', 'Backdrop waits for the cursor'),
    # v5.10.334: the title, genres and ratings step aside while a trailer plays
    'homeui_trailer_clean': ('true', None, 'تريلر بدون كتابة', 'Clean trailer'),
    # v5.10.322 (GitHub #20): the rating at the end of the meta line; the
    # two rows layout with smaller buttons and row names and a wider plot
    'homeui_rating_inline': ('true', None, 'التقييم في سطر التفاصيل', 'Rating on the details line'),
    'homeui_compact_hero': ('true', None, 'هيرو الصفين أصغر', 'Smaller two rows hero'),
    'homeui_hide_plot': ('false', None, 'إخفاء الحبكة في الواجهة الرئيسية', 'Hide the plot on the Home'),
    # v5.10.189: the top bar's tabs are icons; names only on request
    'homeui_nav_names': ('false', None, 'أسماء الأقسام في الشريط العلوي', 'Tab names in the top bar'),
    'homeui_focus_motion': ('zoom', ('zoom', 'lift', 'pop', 'glow'), 'حركة التحديد', 'Focus motion'),
    # v5.10.141: unfocused rows faded to 60 % and made the Home look grey;
    # light (85 %) by default
    'homeui_row_dim': ('light', ('light', 'off', 'strong'), 'تعتيم الصفوف غير المحددة', 'Dim unfocused rows'),
    'homeui_light_mode': ('true', None, 'الوضع الخفيف', 'Light mode'),
    'homeui_live_preview': ('true', None, 'معاينة القنوات', 'Channel previews'),
    'homeui_show_continue': ('true', None, 'صف متابعة المشاهدة', 'Continue Watching row'),
    'homeui_show_nextup': ('false', None, 'صف الحلقة التالية', 'Next Up row'),
    'favorites_layout': ('separate', ('separate', 'merged'), 'ترتيب المفضلة', 'Favorites layout'),
}
DISPLAY_ONLY = frozenset(('homeui_title_poster',)) | frozenset(k for k in OPTIONS if k.startswith('player_') or k in ('homeui_trailers', 'homeui_trailer_mode', 'homeui_trailer_delay', 'homeui_trailer_sound', 'homeui_trailer_quality', 'homeui_immersive', 'homeui_carousel', 'homeui_carousel_interval', 'homeui_kenburns', 'homeui_focus_gif', 'homeui_plot_scroll', 'homeui_hide_plot', 'homeui_rating_inline', 'homeui_compact_hero', 'homeui_nav_icons_only', 'homeui_nav_autohide', 'homeui_nav_focus_open', 'homeui_back_exit', 'homeui_bg_settle', 'homeui_immersive_delay', 'homeui_ss_preload', 'homeui_clean_look', 'homeui_spot_section', 'homeui_spot_count', 'homeui_trailer_clean', 'homeui_focus_motion', 'homeui_live_preview', 'homeui_nav_names')) | frozenset(('show_servers', 'metadata_badges', 'badges_home', 'iptv_epg_enabled', 'iptv_show_live', 'iptv_show_vod', 'iptv_show_movies', 'iptv_show_series', 'favorites_layout', 'player_badge_style', 'player_badges_json_url', 'player_badges_folder', 'homeui_row_dim'))
# Source/player choices affect the next handoff, not catalog contents. The
# service already invalidates settings and publishes player properties first.
DISPLAY_ONLY |= frozenset(('homeui_show_row_source', 'homeui_title_style', 'homeui_background_style', 'homeui_poster_scale', 'homeui_layout_mode', 'homeui_nav_position', 'remember_last_source', 'source_resolution_mode',
    'playback_open_mode', 'quick_same_source_play', 'continue_known_source_direct',
    'library_playback_mode', 'tmdbh_auto_play_first', 'tmdbh_poster_picker',
    'source_dedup_mode', 'quality_profile', 'min_quality', 'hide_cam_ts',
    'prefer_debrid', 'prefer_hdr', 'prefer_atmos', 'exclude_av1', 'exclude_dv',
    'exclude_hevc', 'bypass_all_filters', 'source_priority_order',
    'auto_next_episode', 'auto_next_prompt_seconds', 'next_episode_same_source',
    'use_service_upnext', 'upnext_data_encoding', 'introdb_manual_buttons',
    'subtitle_search_mode', 'subtitle_timeout', 'autosync_enabled', 'autosync_any',
    'autosync_min_match', 'autosync_notify', 'autosync_notify_skip', 'autosync_include_ai',
    'enable_source_formatter', 'follow_aiostreams_formatter', 'source_formatter_template'))


def _addon():
    from .settings_cache import cached_addon
    return cached_addon()


def hero_order(items, source_id=''):
    """v5.10.364: the banner's titles, shuffled when 'Shuffle banner titles'
    is on: one order a day for each catalogue, so a refresh does not reshuffle
    them and a catalogue that changes once a day still shows other titles."""
    items = list(items)
    try:
        if not enabled('homeui_hero_shuffle'):
            return items
        import random
        import time
        random.Random('%s:%s' % (time.strftime('%Y%m%d'), source_id)).shuffle(items)
    except Exception:
        pass
    return items


def _migrate_focus_navigation():
    """Enable the requested focus behavior once; later opt-outs are respected."""
    addon = _addon()
    if addon.getSetting('homeui_nav_focus_migrated') == 'true':
        return
    if addon.setSetting('homeui_nav_focus_open', 'true') is not False:
        addon.setSetting('homeui_nav_focus_migrated', 'true')


def _migrate_loading_art():
    """v5.10.409: on once. Before 5.10.405 it was off by default and Kodi
    kept that "false" in the user's settings, so the new default never came
    (the log: "loading art: not shown (turned off in the settings)").
    Turning it off again afterwards is respected."""
    addon = _addon()
    if addon.getSetting('player_loading_art_migrated') == 'true':
        return
    if addon.setSetting('player_loading_art', 'true') is not False:
        addon.setSetting('player_loading_art_migrated', 'true')


def value(key):
    default, choices, _, _ = OPTIONS[key]
    if key == 'homeui_nav_focus_open':
        _migrate_focus_navigation()
    if key == 'player_loading_art':
        _migrate_loading_art()
    result = _addon().getSetting(key) or default
    if key == 'homeui_trailer_mode':
        from .trailer_preferences import display_mode
        return display_mode(result)
    if key == 'homeui_back_exit':
        # v5.10.367: never on CoreELEC, LibreELEC or OSMC (the skin reads
        # dhs.pref.homeui_back_exit: false keeps Back as it was)
        try:
            from .power import appliance
            if appliance():
                return 'false'
        except Exception:
            pass
    if key == 'library_playback_mode':
        # Old Kodi labelenum editors saved the translated label. Playback and
        # its visible selector must interpret that existing value identically.
        from .library_mode import library_direct
        result = 'native' if library_direct(_addon()) else 'dexhub'
    elif choices:
        aliases = {
            'player_logo_position': {'above work details':'above_info', 'فوق بيانات العمل':'above_info',
                                     'top left':'top_left', 'أعلى اليسار':'top_left',
                                     'top right':'top_right', 'أعلى اليمين':'top_right'},
            'favorites_layout': {'lists by source':'separate', 'قوائم حسب المصدر':'separate',
                                 'merged':'merged', 'مجمّعة':'merged'},
            'homeui_row_dim': {'light':'light', 'خفيف':'light', 'off':'off', 'بدون':'off',
                               'strong':'strong', 'قوي':'strong'},
        }
        result = aliases.get(key, {}).get(str(result).casefold(), result)
    if choices and result not in choices:
        # A slider may have stored "3.0"; preserve its valid numeric choice.
        try:
            result = str(int(float(result)))
        except (ValueError, TypeError):
            result = default
        return result if result in choices else default
    return ('false' if str(result).lower() == 'false' else 'true') if choices is None else result


def enabled(key):
    return value(key) == 'true'


def publish():
    win = xbmcgui.Window(10000)
    for key in OPTIONS:
        shown = value(key)
        prop = 'dhs.pref.' + key
        if win.getProperty(prop) != shown:
            win.setProperty(prop, shown)
    from . import playback_choices
    from .i18n import tr
    shown = tr({'choose':'اختيار المصدر من النتائج', 'best':'أفضل نتيجة تلقائيًا',
                'same':'مصدر العمل أو السيرفر نفسه', 'tmdbh':'تشغيل عبر TMDb Helper'}[playback_choices.mode(_addon())])
    if win.getProperty('dhs.playback.mode') != shown:
        win.setProperty('dhs.playback.mode', shown)
    # v5.10.288: the player bar's buttons and their order (skin.dexhub 3.28.40 slots)
    try:
        from . import osd_buttons
        osd_buttons.publish()
    except Exception:
        pass
    try:
        from . import osd_look
        osd_look.publish()
    except Exception:
        pass


def _applied_path():
    try:
        import os
        import xbmcvfs
        folder = xbmcvfs.translatePath(_addon().getAddonInfo('profile'))
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, 'skin_strings.json')
    except Exception:
        return ''


def _applied():
    try:
        import json
        with open(_applied_path(), encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember_applied(values):
    try:
        import json
        path = _applied_path()
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(values, fh)
        import os
        os.replace(tmp, path)
    except Exception:
        pass


def sync_skin_place(reason='settings'):
    """Synchronize the player design and title page button includes.

    Badge placement is live in skin 3.28.113 and needs no reload.
    What was applied is kept in skin_strings.json and that
    is what a change is measured against: 5.10.310 compared with the skin
    string read back from a service thread, which could stay "stale" and
    reload the skin on every settings write (a source window was closed by
    a reload 0.5 s after it opened). One reload per real change, never more
    than one a minute, never under a playing video, always logged."""
    try:
        if xbmc.getSkinDir() != 'skin.dexhub':
            return False
        want = {'DexHub.OsdDesign': value('player_osd_design'),
                'DexHub.TitleActions': value('player_title_actions')}
        empty = {'DexHub.OsdDesign': 'legacy', 'DexHub.TitleActions': 'icons'}
        applied = _applied()
        if not applied:
            # first run: what the skin holds now
            applied = dict((k, xbmc.getInfoLabel('Skin.String(%s)' % k) or empty[k]) for k in want)
            _remember_applied(applied)
        stale = [(k, v) for k, v in want.items() if applied.get(k, empty[k]) != v]
        if not stale:
            return False
        if xbmc.Player().isPlayingVideo():
            # v5.10.151: never reload the skin under a playing video (Kodi
            # pauses it and leaves the player for the reload); the next
            # settings change after playback does it
            return False
        win = xbmcgui.Window(10000)
        import time
        try:
            last = float(win.getProperty('dexhub.skin_strings.reload') or 0)
        except ValueError:
            last = 0.0
        if time.time() - last < 60:
            xbmc.log('[DexHub] skin strings: reload skipped (one a minute)', xbmc.LOGINFO)
            return False
        for name, v in stale:
            xbmc.executebuiltin('Skin.SetString(%s,%s)' % (name, v))
        applied.update(dict(stale))
        _remember_applied(applied)
        win.setProperty('dexhub.skin_strings.reload', '%.0f' % time.time())
        xbmc.log('[DexHub] skin strings: %s changed (%s): reloading the skin' % (
            ', '.join('%s=%s' % kv for kv in stale), reason), xbmc.LOGINFO)
        xbmc.executebuiltin('ReloadSkin()')
        return True
    except Exception:
        return False



def set_value(key, selected):
    if key not in OPTIONS:
        return False
    choices = OPTIONS[key][1] or ('true', 'false')
    if selected not in choices:
        return False
    if key in ('homeui_layout_mode', 'homeui_nav_position'):
        from .home_preferences import write_setting
        write_setting(key, selected)
    elif key == 'homeui_trailer_mode':
        from .trailer_preferences import write_setting
        write_setting(key, selected)
    else:
        _addon().setSetting(key, selected)
    from .settings_cache import invalidate
    invalidate()
    publish()
    if key in ('player_show_studio', 'player_show_ratings', 'metadata_badges'):
        from . import player_presentation
        player_presentation.refresh_current()
    if key == 'video_back_background':
        try:
            from .homeui import keymap
            keymap.always()
        except Exception:
            pass
    if key.startswith('player_') and xbmc.getSkinDir() == 'skin.dexhub':
        xbmc.executebuiltin('Skin.SetBool(OSD.DisplayInfoOnControls)')
    if key.startswith('player_osd_'):
        # v5.10.302: a ready look writes its parts; a part makes it custom
        try:
            from . import osd_look
            osd_look.after_write(key, selected)
        except Exception:
            pass
    if key == 'homeui_show_row_source':
        # v5.10.286: skin.dexhub publishes its rows again with or without the source line
        try:
            from .homeui.layout import _skin_follows
            _skin_follows()
        except Exception:
            pass
    if key in ('player_osd_design', 'player_title_actions'):
        # v5.10.310: these two are read when the skin loads
        sync_skin_place('you changed %s' % key)
    # Badge placement is runtime visibility in skin 3.28.113. Moving it
    # under a playing video must never reload the skin or pause playback.
    try:
        from . import cloud_look      # v5.10.366
        cloud_look.mark()
    except Exception:
        pass
    return True


def edit(key, txt):
    if key not in OPTIONS:
        return False
    _, choices, ar, en = OPTIONS[key]
    current = value(key)
    if choices is None:
        return set_value(key, 'false' if current == 'true' else 'true')
    labels = {
        'logo': txt('شعار العمل', 'Clear logo'), 'text': txt('نص', 'Text'),
        'soft': txt('هادئ', 'Soft'), 'dark': txt('داكن', 'Dark'), 'solid': txt('واضح بلا تعتيم', 'Solid'),
        'fullscreen': txt('ملء الشاشة', 'Full screen'),  # v5.10.374 (skin.dexhub 3.28.98)
        'cinematic': txt('سينمائي', 'Cinematic') if key == 'homeui_background_style' else txt('هيرو وصفوف', 'Hero and rows'),
        'shelf': txt('رف بطاقات عريضة', 'Wide card shelf'),
        'legacy': txt('ليقسي', 'Legacy'),  # v5.10.325: the Nuvio Legacy stage
        'two_rows': txt('هيرو مختصر وصفين', 'Compact hero and two rows'),
        'cinema': txt('سينمائي ملء الشاشة', 'Cinematic full screen'),  # v5.10.286
        'netflix': txt('هيرو وصف واحد', 'Hero with one row'),  # v5.10.286
        'spotlight_posters': txt('Spotlight وبوسترات', 'Spotlight and posters'),
        'background': txt('خلفية الشاشة', 'Background'),
        'card': txt('داخل البطاقة', 'Inside the card'),
        'top': txt('شريط علوي', 'Top tabs'),
        'side': txt('قائمة جانبية', 'Sidebar'),
        'auto': txt('تلقائي · متناسق', 'Auto · balanced'),
        'small': txt('صغير', 'Small'),
        'normal': txt('متوسط', 'Medium'),
        'large': txt('كبير', 'Large'),
        'short': txt('مختصر', 'Compact'),
        'wide': txt('عريض', 'Wide'),
        'original': txt('الحجم الأصلي', 'Original size'),
        'above_info': txt('فوق بيانات العمل', 'Above work details'),
        'seekbar': txt('فوق شريط التقدم', 'Above the seek bar'),
        'info': txt('تحت بيانات العمل', 'Under the work details'),
        'top_left': txt('أعلى اليسار', 'Top left'),
        'top_right': txt('أعلى اليمين', 'Top right'),
        'bottom_left': txt('أسفل اليسار', 'Bottom left'),
        'bottom_right': txt('أسفل اليمين', 'Bottom right'),
        'separate': txt('قوائم مستقلة حسب المصدر', 'Separate lists by source'),
        'merged': txt('قائمة موحدة', 'Combined list'),
        'light': txt('خفيف', 'Light'),
        'off': txt('بدون', 'None'),
        'strong': txt('قوي', 'Strong'),
        'native': txt('من مصدر المكتبة مباشرة', 'Direct from library source'),
        'dexhub': txt('بحث المصادر عبر Dex Hub', 'Search sources through Dex Hub'),
        'zoom': txt('تكبير ناعم', 'Soft zoom'),
        'lift': txt('رفع للأعلى', 'Lift'),
        'pop': txt('قفزة', 'Pop'),
        'glow': txt('إضاءة فقط', 'Glow only'),
        'stage': txt('خلف الصفحة، ثم ملء الشاشة', 'Behind the page, then full screen'),
        'full': txt('مثل Nuvio TV', 'Like Nuvio TV'),
        'compact': txt('مختصرة', 'Compact'),
    }
    shown = [labels.get(v, v + txt(' ثوانٍ', ' seconds') if key.endswith(('delay', 'interval')) else v) for v in choices]
    if key == 'player_badges_opacity':  # v5.10.286
        shown = [v + '%' for v in choices]
    if key.startswith('player_osd_'):  # v5.10.298, v5.10.302: every player look key
        from . import osd_look
        shown = osd_look.labels(key, txt)
    pick = xbmcgui.Dialog().select(txt(ar, en), shown, preselect=choices.index(current))
    return set_value(key, choices[pick]) if pick >= 0 else True


def player_dialog(txt):
    """Equivalent controls remain available when another skin is active."""
    from . import player_controls
    groups = list(player_controls.GROUPS)
    grouped = {k for row in player_controls.GROUPS.values() for k in row[2]}
    keys = [k for k in OPTIONS if k.startswith('player_') and k not in grouped]
    while True:
        labels = [txt('شكل رموز الجودة · صور ومعاينة', 'Quality badge style · preview')]
        labels += [txt(*player_controls.GROUPS[g][:2]) for g in groups]
        display = {'true': txt('مفعّل', 'On'), 'false': txt('مطفأ', 'Off'), 'above_info': txt('فوق البيانات', 'Above details'), 'top_left': txt('أعلى اليسار', 'Top left'), 'top_right': txt('أعلى اليمين', 'Top right'), 'seekbar': txt('فوق شريط التقدم', 'Above the seek bar'), 'info': txt('تحت البيانات', 'Under the details'), 'legacy': txt('مثل Nuvio legacy', 'Like Nuvio legacy'), 'classic': txt('الكلاسيكي السابق', 'Previous classic'), 'icons': txt('أيقونات', 'Icons'), 'pills': txt('كبسولات نصية', 'Text pills'), 'off': txt('الكل', 'All languages'), 'mine': txt('لغاتي فقط', 'My languages only'), 'first': txt('لغتي الأولى فقط', 'My first language only')}
        from . import osd_look
        labels += [txt(OPTIONS[k][2], OPTIONS[k][3]) + ' · ' + (osd_look.label(k, value(k), txt) if k in osd_look.KEYS else display.get(value(k), value(k))) + ('%' if k == 'player_badges_opacity' else '') for k in keys]
        pick = xbmcgui.Dialog().select(txt('مظهر المشغّل', 'Player appearance'), labels)
        if pick < 0:
            return True
        if pick == 0:
            from . import player_badges
            player_badges.show(txt)
        elif pick <= len(groups):
            player_controls.show(groups[pick - 1])
        else:
            edit(keys[pick - len(groups) - 1], txt)


def iptv_available(media=''):
    if media == 'channels':
        return enabled('iptv_show_live')
    if media in ('movie', 'series'):
        return enabled('iptv_show_vod') and enabled('iptv_show_movies' if media == 'movie' else 'iptv_show_series')
    return iptv_available('channels') or iptv_available('movie') or iptv_available('series')
