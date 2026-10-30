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


def install():
    try:
        old = threading._dangling
        if not isinstance(old, _NoDangling):
            threading._dangling = _NoDangling()
            old.clear()
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
