# -*- coding: utf-8 -*-
"""One menu map shared by popup cards, simple lists and settings pages."""
GROUPS = (
 ('player','المشغّل','Player','التشغيل وأزرار المشغّل وشكله والحلقة التالية','Playback, player controls, appearance and next episode','circle-play'),
 ('trailers','التريلر','Trailers','التشغيل التلقائي والصوت والجودة وملء الشاشة','Automatic trailers, sound, quality and fullscreen','clapperboard'),
 ('theme','الثيم والمظهر','Theme & appearance','الثيم والألوان والزجاج والتحديد','Theme, colours, glass and focus','palette'),
 ('home','الرئيسية والصفوف','Home & rows','التخطيط والتنقّل والبطاقات والبانر','Layout, navigation, cards and featured banner','house'),
 ('metadata','الميتاداتا والصور','Metadata & artwork','المصادر واللغة والبوسترات والشعارات والتقييمات','Sources, language, posters, logos and ratings','image'),
 ('sources','مصادر التشغيل','Stream sources','إضافات البحث والفلترة وترتيب روابط التشغيل','Source add-ons, filters and stream ordering','server'),
 ('subtitles','الترجمة','Subtitles','البحث واللغة والتصفية والمزامنة','Search, language, filters and synchronization','comment-dots'),
 ('accounts','الحسابات والمزامنة','Accounts & sync','الربط وإدارة الحسابات والمزامنة والمشاهدة الجماعية','Connect accounts, sync and watch parties','user-gear'),
 ('library','المكتبة والمتابعة','Library & progress','المفضلة والقوائم وسجل المشاهدة والحلقات','Favourites, lists, watch history and episodes','list-check'),
 ('live','IPTV والسيرفرات','IPTV & servers','القنوات وVOD ودليل البرامج ومكتبات السيرفرات','Live TV, VOD, programme guide and server libraries','display'),
 ('skin','السكين واللغة','Skin & language','السكين المؤقت والتبديل واللغة والحجم والأصوات','Temporary skin, switching, language, size and sounds','brush'),
 ('maintenance','الصيانة والنسخ الاحتياطي','Maintenance & backup','الأداء والكاش والتشخيص والنسخ والاستعادة','Performance, cache, diagnostics, backup and restore','gears'),
 ('system','كودي والخروج','Kodi & exit','إضافات كودي وإعداداته والخروج وإعادة التشغيل','Kodi add-ons, settings, exit and restart','power-off'),
)
GROUPS = tuple(sorted(GROUPS,key=lambda row: ['theme', 'home', 'trailers', 'player', 'metadata', 'sources', 'subtitles', 'accounts', 'library', 'live', 'skin', 'maintenance', 'system'].index(row[0])))
META={row[0]:row for row in GROUPS}
KEY_GROUP={}
def _keys(group, keys):
 for key in keys.split():KEY_GROUP[key]=group
_keys('trailers','homeui_trailers homeui_trailer_sound homeui_trailer_delay homeui_trailer_quality homeui_trailer_volume homeui_trailer_mode homeui_trailer_clean homeui_immersive homeui_immersive_delay')
_keys('theme','theme_preset homeui_clean_look homeui_focus_motion homeui_background_style')
_keys('metadata','homeui_metadata_language server_metadata metadata_badges parents_guide meta_source_cache_days meta_source_warm_pages global_meta_source_all poster_alerts')
_keys('sources','remember_last_source continue_known_source_direct library_playback_mode sources_row_style sources_warm enable_source_formatter follow_aiostreams_formatter source_formatter_template elite_badges_enabled badges_sources_follow badges_sources_rows')
_keys('subtitles','subtitle_search_mode player_subtitle_filter autosync_enabled autosync_any autosync_min_match autosync_notify autosync_notify_skip autosync_include_ai')
_keys('live','show_servers homeui_live_preview badges_home iptv_epg_enabled iptv_show_live iptv_show_vod iptv_show_movies iptv_show_series iptv_in_continue')
_keys('library','library_split_media favorites_layout homeui_show_continue homeui_show_nextup')
_keys('skin','ui_language temporary_skin_on_launch af3_auto_layout homeui_size homeui_confirm_exit homeui_back_exit homeui_on_launch homeui_autostart homeui_ss_preload')
_keys('maintenance','homeui_light_mode lightweight_mode')
_keys('home','player_title_actions')
_keys('player','use_service_upnext upnext_data_encoding auto_next_episode auto_next_prompt_seconds next_episode_same_source video_back_background native_skip_mode')

