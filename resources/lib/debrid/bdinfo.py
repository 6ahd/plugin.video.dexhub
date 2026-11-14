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


def path(encoded_root, index, names, fetch, workers=8):
    """bluray://<root>/menu for a disc whose menus Kodi can play, else
    bluray://<root>/BDMV/PLAYLIST/<longest>.mpls ('…/menu' when no playlist could be read:
    Kodi then picks a title itself). encoded_root is the disc's folder as Kodi's URLEncode writes it."""
    main_only = _kodi_setting() == MAIN_TITLE
    if not main_only and (java_menus_play() or not has_java(index)):
        return 'bluray://%s/menu' % encoded_root, 'menu'
    best = longest(names, fetch, workers)
    if not best:
        return 'bluray://%s/menu' % encoded_root, 'menu (no playlist read)'
    return 'bluray://%s/BDMV/PLAYLIST/%s' % (encoded_root, best), 'main title %s (%s)' % (
        best, "Kodi's setting" if main_only else 'Java menus')
