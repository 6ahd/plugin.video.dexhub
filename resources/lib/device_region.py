# -*- coding: utf-8 -*-
"""The device's country (v5.10.497), one for every page that shows what is
popular "here" (YouTube, Music, later the screensaver).

In this order:
  1. the person's own choice (setting device_region, a two-letter code);
  2. the country YouTube sees this device in (youtube.com's page states it
     for the connection: no other site is asked), kept for 7 days;
  3. by the interface language: SA for Arabic, US for English.
"""
import json
import os
import re
import threading
import time

KEEP = 7 * 86400
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
_lock = threading.Lock()
_held = {}


def _path():
    try:
        from .profile_scope import root
        base = root()
    except Exception:
        base = os.path.join(os.path.expanduser('~'), '.kodi', 'userdata', 'addon_data', 'plugin.video.dexhub')
    return os.path.join(base, 'cache', 'device_region.json')


def _english():
    try:
        from .i18n import is_english
        return is_english()
    except Exception:
        return False


def chosen():
    try:
        import xbmcaddon
        value = (xbmcaddon.Addon('plugin.video.dexhub').getSetting('device_region') or '').strip().upper()
    except Exception:
        value = ''
    return value if re.match(r'^[A-Z]{2}$', value) else ''


def seen(fresh=False):
    """(country, client version) as youtube.com's page states them for this connection."""
    with _lock:
        if not fresh and _held.get('at') and time.time() - _held['at'] < KEEP:
            return _held.get('gl') or '', _held.get('version') or ''
        try:
            with open(_path(), encoding='utf-8') as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            stored = {}
        if not fresh and stored.get('gl') and time.time() - float(stored.get('at') or 0) < KEEP:
            _held.update(stored)
            return stored['gl'], stored.get('version') or ''
        gl, version = '', ''
        try:
            from urllib.request import Request, urlopen
            request = Request('https://www.youtube.com/', headers={'User-Agent': UA, 'Accept-Language': 'en'})
            with urlopen(request, timeout=8) as answer:
                page = answer.read(600000).decode('utf-8', 'replace')
            match = re.search(r'"INNERTUBE_CONTEXT_GL":"([A-Z]{2})"', page) or re.search(r'"GL":"([A-Z]{2})"', page)
            gl = match.group(1) if match else ''
            match = re.search(r'"INNERTUBE_CLIENT_VERSION":"([0-9.]+)"', page)
            version = match.group(1) if match else ''
        except Exception:
            pass
        if gl:
            stored = {'gl': gl, 'version': version, 'at': time.time()}
            _held.update(stored)
            try:
                os.makedirs(os.path.dirname(_path()), exist_ok=True)
                with open(_path(), 'w', encoding='utf-8') as handle:
                    json.dump(stored, handle)
            except OSError:
                pass
            return gl, version
        return stored.get('gl') or '', stored.get('version') or ''


def country():
    """The two-letter country every page uses."""
    return chosen() or seen()[0] or ('US' if _english() else 'SA')


def language():
    return 'en' if _english() else 'ar'
