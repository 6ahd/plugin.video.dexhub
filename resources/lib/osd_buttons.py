# -*- coding: utf-8 -*-
"""The player bar's buttons in skin.dexhub (v5.10.288, skin.dexhub 3.28.40).

The right side of skin.dexhub's player bar is nine slots (ten on Apple TV). Slot n shows the
button this module publishes as dhs.pref.osd_slot_<n>, in the order and with
the buttons the user keeps in "Player buttons"; dhs.pref.osd_order holds the
whole order (never empty once published), and while it is empty the skin
keeps its own default order. Visibility rules of a button itself stay in the
skin: Episodes only while a series plays, Sources only for a Dex Hub video,
Sync subtitle only with DexSubtitles, PPI only with TinyPPI, External
player only on Apple TV (skin.dexhub.tvos; the key is left out of the
editor and of the published slots elsewhere).

Saved as the hidden setting osd_buttons: the keys in bar order, a hidden one
written "-key" (sources,episodes,info,...,-nextsub,video,ppi). A key this
version does not know is dropped; a key added later joins after its default
neighbour.
"""
import xbmc
import xbmcgui

SETTING = 'osd_buttons'
SLOTS = 10     # skin.dexhub draws 9 slots, skin.dexhub.tvos 10 (with External)
# bar order, left to right; "-" = hidden by default
DEFAULT = ('sources', 'episodes', 'info', 'party', 'audio', 'subtitles', 'subsync', '-nextsub', 'external', 'video', 'ppi')
KEYS = tuple(k.lstrip('-') for k in DEFAULT)
NAMES = {
    'sources': ('المصادر', 'Sources'),
    'episodes': ('الحلقات', 'Episodes'),
    'info': ('المعلومات', 'Info'),
    'audio': ('الصوت', 'Audio'),
    'subtitles': ('الترجمة', 'Subtitles'),
    'subsync': ('مزامنة الترجمة', 'Sync subtitle'),
    'nextsub': ('الترجمة التالية', 'Next subtitle'),
    'external': ('المشغل الخارجي', 'External player'),
    'video': ('إعدادات المشغل', 'Player settings'),
    'ppi': ('معلومات التشغيل PPI', 'PPI playback info'),
    'party': ('مشاهدة جماعية', 'Watch party'),     # v5.10.372
}
# when the skin shows a kept button
NOTES = {
    'sources': ('يظهر مع فيديو من Dex Hub', 'Shows with a Dex Hub video'),
    'episodes': ('يظهر مع المسلسلات', 'Shows for series'),
    # v5.10.290: Dex Hub runs both itself; DexSubtitles only takes over the sync when installed
    'subsync': ('يستخدم DexSubtitles لو كانت مثبتة', 'Uses DexSubtitles when installed'),
    'nextsub': ('وبعد آخر ترجمة: بدون ترجمة', 'After the last one: subtitles off'),
    'ppi': ('يحتاج TinyPPI', 'Needs TinyPPI'),
    'external': ('على Apple TV فقط', 'Apple TV only'),
}
# v5.10.289: what each button does, one line in the editor
DOES = {
    'sources': ('يبدل نسخة الفيديو من مصادر العمل', 'Switches to another source of this title'),
    'episodes': ('حلقات الموسم والمواسم، تختار وتشغل', 'Episodes and seasons, pick one to play'),
    'info': ('قصة العمل وتقييماته، OK يفتح صفحته', 'Plot and ratings, OK opens the title page'),
    'audio': ('اختيار مسار الصوت', 'Choose the audio track'),
    'subtitles': ('اختيار الترجمة وإعداداتها', 'Choose the subtitle and its settings'),
    'subsync': ('يزامن الترجمة الشغالة أو يختار الأنسب', 'Syncs the playing subtitle or picks the best one'),
    'nextsub': ('ينتقل للترجمة التالية بضغطة', 'Moves to the next subtitle in one press'),
    'external': ('يفتح الفيديو في مشغل خارجي', 'Opens the video in an external player'),
    'video': ('إعدادات الصورة والتشغيل', 'Picture and playback settings'),
    'ppi': ('معلومات التشغيل وأوضاع Dolby Vision', 'Playback info and Dolby Vision modes'),
    'party': ('تبدأ مشاهدة جماعية بهذا العمل، أو تفتح المشاهدة اللي أنت فيها', 'Starts a watch party with this title, or opens the one you are in'),
}
# the bar's icons, copies of the OSD's own (resources/media/osdbar)
ICONS = {
    'sources': 'layers', 'episodes': 'list-video', 'info': 'info', 'audio': 'audio-lines', 'subtitles': 'captions',
    'subsync': 'subsync', 'nextsub': 'subnext', 'external': 'external', 'video': 'ratio', 'ppi': 'ppi',
    'party': 'party',
}
LEFT_ICONS = ('pause-f', 'skip-back', 'skip-forward', 'stop-f')


