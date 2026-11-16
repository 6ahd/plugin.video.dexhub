# -*- coding: utf-8 -*-
"""Menu or main title: how Kodi should open a whole Blu-ray disc (v5.10.572).

Kodi plays a disc's menus only when they are HDMV. A disc whose menus are Java
(BD-J, most UHD discs and many others) either starts on its main title by
itself (when its First Play is Java) or, when only some titles are Java, starts
in its menus and stops at the first Java one ("BD_EVENT_ERROR: Fatal error":
a 42 GB disc from TorBox, 14 Nov 2026). So a disc with any Java title is opened
on its longest playlist, bluray://<disc>/BDMV/PLAYLIST/<n>.mpls, which is the
title Kodi itself picks as the main movie; a disc that is HDMV only opens in
its menus, bluray://<disc>/menu.

index.bdmv: at the offset in bytes 8-11, a length, then the First Playback and
Top Menu objects and the titles, 12 bytes each, the object type (1 HDMV, 2 BD-J)
in each one's first two bits. A playlist (.mpls): at the offset in bytes 8-11,
a length, two reserved bytes, the play items' count, the sub paths' count, then
the play items, each with its IN and OUT times (45 kHz) at bytes 12 and 16
after its length.

CoreELEC and LibreELEC play Java menus (their Kodi has BD-J), so there every
disc opens in its menus. And Kodi's own setting is followed everywhere:
Settings > Player > Discs > Blu-ray playback set to "Play main movie" opens
every disc on its main title (a bluray://…/menu path would skip that setting).
"""
import re
import struct
import threading

_PLAYLIST = re.compile(r'^\d{5}\.mpls$', re.I)
MAX_PLAYLISTS = 200


def has_java(index):
    """True when the disc has a Java object (or index.bdmv cannot be read: the main title is safe)."""
    try:
        if not index or index[:4] != b'INDX':
            return True
        start = struct.unpack_from('>I', index, 8)[0]
        kinds = [index[start + 4] >> 6, index[start + 16] >> 6]
        titles = struct.unpack_from('>H', index, start + 28)[0]
        kinds += [index[start + 30 + 12 * i] >> 6 for i in range(titles)]
        return 2 in kinds
    except Exception:
        return True


def seconds(mpls):
    """A playlist's length in seconds (0 when it cannot be read)."""
    try:
        if not mpls or mpls[:4] != b'MPLS':
            return 0.0
        start = struct.unpack_from('>I', mpls, 8)[0]
        items = struct.unpack_from('>H', mpls, start + 6)[0]
        at, ticks = start + 10, 0
        for _ in range(items):
            length = struct.unpack_from('>H', mpls, at)[0]
            first, last = struct.unpack_from('>II', mpls, at + 2 + 12)
            ticks += max(0, last - first)
            at += 2 + length
        return ticks / 45000.0
    except Exception:
        return 0.0


