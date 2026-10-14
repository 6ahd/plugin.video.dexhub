# -*- coding: utf-8 -*-
"""Bounded runner for optional Dex Hub background work.

Kodi keeps the Python interpreter alive between plugin invocations.  Creating
one daemon thread for every cache warm, logo refresh, artwork refresh and
manual sync therefore accumulates native threads on long CoreELEC sessions.
This module gives those *optional* one-shot jobs one shared two-worker lane,
deduplicates equivalent jobs and bounds the waiting queue.

Playback, source-result polling and service loops deliberately do not use this
runner: those jobs own a lifecycle and must remain independently cancellable.
"""
import queue as _queue
import threading


class IdleExitPool(object):
    """A small worker pool whose threads exit on their own once idle.

    v5.10.92. concurrent.futures workers are not daemon threads and, once
    their job is done, park on the work queue forever. The module-level
    executor this replaces was never shut down (shutdown_lane_pools only
    closes dexhub.client's lanes), so after any optional job two idle
    DexHub-bg workers stayed alive. Kodi's invoker waits for every thread of
    a finished plugin script before it can reuse the interpreter: the macOS
    crash report of 24 Sep shows one invoker in that wait loop, held by
    exactly DexHub-bg_0 and DexHub-bg_1 idling in queue.get, while the next
    click had to start a fresh interpreter behind the busy dialog.

    Workers here wait at most ``idle_seconds`` for more work and then exit,
    so a finished invocation is left with no DexHub threads a moment after its
    last job. ``submit(fn)`` keeps the executor's contract used by the
    callers: it never blocks, and it raises only when no worker exists and
    none can be started.
    """

    def __init__(self, max_workers=2, name='DexHub-bg', idle_seconds=1.5):
        self._max = max(1, int(max_workers))
        self._name = str(name or 'DexHub-bg')
        self._idle = max(0.2, float(idle_seconds))
        self._queue = _queue.Queue()
        self._lock = threading.Lock()
        self._workers = 0
        self._busy = 0
        self._serial = 0

    def _loop(self):
        while True:
            try:
                job = self._queue.get(timeout=self._idle)
            except _queue.Empty:
                with self._lock:
                    # Re-checked under the lock: submit() enqueues under the
                    # same lock, so a job can never be stranded by a worker
                    # that decided to leave.
                    if self._queue.empty():
                        self._workers -= 1
                        return
                continue
            with self._lock:
                self._busy += 1
            try:
                job()
            except Exception:
                pass
            finally:
                with self._lock:
                    self._busy -= 1

    def submit(self, fn, *args, **kwargs):
        if args or kwargs:
            job = lambda: fn(*args, **kwargs)
        else:
            job = fn
        with self._lock:
            pending = self._queue.qsize() + 1
            idle = self._workers - self._busy
            if self._workers < self._max and idle < pending:
                thread = threading.Thread(
                    target=self._loop, daemon=True,
                    name='%s_%d' % (self._name, self._serial % self._max))
                try:
                    thread.start()
                except Exception:
                    # Out of threads: an existing worker will still get to
                    # the job; with none alive the caller must know.
                    if self._workers == 0:
                        raise
                else:
                    self._workers += 1
                    self._serial += 1
            self._queue.put(job)
        return True


_MAX_QUEUED = 8
_POOL = IdleExitPool(max_workers=2, name='DexHub-bg')
_SLOTS = threading.BoundedSemaphore(_MAX_QUEUED)
_LOCK = threading.Lock()
_ACTIVE_KEYS = set()


def submit_optional(target, args=(), kwargs=None, key='', on_error=None):
    """Submit an optional one-shot job without ever blocking the caller.

    Returns ``True`` when accepted and ``False`` when an equivalent job is
    already active, the small queue is full, or Kodi cannot allocate a worker.
    Exceptions stay contained in the worker and may be reported through the
    optional ``on_error(exc)`` callback.
    """
    if not callable(target):
        return False
    task_key = str(key or '').strip()
    with _LOCK:
        if task_key and task_key in _ACTIVE_KEYS:
            return False
        if not _SLOTS.acquire(False):
            return False
        if task_key:
            _ACTIVE_KEYS.add(task_key)

    def _run():
        try:
            return target(*(tuple(args or ())), **dict(kwargs or {}))
        except Exception as exc:  # optional work must never crash Kodi UI
            if callable(on_error):
                try:
                    on_error(exc)
                except Exception:
                    pass
            return None
        finally:
            with _LOCK:
                if task_key:
                    _ACTIVE_KEYS.discard(task_key)
                try:
                    _SLOTS.release()
                except ValueError:
                    pass

    try:
        _POOL.submit(_run)
        return True
    except Exception as exc:
        with _LOCK:
            if task_key:
                _ACTIVE_KEYS.discard(task_key)
            try:
                _SLOTS.release()
            except ValueError:
                pass
        if callable(on_error):
            try:
                on_error(exc)
            except Exception:
                pass
        return False


def active_count():
    """Small diagnostic snapshot; contains no titles, URLs or user data."""
    with _LOCK:
        return len(_ACTIVE_KEYS)
