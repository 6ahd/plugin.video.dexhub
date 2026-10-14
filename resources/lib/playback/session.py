"""Playback and source-session facade."""
from .. import session_store as _sessions
from ..search import source_cache

def save_session(*args, **kwargs): return _sessions.save_session(*args, **kwargs)
def load_session(*args, **kwargs): return _sessions.load_session(*args, **kwargs)
def clear_session(*args, **kwargs): return _sessions.clear_session(*args, **kwargs)
def __getattr__(name): return getattr(_sessions, name)
