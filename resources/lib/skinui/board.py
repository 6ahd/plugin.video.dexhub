# -*- coding: utf-8 -*-
"""The rows on the skin's Home: which row sits in which slot of a tab, as
Home window properties (dhs.<tab>.<n>.id/title/sub/shape/path).

Light on purpose (no router): at Kodi's start the Home shows the rows saved
last time from here at once, while the service works them out again.
"""
import os

from . import common as C


def load_specs(tab):
    data = C.read_json(C.spec_file(tab), {}) or {}
    return list(data.get('rows') or [])


def find_spec(tab, row_id):
    for spec in load_specs(tab):
        if spec.get('id') == row_id:
            return spec
    from .catalog_follow import spec
    return spec(tab, row_id)

def row_path(tab, row_id, version=0, slot=0):
    """A row's plugin path; v5.10.165: with its slot (n), so the row's call
    finds its Home properties at once instead of reading the slots above
    it one by one (each read waits for Kodi's frame to end)."""
    if slot:
        return C.url('skin_row', m=tab, id=row_id, n=str(slot), v=str(version or 0))
    return C.url('skin_row', m=tab, id=row_id, v=str(version or 0))


def slot_of(tab, row_id, hint=''):
    """The Home slot (1..20) a row is published in, 0 if none: the slot
    the path named when it still holds the row, else the slots in turn."""
    try:
        n = int(hint or 0)
    except ValueError:
        n = 0
    if 1 <= n <= C.ROWS and C.prop('dhs.%s.%d.id' % (tab, n)) == row_id:
        return n
    for n in range(1, C.ROWS + 1):
        current = C.prop('dhs.%s.%d.id' % (tab, n))
        if not current:
            return 0
        if current == row_id:
            return n
    return 0


def _version(tab, row_id):
    try:
        return int(C.prop('dhs.v.%s.%s' % (tab, row_id)) or 0)
    except Exception:
        return 0


def bump(tab, row_id, spec=None):
    """A row's content changed: its path changes, so Kodi reads it again."""
    version = _version(tab, row_id) + 1
    C.set_prop('dhs.v.%s.%s' % (tab, row_id), version)
    for n in range(1, C.ROWS + 1):
        if C.prop('dhs.%s.%d.id' % (tab, n)) == row_id:
            # v5.10.140: no selection capture/restore here. It read the row's
            # list through xbmcgui controls from the service's threads (a
            # skin's list has no items on that side, so it never restored
            # anything) and ran right where Kodi 22 RC1 crashed.
            C.set_prop('dhs.%s.%d.path' % (tab, n), row_path(tab, row_id, version, slot=n))
            if spec is not None:
                C.set_prop('dhs.%s.%d.shape' % (tab, n), spec.get('shape') or 'poster')
            break


SPOT = 'spot'           # the row id of a tab's spotlight picks (skin.dexhub 2)
SPOT_ITEMS = 8          # as many picks as the add-on's billboard (v5.10.379: spot_source.count)


def _spot_worthy(item, chosen=False):
    """A title the spotlight can show: a work with a backdrop that opens its
    page (any work with a backdrop when the user chose the row, v5.10.379:
    Continue watching plays on)."""
    props = item.get('props') or {}
    if props.get('kind') != 'work' or not (item.get('art') or {}).get('fanart'):
        return False
    return chosen or props.get('dhs.click') == 'info'


def _spot_label(tab, spec):
    """The spotlight's source as the tab shows it (its row's published name)."""
    for n in range(1, C.ROWS + 1):
        row_id = C.prop('dhs.%s.%d.id' % (tab, n))
        if not row_id:
            break
        if row_id == spec.get('id'):
            return C.prop('dhs.%s.%d.title' % (tab, n)) or spec.get('title') or ''
    return spec.get('title') or ''


