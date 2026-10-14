"""Emby/Jellyfin authentication facade."""
from .. import emby_client as _backend

def account(): return _backend.account()
def is_signed_in(): return _backend.is_signed_in()
def sign_in(*args, **kwargs): return _backend.sign_in(*args, **kwargs)
def sign_out(): return _backend.sign_out()
def __getattr__(name): return getattr(_backend, name)
