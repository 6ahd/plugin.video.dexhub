# -*- coding: utf-8 -*-
"""Accounts and sources screen, shown on the first run (v5.10.102).

Someone who opens Dex Hub for the first time, with nothing linked, gets this
screen before the Home: one card per account or source (Nuvio, Stremio,
Plex, Emby, Jellyfin, Silo, a Stremio add-on by its link). A card runs Dex
Hub's own sign-in for that service in its own invocation (QR codes and
dialogs open over this screen), and the cards turn linked as that happens.
Start opens the Home with whatever was linked; Later shows the screen again
next time while nothing is linked. The Home's Menu and Settings open the same
screen at any time as Accounts and sources, where a linked card also signs
out.

v5.10.115: Trakt, MDBList and Simkl have cards too, and the screen offers
Kaptain's collection for the Home (kaptain.py), ticked on the first run; it
is added when the screen is finished with the tick on, unless the user has
it (or a collection of their own) already.

WindowXMLDialog on resources/skins/Default/1080i/dexhub_setup.xml (made by
tools/gen_setup.py):
  100  the cards (label, label2; properties icon, linked, status)
  300  Kaptain's collection (label, label2; properties checked, locked)
  200  the buttons (property primary)
  window properties ds.title, ds.subtitle, ds.hint, ds.option
"""
import os
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

ADDON_ID = 'plugin.video.dexhub'
CARDS, BUTTONS, OPTION = 100, 200, 300
SETTING = 'setup_done'
A_CONTEXT, A_MENU = 117, 163
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}

# key, name, description, icon, sign-in action, sign-out action
SERVICES = (
    ('nuvio', 'Nuvio', 'هوم Nuvio وإضافاتك ومتابعة المشاهدة',
     'nuvio.png', 'nuvio_connect', 'nuvio_logout'),
    ('stremio', 'Stremio', 'إضافاتك ومكتبتك في Stremio',
     'stremio.png', 'stremio_qr_login', 'stremio_logout'),
    ('trakt', 'Trakt', 'سجل المشاهدة وقوائمك ومتابعة المشاهدة',
     'trakt.png', 'trakt_auth', 'trakt_logout'),
    ('mdblist', 'MDBList', 'التقييمات بشعاراتها وقوائمك (مفتاح API)',
     'mdblist.png', 'mdblist_set_key', ''),
    ('plex', 'Plex', 'مكتباتك وسيرفراتك في Plex',
     'provider_plex.png', 'plex_login', 'plex_logout'),
    ('emby', 'Emby', 'سيرفر Emby الخاص بك',
     'provider_emby.png', 'emby_login', 'emby_logout'),
    ('jellyfin', 'Jellyfin', 'سيرفر Jellyfin الخاص بك',
     'provider_jellyfin.png', 'jellyfin_login', 'jellyfin_logout'),
    ('silo', 'Silo', 'حسابك في Silo ومكتباته',
     'provider_silo.png', 'silo_qr_login', 'silo_logout'),
    ('simkl', 'Simkl', 'سجل المشاهدة ومزامنته',
     'simkl.png', 'simkl_auth', 'simkl_logout'),
    ('addon', 'إضافة برابط', 'أضف أي إضافة Stremio برابطها',
     'root_add.png', 'add_provider', ''),
)
_KEYS = tuple(row[0] for row in SERVICES)
_MEDIA_KEYS = ('nuvio', 'stremio', 'plex', 'emby', 'jellyfin', 'silo', 'addon')
_BY_KEY = dict((row[0], row) for row in SERVICES)
# set by routes/accounts.py while an account sync runs: "service|start time"
SYNC_PROP = 'dexhub.account_sync.running'
_SYNC_MAX = 180.0
# the first-run screen was shown in this Kodi session: Later asks again at the
# next start, not on every visit to the menu
ASKED_PROP = 'dexhub.setup.asked'


