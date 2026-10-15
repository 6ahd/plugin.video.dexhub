# -*- coding: utf-8 -*-
"""Back in the player keeps the video playing (v5.10.99, widened in 5.10.100).

Kodi's own keymaps stop the video on some Back buttons in the fullscreen
player: a game controller or Bluetooth remote's B button (joystick.xml,
gamepad.xml), the Menu button of an Apple remote (customcontroller maps), and
a held Back or Backspace on a keyboard or IR remote (keyboard.xml, where a slow
IR key release reads as a long press). Other add-ons can add their own: Stremio
for Kodi's map stops the video on every Back. While the
Dex Hub Home is open, this user keymap sends those to the GUI instead (Kodi's
FullScreen action), so Back returns to the Home and the video goes on playing
in its now-playing card. Stop stays on the player's own controls and on the
card.

The map is held while the Dex Hub Home is open and while a Dex Hub video
plays (the companion service holds it for its own playback), and removed when
neither needs it, so Kodi's and other add-ons' keys return. The service clears
one left behind by a crash when Kodi starts.

v5.10.111: Kodi drops every action, the keymap reload too, while the topmost
modal dialog plays its closing animation ("ignoring action 203" in kodi.log),
and that is just when the Home opens from a skin's menu: Kodi's busy dialog is
closing. The map was written but never read, and as the file stayed the same
it was never read later either, so Back stopped the video again. A reload now
waits until no dialog is closing and is sent a second time a moment later, and
the map is read again whenever Kodi may not have it yet.
"""
import os
import threading

import xbmc
import xbmcgui
import xbmcvfs

_PROP = 'dexhub.keymap.%s'
_REASONS = ('home', 'play', 'choice')
# what Kodi read last: 'on' (with this map), 'off' (without), '' (not known)
_LOADED = 'dexhub.keymap.loaded'
_RELOAD = 'Action(reloadkeymaps)'
# Kodi ignores actions while the topmost modal dialog plays its closing
# animation: one condition (one call) for the dialogs that close just before a
# reload most often, and for any modal dialog when it is the only one
_CLOSING = ' | '.join(
    ['[System.HasVisibleModalDialog + !System.HasActiveModalDialog]']
    + ['[Window.IsVisible(%s) + !Window.IsActive(%s)]' % (name, name)
       for name in ('busydialog', 'busydialognocancel', 'selectdialog', 'contextmenu',
                    'yesnodialog', 'movieinformation')])
_SETTLE_WAIT = 4.0      # seconds a reload waits for a closing dialog at most
_SETTLE_STEP = 0.05
_CONFIRM_AFTER = 0.8    # the second reload, once the first had its moment
_lock = threading.Lock()
_job = {'gen': 0, 'thread': None}

NAME = 'zzzz_dexhub_home_player.xml'
# Kodi reads keymap files in name order and the last file wins, so this name
# sorts after other add-ons' maps (Stremio for Kodi installs
# zz-stremio-for-kodi-back.xml, which stops the video on Back). 5.10.99 used
# this older name, which sorted before them.
OLD_NAMES = ('dexhub_home_player.xml',)
CONTENT = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by Dex Hub while its Home is open and removed when it closes:
     Back in the player returns to the Home and the video keeps playing. -->
<keymap>
  <FullscreenVideo>
    <keyboard>
      <backspace>FullScreen</backspace>
      <backspace mod="longpress">FullScreen</backspace>
      <browser_back>FullScreen</browser_back>
      <browser_back mod="longpress">FullScreen</browser_back>
      <escape>FullScreen</escape>
      <escape mod="longpress">FullScreen</escape>
    </keyboard>
    <remote>
      <back>FullScreen</back>
    </remote>
    <joystick profile="game.controller.default">
      <b>FullScreen</b>
      <back>FullScreen</back>
    </joystick>
    <gamepad>
      <B>FullScreen</B>
    </gamepad>
    <customcontroller name="AppleRemote">
      <button id="6">FullScreen</button>
    </customcontroller>
    <customcontroller name="SiriRemote">
      <button id="6">FullScreen</button>
    </customcontroller>
  </FullscreenVideo>
  <VideoOSD>
    <keyboard>
      <backspace>Back</backspace>
      <browser_back>Back</browser_back>
      <escape>Back</escape>
    </keyboard>
    <remote>
      <back>Back</back>
    </remote>
    <joystick profile="game.controller.default">
      <b>Back</b>
    </joystick>
    <customcontroller name="AppleRemote">
      <button id="6">Back</button>
    </customcontroller>
    <customcontroller name="SiriRemote">
      <button id="6">Back</button>
    </customcontroller>
  </VideoOSD>
