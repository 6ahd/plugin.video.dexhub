# -*- coding: utf-8 -*-
"""A media server account linked, changed or removed (v5.10.198).

Plex, Emby, Jellyfin and Silo sign in from another invocation than the pages
that list them (the skin's service, the add-on Home). Every sign-in and
sign-out writes the account's setting, so those pages hear of it as a
settings change (no polling) and read the servers again; the sign-in routes
also stamp PROP (with the Servers tab's Refresh), which tells a list read
before the change, and Plex's memo of the account's servers, to be read
again. A server shows up as soon as it is linked.
"""
import time

import xbmcgui

PROP = 'dexhub.servers.changed'
SERVICES = ('plex', 'emby', 'jellyfin', 'silo')


def changed(service=''):
    """Called by a sign-in or sign-out route."""
    try:
        home = xbmcgui.Window(10000)
        home.setProperty(PROP, '%.3f %s' % (time.time(), service or ''))
        if service in ('', 'plex'):
            # a Plex marked down a minute ago must not hide the new account
            home.setProperty('dexhub.plex.down_until', '')
    except Exception:
        pass


def stamp():
    """When an account last changed (0.0 when none did in this Kodi session)."""
    try:
        return float((xbmcgui.Window(10000).getProperty(PROP) or '0').split(' ', 1)[0] or 0)
    except Exception:
        return 0.0


def signature():
    """What the pages compare: the stamp and the accounts' window copies."""
    try:
        home = xbmcgui.Window(10000)
        parts = [home.getProperty(PROP)]
        parts.extend(home.getProperty('dexhub.%s.auth' % service) for service in SERVICES)
    except Exception:
        return ''
    return '\x1f'.join(parts)
