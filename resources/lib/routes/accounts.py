# -*- coding: utf-8 -*-
"""Account and cloud-sync routes.

This module owns Nuvio/Stremio login/logout/sync UI so the main plugin router
no longer carries account state and dialog orchestration.
"""
from __future__ import absolute_import

# v5.10.102: an account sync running in this invocation, for the Accounts and
# sources screen (another invocation), which waits for it before the Home
# is built: "service|start time".
SYNC_PROP = 'dexhub.account_sync.running'


def _syncing(service=''):
    import time
    import xbmcgui
    try:
        home = xbmcgui.Window(10000)
        if service:
            home.setProperty(SYNC_PROP, '%s|%.0f' % (service, time.time()))
        else:
            home.clearProperty(SYNC_PROP)
    except Exception:
        pass


def qr_login(service):
    from resources.lib.qr_pair import qr_pair
    return qr_pair(service)


def login(service, addon, tr):
    if service == 'nuvio':
        return connect_nuvio(addon, tr)
    import xbmcgui
    from resources.lib.dexhub import nuvio_stremio_sync as sync
    dlg = xbmcgui.Dialog()
    email = (addon.getSetting('%s_email' % service) or '').strip()
    password = addon.getSetting('%s_password' % service) or ''
    if not email or not password:
        dlg.ok('Dex Hub', tr('اكتب البريد وكلمة المرور في الإعدادات أولاً.'))
        return False
    try:
        if service == 'nuvio':
            sync.Nuvio.login(email, password)
        else:
            sync.Stremio.login(email, password)
        try:
            addon.setSetting('%s_password' % service, '')
        except Exception:
            pass
        # v4.0.0: linking implies the user wants sync — enable the master
        # toggle so "Sync now" works right away instead of a dead end.
        try:
            addon.setSetting('%s_sync_enabled' % service, 'true')
        except Exception:
            pass
        dlg.notification('Dex Hub', tr('تم تسجيل الدخول ✓'), xbmcgui.NOTIFICATION_INFO, 4000)
        return True
    except Exception as exc:
        dlg.ok('Dex Hub', tr('فشل تسجيل الدخول:') + '\n' + str(exc))
        return False


def logout(service, tr):
    import xbmcgui
    from resources.lib.dexhub import nuvio_stremio_sync as sync
    if service == 'nuvio':
        sync.Nuvio.clear()
    else:
        sync.Stremio.clear()
    xbmcgui.Dialog().notification('Dex Hub', tr('تم تسجيل الخروج'), xbmcgui.NOTIFICATION_INFO, 3000)
    return True


def direction(addon):
    raw = addon.getSetting('cloud_sync_direction') or 'Two-way'
    if raw in ('Upload only', 'رفع فقط'):
        return 'push'
    if raw in ('Download only', 'سحب فقط'):
        return 'pull'
    return 'both'


def sync_options(addon, tr, setting):
    """Small front door for the adaptive account-sync policy."""
    import xbmcgui
    while True:
        try:
            interval = int(float(setting('cloud_sync_interval_min', '30') or '30'))
        except Exception:
            interval = 30
        continuous = (setting('cloud_sync_continuous', 'true') or 'true'
                      ).strip().lower() in ('true', '1', 'yes', 'on')
        if interval <= 0:
            mode = tr('موقوفة')
        elif continuous:
            mode = tr('مستمرة (فورية)')
        else:
            mode = tr('ذكية وخفيفة')
        rows = [
            '%s:  [B]%s[/B]' % (tr('وضع المزامنة'), mode),
            '%s:  [B]%s[/B]' % (
                tr('فترة السحب'),
                (tr('%d دقيقة') % interval) if interval > 0 else tr('موقوفة')),
            tr('مزامنة الآن'),
        ]
        choice = xbmcgui.Dialog().select(tr('إعدادات المزامنة'), rows)
        if choice < 0:
            return
        if choice == 0:
            # v5.4.1: continuous is selectable again. This dialog wrote
            # cloud_sync_continuous='false' on EVERY pass, so the setting
            # could never be turned on from the one screen built to
            # control it — and the service loop ignored it anyway.
            pick = xbmcgui.Dialog().select(
                tr('وضع المزامنة'),
                [tr('مستمرة (فورية)'), tr('ذكية وخفيفة'), tr('موقوفة')])
            if pick < 0:
                continue
            if pick == 2:
                addon.setSetting('cloud_sync_interval_min', '0')
            else:
                if interval <= 0:
                    addon.setSetting('cloud_sync_interval_min', '30')
                addon.setSetting('cloud_sync_continuous',
                                 'true' if pick == 0 else 'false')
        elif choice == 1:
            options = [5, 15, 30, 60, 120, 240]
            pick = xbmcgui.Dialog().select(
                tr('فترة السحب'), [tr('%d دقيقة') % n for n in options])
            if pick < 0:
                continue
            addon.setSetting('cloud_sync_interval_min', str(options[pick]))
        else:
            return sync_now(addon, tr, only=None)
        try:
            from resources.lib import settings_cache
            settings_cache.invalidate()
        except Exception:
            pass



