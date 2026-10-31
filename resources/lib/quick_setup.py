# -*- coding: utf-8 -*-
"""The quick setup's questions (v5.10.452), one place for the TV and the phone.

People found the settings too many: the quick setup asks the few questions
that change everyday use, each with two to four answers. The TV walks them
as the setup window's steps after the accounts (homeui/wizard.py, the
Control centre's first card); the phone page shows the same questions as
one card at its top (phone_setup.py api/quick). An answer is written the
way the Control centre writes it, so the three places always agree.

  playback   choose a source from the results, or the best one at once
  layout     the Home's layout: three of skin.dexhub's (the current one kept)
  title      the title page: cinematic or classic (skin.dexhub)
  speed      how light Dex Hub runs (homeui/wizard.py SPEED)

The subtitle questions (sub_lang, sub_look) are kept for a step of their
own but left out of the quick setup: people pick very different languages,
and the player's Subtitles and Audio settings hold them all.

An answer: (value, Arabic, English, Arabic line, English line).
"""
import os

import xbmc
import xbmcaddon

ADDON_ID = 'plugin.video.dexhub'
DONE_SETTING = 'quick_setup_done'

PLAYBACK = (
    ('choose', 'أختار المصدر بنفسي', 'I choose the source', 'تطلع لك المصادر وتختار منها', 'The sources show and you pick one'),
    ('best', 'أفضل جودة على طول', 'Best quality at once', 'يشغّل أفضل نتيجة بدون قائمة', 'Plays the best result, no list'),
)
# the Home's layouts offered (skin.dexhub; the rest stay in the Control centre)
LAYOUTS = (
    ('cinematic', 'هيرو وصفوف', 'Hero and rows', 'عمل بارز وتحته الصفوف', 'A featured title with the rows below'),
    ('cinema', 'سينمائي ملء الشاشة', 'Full screen', 'الخلفية بملء الشاشة والصفوف تحتها', 'Full screen artwork, the rows below'),
    ('netflix', 'هيرو وصف واحد', 'Hero and one row', 'عمل بارز كبير وتحته صف واحد', 'A big featured title and one row'),
    ('two_rows', 'هيرو مختصر وصفين', 'Compact hero, two rows', 'شريط بارز صغير وصفين كاملين', 'A small featured strip and two rows'),
    ('shelf', 'رف بطاقات عريضة', 'Wide card shelf', 'بدون هيرو: بطاقات عريضة ثم الصفوف', 'No hero: wide cards, then the rows'),
    ('legacy', 'ليقسي', 'Legacy', 'شعار العمل فوق والصفوف تحته', 'The title on top, the rows below'),
    ('spotlight_posters', 'Spotlight وبوسترات', 'Spotlight and posters', 'Spotlight بارز وصف بوسترات', 'A Spotlight panel and posters'),
)
OFFERED_LAYOUTS = ('cinematic', 'cinema', 'netflix')
TITLES = (
    ('cinematic', 'سينمائي', 'Cinematic', 'الخلفية بالشاشة كلها وأزرار على زجاج', 'Full screen artwork, glass buttons'),
    ('classic', 'كلاسيكي', 'Classic', 'البوستر والتفاصيل والمصادر', 'The poster, the details and the sources'),
)
SUB_LANGS = (
    ('ar', 'العربي أولًا', 'Arabic first', 'وبعده الإنجليزي', 'Then English'),
    ('en', 'الإنجليزي أولًا', 'English first', 'وبعده العربي', 'Then Arabic'),
    ('off', 'بدون ترجمة', 'No subtitles', 'يشغّل بدون ما يدوّر ترجمة', 'Plays without looking for any'),
)
# value: (preset, sample colour)
SUB_LOOKS = (
    ('dexhub', 'واضح', 'Clear', 'أبيض بإطار أسود', 'White with a black border'),
    ('cinema', 'سينمائي', 'Cinema', 'أصفر فاتح مثل السينما', 'Soft yellow, like a cinema'),
    ('box', 'بخلفية', 'Boxed', 'أبيض على مربع غامق', 'White on a dark box'),
)
SUB_SAMPLE = {'dexhub': 'FFFFFFFF', 'cinema': 'FFFFF59D', 'box': 'FFFFFFFF'}
SPEED_LINES = {
    'light': ('جهاز خفيف', 'Light device', 'Fire Stick وأجهزة TV Box، بدون تريلر', 'Fire Stick and TV boxes, no trailers'),
    'balanced': ('متوازن', 'Balanced', 'تريلرات، والخلفيات ثابتة', 'Trailers, still backgrounds'),
    'full': ('جهاز قوي', 'Strong device', 'Apple TV 4K وShield والكمبيوتر', 'Apple TV 4K, Shield, computers'),
}

