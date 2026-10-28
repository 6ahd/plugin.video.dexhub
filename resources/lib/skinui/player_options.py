# -*- coding: utf-8 -*-
"""The player's options in skin.dexhub (v5.10.117, skin.dexhub 3.4.0).

skin.dexhub draws Arctic Fuse 3's player (the user's copy, with ABUKARIM's
CoreELEC tools). Its options stay skin settings there (skin port: live
settings), and this dialog changes them: the info line (codec logos, two
rows, the audio format), the seek bar's time, what pausing does, how long
the OSD stays, its panels and what Down opens. A first start of skin.dexhub
takes the values Arctic Fuse 3 had (seed_from_af3).
"""
import os
import re

import xbmc
import xbmcgui

SEEDED = 'DexHub.PlayerSeeded'

# (label, kind, data): kind 'place' (off/left/center/right over three
# settings), 'on' (a setting that turns it on), 'off' (one that turns it
# off), 'string' ((setting, [(value, label)])), 'seconds' (a number)
OPTIONS = (
    ('خلفية وشعار أثناء التحميل', 'presentation', 'loading'),
    ('شعار العمل · الحجم والمكان', 'presentation', 'logo'),
    ('شعار الاستديو · الحجم والمكان', 'presentation', 'studio'),
    ('الوصف · الخط والعرض والتمرير', 'presentation', 'plot'),
    ('شارات الجودة · الحجم والمكان والوضوح', 'presentation', 'badges'),
    ('بيانات العمل والتقييمات', 'presentation', 'info'),
    ('رموز المشغّل', 'badges', 'player_badge_style'),
    # v5.10.288: the bar's buttons, shown or hidden, in their order (skin 3.28.40 slots)
    ('أزرار المشغل', 'osd_buttons', ''),
    ('شعارات الكودك في سطر المعلومات', 'off', 'Furniture.DisableCodecLogos'),
    ('سطر المعلومات في صفين', 'on', 'InfoBar.TwoRow'),
    ('صيغة الصوت في سطر المعلومات', 'on', 'InfoTags.EnableAudioCodec'),
    ('وقت شريط التقدم', 'string', ('Seekbar.TimeDisplay', (('', 'المنقضي / المدة'), ('Remaining', 'المتبقي / المدة'),
                                                         ('Combined', 'المنقضي / المتبقي')))),
    ('عند الإيقاف المؤقت', 'string', ('OSD.AutoOnPause', (('', 'لا شيء'), ('Info', 'إظهار المعلومات'),
                                                       ('Hide', 'إخفاء شريط التقدم')))),
    ('مهلة الإيقاف المؤقت', 'string', ('OSD.AutoOnPause.Delay', (('', 'الافتراضي'), ('0s', '0 ث'), ('1s', '1 ث'),
                                                              ('2s', '2 ث'), ('3s', '3 ث')))),
    ('إخفاء قائمة المشغل بعد', 'seconds', 'OSD_Timeout'),
    # v5.10.140: 'ExtraOSD' left the list: skin.dexhub 3.18 shows the badges
    # whenever their style is not "off" (the style is the one switch)
    ('المعلومات فوق أزرار المشغل', 'on', 'OSD.DisplayInfoOnControls'),
    ('القصة والتقييمات في لوحة المعلومات', 'off', 'OSD.DisablePlotRatingsOnInfo'),
    ('القصة والتقييمات في المعلومات العلوية', 'off', 'OSD.InfoOverlay.DisablePlotRatingsOnInfo'),
    ('الصورة العريضة في المعلومات العلوية', 'off', 'OSD.InfoOverlay.DisableLandscapeOnInfo'),
    ('السهم للأسفل في المشغل: العلامات', 'off', 'OSD.OnDown.DisableVideoBookmarks'),
    ('السهم للأسفل في المشغل: قائمة التشغيل', 'off', 'OSD.OnDown.Disable1140'),
    ('السهم للأسفل في المشغل: دليل القنوات', 'off', 'OSD.OnDown.DisablePVROSDGuide'),
)
# options Kodi reads when the window loads (include conditions): the skin is
# reloaded once the dialog closes when one of them changed
RELOAD = set(['InfoBar.TwoRow', 'InfoTags.EnableAudioCodec'])
PLACES = ('بدون', 'يسار', 'وسط', 'يمين')


def _on(name):
    return bool(xbmc.getCondVisibility('Skin.HasSetting(%s)' % name))


def _string(name):
    return xbmc.getInfoLabel('Skin.String(%s)' % name) or ''


def _run(builtin):
    # wait: the list reads the value right after
    try:
        xbmc.executebuiltin(builtin, True)
    except TypeError:
        xbmc.executebuiltin(builtin)


def _set(name, value):
    _run(('Skin.SetBool(%s)' if value else 'Skin.Reset(%s)') % name)


