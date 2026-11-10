# -*- coding: utf-8 -*-
"""The bar's icons in your own order (v5.10.556; skin.dexhub 3.28.224).

The user asked (10 Nov 2026) to arrange the icons, up and down, in the side menu and the top
bar. One order serves both: the side menu shows every entry (the part below 'Bottom group'
sits at the menu's foot), the top bar shows the sections in the same order (profile, search,
the bottom group and the gap are its own fixed buttons there).

The skin reads Skin.String(dh.ord.<n>) = key for each place and Skin.String(dh.ord) = on
(tools/s556/bar_order.py); Kodi applies include conditions when it loads a window, so the
skin is reloaded once when you leave with a changed order. 'Default order' clears the
strings and each bar shows its old order again.
"""
import xbmc
import xbmcgui

from .bar_sections import ICON_PATH, _t

# the side menu's entries in their old order (the skin's RAIL)
DEFAULT = ('profile', 'search', 'home', 'movie', 'series', 'servers', 'sport', 'addons', 'c1', 'c2', 'c3',
           'divider', 'discover', 'live', 'library', 'dexworld', 'youtube', 'gap', 'edit', 'alerts', 'menu', 'power')
NAMES = {'profile': ('الملف الشخصي', 'Profile'), 'search': ('البحث', 'Search'), 'home': ('الرئيسية', 'Home'),
         'movie': ('الأفلام', 'Movies'), 'series': ('المسلسلات', 'Series'), 'servers': ('السيرفرات', 'Servers'),
         'sport': ('الرياضة', 'Sport'), 'addons': ('الإضافات', 'Add-ons'), 'discover': ('Discover', 'Discover'),
         'live': ('Live TV', 'Live TV'), 'library': ('مكتبتي', 'My Library'), 'dexworld': ('DexWorld', 'DexWorld'),
         'youtube': ('YouTube', 'YouTube'), 'edit': ('تعديل الرئيسية', 'Edit Home'),
         'alerts': ('التنبيهات', 'Alerts'), 'menu': ('القائمة', 'Menu'), 'power': ('الطاقة', 'Power')}
ICONS = {'profile': 'party', 'edit': 'edit', 'alerts': 'bell', 'search': 'search', 'menu': 'menu', 'power': 'power'}
TOP_ONLY_MENU = ('profile', 'search', 'gap', 'edit', 'alerts', 'menu', 'power')   # not sections: the top bar has its own


def saved():
    """The order on show: the saved one, or the old order."""
    if xbmc.getInfoLabel('Skin.String(dh.ord)') != 'on':
        return list(DEFAULT)
    order = [xbmc.getInfoLabel('Skin.String(dh.ord.%d)' % n) for n in range(1, len(DEFAULT) + 1)]
    known = [k for k in order if k in DEFAULT]
    # a key the skin knows that is missing (a newer version) goes where it was by default
    for n, key in enumerate(DEFAULT):
        if key not in known:
            known.insert(min(n, len(known)), key)
    seen, out = set(), []
    for key in known:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def write(order):
    """The places, and each entry's neighbours (the side menu's moves read
    Skin.String(dh.up.<key>) and dh.dn.<key>; the top bar links itself)."""
    if list(order) == list(DEFAULT):
        reset()
        return
    for n, key in enumerate(order, 1):
        xbmc.executebuiltin('Skin.SetString(dh.ord.%d,%s)' % (n, key))
    for n, key in enumerate(order):
        for name, other in (('up', order[n - 1] if n > 0 else ''), ('dn', order[n + 1] if n + 1 < len(order) else '')):
            if other:
                xbmc.executebuiltin('Skin.SetString(dh.%s.%s,%s)' % (name, key, other))
            else:
                xbmc.executebuiltin('Skin.Reset(dh.%s.%s)' % (name, key))
    xbmc.executebuiltin('Skin.SetString(dh.ord,on)')