ACTION_GROUP={}
def _actions(group, keys):
 for key in keys.split():ACTION_GROUP[key]=group
_actions('theme','appearance_options theme_select')
_actions('home','home_ui home_layout home_community collection_settings_hub')
_actions('skin','skin_pick skin_af3 skin_use skin_back home_screensaver')
_actions('player','track_preferences simple_episode_controls simple_tmdbh_install player_badges player_badge_packs badges_section player_appearance osd_buttons')
_actions('sources','playback_mode simple_source_behavior setup_quality source_addons shortcut_settings_dialog selfhost_settings_menu badges_url')
_actions('subtitles','setup_subtitles')
_actions('metadata','meta_source_picker meta_cache_reload betterposters_settings_dialog simple_fanarttv poster_pick_global meta_sources_menu simple_tmdb_api_key poster_review')
_actions('accounts','phone_setup nuvio_sync_menu simple_trakt sync_accounts_menu add_provider home_setup setup_connections')
_actions('library','simple_favorites simple_watching')
_actions('live','home_live media_libraries_menu')
_actions('maintenance','setup_diagnostics config_backup_menu clear_cache setup_stability simple_quick_setup')
_actions('system','open_kodi_addons open_kodi_settings temporary_skin_exit')

CONTROL_GROUP={}
def _controls(group, keys):
 for key in keys.split():CONTROL_GROUP[key]=group
_controls('player','audio_tracks player_element_capsule player_element_loading player_element_logo player_element_studio player_element_plot player_element_badges player osd_buttons osd_preset osd_style osd_dim osd_area osd_scrim osd_buttons_bg osd_shape osd_accent badges_visible badges_brightness badges episodes credits_panel')
_controls('trailers','trailers trailer_sound trailer_volume trailer_place trailer_clean immersive_after')
_controls('theme','glass_focus theme focus_colour focus_text kodi_focus amoled glow focus_glow clean_look focus_motion background_style')
_controls('home','catalogs search layout new_rows group_recs community refresh home_layout_mode nav_position nav_icons nav_autohide nav_focus_open bg_settle nav_names card_style title_poster title_style spotlight spot_count spot_section row_dim watched_titles row_source season_names rating_inline compact_hero kenburns focus_gif carousel plot_scroll hide_plot hero_shuffle')
_controls('metadata','image_quality meta_source meta_lang server_meta tmdb_key posters logos ratings ratings_pick meta_refresh')
_controls('sources','sources_style playback source_behavior source_addons add_source')
_controls('subtitles','subtitles')
_controls('accounts','phone setup sources nuvio trakt hub_extra party')
_controls('library','library follow library_split watching continue_split follow_order episode_art episode_titles follow_merge nextup_unaired lib_sort trakt_smart reminders')
_controls('live','servers')
_controls('skin','temporary_skin skin_pick skin_back skin screensaver ss_preload back_exit size ui_sounds')
_controls('maintenance','speed diagnostics backup clear_cache')
_controls('system','leave_dexhub classic kodi power android')

LEGACY={'30930':'skin','31000':'home','30931':'player','31140':'player','30934':'metadata','30933':'accounts','32910':'library','31060':'live','30935':'subtitles','30936':'maintenance','30937':'sources','32960':'player'}
CONTROL_ALIASES={'look_theme':'theme','look_layout':'home','look_motion':'trailers','following':'library','all_settings':'player'}

