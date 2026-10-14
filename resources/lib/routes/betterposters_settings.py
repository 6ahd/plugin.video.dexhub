# -*- coding: utf-8 -*-
"""One lightweight, skin-independent BetterPosters settings dialog."""

import xbmc
import xbmcgui

from resources.lib import better_posters


def _select(title, labels, preselect=0):
    try:
        return xbmcgui.Dialog().select(title, labels, preselect=preselect)
    except TypeError:
        return xbmcgui.Dialog().select(title, labels)


def _on(value, txt):
    return txt('مفعّل', 'On') if value else txt('متوقف', 'Off')


def _set(addon, key, value):
    addon.setSetting(str(key), str(value))
    better_posters.invalidate()


def _toggle(addon, key):
    current = str(addon.getSetting(key) or 'false').strip().lower()
    _set(addon, key, 'false' if current in ('1', 'true', 'yes', 'on') else 'true')


def _edit_template(addon, txt, fallback=False):
    key = 'betterposters_fallback_template' if fallback else 'betterposters_url_template'
    current = addon.getSetting(key) or (
        better_posters.DEFAULT_FALLBACK_TEMPLATE if fallback else '')
    title = (txt('رابط الاحتياط غير المعرّب', 'Non-localized fallback URL')
             if fallback else txt('رابط البوستر المخصص', 'Custom poster URL'))
    try:
        from resources.lib import kb_private
        value = kb_private.dialog_input(title, defaultt=current, type=xbmcgui.INPUT_ALPHANUM)
    except Exception:
        return
    value = str(value or '').strip()
    if value and (not value.lower().startswith('https://') or '{imdb_id}' not in value):
        xbmcgui.Dialog().ok(
            'Dex Hub • BetterPosters',
            txt('يجب أن يبدأ الرابط بـ https:// ويحتوي {imdb_id}',
                'The URL must start with https:// and contain {imdb_id}'))
        return
    _set(addon, key, value)


def _preview(addon, txt):
    try:
        imdb_id = xbmcgui.Dialog().input('IMDb ID', defaultt='tt0111161',
                                         type=xbmcgui.INPUT_ALPHANUM)
    except Exception:
        return
    url = better_posters.build_url(
        str(imdb_id or '').strip(), better_posters.config(addon))
    if not url:
        xbmcgui.Dialog().ok(
            'Dex Hub • BetterPosters',
            txt('IMDb ID غير صالح؛ مثال: tt0111161',
                'Invalid IMDb ID; example: tt0111161'))
        return
    try:
        xbmc.executebuiltin('ShowPicture(%s)' % url)
    except Exception:
        xbmcgui.Dialog().ok('Dex Hub • BetterPosters', url)


def _reset(addon, txt):
    if not xbmcgui.Dialog().yesno(
            'Dex Hub • BetterPosters',
            txt('استعادة الإعداد المقترح: العربية، IMDb، Genre وTrend؟',
                'Restore the recommended Arabic, IMDb, Genre and Trend preset?')):
        return
    defaults = {
        'betterposters_enabled': 'true',
        'betterposters_language': 'ar',
        'betterposters_trend_tags': 'true',
        'betterposters_quality_tags': 'false',
        'betterposters_genre': 'true',
        'betterposters_rating': 'true',
        'betterposters_rating_source': 'IM',
        'betterposters_age_rating': 'false',
        'betterposters_url_template': '',
        'betterposters_fallback_template': better_posters.DEFAULT_FALLBACK_TEMPLATE,
        'betterposters_catalogs_enabled': 'true',
    }
    for key, value in defaults.items():
        addon.setSetting(key, value)
    better_posters.invalidate()