def reset():
    for n in range(1, len(DEFAULT) + 1):
        xbmc.executebuiltin('Skin.Reset(dh.ord.%d)' % n)
    for key in DEFAULT:
        xbmc.executebuiltin('Skin.Reset(dh.up.%s)' % key)
        xbmc.executebuiltin('Skin.Reset(dh.dn.%s)' % key)
    xbmc.executebuiltin('Skin.Reset(dh.ord)')


def _name(key):
    if key in ('c1', 'c2', 'c3'):
        custom = xbmc.getInfoLabel('Window(Home).Property(dhs.custom.%s.name)' % key[1:])
        return custom or _t('قسمك %s', 'Your section %s') % key[1:]
    if key == 'divider':
        return _t('── خط فاصل ──', '── Divider line ──')
    if key == 'gap':
        return _t('▼ تحت: المجموعة السفلية تبدأ هنا (القائمة الجانبية)', '▼ Bottom group starts here (side menu)')
    ar, en = NAMES.get(key, (key, key))
    return _t(ar, en)


GROUP = {'profile': ('حسابك', 'Your profile'), 'search': ('بحث', 'Search'), 'edit': ('زر', 'Button'),
         'alerts': ('زر', 'Button'), 'menu': ('زر', 'Button'), 'power': ('زر', 'Button')}
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
LIST, STRIP, RESET, DONE = 100, 110, 201, 202


def _row(key, lifted=False):
    """One entry for the window's list (and its top-bar strip)."""
    line = key in ('divider', 'gap')
    if key == 'divider':
        name = _t('خط فاصل', 'Divider line')
    elif key == 'gap':
        name = _t('▼ المجموعة السفلية', '▼ Bottom group')
    else:
        name = _name(key)
    if key in ('c1', 'c2', 'c3'):
        kind = _t('قسمك', 'Yours')
    else:
        kind = _t(*GROUP.get(key, ('قسم', 'Section')))
    if not line and _hidden(key):
        kind = _t('مخفي من الشريط', 'Hidden from the bar')
    li = xbmcgui.ListItem(name, _t('فوق وتحت', 'Up / Down') if lifted else ('' if line else kind), offscreen=True)
    li.setProperty('key', key)
    if line:
        li.setProperty('line', '1')
    elif key in ('c1', 'c2', 'c3'):
        icon = xbmc.getInfoLabel('Window(Home).Property(dhs.custom.%s.icon)' % key[1:])
        if icon and '/' not in icon and not icon.startswith('special://'):
            icon = 'special://home/addons/skin.dexhub/media/' + icon
        li.setProperty('icon', icon)
        li.setProperty('initial', (name or '?')[:1].upper())
    else:
        li.setProperty('icon', ICON_PATH % ICONS.get(key, key))
    if lifted:
        li.setProperty('lift', '1')
    return li


def _exists(key):
    """False for an entry the bars cannot show at all: a section of yours not made yet,
    Sport while its tab is off. (They keep their place in the saved order.)"""
    if key in ('c1', 'c2', 'c3'):
        return bool(xbmc.getInfoLabel('Window(Home).Property(dhs.custom.%s.name)' % key[1:]))
    if key == 'sport':
        return bool(xbmc.getCondVisibility('Skin.HasSetting(DexHub.SportTab)'))
    return True


def _hidden(key):
    return bool(xbmc.getInfoLabel('Window(Home).Property(dhs.bar.off.%s)' % key))


