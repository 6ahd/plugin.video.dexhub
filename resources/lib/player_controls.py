"""Element-based player controls shared by the TV menus and phone settings."""
import xbmcgui

GROUPS = {
    'loading': ('بدء التشغيل', 'Playback start', ('player_loading_art',)),
    'logo': ('شعار العمل · Clear logo', 'Clear logo',
             ('player_show_clearlogo', 'player_logo_size', 'player_logo_position')),
    'studio': ('شعار الاستديو', 'Studio logo',
               ('player_show_studio', 'player_studio_size', 'player_studio_position')),
    'plot': ('الوصف · Plot', 'Plot',
             ('player_show_plot', 'player_plot_size', 'player_plot_width', 'player_plot_scroll')),
    'badges': ('شارات الجودة · Badges', 'Quality badges',
               ('player_badges_visible', 'player_badges_size', 'player_badges_place',
                'player_badges_opacity', 'player_badges_brightness')),
    'info': ('البيانات والتقييمات', 'Metadata and ratings',
             ('player_show_metadata', 'player_show_ratings')),
}


def text(ar, en):
    from .i18n import is_english
    return en if is_english() else ar


def label(value):
    labels = {'true': ('مفعّل', 'On'), 'false': ('مطفأ', 'Off'),
              'normal': ('متوسط', 'Medium'), 'small': ('صغير', 'Small'),
              'large': ('كبير', 'Large'), 'short': ('مختصر', 'Compact'),
              'wide': ('عريض', 'Wide'), 'auto': ('تلقائي', 'Auto'),
              'original': ('أصلي', 'Original'), 'solid': ('واضح', 'Solid'),
              'soft': ('هادئ', 'Soft'), 'above_info': ('فوق البيانات', 'Above details'),
              'top_left': ('أعلى اليسار', 'Top left'), 'top_right': ('أعلى اليمين', 'Top right'),
              'bottom_left': ('أسفل اليسار', 'Bottom left'), 'bottom_right': ('أسفل اليمين', 'Bottom right'),
              'seekbar': ('فوق شريط التقدم', 'Above seek bar'), 'info': ('تحت البيانات', 'Under details')}
    return text(*labels[value]) if value in labels else value


def show(group):
    from . import ui_preferences as prefs
    ar, en, keys = GROUPS[group]
    selected = 0
    while True:
        items = [xbmcgui.ListItem(label=text(*prefs.OPTIONS[k][2:]),
                                  label2=label(prefs.value(k)) + ('%' if k.endswith('opacity') else ''),
                                  offscreen=True) for k in keys]
        selected = xbmcgui.Dialog().select(text(ar, en), items, useDetails=True, preselect=selected)
        if selected < 0:
            return
        prefs.edit(keys[selected], text)
