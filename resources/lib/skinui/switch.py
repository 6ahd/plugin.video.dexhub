# -*- coding: utf-8 -*-
"""The Dex Hub skin as Kodi's skin, and back again (v5.10.113).

skin_use   switches Kodi to skin.dexhub:
             1. the skin is installed when it is missing: from the
                repositories Kodi knows (Dex Hub's repository carries it),
                else from a zip the user picks;
             2. it is enabled (a disabled skin makes Kodi ask about it);
             3. Dex Hub's own windows close (a skin change tears every window
                down, and a Python window torn down under its script hangs or
                crashes), a Dex Hub trailer stops, and the skin in use is
                remembered;
             4. Kodi takes the skin.
skin_back  switches back to the skin that was in use before (Estuary when
           there is none, or it is gone).

Kodi asks questions on the way: "install this add-on?", and after the new
skin loaded "keep this change?", going back to the old skin when nobody
answers within ten seconds. As skin switchers do it (ABUKARIM TOOLS' Skin
Switcher answers the same way), the answers are given here: Yes on Kodi's
yes/no dialog (its control 11) while a step runs, so the user sees the new
skin's Home and nothing else. One switch runs at a time (a Home property:
it outlives the skin change, and a restart of Kodi clears it).
"""
import json
import os
import shutil
import tempfile
import threading
import time
import zipfile

import xbmc
import xbmcgui

from . import common as C

SKIN = C.SKIN_ID
DEFAULT_SKIN = 'skin.estuary'
STATE = 'switch.json'           # previous successful skin; back toggles between the last two
P_BUSY = 'dexhub.skinswitch.busy'
YES = 11                        # the Yes button of Kodi's yes/no dialog
_BUSY_FOR = 180.0               # a switch older than this is over (it died)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _tr(text):
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


def _cond(expr):
    try:
        return bool(xbmc.getCondVisibility(expr))
    except Exception:
        return False


def _rpc(method, params=None):
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params is not None:
        payload['params'] = params
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(payload))) or {}
    except Exception as exc:
        C.log('skin switch: %s failed: %s' % (method, exc), xbmc.LOGWARNING)
        return {}


def current():
    try:
        return xbmc.getSkinDir() or ''
    except Exception:
        return ''


def installed(addon_id):
    return _cond('System.HasAddon(%s)' % addon_id)


def enabled(addon_id):
    result = _rpc('Addons.GetAddonDetails', {'addonid': addon_id, 'properties': ['enabled']})
    try:
        return bool(result['result']['addon']['enabled'])
    except Exception:
        return False


def enable(addon_id, monitor):
    """Enable without Kodi's "enable this add-on?" question (JSON-RPC)."""
    if enabled(addon_id):
        return True
    _rpc('Addons.SetAddonEnabled', {'addonid': addon_id, 'enabled': True})
    for _ in range(20):
        if enabled(addon_id):
            return True
        if monitor.waitForAbort(0.2):
            return False
    return enabled(addon_id)


def addon_name(addon_id):
    try:
        import xbmcaddon
        return xbmcaddon.Addon(addon_id).getAddonInfo('name') or addon_id
    except Exception:
        return addon_id


def _state_path():
    return os.path.join(C.folder(), STATE)


def previous():
    """Last successfully replaced skin, when installed; otherwise Estuary."""
    skin = (C.read_json(_state_path(), {}) or {}).get('previous') or ''
    if skin and skin != current() and installed(skin):
        return skin
    return DEFAULT_SKIN


def _remember(skin):
    if skin:
        state = C.read_json(_state_path(), {}) or {}
        state['previous'] = skin
        state['t'] = int(time.time())
        C.write_json(_state_path(), state)


def busy():
    try:
        since = float(C.prop(P_BUSY) or 0)
    except ValueError:
        return False
    return bool(since) and time.time() - since < _BUSY_FOR


def _notify(message, icon=xbmcgui.NOTIFICATION_INFO, ms=3000):
    try:
        xbmcgui.Dialog().notification(_tr('سكين Dex Hub'), message, icon, ms, sound=False)
    except Exception:
        pass


def _ok(message):
    try:
        xbmcgui.Dialog().ok(_tr('سكين Dex Hub'), message)
    except Exception:
        pass


