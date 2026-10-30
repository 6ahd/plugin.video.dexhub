# -*- coding: utf-8 -*-
"""The skin's actions and grids (they use the Dex Hub router).

  skin_publish   the Home's rows, when the service has not published them
  skin_play / skin_sources / skin_trailer / skin_fav / skin_more
                 the title page's buttons (the page's item is read from the
                 open dialog: ListItem.* of Kodi's info dialog)
  skin_open      another title's page (from More like this)
  skin_menu      the Home's menu: layout, accounts, settings, refresh, the
                 skin used before (switch.py)
  skin_list      a Dex Hub listing as the skin's grid (Videos window, views
                 600/601): See all, collections, a server's shows, favourites
  skin_folder    a collection folder's catalogs, as cards
  skin_facet     Sort, Genre and Filter of a grid (homeui grid_filters)
"""
import json
import re
import threading
import time
from .. import lazy_tb as traceback
from urllib.parse import parse_qsl, urlencode, urlsplit

import xbmc
import xbmcgui

from . import common as C


def run(action, params, handle):
    fn = ROUTES.get(action)
    if fn is None:
        C.log('unknown action %s' % action, xbmc.LOGWARNING)
        if handle >= 0:
            _end(handle)
        return None
    try:
        return fn(params, handle)
    except Exception:
        C.log('%s failed:\n%s' % (action, traceback.format_exc()), xbmc.LOGERROR)
        if handle >= 0:
            _end(handle, ok=False)
        return None


def _end(handle, ok=True):
    from . import items as I
    box = I.captured()
    if box is not None:
        # worked out by the service for a plugin call (listing.py)
        box.update(done=True, failed=not ok, items=[], kw={})
        return
    try:
        import xbmcplugin
        xbmcplugin.endOfDirectory(handle, succeeded=ok, cacheToDisc=False)
    except Exception:
        pass


def _tr(text):
    # v5.10.235: straight from i18n, as HeadlessApp.tr does; rows.app() built
    # the Home's row machinery (homeui.window included) to translate the menu
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


def notify(message, icon=xbmcgui.NOTIFICATION_INFO):
    try:
        xbmcgui.Dialog().notification('Dex Hub', message, icon, 2500, sound=False)
    except Exception:
        pass


def _arg(path):
    """A path quoted for a builtin (plugin paths may hold commas)."""
    return '"%s"' % str(path).replace('\\', '\\\\').replace('"', '\\"')


def launch(command):
    if command:
        xbmc.executebuiltin(command)


def run_builtin(command):
    """A route's menu command (homeui window.run_builtin)."""
    import re
    command = str(command or '').strip()
    if not command:
        return
    match = re.match(r'^Container\.Update\((.*?)(?:,\s*replace)?\)$', command, re.I)
    if match:
        command = 'ActivateWindow(Videos,%s,return)' % match.group(1)
    xbmc.executebuiltin(command)


def worker_alive():
    try:
        return time.time() - float(C.prop('dhs.worker') or 0) < 15.0
    except Exception:
        return False


def dialog_label(info):
    """An info label of the open title page's item."""
    try:
        return xbmc.getInfoLabel('ListItem.%s' % info) or ''
    except Exception:
        return ''


# --------------------------------------------------------------------------
# the Home
# --------------------------------------------------------------------------

def publish(params, handle):
    """The Home opened with no rows on it (Kodi's start): the rows saved last
    time go up at once, and the service works them out again."""
    from . import board
    shown = board.publish_saved()
    if worker_alive():
        if C.prop(C.PROP_PUBLISHED):
            C.ask_republish('home')     # else the service is publishing as it starts
        return None
    if shown:
        return None         # the service publishes when it starts
    from . import rows
    rows.publish(reason='home')
    return None


def refresh_home(params, handle):
    """Read every row again (the menu's Refresh)."""
    import os
    folder = C.folder('rows')
    for name in os.listdir(folder):
        if name.endswith('.json'):
            try:
                os.remove(os.path.join(folder, name))
            except Exception:
                pass
    C.ask_fresh()                   # past the HTTP and page caches
    C.ask_republish('refresh')
    if not worker_alive():
        from . import rows
        rows.publish(reason='refresh')
    notify(_tr('جاري تحديث الرئيسية'))
    return None