QUESTIONS = (
    ('playback', 'طريقة التشغيل', 'Playback', 'لما تضغط تشغيل', 'When you press Play'),
    ('layout', 'شكل الرئيسية', 'The Home', 'كيف تنعرض الرئيسية', 'How the Home looks'),
    ('title', 'صفحة العمل', 'The title page', 'لما تفتح فيلم أو مسلسل', 'When you open a film or a show'),
    ('speed', 'جهازك', 'Your device', 'كيف يشتغل Dex Hub عليه', 'How Dex Hub runs on it'),
)
SKIN_ONLY = ('layout', 'title')


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _media(*parts):
    return os.path.join(_addon().getAddonInfo('path'), 'resources', 'media', *parts)


def _settled():
    try:
        from . import settings_cache
        settings_cache.invalidate()
        from . import ui_preferences
        ui_preferences.publish()
    except Exception:
        pass


def in_skin():
    return xbmc.getSkinDir() == 'skin.dexhub'


def speeds():
    from .homeui.wizard import SPEED
    return tuple((key,) + SPEED_LINES.get(key, (ar, en, dar, den)) for key, ar, en, dar, den, _v in SPEED)


def recommended_speed():
    """A computer runs everything; an Apple TV the balanced one; a box light."""
    if xbmc.getCondVisibility('System.Platform.OSX') or xbmc.getCondVisibility('System.Platform.Windows'):
        return 'full'
    if xbmc.getCondVisibility('System.Platform.TVOS'):
        return 'balanced'
    return 'light'


def layouts():
    """The three offered, with the one in force kept in the third place."""
    now = current('layout')
    keys = list(OFFERED_LAYOUTS)
    if now and now not in keys:
        keys[-1] = now
    by = dict((row[0], row) for row in LAYOUTS)
    return tuple(by[k] for k in keys if k in by)


def answers(key):
    return {'playback': PLAYBACK, 'layout': layouts(), 'title': TITLES, 'sub_lang': SUB_LANGS,
            'sub_look': SUB_LOOKS, 'speed': speeds()}.get(key, ())


def picture(key, value):
    """The drawing of a look answer ('' when there is none)."""
    if key == 'layout':
        path = _media('homeui', 'homelayout_%s.jpg' % value)
    elif key == 'title':
        from .homeui.control import LOOKS_REV
        path = _media('looks', 'title_layout_%s_%d.jpg' % (value, LOOKS_REV))
    else:
        return ''
    return path if os.path.exists(path) else ''


def _subtitle_get(name):
    from .skinui import substyle
    return substyle._get(name)