def _yesno(message):
    try:
        return bool(xbmcgui.Dialog().yesno(_tr('سكين Dex Hub'), message))
    except Exception:
        return False


class _Answers(threading.Thread):
    """Answers Yes to Kodi's yes/no questions while a step runs (install this
    add-on? keep this skin?). ``clicks`` counts them."""

    def __init__(self, target=''):
        super(_Answers, self).__init__(name='dexhub-skin-answers')
        self.daemon = True
        self._halt = threading.Event()
        self.clicks = 0
        self.last = 0.0
        self.target = target

    def run(self):
        while not self._halt.is_set():
            if (_cond('Window.IsActive(yesnodialog)') and time.time() - self.last > 0.35
                    and (not self.target or current() == self.target)):
                xbmc.executebuiltin('SendClick(yesnodialog,%d)' % YES)
                self.clicks += 1
                self.last = time.time()
                C.log('skin switch: answered Kodi\'s question (yes)')
            self._halt.wait(0.08)

    def stop(self):
        self._halt.set()
        if self is not threading.current_thread() and self.is_alive():
            self.join(0.4)


# --------------------------------------------------------------------------
# installing
# --------------------------------------------------------------------------

def _install_from_repo(monitor):
    """Kodi installs the skin from a repository it knows (it asks first: the
    answer is yes). False when no repository carries it."""
    for attempt in (1, 2):
        if attempt == 2:
            # the repositories' lists may be older than the skin: read again
            xbmc.executebuiltin('UpdateAddonRepos')
            if monitor.waitForAbort(8):
                return False
        answers = _Answers()
        answers.start()
        try:
            xbmc.executebuiltin('InstallAddon(%s)' % SKIN)
            started = time.time()
            seen = False
            while time.time() - started < 240:
                if installed(SKIN):
                    break
                if answers.clicks or _cond('Window.IsActive(progressdialog) | '
                                           'Window.IsActive(extendedprogressdialog)'):
                    seen = True
                if not seen and time.time() - started > 6:
                    break               # no repository has it: Kodi said nothing
                if monitor.waitForAbort(0.25):
                    return False
        finally:
            answers.stop()
        if installed(SKIN):
            C.log('skin switch: %s installed from a repository' % SKIN)
            return True
    return False


def _merge(src, dest):
    """Copy a folder over another file by file: files Kodi holds open (a skin
    in use) are written in place."""
    if os.path.isdir(src):
        os.makedirs(dest, exist_ok=True)
        for name in os.listdir(src):
            _merge(os.path.join(src, name), os.path.join(dest, name))
    else:
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copyfile(src, dest)


def _install_from_zip(monitor):
    """The skin from a zip the user picks (skin.dexhub-x.y.z.zip)."""
    import xbmcvfs
    path = xbmcgui.Dialog().browse(1, _tr('اختر ملف سكين Dex Hub (zip)'), 'files', '.zip')
    if not path:
        return False
    work = tempfile.mkdtemp(prefix='dexhub_skin_', dir=xbmcvfs.translatePath('special://temp/'))
    try:
        local = xbmcvfs.translatePath(path)
        if not os.path.isfile(local):
            # a network share: copied here first
            local = os.path.join(work, 'skin.zip')
            if not xbmcvfs.copy(path, local):
                raise ValueError('could not read %s' % path)
        with zipfile.ZipFile(local) as archive:
            names = archive.namelist()
            if '%s/addon.xml' % SKIN not in names or any(not n.startswith(SKIN + '/') for n in names):
                _ok(_tr('هذا الملف ليس سكين Dex Hub.'))
                return False
            archive.extractall(os.path.join(work, 'x'))
        src = os.path.join(work, 'x', SKIN)
        dest = os.path.join(xbmcvfs.translatePath('special://home/addons/'), SKIN)
        if os.path.isdir(dest) and current() != SKIN:
            # not in use: the old copy goes as a whole (no leftovers of it)
            shutil.rmtree(dest, ignore_errors=True)
        _merge(src, dest)
    except Exception as exc:
        C.log('skin switch: the zip could not be installed: %s' % exc, xbmc.LOGWARNING)
        _ok(_tr('تعذر تثبيت سكين Dex Hub.'))
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)
    xbmc.executebuiltin('UpdateLocalAddons')
    for _ in range(40):
        if installed(SKIN):
            C.log('skin switch: %s installed from %s' % (SKIN, os.path.basename(path.rstrip('/'))))
            return True
        if monitor.waitForAbort(0.25):
            return False
    return installed(SKIN)