def focus_colour(params, handle):
    """Configure skin > Appearance > Focus colour (v5.10.132): the theme's own
    colour, the named ones (Arctic Fuse 3's highlight colours, Nuvio's red,
    a few of Dex Hub's), or any other from Kodi's colour picker."""
    from ..homeui.options import choose
    from .. import skin_theme as T
    from ..i18n import is_english
    tr = _tr
    current = (xbmc.getInfoLabel('Skin.String(dh.accent.custom)') or '').strip().upper()
    english = is_english()
    entries = [('', tr('لون الثيم'), T.THEME_PRESETS.get(T.preset_for_colour(T.skin_colour_theme()) or '7', {})
                .get('accent', ''))]
    for colour, arabic, name in T.FOCUS_COLOURS:
        entries.append((colour, name if english else arabic, colour))
    entries.append(('more', tr('لون آخر…'), ''))
    labels, preselect = [], -1
    for index, (key, label, swatch) in enumerate(entries):
        shown = '[COLOR %s]●[/COLOR]  %s' % (swatch, label) if swatch else label
        labels.append(shown)
        if key and key.upper() == current or (not key and not current):
            preselect = index
    if preselect < 0 and current:
        preselect = len(entries) - 1          # a colour of the picker's
    choice = choose(tr('لون المؤشر'), labels, preselect=preselect, subtitle=tr('المظهر'))
    if choice < 0:
        return None
    key, name = entries[choice][0], entries[choice][1]
    if key == 'more':
        if xbmc.getSkinDir() == T.DEXHUB_SKIN:
            xbmc.executebuiltin('Skin.Reset(dh.accent.name)', True)
            xbmc.executebuiltin('Skin.SetColor(dh.accent.custom,31888,%s,special://skin/extras/accents.xml)'
                                % (current or entries[0][2] or 'FF9B6BFF'), True)
    elif key:
        xbmc.executebuiltin('Skin.SetString(dh.accent.custom,%s)' % key, True)
        # the name the skin's settings page shows (a comma would split the builtin)
        xbmc.executebuiltin('Skin.SetString(dh.accent.name,%s)' % name.replace(',', ' '), True)
    else:
        xbmc.executebuiltin('Skin.Reset(dh.accent.custom)', True)
        xbmc.executebuiltin('Skin.Reset(dh.accent.name)', True)
    try:
        T.publish_theme()
    except Exception:
        C.log('focus colour:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    return None


# v5.10.358: what each Menu entry does on its way back. In this run: the
# Menu opens again when it is done. A page or another run: the Menu waits for
# it to close (menu_return). The rest leave the Menu (a skin switch, a reboot).
_BACK_HERE = ('layout', 'community', 'setup', 'player', 'badges', 'size', 'screensaver')
_BACK_WAIT = ('search', 'sources', 'classic', 'kodi', 'theme', 'power')


def menu(params, handle):
    """The Home's Menu: the control centre (v5.10.198, homeui/control.py);
    the skin's Settings page names an entry at once (key=...).
    v5.10.358: Back from what an entry opened comes back to the Menu (and
    from a settings section to the settings cards), not to the Home."""
    from . import menu_return
    menu_return.forget()
    # opened from the Menu itself, or from the settings cards it opened
    from_menu = not (params.get('key') or '') or params.get('back') == 'menu'
    while True:
        key = _menu_once(params, from_menu)
        params = {}
        if not key or not from_menu:
            return None
        if key in _BACK_HERE or key == 'settings:cancel':
            continue                    # the Menu again, on the same section
        if key in _BACK_WAIT or key.startswith('control:'):
            menu_return.back_to(C.url('skin_menu'))
        return None


def _menu_once(params, from_menu=False):
    """One pass of the Menu: the key it handled ('' when left with Back)."""
    tr = _tr
    key = params.get('key') or ''
    # v5.10.299: the skin's settings page opens the one Appearance place
    looks = {'customize': 'home', 'look': 'theme', 'look_theme': 'theme',
             'look_layout': 'home', 'look_motion': 'trailers'}
    from ..menu_taxonomy import META
    looks.update({'section_'+section:section for section in META})
    if not key or key in looks:
        from ..homeui import control
        if key in looks:
            xbmcgui.Window(10000).setProperty(control.LAST_PROP, looks[key])
        tab = C.prop('dhs.tab') or 'all'
        key = control.open_centre(tr=tr, media=tab)
        if key is None:
            key = control.fallback_choose(tr)   # a skin without the window: a plain list
        if not key:
            return ''
    if key == 'search':
        # Dex Hub's search page over the skin (catalogs, servers, people)
        launch('RunPlugin(%s)' % _arg(C.url('home_search')))
    elif key == 'layout':
        layout_editor()
    elif key == 'community':
        community()
    elif key == 'setup':
        from ..homeui import setup
        if setup.open_setup(tr=tr, first_run=False):
            _republish('accounts')
    elif key == 'player':
        from . import player_options
        player_options.show(tr)
    elif key == 'badges':
        # v5.10.146: the Badges section (skin.dexhub's settings page)
        # v5.10.148: the badge gallery (the switches stay in Badges settings)
        from ..homeui import wizard
        wizard.open_wizard(steps=('badges',))
    elif key == 'sources':
        launch('ActivateWindow(Videos,%s,return)' % _arg(C.url('setup_connections')))
    elif key == 'refresh':
        refresh_home(params, None)       # v5.10.411: this menu has no handle (NameError)
    elif key == 'size':
        if C.served() == 'dexhub':
            # v5.10.120: skin.dexhub's cards take the size too (3.6.0)
            skin_size(tr)
        else:
            # the size of Dex Hub's own pages over the skin (Servers, Live TV,
            # Search, menus): the skin's hubs keep Arctic Fuse's size
            from ..homeui import ui_size
            ui_size.choose(tr)
    elif key == 'screensaver':
        from ..homeui import screensaver
        screensaver.setup_menu(tr)
    elif key == 'classic':
        launch('ActivateWindow(Videos,%s,return)' % _arg(C.url('home_classic')))
    elif key == 'settings':
        # v5.10.147: the settings home as cards, like this menu
        # v5.10.358: Back on the cards comes back to the Menu
        from ..routes.simple_entry import settings_cards
        if settings_cards(back='menu' if from_menu else '') == 'cancel':
            return 'settings:cancel'
        return 'settings'
    elif key == 'kodi':
        launch('RunPlugin(%s)' % _arg(C.url('open_kodi_settings')))
    elif key == 'theme':
        launch('ActivateWindow(1191)' if xbmc.getSkinDir() == 'skin.dexhub' else 'RunPlugin(%s)' % _arg(C.url('theme_select')))          # v5.10.299: the skin's theme gallery
    elif key == 'skin_pick':
        from . import switch
        switch.pick({})
    elif key == 'skin_back':
        from . import switch
        switch.back({})
    elif key == 'power':
        launch('ActivateWindow(ShutdownMenu)')
    elif key == 'android':
        from .. import power
        power.reboot_to_android(tr)
    elif key == 'skin':
        launch('RunPlugin(%s)' % _arg(C.url('skin_use')))
    else:
        from ..homeui import control
        if control.launch(key):
            return 'control:' + key
    return key


SKIN_SIZES = ('', 'large', 'xlarge')       # skin.dexhub 3.6's Skin.String(DexHub.CardSize)


def skin_size(tr):
    """Interface size in skin.dexhub (v5.10.120): the Home's and the folder
    pages' cards (the skin zooms its rows) and Dex Hub's own pages over the
    skin, together. True when it changed."""
    from ..homeui import ui_size
    from ..homeui.options import choose as menu
    from ..home_preferences import set_poster_scale, choose_poster_scale, current_scale, SCALE_NAMES
    import xbmcaddon
    addon = xbmcaddon.Addon(C.ADDON_ID)
    # v5.10.288: one reading of the size in force, shared with the Menu's card
    current = current_scale(addon)
    scales = tuple(value for value, _name in SCALE_NAMES)
    names = tuple(name for _value, name in SCALE_NAMES)
    options = [(tr(name), '%d%%' % value) for name, value in zip(names, scales)]
    # v5.10.288: a custom size shows its own percentage on the Custom row
    options.append((tr('مخصص'), '50–200%' if current in scales else '%d%%' % current))
    choice = menu(tr('حجم الواجهة'), options, preselect=scales.index(current) if current in scales else 6,
                  subtitle=tr('وضع الصفين يضبط الحجم تلقائياً لعرض صفين كاملين'))
    if choice == 6:
        return choose_poster_scale(tr)
    if choice < 0:
        return False
    set_poster_scale(scales[choice])
    addon.setSetting(ui_size.SETTING, ui_size.ORDER[min(choice, 2)])
    C.log('poster size: %d%%' % scales[choice])
    return True


def layout_editor():
    """The Home layout editor (homeui), for the tab on show; the rows follow it."""
    from ..homeui.layout_window import open_editor
    from . import rows
    tab = C.prop('dhs.tab') or 'all'
    if tab not in C.TABS or tab in ('library', 'servers'):
        tab = 'all'         # the add-ons page and the Library have no editable rows
    app = rows.app()
    changed = open_editor(app, rows.HeadlessHome(app, tab), media=tab)
    if changed:
        _republish('layout')


def community():
    """Dex Community (v5.10.195) for the tab on show; the rows follow it."""
    from ..homeui.community_window import open_community
    from . import rows
    tab = C.prop('dhs.tab') or 'all'
    page = tab if tab in ('movie', 'series') else 'all'
    changed = open_community(rows.app(), page=page)
    if changed:
        _republish('layout')


def _republish(reason):
    from . import rows
    if worker_alive():
        C.ask_republish(reason)
    else:
        rows.publish(reason=reason)


# --------------------------------------------------------------------------
# the title page
# --------------------------------------------------------------------------

def _title(params):
    from . import title as T
    t = T.HeadlessTitle(T.query_of(params))
    t.load()
    t.fresh_state()
    return t


def _same_page(params):
    """The info dialog still shows this title (its art and play route are read from it)."""
    return bool(params.get('i')) and dialog_label('Property(dhs.id)') == params.get('i')


def _hero_tile(t, params):
    tile = dict(t.hero)
    if _same_page(params):
        for key, info in (('poster', 'Art(poster)'), ('fanart', 'Art(fanart)'), ('clearlogo', 'Art(clearlogo)')):
            value = dialog_label(info)
            if value and not tile.get(key):
                tile[key] = value
    return tile


def play(params, handle):
    from . import rows
    from ..homeui import rows as R
    clear_trailer_mark()
    if _same_page(params):
        exact = dialog_label('Property(dhs.play)')
        if exact.startswith(C.BASE) and R.params_of(exact).get('action') in R.PLAYER_ACTIONS:
            launch('RunPlugin(%s)' % _arg(exact))
            return None
    t = _title(params)
    ctx = rows.Context('all')
    if not t.series:
        path = dialog_label('Property(dhs.play)') if _same_page(params) else ''
        if not path:
            api = rows.api()
            tile = _hero_tile(t, params)
            try:
                raw, _folder = api._content_click_path(
                    media_type='movie', canonical_id=t.canonical, title=t.hero.get('title') or '',
                    tmdb_id=t.hero.get('tmdb_id') or '', imdb_id=t.hero.get('imdb_id') or '',
                    source_provider_id=t.source_provider_id, force_dexhub=True)
            except Exception:
                raw = ''
            if not raw:
                notify(_tr('تعذر تشغيل هذا العمل'))
                return None
            action = R.params_of(raw).get('action', '')
            path = ctx.home_route(ctx.with_ui_art(raw, action, tile))
        launch('RunPlugin(%s)' % _arg(path))
        return None
    t.ensure_episodes()
    label, _status, target = t.play_choice()
    row = None
    if target:
        rows_of = t._season_rows(target[0]) or []
        row = next((r for r in rows_of if int(r.get('episode') or 0) == int(target[1] or 0)), None)
        if row is None and rows_of:
            row = rows_of[0]
    if row is None and t.seasons:
        rows_of = t._season_rows(t.seasons[0][0]) or []
        row = rows_of[0] if rows_of else None
    _play_episode(t, ctx, row, params)
    return None


def _route_of(command):
    """The Dex Hub route of a Container.Update(...) menu command, else ''."""
    import re
    match = re.match(r'^Container\.Update\((.*?)(?:,\s*replace)?\)$', str(command or '').strip(), re.I)
    path = match.group(1).strip().strip('"') if match else ''
    return path if path.startswith(C.BASE) else ''


def _choose_source(t, ctx, command, params, extra=None):
    """The source list of a title over its page (v5.10.110). The page is a
    modal dialog and Kodi refuses to open a window over it ("Activate of
    window refused because there are active modal dialogs"), so the route
    runs as a plugin call, as an episode's OK does: its source list opens
    over the page and Back returns to it. False when the command is not a
    route that plays."""
    from ..homeui import rows as R
    path = _route_of(command)
    action = R.params_of(path).get('action', '') if path else ''
    if action not in R.PLAYER_ACTIONS:
        return False
    tile = _hero_tile(t, params)
    tile.update(extra or {})
    launch('RunPlugin(%s)' % _arg(ctx.home_route(ctx.with_ui_art(path, action, tile))))
    return True


def _play_episode(t, ctx, row, params, choose=False):
    from ..homeui import rows as R
    if not row:
        notify(_tr('لا توجد حلقات لهذا المسلسل في مصادر البيانات'))
        return
    if choose:
        command = next((m[1] for m in row.get('menu') or [] if m and len(m) > 1), '')
        extra = {'media_type': 'episode', 'season': str(row.get('season')), 'episode': str(row.get('episode'))}
        if command and _choose_source(t, ctx, command, params, extra):
            return
        if command:
            run_builtin(command)
            return
    path = row.get('path') or ''
    if not path:
        notify(_tr('تعذر تشغيل هذه الحلقة'))
        return
    tile = _hero_tile(t, params)
    tile.update({'path': path, 'media_type': 'episode', 'season': str(row.get('season')),
                 'episode': str(row.get('episode'))})
    action = R.params_of(path).get('action', '')
    launch('RunPlugin(%s)' % _arg(ctx.home_route(ctx.with_ui_art(path, action, tile))))


def _selected_episode(t):
    """The episode under the cursor on the page (or None)."""
    episodes = C.layout().tp['episodes']
    try:
        season = int(xbmc.getInfoLabel('Container(%d).ListItem.Property(season)' % episodes) or -1)
        episode = int(xbmc.getInfoLabel('Container(%d).ListItem.Property(episode)' % episodes) or -1)
    except Exception:
        return None
    if season < 0 or episode < 0:
        return None
    for row in t._season_rows(season) or []:
        if int(row.get('episode') or 0) == episode:
            return row
    return None


def sources(params, handle):
    from . import rows
    clear_trailer_mark()
    t = _title(params)
    ctx = rows.Context('all')
    if t.series:
        row = _selected_episode(t) if _same_page(params) else None
        if row is None:
            _label, _status, target = t.play_choice()
            if target:
                row = next((r for r in t._season_rows(target[0]) or []
                            if int(r.get('episode') or 0) == int(target[1] or 0)), None)
        _play_episode(t, ctx, row, params, choose=True)
        return None
    try:
        api = rows.api()
        entries = api._build_source_picker_menu(media_type='movie', canonical_id=t.canonical,
                                               title=t.hero.get('title') or '',
                                               source_provider_id=t.source_provider_id)
    except Exception:
        entries = []
    command = next((m[1] for m in entries if m and len(m) > 1), '')
    if command and _choose_source(t, ctx, command, params):
        return None
    if command:
        run_builtin(command)
    else:
        play(params, handle)
    return None


def open_grid(params, handle):
    """v5.10.315: the title page's Collection card and Studios tiles: the
    page closes and the grid opens on their titles."""
    url = params.get('u') or ''
    if not url.startswith(C.BASE):
        return None
    xbmc.executebuiltin('Dialog.Close(all,true)', True)
    xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % url.replace('"', '%22'))
    return None


