"""One service owner per add-on, with a cheap playback-side wakeup."""
import atexit
import json
import os
import time
import uuid
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.dexhub'
PROP = ADDON_ID + '.service.lease'
_handle = None
_owner = ''
_last_wake = 0.0


def pulse():
    if _handle is not None:
        xbmcgui.Window(10000).setProperty(PROP, json.dumps({'owner': _owner, 'at': time.time()}))


def release():
    global _handle, _owner
    handle, owner = _handle, _owner
    _handle, _owner = None, ''
    if handle is None:
        return
    try:
        home = xbmcgui.Window(10000)
        if json.loads(home.getProperty(PROP) or '{}').get('owner') == owner:
            home.clearProperty(PROP)
    except Exception:
        pass
    handle.close()  # The OS also releases this lock if Kodi exits/crashes.


def claim():
    global _handle, _owner
    if _handle is not None:
        return False
    folder = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('profile'))
    os.makedirs(folder, exist_ok=True)
    handle = open(os.path.join(folder, 'service-owner.lock'), 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _handle, _owner = handle, uuid.uuid4().hex
    pulse()
    atexit.register(release)
    return True


def ensure_started():
    """No waiting, HTTP or worker duplication on the playback path."""
    global _last_wake
    now = time.time()
    try:
        value = json.loads(xbmcgui.Window(10000).getProperty(PROP) or '{}')
        age = now - float(value.get('at') or 0)
        if value.get('owner') and 0 <= age < 60:
            return False
    except (TypeError, ValueError):
        pass
    if now - _last_wake < 10:
        return False
    _last_wake = now
    path = os.path.join(xbmcaddon.Addon(ADDON_ID).getAddonInfo('path'), 'service.py')
    arg = '"%s"' % path.replace('\\', '\\\\').replace('"', '\\"')
    xbmc.executebuiltin('RunScript(%s)' % arg)
    return True
