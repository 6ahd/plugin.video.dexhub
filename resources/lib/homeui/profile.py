# -*- coding: utf-8 -*-
"""The Nuvio profile behind the Dex Hub logo on the Home (v5.10.98).

The logo on the Home's top bar is a button: it shows the Nuvio profile in use
and switches it. Switching pulls the new profile's Home (collections, add-ons,
catalog order), its TMDb settings and its watch progress, the same read-only
pulls the Nuvio setup runs, and nothing is pushed.
"""
import xbmc
import xbmcgui


def _sync():
    from ..dexhub import nuvio_stremio_sync as sync
    return sync


def linked():
    try:
        return bool(_sync().Nuvio.is_linked())
    except Exception:
        return False


def current_name():
    """The profile name to show under the logo, or '' without a Nuvio account."""
    try:
        sync = _sync()
        if not sync.Nuvio.is_linked():
            return ''
        return sync.Nuvio.profile_name() or ''
    except Exception:
        return ''


def _row_index(row):
    try:
        return int(row.get('profile_index', row.get('profileIndex', row.get('index'))))
    except Exception:
        return None


def choose(tr):
    """Ask for a profile. Returns the new profile's name, or '' when nothing changed."""
    sync = _sync()
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        rows = sync.Nuvio.profiles() or []
    except Exception as exc:
        rows = None
        error = exc
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    if rows is None:
        # v5.10.120: a note, not a dialog to dismiss (Nuvio's 429 is passing)
        xbmcgui.Dialog().notification('Dex Hub', tr('تعذر قراءة بروفايلات Nuvio: %s') % error,
                                      xbmcgui.NOTIFICATION_WARNING, 5000)
        return ''
    if not rows:
        xbmcgui.Dialog().notification('Dex Hub', tr('لا توجد بروفايلات في حساب Nuvio'),
                                      xbmcgui.NOTIFICATION_INFO, 3000)
        return ''
    try:
        current = int(sync.Nuvio.token().get('profile_index'))
    except Exception:
        current = None
    preselect = next((idx for idx, row in enumerate(rows)
                      if current is not None and _row_index(row) == current), -1)
    # v5.10.162: Nuvio's own "Who's watching?" screen with the avatars (the
    # logo button on the skin's Home and on the add-on's Home come here)
    try:
        from . import profiles
        pick = profiles.choose(rows, current)
    except Exception as exc:
        xbmc.log('[DexHub] profile screen: %s' % exc.__class__.__name__, xbmc.LOGINFO)
        pick = None
    if pick is not None:
        if pick < 0 or pick == preselect:
            return ''
        if not sync.Nuvio.select_profile(rows[pick]):
            return ''
        try:
            from . import profiles
            avatar = profiles.avatar_of(rows[pick], profiles.avatar_catalog(sync))
            data = sync.Nuvio.token()
            if avatar and data.get('profile_avatar') != avatar:
                data['profile_avatar'] = avatar
                sync.Nuvio.save_token(data)
        except Exception:
            pass
        return sync.Nuvio.profile_name() or str(rows[pick].get('name') or '')
    items, preselect, has_art = [], -1, False
    for idx, row in enumerate(rows):
        name = str(row.get('name') or row.get('title') or ('Profile %d' % (idx + 1)))
        li = xbmcgui.ListItem(label=name)
        avatar = str(row.get('avatar_url') or row.get('avatar') or row.get('image') or '')
        if avatar.startswith('http'):
            li.setArt({'icon': avatar, 'thumb': avatar})
            has_art = True
        items.append(li)
        if current is not None and _row_index(row) == current:
            preselect = idx
    pick = xbmcgui.Dialog().select(tr('اختيار بروفايل Nuvio'), items, 0, preselect,
                                   useDetails=has_art)
    if pick < 0 or pick == preselect:
        return ''
    if not sync.Nuvio.select_profile(rows[pick]):
        return ''
    return sync.Nuvio.profile_name() or items[pick].getLabel()


def _on(addon, setting_id):
    try:
        return str(addon.getSetting(setting_id) or '').lower() in ('true', '1', 'yes', 'on')
    except Exception:
        return False


def pull(addon, log):
    """Read the new profile's data from Nuvio. Returns the parts that failed."""
    sync = _sync()
    failed = []
    try:
        sync.Nuvio.pull_profile_settings_blob('tv')
    except Exception as exc:
        log('profile settings pull failed: %s' % exc)
    if _on(addon, 'nuvio_sync_collections') or _on(addon, 'nuvio_sync_addons'):
        try:
            sync.pull_nuvio_home_to_local()
        except Exception as exc:
            log('profile home pull failed: %s' % exc)
            failed.append('Home')
    if _on(addon, 'nuvio_sync_progress') or _on(addon, 'nuvio_sync_library'):
        try:
            result = sync.run_sync(direction='pull', targets=['nuvio'], force_full=True,
                                   sections={'addons': False, 'collections': False,
                                             'progress': _on(addon, 'nuvio_sync_progress'),
                                             'library': _on(addon, 'nuvio_sync_library')})
            if result and not result.get('ok'):
                failed.append('progress')
        except Exception as exc:
            log('profile progress pull failed: %s' % exc)
            failed.append('progress')
    if failed:
        # v5.10.120: the service reads the whole profile once Nuvio answers again
        try:
            xbmcgui.Window(10000).setProperty('dexhub.sync.want_full', '1')
        except Exception:
            pass
    return failed
