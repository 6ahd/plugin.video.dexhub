# -*- coding: utf-8 -*-
"""v5.10.379: where each tab's spotlight takes its titles from (skin.dexhub).

Ahmed: a Spotlight section whose titles I choose, as in Arctic Fuse 3 (there
each hub's spotlight is a widget path the user picks, with a label and a
limit). Here a tab's spotlight was always the first catalog row with three
titles that have a backdrop (board.update_spot). Now, per tab:

    auto   the first such catalog row (as before)
    off    no spotlight: the Home's hero follows the rows only
    <id>   that row of the tab, whatever it is: a catalog, Your list, a
           watchlist, Continue watching (titles with a backdrop)

and how many titles (homeui_spot_count: 5 to 20, 8 before). Kept in
homeui_spot_source as JSON {tab: choice} (a hidden text setting: DexCloud's
profile carries it like the other Home settings).
"""
import json

import xbmc
import xbmcgui

from . import common as C

SETTING = 'homeui_spot_source'
COUNT = 'homeui_spot_count'
COUNTS = (5, 8, 10, 12, 15, 20)
AUTO, OFF = 'auto', 'off'
SPOT_TABS = ('all', 'movie', 'series')      # the Library and Servers have no spotlight
TAB_NAMES = {'all': ('الرئيسية', 'Home'), 'movie': ('الأفلام', 'Movies'), 'series': ('المسلسلات', 'Series')}


def _addon():
    from ..settings_cache import cached_addon
    return cached_addon()


def _all():
    try:
        data = json.loads(_addon().getSetting(SETTING) or '{}')
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def choice(tab):
    value = str(_all().get(tab) or AUTO)
    return value or AUTO


def count():
    try:
        value = int(float(_addon().getSetting(COUNT) or 8))
    except ValueError:
        value = 8
    return max(COUNTS[0], min(COUNTS[-1], value))


def save(tab, value):
    data = _all()
    if value in ('', AUTO):
        data.pop(tab, None)
    else:
        data[tab] = value
    _addon().setSetting(SETTING, json.dumps(data, sort_keys=True) if data else '')


def _tab_rows(tab):
    """(id, title) of the rows on show in the tab, in order."""
    out = []
    for n in range(1, C.ROWS + 1):
        row_id = C.prop('dhs.%s.%d.id' % (tab, n))
        if not row_id:
            break
        title = C.prop('dhs.%s.%d.title' % (tab, n)) or row_id
        out.append((row_id, title))
    return out


def _tab():
    tab = C.prop('dhs.tab') or 'all'
    return tab if tab in SPOT_TABS else 'all'


def tab_label(tab, tr):
    names = TAB_NAMES.get(tab)
    return tr(names[0]) if names else tab


def summary(tr):
    """The control centre's value for the entry: this tab's choice."""
    tab = _tab()
    value = choice(tab)
    if value == OFF:
        return tr('بدون')
    if value == AUTO:
        return tr('تلقائي')
    for row_id, title in _tab_rows(tab):
        if row_id == value:
            return title[:40]
    return tr('تلقائي')


def pick(tr):
    """Choose the current tab's spotlight. True when it changed."""
    from ..homeui.options import choose
    tab = _tab()
    rows = _tab_rows(tab)
    now = choice(tab)
    labels = [(tr('تلقائي'), tr('أول كتالوج فيه أعمال بخلفيات')),
              (tr('بدون سبوت لايت'), tr('الهيرو يتبع الصفوف بس'))]
    values = [AUTO, OFF]
    for row_id, title in rows:
        labels.append((title, tr('صف في %s') % tab_label(tab, tr)))
        values.append(row_id)
    preselect = values.index(now) if now in values else 0
    picked = choose(tr('أعمال السبوت لايت: %s') % tab_label(tab, tr), labels, preselect=preselect,
                    subtitle=tr('اختر الصف اللي تجي منه أعمال السبوت لايت في هذا القسم'))
    if picked < 0 or values[picked] == now:
        return False
    save(tab, values[picked])
    xbmc.log('[DexHub] spotlight of %s: %s' % (tab, values[picked]), xbmc.LOGINFO)
    return True


def pick_count(tr):
    from ..homeui.options import choose
    now = count()
    labels = [tr('%d أعمال') % n for n in COUNTS]
    picked = choose(tr('عدد أعمال السبوت لايت'), labels,
                    preselect=COUNTS.index(now) if now in COUNTS else 1)
    if picked < 0 or COUNTS[picked] == now:
        return False
    _addon().setSetting(COUNT, str(COUNTS[picked]))
    return True
