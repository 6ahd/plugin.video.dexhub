# -*- coding: utf-8 -*-
"""skin.dexhub's colour theme and Dex Hub's theme, kept in step (v5.10.131).

Kodi lists skin.dexhub's colour themes in Interface > Skin > Colours: they
are Dex Hub's themes by name (the skin's colors/<name>.xml). On skin.dexhub
the palette Dex Hub publishes is the colour theme's (skin_theme.resolve_palette),
so the skin and Dex Hub's own windows always wear the same theme. This keeps
Dex Hub's theme setting the same as the colour theme picked in Kodi, and a
theme picked in Dex Hub (its theme picker, its settings) becomes the skin's
colour theme. Kodi reloads the skin for a new colour theme, which closes
script windows, so that waits until no Dex Hub window is open, no dialog
shows and no video plays.

tick() runs on the skin worker's thread (skinui/worker.py) while skin.dexhub
is in use; nothing here runs under any other skin.
"""
import json
import time

import xbmc
import xbmcaddon
import xbmcgui

from . import skin_theme as T

ADDON_ID = 'plugin.video.dexhub'
# set by the Home's app while a Dex Hub window is open (homeui/app.py)
PROP_WINDOWS = 'dexhub.homeui.windows'
# v5.10.133: a theme picked in skin.dexhub's own settings (its theme dialog sets it)
PROP_WANT = 'dhs.theme.want'
_EVERY = 2.0

_state = {'colour': None, 'setting': None, 'look': None, 'pending': '', 'asked_at': 0.0, 'at': 0.0}


def _log(msg):
    xbmc.log('[DexHub] theme: %s' % msg, xbmc.LOGINFO)


def _rpc(method, params):
    try:
        raw = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}))
        return json.loads(raw or '{}')
    except Exception:
        return {}


def skin_colours():
    """Kodi's colour theme setting: 'SKINDEFAULT' or a theme's name."""
    reply = _rpc('Settings.GetSettingValue', {'setting': 'lookandfeel.skincolors'})
    return str(((reply or {}).get('result') or {}).get('value') or '')


def set_skin_colours(key):
    """Make preset ``key`` the skin's colour theme (Kodi reloads the skin)."""
    name = T.colour_file(key)
    if not name:
        return False
    reply = _rpc('Settings.SetSettingValue', {'setting': 'lookandfeel.skincolors', 'value': name})
    ok = bool((reply or {}).get('result'))
    _log('skin colours set to %s (%s)' % (name, 'done' if ok else 'refused'))
    return ok


def _setting():
    try:
        return (xbmcaddon.Addon(ADDON_ID).getSetting('theme_preset') or T.DEFAULT_PRESET).strip()
    except Exception:
        return T.DEFAULT_PRESET


def _write_setting(key):
    try:
        xbmcaddon.Addon(ADDON_ID).setSetting('theme_preset', str(key))
    except Exception:
        pass


def busy():
    """A Dex Hub window, a dialog or a video: no skin reload now."""
    try:
        if xbmcgui.Window(10000).getProperty(PROP_WINDOWS):
            return True
    except Exception:
        return True
    return xbmc.getCondVisibility('System.HasActiveModalDialog | Player.HasVideo | Window.IsActive(addonsettings) | '
                                  'Window.IsActive(busydialog) | Window.IsActive(busydialognocancel) | '
                                  'Window.IsActive(progressdialog) | Window.IsActive(extendedprogressdialog) | '
                                  'Window.IsActive(Custom_1191_DexThemes.xml) | '
                                  'Window.IsActive(Custom_1192_DexFocus.xml)')


def _look():
    """The skin's own theme options (glow strength, an accent of the user's,
    the text on the focus and the focus glow, v5.10.132)."""
    return '|'.join(xbmc.getInfoLabel('Skin.String(%s)' % name)
                    for name in ('dh.glow', 'dh.accent.custom', 'dh.ontext', 'dh.focusglow', 'dh.amoled'))


def _take_want():
    """v5.10.133: a theme picked in skin.dexhub's theme dialog: shown at once
    (published as pending: skin_theme.resolve_palette wears it), Dex Hub's
    setting follows, and Kodi's colour theme takes it as soon as the dialog
    is closed (a colour theme reloads the skin). True when there was one."""
    home = xbmcgui.Window(10000)
    want = (home.getProperty(PROP_WANT) or '').strip()
    if not want:
        return False
    home.clearProperty(PROP_WANT)
    if not T.colour_file(want):
        return False
    _write_setting(want)
    st = _state
    st['setting'] = want
    if T.preset_for_colour(skin_colours()) == want:
        # the colour theme Kodi has already
        home.clearProperty(T.PROP_PENDING)
        st['pending'] = ''
    else:
        home.setProperty(T.PROP_PENDING, want)
        st['pending'] = want
        st['asked_at'] = 0.0
    _log('theme %s picked in the skin' % T.colour_file(want))
    T.publish_theme()
    return True