def update_spot(tab, specs=None):
    """The tab's spotlight: by default the first titles with artwork of its
    first catalog row (as the add-on's billboard picks them); v5.10.379: or
    the row the user chose for it, or none (spot_source), and as many titles
    as chosen (5 to 20). Saved as a row of its own (rows/<tab>_spot.json) and
    published as dhs.<tab>.spot.*; each title carries the source's name
    (dhs.spot_label) for the skin. True when the picks changed."""
    from . import spot_source
    if tab in ('library', 'servers'):
        specs = []          # v5.10.155: the Library has its own header, no spotlight
    if specs is None:
        specs = [spec for _slot, spec in published(tab)]
    wanted = spot_source.choice(tab) if specs else spot_source.AUTO
    limit = spot_source.count()
    from ..ui_preferences import hero_order       # v5.10.364
    source, data, items = None, None, []
    if wanted == spot_source.OFF:
        specs = []
    elif wanted != spot_source.AUTO:
        chosen = [spec for spec in specs if spec.get('id') == wanted]
        for spec in chosen:
            data = C.read_row(C.row_file(tab, spec['id'])) or {}
            items = hero_order([item for item in data.get('items') or [] if _spot_worthy(item, True)], spec['id'])[:limit]
            if items:
                source = spec
        if source is None:
            C.log('spotlight of %s: the chosen row %s has no titles with a backdrop yet' % (tab, wanted))
    if source is None:
        for spec in specs:
            if spec.get('_follow_parent') or spec.get('own') or spec.get('progress') or (spec.get('kind') or 'catalog') != 'catalog':
                continue
            data = C.read_row(C.row_file(tab, spec['id'])) or {}     # v5.10.188: its quick copy
            items = hero_order([item for item in data.get('items') or [] if _spot_worthy(item)], spec['id'])[:limit]
            if len(items) >= 3:
                source = spec
                break
            items = []
    prefix = 'dhs.%s.spot.' % tab
    if source is None:
        for field in ('id', 'title', 'key', 'path', 'empty'):
            C.set_prop(prefix + field, '')
        return False
    label = _spot_label(tab, source)
    marked = []
    for item in items:
        item = dict(item)
        item['props'] = dict(item.get('props') or {}, **{'dhs.spot_label': label})
        marked.append(item)
    items = marked
    digest = C.content_hash(items, {'src': source['id']})
    old = C.read_row(C.row_file(tab, SPOT)) or {}
    changed = old.get('h') != digest
    if changed:
        C.write_json(C.row_file(tab, SPOT), {
            't': C.now(), 'key': SPOT, 'title': source.get('title') or '', 'shape': 'landscape',
            'kind': 'spot', 'items': items, 'more': None, 'h': digest, 'lang': (data or {}).get('lang'),
            'enriched': True, 'ttl': 10.0 ** 9, 'f': C.FORMAT, 'src': source['id']})
        C.set_prop(prefix + 'empty', '')
        served = C.prop('dhs.sv.%s.%s' % (tab, SPOT))
        if served and served != digest:
            # Kodi holds the old picks: a new path makes it read the new ones
            C.set_prop('dhs.v.%s.%s' % (tab, SPOT), _version(tab, SPOT) + 1)
    C.set_prop(prefix + 'id', SPOT)
    C.set_prop(prefix + 'title', label)
    C.set_prop(prefix + 'src', source['id'])
    C.set_prop(prefix + 'key', '%s:%s' % (tab, SPOT))
    C.set_prop(prefix + 'path', row_path(tab, SPOT, _version(tab, SPOT)))
    return changed


# v5.10.118: what publishing needs of a row's cache (is it there, has it
# titles, its shape), kept by the file's stamp. A publish read every row's
# whole cache again (60 rows: 4 to 7 s in a box's kodi.log, each refresh).
_BRIEF = {}


def _row_brief(path):
    """None when the row has no cache, else {'items': bool, 'shape': str,
    'src': the fingerprint of the cards a collection row was built from,
    'f': its format}."""
    try:
        st = os.stat(path)
    except OSError:
        _BRIEF.pop(path, None)
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _BRIEF.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    data = C.read_json(path)
    brief = None if data is None else {'items': bool(data.get('items')), 'shape': data.get('shape') or '',
                                       'src': data.get('src') or '', 'f': data.get('f'), 'filter_summary': data.get('filter_summary') or '',
                                       'look': data.get('look') or ''}
    if len(_BRIEF) > 400:
        _BRIEF.clear()
    _BRIEF[path] = (stamp, brief)
    return brief


_FAR = {'ranked': 'poster', 'wide_ranked': 'wide', 'tall': 'wide', 'tall_ranked': 'wide',
        'collage': 'wide', 'mosaic': 'wide', 'stack': 'wide'}


