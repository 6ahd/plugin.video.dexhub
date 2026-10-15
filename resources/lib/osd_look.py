# -*- coding: utf-8 -*-
"""The player bar's look (v5.10.298): style, background darkness and colour.

v5.10.302: the shades above and below the bar can be any colour or none at
all (transparent), the buttons can sit on a soft shape or an outline, two
more cursor shapes (a dot under the icon, a soft see-through fill), and
ready looks that set all of it at once (player_osd_preset).

Three settings (ui_preferences.OPTIONS, shown in the player buttons editor,
the appearance menu and the phone setup page):

  player_osd_style   classic | glass | cinematic | minimal
  player_osd_dim     how dark the bar's background is: 30 50 70 85 95 (%)
  player_osd_accent  white | theme | artwork | violet | blue | cyan | green |
                     gold | orange | red | pink

The skin reads only Home properties, so a change shows at once, also while a
video plays:

  dhs.osd.style      the style name
  dhs.osd.bg         AARRGGBB of the bar's gradients (darkness, faint tint)
  dhs.osd.glass      AARRGGBB of the glass style's panel
  dhs.osd.accent     AARRGGBB of the focused button, the progress and the nib
  dhs.osd.on_accent  AARRGGBB of the icon on the focused button
  dhs.osd.bar        AARRGGBB of the progress fill

"artwork" takes the colour from the playing title's fanart (or poster): the
image is read once with PIL when Kodi has it (script.module.pil), else the
theme's colour stands in. The colour is kept per image.
"""
import colorsys
import json
import os
import threading

import xbmc
import xbmcgui

STYLE = 'player_osd_style'
DIM = 'player_osd_dim'
ACCENT = 'player_osd_accent'
SHAPE = 'player_osd_shape'       # v5.10.301: the cursor's shape
SCRIM = 'player_osd_scrim'       # v5.10.302: the shades' colour
AREA = 'player_osd_area'         # v5.10.302: below only, above and below, none
BUTTONS = 'player_osd_buttons'   # v5.10.302: what the buttons sit on
PRESET = 'player_osd_preset'     # v5.10.302: a ready look (sets the others)
KEYS = (STYLE, DIM, ACCENT, SHAPE, SCRIM, AREA, BUTTONS, PRESET)

STYLES = (
    ('classic', 'كلاسيكي', 'Classic'),
    ('glass', 'زجاجي', 'Glass'),
    ('cinematic', 'سينمائي', 'Cinematic'),
    ('minimal', 'بسيط', 'Minimal'),
)
SHAPES = (
    ('circle', 'دائرة', 'Circle'),
    ('square', 'مربع مثل Apple TV', 'Rounded square, like Apple TV'),
    ('ring', 'حلقة', 'Ring'),
    ('line', 'خط تحت الأيقونة', 'Line under the icon'),
    ('dot', 'نقطة تحت الأيقونة', 'Dot under the icon'),
    ('soft', 'دائرة شفافة', 'See-through circle'),
)
DIMS = (
    ('0', 'شفاف · بدون ظل', 'Transparent · no shade'),
    ('15', 'خفيف جداً · 15%', 'Barely there · 15%'),
    ('30', 'فاتح جداً · 30%', 'Very light · 30%'),
    ('50', 'فاتح · 50%', 'Light · 50%'),
    ('60', 'هادئ · 60%', 'Soft · 60%'),
    ('70', 'متوسط · 70%', 'Medium · 70%'),
    ('80', 'مثل Nuvio · 80%', 'Like Nuvio · 80%'),
    ('85', 'داكن · 85%', 'Dark · 85%'),
    ('95', 'داكن جداً · 95%', 'Very dark · 95%'),
)
SCRIMS = (
    ('auto', 'أسود بلمسة من لون المشغّل', 'Black with a touch of the bar colour'),
    ('black', 'أسود', 'Black'),
    ('theme', 'لون خلفية الثيم', 'The theme background'),
    ('artwork', 'يتبع ألوان العمل المشغّل', 'Follow the playing title'),
    ('navy', 'كحلي', 'Navy'),
    ('plum', 'بنفسجي غامق', 'Plum'),
    ('wine', 'عنابي', 'Wine'),
    ('forest', 'أخضر غامق', 'Forest'),
    ('slate', 'رمادي أزرق', 'Slate'),
    ('warm', 'بني دافئ', 'Warm brown'),
)
SCRIM_RGB = {'black': (0, 0, 0), 'navy': (8, 20, 46), 'plum': (34, 16, 52), 'wine': (52, 10, 22),
             'forest': (10, 34, 22), 'slate': (22, 30, 44), 'warm': (44, 26, 12)}
