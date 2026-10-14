# -*- coding: utf-8 -*-
"""The Home layout editor (v5.10.98): order, hide and add the Home rows.

WindowXMLDialog on resources/skins/Default/1080i/dexhub_layout.xml:

  300  the rows of the page being arranged
  310  the pages: Home, Movies, TV Shows
  320  actions: add a row, restore the Home's own order, done

Remote: OK on a row opens its options (move, hide or show, to the top, to the
bottom, remove an added row). While a row moves, Up and Down carry it and OK
or Back puts it down. Right on a row hides or shows it at once. Every change
is saved as it is made; the Home rebuilds its rows when the editor closes.

The editor runs on the Home's GUI callback thread only, and it keeps its own
ListItems (see _BaseWindow._control in window.py for why); a move repaints
the two items in place instead of resetting the list, so nothing jumps.
"""
import xbmc
import xbmcgui

from . import layout as L

ROWS, PAGES, ACTIONS = 300, 310, 320
A_LEFT, A_RIGHT, A_UP, A_DOWN, A_PGUP, A_PGDN = 1, 2, 3, 4, 5, 6
A_CONTEXT, A_MENU = 117, 163
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
PAGE_LABELS = (('all', 'الرئيسية'), ('movie', 'أفلام'), ('series', 'مسلسلات'))
ACTION_ITEMS = (('add', 'إضافة صف', 'lay_add.png'),
                ('reset', 'استعادة الترتيب الأصلي', 'lay_reset.png'),
                ('done', 'تم', 'lay_done.png'))


class LayoutWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(LayoutWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.provider = kwargs.get('provider')
        self.media = L.page_key(kwargs.get('media'))
        self.select_key = kwargs.get('select_key') or ''
        self.entries = []
        self.items = []
        self.moving = -1
        self.changed = set()
        self._ctrls = {}
        self._page_items = []
        self._inited = False
        self._last_pos = 0

    def tr(self, text):
        return self.app.tr(text)

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        for control_id in (ROWS, PAGES, ACTIONS):
            try:
                self._ctrls[control_id] = self.getControl(control_id)
            except Exception:
                pass
        self.setProperty('dl.title', self.tr('ترتيب الصفحة الرئيسية'))
        self.setProperty('dl.desc', self.tr(
            'رتّب الصفوف، أخفِ ما لا تحتاجه، وأضف صفوفًا من المصادر والمجموعات والمكتبات.'))
        self.setProperty('dl.pages', self.tr('الصفحة'))
        self._fill_pages()
        self._fill_actions()
        self._load_page(self.media, select_key=self.select_key)
        self.setFocusId(ROWS)

    def _fill_pages(self):
        items = []
        for key, label in PAGE_LABELS:
            li = xbmcgui.ListItem(label=self.tr(label))
            li.setProperty('key', key)
            items.append(li)
        self._page_items = items
        ctrl = self._ctrls.get(PAGES)
        if ctrl is not None:
            ctrl.reset()
            ctrl.addItems(items)

    def _mark_pages(self):
        for li, (key, _label) in zip(self._page_items, PAGE_LABELS):
            li.setProperty('active', '1' if key == self.media else '')
        self.setProperty('dl.page', self.tr(dict(PAGE_LABELS).get(self.media, '')))

    def _fill_actions(self):
        items = []
        for key, label, icon in ACTION_ITEMS:
            li = xbmcgui.ListItem(label=self.tr(label))
            li.setProperty('key', key)
            li.setProperty('icon', self.app.media_path(icon))
            items.append(li)
        self._action_items = items
        ctrl = self._ctrls.get(ACTIONS)
        if ctrl is not None:
            ctrl.reset()
            ctrl.addItems(items)

    # -------------------------------------------------------------- data
    def _entry(self, row, hidden):
        meta = row.meta or {}
        custom_id = meta.get('custom_id') or ''
        shelf = None
        if custom_id:
            icon = 'lay_added.png'
            kind = meta.get('source_label') or self.tr('صف مضاف')
            detail = ''
            shelf = self._custom_spec(custom_id)
            if not (shelf and shelf.get('type') == 'shelf'):
                shelf = None
            else:
                icon = 'lay_collection.png'
                kind = self.tr('كوليكشن  •  %d كتالوج') % len(shelf.get('members') or [])
        elif row.kind == 'continue':
            icon, kind, detail = 'lay_continue.png', self.tr('تقدّم المشاهدة'), ''
        elif row.kind == 'collection':
            icon, kind, detail = 'lay_collection.png', self.tr('مجموعة'), ''
        else:
            icon, kind, detail = 'lay_catalog.png', self.tr('كتالوج'), row.subtitle or ''
        if meta.get('missing'):
            detail = self.tr('غير متاح الآن')
        return {
            'key': row.key, 'label': row.title or row.key,
            'sub': '  •  '.join([part for part in (kind, detail) if part]),
            'icon': icon, 'hidden': bool(hidden),
            'default_hidden': bool(meta.get('default_hidden')), 'custom_id': custom_id,
            'shelf': bool(shelf), 'params': dict(meta.get('params') or {}),
            'source': meta.get('source_label') or row.subtitle or '',
            'progress': L.is_progress_row(row),
        }

    def _custom_spec(self, custom_id):
        for spec in self.app.layout.page(self.media)['custom']:
            if spec.get('id') == custom_id:
                return spec
        return None

    def _save_custom(self, spec):
        """One added row changed (a collection's catalogs, its name)."""
        page = self.app.layout.page(self.media)
        custom = [spec if s.get('id') == spec.get('id') else s for s in page['custom']]
        self._save(custom=custom)

    def _load_page(self, media, select_key=''):
        self.media = L.page_key(media)
        self.moving = -1
        self.setProperty('dl.moving', '')
        try:
            rows = self.provider.layout_rows(self.media)
        except Exception:
            self.app.log('layout rows failed', xbmc.LOGWARNING)
            rows = []
        self.entries = [self._entry(row, hidden) for row, hidden in self.app.layout.arrange(rows, self.media)]
        self._mark_pages()
        position = 0
        for index, entry in enumerate(self.entries):
            if entry['key'] == select_key:
                position = index
                break
        self._fill_rows(position)
        self._hint()

    def _save(self, custom=None):
        order = [entry['key'] for entry in self.entries]
        hidden = dict((entry['key'], entry['hidden']) for entry in self.entries
                      if entry['hidden'] != entry['default_hidden'])
        self.app.layout.save_page(self.media, order=order, hidden=hidden, custom=custom)
        self.changed.add(self.media)

    # ------------------------------------------------------------ painting
    def _paint(self, li, entry, index):
        li.setLabel(entry['label'])
        li.setLabel2(entry['sub'])
        li.setProperty('index', str(index + 1))
        li.setProperty('icon', self.app.media_path(entry['icon']))
        li.setProperty('hidden', '1' if entry['hidden'] else '')
        li.setProperty('state', self.tr('مخفي') if entry['hidden'] else self.tr('ظاهر'))
        li.setProperty('moving', '1' if index == self.moving else '')
        li.setProperty('plus', '1' if entry.get('shelf') else '')

    def _fill_rows(self, select=0):
        items = []
        for index, entry in enumerate(self.entries):
            li = xbmcgui.ListItem()
            self._paint(li, entry, index)
            items.append(li)
        self.items = items
        ctrl = self._ctrls.get(ROWS)
        if ctrl is not None:
            ctrl.reset()
            if items:
                ctrl.addItems(items)
                select = max(0, min(select, len(items) - 1))
                if select:
                    ctrl.selectItem(select)
        self._last_pos = select if items else 0
        self._count()

    def _repaint(self, index):
        if 0 <= index < len(self.items) and index < len(self.entries):
            self._paint(self.items[index], self.entries[index], index)

    def _count(self):
        hidden = sum(1 for entry in self.entries if entry['hidden'])
        text = self.tr('%d صفًا') % len(self.entries)
        if hidden:
            text += '  •  ' + (self.tr('%d مخفية') % hidden)
        self.setProperty('dl.count', text)
        self.setProperty('dl.empty', '' if self.entries else self.tr('لا توجد صفوف في هذه الصفحة بعد'))

    def _hint(self):
        if self.moving >= 0:
            text = self.tr('أعلى وأسفل: حرّك الصف   •   OK أو رجوع: ثبّته هنا')
        else:
            text = self.tr('OK: خيارات الصف   •   يمين: إظهار أو إخفاء   •   رجوع: حفظ وخروج')
        self.setProperty('dl.hint', text)

    def _selected(self, control_id=ROWS):
        ctrl = self._ctrls.get(control_id)
        try:
            return int(ctrl.getSelectedPosition()) if ctrl is not None else -1
        except Exception:
            return -1

    def _select(self, index):
        ctrl = self._ctrls.get(ROWS)
        if ctrl is not None and 0 <= index < len(self.items):
            try:
                ctrl.selectItem(index)
            except Exception:
                pass
        self._last_pos = index

    # ----------------------------------------------------------- editing
    def _toggle(self, index):
        if not 0 <= index < len(self.entries):
            return
        entry = self.entries[index]
        entry['hidden'] = not entry['hidden']
        self._repaint(index)
        self._count()
        self._save()

    def _start_move(self, index):
        if not 0 <= index < len(self.entries):
            return
        self.moving = index
        self._repaint(index)
        self.setProperty('dl.moving', '1')
        self._hint()

    def _move_to(self, target):
        current = self.moving
        if current < 0:
            return
        target = max(0, min(target, len(self.entries) - 1))
        if target != current:
            entry = self.entries.pop(current)
            self.entries.insert(target, entry)
            self.moving = target
            for index in range(min(current, target), max(current, target) + 1):
                self._repaint(index)
        self._select(target)

    def _end_move(self):
        index = self.moving
        self.moving = -1
        if index >= 0:
            self._repaint(index)
        self.setProperty('dl.moving', '')
        self._hint()
        self._save()

    def _place(self, index, target):
        """Move one row straight to ``target`` (to the top or the bottom)."""
        if not 0 <= index < len(self.entries):
            return
        entry = self.entries.pop(index)
        target = max(0, min(target, len(self.entries)))
        self.entries.insert(target, entry)
        for position in range(min(index, target), max(index, target) + 1):
            self._repaint(position)
        self._select(target)
        self._save()

    def _remove(self, index):
        if not 0 <= index < len(self.entries):
            return
        entry = self.entries[index]
        custom_id = entry.get('custom_id')
        if not custom_id:
            return
        if not xbmcgui.Dialog().yesno(self.tr('حذف الصف'),
                                      self.tr('حذف «%s» من الصفحة الرئيسية؟') % entry['label']):
            return
        page = self.app.layout.page(self.media)
        custom = [spec for spec in page['custom'] if spec.get('id') != custom_id]
        del self.entries[index]
        self._save(custom=custom)
        self._fill_rows(min(index, len(self.entries) - 1))

    def _row_options(self, index):
        if not 0 <= index < len(self.entries):
            return
        entry = self.entries[index]
        options = []
        if entry.get('shelf'):
            options += [('shelf_add', self.tr('+  أضف كتالوج للكوليكشن')),
                        ('shelf_edit', self.tr('كتالوجات الكوليكشن (ترتيب وحذف)')),
                        ('rename', self.tr('إعادة تسمية الكوليكشن'))]
        options += [('move', self.tr('تحريك الصف')),
                    ('style', self.tr('شكل الصف')),
                    ('toggle', self.tr('إظهار الصف') if entry['hidden'] else self.tr('إخفاء الصف')),
                    ('top', self.tr('إلى أعلى الصفحة')),
                    ('bottom', self.tr('إلى أسفل الصفحة'))]
        if not entry.get('shelf') and entry.get('params'):
            options.append(('combine', self.tr('اجمعه مع صف آخر في كوليكشن (جنب بعض)')))
        if entry.get('custom_id'):
            options.append(('remove', self.tr('حذف هذا الصف')))
        from .options import choose
        choice = choose(entry.get('title') or entry.get('label') or self.tr('الصف'),
                        [label for _key, label in options], subtitle=self.tr('ترتيب الصفحة الرئيسية'))
        if choice < 0:
            return
        key = options[choice][0]
        if key == 'move':
            self._start_move(index)
        elif key == 'toggle':
            self._toggle(index)
        elif key == 'top':
            self._place(index, 0)
        elif key == 'bottom':
            self._place(index, len(self.entries) - 1)
        elif key == 'remove':
            self._remove(index)
        elif key == 'shelf_add':
            self._shelf_add(index)
        elif key == 'shelf_edit':
            self._shelf_edit(index)
        elif key == 'rename':
            self._rename(index)
        elif key == 'style':
            self._style(index)
        elif key == 'combine':
            self._combine(index)

    # ------------------------------------------------- v5.10.183: collections
    def _shelf_add(self, index):
        """+ on a collection row: one more catalog, beside the others."""
        if not 0 <= index < len(self.entries) or not self.entries[index].get('shelf'):
            return
        entry = self.entries[index]
        spec = self._custom_spec(entry['custom_id'])
        if not spec:
            return
        from .layout_pick import pick_member
        member = pick_member(self.app, self.media, self.tr('أضف كتالوج إلى «%s»') % entry['label'])
        if not member:
            return
        spec = dict(spec)
        spec['members'] = list(spec.get('members') or []) + [{
            'label': member.get('label') or '', 'params': dict(member.get('params') or {}),
            'source_label': member.get('source_label') or ''}]
        self._save_custom(spec)
        entry['sub'] = self.tr('كوليكشن  •  %d كتالوج') % len(spec['members'])
        self._repaint(index)
        self._notify(self.tr('أُضيف «%s»') % (member.get('label') or ''))

    def _shelf_edit(self, index):
        """A collection's catalogs: move one to the front, left, right, or take it out."""
        entry = self.entries[index]
        from .options import choose
        while True:
            spec = self._custom_spec(entry['custom_id'])
            members = list((spec or {}).get('members') or [])
            if not members:
                return
            names = ['%d.  %s' % (i + 1, m.get('label') or '') for i, m in enumerate(members)]
            pick = choose(entry['label'], names + [self.tr('+  أضف كتالوج')],
                          subtitle=self.tr('كتالوجات الكوليكشن'))
            if pick < 0:
                return
            if pick == len(members):
                self._shelf_add(index)
                continue
            acts = [('first', self.tr('الأول')), ('left', self.tr('قبله بخانة')),
                    ('right', self.tr('بعده بخانة')), ('last', self.tr('الأخير')),
                    ('out', self.tr('شيله من الكوليكشن'))]
            act = choose(members[pick].get('label') or '', [label for _k, label in acts],
                         subtitle=entry['label'])
            if act < 0:
                continue
            key = acts[act][0]
            member = members.pop(pick)
            if key == 'out':
                if not members:
                    self._notify(self.tr('الكوليكشن يحتاج كتالوج واحد على الأقل'))
                    members.insert(pick, member)
                    continue
            elif key == 'first':
                members.insert(0, member)
            elif key == 'last':
                members.append(member)
            elif key == 'left':
                members.insert(max(0, pick - 1), member)
            else:
                members.insert(min(len(members), pick + 1), member)
            spec = dict(spec)
            spec['members'] = members
            self._save_custom(spec)
            entry['sub'] = self.tr('كوليكشن  •  %d كتالوج') % len(members)
            self._repaint(index)

    def _rename(self, index):
        entry = self.entries[index]
        spec = self._custom_spec(entry['custom_id'])
        if not spec:
            return
        name = xbmcgui.Dialog().input(self.tr('اسم الكوليكشن'), defaultt=spec.get('label') or '')
        if not name:
            return
        spec = dict(spec)
        spec['label'] = name
        self._save_custom(spec)
        entry['label'] = name
        self._repaint(index)

    def _style(self, index):
        entry = self.entries[index]
        from .layout import pick_style, style_choices_for
        current = self.app.layout.shape(entry['key'])
        only = style_choices_for(shelf=bool(entry.get('shelf')), progress=bool(entry.get('progress')))
        key = pick_style(self.tr, self.app.media_path, current, entry['label'], only=only)
        if key is None or key == current:
            return
        self.app.layout.set_shape(entry['key'], key)
        self.changed.add(self.media)

    def _combine(self, index):
        """Two catalog rows side by side: a collection row holding both (the
        two rows are hidden; showing them again brings them back)."""
        entry = self.entries[index]
        others = [(i, e) for i, e in enumerate(self.entries)
                  if i != index and e.get('params') and not e.get('shelf')]
        if not others:
            self._notify(self.tr('ما فيه صف كتالوج ثاني في هذه الصفحة'))
            return
        from .options import choose
        pick = choose(self.tr('اجمع «%s» مع') % entry['label'],
                      ['%s   %s' % (e['label'], ('•  ' + e['sub']) if e.get('sub') else '') for _i, e in others],
                      subtitle=self.tr('كوليكشن: الكتالوجين جنب بعض في صف واحد'))
        if pick < 0:
            return
        other_index, other = others[pick]
        name = xbmcgui.Dialog().input(self.tr('اسم الكوليكشن'), defaultt=self.tr('كوليكشن'))
        if not name:
            return
        members = [{'label': e['label'], 'params': dict(e['params']), 'source_label': e.get('source') or ''}
                   for e in (entry, other)]
        spec = L.new_custom({'type': 'shelf', 'label': name, 'members': members,
                             'source_label': self.tr('كوليكشن  •  %d كتالوج') % len(members)})
        for e in (entry, other):
            e['hidden'] = True
        at = min(index, other_index)
        page = self.app.layout.page(self.media)
        self.entries.insert(at, {
            'key': L.custom_key(spec), 'label': name,
            'sub': self.tr('كوليكشن  •  %d كتالوج') % len(members), 'icon': 'lay_collection.png',
            'hidden': False, 'default_hidden': False, 'custom_id': spec['id'], 'shelf': True,
            'params': {}, 'source': ''})
        self._save(custom=page['custom'] + [spec])
        self._fill_rows(at)
        self._notify(self.tr('صار «%s» كوليكشن فيه الكتالوجين جنب بعض') % name)

    def _notify(self, text):
        try:
            xbmcgui.Dialog().notification('Dex Hub', text, xbmcgui.NOTIFICATION_INFO, 2500)
        except Exception:
            pass

    def _add_row(self):
        from .layout_pick import pick_row
        spec = pick_row(self.app, self.media)
        if not spec:
            return
        spec = L.new_custom(spec)
        page = self.app.layout.page(self.media)
        custom = page['custom'] + [spec]
        at = min(max(self._last_pos, -1) + 1, len(self.entries))
        self.entries.insert(at, {
            'key': L.custom_key(spec), 'label': spec.get('label') or '',
            'sub': spec.get('source_label') or '', 'icon': 'lay_added.png',
            'hidden': False, 'default_hidden': False, 'custom_id': spec['id'],
            'shelf': spec.get('type') == 'shelf', 'params': dict(spec.get('params') or {}), 'source': '',
        })
        self._save(custom=custom)
        self._fill_rows(at)
        self.setFocusId(ROWS)
        try:
            xbmcgui.Dialog().notification('Dex Hub', self.tr('أُضيف الصف «%s»') % (spec.get('label') or ''),
                                          xbmcgui.NOTIFICATION_INFO, 2500)
        except Exception:
            pass

    def _reset(self):
        if not xbmcgui.Dialog().yesno(self.tr('استعادة الترتيب الأصلي'), self.tr(
                'ترجع صفوف هذه الصفحة لترتيبها الأصلي وتظهر المخفية. الصفوف التي أضفتها تبقى في آخر الصفحة.')):
            return
        self.app.layout.reset_page(self.media)
        self.changed.add(self.media)
        self._load_page(self.media)

    # ------------------------------------------------------------ events
    def onClick(self, control_id):
        if control_id == ROWS:
            index = self._selected()
            if self.moving >= 0:
                self._end_move()
            else:
                self._last_pos = index
                self._row_options(index)
        elif control_id == PAGES:
            index = self._selected(PAGES)
            if 0 <= index < len(PAGE_LABELS) and PAGE_LABELS[index][0] != self.media:
                self._load_page(PAGE_LABELS[index][0])
            self.setFocusId(ROWS)
        elif control_id == ACTIONS:
            index = self._selected(ACTIONS)
            key = ACTION_ITEMS[index][0] if 0 <= index < len(ACTION_ITEMS) else ''
            if key == 'add':
                self._add_row()
            elif key == 'reset':
                self._reset()
            elif key == 'done':
                self.close()

    def onAction(self, action):
        aid = action.getId()
        focus = self.getFocusId()
        if self.moving >= 0:
            if aid in (A_UP, A_DOWN, A_PGUP, A_PGDN):
                step = {A_UP: -1, A_DOWN: 1, A_PGUP: -5, A_PGDN: 5}[aid]
                # Kodi has already moved the cursor; follow it, or step when
                # it could not move (the list does not wrap)
                now = self._selected()
                self._move_to(now if (now >= 0 and now != self.moving) else self.moving + step)
                return
            if aid in A_BACK:
                self._end_move()
                return
            return
        if focus == ROWS:
            if aid in (A_UP, A_DOWN, A_PGUP, A_PGDN):
                self._last_pos = self._selected()
                return
            if aid == A_RIGHT:
                self._toggle(self._selected())
                return

            if aid in (A_CONTEXT, A_MENU):
                self._row_options(self._selected())
                return
        if aid in A_BACK:
            self.close()


def open_editor(app, provider, media='all', select_key=''):
    """Show the editor; returns the pages whose layout changed."""
    from . import ui_size
    window = LayoutWindow(ui_size.xml('dexhub_layout.xml'), app.addon_path, 'Default', '1080i',
                          app=app, provider=provider, media=media, select_key=select_key)
    try:
        window.doModal()
        return set(window.changed)
    finally:
        del window
