"""One service owner per add-on, with a cheap playback-side wakeup."""
import atexit
import errno
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


def _other_owner_alive():
    """Another interpreter owns the service: it pulsed within the minute."""
    try:
        value = json.loads(xbmcgui.Window(10000).getProperty(PROP) or '{}')
        return bool(value.get('owner')) and 0 <= time.time() - float(value.get('at') or 0) < 60
    except (TypeError, ValueError):
        return False


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
    except OSError as exc:
        # v5.10.542: Android's shared storage answers ENOSYS (seen on Android
        # 14): that is no lock to take, not another owner, and the service
        # never started there
        if exc.errno not in (errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP) or _other_owner_alive():
            # v5.10.543: with no file lock, a service that pulsed in the last
            # minute still owns it; 5.10.542 let a second copy run every worker twice
            handle.close()
            return False
    except (ImportError, AttributeError):
        # v5.10.495: no file lock on this Python (Kodi on Xbox, the Windows
        # Store build, lacks parts of os/msvcrt): one Kodi runs one service,
        # so it carries on as the owner instead of never starting
        if _other_owner_alive():
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