AREAS = (
    ('bottom', 'تحت الشريط فقط', 'Below the bar only'),
    ('both', 'فوق وتحت مثل Nuvio', 'Above and below, like Nuvio'),
    ('none', 'بدون ظل · شفاف', 'None · transparent'),
)
BUTTON_BGS = (
    ('none', 'بدون خلفية', 'No background'),
    ('soft', 'شكل خفيف خلف كل زر', 'A soft shape behind each button'),
    ('outline', 'إطار حول كل زر', 'An outline around each button'),
)
# v5.10.302: ready looks: (key, ar, en, {setting: value})
PRESETS = (
    ('custom', 'مخصص (اختياراتك)', 'Custom (your choices)', {}),
    ('nuvio', 'Nuvio', 'Nuvio',
     {STYLE: 'classic', DIM: '80', ACCENT: 'white', SHAPE: 'circle', SCRIM: 'black', AREA: 'both', BUTTONS: 'none'}),
    ('appletv', 'Apple TV', 'Apple TV',
     {STYLE: 'glass', DIM: '50', ACCENT: 'white', SHAPE: 'square', SCRIM: 'black', AREA: 'bottom', BUTTONS: 'soft'}),
    ('netflix', 'على طريقة Netflix', 'Netflix style',
     {STYLE: 'cinematic', DIM: '85', ACCENT: 'red', SHAPE: 'line', SCRIM: 'black', AREA: 'both', BUTTONS: 'none'}),
    ('infuse', 'على طريقة Infuse', 'Infuse style',
     {STYLE: 'classic', DIM: '70', ACCENT: 'orange', SHAPE: 'soft', SCRIM: 'black', AREA: 'bottom', BUTTONS: 'none'}),
    ('plex', 'على طريقة Plex', 'Plex style',
     {STYLE: 'classic', DIM: '85', ACCENT: 'gold', SHAPE: 'square', SCRIM: 'black', AREA: 'both', BUTTONS: 'none'}),
    ('disney', 'على طريقة Disney+', 'Disney+ style',
     {STYLE: 'glass', DIM: '60', ACCENT: 'blue', SHAPE: 'ring', SCRIM: 'navy', AREA: 'bottom', BUTTONS: 'outline'}),
    ('artwork', 'ألوان العمل', 'The title\'s colours',
     {STYLE: 'cinematic', DIM: '85', ACCENT: 'artwork', SHAPE: 'soft', SCRIM: 'artwork', AREA: 'both', BUTTONS: 'soft'}),
    ('clean', 'شفاف ونظيف', 'Clean and transparent',
     {STYLE: 'minimal', DIM: '0', ACCENT: 'white', SHAPE: 'dot', SCRIM: 'black', AREA: 'none', BUTTONS: 'none'}),
    ('theme', 'ألوان الثيم', 'The theme\'s colours',
     {STYLE: 'classic', DIM: '85', ACCENT: 'theme', SHAPE: 'square', SCRIM: 'theme', AREA: 'both', BUTTONS: 'outline'}),
)
COLOURS = {
    'violet': 'FF9B6BFF', 'blue': 'FF3B82F6', 'cyan': 'FF22D3EE', 'green': 'FF22C55E',
    'gold': 'FFF5B301', 'orange': 'FFF97316', 'red': 'FFEF4444', 'pink': 'FFEC4899',
}
ACCENTS = (
    ('white', 'أبيض (الافتراضي)', 'White (default)'),
    ('theme', 'لون الثيم', 'Theme colour'),
    ('artwork', 'يتبع ألوان العمل المشغّل', 'Follow the playing title'),
    ('violet', 'بنفسجي', 'Violet'),
    ('blue', 'أزرق', 'Blue'),
    ('cyan', 'سماوي', 'Cyan'),
    ('green', 'أخضر', 'Green'),
    ('gold', 'ذهبي', 'Gold'),
    ('orange', 'برتقالي', 'Orange'),
    ('red', 'أحمر', 'Red'),
    ('pink', 'وردي', 'Pink'),
)
CHOICES = {
    STYLE: tuple(v for v, _, _ in STYLES),
    DIM: tuple(v for v, _, _ in DIMS),
    ACCENT: tuple(v for v, _, _ in ACCENTS),
    SHAPE: tuple(v for v, _, _ in SHAPES),
    SCRIM: tuple(v for v, _, _ in SCRIMS),
    AREA: tuple(v for v, _, _ in AREAS),
    BUTTONS: tuple(v for v, _, _ in BUTTON_BGS),
    PRESET: tuple(p[0] for p in PRESETS),
}
DEFAULTS = {STYLE: 'classic', DIM: '70', ACCENT: 'white', SHAPE: 'circle',
            SCRIM: 'auto', AREA: 'bottom', BUTTONS: 'none', PRESET: 'custom'}
