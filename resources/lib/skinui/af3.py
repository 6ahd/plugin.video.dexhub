from resources.lib.tvos_assets import resolve_assets as _tvos_assets
# -*- coding: utf-8 -*-
"""Dex Hub inside Arctic Fuse 3 (v5.10.111, reworked in v5.10.112).

For whoever keeps the regular Arctic Fuse 3: Dex Hub's rows become the
skin's own widgets, its picks the hubs' spotlight, its search a search
widget, and (by choice) its trailers play behind its titles. The skin's
font, player and settings stay as they are.

Arctic Fuse 3 builds its hubs from script.skinvariables menus, one JSON file
per menu in userdata/addon_data/script.skinvariables/nodes/<skin>/
(homewidgets, 1101widgets to 1104widgets, homesubmenu, searchwidgets,
powermenu); script.skinvariables turns them into the skin's includes and the
skin reloads.

What changed in v5.10.112:

  * every row of the Dex Hub tab comes (5.10.111 took the first twelve),
    the films and series tabs get a hub each by default, and the rows follow
    Dex Hub: a row added, removed, renamed or moved there is written into the
    skin's menus and the skin rebuilds itself at the next quiet moment (the
    Home on show, nothing playing, no key for a few seconds);
  * a widget call leaves no Python interpreter for Kodi to reuse
    (serve.widget) and a row whose titles changed is asked for again one at
    a time (gates.py): twelve Dex Hub rows asked at once lost rows and, in
    the test harness, crashed Kodi (most likely the crash seen on a Mac
    opening a hub);
  * Install takes a backup first (the skin's menus as they were, the skin's
    settings file and, for the trailer patch, the skin files it changes);
    Remove puts them back: a menu Dex Hub wrote last is restored as it was
    before the install, one the user edited since keeps their edits without
    Dex Hub's entries; the skin strings Dex Hub changed get their old values;
  * the choices say what each one brings, with the number of rows;
  * the trailer patch (af3patch.py) when the skin has no trailers of its own.

v5.10.119: Dex Hub's own pages inside the skin (af3pages.py): its title page
in the skin's information dialog for Dex Hub's titles, its collection folders
as pages of rows, the playing title's logo on the seek bar's line. A choice
of its own ("pages"), ticked by default; an install made before it gets it
once, at a quiet moment.

v5.10.120: Dex Hub's own sections after the skin's (af3hubs.py): "Dex Hub"
(its Home's rows), its films and its series, with Dex Hub's mark as their
icon. The skin's Home and its four hubs are no longer touched: Dex Hub's rows
on the skin's Home are a choice of their own, off by default, and an install
made before moves its rows out of the skin's Home and hubs into its sections
once, at a quiet moment. The skin's empty widgets are hidden (its own
setting, quiet_follow): a choice ticked by default, given once to an install
made before.
"""
import hashlib
import json
import os
import shutil
import threading
import time
from .. import lazy_tb as traceback

import xbmc
import xbmcgui

from . import common as C
from . import gates as G

MARK = 'dexhub'
PREFIX = 'skinvariables-shortcut-'
HUBS = ('1101', '1102', '1103', '1104')
SECTIONS = ('dexhub', 'movie', 'series')     # Dex Hub's own hubs (af3hubs.py)
ROW_CAP = 60                # widget ids 501.. stay clear of the skin's own (600+)
ICON = _tvos_assets('special://home/addons/plugin.video.dexhub/resources/media/dexhub_infinity.png')
OPEN_HOME = 'RunPlugin(plugin://plugin.video.dexhub/?action=home_ui)'
PLACES = ('dexhub', 'movie', 'series', 'home', 'search', 'power', 'pages', 'trailers', 'quiet')
TAB_OF = {'home': 'all', 'dexhub': 'all', 'movie': 'movie', 'series': 'series'}
DEFAULT_OFF = ('home',)     # the skin's own Home stays the user's unless asked
QUIET_IDLE = 8              # seconds without a key before the skin may be rebuilt
CHECK_EVERY = 5.0
# v5.10.120: the skin's own setting ("Empty widget placeholder"): set, an
# empty widget is hidden instead of showing a "No Results" card
NO_RESULTS = 'Widgets.DisableNoResultsItem'
FORMAT = 3                  # how the menus are written (a new one: written again at the next quiet moment)


def _tr(text):
    try:
        from ..i18n import tr       # v5.10.235: not through rows.app()
        return tr(text)
    except Exception:
        return text


def _translate(path):
    import xbmcvfs
    return xbmcvfs.translatePath(path)


def _sha1_file(path):
    try:
        with open(path, 'rb') as handle:
            return hashlib.sha1(handle.read()).hexdigest()
    except Exception:
        return ''


# --------------------------------------------------------------------------
# the skin's menus
# --------------------------------------------------------------------------

def supported():
    """The skin in use builds its hubs from script.skinvariables menus (Arctic
    Fuse 3, and skins built the same way)."""
    try:
        shortcuts = os.path.join(_translate('special://skin/'), 'shortcuts')
    except Exception:
        return False
    return (os.path.exists(os.path.join(shortcuts, PREFIX + 'homewidgets.json'))
            and os.path.exists(os.path.join(shortcuts, 'skinvariables-generator.json'))
            and xbmc.getCondVisibility('System.HasAddon(script.skinvariables)'))


def _nodes_dir(skin):
    return _translate('special://profile/addon_data/script.skinvariables/nodes/%s/' % skin)


def _node_path(skin, menu):
    return os.path.join(_nodes_dir(skin), PREFIX + menu + '.json')


def _read(skin, menu):
    """A menu as the user has it (their own copy), else the skin's default."""
    data = C.read_json(_node_path(skin, menu))
    if data is None:
        data = C.read_json(os.path.join(_translate('special://skin/'), 'shortcuts', PREFIX + menu + '.json'))
    return data if isinstance(data, list) else ([] if data is None else None)


def _write(skin, menu, items, state=None):
    folder = _nodes_dir(skin)
    os.makedirs(folder, exist_ok=True)
    path = _node_path(skin, menu)
    tmp = path + '.dexhub.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(items, handle, ensure_ascii=True, indent=4)   # v5.10.168: \u escapes, read right whatever the reader's encoding
    os.replace(tmp, path)
    if state is not None:
        state.setdefault('written', {})[menu] = _sha1_file(path)
    # script.skinvariables keeps a copy of every menu in a Home property
    try:
        xbmcgui.Window(10000).clearProperty('SkinVariables.ShortcutsNode.%s-%s' % (skin, PREFIX + menu + '.json'))
    except Exception:
        pass


def _ours(item):
    if not isinstance(item, dict):
        return False
    if str(item.get(MARK) or '').lower() == 'true':
        return True
    path = str(item.get('path') or '')
    return (path.startswith(C.BASE + '?action=skin_row') or path.startswith(C.BASE + '?action=skin_search')
            or 'dhs.g.' in path)


