# -*- coding: utf-8 -*-
"""Dependent rows in the portable Home, using its scheduler and task lanes."""
from .. import profile_scope


def update(window, tile):
    from ..ui_preferences import enabled
    from ..catalog_follow import target_for, load_target, child_shape
    from ..row_follow import filter_target
    from .rows import Row
    if window.zone != 'rows' or not (0 <= window.ri < len(window.rows)):
        return
    parent = window.rows[window.ri]
    follow = getattr(window, '_follow_children', None)
    if follow is None:
        follow = window._follow_children = {}
        window._follow_versions = {}
    versions = window._follow_versions
    def restored_index(active, ordered):
        by_key = {row.key: row for row in window.rows}
        visible = {row.key for row in ordered}
        wanted, visited = active.key, set()
        while wanted not in visible and wanted not in visited:
            visited.add(wanted)
            old = by_key.get(wanted)
            wanted = (old.meta.get('_follow_parent') if old else '') or ''
        return next((i for i, row in enumerate(ordered) if row.key == wanted),
                    min(window.ri, max(0, len(ordered) - 1)))
    if not enabled('homeui_follow_rows'):
        if follow:
            follow.clear()
            with window._lock:
                active = window.rows[window.ri] if 0 <= window.ri < len(window.rows) else parent
                ordered = [r for r in window.rows if not r.meta.get('_follow_parent')]
                window.ri = restored_index(active, ordered)
                window.rows = ordered
                window._render_rows()
        return
    target = target_for(tile, include_works=True)
    # Nuvio collection folders need their resolved grid route.
    if tile.get('kind') == 'folder' and not tile.get('catalog_node'):
        from ..skinui.rows import Context
        click, destination = Context('all').click(tile, {})
        target = target_for(tile, click, destination, include_works=True)
    target = filter_target(window.media, parent.key, target)
    previous = follow.get(parent.key)
    unchanged = bool(previous and previous.meta.get('_follow_target') == target)

    def remove(key):
        old = follow.pop(key, None)
        if old:
            remove(old.key)
        versions[key] = versions.get(key, 0) + 1
    pruned = False
    # A preference changed from the layout editor or another interpreter.
    for key, child in list(follow.items()):
        if not filter_target(window.media, key, child.meta.get('_follow_target') or ''):
            remove(key)
            pruned = True
    if unchanged and not pruned:
        return
    if not unchanged:
        remove(parent.key)
    token = versions.get(parent.key, 0)
    def compose():
        base = [r for r in window.rows if not r.meta.get('_follow_parent')]
        ordered = []
        def append(row, depth=0):
            ordered.append(row)
            if row.key in follow and depth < 6:
                append(follow[row.key], depth + 1)
        for row in base:
            append(row)
        return ordered
    def apply_rows():
        active = window.rows[window.ri] if 0 <= window.ri < len(window.rows) else parent
        ordered = compose()
        window.ri = restored_index(active, ordered)
        window.rows = ordered
        window._render_rows()
    if unchanged or not target or not any(row.key == parent.key for row in compose()):
        with window._lock:
            apply_rows()
        return
    # Do not recurse into the same catalog or past six levels.
    ancestor, depth = parent, 0
    while ancestor.meta.get('_follow_parent'):
        depth += 1
        if depth >= 6 or ancestor.meta.get('_follow_target') == target:
            return
        ancestor = next((r for r in window.rows if r.key == ancestor.meta['_follow_parent']), parent)
        if ancestor is parent:
            break
    child = Row('follow:' + parent.key, tile.get('title') or tile.get('label') or '', shape='landscape',
                tiles=[{'kind': 'note', 'title': window.tr('جارٍ تحميل الكتالوج…')}],
                meta={'_follow_parent': parent.key, '_follow_target': target})
    follow[parent.key] = child
    with window._lock:
        apply_rows()
    def load():
        if window._closing or versions.get(parent.key) != token:
            return
        try:
            found = load_target(target, window.media)
        except Exception:
            found = []
        if (window._closing or not enabled('homeui_follow_rows') or versions.get(parent.key) != token
                or not profile_scope.is_current() or not filter_target(window.media, parent.key, target)):
            return
        with window._lock:
            if found:
                child.shape = child_shape(found)
                child.tiles, child.state = found, 'ready'
                child.version += 1
            else:
                remove(parent.key)
            apply_rows()
    window.app.bg.cancel_where(lambda key: isinstance(key, tuple) and key[:3] == ('follow', window.key, parent.key))
    window.app.bg.submit(profile_scope.capture(load), priority=1, key=('follow', window.key, parent.key, token))