def warm_sources(params, handle):
    """v5.10.311: the Play button got the focus: ask every add-on for this
    title's (or the next episode's) sources in the background, so the list
    opens full a moment later. Once per title every four minutes."""
    try:
        from .. import ui_preferences
        if ui_preferences.value('sources_warm') != 'true':
            return None
    except Exception:
        pass
    from . import rows
    home = xbmcgui.Window(10000)
    stamp = 'dexhub.warm.%s' % (params.get('id') or params.get('canonical_id') or '')
    try:
        if time.time() - float(home.getProperty(stamp) or 0) < 240:
            # warmed a moment ago: the card shows what that run found
            _republish_available(home, params.get('pid'))
            return None
    except Exception:
        pass
    home.setProperty(stamp, '%.0f' % time.time())
    t = _title(params)
    ctx = rows.Context('all')
    path = ''
    if t.series:
        row = None
        _label, _status, target = t.play_choice()
        if target:
            row = next((r for r in t._season_rows(target[0]) or []
                        if int(r.get('episode') or 0) == int(target[1] or 0)), None)
        command = next((m[1] for m in (row or {}).get('menu') or [] if m and len(m) > 1), '')
        path = _route_of(command) if command else ''
    else:
        try:
            api = rows.api()
            entries = api._build_source_picker_menu(media_type='movie', canonical_id=t.canonical,
                                                   title=t.hero.get('title') or '',
                                                   source_provider_id=t.source_provider_id)
        except Exception:
            entries = []
        command = next((m[1] for m in entries if m and len(m) > 1), '')
        path = _route_of(command) if command else ''
    if not path:
        C.log('warm: no sources route for "%s"' % (t.hero.get('title') or t.canonical))
        return None
    from ..homeui import rows as R
    q = R.params_of(path)
    api = rows.api()
    page = params.get('pid') or t.title_id
    api._WARM_SUMMARY = []
    api._WARM_ONLY = True
    home.setProperty('dhs.tp.avail.busy', page or '')
    try:
        if q.get('action') == 'episode_streams':
            api.episode_streams(q.get('canonical_id', ''), q.get('video_id', ''), q.get('season', ''),
                                q.get('episode', ''), title=q.get('title', ''), media_type=q.get('media_type', ''),
                                source_provider_id=q.get('source_provider_id', ''))
        elif q.get('action') == 'streams':
            api.streams(q.get('media_type', 'movie'), q.get('canonical_id', ''), title=q.get('title', ''),
                        source_provider_id=q.get('source_provider_id', ''))
    except Exception:
        C.log('warm failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    finally:
        api._WARM_ONLY = False
    _publish_available(home, page, getattr(api, '_WARM_SUMMARY', None) or [])
    if home.getProperty('dhs.tp.avail.busy') == page:
        home.setProperty('dhs.tp.avail.busy', '')
    return None


def _publish_available(home, page, summary, rows=3):
    """v5.10.448: the cinematic title page's "Available on" card
    (dhs.tp.avail.*): the warmed providers, best first; shown while
    dhs.tp.avail.id names the open page's title (dhs.id)."""
    if not page:
        return
    home.setProperty('dhs.tp.avail.id', '')
    for n in range(1, rows + 1):
        row = summary[n - 1] if n <= len(summary) else {}
        for field in ('name', 'q', 'hdr'):
            home.setProperty('dhs.tp.avail.%d.%s' % (n, field), str(row.get(field) or ''))
        home.setProperty('dhs.tp.avail.%d.n' % n, str(row.get('n') or ''))
    home.setProperty('dhs.tp.avail.total', str(sum(r.get('n') or 0 for r in summary)) if summary else '')
    home.setProperty('dhs.tp.avail.id', page if summary else '')
    home.setProperty('dhs.tp.avail.last.%s' % page, json.dumps(summary[:rows]))


def _republish_available(home, page):
    """The page opened again inside the warm-up's four minutes (no new
    run): the card gets that title's last summary back, even when another
    title's sources were shown in between."""
    if not page or home.getProperty('dhs.tp.avail.id') == page:
        return
    try:
        summary = json.loads(home.getProperty('dhs.tp.avail.last.%s' % page) or '[]')
    except ValueError:
        summary = []
    if summary:
        _publish_available(home, page, summary)


def trailer(params, handle):
    from . import rows
    t = _title(params)
    imdb = t.hero.get('imdb_id') or params.get('imdb') or ''
    if not imdb:
        try:
            enricher = rows.HeadlessEnricher(rows.app())
            tile = dict(t.hero)
            enricher._enrich_now(tile, '')
            imdb = tile.get('imdb_id') or ''
        except Exception:
            imdb = ''
    stream = None
    if imdb:
        from ..homeui.trailers import TrailerResolver
        app = rows.app()
        quality = {'1080p': 1080, '720p': 720, '480p': 480}.get(
            app.settings.text('homeui_trailer_quality', '720p'), 720)
        resolver = TrailerResolver(app.profile, quality=quality, log=app.log)
        try:
            stream = resolver.resolve(imdb)
        finally:
            try:
                resolver.flush()
            except Exception:
                pass
    if not stream or not stream.get('url'):
        notify(_tr('لا يوجد تريلر لهذا العمل'))
        return None
    title = t.hero.get('title') or 'Trailer'
    item = xbmcgui.ListItem(label=title, path=stream['url'], offscreen=True)
    try:
        item.setMimeType(stream.get('mime') or 'video/mp4')
        item.setContentLookup(False)
        tag = item.getVideoInfoTag()
        tag.setTitle(title)
        tag.setMediaType('video')
    except Exception:
        pass
    item.setProperty('dexhub.trailer', '1')
    if xbmc.getCondVisibility('System.Platform.TVOS'):
        item.setProperty('dexhub.playercore', 'VideoPlayer')
    from ..trailer_playback import request_takeover, finish_takeover, foreground_if_playing
    # v5.10.286 (issue #14): this title's trailer still plays behind the GUI
    # (Back from it): shown full screen again, not opened a second time
    if C.prop('dhs.trailer') and C.prop('dexhub.trailer.active'):
        xbmc.executebuiltin('Dialog.Close(movieinformation,true)', True)
        if foreground_if_playing(stream['url']):
            return None
    if not request_takeover():
        finish_takeover()
        notify(_tr('التريلر السابق ما زال يغلق، حاول مرة أخرى'))
        return None
    # a preview, never a session to resume or scrobble (companion.py); the
    # service clears the mark when it ends, or when something else plays
    C.set_prop('dhs.trailer.seen', '')
    C.set_prop('dexhub.trailer.url', stream['url'])
    C.set_prop('dexhub.trailer.active', '1')
    C.set_prop('dhs.trailer', '%.1f' % time.time())
    from .. import refresh_guard
    refresh_guard.suspend()        # v5.10.156 (issue #5)
    xbmc.executebuiltin('Dialog.Close(movieinformation,true)', True)
    from ..trailer_playback import play_fullscreen
    try:
        play_fullscreen(stream['url'], item)
    finally:
        finish_takeover()
    return None


def clear_trailer_mark():
    """Real playback starts from the title page: no trailer mark may stay."""
    for key in ('dhs.trailer', 'dhs.trailer.seen', 'dexhub.trailer.active', 'dexhub.trailer.url'):
        C.set_prop(key, '')
    from .. import refresh_guard
    refresh_guard.restore()


def favourite(params, handle):
    from .. import favorites_store as F
    t = _title(params)
    media = t.media_type if t.series else 'movie'
    tile = _hero_tile(t, params)
    try:
        if t.favorite:
            F.remove(media, t.canonical)
            t.favorite = False
            notify(_tr('حُذف من المفضلة'))
        else:
            year = 0
            try:
                year = int(tile.get('year') or 0)
            except Exception:
                year = 0
            F.add(media, t.canonical, tile.get('title') or '', poster=tile.get('poster') or '',
                  background=tile.get('fanart') or '', clearlogo=tile.get('clearlogo') or '',
                  year=year, plot=tile.get('plot') or dialog_label('Plot'))
            t.favorite = True
            notify(_tr('أُضيف للمفضلة'))
    except Exception:
        C.log('favourite failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    t.publish()
    return None


def _work(t, params):
    """v5.10.365: the title as the tracking services know it."""
    from .. import tracking
    tile = _hero_tile(t, params)
    return tracking.Work('series' if t.series else 'movie', canonical=t.canonical,
                         tmdb=t.tmdb_id or tile.get('tmdb_id') or '', imdb=tile.get('imdb_id') or t._imdb() or '',
                         title=tile.get('title') or dialog_label('Title'), year=tile.get('year') or 0,
                         poster=tile.get('poster') or '', fanart=tile.get('fanart') or '',
                         clearlogo=tile.get('clearlogo') or '', plot=tile.get('plot') or '')


def track(params, handle):
    """v5.10.153: the tracking panel (status, rating, lists) for this title."""
    from .. import tracking, tracking_ui
    t = _title(params)
    tile = _hero_tile(t, params)
    work = tracking.Work('series' if t.series else 'movie', canonical=t.canonical,
                         tmdb=t.tmdb_id or tile.get('tmdb_id') or '', imdb=tile.get('imdb_id') or '',
                         title=tile.get('title') or dialog_label('Title'), year=tile.get('year') or 0,
                         poster=tile.get('poster') or '', fanart=tile.get('fanart') or '',
                         clearlogo=tile.get('clearlogo') or '', plot=tile.get('plot') or '')
    try:
        tracking_ui.open_panel(work)
    except Exception:
        C.log('track panel failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    t.fresh_state()
    t.publish()
    return None


def more(params, handle):
    from ..homeui.options import choose
    from . import rows
    t = _title(params)
    ctx = rows.Context('all')
    row = _selected_episode(t) if (t.series and _same_page(params)) else None
    if t.series and row is None:
        _label, _status, target = t.play_choice()
        if target:
            row = next((r for r in t._season_rows(target[0]) or []
                        if int(r.get('episode') or 0) == int(target[1] or 0)), None)
    key = (int(row['season']), int(row['episode'])) if row else (0, 0)
    watched = t._watched(t.progress.get(key))
    entries = []
    if t.series and row:
        code = 'S%dE%d' % key
        entries.append((_tr('تعليم %s كغير مشاهدة') % code if watched else _tr('تعليم %s كمشاهدة') % code,
                        'unwatch' if watched else 'watch'))
    elif not t.series:
        entries.append((_tr('تعليم كغير مشاهد') if watched else _tr('تعليم كمشاهد'),
                        'unwatch' if watched else 'watch'))
    helper = ctx.helper_path(dict(t.hero, kind='work', path=''))
    if helper:
        entries.append((_tr('فتح في TMDb Helper'), 'helper'))
    # v5.10.322: as Nuvio Legacy's "..." menu
    if t.hero.get('plot'):
        entries.append((_tr('قراءة النبذة'), 'synopsis'))
    # v5.10.410: Change artwork and Reviews left the menu; they are the
    # page's Pictures and Reviews rows now (both still open by what=)
    entries.append((_tr('مشاهدة جماعية'), 'party'))      # v5.10.366
    if t.series:
        entries.append((_tr('الصفحة الكلاسيكية'), 'classic'))
    what = (params.get('what') or '').strip()     # v5.10.309: the title page's own shortcut discs
    if what == 'watch':
        what = next((e[1] for e in entries if e[1] in ('watch', 'unwatch')), '')
    if what not in [e[1] for e in entries] + ['artwork', 'reviews']:
        choice = choose(t.hero.get('title') or '', [e[0] for e in entries], subtitle=_tr('المزيد'))
        if choice < 0:
            return None
        what = entries[choice][1]
    if what in ('watch', 'unwatch'):
        _set_watched(t, row, what == 'watch')
    elif what == 'synopsis':
        _synopsis(t)
    elif what == 'party':
        from .. import watch_party
        work = _work(t, params)
        watch_party.from_title({'media': work.media, 'canonical': t.canonical or '', 'imdb': work.imdb or '',
                                'tmdb': str(work.tmdb or ''), 'title': t.hero.get('title') or work.title or '',
                                'poster': work.poster or '', 'fanart': work.fanart or '', 'clearlogo': work.clearlogo or ''})
    elif what == 'reviews':
        from .. import title_reviews
        work = _work(t, params)
        title_reviews.show(work.media, work.imdb, work.tmdb, t.hero.get('title') or work.title, _tr)
    elif what == 'artwork':
        from .. import art_choice
        tmdb = t.tmdb_id or t.hero.get('tmdb_id') or ''
        if not str(tmdb).isdigit():
            try:
                tmdb = t._resolve_tmdb_id() or ''
            except Exception:
                tmdb = ''
        current = {'fanart': xbmc.getInfoLabel('ListItem.Art(fanart)'),
                   'clearlogo': xbmc.getInfoLabel('ListItem.Art(clearlogo)')}
        if art_choice.pick(tmdb, t._imdb(), 'series' if t.series else 'movie', t.hero.get('title') or '',
                           _tr, current):
            t.publish()
    elif what == 'helper':
        xbmc.executebuiltin('Dialog.Close(all,true)', True)
        launch('ActivateWindow(Videos,%s,return)' % _arg(helper))
    elif what == 'classic':
        try:
            api = rows.api()
            path, _folder = api._content_click_path(
                media_type=t.media_type, canonical_id=t.canonical, title=t.hero.get('title') or '',
                source_provider_id=t.source_provider_id, force_dexhub=True)
        except Exception:
            path = ''
        if path:
            xbmc.executebuiltin('Dialog.Close(all,true)', True)
            launch('ActivateWindow(Videos,%s,return)' % _arg(ctx.home_route(path)))
    return None


def _synopsis(t):
    """v5.10.322: the whole plot (Nuvio's Read synopsis), with the tagline
    and IMDb's Parents Guide when known."""
    hero = t.hero
    parts = []
    if hero.get('tagline'):
        parts.append('[I]%s[/I]' % hero['tagline'])
    parts.append(str(hero.get('plot') or ''))
    try:
        from .. import parents_guide as PG
        rows = PG.guide(t._imdb(), network=False)
        if rows:
            parts.append('[B]%s[/B][CR]%s' % (_tr('دليل الوالدين (IMDb)'), PG.lines(rows)))
    except Exception:
        pass
    xbmcgui.Dialog().textviewer(hero.get('title') or '', '[CR][CR]'.join(p for p in parts if p))


def _set_watched(t, row, watched):
    """homeui details._set_watched, then the page and the Home follow."""
    from ..homeui import details as D
    fn = D.DetailsWindow.__dict__['_set_watched']
    t._fill_buttons = lambda: None
    t._show_season = lambda *a, **k: None
    t.season_at = 0
    try:
        fn(t, row, watched)
    except Exception:
        C.log('watched failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    t.fresh_state()
    t.publish()
    # the episodes list reads its season again; the Home's progress rows follow
    C.set_prop('dhs.tp.rev.episodes', '%d' % int(time.time() * 1000))


def open_title(params, handle):
    """Another title's page, from More like this on a page."""
    from . import rows
    from . import items as I
    from . import title as T
    q = T.query_of(params)
    q.pop('page', None)
    from .title_seed import cached_item
    seeded = cached_item(q)
    if seeded is not None:
        xbmc.executebuiltin('Dialog.Close(movieinformation,true)', True)
        C.show_info(I.listitem(seeded))
        return None
    tile = None
    # the related title's own data, as the page that listed it knew it
    page = None
    try:
        page_q = dict(parse_qsl(params.get('page') or ''))
        if page_q:
            page = T.HeadlessTitle(page_q).load()
    except Exception:
        page = None
    for candidate in (page.related if page is not None else []):
        if str(candidate.get('tmdb_id') or '') == str(q.get('tmdb') or '') and q.get('tmdb'):
            tile = dict(candidate)
            break
    if tile is None:
        media = 'series' if q.get('m') in ('series', 'anime') else 'movie'
        tile = {'kind': 'work', 'media_type': media, 'title': q.get('title') or '', 'label': q.get('title') or '',
                'year': q.get('year') or '', 'tmdb_id': q.get('tmdb') or '', 'imdb_id': q.get('imdb') or '',
                'path': C.url('item_open', media_type=media, canonical_id=q.get('id') or '',
                              title=q.get('title') or '', tmdb_id=q.get('tmdb') or ''),
                'folder': media == 'series'}
    ctx = rows.Context('all')
    item = ctx.item(tile, {'shape': 'poster'})
    li = I.listitem(item)
    xbmc.executebuiltin('Dialog.Close(movieinformation,true)', True)
    C.show_info(li)
    return None


# --------------------------------------------------------------------------
# grids (the Videos window, views 600 and 601)
# --------------------------------------------------------------------------

_FILL = 30          # titles a grid page aims for: one source page, at once (v5.10.323; the next page's card numbers the rest)
_READS = 3
_LOCAL_FILL = 36    # with a filter made here (homeui GridWindow)
_LOCAL_READS = 8


def _genres_file(key):
    return C.folder('grids') + '/genres_%s.json' % C.short_id(key)


def grid(params, handle):
    from . import rows
    from . import items as I
    from ..homeui import rows as R
    from ..homeui import grid_filters as GF
    from ..catalog_navigation import unwrap
    route = unwrap(dict(parse_qsl(params.get('r') or '', keep_blank_values=True)))
    if not route:
        return _end(handle)
    title = params.get('t') or ''
    shape = params.get('s') or ''
    continuation = bool(params.get('n'))
    app = rows.app()
    if route.get('action') == 'favorites' and not title:
        title = app.tr('المفضلة')
    facets, state, local = None, None, None
    fetch_params = dict(route)
    if not continuation:
        facets = GF.describe(route, app)
        if facets is None:
            local = GF.describe_local(route)
            if local is not None and not GF.is_empty(GF.load_state(app.profile, local['key'])):
                facets, local = local, None
        if facets is not None:
            saved = GF.native_state(facets, GF.load_state(app.profile, facets['key']))
            if not GF.is_empty(saved):
                state = saved
                fetch_params = GF.route_params(facets, state)
            else:
                state = facets.get('preset') or GF.empty_state()
    else:
        try:
            base = dict(parse_qsl(params.get('b') or '', keep_blank_values=True))
            facets = GF.describe(base, app) or GF.describe_local(base)
            if facets is not None:
                state = GF.native_state(facets, GF.load_state(app.profile, facets['key']))
        except Exception:
            facets = None
    rules = GF.local_rules(facets, state) if facets is not None and state else {}
    order = GF.local_sort(state) if facets is not None and state else ''
    tiles, more, reads = [], None, 0
    api = rows.api()
    if order:
        seen, nxt = set(), fetch_params
        while nxt and reads < GF.SORT_PAGES and len(tiles) < GF.SORT_CAP:
            page, nxt_more = _page(api, nxt, shape)
            reads += 1
            fresh = 0
            for tile in page:
                mark = tile.get('path') or id(tile)
                if mark in seen:
                    continue
                seen.add(mark)
                tiles.append(tile)
                fresh += 1
            nxt = nxt_more if fresh else None
        if rules:
            tiles = [t for t in tiles if GF.passes(t, rules)]
        tiles = GF.sort_tiles(tiles[:GF.SORT_CAP], order)
        more = None
    else:
        nxt = fetch_params
        fill, limit = (_LOCAL_FILL, _LOCAL_READS) if rules else (_FILL, _READS)
        while nxt and reads < limit:
            page, more = _page(api, nxt, shape)
            reads += 1
            if rules:
                page = [t for t in page if GF.passes(t, rules)]
            tiles.extend(page)
            nxt = more
            if len(tiles) >= fill:
                break
    if facets is None and local is not None and GF.titles_page(tiles):
        facets, state = local, GF.empty_state()
    # the grid's shape: what its titles are
    if not shape:
        shape = R.dominant_shape(tiles, 'poster')
        if R.is_show_page(route) and sum(1 for t in tiles if t.get('media_type') == 'episode') * 2 > len(tiles):
            shape = 'landscape'
    ctx = rows.Context('all')
    ctx.batch = True
    spec = {'shape': shape, 'grid': True}
    items = []
    for tile in tiles:
        try:
            items.append(ctx.item(tile, spec))
        except Exception:
            continue
    ctx.flush_seeds()
    # the titles' plot, logo and backdrop as the add-on's grid shows them:
    # what is known now, the rest looked up by the service (gridfocus.py)
    todo = []
    try:
        from . import gridfocus
        todo = gridfocus.prepare(items, ctx.language or '-')
    except Exception:
        C.log('grid enrichment:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
    # v5.10.322 (GitHub #10): the page's number, on the next page's card
    try:
        page_no = max(1, int(params.get('pn') or 1))
    except Exception:
        page_no = 1
    if more:
        label = '%s  ·  %d' % (app.tr('الصفحة التالية'), page_no + 1)
        nxt_url = C.url('skin_list', r=urlencode(sorted((k, v) for k, v in more.items() if v not in (None, ''))),
                        t=title, s=shape, n='1', b=params.get('b') or (params.get('r') or ''), pn=str(page_no + 1),
                        lg=params.get('lg') or '')
        art = app.media_path('more_tile.png')
        items.append({'label': label, 'path': nxt_url, 'folder': True,
                      'art': {'poster': art, 'landscape': art, 'thumb': art},
                      'props': {'kind': 'more', 'dhs.click': 'grid', 'dhs.target': nxt_url}})
    props = {'dhs.grid': '1', 'dhs.title': ('%s  ·  %d' % (title, page_no)) if page_no > 1 else title,
             'dhs.shape': shape}
    if params.get('lg'):
        props['dhs.title_logo'] = params['lg']     # v5.10.360: the studio's logo in place of its name
    if facets is not None and not continuation:
        props.update(_facet_props(facets, state or GF.empty_state(), app))
        genres = {}
        for tile in tiles:
            if tile.get('kind') != 'work':
                continue
            for name in tile.get('genres') or []:
                name = str(name or '').strip()
                if name:
                    entry = genres.setdefault(name.lower(), [name, 0])
                    entry[1] += 1
        if genres:
            C.write_json(_genres_file(facets['key']), genres)
    content = 'episodes' if shape == 'landscape' else 'movies'
    if any(t.get('kind') == 'folder' for t in tiles) and not any(t.get('kind') == 'work' for t in tiles):
        content = 'videos'
    # the Videos window clicks its items itself (items.kodi_click)
    I.directory(handle, items, content=content, props=props, kodi_clicks=True)
    if todo:
        try:
            from . import gridfocus
            gridfocus.want(todo)
        except Exception:
            pass
    return None


def _page(api, params, shape):
    if params.get('action') == 'tmdb_titles' and params.get('mode') == 'collection':
        from .title_extras import collection_tiles
        return collection_tiles(params.get('id') or ''), None
    from ..homeui import rows as R
    try:
        tiles, more = R.capture_tiles(params, api, shape or 'poster', works_only=False, limit=200)
    except Exception as exc:
        C.log('grid page failed: %s' % exc, xbmc.LOGWARNING)
        return [], None
    return tiles, more


def _facet_props(facets, state, app):
    from ..homeui import grid_filters as GF
    tr = app.tr
    genre = state.get('genre') or {}
    chosen_genre = str(genre.get('label') or genre.get('value') or '')
    sort = state.get('sort') or {}
    chosen_sort = tr(str(sort.get('label') or sort.get('value') or ''))
    active = [f for f in facets['filters'] if (state.get('filters') or {}).get(f['name'])]
    if active:
        first = state['filters'][active[0]['name']]
        text = tr(str(first.get('label') or first.get('value') or ''))
        if len(active) > 1:
            text = '%s  +%d' % (text, len(active) - 1)
    else:
        text = tr('الكل')
    return {
        'dhs.facets': '1',
        'dhs.sort': chosen_sort or tr('ترتيب المصدر'), 'dhs.sort.on': '1' if chosen_sort else '',
        'dhs.genre': chosen_genre or tr('الكل'), 'dhs.genre.on': '1' if chosen_genre else '',
        'dhs.filter': text, 'dhs.filter.on': '1' if active else '',
        'dhs.filters': GF.summary(facets, state, tr),
    }


# v5.10.448: the folder view, after Nuvio's "tabs over a grid": the
# collection's catalogs are tabs (this listing, part=tabs), the titles of the
# tab the cursor rests on fill the row under them (folderpage.publish_pick
# puts that catalog in the page's first row slot), and the focused title (or
# catalog) fills the top of the page. A tab keeps what it shows in
# fold_art.json once found (a listing no longer opens every catalog's cached
# row), asked at the size it is shown (a 4K "original" backdrop was the
# slowest part of the page on a TV box).
KINDS = {
    # kind: (tint, Font Awesome glyph)
    'all': ('FF6E8BFF', '\uf5fd'),        # layer group
    'movie': ('FFE8A33D', '\uf008'),      # film
    'series': ('FF35BFA8', '\uf26c'),     # tv
    '': ('FF8A94A6', '\uf03a'),           # list
}
_TOP = re.compile(r'\btop[ ._-]?(?:10|ten)\b|أفضل\s*10|توب\s*10', re.I)


def _art_index():
    import os
    return os.path.join(C.folder(), 'fold_art.json')


def _catalog_kinds(folder_data, specs):
    """'all', 'movie', 'series' or '' for each catalog."""
    sources = ((folder_data or {}).get('payload') or {}).get('sources') or []
    out = []
    for key, name, _sub, _route in specs:
        index = str(key).rpartition(':')[2]
        if index == 'all':
            out.append('all')
            continue
        try:
            source = sources[int(index)] or {}
        except Exception:
            source = {}
        kind = str(source.get('mediaType') or source.get('catalogType') or source.get('type') or '').lower()
        if kind in ('series', 'tv', 'show', 'shows', 'tvshow', 'anime'):
            out.append('series')
        elif kind in ('movie', 'movies', 'film'):
            out.append('movie')
        else:
            low = str(name or '').lower()
            out.append('series' if any(w in low for w in ('series', 'show', ' tv', 'مسلسل')) else
                       'movie' if any(w in low for w in ('movie', 'film', 'فيلم', 'أفلام')) else '')
    return out


def _bare(url):
    return str(url or '').split('?', 1)[0].rstrip('/')


def _catalog_art(ref, specs, wanted=None, avoid=()):
    """What each catalog's tab shows, {'a': backdrop, 'n': titles read}
    (wanted: the indexes to look up; the others are {} unless known). The
    backdrop: a cover chosen for the catalog (cover_overrides) first, then
    the one kept for it, then the first picture its row read that no
    catalog before it uses. avoid: the collection's own pictures (a Trakt
    row's titles without a backdrop carry the collection's)."""
    from .folderpage import TAB, _specs
    try:
        from ..cover_overrides import catalog_cover
    except Exception:
        catalog_cover = None
    index_path = _art_index()
    known = C.read_json(index_path, {}) or {}
    avoid = set(_bare(u) for u in avoid if u)
    for key, value in list(known.items()):
        value = value if isinstance(value, dict) else {'a': value}      # 5.10.448 first kept the picture only
        if _bare(value.get('a')) in avoid:
            value = dict(value, a='')
        known[key] = value
    used = set(v.get('a') for v in known.values() if v.get('a'))
    found, added = [], False
    for n, spec in enumerate(_specs(ref, specs)):
        index = str(spec.get('key') or '').rpartition(':')[2]     # fold:src:<n> / fold:src:all
        entry = dict(known.get(spec['id']) or {})
        if (wanted is None or n in wanted) and 'n' not in entry:
            try:
                data = C.read_row(C.row_file(TAB, spec['id'])) or {}
            except Exception:
                data = {}
            items = data.get('items') or []
            if items:
                if not entry.get('a'):
                    for item in items[:12]:
                        pics = item.get('art') or {}
                        pic = str(pics.get('fanart') or pics.get('landscape') or '')
                        if pic and pic not in used and _bare(pic) not in avoid:
                            entry['a'] = pic
                            used.add(pic)
                            break
                entry['n'] = len(items)
                known[spec['id']] = entry
                added = True
        cover = catalog_cover(ref.get('set_id'), ref.get('folder_id'), index) if catalog_cover else ''
        if cover:
            entry = dict(entry, a=cover)
        found.append(entry)
    if added:
        if len(known) > 600:
            known = dict(list(known.items())[-400:])
        C.write_json(index_path, known)
    return found


def folder(params, handle):
    """A collection folder's catalogs as cards ("All" first when it has it)."""
    from . import rows
    from . import items as I
    from ..homeui import window as W
    app = rows.app()
    home = rows.HeadlessHome(app, 'all')
    ref = {'set_id': params.get('set_id') or '', 'group_id': params.get('group_id') or '',
           'folder_id': params.get('folder_id') or '', 'media_filter': params.get('media_filter') or ''}
    fn = W.BrowseWindow.__dict__['_folder_sources']
    folder_data, specs = fn(home, ref)
    title = (folder_data or {}).get('name') or params.get('t') or ''
    wrap = getattr(rows.api(), '_wrap_tokenized_url', None) or (lambda u: u)
    cover = ''
    background = ''
    if folder_data:
        cover = wrap(str(folder_data.get('background') or folder_data.get('poster') or ''))
        background = wrap(str(folder_data.get('background') or folder_data.get('poster') or ''))
    logo = wrap(str((folder_data or {}).get('clearlogo') or ((folder_data or {}).get('poster') if (folder_data or {}).get('hide_title') else '') or ''))
    items = []
    inline = params.get('inline') == '1'
    part = (params.get('part') or '') if inline else ''
    want = params.get('k') or ''                 # the tabs' kind filter (All / Movies / Series)
    kinds = _catalog_kinds(folder_data, specs)
    avoid = [(folder_data or {}).get(k) for k in ('background', 'poster')] + [cover, background]
    shown = _catalog_art(ref, specs, None, avoid) if inline else [{}] * len(specs)
    from ..image_policy import cap_url
    kind_labels = {'all': app.tr('الكل'), 'movie': app.tr('أفلام'), 'series': app.tr('مسلسلات'), '': app.tr('كتالوج')}
    from ..cover_overrides import folder_key, catalog_key
    cover_menu = []
    if ref.get('folder_id'):
        cover_menu.append((app.tr('غلاف المجلد…'), 'RunPlugin(%s)' % C.url(
            'skin_cover_pick', kind='folder', name=title,
            # a top-level folder is the collection's entry itself (no group)
            key=folder_key(ref['set_id'], ref.get('group_id'), ref['folder_id']) if ref.get('group_id')
            else folder_key(ref['set_id'], ref['folder_id']))))
    for n, ((key, name, subtitle, route), entry, kind) in enumerate(zip(specs, shown, kinds)):
        if part == 'tabs' and want and kind != want:
            continue
        target = rows.list_url(route, title='%s  •  %s' % (title, name) if title else name)
        art = entry.get('a') or ''
        card = cap_url(art, 'w780') if art else cover
        index = str(key).rpartition(':')[2]
        tint, glyph = KINDS.get(kind) or KINDS['']
        menu = [(app.tr('غلاف الكتالوج…'), 'RunPlugin(%s)' % C.url(
            'skin_cover_pick', kind='catalog', name=name,
            key=catalog_key(ref['set_id'], ref['folder_id'], index)))] + cover_menu
        count = (app.tr('%d عنوان') % entry['n']) if entry.get('n') else ''
        items.append({
            'menu': menu,
            'label': name, 'label2': subtitle, 'path': target, 'folder': True,
            'art': {'poster': cover, 'landscape': card, 'thumb': card,
                    'fanart': cap_url(art, 'w1280') if art else background, 'clearlogo': ''},
            'info': {'title': name, 'mediatype': 'video', 'plot': ''},
            'props': {'kind': 'nav', 'subtitle': subtitle, 'dhs.click': 'grid', 'dhs.target': target,
                      'dhs.badge': app.tr('كتالوج'), 'dhs.spec': str(n),
                      'dhs.meta': '  ·  '.join(p for p in (subtitle, count) if p),
                      'dhs.top10': '1' if _TOP.search(name or '') else '',
                      'dhs.ckind': kind or 'other', 'dhs.ckind_label': kind_labels.get(kind, ''),
                      'dhs.tint': tint, 'dhs.glyph': glyph},
        })
    row = params.get('row') or ''
    if inline:
        # the folder view's tabs (an older skin's card lists get nothing)
        if part != 'tabs':
            items = []
        I.directory(handle, items, content='videos')
        return None
    if row:
        # the last row of a collection folder's page (folderpage.py)
        I.directory(handle, items, tab=row.split(':', 1)[0], content='videos', row=row)
        raise SystemExit        # a row of a page, like the Home's (serve.widget)
    data = C.read_json(C.spec_file('fold'), {}) or {}
    if data.get('ref') != ref:
        C.write_json(C.spec_file('fold'), {'ref': ref, 'title': title})
    for item in items:
        item['menu'] = list(item.get('menu') or []) + [(app.tr('طريقة فتح الكوليكشن'), 'RunPlugin(%s)' % C.url('skin_folder_view'))]
    I.directory(handle, items, content='videos',
                props={'dhs.grid': '1', 'dhs.title': title, 'dhs.title_logo': logo, 'dhs.shape': 'landscape'})
    return None


class _FacetPicker(object):
    """homeui GridWindow's Sort, Genre and Filter dialogs, without the grid."""

    def __init__(self, app, facets, state, title, genres):
        self.app = app
        self.s = app.settings
        self._facets = facets
        self._fstate = state
        self.title = title
        self._seen_genres = genres
        self._lock = threading.RLock()
        self._loading = False
        self._bar_key = ''
        self.picked = None

    def tr(self, text):
        return self.app.tr(text)

    def prop(self, *_args):
        pass

    def notify(self, message):
        notify(message)

    def _set_state(self, state, save=True, params=None):
        self.picked = state


def _bind_picker():
    if getattr(_FacetPicker, '_bound', False):
        return
    from ..homeui import window as W
    for name in ('_choose_sort', '_choose_genre', '_choose_filter', '_filter_hint', '_genre_entries',
                 '_server_genres'):
        setattr(_FacetPicker, name, W.GridWindow.__dict__[name])
    for name in ('tmdb_settings', '_tmdb_language'):
        setattr(_FacetPicker, name, W._BaseWindow.__dict__[name])
    _FacetPicker._bound = True


def facet(params, handle):
    from . import rows
    from ..homeui import grid_filters as GF
    which = params.get('which') or 'sort'
    path = xbmc.getInfoLabel('Container.FolderPath') or ''
    query = dict(parse_qsl(urlsplit(path).query, keep_blank_values=True))
    if query.get('action') != 'skin_list':
        return None
    route = dict(parse_qsl(query.get('r') or '', keep_blank_values=True))
    app = rows.app()
    facets = GF.describe(route, app) or GF.describe_local(route)
    if facets is None:
        return None
    saved = GF.native_state(facets, GF.load_state(app.profile, facets['key']))
    state = saved if not GF.is_empty(saved) else (facets.get('preset') or GF.empty_state())
    genres = C.read_json(_genres_file(facets['key']), {}) or {}
    _bind_picker()
    picker = _FacetPicker(app, facets, state, query.get('t') or '', genres)
    {'sort': picker._choose_sort, 'genre': picker._choose_genre,
     'filter': picker._choose_filter}.get(which, picker._choose_sort)()
    if picker.picked is None:
        return None
    GF.save_state(app.profile, facets['key'], picker.picked)
    xbmc.executebuiltin('Container.Refresh')
    return None


def row_facet(params, handle):
    from . import rows
    from ..homeui import catalog_controls as CC
    tab = params.get('m') or C.prop('dhs.tab') or 'all'
    try:
        slot = int(params.get('slot') or 1)
    except ValueError:
        return
    row_id = params.get('id') or C.prop('dhs.%s.%d.id' % (tab, slot))
    spec = rows.find_spec(tab, row_id)
    if not spec or spec.get('own') or spec.get('progress'):
        return
    route = spec.get('params') or {}
    cached = C.read_json(C.row_file(tab, row_id)) or {}
    if CC.choose(route, rows.app(), spec.get('title') or '', cached.get('items') or []):
        if cached:
            cached.update({'choice_changed': True, 'fails': 0, 't': 0})
            C.write_json(C.row_file(tab, row_id), cached)
        C.request_refresh(tab, row_id)
        # Choices are already saved; the service refreshes without holding the UI.
        if tab == 'fold':
            xbmc.executebuiltin('SetFocus(%d)' % (7000 + slot))
        elif C.skin_active() and xbmc.getCondVisibility('Window.IsActive(home)'):
            xbmc.executebuiltin('SetFocus(%d)' % (5000 + slot))


_PLAIN_STYLES = ('poster', 'landscape', 'square', 'wide', 'tall', 'poster_large', 'feature', 'landscape_info', 'circle')


def row_shape(params, handle):
    """How one Home row draws its cards (v5.10.173): big banner with the
    logo, banner, poster, square, or the row's own."""
    from . import rows
    tab = params.get('m') or C.prop('dhs.tab') or 'all'
    row_id = params.get('id') or ''
    spec = rows.find_spec(tab, row_id)
    if not spec or not spec.get('key'):
        return
    a = rows.app()
    current = a.layout.shape(spec['key'])
    from ..homeui.layout import pick_style, style_choices_for, suggested_styles
    progress = bool(spec.get('own') or spec.get('progress'))
    key = pick_style(a.tr, a.media_path, current, spec.get('title') or '',
                     only=style_choices_for(shelf=bool(spec.get('shelf')), progress=progress),
                     suggested=suggested_styles(spec.get('title') or '', collection=spec.get('kind') == 'collection',
                                                progress=progress))
    if key is None or key == current:
        return
    a.layout.set_shape(spec['key'], key)
    # v5.10.179: the rows are published again with the new style (a row's
    # shape, a collection row's look) at once
    C.ask_republish('row style')
    if key in _PLAIN_STYLES and not spec.get('shelf') and not spec.get('walls') and spec.get('kind') != 'collection':
        # v5.10.188: a plain style shows at once on the row as it is (the
        # service's publish follows within a second with the row drawn again)
        from . import board
        slot = board.slot_of(tab, row_id)
        if slot:
            C.set_prop('dhs.%s.%d.shape' % (tab, slot), board._FAR.get(key, key) if slot > 10 else key)
    cached = C.read_json(C.row_file(tab, row_id))
    if cached:
        # drawn again in the new shape; a big banner row looks its logos up
        cached.update({'enriched': False, 't': 0})
        if not key:
            # v5.10.188: back to the automatic style: the copy's shape (a
            # collage, say) no longer decides the row's (it kept it)
            cached.pop('shape', None)
            cached.pop('look', None)
        C.write_json(C.row_file(tab, row_id), cached)
    C.request_refresh(tab, row_id)


def row_follow(params, handle):
    from . import rows
    from .. import row_follow as policy
    tab = params.get('m') or C.prop('dhs.tab') or 'all'
    spec = rows.find_spec(tab, params.get('id') or '')
    if spec and spec.get('key'):
        a = rows.app()
        policy.edit(tab, spec['key'], spec.get('title') or '', a.tr)


def trailer_config(params, handle):
    from ..trailer_preferences import publish_presentation
    publish_presentation()


def _social(name):
    # v5.10.406: the title page's Reviews and Pictures rows (title_social)
    def run(params, handle):
        from . import title_social
        return getattr(title_social, name)(params, handle)
    return run


ROUTES = {
    'skin_review_read': _social('review_read'),
    'skin_review_write': _social('review_write'),
    'skin_art_set': _social('art_set'),
    'skin_trailer_config': trailer_config,
    'skin_row_shape': row_shape,
    'skin_row_follow': row_follow,
    'skin_publish': publish,
    'skin_refresh': refresh_home,
    'skin_menu': menu,
    'skin_focus': focus_colour,
    'skin_play': play,
    'skin_sources': sources,
    'skin_warm_sources': warm_sources,
    'skin_opengrid': open_grid,
    'skin_panel': lambda params, handle: _panel('listing', params, handle),
    'skin_alert_readall': lambda params, handle: _panel('read_all', params, handle),
    'skin_alert_open': lambda params, handle: _panel('open_alert', params, handle),
    'skin_panel_counts': lambda params, handle: _panel('counts', params, handle),
    'skin_watch_sources': lambda params, handle: __import__('resources.lib.watch_sources', fromlist=['menu']).menu(params, handle),
    'skin_panel_refresh': lambda params, handle: _panel('refresh', params, handle),
    'skin_trailer': trailer,
    'skin_fav': favourite,
    'skin_track': track,
    'skin_more': more,
    'skin_open': open_title,
    'skin_title': lambda params, handle: _dispatch_title(params, handle),
    'skin_list': grid,
    'skin_folder': folder,
    'skin_fold': lambda params, handle: _fold(params, handle),
    'skin_facet': facet,
    'skin_row_facet': row_facet,
    'skin_profile': lambda params, handle: _profile(params, handle),
}


def _panel(name, params, handle):
    # v5.10.313: the title page's island (panel.py)
    from . import panel
    return getattr(panel, name)(params, handle)


def _fold(params, handle):
    from ..folder_view import rows_enabled
    if rows_enabled():
        from .folderpage import open_page
        return open_page(params, handle)
    from ..catalog_navigation import open_selector
    return open_selector(params, handle)


def _profile(params, handle):
    """The profile button of the Home (v5.10.114)."""
    from . import profile
    return profile.switch(params, handle)


def _dispatch_title(params, handle):
    from .title_route import open_title
    return open_title(params, handle)