def _value(kind, data, tr):
    if kind == 'presentation':
        from .. import player_controls, ui_preferences
        keys = player_controls.GROUPS[data][2]
        return ' · '.join(player_controls.label(ui_preferences.value(k)) for k in keys[:3])
    if kind == 'badges':
        from .. import player_badges
        return player_badges.style_label()
    if kind == 'osd_buttons':
        from .. import osd_buttons
        return osd_buttons.summary()
    if kind == 'place':
        for index, name in enumerate(data):
            if _on(name):
                return tr(PLACES[index + 1])
        return tr(PLACES[0])
    if kind in ('on', 'off'):
        on = _on(data) if kind == 'on' else not _on(data)
        return tr('مفعّل') if on else tr('مطفأ')
    if kind == 'string':
        name, choices = data
        current = _string(name)
        for value, label in choices:
            if value.lower() == current.lower():
                return tr(label)
        return current or tr(choices[0][1])
    if kind == 'seconds':
        current = _string(data)
        return (tr('%s ث') % current) if current else tr('الافتراضي')
    return ''


def _change(kind, data, tr, label):
    """Change one option; the settings it touched."""
    if kind == 'presentation':
        from .. import player_controls
        player_controls.show(data)
        return []
    if kind == 'osd_buttons':
        from .. import osd_buttons
        osd_buttons.edit()
        return []
    if kind == 'badges':
        # v5.10.148: the badge gallery, chosen in place and in force at once
        from ..homeui import wizard
        wizard.open_wizard(steps=('badges',))
        return []
    if kind == 'place':
        current = 0
        for index, name in enumerate(data):
            if _on(name):
                current = index + 1
        choice = xbmcgui.Dialog().select(tr(label), [tr(p) for p in PLACES], preselect=current)
        if choice < 0 or choice == current:
            return []
        for index, name in enumerate(data):
            _set(name, index + 1 == choice)
        return list(data)
    if kind in ('on', 'off'):
        _set(data, not _on(data))
        return [data]
    if kind == 'string':
        name, choices = data
        current = _string(name).lower()
        values = [value for value, _label in choices]
        index = next((i for i, v in enumerate(values) if v.lower() == current), 0)
        value = values[(index + 1) % len(values)]
        _run('Skin.SetString(%s,%s)' % (name, value) if value else 'Skin.Reset(%s)' % name)
        return [name]
    if kind == 'seconds':
        current = _string(data)
        typed = xbmcgui.Dialog().numeric(0, tr(label), current)
        if typed is None or typed == current:
            return []
        _run('Skin.SetString(%s,%s)' % (data, typed.strip()) if typed.strip() else 'Skin.Reset(%s)' % data)
        return [data]
    return []


def show(tr):
    """The options, each with its value; a click changes it."""
    changed = set()
    selected = 0
    while True:
        # v5.10.289: Dex Hub's own options panel (each option with its value
        # on the right, like the rest of Dex Hub's menus); Kodi's list when
        # the panel cannot open
        rows = [(tr(label), _value(kind, data, tr)) for label, kind, data in OPTIONS]
        try:
            from ..homeui.options import choose
            choice = choose(tr('خيارات المشغل'), rows, preselect=selected)
        except Exception:
            items = [xbmcgui.ListItem(label=l, label2=v, offscreen=True) for l, v in rows]
            choice = xbmcgui.Dialog().select(tr('خيارات المشغل'), items, preselect=selected, useDetails=True)
        if choice < 0:
            break
        selected = choice
        label, kind, data = OPTIONS[choice]
        changed.update(_change(kind, data, tr, label))
    if changed & RELOAD:
        xbmc.executebuiltin('ReloadSkin()')
    return bool(changed)


# ------------------------------------------------------------- the seed
_SETTING = re.compile(r'<setting id="([^"]+)" type="(bool|string)"[^>]*>([^<]*)</setting>')


def _names():
    out = {}
    for _label, kind, data in OPTIONS:
        if kind == 'place':
            for name in data:
                out[name.lower()] = (name, 'bool')
        elif kind in ('on', 'off'):
            out[data.lower()] = (data, 'bool')
        elif kind == 'string':
            out[data[0].lower()] = (data[0], 'string')
        elif kind == 'seconds':
            out[data.lower()] = (data, 'string')
    return out


def seed_from_af3():
    """skin.dexhub's first start: the player's options as Arctic Fuse 3 had
    them (once; nothing to do when AF3 never ran here)."""
    if _on(SEEDED):
        return False
    try:
        import xbmcvfs
        path = xbmcvfs.translatePath('special://profile/addon_data/skin.arctic.fuse.3/settings.xml')
        with open(path, 'r', encoding='utf-8') as handle:
            text = handle.read()
    except Exception:
        text = ''
    names = _names()
    applied = 0
    for sid, kind, value in _SETTING.findall(text):
        known = names.get(sid.lower())
        if not known:
            continue
        name, want = known
        value = (value or '').strip()
        if want == 'bool' and kind == 'bool' and value.lower() == 'true':
            xbmc.executebuiltin('Skin.SetBool(%s)' % name)
            applied += 1
        elif want == 'string' and kind == 'string' and value and ',' not in value and ')' not in value:
            xbmc.executebuiltin('Skin.SetString(%s,%s)' % (name, value))
            applied += 1
    xbmc.executebuiltin('Skin.SetBool(%s)' % SEEDED)
    if applied:
        xbmc.log('[DexHub] skin: %d player option(s) taken from Arctic Fuse 3' % applied, xbmc.LOGINFO)
    return bool(applied)