def group_for_setting(setting, source):
 key=setting.get('id','')
 if key in KEY_GROUP:return KEY_GROUP[key]
 if key.startswith('player_'):return 'player'
 if key.startswith('homeui_trailer') or key.startswith('homeui_immersive'):return 'trailers'
 if key.startswith('homeui_'):return 'home'
 if key.startswith('iptv_'):return 'live'
 if key.startswith(('library_','favorites_','continue_','nextup_')):return 'library'
 if key.startswith(('meta_','global_meta','poster_')):return 'metadata'
 if key.startswith(('subtitle_','autosync_')):return 'subtitles'
 if key.startswith('source_'):return 'sources'
 if key=='clear_cache_action':return 'maintenance'
 if setting.get('type')=='action':
  import re
  from urllib.parse import urlsplit,parse_qsl
  raw=setting.get('action','')
  match=re.search(r'plugin://[^)]+',raw)
  if match:
   params=dict(parse_qsl(urlsplit(match.group(0).split(',return')[0]).query))
   action=params.get('action','')
   if action=='simple_open_page':action=params.get('target','')
   if action in ACTION_GROUP:return ACTION_GROUP[action]
   if action in ('simple_settings','setup_center'):return None
   if action=='hub_search_menu':return 'home'
  if 'service.upnext' in raw:return 'player'
 return LEGACY.get(source,'skin')

def organize(categories, arabic):
 from xml.etree import ElementTree as ET
 groups={row[0]:[] for row in GROUPS};contexts={};seen_actions=set()
 for category,siblings in categories:
  source=category.get('label','')
  for index,setting in enumerate(siblings):
   contexts[id(setting)]=(siblings,index)
   if setting.get('visible','').lower()=='false' or setting.get('type') not in ('action','bool','enum','labelenum','slider','text','number','folder','preference'):continue
   if setting.get('type')=='action':
    signature=(setting.get('action',''),setting.get('visible',''),setting.get('enable',''))
    if signature in seen_actions:continue
    seen_actions.add(signature)
   group=group_for_setting(setting,source)
   if group:groups[group].append(setting)
 result=[]
 for key,ar,en,_,_,_ in GROUPS:
  category=ET.Element('category',{'id':key,'label':ar if arabic else en})
  result.append((category,groups[key]))
 return result,contexts

