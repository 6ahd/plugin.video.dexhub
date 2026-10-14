# -*- coding: utf-8 -*-
"""The Dex Community window (v5.10.195): browse the ready catalogs and add
them to the Home, Movies or Series page (community.py has the data).

WindowXMLDialog on dexhub_community*.xml (gen_community_xml.py):

  100  the pages a catalog goes to: Home, Movies, Series
  200  the groups (Streaming services, Genres, Moods...)
  300  the group's folders as cards (three posters each)
  400  a folder's catalogs, each with the first posters it shows (detail)
  500  the detail's buttons: add all, take all out
  600  the source of the titles, close

OK on a catalog adds it to the chosen page (again takes it out); Menu on
it offers the other pages. Posters are read in the background, a few at a
time, and kept a day.
"""
import threading
from .. import lazy_tb as traceback
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcgui

from . import community as CM

SECTIONS, GROUPS, FOLDERS, CATALOGS, DBUTTONS, HBUTTONS, PICKS = 100, 200, 300, 400, 500, 600, 700
A_LEFT, A_RIGHT, A_UP, A_DOWN, A_PGUP, A_PGDN = 1, 2, 3, 4, 5, 6
A_CONTEXT, A_MENU = 117, 163
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
MOVES = (A_LEFT, A_RIGHT, A_UP, A_DOWN, A_PGUP, A_PGDN)
# the groups' names in Arabic (the collection names them in English)
GROUP_AR = {'Spotlight': 'المختارات', 'Discover': 'اكتشف', 'Genres': 'التصنيفات', 'Moods & Vibes': 'المزاج والأجواء',
            'Based on': 'مأخوذ عن', 'Film Collections': 'سلاسل الأفلام', 'Actors': 'الممثلون',
            'Legendary Directors': 'مخرجون كبار', 'Studios': 'الاستوديوهات', 'By Decade': 'حسب العقد',
            'Anime': 'أنمي', 'Awards': 'الجوائز', 'International Cinema': 'سينما عالمية',
            'Documentaries': 'وثائقيات', 'Kids and Family': 'أطفال وعائلة', 'Reality TV': 'برامج الواقع',
            'Streaming Services': 'منصات البث', 'Networks': 'الشبكات'}


class CommunityWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(CommunityWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.page = kwargs.get('page') if kwargs.get('page') in ('all', 'movie', 'series') else 'all'
        self.changed = set()
        self.groups = []
        self.group_index = 0
        self.folders = []
        self.folder = None
        self.items = {}
        self._ctrls = {}
        self._inited = False
        self._closing = False
        self._gen = 0
        self._pool = ThreadPoolExecutor(max_workers=4)
        self._lock = threading.Lock()
        self.previews = CM.Previews(self.app.profile)
        self._timer = None
        self.picks = []         # [(group, folder)] chosen for one collection row (v5.10.197)

    def tr(self, text):
        return self.app.tr(text)

    def group_name(self, group):
        name = group['name']
        try:
            from ..i18n import is_english
            if is_english():
                return name
        except Exception:
            return name
        return GROUP_AR.get(name, name)

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        for cid in (SECTIONS, GROUPS, FOLDERS, CATALOGS, DBUTTONS, HBUTTONS, PICKS):
            try:
                self._ctrls[cid] = self.getControl(cid)
            except Exception:
                self._ctrls[cid] = None
        self.setProperty('cm.sub', self.tr('كتالوجات جاهزة، اختر ما يعجبك وينضاف لصفحتك'))
        self.setProperty('cm.src.title', self.tr('مصدر العناوين'))
        self._fill_header()
        self._fill_sections()
        self.groups = CM.groups()
        if not self.groups:
            self.setProperty('cm.msg', self.tr('تعذر قراءة الكتالوجات'))
            return
        self._fill_groups()
        self._show_group(0)
        self._focus(GROUPS)
        self._ask_source_once()

    def close_window(self):
        self._closing = True
        self._gen += 1
        try:
            self._pool.shutdown(wait=False)
        except Exception:
            pass
        self.previews.save()
        self.close()

    # ------------------------------------------------------------ helpers
    def _focus(self, cid):
        try:
            self.setFocusId(cid)
        except Exception:
            pass

    def _set_items(self, cid, items):
        ctrl = self._ctrls.get(cid)
        if ctrl is None:
            return
        ctrl.reset()
        ctrl.addItems(items)
        self.items[cid] = items

    def _pos(self, cid):
        ctrl = self._ctrls.get(cid)
        try:
            return int(ctrl.getSelectedPosition()) if ctrl is not None else -1
        except Exception:
            return -1

    def _layout(self):
        return self.app.layout

    def _added(self, page=None):
        return CM.added(self._layout(), page or self.page)

    # -------------------------------------------------------------- header
    def _fill_header(self):
        state, line = CM.source_state(self.tr)
        self.setProperty('cm.src', line)
        items = []
        for key, text in (('import', self.tr('استيراد')), ('share', self.tr('مشاركة')),
                          ('source', self.tr('مصدر البيانات')), ('close', self.tr('إغلاق'))):
            li = xbmcgui.ListItem(label=text)
            li.setProperty('key', key)
            items.append(li)
        self._set_items(HBUTTONS, items)

    def _fill_sections(self):
        items = []
        for key, text in CM.PAGES:
            li = xbmcgui.ListItem(label=self.tr(text))
            li.setProperty('key', key)
            if key == self.page:
                li.setProperty('on', '1')
            items.append(li)
        self._set_items(SECTIONS, items)
        index = [k for k, _t in CM.PAGES].index(self.page)
        try:
            self._ctrls[SECTIONS].selectItem(index)
        except Exception:
            pass

    def _fill_groups(self):
        items = []
        for index, group in enumerate(self.groups):
            folders = CM.folders_for(group, self.page)
            li = xbmcgui.ListItem(label=self.group_name(group), label2=str(len(folders)))
            if group.get('set_name'):
                # v5.10.211: an imported collection's group says whose it is
                li.setProperty('set', group['set_name'])
            if index == self.group_index:
                li.setProperty('on', '1')
            items.append(li)
        self._set_items(GROUPS, items)
        try:
            self._ctrls[GROUPS].selectItem(self.group_index)
        except Exception:
            pass

    # ------------------------------------------------------------- folders
    def _show_group(self, index):
        if not self.groups:
            return
        index = max(0, min(index, len(self.groups) - 1))
        self.group_index = index
        group = self.groups[index]
        for i, li in enumerate(self.items.get(GROUPS) or []):
            li.setProperty('on', '1' if i == index else '')
        self.folders = CM.folders_for(group, self.page)
        page_name = dict(CM.PAGES).get(self.page, '')
        self.setProperty('cm.group', self.group_name(group))
        self.setProperty('cm.group.line', self.tr('%d مجلد • OK للمعاينة والإضافة إلى %s') % (
            len(self.folders), self.tr(page_name)))
        self.setProperty('cm.msg', '' if self.folders else self.tr('لا شيء هنا لهذه الصفحة'))
        added = self._added()
        home = CM.nuvio_home()
        on_nuvio = bool(group.get('entry_id') or group['id']) and \
            (group.get('entry_id') in home[1] or group['id'] in home[1])
        if on_nuvio:
            self.setProperty('cm.group.line', self.getProperty('cm.group.line') + '  •  ' +
                             self.tr('المجموعة في رئيستك من Nuvio'))
        items = []
        for folder in self.folders:
            catalogs = [c for c in folder['catalogs'] if CM.fits(c, self.page)]
            li = xbmcgui.ListItem(label=folder['name'], label2=self._count_line(catalogs))
            count = sum(1 for c in catalogs if CM.key_of(group['id'], folder['id'], c['idx']) in added
                        or CM.on_nuvio_home(group, c, home))
            if count:
                li.setProperty('added', str(count))
            # v5.10.197: the folder's own cover when it has one
            if folder.get('cover'):
                li.setArt({'cover': folder['cover']})
                li.setProperty('cshape', 'wide' if folder.get('shape') == 'landscape' else 'tall')
            if self._picked(group, folder):
                li.setProperty('sel', '1')
            items.append(li)
        self._set_items(FOLDERS, items)
        self._fill_picks()
        self._gen += 1
        self._load_folder_posters(group, [f for f in self.folders if not f.get('cover')],
                                  [li for f, li in zip(self.folders, items) if not f.get('cover')], self._gen)

    # ------------------------------------------------- one row of folders
    def _picked(self, group, folder):
        return any(g['id'] == group['id'] and f['id'] == folder['id'] for g, f in self.picks)

    def _toggle_pick(self, folder, li=None):
        group = self.groups[self.group_index]
        if group.get('loose') or any(g.get('set_id') != group.get('set_id') for g, _f in self.picks):
            xbmcgui.Dialog().notification('Dex Community', self.tr('الصف الواحد من مجموعة وحدة')
                                          if not group.get('loose') else self.tr('كتالوجات هذه المجموعة تنضاف كصفوف'),
                                          time=2500, sound=False)
            return
        if self._picked(group, folder):
            self.picks = [(g, f) for g, f in self.picks if not (g['id'] == group['id'] and f['id'] == folder['id'])]
            on = False
        else:
            self.picks.append((group, folder))
            on = True
        if li is not None:
            li.setProperty('sel', '1' if on else '')
        self._fill_picks()

    def _fill_picks(self):
        group = self.groups[self.group_index] if self.groups else None
        have = group is not None and CM.group_key(group) in self._added()
        items = []
        whole = xbmcgui.ListItem(label=self.tr('المجموعة مثبتة كصف') if have else self.tr('ثبّت المجموعة كصف'))
        whole.setProperty('key', 'group')
        if have:
            whole.setProperty('on', '1')
        items.append(whole)
        n = len(self.picks)
        chosen = xbmcgui.ListItem(label=(self.tr('ثبّت المختارة (%d)') % n) if n else self.tr('القائمة: اختر مجلدات'))
        chosen.setProperty('key', 'picks')
        if n:
            chosen.setProperty('on', '1')
        items.append(chosen)
        self._set_items(PICKS, items)

    def _pin_group(self):
        group = self.groups[self.group_index]
        if group.get('loose'):
            xbmcgui.Dialog().notification('Dex Community', self.tr('كتالوجات هذه المجموعة تنضاف كصفوف'),
                                          time=2500, sound=False)
            return
        layout = self._layout()
        name = self.tr(dict(CM.PAGES)[self.page])
        try:
            if CM.group_key(group) in CM.added(layout, self.page):
                CM.remove(layout, self.page, CM.group_key(group))
                message = self.tr('أُزيل من %s') % name
            else:
                CM.add_group(layout, self.page, group, label=self.group_name(group))
                message = self.tr('أُضيف إلى %s') % name
            self.changed.add(self.page)
        except Exception:
            self.app.log('community: group row failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            message = self.tr('تعذر الحفظ')
        xbmcgui.Dialog().notification('Dex Community', message, time=1800, sound=False)
        self._fill_picks()

    def _pin_picks(self):
        if not self.picks:
            xbmcgui.Dialog().notification('Dex Community', self.tr('اضغط القائمة على المجلدات اللي تبيها'),
                                          time=2500, sound=False)
            return
        groups = set(g['id'] for g, _f in self.picks)
        default = self.group_name(self.picks[0][0]) if len(groups) == 1 else self.tr('اختياراتي')
        kb = xbmc.Keyboard(default, self.tr('اسم الصف'))
        kb.doModal()
        if not kb.isConfirmed():
            return
        label = kb.getText().strip() or default
        try:
            CM.add_folders(self._layout(), self.page, label, self.picks)
            self.changed.add(self.page)
            message = self.tr('أُضيف %d إلى %s') % (len(self.picks), self.tr(dict(CM.PAGES)[self.page]))
            self.picks = []
        except Exception:
            self.app.log('community: folders row failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            message = self.tr('تعذر الحفظ')
        xbmcgui.Dialog().notification('Dex Community', message, time=2000, sound=False)
        self._show_group(self.group_index)

    def _count_line(self, catalogs):
        n = len(catalogs)
        if n == 1:
            return self.tr('كتالوج واحد')
        if n == 2:
            return self.tr('كتالوجان')
        if n <= 10:
            return self.tr('%d كتالوجات') % n
        return self.tr('%d كتالوج') % n

    def _first_catalog(self, folder):
        return next((c for c in folder['catalogs'] if CM.fits(c, self.page)), folder['catalogs'][0])

    def _load_folder_posters(self, group, folders, items, gen):
        api = self.app.api()

        def one(folder, li):
            if self._closing or gen != self._gen:
                return
            catalog = self._first_catalog(folder)
            key = CM.key_of(group['id'], folder['id'], catalog['idx'])
            posters = self.previews.read(key, CM.params_of(group['id'], folder['id'], catalog['idx']), api)
            if self._closing or gen != self._gen:
                return
            li.setArt(dict(('p%d' % (n + 1), p) for n, p in enumerate(posters[:3])))
        for folder, li in list(zip(folders, items))[:48]:
            hit = self.previews.get(CM.key_of(group['id'], folder['id'], self._first_catalog(folder)['idx']))
            if hit is not None:
                li.setArt(dict(('p%d' % (n + 1), p) for n, p in enumerate(hit[:3])))
                continue
            try:
                self._pool.submit(one, folder, li)
            except Exception:
                return

    # -------------------------------------------------------------- detail
    def _open_folder(self, index):
        if not (0 <= index < len(self.folders)):
            return
        group = self.groups[self.group_index]
        self.folder = self.folders[index]
        self._fill_detail()
        items = []
        picked = self._picked(group, self.folder)
        for key, text in (('all', self.tr('أضف الكل')), ('none', self.tr('شيل الكل')),
                          ('pick', self.tr('مختار للصف') if picked else self.tr('اختره للصف'))):
            li = xbmcgui.ListItem(label=text)
            li.setProperty('key', key)
            items.append(li)
        self._set_items(DBUTTONS, items)
        self.setProperty('cm.d.path', '%s  ›  %s' % (self.group_name(group), self.folder['name']))
        self.setProperty('cm.d.title', self.folder['name'])
        self.setProperty('cm.detail', '1')
        self._focus(CATALOGS)

    def _detail_catalogs(self):
        return [c for c in self.folder['catalogs'] if CM.fits(c, self.page)] if self.folder else []

    def _fill_detail(self):
        group = self.groups[self.group_index]
        catalogs = self._detail_catalogs()
        added_by_page = dict((page, self._added(page)) for page, _t in CM.PAGES)
        names = dict((k, self.tr(t)) for k, t in CM.PAGES)
        page_name = names.get(self.page, '')
        self.setProperty('cm.d.line', '%s  •  %s' % (self._count_line(catalogs),
                                                     self.tr('OK يضيف إلى %s، القائمة لصفحة ثانية') % page_name))
        pos = self._pos(CATALOGS)
        home = CM.nuvio_home()
        items = []
        for catalog in catalogs:
            key = CM.key_of(group['id'], self.folder['id'], catalog['idx'])
            on = [names[p] for p, _t in CM.PAGES if key in added_by_page[p]]
            kind = self.tr('أفلام') if catalog['media'] == 'movie' else self.tr('مسلسلات')
            li = xbmcgui.ListItem(label=catalog['name'], label2=kind)
            if on:
                li.setProperty('added', self.tr('مضاف إلى: %s') % '، '.join(on))
            elif CM.on_nuvio_home(group, catalog, home):
                # v5.10.213: on the Home already through the user's Nuvio
                li.setProperty('added', self.tr('موجود في رئيستك من Nuvio  •  OK يضيفه هنا كمان'))
            li.setProperty('hint', self.tr('غير مضاف • OK يضيفه إلى %s') % page_name)
            hit = self.previews.get(key)
            if hit:
                li.setArt(dict(('p%d' % (n + 1), p) for n, p in enumerate(hit)))
            items.append(li)
        self._set_items(CATALOGS, items)
        if 0 < pos < len(items):
            try:
                self._ctrls[CATALOGS].selectItem(pos)
            except Exception:
                pass
        self._gen += 1
        gen = self._gen
        api = self.app.api()
        folder = self.folder

        def one(catalog, li):
            if self._closing or gen != self._gen:
                return
            key = CM.key_of(group['id'], folder['id'], catalog['idx'])
            posters = self.previews.read(key, CM.params_of(group['id'], folder['id'], catalog['idx']), api, count=9)
            if not self._closing and gen == self._gen:
                li.setArt(dict(('p%d' % (n + 1), p) for n, p in enumerate(posters)))
        for catalog, li in zip(catalogs, items):
            if self.previews.get(CM.key_of(group['id'], folder['id'], catalog['idx'])) is None or \
                    len(self.previews.get(CM.key_of(group['id'], folder['id'], catalog['idx'])) or []) < 6:
                try:
                    self._pool.submit(one, catalog, li)
                except Exception:
                    break

    def _close_folder(self):
        self.setProperty('cm.detail', '')
        index = self.folders.index(self.folder) if self.folder in self.folders else 0
        self.folder = None
        self._show_group(self.group_index)
        try:
            self._ctrls[FOLDERS].selectItem(index)
        except Exception:
            pass
        self._focus(FOLDERS)

    def _toggle(self, catalog, page):
        group = self.groups[self.group_index]
        key = CM.key_of(group['id'], self.folder['id'], catalog['idx'])
        layout = self._layout()
        try:
            if key in CM.added(layout, page):
                CM.remove(layout, page, key)
                message = self.tr('أُزيل من %s') % self.tr(dict(CM.PAGES)[page])
            else:
                CM.add(layout, page, group, self.folder, catalog)
                message = self.tr('أُضيف إلى %s') % self.tr(dict(CM.PAGES)[page])
            self.changed.add(page)
        except Exception:
            self.app.log('community: change failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            message = self.tr('تعذر الحفظ')
        xbmcgui.Dialog().notification('Dex Community', message, time=1800, sound=False)
        self._fill_detail()

    def _catalog_menu(self, catalog):
        group = self.groups[self.group_index]
        key = CM.key_of(group['id'], self.folder['id'], catalog['idx'])
        options = []
        for page, text in CM.PAGES:
            if not CM.fits(catalog, page):
                continue
            on = key in self._added(page)
            options.append((page, (self.tr('شيله من %s') if on else self.tr('أضفه إلى %s')) % self.tr(text)))
        from .options import choose
        choice = choose(catalog['name'], [label for _p, label in options], subtitle='Dex Community')
        if choice >= 0:
            self._toggle(catalog, options[choice][0])

    def _all(self, add):
        group = self.groups[self.group_index]
        layout = self._layout()
        count = 0
        for catalog in self._detail_catalogs():
            key = CM.key_of(group['id'], self.folder['id'], catalog['idx'])
            have = key in CM.added(layout, self.page)
            try:
                if add and not have:
                    count += 1 if CM.add(layout, self.page, group, self.folder, catalog) else 0
                elif not add and have:
                    count += 1 if CM.remove(layout, self.page, key) else 0
            except Exception:
                self.app.log('community: change failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        if count:
            self.changed.add(self.page)
        page_name = self.tr(dict(CM.PAGES)[self.page])
        message = (self.tr('أُضيف %d إلى %s') if add else self.tr('أُزيل %d من %s')) % (count, page_name)
        xbmcgui.Dialog().notification('Dex Community', message, time=1800, sound=False)
        self._fill_detail()

    # -------------------------------------------------------------- source
    def _ask_source_once(self):
        state, _line = CM.source_state(self.tr)
        if state != 'shared':
            return
        import os
        flag = os.path.join(self.app.profile, 'community_asked')
        if os.path.exists(flag):
            return
        try:
            open(flag, 'w').close()
        except Exception:
            pass
        self._source_menu(first=True)

    def _source_menu(self, first=False):
        state, line = CM.source_state(self.tr)
        options = [('key', self.tr('مفتاح TMDb الخاص بي')), ('aio', self.tr('رابط AIOMetadata الخاص بي'))]
        if CM.aio_url():
            options.append(('unlink', self.tr('إلغاء ربط AIOMetadata')))
        options.append(('later', self.tr('لاحقًا') if first else self.tr('رجوع')))
        from .options import choose
        subtitle = self.tr('الكتالوجات تُقرأ من TMDb بمفتاحك، أو من AIOMetadata حقك إذا فيه الكتالوج')
        choice = choose(self.tr('مصدر العناوين'), [label for _k, label in options], subtitle=subtitle)
        if choice < 0:
            return
        key = options[choice][0]
        if key == 'key':
            try:
                from ..routes.simple_entry import tmdb_api_key_dialog
                tmdb_api_key_dialog()
            except Exception:
                self.app.log('community: TMDb key dialog failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        elif key == 'aio':
            kb = xbmc.Keyboard(CM.aio_url(), self.tr('رابط AIOMetadata (manifest)'))
            kb.doModal()
            if kb.isConfirmed() and kb.getText().strip():
                ok, message = CM.link_aio(kb.getText())
                xbmcgui.Dialog().notification(
                    'Dex Community', (self.tr('تم الربط: %s') % message) if ok else self.tr(message), time=2500,
                    sound=False)
        elif key == 'unlink':
            CM.unlink_aio()
        self._fill_header()

    # -------------------------------------------------------------- import
    def _resolver(self):
        # no questions about missing add-ons here: the catalogs are read the
        # Community's way (the user's AIOMetadata, else TMDb), and an add-on
        # Dex Hub knows is linked on its own
        return None

    def _import_menu(self):
        """v5.10.211: a collection of the user's own (Nuvio, Kaptain or Fusion,
        from a link or a JSON file) browsed here like the Dex Collection."""
        from .options import choose
        options = [('url', self.tr('من رابط')), ('file', self.tr('من ملف JSON'))]
        mine = CM.imported_sets()
        if mine:
            options.append(('remove', self.tr('إزالة مجموعة مستوردة')))
        choice = choose(self.tr('استيراد مجموعة'), [label for _k, label in options],
                        subtitle=self.tr('Nuvio وKaptain وFusion: مجلداتها تظهر هنا وتضيفها لصفحاتك'))
        if choice < 0:
            return
        key = options[choice][0]
        ok, message = False, ''
        if key == 'url':
            kb = xbmc.Keyboard('', self.tr('رابط المجموعة'))
            kb.doModal()
            if not kb.isConfirmed() or not kb.getText().strip():
                return
            xbmcgui.Dialog().notification('Dex Community', self.tr('يقرأ المجموعة…'), time=1500, sound=False)
            ok, message = CM.import_url(kb.getText().strip(), self._resolver())
        elif key == 'file':
            path = xbmcgui.Dialog().browse(1, self.tr('ملف المجموعة (JSON)'), 'files', '.json')
            if not path:
                return
            ok, message = CM.import_file(path, self._resolver(), layout=self._layout())
            if ok and message.startswith('Dex Community'):
                self.changed.update(page for page, _n in CM.PAGES)
        elif key == 'remove':
            names = [str(r.get('name') or r.get('id')) for r in mine]
            pick = choose(self.tr('إزالة مجموعة مستوردة'), names)
            if pick < 0:
                return
            if not xbmcgui.Dialog().yesno('Dex Community', self.tr('نشيل «%s» من المجموعات؟') % names[pick]):
                return
            CM.remove_imported(mine[pick].get('id'))
            ok, message = True, self.tr('أُزيلت')
            self._reload_groups(None)
            xbmcgui.Dialog().notification('Dex Community', message, time=2000, sound=False)
            return
        if not ok:
            xbmcgui.Dialog().notification('Dex Community', self.tr(message) or self.tr('تعذر الاستيراد'),
                                          xbmcgui.NOTIFICATION_WARNING, 3500)
            return
        xbmcgui.Dialog().notification('Dex Community', self.tr('أُضيفت: %s') % message, time=2500, sound=False)
        self._reload_groups(last=True)

    def _share(self):
        """v5.10.212: the rows added here and their collections, as one file
        another Dex Hub opens with Import."""
        data = CM.share_data(self._layout())
        rows = sum(len(v) for v in data['pages'].values())
        if not rows and not data['collections']:
            xbmcgui.Dialog().notification('Dex Community', self.tr('ما فيه شي تشاركه للحين'), time=2500, sound=False)
            return
        folder = xbmcgui.Dialog().browse(3, self.tr('وين تحفظ ملف المشاركة؟'), 'files')
        if not folder:
            return
        import json as _json
        import os as _os
        import time as _time
        import xbmcvfs
        name = 'dexhub-community-%s.json' % _time.strftime('%Y%m%d-%H%M')
        path = folder + name if folder.endswith(('/', '\\')) else _os.path.join(folder, name)
        try:
            handle = xbmcvfs.File(path, 'w')
            try:
                handle.write(_json.dumps(data, ensure_ascii=False))
            finally:
                handle.close()
        except Exception as exc:
            xbmcgui.Dialog().ok('Dex Community', self.tr('تعذر الحفظ: %s') % exc)
            return
        xbmcgui.Dialog().ok('Dex Community', self.tr('انحفظ: %d صف و%d مجموعة\n%s\n\nاللي يستلمه يفتحه من استيراد ← من ملف JSON')
                            % (rows, len(data['collections']), path))

    def _reload_groups(self, last=False):
        self.groups = CM.groups()
        if not self.groups:
            return
        index = len(self.groups) - 1 if last else min(self.group_index, len(self.groups) - 1)
        if last:
            # the first group of the collection just added
            newest = self.groups[-1].get('set_id')
            index = next((i for i, g in enumerate(self.groups) if g.get('set_id') == newest), index)
        self.group_index = index
        self._fill_groups()
        self._show_group(index)
        self._focus(GROUPS)

    # --------------------------------------------------------------- input
    def onClick(self, cid):
        if cid == SECTIONS:
            pos = self._pos(SECTIONS)
            if 0 <= pos < len(CM.PAGES) and CM.PAGES[pos][0] != self.page:
                self.page = CM.PAGES[pos][0]
                self._fill_sections()
                self._fill_groups()
                self._show_group(self.group_index)
        elif cid == GROUPS:
            self._show_group(self._pos(GROUPS))
            self._focus(FOLDERS)
        elif cid == FOLDERS:
            self._open_folder(self._pos(FOLDERS))
        elif cid == CATALOGS:
            catalogs = self._detail_catalogs()
            pos = self._pos(CATALOGS)
            if 0 <= pos < len(catalogs):
                self._toggle(catalogs[pos], self.page)
        elif cid == DBUTTONS:
            pos = self._pos(DBUTTONS)
            if pos == 2:
                self._toggle_pick(self.folder)
                li = (self.items.get(DBUTTONS) or [None, None, None])[2]
                if li is not None:
                    li.setLabel(self.tr('مختار للصف') if self._picked(self.groups[self.group_index], self.folder)
                                else self.tr('اختره للصف'))
            else:
                self._all(pos == 0)
        elif cid == PICKS:
            if self._pos(PICKS) == 0:
                self._pin_group()
            else:
                self._pin_picks()
        elif cid == HBUTTONS:
            pos = self._pos(HBUTTONS)
            if pos == 0:
                self._import_menu()
            elif pos == 1:
                self._share()
            elif pos == 2:
                self._source_menu()
            else:
                self.close_window()

    def _group_moved(self):
        pos = self._pos(GROUPS)
        if pos == self.group_index:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(0.35, self._settle_group, args=(pos,))
        self._timer.daemon = True
        self._timer.start()

    def _settle_group(self, pos):
        if self._closing or self._pos(GROUPS) != pos:
            return
        try:
            self._show_group(pos)
        except Exception:
            self.app.log('community: group failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    def onAction(self, action):
        aid = action.getId()
        focus = self.getFocusId()
        if aid in A_BACK:
            if self.getProperty('cm.detail'):
                self._close_folder()
            else:
                self.close_window()
            return
        if aid in (A_CONTEXT, A_MENU) and focus == CATALOGS and self.folder:
            catalogs = self._detail_catalogs()
            pos = self._pos(CATALOGS)
            if 0 <= pos < len(catalogs):
                self._catalog_menu(catalogs[pos])
            return
        if aid in (A_CONTEXT, A_MENU) and focus == FOLDERS:
            pos = self._pos(FOLDERS)
            if 0 <= pos < len(self.folders):
                self._toggle_pick(self.folders[pos], (self.items.get(FOLDERS) or [])[pos])
            return
        if aid in MOVES and focus == GROUPS:
            self._group_moved()


def open_community(app, page='all'):
    """Show the window; returns the pages whose rows changed."""
    from . import ui_size
    window = CommunityWindow(ui_size.xml('dexhub_community.xml'), app.addon_path, 'Default', '1080i',
                             app=app, page=page)
    try:
        window.doModal()
        return set(window.changed)
    finally:
        del window