def current(key):
    addon = _addon()
    if key == 'playback':
        from . import playback_choices
        return playback_choices.mode(addon)
    if key == 'layout':
        return (addon.getSetting('homeui_layout_mode') or 'cinematic').strip().lower()
    if key == 'title':
        value = (xbmc.getInfoLabel('Skin.String(dh.titlelayout)') or '').strip().lower()
        return 'cinematic' if value == 'cinematic' else 'classic'
    if key == 'sub_lang':
        if (addon.getSetting('subtitle_search_mode') or 'Play with subtitles') == 'Play only':
            return 'off'
        codes = [c.strip().lower() for c in (addon.getSetting('preferred_subtitle_langs') or 'ar,en').split(',') if c.strip()]
        return 'en' if codes[:1] == ['en'] else ('ar' if codes[:1] == ['ar'] else '')
    if key == 'sub_look':
        try:
            colour = str(_subtitle_get('subtitles.colorpick') or '').upper()
            box = int(_subtitle_get('subtitles.backgroundtype') or 0) == 2
        except Exception:
            return ''
        if box:
            return 'box'
        return {'FFFFFFFF': 'dexhub', 'FFFFF59D': 'cinema'}.get(colour, '')
    if key == 'speed':
        from .homeui.wizard import current_speed
        return current_speed()
    return ''


def apply(key, value):
    """The answer written; True when something changed. ValueError for an
    unknown question or answer."""
    value = str(value or '')
    if key == 'layout':
        allowed = [row[0] for row in LAYOUTS]
    else:
        allowed = [row[0] for row in answers(key)]
    if value not in allowed:
        raise ValueError('bad answer')
    if key in SKIN_ONLY and not in_skin():
        raise ValueError('skin.dexhub only')
    if value == current(key):
        return False
    addon = _addon()
    if key == 'playback':
        from . import playback_choices
        playback_choices.set_mode(addon, value)
        _settled()
    elif key == 'layout':
        from .homeui import control
        control.apply_entry('home_layout_mode', value)
    elif key == 'title':
        from .homeui import control
        control.set_skin_string('dh.titlelayout', 'cinematic' if value == 'cinematic' else '')
    elif key == 'sub_lang':
        if value == 'off':
            addon.setSetting('subtitle_search_mode', 'Play only')
        else:
            other = 'en' if value == 'ar' else 'ar'
            kept = [c.strip().lower() for c in (addon.getSetting('preferred_subtitle_langs') or '').split(',')
                    if c.strip() and c.strip().lower() not in (value, other)]
            addon.setSetting('preferred_subtitle_langs', ','.join([value, other] + kept[:4]))
            if (addon.getSetting('subtitle_search_mode') or '') == 'Play only':
                addon.setSetting('subtitle_search_mode', 'Play with subtitles')
        _settled()
    elif key == 'sub_look':
        from .skinui import substyle
        substyle.preset({'name': value})
    elif key == 'speed':
        from .homeui.wizard import SPEED
        values = next(v for k, _a, _e, _da, _de, v in SPEED if k == value)
        for name, setting in values.items():
            if (addon.getSetting(name) or '').lower() != setting:
                addon.setSetting(name, setting)
        _settled()
    return True


def state():
    """The questions for the phone, both languages (the page picks its own):
    [{key, title: [ar, en], line: [ar, en], value, answers: [{value, label,
    line, sample (sub_look), tip (speed's recommended)}]}]."""
    out = []
    skin = in_skin()
    tip = recommended_speed()
    for key, ar, en, line_ar, line_en in QUESTIONS:
        if key in SKIN_ONLY and not skin:
            continue
        try:
            now = current(key)
        except Exception:
            now = ''
        rows = []
        for value, a_ar, a_en, l_ar, l_en in answers(key):
            row = {'value': value, 'label': [a_ar, a_en], 'line': [l_ar, l_en]}
            if key == 'sub_look':
                row['sample'] = '#' + SUB_SAMPLE[value][2:]
            if key == 'speed' and value == tip:
                row['tip'] = ['موصى به', 'Recommended']
            rows.append(row)
        out.append({'key': key, 'title': [ar, en], 'line': [line_ar, line_en], 'value': now, 'answers': rows})
    return out


def mark_done():
    try:
        _addon().setSetting(DONE_SETTING, 'true')
    except Exception:
        pass