def longest(names, fetch, workers=8):
    """The name of the longest playlist among `names` (files of BDMV/PLAYLIST), each read with
    fetch(name) -> bytes; '' when none could be read."""
    names = sorted(n for n in names if _PLAYLIST.match(n))[:MAX_PLAYLISTS]
    lengths, lock, queue = {}, threading.Lock(), list(names)

    def work():
        while True:
            with lock:
                if not queue:
                    return
                name = queue.pop(0)
            try:
                value = seconds(fetch(name))
            except Exception:
                value = 0.0
            with lock:
                lengths[name] = value

    threads = [threading.Thread(target=work, daemon=True) for _ in range(min(workers, len(names)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    best = max(lengths.items(), key=lambda kv: (kv[1], -int(kv[0][:5])), default=('', 0.0))
    return best[0] if best[1] > 0 else ''


MAIN_TITLE = 2               # Kodi's BD_PLAYBACK_MAIN_TITLE (disc.playback)


def _kodi_setting():
    try:
        from ..disc_menu import _playback_setting
        return _playback_setting()
    except Exception:
        return None


def java_menus_play():
    """True where Kodi runs a disc's Java menus: CoreELEC and LibreELEC."""
    try:
        from ..disc_play import on_coreelec
        return bool(on_coreelec())
    except Exception:
        return False


# ISO 639-1 (Dex Hub's subtitle languages) to the 639-2 codes a disc writes (both forms)
_THREE = {'ar': ('ara',), 'en': ('eng',), 'fr': ('fra', 'fre'), 'de': ('deu', 'ger'), 'es': ('spa',),
          'it': ('ita',), 'tr': ('tur',), 'fa': ('fas', 'per'), 'ur': ('urd',), 'hi': ('hin',),
          'ru': ('rus',), 'ja': ('jpn',), 'ko': ('kor',), 'zh': ('zho', 'chi'), 'pt': ('por',),
          'nl': ('nld', 'dut'), 'sv': ('swe',), 'da': ('dan',), 'no': ('nor', 'nob', 'nno'),
          'fi': ('fin',), 'pl': ('pol',), 'he': ('heb',), 'id': ('ind',), 'ms': ('msa', 'may'),
          'el': ('ell', 'gre'), 'cs': ('ces', 'cze'), 'hu': ('hun',), 'ro': ('ron', 'rum'),
          'th': ('tha',), 'vi': ('vie',), 'uk': ('ukr',), 'bg': ('bul',), 'hr': ('hrv',)}


def subtitle_languages(mpls):
    """The languages (639-2, lower case) of the subtitles a playlist's first play item offers
    (its STN table: graphic and text subtitles), or None when the playlist cannot be read.

    A play item, after its length: clip name (5), codec (4), flags (2, bit 4 of the second
    is multi-angle), STC id (1), IN and OUT times (4 each), UO mask (8), random access (1),
    still mode (1), still time (2); for multi-angle, the angle count and flags (1 each) and 10
    bytes for every angle after the first. Then STN: length (2), reserved (2), the counts of
    video, audio, subtitle, IG, secondary audio, secondary video and PiP subtitle streams
    (1 each), reserved (5), then each stream as an entry (length, bytes) and its attributes
    (length, bytes: coding type first; a subtitle's language follows it, after a character
    set byte for a text subtitle; an audio's after a format byte)."""
    try:
        if not mpls or mpls[:4] != b'MPLS':
            return None
        start = struct.unpack_from('>I', mpls, 8)[0]
        if not struct.unpack_from('>H', mpls, start + 6)[0]:
            return None
        at = start + 10 + 2                     # the first play item, after its length
        stn = at + 32
        if mpls[at + 10] & 0x10:                # multi-angle
            angles = mpls[stn]
            stn += 2 + 10 * max(0, angles - 1)
        counts = list(mpls[stn + 4:stn + 11])
        pos = stn + 16
        langs = set()
        for kind, count in enumerate(counts[:3]):          # video, audio, subtitles
            for _ in range(count):
                pos += 1 + mpls[pos]                         # the stream entry
                size = mpls[pos]
                attrs = mpls[pos + 1:pos + 1 + size]
                pos += 1 + size
                if kind == 2 and attrs:
                    code = attrs[0]
                    raw = attrs[2:5] if code == 0x92 else attrs[1:4]
                    langs.add(raw.decode('ascii', 'replace').lower())
        return langs
    except Exception:
        return None


def _wanted_language():
    """The viewer's first subtitle language (Dex Hub's subtitle settings), as 639-1."""
    try:
        from ..subtitle_broker import _preferred_languages
        first = str((_preferred_languages() or ['ar'])[0] or 'ar')
    except Exception:
        first = 'ar'
    return first.split('-')[0].lower()


def film_for_subtitles():
    """Dex Hub's option: a disc without the viewer's subtitle language opens on its film."""
    try:
        from . import accounts
        return bool(accounts.option('disc_film_for_subtitles'))
    except Exception:
        return False


def lacks_wanted_subtitle(mpls):
    """True when the playlist has no subtitle in the viewer's first language (so a file
    will be wanted, and a file never shows while the disc runs its menus)."""
    langs = subtitle_languages(mpls)
    if langs is None:
        return False
    wanted = _wanted_language()
    return not (set(_THREE.get(wanted, (wanted,))) & langs)


def path(encoded_root, index, names, fetch, workers=8):
    """bluray://<root>/menu for a disc whose menus Kodi can play, else
    bluray://<root>/BDMV/PLAYLIST/<longest>.mpls ('…/menu' when no playlist could be read:
    Kodi then picks a title itself). encoded_root is the disc's folder as Kodi's URLEncode writes it.

    v5.10.576: a disc whose main film has no subtitle in the viewer's first language opens on
    the film too (the option "Disc without your subtitles opens on the film", off by
    default): in its menus a subtitle file never shows, and the disc puts its own subtitle back
    when one is picked (CoreELEC box, 16 Nov 2026), so the film had to be played again."""
    main_only = _kodi_setting() == MAIN_TITLE
    menus = not main_only and (java_menus_play() or not has_java(index))
    if menus and not film_for_subtitles():
        return 'bluray://%s/menu' % encoded_root, 'menu'
    best = longest(names, fetch, workers)
    if not best:
        return 'bluray://%s/menu' % encoded_root, 'menu (no playlist read)'
    if menus:
        try:
            lacking = lacks_wanted_subtitle(fetch(best))
        except Exception:
            lacking = False
        if not lacking:
            return 'bluray://%s/menu' % encoded_root, 'menu (its subtitles have %s)' % _wanted_language()
        return 'bluray://%s/BDMV/PLAYLIST/%s' % (encoded_root, best), 'main title %s (no %s subtitle on the disc)' % (
            best, _wanted_language())
    return 'bluray://%s/BDMV/PLAYLIST/%s' % (encoded_root, best), 'main title %s (%s)' % (
        best, "Kodi's setting" if main_only else 'Java menus')