def show(addon, txt):
    """Run the complete editor as nested native dialogs.

    Unlike an ActivateWindow folder, this is safe while Kodi's Add-on Settings
    dialog is still open and does not import Dex Hub's large plugin router.
    """
    try:
        if xbmc.getCondVisibility('Window.IsVisible(addonsettings)'):
            xbmc.executebuiltin('Dialog.Close(addonsettings)')
            xbmc.sleep(180)
    except Exception:
        pass
    while True:
        cfg = better_posters.config(addon)
        lang_name = next((name for code, name in better_posters.LANGUAGES
                          if code == cfg.get('language')), cfg.get('language') or 'English')
        rating_name = next((name for code, name in better_posters.RATING_SOURCES
                            if code == cfg.get('rating_source')),
                           cfg.get('rating_source') or 'Average')
        rows = [
            'BetterPosters • %s' % _on(cfg.get('enabled'), txt),
            '%s • %s' % (txt('لغة البوستر والكتالوجات', 'Poster & catalog language'), lang_name),
            '%s • %s' % (txt('كتالوجات BetterPosters الثابتة', 'Built-in BetterPosters catalogs'), _on(cfg.get('catalogs_enabled'), txt)),
            '%s • %s' % (txt('وسم الترند', 'Trend tag'), _on(cfg.get('trend'), txt)),
            '%s • %s' % (txt('وسوم الجودة', 'Quality tags'), _on(cfg.get('quality'), txt)),
            '%s • %s' % (txt('التصنيف Genre', 'Genre'), _on(cfg.get('genre'), txt)),
            '%s • %s' % (txt('التقييم', 'Rating'), _on(cfg.get('rating'), txt)),
            '%s • %s' % (txt('مصدر التقييم', 'Rating source'), rating_name),
            '%s • %s' % (txt('التصنيف العمري', 'Age rating'), _on(cfg.get('age'), txt)),
            txt('رابط البوستر المخصص', 'Custom poster URL'),
            txt('رابط الاحتياط غير المعرّب', 'Non-localized fallback URL'),
            txt('معاينة واختبار IMDb ID', 'Preview with an IMDb ID'),
            txt('استعادة الإعداد المقترح', 'Restore recommended preset'),
            txt('رجوع', 'Back'),
        ]
        choice = _select('Dex Hub • BetterPosters', rows)
        if choice < 0 or choice == 13:
            return True
        if choice == 0:
            _toggle(addon, 'betterposters_enabled')
        elif choice == 1:
            langs = list(better_posters.LANGUAGES)
            pre = next((i for i, row in enumerate(langs)
                        if row[0] == cfg.get('language')), 0)
            picked = _select(txt('لغة البوستر', 'Poster language'),
                             [name for _code, name in langs], pre)
            if picked >= 0:
                value = langs[picked][0] or 'English'
                _set(addon, 'betterposters_language', value)
                _set(addon, 'betterposters_url_template', '')
        elif choice == 2:
            _toggle(addon, 'betterposters_catalogs_enabled')
        elif choice == 3:
            _toggle(addon, 'betterposters_trend_tags')
        elif choice == 4:
            _toggle(addon, 'betterposters_quality_tags')
        elif choice == 5:
            _toggle(addon, 'betterposters_genre')
        elif choice == 6:
            _toggle(addon, 'betterposters_rating')
        elif choice == 7:
            sources = list(better_posters.RATING_SOURCES)
            pre = next((i for i, row in enumerate(sources)
                        if row[0] == cfg.get('rating_source')), 1)
            picked = _select(txt('مصدر التقييم', 'Rating source'),
                             [name for _code, name in sources], pre)
            if picked >= 0:
                value = sources[picked][0] or 'Average'
                _set(addon, 'betterposters_rating_source', value)
                _set(addon, 'betterposters_url_template', '')
        elif choice == 8:
            _toggle(addon, 'betterposters_age_rating')
        elif choice == 9:
            _edit_template(addon, txt, fallback=False)
        elif choice == 10:
            _edit_template(addon, txt, fallback=True)
        elif choice == 11:
            _preview(addon, txt)
        elif choice == 12:
            _reset(addon, txt)
