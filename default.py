# -*- coding: utf-8 -*-
import os
import re
import sys
addon_path = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(addon_path, 'resources', 'lib')
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

# v5.10.448: no weak set of threads (thread_registry.py: Kodi 22 crash)
from resources.lib.thread_registry import install as _no_dangling
_no_dangling()

# v5.10.408: the screensaver's entry is this file too (xbmc.ui.screensaver,
# library default.py). Kodi 22 beta1 ran screensaver.py for the add-on's
# plugin:// calls, every widget and row of the Home, and the call then hung
# on "waiting on thread"; with one library there is no other file to run.
# A screensaver start has no plugin:// address.
if not (sys.argv and str(sys.argv[0]).startswith('plugin://')):
    # started as before from screensaver.py: nothing else loaded first
    from resources.lib.homeui.screensaver import run as _screensaver
    _screensaver()
    sys.exit(0)

from resources.lib.tvos_compat import install as install_tvos_compat
install_tvos_compat()

from resources.lib.profile_scope import initialize as initialize_profiles
initialize_profiles()

# v5.10.301: one settings snapshot per two seconds instead of a full settings
# read for every xbmcaddon.Addon() (the skin's rows built dozens a second)
from resources.lib.settings_cache import install_shared as _install_shared_settings
_install_shared_settings(2.0)

from resources.lib.tvos_assets import publish_asset_root
publish_asset_root()

from resources.lib.bootstrap import run



# the short-lived threads a call waits for: the source searches (each with its
# own time limit) and the optional-work lane, whose idle workers leave after
# 1.5 s. Playback's threads (lifecycle, resume, panels) live with the playback
# and are not waited for.
_SETTLE_NAMES = ('DexHubWarm', 'DexHubKodiSource', 'DexHubColdNative', 'DexHubAltId', 'DexHubPlexServers',
                 'DexHubDialogWatch')
_SETTLE_PREFIXES = ('DexHub-bg_',)
# v5.10.448: lane workers live for the whole call; waited for only while one
# is still on a request (an idle one sits in its queue until the lanes close)
_BUSY_PREFIXES = ('DexHub-streams_', 'DexHub-browse_', 'DexHub-subtitles_')
# ... and once the lanes are closed (run() closes them), until every worker
# of this call has ended: DexHub-<lane>_<n>
_LANE = re.compile(r'^DexHub-[a-z]+_\d+$')
_IDLE_FILES = ('queue.py', 'threading.py', 'concurrent/futures/thread.py')


def _busy(thread):
    """A lane worker doing a request (not waiting in its queue)."""
    frame = sys._current_frames().get(thread.ident)
    while frame is not None:
        if not frame.f_code.co_filename.replace('\\', '/').endswith(_IDLE_FILES):
            return True
        frame = frame.f_back
    return False


def _settle(limit=90.0):
    """v5.10.448: Kodi 22 (Python 3.14) crashed when a call ended while its
    source searches (DexHubWarm, DexHubKodiSource) were still asking the
    add-ons: the interpreter went away under them. The call now waits for
    them (each has its own time limit), never past Kodi closing. The crash
    report of 30 Oct 02:06 shows the same teardown with only the idle lane
    worker DexHub-bg_1 left, and the Plex searches end the same way: those
    are waited for too."""
    import threading
    import time
    import xbmc
    monitor = xbmc.Monitor()
    end = time.monotonic() + limit
    me = threading.current_thread()
    while time.monotonic() < end and not monitor.abortRequested():
        busy = [t for t in threading.enumerate()
                if t is not me and t.is_alive() and (t.name in _SETTLE_NAMES or t.name.startswith(_SETTLE_PREFIXES)
                                                     or (t.name.startswith(_BUSY_PREFIXES) and _busy(t)))]
        if not busy:
            return
        busy[0].join(0.5)


