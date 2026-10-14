"""Plex authentication facade.

Keeps account/PIN/client-id persistence separate from discovery and search.
The legacy plex_client remains the storage backend during the staged refactor.
"""
from .. import plex_client as _backend

def account(): return _backend.account()
def is_signed_in(): return _backend.is_signed_in()
def client_identifier(): return _backend.client_identifier()
def request_pin(): return _backend.request_pin()
def poll_pin(*args, **kwargs): return _backend.poll_pin(*args, **kwargs)
def sign_out(): return _backend.sign_out()
def __getattr__(name): return getattr(_backend, name)
