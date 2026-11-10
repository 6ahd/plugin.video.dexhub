# -*- coding: utf-8 -*-
"""The title page's look as picture cards (v5.10.556; window dexhub_look.xml, written by
tools/s556/make_look_window.py).

Two rows of cards: the layout (classic with the poster, classic without it, cinematic) and
the page's sections (all on one page, tabs). The look in use is ticked; OK on a card applies
it at once, Back closes. The plain list in skinui/actions.title_look stays as the fallback.
"""
import xbmc
import xbmcgui

from .bar_sections import _t

LOOKS, PARTS = 100, 110
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
LOOK_CARDS = (('poster', ('كلاسيكي مع البوستر', 'Classic with the poster'), ('البوستر والمعلومات بجانبه', 'The poster, the details beside it')),
              ('plain', ('كلاسيكي بدون بوستر', 'Classic without the poster'), ('المعلومات من يسار الصفحة', 'The details from the left edge')),
              ('cinematic', ('سينمائي', 'Cinematic'), ('صورة العمل تملأ الصفحة', "The title's picture fills the page")))
PART_CARDS = (('page', ('صفحة وحدة', 'One page'), ('الأقسام تحت بعض، تنزل لها', 'Sections one under another')),
              ('tabs', ('تبويبات', 'Tabs'), ('قسم واحد في كل مرة، أخف وأسرع', 'One section at a time, lighter')))


def state():
    """(look, part) in use."""
    from .homeui import control as CT
    cinematic = (xbmc.getInfoLabel('Skin.String(dh.titlelayout)') or '').strip().lower() == 'cinematic'
    try:
        poster = (CT._addon().getSetting('homeui_title_poster') or 'true').lower() != 'false'
    except Exception:
        poster = True
    look = 'cinematic' if cinematic else ('poster' if poster else 'plain')
    return look, ('tabs' if CT._skin_on('title_tabs') else 'page')


def apply(key):
    from .homeui import control as CT
    look, part = state()
    if key in ('poster', 'plain', 'cinematic'):
        if key != 'cinematic' and (look == 'cinematic' or look != key):
            CT.apply_entry('title_poster', key == 'poster')
        if (look == 'cinematic') != (key == 'cinematic'):
            CT.set_skin_string('dh.titlelayout', 'cinematic' if key == 'cinematic' else '')
    elif key in ('page', 'tabs') and key != part:
        CT.set_skin_string('dh.titletabs', 'on' if key == 'tabs' else '')


class LookWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(LookWindow, self).__init__(*args)
        self._inited = False

    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('tl.title', _t('شكل صفحة العمل', 'Title page look'))
        self.setProperty('tl.desc', _t('اختر شكل الصفحة وطريقة عرض أقسامها. يتطبق على طول.',
                                       'Pick the page layout and how its sections show. It applies at once.'))
        self.setProperty('tl.look', _t('الشكل', 'Layout'))
        self.setProperty('tl.parts', _t('أقسام الصفحة', 'Page sections'))
        self.setProperty('tl.hint', _t('OK: اختر    فوق وتحت: بين الشكل والأقسام    رجوع: إغلاق',
                                       'OK: pick    Up/Down: layout or sections    Back: close'))
        self.fill(focus=True)

    def fill(self, focus=False):
        look, part = state()
        for cid, cards, current in ((LOOKS, LOOK_CARDS, look), (PARTS, PART_CARDS, part)):
            try:
                control = self.getControl(cid)
                at = control.getSelectedPosition()
                items = []
                for key, name, line in cards:
                    li = xbmcgui.ListItem(_t(*name), _t(*line), offscreen=True)
                    li.setProperty('kind', key)
                    if key == current:
                        li.setProperty('on', '1')
                    items.append(li)
                control.reset()
                control.addItems(items)
                keys = [k for k, _n, _l in cards]
                control.selectItem(keys.index(current) if focus or at < 0 else at)
            except Exception as exc:
                xbmc.log('[DexHub] title look: %s' % exc, xbmc.LOGWARNING)
        if focus:
            self.setFocusId(LOOKS)

    def onClick(self, cid):
        if cid not in (LOOKS, PARTS):
            return
        try:
            key = self.getControl(cid).getSelectedItem().getProperty('kind')
            apply(key)
        except Exception as exc:
            xbmc.log('[DexHub] title look: %s' % exc, xbmc.LOGWARNING)
            return
        xbmc.sleep(180)
        self.fill()

    def onAction(self, action):
        if action.getId() in A_BACK:
            self.close()


def run():
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    window = LookWindow('dexhub_look.xml', path, 'Default', '1080i')
    try:
        window.doModal()
    finally:
        del window
