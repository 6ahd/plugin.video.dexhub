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
    try:
        _drop_shared()          # v5.10.301
    except NameError:
        pass


# --------------------------------------------------------------------------- #
#  v5.10.301: one settings snapshot per add-on for the whole service          #
#                                                                             #
#  Every xbmcaddon.Addon() reads the add-on's settings again; on Apple TV     #
#  that is a decompress of settings.xml from NSUserDefaults plus a parse of   #
#  the full settings definition. The service's watchers built one every       #
#  second or two (about 1,500 in seven minutes of a v21.7 log, 41 a second    #
#  while browsing), also during playback. install_shared() makes            #
#  xbmcaddon.Addon in the service return a light proxy over one real         #
#  instance per add-on id, rebuilt when the settings change (the service's   #
#  onSettingsChanged calls invalidate()) and at the latest every minute.     #
#  Writes always go through a fresh real instance (v4.7.6: a write from an    #
#  old snapshot re-saves the whole old tree) and drop the snapshot.          #
# --------------------------------------------------------------------------- #

import threading as _threading

_REAL_ADDON = None
_SHARED = {}
_SHARED_LOCK = _threading.Lock()
_SHARED_TTL = 60.0
_GENERATION = [0]
_OWN_ID = ['']


def _real_for(addon_id):
    now = time.time()
    with _SHARED_LOCK:
        entry = _SHARED.get(addon_id)
        if entry and entry[1] == _GENERATION[0] and now - entry[2] < _SHARED_TTL:
            return entry[0]
    real = _REAL_ADDON(addon_id) if addon_id else _REAL_ADDON()
    with _SHARED_LOCK:
        _SHARED[addon_id] = (real, _GENERATION[0], now)
    return real


def _drop_shared():
    with _SHARED_LOCK:
        _GENERATION[0] += 1
        _SHARED.clear()


class SharedAddon(object):
    """What xbmcaddon.Addon returns in the service after install_shared()."""

    def __init__(self, addon_id=None):
        self._id = addon_id or _OWN_ID[0] or ''

    def _real(self):
        return _real_for(self._id)

    def _writer(self):
        return _REAL_ADDON(self._id) if self._id else _REAL_ADDON()

    def _write(self, method, sid, value):
        result = getattr(self._writer(), method)(sid, value)
        _drop_shared()
        if _INSTANCE is not None:
            _INSTANCE.invalidate()
        return result

    def setSetting(self, sid, value):
        return self._write('setSetting', sid, value)

    def setSettingBool(self, sid, value):
        return self._write('setSettingBool', sid, value)

    def setSettingInt(self, sid, value):
        return self._write('setSettingInt', sid, value)

    def setSettingNumber(self, sid, value):
        return self._write('setSettingNumber', sid, value)

    def setSettingString(self, sid, value):
        return self._write('setSettingString', sid, value)

    def openSettings(self, *args):
        result = self._writer().openSettings(*args)
        _drop_shared()
        return result

    def __getattr__(self, name):
        return getattr(self._real(), name)


def install_shared(ttl=60.0):
    """Route xbmcaddon.Addon through SharedAddon in this process. The
    service keeps a snapshot up to a minute (its onSettingsChanged drops
    it); plugin calls, which Kodi may keep alive between calls without
    telling them about changes, keep the old two seconds."""
    global _REAL_ADDON, _SHARED_TTL
    _SHARED_TTL = float(ttl)
    if getattr(xbmcaddon, '_dexhub_shared', False):
        return False
    _REAL_ADDON = xbmcaddon.Addon
    try:
        _OWN_ID[0] = _REAL_ADDON().getAddonInfo('id')
    except Exception:
        _OWN_ID[0] = ''
    xbmcaddon.Addon = SharedAddon
    xbmcaddon._dexhub_shared = True
    return True
