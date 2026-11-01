# -*- coding: utf-8 -*-
"""Which alerts the bell gives (v5.10.473), one switch per kind.

The notices come from several places (island.notices): a followed team's
game, Silo's new-episode notices, new episodes from the watch sources, what
was stopped half way, a watch party; the banner is the pop-up that drops
under the bell when one comes. Each kind has a setting alerts_<kind>
(true by default) and a switch in the inbox (the bell's page). The service
reads them every second, so they are kept a few seconds in memory.
"""
import time

KINDS = (
    ('sport', 'مباريات فرقك', 'Your teams\' games', 'مباشر ونتيجة نهاية المباراة', 'Live and full-time scores'),
    ('silo', 'تنبيهات Silo', 'Silo notices', 'حلقات جديدة يرصدها Silo', 'New episodes Silo spots'),
    ('newep', 'حلقات جديدة', 'New episodes', 'من مسلسلاتك اللي تتابعها', 'Of the shows you follow'),
    ('resume', 'كمّل اللي وقفته', 'Pick up where you stopped', 'بعد ما توقف عمل بنصه', 'After you stop a title half way'),
    ('party', 'المشاهدة الجماعية', 'Watch party', 'دعوات ومشاهدات جماعية', 'Invites and parties'),
    ('banner', 'البانر المنبثق', 'Pop-up banner', 'ينزل تحت الجرس لما يجي تنبيه جديد', 'Drops under the bell when one comes'),
)
KEYS = tuple(kind for kind, *_rest in KINDS)
# the notice kinds each switch covers
COVERS = {'sport': ('sport',), 'silo': ('silo',), 'newep': ('newep', 'soon'), 'resume': ('resume',), 'party': ('party',)}
_MEMO = {'at': 0.0, 'values': {}}
TTL = 5.0


def _addon():
    from .settings_cache import cached_addon
    return cached_addon()


def _english():
    try:
        from .i18n import is_english
        return bool(is_english())
    except Exception:
        return False


def values():
    now = time.monotonic()
    if now - _MEMO['at'] > TTL or not _MEMO['values']:
        addon = _addon()
        _MEMO['values'] = dict((key, (addon.getSetting('alerts_%s' % key) or 'true').lower() != 'false') for key in KEYS)
        _MEMO['at'] = now
    return _MEMO['values']


def on(key):
    return values().get(key, True)


def notice_on(kind):
    """Is a notice of this kind (island.notices' 'kind') wanted?"""
    for key, kinds in COVERS.items():
        if kind in kinds:
            return on(key)
    return True


def toggle(key):
    value = not on(key)
    _addon().setSetting('alerts_%s' % key, 'true' if value else 'false')
    _MEMO['at'] = 0.0
    return value


def rows():
    """[(key, title, line, on)] in the interface's language."""
    english = _english()
    return [(key, en if english else ar, en_line if english else ar_line, on(key))
            for key, ar, en, ar_line, en_line in KINDS]
