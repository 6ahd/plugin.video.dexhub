# -*- coding: utf-8 -*-
"""The player's subtitle look (v5.10.314), in the subtitle panel itself:
font, size, weight, colour, opacity, border, background, shadow, blur, line
spacing and position, each changed with Left and Right while the video
plays (Kodi applies them at once, so the line on screen is the preview),
and four looks in one press (Dex Hub, Cinema, Box, Large).

These are Kodi's own subtitle settings (Settings > Player > Subtitles), read
and written through JSON-RPC: the same values Kodi's dialog shows.

skin.dexhub draws it (Custom_1196_DexSubStyle.xml); routes:
  skin_osd_substyle         the rows (a list)
  skin_osd_substyle_set     key=..., step=1|-1
  skin_osd_substyle_preset  name=dexhub|cinema|box|large
"""
import json
import os
import shutil
import time

import xbmc
import xbmcgui
import xbmcplugin

from . import common as C

REV = 'dhs.substyle.rev'


def _t(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _rpc(method, params=None):
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params is not None:
        payload['params'] = params
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(payload)))
    except Exception:
        return {}


def _get(key):
    return (_rpc('Settings.GetSettingValue', {'setting': key}).get('result') or {}).get('value')


def _set(key, value):
    answer = _rpc('Settings.SetSettingValue', {'setting': key, 'value': value})
    ok = bool(answer.get('result'))
    if not ok:
        C.log('subtitle look: %s=%r refused (%s)' % (key, value, answer.get('error')), xbmc.LOGWARNING)
    return ok


def _all():
    """{setting id: setting} of Kodi's subtitle category (values and options)."""
    answer = _rpc('Settings.GetSettings', {'level': 'expert', 'filter': {'section': 'player', 'category': 'subtitles'}})
    out = {}
    for setting in ((answer.get('result') or {}).get('settings') or []):
        if isinstance(setting, dict) and setting.get('id'):
            out[setting['id']] = setting
    return out


# colours (ARGB, as Kodi's colour buttons keep them)
TEXT = (('FFFFFFFF', 'أبيض', 'White'), ('FFFFE680', 'أصفر فاتح', 'Soft yellow'), ('FFFFD60A', 'أصفر', 'Yellow'),
        ('FF8CEBFF', 'سماوي', 'Cyan'), ('FFB9F6A8', 'أخضر فاتح', 'Light green'), ('FFD6D6D6', 'رمادي فاتح', 'Light grey'),
        ('FFFFB3C7', 'وردي', 'Pink'), ('FF000000', 'أسود', 'Black'))
DARK = (('FF000000', 'أسود', 'Black'), ('FF1C1C1E', 'رمادي داكن', 'Dark grey'), ('FF0B1A33', 'كحلي', 'Navy'),
        ('FF2B1B3F', 'بنفسجي داكن', 'Deep purple'), ('FFFFFFFF', 'أبيض', 'White'))

