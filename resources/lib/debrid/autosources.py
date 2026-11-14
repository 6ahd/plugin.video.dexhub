# -*- coding: utf-8 -*-
"""Torrent sources added for the viewer once a Debrid account is linked.

Torrentio, Comet and Peerflix are added without any key: they only name the torrents
(their hashes); Dex Hub asks the viewer's own service which ones are ready
and plays them through it. Nothing has to be pasted or configured.
An add-on already in the list (any address on the same host) is left as it is.
"""
import urllib.parse

from . import accounts

SOURCES = (
    ('Torrentio', 'https://torrentio.strem.fun/sort=qualitysize|qualityfilter=threed,480p,scr,cam/manifest.json'),
    ('Comet', 'https://comet.feels.legal/manifest.json'),
    ('Peerflix', 'https://addon.peerflix.mov/manifest.json'),
)


def _host(url):
    try:
        return urllib.parse.urlparse(str(url or '')).netloc.lower()
    except Exception:
        return ''


def present():
    try:
        from ..dexhub import store
        return {_host(row.get('manifest_url') or row.get('base_url')) for row in store.list_providers() or []}
    except Exception:
        return set()


def install(announce=False):
    """Add the missing ones. The names added (a list)."""
    have = present()
    added, failed = [], []
    for name, url in SOURCES:
        if _host(url) in have:
            continue
        try:
            from ..dexhub import api
            api.add_provider_from_url(url, name=name)
            added.append(name)
        except Exception as exc:
            accounts.log('%s not added: %s' % (name, exc), True)
            failed.append(name)
    if announce:
        try:
            import xbmcgui
            from .signin import _t
            if added:
                xbmcgui.Dialog().notification('Dex Hub', _t('أضفنا للمصادر: %s', 'Added to the sources: %s') % ', '.join(added),
                                              xbmcgui.NOTIFICATION_INFO, 4000)
            elif failed:
                xbmcgui.Dialog().notification('Dex Hub', _t('ما قدرنا نضيف: %s', 'Could not add: %s') % ', '.join(failed),
                                              xbmcgui.NOTIFICATION_WARNING, 4000)
        except Exception:
            pass
    return added
