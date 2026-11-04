# -*- coding: utf-8 -*-
"""IPv4 first where IPv6 goes nowhere (v5.10.499).

A network can hand out IPv6 addresses that do not reach the internet. Python
then tries every IPv6 address of a name (with retries) before the IPv4 ones:
a YouTube video waited 78 s to start, and the player's Sources button 30 s on
a GitHub download. Kodi's own player is not affected (curl races both).

Once every half hour (shared by every interpreter through a Home property) a
1 s connection over IPv6 is tried. When it fails, every name looked up in
this interpreter gives its IPv4 addresses first; the IPv6 ones stay behind
them, so nothing that only has IPv6 is lost. Where IPv6 works nothing changes.
"""
import socket
import time

PROP = 'dexhub.net.ipv6'
TTL = 1800
_installed = False


def _ipv6_works():
    try:
        import xbmcgui
        held = xbmcgui.Window(10000).getProperty(PROP)
    except Exception:
        held = ''
    state, _sep, at = held.partition('|')
    try:
        if state and time.time() - float(at) < TTL:
            return state == 'ok'
    except ValueError:
        pass
    works = True
    try:
        # YouTube's own name: a network here answered www.google.com over IPv6
        # but gave www.youtube.com IPv6 addresses that go nowhere
        found = socket.getaddrinfo('www.youtube.com', 443, socket.AF_INET6, socket.SOCK_STREAM)
        probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            probe.connect(found[0][4])
        finally:
            probe.close()
    except Exception:
        works = False
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(PROP, '%s|%d' % ('ok' if works else 'no', time.time()))
        if not works and state != 'no':
            import xbmc
            xbmc.log('[DexHub] IPv6 does not reach the internet here: IPv4 addresses are tried first', xbmc.LOGINFO)
    except Exception:
        pass
    return works


def install():
    """IPv4 first for every lookup of this interpreter, when IPv6 does not work."""
    global _installed
    if _installed:
        return
    _installed = True
    if _ipv6_works():
        return
    original = socket.getaddrinfo

    def getaddrinfo(*args, **kwargs):
        found = original(*args, **kwargs)
        return sorted(found, key=lambda entry: entry[0] != socket.AF_INET)
    socket.getaddrinfo = getaddrinfo