def _lanes_done(limit=30.0):
    """v5.10.448: every lane worker of this call ended (run() closed the
    lanes, so each leaves after its request), then the closed executors are
    freed and the garbage collected, with nothing of the call still running.
    The crash report of 30 Oct 06:25: Kodi's own collection at the end of
    skin_warm_sources ran with its search workers alive, and a weak
    reference's callback crashed Kodi."""
    import threading
    import time
    import xbmc
    from resources.lib import runtime_cleanup
    runtime_cleanup.shutdown_loaded_pools()        # a lane opened after run() closed them
    monitor = xbmc.Monitor()
    end = time.monotonic() + limit
    me = threading.current_thread()
    while time.monotonic() < end and not monitor.abortRequested():
        alive = [t for t in threading.enumerate() if t is not me and t.is_alive() and _LANE.match(t.name or '')]
        if not alive:
            break
        alive[0].join(0.5)
    runtime_cleanup.release_retired_pools()


def _where(thread):
    """file:line function of the thread's own code (not the library it waits in)."""
    frame = sys._current_frames().get(thread.ident)
    last = ''
    while frame is not None:
        code = frame.f_code
        here = '%s:%d %s' % (os.path.basename(code.co_filename), frame.f_lineno, code.co_name)
        if 'plugin.video.dexhub' in code.co_filename:
            return here
        last = last or here
        frame = frame.f_back
    return last or '?'


def _alone(limit=45.0):
    """v5.10.448: True once this call's main thread is its only thread (each
    other one has its own time limit), within limit seconds; False when one
    lives on (playback's run as long as the video). The crash report of 30
    Oct 06:32: a collection with a Plex lookup and unnamed executor workers
    of the same call still running crashed Kodi in a weak reference's
    callback, like those of 05:22 and 06:25.
    Only the threads this call started count: Kodi 22 runs the call on a
    thread Python calls Dummy-1, and the interpreter's MainThread (another
    thread of Kodi's, always alive) kept every call open the whole 45 s."""
    import threading
    import time
    import xbmc
    monitor = xbmc.Monitor()
    end = time.monotonic() + limit
    me = threading.current_thread()
    kodi = (me, threading.main_thread())
    while not monitor.abortRequested():
        others = [t for t in threading.enumerate()
                  if t not in kodi and not isinstance(t, threading._DummyThread) and t.is_alive()]
        if not others:
            return True
        if time.monotonic() >= end:
            xbmc.log('[DexHub] call ends with threads alive: %s' % ' | '.join(
                '%s%s @ %s' % (t.name, ' (daemon)' if t.daemon else '', _where(t)) for t in others), xbmc.LOGINFO)
            return False
        others[0].join(0.5)
    return False


def _forget_threads(alone):
    """v5.10.448: Python keeps every thread in weak sets whose callbacks
    run when a thread object is freed. At the end of a call on Kodi 22
    (Python 3.14) that callback met its set already torn down: "Exception
    ignored while calling weakref callback WeakSet._remove ... expected 0
    arguments, got 1", then a segfault in PyObject_ClearWeakRefs (crash
    reports of 30 Oct 06:39 and 06:46). Emptied here, the callbacks go with
    them (threading._dangling only serves debugging; the executors' queue
    registry is read at exit to join workers, so it is emptied only when
    nothing else of the call runs)."""
    try:
        import threading
        threading._dangling.clear()
    except Exception:
        pass
    if alone:
        try:
            import sys as _sys
            module = _sys.modules.get('concurrent.futures.thread')
            if module is not None:
                module._threads_queues.clear()
        except Exception:
            pass


def _restore_page():
    try:
        from resources.lib import page_guard
        page_guard.restore()
    except Exception:
        pass


if __name__ == '__main__':
    # v5.10.448: no garbage collection while the call's threads run. The crash
    # report of 30 Oct 05:22 (Kodi 22, Python 3.14): the collector ran in this
    # frame just after run() returned, with the player's new source search
    # still connecting on DexHub-streams, and a weak reference's callback
    # crashed Kodi. Collection waits until those threads have finished.
    # No gc.collect() of its own any more: the crash report of 30 Oct 10:29
    # has the call's own collection (its threads all gone) crashing the same
    # way while the service's search ahead still had its workers; Kodi
    # collects once anyway as the call's interpreter closes.
    import gc
    gc.disable()
    try:
        run()
        _restore_page()
        _settle()
        _lanes_done()
        _forget_threads(_alone())
    finally:
        _forget_threads(False)
        gc.enable()