class OrderWindow(xbmcgui.WindowXMLDialog):
    """v5.10.556: the bar's entries in one window; OK lifts one, Up/Down carries it."""

    def __init__(self, *args, **kwargs):
        super(OrderWindow, self).__init__(*args)
        self.order = list(kwargs.get('order') or saved())
        self.start = list(self.order)
        self.cur = 0
        self.lifted = False
        self._inited = False

    def shown(self):
        return [k for k in self.order if _exists(k)]

    def carry(self, n):
        """The lifted entry to place n of the list; entries not listed keep their places."""
        keys = self.shown()
        if not (0 <= n < len(keys)) or n == self.cur:
            return
        slots = sorted(self.order.index(k) for k in keys)
        keys.insert(n, keys.pop(self.cur))
        for slot, key in zip(slots, keys):
            self.order[slot] = key
        self.cur = n

    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('bo.title', _t('ترتيب أيقونات الشريط', 'Bar icon order'))
        self.setProperty('bo.desc', _t('ترتيب واحد للقائمة الجانبية والشريط العلوي',
                                       'One order for the side menu and the top bar'))
        self.setProperty('bo.top', _t('الشريط العلوي', 'Top bar'))
        self.setProperty('bo.how', _t('الطريقة', 'How it works'))
        self.setProperty('bo.steps', _t(
            '• OK على أي أيقونة يرفعها[CR]• فوق وتحت ينقلها[CR]• OK مرة ثانية يحطها[CR][CR]'
            'اللي تحت «المجموعة السفلية» ينزل لأسفل القائمة الجانبية. الأقسام المخفية تبقى مخفية وين ما حطيتها.',
            '• OK on an icon lifts it[CR]• Up and Down carry it[CR]• OK again puts it down[CR][CR]'
            'What sits below “Bottom group” goes to the foot of the side menu. Hidden sections stay hidden wherever you put them.'))
        self.setProperty('bo.done', _t('✓  تم', '✓  Done'))
        self.setProperty('bo.reset', _t('↺  الترتيب الأصلي', '↺  Default order'))
        self.fill()
        self.setFocusId(LIST)

    def fill(self):
        try:
            rows = self.getControl(LIST)
            rows.reset()
            keys = self.shown()
            rows.addItems([_row(k, self.lifted and n == self.cur) for n, k in enumerate(keys)])
            rows.selectItem(self.cur)
            strip = self.getControl(STRIP)
            strip.reset()
            strip.addItems([_row(k, self.lifted and n == self.cur) for n, k in enumerate(keys)
                            if k not in TOP_ONLY_MENU and not _hidden(k)])
        except Exception as exc:
            xbmc.log('[DexHub] bar order: %s' % exc, xbmc.LOGWARNING)
        self.hint()

    def hint(self):
        if self.lifted:
            text = _t('فوق وتحت: انقل الأيقونة    OK: حطها هنا', 'Up/Down: carry the icon    OK: put it here')
        elif self.getFocusId() == LIST:
            text = _t('OK: ارفع الأيقونة    يمين: تم    رجوع: حفظ وإغلاق', 'OK: lift the icon    Right: Done    Back: save and close')
        else:
            text = _t('رجوع: حفظ وإغلاق', 'Back: save and close')
        self.setProperty('bo.hint', text)

    def pos(self):
        try:
            return self.getControl(LIST).getSelectedPosition()
        except Exception:
            return self.cur

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            if self.lifted:
                self.lifted = False
                self.fill()
            else:
                self.close()
            return
        if self.getFocusId() == LIST and aid in (3, 4):
            n = self.pos()
            if self.lifted and n != self.cur:
                self.carry(n)
                self.fill()
                return
            self.cur = n
        self.hint()

    def onClick(self, cid):
        if cid == LIST:
            self.cur = self.pos()
            self.lifted = not self.lifted
            self.fill()
        elif cid == RESET:
            self.order = list(DEFAULT)
            self.cur, self.lifted = 0, False
            self.fill()
        elif cid == DONE:
            self.close()

    def onFocus(self, cid):
        if cid != LIST and self.lifted:
            self.lifted = False
            self.fill()
        self.hint()


def edit(order=None):
    """The order window on ``order`` (the saved one by default); returns the order the
    user left it in. Nothing is saved here."""
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    window = OrderWindow('dexhub_order.xml', path, 'Default', '1080i', order=order)
    try:
        window.doModal()
        return list(window.order)
    finally:
        del window


def run():
    start = saved()
    order = edit(start)
    order = order if order != start else None
    if order:
        write(order)
        xbmc.sleep(150)
        xbmc.executebuiltin('ReloadSkin()')