def _addon():
    # a fresh handle: it reads what other invocations saved, and a write
    # through it cannot put back an older copy of the other settings
    return xbmcaddon.Addon(ADDON_ID)


def _linked(key):
    """Whether a service is linked. Local reads only (token files, settings)."""
    try:
        if key in ('nuvio', 'stremio'):
            from ..dexhub import nuvio_stremio_sync as sync
            return bool((sync.Nuvio if key == 'nuvio' else sync.Stremio).is_linked())
        if key == 'plex':
            from .. import plex_client
            return bool(plex_client.is_signed_in())
        if key in ('emby', 'jellyfin'):
            from .. import emby_client
            return bool(emby_client.is_signed_in(key))
        if key == 'silo':
            from ..providers import silo_provider
            return bool(silo_provider.is_signed_in())
        if key == 'addon':
            return bool(_addon_count())
        if key == 'trakt':
            from .. import trakt
            return bool(trakt.authorized())
        if key == 'simkl':
            from .. import simkl
            return bool(simkl.authorized())
        if key == 'mdblist':
            return bool((_addon().getSetting('mdblist_api_key') or '').strip())
    except Exception:
        return False
    return False


def _addon_count():
    try:
        from ..dexhub import store
        return len(store.list_providers() or [])
    except Exception:
        return 0


def syncing():
    """The services an account sync is pulling right now (another invocation)."""
    try:
        raw = xbmcgui.Window(10000).getProperty(SYNC_PROP) or ''
    except Exception:
        return ()
    if not raw:
        return ()
    services, _sep, started = raw.partition('|')
    try:
        if time.time() - float(started or 0) > _SYNC_MAX:
            return ()               # left behind by an invocation that died
    except Exception:
        return ()
    return tuple(part for part in services.split(',') if part)


def state():
    """What is linked now, for telling whether the screen changed anything."""
    try:
        from .. import collection_sets as cs
        collection = cs.builtin_collection_enabled()
    except Exception:
        collection = False
    return tuple(_linked(key) for key in _KEYS) + (_addon_count(), collection)


def should_show():
    """Ask when no media account/source is linked, once per Kodi session.

    A stale setup_done flag, ratings key or watch-history token does not
    make an empty installation ready to browse.
    """
    try:
        if xbmcgui.Window(10000).getProperty(ASKED_PROP):
            return False
    except Exception:
        return False
    if any(_linked(key) for key in _MEDIA_KEYS):
        # set up before this screen existed
        mark_done()
        return False
    return True


def mark_done():
    try:
        _addon().setSetting(SETTING, 'true')
    except Exception:
        pass


class SetupWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(SetupWindow, self).__init__(*args)
        self.tr = kwargs.get('tr') or (lambda text: text)
        self.media_dir = kwargs.get('media_dir') or ''
        self.first_run = bool(kwargs.get('first_run'))
        self.finished = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ctrls = {}
        self._cards = []
        self._buttons = []
        self._shown = {}
        self._linked_now = {}
        self._pull_due = 0.0
        self._hint = ''
        self._hint_shown = None
        self._thread = None
        self._inited = False
        # Kaptain's collection: ticked on the first run (kaptain.py)
        self._option = None
        self._checked = self.first_run
        self._option_shown = None

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            self._refresh()
            return
        self._inited = True
        tr = self.tr
        for control_id in (CARDS, BUTTONS):
            try:
                self._ctrls[control_id] = self.getControl(control_id)
            except Exception:
                pass
        if self.first_run:
            self.setProperty('ds.title', tr('مرحباً بك في Dex Hub'))
            self.setProperty('ds.subtitle', tr(
                'اربط Nuvio أو Stremio لتجيك صفحتك الرئيسية ومتابعة المشاهدة، وأضف أي خدمة ثانية '
                'تستخدمها. كل شيء اختياري وتقدر تغيّره لاحقاً.'))
            self._hint = tr('تقدر ترجع لهذه الشاشة متى ما تبي من القائمة في الصفحة الرئيسية أو من الإعدادات.')
            buttons = (('start', tr('ابدأ'), '1'), ('later', tr('لاحقاً'), ''))
        else:
            self.setProperty('ds.title', tr('الحسابات والمصادر'))
            self.setProperty('ds.subtitle', tr(
                'اربط حساباً أو مصدراً جديداً، أو اضغط على المربوط منها لخياراته. '
                'الصفحة الرئيسية تتحدث بعد الإغلاق.'))
            self._hint = ''
            buttons = (('start', tr('تم'), '1'),)
        self._hint_shown = None
        cards = []
        for key, name, desc, icon, _login, _logout in SERVICES:
            item = xbmcgui.ListItem(label=tr(name), label2=tr(desc))
            item.setProperty('key', key)
            item.setProperty('icon', os.path.join(self.media_dir, icon))
            cards.append(item)
        items = []
        for key, label, primary in buttons:
            item = xbmcgui.ListItem(label=label)
            item.setProperty('key', key)
            item.setProperty('primary', primary)
            items.append(item)
        option = xbmcgui.ListItem(label=tr('أضف مجموعة Kaptain للصفحة الرئيسية'))
        try:
            self._ctrls[OPTION] = self.getControl(OPTION)
        except Exception:
            option = None          # an older window file
        with self._lock:
            self._cards, self._buttons, self._option = cards, items, option
        self._refresh()
        for control_id, pool in ((CARDS, cards), (BUTTONS, items), (OPTION, [option] if option else [])):
            control = self._ctrls.get(control_id)
            if control is not None and pool:
                control.reset()
                control.addItems(pool)
        if option is not None:
            self.setProperty('ds.option', '1')
        try:
            self.setFocusId(CARDS)
        except Exception:
            pass
        self._thread = threading.Thread(target=self._watch, name='DexHub-setup-watch', daemon=True)
        self._thread.start()

    def _refresh(self):
        """Show what is linked. The sign-ins run in other invocations, so this polls."""
        tr = self.tr
        now = dict((key, _linked(key)) for key in _KEYS if key != 'addon')
        count = _addon_count()
        busy = syncing()
        if (self._linked_now and not self._linked_now.get('stremio') and now.get('stremio')):
            # linked here just now by QR: bring its add-ons and progress in
            # once the sign-in has saved its settings
            self._pull_due = time.monotonic() + 3.0
        self._linked_now = now
        with self._lock:
            for item in self._cards:
                key = item.getProperty('key')
                if key == 'addon':
                    on = count > 0
                    text = (tr('%d إضافة مثبتة') % count) if count else tr('أضف رابطاً')
                else:
                    on = bool(now.get(key))
                    text = tr('مربوط ✓') if on else tr('اضغط للربط')
                if on and key in busy or (key == 'stremio' and on and self._pull_due):
                    text = tr('جاري المزامنة…')
                if self._shown.get(key) != (on, text):
                    item.setProperty('linked', '1' if on else '')
                    item.setProperty('status', text)
                    self._shown[key] = (on, text)
        # a linked media server: its libraries become Home rows in the layout editor
        hint = (tr('تضيف مكتباتك كصفوف في الصفحة الرئيسية من القائمة › ترتيب الصفحة الرئيسية › إضافة صف.')
                if any(now.get(key) for key in ('plex', 'emby', 'jellyfin', 'silo')) else self._hint)
        if hint != self._hint_shown:
            self._hint_shown = hint
            try:
                self.setProperty('ds.hint', hint)
            except Exception:
                pass
        if self._pull_due and time.monotonic() >= self._pull_due:
            self._pull_due = 0.0
            self._run('account_sync_now&service=stremio&direction=pull')
        self._refresh_option(busy)

    def _refresh_option(self, busy=()):
        """Kaptain's collection: what adding it would do now (a Nuvio account
        linked meanwhile may bring collections of its own)."""
        option = self._option
        if option is None:
            return
        tr = self.tr
        from . import kaptain
        try:
            found = kaptain.state() if not busy else kaptain.STATE_NONE
        except Exception:
            found = kaptain.STATE_NONE
        if found == kaptain.STATE_HAS:
            text, locked = tr('موجودة عندك ✓'), True
        elif found == kaptain.STATE_OTHER:
            text, locked = tr('صفحتك الرئيسية فيها مجموعاتك الخاصة، فما نضيفها'), True
        else:
            text = tr('مجموعات جاهزة: منصات البث، الشبكات، الأنواع، الممثلين، المخرجين، السلاسل وغيرها')
            locked = False
        shown = (text, locked, self._checked, found)
        if shown != self._option_shown:
            self._option_shown = shown
            option.setLabel2(text)
            option.setProperty('locked', '1' if locked else '')
            option.setProperty('checked', '1' if (self._checked and not locked) else '')
            option.setProperty('has', '1' if found == kaptain.STATE_HAS else '')

    def _watch(self):
        monitor = xbmc.Monitor()
        while not self._stop.wait(1.5):
            if monitor.abortRequested():
                break
            try:
                self._refresh()
            except Exception:
                pass

    # ------------------------------------------------------------ actions
    def _selected(self, control_id):
        control = self._ctrls.get(control_id)
        if control is None:
            return ''
        try:
            pos = control.getSelectedPosition()
        except Exception:
            return ''
        with self._lock:
            pool = self._cards if control_id == CARDS else self._buttons
            if 0 <= pos < len(pool):
                return pool[pos].getProperty('key')
        return ''

    def _run(self, action):
        xbmc.executebuiltin('RunPlugin(plugin://%s/?action=%s)' % (ADDON_ID, action))

    def _card(self, key):
        row = _BY_KEY.get(key)
        if row is None:
            return
        _key, name, _desc, _icon, login, logout = row
        if key == 'addon' or not _linked(key):
            self._run(login)
            return
        tr = self.tr
        if key == 'mdblist':
            options = [('login', tr('تغيير المفتاح')), ('logout', tr('مسح المفتاح'))]
        else:
            options = [('login', tr('إعدادات Nuvio') if key == 'nuvio' else tr('إعادة الربط')),
                       ('logout', tr('فصل الحساب'))]
        from .options import choose
        choice = choose(name, [label for _k, label in options], subtitle=tr('مربوط'))
        if choice < 0:
            return
        if options[choice][0] == 'login':
            self._run(login)
        elif key == 'mdblist':
            if xbmcgui.Dialog().yesno(tr(name), tr('مسح مفتاح MDBList من Dex Hub؟')):
                try:
                    _addon().setSetting('mdblist_api_key', '')
                except Exception:
                    pass
                self._refresh()
        elif key == 'plex':
            self._run(logout)           # Plex asks itself
        elif xbmcgui.Dialog().yesno(tr(name), tr('فصل %s من Dex Hub؟') % tr(name)):
            self._run(logout)

    def onClick(self, control_id):
        if control_id == OPTION:
            option = self._option
            if option is not None and option.getProperty('locked') != '1':
                self._checked = not self._checked
                self._option_shown = None
                self._refresh_option()
            return
        key = self._selected(control_id)
        if not key:
            return
        if control_id == CARDS:
            self._card(key)
        elif control_id == BUTTONS:
            self.finish(done=(key == 'start'))

    def onAction(self, action):
        aid = action.getId()
        if aid in A_BACK:
            self.finish(done=True)
        elif aid in (A_CONTEXT, A_MENU):
            try:
                if self.getFocusId() == CARDS:
                    self._card(self._selected(CARDS))
            except Exception:
                pass

    def finish(self, done=True):
        expect = bool(self._pull_due)
        if expect:
            self._pull_due = 0.0
            self._run('account_sync_now&service=stremio&direction=pull')
        self._wait_for_sync(expect)
        self._stop.set()
        if done:
            mark_done()
        if self._checked and (done or not self.first_run):
            self._add_collection()
        self.finished = True
        self.close()

    def _add_collection(self):
        """Kaptain's collection, ticked: added unless the user has it (kaptain.py)."""
        from . import kaptain
        try:
            if kaptain.add(tr=self.tr) == kaptain.STATE_NONE:
                xbmcgui.Dialog().notification('Dex Hub', self.tr('أضفنا مجموعة Kaptain للصفحة الرئيسية'),
                                              xbmcgui.NOTIFICATION_INFO, 3500)
        except Exception:
            import traceback
            xbmc.log('[DexHub] Kaptain collection: %s' % traceback.format_exc(), xbmc.LOGWARNING)

    def _wait_for_sync(self, expect=False):
        """A sync still pulling an account: the Home is built from what it brings."""
        busy = syncing()
        if not busy and expect:
            # just started in another invocation, which marks itself shortly
            monitor = xbmc.Monitor()
            deadline = time.monotonic() + 4.0
            while not busy and time.monotonic() < deadline:
                if monitor.waitForAbort(0.1):
                    return
                busy = syncing()
        if not busy:
            return
        names = ', '.join(_BY_KEY[key][1] for key in busy if key in _BY_KEY) or 'Dex Hub'
        progress = xbmcgui.DialogProgress()
        progress.create('Dex Hub', self.tr('ننتظر اكتمال مزامنة %s…') % names)
        monitor = xbmc.Monitor()
        started = time.monotonic()
        try:
            while syncing() and not progress.iscanceled():
                elapsed = time.monotonic() - started
                progress.update(min(95, int(elapsed * 2)))
                if elapsed > 120 or monitor.waitForAbort(0.3):
                    break
        finally:
            progress.close()