def _pending_done(colour):
    """Kodi's colour theme is the one picked: nothing pending any more."""
    home = xbmcgui.Window(10000)
    pending = home.getProperty(T.PROP_PENDING)
    if pending and T.preset_for_colour(colour) == pending:
        home.clearProperty(T.PROP_PENDING)
        return True
    return False


def picked(key):
    """A theme picked in Dex Hub on skin.dexhub: the skin takes it now when it
    can, else as soon as nothing is open. True when it applies now."""
    if xbmc.getSkinDir() != T.DEXHUB_SKIN or not T.colour_file(key):
        return False
    _state['setting'] = str(key)
    if T.preset_for_colour(skin_colours()) == str(key):
        return True
    if busy():
        _state['pending'] = str(key)
        return False
    _state['asked_at'] = time.time()
    return set_skin_colours(key)


def reset():
    """skin.dexhub is (again) the skin: the next tick takes a first look.

    Kodi puts the colour theme back to the skin's default when the skin
    changes, so a default colour theme on return is no choice of the user's:
    Dex Hub's theme goes back to the skin then.
    """
    _state.update(colour=None, setting=None, look=None, pending='', asked_at=0.0, at=0.0)


def _idle():
    try:
        return xbmc.getGlobalIdleTime() >= 20
    except Exception:
        return False


def tick(force=False):
    """Every couple of seconds while skin.dexhub is in use (the skin's own
    look options, a few info labels, at every call: a focus colour or text
    picked in its settings shows at once, v5.10.132)."""
    T.ensure_theme()     # v5.10.298: a skin reload can drop the Home properties
    now = time.time()
    if not force and now - _state['at'] < _EVERY:
        if _idle():
            return          # v5.10.198: nothing is being picked while nothing is pressed
        if _state['look'] is not None and xbmc.getSkinDir() == T.DEXHUB_SKIN:
            if _take_want():
                return
            look = _look()
            if look != _state['look']:
                _state['look'] = look
                T.publish_theme()
        return
    _state['at'] = now
    if xbmc.getSkinDir() != T.DEXHUB_SKIN:
        _state['colour'] = None
        return
    colour = skin_colours()
    setting = _setting()
    look = _look()
    from_colour = T.preset_for_colour(colour)
    st = _state
    if st['colour'] is None:
        # the first look (Kodi started, or the skin was just picked)
        st.update(colour=colour, setting=setting, look=look)
        if colour in ('', 'SKINDEFAULT') and setting not in ('0', T.DEFAULT_PRESET) and T.colour_file(setting):
            # a theme picked in Dex Hub before the skin had colour themes:
            # it becomes the skin's
            st['pending'] = setting
        elif setting not in ('0', from_colour):
            _write_setting(from_colour)
            st['setting'] = from_colour
        T.publish_theme()
        return
    if _take_want():
        return
    if colour != st['colour']:
        # Kodi's Colours changed (the user, or a theme picked in Dex Hub)
        st['colour'] = colour
        _pending_done(colour)
        if st['pending'] == from_colour:
            st['pending'] = ''
        if setting not in ('0', from_colour) and not st['pending']:
            _write_setting(from_colour)
            setting = from_colour
        st['setting'] = setting
        _log('colour theme %s' % (colour or 'SKINDEFAULT'))
        T.publish_theme()
        return
    if setting != st['setting']:
        # Dex Hub's theme setting changed (its settings dialog)
        st['setting'] = setting
        if setting != '0' and T.colour_file(setting) and setting != from_colour:
            st['pending'] = setting
    if look != st['look']:
        st['look'] = look
        T.publish_theme()
        try:
            from . import cloud_look      # v5.10.366: the skin's look goes to the cloud
            cloud_look.mark()
        except Exception:
            pass
    if st['pending'] and now - st['asked_at'] > 10.0 and not busy():
        st['asked_at'] = now
        if st['pending'] == from_colour:
            st['pending'] = ''
            _pending_done(colour)
            return
        set_skin_colours(st['pending'])