def ensure_installed(monitor, interactive=True):
    if installed(SKIN):
        return True
    if interactive and not _yesno(_tr('سكين Dex Hub غير مثبت. يثبت الآن من المستودع؟')):
        return False
    _notify(_tr('يثبت سكين Dex Hub'))
    if _install_from_repo(monitor):
        return True
    if not interactive:
        return False
    if not _yesno(_tr('سكين Dex Hub غير موجود في مستودعاتك. تختار ملفه (zip)؟')):
        return False
    return _install_from_zip(monitor)


# --------------------------------------------------------------------------
# switching
# --------------------------------------------------------------------------

def _clear_screen(monitor):
    """Nothing of Dex Hub left open when the skin changes: Kodi's dialogs
    close, Kodi's Home comes up (Dex Hub's own Home windows close themselves
    there, homeui.app), and a Dex Hub trailer stops."""
    if C.prop('dexhub.trailer.active'):
        try:
            xbmc.Player().stop()
        except Exception:
            pass
    if _cond('Window.IsActive(addonsettings)'):
        # the switch was asked from the add-on's settings: OK keeps what was
        # changed there (a plain close would throw it away)
        xbmc.executebuiltin('SendClick(addonsettings,28)')
        for _ in range(10):
            if not _cond('Window.IsActive(addonsettings)'):
                break
            if monitor.waitForAbort(0.2):
                return False
    xbmc.executebuiltin('Dialog.Close(all,true)')
    for _ in range(15):
        if not _cond('System.HasActiveModalDialog'):
            break
        if monitor.waitForAbort(0.2):
            return False
    xbmc.executebuiltin('ActivateWindow(Home)')
    deadline = time.time() + 6.0
    while time.time() < deadline:
        try:
            alive = float(C.prop('dexhub.homeui.alive') or 0)
        except ValueError:
            alive = 0.0
        if not C.prop('dexhub.homeui.window') or time.time() - alive > 2.5:
            break
        if monitor.waitForAbort(0.25):
            return False
    # the trailer's stop and the windows' close settle
    return not monitor.waitForAbort(0.6)


def apply(target, monitor):
    """Kodi takes the skin ``target``; its questions are answered yes. True
    when the skin is in use and stays (Kodi has not gone back)."""
    answers = _Answers(target=target)
    answers.start()
    try:
        for attempt in range(3):
            if current() == target:
                break
            _rpc('Settings.SetSettingValue', {'setting': 'lookandfeel.skin', 'value': target})
            deadline = time.time() + 12.0
            while time.time() < deadline and current() != target:
                if monitor.waitForAbort(0.2):
                    return False
            C.log('skin switch: attempt %d, skin in use %s' % (attempt + 1, current()))
        if current() != target:
            return False
        # "keep this change?" comes right after the skin loaded: answered by
        # the watcher. Over once it was answered and nothing is asked for a
        # moment, or when Kodi asked nothing within five seconds.
        loaded = time.time()
        quiet = time.time()
        while time.time() - loaded < 16.0:
            if _cond('Window.IsActive(yesnodialog)'):
                quiet = time.time()
            elif answers.clicks and time.time() - quiet > 0.45:
                break
            elif not answers.clicks and time.time() - loaded > 5.0:
                break
            if monitor.waitForAbort(0.2):
                return False
    finally:
        answers.stop()
    # Kodi went back when the question was not answered in time
    if monitor.waitForAbort(1.0):
        return False
    return current() == target


