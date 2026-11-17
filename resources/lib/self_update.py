# -*- coding: utf-8 -*-
"""Updates for the Dex Hub Android app (v5.10.542).

The app carries Dex Hub and its skin as Kodi's own add-ons, and Kodi looks for
updates of those in its official repositories only (AddonRepos.cpp,
DoAddonUpdateCheck, ORIGIN_SYSTEM), so repository.dexhub never updated them.
Here the add-on reads that repository itself and puts a newer version in
special://home/addons, which Kodi prefers to the bundled copy. It takes effect
the next time the app opens, so nothing changes under a running page or video.
"""
import os
import re
import shutil
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .android_files import PACKAGE, package

REPOSITORY = 'https://dexworld.cc/dexhub/'
ADDONS = ('plugin.video.dexhub', 'skin.dexhub')
FIRST_WAIT = 120
EVERY = 6 * 3600
BUSY_WAIT = 600
TIMEOUT = 30


def _log(message, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] self update: %s' % message, level)


def _version(text):
    return tuple(int(part) for part in re.findall(r'\d+', str(text or '')))


def _repository():
    """A test repository from a file in the add-on's data, else the real one."""
    path = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/self_update_repo.txt')
    try:
        with open(path, encoding='utf-8') as handle:
            url = handle.read().strip()
        if url.startswith('http'):
            return url.rstrip('/') + '/'
    except OSError:
        pass
    return REPOSITORY


def _fetch(url, target=None):
    request = urllib.request.Request(url, headers={'User-Agent': 'DexHub-Android'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
        if target is None:
            return answer.read()
        with open(target, 'wb') as handle:
            shutil.copyfileobj(answer, handle, 1 << 20)
    return None


def _available(data):
    found = {}
    for addon in ET.fromstring(data).findall('addon'):
        if addon.get('id') in ADDONS:
            found[addon.get('id')] = addon.get('version') or ''
    return found


def _installed(identifier):
    try:
        return xbmcaddon.Addon(identifier).getAddonInfo('version')
    except Exception:
        return ''


def _checked(path, identifier, version):
    """The zip holds this add-on at this version, and nothing outside its folder."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        top = identifier + '/'
        if not names or any(not name.startswith(top) or '..' in name.split('/') for name in names):
            raise ValueError('unexpected files in the zip')
        descriptor = ET.fromstring(archive.read(top + 'addon.xml'))
    if descriptor.get('id') != identifier or descriptor.get('version') != version:
        raise ValueError('the zip holds %s %s' % (descriptor.get('id'), descriptor.get('version')))


def _install(base, identifier, version):
    home = xbmcvfs.translatePath('special://home/addons')
    stage = os.path.join(home, 'packages', 'dexhub-update')
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    try:
        path = os.path.join(stage, '%s-%s.zip' % (identifier, version))
        _fetch('%s%s/%s-%s.zip' % (base, identifier, identifier, version), path)
        _checked(path, identifier, version)
        with zipfile.ZipFile(path) as archive:
            archive.extractall(stage)
        target = os.path.join(home, identifier)
        old = os.path.join(stage, 'old')
        if os.path.isdir(target):
            os.rename(target, old)
        os.rename(os.path.join(stage, identifier), target)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def check():
    """Installs what is newer in the repository; (identifier, version) of each."""
    base = _repository()
    done = []
    for identifier, version in _available(_fetch(base + 'addons.xml')).items():
        current = _installed(identifier)
        if not current or _version(version) <= _version(current):
            continue
        _install(base, identifier, version)
        _log('%s %s -> %s' % (identifier, current, version))
        done.append((identifier, version))
    return done


def _told(done):
    try:
        from .android_files import _t
        message = _t('التحديث يطبق عند فتح التطبيق المرة الجاية', 'It applies the next time the app opens')
        xbmcgui.Dialog().notification('Dex Hub %s' % done[0][1], message, xbmcgui.NOTIFICATION_INFO, 6000)
    except Exception as exc:
        _log('notice: %s' % exc, xbmc.LOGDEBUG)


def run(monitor):
    if not xbmc.getCondVisibility('System.Platform.Android') or package() != PACKAGE:
        return
    wait = FIRST_WAIT
    while not monitor.waitForAbort(wait):
        if xbmc.getCondVisibility('Player.HasMedia'):
            wait = BUSY_WAIT
            continue
        wait = EVERY
        try:
            done = check()
        except Exception as exc:
            _log('failed: %r' % exc, xbmc.LOGWARNING)
            continue
        if done:
            xbmc.executebuiltin('UpdateLocalAddons')
            _told(done)