def pull_nuvio_collections(addon, tr):
    """Refresh the complete Nuvio Home mirror. Never pushes."""
    import xbmc
    import xbmcgui
    from resources.lib.dexhub import nuvio_stremio_sync as sync
    if not sync.Nuvio.is_linked():
        xbmcgui.Dialog().ok('Dex Hub', tr('اربط حساب Nuvio أولاً.'))
        return False
    pd = xbmcgui.DialogProgressBG()
    pd.create('Dex Hub', tr('تحديث صفحة Nuvio…'))
    try:
        pd.update(15, tr('سحب ترتيب هوم Nuvio…'))
        result = sync.pull_nuvio_home_to_local()
        pd.update(90, tr('حفظ مرآة Nuvio…'))
    except Exception as exc:
        xbmcgui.Dialog().ok('Dex Hub', tr('فشل تحديث صفحة Nuvio: %s') % exc)
        return False
    finally:
        pd.close()
    if result.get('empty'):
        xbmcgui.Dialog().notification('Dex Hub', tr('لا توجد بيانات هوم محفوظة في Nuvio'),
                                      xbmcgui.NOTIFICATION_INFO, 4500)
        return True
    try:
        addon.setSetting('nuvio_sync_collections', 'true')
        addon.setSetting('nuvio_sync_addons', 'true')
    except Exception:
        pass
    xbmcgui.Dialog().notification(
        'Dex Hub', tr('تم تحديث Nuvio Home • %d صف • %d كوليكشن') % (
            int(result.get('home_rows') or 0), int(result.get('collections') or 0)),
        xbmcgui.NOTIFICATION_INFO, 5500)
    try:
        xbmc.executebuiltin('Container.Refresh')
    except Exception:
        pass
    return True

def sync_now(addon, tr, only=None, direction=None):
    """direction 'pull' (v5.10.102): the first sync after a link from the
    Accounts and sources screen only brings the account in."""
    import xbmcgui
    from resources.lib.dexhub import nuvio_stremio_sync as sync
    dlg = xbmcgui.Dialog()
    targets = sync.enabled_targets()
    if only:
        targets = [t for t in targets if t == only]
        if not targets:
            dlg.ok('Dex Hub', tr('لا يوجد حساب مفعّل لهذه الخدمة.'))
            return False
    if not targets:
        dlg.ok('Dex Hub', tr('فعّل حساب Nuvio أو Stremio وسجّل الدخول أولاً.'))
        return False
    pd = xbmcgui.DialogProgressBG()
    pd.create('Dex Hub', tr('مزامنة الحسابات…'))
    _syncing(only or ','.join(targets))
    try:
        pd.update(15, tr('تحضير المزامنة…'))
        # v4.0.0: direction and sections resolve per service from settings.
        result = sync.run_sync(direction=direction, targets=targets, force_full=bool(direction))
        pd.update(95, tr('حفظ النتائج…'))
    finally:
        pd.close()
        _syncing()
    if not result or not result.get('ok'):
        errors = []
        for row in (result or {}).get('report', []):
            for name, section in (row.get('sections') or {}).items():
                if not section.get('ok'):
                    errors.append('%s/%s: %s' % (row.get('service'), name, section.get('error', '')))
        dlg.ok('Dex Hub', tr('فشلت المزامنة:') + '\n' + ('\n'.join(errors) or str((result or {}).get('error', ''))))
        return False
    ok_services = [r['service'] for r in result.get('report', []) if r.get('ok')]
    wb = result.get('writeback', {})
    msg = tr('تمت المزامنة')
    if ok_services:
        msg += ' • ' + ', '.join(ok_services)
    msg += ' • ' + tr('+%d إضافة، %d كوليكشن، %d متابعة') % (
        wb.get('providers', 0), wb.get('collections', 0), wb.get('progress', 0))
    dlg.notification('Dex Hub', msg, xbmcgui.NOTIFICATION_INFO, 6000)
    errors = []
    for row in result.get('report', []):
        for name, section in (row.get('sections') or {}).items():
            if not section.get('ok'):
                errors.append('%s/%s: %s' % (row.get('service'), name, section.get('error', '')))
    if errors:
        dlg.ok('Dex Hub • Sync details', '\n'.join(errors))
    return True


