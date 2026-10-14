# -*- coding: utf-8 -*-
"""How a badge image is drawn in skin.dexhub's badge row (v5.10.141).

No Kodi imports: the skin's generator reads the same classes.

The row is one height. A badge image is drawn at that height in a box whose
width is a whole number of quarter-heights ("k12": 12 quarters, aspect
kept). A pack image with empty transparent rows above and below its drawing
(several of the Elite and Gold images: up to half of their height) would be
drawn small; for the images of the packs Dex Hub names (TRIMS: their empty
rows, measured from the images) the box is cut to the drawing instead and
the image is cropped to it ("c10": centred, "b" or "t": the bottom or top
part kept), so every logo stands at the row's full height. Kodi crops an
image drawn with aspectratio "scale" to its control; it cannot crop sideways
at the same time, so a drawing's empty columns stay.
"""
import struct

STEP = 4                  # quarter-heights in one height
ROW = 46.0                # the height of skin.dexhub's badge rows (paddings are counted in it)
GAP_STEP, GAP_MAX = 4, 40  # the skin's gap corrections: 4 to 40 px
MIN_Q, MAX_Q = 3, 26      # the widths the skin has: 0.75 to 6.5 heights
FALLBACK = 'k12'          # an image whose size is unknown: three heights

# 'basename:WxH' of the Elite and Gold packs' images: the empty rows above and
# below the drawing, the empty columns left and right of it (alpha above 8
# counting as drawn)
TRIMS = {
    '1080p.png:764x320': (60, 17, 11, 8),
    '1080p_full_hd.png:1380x760': (4, 4, 4, 4),
    '10bit_transparent_4x.png:1360x692': (72, 72, 72, 72),
    '480p_sd.png:1436x760': (4, 4, 4, 24),
    '4k_ultra_hd.png:1420x760': (4, 4, 16, 8),
    '4kn3.png:1024x732': (19, 20, 0, 20),
    '5_1_audio.png:864x312': (31, 32, 36, 37),
    '71n.png:856x656': (28, 10, 29, 13),
    '720p.png:608x320': (60, 17, 7, 10),
    '720p_hd.png:1420x760': (4, 4, 20, 8),
    '7_1_audio.png:876x312': (31, 32, 35, 43),
    '8bit_transparent_4x.png:1256x692': (72, 72, 72, 72),
    'atmosn.png:1361x272': (12, 28, 28, 25),
    'avc_transparent_4x.png:2600x1000': (164, 164, 205, 206),
    'blu_ray_disc.png:3400x2200': (305, 300, 233, 227),
    'dolby_atmos.png:2576x1124': (40, 40, 40, 40),
    'dolby_digital.png:3704x1180': (40, 40, 40, 40),
    'dolby_digital_plus.png:4016x1196': (56, 40, 41, 40),
    'dolby_vision.png:2624x1124': (40, 40, 39, 41),
    'dts.png:801x320': (64, 64, 0, 0),
    'dts.png:812x340': (31, 22, 32, 32),
    'dts_hd.png:1264x340': (31, 24, 36, 35),
    'dts_hd_master_audio.png:3400x2200': (470, 468, 230, 224),
    'dts_x.png:1196x340': (20, 0, 33, 28),
    'dtshd.png:1116x320': (64, 64, 0, 1),
    'dtshdman.png:1442x270': (28, 28, 30, 28),
    'dtsxn.png:1436x352': (26, 30, 27, 28),
    'dvd_rip_transparent_4x.png:1244x680': (72, 72, 72, 72),
    'hdr.png:3200x1800': (272, 264, 226, 224),
    'hdr.png:504x320': (66, 66, 0, 0),
    'hdr10.png:724x228': (35, 7, 35, 26),
    'hdr10.png:814x320': (66, 66, 0, 0),
    'hdr10_plus.png:1032x240': (32, 0, 33, 34),
    'hdr10pn.png:1422x332': (28, 29, 30, 31),
    'hdtv_transparent_4x.png:1516x572': (72, 72, 72, 72),
    'hevc_transparent_4x.png:2600x1000': (232, 232, 203, 205),
    'imax-enhanced.png:563x320': (64, 64, 0, 0),
    'imax.png:1088x292': (33, 23, 35, 31),
    'imax.png:972x320': (68, 68, 0, 0),
    'imax_enhanced.png:1112x388': (35, 14, 35, 32),
    'mono-bluray.png:1196x320': (24, 24, 0, 36),
    'mono-seadex.png:864x320': (61, 60, 8, 6),
    'mono-webdl.png:1228x320': (32, 32, 0, 41),
    'mono-webrip.png:744x251': (45, 21, 0, 49),
    'remux.png:2228x576': (72, 72, 72, 72),
    'remuxn3.png:999x288': (29, 25, 34, 21),
    'sdr.png:1234x512': (25, 24, 27, 29),
    'sdr_transparent_4x.png:3200x1800': (276, 274, 205, 205),
    'truehd.png:924x256': (31, 4, 36, 24),
    'truehdn.png:1378x259': (29, 12, 23, 29),
    'visionn3.png:1373x292': (14, 26, 28, 30),
    'webdl_transparent_4x.png:1496x640': (72, 72, 72, 72),
    'webrip_transparent_4x.png:3400x1400': (128, 128, 204, 204),
    'white_51.png:112x80': (17, 15, 20, 24),
    'white_61.png:112x80': (16, 15, 19, 23),
    'white_dd.png:322x80': (14, 2, 16, 17),
    'white_ddplus.png:363x80': (14, 2, 16, 18),
}


