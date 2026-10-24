"""Stremio WatchedField: anchor video, anchor length, base64(zlib(LSB bitmap))."""
import base64
import zlib


def unpack(value, videos):
    result = [False] * len(videos)
    if not value:
        return result
    anchor, count, encoded = value.rsplit(':', 2)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(encoded, validate=True), 1000000)
    if not decoder.eof or decoder.unused_data:
        raise ValueError('Invalid Stremio watched payload')
    if anchor not in videos and not any(raw):
        return result
    if anchor not in videos:
        # Replacing an unknown anchor would erase history. Retry with the
        # correct metadata rather than synthesizing an episode index.
        raise ValueError('Stremio watched anchor is absent from episode metadata')
    offset = int(count) - videos.index(anchor) - 1
    for i in range(len(videos)):
        old = i + offset
        result[i] = old >= 0 and old // 8 < len(raw) and bool(raw[old // 8] & (1 << (old % 8)))
    return result


def pack(watched, videos):
    raw = bytearray((len(videos) + 7) // 8)
    for i, value in enumerate(watched):
        if value:
            raw[i // 8] |= 1 << (i % 8)
    last = max((i for i,v in enumerate(watched) if v), default=0)
    return '%s:%d:%s' % (videos[last] if videos else 'undefined', last + 1,
                         base64.b64encode(zlib.compress(bytes(raw), 6)).decode('ascii'))


def mark(value, videos, video):
    if video not in videos:
        raise ValueError('Completed episode is absent from Stremio metadata')
    bits = unpack(value, videos)
    i = videos.index(video)
    changed = not bits[i]
    bits[i] = True
    return pack(bits, videos), changed
