# -*- coding: utf-8 -*-
"""Dex Hub's setup window (v5.10.148).

The first run walks four steps in one window: link the accounts (each one
syncs at once when it links), speed (how light Dex Hub runs), metadata
(language, TMDb key, a ready collection for the Home) and the player's
badges (each style a card with its real sample, in force when chosen).
Any step also opens on its own: the badge gallery (Dex Hub menu, Player
options, the skin's Badges section) and Accounts and sources.

WindowXMLDialog on dexhub_wizard.xml (made by gen_wizard.py): rail list 50,
card panel 100, buttons list 200.
"""
import os
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

from . import setup as S

ADDON_ID = 'plugin.video.dexhub'
RAIL, CARDS, BUTTONS = 50, 100, 200
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
STEPS = ('accounts', 'speed', 'metadata', 'badges')
SYNC_PROP = 'dexhub.wizard.sync.'       # + service: running / done / failed
FLOPPY = 'service.dexhub.floppy'

# profile: (Arabic, English, Arabic line, English line, settings)
SPEED = (
    ('light', 'خفيف جداً', 'Lightest', 'لأجهزة TV Box: بدون تريلر ولا حركة، أسرع تنقل',
     'For TV boxes: no trailers or motion, the fastest moves',
     {'lightweight_mode': 'true', 'homeui_light_mode': 'true', 'homeui_trailers': 'false',
      'homeui_kenburns': 'false', 'homeui_focus_gif': 'false', 'homeui_live_preview': 'false',
      'homeui_plot_scroll': 'false'}),
    ('balanced', 'متوازن', 'Balanced', 'تريلر ومعاينة القنوات، بدون حركة الخلفيات',
     'Trailers and channel previews, still backgrounds',
     {'lightweight_mode': 'true', 'homeui_light_mode': 'true', 'homeui_trailers': 'true',
      'homeui_kenburns': 'false', 'homeui_focus_gif': 'false', 'homeui_live_preview': 'true',
      'homeui_plot_scroll': 'true'}),
    ('full', 'كامل', 'Full', 'كل الحركة والتريلرات، للأجهزة القوية',
     'Every effect and trailer, for strong devices',
     {'lightweight_mode': 'false', 'homeui_light_mode': 'false', 'homeui_trailers': 'true',
      'homeui_kenburns': 'true', 'homeui_focus_gif': 'true', 'homeui_live_preview': 'true',
      'homeui_plot_scroll': 'true'}),
)
LANGS = (('Auto', 'تلقائي', 'Automatic', 'حسب لغة كودي', "Kodi's language"),
         ('Arabic', 'العربية', 'Arabic', 'العناوين والقصص بالعربي متى توفرت', 'Titles and plots in Arabic when there'),
         ('English', 'الإنجليزية', 'English', 'العناوين والقصص بالإنجليزي', 'Titles and plots in English'))


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _media(name):
    return os.path.join(_addon().getAddonInfo('path'), 'resources', 'media', name)


def _floppy_linked():
    try:
        from .. import floppy_progress
        return floppy_progress.configured()
    except Exception:
        return False


def _sync_state(key):
    try:
        return xbmcgui.Window(10000).getProperty(SYNC_PROP + key) or ''
    except Exception:
        return ''


def current_speed():
    addon = _addon()
    for key, _a, _e, _da, _de, values in SPEED:
        if all((addon.getSetting(k) or '').lower() == v for k, v in values.items()):
            return key
    return ''


