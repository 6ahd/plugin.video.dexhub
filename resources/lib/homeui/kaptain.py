# -*- coding: utf-8 -*-
"""Kaptain's collection on the first run (v5.10.115).

Kaptain's Mega Collection (https://imkaptain.github.io/Kaptain-Collection/,
bundled with Dex Hub as its "Dex Collection") fills a Home with ready-made
collections: streaming services, networks, genres, moods, film series,
actors, directors, studios, decades, anime, awards, world cinema, kids.

The first-run screen offers it ticked; it is added when the screen is
finished with the tick on, unless the user has it already:

  * the bundled collection is on, or a collection of the user's (imported,
    or Nuvio's) holds Kaptain's groups: nothing to add;
  * no Nuvio: a collection the user chose is the Home's already: it stays;
    else the bundled one becomes the Home's collection;
  * Nuvio: its Home is the user's own; one with collections of its own
    stays as it is, one without gets Kaptain's groups as added rows at its
    end (the layout editor removes them like any added row).
"""
import os

STATE_HAS = 'has'           # Kaptain's collection is there already
STATE_OTHER = 'other'       # another collection of the user's leads the Home
STATE_NONE = 'none'         # nothing: it can be added

_IDS = None
_MIN_NAMES = 3              # Kaptain's group names that name a copy of it


def _cs():
    from .. import collection_sets as cs
    return cs


def kaptain_groups():
    """[(id, name)] of the bundled Kaptain collection's groups."""
    global _IDS
    if _IDS is None:
        cs = _cs()
        try:
            entries = cs.parse_collection_json(cs._bundled_kaptain_snapshot()) or []
        except Exception:
            entries = []
        _IDS = [(str(e.get('id') or ''), str(e.get('name') or '').strip())
                for e in entries if isinstance(e, dict) and e.get('kind') == 'nuvioGroup']
    return list(_IDS)


def _is_kaptain(row):
    if not isinstance(row, dict):
        return False
    if 'kaptain' in str(row.get('source_url') or '').lower():
        return True
    groups = kaptain_groups()
    ids = set(i for i, _n in groups if i)
    names = set(n.lower() for _i, n in groups if n)
    seen_names = 0
    for entry in row.get('entries') or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get('id') or '') in ids:
            return True
        if str(entry.get('name') or '').strip().lower() in names:
            seen_names += 1
    return seen_names >= _MIN_NAMES


def _nuvio_linked():
    try:
        from ..dexhub.nuvio_stremio_sync import Nuvio
        return bool(Nuvio.is_linked())
    except Exception:
        return False


def state():
    cs = _cs()
    try:
        if cs.builtin_collection_enabled():
            return STATE_HAS
        rows = list(cs._read() or [])
    except Exception:
        return STATE_NONE
    if any(_is_kaptain(row) for row in rows):
        return STATE_HAS
    cloud = cs.get_set(cs.NUVIO_CLOUD_ID) or {}
    cloud_has = any(isinstance(e, dict) and e.get('kind') == 'nuvioGroup' for e in cloud.get('entries') or [])
    if _nuvio_linked():
        # Nuvio's Home is the user's own: one with collections stays as it is
        return STATE_OTHER if cloud_has else STATE_NONE
    # without Nuvio the Home shows one collection: the one chosen, else the
    # first imported (or the copy of Nuvio's kept after unlinking)
    if cloud_has or any(isinstance(row, dict) and row.get('id') != cs.NUVIO_CLOUD_ID and row.get('entries')
                        for row in rows):
        return STATE_OTHER
    return STATE_NONE


def add(profile_dir=None, tr=None):
    """Add Kaptain's collection unless the user has it (or a collection of
    their own leads the Home). Returns the state found: STATE_NONE means it
    was added now."""
    found = state()
    if found != STATE_NONE:
        return found
    cs = _cs()
    cs.set_builtin_collection_enabled(True)
    if _nuvio_linked():
        _add_rows(profile_dir, tr)
    else:
        try:
            cs.set_active_set(cs.BUILTIN_KAPTAIN_ID)
        except Exception:
            pass
    return STATE_NONE


def _add_rows(profile_dir=None, tr=None):
    """Kaptain's groups as added rows at the end of a Nuvio Home without
    collections of its own."""
    from .layout import Layout, new_custom
    if not profile_dir:
        import xbmcaddon
        import xbmcvfs
        addon = xbmcaddon.Addon('plugin.video.dexhub')
        profile_dir = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo('profile')), 'homeui')
    os.makedirs(profile_dir, exist_ok=True)
    tr = tr or (lambda text: text)
    cs = _cs()
    layout = Layout(profile_dir)
    page = layout.page('all')
    custom = list(page['custom'])
    have = set((c.get('set_id'), c.get('entry_id')) for c in custom)
    for entry_id, name in kaptain_groups():
        if not entry_id or (cs.BUILTIN_KAPTAIN_ID, entry_id) in have:
            continue
        custom.append(new_custom({
            'type': 'collection', 'label': name, 'source': 'collection',
            'source_label': '%s  •  %s' % (tr('مجموعة'), 'Kaptain'),
            'set_id': cs.BUILTIN_KAPTAIN_ID, 'entry_id': entry_id,
        }))
    layout.save_page('all', custom=custom)