def _tvos():
    try:
        return bool(xbmc.getCondVisibility('System.Platform.TVOS'))
    except Exception:
        return False


def _offered(key):
    """External player only on Apple TV."""
    return key != 'external' or _tvos()


def _addon():
    from .settings_cache import cached_addon
    return cached_addon()


def _txt(ar, en):
    try:
        arabic = (_addon().getSetting('ui_language') or 'English').strip().lower().startswith('ar')
    except Exception:
        arabic = False
    return ar if arabic else en


def _parse(raw):
    """[(key, shown)] in bar order from the saved text."""
    entries, seen = [], set()
    for token in str(raw or '').replace(' ', '').split(','):
        shown = not token.startswith('-')
        key = token.lstrip('-')
        if key in KEYS and key not in seen:
            seen.add(key)
            entries.append((key, shown))
    if not entries:
        return [(k.lstrip('-'), not k.startswith('-')) for k in DEFAULT]
    # a key this list does not name yet (added in a later version) joins
    # after its default neighbour, with its default visibility
    for index, token in enumerate(DEFAULT):
        key = token.lstrip('-')
        if key in seen:
            continue
        at = 0
        for before in reversed(DEFAULT[:index]):
            names = [k for k, _s in entries]
            if before.lstrip('-') in names:
                at = names.index(before.lstrip('-')) + 1
                break
        entries.insert(at, (key, not token.startswith('-')))
        seen.add(key)
    return entries


def _format(entries):
    return ','.join(key if shown else '-' + key for key, shown in entries)


def load():
    try:
        raw = _addon().getSetting(SETTING)
    except Exception:
        raw = ''
    return _parse(raw)


def save(entries):
    _addon().setSetting(SETTING, _format(entries))
    try:
        from .settings_cache import invalidate
        invalidate()
    except Exception:
        pass
    publish(entries)


def publish(entries=None):
    """dhs.pref.osd_order and dhs.pref.osd_slot_1..9 for the skin's slots."""
    entries = load() if entries is None else entries
    shown = [key for key, on in entries if on and _offered(key)][:SLOTS]
    win = xbmcgui.Window(10000)
    values = {'dhs.pref.osd_order': ','.join(shown) or 'none', 'dhs.pref.osd_summary': summary(entries)}
    for n in range(1, SLOTS + 1):
        values['dhs.pref.osd_slot_%d' % n] = shown[n - 1] if n <= len(shown) else ''
    for prop, value in values.items():
        if win.getProperty(prop) != value:
            win.setProperty(prop, value)


def summary(entries=None):
    entries = [e for e in (load() if entries is None else entries) if _offered(e[0])]
    return '%d / %d' % (len([1 for _k, on in entries if on]), len(entries))