ROWS = {STYLE: STYLES, DIM: DIMS, ACCENT: ACCENTS, SHAPE: SHAPES, SCRIM: SCRIMS, AREA: AREAS,
        BUTTONS: BUTTON_BGS, PRESET: tuple(p[:3] for p in PRESETS)}

PROP_ART = 'dhs.osd.art_color'
PROP_ART_KEY = 'dhs.osd.art_key'
_HOME = xbmcgui.Window(10000)
_LOCK = threading.Lock()
_CACHE = {}
_CACHE_MAX = 400


def _log(message):
    try:
        xbmc.log('[DexHub] osd look: %s' % message, xbmc.LOGINFO)
    except Exception:
        pass


def value(key):
    try:
        from . import ui_preferences
        return ui_preferences.value(key)
    except Exception:
        return DEFAULTS[key]


def label(key, choice, txt):
    for v, ar, en in ROWS.get(key, ()):
        if v == choice:
            return txt(ar, en)
    return choice


def labels(key, txt):
    return [txt(ar, en) for _, ar, en in ROWS.get(key, ())]


# ------------------------------------------------------------------ colours

def _hex(value):
    text = str(value or '').strip().lstrip('#')
    if len(text) == 6:
        text = 'FF' + text
    if len(text) != 8:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return text.upper()


def _rgb(argb):
    return int(argb[2:4], 16), int(argb[4:6], 16), int(argb[6:8], 16)


def _argb(alpha, rgb):
    return '%02X%02X%02X%02X' % (alpha, rgb[0], rgb[1], rgb[2])


def _on(argb):
    """Dark or white icon on a focused button of this colour."""
    r, g, b = _rgb(argb)
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    if argb == 'FFFFFFFF':
        return 'FF272727'          # the bar's look before v5.10.298
    return 'FF161616' if lum > 0.58 else 'FFFFFFFF'


def _theme_accent():
    return _hex(_HOME.getProperty('dexhub.tvos.theme.accent')) or 'FF9B6BFF'


def accent_colour(choice=None):
    choice = choice or value(ACCENT)
    if choice == 'white':
        return 'FFFFFFFF'
    if choice == 'theme':
        return _theme_accent()
    if choice == 'artwork':
        return _hex(_HOME.getProperty(PROP_ART)) or _theme_accent()
    return COLOURS.get(choice) or 'FFFFFFFF'


def props():
    """The Home properties for the current settings."""
    style = value(STYLE)
    try:
        dim = max(0, min(100, int(value(DIM))))   # v5.10.302: 0 is transparent
    except (TypeError, ValueError):
        dim = 70
    accent = accent_colour()
    area = value(AREA)
    if area not in CHOICES[AREA]:
        area = 'bottom'
    alpha = 0 if area == 'none' else int(round(255 * max(0, dim) / 100.0))
    tint = scrim_rgb(accent)
    shape = value(SHAPE)
    if shape not in CHOICES[SHAPE]:
        shape = 'circle'
    # the icon on the cursor: dark or white on a filled shape, white inside
    # the ring and on the see-through circle, the colour itself over the
    # line and the dot
    focus = accent
    if shape in ('ring', 'soft'):
        ink = 'FFFFFFFF'
    elif shape in ('line', 'dot'):
        ink = accent
    else:
        ink = _on(accent)
    if shape == 'soft':
        focus = '66' + accent[2:]
    glass_alpha = max(0x40, min(0xE6, int(max(alpha, 0x60) * 0.8)))
    glass_tint = tuple(min(255, c + 18) for c in tint)
    buttons = value(BUTTONS)
    if buttons not in CHOICES[BUTTONS]:
        buttons = 'none'
    square = shape == 'square'
    if buttons == 'soft':
        btn, btn_tex = '24FFFFFF', ('dexhub/osdfocus_square.png' if square else 'af3/common/circle.png')
    elif buttons == 'outline':
        btn, btn_tex = '4DFFFFFF', ('dexhub/osdfocus_square_ring.png' if square else 'dexhub/osdfocus_ring.png')
    else:
        btn, btn_tex = '00FFFFFF', 'af3/common/circle.png'
    top = area == 'both' or (style == 'cinematic' and area != 'none')
    return {
        'dhs.osd.style': style,
        'dhs.osd.bg': _argb(alpha, tint),
        'dhs.osd.glass': _argb(glass_alpha, glass_tint),
        'dhs.osd.accent': accent,
        'dhs.osd.focus': focus,
        'dhs.osd.on_accent': ink,
        # v5.10.303: ink on a row filled with the colour (the player's panels)
        'dhs.osd.on_fill': _on(accent),
        'dhs.osd.shape': shape,
        'dhs.osd.bar': accent,
        'dhs.osd.top': '1' if top else '0',
        'dhs.osd.btn': btn,
        'dhs.osd.btn_tex': btn_tex,
    }