def _restyle(tab, spec):
    path = C.row_file(tab, spec['id'])
    data = C.read_json(path)
    if not data:
        return
    data.update({'shape': spec['shape'], 'enriched': False, 't': 0})
    C.write_json(path, data)
    try:
        C.request_refresh(tab, spec['id'])
    except Exception:
        pass


def show_source():
    """v5.10.286: a row's source line ("From ...") beside its title, unless
    the user hid it (homeui_show_row_source, Menu: Look)."""
    try:
        from ..ui_preferences import enabled
        return enabled('homeui_show_row_source')
    except Exception:
        return True


# v5.10.378: a row's name as people say it (the clean look): «Dex Max - Continue
# Watching Movies - Movie» from Dex Max is «Continue Watching Movies»
_KINDS = frozenset(('movie', 'movies', 'series', 'tv', 'show', 'shows', 'tv shows', 'anime', 'channel', 'channels',
                    'film', 'films', 'فيلم', 'أفلام', 'افلام', 'مسلسل', 'مسلسلات', 'أنمي', 'انمي'))
_DASHES = (' - ', ' – ', ' — ', ' | ')


def clean_look():
    try:
        from ..ui_preferences import enabled
        return enabled('homeui_clean_look')
    except Exception:
        return True


def _split(title):
    for dash in _DASHES:
        if dash in title:
            return [p.strip() for p in title.split(dash)], dash
    return [title.strip()], ' - '


def tidy_title(title, source='', keep_kind=False):
    """The row's name without its source in front and its type at the end."""
    text = str(title or '').strip()
    parts, dash = _split(text)
    if len(parts) < 2:
        return text
    src = str(source or '').strip().lower()
    if src and parts[0].lower() == src:
        parts = parts[1:]
    if not keep_kind and len(parts) >= 2 and parts[-1].lower() in _KINDS:
        parts = parts[:-1]
    return dash.join(p for p in parts if p) or text


def tidy_titles(specs):
    """{spec id: name} for a tab's rows; two rows that would read the same
    keep their type (Popular Movies and Popular Series stay apart)."""
    first = dict((s['id'], tidy_title(s.get('title'), s.get('sub'))) for s in specs)
    seen = {}
    for name in first.values():
        seen[name.lower()] = seen.get(name.lower(), 0) + 1
    out = {}
    for s in specs:
        name = first[s['id']]
        out[s['id']] = tidy_title(s.get('title'), s.get('sub'), keep_kind=True) if seen[name.lower()] > 1 else name
    return out


def _publish_tab(tab, specs):
    from .catalog_follow import compose
    specs = compose(tab, specs)
    shown = 0
    source_on = show_source()       # v5.10.286
    names = tidy_titles(specs) if clean_look() else {}
    shapes = []
    shown_specs = []
    for spec in specs:
        if shown >= C.ROWS:
            break
        data = _row_brief(C.row_file(tab, spec['id']))
        if data is not None and not data.get('items'):
            # a row that came back empty last time stays off the Home until
            # it has titles again (the service reads it again later; Continue
            # Watching after the next play)
            continue
        if data is not None and data.get('shape'):
            if spec.get('auto_shape') or not spec.get('shape'):
                spec['shape'] = data['shape']
            elif data['shape'] != spec['shape'] or ((spec.get('shelf') or spec.get('walls')) and data.get('look') != spec.get('look')):
                # v5.10.173: the row's shape changed (the user's pick, or a
                # new default such as Continue Watching's big banners): its
                # copy is drawn again in it, with its logos looked up
                _restyle(tab, spec)
        shown += 1
        prefix = 'dhs.%s.%d.' % (tab, shown)
        key = '%s:%s' % (tab, spec['id'])
        mark = C.prop(prefix + 'empty')
        if mark and (mark != key or data is not None):
            # v5.10.120: another row in the slot, or titles now
            C.set_prop(prefix + 'empty', '')
        eligible = (spec.get('kind') == 'catalog' and not spec.get('own')
                    and not spec.get('progress') and bool((spec.get('params') or {}).get('action')))
        C.set_prop(prefix + 'filterable', '1' if eligible else '')
        C.set_prop(prefix + 'id', spec['id'])
        C.set_prop(prefix + 'title', names.get(spec['id']) or spec['title'])
        sub = (spec.get('sub') or '') if source_on else ''     # v5.10.286: hidden source
        sub = '' if sub.strip().lower() == (spec.get('title') or '').strip().lower() else sub
        C.set_prop(prefix + 'sub', '  ·  '.join(p for p in (sub, (data or {}).get('filter_summary')) if p))
        # v5.10.180: skin.dexhub 3.26.1 draws the newer styles on its first ten
        # rows; a row further down shows them as their nearest
        shape = spec['shape']
        if shown == 1 and C.prop('dhs.home.layout') == 'shelf' and (shape or 'poster') in ('poster', 'landscape', 'wide', 'big', 'square'):
            # v5.10.286: the Wide card shelf Home opens on big banner cards
            shape = 'feature'
        if shown > 10:
            shape = _FAR.get(shape, shape)
        C.set_prop(prefix + 'shape', shape)
        C.set_prop(prefix + 'key', key)
        C.set_prop(prefix + 'path', row_path(tab, spec['id'], _version(tab, spec['id']), slot=shown))
        shapes.append(shape or 'poster')
        shown_specs.append(spec)
    for n in range(shown + 1, C.ROWS + 1):
        prefix = 'dhs.%s.%d.' % (tab, n)
        if not C.prop(prefix + 'path') and not C.prop(prefix + 'id'):
            break
        for field in ('id', 'title', 'sub', 'shape', 'key', 'path', 'empty', 'filterable', 'loading'):
            C.set_prop(prefix + field, '')
    C.set_prop('dhs.%s.count' % tab, shown)
    # the shapes of the hub's rows (skin.dexhub 2 builds a row's layout from
    # its dhs.<tab>.<n>.shape when the hub opens; Kodi rebuilds the hub the
    # next time it opens when one changed)
    C.set_prop('dhs.%s.shapes' % tab, '|'.join(shapes))
    # v5.10.447: the Home rows take only the card styles they use (skin.dexhub 3.28.167)
    try:
        from . import home_layouts
        home_layouts.sync()
    except Exception as exc:
        C.log('home rows: %s' % exc)
    try:
        update_spot(tab, shown_specs)
    except Exception as exc:
        C.log('spotlight of %s: %s' % (tab, exc))
    return shown


