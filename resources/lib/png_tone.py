# -*- coding: utf-8 -*-
"""How dark and how colourful a PNG logo is (v5.10.508), without PIL (the
Apple TV has none): enough of PNG for TMDb's logos (8-bit grey, grey+alpha,
RGB, RGBA and palette, any filter, no interlace).

tone(data) -> (lightness 0-1, saturation 0-1) of its opaque pixels, or None.
A dark, colourless logo (HBO, black on clear) is unseen on Dex Hub's dark
pages: dexworld.network_logo asks TMDb for its inverted copy then.
"""
import struct
import zlib

_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def tone(data, step=3):
    if not data or data[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    pos, idat, palette, trns = 8, [], None, None
    width = height = depth = ctype = interlace = 0
    while pos + 8 <= len(data):
        size, kind = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + size]
        pos += 12 + size
        if kind == b'IHDR':
            width, height, depth, ctype, _c, _f, interlace = struct.unpack('>IIBBBBB', body)
        elif kind == b'PLTE':
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b'tRNS':
            trns = body
        elif kind == b'IDAT':
            idat.append(body)
        elif kind == b'IEND':
            break
    if depth != 8 or interlace or ctype not in _CHANNELS or not width or width * height > 2000000:
        return None
    raw = zlib.decompress(b''.join(idat))
    bpp = _CHANNELS[ctype]
    stride = width * bpp
    prev = bytearray(stride)
    light = sat = weight = 0.0
    at = 0
    for y in range(height):
        kind = raw[at]
        line = bytearray(raw[at + 1:at + 1 + stride])
        at += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif kind == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 255
        prev = line
        if y % step:
            continue
        for x in range(0, width, step):
            p = line[x * bpp:(x + 1) * bpp]
            if ctype == 6:
                r, g, b_, alpha = p
            elif ctype == 2:
                (r, g, b_), alpha = p, 255
            elif ctype == 4:
                r = g = b_ = p[0]
                alpha = p[1]
            elif ctype == 0:
                r = g = b_ = p[0]
                alpha = 255
            else:
                index = p[0]
                if not palette or index >= len(palette):
                    continue
                r, g, b_ = palette[index]
                alpha = trns[index] if trns and index < len(trns) else 255
            if alpha < 128:
                continue
            hi, lo = max(r, g, b_), min(r, g, b_)
            light += (hi + lo) / 510.0
            sat += (hi - lo) / 255.0
            weight += 1
    if not weight:
        return None
    return light / weight, sat / weight
