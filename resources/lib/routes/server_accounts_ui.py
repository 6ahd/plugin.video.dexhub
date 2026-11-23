# -*- coding: utf-8 -*-
"""The accounts of a media server service: the main one, the extra ones, and "Add another
account" (v5.10.605, account_slots.py).

  server_accounts&service=plex|emby|jellyfin|silo   the list (a choice menu)
  server_account_add&service=...                    the service's own sign-in, for a new slot

Adding runs the very sign-in the service always had (Plex's QR, Silo's QR or password and
profile, Emby's and Jellyfin's address and user) while a new slot is in use, so what it
saves becomes the new account and the main one is left as it is. A sign-in that was
cancelled leaves no slot behind.
"""
import xbmc
import xbmcgui

from .. import account_slots

NAMES = {'plex': 'Plex', 'emby': 'Emby', 'jellyfin': 'Jellyfin', 'silo': 'Silo'}


def _txt(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _signed_in(api, service):
    try:
        if service == 'plex':
            from .. import plex_client
            return bool(plex_client.account().get('token'))
        if service == 'silo':
            from ..providers import silo_provider
            return bool(silo_provider.is_signed_in())
        from .. import emby_client
        return bool(emby_client.is_signed_in(service))
    except Exception as exc:
        xbmc.log('[DexHub] accounts: %s sign-in state unread (%s)' % (service, exc), xbmc.LOGWARNING)
        return False


def _main_auth(api, service):
    try:
        if service == 'plex':
            from .. import plex_client
            return plex_client.account() or {}
        if service == 'silo':
            from ..providers import silo_provider
            return silo_provider.account() or {}
        from .. import emby_client
        return emby_client.account(service) or {}
    except Exception:
        return {}


def _login(api, service):
    if service == 'plex':
        return api.plex_login()
    if service == 'emby':
        return api.emby_login()
    if service == 'jellyfin':
        from . import jellyfin
        return jellyfin.run(api, 'jellyfin_login', {})
    if service == 'silo':
        way = _choose('Silo', [_txt('عبر QR / المتصفح', 'With a QR code / the browser'),
                               _txt('الدخول اليدوي', 'Username and password')])
        if way < 0:
            return None
        return api.silo_qr_login() if way == 0 else api.silo_login()
    return None


def _choose(title, labels, subtitle=''):
    try:
        from ..homeui.options import choose
        return choose(title, labels, subtitle=subtitle)
    except Exception:
        return xbmcgui.Dialog().select(title, labels)


def _changed(api, service):
    try:
        api._servers_changed(service)
    except Exception:
        pass
    if service == 'plex':
        try:
            api.invalidate_plex_servers_memo()
        except Exception:
            pass


def add(api, service):
    if service not in NAMES:
        return
    if not _signed_in(api, service):
        # no account yet: this is the main one
        return _login(api, service)
    slot = account_slots.new_slot()
    before = len(account_slots.ids(service))
    try:
        with account_slots.use(service, slot):
            _login(api, service)
    finally:
        account_slots.drop_unlinked(service)
    if len(account_slots.ids(service)) > before:
        xbmc.log('[DexHub] %s: another account added (%d in all)'
                 % (NAMES[service], len(account_slots.ids(service)) + 1), xbmc.LOGINFO)
        _changed(api, service)
        try:
            xbmc.executebuiltin('Container.Refresh')
        except Exception:
            pass


def _remove_main(api, service):
    if service == 'plex':
        api.plex_client.sign_out()
    elif service == 'silo':
        api.silo_client.sign_out()
    else:
        api.emby_client.sign_out(service)


def manage(api, service):
    if service not in NAMES:
        return
    name = NAMES[service]
    while True:
        rows, labels = [], []
        if _signed_in(api, service):
            rows.append(('main', ''))
            labels.append((account_slots.label(service, _main_auth(api, service)), _txt('الحساب الأساسي', 'Main account')))
        for row in account_slots.slots(service):
            rows.append(('extra', row['slot']))
            labels.append((account_slots.label(service, row.get('auth')), _txt('حساب إضافي', 'Extra account')))
        rows.append(('add', ''))
        labels.append((_txt('+ إضافة حساب آخر', '+ Add another account') if len(rows) > 1
                       else _txt('+ ربط حساب', '+ Connect an account'),
                       _txt('مكتباته ونسخه تنضم للمصادر مع الباقي', 'Its libraries and copies join the others among the streams')))
        xbmc.log('[DexHub] accounts: %s main=%s extra=%d' % (service, rows[0][0] == 'main', len(account_slots.ids(service))), xbmc.LOGINFO)
        total = len(rows) - 1
        pick = _choose(name, labels, subtitle=_txt('%d حساب' % total, '%d account%s' % (total, '' if total == 1 else 's')))
        if pick < 0:
            return
        kind, slot = rows[pick]
        if kind == 'add':
            add(api, service)
            continue
        if _choose(labels[pick][0], [_txt('فصل هذا الحساب', 'Disconnect this account')]) != 0:
            continue
        if not xbmcgui.Dialog().yesno(name, _txt('فصل %s من Dex Hub؟', 'Disconnect %s from Dex Hub?') % labels[pick][0]):
            continue
        if kind == 'extra':
            account_slots.remove(service, slot)
        else:
            _remove_main(api, service)
        _changed(api, service)
        try:
            xbmc.executebuiltin('Container.Refresh')
        except Exception:
            pass


def count(service):
    """How many extra accounts a service has (for labels)."""
    try:
        return len(account_slots.ids(service))
    except Exception:
        return 0
