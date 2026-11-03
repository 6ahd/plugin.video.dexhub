"""Normalize known text encodings without changing cues, timing or ASS styling."""

def normalize(payload, language_hint=''):
    raw=bytes(payload)
    try:
        if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
            return raw.decode('utf-32').encode('utf-8')
        if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
            return raw.decode('utf-16').encode('utf-8')
        if raw.startswith(b'\xef\xbb\xbf'):
            return raw.decode('utf-8-sig').encode('utf-8')
        raw.decode('utf-8')
        return raw
    except UnicodeError:
        pass
    from .track_preferences import language
    # Only an explicit language selects a legacy encoding; do not guess from
    # a filename, URL, locale or a valid UTF-8 subtitle.
    codec={'ar':'cp1256','he':'cp1255','ru':'cp1251','uk':'cp1251','el':'cp1253','tr':'cp1254'}.get(language(language_hint).split('-')[0])
    if codec:
        try:return raw.decode(codec).encode('utf-8')
        except UnicodeError:pass
    return raw # Kodi can apply its own charset preference for unknown files.


def for_kodi(payload, ext=''):
    """UTF-8 text marked as such for Kodi: without the mark Kodi reads an SRT or
    WebVTT file in its own charset (Windows-1252 by default), and Arabic shows
    as "Ø§Ù„..." (a SubDL subtitle from Silo, v5.10.490). Plain ASCII and ASS
    files are left as they are."""
    raw = bytes(payload)
    if str(ext).lower() not in ('srt', 'vtt') or raw.startswith(b'\xef\xbb\xbf'):
        return raw
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        return raw
    if all(ord(c) < 128 for c in text):
        return raw
    return b'\xef\xbb\xbf' + raw