def _replace(skin, menu, mine, state, top=True):
    """The menu with Dex Hub's entries swapped for ``mine`` (at the top, or at
    the end); False when the menu is not a list this can edit."""
    items = _read(skin, menu)
    if items is None:
        C.log('Arctic Fuse 3: menu %s is not a list, left alone' % menu, xbmc.LOGWARNING)
        return False
    theirs = [item for item in items if not _ours(item)]
    new = (list(mine) + theirs) if top else (theirs + list(mine))
    if new != items or (mine and menu not in (state.get('written') or {})):
        _write(skin, menu, new, state)
    return True


# --------------------------------------------------------------------------
# what goes in
# --------------------------------------------------------------------------

def slots(tab):
    """[[row id, title, shape]] of a Dex Hub tab, as its Home shows them: in
    order. Cached item counts do not alter the skin structure."""
    from . import board
    out = []
    for spec in board.load_specs(tab):
        row_id = spec.get('id') or ''
        if not row_id:
            continue
        data = C.read_json(C.row_file(tab, row_id))
        shape = spec.get('shape') or 'poster'
        out.append([row_id, spec.get('title') or '', shape])
        if len(out) >= ROW_CAP:
            break
    return out


def _style(shape):
    return {'landscape': 'Landscape', 'square': 'Square', 'circle': 'Circle'}.get(shape or '', 'Poster')


def _title_prop(menu, slot):
    return 'dhs.af3t.%s.%d' % (menu, slot)


def _label(menu, slot, title):
    """v5.10.168: a row title that is not plain ASCII goes in as a Home
    property. script.skinvariables writes the skin's include without an
    encoding, and Kodi read it in the system's code page on some devices
    (Windows 1256 for Arabic), so Arabic titles showed as garbled letters."""
    title = title or 'Dex Hub'
    try:
        title.encode('ascii')
        return title
    except UnicodeError:
        return '$INFO[Window(Home).Property(%s)]' % _title_prop(menu, slot)


def publish_titles(state):
    """The titles the menus point at (each start of Kodi, after a write)."""
    for menu, info in (state.get('menus') or {}).items():
        for slot, entry in enumerate(info.get('rows') or []):
            try:
                C.set_prop(_title_prop(menu, slot), entry[1] or 'Dex Hub')
            except Exception:
                pass


def relabel(state):
    """An install made before 5.10.168: its row titles written as text are
    turned into the properties; True when a menu changed."""
    skin = state.get('skin') or ''
    changed = False
    for menu, info in (state.get('menus') or {}).items():
        node = info.get('node') or ''
        items = _read(skin, node) if node else None
        if not items:
            continue
        rows = info.get('rows') or []
        new = []
        for item in items:
            if _ours(item) and str(item.get('guid') or '').startswith('dexhub.%s.' % menu):
                try:
                    slot = int(str(item['guid']).rsplit('.', 1)[1])
                except (ValueError, IndexError):
                    slot = -1
                if 0 <= slot < len(rows):
                    label = _label(menu, slot, rows[slot][1])
                    if item.get('label') != label:
                        item = dict(item, label=label)
            new.append(item)
        if new != items:
            _write(skin, node, new, state)
            changed = True
    return changed


def _row_widgets(menu, tab, rows):
    out = []
    for slot, (row_id, title, shape) in enumerate(rows):
        out.append({'label': _label(menu, slot, title), 'icon': '',
                    'path': G.widget_path(tab, row_id, menu),
                    'target': 'videos', 'widget_style': _style(shape),
                    # Kodi reads a list again after playback unless it is sorted as given
                    'widget_sortby': 'playlist',
                    'guid': 'dexhub.%s.%d' % (menu, slot), MARK: 'true'})
    return out


def _search_widgets():
    # one widget, films and series together: two Dex Hub calls at once is what gates.py avoids
    return [{'label': 'Dex Hub', 'icon': ICON, 'path': C.url('skin_search', m='all') + '&query=',
             'target': 'videos', 'widget_style': 'Poster', 'widget_sortby': 'playlist',
             'guid': 'dexhub.search', MARK: 'true'}]


def _spot_url(tab, menu):
    if not C.read_json(C.row_file(tab, 'spot')):
        return ''
    version = C.prop('dhs.v.%s.spot' % tab) or '0'
    return C.url('skin_row', m=tab, id='spot', af3=menu, v=version)


def _skin_string(key):
    return xbmc.getInfoLabel('Skin.String(%s)' % key) or ''


def _set_strings(state, values):
    """Skin strings Dex Hub sets; the user's own values are kept to put back.
    A hub's name or icon the user changed since Dex Hub set it stays theirs."""
    saved = state.setdefault('saved', {})
    ours = state.setdefault('set', {})
    for key, value in values:
        current = _skin_string(key)
        if key not in saved:
            saved[key] = current
        elif key.endswith(('.Name', '.Icon')) and key in ours and current and current != ours[key]:
            continue
        ours[key] = value
        if current != value:
            xbmc.executebuiltin('Skin.SetString(%s,%s)' % (key, value))


def _restore_strings(state, prefix=''):
    saved = state.get('saved') or {}
    for key in list(saved):
        if prefix and not key.startswith(prefix):
            continue
        old = saved.pop(key)
        (state.get('set') or {}).pop(key, None)
        if old:
            xbmc.executebuiltin('Skin.SetString(%s,%s)' % (key, old))
        else:
            xbmc.executebuiltin('Skin.Reset(%s)' % key)


def _restore_bools(state):
    """Skin settings Dex Hub turned on or off (the empty widgets' setting,
    quiet_follow): their values from before, back."""
    for key, was in list((state.get('saved_bools') or {}).items()):
        xbmc.executebuiltin(('Skin.SetBool(%s)' if was else 'Skin.Reset(%s)') % key)
    state['saved_bools'] = {}


def quiet_follow(state, places):
    """v5.10.120: the skin's empty widgets hidden (``quiet`` chosen). For
    whoever keeps everything in Dex Hub, the skin's library rows (On Deck,
    In-Progress, Recently added...) are always empty, and each showed a
    "No Results" card. The skin's own setting does it; the user's value
    comes back when the choice is taken away, and with Remove. The skin
    reads it as it loads (the rebuild that follows)."""
    saved = state.setdefault('saved_bools', {})
    have = bool(xbmc.getCondVisibility('Skin.HasSetting(%s)' % NO_RESULTS))
    if 'quiet' in places:
        if NO_RESULTS not in saved:
            saved[NO_RESULTS] = have
        if not have:
            xbmc.executebuiltin('Skin.SetBool(%s)' % NO_RESULTS)
            C.log('Arctic Fuse 3: empty widgets hidden')
    elif NO_RESULTS in saved:
        was = saved.pop(NO_RESULTS)
        if was != have:
            xbmc.executebuiltin(('Skin.SetBool(%s)' if was else 'Skin.Reset(%s)') % NO_RESULTS)


def _free_hub(skin, taken):
    for hub in HUBS:
        if hub in taken:
            continue
        items = _read(skin, hub + 'widgets') or []
        if [i for i in items if not _ours(i)]:
            continue        # the user's own hub
        return hub
    return ''


