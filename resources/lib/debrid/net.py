# -*- coding: utf-8 -*-
"""One small HTTP helper for the Debrid services (JSON in, JSON out)."""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

AGENT = 'Dex Hub (Kodi)'


class HttpError(Exception):
    def __init__(self, code, body='', url=''):
        Exception.__init__(self, 'HTTP %s' % code)
        self.code = code
        self.body = body or ''
        self.url = url or ''

    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return {}


# Replaced by tests: a function (method, url, headers, body) -> (status, text).
_TRANSPORT = None


def _context():
    try:
        return ssl.create_default_context()
    except Exception:
        return None


def multipart(fields):
    """(body bytes, content type) for a multipart/form-data request."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, value))
    parts.append('--%s--\r\n' % boundary)
    return ''.join(parts).encode('utf-8'), 'multipart/form-data; boundary=%s' % boundary


def request(method, url, headers=None, form=None, data=None, json_body=None, raw=None, timeout=20, want_json=True):
    """Answer of one request: parsed JSON (or text when want_json is False).

    form: dict or list of pairs, sent url-encoded; json_body: sent as JSON;
    raw: (bytes, content type). HTTP errors raise HttpError with the body.
    """
    head = {'User-Agent': AGENT, 'Accept': 'application/json'}
    head.update(headers or {})
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form, doseq=True).encode('utf-8')
        head['Content-Type'] = 'application/x-www-form-urlencoded'
    elif json_body is not None:
        body = json.dumps(json_body).encode('utf-8')
        head['Content-Type'] = 'application/json'
    elif raw is not None:
        body, head['Content-Type'] = raw
    elif data is not None:
        body = data
    if _TRANSPORT is not None:
        status, text = _TRANSPORT(method, url, head, body)
        if status >= 400:
            raise HttpError(status, text, url)
    else:
        req = urllib.request.Request(url, data=body, headers=head, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_context()) as answer:
                text = answer.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read().decode('utf-8', 'replace')
            except Exception:
                text = ''
            raise HttpError(exc.code, text, url)
    if not want_json:
        return text
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        raise HttpError(0, text[:300], url)


def get(url, **kw):
    return request('GET', url, **kw)


def post(url, **kw):
    return request('POST', url, **kw)
