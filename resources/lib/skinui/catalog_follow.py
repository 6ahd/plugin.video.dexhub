# -*- coding: utf-8 -*-
"""Focus-following Home rows with cancellable requests and reversible layout.

Base specifications stay on disk unchanged. Dependent rows are temporary
insertions; their cached contents exist before their paths reach the skin.
"""
import threading
import time
from . import common as C
from .. import profile_scope

_LOCK = threading.RLock()
_TREES = {}
_MAX_DEPTH = 6


class Tree:
    def __init__(self):
        self.children = {}
        self.versions = {}

    def remove(self, parent):
        old = self.children.pop(parent, None)
        if old:
            self.remove(old['id'])
        self.versions[parent] = self.versions.get(parent, 0) + 1

    def begin(self, parent, spec):
        self.remove(parent)
        token = self.versions[parent]
        self.children[parent] = spec
        return token

    def valid(self, parent, token):
        return self.versions.get(parent) == token and parent in self.children

    def compose(self, base):
        out, seen = [], set()
        def add(spec, depth=0):
            if spec['id'] in seen:
                return
            seen.add(spec['id']);out.append(spec)
            child = self.children.get(spec['id'])
            if child and depth < _MAX_DEPTH:
                add(child, depth + 1)
        for spec in base:
            add(spec)
        return out[:C.ROWS]


def _tree(tab):
    return _TREES.setdefault((profile_scope.key(), tab), Tree())


def compose(tab, base):
    from ..ui_preferences import enabled
    if not enabled('homeui_follow_rows'):
        return base
    with _LOCK:
        tree = _tree(tab)
        from ..row_follow import filter_target
        for parent, child in list(tree.children.items()):
            if not filter_target(tab, child.get('_follow_key') or parent, child['params'].get('target') or ''):
                tree.remove(parent)
        return tree.compose(base)


def virtuals(tab):
    with _LOCK:
        return list(_tree(tab).children.values())


def spec(tab, row_id):
    for found in virtuals(tab):
        if found['id'] == row_id:
            return found
    # A row may be read by a different Kodi interpreter.
    data = C.read_json(C.folder('follow') + '/%s.json' % tab, {}) or {}
    return (data.get('rows') or {}).get(row_id)


def _save_specs(tab):
    rows = {s['id']: s for s in virtuals(tab)}
    C.write_json(C.folder('follow') + '/%s.json' % tab, {'rows': rows, 't': time.time()})


def _publish(tab):
    from . import board
    _save_specs(tab)
    board._publish_tab(tab, board.load_specs(tab))


def _make(tab, parent, target, title):
    from .board import find_spec
    source = find_spec(tab, parent) or {}
    return {'id': C.short_id('follow|' + parent + '|' + target), 'key': 'follow:' + parent,
            'tab': tab, 'title': title, 'sub': '↳', 'shape': 'landscape', 'kind': 'catalog',
            'params': {'action': 'skin_follow', 'target': target}, 'own': False, 'progress': False,
            'auto_shape': True, 'profile_scope': profile_scope.key(), '_follow_parent': parent, '_follow_key': source.get('key') or parent}