VALUE_LABELS={
 'above_info':('فوق بيانات العمل','Above title details'),'artwork':('ألوان العمل','Title artwork'),'auto':('تلقائي','Automatic'),'background':('خلفية الصفحة','Page background'),'black':('أسود','Black'),'blue':('أزرق','Blue'),'both':('أعلى وأسفل','Top and bottom'),'bottom':('أسفل','Bottom'),'bottom_left':('أسفل اليسار','Bottom left'),'bottom_right':('أسفل اليمين','Bottom right'),'card':('داخل البطاقة','Inside the card'),'cinema':('سينمائي بملء الشاشة','Fullscreen cinema'),'cinematic':('سينمائي','Cinematic'),'circle':('دائرة','Circle'),'classic':('كلاسيكي','Classic'),'clean':('نظيف','Clean'),'compact':('مختصر','Compact'),'custom':('مخصص','Custom'),'cyan':('سماوي','Cyan'),'dark':('داكن','Dark'),'dexhub':('بحث Dex Hub','Dex Hub search'),'dot':('نقطة','Dot'),'first':('لغتي الأولى','My first language'),'forest':('أخضر غامق','Forest green'),'full':('عرض كامل','Full layout'),'fullscreen':('خلفية كاملة','Full background'),'glass':('زجاجي','Glass'),'glow':('توهّج','Glow'),'gold':('ذهبي','Gold'),'green':('أخضر','Green'),'icons':('أيقونات','Icons'),'info':('تحت بيانات العمل','Below title details'),'large':('كبير','Large'),'legacy':('التخطيط الكلاسيكي','Legacy layout'),'lift':('ارتفاع','Lift'),'light':('خفيف','Light'),'line':('خط','Line'),'logo':('شعار العمل','Title logo'),'merged':('قائمة موحّدة','Combined list'),'mine':('لغاتي','My languages'),'minimal':('بسيط','Minimal'),'native':('من مصدر المكتبة','Library source'),'navy':('كحلي','Navy'),'none':('بدون','None'),'normal':('متوسط','Medium'),'off':('بدون','None'),'ok':('عند التأكيد','On confirmation'),'orange':('برتقالي','Orange'),'original':('الحجم الأصلي','Original size'),'outline':('إطار','Outline'),'pills':('كبسولات نصية','Text pills'),'pink':('وردي','Pink'),'plum':('بنفسجي غامق','Plum'),'pop':('نبضة','Pop'),'red':('أحمر','Red'),'ring':('حلقة','Ring'),'seekbar':('فوق شريط التقدم','Above the seek bar'),'separate':('قوائم منفصلة','Separate lists'),'shelf':('رف بطاقات','Card shelf'),'short':('مختصر','Compact'),'side':('قائمة جانبية','Sidebar'),'slate':('رمادي مزرق','Slate'),'small':('صغير','Small'),'soft':('هادئ','Soft'),'solid':('واضح','Solid'),'spotlight_posters':('سبوت لايت وبوسترات','Spotlight and posters'),'square':('مربع','Square'),'start':('عند بدء التشغيل','At playback start'),'strong':('قوي','Strong'),'text':('نص','Text'),'theme':('لون الثيم','Theme colour'),'top':('أعلى','Top'),'top_center':('أعلى الوسط','Top centre'),'top_left':('أعلى اليسار','Top left'),'top_right':('أعلى اليمين','Top right'),'two_rows':('صفّان','Two rows'),'violet':('بنفسجي','Violet'),'warm':('دافئ','Warm'),'white':('أبيض','White'),'wide':('عريض','Wide'),'wine':('عنابي','Wine'),'zoom':('تكبير','Zoom'),
 'stage':('خلف الصفحة ثم ملء الشاشة','Behind the page, then fullscreen'),'nuvio':('Nuvio','Nuvio'),'appletv':('Apple TV','Apple TV'),'netflix':('Netflix','Netflix'),'infuse':('Infuse','Infuse'),'plex':('Plex','Plex'),'disney':('Disney+','Disney+'),
}

def value_label(key,value,arabic):
 if value in ('true','false'):return ('مفعّل' if value=='true' else 'مطفأ') if arabic else ('On' if value=='true' else 'Off')
 if key=='homeui_background_style' and value=='fullscreen':return 'خلفية كاملة' if arabic else 'Full background'
 if key=='homeui_nav_position' and value=='top':return 'شريط علوي' if arabic else 'Top bar'
 if key=='homeui_layout_mode' and value in ('cinematic','netflix','two_rows'):return {'cinematic':('هيرو وصفوف','Hero and rows'),'netflix':('هيرو وصف واحد','Hero with one row'),'two_rows':('هيرو مختصر وصفّان','Compact hero and two rows')}[value][0 if arabic else 1]
 if key=='player_capsule_when' and value=='both':return 'عند التشغيل ومع OK' if arabic else 'At start and with OK'
 if value in VALUE_LABELS:return VALUE_LABELS[value][0 if arabic else 1]
 if value.replace('.','',1).isdigit() or value.endswith('p'):return value
 return value