def is_published(tab, row_id):
    for n in range(1, C.ROWS + 1):
        current = C.prop('dhs.%s.%d.id' % (tab, n))
        if not current:
            return False
        if current == row_id:
            return True
    return False


def mark_empty(tab, row_id, empty, slot=None):
    """v5.10.120: a row served with no titles. skin.dexhub loads a row once
    the row above has its first title (one plugin call at a time; rows 1-4
    together since 3.28.139, tools/release/skin_rows_together.py); a row
    that came back empty stopped the rows under it until the next publish
    took it away. Its slot is marked (dhs.<tab>.<n>.empty = its key) and
    the rows under it load at once; the mark goes with the next publish
    (another row in the slot) or the row's first titles. ``slot``: the slot
    already found (slot_of, v5.10.165)."""
    if row_id == SPOT:
        name, key = 'dhs.%s.spot.empty' % tab, '%s:%s' % (tab, SPOT)
    else:
        n = slot if slot is not None else slot_of(tab, row_id)
        if not n:
            return
        name, key = 'dhs.%s.%d.empty' % (tab, n), '%s:%s' % (tab, row_id)
    value = key if empty else ''
    if C.prop(name) != value:
        C.set_prop(name, value)


def mark_pending(tab, row_id, pending, slot=None):
    n = slot if slot is not None else slot_of(tab, row_id)
    if n:
        C.set_prop('dhs.%s.%d.loading' % (tab, n), '1' if pending else '')


def published(tab):
    """[(slot, spec)] of the rows on the tab now."""
    from .catalog_follow import virtuals
    specs = dict((s['id'], s) for s in load_specs(tab) + virtuals(tab))
    out = []
    for n in range(1, C.ROWS + 1):
        row_id = C.prop('dhs.%s.%d.id' % (tab, n))
        if not row_id:
            break
        if row_id in specs:
            out.append((n, specs[row_id]))
    return out


def publish_saved(tabs=C.TABS):
    """The rows saved by the last publish, put on the Home at once (a few
    milliseconds); False when there is nothing saved yet."""
    from . import strings
    strings.publish()
    total, found = 0, False
    for tab in tabs:
        specs = load_specs(tab)
        if specs:
            found = True
            total += _publish_tab(tab, specs)
    if found:
        C.set_prop(C.PROP_READY, '1')
        C.log('rows shown from the last session: %d' % total)
    return found
