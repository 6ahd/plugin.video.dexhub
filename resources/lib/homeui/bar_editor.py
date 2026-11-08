# -*- coding: utf-8 -*-
"""Bar sections in one window (v5.10.481): the user asked for one simple,
finer window than Arctic Fuse 3's menu editor, instead of a chain of lists.

WindowXMLDialog on dexhub_bars.xml (tools/s474/make_bar_editor.py):
the sections on the left (a switch for each built-in one; yours; + New
section), your bar as it shows on the top right, and the open section on the
right: its buttons, the ways to add catalogs, and its catalogs, each with the
picture of its style. OK on a catalog edits it in place: Left/Right change
its style, Up/Down move it, OK or Back put it down. Right on a catalog:
Rename / Remove. The icons, the add-on folders and one style for all open in
the right half of the same window (Window property bs.mode).

Every change is saved at once (bar_sections.write publishes the bar); the
Home reads the rows again when the window closes.

v5.10.501: the one editor of the Home. The pen opens it too (on the section
on show): Home, Movies and TV Shows open their rows on the right like a
section of yours (layout.json, the Home's own: order, hidden, style, rows
added from any source); hiding a built-in row hides it, removing is for the
rows you added.
"""
import os

import xbmc
import xbmcgui

from .. import bar_sections as B

SECTIONS, BAR, BUTTONS, CATALOGS, ADDS, ROW_ACTIONS = 300, 310, 320, 330, 340, 350
ICON_TABS, ICONS, STYLES, PICK_BUTTONS, PICK = 370, 380, 385, 390, 395
A_LEFT, A_RIGHT, A_UP, A_DOWN = 1, 2, 3, 4
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
_t = B._t
# v5.10.505: the styles a collection row takes (the Home's collections, DexWorld's kits)
COLLECTION_CHOICES = ('collection_spotlight', 'collection_compact', 'collection_ranking', 'landscape', 'wide',
                      'collage', 'mosaic', 'stack')


def _li(label, label2='', icon='', **props):
    """A list item; its icon is both its art (ListItem.Icon) and its property 'icon'."""
    li = xbmcgui.ListItem(label=label, label2=label2, offscreen=True)
    if icon:
        li.setArt({'icon': icon, 'thumb': icon})
        li.setProperty('icon', icon)
    for key, value in props.items():
        li.setProperty(key, value)
    return li


def _icon_name(path):
    return os.path.splitext(os.path.basename(str(path)))[0].replace('-', ' ')