</keymap>
'''


# v5.10.245: the choice "Back stops the video" (Settings, Player): the same
# keys stop it instead, so it never plays on unseen behind the Home
STOP_CONTENT = CONTENT.replace('<!-- Written by Dex Hub while its Home is open and removed when it closes:\n'
                               '     Back in the player returns to the Home and the video keeps playing. -->',
                               '<!-- Written by Dex Hub: Back in the player stops the video (Settings, Player). -->'
                               ).replace('>FullScreen</', '>Stop</')
_CHOICE = 'video_back_background'


def _background():
    """True: Back leaves the video playing behind the Home (the default)."""
    try:
        import xbmcaddon
        return (xbmcaddon.Addon('plugin.video.dexhub').getSetting(_CHOICE) or 'true').lower() != 'false'
    except Exception:
        return True


def _content():
    return CONTENT if _background() else STOP_CONTENT


def always():
    """The choice changed: the map is written again in its new form (and
    held for every video), so Kodi's or another add-on's map never decides
    what Back does in the player."""
    hold('choice')


def _path():
    folder = xbmcvfs.translatePath('special://profile/keymaps/')
    return folder, os.path.join(folder, NAME)


def _loaded(value=None):
    window = xbmcgui.Window(10000)
    if value is None:
        return window.getProperty(_LOADED)
    window.setProperty(_LOADED, value)
    return value


def _closing():
    try:
        return xbmc.getCondVisibility(_CLOSING)
    except Exception:
        return False


def _send(monitor):
    """The reload, once no dialog is closing: True when it went at such a
    moment, False when it went after the longest wait (Kodi may drop it)."""
    waited = 0.0
    while _closing():
        if waited >= _SETTLE_WAIT:
            xbmc.log('[DexHub] homeui: player keymap read while a dialog still closes', xbmc.LOGWARNING)
            xbmc.executebuiltin(_RELOAD)
            return False
        if monitor.waitForAbort(_SETTLE_STEP):
            return False
        waited += _SETTLE_STEP
    xbmc.executebuiltin(_RELOAD)
    if waited:
        xbmc.log('[DexHub] homeui: player keymap read after a closing dialog (%.2fs)' % waited, xbmc.LOGINFO)
    return True


def _run():
    monitor = xbmc.Monitor()
    finished = False
    try:
        while not monitor.abortRequested():
            with _lock:
                gen = _job['gen']
            _send(monitor)
            # a dialog that began to close just after the check above still
            # drops the first one: the second goes once things are calm
            if monitor.waitForAbort(_CONFIRM_AFTER):
                break
            settled = _send(monitor)
            state = 'on' if os.path.exists(_path()[1]) else 'off'
            with _lock:
                if _job['gen'] == gen:
                    # done, in the same step as a new change would look
                    _job['thread'] = None
                    finished = True
                    try:
                        _loaded(state if settled else '')
                    except Exception:
                        pass
                    return
            # the map changed meanwhile: read it again
    finally:
        if not finished:
            with _lock:
                _job['thread'] = None


def _reload():
    """Have Kodi read the keymaps again (in a short thread: the reload waits
    for a closing dialog, and goes twice)."""
    try:
        _loaded('')
    except Exception:
        pass
    with _lock:
        _job['gen'] += 1
        if _job['thread'] is not None:
            return          # the running pass reads the map again for this
        thread = threading.Thread(target=_run, name='DexHub-keymap')
        thread.daemon = True
        _job['thread'] = thread
    try:
        thread.start()
    except Exception:
        with _lock:
            _job['thread'] = None
        xbmc.executebuiltin(_RELOAD)


def install():
    """Write the keymap (once) and have Kodi read it, again whenever Kodi
    may not have read it yet."""
    try:
        folder, path = _path()
        for old in OLD_NAMES:
            try:
                os.remove(os.path.join(folder, old))
            except Exception:
                pass
        same = False
        try:
            with open(path, encoding='utf-8') as handle:
                same = handle.read() == _content()
        except Exception:
            pass
        if not same:
            os.makedirs(folder, exist_ok=True)
            # v5.10.305: written whole then moved in place; Kodi reading the
            # file half written logged "Error loading keymap ... Line 0"
            temp = path + '.tmp'
            with open(temp, 'w', encoding='utf-8') as handle:
                handle.write(_content())
            os.replace(temp, path)
        if not same or _loaded() != 'on':
            _reload()
    except Exception as exc:
        xbmc.log('[DexHub] homeui: player keymap not written: %s' % exc, xbmc.LOGWARNING)


def remove():
    """Take the keymap away again; Kodi's own Back keys return."""
    try:
        folder, path = _path()
        removed = False
        for name in (NAME,) + OLD_NAMES:
            target = os.path.join(folder, name)
            if os.path.exists(target):
                os.remove(target)
                removed = True
        if removed or _loaded() == 'on':
            _reload()
    except Exception as exc:
        xbmc.log('[DexHub] homeui: player keymap not removed: %s' % exc, xbmc.LOGWARNING)


def _flag(reason, value=None):
    window = xbmcgui.Window(10000)
    if value is None:
        return window.getProperty(_PROP % reason)
    window.setProperty(_PROP % reason, value)
    return value


def sync():
    """Install the map while something holds it, remove it otherwise."""
    try:
        wanted = any(_flag(reason) for reason in _REASONS)
    except Exception:
        wanted = False
    if wanted:
        install()
    else:
        remove()


def hold(reason):
    """reason: 'home' (the Home is open) or 'play' (a Dex Hub video plays)."""
    try:
        _flag(reason, '1')
    except Exception:
        pass
    sync()


def release(reason):
    try:
        _flag(reason, '')
    except Exception:
        pass
    sync()


def reset():
    """Kodi's start: nothing holds the map yet, so a leftover file goes."""
    for reason in _REASONS:
        try:
            _flag(reason, '')
        except Exception:
            pass
    remove()