# id, Arabic, English, kind, extra
ROWS = (
    ('subtitles.fontname', 'الخط', 'Font', 'options', None),
    ('subtitles.fontsize', 'الحجم', 'Size', 'int', (12, 74, 2)),
    ('subtitles.style', 'النمط', 'Style', 'choice', ((0, 'عادي', 'Normal'), (1, 'عريض', 'Bold'), (2, 'مائل', 'Italic'),
                                                     (3, 'عريض مائل', 'Bold italic'))),
    ('subtitles.colorpick', 'اللون', 'Colour', 'colour', TEXT),
    ('subtitles.opacity', 'الشفافية', 'Opacity', 'pct', (0, 100, 5)),
    ('subtitles.bordersize', 'الحدود', 'Border', 'pct', (0, 100, 4)),
    ('subtitles.bordercolorpick', 'لون الحدود', 'Border colour', 'colour', DARK),
    ('subtitles.backgroundtype', 'الخلفية', 'Background', 'choice', ((0, 'بدون', 'None'), (1, 'ظل', 'Shadow'),
                                                                     (2, 'صندوق', 'Box'), (3, 'صندوق مربع', 'Square box'))),
    ('subtitles.bgcolorpick', 'لون الصندوق', 'Box colour', 'colour', DARK),
    ('subtitles.bgopacity', 'شفافية الصندوق', 'Box opacity', 'pct', (0, 100, 5)),
    ('subtitles.shadowcolor', 'لون الظل', 'Shadow colour', 'colour', DARK),
    ('subtitles.shadowopacity', 'شفافية الظل', 'Shadow opacity', 'pct', (0, 100, 5)),
    ('subtitles.shadowsize', 'حجم الظل', 'Shadow size', 'pct', (0, 100, 5)),
    ('subtitles.blur', 'النعومة', 'Blur', 'pct', (0, 100, 5)),
    ('subtitles.linespacing', 'تباعد الأسطر', 'Line spacing', 'pct', (0, 100, 4)),
    ('subtitles.align', 'الموقع', 'Position', 'choice', ((1, 'أسفل', 'Bottom'),
                                                         (2, 'تحت الصورة', 'Below picture'),
                                                         (3, 'أعلى', 'Top'),
                                                         (4, 'فوق الصورة', 'Above picture'),
                                                         (0, 'يدوي', 'Manual'))),
)
_BOX = (2, 3)
_SHADOW = (1, 2)
_SHOWN_IF = {
    'subtitles.bgcolorpick': _BOX, 'subtitles.bgopacity': _BOX,
    'subtitles.shadowcolor': _SHADOW, 'subtitles.shadowopacity': _SHADOW, 'subtitles.shadowsize': _SHADOW,
}

PRESETS = {
    # a clear, heavy Arabic face with a firm border: reads on any picture
    'dexhub': {'subtitles.fontsize': 46, 'subtitles.style': 0, 'subtitles.colorpick': 'FFFFFFFF',
               'subtitles.opacity': 100, 'subtitles.bordersize': 24, 'subtitles.bordercolorpick': 'FF000000',
               'subtitles.backgroundtype': 1, 'subtitles.shadowcolor': 'FF000000', 'subtitles.shadowopacity': 60,
               'subtitles.shadowsize': 10, 'subtitles.blur': 0, 'subtitles.linespacing': 12},
    'cinema': {'subtitles.fontsize': 44, 'subtitles.style': 0, 'subtitles.colorpick': 'FFFFE680',
               'subtitles.opacity': 100, 'subtitles.bordersize': 20, 'subtitles.bordercolorpick': 'FF000000',
               'subtitles.backgroundtype': 1, 'subtitles.shadowcolor': 'FF000000', 'subtitles.shadowopacity': 70,
               'subtitles.shadowsize': 12, 'subtitles.blur': 0, 'subtitles.linespacing': 12},
    'box': {'subtitles.fontsize': 42, 'subtitles.style': 0, 'subtitles.colorpick': 'FFFFFFFF',
            'subtitles.opacity': 100, 'subtitles.bordersize': 0, 'subtitles.backgroundtype': 2,
            'subtitles.bgcolorpick': 'FF000000', 'subtitles.bgopacity': 60, 'subtitles.blur': 0,
            'subtitles.linespacing': 16},
    'large': {'subtitles.fontsize': 56, 'subtitles.style': 1, 'subtitles.colorpick': 'FFFFFFFF',
              'subtitles.opacity': 100, 'subtitles.bordersize': 28, 'subtitles.bordercolorpick': 'FF000000',
              'subtitles.backgroundtype': 1, 'subtitles.shadowcolor': 'FF000000', 'subtitles.shadowopacity': 60,
              'subtitles.shadowsize': 12, 'subtitles.blur': 0, 'subtitles.linespacing': 12},
}
# the font each look prefers, when it is installed (the first found)
PRESET_FONTS = {
    'dexhub': ('DexHub InterArabicBold', 'DexHub Jazeerahbold'),
    'cinema': ('DexHub Jazeerahbold', 'DexHub InterArabicBold'),
    'box': ('DexHub InterArabicRegular', 'DexHub InterArabicBold'),
    'large': ('DexHub InterArabicBold', 'DexHub Jazeerahbold'),
}
# the names the skin's faces carry, as people know them
FONT_NAMES = {
    'interarabicbold': ('إنتر عربي عريض', 'Inter Arabic Bold'), 'interarabicregular': ('إنتر عربي', 'Inter Arabic'),
    'jazeerahbold': ('الجزيرة عريض', 'Al Jazeera Bold'), 'jazeerah': ('الجزيرة', 'Al Jazeera'),
    'mohandbold': ('مهند عريض', 'Mohand Bold'), 'mohandregular': ('مهند', 'Mohand'),
    'hacenpromoterbold': ('حسن عريض', 'Hacen Promoter Bold'), 'hacenpromoter': ('حسن', 'Hacen Promoter'),
    'iphone': ('آيفون عريض', 'iPhone Arabic Bold'), 'iphoneregular': ('آيفون', 'iPhone Arabic'),
    'monadibold': ('منادي عريض', 'Monadi Bold'), 'tonosbold': ('تونس عريض', 'Tonos Bold'),
}


