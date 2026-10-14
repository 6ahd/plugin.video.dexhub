# -*- coding: utf-8 -*-
"""Stremio source adapter.

Keeps Kodi-facing routing out of the generic HTTP client and normalises the
media types / ids used by Stremio stream resources.
"""
from .. import client as _backend

SERIES_TYPES = ("series", "tv", "show", "episode")

def normalized_media_type(media_type):
    value = str(media_type or "movie").strip().lower()
    if value in SERIES_TYPES:
        return "series"
    return value

def supports_stream(provider, media_type, item_id=None):
    mt = normalized_media_type(media_type)
    if _backend.supports_resource(provider, "stream", media_type=mt, item_id=item_id):
        return True
    # Anime addons vary: some declare anime, others only series.
    if mt == "anime":
        return _backend.supports_resource(provider, "stream", media_type="series", item_id=item_id)
    return False

def fetch(provider, media_type, item_id, timeout_override=None):
    mt = normalized_media_type(media_type)
    data = _backend.fetch_streams(provider, mt, item_id, timeout_override=timeout_override)
    if (not (data or {}).get("streams")) and mt == "anime":
        data = _backend.fetch_streams(provider, "series", item_id, timeout_override=timeout_override)
    return data

def __getattr__(name):
    return getattr(_backend, name)
