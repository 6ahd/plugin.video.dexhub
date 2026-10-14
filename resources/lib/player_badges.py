# -*- coding: utf-8 -*-
"""Player badges (v5.10.141): the style the skin draws, Badger packs kept on
the device, and a folder of the user's own images.

Nothing is fetched when a video starts or the player's controls open. A
pack is read once, when it is chosen, together with its images, which are
kept in the add-on's folder (Kodi cannot draw SVG: an SVG badge is drawn as
its name, framed in the pack's colours). The skin draws a badge image at the
row's height with no plate behind it, in a box as wide as the image's shape
(badge_shapes: the Elite and Gold images are cut to their drawing), so a
pack looks the way it was drawn.

The folder style: the skin's own badges, any of them replaced by an image of
the same name in a folder (badges/ in the add-on's data folder, or one the
user picks). The badge is chosen the way the skin chooses its colourful
badges (detect()), from what Kodi reports of the stream.
"""
import hashlib
import json
import os
import re
import shutil
import time
from urllib.parse import unquote, urlsplit

import xbmc
import xbmcgui
import xbmcvfs

from . import badge_shapes

STYLES = ('colorful', 'minimal', 'compact', 'custom', 'folder', 'off')
PRESETS = (
    # key, Arabic, English, the pack's JSON
    ('elite', 'رسمية · Elite', 'Official · Elite',
     'https://raw.githubusercontent.com/leonevz/Elite-Badges/main/badges.json'),
    ('gold', 'ذهبية · Gold', 'Gold',
     'https://raw.githubusercontent.com/k45sle/NUVIO_BADGES/refs/heads/main/GOLD-NONAMES.json'),
    ('white', 'بيضاء بسيطة · Minimalist', 'Minimalist white',
     'https://raw.githubusercontent.com/sweatycab/nuvio-minimalist-badges/main/badges-white.json'),
    # v5.10.162: the sources page's JSON links (routes/badges, source_browser)
    ('sterzeck', 'Colorful & Concise', 'Colorful & Concise',
     'https://raw.githubusercontent.com/danielsdian/ColorfulAndConcise/refs/heads/main/Sterzeck_badge.json'),
    ('betterformatter', 'BetterFormatter Mono', 'BetterFormatter Mono',
     'https://raw.githubusercontent.com/9mousaa/BetterFormatter/main/presets/mono-bgb-sep-nodv.json'),
    ('nosv_transparent', 'Nosvasedis شفافة', 'Nosvasedis Transparent',
     'https://gist.githubusercontent.com/nosvasedis/63b769d205bddbbef79faf8beef53c28/raw/transparent-nosvasedis-badges-nuvio'),
    ('nosv_mono', 'Nosvasedis أحادية', 'Nosvasedis Mono',
     'https://gist.githubusercontent.com/nosvasedis/1858e332fef11d136f76c697ea6c7439/raw/mono-nosvasedis-badges-nuvio'),
    ('nosv_solid', 'Nosvasedis مصمتة', 'Nosvasedis Solid',
     'https://gist.githubusercontent.com/nosvasedis/7abd79424bb8981b511838524c52f097/raw/solid-nosvasedis-badges-nuvio'),
    ('nosv_classic', 'Nosvasedis كلاسيك', 'Nosvasedis Classic',
     'https://gist.githubusercontent.com/nosvasedis/7abd79424bb8981b511838524c52f097/raw/76c519d05f6518f0ed186ba802847c625ba730fa/nosvasedis-badges-nuvio'),
)
# v5.10.162: where a gallery choice applies (the player, the sources page,
# the hero of My Library and My Servers), or everywhere
AREAS = (('all', 'كل الأماكن', 'Everywhere'), ('player', 'المشغّل فقط', 'The player only'),
         ('sources', 'صفحة المصادر فقط', 'The sources page only'),
         ('library', 'المكتبة والسيرفرات فقط', 'Library and Servers only'))
AREA_NAMES = {'player': ('المشغّل', 'Player'), 'sources': ('المصادر', 'Sources'),
              'library': ('المكتبة', 'Library')}
SLOTS = 8
PER_GROUP = 2
DEFAULT_FOLDER = 'special://profile/addon_data/plugin.video.dexhub/badges/'
_IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
_MAX_JSON = 2 * 1024 * 1024
_MAX_IMAGE = 12 * 1024 * 1024
_MAX_TOTAL = 150 * 1024 * 1024
_LABELS = ('VideoPlayer.VideoCodec', 'VideoPlayer.AudioCodec', 'VideoPlayer.AudioChannels',
           'VideoPlayer.VideoResolution', 'VideoPlayer.HdrType', 'Player.FileNameAndPath',
           'Player.Process(amlogic.vs10.mode)', 'Player.Process(amlogic.eoft_gamut)')
_PROPS = ('image', 'w', 'x', 'text', 'look', 'fill', 'border', 'color')
_RULES = {}
_SIZES = {}
_LISTING = {}

# the skin's badges (media/dexhub/badges/<name>.png), in the order it shows
# them; a file of the same name in the folder replaces one
BUILTIN = (
    ('other_3d', 'ثلاثي الأبعاد 3D', '3D'),
    ('src_web_uhd', 'ويب 4K (WEB-DL و WEBRip)', 'WEB at 4K (WEB-DL, WEBRip)'),
    ('src_web', 'ويب (WEB-DL و WEBRip)', 'WEB (WEB-DL, WEBRip)'),
    ('src_bluray_uhd', 'بلوراي 4K', 'Ultra HD Blu-ray'),
    ('src_bluray', 'بلوراي و Remux', 'Blu-ray and Remux'),
    ('src_hdtv', 'HDTV', 'HDTV'),
    ('src_dvd', 'DVD', 'DVD'),
    ('res_8k', 'دقة 8K', '8K'),
    ('res_4k', 'دقة 4K', '4K'),
    ('res_1080', 'دقة 1080', '1080p'),
    ('res_720', 'دقة 720', '720p'),
    ('res_576', 'دقة 576', '576p'),
    ('res_540', 'دقة 540', '540p'),
    ('res_480', 'دقة 480', '480p'),
    ('hdr_dv', 'Dolby Vision', 'Dolby Vision'),
    ('hdr_10plus', 'HDR10+', 'HDR10+'),
    ('hdr_10', 'HDR10', 'HDR10'),
    ('hdr_hlg', 'HLG', 'HLG'),
    ('hdr_generic', 'HDR عام', 'HDR (other)'),
    ('codec_hevc', 'HEVC و H.265', 'HEVC, H.265'),
    ('codec_h264', 'H.264 و AVC', 'H.264, AVC'),
    ('codec_av1', 'AV1', 'AV1'),
    ('codec_vc1', 'VC 1', 'VC-1'),
    ('codec_mpeg2', 'MPEG 2', 'MPEG-2'),
    ('audio_atmos', 'Dolby Atmos', 'Dolby Atmos'),
    ('audio_dtsx_imax', 'DTS:X IMAX', 'DTS:X IMAX'),
    ('audio_dtsx', 'DTS:X', 'DTS:X'),
    ('audio_dtshd_ma', 'DTS HD Master Audio', 'DTS-HD Master Audio'),
    ('audio_dtshd_hra', 'DTS HD High Resolution', 'DTS-HD High Resolution'),
    ('audio_dts', 'DTS', 'DTS'),
    ('audio_truehd', 'Dolby TrueHD', 'Dolby TrueHD'),
    ('audio_ddplus', 'Dolby Digital Plus (DD+)', 'Dolby Digital Plus (DD+)'),
    ('audio_dd', 'Dolby Digital (DD)', 'Dolby Digital (DD)'),
    ('audio_aac', 'AAC', 'AAC'),
    ('audio_flac', 'FLAC', 'FLAC'),
    ('audio_opus', 'Opus', 'Opus'),
    ('audio_mp3', 'MP3', 'MP3'),
    ('audio_pcm', 'PCM', 'PCM'),
    ('audio_vorbis', 'Vorbis', 'Vorbis'),
    ('ch_714', 'قنوات 7.1.4', '7.1.4 channels'),
    ('ch_514', 'قنوات 5.1.4', '5.1.4 channels'),
    ('ch_10', 'قنوات 9.1', '9.1 channels'),
    ('ch_8', 'قنوات 7.1', '7.1 channels'),
    ('ch_7', 'قنوات 6.1', '6.1 channels'),
    ('ch_6', 'قنوات 5.1', '5.1 channels'),
    ('ch_5', 'قنوات 4.1', '4.1 channels'),
    ('ch_4', 'قنوات 4.0', '4.0 channels'),
    ('ch_3', 'قنوات 2.1', '2.1 channels'),
    ('ch_2', 'قنوات 2.0 ستيريو', '2.0 stereo'),
    ('ch_1', 'قناة 1.0', '1.0 mono'),
)
SAMPLE = ('src_bluray_uhd', 'res_4k', 'hdr_dv', 'codec_hevc', 'audio_atmos', 'ch_714')
SAMPLE_BLOB = 'REMUX 2160p 4K UHD Dolby Vision DV H.265 HEVC TrueHD Dolby TrueHD Atmos Dolby Atmos 7.1'


def _addon():
    from .settings_cache import cached_addon
    return cached_addon()


