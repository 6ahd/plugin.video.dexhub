# -*- coding: utf-8 -*-
"""The screensaver and alerts page (v5.10.334).

One page instead of the lists of select dialogs: the screensaver's style
(Cinema, Spotlight, Your List, Film news; v5.10.357), the sources with a Show switch (the
screensaver) and an Alerts switch (new episodes, the island), TMDb's lists
as one more source, and the timing (seconds a title stays, the order, the
clock, how often the lists are read again, films or series).

Layout: resources/skins/*/1080i/dexhub_watchsources.xml (gen_page.py).
  list 100 the styles (label, art thumb, prop on)
  list 200 the sources (label, label2, props letter, color, show, show_on,
           alerts, alerts_on); Left and Right move between the card and its
           two switches (window prop wp.col 0, 1, 2), OK acts on that one
  list 300 the timing tiles (label, label2)
  buttons 51 to 54: preview, the screensaver on or off, refresh, alerts read
"""
import os
import threading

import xbmc
import xbmcgui

from .. import watch_sources as W

STYLE_LIST, SOURCE_LIST, TILE_LIST, ADD = 100, 200, 300, 210
A_LEFT, A_RIGHT = 1, 2
# v5.10.357: Up next became Your List (same key), News joined
STYLES = (('cinema', 'سينما', 'Cinema'), ('spotlight', 'سبوتلايت', 'Spotlight'), ('upnext', 'Your List', 'Your List'),
          ('news', 'أخبار السينما', 'Film news'))
COLORS = {'silo': ('S', 'FF3FB0F0'), 'trakt': ('T', 'FFED1C24'), 'mdblist': ('M', 'FF3B82F6'), 'catalog': ('H', 'FFE5484D'),
          'continue': ('D', 'FFE5484D'), 'favorites': ('D', 'FFE5484D'), 'tmdb': ('T', 'FF0DB5D8')}
KINDS = {'silo': 'Silo', 'trakt': 'Trakt', 'mdblist': 'MDBList', 'catalog': ('الرئيسية', 'Home'), 'continue': 'Dex Hub',
         'favorites': 'Dex Hub', 'tmdb': 'TMDb'}


def _t(ar, en):
    from ..i18n import is_english
    return en if is_english() else ar


def _addon_path():
    import xbmcaddon
    from .screensaver import ADDON_ID
    return xbmcaddon.Addon(ADDON_ID).getAddonInfo('path')


