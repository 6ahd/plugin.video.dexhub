# -*- coding: utf-8 -*-
"""urllib follows redirects with every header it was given (v5.10.530).

HTTPRedirectHandler copies all but Content-Length and Content-Type, so a
token sent to one server went on to wherever that server pointed: Trakt,
Simkl, Plex, Emby, Silo and the rest, about 45 files. The rule is applied once
here (redirect_policy.py), on the handler every opener is built from.

urllib.request costs 120-150 ms to import on a slow ARM box and most calls
never need it, so this waits for it: a finder patches it as it is imported.
"""
import sys

_applied = []


def _apply(module):
    if _applied:
        return
    _applied.append(True)
    from .redirect_policy import next_headers, origin
    plain = module.HTTPRedirectHandler.redirect_request

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        following = plain(self, req, fp, code, msg, headers, newurl)
        if following is not None:
            kept = next_headers(dict(following.headers), origin(req.full_url), origin(newurl))
            if kept is not following.headers:
                following.headers = dict(kept)
        return following

    module.HTTPRedirectHandler.redirect_request = redirect_request


class _Finder(object):
    """Steps aside at the first import of urllib.request, and patches it."""

    def find_spec(self, name, path=None, target=None):
        if name != 'urllib.request':
            return None
        sys.meta_path.remove(self)
        from importlib.machinery import PathFinder
        spec = PathFinder.find_spec(name, path, target)
        if spec is None or spec.loader is None or not hasattr(spec.loader, 'exec_module'):
            return None
        run = spec.loader.exec_module

        def exec_module(module):
            run(module)
            _apply(module)

        spec.loader.exec_module = exec_module
        return spec


def install():
    module = sys.modules.get('urllib.request')
    if module is not None:
        _apply(module)
    elif not _applied and not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
