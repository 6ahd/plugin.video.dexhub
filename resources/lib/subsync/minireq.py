# -*- coding: utf-8 -*-
"""The few parts of `requests` the AutoSync modules use, on urllib (v5.10.117).

Used only when script.module.requests is not installed: Session().get(url,
headers=, stream=, allow_redirects=, verify=, timeout=) and a response with
status_code, headers, url, iter_content(), content and close().
"""
import ssl
import urllib.error
import urllib.request

_UNVERIFIED = ssl.create_default_context()
_UNVERIFIED.check_hostname = False
_UNVERIFIED.verify_mode = ssl.CERT_NONE


class _Headers(dict):
    def get(self, key, default=None):
        key = str(key or '').lower()
        for name, value in self.items():
            if str(name).lower() == key:
                return value
        return default


class Response(object):
    def __init__(self, raw, status, headers, url):
        self._raw = raw
        self.status_code = int(status or 0)
        self.headers = _Headers(headers or {})
        self.url = url
        self._content = None

    def iter_content(self, size=65536):
        if self._content is not None:
            for start in range(0, len(self._content), size):
                yield self._content[start:start + size]
            return
        if self._raw is None:
            return
        while True:
            piece = self._raw.read(size)
            if not piece:
                break
            yield piece

    @property
    def content(self):
        if self._content is None:
            self._content = b''.join(self.iter_content()) if self._raw is not None else b''
        return self._content

    def close(self):
        try:
            if self._raw is not None:
                self._raw.close()
        except Exception:
            pass


class Session(object):
    def __init__(self):
        self.headers = {}

    def get(self, url, headers=None, stream=False, allow_redirects=True, verify=True, timeout=None, **_kw):
        merged = dict(self.headers)
        merged.update(headers or {})
        if isinstance(timeout, (tuple, list)):
            timeout = max(float(t or 0) for t in timeout) or None
        request = urllib.request.Request(url, headers=merged)
        context = None if verify else _UNVERIFIED
        try:
            raw = urllib.request.urlopen(request, timeout=timeout, context=context)
            return Response(raw, raw.getcode(), dict(raw.headers.items()), raw.geturl())
        except urllib.error.HTTPError as exc:
            return Response(None, exc.code, dict(exc.headers.items()) if exc.headers else {}, url)


_DEFAULT = Session()


def get(url, **kw):
    return _DEFAULT.get(url, **kw)
