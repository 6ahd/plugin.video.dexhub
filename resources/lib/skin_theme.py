# -*- coding: utf-8 -*-
"""
DexHub skin-aware theming engine.

Reads the *active* Kodi skin's accent colour and derives a full, mathematically
consistent palette from it, then publishes the palette as Home-window
properties that DexHub's WindowXML dialogs bind to. The result: DexHub adopts
the host skin's identity instead of looking like a foreign add-on.

Supported skins get their real accent read directly; any unknown skin falls
back to the DexWorld brand palette so nothing ever looks broken.

Published properties (Window 10000):
    dexhub.tvos.theme.accent        AARRGGBB  primary accent
    dexhub.tvos.theme.accent_soft   AARRGGBB  ~70% accent (sub-accents, icons)
    dexhub.tvos.theme.accent_dim    AARRGGBB  ~28% accent (fills, hovers)
    dexhub.tvos.theme.accent_glow   AARRGGBB  lightened accent (highlights/sheen)
    dexhub.tvos.theme.secondary     AARRGGBB  complementary/secondary accent
    dexhub.tvos.theme.surface       AARRGGBB  deep surface background
    dexhub.tvos.theme.surface_card  AARRGGBB  raised card surface
    dexhub.tvos.theme.text          AARRGGBB  primary text
    dexhub.tvos.theme.muted         AARRGGBB  muted/secondary text
    dexhub.tvos.theme.ok            AARRGGBB  success/cached green (kept stable)
    dexhub.tvos.theme.on_accent     AARRGGBB  text and icons drawn on the accent
                                         (v5.10.131: dark on a light accent)
    dexhub.tvos.theme.glow          AARRGGBB  the accent as a soft page light
    dexhub.tvos.theme.name          string    the theme's name
    dexhub.tvos.theme.skin          string    active skin id
    dexhub.tvos.theme.ready         '1' once published

v5.10.131: on skin.dexhub the theme is the skin's colour theme (Kodi's
Interface > Skin > Colours), which lists Dex Hub's themes by name: the
palette follows it, and the add-on's own theme setting follows it too
(theme_sync keeps both in step).
"""

import xbmc
import xbmcgui


HOME = xbmcgui.Window(10000)

# DexWorld brand palette — the fallback identity when the skin is unknown
# or exposes no usable accent.
# v5.10.28: aligned with the generated identity (build_art palette):
# violet accent, electric-blue secondary, navy surfaces.
BRAND_ACCENT = 'FF9B6BFF'     # infinity violet
BRAND_SECONDARY = 'FF4F8BFF'  # electric blue
BRAND_SURFACE = 'FF0B0E1C'
BRAND_SURFACE_CARD = 'FF161B30'
BRAND_TEXT = 'FFF3F5FF'
BRAND_MUTED = 'FF9AA3C4'
BRAND_OK = 'FF34D399'


# --------------------------------------------------------------------------- #
#  Colour maths (pure stdlib — operates on AARRGGBB / RRGGBB hex strings)      #
# --------------------------------------------------------------------------- #

def _parse_hex(value):
    """Return (a, r, g, b) ints from an AARRGGBB or RRGGBB hex string, or None."""
    if not value:
        return None
    v = value.strip().lstrip('#')
    # Kodi colours are AARRGGBB; some skins store RRGGBB.
    if len(v) == 6:
        v = 'FF' + v
    if len(v) != 8:
        return None
    try:
        a = int(v[0:2], 16)
        r = int(v[2:4], 16)
        g = int(v[4:6], 16)
        b = int(v[6:8], 16)
        return (a, r, g, b)
    except ValueError:
        return None


def _to_hex(a, r, g, b):
    clamp = lambda x: max(0, min(255, int(round(x))))
    return '%02X%02X%02X%02X' % (clamp(a), clamp(r), clamp(g), clamp(b))


def _with_alpha(argb, alpha):
    a, r, g, b = argb
    return _to_hex(alpha, r, g, b)


def _scale(argb, factor):
    """Lighten (factor>1) or darken (factor<1) the RGB channels."""
    a, r, g, b = argb
    return _to_hex(a, r * factor, g * factor, b * factor)


def _mix(argb, tint, ratio):
    """Mix argb toward a tint (r,g,b) by ratio 0..1, keeping argb's alpha."""
    a, r, g, b = argb
    tr, tg, tb = tint
    nr = r + (tr - r) * ratio
    ng = g + (tg - g) * ratio
    nb = b + (tb - b) * ratio
    return _to_hex(a, nr, ng, nb)