def _apply_nuvio_selection(addon, selected):
    """Persist the simplified Nuvio choices.

    `home` is one user-facing switch but internally means both add-ons and
    Collections, because both are required to reproduce Nuvio Home exactly.
    """
    picked = set(selected or [])
    home = 'home' in picked
    values = {
        'nuvio_sync_enabled': 'true',
        'nuvio_sync_collections': 'true' if home else 'false',
        'nuvio_sync_addons': 'true' if home else 'false',
        'nuvio_sync_progress': 'true' if 'progress' in picked else 'false',
        'nuvio_sync_library': 'true' if 'library' in picked else 'false',
        'nuvio_sync_direction': 'Two-way',
    }
    for key, value in values.items():
        try:
            addon.setSetting(key, value)
        except Exception:
            pass
    try:
        if not (addon.getSetting('cloud_sync_interval_min') or '').strip():
            addon.setSetting('cloud_sync_interval_min', '30')
        # Home itself is cache-first and refreshes stale snapshots when opened.
        # Keep the always-on poller off for a Home-only setup; enable it only
        # when the user selected dynamic progress/library synchronisation.
        continuous = ('progress' in picked or 'library' in picked)
        addon.setSetting('cloud_sync_continuous', 'true' if continuous else 'false')
    except Exception:
        pass
    try:
        from resources.lib import settings_cache
        settings_cache.invalidate()
    except Exception:
        pass
    return values


def nuvio_profile_picker(addon, tr, force=False):
    import xbmcgui
    from resources.lib.dexhub import nuvio_stremio_sync as sync
    if not sync.Nuvio.is_linked():
        xbmcgui.Dialog().ok('Dex Hub', tr('اربط حساب Nuvio أولاً.'))
        return False
    try:
        rows = sync.Nuvio.profiles(fresh=bool(force))
    except Exception as exc:
        xbmcgui.Dialog().notification('Dex Hub', tr('تعذر قراءة بروفايلات Nuvio: %s') % exc,
                                      xbmcgui.NOTIFICATION_WARNING, 5000)
        return False
    if not rows:
        return True
    current = sync.Nuvio.token().get('profile_index')
    if len(rows) == 1 and not force:
        sync.Nuvio.select_profile(rows[0])
        return True
    # v5.10.162: Nuvio's own "Who's watching?" screen with the avatars
    try:
        from resources.lib.homeui import profiles
        try:
            current_index = int(current)
        except (TypeError, ValueError):
            current_index = None
        pick = profiles.choose(rows, current_index)
    except Exception as exc:
        import xbmc
        xbmc.log('[DexHub] profile screen: %s' % exc.__class__.__name__, xbmc.LOGINFO)
        pick = None
    if pick is None:
        labels = []
        preselect = -1
        for idx, row in enumerate(rows):
            name = str(row.get('name') or row.get('title') or ('Profile %d' % (idx + 1)))
            labels.append(name)
            try:
                if int(row.get('profile_index', row.get('profileIndex', -999))) == int(current):
                    preselect = idx
            except Exception:
                pass
        pick = xbmcgui.Dialog().select(tr('اختيار بروفايل Nuvio'), labels, 0, preselect)
    if pick < 0:
        return False
    done = bool(sync.Nuvio.select_profile(rows[pick]))
    if done:
        try:
            from resources.lib.homeui import profiles
            avatar = profiles.avatar_of(rows[pick], profiles.avatar_catalog(sync))
            data = sync.Nuvio.token()
            if avatar and data.get('profile_avatar') != avatar:
                data['profile_avatar'] = avatar
                sync.Nuvio.save_token(data)
        except Exception:
            pass
    return done