class BarEditor(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(BarEditor, self).__init__(*args)
        self.addon_path = kwargs.get('addon_path') or ''
        self.data = B.read()
        self.cur = 0
        self.mode = ''
        self.edit = -1
        self.rows_changed = False
        self.pick = {}
        self.icon_files = []
        self._inited = False
        self._c = {}
        self.page = None            # the open page's rows as a section (see page_section)
        self.insert_key = None      # '+ Above': new rows go above this row (its key)
        self._page_removed = set()  # the added rows the user removed (the others are always kept)
        self.pages_changed = set()
        self._pages = {}            # v5.10.545: each page's rows, kept while the editor is open
        self._styles_dirty = False  # styles are written when the editor closes (a new style reloads the skin)
        self.view = []              # v5.10.545: list position -> row index (-1: the hidden rows' fold)
        self.fold_open = False
        select = kwargs.get('select') or ''
        if select:
            for n, e in enumerate(self.entries()):
                if self.page_media(e) == ('all' if select == 'all' else select) or e.get('key') == select:
                    self.cur = n
                    break

    # ---------------------------------------------------------- helpers
    def media(self, name):
        return os.path.join(self.addon_path, 'resources', 'media', 'homeui', name)

    def ctrl(self, cid):
        if cid not in self._c:
            try:
                self._c[cid] = self.getControl(cid)
            except Exception:
                self._c[cid] = None
        return self._c[cid]

    def fill(self, cid, items, select=0):
        c = self.ctrl(cid)
        if c is None:
            return
        c.reset()
        c.addItems(items)
        if items:
            c.selectItem(max(0, min(select, len(items) - 1)))

    def pos(self, cid):
        c = self.ctrl(cid)
        try:
            return c.getSelectedPosition() if c is not None else -1
        except Exception:
            return -1

    def item(self, cid, index=None):
        c = self.ctrl(cid)
        try:
            return c.getListItem(self.pos(cid) if index is None else index)
        except Exception:
            return None

    def save(self, rows=False):
        if self.page is not None and self.page_media(self.entry()):
            self.save_page(styles=False)
            self.rows_changed = True
            return
        B.write(self.data)
        self.rows_changed = self.rows_changed or rows

    # ---------------------------------------------------------- the data
    def entries(self):
        out = [{'kind': 'home', 'key': 'home'}]
        out += [{'kind': 'builtin', 'key': key, 'name': _t(ar, en)} for key, ar, en in B.KEYS]
        out += [{'kind': 'custom', 'index': n} for n in range(len(self.data['custom']))]
        if len(self.data['custom']) < len(B.CUSTOM):
            out.append({'kind': 'add'})
        return out

    def entry(self):
        es = self.entries()
        return es[min(self.cur, len(es) - 1)]

    def custom(self):
        e = self.entry()
        if self.page_media(e):
            return self.page
        return self.data['custom'][e['index']] if e['kind'] == 'custom' else None

    # ---------------------------------------------------------- Home, Movies, TV Shows
    @staticmethod
    def page_media(e):
        """'all', 'movie' or 'series' for a section whose rows are the Home's own; '' else."""
        if e.get('kind') == 'home':
            return 'all'
        if e.get('kind') == 'builtin' and e.get('key') in ('movie', 'series'):
            return e['key']
        return ''

    def limit(self):
        return 120 if (self.custom() or {}).get('_page') else B.MAX_ROWS

    def page_section(self, media):
        """The page's rows (the Home's own and the ones added) in a section's shape."""
        from ..skinui import rows as R
        app = R.app()
        try:
            found = R.HeadlessHome(app, media).layout_rows(media)
        except Exception:
            xbmc.log('[DexHub] bar editor: rows of %s failed' % media, xbmc.LOGWARNING)
            found = []
        specs = []
        for row, hidden in app.layout.arrange(found, media):
            meta = row.meta or {}
            key = row.key
            collection = key.startswith('col:') or getattr(row, 'kind', '') == 'collection'
            specs.append({'key': key, 'title': row.title or key, 'sub': meta.get('source_label') or row.subtitle or '',
                          'style': app.layout.shape(key) or (row.shape if collection else ''), 'hidden': bool(hidden),
                          'default_hidden': bool(meta.get('default_hidden')), 'known': True,
                          'custom_id': key[3:] if key.startswith('my:') else '', 'collection': collection,
                          '_style0': app.layout.shape(key) or (row.shape if collection else ''),
                          'params': dict(meta.get('params') or {})})
        return {'name': '', 'icon': '', 'rows': specs, '_page': media, '_app': app}

    def save_page(self, styles=True, page=None):
        """The open page back to layout.json: order, hidden, the added rows, the styles
        (v5.10.545: the styles only when the editor closes: a style the Home has not
        drawn yet reloads the skin, which closed the editor under the user)."""
        from . import layout as L
        c = page or self.page
        app = c['_app']
        media = c['_page']
        before = app.layout.page(media)
        old_custom = dict((spec.get('id'), spec) for spec in before.get('custom') or [])
        rows = c['rows']
        # a built-in row unticked in a picker is not removed: it is hidden at the end
        keys = set(r['key'] for r in rows)
        for r in c.get('_known') or []:
            if r['key'] not in keys and not r.get('custom_id'):
                rows.append(dict(r, hidden=True))
        custom = []
        for r in rows:
            if r.get('custom_id') and r['custom_id'] in old_custom:
                spec = dict(old_custom[r['custom_id']])
                spec['label'] = r.get('title') or spec.get('label')
                custom.append(spec)
            elif not r.get('known'):
                params = dict(r.get('params') or {})
                spec = L.new_custom({'type': 'route', 'label': r.get('title') or '', 'source': 'catalog',
                                     'source_label': r.get('sub') or '', 'subtitle': r.get('sub') or '',
                                     'params': params, 'shape': ''})
                r.update(known=True, custom_id=spec['id'], key=L.custom_key(spec))
                custom.append(spec)
        # an added row not listed now (its source away for a while) is kept: only
        # the user's Remove takes one out
        listed = set(r.get('custom_id') for r in rows if r.get('custom_id'))
        custom += [spec for spec in before.get('custom') or []
                   if spec.get('id') not in listed and spec.get('id') not in self._page_removed]
        order = [r['key'] for r in rows]
        # only what changed: the saved order stays as it was when the rows did not
        # move, and a row's hidden switch is kept while the row is not listed now
        # (a catalog away for a while comes back hidden as it was)
        if order == c.get('_order') and not before.get('order'):
            order = None
        hidden = dict(before.get('hidden') or {})
        for r in rows:
            if bool(r.get('hidden')) != bool(r.get('default_hidden')):
                hidden[r['key']] = bool(r.get('hidden'))
            else:
                hidden.pop(r['key'], None)
        if hidden == dict(before.get('hidden') or {}):
            hidden = None
        if sorted(s.get('id') for s in custom) == sorted(s.get('id') for s in before.get('custom') or []) and \
                all(s.get('label') == old_custom.get(s.get('id'), {}).get('label') for s in custom):
            custom = None
        app.layout.save_page(media, order=order, hidden=hidden, custom=custom)
        for r in (rows if styles else ()):
            # a style is saved only when it was changed here (a collection row's
            # own shape, shown as its style, is not written as a choice)
            if (r.get('style') or '') != (r.get('_style0') or ''):
                app.layout.set_shape(r['key'], r.get('style') or '')
                r['_style0'] = r.get('style') or ''
        c['_known'] = [dict(r) for r in rows if r.get('known')]
        c['_order'] = [r['key'] for r in rows]
        self.pages_changed.add(media)

    def flush_styles(self):
        """The styles changed with Left/Right, written as the editor closes."""
        if not self._styles_dirty:
            return
        self._styles_dirty = False
        for page in list(self._pages.values()):
            try:
                self.save_page(styles=True, page=page)
            except Exception:
                xbmc.log('[DexHub] bar editor: styles of %s not saved' % page.get('_page'), xbmc.LOGWARNING)
        B.write(self.data)
        self.rows_changed = True

    def styles(self):
        from .layout import style_choices
        from ..i18n import tr
        return style_choices(tr, self.media, B._styles())

    def style_name(self, key):
        from .layout import style_choices
        from ..i18n import tr
        for k, name, _line, _preview, _group in list(self.styles()) + list(style_choices(tr, self.media, COLLECTION_CHOICES)):
            if k == (key or ''):
                return name
        return key or ''

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('bs.title', _t('تعديل الرئيسية', 'Edit Home'))
        self.setProperty('bs.desc', _t('أقسام الشريط وصفوف كل قسم', 'The bar\'s sections and the rows of each'))
        self.setProperty('bs.yourbar', _t('شريطك', 'Your bar'))
        self.fill_sections()
        self.show()
        self.focus(SECTIONS)
        self.hint()

    def fill_sections(self):
        hidden = set(self.data['hidden'])
        items = []
        for e in self.entries():
            kind = e['kind']
            if kind == 'home':
                items.append(_li(_t('الرئيسية', 'Home'), _t('دايم ظاهرة', 'Always shown'), B.ICON_PATH % 'home', kind='home'))
            elif kind == 'builtin':
                off = e['key'] in hidden
                items.append(_li(e['name'], _t('مخفي من الشريط', 'Hidden from the bar') if off else _t('ظاهر في الشريط', 'On the bar'),
                                 B.ICON_PATH % e['key'], kind='builtin', off='1' if off else ''))
            elif kind == 'custom':
                c = self.data['custom'][e['index']]
                n = len(c.get('rows') or [])
                items.append(_li(c['name'], _t('قسمك · %d كتالوج', 'Yours · %d catalogs') % n,
                                 B._icon_preview(c.get('icon') or ''), kind='custom', initial=c['name'][:1].upper()))
            else:
                items.append(_li(_t('قسم جديد', 'New section'), _t('اسم وأيقونة وكتالوجات', 'A name, an icon and catalogs'),
                                 B.MENU_ICONS + 'wand-magic-sparkles.png', kind='add'))
        for n, li in enumerate(items):
            li.setProperty('open', '1' if n == self.cur else '')
        self.fill(SECTIONS, items, self.cur)
        self.fill_bar()

    def fill_bar(self):
        hidden = set(self.data['hidden'])
        here = self.entry()
        open_key = here.get('key') or ('c%d' % (here['index'] + 1) if here['kind'] == 'custom' else '')
        items = []

        def add(key, icon, initial=''):
            items.append(_li('', '', icon, open='1' if key == open_key else '', initial=initial))
        add('home', B.ICON_PATH % 'home')
        for key in ('movie', 'series', 'servers'):
            if key not in hidden:
                add(key, B.ICON_PATH % key)
        for n, c in enumerate(self.data['custom']):
            add('c%d' % (n + 1), B._icon_preview(c.get('icon') or ''), c['name'][:1].upper())
        items.append(_li('', div='1'))
        for key in ('discover', 'live', 'library', 'dexworld', 'youtube'):
            if key not in hidden:
                add(key, B.ICON_PATH % key)
        self.fill(BAR, items)

    def show(self):
        """The right half for the section under the cursor."""
        e = self.entry()
        kind = e['kind']
        media = self.page_media(e)
        if media:
            if not self.page or self.page.get('_page') != media:
                if media not in self._pages:
                    page = self.page_section(media)
                    page['_known'] = [dict(r) for r in page['rows']]
                    page['_order'] = [r['key'] for r in page['rows']]
                    self._pages[media] = page
                self.page = self._pages[media]
                self.fold_open = False
            self.show_page(e, media)
            return
        self.page = None
        c = self.custom()
        self.setProperty('bs.custom', '1' if c is not None else '')
        if kind == 'home':
            self.setProperty('bs.name', _t('الرئيسية', 'Home'))
            self.setProperty('bs.icon', B.ICON_PATH % 'home')
            self.setProperty('bs.sub', _t('قسم أساسي', 'Built in'))
            self.setProperty('bs.note', _t('الرئيسية دايم في أول الشريط. رتّب صفوفها من «ترتيب الرئيسية».',
                                           'The Home is always first on the bar. Arrange its rows from Home layout.'))
            buttons = [_li(_t('دايم ظاهرة', 'Always shown'), key='', dot='on')]
        elif kind == 'builtin':
            off = e['key'] in self.data['hidden']
            self.setProperty('bs.name', e['name'])
            self.setProperty('bs.icon', B.ICON_PATH % e['key'])
            self.setProperty('bs.sub', _t('قسم أساسي', 'Built in'))
            self.setProperty('bs.note', _t('تخفيه يشيله من الشريط بس، وصفوفه وإعداداته تبقى محفوظة لين ترجعه.',
                                           'Hiding it only takes it off the bar; its rows and settings stay saved until you bring it back.'))
            buttons = [_li(_t('مخفي من الشريط', 'Hidden from the bar') if off else _t('ظاهر في الشريط', 'On the bar'),
                           key='toggle', dot='off' if off else 'on')]
        elif kind == 'add':
            self.setProperty('bs.name', _t('قسم جديد', 'New section'))
            self.setProperty('bs.icon', B.MENU_ICONS + 'wand-magic-sparkles.png')
            self.setProperty('bs.sub', _t('لين ثلاث أقسام خاصة فيك', 'Up to three of your own'))
            self.setProperty('bs.note', _t('قسم باسمك في الشريط، بأيقونة تختارها (منها أيقونات Arctic Fuse 3)، وكتالوجات من صفوف Dex Hub أو من أي إضافة في كودي، ولكل كتالوج شكله.',
                                           'A section of your own on the bar, with an icon you pick (Arctic Fuse 3\'s too), catalogs from Dex Hub\'s rows or any Kodi add-on, each in its own style.'))
            buttons = [_li(_t('+ سوّ القسم', '+ Make the section'), key='create')]
        else:
            n = len(c.get('rows') or [])
            self.setProperty('bs.name', c['name'])
            self.setProperty('bs.icon', B._icon_preview(c.get('icon') or ''))
            self.setProperty('bs.initial', c['name'][:1].upper())
            self.setProperty('bs.sub', _t('قسمك · %d كتالوج', 'Yours · %d catalogs') % n)
            self.setProperty('bs.catalogs', _t('الكتالوجات', 'CATALOGS'))
            self.setProperty('bs.empty', _t('ما فيه كتالوجات للحين. أضف من صفوف Dex Hub أو من أي إضافة.',
                                            'No catalogs yet. Add some from Dex Hub\'s rows or any add-on.'))
            buttons = [_li(_t('تغيير الاسم', 'Rename'), key='rename'), _li(_t('الأيقونة', 'Icon'), key='icon'),
                       _li(_t('حذف القسم', 'Delete section'), key='delete')]
            self.fill(ADDS, [_li(_t('+ إضافة صفوف', '+ Add rows'), key='sources'),
                             _li(_t('شكل واحد للكل', 'One style for all'), key='allstyle')])
            self.fill_catalogs()
        self.fill(BUTTONS, buttons)

    def show_page(self, e, media):
        """Home, Movies or TV Shows: their rows on the right, edited like a section's."""
        c = self.page
        self.setProperty('bs.custom', '1')
        name = _t('الرئيسية', 'Home') if media == 'all' else e['name']
        shown = len([r for r in c['rows'] if not r.get('hidden')])
        self.setProperty('bs.name', name)
        self.setProperty('bs.icon', B.ICON_PATH % ('home' if media == 'all' else media))
        self.setProperty('bs.initial', '')
        self.setProperty('bs.sub', _t('قسم أساسي · %d صف ظاهر من %d', 'Built in · %d of %d rows shown') % (shown, len(c['rows'])))
        self.setProperty('bs.catalogs', _t('الصفوف', 'ROWS'))
        self.setProperty('bs.empty', _t('ما فيه صفوف.', 'No rows.'))
        if media == 'all':
            buttons = [_li(_t('دايم ظاهرة', 'Always shown'), key='', dot='on')]
        else:
            off = e['key'] in self.data['hidden']
            buttons = [_li(_t('مخفي من الشريط', 'Hidden from the bar') if off else _t('ظاهر في الشريط', 'On the bar'),
                           key='toggle', dot='off' if off else 'on')]
        self.fill(ADDS, [_li(_t('+ إضافة صفوف', '+ Add rows'), key='sources'),
                         _li(_t('إظهار وإخفاء', 'Show and hide'), key='showhide'),
                         _li(_t('شكل واحد للكل', 'One style for all'), key='allstyle')])
        self.fill_catalogs()
        self.fill(BUTTONS, buttons)

    @staticmethod
    def catalog_line(spec):
        parts = [_t('مخفي', 'Hidden')] if spec.get('hidden') else []
        parts.append(spec.get('sub') or '')
        if spec.get('sort'):
            parts.append('↕ ' + B.sort_name(spec['sort']))
        return '  ·  '.join(p for p in parts if p)

    def catalog_item(self, spec, editing=False):
        style = spec.get('style') or ''
        return _li(spec.get('title') or '', self.catalog_line(spec), thumb=self.media('rowpreview_%s.jpg' % (style or 'auto')),
                   style=self.style_name(style) if style else _t('كوليكشن', 'Collection'),
                   editing='1' if editing else '', off='1' if spec.get('hidden') else '')

    def fill_catalogs(self, select=0):
        """v5.10.545: the shown rows, then one line for the hidden ones (OK opens
        them under it): a page of 120 rows was a wall to scroll through."""
        c = self.custom()
        rows = (c.get('rows') or []) if c else []
        shown = [i for i, r in enumerate(rows) if not r.get('hidden')]
        hidden = [i for i, r in enumerate(rows) if r.get('hidden')]
        self.view = list(shown)
        if hidden:
            self.view.append(-1)
            if self.fold_open:
                self.view += hidden
        items = []
        for i in self.view:
            if i < 0:
                items.append(_li((_t('إخفاء الصفوف المخفية', 'Fold the hidden rows') if self.fold_open
                                  else _t('الصفوف المخفية · %d', 'Hidden rows · %d') % len(hidden)),
                                 _t('OK يفتحها', 'OK shows them') if not self.fold_open else '',
                                 fold='1', open='1' if self.fold_open else ''))
            else:
                items.append(self.catalog_item(rows[i]))
        self.fill(CATALOGS, items, self.vpos(select))

    def rix(self, pos=None):
        """The row index under list position pos (the cursor's); -1 for the fold."""
        pos = self.pos(CATALOGS) if pos is None else pos
        return self.view[pos] if 0 <= pos < len(self.view) else -1

    def vpos(self, index):
        """The list position of row index (the first position when it is not listed)."""
        try:
            return self.view.index(index)
        except ValueError:
            return 0

    def refresh_catalog(self, index):
        c = self.custom()
        if index not in self.view:
            return
        li = self.item(CATALOGS, self.vpos(index))
        if not c or li is None or index >= len(c['rows']):
            return
        spec = c['rows'][index]
        style = spec.get('style') or ''
        li.setLabel(spec.get('title') or '')
        li.setLabel2(self.catalog_line(spec))
        li.setProperty('thumb', self.media('rowpreview_%s.jpg' % (style or 'auto')))
        li.setProperty('style', self.style_name(style) if style else _t('كوليكشن', 'Collection'))
        li.setProperty('editing', '1' if index == self.edit else '')
        li.setProperty('off', '1' if spec.get('hidden') else '')

    def after_section_change(self, select_rows=None):
        """The section's name, icon or catalogs changed: the left list and the bar again."""
        self.fill_sections()
        self.show()
        if select_rows is not None:
            self.fill_catalogs(select_rows)

    def hint(self):
        focus = self.getFocusId()
        if self.edit >= 0:
            text = _t('فوق وتحت: المكان    يمين ويسار: الشكل    OK: تم', 'Up/Down: place    Left/Right: style    OK: done')
        elif focus == SECTIONS:
            text = _t('OK يفتح القسم أو يخفيه ويظهره    رجوع: إغلاق', 'OK opens a section or switches it    Back: close')
        elif focus == CATALOGS and self.rix() < 0:
            text = _t('OK يفتح الصفوف المخفية أو يطويها', 'OK opens or folds the hidden rows')
        elif focus == CATALOGS:
            text = _t('يمين ويسار: الشكل    OK: الخيارات    رجوع: الأقسام', 'Left/Right: style    OK: options    Back: sections')
        elif focus == ICONS:
            text = _t('OK يختار الأيقونة    Back رجوع', 'OK picks the icon    Back returns')
        elif focus == PICK and self.pick.get('source') == 'dexhub':
            text = _t('OK يحدد الكتالوج، وبعدها «حفظ»', 'OK ticks a catalog, then Save')
        elif focus == PICK and self.pick.get('many'):
            text = _t('OK يحدد المجلد، وبعدها «أضف المحدد»', 'OK ticks a folder, then Add the ticked')
        elif focus == PICK and self.pick.get('source') == 'sources':
            text = _t('OK يفتح المصدر    رجوع: إلغاء', 'OK opens the source    Back: cancel')
        elif focus == PICK and self.pick.get('source') == 'addons':
            text = _t('OK يفتح الإضافة    Back رجوع', 'OK opens the add-on    Back returns')
        elif focus == PICK:
            text = _t('OK يفتح المجلد    Back مجلد لورا', 'OK opens the folder    Back goes up a folder')
        elif focus == BUTTONS:
            text = _t('OK على الزر    رجوع: الأقسام', 'OK on a button    Back: sections')
        elif focus == ADDS:
            text = _t('OK على الزر    تحت: الصفوف    رجوع: الأقسام', 'OK on a button    Down: the rows    Back: sections')
        elif focus == ICON_TABS:
            text = _t('مصدر الأيقونات    تحت: الأيقونات', 'Where the icons come from    Down: the icons')
        elif focus == PICK_BUTTONS:
            text = _t('OK على الزر    تحت: القائمة', 'OK on a button    Down: the list')
        elif focus == ROW_ACTIONS:
            text = _t('الترتيب: OK للي بعده    ◀ رجوع للكتالوج', 'Sort: OK for the next order    ◀ back to the catalog')
        elif focus == STYLES:
            text = _t('OK يطبّق الشكل على كل كتالوجات القسم', 'OK gives every catalog of the section this style')
        else:
            text = _t('Back للإغلاق', 'Back closes')
        self.setProperty('bs.hint', text)

    # ---------------------------------------------------------- input
    def onAction(self, action):
        aid = action.getId()
        focus = self.getFocusId()
        if aid in A_BACK:
            self.back()
            return
        if self.edit >= 0 and focus == CATALOGS:
            self.edit_key(aid)
            return
        if focus == CATALOGS and aid in (A_LEFT, A_RIGHT):
            # v5.10.545: the row's style, right on the row (its picture follows)
            self.step_style(self.rix(), 1 if aid == A_RIGHT else -1)
            self.hint()
            return
        if focus == SECTIONS and aid in (A_UP, A_DOWN):
            n = self.pos(SECTIONS)
            if n >= 0 and n != self.cur:
                self.cur = n
                for i in range(len(self.entries())):
                    li = self.item(SECTIONS, i)
                    if li is not None:
                        li.setProperty('open', '1' if i == n else '')
                self.fill_bar()
                self.show()
        elif focus == ICON_TABS and aid in (A_LEFT, A_RIGHT):
            li = self.item(ICON_TABS)
            if li is not None and li.getProperty('grid'):
                self.icon_grid(li.getProperty('key'))
        self.hint()

    def back(self):
        if self.edit >= 0:
            self.end_edit()
        elif self.mode == 'pick' and self.pick.get('many'):
            self.pick['many'] = False
            self.pick['checked'] = set()
            self.pick_render()
        elif self.mode == 'pick' and self.pick.get('source') == 'folder':
            self.folder_up()
        elif self.mode == 'pick' and self.pick.get('back') == 'sources':
            self.pick_sources()
        elif self.mode:
            self.insert_key = None
            self.leave(BUTTONS if self.mode == 'icons' else ADDS)
        elif self.getFocusId() in (CATALOGS, ADDS, BUTTONS, ROW_ACTIONS):
            # v5.10.545: Back goes to the sections first, then closes
            self.setFocusId(SECTIONS)
        else:
            self.close()
            return
        self.hint()

    def onClick(self, cid):
        handler = {SECTIONS: self.click_section, BUTTONS: self.click_button, ADDS: self.click_add,
                   CATALOGS: self.click_catalog, ROW_ACTIONS: self.click_row_action, ICON_TABS: self.click_icon_tab,
                   ICONS: self.click_icon, STYLES: self.click_style, PICK_BUTTONS: self.click_pick_button,
                   PICK: self.click_pick}.get(cid)
        if handler:
            handler()
        self.hint()

    def onFocus(self, cid):
        if cid == ROW_ACTIONS:
            self.sort_label()
        self.hint()

    def sort_label(self):
        return                          # v5.10.545: the buttons beside a row are gone (OK's menu has the order)
        c = self.custom()
        n = self.rix()
        li = self.item(ROW_ACTIONS, 2)
        if c and not c.get('_page') and li is not None and 0 <= n < len(c['rows']):
            li.setLabel('↕ ' + B.sort_name(c['rows'][n].get('sort')))

    # ---------------------------------------------------------- sections
    def click_section(self):
        e = self.entry()
        if self.page_media(e):
            self.setFocusId(CATALOGS if (self.page or {}).get('rows') else ADDS)
        elif e['kind'] == 'builtin':
            self.toggle()
        elif e['kind'] == 'custom':
            self.setFocusId(CATALOGS if self.custom().get('rows') else ADDS)
        elif e['kind'] == 'add':
            self.create()

    def toggle(self):
        e = self.entry()
        hidden = set(self.data['hidden'])
        hidden.symmetric_difference_update([e['key']])
        self.data['hidden'] = sorted(hidden)
        self.save()
        self.fill_sections()
        self.show()

    def create(self):
        name = (xbmcgui.Dialog().input(_t('اسم القسم', 'Section name')) or '').strip()[:24]
        if not name:
            return
        self.data['custom'].append({'name': name, 'icon': '', 'rows': []})
        self.save()
        self.cur = 1 + len(B.KEYS) + len(self.data['custom']) - 1
        self.after_section_change()
        self.setFocusId(ADDS)

    def click_button(self):
        li = self.item(BUTTONS)
        key = li.getProperty('key') if li is not None else ''
        c = self.custom()
        if key == 'toggle':
            self.toggle()
            self.setFocusId(BUTTONS)
        elif key == 'create':
            self.create()
        elif key == 'rename' and c:
            name = (xbmcgui.Dialog().input(_t('اسم القسم', 'Section name'), c['name']) or '').strip()[:24]
            if name:
                c['name'] = name
                self.save()
                self.after_section_change()
        elif key == 'icon' and c:
            self.icons_open()
        elif key == 'delete' and c:
            if xbmcgui.Dialog().yesno(c['name'], _t('تحذف هالقسم من الشريط؟', 'Remove this section from the bar?')):
                self.data['custom'].remove(c)
                self.save(rows=True)
                self.cur = max(0, self.cur - 1)
                self.after_section_change()
                self.setFocusId(SECTIONS)

    def click_add(self):
        li = self.item(ADDS)
        key = li.getProperty('key') if li is not None else ''
        if key == 'sources':
            self.pick_sources()
        elif key == 'showhide':
            self.show_hide()
        elif key == 'dexhub':
            self.pick_dexhub()
        elif key == 'dexworld':
            self.pick_dexworld()
        elif key == 'youtube':
            self.pick_youtube()
        elif key == 'addon':
            self.pick_addons()
        elif key == 'allstyle' and (self.custom() or {}).get('rows'):
            self.styles_open()

    # ---------------------------------------------------------- catalogs
    def click_catalog(self):
        n = self.rix()
        if self.edit >= 0:
            self.end_edit()
        elif n >= 0:
            self.row_menu(n)
        elif self.view and self.pos(CATALOGS) >= 0:
            # the hidden rows' line: open or fold them, the cursor stays on it
            self.fold_open = not self.fold_open
            self.fill_catalogs(-1)

    def step_style(self, n, step):
        c = self.custom()
        rows = (c or {}).get('rows') or []
        if not (0 <= n < len(rows)):
            return
        spec = rows[n]
        keys = list(COLLECTION_CHOICES) if spec.get('collection') else list(B._styles())
        at = keys.index(spec.get('style') or '') if (spec.get('style') or '') in keys else (-1 if step > 0 else 0)
        spec['style'] = keys[(at + step) % len(keys)]
        self.refresh_catalog(n)
        self._styles_dirty = True

    def row_menu(self, n):
        """v5.10.543: OK on a row is one plain menu (hide, move, style, name),
        instead of a move mode and a side list found with ▶."""
        c = self.custom()
        rows = (c or {}).get('rows') or []
        if not (0 <= n < len(rows)):
            return
        spec = rows[n]
        page = bool(c.get('_page'))
        mine = not page or bool(spec.get('custom_id')) or not spec.get('known')
        menu = []
        # v5.10.545: plain words (Kodi's font has no emoji: a label with one showed empty)
        menu = [('move', _t('نقل (فوق وتحت ثم OK)', 'Move (Up/Down, then OK)'))]
        if n > 0:
            menu.append(('top', _t('أول صف', 'Make it the first row')))
        if n < len(rows) - 1:
            menu.append(('bottom', _t('آخر صف', 'Make it the last row')))
        if page:
            menu.append(('hide', _t('إظهار الصف', 'Show this row') if spec.get('hidden')
                         else _t('إخفاء الصف', 'Hide this row')))
        menu.append(('style', _t('الشكل: %s', 'Style: %s') % (self.style_name(spec.get('style') or '') or _t('تلقائي', 'Automatic'))))
        if not page:
            menu.append(('sort', _t('الترتيب: %s', 'Order: %s') % B.sort_name(spec.get('sort') or '')))
        menu.append(('above', _t('أضف صفوف فوقه', 'Add rows above it')))
        if mine:
            menu += [('rename', _t('تغيير الاسم', 'Rename')), ('remove', _t('حذف', 'Remove'))]
        pick = xbmcgui.Dialog().select(spec.get('title') or '', [label for _k, label in menu])
        if pick < 0:
            return
        key = menu[pick][0]
        if key == 'above':
            self.insert_key = spec.get('key')
            self.pick_sources()
            return
        if key == 'hide':
            spec['hidden'] = not spec.get('hidden')
        elif key in ('up', 'down', 'top', 'bottom'):
            row = rows.pop(n)
            to = {'up': n - 1, 'down': n + 1, 'top': 0, 'bottom': len(rows)}[key]
            rows.insert(to, row)
            n = to
        elif key == 'move':
            self.edit = n
            self.setProperty('bs.edit', '1')
            self.refresh_catalog(n)
            return
        elif key == 'style':
            choices = list(self.styles())
            if spec.get('collection'):
                from .layout import style_choices
                from ..i18n import tr
                choices = list(style_choices(tr, self.media, COLLECTION_CHOICES))
            keys = [k for k, _name, _line, _preview, _group in choices]
            at = keys.index(spec.get('style') or '') if (spec.get('style') or '') in keys else 0
            chosen = xbmcgui.Dialog().select(_t('شكل الصف', 'Row style'), [name for _k, name, _l, _p, _g in choices], preselect=at)
            if chosen < 0:
                return
            spec['style'] = keys[chosen]
            self._styles_dirty = True
        elif key == 'sort':
            keys = [k for k, _ar, _en in B.SORTS]
            names = [_t(ar, en) for _k, ar, en in B.SORTS]
            at = keys.index(spec.get('sort') or '') if (spec.get('sort') or '') in keys else 0
            chosen = xbmcgui.Dialog().select(_t('ترتيب الصف', 'Row order'), names, preselect=at)
            if chosen < 0:
                return
            spec['sort'] = keys[chosen]
        elif key in ('rename', 'remove'):
            if page:
                self.page_row_action(key, spec, n)
                return
            if key == 'remove':
                if not xbmcgui.Dialog().yesno(spec.get('title') or '', _t('تحذف هالصف؟', 'Remove this row?')):
                    return
                rows.pop(n)
                self.save(rows=True)
                self.after_section_change(select_rows=max(0, min(n, len(rows) - 1)))
                self.setFocusId(CATALOGS if rows else ADDS)
                return
            name = (xbmcgui.Dialog().input(_t('اسم الصف', 'Row name'), spec.get('title') or '') or '').strip()
            if not name:
                return
            spec['title'] = name
            if (spec.get('params') or {}).get('action') == 'kodi_route_browse':
                spec['params']['title'] = name
        self.save(rows=True)
        if page:
            self.show_page(self.entry(), c['_page'])
        else:
            self.fill_catalogs(n)
        self.pick_row(n)

    def show_hide(self):
        """Every row of the page in one checklist: ticked rows show."""
        c = self.custom()
        rows = (c or {}).get('rows') or []
        if not rows:
            return
        shown = [i for i, r in enumerate(rows) if not r.get('hidden')]
        picked = xbmcgui.Dialog().multiselect(_t('الصفوف الظاهرة (شيل العلامة عشان تخفي)', 'Rows shown (untick to hide)'),
                                              [r.get('title') or '' for r in rows], preselect=shown)
        if picked is None:
            return
        keep = set(picked)
        for i, spec in enumerate(rows):
            spec['hidden'] = i not in keep
        self.save(rows=True)
        self.show_page(self.entry(), c['_page'])
        self.pick_row(0)

    def end_edit(self):
        n = self.edit
        self.edit = -1
        self.clearProperty('bs.edit')
        self.save(rows=True)
        self.fill_catalogs(n)

    def edit_key(self, aid):
        c = self.custom()
        rows = c['rows']
        if aid in (A_LEFT, A_RIGHT):
            keys = list(B._styles())
            spec = rows[self.edit]
            if spec.get('collection'):
                keys = list(COLLECTION_CHOICES)
            at = keys.index(spec.get('style') or '') if (spec.get('style') or '') in keys else 0
            spec['style'] = keys[(at + (1 if aid == A_RIGHT else -1)) % len(keys)]
            self._styles_dirty = True
            self.refresh_catalog(self.edit)
        elif aid in (A_UP, A_DOWN):
            # the list moved already: the edited catalog follows the cursor
            n = self.rix()
            if n < 0:
                # not onto the hidden rows' line
                try:
                    self.ctrl(CATALOGS).selectItem(self.vpos(self.edit))
                except (RuntimeError, AttributeError):
                    pass
                return
            if n != self.edit and 0 <= n < len(rows):
                rows[n], rows[self.edit] = rows[self.edit], rows[n]
                old, self.edit = self.edit, n
                self.refresh_catalog(old)
                self.refresh_catalog(n)
        self.hint()

    def click_row_action(self):
        li = self.item(ROW_ACTIONS)
        key = li.getProperty('key') if li is not None else ''
        c = self.custom()
        n = self.rix()
        if not c or not (0 <= n < len(c['rows'])):
            return
        spec = c['rows'][n]
        if key == 'top':
            c['rows'].insert(0, c['rows'].pop(n))
            self.save(rows=True)
            self.fill_catalogs(0)
            self.pick_row(0)
            return
        if key == 'above':
            self.add_above(spec)
            return
        if c.get('_page'):
            self.page_row_action(key, spec, n)
            return
        if key == 'sort':
            # each OK the next order; the row is read again in it
            keys = [k for k, _ar, _en in B.SORTS]
            at = keys.index(spec.get('sort') or '') if (spec.get('sort') or '') in keys else 0
            spec['sort'] = keys[(at + 1) % len(keys)]
            self.save(rows=True)
            self.refresh_catalog(n)
            self.sort_label()
            return
        if key == 'rename':
            name = (xbmcgui.Dialog().input(_t('اسم الصف', 'Row name'), spec.get('title') or '') or '').strip()
            if name:
                spec['title'] = name
                if (spec.get('params') or {}).get('action') == 'kodi_route_browse':
                    spec['params']['title'] = name
                self.save(rows=True)
                self.refresh_catalog(n)
        elif key == 'remove':
            c['rows'].pop(n)
            self.save(rows=True)
            self.after_section_change(select_rows=min(n, len(c['rows']) - 1))
        self.setFocusId(CATALOGS if c['rows'] else ADDS)

    def page_row_action(self, key, spec, n):
        c = self.page
        mine = bool(spec.get('custom_id')) or not spec.get('known')
        if key == 'hide':
            spec['hidden'] = not spec.get('hidden')
            self.save(rows=True)
            self.refresh_catalog(n)
            self.show_page(self.entry(), c['_page'])
            self.pick_row(n)
            return
        if key in ('rename', 'remove') and not mine:
            xbmcgui.Dialog().notification('Dex Hub', _t('صفوف Dex Hub الأساسية تنخفي بس، الحذف والاسم للصفوف اللي أضفتها',
                                                        "Dex Hub's own rows can only be hidden; rename and remove are for the rows you added"),
                                          xbmcgui.NOTIFICATION_INFO, 4000)
            return
        if key == 'rename':
            name = (xbmcgui.Dialog().input(_t('اسم الصف', 'Row name'), spec.get('title') or '') or '').strip()
            if name:
                spec['title'] = name
                self.save(rows=True)
                self.refresh_catalog(n)
        elif key == 'remove':
            c['rows'].pop(n)
            self.page['_known'] = [r for r in self.page['_known'] if r['key'] != spec['key']]
            if spec.get('custom_id'):
                self._page_removed.add(spec['custom_id'])
            self.save(rows=True)
            self.show_page(self.entry(), c['_page'])
            self.pick_row(min(n, len(c['rows']) - 1))
        self.setFocusId(CATALOGS)

    def add_above(self, spec):
        """'+ Above': where the new rows come from; they go above this row."""
        sources = [('dexhub', _t('صفوف Dex Hub', 'Dex Hub rows')), ('dexworld', 'DexWorld'), ('youtube', 'YouTube'),
                   ('nas', 'NAS'), ('addon', _t('من إضافة', 'From an add-on'))]
        pick = xbmcgui.Dialog().select(_t('أضف فوق «%s»', 'Add above "%s"') % (spec.get('title') or ''),
                                       [label for _k, label in sources])
        if pick < 0:
            return
        self.insert_key = spec.get('key')
        {'dexhub': self.pick_dexhub, 'dexworld': self.pick_dexworld, 'youtube': self.pick_youtube,
         'nas': self.pick_nas, 'addon': self.pick_addons}[sources[pick][0]]()
        if self.mode != 'pick':
            self.insert_key = None          # nothing opened (no rows to pick from)

    def place_new(self, rows, fresh):
        """The new rows go above the row '+ Above' was pressed on, else first, where
        they are seen (v5.10.504: at the end of a page of 100 rows nobody saw them)."""
        at = 0
        if self.insert_key is not None:
            for i, r in enumerate(rows):
                if r.get('key') == self.insert_key:
                    at = i
                    break
        self.insert_key = None
        return rows[:at] + list(fresh) + rows[at:], at

    def pick_row(self, n):
        try:
            self.ctrl(CATALOGS).selectItem(self.vpos(max(0, n)))
        except (RuntimeError, AttributeError):
            pass
        self.setFocusId(CATALOGS)

    # ---------------------------------------------------------- modes
    def focus(self, cid):
        """Focus a control of the half that the mode just showed: Kodi takes
        the focus only once the control is visible (a frame or two later)."""
        for _n in range(15):
            if xbmc.getCondVisibility('Control.IsVisible(%d)' % cid):
                break
            xbmc.sleep(20)
        self.setFocusId(cid)

    def enter(self, mode, focus):
        self.mode = mode
        self.setProperty('bs.mode', mode)
        self.focus(focus)

    def leave(self, focus):
        self.mode = ''
        self.pick = {}
        self.clearProperty('bs.mode')
        self.focus(focus)

    # ---------------------------------------------------------- icons
    def icons_open(self):
        self.icon_files = []
        af3 = B._files(B.AF3_ICONS)
        self.icon_sources = [('bar', _t('أيقونات الشريط', 'Bar icons'), True),
                             ('menu', 'Dex Hub', True)]
        if af3:
            self.icon_sources += [('af3', 'Arctic Fuse 3', True), ('search', _t('بحث', 'Search'), False)]
        self.icon_sources += [('picture', _t('صورتك', 'Your picture'), False), ('none', _t('بدون', 'None'), False)]
        self.fill(ICON_TABS, [_li(name, key=key, grid='1' if grid else '') for key, name, grid in self.icon_sources],
                  2 if af3 else 0)
        self.setProperty('bs.ptitle', _t('أيقونة القسم', 'Section icon'))
        self.setProperty('bs.pempty', _t('ما فيه أيقونة بهالاسم', 'No icon by that name'))
        self.icon_grid('af3' if af3 else 'bar')
        self.enter('icons', ICONS)

    def icon_grid(self, source, word=''):
        current = B.icon_texture((self.custom() or {}).get('icon') or '')
        if source == 'bar':
            found = [(B.ICON_PATH % k, _t(ar, en), B.NAV % k) for k, ar, en in B.ICONS]
        else:
            folders = [B.MENU_ICONS] if source == 'menu' else [B.AF3_ICONS] if source == 'af3' else [B.AF3_ICONS, B.MENU_ICONS]
            found = []
            for folder in folders:
                for f in B._files(folder):
                    if word and word not in f.lower():
                        continue
                    found.append((folder + f, _icon_name(f), folder + f))
        self.icon_files = found
        select = 0
        items = []
        for n, (preview, name, value) in enumerate(found):
            cur = value == current
            select = n if cur else select
            items.append(_li(name, icon=preview, cur='1' if cur else ''))
        label = {'bar': _t('أيقونات الشريط', 'Bar icons'), 'menu': 'Dex Hub', 'af3': 'Arctic Fuse 3'}.get(source, word)
        self.setProperty('bs.psub', _t('%s · %d أيقونة', '%s · %d icons') % (label, len(found)))
        self.fill(ICONS, items, select)

    def click_icon_tab(self):
        li = self.item(ICON_TABS)
        key = li.getProperty('key') if li is not None else ''
        if key in ('bar', 'menu', 'af3'):
            self.icon_grid(key)
            self.setFocusId(ICONS)
        elif key == 'search':
            word = (xbmcgui.Dialog().input(_t('اسم الأيقونة بالإنجليزي (film أو tv أو star)', 'Icon name (film, tv, star...)')) or '').strip().lower()
            if word:
                self.icon_grid('search', word)
                if self.icon_files:
                    self.setFocusId(ICONS)
        elif key == 'picture':
            path = xbmcgui.Dialog().browse(2, _t('اختر صورة', 'Pick a picture'), 'files', '.png|.jpg', True, False)
            if path:
                self.set_icon(path)
        elif key == 'none':
            self.set_icon('')

    def click_icon(self):
        n = self.pos(ICONS)
        if 0 <= n < len(self.icon_files):
            self.set_icon(self.icon_files[n][2])

    def set_icon(self, value):
        c = self.custom()
        if c is None:
            return
        c['icon'] = value
        self.save()
        self.after_section_change()
        self.leave(BUTTONS)

    # ---------------------------------------------------------- one style for all
    def styles_open(self):
        rows = self.custom()['rows']
        same = set(r.get('style') or '' for r in rows)
        items = [_li(name, line, thumb=preview, key=key, cur='1' if same == {key} else '')
                 for key, name, line, preview, _group in self.styles()]
        self.fill(STYLES, items)
        self.setProperty('bs.ptitle', _t('شكل واحد للكل', 'One style for all'))
        self.setProperty('bs.psub', _t('%d كتالوج بنفس الشكل', '%d catalogs in the same style') % len(rows))
        self.enter('styles', STYLES)

    def click_style(self):
        li = self.item(STYLES)
        c = self.custom()
        if li is None or c is None:
            return
        for spec in c['rows']:
            if not spec.get('collection'):
                spec['style'] = li.getProperty('key')
        self._styles_dirty = True
        self.save(rows=True)
        self.fill_catalogs()
        self.leave(CATALOGS)

    # ---------------------------------------------------------- adding catalogs
    SOURCES = (('dexhub', 'صفوف Dex Hub', 'Dex Hub rows', 'الكتالوجات والقوائم اللي في الرئيسية',
                'The catalogs and lists of the Home', 'star.png'),
               ('dexworld', 'DexWorld', 'DexWorld', 'صفوف وكوليكشنات جاهزة', 'Ready rows and collections', 'globe.png'),
               ('youtube', 'YouTube', 'YouTube', 'قنواتك والرائج والمواضيع، أو أي بحث', 'Your channels, trending, topics or any search',
                'circle-play.png'),
               ('nas', 'NAS ومجلدات الشبكة', 'NAS and network folders', 'أفلام ومسلسلات وجديد مجلداتك (SMB و NFS)',
                'Films, series and the newest of your folders (SMB, NFS)', 'server.png'),
               ('addon', 'من إضافة', 'From an add-on', 'مجلد أو كم مجلد من أي إضافة فيديو', 'A folder or several from any video add-on',
                'layer-group.png'))

    def pick_sources(self):
        """v5.10.545: where new rows come from, as one list in the window (it was a
        row of + buttons running off the screen)."""
        self.pick = {'source': 'sources'}
        self.setProperty('bs.ptitle', _t('إضافة صفوف', 'Add rows'))
        self.setProperty('bs.psub', _t('اختر المصدر، حدد صف أو أكثر، وتنضاف فوق', 'Pick a source, tick one row or more; they go to the top'))
        self.setProperty('bs.pempty', '')
        self.pick_render(0)
        self.enter('pick', PICK)

    def open_source(self, key):
        {'dexhub': self.pick_dexhub, 'dexworld': self.pick_dexworld, 'youtube': self.pick_youtube,
         'nas': self.pick_nas, 'addon': self.pick_addons}[key]()
        if self.mode == 'pick' and self.pick.get('source') != 'sources':
            self.pick['back'] = 'sources'

    def pick_dexhub(self):
        found = B.candidates()
        if not found:
            xbmcgui.Dialog().notification('Dex Hub', _t('افتح الرئيسية مرة وارجع', 'Open the Home once and come back'), time=3000, sound=False)
            return
        import json
        chosen = set(json.dumps(r.get('params') or {}, sort_keys=True) for r in self.custom()['rows'])
        self.pick = {'source': 'dexhub', 'found': found,
                     'checked': set(i for i, (spec, _w) in enumerate(found) if json.dumps(spec['params'], sort_keys=True) in chosen)}
        self.setProperty('bs.ptitle', _t('صفوف Dex Hub', 'Dex Hub rows'))
        self.setProperty('bs.psub', _t('الرئيسية والأفلام والمسلسلات · حدد كم واحد تبي', 'Home, Movies and Series · tick as many as you like'))
        self.pick_render(0)
        self.enter('pick', PICK)

    def pick_list(self, found, title, sub):
        """Tick rows from a list of (spec, where), then Save (the Dex Hub rows' picker)."""
        import json
        chosen = set(json.dumps(r.get('params') or {}, sort_keys=True) for r in self.custom()['rows'])
        self.pick = {'source': 'dexhub', 'found': found,
                     'checked': set(i for i, (spec, _w) in enumerate(found) if json.dumps(spec['params'], sort_keys=True) in chosen)}
        self.setProperty('bs.ptitle', title)
        self.setProperty('bs.psub', sub)
        self.pick_render(0)
        self.enter('pick', PICK)

    def pick_dexworld(self):
        from .. import dexworld as DW
        xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
        try:
            data = DW.page()
        finally:
            xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
        found = [(DW.spec_of(row), (row.get('cat') or 'DexWorld')) for row in data.get('rows') or [] if row.get('id') and row.get('title')]
        if not found:
            xbmcgui.Dialog().notification('DexWorld', _t('الموقع ما رد الحين، جرّب بعد شوي', 'The site did not answer; try again shortly'),
                                          xbmcgui.NOTIFICATION_INFO, 3500)
            return
        self.pick_list(found, 'DexWorld', _t('قوائم وكوليكشن DexWorld · حدد كم واحد تبي', "DexWorld's lists and collections · tick as many as you like"))

    def pick_youtube(self):
        from .. import youtube_feed as Y
        xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
        try:
            found = Y.row_choices()
        finally:
            xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
        search = ({'key': 'youtube:ask', 'title': _t('ابحث وأضف…', 'Search and add…'), 'params': {'action': 'youtube_ask'}},
                  _t('أي بحث كصف', 'Any search as a row'))
        self.pick_list([search] + found, 'YouTube',
                       _t('حسابك، البث المباشر، الرائج، الأقسام، وقنواتك · حدد كم واحد تبي',
                          'Your account, live, trending, the topics and your channels · tick as many as you like'))

    def pick_nas(self):
        """v5.10.550: a NAS folder's films, series or newest files as rows (Kodi's
        own SMB/NFS folders are taken in first)."""
        from .. import nas_sources as N
        xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
        try:
            if N.import_kodi_sources():
                N.scan()
            elif N.is_set_up() and not (N._index().get('files') or []):
                N.scan()
            found = N.row_choices()
        finally:
            xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
        if not found:
            if xbmcgui.Dialog().yesno('NAS', _t('ما فيه مجلدات شبكة فيها أفلام أو مسلسلات للحين. تبي تضيف NAS أو مجلد؟',
                                               'No network folder with films or series yet. Add a NAS or a folder?')):
                N.setup()
                found = N.row_choices()
            if not found:
                return
        self.pick_list(found, _t('NAS ومجلدات الشبكة', 'NAS and network folders'),
                       _t('الأفلام والمسلسلات وأضيف مؤخراً لكل مجلد · حدد كم واحد تبي',
                          'Films, series and recently added for each folder · tick as many as you like'))

    def pick_addons(self):
        addons = B._rpc('Addons.GetAddons', {'type': 'xbmc.addon.video', 'enabled': True, 'properties': ['name', 'thumbnail']}).get('addons') or []
        addons = sorted((a for a in addons if a.get('addonid') and not str(a['addonid']).startswith('plugin.video.dexhub')),
                        key=lambda a: (a.get('name') or '').lower())
        self.pick = {'source': 'addons', 'addons': addons}
        self.setProperty('bs.ptitle', _t('من إضافة', 'From an add-on'))
        self.setProperty('bs.psub', _t('اختر الإضافة، وبعدها مجلد أو كم مجلد أو كلها', 'Pick the add-on, then a folder, several, or all of them'))
        self.setProperty('bs.pempty', _t('ما فيه إضافات فيديو مثبتة', 'No video add-ons installed'))
        self.pick_render(0)
        self.enter('pick', PICK)

    def folder_open(self, path, name):
        p = self.pick
        p['paths'].append(path)
        p['trail'].append(name)
        self.folder_read()

    def folder_up(self):
        p = self.pick
        p['paths'].pop()
        p['trail'].pop()
        if not p['paths']:
            self.pick_addons()
            return
        self.folder_read()

    def folder_read(self):
        p = self.pick
        p['many'] = False
        p['checked'] = set()
        self.setProperty('bs.psub', _t('يقرأ المجلد…', 'Reading the folder…'))
        files = B._rpc('Files.GetDirectory', {'directory': p['paths'][-1], 'media': 'video', 'properties': ['title', 'thumbnail']}).get('files') or []
        p['folders'] = [f for f in files if f.get('filetype') == 'directory' and f.get('file')][:B.LIST_LIMIT]
        p['playable'] = len(files) - len(p['folders'])
        self.setProperty('bs.ptitle', p['trail'][-1])
        self.setProperty('bs.psub', ' › '.join(p['trail']) + ((_t('  ·  %d عنصر', '  ·  %d items') % p['playable']) if p['playable'] else ''))
        self.setProperty('bs.pempty', _t('ما فيه مجلدات هنا. أضف هالمجلد نفسه من فوق.', 'No folders here. Add this folder itself from above.'))
        self.pick_render(0)
        self.setFocusId(PICK if p['folders'] else PICK_BUTTONS)

    def pick_render(self, select=None):
        p = self.pick
        src = p.get('source')
        if src == 'sources':
            buttons = [_li(_t('إلغاء', 'Cancel'), key='cancel')]
            rows = [_li(_t(ar, en), _t(lar, len_), (B.ICON_PATH % k) if k in ('dexworld', 'youtube') else B.MENU_ICONS + icon,
                        folder='1', glyph='1') for k, ar, en, lar, len_, icon in self.SOURCES]
        elif src == 'dexhub':
            buttons = [_li(_t('حفظ (%d)', 'Save (%d)') % len(p['checked']), key='save'), _li(_t('إلغاء', 'Cancel'), key='cancel')]
            rows = [_li(spec['title'], where, self.media('lay_catalog.png'), checkable='1', check='1' if i in p['checked'] else '')
                    for i, (spec, where) in enumerate(p['found'])]
        elif src == 'addons':
            buttons = [_li(_t('إلغاء', 'Cancel'), key='cancel')]
            rows = [_li(a.get('name') or a['addonid'], a['addonid'], a.get('thumbnail') or '', folder='1') for a in p['addons']]
        else:
            folders = p.get('folders') or []
            if p.get('many'):
                buttons = [_li(_t('أضف المحدد (%d)', 'Add the ticked (%d)') % len(p['checked']), key='add_ticked'),
                           _li(_t('إلغاء', 'Cancel'), key='cancel_many')]
            else:
                buttons = [_li(_t('أضف هالمجلد', 'Add this folder'), key='add_this')]
                if folders:
                    buttons += [_li(_t('حدد كم مجلد', 'Tick several'), key='many'),
                                _li(_t('أضف الكل (%d)', 'Add all (%d)') % len(folders), key='add_all')]
            rows = [_li(f.get('label') or f.get('title') or '', '', f.get('thumbnail') or B.MENU_ICONS + 'layer-group.png',
                        folder='' if p.get('many') else '1', checkable='1' if p.get('many') else '',
                        check='1' if i in p['checked'] else '') for i, f in enumerate(folders)]
        if select is None:
            select = self.pos(PICK) if self.mode == 'pick' else 0
        self.fill(PICK_BUTTONS, buttons)
        self.fill(PICK, rows, max(0, select))

    def click_pick(self):
        p = self.pick
        n = self.pos(PICK)
        src = p.get('source')
        if src == 'sources':
            if 0 <= n < len(self.SOURCES):
                self.open_source(self.SOURCES[n][0])
            return
        if src == 'dexhub' and 0 <= n < len(p['found']) and p['found'][n][0]['params'].get('action') == 'youtube_ask':
            words = (xbmcgui.Dialog().input(_t('ابحث في YouTube', 'Search YouTube')) or '').strip()
            if words:
                from .. import youtube_feed as Y
                p['found'].insert(n + 1, (Y.row_spec('search', words, title=words), _t('بحث', 'Search')))
                p['checked'] = set(i + 1 if i > n else i for i in p['checked']) | {n + 1}
                self.pick_render(n + 1)
            return
        if src == 'dexhub' or (src == 'folder' and p.get('many')):
            p['checked'].symmetric_difference_update([n])
            li = self.item(PICK)
            if li is not None:
                li.setProperty('check', '1' if n in p['checked'] else '')
            b = self.item(PICK_BUTTONS, 0)
            if b is not None:
                b.setLabel((_t('حفظ (%d)', 'Save (%d)') if src == 'dexhub' else _t('أضف المحدد (%d)', 'Add the ticked (%d)')) % len(p['checked']))
        elif src == 'addons' and 0 <= n < len(p['addons']):
            a = p['addons'][n]
            self.pick = {'source': 'folder', 'addon': a.get('name') or a['addonid'], 'paths': [], 'trail': []}
            self.folder_open('plugin://%s/' % a['addonid'], a.get('name') or a['addonid'])
        elif src == 'folder' and 0 <= n < len(p.get('folders') or []):
            f = p['folders'][n]
            self.folder_open(f['file'], f.get('label') or '')

    def click_pick_button(self):
        li = self.item(PICK_BUTTONS)
        key = li.getProperty('key') if li is not None else ''
        p = self.pick
        if key == 'cancel':
            self.insert_key = None
            self.leave(ADDS)
        elif key == 'save':
            self.save_dexhub()
        elif key == 'many':
            p['many'] = True
            self.pick_render()
            self.setFocusId(PICK)
        elif key == 'cancel_many':
            p['many'] = False
            p['checked'] = set()
            self.pick_render()
        elif key == 'add_this':
            # the name's dialog cancelled: nothing is added
            title = (xbmcgui.Dialog().input(_t('اسم الكتالوج', 'Catalog name'), p['trail'][-1]) or '').strip()
            if title:
                self.add_rows([B._folder_spec(p['paths'][-1], title, p['addon'])])
        elif key in ('add_ticked', 'add_all'):
            folders = p.get('folders') or []
            picked = sorted(p['checked']) if key == 'add_ticked' else range(len(folders))
            got = [B._folder_spec(folders[i]['file'], folders[i].get('label') or '', p['addon']) for i in picked]
            if got:
                self.add_rows(got)

    def save_dexhub(self):
        import json
        p = self.pick
        c = self.custom()
        idents = [json.dumps(spec['params'], sort_keys=True) for spec, _w in p['found']]
        p['checked'] = set(i for i in p['checked'] if p['found'][i][0]['params'].get('action') != 'youtube_ask')
        keep = [idents[i] for i in p['checked']]
        before = [json.dumps(r.get('params') or {}, sort_keys=True) for r in c['rows']]
        rows = [r for r, ident in zip(c['rows'], before) if ident not in idents or ident in keep]
        rows, at = self.place_new(rows, [dict(p['found'][i][0], style='') for i in sorted(p['checked']) if idents[i] not in before])
        if len(rows) > self.limit():
            xbmcgui.Dialog().notification('Dex Hub', _t('القسم وصل حده (%d صف)', 'The section is full (%d rows)') % self.limit(),
                                          xbmcgui.NOTIFICATION_INFO, 3500)
        c['rows'] = rows[:self.limit()]
        rows = c['rows']
        self.save(rows=True)
        if c.get('_page'):
            self.show_page(self.entry(), c['_page'])
            self.leave(CATALOGS)
            self.pick_row(min(at, max(0, len(rows) - 1)))
            return
        self.after_section_change(select_rows=min(at, max(0, len(rows) - 1)))
        self.leave(CATALOGS if rows else ADDS)

    def add_rows(self, specs):
        c = self.custom()
        start = len(c['rows'])
        # a folder once, and no more than the Home has rows for
        have = set(r.get('key') for r in c['rows'])
        fresh = []
        for spec in specs:
            if spec.get('key') not in have:
                have.add(spec.get('key'))
                fresh.append(spec)
        room = self.limit() - start
        c['rows'], start = self.place_new(c['rows'], fresh[:max(0, room)])
        left = len(specs) - len(fresh[:max(0, room)])
        if left:
            xbmcgui.Dialog().notification('Dex Hub', _t('ما انضاف %d: مكرر أو القسم وصل حده (%d صف)',
                                                        '%d not added: already there or the section is full (%d rows)') % (left, B.MAX_ROWS),
                                          xbmcgui.NOTIFICATION_INFO, 3500)
        self.save(rows=True)
        if c.get('_page'):
            self.show_page(self.entry(), c['_page'])
            self.pick_row(min(start, max(0, len(c['rows']) - 1)))
            self.leave(CATALOGS)
            return
        self.after_section_change(select_rows=min(start, max(0, len(c['rows']) - 1)))
        self.leave(CATALOGS if c['rows'] else ADDS)


def open_editor(select=''):
    """The Home's one editor; ``select`` opens it on a section (the pen: the tab on show)."""
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    window = BarEditor('dexhub_bars.xml', path, 'Default', '1080i', addon_path=path, select=select)
    try:
        window.doModal()
        window.flush_styles()
        changed = window.rows_changed
        pages = set(window.pages_changed)
    finally:
        del window
    if pages or changed:
        # one request for the layout and the sections, every tab at once
        _home_changed()
    return True


def _home_changed():
    """The Home's rows again, every tab, now (the worker's 'home editor'); with no
    worker running (the add-on's own Home), they are worked out here."""
    try:
        from ..skinui import common as C
        from ..skinui.actions import worker_alive
        if worker_alive():
            from ..skinui import home_layouts
            home_layouts.force_next()       # v5.10.532: the user's change shows at once
            C.ask_republish('home editor')
        else:
            from ..skinui import rows
            rows.publish(reason='home editor')
    except Exception:
        xbmc.log('[DexHub] bar editor: the Home was not asked to rebuild', xbmc.LOGWARNING)
