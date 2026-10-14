"""Native Plex provider facade.

This is the only interface search/routes should use.  The implementation is
forwarded to the proven plex_client while the large module is split safely.
"""
from .. import plex_client as _backend
from ..auth import plex_auth

def is_signed_in(): return plex_auth.is_signed_in()
def servers(*args, **kwargs): return _backend.servers(*args, **kwargs)
def server_by_id(*args, **kwargs): return _backend.server_by_id(*args, **kwargs)
def libraries(*args, **kwargs): return _backend.libraries(*args, **kwargs)
def find_all_by_ids(*args, **kwargs): return _backend.find_all_by_ids(*args, **kwargs)
def __getattr__(name): return getattr(_backend, name)
