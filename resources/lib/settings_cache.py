# -*- coding: utf-8 -*-
"""Short-lived cache in front of xbmcaddon.Addon().getSetting().

Every getSetting() is a Python->C++ round trip. A single menu render reads
the same handful of settings dozens of times, and with
reuselanguageinvoker=true those modules stay resident, so the reads repeat
for the life of the process.

A 2 second TTL is the compromise: within one invocation every repeat read is
free, while the long-running service.py still picks up a settings change
almost immediately. setSetting() always invalidates.
"""
import time

import xbmcaddon

_TTL = 2.0


class CachedAddon(object):
    """Attribute-transparent proxy. Anything not cached is forwarded."""

    def __init__(self, addon):
        self._addon = addon
        self._cache = {}
        self._stamp = 0.0

    def _fresh(self):
        now = time.time()
        if now - self._stamp > _TTL:
            self._cache.clear()
            try:
                self._addon = xbmcaddon.Addon()
            except Exception:
                pass
            self._stamp = now

    def getSetting(self, sid):
        self._fresh()
        if sid in self._cache:
            return self._cache[sid]
        value = self._addon.getSetting(sid)
        self._cache[sid] = value
        return value

    def getSettingBool(self, sid):
        self._fresh()
        key = ('bool', sid)
        if key in self._cache:
            return self._cache[key]
        value = self._addon.getSettingBool(sid)
        self._cache[key] = value
        return value

    # v4.7.6: Kodi persists an addon's settings by writing the WHOLE tree
    # from the instance the write is made on. This proxy is created once and,
    # under reuselanguageinvoker, lives for the whole process — so writing a
    # single key through it re-saves a snapshot that predates anything the
    # user changed in the settings dialog meanwhile, silently REVERTING it.
    # That is the "I paste a new badges URL and it goes back to the previous
    # one" report: nothing writes that setting, it was collateral damage of
    # an unrelated write. Writes now always go through a fresh instance.
    def _write_addon(self):
        try:
            return xbmcaddon.Addon()
        except Exception:
            return self._addon

    def setSetting(self, sid, value):
        self._cache.clear()
        self._stamp = 0.0
        return self._write_addon().setSetting(sid, value)

    def setSettingBool(self, sid, value):
        self._cache.clear()
        self._stamp = 0.0
        return self._write_addon().setSettingBool(sid, value)

    def invalidate(self):
        # v5.10.140: also take a fresh Addon on the next read. An instance
        # keeps the settings it first read, so after a change made in the
        # settings dialog the service published the old values for up to
        # two seconds, and nothing published them again afterwards.
        self._cache.clear()
        self._stamp = 0.0

    def __getattr__(self, name):
        return getattr(self._addon, name)


_INSTANCE = None


def cached_addon():
    """One shared proxy per process."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = CachedAddon(xbmcaddon.Addon())
    return _INSTANCE


def invalidate():
    if _INSTANCE is not None:
        _INSTANCE.invalidate()
