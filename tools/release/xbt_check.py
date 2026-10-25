#!/usr/bin/env python3
"""Check a skin's media/Textures.xbt against its loose images.

    python3 tools/release/xbt_check.py SKIN_DIR

Kodi reads an image from Textures.xbt before the file of the same name, so a
damaged entry shows on screen even when the PNG beside it is right. The
3.28.124-3.28.132 pack (built elsewhere) had 65 such images: Kodi settings'
Games, File manager, Add-ons, Information and Event log icons, the player's
skip and pause icons, drawn as stripes. Each 32-bit entry is unpacked
(LZO1X, written out below: no module needed) and its alpha compared with the
PNG's. Other formats (DXT and the two-channel ones) are not checked.
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


def damaged(skin):
    """[(name, reason)] of the pack's 32-bit images that differ from their PNG."""
    from PIL import Image, ImageChops
    media = os.path.join(skin, 'media')
    data, found = entries(os.path.join(media, 'Textures.xbt'))
    loose = {}
    for root, _dirs, files in os.walk(media):
        for name in files:
            full = os.path.join(root, name)
            loose[os.path.relpath(full, media).replace(os.sep, '/').lower()] = full
    bad = []
    for name, (w, h, _fmt, packed, unpacked, at) in found.items():
        png = loose.get(name.lower())
        if unpacked != w * h * 4 or not png or not png.lower().endswith('.png'):
            continue
        raw = data[at:at + packed]
        try:
            pixels = raw if packed == unpacked else lzo1x_decompress(raw)
            image = Image.frombytes('RGBA', (w, h), pixels)
            ref = Image.open(png).convert('RGBA')
        except Exception as exc:
            bad.append((name, 'unreadable: %s' % exc))
            continue
        if ref.size != (w, h):
            bad.append((name, 'size %dx%d, the file is %dx%d' % (w, h, ref.size[0], ref.size[1])))
            continue
        off = sum(ImageChops.difference(image.split()[3], ref.split()[3]).histogram()[40:])
        if off > w * h * 0.02:
            bad.append((name, '%d%% of its pixels differ' % (100 * off // (w * h))))
    return bad


def main(argv=None):
    skin = (argv or sys.argv[1:])[0]
    bad = damaged(skin)
    for name, why in bad:
        print('%s: %s' % (name, why))
    print('%d damaged image(s)' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