def _tr(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return en


def _profile():
    return xbmcvfs.translatePath(_addon().getAddonInfo('profile'))


def _set(key, value):
    """A setting written only when it changes: every write makes Kodi tell
    the service, which reads the whole file again."""
    addon = _addon()
    if (addon.getSetting(key) or '') != value:
        addon.setSetting(key, value)


def style():
    value = (_addon().getSetting('player_badge_style') or 'colorful').strip()
    value = value if value in STYLES else 'colorful'
    if value == 'custom':
        _to_bundled()
    return _adopt_colorful() if value == 'colorful' else value


def _to_bundled():
    """Keep the chosen pack where it is read now.

    v5.10.148: a pack chosen from the public repository add-on that also
    ships inside Dex Hub read Dex Hub's copy (the add-on may go away).
    v5.10.152: the other way round while the repository add-on is installed
    (its copy is the newest one published, jmxd White replaced Kometa in
    1.3.0); and a pack from the internet the repository now carries (Elite,
    Gold, Minimalist white) reads the repository's copy, with its preview."""
    url = _addon().getSetting('player_badges_json_url') or ''
    for root in (RESOURCE_PREFIX, BUNDLED_ROOT):
        head = 'addon://%s/' % root
        if url.startswith(head):
            sub = url[len(head):]
            if sub in BUNDLED:
                want = 'addon://' + _pack_home(sub)
                if want != url:
                    _set('player_badges_json_url', want)
            return
    preset = preset_of(url)
    local = _local_preset(preset[0]) if preset else ''
    if local:
        _set('player_badges_json_url', 'addon://' + local)


_ADOPT = {'at': 0.0}


def _adopt_colorful():
    """v5.10.144: the colourful badges left skin.dexhub for a pack of their
    own (resource.images.dexhub.badges.colorful). Whoever kept the colourful
    style gets the pack once it is installed, once; a later choice stays."""
    now = time.time()
    if now - _ADOPT['at'] < 60:
        return 'colorful'
    _ADOPT['at'] = now
    try:
        addon = _addon()
        pack = RESOURCE_PREFIX + '.colorful'
        if (addon.getSetting('player_badges_colorful_adopted') or '') == 'true' \
                or not os.path.isfile(os.path.join(_resource_folder(pack), 'badges.json')):
            return 'colorful'
        _set('player_badges_colorful_adopted', 'true')
        _set('player_badges_json_url', 'addon://' + pack)
        _set('player_badge_style', 'custom')
        xbmc.log('[DexHub] badges: the Colorful pack is installed, the colourful style now uses it', xbmc.LOGINFO)
        return 'custom'
    except Exception:
        return 'colorful'


def preset_of(url):
    url = str(url or '').strip()
    return next((p for p in PRESETS if p[3] == url), None)


def pack_name(url=None):
    url = _addon().getSetting('player_badges_json_url') if url is None else url
    preset = preset_of(url)
    if preset:
        return _tr(preset[1], preset[2])
    if resource_id(url):
        return _resource_label(resource_id(url))
    return _tr('حزمة برابط', 'Pack from a link') if url else ''


def style_key(current=None):
    """The badge page's item for the style in use: a style, a preset's key,
    or 'url' (a pack from a link)."""
    current = current or style()
    if current != 'custom':
        return current
    url = _addon().getSetting('player_badges_json_url') or ''
    if resource_id(url):
        return 'packs'
    return (preset_of(url) or ('url',))[0]


def style_label(current=None):
    current = current or style()
    if current == 'custom':
        return pack_name() or _tr('حزمة برابط', 'Pack from a link')
    return {'colorful': _tr('مؤطرة', 'Framed'), 'minimal': _tr('بسيطة', 'Minimal'),
            'compact': _tr('مدمجة', 'Compact'), 'folder': _tr('من مجلدي', 'From my folder'),
            'off': _tr('بدون رموز', 'No badges')}.get(current, current)


def pack_scale(url=None):
    """Keep custom and folder images in the bounded small row by default."""
    from .ui_preferences import value as preference
    return '' if preference('player_badges_size') == 'original' else str(SMALL)


def _flag(key, default='true'):
    value = (_addon().getSetting(key) or default).strip().lower()
    return 'false' if value == 'false' else 'true'


_SAMPLE_STATE = {'key': None}
SAMPLE_TEXT = '4K  \u2022  DV  \u2022  HEVC  \u2022  ATMOS  \u2022  7.1.4'


def _publish_sample(win, current, url):
    """v5.10.152: the chosen badges' own sample, as on their gallery card,
    for skin.dexhub's Badges section (dhs.badges.sample.1-6, .pair when the
    pack's sample is one or two long pictures, .text for the word styles).
    Worked out again only when the style or the pack changes."""
    key = (current, url, folder_shown() if current == 'folder' else '')
    if _SAMPLE_STATE['key'] == key:
        return
    _SAMPLE_STATE['key'] = key
    images, text = [], ''
    try:
        if current == 'custom':
            pack = resource_id(url)
            rules = _resource_rules(pack) if pack else _cached_rules(url)
            images = _sample_images(matched_badges(rules, SAMPLE_BLOB))
        elif current == 'colorful' and _skin_badges():
            images = _sample_images(folder_badges(list(SAMPLE), mine=False))
        elif current == 'folder':
            images = _sample_images(folder_badges(list(SAMPLE)))
        elif current in ('minimal', 'compact'):
            text = SAMPLE_TEXT
    except Exception as exc:
        xbmc.log('[DexHub] badge sample: %s' % exc, xbmc.LOGDEBUG)
        images = []
    # the skin's slot set, so the pictures stand at one height: two long
    # places for a one- or two-picture sample (jmxd), else six 150 or 190
    # apart, or four 285 apart for long pictures (Minimalist, Golden)
    mode = ''
    if 0 < len(images) <= 2:
        mode = 'pair'
    elif images:
        widest = 0.0
        for image in images:
            local = xbmcvfs.translatePath(image) if image.startswith('special://') else image
            size = badge_shapes.image_size(local)
            if size and size[1]:
                widest = max(widest, float(size[0]) / float(size[1]))
        mode = 'c' if widest <= 3.3 else ('' if widest <= 4.4 else 'w')
        if mode == 'w':
            images = images[:4]
    values = dict(('dhs.badges.sample.%d' % (index + 1), images[index] if index < len(images) else '')
                  for index in range(6))
    values['dhs.badges.sample.pair'] = '1' if mode == 'pair' else ''
    values['dhs.badges.sample.mode'] = mode
    values['dhs.badges.sample.text'] = text
    for prop, value in values.items():
        if win.getProperty(prop) != value:
            win.setProperty(prop, value)


def publish_style():
    win = xbmcgui.Window(10000)
    current = style()
    try:
        _publish_sample(win, current, (_addon().getSetting('player_badges_json_url') or '') if current == 'custom' else '')
    except Exception:
        pass
    for prop, value in (('dhs.osd.badge.style', current), ('dhs.osd.badge.key', style_key(current)),
                        ('dhs.osd.badge.packname', pack_name() if current == 'custom' else ''),
                        ('dhs.osd.badge.scale', pack_scale() if current in ('custom', 'folder') else ''),
                        # v5.10.145: the skin's Badges section shows these at once
                        ('dhs.badges.sources', _flag('elite_badges_enabled')),
                        ('dhs.badges.follow', _flag('badges_sources_follow')),
                        # v5.10.146: the sources page's badge images on every row
                        ('dhs.badges.rows', _flag('badges_sources_rows'))):
        if win.getProperty(prop) != value:
            win.setProperty(prop, value)


# v5.10.145: the switches of the Badges section, changed from the skin (or
# a list on other skins) and in force at once, with no settings dialog
SWITCHES = ('badges_home', 'elite_badges_enabled', 'badges_sources_follow', 'badges_sources_rows')


def toggle(key):
    if key not in SWITCHES:
        return False
    addon = _addon()
    now = _flag(key)
    addon.setSetting(key, 'false' if now == 'true' else 'true')
    publish_style()
    from . import ui_preferences
    ui_preferences.publish()
    if key == 'elite_badges_enabled':
        xbmcgui.Window(10000).setProperty('dexhub.badges.enabled', _flag(key))
    if key == 'badges_home' and xbmc.getSkinDir() == 'skin.dexhub':
        try:
            from .skinui import common as _skin_common
            _skin_common.ask_republish('badges')
        except Exception:
            pass
    return True


# --------------------------------------------------------------- packs
def _path(url):
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
    return os.path.join(_profile(), 'player_badges_%s.json' % key)


def _pack_dir(url):
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
    return os.path.join(_profile(), 'badge_packs', key)


def _entries(data, group='other'):
    if isinstance(data, list):
        for value in data:
            yield from _entries(value, group)
    elif isinstance(data, dict):
        if any(data.get(k) for k in ('pattern', 'regex', 'match')):
            yield group, data
            return
        if isinstance(data.get('groups'), (dict, list)):
            groups = data['groups'].values() if isinstance(data['groups'], dict) else data['groups']
            for node in groups:
                if isinstance(node, dict):
                    yield from _entries(node, str(node.get('id') or node.get('name') or group))
        found = False
        for key in ('filters', 'badges', 'rules', 'items'):
            if key in data:
                found = True
                coll = data[key]
                yield from _entries(list(coll.values()) if isinstance(coll, dict) else coll, group)
        if not found:
            for key, value in data.items():
                if key != 'groups' and isinstance(value, (list, dict)):
                    yield from _entries(value, group)


def kodi_color(value, default):
    value = str(value or '').strip().lstrip('#')
    if not re.fullmatch(r'[0-9a-fA-F]{6}([0-9a-fA-F]{2})?', value):
        return default
    return ('ff' + value if len(value) == 6 else value[6:] + value[:6]).lower()


_NOT_DRAWN = re.compile(r'[\U00010000-\U0010ffff\ufe0f\u200d\u2060-\u2064]')


def _words(text):
    """A badge's name as Kodi's fonts draw it (flags and other emoji out)."""
    return re.sub(r'\s+', ' ', _NOT_DRAWN.sub('', str(text or ''))).strip()[:32]


def parse_pack(data, base=''):
    """[{'group', 'rx', 'image', 'name', 'text', 'look', 'fill', 'border', 'color', 'w', 'limit'}].

    ``base``: where a pack installed from the repository keeps its images
    (5.10.144), so an image named without an address is that folder's.
    ``perGroup`` (the pack) or ``limit`` (a badge): badges a group shows."""
    rules = []
    try:
        per_group = max(1, min(4, int((data or {}).get('perGroup') or PER_GROUP))) if isinstance(data, dict) else PER_GROUP
    except (TypeError, ValueError):
        per_group = PER_GROUP
    for inherited, row in _entries(data):
        if row.get('isEnabled') is False or row.get('enabled') is False:
            continue
        pattern = row.get('pattern') or row.get('regex') or row.get('match') or ''
        if len(str(pattern)) > 1024:
            continue
        try:
            rx = re.compile(str(pattern).replace('(?i)', ''), re.I)
        except re.error:
            continue
        image = str(row.get('imageURL') or row.get('imageUrl') or row.get('image_url') or row.get('image') or row.get('icon') or row.get('url') or '').strip()
        if image and urlsplit(image).scheme.lower() not in ('http', 'https'):
            rel = image.replace('\\', '/').lstrip('/')
            image = (base + rel) if (base and not urlsplit(image).scheme and '..' not in rel.split('/')) else ''
        ext = os.path.splitext(urlsplit(image).path)[1].lower() if image else ''
        if ext and ext not in _IMAGE_EXT:
            image = ''          # SVG and the like: Kodi cannot draw them, the name instead
        name = _words(row.get('name') or row.get('label') or '')
        if not image and not name:
            continue
        group = str(row.get('groupId') or row.get('group') or row.get('category') or inherited)
        tag = str(row.get('tagStyle') or '').lower()
        fill = kodi_color(row.get('tagColor'), '00000000')
        border = kodi_color(row.get('borderColor'), '00000000')
        color = kodi_color(row.get('textColor'), 'ffffffff')
        if color.startswith('00'):
            color = 'ffffffff'          # a word must be readable
        look = 'fill' if ('fill' in tag and not fill.startswith('00')) else 'outline'
        if look == 'outline' and border.startswith('00'):
            border = color
        rules.append({'group': group, 'rx': rx, 'image': image, 'name': name,
                      'text': '' if image else name, 'look': look,
                      'fill': fill, 'border': border, 'color': color, 'w': '', 'pad': (0, 0),
                      'limit': _int(row.get('limit'), per_group)})
        if len(rules) == 512:
            break
    return rules


def _int(value, default):
    try:
        return max(1, min(4, int(value)))
    except (TypeError, ValueError):
        return default


def _read_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _cached_rules(url):
    """The saved pack's rules, its images on the device where they were kept."""
    if not url:
        return []
    if resource_id(url):
        return _resource_rules(resource_id(url))
    path = _path(url)
    manifest_path = os.path.join(_pack_dir(url), 'manifest.json')
    try:
        stamp = (os.stat(path).st_mtime_ns,
                 os.stat(manifest_path).st_mtime_ns if os.path.isfile(manifest_path) else 0)
    except OSError:
        return []
    cached = _RULES.get(url)
    if cached and cached[0] == stamp:
        return cached[1]
    data = _read_json(path)
    if data is None:
        return []
    rules = parse_pack(data)
    manifest = _read_json(manifest_path) if stamp[1] else None
    kept = (manifest or {}).get('images') or {}
    folder = os.path.dirname(manifest_path)
    for rule in rules:
        if not rule['image']:
            continue
        local = kept.get(rule['image'])
        if local:
            size, trim = (local.get('w'), local.get('h')), local.get('trim')
            rule['w'] = badge_shapes.width_class(size, trim)
            rule['pad'] = badge_shapes.paddings(rule['w'], size, trim)
            rule['image'] = os.path.join(folder, local['file'])
        elif manifest is not None:
            rule['image'], rule['text'] = '', rule['name']      # not on the device: its name
        else:
            rule['w'] = badge_shapes.FALLBACK       # saved before 5.10.141: from the web, as then
    _RULES[url] = (stamp, rules)
    return rules


def _fetch(url, limit, timeout=12):
    from urllib.request import Request, urlopen
    if urlsplit(url).scheme.lower() not in ('https', 'http'):
        raise ValueError('not a web address')
    with urlopen(Request(url, headers={'User-Agent': 'DexHub Kodi'}), timeout=timeout) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('too large')
    return raw


def _sniff(raw):
    if raw[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if raw[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return '.webp'
    if raw[:2] == b'\xff\xd8':
        return '.jpg'
    return ''


def load_pack(url, progress=None, select=True):
    """Explicit choice only: the pack's JSON and its images onto the device.
    The previous pack stays when this one cannot be read. ``select``: the
    player uses it at once (False: only saved, for another place)."""
    data = json.loads(_fetch(url, _MAX_JSON).decode('utf-8-sig'))
    rules = parse_pack(data)
    if not rules:
        raise ValueError('No enabled compatible badges in JSON')
    images = []
    for rule in rules:
        if rule['image'] and rule['image'] not in images:
            images.append(rule['image'])
    folder = _pack_dir(url)
    tmp = folder + '.new'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    kept, total = {}, 0
    for index, image in enumerate(images):
        if progress is not None:
            if progress.iscanceled():
                shutil.rmtree(tmp, ignore_errors=True)
                raise ValueError('cancelled')
            progress.update(int(100 * index / max(1, len(images))),
                            _tr('تحميل صور الرموز %d من %d', 'Badge images %d of %d') % (index + 1, len(images)))
        try:
            raw = _fetch(image, _MAX_IMAGE)
        except Exception as exc:
            xbmc.log('[DexHub] badge image unavailable (%s): %s' % (os.path.basename(urlsplit(image).path), exc), xbmc.LOGINFO)
            continue
        ext = _sniff(raw)
        if not ext or total + len(raw) > _MAX_TOTAL:
            continue
        name = '%03d%s' % (index, ext)
        with open(os.path.join(tmp, name), 'wb') as handle:
            handle.write(raw)
        total += len(raw)
        size = badge_shapes.image_size(os.path.join(tmp, name)) or (0, 0)
        entry = {'file': name, 'w': size[0], 'h': size[1]}
        trim = badge_shapes.trim_of(os.path.basename(unquote(urlsplit(image).path)), size)
        if trim:
            entry['trim'] = list(trim)
        kept[image] = entry
    with open(os.path.join(tmp, 'manifest.json'), 'w', encoding='utf-8') as handle:
        json.dump({'url': url, 'images': kept}, handle)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(os.path.dirname(folder), exist_ok=True)
    os.replace(tmp, folder)
    path = _path(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(path + '.tmp', path)
    if select:
        _set('player_badges_json_url', url)
    _RULES.pop(url, None)
    return len(rules), len(kept), len(images)


def pack_ready(url):
    if resource_id(url):
        return os.path.isfile(os.path.join(_resource_folder(resource_id(url)), 'badges.json'))
    return bool(url) and os.path.exists(_path(url)) and os.path.isfile(os.path.join(_pack_dir(url), 'manifest.json'))


def stream_blob(labels):
    """Real stream details take priority; do not match tokens or query strings."""
    video = labels.get('VideoPlayer.VideoCodec', '')
    audio = labels.get('VideoPlayer.AudioCodec', '')
    hdr = labels.get('VideoPlayer.HdrType', '')
    channels = labels.get('VideoPlayer.AudioChannels', '')
    channels = {'2': '2.0', '6': '5.1', '7': '6.1', '8': '7.1'}.get(channels, channels)
    resolution = labels.get('VideoPlayer.VideoResolution', '')
    aliases = {'h264': 'H.264 AVC', 'avc': 'H.264 AVC', 'avc1': 'H.264 AVC', 'h265': 'H.265 HEVC',
               'hevc': 'H.265 HEVC', 'eac3': 'EAC3 DD+ DDP Dolby Digital Plus',
               'ac3': 'AC3 DD Dolby Digital', 'eac3_ddp_atmos': 'EAC3 DD+ DDP Dolby Digital Plus Atmos Dolby Atmos',
               'truehd_atmos': 'TrueHD Dolby TrueHD Atmos Dolby Atmos', 'truehd': 'TrueHD Dolby TrueHD',
               'dtshd_ma_x': 'DTS:X DTS-X', 'dtshd_ma_x_imax': 'DTS:X DTS-X IMAX',
               'dtshd_ma': 'DTS-HD MA DTS-HD Master Audio', 'dtshd_hra': 'DTS-HD HRA DTS-HD',
               'dca': 'DTS', 'dts': 'DTS', 'aac': 'AAC', 'flac': 'FLAC', 'opus': 'Opus',
               'dolbyvision': 'Dolby Vision DV', 'hdr10plus': 'HDR10+ HDR10Plus', 'hdr10': 'HDR10',
               'hlg': 'HLG'}
    filename = unquote(urlsplit(labels.get('Player.FileNameAndPath', '')).path).rsplit('/', 1)[-1]
    if not hdr:
        # some outputs report no HDR type: the release name's own
        for pattern, name in ((r'(?:^|[ ._-])(?:dv|dovi|dolby[ ._-]?vision)(?:[ ._-]|$)', 'dolbyvision'),
                              (r'hdr10(?:\+|plus|p(?![a-z]))', 'hdr10plus'), (r'hdr10', 'hdr10'),
                              (r'(?:^|[ ._-])hdr(?:[ ._-]|$)', 'hdr')):
            if re.search(pattern, filename, re.I):
                hdr = name
                break
    bits = [aliases.get(video.lower(), video), aliases.get(audio.lower(), audio),
            aliases.get(hdr.lower(), hdr.upper()) or ('SDR' if video else ''), channels,
            ('%sp' % resolution if resolution.isdigit() else resolution)]
    if resolution in ('2160', '4K'):
        bits.append('2160p 4K UHD')
    # the actual HDR label supersedes release-name hints; only known release
    # sources come from the name, never digits from a channel, URL or title
    for pattern, name in ((r'web[ ._-]?dl', 'WEB-DL'), (r'web[ ._-]?rip', 'WEBRip'),
                          (r'blu[ ._-]?ray|bdrip|brrip', 'BluRay'), (r'\bremux\b', 'REMUX'),
                          (r'\bimax\b', 'IMAX'), (r'\bhdtv\b', 'HDTV')):
        if re.search(pattern, filename, re.I):
            bits.append(name)
    bits += edition_tokens(filename)
    return ' '.join(str(b) for b in bits if b)


def matched_badges(rules, blob, limit=SLOTS):
    """The pack's badges for this stream: in the pack's order, at most two a
    group (DV before HDR10, Atmos with TrueHD), one image or word once."""
    out, seen, counts = [], set(), {}
    for rule in rules:
        key = (rule['image'], rule['text'])
        if counts.get(rule['group'], 0) >= rule.get('limit', PER_GROUP) or key in seen:
            continue
        if rule['rx'].search(blob):
            out.append(rule)
            counts[rule['group']] = counts.get(rule['group'], 0) + 1
            seen.add(key)
            if len(out) >= limit:
                break
    return out


# ------------------------------------------------------- repository packs
# v5.10.144: badge packs installed from the repository as Kodi resource
# add-ons (resource.images.dexhub.badges.*): their images and a badges.json
# in Badger's shape, read where Kodi installed them. Nothing is downloaded
# by Dex Hub, and the skin stays light.
RESOURCE_PREFIX = 'resource.images.dexhub.badges'
REPOSITORY = 'repository.dexworld'
# v5.10.145: the public repository (repository.dexhub) carries the same packs
REPOSITORIES = (REPOSITORY, 'repository.dexhub')
# v5.10.145: packs whose images are bare lettering cut to the drawing look
# bigger than the Colorful flags at the same height: drawn smaller (skin.dexhub
# 3.20.1 reads dhs.osd.badge.scale), a pack may ask for it itself with
# "scale": <percent of the row, 85 or less> in its badges.json
# v5.10.148: the public packs ship inside Dex Hub (resources/badge_packs):
# shown at once with their own preview, nothing to install or download.
# Only Colorful comes from the (private) repository.
BUNDLED_ROOT = 'plugin.video.dexhub/badge_packs'
BUNDLED = ('studio', 'jmxdfull', 'kometa', 'jmxd')
PACK_SCALE = {RESOURCE_PREFIX + '/kometa': 72, RESOURCE_PREFIX + '/jmxd': 72,
              RESOURCE_PREFIX + '.kometa': 72, RESOURCE_PREFIX + '.jmxd': 72,
              BUNDLED_ROOT + '/kometa': 72, BUNDLED_ROOT + '/jmxd': 72}
SMALL = 72      # the one smaller row skin.dexhub 3.20.1 draws
_PACK_LABELS = {}


def _addons_dir():
    return xbmcvfs.translatePath('special://home/addons/')


def resource_id(url):
    """The pack's key in an addon:// address: an add-on's id, or id/folder
    for one of several packs in one add-on (resource.images.dexhub.badges)."""
    url = str(url or '')
    return url[len('addon://'):] if url.startswith('addon://') else ''


def _split(key):
    addon_id, _sep, sub = str(key or '').partition('/')
    return addon_id, sub.strip('/')


def _resource_folder(key):
    addon_id, sub = _split(key)
    parts = [_addons_dir(), addon_id, 'resources'] + ([sub] if sub else [])
    return os.path.join(*parts)


def _resource_label(key):
    hit = _PACK_LABELS.get(key)
    if hit:
        return hit
    addon_id, sub = _split(key)
    label = (sub or addon_id[len(RESOURCE_PREFIX):].lstrip('.')).replace('_', ' ').title()
    try:
        if sub:
            name = str((_read_json(os.path.join(_resource_folder(key), 'badges.json')) or {}).get('name') or '')
        else:
            with open(os.path.join(_addons_dir(), addon_id, 'addon.xml'), encoding='utf-8') as handle:
                found = re.search(r'<addon\b[^>]*\sname="([^"]+)"', handle.read(4096))
            name = found.group(1) if found else ''
        label = name.split(':', 1)[-1].strip() or label
    except (OSError, AttributeError):
        pass
    _PACK_LABELS[key] = label
    return label


# the version of resource.images.dexhub.badges whose copies Dex Hub carries
BUNDLED_FROM = (1, 3, 0)
_REPO_VERSION = {'at': 0.0, 'version': None}


def _repo_version():
    """The installed resource.images.dexhub.badges version as a tuple (read
    at most once a minute), or None."""
    now = time.time()
    if now - _REPO_VERSION['at'] < 60:
        return _REPO_VERSION['version']
    version = None
    try:
        with open(os.path.join(_addons_dir(), RESOURCE_PREFIX, 'addon.xml'), encoding='utf-8') as handle:
            found = re.search(r'<addon\b[^>]*\sversion="([0-9.]+)"', handle.read(4096))
        if found:
            version = tuple(int(part) for part in found.group(1).split('.') if part.isdigit())
    except (OSError, ValueError):
        version = None
    _REPO_VERSION.update({'at': now, 'version': version})
    return version


def _pack_home(sub):
    """v5.10.152: where one of the packs Dex Hub carries is read: the
    repository add-on's copy when it is installed and at least as new as
    Dex Hub's (1.3.0: jmxd White replaced Kometa), else Dex Hub's own."""
    repo = '%s/%s' % (RESOURCE_PREFIX, sub)
    version = _repo_version()
    if version and version >= BUNDLED_FROM and \
            os.path.isfile(os.path.join(_resource_folder(repo), 'badges.json')):
        return repo
    return '%s/%s' % (BUNDLED_ROOT, sub)


# v5.10.152: internet packs (PRESETS) the repository add-on carries since
# 1.3.0, trimmed and with their preview: preset key -> its folder there
LOCAL_PRESETS = {'elite': 'elite', 'gold': 'goldk45', 'white': 'minimalwhite',
                 'sterzeck': 'sterzeck', 'betterformatter': 'betterformatter'}    # 1.4.0


def _local_preset(key):
    """The repository's copy of an internet pack ('' when not installed)."""
    sub = LOCAL_PRESETS.get(key)
    if not sub:
        return ''
    local = '%s/%s' % (RESOURCE_PREFIX, sub)
    return local if os.path.isfile(os.path.join(_resource_folder(local), 'badges.json')) else ''


def bundled_packs():
    """[(pack key, name)] of the packs Dex Hub carries (read from the
    repository add-on when it has them, v5.10.152)."""
    out = []
    for sub in BUNDLED:
        key = _pack_home(sub)
        if os.path.isfile(os.path.join(_resource_folder(key), 'badges.json')):
            out.append((key, _resource_label(key)))
    return out


def installed_packs():
    """[(pack key, name)] of the badge packs: Dex Hub's own first, then the
    ones installed from the repository (an add-on with one pack, or one with
    a folder per pack); a repository copy of a bundled pack is left out."""
    root = _addons_dir()
    out = bundled_packs()
    own = set(BUNDLED)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if not (name == RESOURCE_PREFIX or name.startswith(RESOURCE_PREFIX + '.')):
            continue
        resources = os.path.join(root, name, 'resources')
        if os.path.isfile(os.path.join(resources, 'badges.json')):
            out.append((name, _resource_label(name)))
            continue
        try:
            subs = sorted(os.listdir(resources))
        except OSError:
            continue
        for sub in subs:
            if name == RESOURCE_PREFIX and sub in own:
                continue
            if os.path.isfile(os.path.join(resources, sub, 'badges.json')):
                key = '%s/%s' % (name, sub)
                out.append((key, _resource_label(key)))
    return out


def _resource_rules(pack):
    folder = _resource_folder(pack)
    path = os.path.join(folder, 'badges.json')
    key = 'addon://' + pack
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return []
    cached = _RULES.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    data = _read_json(path)
    if data is None:
        return []
    addon_id, sub = _split(pack)
    base = 'special://home/addons/%s/resources/%s' % (addon_id, (sub + '/') if sub else '')
    rules = parse_pack(data, base=base)
    for rule in rules:
        if not rule['image']:
            continue
        rel = rule['image'][len(base):] if rule['image'].startswith(base) else ''
        size = badge_shapes.image_size(os.path.join(folder, *rel.split('/'))) if rel else None
        if size:
            rule['w'] = badge_shapes.width_class(size)
            rule['pad'] = badge_shapes.paddings(rule['w'], size)
        else:
            rule['image'], rule['text'] = '', rule['name']
    _RULES[key] = (stamp, rules)
    return rules


# -------------------------------------------------------------- editions
EDITIONS = (
    ('alternate', r'alternate[ ._-]?cut'), ('anniversary', r'anniversary'),
    ('blackchrome', r'black[ ._-]?(?:and[ ._-]?|&[ ._-]?)?chrome'), ('coda', r'coda[ ._-]?cut'),
    ('collector', r"collector'?s"), ('criterion', r'criterion'), ('definitive', r'definitive'),
    ('diamond', r'diamond[ ._-]?edition'), ('directors', r"director'?s[ ._-]?cut"),
    ('imax_enhanced', r'imax[ ._-]?enhanced'), ('imax', r'\bimax\b'),
    ('extended_cut', r'extended[ ._-]?cut'), ('extended', r'\bextended\b'),
    ('final', r'final[ ._-]?cut'), ('international', r'international[ ._-]?cut'),
    ('platinum', r'platinum[ ._-]?edition'), ('remastered', r'remaster'), ('restored', r'\brestored\b'),
    ('donner', r'donner[ ._-]?cut'), ('signature', r'signature[ ._-]?edition'),
    ('special', r'special[ ._-]?edition'), ('theatrical_cut', r'theatrical[ ._-]?cut'),
    ('theatrical', r'\btheatrical\b'), ('ultimate', r'ultimate[ ._-]?(?:edition|cut)'),
    ('ulysses', r'ulysses[ ._-]?cut'), ('uncut', r'\buncut\b'), ('unrated', r'\bunrated\b'),
    ('minus_color', r'minus[ ._-]?colou?r'), ('open_matte', r'open[ ._-]?matte'),
)
_EDITION_RX = tuple((key, re.compile(pattern, re.I)) for key, pattern in EDITIONS)


def edition_tokens(name):
    """EDITION_<key> for each edition a release name carries (packs match these)."""
    text = re.sub(r'[._]+', ' ', str(name or ''))
    return ['EDITION_' + key for key, rx in _EDITION_RX if rx.search(text)] if text else []


def name_tokens(text):
    """A release name's facts in the words the packs match (the sources page)."""
    low = str(text or '').lower()
    out = []
    for pattern, words in ((r'\b(?:ddp|dd\+|e-?ac-?3)', 'DD+ EAC3'), (r'true[ .-]?hd', 'TrueHD'),
                           (r'atmos', 'Atmos'), (r'dts[ .:-]?x\b', 'DTS:X'),
                           (r'dts[ .-]?hd[ .-]?(?:ma|master)', 'DTS-HD MA'),
                           (r'\b(?:dv|dovi|dolby[ .]?vision)\b', 'DV'),
                           (r'hdr10(?:\+|plus|p\b)', 'HDR10+'), (r'\b(?:2160p|4k|uhd)\b', '2160p 4K')):
        if re.search(pattern, low):
            out.append(words)
    if re.search(r'\b(?:dd(?:[ .]?[257][ .]?[01])?|ac-?3)\b', low) and 'DD+ EAC3' not in out:
        out.append('AC3')
    return ' '.join(out + edition_tokens(text))


# ------------------------------------------------------ servers page hero
_SIZE_RX = re.compile(r'^\d+(?:[.,]\d+)?\s*(?:TB|GB|MB)$', re.I)
_ACODECS = {'TRUEHD': 'truehd', 'DTS': 'dca', 'DCA': 'dca', 'DCA-MA': 'dtshd_ma', 'DTS-HD': 'dtshd_ma',
            'DTS-HD MA': 'dtshd_ma', 'DTS-MA': 'dtshd_ma', 'DTS-X': 'dtshd_ma_x', 'DTS:X': 'dtshd_ma_x',
            'DCA-HRA': 'dtshd_hra', 'DTS-HD HRA': 'dtshd_hra', 'EAC3': 'eac3', 'E-AC3': 'eac3',
            'DD+': 'eac3', 'AC3': 'ac3', 'DD': 'ac3', 'AAC': 'aac', 'MP3': 'mp3', 'FLAC': 'flac',
            'OPUS': 'opus', 'PCM': 'pcm_s16le', 'LPCM': 'pcm_s16le', 'VORBIS': 'vorbis'}
_VCODECS = {'HEVC': 'hevc', 'H.265': 'hevc', 'H265': 'hevc', 'X265': 'hevc', 'H.264': 'h264', 'H264': 'h264',
            'AVC': 'h264', 'X264': 'h264', 'AV1': 'av1', 'VC-1': 'vc1', 'VC1': 'vc1', 'MPEG2': 'mpeg2video',
            'MPEG-2': 'mpeg2video', 'MPEG4': 'mpeg4', 'VP9': 'vp9'}
_CHANNELS = {'1.0': '1', '2.0': '2', '2.1': '3', '5.0': '5', '5.1': '6', '6.1': '7', '7.1': '8'}
_WORDS = {'src_web_uhd': 'WEB 4K', 'src_web': 'WEB', 'src_bluray_uhd': 'UHD BLU-RAY', 'src_bluray': 'BLU-RAY',
          'src_hdtv': 'HDTV', 'src_dvd': 'DVD', 'res_8k': '8K', 'res_4k': '4K', 'res_1080': '1080P',
          'res_720': '720P', 'res_576': '576P', 'res_540': '540P', 'res_480': '480P', 'hdr_dv': 'DOLBY VISION',
          'hdr_10plus': 'HDR10+', 'hdr_10': 'HDR10', 'hdr_hlg': 'HLG', 'hdr_generic': 'HDR', 'codec_hevc': 'HEVC',
          'codec_h264': 'H.264', 'codec_av1': 'AV1', 'codec_vc1': 'VC-1', 'codec_mpeg2': 'MPEG-2',
          'audio_atmos': 'ATMOS', 'audio_dtsx_imax': 'DTS:X IMAX', 'audio_dtsx': 'DTS:X',
          'audio_dtshd_ma': 'DTS-HD MA', 'audio_dtshd_hra': 'DTS-HD HRA', 'audio_dts': 'DTS',
          'audio_truehd': 'TRUEHD', 'audio_ddplus': 'DD+', 'audio_dd': 'DD', 'audio_aac': 'AAC',
          'audio_flac': 'FLAC', 'audio_opus': 'OPUS', 'audio_mp3': 'MP3', 'audio_pcm': 'PCM',
          'audio_vorbis': 'VORBIS', 'ch_714': '7.1.4', 'ch_514': '5.1.4', 'ch_10': '9.1', 'ch_8': '7.1',
          'ch_7': '6.1', 'ch_6': '5.1', 'ch_5': '5.0', 'ch_4': '4.0', 'ch_3': '2.1', 'ch_2': '2.0',
          'ch_1': '1.0', 'other_3d': '3D'}
FACTS_LINE = re.compile(r'^\s*((?:\[[^\]\n]+\]\s*(?:•\s*)?)+)\s*(?:\n+|$)')



def facts_labels(line, filename=''):
    """Kodi's stream labels (as the player reports them) and the size, from a facts line."""
    labels = dict((key, '') for key in _LABELS)
    labels['Player.FileNameAndPath'] = filename or ''
    size = ''
    atmos = 'atmos' in (filename or '').lower()
    for block in re.findall(r'\[\s*([^\]]+?)\s*\]', line or ''):
        upper = block.upper().strip()
        if _SIZE_RX.match(block.strip()):
            size = block.strip()
            continue
        resolution = {'4K': '2160', 'UHD': '2160', '2160P': '2160', '8K': '4320', 'SD': '480'}.get(upper)
        found = re.fullmatch(r'(\d{3,4})P', upper)
        if resolution or found:
            labels['VideoPlayer.VideoResolution'] = resolution or found.group(1)
            continue
        hdr = {'DOLBY VISION': 'dolbyvision', 'DV': 'dolbyvision', 'DOVI': 'dolbyvision',
               'HDR10+': 'hdr10plus', 'HDR10PLUS': 'hdr10plus', 'HDR10': 'hdr10', 'HDR': 'hdr10',
               'HLG': 'hlg'}.get(upper)
        if hdr:
            labels['VideoPlayer.HdrType'] = hdr
            continue
        if upper in _VCODECS:
            labels['VideoPlayer.VideoCodec'] = _VCODECS[upper]
            continue
        parts = upper.split()
        channels = next((p for p in parts if p in _CHANNELS or re.fullmatch(r'\d+CH', p)), '')
        atmos = atmos or 'ATMOS' in parts
        codec = ' '.join(p for p in parts if p not in (channels, 'ATMOS'))
        if codec in _ACODECS or channels:
            name = _ACODECS.get(codec, codec.lower())
            if atmos and name == 'truehd':
                name = 'truehd_atmos'
            elif atmos and name == 'eac3':
                name = 'eac3_ddp_atmos'
            labels['VideoPlayer.AudioCodec'] = name
            if channels:
                labels['VideoPlayer.AudioChannels'] = _CHANNELS.get(channels, channels[:-2])
    return labels, size


def _word(text):
    return {'text': text, 'look': 'outline', 'fill': '00000000', 'border': 'ccffffff',
            'color': 'ffffffff', 'pad': (0, 0)}


def hero_style():
    """The style of the hero's badges (My Library, My Servers): its own
    choice, else the player's (v5.10.162)."""
    own = (_addon().getSetting('hero_badge_style') or '').strip()
    return own if own in STYLES else style()


def hero_url():
    own = (_addon().getSetting('hero_badge_style') or '').strip()
    if own in STYLES:
        return _addon().getSetting('hero_badges_json_url') or ''
    return _addon().getSetting('player_badges_json_url') or ''


def hero_badges(line, filename=''):
    """The badges of a server copy for the servers page (the player's style,
    or the Library's own since v5.10.162), its size last as a word."""
    labels, size = facts_labels(line, filename)
    current = hero_style()
    if current == 'off' or not (size or any(v for k, v in labels.items() if k != 'Player.FileNameAndPath')):
        return []
    if current == 'custom':
        badges = matched_badges(_cached_rules(hero_url()), stream_blob(labels), limit=SLOTS - 1)
    elif current in ('colorful', 'folder'):
        badges = folder_badges(detect(labels), mine=current == 'folder')
    else:
        badges = [_word(_WORDS[key]) for key in detect(labels) if key in _WORDS]
    badges = badges[:SLOTS - 1]
    if size:
        badges.append(_word(size.upper().replace(',', '.')))
    return badges


# --------------------------------------------------------------- folder
def folder_path():
    path = (_addon().getSetting('player_badges_folder') or '').strip() or DEFAULT_FOLDER
    return path if path.endswith(('/', '\\')) else path + '/'


def folder_shown(path=None):
    """The folder as the user finds it (its real path on this device)."""
    return xbmcvfs.translatePath(path or folder_path())


def _skin_badges():
    """skin.dexhub's own badge images on the device ('' without the skin)."""
    try:
        import xbmcaddon
        root = xbmcaddon.Addon('skin.dexhub').getAddonInfo('path')
    except Exception:
        return ''
    path = os.path.join(xbmcvfs.translatePath(root), 'media', 'dexhub', 'badges')
    return path if os.path.isdir(path) else ''


def readme():
    lines = [_tr('رموز المشغّل من مجلدك', 'Player badges from your folder'), '',
             _tr('ضع صورة بنفس الاسم لأي رمز تبغى تبدله، وباقي الرموز تبقى من السكين.',
                 'Put an image with the same name for any badge you want to replace; the rest stay the skin\'s.'),
             _tr('يقبل PNG و JPG و WebP و GIF. الأفضل PNG شفاف مقصوص على الشعار بدون فراغ حوله.',
                 'PNG, JPG, WebP and GIF. Best: a transparent PNG cut to the logo, with no empty margin.'),
             _tr('يرسم الرمز بارتفاع واحد وعرضه حسب شكل صورتك (حتى ستة أضعاف ونص ارتفاعه).',
                 'Drawn at one height, as wide as your image\'s shape (up to six and a half times its height).'),
             _tr('بعد التبديل افتح أزرار المشغّل من جديد أو شغّل مقطع جديد.',
                 'After a change, open the player controls again or start another video.'), '']
    for name, ar, en in BUILTIN:
        lines.append('%-18s %s' % (name + '.png', _tr(ar, en)))
    return '\n'.join(lines) + '\n'


def _listing(folder, fresh=False):
    """{badge name: file name} of the folder's images (kept 20 seconds)."""
    hit = _LISTING.get(folder)
    if hit and not fresh and time.time() - hit[0] < 20:
        return hit[1]
    try:
        files = xbmcvfs.listdir(folder)[1] if xbmcvfs.exists(folder) else []
    except Exception:
        files = []
    mine = {}
    for name in files:
        stem, ext = os.path.splitext(name)
        if ext.lower() in _IMAGE_EXT:
            mine.setdefault(stem.lower(), name)
    _LISTING[folder] = (time.time(), mine)
    return mine


def prepare_folder(path=None, refill=False):
    """The folder ready: created, the skin's badges copied in when it holds
    none (or on request), and a list of the names. Its path."""
    folder = path or folder_path()
    if not folder.endswith(('/', '\\')):
        folder += '/'
    if not xbmcvfs.exists(folder) and not xbmcvfs.mkdirs(folder):
        raise OSError('cannot create %s' % folder)
    have = _listing(folder, fresh=True)
    source = _skin_badges()
    if source and (refill or not have):
        for name, _ar, _en in BUILTIN:
            src = os.path.join(source, name + '.png')
            if os.path.isfile(src) and (refill or name not in have):
                xbmcvfs.copy(src, folder + name + '.png')
    handle = xbmcvfs.File(folder + 'README.txt', 'w')
    try:
        handle.write(bytearray(readme().encode('utf-8')))
    finally:
        handle.close()
    _LISTING.pop(folder, None)
    _SIZES.clear()
    return folder


def detect(labels, stereo=False):
    """The skin's colourful badges for this stream (Includes_DexPlayer.xml's
    DhB_* conditions): the first that fits in each group, in its order."""
    def lower(key):
        return str(labels.get(key) or '').lower()
    res = str(labels.get('VideoPlayer.VideoResolution') or '')
    hdr, vc, ac = lower('VideoPlayer.HdrType'), lower('VideoPlayer.VideoCodec'), lower('VideoPlayer.AudioCodec')
    try:
        ch = int(labels.get('VideoPlayer.AudioChannels') or 0)
    except ValueError:
        ch = 0
    path = unquote(lower('Player.FileNameAndPath'))
    vs10, eotf = lower('Player.Process(amlogic.vs10.mode)'), lower('Player.Process(amlogic.eoft_gamut)')

    def has(*words):
        return any(w in path for w in words)
    keys = ['other_3d'] if stereo else []
    web = has('web-dl', 'webdl', 'web.dl', 'web_dl', 'webrip', 'web-rip', 'web.rip')
    uhd = has('2160p', 'uhd', 'ultrahd', 'ultra.hd', 'ultra-hd') or res in ('4K', '2160')
    bluray = has('blu-ray', 'bluray', 'blu.ray', 'bdrip', 'brrip', 'bdremux', 'remux', 'bd25', 'bd50', 'bdmv')
    if web:
        keys.append('src_web_uhd' if uhd else 'src_web')
    elif bluray:
        keys.append('src_bluray_uhd' if uhd else 'src_bluray')
    elif has('hdtv', 'pdtv'):
        keys.append('src_hdtv')
    elif has('dvdrip', '.dvd.', 'dvd5', 'dvd9', 'dvd-r'):
        keys.append('src_dvd')
    resolution = {'8K': 'res_8k', '4320': 'res_8k', '4K': 'res_4k', '2160': 'res_4k', '1080': 'res_1080',
                  '720': 'res_720', '576': 'res_576', '540': 'res_540', '480': 'res_480'}.get(res)
    if resolution:
        keys.append(resolution)
    if hdr == 'dolbyvision' or 'dolby vision' in vs10 or has('.dv.', '.dovi.', 'dolby.vision', 'dolbyvision', '-dv-', '_dv_'):
        keys.append('hdr_dv')
    elif hdr == 'hdr10plus' or has('hdr10plus', 'hdr10p', '.hdrplus.'):
        keys.append('hdr_10plus')
    elif hdr == 'hdr10' or 'hdr10' in vs10:
        keys.append('hdr_10')
    elif hdr == 'hlg' or 'hlg' in eotf:
        keys.append('hdr_hlg')
    elif hdr or has('.hdr.', '-hdr-', '.hdr10.'):
        keys.append('hdr_generic')
    codec = next((key for key, names in (
        ('codec_hevc', ('hevc', 'h265', 'hev1', 'hvc1')), ('codec_h264', ('h264', 'avc1', 'avc')),
        ('codec_av1', ('av1',)), ('codec_vc1', ('vc1', 'wvc1')), ('codec_mpeg2', ('mpeg2video', 'mpeg2'))) if vc in names), '')
    if codec:
        keys.append(codec)
    audio = next((key for key, names in (
        ('audio_atmos', ('eac3_ddp_atmos', 'truehd_atmos')), ('audio_dtsx_imax', ('dtshd_ma_x_imax',)),
        ('audio_dtsx', ('dtshd_ma_x', 'dts_x')), ('audio_dtshd_ma', ('dtshd_ma',)), ('audio_dtshd_hra', ('dtshd_hra',)),
        ('audio_dts', ('dca', 'dts')), ('audio_truehd', ('truehd', 'mlp')), ('audio_ddplus', ('eac3',)),
        ('audio_dd', ('ac3',)), ('audio_flac', ('flac',)), ('audio_opus', ('opus',)),
        ('audio_mp3', ('mp3', 'mp3float')), ('audio_vorbis', ('vorbis',))) if ac in names), '')
    if not audio and (ac.startswith('aac') or ac.startswith('he_aac')):
        audio = 'audio_aac'
    if not audio and ac.startswith('pcm'):
        audio = 'audio_pcm'
    if audio:
        keys.append(audio)
    if ch == 8 and ac in ('truehd_atmos', 'dtshd_ma_x', 'dtshd_ma_x_imax'):
        keys.append('ch_714')
    elif ch == 6 and ac == 'eac3_ddp_atmos':
        keys.append('ch_514')
    elif ch in (1, 2, 3, 4, 5, 6, 7, 8, 10):
        keys.append('ch_%d' % ch)
    return keys


def _stamp(path, local):
    try:
        if local:
            info = os.stat(path)
            return '%d:%d' % (info.st_mtime_ns, info.st_size)
        info = xbmcvfs.Stat(path)
        return '%d:%d' % (info.st_mtime(), info.st_size())
    except Exception:
        return ''


def _image_class(path, local, stamp):
    """The skin's width class of an image file and the empty width beside
    it in that box (its size read once)."""
    hit = _SIZES.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    size = None
    if local:
        size = badge_shapes.image_size(path)
    else:
        handle = xbmcvfs.File(path)
        try:
            size = badge_shapes.image_size_of(bytes(handle.readBytes(65536)))
        finally:
            handle.close()
    shape = badge_shapes.width_class(size) if size else badge_shapes.FALLBACK
    found = (shape, badge_shapes.paddings(shape, size) if size else (0, 0))
    _SIZES[path] = (stamp, found)
    return found


def _forget_textures(paths):
    """Kodi keeps its own copy of each image it has drawn and shows that
    copy once more after the file changed: the copies of replaced badges
    are dropped, so the new image shows the next time. Kodi lists them by
    their wrapped address (image://special%3a%2f%2f...res_4k.png/)."""
    for path in paths:
        name = path.replace('\\', '/').rsplit('/', 1)[-1]
        query = {'jsonrpc': '2.0', 'id': 1, 'method': 'Textures.GetTextures',
                 'params': {'properties': ['url'],
                            'filter': {'field': 'url', 'operator': 'contains', 'value': name}}}
        try:
            found = json.loads(xbmc.executeJSONRPC(json.dumps(query))).get('result', {}).get('textures') or []
        except Exception:
            found = []
        for texture in found:
            url = unquote(str(texture.get('url') or '')).replace('\\', '/')
            if path in url or url.rstrip('/').endswith('/' + name):
                xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Textures.RemoveTexture',
                                                'params': {'textureid': texture.get('textureid')}}))


def folder_badges(keys, mine=True):
    """The images for these badges: the folder's where it has one (``mine``), else the skin's."""
    folder = folder_path()
    mine = _listing(folder) if mine else {}
    skin = _skin_badges()
    local = bool(folder.startswith('special://') or folder.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', folder))
    seen_path = os.path.join(_profile(), 'badge_folder_seen.json')
    before = (_read_json(seen_path) or {}) if mine else {}
    seen = dict(before)
    changed, out = [], []
    for key in keys[:SLOTS]:
        if key in mine:
            path = folder + mine[key]
            real = xbmcvfs.translatePath(path) if local else path
            stamp = _stamp(real, local)
            if seen.get(path) and seen.get(path) != stamp:
                changed.append(path)
            seen[path] = stamp
            shape, pad = _image_class(real, local, stamp)
        elif skin:
            path = 'dexhub/badges/%s.png' % key
            real = os.path.join(skin, key + '.png')
            shape, pad = _image_class(real, True, _stamp(real, True))
        else:
            continue
        out.append({'image': path, 'w': shape, 'pad': pad, 'text': ''})
    if seen != before:
        try:
            with open(seen_path, 'w', encoding='utf-8') as handle:
                json.dump(seen, handle)
        except OSError:
            pass
    if changed:
        _forget_textures(changed)
    return out


# --------------------------------------------------------------- publish
def _write(win, prefix, badges):
    """The badges' properties: per slot its image and width class (or its
    word and colours) and 'x', the empty width between it and the next
    drawing, which the skin takes back from the gap; 'lead' the first's."""
    pads = [badge.get('pad') or (0, 0) for badge in badges]
    lead = badge_shapes.gap_class(pads[0][0]) if badges else ''
    if win.getProperty(prefix + '.lead') != lead:
        win.setProperty(prefix + '.lead', lead)
    for index in range(1, SLOTS + 1):
        badge = badges[index - 1] if index <= len(badges) else None
        values = {}
        if badge:
            if badge.get('image'):
                # 'fill' and 'border' fully transparent: skin.dexhub 3.18 and
                # older draw a plate under every image from them, and an empty
                # colour is white there (Kodi: no tint), the grey box behind it
                values = {'image': badge['image'], 'w': badge.get('w') or badge_shapes.FALLBACK,
                          'fill': '00ffffff', 'border': '00ffffff'}
            elif badge.get('text'):
                values = {'text': badge['text'], 'look': badge.get('look') or 'outline',
                          'fill': badge.get('fill') or '00000000', 'border': badge.get('border') or 'ffffffff',
                          'color': badge.get('color') or 'ffffffff'}
            if values and index < len(badges):
                values['x'] = badge_shapes.gap_class(pads[index - 1][1] + pads[index][0])
        for kind in _PROPS:
            prop = '%s.%d.%s' % (prefix, index, kind)
            value = values.get(kind, '')
            if win.getProperty(prop) != value:
                if value:
                    win.setProperty(prop, value)
                else:
                    win.clearProperty(prop)
    win.setProperty('%s.count' % prefix, str(len(badges)) if badges else '')


def current_badges():
    """The badges of what plays now, for the custom and folder styles."""
    current = style()
    if current not in ('custom', 'folder') or not xbmc.Player().isPlayingVideo():
        return []
    labels = {key: xbmc.getInfoLabel(key) or '' for key in _LABELS}
    if current == 'custom':
        return matched_badges(_cached_rules(_addon().getSetting('player_badges_json_url') or ''),
                              stream_blob(labels))
    return folder_badges(detect(labels, bool(xbmc.getCondVisibility('VideoPlayer.IsStereoscopic'))))


def publish():
    """Read labels/cache once per stream start or change, never poll/fetch."""
    win = xbmcgui.Window(10000)
    publish_style()
    _write(win, 'dhs.osd.badge', current_badges())


def preview():
    """Samples for the badge page: each kept preset's, the folder's and a
    pack from a link. The actual OSD badges are not touched."""
    win = xbmcgui.Window(10000)
    for key, _ar, _en, url in PRESETS:
        ready = pack_ready(url)
        _write(win, 'dhs.preview.%s' % key, matched_badges(_cached_rules(url), SAMPLE_BLOB) if ready else [])
        win.setProperty('dhs.preview.%s.ready' % key, 'true' if ready else '')
    _write(win, 'dhs.preview.folder', folder_badges(list(SAMPLE)))
    win.setProperty('dhs.preview.folderpath', folder_shown())
    url = _addon().getSetting('player_badges_json_url') or ''
    other = url if url and not preset_of(url) and not resource_id(url) else ''
    _write(win, 'dhs.preview.url', matched_badges(_cached_rules(other), SAMPLE_BLOB) if other else [])
    # v5.10.144: the repository pack in use, else the first one installed
    packs = installed_packs()
    shown = resource_id(url) or (packs[0][0] if packs else '')
    _write(win, 'dhs.preview.packs', matched_badges(_resource_rules(shown), SAMPLE_BLOB + ' EDITION_extended') if shown else [])
    win.setProperty('dhs.preview.packs.ready', 'true' if shown else '')
    win.setProperty('dhs.preview.packs.name', _resource_label(shown) if shown else '')
    win.setProperty('dhs.preview.packs.count', str(len(packs)) if packs else '')
    publish_style()
    from . import ui_preferences
    ui_preferences.publish()
    return True


def skin_outdated():
    """skin.dexhub's version when it is older than 3.19 (it draws a pack's
    badges on plates and has no folder style), else ''."""
    if xbmc.getSkinDir() != 'skin.dexhub':
        return ''
    try:
        import xbmcaddon
        version = xbmcaddon.Addon('skin.dexhub').getAddonInfo('version') or ''
    except Exception:
        return ''
    parts = tuple(int(p) for p in re.findall(r'\d+', version)[:3])
    return version if parts and parts < (3, 19, 0) else ''


def warn_old_skin(always=False):
    """Once at start (and on the badge page): a pack or the folder chosen
    with a skin.dexhub older than 3.19, which Kodi kept when the 3.19 zip was
    installed before the add-on it asked for."""
    old = skin_outdated()
    if not old or (not always and style() not in ('custom', 'folder')):
        return False
    xbmc.log('[DexHub] skin.dexhub %s is older than 3.19: player badges stay on plates until it is installed' % old,
             xbmc.LOGWARNING)
    xbmcgui.Dialog().notification('Dex Hub', _tr('ثبّت سكين Dex Hub 3.19 (المثبت %s) عشان تظهر الرموز بدون خلفية',
                                                 'Install skin Dex Hub 3.19 (installed: %s) for badges without plates') % old,
                                  time=9000, sound=False)
    return True


# --------------------------------------------------------------- choices
def _ensure_extra_osd():
    if xbmc.getSkinDir() == 'skin.dexhub':
        xbmc.executebuiltin('Skin.SetBool(ExtraOSD)')     # skins before 3.18 still ask for it


def _chosen(label):
    xbmcgui.Dialog().notification('Dex Hub', _tr('رموز المشغّل: %s', 'Player badges: %s') % label,
                                  time=2000, sound=False)


def select_style(selected, quiet=False):
    if selected not in STYLES:
        return False
    if selected == 'folder':
        try:
            prepare_folder()
        except Exception as exc:
            xbmc.log('[DexHub] badge folder unavailable: %s' % exc, xbmc.LOGWARNING)
            xbmcgui.Dialog().notification('Dex Hub', _tr('تعذر تجهيز مجلد الرموز', 'Could not prepare the badge folder'),
                                          time=3000, sound=False)
            return False
    _set('player_badge_style', selected)
    if selected != 'off':
        _ensure_extra_osd()
    publish()
    if quiet or warn_old_skin():
        return True
    if selected == 'folder':
        _folder_notice()
    else:
        _chosen(style_label(selected))
    return True


def prepare_pack(url, select=False):
    """The pack on the device (read with its images once); True when ready.
    ``select``: the player takes it too."""
    url = str(url or '').strip()
    if not url:
        return False
    if resource_id(url) and not pack_ready(url):
        xbmcgui.Dialog().notification('Dex Hub', _tr('الحزمة غير مثبتة', 'The pack is not installed'),
                                      time=3000, sound=False)
        return False
    if not pack_ready(url):
        progress = xbmcgui.DialogProgress()
        progress.create('Dex Hub', _tr('تحميل حزمة الرموز مرة وحدة…', 'Loading the badge pack once…'))
        try:
            count, kept, images = load_pack(url, progress, select=select)
        except Exception as exc:
            progress.close()
            xbmc.log('[DexHub] badge pack unavailable: %s' % exc, xbmc.LOGWARNING)
            if str(exc) != 'cancelled':
                xbmcgui.Dialog().notification('Dex Hub', _tr('تعذر تحميل الحزمة؛ المحفوظة باقية',
                                                             'Could not load pack; saved pack retained'),
                                              time=3000, sound=False)
            return False
        progress.close()
        xbmc.log('[DexHub] badge pack saved: %d badges, %d of %d images on the device' % (count, kept, images), xbmc.LOGINFO)
    elif select:
        _set('player_badges_json_url', url)
    return True


def use_pack(url, quiet=False):
    """A pack chosen: read with its images (once), then shown."""
    url = str(url or '').strip()
    if not url:
        return False
    if resource_id(url) and not pack_ready(url):
        xbmcgui.Dialog().notification('Dex Hub', _tr('الحزمة غير مثبتة', 'The pack is not installed'),
                                      time=3000, sound=False)
        return False
    if not pack_ready(url):
        progress = xbmcgui.DialogProgress()
        progress.create('Dex Hub', _tr('تحميل حزمة الرموز مرة وحدة…', 'Loading the badge pack once…'))
        try:
            count, kept, images = load_pack(url, progress)
        except Exception as exc:
            progress.close()
            xbmc.log('[DexHub] player badge pack unavailable: %s' % exc, xbmc.LOGWARNING)
            if str(exc) != 'cancelled':
                xbmcgui.Dialog().notification('Dex Hub', _tr('تعذر تحميل الحزمة؛ المحفوظة باقية',
                                                             'Could not load pack; saved pack retained'),
                                              time=3000, sound=False)
            return False
        progress.close()
        xbmc.log('[DexHub] badge pack saved: %d badges, %d of %d images on the device' % (count, kept, images), xbmc.LOGINFO)
    else:
        _set('player_badges_json_url', url)
    _set('player_badge_style', 'custom')
    _ensure_extra_osd()
    publish()
    if not quiet and not warn_old_skin():
        _chosen(pack_name(url))
    return True


def use_preset(key, quiet=False):
    preset = next((p for p in PRESETS if p[0] == key), None)
    if not preset:
        return False
    # v5.10.152: the repository's copy when installed (nothing to download)
    local = _local_preset(key)
    return use_pack('addon://' + local if local else preset[3], quiet=quiet)


def _folder_notice():
    xbmcgui.Dialog().ok(_tr('رموز من مجلدك', 'Badges from your folder'), _tr(
        'المجلد: %s[CR]بدّل أي صورة فيه بصورة بنفس الاسم (الأسماء في README.txt) وتظهر في المشغّل. '
        'توصل له من مدير الملفات في كودي أو من الشبكة: Userdata ثم addon_data ثم plugin.video.dexhub ثم badges.',
        'Folder: %s[CR]Replace any image in it with one of the same name (listed in README.txt) and the player shows it. '
        'Reach it from Kodi\'s file manager or over the network: Userdata, addon_data, plugin.video.dexhub, badges.')
        % folder_shown())


def folder_menu(txt=None):
    """The folder's options: where it is, another folder, the skin's images back."""
    while True:
        options = [_tr('مكان المجلد وأسماء الصور', 'Where it is and the image names'),
                   _tr('اختيار مجلد آخر', 'Choose another folder'),
                   _tr('رجوع للمجلد الافتراضي', 'Back to the default folder'),
                   _tr('إرجاع صور السكين الأصلية للمجلد', 'Put the skin\'s images back in the folder'),
                   _tr('استخدام رموز المجلد الآن', 'Use the folder\'s badges now')]
        pick = xbmcgui.Dialog().select(_tr('رموز من مجلدك', 'Badges from your folder'), options)
        if pick < 0:
            return True
        try:
            if pick == 0:
                prepare_folder()
                xbmcgui.Dialog().textviewer(folder_shown(), readme())
            elif pick == 1:
                chosen = xbmcgui.Dialog().browse(0, _tr('مجلد الرموز', 'Badge folder'), 'files', '', False, False,
                                                 folder_path())
                if chosen and chosen != folder_path():
                    prepare_folder(chosen)
                    _set('player_badges_folder', chosen)
                    publish()
            elif pick == 2:
                _set('player_badges_folder', '')
                prepare_folder()
                publish()
            elif pick == 3:
                if xbmcgui.Dialog().yesno('Dex Hub', _tr('تُستبدل صور المجلد بصور السكين الأصلية. متأكد؟',
                                                        'The folder\'s images are replaced by the skin\'s. Sure?')):
                    prepare_folder(refill=True)
                    publish()
            elif pick == 4:
                select_style('folder')
                return True
        except Exception as exc:
            xbmc.log('[DexHub] badge folder: %s' % exc, xbmc.LOGWARNING)
            xbmcgui.Dialog().notification('Dex Hub', _tr('تعذر الوصول للمجلد', 'Could not reach the folder'),
                                          time=3000, sound=False)


def show(txt=None):
    """Other skins (and the add-on's settings): the styles in a list."""
    while True:
        labels = [_tr('مؤطرة (صور السكين)', 'Framed (the skin\'s images)')]
        labels += [_tr(ar, en) for _key, ar, en, _url in PRESETS]
        labels += [_tr('حزم من السورس (Kometa و jmxd وغيرها)', 'Packs from the repository (Kometa, jmxd and more)'),
                   _tr('من مجلدي (تبديل يدوي)', 'From my folder (replace by hand)'),
                   _tr('بسيطة (كلمات)', 'Minimal (words)'), _tr('مدمجة (سطر)', 'Compact (one line)'),
                   _tr('حزمة برابط JSON', 'Pack from a JSON link'), _tr('بدون رموز', 'No badges')]
        pick = xbmcgui.Dialog().select(_tr('رموز المشغّل', 'Player badges'), labels)
        if pick < 0:
            return True
        if pick == 0:
            return select_style('colorful')
        if pick <= len(PRESETS):
            if use_preset(PRESETS[pick - 1][0]):
                return True
            continue
        rest = pick - len(PRESETS)
        if rest == 1:
            choose_installed()
            return True
        if rest == 2:
            folder_menu()
            return True
        if rest == 3:
            return select_style('minimal')
        if rest == 4:
            return select_style('compact')
        if rest == 5:
            choose_pack()
            return True
        return select_style('off')


def open_repository():
    """Kodi's add-on browser at the repository's badge packs."""
    repo = next((r for r in REPOSITORIES if xbmc.getCondVisibility('System.HasAddon(%s)' % r)), '')
    path = ('addons://%s/kodi.resource.images/' % repo) if repo else 'addons://all/kodi.resource.images/'
    xbmc.executebuiltin('ActivateWindow(AddonBrowser,%s,return)' % path)


def choose_installed(txt=None):
    """The packs installed from the repository, and the way to more."""
    packs = installed_packs()
    current = resource_id(_addon().getSetting('player_badges_json_url') or '') if style() == 'custom' else ''
    labels = [('%s  [B]\u2713[/B]' % name) if addon_id == current else name for addon_id, name in packs]
    labels.append(_tr('حزم أكثر من السورس…', 'More packs from the repository…'))
    pick = xbmcgui.Dialog().select(_tr('حزم الرموز من السورس', 'Badge packs from the repository'), labels)
    if pick < 0:
        return True
    if pick == len(packs):
        open_repository()
        return True
    if use_pack('addon://' + packs[pick][0]):
        preview()
    return True


def choose_pack(txt=None):
    addon = _addon()
    current = addon.getSetting('player_badges_json_url') or ''
    url = xbmcgui.Dialog().input(_tr('رابط JSON لحزمة Badger', 'Badger pack JSON link'),
                                 defaultt='' if preset_of(current) or resource_id(current) else current)
    url = (url or '').strip()
    if not url:
        return True
    if pack_ready(url) and not preset_of(url):
        # a link chosen again: read anew (the pack may have changed)
        shutil.rmtree(_pack_dir(url), ignore_errors=True)
    if use_pack(url):
        preview()
    return True


# ------------------------------------------------------------ the gallery
# v5.10.148: one page for the badges everywhere (Dex Hub menu, Player
# options, the skin's Badges section, the setup steps): each style as a
# card with its own sample row drawn from its real images, chosen in place
# (in force at once, the page stays).
def _sample_images(badges, limit=6):
    out = []
    for badge in badges or []:
        image = badge.get('image') if isinstance(badge, dict) else ''
        if image:
            if image.startswith('dexhub/badges/'):
                image = 'special://skin/media/' + image
            out.append(image)
        if len(out) >= limit:
            break
    return out


def _card_of(style_value, url):
    """The gallery card of a style and its pack."""
    if style_value != 'custom':
        return 'style:' + style_value
    rid = resource_id(url)
    if rid:
        return 'pack:' + rid
    preset = preset_of(url)
    if preset:
        local = _local_preset(preset[0])
        return ('pack:' + local) if local else ('preset:' + preset[0])
    return 'action:link' if url else 'style:off'


def area_cards():
    """{area: the gallery card in force there} (v5.10.162)."""
    addon = _addon()
    player_url = addon.getSetting('player_badges_json_url') or ''
    current = style()
    player = _card_of(current, player_url)
    if (addon.getSetting('elite_badges_enabled') or 'true').lower() == 'false':
        sources = 'style:off'
    else:
        own = addon.getSetting('sources_badges_json_url') or ''
        follow = (addon.getSetting('badges_sources_follow') or 'true').lower() != 'false'
        preset = preset_of(player_url)
        if own:
            sources = _card_of('custom', own)
        elif follow and current == 'custom' and player_url and not (preset and preset[0] == 'elite'):
            sources = player
        else:
            sources = 'sources:own'
    hero = (addon.getSetting('hero_badge_style') or '').strip()
    library = _card_of(hero, addon.getSetting('hero_badges_json_url') or '') if hero in STYLES else player
    return {'player': player, 'sources': sources, 'library': library}


def _where(key, used):
    areas = [area for area in ('player', 'sources', 'library') if used.get(area) == key]
    if len(areas) == 3:
        return _tr('كل الأماكن', 'Everywhere')
    return _tr(u'\u060c ', ', ').join(_tr(*AREA_NAMES[area]) for area in areas)


def gallery():
    """[{key, label, desc, images, text, current, where}] for the badge cards."""
    current = style()
    url = _addon().getSetting('player_badges_json_url') or ''
    used = area_cards()
    cards = []
    if _skin_badges():
        cards.append({'key': 'style:colorful', 'label': _tr('ملونة مؤطرة', 'Colorful framed'),
                      'desc': _tr('أعلام السكين الملونة', "The skin's colourful flags"),
                      'images': _sample_images(folder_badges(list(SAMPLE), mine=False))})
    for key, name in installed_packs():
        rules = _resource_rules(key)
        images = _sample_images(matched_badges(rules, SAMPLE_BLOB))
        own = key.startswith(BUNDLED_ROOT)
        cards.append({'key': 'pack:' + key, 'label': name,
                      'desc': _tr('داخل Dex Hub', 'Inside Dex Hub') if own else _tr('من السورس', 'From the repository'),
                      'images': images})
    sample = '4K  \u2022  DV  \u2022  HEVC  \u2022  ATMOS  \u2022  7.1.4'
    cards.append({'key': 'style:minimal', 'label': _tr('بسيطة', 'Minimal'),
                  'desc': _tr('كلمات في إطارات', 'Words in frames'), 'text': sample})
    cards.append({'key': 'style:compact', 'label': _tr('سطر واحد', 'One line'),
                  'desc': _tr('كل شيء في سطر نصي', 'Everything on one text line'), 'text': sample})
    cards.append({'key': 'style:folder', 'label': _tr('من مجلدي', 'From my folder'),
                  'desc': _tr('بدّل أي صورة بصورتك', 'Replace any image with yours'),
                  'images': _sample_images(folder_badges(list(SAMPLE))) if current == 'folder' else [],
                  'text': '' if current == 'folder' else _tr('صورك أنت', 'Your own images')})
    for key, ar, en, purl in PRESETS:
        if _local_preset(key):
            continue        # v5.10.152: the repository's copy is listed above
        ready = pack_ready(purl)
        cards.append({'key': 'preset:' + key, 'label': _tr(ar, en),
                      'desc': _tr('من الإنترنت', 'From the internet') if ready
                      else _tr('تتحمّل مرة وحدة عند اختيارها', 'Downloaded once when chosen'),
                      'images': _sample_images(matched_badges(_cached_rules(purl), SAMPLE_BLOB)) if ready else [],
                      'text': '' if ready else _tr('بدون معاينة قبل التحميل', 'No preview before download')})
    cards.append({'key': 'sources:own', 'label': _tr('الافتراضية للمصادر', 'Sources page default'),
                  'desc': _tr('رموز صفحة المصادر الأصلية (للمصادر فقط)', "The sources page's own badges (sources only)"),
                  'text': _tr('للمصادر فقط', 'Sources page only')})
    cards.append({'key': 'action:link', 'label': _tr('حزمة برابط', 'Pack from a link'),
                  'desc': _tr('أي حزمة Badger برابط JSON', 'Any Badger pack by its JSON link')})
    cards.append({'key': 'style:off', 'label': _tr('بدون رموز', 'No badges'),
                  'desc': _tr('بدون رموز في المكان اللي تختاره', 'No badges where you choose')})
    for card in cards:
        where = _where(card['key'], used)
        card['current'] = bool(where)
        card['where'] = where
        if where:
            card['desc'] = _tr('مستخدمة: %s', 'In use: %s') % where
    return cards


def choose_area(label=''):
    """Where a gallery choice applies; '' when Back."""
    try:
        from .homeui.options import choose
        pick = choose(label or _tr('رموز الجودة', 'Quality badges'),
                      [_tr(ar, en) for _k, ar, en in AREAS],
                      subtitle=_tr('وين تبي تطبقها؟', 'Where should it apply?'))
    except Exception:
        pick = xbmcgui.Dialog().select(_tr('وين تبي تطبقها؟', 'Where should it apply?'),
                                       [_tr(ar, en) for _k, ar, en in AREAS])
    return AREAS[pick][0] if 0 <= pick < len(AREAS) else ''


def _pin_followers():
    """Before the player alone changes: the places that followed it keep
    what they showed."""
    addon = _addon()
    old_style, old_url = style(), addon.getSetting('player_badges_json_url') or ''
    if not (addon.getSetting('hero_badge_style') or '').strip():
        _set('hero_badge_style', old_style)
        _set('hero_badges_json_url', old_url if old_style == 'custom' else '')
    preset = preset_of(old_url)
    if (not addon.getSetting('sources_badges_json_url')
            and (addon.getSetting('badges_sources_follow') or 'true').lower() != 'false'
            and old_style == 'custom' and old_url and not (preset and preset[0] == 'elite')):
        _set('sources_badges_json_url', old_url)


def apply_card(key, area='all'):
    """Choose a gallery card for ``area``; True when the badges changed."""
    kind, _sep, value = str(key or '').partition(':')
    if not area:
        return False
    chosen_style, url = '', ''
    if kind == 'style':
        chosen_style = value
    elif kind == 'pack':
        chosen_style, url = 'custom', 'addon://' + value
    elif kind == 'preset':
        preset = next((p for p in PRESETS if p[0] == value), None)
        if not preset:
            return False
        local = _local_preset(value)
        chosen_style, url = 'custom', ('addon://' + local) if local else preset[3]
    elif kind == 'action' and value == 'link':
        current = _addon().getSetting('player_badges_json_url') or ''
        url = (xbmcgui.Dialog().input(_tr('رابط JSON لحزمة Badger', 'Badger pack JSON link'),
                                      defaultt='' if preset_of(current) or resource_id(current) else current) or '').strip()
        if not url:
            return False
        if pack_ready(url) and not preset_of(url):
            shutil.rmtree(_pack_dir(url), ignore_errors=True)      # read anew
        chosen_style = 'custom'
    elif kind == 'sources' and value == 'own':
        area = 'sources'
    else:
        return False
    if chosen_style and chosen_style not in STYLES:
        return False
    if url and not prepare_pack(url, select=False):
        return False
    addon = _addon()
    if area == 'sources':
        if kind == 'sources':
            _set('sources_badges_json_url', '')
            _set('badges_sources_follow', 'false')
            _set('elite_badges_enabled', 'true')
        elif chosen_style == 'off':
            _set('elite_badges_enabled', 'false')
        elif chosen_style == 'custom':
            _set('sources_badges_json_url', url)
            _set('elite_badges_enabled', 'true')
        else:
            xbmcgui.Dialog().notification('Dex Hub', _tr('صفحة المصادر تعرض حزم الصور فقط',
                                                         'The sources page shows image packs only'),
                                          time=3000, sound=False)
            return False
    if area == 'library':
        if chosen_style == 'folder':
            prepare_folder()
        _set('hero_badge_style', chosen_style)
        _set('hero_badges_json_url', url)
    if area in ('player', 'all'):
        if area == 'player':
            _pin_followers()
        if chosen_style == 'custom':
            _set('player_badges_json_url', url)
            _set('player_badge_style', 'custom')
            _ensure_extra_osd()
        elif not select_style(chosen_style, quiet=True):
            return False
    if area == 'all':
        _set('hero_badge_style', '')
        _set('hero_badges_json_url', '')
        _set('sources_badges_json_url', '')
        _set('badges_sources_follow', 'true')
        _set('elite_badges_enabled', 'false' if chosen_style == 'off' else 'true')
    try:
        xbmcgui.Window(10000).setProperty('dexhub.badges.enabled', _flag('elite_badges_enabled'))
    except Exception:
        pass
    publish()
    try:
        from .skinui import common as _skin_common
        _skin_common.ask_republish('badges')
    except Exception:
        pass
    return True


