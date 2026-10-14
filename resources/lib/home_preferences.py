"""Profile-scoped Home presentation, independent of the trailer service."""
# v5.10.286: cinema (full screen) and netflix (hero and one row) join; the
# first stays the fallback default
MODES = ('cinematic', 'two_rows', 'spotlight_posters', 'cinema', 'netflix', 'shelf')
NAV_POSITIONS = ('top', 'side')


def publish():
    import xbmc
    import xbmcaddon
    import xbmcgui
    from . import profile_scope
    if not profile_scope.is_current():
        return
    addon = xbmcaddon.Addon('plugin.video.dexhub')
    home = xbmcgui.Window(10000)
    scale = poster_scale(addon.getSetting('homeui_poster_scale'))
    # Old skin zoom must not stack on top of the percentage geometry. Preserve
    # the legacy choice once, in the active profile's numeric preference.
    legacy = xbmc.getInfoLabel('Skin.String(DexHub.CardSize)')
    if legacy in ('large', 'xlarge'):
        if scale == 100:
            scale = 120 if legacy == 'large' else 130
            from .trailer_preferences import write_setting as save
            save('homeui_poster_scale', str(scale))
        xbmc.executebuiltin('Skin.Reset(DexHub.CardSize)')
    # Two complete rows must fit; keep the user's larger preference for the
    # cinematic/Spotlight layouts and restore it when they switch back.
    if addon.getSetting('homeui_layout_mode') == 'two_rows':
        scale = min(scale, 100)
    home.setProperty('dhs.home.poster_scale', str(scale))
    for key, prop, choices in (
        ('homeui_layout_mode', 'dhs.home.layout', MODES),
        ('homeui_nav_position', 'dhs.home.nav', NAV_POSITIONS),
    ):
        value = addon.getSetting(key)
        home.setProperty(prop, value if value in choices else choices[0])


def write_setting(key, value):
    from .trailer_preferences import write_setting as save
    choices = {'homeui_layout_mode': MODES, 'homeui_nav_position': NAV_POSITIONS}
    if key not in choices or value not in choices[key]:
        raise ValueError('Invalid Home presentation')
    save(key, value)
    publish()
    if key == 'homeui_layout_mode':
        # v5.10.286: the rows are published again (the Wide card shelf opens on big cards)
        try:
            from .homeui.layout import _skin_follows
            _skin_follows()
        except Exception:
            pass


def poster_scale(value):
    try:
        return max(50, min(200, int(float(value))))
    except (TypeError, ValueError, OverflowError):
        return 100


# v5.10.288: the named sizes of the Home size picker (skinui.actions.skin_size);
# any other percentage is a custom size and shows as the bare number
SCALE_NAMES = ((100, 'عادي'), (120, 'كبير'), (130, 'كبير جداً'), (150, 'ضخم'),
               (175, 'ضخم جداً'), (200, 'أقصى حجم'))


def current_scale(addon=None, setting=None):
    """v5.10.288: the Home size in force, in percent: homeui_poster_scale, or
    skin.dexhub 3.6's old Skin.String(DexHub.CardSize) while it is still set
    and the setting is at its default (publish() moves it over once)."""
    import xbmc
    if setting is None:
        try:
            if addon is None:
                import xbmcaddon
                addon = xbmcaddon.Addon('plugin.video.dexhub')
            setting = addon.getSetting('homeui_poster_scale')
        except Exception:
            setting = ''
    scale = poster_scale(setting)
    if scale == 100:
        try:
            legacy = xbmc.getInfoLabel('Skin.String(DexHub.CardSize)') or ''
        except Exception:
            legacy = ''
        scale = {'large': 120, 'xlarge': 130}.get(legacy, scale)
    return scale


def scale_label(tr, value):
    """v5.10.288: the Home size as the Menu shows it: '100% · Normal',
    '120% · Large' or '85%' for a custom size (tr turns the name to English)."""
    scale = poster_scale(value)
    name = dict(SCALE_NAMES).get(scale)
    if name:
        return u'%d%% \u00b7 %s' % (scale, tr(name))
    return u'%d%%' % scale


# v5.10.288: homeui_size (Dex Hub's own windows outside skin.dexhub) in percent
UI_SIZE_PERCENT = {'normal': 100, 'large': 120, 'extra large': 130, 'xlarge': 130}


def size_label(tr, addon=None):
    """v5.10.288: the Home size entry's value wherever it is listed (the
    Menu, Home options): skin.dexhub sizes its Home by homeui_poster_scale
    (50-200 %); another skin opens Dex Hub's windows at homeui_size."""
    import xbmc
    if xbmc.getSkinDir() == 'skin.dexhub':
        return scale_label(tr, current_scale(addon))
    try:
        if addon is None:
            import xbmcaddon
            addon = xbmcaddon.Addon('plugin.video.dexhub')
        current = (addon.getSetting('homeui_size') or 'Large').strip().lower()
    except Exception:
        current = 'large'
    return scale_label(tr, UI_SIZE_PERCENT.get(current, 120))


def set_poster_scale(value):
    import xbmc
    from .trailer_preferences import write_setting as save
    value = str(poster_scale(value))
    save('homeui_poster_scale', value)
    xbmc.executebuiltin('Skin.Reset(DexHub.CardSize)')
    publish()
    return value


def choose_poster_scale(tr):
    import xbmcaddon
    import xbmcgui
    # v5.10.288: the size in force (the old skin size too), as the Menu shows it
    current = str(current_scale(xbmcaddon.Addon('plugin.video.dexhub')))
    value = xbmcgui.Dialog().numeric(0, tr('حجم البوستر المخصص') + ' (50–200%)', current)
    if not value:
        return False
    try:
        number = int(value)
    except (TypeError, ValueError):
        return False
    if not 50 <= number <= 200:
        xbmcgui.Dialog().ok('Dex Hub', tr('اختر نسبة بين 50 و200'))
        return False
    set_poster_scale(number)
    return True


def apply_pending_scale():
    """Kodi loads numeric skin constants once. Reload only when the Home is idle."""
    import xbmc
    import xbmcgui
    from . import profile_scope
    home = xbmcgui.Window(10000)
    desired = home.getProperty('dhs.home.poster_scale') or '100'
    loaded = home.getProperty('dhs.scale.loaded')
    if not loaded or desired == loaded or not profile_scope.is_current():
        return False
    if (xbmc.getSkinDir() != 'skin.dexhub' or
            not xbmc.getCondVisibility('Window.IsActive(home)') or
            xbmc.getCondVisibility('Player.HasVideo | System.HasActiveModalDialog')):
        return False
    import time
    try:
        last = float(home.getProperty('dhs.scale.reload_at') or 0)
    except ValueError:
        last = 0
    if time.time() - last < 5:
        return False
    home.setProperty('dhs.scale.reload_at', str(time.time()))
    xbmc.executebuiltin('ReloadSkin()')
    return True