def _switch(target, monitor, interactive, remember):
    if busy():
        _notify(_tr('تغيير السكين يجري الآن'))
        return False
    C.set_prop(P_BUSY, '%.1f' % time.time())
    try:
        origin = current()
        if origin == target:
            return True
        if _cond('Player.HasVideo') and not C.prop('dexhub.trailer.active'):
            # Pressing the switch button explicitly requests this transition.
            xbmc.Player().stop()
            for _ in range(25):
                if not _cond('Player.HasMedia'):
                    break
                if monitor.waitForAbort(0.2):
                    return False
        if not enable(target, monitor):
            _ok(_tr('تعذر تغيير السكين.'))
            return False
        if not _clear_screen(monitor):
            return False
        C.log('skin switch: %s -> %s' % (current(), target))
        ok = apply(target, monitor)
        C.log('skin switch: %s (skin in use %s)' % ('done' if ok else 'failed', current()))
        if ok:
            _remember(origin)  # every successful switch, including between other skins
            xbmc.executebuiltin('Dialog.Close(all,true)')
            xbmc.executebuiltin('ActivateWindow(Home)')
            if target == SKIN:
                # Kodi gives the Home the focus the old skin's Home had (its
                # control's number): the spotlight's Play takes it instead
                hero = getattr(C.layout(), 'hero_buttons', 0)
                if hero and not monitor.waitForAbort(0.3):
                    xbmc.executebuiltin('SetFocus(%d)' % hero)
        else:
            _ok(_tr('تعذر تغيير السكين.'))
        return ok
    finally:
        C.set_prop(P_BUSY, '')


def use(params=None, handle=-1):
    """skin_use: Kodi's skin becomes skin.dexhub (installed first when missing)."""
    monitor = xbmc.Monitor()
    interactive = (params or {}).get('ask', '1') != '0'
    if current() == SKIN and C.skin_active():
        _notify(_tr('سكين Dex Hub مستخدم الآن'))
        return None
    if interactive and installed(SKIN):
        if not _yesno(_tr('استخدام سكين Dex Hub؟') + '[CR]' +
                      _tr('يتغير سكين كودي إلى سكين Dex Hub، وتقدر ترجع للسكين الحالي من قائمته.')):
            return None
    if not ensure_installed(monitor, interactive=interactive):
        if not installed(SKIN):
            _ok(_tr('تعذر تثبيت سكين Dex Hub.'))
        return None
    _switch(SKIN, monitor, interactive, remember=True)
    return None


def skins():
    """Kodi's installed skins: [(id, name, icon, version)], the one in use
    first, then by name."""
    result = _rpc('Addons.GetAddons', {'type': 'xbmc.gui.skin', 'installed': True, 'enabled': 'all',
                                       'properties': ['name', 'thumbnail', 'version']})
    try:
        found = (result.get('result') or {}).get('addons') or []
    except Exception:
        found = []
    out = []
    for addon in found:
        sid = str(addon.get('addonid') or '').strip()
        if not sid:
            continue
        out.append((sid, str(addon.get('name') or sid).strip(), str(addon.get('thumbnail') or ''),
                    str(addon.get('version') or '')))
    in_use = current()
    out.sort(key=lambda s: (s[0] != in_use, s[1].lower()))
    return out


def pick(params=None, handle=-1):
    """skin_pick (v5.10.118): any installed skin, taken at once: the list of
    Kodi's skins, and the one chosen is Kodi's skin a moment later, Kodi's
    "keep this change?" answered, no trip through Kodi's settings."""
    monitor = xbmc.Monitor()
    found = skins()
    if not found:
        _ok(_tr('تعذر قراءة السكينات المثبتة.'))
        return None
    in_use = current()
    items, preselect = [], 0
    for index, (sid, name, icon, version) in enumerate(found):
        item = xbmcgui.ListItem(label=name, offscreen=True)
        if sid == in_use:
            item.setLabel2(_tr('المستخدم الآن'))
            preselect = index
        else:
            item.setLabel2(('%s  %s' % (sid, version)).strip())
        if icon:
            item.setArt({'icon': icon, 'thumb': icon})
        items.append(item)
    choice = xbmcgui.Dialog().select(_tr('تغيير السكين'), items, preselect=preselect, useDetails=True)
    if choice < 0:
        return None
    target = found[choice][0]
    if target == in_use:
        _notify(_tr('هذا السكين مستخدم الآن'))
        return None
    C.log('skin switch: picked %s' % target)
    _switch(target, monitor, True, remember=(target == SKIN))
    return None


def back(params=None, handle=-1):
    """skin_back: the skin in use before skin.dexhub (Estuary without one)."""
    monitor = xbmc.Monitor()
    interactive = (params or {}).get('ask', '1') != '0'
    target = previous()
    if current() == target:
        return None
    _switch(target, monitor, interactive, remember=False)
    return None