def image_size_of(data):
    """(width, height) from the start of a PNG, GIF, WebP or JPEG file, or None."""
    try:
        if data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
            return struct.unpack('>II', data[16:24])
        if data[:6] in (b'GIF87a', b'GIF89a'):
            return struct.unpack('<HH', data[6:10])
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            kind = data[12:16]
            if kind == b'VP8X':
                return (int.from_bytes(data[24:27], 'little') + 1, int.from_bytes(data[27:30], 'little') + 1)
            if kind == b'VP8L':
                bits = int.from_bytes(data[21:25], 'little')
                return ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
            if kind == b'VP8 ':
                w, h = struct.unpack('<HH', data[26:30])
                return (w & 0x3fff, h & 0x3fff)
            return None
        if data[:2] == b'\xff\xd8':
            pos = 2
            while pos + 4 <= len(data):
                if data[pos] != 0xff:
                    return None
                marker = data[pos + 1]
                if marker in (0xd8, 0x01, 0xff) or 0xd0 <= marker <= 0xd7:
                    pos += 1 if marker == 0xff else 2
                    continue
                length = struct.unpack('>H', data[pos + 2:pos + 4])[0]
                if 0xc0 <= marker <= 0xcf and marker not in (0xc4, 0xc8, 0xcc):
                    h, w = struct.unpack('>HH', data[pos + 5:pos + 9])
                    return (w, h)
                pos += 2 + length
    except (struct.error, ValueError, IndexError):
        return None
    return None


def image_size(path):
    """(width, height) of an image file on the device, or None."""
    try:
        with open(path, 'rb') as handle:
            return image_size_of(handle.read(262144))
    except OSError:
        return None


def trim_of(basename, size):
    try:
        return TRIMS.get('%s:%dx%d' % (str(basename).lower(), size[0], size[1]))
    except (TypeError, IndexError):
        return None


def _shape(w, h, trim=None):
    """(class, drawn height as a share of the row) for a w x h image."""
    full = STEP * w / float(h)
    keep = max(MIN_Q, min(MAX_Q, int(full) + (0 if full == int(full) else 1)))
    share = min(1.0, keep / full)       # wider than the row has: drawn lower
    if trim:
        share *= (h - trim[0] - trim[1]) / float(h)
    best = ('k%d' % keep, share)
    if trim:
        top, bottom = trim[0], trim[1]
        drawing = h - top - bottom
        for align, rows in (('c', h - 2 * min(top, bottom)), ('b', h - top), ('t', h - bottom)):
            if rows <= 0 or rows >= h:
                continue
            q = int(STEP * w / float(rows) + 1e-9)
            if q < full or q < MIN_Q or q > MAX_Q:
                continue        # its sides would be cut, or wider than the row has
            share = drawing * q / (STEP * w / float(h)) / float(h)
            if share > best[1] + 0.005:
                best = ('%s%d' % (align, q), share)
    return best


def width_class(size, trim=None):
    """The skin's control for an image of this size: 'k<q>' (aspect kept),
    'c<q>', 'b<q>' or 't<q>' (cropped to its drawing)."""
    try:
        w, h = int(size[0]), int(size[1])
    except (TypeError, ValueError, IndexError):
        return FALLBACK
    if w <= 0 or h <= 0:
        return FALLBACK
    shape = _shape(w, h, trim)[0]
    return shape if shape[0] == 'k' or shape in CROP_CLASSES else _shape(w, h)[0]


def paddings(shape, size, trim=None):
    """(left, right): the empty width beside the drawing in a box of this
    class, in skin pixels at the row's height (its transparent columns and,
    with the aspect kept, the box's slack)."""
    try:
        w, h = float(size[0]), float(size[1])
        box = int(shape[1:]) * ROW / STEP
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0
    if w <= 0 or h <= 0:
        return 0.0, 0.0
    left, right = (trim[2], trim[3]) if trim and len(trim) > 3 else (0, 0)
    if shape[0] == 'k':
        scale = min(ROW / h, box / w)
        slack = (box - w * scale) / 2.0
        return slack + left * scale, slack + right * scale
    scale = box / w             # cut to the drawing: the image fills the box's width
    return left * scale, right * scale


def gap_class(width):
    """The skin's correction for this much empty width: 'x4' to 'x40', or ''."""
    step = int(round(width / GAP_STEP)) * GAP_STEP
    return 'x%d' % min(GAP_MAX, step) if step >= GAP_STEP else ''


GAP_CLASSES = tuple('x%d' % v for v in range(GAP_STEP, GAP_MAX + 1, GAP_STEP))


def _crop_classes():
    out = set()
    for key, trim in TRIMS.items():
        w, h = key.rsplit(':', 1)[1].split('x')
        shape = _shape(int(w), int(h), trim)[0]
        if shape[0] != 'k':
            out.add(shape)
    return frozenset(out)


CROP_CLASSES = _crop_classes()
KEEP_CLASSES = tuple('k%d' % q for q in range(MIN_Q, MAX_Q + 1))