def _luminance(argb):
    _, r, g, b = argb
    # Rec. 601 luma
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _linear(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(argb):
    _, r, g, b = argb
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def on_color(accent_hex, dark='FF0B0E1C'):
    """The text colour for a fill of ``accent_hex`` (v5.10.131).

    White while it reads well on the accent (contrast 3:1, the bar for bold
    labels), else the dark surface colour: white on a light accent (Slate
    Mono, a white focus colour) was unreadable.
    """
    argb = _parse_hex(accent_hex)
    if not argb:
        return 'FFFFFFFF'
    light = _relative_luminance(argb)
    return 'FFFFFFFF' if (1.05 / (light + 0.05)) >= 3.0 else dark


def _contrast(first, second):
    """WCAG contrast ratio of two AARRGGBB colours."""
    a, b = _parse_hex(first), _parse_hex(second)
    if not a or not b:
        return 21.0
    la, lb = sorted((_relative_luminance(a), _relative_luminance(b)))
    return (lb + 0.05) / (la + 0.05)


def _refine(palette):
    """v5.10.301: every theme legible the same way. The cards stood barely
    off the page (1.10 to 1.16:1, Nuvio's are about 1.25), and a dark accent
    hid on its page (Nuvio Violet 2.8:1): the card is lifted toward the text
    colour and the accent toward white just enough, nothing else changes."""
    surface, card = palette.get('surface'), palette.get('surface_card')
    text = _parse_hex(palette.get('text')) or (255, 243, 245, 255)
    if _parse_hex(surface) and _parse_hex(card):
        step = 0
        while _contrast(card, surface) < 1.24 and step < 12:
            step += 1
            card = _mix(_parse_hex(palette['surface_card']), text[1:], 0.02 * step)
        palette['surface_card'] = card
    accent = palette.get('accent')
    if _parse_hex(accent) and _parse_hex(surface):
        step, lifted = 0, accent
        while _contrast(lifted, surface) < 3.0 and step < 15:
            step += 1
            lifted = _mix(_parse_hex(accent), (255, 255, 255), 0.03 * step)
        if lifted != accent:
            a = _parse_hex(lifted)
            palette['accent'] = lifted
            palette['accent_soft'] = _with_alpha(a, 0xB3)
            palette['accent_dim'] = _with_alpha(a, 0x47)
            palette['glow'] = _with_alpha(a, 0x26)
    if _parse_hex(palette.get('accent')):
        palette['on_accent'] = on_color(palette['accent'], dark=surface if _parse_hex(surface) else 'FF0B0E1C')
    return palette


def _complement(argb):
    """A pleasing secondary: rotate toward a cooler/warmer partner.

    We don't do full HSL rotation (overkill); instead we swap emphasis between
    channels to get a harmonious, distinct secondary that still feels related.
    """
    a, r, g, b = argb
    # Emphasise the two lower channels, damp the dominant one a touch.
    mx = max(r, g, b)
    nr = b if r == mx else r
    ng = r if g == mx else g
    nb = g if b == mx else b
    return _to_hex(a, (nr + b) / 2, (ng + r) / 2, (nb + g) / 2)


# --------------------------------------------------------------------------- #
#  Skin accent detection                                                      #
# --------------------------------------------------------------------------- #

# Per-skin recipe: which Skin.String / Skin.Color holds the accent, and an
# optional explicit fallback accent for that skin family.
_SKIN_ACCENT_SOURCES = {
    'skin.arctic.fuse.3': ['Skin.String(focuscolor.name)'],
    'skin.arctic.fuse.2': ['Skin.String(focuscolor.name)'],
    'skin.arctic.horizon.2': ['Skin.String(focuscolor.name)'],
    'skin.arctic.zephyr.2.resurrection.mod': ['Skin.String(focuscolor.name)'],
    'skin.arctic.zephyr.reloaded': ['Skin.String(focuscolor.name)'],
    'skin.estuary': ['Skin.String(focuscolor.name)'],
}

# Generic skin-string names worth probing on any unknown skin.
_GENERIC_ACCENT_PROBES = [
    'Skin.String(focuscolor.name)',
    'Skin.String(highlightcolor)',
    'Skin.String(accentcolor)',
    'Skin.String(AccentColor)',
    'Skin.String(focuscolor)',
]


def _looks_like_hex(value):
    return _parse_hex(value) is not None


def _read_active_accent(skin_id):
    """Try the skin-specific source first, then generic probes."""
    probes = list(_SKIN_ACCENT_SOURCES.get(skin_id, []))
    for p in _GENERIC_ACCENT_PROBES:
        if p not in probes:
            probes.append(p)

    for probe in probes:
        try:
            val = xbmc.getInfoLabel(probe) or ''
        except Exception:  # pylint: disable=broad-except
            val = ''
        if _looks_like_hex(val):
            argb = _parse_hex(val)
            # Reject near-black / near-white "accents" (skins sometimes store
            # text colours here) — they make a terrible accent.
            lum = _luminance(argb)
            if 0.06 < lum < 0.95:
                return val
    return None


# --------------------------------------------------------------------------- #
#  Palette construction                                                        #
# --------------------------------------------------------------------------- #

def build_palette(accent_hex):
    """Derive a full palette from a single accent hex (AARRGGBB or RRGGBB)."""
    argb = _parse_hex(accent_hex) or _parse_hex(BRAND_ACCENT)
    # Force full opacity on the accent itself.
    a, r, g, b = argb
    accent = _to_hex(255, r, g, b)
    accent_argb = (255, r, g, b)

    lum = _luminance(accent_argb)
    # If the accent is quite dark, lighten the glow more aggressively so the
    # "lit from above" sheen still reads.
    glow_factor = 1.55 if lum < 0.35 else 1.28

    palette = {
        'accent': accent,
        'accent_soft': _with_alpha(accent_argb, 0xB3),   # 70%
        'accent_dim':  _with_alpha(accent_argb, 0x47),   # 28%
        'accent_glow': _scale(accent_argb, glow_factor),
        'secondary':   _complement(accent_argb),
        # Neutral surfaces: very dark, faintly tinted by the accent so the UI
        # feels cohesive rather than a pure-grey slab.
        'surface':      _mix(_parse_hex(BRAND_SURFACE), (r, g, b), 0.06),
        'surface_card': _mix(_parse_hex(BRAND_SURFACE_CARD), (r, g, b), 0.10),
        'text':  BRAND_TEXT,
        'muted': BRAND_MUTED,
        'ok':    BRAND_OK,  # cached/success stays a stable green for meaning
        'on_accent': on_color(accent),
        'glow': _with_alpha(accent_argb, 0x26),
    }
    return palette


# --------------------------------------------------------------------------- #
#  Public API                                                                  #
# --------------------------------------------------------------------------- #

def current_skin():
    try:
        return xbmc.getSkinDir() or ''
    except Exception:  # pylint: disable=broad-except
        return ''


# --------------------------------------------------------------------------- #
#  v4.4.0: fixed theme presets (the six approved design mockups)              #
# --------------------------------------------------------------------------- #

THEME_PRESETS = {
    # v5.10.131: calmer accents, and a light accent's text is dark (on_accent)
    '1': dict(name='Dex Crimson',    accent='FFE04355', secondary='FFF2A0A8',
              surface='FF0D0D12', surface_card='FF1B1B24', text='FFF2F2F7', muted='FF8E94A6'),
    '2': dict(name='Midnight Ocean', accent='FF38BDF8', secondary='FF7DD3FC',
              surface='FF070B14', surface_card='FF121B2E', text='FFEAF4FF', muted='FF7C8AA6'),
    '3': dict(name='Royal Violet',   accent='FF8B5CF6', secondary='FFC4B5FD',
              surface='FF0E0A16', surface_card='FF1D1430', text='FFF1EDFB', muted='FF8B84A6'),
    '4': dict(name='Emerald Noir',   accent='FF34C495', secondary='FF6EE7B7',
              surface='FF08110D', surface_card='FF12211A', text='FFECFDF5', muted='FF7FA695'),
    '5': dict(name='Amber Cinema',   accent='FFF2A93B', secondary='FFFCD34D',
              surface='FF120F0A', surface_card='FF241C12', text='FFFDF6EC', muted='FFA6987C'),
    '6': dict(name='Slate Mono',     accent='FFE5E7EB', secondary='FFA1A1AA',
              surface='FF0C0D0F', surface_card='FF1A1C21', text='FFF5F5F6', muted='FF71717A'),
    # v5.10.28: the identity preset — same navy/blue/violet as every bundled
    # tile, banner and poster, so the windows read as one system with the
    # artwork. Default for new installs; existing explicit choices are kept.
    '7': dict(name='Dex Infinity',   accent='FF9B6BFF', secondary='FF4F8BFF',
              surface='FF0B0E1C', surface_card='FF161B30', text='FFF3F5FF', muted='FF9AA3C4'),
    # v5.10.131: darker ones
    '8': dict(name='OLED Black',     accent='FF8C9BFF', secondary='FFA5B4FC',
              surface='FF000000', surface_card='FF111216', text='FFF2F3F7', muted='FF8A8F9E'),
    '9': dict(name='Graphite',       accent='FF8AB4F8', secondary='FFBAC8E0',
              surface='FF101113', surface_card='FF1C1E22', text='FFEDEEF0', muted='FF8E939C'),
    '10': dict(name='Deep Teal',     accent='FF2DD4BF', secondary='FF99F6E4',
               surface='FF061214', surface_card='FF0F2226', text='FFE8F7F5', muted='FF7FA3A0'),
    '11': dict(name='Rose Noir',     accent='FFF472B6', secondary='FFFBCFE8',
               surface='FF130A10', surface_card='FF23131D', text='FFFBEFF5', muted='FFA3889A'),
    '12': dict(name='Nordic Night',  accent='FF88C0D0', secondary='FF81A1C1',
               surface='FF0E1218', surface_card='FF1A212B', text='FFECEFF4', muted='FF8C97A8'),
    # v5.10.132: pure black ones, as Nuvio draws its pages (black, a near
    # black card, one strong colour on the focused card)
    '13': dict(name='Nuvio Black',   accent='FFE5484D', secondary='FFFF8A80',
               surface='FF000000', surface_card='FF121212', text='FFFFFFFF', muted='FF8C8C8C'),
    '14': dict(name='Pure Black',    accent='FFF5F5F5', secondary='FFB3B3B3',
               surface='FF000000', surface_card='FF141414', text='FFF5F5F5', muted='FF8A8A8A'),
    '15': dict(name='Black Gold',    accent='FFE6B450', secondary='FFF5D58A',
               surface='FF000000', surface_card='FF15130F', text='FFFAF7F0', muted='FF948C7C'),
    '16': dict(name='Black Aqua',    accent='FF00B8D4', secondary='FF80DEEA',
               surface='FF000000', surface_card='FF0E1416', text='FFF0FAFB', muted='FF86979B'),
    # v5.10.133: Nuvio's own themes (its ThemeColors and SupporterThemeColors):
    # the page (background), the card (backgroundCard), the strong colour
    # (secondary: the focused buttons) and the light one (focusRing: the
    # ring around a focused card) as Dex Hub's accent and secondary
    '17': dict(name='Nuvio White',       accent='FFF5F5F5', secondary='FFFFFFFF',
               surface='FF0D0D0D', surface_card='FF222222', text='FFFFFFFF', muted='FFB3B3B3'),
    '18': dict(name='Nuvio Crimson',     accent='FFE53935', secondary='FFFF5252',
               surface='FF0D0D0D', surface_card='FF241A1A', text='FFFFFFFF', muted='FFB3B3B3'),
    '19': dict(name='Nuvio Ocean',       accent='FF1E88E5', secondary='FF42A5F5',
               surface='FF0D0D0F', surface_card='FF1A1F24', text='FFFFFFFF', muted='FFB3B3B3'),
    '20': dict(name='Nuvio Violet',      accent='FF8E24AA', secondary='FFAB47BC',
               surface='FF0D0D0F', surface_card='FF1F1A24', text='FFFFFFFF', muted='FFB3B3B3'),
    '21': dict(name='Nuvio Emerald',     accent='FF43A047', secondary='FF66BB6A',
               surface='FF0D0D0D', surface_card='FF1A241A', text='FFFFFFFF', muted='FFB3B3B3'),
    '22': dict(name='Nuvio Amber',       accent='FFFB8C00', secondary='FFFFA726',
               surface='FF0F0D0D', surface_card='FF24201A', text='FFFFFFFF', muted='FFB3B3B3'),
    '23': dict(name='Nuvio Rose',        accent='FFD81B60', secondary='FFEC407A',
               surface='FF0D0D0D', surface_card='FF241A1F', text='FFFFFFFF', muted='FFB3B3B3'),
    '24': dict(name='Nuvio Gold',        accent='FFE8A91C', secondary='FFFFD45C',
               surface='FF0F0E0B', surface_card='FF262116', text='FFFFFFFF', muted='FFB3B3B3'),
    '25': dict(name='Nuvio Jade',        accent='FF22D37C', secondary='FF7BF08D',
               surface='FF0B0F0D', surface_card='FF16251D', text='FFFFFFFF', muted='FFB3B3B3'),
    '26': dict(name='Nuvio Rose Gold',   accent='FFEC70A9', secondary='FFFFB37A',
               surface='FF100C0F', surface_card='FF281A24', text='FFFFFFFF', muted='FFB3B3B3'),
    '27': dict(name='Nuvio Arctic Blue', accent='FF3185F5', secondary='FF4DE3FF',
               surface='FF0B0E14', surface_card='FF161E2A', text='FFFFFFFF', muted='FFB3B3B3'),
    '28': dict(name='Nuvio Graphite',    accent='FFAAB2BE', secondary='FFF3F5F7',
               surface='FF0C0D0F', surface_card='FF20242A', text='FFFFFFFF', muted='FFB3B3B3'),
}

# v5.10.133: the order the skin's theme picker lists them in (Nuvio's first)
PICKER_ORDER = ('17', '18', '19', '20', '21', '22', '23', '24', '25', '26', '27', '28', '13', '14', '15', '16',
                '7', '8', '9', '10', '11', '12', '1', '2', '3', '4', '5', '6')

# v5.10.132: the focus colours skin.dexhub offers by name (as Arctic Fuse 3's
# highlight colours, Nuvio's red and a few of Dex Hub's own); any other one
# comes from Kodi's colour picker (the skin's extras/accents.xml)
FOCUS_COLOURS = (
    ('FFF5F5F5', 'أبيض', 'White'),
    ('FF00B8D4', 'أكوا', 'Aqua'),
    ('FFF4511E', 'مرجاني', 'Coral'),
    ('FFE91E63', 'وردي', 'Vaporwave'),
    ('FF0091EA', 'أزرق', 'Blue'),
    ('FF5528A8', 'بنفسجي غامق', 'Purple'),
    ('FFE5484D', 'أحمر نوفيو', 'Nuvio red'),
    ('FFE6B450', 'ذهبي', 'Gold'),
    ('FF34C495', 'أخضر', 'Green'),
    ('FF9B6BFF', 'بنفسجي Dex Hub', 'Dex Hub violet'),
)


def _preset_setting():
    """Read the theme_preset enum ('0' auto / '1'..'7' fixed presets)."""
    try:
        import xbmcaddon
        return (xbmcaddon.Addon().getSetting('theme_preset') or '7').strip()
    except Exception:  # pylint: disable=broad-except
        return '7'


def _preset_palette(key, accent=None):
    preset = THEME_PRESETS.get(key)
    if not preset:
        return None
    palette = build_palette(accent or preset['accent'])
    for token in ('secondary', 'surface', 'surface_card', 'text', 'muted'):
        palette[token] = preset[token]
    palette['on_accent'] = on_color(palette['accent'], dark=preset['surface'])
    palette['name'] = preset['name']
    return _refine(palette)


# --------------------------------------------------------------------------- #
#  v5.10.131: skin.dexhub's colour themes are Dex Hub's themes                 #
# --------------------------------------------------------------------------- #

DEXHUB_SKIN = 'skin.dexhub'
DEFAULT_PRESET = '7'


def colour_file(key):
    """The skin colour theme (colors/<name>.xml) of a preset key."""
    preset = THEME_PRESETS.get(str(key))
    return preset['name'] if preset else ''


def preset_for_colour(name):
    """The preset key of a skin colour theme name ('' when it is none of ours)."""
    text = str(name or '').strip()
    if not text or text.lower() in ('skindefault', 'default', 'defaults'):
        return DEFAULT_PRESET
    for key, preset in THEME_PRESETS.items():
        if preset['name'].lower() == text.lower():
            return key
    return ''


def skin_colour_theme():
    try:
        return xbmc.getInfoLabel('Skin.CurrentColourTheme') or ''
    except Exception:  # pylint: disable=broad-except
        return ''


def skin_accent():
    """skin.dexhub's own accent, picked in its settings (Skin.String(dh.accent.custom))."""
    try:
        value = xbmc.getInfoLabel('Skin.String(dh.accent.custom)') or ''
    except Exception:  # pylint: disable=broad-except
        value = ''
    return value if _parse_hex(value) else ''


def focus_text():
    """skin.dexhub's text on the focused item (Skin.String(dh.ontext)): ''
    (by the colour: dark on a light one), 'white' or 'dark' (as Arctic Fuse
    3's selected text option)."""
    try:
        value = (xbmc.getInfoLabel('Skin.String(dh.ontext)') or '').strip().lower()
    except Exception:  # pylint: disable=broad-except
        value = ''
    return value if value in ('white', 'dark') else ''


def amoled():
    """skin.dexhub's pure black pages (Skin.String(dh.amoled) on, v5.10.133):
    every theme's page colour becomes black, as Nuvio's AMOLED mode."""
    try:
        return (xbmc.getInfoLabel('Skin.String(dh.amoled)') or '').strip().lower() == 'on'
    except Exception:  # pylint: disable=broad-except
        return False


PROP_PENDING = 'dexhub.tvos.theme.pending'


def pending_preset():
    """A theme picked in skin.dexhub's settings that Kodi's colour theme has
    not taken yet (theme_sync applies it when the settings close): shown at
    once meanwhile."""
    try:
        key = (HOME.getProperty(PROP_PENDING) or '').strip()
    except Exception:  # pylint: disable=broad-except
        key = ''
    return key if key in THEME_PRESETS else ''


def focus_glow():
    """False when skin.dexhub's focus glow is off (Skin.String(dh.focusglow))."""
    try:
        return (xbmc.getInfoLabel('Skin.String(dh.focusglow)') or '').strip().lower() != 'off'
    except Exception:  # pylint: disable=broad-except
        return True


def _skin_choices(palette):
    """The text and glow choices of skin.dexhub's settings on a palette."""
    if amoled():
        palette['surface'] = 'FF000000'
    text = focus_text()
    if text == 'white':
        palette['on_accent'] = 'FFFFFFFF'
    elif text == 'dark':
        # the theme's own dark (its page colour)
        palette['on_accent'] = palette.get('surface') if _parse_hex(palette.get('surface')) else 'FF101010'
    return palette


def resolve_palette():
    """Return (palette_dict, skin_id, accent_source_str)."""
    skin_id = current_skin()
    if skin_id == DEXHUB_SKIN:
        colour = skin_colour_theme()
        key = pending_preset() or preset_for_colour(colour)
        if key:
            palette = _preset_palette(key, accent=skin_accent() or None)
            if palette:
                return _skin_choices(palette), skin_id, 'skin-colours:%s' % (colour or 'default')
    preset_key = _preset_setting()
    if preset_key and preset_key != '0':
        palette = _preset_palette(preset_key)
        if palette:
            return palette, skin_id, 'preset:%s' % preset_key
    accent = _read_active_accent(skin_id)
    source = accent if accent else 'brand-fallback'
    if not accent:
        accent = BRAND_ACCENT
    return build_palette(accent), skin_id, source


# v5.10.131: the page lights (glows) at three strengths, picked in skin.dexhub
# (Skin.String(dh.glow): off, soft, vivid); soft everywhere else.
#   glow        the light of Dex Hub's own pages (the guide)
#   glow_a      skin.dexhub's page light (the accent)
#   glow_b      its second light (the theme's secondary colour)
#   glow_focus  the light around a focused card
GLOW_ALPHA = {'off': (0x00, 0x00, 0x00, 0x00), 'soft': (0x26, 0x5C, 0x40, 0x99),
              'vivid': (0x40, 0xB3, 0x8C, 0xFF)}


def glow_level(skin_id=None):
    if (skin_id or current_skin()) != DEXHUB_SKIN:
        return 'soft'
    try:
        value = (xbmc.getInfoLabel('Skin.String(dh.glow)') or '').strip().lower()
    except Exception:  # pylint: disable=broad-except
        value = ''
    return value if value in GLOW_ALPHA else 'soft'


def glows(palette, level='soft'):
    accent = _parse_hex(palette.get('accent')) or _parse_hex(BRAND_ACCENT)
    secondary = _parse_hex(palette.get('secondary')) or _parse_hex(BRAND_SECONDARY)
    page, page_a, page_b, focus = GLOW_ALPHA.get(level, GLOW_ALPHA['soft'])
    return {'glow': _with_alpha(accent, page), 'glow_a': _with_alpha(accent, page_a),
            'glow_b': _with_alpha(secondary, page_b), 'glow_focus': _with_alpha(accent, focus)}


def publish_theme(window=None, log=None):
    """Compute and publish the theme to the Home window (and optionally a
    specific window). Safe to call repeatedly (e.g. on every dialog onInit).
    """
    from .tvos_assets import publish_asset_root
    publish_asset_root()
    palette, skin_id, source = resolve_palette()
    palette = dict(palette)
    # Keep original opaque base surfaces; reject translucent/stale alpha.
    for key in ('surface', 'surface_card'):
        value = str(palette.get(key) or '').strip()
        if len(value) == 8 and all(c in '0123456789abcdefABCDEF' for c in value):
            palette[key] = 'FF' + value[2:]
    palette.update(glows(palette, glow_level(skin_id)))
    if skin_id == DEXHUB_SKIN and not focus_glow():
        # v5.10.132: no light around the focused card (Nuvio's look)
        palette['glow_focus'] = '00000000'

    targets = [HOME]
    if window is not None and window is not HOME:
        targets.append(window)

    for win in targets:
        try:
            for key, val in palette.items():
                win.setProperty('dexhub.tvos.theme.%s' % key, val)
            # v4.4.0: glass-card token — surface_card with translucent alpha
            # so result rows let the fanart backdrop breathe through.
            try:
                win.setProperty('dexhub.tvos.theme.card_glass', 'B8' + palette['surface_card'][2:])
            except Exception:
                pass
            win.setProperty('dexhub.tvos.theme.skin', skin_id)
            win.setProperty('dexhub.tvos.theme.ready', '1')
        except Exception:  # pylint: disable=broad-except
            pass

    try:
        from . import osd_look      # v5.10.298: a player bar in the theme's colour follows it
        osd_look.publish()
    except Exception:  # pylint: disable=broad-except
        pass

    if log:
        try:
            log('skin_theme: skin=%s accent=%s source=%s'
                % (skin_id, palette['accent'], source))
        except Exception:  # pylint: disable=broad-except
            pass

    return palette


def ensure_theme():
    """v5.10.298: publish the theme when the Home window lost it.

    Dex Hub's windows colour every card and label from these properties; when
    they are missing (a skin reload, a service that has not ticked yet) the
    cards draw plain white and the white text vanishes on them. Cheap: three
    property reads when the theme is there."""
    try:
        if (HOME.getProperty('dexhub.tvos.theme.ready') and HOME.getProperty('dexhub.tvos.theme.text')
                and HOME.getProperty('dexhub.tvos.theme.surface_card') and HOME.getProperty('dexhub.assetroot')):
            return False
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        publish_theme()
        return True
    except Exception:  # pylint: disable=broad-except
        return False



# --------------------------------------------------------------------------- #
#  v4.6.0: stable per-provider identity colours                               #
#                                                                             #
#  Every provider (Dexstreams, Arabmedia, Plex, ...) gets ONE colour that is  #
#  identical on the loading dashboard, the results rows, and the filter       #
#  chips — session after session — because it is derived from the provider    #
#  NAME (crc32), not from arrival order. Eight hues, tuned to stay readable   #
#  as 3-6px bars and small dots on the dark surfaces of every theme preset.   #
# --------------------------------------------------------------------------- #

PROVIDER_PALETTE = (
    'FF22D3EE',  # 1 cyan
    'FFF59E0B',  # 2 amber
    'FFA78BFA',  # 3 violet
    'FF34D399',  # 4 emerald
    'FFF472B6',  # 5 pink
    'FF60A5FA',  # 6 blue
    'FFE879F9',  # 7 magenta   (v5.10.28: was orange; on-brand)
    'FF818CF8',  # 8 indigo    (v5.10.28: was lime; on-brand)
)


def provider_color_index(name):
    """1-based palette index for a provider name. Deterministic across
    sessions and windows. Empty names collapse to slot 1."""
    try:
        import zlib
        text = str(name or '').strip().lower()
        if not text:
            return 1
        return (zlib.crc32(text.encode('utf-8', 'replace')) % len(PROVIDER_PALETTE)) + 1
    except Exception:  # pylint: disable=broad-except
        return 1


