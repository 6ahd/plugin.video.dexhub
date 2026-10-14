"""Unified source-session cache facade."""
from .. import source_session as _backend

def create(*args, **kwargs): return _backend.create(*args, **kwargs)
def load(*args, **kwargs): return _backend.load(*args, **kwargs)
def update(*args, **kwargs): return _backend.update(*args, **kwargs)
def delete(*args, **kwargs): return _backend.delete(*args, **kwargs)
def __getattr__(name): return getattr(_backend, name)
