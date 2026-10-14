"""Unified ranking facade."""
from .. import stream_ranking as _backend

def __getattr__(name): return getattr(_backend, name)
