#!/usr/bin/env python3
"""Check a skin's media/Textures.xbt against its loose images.

    python3 tools/release/xbt_check.py SKIN_DIR

Kodi reads an image from Textures.xbt before the file of the same name, so a
damaged entry shows on screen even when the PNG beside it is right. The
3.28.124-3.28.132 pack (built elsewhere) had 65 such images: Kodi settings'
Games, File manager, Add-ons, Information and Event log icons, the player's
skip and pause icons, drawn as stripes.

Every entry is unpacked (LZO1X: the system's liblzo2 when there is one, else
written out below) and drawn back to RGBA the way Kodi draws it: BGRA, and
the one and two channel forms TexturePacker makes of plain images (alpha
only, grey, grey with alpha, intensity; XBT 3's KD_TEX_FMT_SDR_R8 / RG8 with
their swizzle, xbmc/guilib/TextureFormats.h), then compared with its file:
every channel where the image shows (alpha), alpha everywhere. DXT and the
GPU formats TexturePacker does not write are reported, not checked.
"""
import os
import struct
import sys


def lzo1x_decompress(src):
    """LZO1X, as lzo1x_decompress_safe reads it."""
    dst = bytearray()
    ip, state = 0, 0

    def copy_match(dist, length):
        start = len(dst) - dist
        for i in range(length):
            dst.append(dst[start + i])

    def long_length(base):
        nonlocal ip
        length = 0
        while src[ip] == 0:
            length += 255
            ip += 1
        length += base + src[ip]
        ip += 1
        return length

    if src[0] > 17:
        t = src[0] - 17
        ip = 1
        dst += src[ip:ip + t]
        ip += t
        state = 4 if t >= 4 else t
    while True:
        t = src[ip]
        ip += 1
        if t >= 64:
            dist = ((t >> 2) & 7) + (src[ip] << 3) + 1
            ip += 1
            copy_match(dist, (t >> 5) + 1)
            state = t & 3
        elif t >= 32:
            length = (t & 31) or long_length(31)
            dist = (src[ip] >> 2) + (src[ip + 1] << 6) + 1
            state = src[ip] & 3
            ip += 2
            copy_match(dist, length + 2)
        elif t >= 16:
            length = (t & 7) or long_length(7)
            dist = ((t & 8) << 11) + (src[ip] >> 2) + (src[ip + 1] << 6)
            state = src[ip] & 3
            ip += 2
            if dist == 0:
                break
            copy_match(dist + 16384, length + 2)
        elif state == 0:
            length = (t or long_length(15)) + 3
            dst += src[ip:ip + length]
            ip += length
            state = 4
            continue
        elif state < 4:
            dist = (t >> 2) + (src[ip] << 2) + 1
            state = t & 3
            ip += 1
            copy_match(dist, 2)
        else:
            dist = (t >> 2) + (src[ip] << 2) + 2049
            state = t & 3
            ip += 1
            copy_match(dist, 3)
        if state:
            dst += src[ip:ip + state]
            ip += state
    return bytes(dst)


def entries(path):
    data = open(path, 'rb').read()
    if data[:4] != b'XBTF':
        raise ValueError('not an XBT file')
    count = struct.unpack('<I', data[5:9])[0]
    off, out = 9, {}
    for _ in range(count):
        name = data[off:off + 256].split(b'\0')[0].decode('utf-8', 'replace')
        off += 256
        _loop, frames = struct.unpack('<II', data[off:off + 8])
        off += 8
        for index in range(frames):
            w, h, fmt, packed, unpacked, _dur, at = struct.unpack('<IIIQQIQ', data[off:off + 40])
            off += 40
            if index == 0:
                out[name] = (w, h, fmt, packed, unpacked, at)
    return data, out


_LIB = []


def _native():
    """liblzo2's lzo1x_decompress_safe, or None."""
    if not _LIB:
        lib = None
        try:
            import ctypes
            import ctypes.util
            name = ctypes.util.find_library('lzo2') or 'liblzo2.so.2'
            lib = ctypes.CDLL(name)
            lib.lzo1x_decompress_safe.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p,
                                                  ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p]
        except Exception:
            lib = None
        _LIB.append(lib)
    return _LIB[0]


def unpack(raw, size):
    lib = _native()
    if lib is None:
        return bytes(lzo1x_decompress(raw))
    import ctypes
    out = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(size)
    if lib.lzo1x_decompress_safe(raw, len(raw), out, ctypes.byref(got), None) != 0 or got.value != size:
        raise ValueError('LZO data does not unpack')
    return out.raw