A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
A_LEFT, A_RIGHT = 1, 2
LIST, RESTORE, DONE = 100, 301, 302
LOOK_STYLE, LOOK_DIM, LOOK_ACCENT = 303, 304, 305     # v5.10.298: the Look row
LOOK_SHAPE = 306                                        # v5.10.301: the cursor's shape
LOOK_NAMES, LOOK_DESIGN, LOOK_TITLE = 307, 308, 309      # v5.10.309: the second Look row


class EditorWindow(xbmcgui.WindowXMLDialog):
    """v5.10.289: dexhub_osd_buttons.xml, a live drawing of the player bar
    over the list of its buttons. OK shows or hides the focused button,
    Left / Right move it earlier / later in the bar; every change is saved
    and published at once, so an open player follows."""

    def __init__(self, *args, **kwargs):
        super(EditorWindow, self).__init__(*args)
        self.txt = kwargs.get('txt') or _txt
        self.root = kwargs.get('root') or ''
        self.entries = load()

    def _icon(self, key):
        return '%s/resources/media/osdbar/%s.png' % (self.root, ICONS.get(key, key))

    def _view(self):
        return [i for i, (k, _s) in enumerate(self.entries) if _offered(k)]

    def onInit(self):
        txt = self.txt
        props = {
            'ob.root': self.root,
            'ob.title': txt('أزرار المشغل', 'Player buttons'),
            'ob.subtitle': txt('اختر الأزرار التي تظهر في شريط المشغل وترتيبها', 'Choose which buttons the player bar shows, and their order'),
            'ob.preview': txt('شريط المشغل كما سيظهر', 'The player bar as it will look'),
            'ob.hidden': txt('مخفي، ليس في الشريط', 'hidden, not in the bar'),
            'ob.hint': txt('OK إظهار أو إخفاء   ·   ◀ ▶ تحريك في الشريط', 'OK show or hide   ·   ◀ ▶ move in the bar'),
            'ob.restore': txt('الترتيب الافتراضي', 'Restore default'),
            'ob.done': txt('تم', 'Done'),
        }
        for n, name in enumerate(LEFT_ICONS, 1):
            props['ob.left.%d' % n] = '%s/resources/media/osdbar/%s.png' % (self.root, name)
        for prop, value in props.items():
            self.setProperty(prop, value)
        self._look()
        self._fill(0)
        self.setFocusId(LIST)

    def _look(self):
        """v5.10.298: the Look row's labels and the preview in the chosen
        look (style, darkness, colour), as the player will wear it."""
        from . import osd_look
        txt = self.txt
        osd_look.publish()
        props = osd_look.props()
        style, dim, accent, shape = (osd_look.value(k) for k in (
            osd_look.STYLE, osd_look.DIM, osd_look.ACCENT, osd_look.SHAPE))
        fanart = (xbmc.getInfoLabel('Player.Art(fanart)') or xbmc.getInfoLabel('Player.Art(tvshow.fanart)')
                  or xbmcgui.Window(10000).getProperty('dexhub.source.fanart')
                  or '%s/resources/media/bp_popular_movie.jpg' % self.root)
        values = {
            'ob.style': style,
            'ob.fanart': fanart,
            'ob.c.bg': props['dhs.osd.bg'],
            'ob.c.glass': props['dhs.osd.glass'],
            'ob.c.accent': props.get('dhs.osd.focus') or props['dhs.osd.accent'],
            'ob.c.onaccent': props['dhs.osd.on_accent'],
            'ob.c.shape': '%s/resources/media/osdshape/osdfocus_%s.png' % (self.root, props.get('dhs.osd.shape') or 'circle'),
            'ob.look.shape': '%s:  [B]%s[/B]' % (txt('المؤشر', 'Cursor'), osd_look.label(osd_look.SHAPE, shape, txt)),
            'ob.look.style': '%s:  [B]%s[/B]' % (txt('النمط', 'Style'), osd_look.label(osd_look.STYLE, style, txt)),
            'ob.look.dim': '%s:  [B]%s[/B]' % (txt('التعتيم', 'Darkness'), osd_look.label(osd_look.DIM, dim, txt)),
            'ob.look.accent': '%s:  [B]%s[/B]' % (txt('اللون', 'Colour'), osd_look.label(osd_look.ACCENT, accent, txt)),
            'ob.look.names': '%s:  [B]%s[/B]' % (txt('الاسم في الكبسولة', 'Name in pill'), osd_look.label(osd_look.NAMES, osd_look.value(osd_look.NAMES), txt)),
            'ob.look.design': '%s:  [B]%s[/B]' % (txt('التصميم', 'Design'), osd_look.label(osd_look.DESIGN, osd_look.value(osd_look.DESIGN), txt)),
            'ob.look.title': '%s:  [B]%s[/B]' % (txt('أزرار صفحة العمل', 'Title buttons'), osd_look.label(osd_look.TITLE, osd_look.value(osd_look.TITLE), txt)),
        }
        for prop, value in values.items():
            self.setProperty(prop, value)

    def _cycle(self, key):
        from . import osd_look, ui_preferences
        choices = osd_look.CHOICES[key]
        current = osd_look.value(key)
        if key == osd_look.ACCENT:
            names = osd_look.labels(key, self.txt)
            if 'artwork' in choices and not osd_look.has_pil():
                at = choices.index('artwork')
                names[at] += self.txt('  (يحتاج Pillow، وإلا لون الثيم)', '  (needs Pillow, else the theme colour)')
            pick = xbmcgui.Dialog().select(self.txt('لون المشغّل', 'Player colour'), names,
                                           preselect=choices.index(current) if current in choices else 0)
            if pick < 0:
                return
            chosen = choices[pick]
        else:
            at = choices.index(current) if current in choices else 0
            chosen = choices[(at + 1) % len(choices)]
        ui_preferences.set_value(key, chosen)
        if key == osd_look.ACCENT and chosen == 'artwork':
            osd_look.follow_playing()
        self._look()

    def _fill(self, select):
        """The list and the bar from self.entries; the focus on row select."""
        txt = self.txt
        items = []
        for pos, i in enumerate(self._view(), 1):
            key, shown = self.entries[i]
            li = xbmcgui.ListItem(label=txt(*NAMES[key]), offscreen=True)
            li.setArt({'icon': self._icon(key)})
            note = txt(*DOES[key])
            if key in NOTES:
                note += '  ·  ' + txt(*NOTES[key])
            li.setProperties({'key': key, 'note': note, 'pos': str(pos),
                              'state': 'on' if shown else 'off',
                              'statelabel': txt('ظاهر', 'On') if shown else txt('مخفي', 'Off')})
            items.append(li)
        try:
            ctrl = self.getControl(LIST)
            ctrl.reset()
            ctrl.addItems(items)
            ctrl.selectItem(max(0, min(select, len(items) - 1)))
        except Exception:
            pass
        shown = [k for k, on in self.entries if on and _offered(k)][:SLOTS]
        for n in range(1, SLOTS + 1):
            key = shown[n - 1] if n <= len(shown) else ''
            self.setProperty('ob.bar.%d.key' % n, key)
            self.setProperty('ob.bar.%d.icon' % n, self._icon(key) if key else '')
            self.setProperty('ob.bar.%d.name' % n, self.txt(*NAMES[key]) if key else '')

    def _selected(self):
        try:
            return int(self.getControl(LIST).getSelectedPosition())
        except Exception:
            return -1

    def _change(self, row, action):
        view = self._view()
        if not 0 <= row < len(view):
            return
        at = view[row]
        key, shown = self.entries[at]
        select = row
        if action == 'toggle':
            self.entries[at] = (key, not shown)
        elif action in ('up', 'down'):
            other = row - 1 if action == 'up' else row + 1
            if not 0 <= other < len(view):
                return
            self.entries[view[other]], self.entries[at] = self.entries[at], self.entries[view[other]]
            select = other
        save(self.entries)
        self._fill(select)

    def onClick(self, control_id):
        if control_id == LIST:
            self._change(self._selected(), 'toggle')
        elif control_id == RESTORE:
            self.entries = _parse('')
            save(self.entries)
            self._fill(0)
            self.setFocusId(LIST)
        elif control_id == DONE:
            self.close()
        elif control_id in (LOOK_STYLE, LOOK_DIM, LOOK_ACCENT, LOOK_SHAPE, LOOK_NAMES, LOOK_DESIGN, LOOK_TITLE):
            from . import osd_look
            self._cycle({LOOK_STYLE: osd_look.STYLE, LOOK_DIM: osd_look.DIM,
                         LOOK_ACCENT: osd_look.ACCENT, LOOK_SHAPE: osd_look.SHAPE,
                         LOOK_NAMES: osd_look.NAMES, LOOK_DESIGN: osd_look.DESIGN, LOOK_TITLE: osd_look.TITLE}[control_id])

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self.close()
        elif aid in (A_LEFT, A_RIGHT) and self.getFocusId() == LIST:
            self._change(self._selected(), 'up' if aid == A_LEFT else 'down')