def scrim_rgb(accent=None):
    """v5.10.302: the colour of the shades above and below the bar."""
    choice = value(SCRIM)
    if choice in SCRIM_RGB:
        return SCRIM_RGB[choice]
    if choice == 'theme':
        surface = _hex(_HOME.getProperty('dexhub.tvos.theme.surface'))
        if surface:
            return _rgb(surface)
        return (0, 0, 0)
    if choice == 'artwork':
        art = _hex(_HOME.getProperty(PROP_ART))
        if art:
            r, g, b = _rgb(art)
            return (int(r * 0.22), int(g * 0.22), int(b * 0.22))
        return (0, 0, 0)
    accent = accent or accent_colour()
    if accent == 'FFFFFFFF':
        return (0, 0, 0)
    # auto: a faint shade of the colour in the dark: the bar belongs to it
    r, g, b = _rgb(accent)
    return (int(r * 0.12), int(g * 0.12), int(b * 0.12))


def after_write(key, val):
    """v5.10.302: a player look setting was written (the centre, the phone
    page or the editor): a ready look writes its parts, a part makes the
    look custom again."""
    try:
        if key == PRESET:
            if val and val != 'custom':
                apply_preset(val)
            return
        if key in KEYS and value(PRESET) != 'custom':
            import xbmcaddon
            xbmcaddon.Addon().setSetting(PRESET, 'custom')
            from .settings_cache import invalidate
            invalidate()
        if val == 'artwork' and key in (ACCENT, SCRIM):
            follow_playing()
        publish()
    except Exception as exc:
        _log('after write %s: %s' % (key, exc))


def apply_preset(name):
    """v5.10.302: write a ready look's settings (the choices stay editable
    one by one; changing one makes the look "custom" again)."""
    for key, _, _, values in PRESETS:
        if key == name:
            break
    else:
        return False
    try:
        import xbmcaddon
        addon = xbmcaddon.Addon()
        for setting, val in values.items():
            addon.setSetting(setting, val)
        addon.setSetting(PRESET, name)
        from .settings_cache import invalidate
        invalidate()
    except Exception as exc:
        _log('preset %s failed: %s' % (name, exc))
        return False
    if values.get(ACCENT) == 'artwork' or values.get(SCRIM) == 'artwork':
        follow_playing()
    publish()
    return True


def publish():
    try:
        for key, val in props().items():
            if _HOME.getProperty(key) != val:
                _HOME.setProperty(key, val)
    except Exception as exc:
        _log('publish failed: %s' % exc)


# ------------------------------------------------------- artwork colour

def _pil():
    try:
        from PIL import Image  # noqa: F401
        return Image
    except Exception:
        pass
    import sys
    for root in ('special://home/addons/script.module.pil/lib',
                 'special://xbmc/addons/script.module.pil/lib',
                 'special://xbmcbinaddons/script.module.pil/lib'):
        try:
            path = xbmc.translatePath(root) if hasattr(xbmc, 'translatePath') else ''
        except Exception:
            path = ''
        if not path:
            try:
                import xbmcvfs
                path = xbmcvfs.translatePath(root)
            except Exception:
                path = ''
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)
            try:
                from PIL import Image
                return Image
            except Exception:
                continue
    return None