def _section_name(kind):
    return {'dexhub': 'Dex Hub', 'movie': _tr('أفلام Dex'), 'series': _tr('مسلسلات Dex')}.get(kind, 'Dex Hub')


def _hub_icon(name):
    path = os.path.join(_translate('special://skin/'), 'extras', 'icons', name)
    return 'special://skin/extras/icons/%s' % name if os.path.exists(path) else ICON


def _apply(state, places):
    """Write Dex Hub into the skin's menus for ``places``; the places no
    longer chosen are taken out again."""
    skin = state['skin']
    hubs = dict(state.get('hubs') or {})
    menus = {}
    # the Home hub
    if 'home' in places:
        rows = slots('all')
        _replace(skin, 'homewidgets', _row_widgets('home', 'all', rows), state)
        _replace(skin, 'homesubmenu', [{'label': 'Dex Hub', 'icon': ICON, 'path': OPEN_HOME, 'target': '',
                                        'guid': 'dexhub.open', MARK: 'true'}], state)
        menus['home'] = {'node': 'homewidgets', 'tab': 'all', 'rows': rows}
        spot = _spot_url('all', 'home')
        if spot:
            menus['home']['spot'] = True
            _set_strings(state, [('HomeSwitcher.Home.Spotlight.Path', spot),
                                 ('HomeSwitcher.Home.Spotlight.Target', 'videos'),
                                 ('HomeSwitcher.Home.Spotlight.Label', 'Dex Hub'),
                                 ('HomeSwitcher.Home.Spotlight.SortMethod', 'playlist')])
    else:
        _replace(skin, 'homewidgets', [], state)
        _replace(skin, 'homesubmenu', [], state)
        _restore_strings(state, 'HomeSwitcher.Home.')
    sections = bool((state.get('sections') or {}).get('added'))
    if sections:
        # v5.10.120: Dex Hub's own hubs; the skin's hubs it took before are the skin's again
        for kind in ('movie', 'series'):
            hub = hubs.get(kind) or ''
            if hub in HUBS:
                _replace(skin, hub + 'widgets', [], state)
                _restore_strings(state, 'HomeSwitcher.%s.' % hub)
                hubs.pop(kind, None)
        from . import af3hubs
        for kind in SECTIONS:
            hub = af3hubs.WINDOW_OF[TAB_OF[kind]]
            if kind in places:
                hubs[kind] = hub
                rows = slots(TAB_OF[kind])
                _replace(skin, hub + 'widgets', _row_widgets(hub, TAB_OF[kind], rows), state)
                _replace(skin, hub + 'submenu', [{'label': 'Dex Hub', 'icon': ICON, 'path': OPEN_HOME,
                                                  'target': '', 'guid': 'dexhub.open.%s' % hub, MARK: 'true'}],
                         state)
                menus[hub] = {'node': hub + 'widgets', 'tab': TAB_OF[kind], 'rows': rows}
                values = [('HomeSwitcher.%s.Toggle' % hub, 'true'),
                          ('HomeSwitcher.%s.Name' % hub, _section_name(kind))]
                if _skin_string('HomeSwitcher.%s.Mode' % hub) not in ('Standard', 'Combined', 'Wall'):
                    values.append(('HomeSwitcher.%s.Mode' % hub, 'Standard'))
                spot = _spot_url(TAB_OF[kind], hub)
                if spot:
                    menus[hub]['spot'] = True
                    values += [('HomeSwitcher.%s.Spotlight.Path' % hub, spot),
                               ('HomeSwitcher.%s.Spotlight.Target' % hub, 'videos'),
                               ('HomeSwitcher.%s.Spotlight.Label' % hub, 'Dex Hub'),
                               ('HomeSwitcher.%s.Spotlight.SortMethod' % hub, 'playlist')]
                _set_strings(state, values)
            else:
                _replace(skin, hub + 'widgets', [], state)
                _replace(skin, hub + 'submenu', [], state)
                _restore_strings(state, 'HomeSwitcher.%s.' % hub)
                if hubs.get(kind) == hub:
                    hubs.pop(kind, None)
    else:
        # a skin the sections cannot go in: a free hub of the skin's for films and for series
        for kind, name, icon in (('movie', _tr('أفلام'), 'film.png'), ('series', _tr('مسلسلات'), 'tv.png')):
            hub = hubs.get(kind) or ''
            if hub and hub not in HUBS:
                hub = ''
            if kind in places:
                if not hub:
                    hub = _free_hub(skin, set(hubs.values()))
                if not hub:
                    C.log('Arctic Fuse 3: no free hub for %s' % kind, xbmc.LOGWARNING)
                    state.setdefault('notes', []).append('nohub:%s' % kind)
                    continue
                hubs[kind] = hub
                rows = slots(TAB_OF[kind])
                _replace(skin, hub + 'widgets', _row_widgets(hub, TAB_OF[kind], rows), state)
                menus[hub] = {'node': hub + 'widgets', 'tab': TAB_OF[kind], 'rows': rows}
                values = [('HomeSwitcher.%s.Toggle' % hub, 'true'), ('HomeSwitcher.%s.Name' % hub, name),
                          ('HomeSwitcher.%s.Icon' % hub, _hub_icon(icon))]
                spot = _spot_url(TAB_OF[kind], hub)
                if spot:
                    menus[hub]['spot'] = True
                    values += [('HomeSwitcher.%s.Spotlight.Path' % hub, spot),
                               ('HomeSwitcher.%s.Spotlight.Target' % hub, 'videos'),
                               ('HomeSwitcher.%s.Spotlight.Label' % hub, 'Dex Hub'),
                               ('HomeSwitcher.%s.Spotlight.SortMethod' % hub, 'playlist')]
                _set_strings(state, values)
            elif hub:
                _replace(skin, hub + 'widgets', [], state)
                _restore_strings(state, 'HomeSwitcher.%s.' % hub)
                hubs.pop(kind, None)
    state['hubs'] = hubs
    state['menus'] = menus
    state['labels'] = 2
    publish_titles(state)
    _replace(skin, 'searchwidgets', _search_widgets() if 'search' in places else [], state)
    _replace(skin, 'powermenu', [{'label': 'Dex Hub', 'icon': ICON, 'path': OPEN_HOME, 'target': '',
                                  'guid': 'dexhub.power', MARK: 'true'}] if 'power' in places else [],
             state, top=False)
    state['places'] = [p for p in PLACES if p in places]
    state['sig'] = signature(state['places'])
    state['t'] = time.time()


def signature(places):
    """What the chosen places would show now: the rows (ids, titles, shapes)
    and whether a spotlight exists. A difference means the menus must be
    written again."""
    out = [FORMAT]
    for place in places:
        tab = TAB_OF.get(place)
        if tab:
            out.append([place, bool(C.read_json(C.row_file(tab, 'spot')))] + slots(tab))
    return out


def clear_versions(state):
    for info in (state.get('menus') or {}).values():
        for entry in info.get('rows') or []:
            C.set_prop(G.p_version(info.get('tab'), entry[0]), '')


def _save(state):
    C.write_json(os.path.join(C.folder(), C.AF3_STATE), state)
    C.af3_state(fresh=True)