def open_setup(tr=None, first_run=False):
    """v5.10.148: the setup window: every step on the first run, the
    accounts step on its own from the menu. True when something changed."""
    if first_run:
        # v5.10.229: the first run offers the phone first (a QR on the TV);
        # Back goes on with the setup on the TV
        try:
            from .. import phone_setup
            if phone_setup.run(first_run=True):
                mark_done()
                try:
                    xbmcgui.Window(10000).setProperty(ASKED_PROP, '1')
                    _addon().setSetting('welcome_seen', 'identity-2026')
                except Exception:
                    pass
                if any(_linked(key) for key in _MEDIA_KEYS):
                    return True
        except Exception:
            import traceback
            xbmc.log('[DexHub] phone setup: %s' % traceback.format_exc(), xbmc.LOGWARNING)
    try:
        from . import wizard
        return wizard.open_wizard(steps=None if first_run else ('accounts',), first_run=first_run, tr=tr)
    except Exception:
        import traceback
        xbmc.log('[DexHub] setup window: %s' % traceback.format_exc(), xbmc.LOGWARNING)
    return _open_setup_screen(tr, first_run)


def _open_setup_screen(tr=None, first_run=False):
    """Show the screen until it is closed. True when it linked or removed something."""
    addon = _addon()
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
    path = addon.getAddonInfo('path')
    if first_run:
        try:
            xbmcgui.Window(10000).setProperty(ASKED_PROP, '1')
        except Exception:
            pass
        try:
            # this screen is the welcome now; the older one is not shown after it
            addon.setSetting('welcome_seen', 'identity-2026')
        except Exception:
            pass
    before = state()
    from . import ui_size
    window = SetupWindow(ui_size.xml('dexhub_setup.xml'), path, 'Default', '1080i', tr=tr,
                         media_dir=os.path.join(path, 'resources', 'media'),
                         first_run=first_run)
    try:
        window.doModal()
    finally:
        window._stop.set()
        # the poll thread ends with the screen, before this invocation does
        if window._thread is not None:
            window._thread.join(2.0)
        del window
    return state() != before