# Old schemas store many ordinary controls as hidden because older menus owned
# them. Expose these in the add-on menu, keeping session data and migrations out.
EXPOSE = frozenset('''search_style continue_art_style catalog_click_mode catalog_genre_folders hide_empty_collection_entries
homeui_debug quality_profile bypass_all_filters min_quality hide_cam_ts prefer_debrid prefer_hdr prefer_atmos exclude_av1 exclude_dv exclude_hevc source_resolution_mode stremio_sources_enabled streams_quick_open_seconds streams_result_goal streams_enough_wait_seconds playback_open_mode quick_same_source_play introdb_manual_buttons timeout http_keepalive_pool show_playback_waiter safe_playback_handoff pre_cache_next_episode streams_full_parallel_scan streams_quick_to_results source_dedup_mode server_lookup_seconds search_ceiling_seconds kodi22_minimal_item anime_id_mapping
default_meta_source art_configuration_mode deep_meta_enrich poster_reliability_mode fanarttv_enrich fanarttv_api_key server_art_from_tmdb tmdbh_auto_play_first tmdbh_poster_picker
cloud_sync_direction cloud_sync_interval_min cloud_sync_continuous nuvio_sync_enabled nuvio_sync_direction nuvio_sync_addons nuvio_sync_progress nuvio_sync_collections nuvio_sync_library stremio_sync_enabled stremio_sync_direction stremio_sync_progress enable_trakt trakt_scrobble trakt_sync_progress trakt_fetch_art trakt_sync_watchlist trakt_service_sync_interval enable_simkl simkl_mark_watched simkl_watched_threshold simkl_service_sync watchlist_merge_simkl watchlist_merge_mdblist watchlist_merge_floppy tracking_sync_trakt tracking_sync_simkl tracking_sync_mdblist plex_in_sources emby_in_sources silo_in_sources enable_companion_sync companion_interval mdblist_api_key stremio_sync_addons
enable_stremio_subtitle_broker never_auto_ai_subtitles subtitle_timeout preferred_subtitle_langs
parallel_workers large_section_limit http_gzip clean_catalog_view hide_pagination_more catalog_cache_ttl meta_cache_ttl index_mode index_sync_interval_hours verbose_logging selfhost_enabled selfhost_aio_manifest_url dexworld_api_key library_export_root'''.split())
_keys('home','search_style catalog_click_mode catalog_genre_folders hide_empty_collection_entries clean_catalog_view hide_pagination_more')
_keys('library','continue_art_style library_export_root')
_keys('metadata','default_meta_source art_configuration_mode deep_meta_enrich poster_reliability_mode fanarttv_enrich fanarttv_api_key server_art_from_tmdb meta_cache_ttl')
_keys('sources','quality_profile bypass_all_filters min_quality hide_cam_ts prefer_debrid prefer_hdr prefer_atmos exclude_av1 exclude_dv exclude_hevc source_resolution_mode stremio_sources_enabled streams_quick_open_seconds streams_result_goal streams_enough_wait_seconds playback_open_mode quick_same_source_play streams_full_parallel_scan streams_quick_to_results source_dedup_mode server_lookup_seconds search_ceiling_seconds anime_id_mapping plex_in_sources emby_in_sources silo_in_sources selfhost_enabled selfhost_aio_manifest_url')
_keys('player','introdb_manual_buttons show_playback_waiter safe_playback_handoff pre_cache_next_episode kodi22_minimal_item tmdbh_auto_play_first tmdbh_poster_picker')
_keys('subtitles','enable_stremio_subtitle_broker never_auto_ai_subtitles subtitle_timeout preferred_subtitle_langs dexworld_api_key')
_keys('maintenance','homeui_debug timeout http_keepalive_pool parallel_workers large_section_limit http_gzip catalog_cache_ttl index_mode index_sync_interval_hours verbose_logging')

def expose_user_settings(categories,options):
 for _,siblings in categories:
  for setting in siblings:
   if setting.get('id') in EXPOSE and setting.get('id') not in options:
    setting.set('visible','true')
 return categories

_keys('metadata','metadata_refresh')
_controls('metadata','metadata_refresh')
_controls('home','follow_rows follow_works')

_keys('player','preferred_audio_language secondary_audio_language audio_prefer_original audio_avoid_commentary')
_keys('subtitles','embedded_subtitle_mode subtitle_sdh_preference')

_actions('player','av_settings')
_actions('theme','interface_fonts')
_controls('theme','interface_fonts')
_keys('theme','interface_font interface_font_scale')
