# -*- coding: utf-8 -*-
"""Local, non-network studio-logo resolution for Kodi skins.

Studio resource packs contain only a subset of production-company names.
Returning a guessed ``resource://.../<name>.png`` path makes Kodi's texture
loader emit an error every time a missing logo is focused. Resolve only real
textures and cache both hits and misses for the warm add-on interpreter.
"""
import os
import re

import xbmc
import xbmcaddon
import xbmcvfs


_CACHE = {}
_PACK_ROOTS = {}
_BUILTIN_ROOT = None
_PLATFORMS = {
    'netflix': 'netflix', 'netflix studios': 'netflix', 'netflix animation': 'netflix',
    'disney+': 'disneyplus', 'disney plus': 'disneyplus',
    'amazon prime video': 'primevideo', 'prime video': 'primevideo', 'amazon prime': 'primevideo',
    'apple tv+': 'appletvplus', 'apple tv plus': 'appletvplus',
    'hbo': 'hbo',
    'hulu': 'hulu', 'paramount+': 'paramountplus', 'paramount plus': 'paramountplus',
    'peacock': 'peacock', 'starz': 'starz', 'osn': 'osn', 'osn+': 'osn',
    'shahid': 'shahid', 'shahid vip': 'shahid', 'شاهد': 'shahid', 'crunchyroll': 'crunchyroll',
}
_PACKS = (
    'resource.images.studios.coloured',
    'resource.images.studios.white',
)


def _pack_root(pack):
    """Resolve an installed image add-on to its physical resources folder."""
    if pack in _PACK_ROOTS:
        return _PACK_ROOTS[pack]
    root = ''
    try:
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % pack):
            raise LookupError(pack)     # not installed: no EXCEPTION line in the log
        addon_path = xbmcaddon.Addon(pack).getAddonInfo('path') or ''
        addon_path = xbmcvfs.translatePath(addon_path) if addon_path else ''
        if addon_path:
            root = os.path.join(addon_path, 'resources')
    except Exception:
        root = ''
    _PACK_ROOTS[pack] = root
    return root


def _names(value):
    values = value if isinstance(value, (list, tuple)) else [value]
    out = []
    seen = set()
    for entry in values:
        if isinstance(entry, dict):
            entry = entry.get('name') or entry.get('title') or ''
        name = str(entry or '').strip()
        if not name:
            continue
        variants = [name.replace('/', '-').replace('\\', '-').strip()]
        # Providers often append a country/role while packs keep the canonical
        # company name only. Probe both forms without changing shown metadata.
        compact = re.sub(r'\s*\([^)]{2,80}\)\s*$', '', variants[0]).strip()
        if compact and compact != variants[0]:
            variants.append(compact)
        for candidate in variants:
            folded = candidate.casefold()
            if candidate and folded not in seen:
                seen.add(folded)
                out.append(candidate)
    return out


def resolve(value):
    """Return the first installed studio texture, or an empty string."""
    key = tuple(_names(value))
    if not key:
        return ''
    if key in _CACHE:
        return _CACHE[key]
    for name in key:
        slug = _PLATFORMS.get(name.casefold())
        if slug:
            global _BUILTIN_ROOT
            if _BUILTIN_ROOT is None:
                try:
                    _BUILTIN_ROOT = os.path.join(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path'),
                                                'resources', 'media', 'platforms')
                except Exception:
                    _BUILTIN_ROOT = ''
            local = os.path.join(_BUILTIN_ROOT, slug + '.png') if _BUILTIN_ROOT else ''
            if local and xbmcvfs.exists(local):
                _CACHE[key] = local
                return local
        for pack in _PACKS:
            root = _pack_root(pack)
            if not root:
                continue
            physical_path = os.path.join(root, '%s.png' % name)
            try:
                if xbmcvfs.exists(physical_path):
                    resource_path = 'resource://%s/%s.png' % (pack, name)
                    _CACHE[key] = resource_path
                    return resource_path
            except Exception:
                continue
    _CACHE[key] = ''
    return ''


_PLAYER_PACKS = (
    'resource.images.studios.white',
    'resource.images.studios.coloured',
)


def resolve_player(value):
    """The player's network/studio logo (v5.10.140): drawn on the picture
    with no plate behind it, so only an installed pack logo will do, the
    white pack first (one look over any scene), then the coloured pack.
    Never TMDb's (often dark) remote logos, nor Dex Hub's platform tiles
    (app icons on their own square backgrounds)."""
    names = _names(value)
    key = ('player',) + tuple(names)
    if not names:
        return ''
    if key in _CACHE:
        return _CACHE[key]
    found = ''
    for name in names:
        for pack in _PLAYER_PACKS:
            root = _pack_root(pack)
            if not root:
                continue
            try:
                if xbmcvfs.exists(os.path.join(root, '%s.png' % name)):
                    found = 'resource://%s/%s.png' % (pack, name)
                    break
            except Exception:
                continue
        if found:
            break
    _CACHE[key] = found
    return found


def clear_cache():
    global _BUILTIN_ROOT
    _CACHE.clear()
    _PACK_ROOTS.clear()
    _BUILTIN_ROOT = None


def pick(rows=None, names=None, limit=1):
    """Studio logos to draw, as [(texture, chip)] (v5.10.105).

    rows: [{'name', 'logo'}] from TMDb Helper's cache or TMDb (logo is the
    TMDb image URL). An installed studio pack logo (made for dark skins) is
    drawn as it is; otherwise the TMDb logo, which is often dark, is drawn
    on a light chip (chip True). names: plain studio names tried against the
    packs when no row gave a logo.
    """
    out = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get('name') or '').strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        local = resolve(name)
        if local:
            out.append((local, False))
        elif row.get('logo'):
            out.append((str(row.get('logo')), True))
        if len(out) >= limit:
            return out
    if not out:
        for name in _names(names or []):
            if name.casefold() in seen:
                continue
            seen.add(name.casefold())
            local = resolve(name)
            if local:
                out.append((local, False))
            if len(out) >= limit:
                break
    return out
