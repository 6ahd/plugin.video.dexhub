# -*- coding: utf-8 -*-
"""v5.10.28 first-run welcome screen.

Shown once per identity release (keyed by WELCOME_KEY, not the addon
version, so ordinary bug-fix releases never nag). Everything here is
best-effort: any failure falls back to nothing, never to a broken Home.
"""
import xbmcaddon
import xbmcgui

from .i18n import tr

WELCOME_KEY = 'identity-2026'
_SKIN_FILE = 'welcome.xml'


def _addon():
    try:
        from .settings_cache import cached_addon
        return cached_addon()
    except Exception:
        return xbmcaddon.Addon()


class _WelcomeWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(_WelcomeWindow, self).__init__(*args)
        self._version = str(kwargs.get('version') or '')

    def onInit(self):
        try:
            from . import skin_theme
            skin_theme.publish_theme(window=self)
        except Exception:
            pass
        self.setProperty('title', tr('مرحباً بك في Dex Hub'))
        self.setProperty('line1', tr('هوية جديدة، نفس التجربة التي تعرفها'))
        self.setProperty('line2', tr('بحث أسرع، تشغيل أسرع، وترجمات Silo تعمل من أول مرة'))
        self.setProperty('line3', tr('يمكنك تغيير الثيم في أي وقت من الإعدادات'))
        self.setProperty('button_label', tr('ابدأ'))
        self.setProperty('version', 'Dex Hub %s' % self._version)
        try:
            self.setFocusId(9000)
        except Exception:
            pass

    def onClick(self, control_id):
        if control_id == 9000:
            self.close()

    def onAction(self, action):
        try:
            if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
                self.close()
        except Exception:
            self.close()


def should_show():
    try:
        seen = (_addon().getSetting('welcome_seen') or '').strip()
    except Exception:
        return False
    return seen != WELCOME_KEY


