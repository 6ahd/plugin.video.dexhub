# -*- coding: utf-8 -*-
"""Dex Hub's YouTube engine (script.dexhub.youtube) put on the device by Dex Hub (v5.10.616).

The engine is the official YouTube add-on under an id of its own (its own sign-in, its own local
server on port 50153): no build, wizard or other repository replaces it. Opening YouTube in Dex Hub
without it installs it out of sight, the way the Dex Hub skin is installed (skinui/switch.py):
  1. from a repository Kodi knows (DexWorld's carries it): Kodi brings what it needs too
     (script.module.requests, inputstream.adaptive), and its question is answered yes;
  2. else straight from DexWorld's repository (the zip, checked), then whatever it needs that the
     device lacks from Kodi's repositories;
  3. it is switched on (JSON-RPC, no question).
While it is missing, or when it cannot be installed, YouTube plays through the official add-on as
before. A failed try is not repeated for a day; never on Apple TV (no inputstream.adaptive there).
"""
import os
import shutil
import tempfile
import threading
import time
import zipfile

import xbmc

ENGINE = 'script.dexhub.youtube'
NEEDS = ('script.module.requests', 'inputstream.adaptive')
DEXWORLD_REPOS = ('https://dexworld.cc/kodi/', 'https://dexworld.cc/dexhub/')
RETRY_AFTER = 86400
BUSY = 'dexhub.yt.engine.busy'
_STATE = 'youtube_engine.json'


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] youtube engine: %s' % msg, level)


def ready():
    return bool(xbmc.getCondVisibility('System.HasAddon(%s)' % ENGINE)
                and xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % ENGINE))


def _state_path():
    import xbmcaddon
    import xbmcvfs
    return os.path.join(xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile')), _STATE)


def _state():
    try:
        import json
        with open(_state_path(), encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember(ok, how=''):
    try:
        import json
        path = _state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w', encoding='utf-8') as handle:
            json.dump({'t': int(time.time()), 'ok': bool(ok), 'how': how}, handle)
        os.replace(path + '.tmp', path)
    except Exception as exc:
        _log('not saved: %s' % exc, xbmc.LOGWARNING)


def due():
    """True when the engine is missing and a try is allowed now."""
    if ready() or xbmc.getCondVisibility('System.Platform.TVOS'):
        return False
    import xbmcgui
    if xbmcgui.Window(10000).getProperty(BUSY):
        return False
    # once a day at most: a failed try, or an engine the user took off
    last = _state()
    return not last or time.time() - float(last.get('t') or 0) > RETRY_AFTER


def ensure_async():
    """From a Dex Hub page (never inside a plugin call Kodi waits on): the install runs beside
    the page, which goes on with the official add-on meanwhile."""
    try:
        if not due():
            return False
    except Exception:
        return False
    thread = threading.Thread(target=ensure, name='DexHub-youtube-engine')
    thread.start()
    return True


def ensure(monitor=None):
    import xbmcgui
    home = xbmcgui.Window(10000)
    if ready():
        return True
    home.setProperty(BUSY, '1')
    monitor = monitor or xbmc.Monitor()
    try:
        how = ''
        if _from_repo(monitor):
            how = 'repository'
        elif _from_dexworld(monitor):
            how = 'dexworld'
        ok = bool(how) and _switch_on(monitor)
        _remember(ok, how)
        if ok:
            _log('installed (%s)' % how)
        else:
            _log('not installed: YouTube stays on plugin.video.youtube', xbmc.LOGWARNING)
        return ok
    except Exception as exc:
        _log('failed: %s' % exc, xbmc.LOGWARNING)
        _remember(False, 'error')
        return False
    finally:
        home.clearProperty(BUSY)


def _installed(addon_id):
    """Installed, on or off (System.HasAddon answers only for an add-on that is on)."""
    if xbmc.getCondVisibility('System.HasAddon(%s)' % addon_id):
        return True
    from .skinui import switch
    return bool((switch._rpc('Addons.GetAddonDetails', {'addonid': addon_id}) or {}).get('result'))


def _install_addon(addon_id, monitor, wait=240):
    """Kodi installs *addon_id* from a repository it knows (its question answered yes); False when
    no repository has it (Kodi then says nothing)."""
    from .skinui import switch
    answers = switch._Answers()
    answers.start()
    try:
        xbmc.executebuiltin('InstallAddon(%s)' % addon_id)
        started, seen = time.time(), False
        while time.time() - started < wait:
            if _installed(addon_id):
                return True
            if answers.clicks or xbmc.getCondVisibility('Window.IsActive(progressdialog) | '
                                                        'Window.IsActive(extendedprogressdialog)'):
                seen = True
            if not seen and time.time() - started > 6:
                return False
            if monitor.waitForAbort(0.25):
                return False
    finally:
        answers.stop()
    return _installed(addon_id)


def _from_repo(monitor):
    for attempt in (1, 2):
        if attempt == 2:
            xbmc.executebuiltin('UpdateAddonRepos')      # the lists may be older than the engine
            if monitor.waitForAbort(8):
                return False
        if _install_addon(ENGINE, monitor):
            return True
    return False


def _from_dexworld(monitor):
    """The engine's zip from DexWorld's repository, checked, put in Kodi's add-ons folder; then
    what it needs from Kodi's repositories."""
    import urllib.request
    import xml.etree.ElementTree as ET
    import xbmcvfs
    from .skinui import switch
    home = xbmcvfs.translatePath('special://home/addons/')
    for base in DEXWORLD_REPOS:
        work = tempfile.mkdtemp(prefix='dexhub_yt_', dir=xbmcvfs.translatePath('special://temp/'))
        try:
            def fetch(url, target=None):
                req = urllib.request.Request(url, headers={'User-Agent': 'DexHub'})
                with urllib.request.urlopen(req, timeout=30) as answer:
                    if target is None:
                        return answer.read()
                    with open(target, 'wb') as handle:
                        shutil.copyfileobj(answer, handle, 1 << 20)
            index = ET.fromstring(fetch(base + 'addons.xml'))
            version = next((a.get('version') for a in index.findall('addon') if a.get('id') == ENGINE), '')
            if not version:
                continue
            path = os.path.join(work, 'engine.zip')
            fetch('%s%s/%s-%s.zip' % (base, ENGINE, ENGINE, version), path)
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if (not names or any(not n.startswith(ENGINE + '/') or '..' in n.split('/') for n in names)
                        or ET.fromstring(archive.read(ENGINE + '/addon.xml')).get('id') != ENGINE):
                    raise ValueError('not the engine')
                archive.extractall(os.path.join(work, 'x'))
            dest = os.path.join(home, ENGINE)
            if os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)
            switch._merge(os.path.join(work, 'x', ENGINE), dest)
        except Exception as exc:
            _log('from %s: %s' % (base, exc), xbmc.LOGWARNING)
            continue
        finally:
            shutil.rmtree(work, ignore_errors=True)
        for need in NEEDS:
            if not _installed(need) and not _install_addon(need, monitor):
                _log('%s is missing and no repository has it' % need, xbmc.LOGWARNING)
        xbmc.executebuiltin('UpdateLocalAddons')
        for _ in range(60):
            if _installed(ENGINE):
                _log('%s %s from %s' % (ENGINE, version, base))
                return True
            if monitor.waitForAbort(0.25):
                return False
    return _installed(ENGINE)


def _switch_on(monitor):
    from .skinui import switch
    return switch.enable(ENGINE, monitor)