def _font_label(value, label):
    key = str(value or '').lower().replace(' ', '')
    if key.startswith('dexhub'):
        names = FONT_NAMES.get(key[len('dexhub'):])
        if names:
            return _t(*names)
        return str(value)[len('DexHub '):]
    return label or str(value)


def _font_options(setting):
    out = []
    for option in (setting or {}).get('options') or []:
        if isinstance(option, dict) and option.get('value') not in (None, ''):
            out.append((option['value'], _font_label(option['value'], option.get('label') or option['value'])))
    return out


def _value_text(row, setting, value):
    key, ar, en, kind, extra = row
    if kind == 'options':
        for option_value, label in _font_options(setting):
            if option_value == value:
                return label
        return str(value or '')
    if kind == 'int':
        return '%s' % value
    if kind == 'pct':
        return '%s %%' % value
    if kind == 'choice':
        for option_value, a, e in extra:
            if option_value == value:
                return _t(a, e)
        return str(value)
    if kind == 'colour':
        for option_value, a, e in extra:
            if str(option_value).upper() == str(value or '').upper():
                return _t(a, e)
        return '#%s' % str(value or '')[-6:]
    return str(value)


def listing(params, handle):
    settings = _all()
    background = (settings.get('subtitles.backgroundtype') or {}).get('value')
    entries = []
    for row in ROWS:
        key, ar, en, kind, extra = row
        setting = settings.get(key)
        if not setting:
            continue
        if key in _SHOWN_IF and background not in _SHOWN_IF[key]:
            continue
        value = setting.get('value')
        li = xbmcgui.ListItem(label=_t(ar, en), label2=_value_text(row, setting, value), offscreen=True)
        props = {'key': key, 'kind': kind, 'isfolder': 'false'}
        if kind == 'colour':
            props['swatch'] = str(value or 'FFFFFFFF')
        if kind in ('pct', 'int'):
            low, high, _step = extra
            try:
                pct = int(round((float(value) - low) * 100.0 / max(1, high - low)))
            except Exception:
                pct = 0
            props['pct'] = str(max(0, min(100, pct)))
            # the bar: Kodi's percent played (a progress control reads no property)
            li.getVideoInfoTag().setResumePoint(float(max(1, min(99, pct))), 100.0)
        if setting.get('enabled') is False:
            props['disabled'] = '1'
        li.setProperties(props)
        entries.append(('substyle://%s' % key, li, False))
    if fonts_waiting():
        li = xbmcgui.ListItem(label=_t('خطوط عربية جديدة', 'New Arabic fonts'),
                              label2=_t('تظهر بعد إعادة تشغيل Kodi', 'Show after Kodi restarts'), offscreen=True)
        li.setProperties({'key': '', 'kind': 'note', 'isfolder': 'false'})
        entries.append(('substyle://note', li, False))
    li = xbmcgui.ListItem(label=_t('إعدادات Kodi للترجمة', "Kodi's subtitle settings"), label2='', offscreen=True)
    li.setProperties({'key': '', 'kind': 'kodi', 'isfolder': 'false'})
    entries.append(('substyle://kodi', li, False))
    if handle >= 0:
        xbmcplugin.addDirectoryItems(handle, entries, len(entries))
        xbmcplugin.endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=False)


