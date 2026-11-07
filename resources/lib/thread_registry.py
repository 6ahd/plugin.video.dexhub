# -*- coding: utf-8 -*-
"""v5.10.448: Python keeps every thread in threading._dangling, a WeakSet
whose callback runs when a thread object is freed. On Kodi 22 (Python 3.14)
that callback met its set already torn down at the end of a call ("Exception
ignored while calling weakref callback WeakSet._remove", then a segfault in
PyObject_ClearWeakRefs: crash reports of 30 Oct 05:22 to 06:46). Only
Python's own tests read the set, so Dex Hub's scripts keep none; logging's
set of handlers (read only after a fork) goes the same way."""
import threading


class _NoDangling(object):
    def add(self, *_args):
        pass

    def discard(self, *_args):
        pass

    def clear(self):
        pass

    def __len__(self):
        return 0

    def __iter__(self):
        return iter(())


def _strong_import_locks():
    """v5.10.543: the import machinery keeps each module's lock behind a weak
    reference whose callback forgets it when the lock is freed, and a weak
    table of the threads waiting on imports. On Kodi 22 that callback crashed
    the Android app (AM8, 6 Nov: SIGSEGV in PyObject_ClearWeakRefs during an
    import, "'dict' object is not callable" in _get_module_lock's cb), more
    often since Dex Hub's service runs there too. Here the locks are kept for
    good, a few hundred small objects, so no callback is ever left to run."""
    try:
        import _frozen_importlib as boot
    except ImportError:
        import importlib._bootstrap as boot
    if getattr(boot, '_dexhub_strong_locks', False):
        return
    imp = boot._imp
    kept = {}
    imp.acquire_lock()
    try:
        for name, ref in list(boot._module_locks.items()):
            lock = ref()
            if lock is not None:
                kept[name] = lock
        # the weak references go too: a reference freed before its lock never
        # calls back, also when the interpreter is torn down at the end of a
        # call (LibreELEC 7 Nov: Kodi's PyGC_Collect in onExecutionDone)
        boot._module_locks.clear()

        def _get_module_lock(name):
            imp.acquire_lock()
            try:
                lock = kept.get(name)
                if lock is None:
                    lock = boot._DummyModuleLock(name) if boot._thread is None else boot._ModuleLock(name)
                    kept[name] = lock
            finally:
                imp.release_lock()
            return lock

        boot._get_module_lock = _get_module_lock
        if getattr(boot, '_blocking_on', None) is not None and not isinstance(boot._blocking_on, dict):
            boot._blocking_on = {}
        boot._dexhub_strong_locks = True
    finally:
        imp.release_lock()


def install():
    try:
        _strong_import_locks()
    except Exception:
        pass
    try:
        old = threading._dangling
        if not isinstance(old, _NoDangling):
            threading._dangling = _NoDangling()
            old.clear()
    except Exception:
        pass
    # v5.10.543: logging keeps its named handlers in a WeakValueDictionary and
    # every handler in a list of weak references with a callback; both call
    # back when a handler is freed at the end of a call. Plain references:
    # a call makes a handful of handlers at most
    try:
        import logging
        if not isinstance(logging._handlers, dict):
            logging._handlers = dict(logging._handlers.items())
        if not getattr(logging, '_dexhub_strong_refs', False):
            logging._handlerList[:] = []
            logging._addHandlerRef = lambda handler: None
            logging._dexhub_strong_refs = True
    except Exception:
        pass
    # logging keeps every handler in a WeakSet too (its locks, renewed after
    # a fork, which Kodi never does); concurrent.futures loads logging, and
    # logging makes its last-resort handler at once
    try:
        import logging
        old = getattr(logging, '_at_fork_reinit_lock_weakset', None)
        if old is not None and not isinstance(old, _NoDangling):
            logging._at_fork_reinit_lock_weakset = _NoDangling()
            old.clear()
    except Exception:
        pass
    # the executors' registry of workers (a WeakKeyDictionary, the same kind
    # of callback): a plain dict; it is only read at exit, to join them
    try:
        from concurrent.futures import thread as _cf_thread
        registry = _cf_thread._threads_queues
        if not isinstance(registry, dict):
            _cf_thread._threads_queues = dict(registry.items())
    except Exception:
        pass


def prune():
    """v5.10.524: the executors' registry (a plain dict since install())
    kept every worker that ever ran, with its queue: in the service, which
    lives as long as Kodi, they piled up. Finished workers go (only those:
    a live one is joined at exit as before); returns how many."""
    try:
        from concurrent.futures import thread as _cf_thread
        registry = _cf_thread._threads_queues
        if not isinstance(registry, dict):
            return 0
        done = [worker for worker in list(registry) if not worker.is_alive()]
        for worker in done:
            registry.pop(worker, None)
        return len(done)
    except Exception:
        return 0