def _rebuild():
    """The skin builds its hubs from the menus again and reloads."""
    xbmc.executebuiltin('RunScript(script.skinvariables,action=buildtemplate,force)')


# --------------------------------------------------------------------------
# backup and restore
# --------------------------------------------------------------------------

def backup_dir(skin):
    return os.path.join(C.folder('af3_backup'), skin)


def take_backup(state, strip_ours=False):
    """The skin as it is before Dex Hub touches it: its menus, its settings
    file. Taken once per install (an update keeps the first one).
    ``strip_ours``: an install of v5.10.111 (it took no backup): the menus as
    they are without Dex Hub's entries, which is how they were."""
    skin = state['skin']
    if (state.get('backup') or {}).get('t'):
        return
    dest = backup_dir(skin)
    if os.path.exists(dest):
        old = '%s.old' % dest
        shutil.rmtree(old, ignore_errors=True)
        try:
            os.replace(dest, old)
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(os.path.join(dest, 'nodes'), exist_ok=True)
    nodes = {}
    source = _nodes_dir(skin)
    try:
        names = sorted(os.listdir(source))
    except Exception:
        names = []
    for name in names:
        path = os.path.join(source, name)
        if not os.path.isfile(path) or name.endswith('.tmp'):
            continue
        keep = os.path.join(dest, 'nodes', name)
        items = C.read_json(path) if strip_ours else None
        if isinstance(items, list) and [i for i in items if _ours(i)]:
            with open(keep, 'w', encoding='utf-8') as handle:
                json.dump([i for i in items if not _ours(i)], handle, ensure_ascii=False, indent=4)
        else:
            shutil.copyfile(path, keep)
        nodes[name] = _sha1_file(keep)
    settings = _translate('special://profile/addon_data/%s/settings.xml' % skin)
    if os.path.exists(settings):
        shutil.copyfile(settings, os.path.join(dest, 'settings.xml'))
    info = {'t': time.time(), 'skin': skin, 'skin_version': _skin_version(), 'nodes': nodes,
            'dexhub': _addon_version()}
    C.write_json(os.path.join(dest, 'info.json'), info)
    state['backup'] = {'t': info['t'], 'nodes': nodes}
    C.log('Arctic Fuse 3: backup of %s taken (%d menu files)' % (skin, len(nodes)))


def restore_menus(state):
    """Every menu Dex Hub wrote: as it was before the install when nobody
    changed it since, else the user's version without Dex Hub's entries."""
    skin = state.get('skin') or xbmc.getSkinDir()
    dest = os.path.join(backup_dir(skin), 'nodes')
    before = (state.get('backup') or {}).get('nodes') or {}
    written = state.get('written') or {}
    restored, stripped = [], []
    from . import af3hubs
    menus = set(written) | set(['homewidgets', 'homesubmenu', 'searchwidgets', 'powermenu'] +
                               [h + 'widgets' for h in HUBS] +
                               [w + part for w in af3hubs.WINDOWS for part in ('widgets', 'submenu')])
    for menu in sorted(menus):
        name = PREFIX + menu + '.json'
        path = _node_path(skin, menu)
        current = _sha1_file(path)
        if menu in written and current and current == written[menu]:
            if name in before and os.path.exists(os.path.join(dest, name)):
                shutil.copyfile(os.path.join(dest, name), path)
            else:
                try:
                    os.remove(path)     # the skin's own default menu again
                except Exception:
                    pass
            restored.append(menu)
        elif current:
            items = C.read_json(path)
            if isinstance(items, list) and [i for i in items if _ours(i)]:
                _write(skin, menu, [i for i in items if not _ours(i)])
                stripped.append(menu)
        try:
            xbmcgui.Window(10000).clearProperty('SkinVariables.ShortcutsNode.%s-%s' % (skin, name))
        except Exception:
            pass
    state['written'] = {}
    C.log('Arctic Fuse 3: menus restored %s, Dex Hub taken out of %s' % (restored or '-', stripped or '-'))
    return restored, stripped


def _skin_version():
    try:
        import xbmcaddon
        return xbmcaddon.Addon(xbmc.getSkinDir()).getAddonInfo('version') or ''
    except Exception:
        return ''


def _addon_version():
    try:
        import xbmcaddon
        return xbmcaddon.Addon(C.ADDON_ID).getAddonInfo('version') or ''
    except Exception:
        return ''


# --------------------------------------------------------------------------
# install, remove
# --------------------------------------------------------------------------

def _options(counts, patch_state, pages_state='', sections_state=''):
    """[(place, ListItem)] for the choices: what each brings (the label with
    the number of rows, the second line with what comes with them)."""
    def rows(n):
        return (_tr('%d صف') % n) if n != 1 else _tr('صف واحد')
    from . import af3hubs
    entries = []
    if sections_state in ('ready', 'in', 'old'):
        entries += [
            ('dexhub', '%s: %s' % (_tr('قسم Dex Hub'), rows(counts.get('all', 0))),
             _tr('قسم جديد في القائمة العلوية بصفوف واجهة Dex Hub وبانرها وشعارها'), af3hubs.icon('1982')),
            ('movie', '%s: %s' % (_tr('قسم أفلام Dex'), rows(counts.get('movie', 0))),
             _tr('قسم جديد بصفوف الأفلام وبانرها؛ أقسام السكين تبقى كما هي'), af3hubs.icon('1983')),
            ('series', '%s: %s' % (_tr('قسم مسلسلات Dex'), rows(counts.get('series', 0))),
             _tr('قسم جديد بصفوف المسلسلات وبانرها؛ أقسام السكين تبقى كما هي'), af3hubs.icon('1984')),
        ]
    else:
        entries += [
            ('movie', '%s: %s' % (_tr('مركز الأفلام'), rows(counts.get('movie', 0))),
             _tr('مركز جديد في القائمة العلوية بصفوف الأفلام وبانرها'), ICON),
            ('series', '%s: %s' % (_tr('مركز المسلسلات'), rows(counts.get('series', 0))),
             _tr('مركز جديد في القائمة العلوية بصفوف المسلسلات وبانرها'), ICON),
        ]
    entries += [
        ('home', '%s: %s' % (_tr('رئيسية السكين أيضاً'), rows(counts.get('all', 0))),
         _tr('صفوف Dex Hub فوق صفوف رئيسية السكين نفسها (غير مختار عادة)'), ICON),
        ('search', _tr('البحث'), _tr('نتائج Dex Hub داخل بحث السكين بالبوستر والشعار'), ICON),
        ('power', _tr('قائمة الطاقة'), _tr('زر يفتح واجهة Dex Hub الكاملة'), ICON),
    ]
    entries.append(('quiet', _tr('إخفاء القوائم الفارغة'),
                    _tr('صفوف السكين الفارغة تختفي بدل بطاقة لا توجد نتائج (إعداد في السكين نفسه)'), ICON))
    if pages_state in ('ready', 'in', 'old'):
        entries.append(('pages', _tr('صفحات Dex Hub'),
                        _tr('صفحة العمل وصفحات المجلدات داخل السكين، بخطوطه'), ICON))
    if patch_state == 'patched':
        entries.append(('trailers', _tr('التريلر خلف البانر'),
                        _tr('مثبت: تريلر بعد ثوان، حركة هادئة للخلفية، وحركة أغلفة المجموعات'), ICON))
    elif patch_state == 'ready':
        entries.append(('trailers', _tr('التريلر خلف البانر'),
                        _tr('تريلر بعد ثوان، حركة للخلفية، وحركة أغلفة المجموعات؛ يعدل 5 ملفات بعد حفظ نسخها'), ICON))
    out = []
    for place, label, detail, art in entries:
        item = xbmcgui.ListItem(label, detail)
        item.setArt({'icon': art, 'thumb': art})
        out.append((place, item))
    return out


