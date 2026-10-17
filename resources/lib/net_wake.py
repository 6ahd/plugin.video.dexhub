# -*- coding: utf-8 -*-
"""The network after the box wakes (v5.10.314).

The log of a CoreELEC box: Kodi woke at 12:48:25 and in the same second Dex
Hub asked every Plex and Emby server; the names did not resolve yet
("Temporary failure in name resolution", "Network is unreachable"), every
server was reported unreachable and the Servers tab came up incomplete.
For a minute and a half after a wake, a request that failed that way waits
for the name to resolve (up to ten seconds) and is asked once more.

skinui/worker.py marks the wake (Home property dexhub.woke).
"""
import socket
import time

WOKE = 'dexhub.woke'
_WINDOW = 90.0
_DOWN = ('Temporary failure in name resolution', 'Name or service not known', 'nodename nor servname',
         'Errno -3', 'Errno -2', 'Errno 101', 'Network is unreachable', 'No address associated',
         'getaddrinfo failed', 'Errno 65', 'No route to host')


def mark_woke():
    try:
        import xbmcgui
        xbmcgui.Window(10000).setProperty(WOKE, '%.0f' % time.time())
    except Exception:
        pass


def woke_recently():
    try:
        import xbmcgui
        stamp = float(xbmcgui.Window(10000).getProperty(WOKE) or 0)
    except Exception:
        return False
    return 0 < time.time() - stamp < _WINDOW


def network_down(exc):
    text = str(exc or '')
    return any(mark in text for mark in _DOWN)


def wait_after_wake(exc, host, budget=10.0):
    """True when the box woke lately, exc says the network was not up yet,
    and the host resolves within budget seconds (ask again then)."""
    if not host or not network_down(exc) or not woke_recently():
        return False
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        try:
            socket.getaddrinfo(host, 443)
            return True
        except Exception:
            time.sleep(0.5)
    return False
