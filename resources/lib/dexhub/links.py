# -*- coding: utf-8 -*-
"""Add-on links as people paste them (v5.10.143, GitHub issue #1).

The install buttons on add-on configure pages hand out
``stremio://host/path/manifest.json``, and Stremio Web links carry the
manifest in ``addon=``. Neither is a URL Python can open ("unknown url type:
stremio"). Stremio opens a stremio:// link over https, so Dex Hub does too,
and every place that saves or reads a pasted add-on link passes it through
``manifest_link`` first.
"""
import re
from urllib.parse import unquote

_WEB_ADDON_RE = re.compile(r'[?&#]addon=([^&#\s]+)', re.I)
_WEB_HOSTS = ('stremio.com', 'strem.io')


def manifest_link(url):
    """The https manifest URL behind an add-on link; any other text comes back trimmed."""
    text = str(url or '').strip().strip('\'"<>').strip()
    lower = text.lower()
    if lower.startswith('stremio:'):
        rest = text[len('stremio:'):].lstrip('/')
        if rest.lower().startswith(('http://', 'https://')):
            return rest
        return ('https://' + rest) if rest else ''
    if 'addon=' in lower and any(host in lower for host in _WEB_HOSTS):
        match = _WEB_ADDON_RE.search(text)
        if match:
            inner = unquote(match.group(1)).strip()
            if inner.lower().startswith(('http://', 'https://', 'stremio:')):
                return manifest_link(inner)
    return text
