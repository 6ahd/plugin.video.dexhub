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
# v5.10.402: links pasted from chats and notes (often from an Arabic keyboard)
# carry direction marks, zero-width and no-break spaces, or a sentence around
# the link; add-on sites also hand out their /configure page
_INVISIBLE_RE = re.compile('[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\u00a0]')
_LINK_RE = re.compile(r'(?:stremio|https?)://[^\s<>"\']+', re.I)


def manifest_variants(url):
    """``url`` and, for a link without a manifest file, the same link with
    ``/manifest.json`` added: the order an add-on install tries them in."""
    link = manifest_link(url)
    path = link.split('?', 1)[0].split('#', 1)[0]
    if link and not path.lower().endswith('.json'):
        query = link[len(path):]
        return [link, path.rstrip('/') + '/manifest.json' + query]
    return [link] if link else []


def manifest_link(url):
    """The https manifest URL behind an add-on link; any other text comes back trimmed."""
    text = _INVISIBLE_RE.sub('', str(url or '')).strip().strip('\'"<>').strip()
    found = _LINK_RE.search(text)
    if found and (found.start() or found.end() < len(text)):
        text = found.group(0)
    text = text.rstrip('.,;)]')
    path = text.split('?', 1)[0].split('#', 1)[0]
    if path.lower().rstrip('/').endswith('/configure'):
        text = path.rstrip('/')[:-len('configure')] + 'manifest.json'
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