def _refresh():
    xbmcgui.Window(10000).setProperty(REV, '%.3f' % time.time())


def step(params):
    key = params.get('key') or ''
    try:
        direction = 1 if int(params.get('step') or 1) >= 0 else -1
    except ValueError:
        direction = 1
    row = next((r for r in ROWS if r[0] == key), None)
    if row is None:
        return
    _key, _ar, _en, kind, extra = row
    if kind == 'options':
        setting = _all().get(key) or {}
        values = [v for v, _label in _font_options(setting)]
        current = setting.get('value')
        if not values:
            return
        index = values.index(current) if current in values else -1
        value = values[(index + direction) % len(values)]
    elif kind in ('int', 'pct'):
        low, high, size = extra
        try:
            current = int(_get(key))
        except Exception:
            current = low
        value = max(low, min(high, current + direction * size))
        if value == current:
            return
    elif kind == 'choice':
        values = [v for v, _a, _e in extra]
        current = _get(key)
        index = values.index(current) if current in values else -1
        value = values[(index + direction) % len(values)]
    else:
        values = [v for v, _a, _e in extra]
        current = str(_get(key) or '').upper()
        upper = [v.upper() for v in values]
        index = upper.index(current) if current in upper else -1
        value = values[(index + direction) % len(values)]
    _set(key, value)
    _refresh()


def preset(params):
    name = params.get('name') or 'dexhub'
    values = PRESETS.get(name)
    if not values:
        return
    fonts = [v for v, _label in _font_options(_all().get('subtitles.fontname'))]
    for wanted in PRESET_FONTS.get(name) or ():
        found = next((f for f in fonts if f.lower().replace(' ', '') == wanted.lower().replace(' ', '')), None)
        if found:
            _set('subtitles.fontname', found)
            break
    for key, value in values.items():
        _set(key, value)
    xbmcgui.Window(10000).setProperty('dhs.substyle.preset', name)
    _refresh()


# ------------------------------------------------------------------ fonts
# The skin's Arabic faces offered to subtitles too: Kodi lists the fonts of
# userdata/media/Fonts in its subtitle font setting (read when Kodi starts).
FONTS = ('InterArabic-Bold.ttf', 'InterArabic-Regular.ttf', 'Jazeerah bold.ttf', 'MOHANDbold.ttf',
         'iphone.ttf', 'Hacen-Promoter -Bold.ttf')
_MARK = '.dexhub-fonts-v1'


def _user_fonts():
    import xbmcvfs
    return xbmcvfs.translatePath('special://home/media/Fonts/')


def fonts_waiting():
    """True until Kodi restarts after Dex Hub added fonts."""
    try:
        return xbmcgui.Window(10000).getProperty('dhs.substyle.fonts_new') == '1'
    except Exception:
        return False


def install_fonts():
    """Once (a mark file): copy the skin's Arabic faces to the user font
    folder, keeping any file already there. Run by the service at start."""
    try:
        import xbmcvfs
        target = _user_fonts()
        if not target or '://' in target:
            return 0
        if os.path.exists(os.path.join(target, _MARK)):
            return 0
        skin = xbmcvfs.translatePath('special://skin/fonts/')
        if not os.path.isdir(skin):
            return 0
        os.makedirs(target, exist_ok=True)
        copied = 0
        for name in FONTS:
            source = os.path.join(skin, name)
            dest = os.path.join(target, name)
            if os.path.isfile(source) and not os.path.exists(dest):
                tmp = dest + '.tmp'
                shutil.copyfile(source, tmp)
                os.replace(tmp, dest)
                copied += 1
        with open(os.path.join(target, _MARK), 'w') as handle:
            handle.write('1')
        if copied:
            xbmcgui.Window(10000).setProperty('dhs.substyle.fonts_new', '1')
            C.log('subtitle look: %d Arabic fonts added for subtitles (listed after Kodi restarts)' % copied)
        return copied
    except Exception as exc:
        C.log('subtitle fonts: %s' % exc, xbmc.LOGWARNING)
        return 0