def configure_nuvio(addon, tr, initial=False, run_initial_sync=True):
    """App-like Nuvio setup: profile → choose data → one safe initial pull."""
    import xbmcgui
    from resources.lib.dexhub import nuvio_stremio_sync as sync
    if not sync.Nuvio.is_linked():
        xbmcgui.Dialog().ok('Dex Hub', tr('اربط حساب Nuvio أولاً.'))
        return False

    # On first link show profile choice only when useful; later the explicit
    # profile action always lets the user change it.
    if not nuvio_profile_picker(addon, tr, force=False):
        if initial:
            return False

    labels = [
        tr('Nuvio Home — نفس الكتالوجات والكوليكشن والترتيب'),
        tr('المشاهدة — Continue Watching والتقدم'),
        tr('المكتبة والمفضلة — مزامنة محفوظاتك'),
    ]
    keys = ['home', 'progress', 'library']
    defaults = []
    for i, key in enumerate(keys):
        setting_id = {
            'home': 'nuvio_sync_collections',
            'progress': 'nuvio_sync_progress',
            'library': 'nuvio_sync_library',
        }[key]
        raw = str(addon.getSetting(setting_id) or '').lower()
        if initial or raw in ('', 'true', '1', 'yes', 'on'):
            defaults.append(i)
    selected_idx = xbmcgui.Dialog().multiselect(
        tr('اختر ما تريد إضافته ومزامنته من Nuvio'), labels, 0, defaults)
    if selected_idx is None:
        return False
    selected = [keys[i] for i in selected_idx]
    _apply_nuvio_selection(addon, selected)

    if not run_initial_sync:
        xbmcgui.Dialog().notification('Dex Hub', tr('تم حفظ إعداد Nuvio'),
                                      xbmcgui.NOTIFICATION_INFO, 3500)
        return True

    # First setup should behave like "install from my Nuvio account", not
    # push the current Kodi state before the user has even seen the result.
    pd = xbmcgui.DialogProgressBG()
    pd.create('Dex Hub', tr('تجهيز Nuvio…'))
    _syncing('nuvio')
    errors = []
    counts = {'collections': 0, 'providers': 0, 'progress': 0, 'library': 0}
    try:
        if 'home' in selected:
            try:
                pd.update(20, tr('سحب Nuvio Home…'))
                r = sync.pull_nuvio_home_to_local()
                counts['collections'] = int((r or {}).get('collections') or 0)
                counts['providers'] = int((r or {}).get('providers') or 0)
            except Exception as exc:
                errors.append('Home: %s' % exc)
        other = {
            'addons': False,
            'collections': False,
            'progress': 'progress' in selected,
            'library': 'library' in selected,
        }
        if any(other.values()):
            try:
                pd.update(50, tr('سحب بيانات Nuvio المختارة…'))
                result = sync.run_sync(direction='pull', targets=['nuvio'],
                                       force_full=True, sections=other)
                wb = (result or {}).get('writeback') or {}
                counts['providers'] = int(wb.get('providers') or 0)
                counts['progress'] = int(wb.get('progress') or 0)
                counts['library'] = int(wb.get('library') or 0)
                if result and not result.get('ok'):
                    errors.append(str(result.get('error') or 'sync failed'))
            except Exception as exc:
                errors.append(str(exc))
        pd.update(95, tr('حفظ إعداد Nuvio…'))
    finally:
        pd.close()
        _syncing()

    if errors:
        xbmcgui.Dialog().ok('Dex Hub', tr('تم ربط Nuvio، لكن بعض عناصر المزامنة لم تكتمل:') +
                            '\n' + '\n'.join(errors[:5]))
    else:
        xbmcgui.Dialog().notification(
            'Dex Hub', tr('تم إعداد Nuvio ومزامنة اختياراتك ✓'),
            xbmcgui.NOTIFICATION_INFO, 5000)
    return True


def connect_nuvio(addon, tr):
    """Pair through nuvio.tv, then run the one-screen Nuvio setup."""
    import xbmcgui
    from resources.lib.nuvio_tv_pair import pair
    from resources.lib.dexhub import nuvio_stremio_sync as sync
    if sync.Nuvio.is_linked():
        return configure_nuvio(addon, tr, initial=False, run_initial_sync=False)
    if not pair():
        return False
    try:
        addon.setSetting('nuvio_sync_enabled', 'true')
    except Exception:
        pass
    ok = configure_nuvio(addon, tr, initial=True, run_initial_sync=True)
    if not ok:
        # Pairing succeeded even if the user cancelled the optional selection.
        xbmcgui.Dialog().notification('Dex Hub', tr('تم ربط Nuvio ✓'),
                                      xbmcgui.NOTIFICATION_INFO, 3500)
    return True
