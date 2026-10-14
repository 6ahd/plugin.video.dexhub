# -*- coding: utf-8 -*-
"""The skin's own labels in Dex Hub's language.

skin.dexhub draws its labels (Play, Sources, Seasons, the tab names...) from
dhs.s.<id> Home window properties, and falls back to Kodi's language until
they are set. They are read here from the skin's strings.po for Dex Hub's
ui_language, so a Kodi in English and a Dex Hub in Arabic show one language.
"""
import os
import re

import xbmc

from . import common as C

_FIRST, _LAST = 31800, 32150        # shared native settings labels
_LANGS = {'arabic': 'resource.language.ar_sa', 'english': 'resource.language.en_gb'}
_CTX = re.compile(r'^msgctxt\s+"#(\d+)"')
_FIELD = re.compile(r'^(msgid|msgstr)\s+"(.*)"\s*$')


def _language():
    try:
        from ..settings_cache import cached_addon
        value = cached_addon().getSetting('ui_language') or 'English'
    except Exception:
        value = 'English'
    return 'arabic' if value.strip().lower().startswith('ar') else 'english'


def _unescape(text):
    return text.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')


def read_po(path):
    """{id: text} of the Dex Hub strings in a strings.po (msgstr, or msgid
    where msgstr is empty, as in en_gb)."""
    out = {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            lines = handle.read().splitlines()
    except Exception:
        return out
    current, fields = None, {}
    for line in lines + ['']:
        line = line.strip()
        match = _CTX.match(line)
        if match or not line:
            if current is not None:
                text = fields.get('msgstr') or fields.get('msgid') or ''
                if text:
                    out[current] = _unescape(text)
            current, fields = None, {}
            if match:
                number = int(match.group(1))
                current = number if _FIRST <= number <= _LAST else None
            continue
        if current is None:
            continue
        match = _FIELD.match(line)
        if match:
            fields[match.group(1)] = match.group(2)
    return out


def publish_options():
    """The add-on's Home settings the skin follows (dhs.opt.*): the moving
    artwork, the spotlight moving on by itself, the animated collection
    covers."""
    from ..trailer_preferences import publish_presentation
    publish_presentation()
    from ..home_preferences import publish as publish_home
    publish_home()
    def flag(addon, key):
        try:
            return (addon.getSetting(key) or 'true').lower() != 'false'
        except Exception:
            return True
    try:
        from ..settings_cache import cached_addon
        addon = cached_addon()
    except Exception:
        addon = None
    for prop, key in (('dhs.opt.kenburns', 'homeui_kenburns'), ('dhs.opt.carousel', 'homeui_carousel'),
                      ('dhs.opt.gif', 'homeui_focus_gif'), ('dhs.opt.plot_scroll', 'homeui_plot_scroll')):
        enabled = addon is None or flag(addon, key)
        C.set_prop(prop, '1' if enabled else '')


# the hub names a first run gives (skin.dexhub 2): a name the user typed stays
_HUBS = (('Home', 31800), ('1101', 31801), ('1102', 31802))
_DEFAULT_NAMES = set(['', 'Home', 'Custom', 'Movies', 'Series', 'TV Shows', 'الرئيسية', 'أفلام', 'مسلسلات',
                      'مخصص', 'الأفلام', 'المسلسلات'])


def _name_hubs(strings):
    if not C.layout().hubs:
        return
    for window, sid in _HUBS:
        text = strings.get(sid)
        if not text or not xbmc.getCondVisibility('Skin.HasSetting(DexHub.Hub.%s)' % window):
            continue
        current = xbmc.getInfoLabel('Skin.String(HomeSwitcher.%s.Name)' % window)
        if current != text and (current in _DEFAULT_NAMES or current == xbmc.getLocalizedString(sid)):
            xbmc.executebuiltin('Skin.SetString(HomeSwitcher.%s.Name,%s)' % (window, text))


def publish(force=False):
    """Set dhs.s.<id> for Dex Hub's language (once per language)."""
    publish_options()
    if not C.skin_active():
        return _publish_pages(force)
    language = _language()
    # v5.10.116: a new skin version brings new labels (Kodi keeps running
    # through a skin update): they are read again for it
    try:
        import xbmcaddon
        version = xbmcaddon.Addon(xbmc.getSkinDir()).getAddonInfo('version')
    except Exception:
        version = ''
    key = '%s|%s|iptv-v1' % (language, version)
    if not force and C.prop('dhs.s.lang') == key:
        return False
    try:
        import xbmcvfs
        skin = xbmcvfs.translatePath('special://skin/')
    except Exception:
        return False
    strings = read_po(os.path.join(skin, 'language', _LANGS[language], 'strings.po'))
    if not strings:
        C.log('no %s strings in the skin' % language, xbmc.LOGWARNING)
        return False
    strings[31804] = 'IPTV'
    for number, text in strings.items():
        C.set_prop('dhs.s.%d' % number, text)
    C.set_prop('dhs.s.lang', key)
    try:
        _name_hubs(strings)
    except Exception as exc:
        C.log('hub names: %s' % exc)
    return True


def _publish_pages(force=False):
    """Arctic Fuse 3 with Dex Hub's pages in (af3pages.py, v5.10.119): their
    labels come with the add-on (the skin has no such strings)."""
    try:
        from . import af3pages
        if not af3pages.installed():
            return False
        language = _language()
        key = 'af3|%s|%d|iptv-v1' % (language, af3pages.version())
        if not force and C.prop('dhs.s.lang') == key:
            return False
        index = 1 if language == 'arabic' else 0
        table = af3pages.strings()
        for number, texts in table.items():
            C.set_prop('dhs.s.%d' % number, 'IPTV' if number == 31804 else (texts[index] or texts[0]))
        C.set_prop('dhs.s.lang', key)
        return bool(table)
    except Exception as exc:
        C.log('pages labels: %s' % exc, xbmc.LOGWARNING)
        return False