def _sections_choice(chosen, sections_state):
    """An install made before v5.10.120: its rows leave the skin's Home and
    hubs for Dex Hub's own sections (the user's hubs stay theirs)."""
    if sections_state not in ('ready', 'in', 'old'):
        return [p for p in PLACES if p in chosen]
    out = [p for p in chosen if p != 'home']
    if 'home' in chosen or not any(p in chosen for p in SECTIONS):
        out.append('dexhub')
    return [p for p in PLACES if p in out]


def _leave_ours():
    """v5.10.120: Kodi reloads the skin into the window that was on show. A
    window of Dex Hub's (a section, the folder page) taken out while it was
    on show left Kodi with no window at all after the reload ("Unable to
    locate window with id 1982"): nothing drawn and the keys lost until
    Kodi restarted. The Home comes first (the dialogs closed: Kodi opens no
    window under a modal one)."""
    try:
        from . import af3hubs, af3pages
        ours = set(10000 + int(w) for w in af3hubs.WINDOWS)
        ours.add(10000 + af3pages.fold_window())
        if xbmcgui.getCurrentWindowId() not in ours:
            return
        xbmc.executebuiltin('Dialog.Close(all,true)')
        xbmc.executebuiltin('ActivateWindow(home)')
        monitor = xbmc.Monitor()
        deadline = time.time() + 3.0
        while time.time() < deadline and xbmcgui.getCurrentWindowId() in ours:
            if monitor.waitForAbort(0.1):
                return
        C.log("Arctic Fuse 3: back to the Home before Dex Hub's windows go")
    except Exception:
        pass


def sections_follow(state, places):
    """Dex Hub's sections in or out of the skin as ``places`` asks
    (af3hubs.py). Returns notes for the user."""
    from . import af3hubs
    skin = state['skin']
    record = state.setdefault('sections', {})
    notes = []
    want = any(p in places for p in SECTIONS)
    try:
        status = af3hubs.status()[0]
        if want:
            if status == 'old':
                af3hubs.revert(backup_dir(skin), record)
                record = state.setdefault('sections', {})
                status = af3hubs.status()[0]
            if status == 'ready':
                af3hubs.apply(backup_dir(skin), record)
        elif record.get('files') or record.get('added'):
            _leave_ours()
            af3hubs.revert(backup_dir(skin), record)
    except af3hubs.PatchError as exc:
        C.log('Arctic Fuse 3: Dex Hub sections not put in: %s' % exc, xbmc.LOGWARNING)
        if want:
            notes.append(_tr('أقسام Dex Hub: هذه النسخة من السكين مختلفة ولا تناسبها'))
    return notes


def pages_follow(state, places):
    """Dex Hub's pages in or out of the skin as ``places`` asks
    (af3pages.py). Returns notes for the user."""
    from . import af3pages
    skin = state['skin']
    pages = state.setdefault('pages', {})
    notes = []
    try:
        status = af3pages.status()[0]
        if 'pages' in places:
            if status == 'old':
                # an older version of the pages out first (the skin's own files back), then this one
                af3pages.revert(backup_dir(skin), pages)
                pages = state.setdefault('pages', {})
                status = af3pages.status()[0]
            if status == 'ready':
                if af3pages.apply(backup_dir(skin), pages):
                    notes.append(_tr('صفحات Dex Hub: أجزاء من هذه النسخة من السكين مختلفة فتُركت كما هي'))
        elif pages.get('files') or pages.get('added'):
            _leave_ours()
            af3pages.revert(backup_dir(skin), pages)
    except af3pages.PatchError as exc:
        C.log('Arctic Fuse 3: Dex Hub pages not put in: %s' % exc, xbmc.LOGWARNING)
        if 'pages' in places:
            notes.append(_tr('صفحات Dex Hub: هذه النسخة من السكين مختلفة ولا تناسبها'))
    return notes