def edit(txt=None):
    """v5.10.289: the editor window; Kodi's list dialogs when it cannot open."""
    txt = txt or _txt
    try:
        root = _addon().getAddonInfo('path')
        window = EditorWindow('dexhub_osd_buttons.xml', root, 'Default', '1080i', txt=txt, root=root)
        window.doModal()
        del window
        return True
    except Exception as exc:
        xbmc.log('[DexHub] player buttons window unavailable (%s); list dialogs instead' % exc, xbmc.LOGWARNING)
    return _edit_lists(txt)


def _edit_lists(txt):
    """The editor in Kodi's list dialogs: each button with its place and
    state; OK on one shows, hides or moves it, in force at once."""
    entries = load()
    selected = 0
    while True:
        view = [i for i, (k, _s) in enumerate(entries) if _offered(k)]
        items = []
        for index, (key, shown) in enumerate([entries[i] for i in view]):
            name = txt(*NAMES[key])
            state = txt('ظاهر', 'Shown') if shown else txt('مخفي', 'Hidden')
            note = NOTES.get(key)
            item = xbmcgui.ListItem(label='%d. %s' % (index + 1, name),
                                    label2=state + (' · ' + txt(*note) if note else ''), offscreen=True)
            items.append(item)
        reset = xbmcgui.ListItem(label=txt('الترتيب الافتراضي', 'Default order'),
                                 label2=txt('الأزرار وترتيبها كما كانت', 'The buttons and order as they came'),
                                 offscreen=True)
        items.append(reset)
        choice = xbmcgui.Dialog().select(txt('أزرار المشغل', 'Player buttons'), items,
                                         preselect=min(selected, len(items) - 1), useDetails=True)
        if choice < 0:
            return True
        if choice == len(view):
            entries = _parse('')
            save(entries)
            selected = 0
            continue
        at = view[choice]
        key, shown = entries[at]
        actions = [('toggle', txt('إخفاء الزر', 'Hide the button') if shown else txt('إظهار الزر', 'Show the button'))]
        if choice > 0:
            actions.append(('up', txt('تقديم (إلى اليسار في الشريط)', 'Move up (left in the bar)')))
        if choice < len(view) - 1:
            actions.append(('down', txt('تأخير (إلى اليمين في الشريط)', 'Move down (right in the bar)')))
        pick = xbmcgui.Dialog().select(txt(*NAMES[key]), [label for _a, label in actions])
        selected = choice
        if pick < 0:
            continue
        action = actions[pick][0]
        if action == 'toggle':
            entries[at] = (key, not shown)
        elif action == 'up':
            other = view[choice - 1]
            entries[other], entries[at] = entries[at], entries[other]
            selected = choice - 1
        elif action == 'down':
            other = view[choice + 1]
            entries[other], entries[at] = entries[at], entries[other]
            selected = choice + 1
        save(entries)
