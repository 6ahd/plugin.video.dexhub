"""Native Emby/Jellyfin provider facade."""
from .. import emby_client as _backend
from ..auth import emby_auth

def is_signed_in(): return emby_auth.is_signed_in()
def servers(*args, **kwargs): return _backend.servers(*args, **kwargs)
def find_all_by_ids(*args, **kwargs): return _backend.find_all_by_ids(*args, **kwargs)
def __getattr__(name): return getattr(_backend, name)