# XBT 3 (xbmc/guilib/TextureFormats.h)
FMT_MASK, SWIZ_MASK = 0xFFFF, 0xF00000
SDR_R8, SDR_RG8, SDR_RGBA8, SDR_BGRA8 = 0x1000, 0x1100, 0x1600, 0x1700
SWIZ_RGBA, SWIZ_RGB1, SWIZ_RRR1, SWIZ_111R, SWIZ_RRRG, SWIZ_RRRR = 0, 0x100000, 0x200000, 0x300000, 0x400000, 0x500000
LEGACY_A8R8G8B8 = 0x10


def to_rgba(w, h, fmt, pixels):
    """The entry as Kodi draws it, an RGBA image; None for a format not checked."""
    from PIL import Image
    kind, swiz = fmt & FMT_MASK, fmt & SWIZ_MASK
    if kind in (SDR_BGRA8, LEGACY_A8R8G8B8):
        b, g, r, a = Image.frombytes('RGBA', (w, h), pixels).split()
        if swiz == SWIZ_RGB1 or fmt & 0x10000:
            a = Image.new('L', (w, h), 255)
        return Image.merge('RGBA', (r, g, b, a))
    if kind == SDR_RGBA8:
        return Image.frombytes('RGBA', (w, h), pixels)
    if kind == SDR_R8:
        c = Image.frombytes('L', (w, h), pixels)
        one = Image.new('L', (w, h), 255)
        if swiz == SWIZ_111R:
            return Image.merge('RGBA', (one, one, one, c))
        if swiz == SWIZ_RRR1:
            return Image.merge('RGBA', (c, c, c, one))
        if swiz == SWIZ_RRRR:
            return Image.merge('RGBA', (c, c, c, c))
        return None
    if kind == SDR_RG8 and swiz == SWIZ_RRRG:
        r, g = Image.frombytes('LA', (w, h), pixels).split()
        return Image.merge('RGBA', (r, r, r, g))
    return None


def _size(w, h, fmt):
    kind = fmt & FMT_MASK
    return w * h * {SDR_R8: 1, SDR_RG8: 2}.get(kind, 4)


def compare(image, ref):
    """The share of pixels that differ: alpha everywhere, colour where shown."""
    from PIL import ImageChops
    w, h = ref.size
    ra, ia = ref.split()[3], image.split()[3]
    off = sum(ImageChops.difference(ia, ra).histogram()[3:])
    shown = ra.point(lambda v: 255 if v > 8 else 0)
    for i in range(3):
        diff = ImageChops.difference(image.split()[i], ref.split()[i])
        diff = ImageChops.multiply(diff.point(lambda v: 255 if v > 3 else 0), shown)
        off += sum(diff.histogram()[1:])
    return off / float(w * h)


def damaged(skin, report=None):
    """[(name, reason)] of the pack's images that differ from their files.
    report: a dict that gets the counts (checked, not checked by format)."""
    from PIL import Image
    media = os.path.join(skin, 'media')
    data, found = entries(os.path.join(media, 'Textures.xbt'))
    loose = {}
    for root, _dirs, files in os.walk(media):
        for name in files:
            full = os.path.join(root, name)
            loose[os.path.relpath(full, media).replace(os.sep, '/').lower()] = full
    bad, checked, skipped = [], 0, {}
    for name, (w, h, fmt, packed, unpacked, at) in found.items():
        path = loose.get(name.lower())
        if not path:
            continue
        raw = data[at:at + packed]
        try:
            pixels = raw if packed == unpacked else unpack(raw, unpacked)
            if len(pixels) != _size(w, h, fmt):
                skipped[hex(fmt)] = skipped.get(hex(fmt), 0) + 1
                continue
            image = to_rgba(w, h, fmt, pixels)
            if image is None:
                skipped[hex(fmt)] = skipped.get(hex(fmt), 0) + 1
                continue
            ref = Image.open(path).convert('RGBA')
        except Exception as exc:
            bad.append((name, 'unreadable: %s' % exc))
            continue
        if ref.size != (w, h):
            bad.append((name, 'size %dx%d, the file is %dx%d' % (w, h, ref.size[0], ref.size[1])))
            continue
        checked += 1
        share = compare(image, ref)
        if share > 0.02:
            bad.append((name, '%d%% of its pixels differ (%s)' % (int(100 * share), hex(fmt))))
    if report is not None:
        report.update(checked=checked, skipped=skipped, entries=len(found))
    return bad


def main(argv=None):
    skin = (argv or sys.argv[1:])[0]
    report = {}
    bad = damaged(skin, report)
    for name, why in bad:
        print('%s: %s' % (name, why))
    print('%d entries, %d checked, not checked: %s' % (report['entries'], report['checked'], report['skipped'] or 'none'))
    print('%d damaged image(s)' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