def pages_publish():
    """What the pages read from Dex Hub: their labels in its language, the
    theme's colours."""
    try:
        from . import strings
        strings.publish(force=True)
    except Exception:
        C.log('Arctic Fuse 3 pages labels:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    try:
        from .. import skin_theme
        skin_theme.publish_theme()
    except Exception:
        C.log('Arctic Fuse 3 pages theme:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)


def install(interactive=True, places=None):
    if not supported():
        if interactive:
            xbmcgui.Dialog().ok('Dex Hub', _tr('هذا الخيار يعمل مع Arctic Fuse 3: اختره سكيناً أولاً ثم ارجع هنا.'))
        return False
    from . import af3patch
    from . import af3pages
    skin = xbmc.getSkinDir()
    state = dict(C.af3_state(fresh=True))
    if state.get('skin') and state.get('skin') != skin:
        state = {}
    from . import af3hubs
    patch_state = af3patch.status()[0]
    pages_state = af3pages.status()[0]
    sections_state = af3hubs.status()[0]
    if state.get('installed') and state.get('by') == FORMAT:
        chosen = list(state.get('places') or [])
        if not state.get('pages_offered') and pages_state in ('ready', 'in', 'old'):
            chosen.append('pages')      # new in v5.10.119: ticked once
        if not state.get('sections_offered'):
            chosen = _sections_choice(chosen, sections_state)
        if not state.get('quiet_offered') and 'quiet' not in chosen:
            chosen.append('quiet')      # new in v5.10.120: ticked once
    else:
        # a first install, or one made by v5.10.111: everything offered is ticked
        chosen = [p for p in PLACES if (p != 'trailers' or patch_state in ('ready', 'patched'))
                  and (p != 'pages' or pages_state in ('ready', 'in', 'old'))
                  and (p != 'dexhub' or sections_state in ('ready', 'in', 'old'))
                  and p not in DEFAULT_OFF]
    if interactive:
        counts = dict((tab, len(slots(tab))) for tab in ('all', 'movie', 'series'))
        options = _options(counts, patch_state, pages_state, sections_state)
        picked = xbmcgui.Dialog().multiselect(_tr('Dex Hub داخل Arctic Fuse 3: اختر ما ينتقل'),
                                              [item for _place, item in options],
                                              preselect=[i for i, (place, _item) in enumerate(options)
                                                         if place in chosen],
                                              useDetails=True)
        if picked is None:
            return False
        places = [options[i][0] for i in picked]
    places = list(places if places is not None else chosen)
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    notes = []
    try:
        from . import rows
        rows.publish(reason='af3')
        state.update({'installed': bool(places), 'skin': skin, 'skin_version': _skin_version(), 'notes': [],
                      'by': FORMAT})
        take_backup(state)
        # the sections first: the menus then go to Dex Hub's own hubs
        notes += sections_follow(state, places)
        state['sections_offered'] = True
        _apply(state, places)
        quiet_follow(state, places)
        state['quiet_offered'] = True
        patch = state.setdefault('patch', {})
        if 'trailers' in places and af3patch.status()[0] == 'ready':
            try:
                af3patch.apply(backup_dir(skin), patch)
            except af3patch.PatchError as exc:
                C.log('Arctic Fuse 3: trailer patch not applied: %s' % exc, xbmc.LOGWARNING)
                notes.append(_tr('التريلر: هذه النسخة من السكين مختلفة ولا يناسبها التعديل'))
        elif 'trailers' not in places and patch.get('files'):
            af3patch.revert(backup_dir(skin), patch)
        notes += pages_follow(state, places)
        state['pages_offered'] = True
        notes += [_tr('لا يوجد مركز فارغ للأفلام') if n == 'nohub:movie' else _tr('لا يوجد مركز فارغ للمسلسلات')
                  for n in state.pop('notes', []) if n.startswith('nohub:')]
        _save(state)
    except Exception:
        C.log('Arctic Fuse 3 install failed:\n%s' % traceback.format_exc(), xbmc.LOGERROR)
        if interactive:
            xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
            xbmcgui.Dialog().ok('Dex Hub', _tr('تعذر تثبيت Dex Hub في السكين.'))
        return False
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    menus = state.get('menus') or {}
    C.log('Arctic Fuse 3: Dex Hub in %s (hubs %s, rows %s, trailers %s, pages %s)' % (
        ', '.join(places) or 'nothing', state.get('hubs'),
        dict((m, len(i.get('rows') or [])) for m, i in menus.items()),
        bool((state.get('patch') or {}).get('files')), bool((state.get('pages') or {}).get('added'))))
    pages_publish()
    if interactive:
        total = sum(len(i.get('rows') or []) for i in menus.values())
        message = _tr('انتقل %d صف، ويعاد بناء السكين الآن') % total
        if notes:
            xbmcgui.Dialog().ok('Dex Hub', '[CR]'.join([message] + notes))
        else:
            xbmcgui.Dialog().notification('Dex Hub', message, ICON, 3500, False)
    _rebuild()
    return True


def remove(interactive=True):
    from . import af3patch
    state = dict(C.af3_state(fresh=True))
    skin = state.get('skin') or xbmc.getSkinDir()
    if xbmc.getSkinDir() != skin:
        if interactive:
            xbmcgui.Dialog().ok('Dex Hub', _tr('اختر Arctic Fuse 3 سكيناً أولاً، ثم احذف منه Dex Hub.'))
        return False
    if interactive and not xbmcgui.Dialog().yesno(
            'Dex Hub', _tr('إزالة Dex Hub من Arctic Fuse 3 وإرجاع السكين كما كان قبل التثبيت؟')):
        return False
    _leave_ours()
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        restore_menus(state)
        _restore_strings(state)
        _restore_bools(state)
        if (state.get('patch') or {}).get('files'):
            af3patch.revert(backup_dir(skin), state['patch'])
        if (state.get('pages') or {}).get('files') or (state.get('pages') or {}).get('added'):
            from . import af3pages
            af3pages.revert(backup_dir(skin), state['pages'])
        if (state.get('sections') or {}).get('files') or (state.get('sections') or {}).get('added'):
            from . import af3hubs
            af3hubs.revert(backup_dir(skin), state['sections'])
        clear_versions(state)
        # the backup stays one more round (as <skin>.restored), for the record
        dest = backup_dir(skin)
        if os.path.exists(dest):
            done = dest + '.restored'
            shutil.rmtree(done, ignore_errors=True)
            try:
                os.replace(dest, done)
            except Exception:
                pass
        _save({'installed': False, 'skin': skin, 't': time.time()})
    except Exception:
        C.log('Arctic Fuse 3 remove failed:\n%s' % traceback.format_exc(), xbmc.LOGERROR)
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
        if interactive:
            xbmcgui.Dialog().ok('Dex Hub', _tr('تعذرت إزالة Dex Hub من السكين.'))
        return False
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    C.log('Arctic Fuse 3: Dex Hub removed, the skin is as it was')
    if interactive:
        xbmcgui.Dialog().notification('Dex Hub', _tr('عاد السكين كما كان، ويعاد بناؤه الآن'), ICON, 3500, False)
    _rebuild()
    return True


def run(params, handle):
    """skin_af3: op=install (also updates) or op=remove, from Dex Hub's settings."""
    if handle >= 0:
        try:
            import xbmcplugin
            xbmcplugin.endOfDirectory(handle, succeeded=False, cacheToDisc=False)
        except Exception:
            pass
    if (params.get('op') or 'install') == 'remove':
        return remove()
    return install()


# --------------------------------------------------------------------------
# the service side: gates, following Dex Hub, the patch after a skin update
# --------------------------------------------------------------------------

def _quiet():
    """A moment the skin may be rebuilt (it reloads): its Home or a hub on
    show with nothing over it, nothing playing, no key for a few seconds."""
    cond = xbmc.getCondVisibility
    if C.prop('dexhub.skinswitch.busy'):
        return False                # Kodi's skin is being changed (switch.py)
    if cond('Player.HasMedia') or C.prop('dexhub.trailer.active'):
        return False
    try:
        if time.time() - float(C.prop('dexhub.homeui.alive') or 0) < 5.0:
            return False
    except (ValueError, TypeError):
        return False
    if cond('System.HasActiveModalDialog') or cond('Window.IsVisible(DialogBusy.xml)'):
        return False
    from .af3hubs import WINDOWS
    if not (cond('Window.IsActive(Home)') or any(cond('Window.IsActive(%s)' % hub) for hub in HUBS + WINDOWS)):
        return False
    return bool(cond('System.IdleTime(%d)' % QUIET_IDLE))


class Versions(object):
    """A row whose titles changed is asked for again: its widget's version
    follows the row's real one (rows.bump), one row at a time, never while a
    Dex Hub widget call runs (gates.py)."""

    SPACING = 0.8           # seconds between two rows asked for again

    def __init__(self):
        self.last = 0.0

    def tick(self, state):
        now = time.time()
        if now - self.last < self.SPACING or G.busy(now):
            return
        for info in (state.get('menus') or {}).values():
            tab = info.get('tab')
            for entry in info.get('rows') or []:
                real = C.prop('dhs.v.%s.%s' % (tab, entry[0]))
                if real and C.prop(G.p_version(tab, entry[0])) != real:
                    C.set_prop(G.p_version(tab, entry[0]), real)
                    self.last = now
                    return


class Service(threading.Thread):
    """The worker's Arctic Fuse 3 side (worker.py, 'af3' mode): a light
    thread of its own, so the rows of a hub on show load in turn even while
    the worker reads rows from the network."""

    TICK = 0.3

    SKIN_EVERY = 20.0       # seconds between two looks at the skin's files (an update brings its own back)

    def __init__(self, monitor):
        super(Service, self).__init__(name='dexhub-af3')
        self.daemon = True
        self.monitor = monitor
        self.halt = threading.Event()
        self.versions = Versions()
        self.next_check = 0.0
        self.next_skin = 0.0
        self.skin_seen = None
        self.files_key = None
        self.pending = 0.0
        self.trailers = None
        self.ratings = None
        self.player = xbmc.Player()
        self.relabel_due = False

    def run(self):
        try:
            self.prepare()
        except Exception:
            C.log('Arctic Fuse 3:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        while not self.halt.is_set() and not self.monitor.abortRequested():
            self.tick()
            if self.monitor.waitForAbort(self.TICK):
                break
        if self.trailers is not None:
            self.trailers.stop()
            self.trailers = None
        if self.ratings is not None:
            self.ratings.stop()
            self.ratings = None

    def prepare(self):
        state = dict(C.af3_state(fresh=True))
        changed = False
        if not (state.get('backup') or {}).get('t'):
            # installed by v5.10.111 (no backup then): its menus without Dex Hub's entries
            try:
                take_backup(state, strip_ours=True)
                changed = True
            except Exception:
                C.log('Arctic Fuse 3 backup:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        if state.get('installed') and not state.get('pages_offered'):
            # v5.10.119: Dex Hub's pages, once, for an install made before them
            from . import af3pages
            state['pages_offered'] = True
            changed = True
            if af3pages.status()[0] in ('ready', 'old') and 'pages' not in (state.get('places') or []):
                state['places'] = [p for p in PLACES if p in (state.get('places') or []) or p == 'pages']
                state['repatch'] = True
                C.log('Arctic Fuse 3: Dex Hub pages go in at the next quiet moment')
        if state.get('installed') and not state.get('sections_offered'):
            # v5.10.120: Dex Hub's own sections, once, for an install made before them
            from . import af3hubs
            state['sections_offered'] = True
            changed = True
            places = _sections_choice(state.get('places') or [], af3hubs.status()[0])
            if places != (state.get('places') or []):
                state['places'] = places
                state['repatch'] = True
                C.log('Arctic Fuse 3: Dex Hub moves into sections of its own at the next quiet moment (%s)'
                      % ', '.join(places))
        if state.get('installed') and not state.get('quiet_offered'):
            # v5.10.120: the skin's empty widgets hidden, once, for an install made before
            state['quiet_offered'] = True
            changed = True
            if 'quiet' not in (state.get('places') or []):
                state['places'] = [p for p in PLACES if p in (state.get('places') or []) or p == 'quiet']
                state['repatch'] = True
                C.log("Arctic Fuse 3: the skin's empty widgets are hidden at the next quiet moment")
        if state.get('installed'):
            publish_titles(state)
            if state.get('labels') != 2:
                self.relabel_due = True
        if changed:
            _save(state)
        self.skin_seen = self.skin_files()
        self.check_patch(state)
        self.check_pages(state)
        self.check_sections(state)
        if (state.get('pages') or {}).get('added'):
            pages_publish()
        self.trailers_follow(state)
        self.ratings_follow(state)

    @staticmethod
    def pages_in(state):
        return 'pages' in (state.get('places') or []) and bool((state.get('pages') or {}).get('added'))

    def trailers_follow(self, state):
        """The trailer thread runs while the trailer patch is in and chosen
        (the hubs), or Dex Hub's pages are in (a folder page plays the
        focused title's trailer behind its rows)."""
        hubs = 'trailers' in (state.get('places') or []) and bool((state.get('patch') or {}).get('files'))
        fold = self.pages_in(state)
        want = hubs or fold
        if want and self.trailers is None:
            try:
                from .spotlight import AF3Spotlight
                self.trailers = AF3Spotlight(self.monitor, hubs=hubs, fold=fold)
                self.trailers.start()
                C.log('Arctic Fuse 3: trailers on (hubs %s, folder pages %s)' % (hubs, fold))
            except Exception:
                self.trailers = None
                C.log('Arctic Fuse 3 trailers off:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        elif not want and self.trailers is not None:
            self.trailers.stop()
            self.trailers = None
            C.log('Arctic Fuse 3: trailers off')
        elif self.trailers is not None:
            self.trailers.hubs, self.trailers.fold = hubs, fold

    def ratings_follow(self, state):
        """The ratings with their logos for the title Dex Hub's pages show
        large (herofocus.py), while the pages are in."""
        want = self.pages_in(state)
        if want and self.ratings is None:
            try:
                from .herofocus import HeroFocus
                from . import af3pages
                self.ratings = HeroFocus(self.monitor, self.watching, fold_window=af3pages.fold_window(),
                                         home=False)
                self.ratings.start()
            except Exception:
                self.ratings = None
                C.log('Arctic Fuse 3 ratings off:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        elif not want and self.ratings is not None:
            self.ratings.stop()
            self.ratings = None

    def watching(self):
        try:
            return self.player.isPlayingVideo()
        except Exception:
            return False

    def stop(self):
        self.halt.set()

    def tick(self):
        state = C.af3_state()
        if not state.get('installed'):
            return
        if self.relabel_due and _quiet():
            # v5.10.168: the titles of an older install, once, then the skin rebuilds
            self.relabel_due = False
            try:
                state = dict(C.af3_state(fresh=True))
                changed = relabel(state)
                state['labels'] = 2
                _save(state)
                if changed:
                    C.log('Arctic Fuse 3: row titles written again (Arabic read right), the skin rebuilds')
                    _rebuild()
            except Exception:
                C.log('Arctic Fuse 3 titles:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
        try:
            self.versions.tick(state)
        except Exception:
            C.log('Arctic Fuse 3 versions:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        now = time.time()
        if now >= self.next_skin:
            self.next_skin = now + self.SKIN_EVERY
            try:
                seen = self.skin_files()
                if seen != self.skin_seen:
                    # the skin's files changed (an update, a reinstall): the patches go in again
                    self.skin_seen = seen
                    state = dict(C.af3_state(fresh=True))
                    self.check_patch(state)
                    self.check_pages(state)
                    self.check_sections(state)
            except Exception:
                C.log('Arctic Fuse 3 skin check:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        if now < self.next_check:
            return
        self.next_check = now + CHECK_EVERY
        try:
            self.follow(state)
            self.spotlights(state)
            self.trailers_follow(state)
            self.ratings_follow(state)
        except Exception:
            C.log('Arctic Fuse 3 sync:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    @staticmethod
    def skin_files():
        """What the skin's Includes.xml is now (a skin update replaces it)."""
        try:
            info = os.stat(os.path.join(_translate('special://skin/'), '1080i', 'Includes.xml'))
            return (info.st_ino, info.st_mtime_ns, info.st_size)
        except Exception:
            return None

    def follow(self, state):
        """Dex Hub's rows changed (added, removed, renamed, moved, back from
        empty): the menus are written again and the skin rebuilt, at a quiet
        moment."""
        places = state.get('places') or []
        repatch = bool(state.get('repatch'))
        if not repatch and signature(places) == state.get('sig'):
            self.pending = 0.0
            return
        if not self.pending:
            self.pending = time.time()
            C.log('Arctic Fuse 3: Dex Hub changed, the skin follows at the next quiet moment')
        # Layout changes stay pending by default. The existing Install/Update
        # action applies them explicitly; widget contents still refresh normally.
        try:
            import xbmcaddon
            auto = xbmcaddon.Addon(C.ADDON_ID).getSetting('af3_auto_layout') == 'true'
        except Exception:
            auto = False
        if not auto:
            # v5.10.130: Dex Hub's own files in the skin still follow it
            self.files_follow(places)
            return
        if not _quiet():
            return
        state = dict(C.af3_state(fresh=True))
        if repatch:
            self.repatch(state)
            places = state.get('places') or []
        _apply(state, places)
        _save(state)
        if repatch and self.pages_in(state):
            pages_publish()
        self.pending = 0.0
        C.log('Arctic Fuse 3: menus written again (%s), the skin rebuilds' % ', '.join(
            '%s %d' % (m, len(i.get('rows') or [])) for m, i in (state.get('menus') or {}).items()))
        _rebuild()

    def files_follow(self, places):
        """v5.10.130: Dex Hub's own files in the skin (its title and folder
        pages, the trailer patch) follow a Dex Hub update, or come back after
        a skin update, by themselves: once, at a quiet moment, and the skin
        reloads (no rebuild of its hubs). With the automatic layout off (the
        default since v5.10.12x) a fix in those files never reached the
        screen without Install/Update. The layout (menus, sections, empty
        widgets) still waits for the setting or Install/Update.

        Looked at again only when the skin's files or the choice change (a
        stat, every few seconds), tried once per Dex Hub and skin version: a
        part that cannot go in is not tried again at every quiet moment."""
        from . import af3patch, af3pages
        mine = tuple(p for p in ('pages', 'trailers') if p in places)
        key = (self.skin_files(), mine)
        if not mine or key == self.files_key:
            return False
        state = dict(C.af3_state(fresh=True))
        due = []
        try:
            if 'trailers' in mine and (state.get('patch') or {}).get('files') \
                    and af3patch.status()[0] in ('ready', 'old'):
                due.append('trailers')
            if 'pages' in mine and af3pages.status()[0] in ('ready', 'old'):
                due.append('pages')
        except Exception:
            C.log('Arctic Fuse 3 files:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            due = []
        tried = '%s|%s|%s' % (af3pages.marker() or '', af3patch.MARKER, key[0])
        if not due or state.get('files_tried') == tried:
            self.files_key = key
            return False
        if not _quiet():
            return False                # looked at again at the next check
        self.files_key = key
        state['files_tried'] = tried
        if 'trailers' in due:
            patch = state.setdefault('patch', {})
            try:
                if af3patch.status()[0] == 'old':
                    # the older patch out first (its originals come back), then this one
                    af3patch.revert(backup_dir(state['skin']), patch)
                    patch = state.setdefault('patch', {})
                if af3patch.status()[0] == 'ready':
                    af3patch.apply(backup_dir(state['skin']), patch)
            except af3patch.PatchError as exc:
                C.log('Arctic Fuse 3: trailer patch not applied again: %s' % exc, xbmc.LOGWARNING)
        if 'pages' in due:
            pages_follow(state, list(places))
        _save(state)
        done = [part for part, status in (('trailers', af3patch.status), ('pages', af3pages.status))
                if part in due and status()[0] in ('in', 'patched')]
        if not done:
            C.log('Arctic Fuse 3: Dex Hub files not brought up to date (%s)' % ', '.join(due), xbmc.LOGWARNING)
            return False
        if 'pages' in done and self.pages_in(state):
            pages_publish()
        C.log('Arctic Fuse 3: Dex Hub files brought up to date (%s), the skin reloads' % ', '.join(done))
        xbmc.executebuiltin('ReloadSkin()')
        return True

    def spotlights(self, state):
        """A spotlight whose picks changed gets its new path (the skin reads
        it again)."""
        for menu, info in (state.get('menus') or {}).items():
            if not info.get('spot'):
                continue
            key = 'HomeSwitcher.%s.Spotlight.Path' % ('Home' if menu == 'home' else menu)
            want = _spot_url(info.get('tab'), menu)
            have = _skin_string(key)
            if want and have != want and 'id=spot' in have and 'af3=' in have:
                xbmc.executebuiltin('Skin.SetString(%s,%s)' % (key, want))

    def check_pages(self, state):
        """Dex Hub's pages chosen but not in the skin (an update brought the
        skin's own files back, or an older version of them is in): they go in
        at the next quiet moment."""
        if 'pages' not in (state.get('places') or []) or state.get('repatch'):
            return
        from . import af3pages
        status = af3pages.status()[0]
        if status in ('ready', 'old'):
            state['repatch'] = True
            _save(state)
            C.log('Arctic Fuse 3: Dex Hub pages go in again (%s)' % status)

    def check_sections(self, state):
        """Dex Hub's sections chosen but not in the skin (an update brought
        the skin's own files back): they go in at the next quiet moment."""
        if not any(p in (state.get('places') or []) for p in SECTIONS) or state.get('repatch'):
            return
        from . import af3hubs
        status = af3hubs.status()[0]
        if status in ('ready', 'old'):
            state['repatch'] = True
            _save(state)
            C.log('Arctic Fuse 3: Dex Hub sections go in again (%s)' % status)

    def check_patch(self, state):
        """A skin updated since the trailer patch went in brought its files
        back: the patch goes in again at the next quiet moment."""
        if 'trailers' not in (state.get('places') or []):
            return
        from . import af3patch
        status = af3patch.status()[0]
        if status in ('ready', 'old') and (state.get('patch') or {}).get('files'):
            # a skin update brought its files back, or an older patch is in
            # (v5.10.114 adds the collections' animated covers): in again
            state['repatch'] = True
            _save(state)
            C.log('Arctic Fuse 3: the trailer patch goes in again (%s)' % status)

    @staticmethod
    def repatch(state):
        from . import af3patch
        state.pop('repatch', None)
        sections_follow(state, state.get('places') or [])
        quiet_follow(state, state.get('places') or [])
        patch = state.setdefault('patch', {})
        try:
            if 'trailers' not in (state.get('places') or []):
                pass                    # the pages alone asked for it (check_pages)
            elif af3patch.status()[0] == 'old':
                # the older patch out first (its originals come back), then this one
                af3patch.revert(backup_dir(state['skin']), patch)
                patch = state.setdefault('patch', {})
            if 'trailers' in (state.get('places') or []) and af3patch.status()[0] == 'ready':
                af3patch.apply(backup_dir(state['skin']), patch)
        except af3patch.PatchError as exc:
            C.log('Arctic Fuse 3: trailer patch not applied again: %s' % exc, xbmc.LOGWARNING)
        pages_follow(state, state.get('places') or [])