class Wizard(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(Wizard, self).__init__(*args)
        self.tr = kwargs.get('tr') or (lambda text: text)
        self.steps = list(kwargs.get('steps') or STEPS)
        self.first_run = bool(kwargs.get('first_run'))
        self.index = 0
        self.items = []
        self.keys = []
        self.changed = False
        self._linked = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._inited = False
        self._button_keys = []

    def t(self, ar, en):
        from ..i18n import is_english
        try:
            return en if is_english() else ar
        except Exception:
            return ar

    # ------------------------------------------------------------ frame
    def onInit(self):
        if self._inited:
            self._fill()
            return
        self._inited = True
        self.setProperty('dw.single', '' if len(self.steps) > 1 else '1')
        self.setProperty('dw.brand', self.t('إعداد سريع، كل شيء تقدر تغيّره بعدين', 'A quick setup, all of it changes later'))
        self._linked = dict((key, self._is_linked(key)) for key in self._account_keys())
        self._show()
        self._thread = threading.Thread(target=self._watch, name='DexHub-wizard', daemon=True)
        self._thread.start()

    def _show(self):
        step = self.steps[self.index]
        t = self.t
        titles = {
            'accounts': (t('اربط حساباتك', 'Link your accounts'),
                         t('اضغط على الخدمة واربطها، وتتزامن على طول: صفحتك ومتابعة المشاهدة واللستات.',
                           'Press a service to link it; it syncs at once: your Home, progress and lists.')),
            'speed': (t('السرعة والخفة', 'Speed and lightness'),
                      t('اختر كيف يشتغل Dex Hub على جهازك.', 'Choose how Dex Hub runs on this device.')),
            'metadata': (t('المعلومات والصور', 'Metadata and artwork'),
                         t('لغة العناوين والقصص، ومفتاح TMDb، ومجموعة جاهزة للرئيسية.',
                           'The language of titles and plots, a TMDb key, a ready collection for the Home.')),
            'badges': (t('رموز الجودة', 'Quality badges'),
                       t('كل شكل بصوره الحقيقية. اختر واحد وحدد وين يتطبق: كل الأماكن أو المشغّل أو المصادر أو المكتبة.',
                         'Each style with its real images. Choose one, then where: everywhere, the player, the sources page or the library.')),
        }
        title, subtitle = titles[step]
        self.setProperty('dw.title', title)
        self.setProperty('dw.subtitle', subtitle)
        if len(self.steps) > 1:
            self.setProperty('dw.step', t('الخطوة %d من %d', 'Step %d of %d') % (self.index + 1, len(self.steps)))
        else:
            self.setProperty('dw.step', 'Dex Hub')
        hints = {
            'accounts': t('ترجع لهذي الصفحة من القائمة: الحسابات والمصادر.', 'Back here any time: Menu, Accounts and sources.'),
            'badges': t('مكان الرموز في المشغّل من إعدادات السكين: الرموز.', "The badges' place in the player: skin settings, Badges."),
        }
        self.setProperty('dw.hint', hints.get(step, ''))
        self._rail()
        self._buttons()
        self._fill(reset=True)
        try:
            self.setFocusId(CARDS)
        except Exception:
            pass

    def _rail(self):
        if len(self.steps) < 2:
            return
        names = {'accounts': (self.t('الحسابات', 'Accounts'), self.t('Nuvio وTrakt والسيرفرات', 'Nuvio, Trakt, servers')),
                 'speed': (self.t('السرعة', 'Speed'), self.t('خفيف أو كامل', 'Light or full')),
                 'metadata': (self.t('المعلومات', 'Metadata'), self.t('اللغة والصور', 'Language and art')),
                 'badges': (self.t('الرموز', 'Badges'), self.t('شكل رموز الجودة', 'The quality badges'))}
        items = []
        for i, step in enumerate(self.steps):
            name, line = names[step]
            item = xbmcgui.ListItem(name, line, offscreen=True)
            item.setProperty('num', str(i + 1))
            item.setProperty('state', 'done' if i < self.index else ('current' if i == self.index else 'todo'))
            items.append(item)
        try:
            rail = self.getControl(RAIL)
            rail.reset()
            rail.addItems(items)
        except Exception:
            pass

    def _buttons(self):
        t = self.t
        last = self.index == len(self.steps) - 1
        buttons = []
        if self.index > 0:
            buttons.append(('back', t('رجوع', 'Back')))
        if len(self.steps) == 1:
            buttons.append(('done', t('تم', 'Done')))
        else:
            buttons.append(('done' if last else 'next', t('ابدأ المشاهدة', 'Start watching') if last else t('التالي', 'Next')))
        self._button_keys = [k for k, _l in buttons]
        try:
            ctrl = self.getControl(BUTTONS)
            ctrl.reset()
            ctrl.addItems([xbmcgui.ListItem(label, offscreen=True) for _k, label in buttons])
            ctrl.selectItem(len(buttons) - 1)
        except Exception:
            pass

    # ------------------------------------------------------------ cards
    def _cards(self):
        step = self.steps[self.index]
        return getattr(self, '_cards_' + step)()

    def _fill(self, reset=False):
        cards = self._cards()
        with self._lock:
            try:
                ctrl = self.getControl(CARDS)
            except Exception:
                return
            if reset or len(cards) != len(self.items):
                items = []
                for card in cards:
                    items.append(self._item(card))
                self.items = items
                self.keys = [c['key'] for c in cards]
                pos = 0
                ctrl.reset()
                ctrl.addItems(items)
                for i, card in enumerate(cards):
                    if card.get('on') and step_focus_on_current(self.steps[self.index]):
                        pos = i
                        break
                ctrl.selectItem(pos)
            else:
                for item, card in zip(self.items, cards):
                    self._update(item, card)
                self.keys = [c['key'] for c in cards]

    def _item(self, card):
        item = xbmcgui.ListItem(card.get('label') or '', offscreen=True)
        icon = card.get('icon') or ''
        if icon:
            item.setArt({'icon': icon})
            if '/media/menu/' not in icon.replace('\\', '/'):
                item.setProperty('logo', 'true')
        self._update(item, card)
        return item

    def _update(self, item, card):
        if item.getLabel() != (card.get('label') or ''):
            item.setLabel(card.get('label') or '')
        for key in ('desc', 'status', 'text'):
            value = str(card.get(key) or '')
            if item.getProperty(key) != value:
                item.setProperty(key, value)
        on = '1' if card.get('on') else ''
        if item.getProperty('on') != on:
            item.setProperty('on', on)
        images = list(card.get('images') or [])[:6]
        pair = '1' if 0 < len(images) <= 2 else ''
        if item.getProperty('pair') != pair:
            item.setProperty('pair', pair)
        for i in range(6):
            value = images[i] if i < len(images) else ''
            if item.getProperty('img%d' % (i + 1)) != value:
                item.setProperty('img%d' % (i + 1), value)

    # accounts
    def _account_keys(self):
        return [row[0] for row in S.SERVICES] + ['floppy']

    def _is_linked(self, key):
        return _floppy_linked() if key == 'floppy' else S._linked(key)

    def _cards_accounts(self):
        t = self.t
        tr = self.tr
        media = os.path.join(_addon().getAddonInfo('path'), 'resources', 'media')
        rows = list(S.SERVICES) + [('floppy', 'Floppy', 'متابعة المشاهدة من سيرفرك الخاص',
                                    'sync_accounts.png', '', '')]
        cards = []
        count = S._addon_count()
        for key, name, desc, icon, _login, _logout in rows:
            if key == 'addon':
                on = count > 0
                status = (t('%d إضافة مثبتة', '%d add-ons installed') % count) if count else t('أضف رابط', 'Add a link')
            else:
                on = bool(self._linked.get(key))
                sync = _sync_state(key)
                if sync == 'running':
                    status = t('جاري المزامنة…', 'Syncing…')
                elif on and sync == 'done':
                    status = t('مربوط ✓  تمت المزامنة', 'Linked ✓  synced')
                elif on and sync == 'failed':
                    status = t('مربوط ✓  المزامنة ما كملت', 'Linked ✓  sync did not finish')
                elif on:
                    status = t('مربوط ✓', 'Linked ✓')
                else:
                    status = t('اضغط للربط', 'Press to link')
            cards.append({'key': key, 'label': tr(name), 'desc': tr(desc) if key != 'floppy' else t(desc, 'Progress from your own server'),
                          'icon': os.path.join(media, icon), 'status': status, 'on': on})
        return cards

    # speed
    def _cards_speed(self):
        current = current_speed()
        return [{'key': key, 'label': self.t(ar, en), 'desc': self.t(dar, den), 'icon': _media('menu/gears.png'),
                 'status': self.t('موصى به', 'Recommended') if key == 'light' else '', 'on': key == current}
                for key, ar, en, dar, den, _values in SPEED]

    # metadata
    def _cards_metadata(self):
        t = self.t
        addon = _addon()
        lang = addon.getSetting('homeui_metadata_language') or 'Auto'
        cards = [{'key': 'lang:' + value, 'label': t(ar, en), 'desc': t(dar, den), 'icon': _media('menu/comment-dots.png'),
                  'on': lang == value} for value, ar, en, dar, den in LANGS]
        key_set = bool((addon.getSetting('tmdb_api_key') or '').strip())
        cards.append({'key': 'tmdb', 'label': t('مفتاح TMDb', 'TMDb key'),
                      'desc': t('صور وقصص أكثر وأسرع (اختياري)', 'More and faster art and plots (optional)'),
                      'icon': _media('tmdb.png'), 'status': t('محفوظ ✓', 'Saved ✓') if key_set else t('غير مضاف', 'Not set'),
                      'on': key_set})
        try:
            from . import kaptain
            found = kaptain.state()
            has = found == kaptain.STATE_HAS
            other = found == kaptain.STATE_OTHER
        except Exception:
            has = other = False
        cards.append({'key': 'kaptain', 'label': t('مجموعة Kaptain', 'Kaptain collection'),
                      'desc': t('منصات البث والشبكات والأنواع والممثلين جاهزة للرئيسية',
                                'Streaming services, networks, genres and people, ready for the Home'),
                      'icon': _media('menu/table-cells-large.png'),
                      'status': t('مضافة ✓', 'Added ✓') if has else (t('عندك مجموعاتك', 'You have your own') if other else t('اضغط للإضافة', 'Press to add')),
                      'on': has})
        return cards

    # badges
    def _cards_badges(self):
        from .. import player_badges as pb
        cards = []
        self._badge_cards = pb.gallery()
        for card in self._badge_cards:
            card = dict(card)
            card['on'] = card.pop('current', False)
            card['icon'] = _media('menu/tags.png')
            cards.append(card)
        return cards

    # ----------------------------------------------------------- actions
    def onClick(self, control_id):
        if control_id == BUTTONS:
            try:
                key = self._button_keys[self.getControl(BUTTONS).getSelectedPosition()]
            except Exception:
                return
            if key == 'back':
                self.index -= 1
                self._show()
            elif key == 'next':
                self.index += 1
                self._show()
            else:
                self.finish()
            return
        if control_id != CARDS:
            return
        try:
            pos = self.getControl(CARDS).getSelectedPosition()
            key = self.keys[pos]
        except Exception:
            return
        step = self.steps[self.index]
        getattr(self, '_click_' + step)(key)
        self._fill()

    def _run(self, action, wait=False):
        builtin = 'RunPlugin(plugin://%s/?action=%s)' % (ADDON_ID, action)
        try:
            xbmc.executebuiltin(builtin, wait)
        except TypeError:
            xbmc.executebuiltin(builtin)

    def _click_accounts(self, key):
        if key == 'floppy':
            if xbmc.getCondVisibility('System.HasAddon(%s)' % FLOPPY):
                xbmc.executebuiltin('Addon.OpenSettings(%s)' % FLOPPY)
            else:
                xbmc.executebuiltin('InstallAddon(%s)' % FLOPPY)
            return
        row = S._BY_KEY.get(key)
        if row is None:
            return
        if key == 'addon' or not self._linked.get(key):
            self._run(row[4])
            return
        t = self.t
        from .options import choose
        options = [('sync', t('مزامنة الآن', 'Sync now')), ('login', t('إعادة الربط', 'Link again'))]
        if row[5]:
            options.append(('logout', t('فصل الحساب', 'Unlink')))
        pick = choose(self.tr(row[1]), [label for _k, label in options], subtitle=t('مربوط', 'Linked'))
        if pick < 0:
            return
        what = options[pick][0]
        if what == 'sync':
            self._sync(key)
        elif what == 'login':
            self._run(row[4])
        elif xbmcgui.Dialog().yesno(self.tr(row[1]), t('فصل %s من Dex Hub؟', 'Unlink %s from Dex Hub?') % self.tr(row[1])):
            self._run(row[5])

    def _sync(self, key):
        self.changed = True
        try:
            xbmcgui.Window(10000).setProperty(SYNC_PROP + key, 'running')
        except Exception:
            pass
        self._run('wizard_sync&service=%s' % key)

    def _click_speed(self, key):
        values = next((v for k, _a, _e, _da, _de, v in SPEED if k == key), None)
        if not values:
            return
        addon = _addon()
        for name, value in values.items():
            if (addon.getSetting(name) or '').lower() != value:
                addon.setSetting(name, value)
        try:
            from .. import ui_preferences
            ui_preferences.publish()
        except Exception:
            pass
        self.changed = True

    def _click_metadata(self, key):
        if key.startswith('lang:'):
            _addon().setSetting('homeui_metadata_language', key[5:])
            self.changed = True
        elif key == 'tmdb':
            self._run('simple_tmdb_api_key', wait=True)
        elif key == 'kaptain':
            from . import kaptain
            try:
                if kaptain.state() == kaptain.STATE_NONE:
                    kaptain.add(tr=self.tr)
                    self.changed = True
            except Exception:
                pass

    def _click_badges(self, key):
        from .. import player_badges as pb
        # v5.10.162: everywhere, or the player, the sources page or the
        # Library and Servers hero alone (the sources default is its own)
        area = 'sources' if key == 'sources:own' else pb.choose_area(
            next((c.get('label') for c in getattr(self, '_badge_cards', []) if c.get('key') == key), ''))
        if area and pb.apply_card(key, area):
            self.changed = True
            try:
                pb.preview()
            except Exception:
                pass

    # ------------------------------------------------------------ watch
    def _watch(self):
        """Sign-ins run in other invocations: a service that links starts
        its sync at once."""
        monitor = xbmc.Monitor()
        while not self._stop.wait(1.5):
            if monitor.abortRequested():
                break
            if self.steps[self.index] != 'accounts':
                continue
            try:
                for key in self._account_keys():
                    if key == 'addon':
                        continue
                    now = bool(self._is_linked(key))
                    if now and not self._linked.get(key):
                        self._linked[key] = True
                        if key not in ('mdblist',):
                            self._sync(key)
                    elif not now:
                        self._linked[key] = False
                self._fill()
            except Exception:
                pass

    def onAction(self, action):
        if action.getId() in A_BACK:
            if self.index > 0 and len(self.steps) > 1:
                self.index -= 1
                self._show()
                return
            self.finish()

    def finish(self):
        self._stop.set()
        if self.first_run:
            S.mark_done()
        self.close()


def step_focus_on_current(step):
    return step in ('speed', 'metadata', 'badges')


def open_wizard(steps=None, first_run=False, tr=None):
    """Show the window; True when something changed (the Home is published again)."""
    if tr is None:
        try:
            from ..i18n import tr
        except Exception:
            tr = lambda text: text
    try:
        from .. import skin_theme
        skin_theme.publish_theme()
    except Exception:
        pass
    addon = _addon()
    if first_run:
        try:
            xbmcgui.Window(10000).setProperty(S.ASKED_PROP, '1')
            addon.setSetting('welcome_seen', 'identity-2026')
        except Exception:
            pass
    before = S.state()
    from . import ui_size
    window = Wizard(ui_size.xml('dexhub_wizard.xml'), addon.getAddonInfo('path'), 'Default', '1080i',
                    tr=tr, steps=steps or STEPS, first_run=first_run)
    try:
        window.doModal()
        changed = window.changed
    finally:
        window._stop.set()
        if window._thread is not None:
            window._thread.join(2.0)
        del window
    changed = changed or S.state() != before
    if changed:
        try:
            if xbmc.getSkinDir() == 'skin.dexhub':
                from ..skinui import common as C
                C.ask_republish('accounts')
        except Exception:
            pass
    return changed