class Controller(threading.Thread):
    def __init__(self, monitor, busy):
        super().__init__(name='DexHubCatalogFollow', daemon=True)
        self.monitor, self.busy = monitor, busy
        self.done = threading.Event()
        self.pending = None
        self.last = None
        self.job = None
        self._scope = profile_scope.key()

    def stop(self):
        self.done.set()

    def snapshot(self):
        import xbmc
        if (self.busy() or not xbmc.getCondVisibility('Window.IsActive(home)') or
                xbmc.getSkinDir() not in ('skin.dexhub', 'skin.dexhub.tvos')):
            return None
        tab = C.prop('dhs.tab') or 'all'
        try:
            slot = int(C.prop('dhs.fr') or 0)
        except ValueError:
            return None
        if tab not in C.TABS or not 1 <= slot < C.ROWS:
            return None
        cid = C.layout().row_list(slot)
        if not xbmc.getCondVisibility('Control.HasFocus(%d)' % cid):
            return None
        prefix = 'Container(%d).ListItem.' % cid
        parent = C.prop('dhs.%s.%d.id' % (tab, slot))
        from .board import find_spec
        from ..row_follow import filter_target
        key = (find_spec(tab, parent) or {}).get('key') or parent
        return (tab, parent, filter_target(tab, key, xbmc.getInfoLabel(prefix + 'Property(dhs.follow)')),
                xbmc.getInfoLabel(prefix + 'Label'))

    def run(self):
        with profile_scope.bind(self._scope):
            previous, since = None, 0.0
            while not self.done.is_set() and not self.monitor.abortRequested() and profile_scope.is_current():
                from ..ui_preferences import enabled
                if not enabled('homeui_follow_rows'):
                    with _LOCK:
                        tabs = [t for scope, t in _TREES if scope == self._scope and _TREES[(scope, t)].children]
                        for tab in tabs:
                            _tree(tab).children.clear()
                            _publish(tab)
                    previous = self.last = None
                else:
                    current = self.snapshot()
                    if current != previous:
                        previous, since = current, time.monotonic()
                    if current and current != self.last and time.monotonic() - since >= 0.4:
                        self.last = current
                        self.request(*current)
                if not self.busy():
                    try:
                        from .focus_metadata import publish
                        publish()
                    except Exception as exc:
                        C.log('focused metadata: %s' % exc.__class__.__name__)
                if self.done.wait(0.15):
                    break

    def request(self, tab, parent, target, title):
        if not parent:
            return
        from . import rows
        from .board import find_spec
        from ..row_follow import filter_target
        parent_key = (find_spec(tab, parent) or {}).get('key') or parent
        target = filter_target(tab, parent_key, target)
        with _LOCK:
            tree = _tree(tab)
            if not target:
                if parent in tree.children:
                    tree.remove(parent);_publish(tab)
                return
            # Bound depth and reject a link back to an ancestor's catalog.
            depth, walk = 0, parent
            while True:
                ancestor = next((p for p, s in tree.children.items() if s['id'] == walk), None)
                if not ancestor:
                    break
                if tree.children[ancestor]['params'].get('target') == target:
                    return
                depth += 1;walk = ancestor
                if depth >= _MAX_DEPTH:
                    return
            existing = tree.children.get(parent)
            if existing and existing['params'].get('target') == target:
                return
            dependent = _make(tab, parent, target, title)
            from ..i18n import is_english
            note = {'kind': 'note', 'title': 'Loading catalog…' if is_english() else 'جارٍ تحميل الكتالوج…',
                    'path': '', 'poster': '', 'folder': False}
            rows._store(tab, dependent, [note], None, enriched=True)
            token = tree.begin(parent, dependent)
            _publish(tab)
        # One active lookup and one newest pending target; no scrolling backlog.
        with _LOCK:
            self.pending = (tab, parent, target, dependent, token)
            if self.job is None:
                self.job = threading.Thread(target=profile_scope.capture(self._load), name='DexHubFollowLoad', daemon=True)
                self.job.start()

    def _load(self):
        from . import rows
        from ..catalog_follow import load_target, child_shape
        from ..row_follow import filter_target
        while not self.done.is_set():
            with _LOCK:
                if self.pending is None:
                    self.job = None
                    return
                tab, parent, target, dependent, token = self.pending
                self.pending = None
            try:
                tiles = load_target(target, 'series' if tab == 'series' else 'movie')
            except Exception as exc:
                C.log('dependent catalog failed: %s' % exc.__class__.__name__)
                tiles = []
            from ..ui_preferences import enabled
            with _LOCK:
                tree = _tree(tab)
                if (self.done.is_set() or not enabled('homeui_follow_rows') or
                        not profile_scope.is_current() or not tree.valid(parent, token) or
                        not filter_target(tab, dependent.get('_follow_key') or parent, target)):
                    continue
                if tiles:
                    dependent['shape'] = child_shape(tiles)
                    rows._store(tab, dependent, tiles, None, enriched=False)
                    rows.bump(tab, dependent['id'], dependent)
                else:
                    from . import board
                    old_slot = board.slot_of(tab, dependent['id'])
                    parent_slot = board.slot_of(tab, parent)
                    tree.remove(parent)
                _publish(tab)
                if not tiles and old_slot and parent_slot:
                    import xbmc
                    if (C.prop('dhs.tab') == tab and xbmc.getCondVisibility('Window.IsActive(home)')
                            and xbmc.getCondVisibility('Control.HasFocus(%d)' % C.layout().row_list(old_slot))):
                        xbmc.executebuiltin('SetFocus(%d)' % C.layout().row_list(parent_slot))