def _small_url(url):
    # TMDb serves every size; a 300 px picture is plenty for one colour
    for size in ('/original/', '/w1280/', '/w780/', '/w500/'):
        if 'image.tmdb.org/t/p' in url and size in url:
            return url.replace(size, '/w300/')
    return url


def _read_image(url):
    """Bytes of the picture: Kodi's own cached copy first, then the web."""
    import xbmcvfs
    try:
        cached = xbmc.getCacheThumbName(url)
        for ext in ('.jpg', '.png', '.webp'):
            path = 'special://thumbnails/%s/%s' % (cached[0], cached[:-4] + ext)
            if xbmcvfs.exists(path):
                handle = xbmcvfs.File(path)
                try:
                    data = handle.readBytes()
                finally:
                    handle.close()
                if data:
                    return bytes(data)
    except Exception:
        pass
    if url.startswith(('http://', 'https://')):
        from urllib.request import Request, urlopen
        try:
            response = urlopen(Request(_small_url(url), headers={'User-Agent': 'Kodi'}), timeout=5)
            try:
                return response.read(4 * 1024 * 1024)
            finally:
                response.close()
        except Exception:
            return None
    try:
        handle = xbmcvfs.File(url)
        try:
            return bytes(handle.readBytes())
        finally:
            handle.close()
    except Exception:
        return None


def _pick(pixels):
    """The picture's most telling colour: frequent, saturated, not too dark
    or too bright; lifted so it reads on a dark bar."""
    buckets = {}
    for r, g, b in pixels:
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        if l < 0.12 or l > 0.9 or s < 0.22:
            continue
        key = (int(h * 24), int(s * 4), int(l * 4))
        entry = buckets.setdefault(key, [0, 0.0, 0.0, 0.0, 0.0])
        weight = s * (1.0 - abs(l - 0.5))
        entry[0] += 1
        entry[1] += r * weight
        entry[2] += g * weight
        entry[3] += b * weight
        entry[4] += weight
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda e: e[4] * (e[0] ** 0.5))
    if best[4] <= 0:
        return None
    r, g, b = best[1] / best[4], best[2] / best[4], best[3] / best[4]
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    l = min(0.68, max(0.52, l))
    s = min(1.0, max(0.55, s))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return _argb(255, (int(r * 255), int(g * 255), int(b * 255)))


def artwork_colour(url):
    url = str(url or '').strip()
    if not url:
        return None
    with _LOCK:
        if url in _CACHE:
            return _CACHE[url]
    colour = None
    image = _pil()
    if image is not None:
        data = _read_image(url)
        if data:
            try:
                import io
                picture = image.open(io.BytesIO(data)).convert('RGB')
                picture.thumbnail((64, 64))
                getdata = getattr(picture, 'get_flattened_data', None) or picture.getdata
                colour = _pick(list(getdata()))
            except Exception as exc:
                _log('could not read %s: %s' % (url[-60:], exc))
    with _LOCK:
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.clear()
        _CACHE[url] = colour
    return colour


def _playing_art():
    for label in ('Player.Art(fanart)', 'Player.Art(tvshow.fanart)', 'Player.Art(poster)',
                  'Player.Art(tvshow.poster)', 'Player.Art(thumb)'):
        try:
            value_ = xbmc.getInfoLabel(label)
        except Exception:
            value_ = ''
        if value_:
            return value_
    for prop in ('dexhub.source.fanart', 'dexhub.source.poster'):
        value_ = _HOME.getProperty(prop)
        if value_:
            return value_
    return ''


def follow_playing(art=None):
    """Playback started (or the art arrived): with "artwork" chosen, the
    bar takes the title's colour. Runs on a small thread."""
    if value(ACCENT) != 'artwork' and value(SCRIM) != 'artwork':
        return

    def run():
        url = art or _playing_art()
        if not url or _HOME.getProperty(PROP_ART_KEY) == url:
            publish()
            return
        colour = artwork_colour(url)
        _HOME.setProperty(PROP_ART_KEY, url)
        if colour:
            _HOME.setProperty(PROP_ART, colour)
        else:
            _HOME.clearProperty(PROP_ART)
        publish()

    threading.Thread(target=run, name='DexHubOsdColour', daemon=True).start()


def has_pil():
    return _pil() is not None