def _age(seconds):
    if seconds < 0:
        return _t('لم تُقرأ بعد', 'Not read yet')
    minutes = int(seconds // 60)
    if minutes < 1:
        return _t('قُرئت الآن', 'Refreshed just now')
    if minutes < 60:
        return _t('قُرئت قبل %d د', 'Refreshed %d min ago') % minutes
    return _t('قُرئت قبل %d س', 'Refreshed %d h ago') % (minutes // 60)


class Page(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(Page, self).__init__(*args)
        self.col = 0
        self.changed = False
        self.rows = []
        self._busy = False

    # ------------------------------------------------------------ fill
    def onInit(self):
        # This dialog also opens before Home/theme startup finishes. Its
        # focus textures must not depend on a shared Home property being set.
        self.setProperty('wp.assets', _addon_path())
        self.setProperty('wp.caption', _t('شاشة التوقف والتنبيهات', 'Screensaver & alerts'))
        self.setProperty('wp.title', _t('شاشة التوقف', 'Screensaver'))
        self.setProperty('wp.sub', _t('شكل شاشة التوقف، ووش تعرض، ومن وين تجيك التنبيهات',
                                      'How the screensaver looks, what it shows, and where alerts come from'))
        self.setProperty('wp.sources', _t('المصادر', 'Sources'))
        self.setProperty('wp.add', _t('إضافة مصدر', 'Add source'))
        self.setProperty('wp.col', '0')
        self.fill()

    def fill(self):
        self._styles()
        self._sources()
        self._tiles()
        self._nav()

    def _styles(self):
        look = W.look()
        thumbs = os.path.join(_addon_path(), 'resources', 'media', 'screensaver')
        items = []
        for key, ar, en in STYLES:
            li = xbmcgui.ListItem(label=_t(ar, en), offscreen=True)
            li.setArt({'thumb': os.path.join(thumbs, 'style_%s.jpg' % key)})
            li.setProperty('on', '1' if look.get('style') == key else '')
            li.setProperty('key', key)
            items.append(li)
        self._refill(STYLE_LIST, items)

    def _sources(self):
        cfg = W.config()
        counts, _age_s, _total = W.counts()
        rows = []
        for source in cfg['sources']:
            kind = source.get('kind') or ''
            letter, color = COLORS.get(kind, ('D', 'FFE5484D'))
            name = KINDS.get(kind, kind.title())
            if isinstance(name, tuple):
                name = _t(*name)
            n = counts.get(source['id'], 0)
            rows.append({'id': source['id'], 'label': source.get('label') or name, 'kind': kind,
                         'sub': '%s  ·  %s' % (name, (_t('%d عمل', '%d titles') % n) if n else _t('بانتظار التحديث', 'waiting for a refresh')),
                         'letter': letter, 'color': color, 'show': bool(source.get('display', True)),
                         'alerts': bool(source.get('monitor', True)), 'alerts_ok': True})
        from . import screensaver as SS
        keep = SS.chosen_sections()
        tmdb_on = bool(keep) and not cfg.get('personal_only')
        rows.append({'id': 'tmdb', 'label': _t('قوائم TMDb', 'TMDb lists'), 'kind': 'tmdb',
                     'sub': 'TMDb  ·  ' + ((_t('%d من %d قوائم', '%d of %d lists') % (len(keep), len(SS.SECTION_KEYS)))
                                          if tmdb_on else _t('متوقفة', 'Off')),
                     'letter': 'T', 'color': COLORS['tmdb'][1], 'show': tmdb_on,
                     'alerts': False, 'alerts_ok': False})
        self.rows = rows
        items = []
        for row in rows:
            li = xbmcgui.ListItem(label=row['label'], label2=row['sub'], offscreen=True)
            li.setProperty('letter', row['letter'])
            li.setProperty('color', row['color'])
            li.setProperty('show', _t('عرض', 'Show'))
            li.setProperty('show_on', '1' if row['show'] else '')
            li.setProperty('alerts', _t('تنبيهات', 'Alerts') if row['alerts_ok'] else '')
            li.setProperty('alerts_on', '1' if row['alerts'] else '')
            items.append(li)
        self._refill(SOURCE_LIST, items)

    def _tiles(self):
        look = W.look()
        from . import screensaver as SS
        media = SS.chosen_media()
        refresh = int(look.get('refresh') or 1)
        tiles = [
            ('slide', _t('العمل التالي كل', 'Next title every'), _t('%d ث', '%d s') % int(look.get('slide') or 14)),
            ('order', _t('الترتيب', 'Order'), _t('عشوائي', 'Shuffle') if look.get('order') != 'newest'
             else _t('اللي تتابعه أولاً', 'Watching first')),
            ('clock', _t('الساعة', 'Clock'), _t('شغالة', 'On') if look.get('clock') is not False else _t('مطفأة', 'Off')),
            ('refresh', _t('التحديث', 'Refresh'), _t('كل ساعة', 'Every hour') if refresh == 1
             else _t('كل %d ساعات', 'Every %d hours') % refresh),
            ('media', _t('تعرض', 'Shows'), {'both': _t('أفلام ومسلسلات', 'Films & series'),
                                            'movie': _t('أفلام فقط', 'Films only'),
                                            'series': _t('مسلسلات فقط', 'Series only')}[media]),
        ]
        # v5.10.357: Your List shows its list first, News its sources
        if look.get('style') == 'upnext':
            from .ss_windows import yourlist_source
            source = yourlist_source() or {}
            tiles = [('yourlist', 'Your List', source.get('label') or _t('اختر قائمة', 'Pick a list'))] + \
                [t for t in tiles if t[0] != 'order']
        elif look.get('style') == 'news':
            from .ss_windows import news_sources
            from . import ss_news
            keys = news_sources()
            names = [label for key, label, _k, _u in ss_news.SOURCES if key in keys]
            tiles = [('news', _t('مصادر الأخبار', 'News sources'),
                      names[0] + (' +%d' % (len(names) - 1) if len(names) > 1 else '') if names else '—')] + \
                [t for t in tiles if t[0] in ('slide', 'clock')]
        items = []
        for key, label, value in tiles:
            li = xbmcgui.ListItem(label=label, label2=value, offscreen=True)
            li.setProperty('key', key)
            items.append(li)
        self._refill(TILE_LIST, items)

    def _nav(self):
        from . import screensaver as SS
        on = SS.current_mode() == SS.ADDON_ID
        values = ((_t('افتحها الآن', 'Preview'), ''),
                  (_t('شاشة توقف Dex Hub', 'Dex Hub screensaver'), _t('شغالة', 'On') if on else _t('مطفأة', 'Off')),
                  (_t('تحديث المصادر', 'Refresh sources'), ''),
                  (_t('التنبيهات مقروءة', 'Mark alerts read'), ''))
        for n, (label, value) in enumerate(values):
            self.setProperty('wp.nav%d' % n, label)
            self.setProperty('wp.nav%d.value' % n, value)
        counts, age, total = W.counts()
        self.setProperty('wp.refreshed', '%s  ·  %s' % (_age(age), _t('%d عمل', '%d titles') % total))

    def _refill(self, cid, items):
        try:
            ctrl = self.getControl(cid)
            pos = ctrl.getSelectedPosition()
            ctrl.reset()
            ctrl.addItems(items)
            if 0 <= pos < len(items):
                ctrl.selectItem(pos)
        except Exception:
            pass

    # ------------------------------------------------------------ keys
    def onAction(self, action):
        aid = action.getId()
        if self.getFocusId() == SOURCE_LIST and aid in (A_LEFT, A_RIGHT):
            row = self._row()
            last = 2 if row and row['alerts_ok'] else 1
            if aid == A_RIGHT:
                self.col = min(last, self.col + 1)
            elif self.col == 0:
                self.setFocusId(50)
            else:
                self.col -= 1
            self.setProperty('wp.col', str(self.col))
            return
        if aid in (9, 10, 92, 216, 247, 257, 275, 61448, 61467):
            self.close()

    def onFocus(self, control_id):
        if control_id != SOURCE_LIST:
            self.col = 0
            self.setProperty('wp.col', '0')

    def _row(self):
        try:
            return self.rows[self.getControl(SOURCE_LIST).getSelectedPosition()]
        except Exception:
            return None

    def onClick(self, control_id):
        if self._busy:
            return
        try:
            if control_id == STYLE_LIST:
                key = self.getControl(STYLE_LIST).getSelectedItem().getProperty('key')
                before = W.look()
                W.set_look('style', key)
                # v5.10.357: Your List asks for its list (the first time, or
                # OK on it again); News for its sources (OK on it again)
                if key == 'upnext' and (before.get('style') == 'upnext' or not before.get('yourlist')):
                    pick_yourlist()
                elif key == 'news' and before.get('style') == 'news':
                    from . import ss_windows
                    ss_windows.pick_news_sources()
                self.changed = True
                self._styles()
                self._sources()
                self._tiles()
            elif control_id == ADD:
                self._add()
            elif control_id == SOURCE_LIST:
                self._source_click()
            elif control_id == TILE_LIST:
                self._tile(self.getControl(TILE_LIST).getSelectedItem().getProperty('key'))
            elif control_id == 51:
                self._done()
                from . import screensaver as SS
                SS.preview()
            elif control_id == 52:
                from . import screensaver as SS
                if SS.current_mode() == SS.ADDON_ID:
                    SS.disable()
                else:
                    SS.enable()
                self._nav()
            elif control_id == 53:
                self._refresh()
            elif control_id == 54:
                import json
                try:
                    events = json.loads(xbmcgui.Window(10000).getProperty('dhs.newep.list') or '[]')
                    W.mark_read([e['key'] for e in events if e.get('key')])
                    self.changed = True
                except (ValueError, TypeError):
                    pass
                xbmcgui.Dialog().notification('Dex Hub', _t('التنبيهات صارت مقروءة', 'Alerts marked as read'),
                                              xbmcgui.NOTIFICATION_INFO, 2000, sound=False)
        except Exception:
            import traceback
            xbmc.log('[DexHub] screensaver page:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)

    # ------------------------------------------------------------ actions
    def _source_click(self):
        row = self._row()
        if not row:
            return
        if row['id'] == 'tmdb':
            # v5.10.335: TMDb's lists go off and on like any source (Show),
            # OK on the card: show, choose the lists, or turn them off
            from . import screensaver as SS
            action = 'toggle' if self.col == 1 else None
            if action is None:
                choices = [_t('عرض في شاشة التوقف', 'Show in the screensaver') + ('  ✓' if row['show'] else ''),
                           _t('اختيار القوائم', 'Choose the lists'),
                           _t('إيقاف قوائم TMDb', 'Turn TMDb lists off')]
                pick = xbmcgui.Dialog().select(row['label'], choices)
                if pick < 0:
                    return
                action = ('toggle', 'choose', 'off')[pick]
            if action == 'choose':
                SS.pick_sources()
                if SS.chosen_sections():
                    SS.set_tmdb(True)
            else:
                SS.set_tmdb(False if action == 'off' else not row['show'])
            self.changed = True
            self._sources()
            return
        cfg = W.config()
        source = next((s for s in cfg['sources'] if s['id'] == row['id']), None)
        if source is None:
            return
        if self.col in (1, 2):
            field = 'display' if self.col == 1 else 'monitor'
            source[field] = not source.get(field, True)
        else:
            choices = [_t('عرض في شاشة التوقف', 'Show in the screensaver') + ('  ✓' if source.get('display', True) else ''),
                       _t('تنبيهات الحلقات الجديدة', 'New episode alerts') + ('  ✓' if source.get('monitor', True) else ''),
                       _t('حذف المصدر', 'Remove source')]
            pick = xbmcgui.Dialog().select(source.get('label') or '', choices)
            if pick < 0:
                return
            if pick == 2:
                if not xbmcgui.Dialog().yesno('Dex Hub', _t('حذف %s من المصادر؟', 'Remove %s from the sources?')
                                              % (source.get('label') or '')):
                    return
                cfg['sources'].remove(source)
            else:
                field = ('display', 'monitor')[pick]
                source[field] = not source.get(field, True)
        W._write('watch_sources.json', cfg)
        self.changed = True
        self._sources()

    def _add(self):
        kinds = [('trakt', _t('قائمة Trakt', 'Trakt list')), ('mdblist', _t('قائمة MDBList', 'MDBList list')),
                 ('silo', _t('قائمة أو كوليكشن Silo', 'Silo watchlist or collection')),
                 ('catalog', _t('كتالوج من الرئيسية', 'Home catalog')),
                 ('tmdb', _t('قوائم TMDb', 'TMDb lists'))]
        pick = xbmcgui.Dialog().select(_t('إضافة مصدر', 'Add source'), [label for _k, label in kinds])
        if pick < 0:
            return
        kind = kinds[pick][0]
        if kind == 'tmdb':
            from . import screensaver as SS
            SS.pick_sources()
            if SS.chosen_sections():
                SS.set_tmdb(True)
        else:
            W._add(kind)
        self.changed = True
        self._sources()

    def _tile(self, key):
        look = W.look()
        if key == 'yourlist':
            pick_yourlist()
            self._sources()
        elif key == 'news':
            from . import ss_windows
            ss_windows.pick_news_sources()
        elif key == 'slide':
            steps = W.SLIDES
            now = int(look.get('slide') or 14)
            W.set_look('slide', steps[(steps.index(now) + 1) % len(steps)] if now in steps else 14)
        elif key == 'order':
            W.set_look('order', 'shuffle' if look.get('order') == 'newest' else 'newest')
        elif key == 'clock':
            W.set_look('clock', look.get('clock') is False)
        elif key == 'refresh':
            steps = W.REFRESH
            now = int(look.get('refresh') or 1)
            W.set_look('refresh', steps[(steps.index(now) + 1) % len(steps)] if now in steps else 1)
        elif key == 'media':
            import xbmcaddon
            from . import screensaver as SS
            now = SS.chosen_media()
            order = SS.MEDIA_CHOICES
            xbmcaddon.Addon(SS.ADDON_ID).setSetting('screensaver_media', order[(order.index(now) + 1) % len(order)])
            SS._forget()
        self.changed = True
        self._tiles()

    def _refresh(self):
        self._busy = True
        self.setProperty('wp.refreshed', _t('جاري التحديث…', 'Refreshing…'))

        def job():
            try:
                W.refresh(force=True)
            except Exception as exc:
                xbmc.log('[DexHub] sources refresh: %s' % exc, xbmc.LOGWARNING)
            self._busy = False
            self.changed = True
            try:
                self._sources()
                self._nav()
            except Exception:
                pass
        threading.Thread(target=job, name='DexHub-ss-refresh', daemon=True).start()

    def _done(self):
        if self.changed:
            self.changed = False
            try:
                W._changed()
            except Exception:
                pass


def pick_yourlist():
    """v5.10.357: the list Your List shows: any source already kept, or a new
    Trakt, MDBList, Silo list or Home catalog (added with Show and Alerts off,
    so it feeds Your List only)."""
    cfg = W.config()
    current = str(cfg['look'].get('yourlist') or '')
    sources = list(cfg.get('sources') or [])
    labels = []
    for source in sources:
        kind = source.get('kind') or ''
        name = KINDS.get(kind, kind.title())
        if isinstance(name, tuple):
            name = _t(*name)
        labels.append('%s  ·  %s%s' % (source.get('label') or name, name, '  ✓' if source.get('id') == current else ''))
    adds = [('trakt', _t('قائمة Trakt جديدة', 'New Trakt list')), ('mdblist', _t('قائمة MDBList جديدة', 'New MDBList list')),
            ('silo', _t('قائمة أو كوليكشن Silo', 'Silo watchlist or collection')),
            ('catalog', _t('كتالوج من الرئيسية', 'Home catalog'))]
    pick = xbmcgui.Dialog().select('Your List', labels + ['+  ' + label for _k, label in adds])
    if pick < 0:
        return False
    if pick < len(sources):
        chosen = sources[pick]
    else:
        chosen = W._add(adds[pick - len(sources)][0], display=False, monitor=False)
    if not chosen:
        return False
    W.set_look('yourlist', chosen['id'])
    try:
        from . import screensaver as SS
        SS._forget()
    except Exception:
        pass

    def job():
        try:
            W.refresh(force=True)
        except Exception as exc:
            xbmc.log('[DexHub] your list refresh: %s' % exc, xbmc.LOGWARNING)
    threading.Thread(target=job, name='DexHub-yourlist-refresh', daemon=True).start()
    return True


def open_page():
    path = _addon_path()
    page = Page('dexhub_watchsources.xml', path, 'Default', '1080i')
    try:
        page.doModal()
    finally:
        page._done()
        del page
