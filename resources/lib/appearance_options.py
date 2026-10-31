# -*- coding: utf-8 -*-
"""Glass and focus controls in Dex Hub's simple menu."""

def open_settings():
    import xbmc
    import xbmcgui
    from .settings_cache import cached_addon
    addon = cached_addon()
    arabic = str(addon.getSetting('ui_language') or '').lower().startswith('ar')
    def text(ar, en):
        return ar if arabic else en
    if xbmc.getSkinDir() != 'skin.dexhub':
        xbmcgui.Dialog().ok('Dex Hub', text(
            'ثبّت وفعّل سكين Dex Hub المرفق أولًا، ثم افتح الزجاج والتحديد.',
            'Install and activate the supplied Dex Hub skin first, then open Glass & focus.'))
        return False
    from .routes.simple_entry import _select_dialog, _row, _close_native_settings_dialog
    from .ui_preferences import value, set_value
    _close_native_settings_dialog()
    glass = not xbmc.getCondVisibility('Skin.HasSetting(dh.noglass)')
    colour = 'accent' if xbmc.getCondVisibility('String.IsEqual(Skin.String(dh.kodifocus),accent)') else 'white'
    motion = value('homeui_focus_motion')
    calm = value('homeui_calm_motion') == 'true'      # v5.10.451
    motions = ('tvos', 'zoom', 'lift', 'pop', 'glow')
    motion_names = ('Apple TV', text('تكبير', 'Zoom'), text('ارتفاع', 'Lift'), text('نبضة', 'Pop'), text('توهّج', 'Glow'))
    cursor = 0
    while True:
        motion_index = motions.index(motion) if motion in motions else 0
        pick = _select_dialog(text('الزجاج والتحديد', 'Glass & focus'), [
            _row(text('الوضع الزجاجي', 'Glass mode'), text('مفعّل', 'On') if glass else text('مطفأ', 'Off')),
            _row(text('لون التحديد', 'Focus colour'), text('لون الثيم', 'Theme colour') if colour == 'accent' else text('أبيض', 'White')),
            _row(text('حركة التحديد', 'Focus motion'), motion_names[motion_index]),
            _row(text('حركة أقل', 'Reduce motion'), text('مفعّل', 'On') if calm else text('مطفأ', 'Off')),
            _row(text('إعادة ضبط الزجاج والتحديد', 'Reset glass & focus'), text('زجاج مفعّل، تحديد أبيض، حركة Apple TV', 'Glass on, white focus, Apple TV motion')),
        ], preselect=cursor)
        if pick < 0:
            return True
        cursor = pick
        if pick == 0:
            glass = not glass
            xbmc.executebuiltin('Skin.Reset(dh.noglass)' if glass else 'Skin.SetBool(dh.noglass)', wait=True)
        elif pick == 1:
            selected = _select_dialog(text('لون التحديد', 'Focus colour'), [
                _row(text('أبيض', 'White')), _row(text('لون الثيم', 'Theme colour')),
            ], preselect=1 if colour == 'accent' else 0)
            if selected in (0, 1):
                colour = ('white', 'accent')[selected]
                xbmc.executebuiltin('Skin.SetString(dh.kodifocus,%s)' % colour, wait=True)
        elif pick == 2:
            selected = _select_dialog(text('حركة التحديد', 'Focus motion'), [_row(name) for name in motion_names], preselect=motion_index)
            if selected in range(len(motions)) and set_value('homeui_focus_motion', motions[selected]):
                motion = motions[selected]
        elif pick == 3:
            if set_value('homeui_calm_motion', 'false' if calm else 'true'):
                calm = not calm
                from .skinui import strings as _skin_strings
                _skin_strings.publish_options()
        elif pick == 4:
            xbmc.executebuiltin('Skin.Reset(dh.noglass)', wait=True)
            xbmc.executebuiltin('Skin.SetString(dh.kodifocus,white)', wait=True)
            glass, colour = True, 'white'
            if set_value('homeui_focus_motion', 'tvos'):
                motion = 'tvos'
            if set_value('homeui_calm_motion', 'false'):
                calm = False
