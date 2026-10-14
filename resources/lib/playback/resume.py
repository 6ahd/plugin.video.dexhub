"""Resume boundary for the staged refactor."""
from .. import playback_store as _backend

def __getattr__(name): return getattr(_backend, name)
