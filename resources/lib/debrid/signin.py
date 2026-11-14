# -*- coding: utf-8 -*-
"""Linking a Debrid account from the TV: a QR, a short code, and the phone does the rest.

Real-Debrid, AllDebrid and TorBox: their device sign-in (scan, approve, done;
Dex Hub waits by itself). Premiumize has none: its key is typed once (or
pasted from the phone page).
"""
import time

import xbmc
import xbmcgui

from . import accounts, services

try:
    from ..i18n import is_english
except Exception:
    def is_english():
        return False


def _t(ar, en):
    try:
        return en if is_english() else ar
    except Exception:
        return ar


def _notify(text, icon=xbmcgui.NOTIFICATION_INFO):
    xbmcgui.Dialog().notification('Dex Hub', text, icon, 3500)


def _status_line(service):
    account = accounts.get(service.id)
    if account.get('token'):
        who = account.get('user') or ''
        return _t('مربوط ✓', 'Linked ✓') + (' · %s' % who if who else '')
    return _t('غير مربوط', 'Not linked')


def menu():
    """The Debrid page: every service with its state; pick one to link it or manage it."""
    dialog = xbmcgui.Dialog()
    while True:
        rows = [services.SERVICES[s] for s in accounts.SERVICES]
        labels = ['%s  ·  %s' % (s.name, _status_line(s)) for s in rows]
        stremthru = accounts.option('stremthru')
        labels.append(_t('معرفة الجاهز قبل التشغيل (Real-Debrid وAllDebrid): %s',
                         'Know what is ready before playing (Real-Debrid, AllDebrid): %s') % (
            _t('شغّال', 'On') if stremthru else _t('مطفي', 'Off')))
        cache_play = accounts.option('cache_and_play')
        labels.append(_t('تشغيل غير الجاهز بعد تحميله: %s', 'Play what is not ready once it downloads: %s') % (
            _t('شغّال', 'On') if cache_play else _t('مطفي', 'Off')))
        labels.append(_t('إضافة Torrentio وComet للمصادر', 'Add Torrentio and Comet to the sources'))
        choice = dialog.select(_t('Debrid: حساباتك', 'Debrid: your accounts'), labels)
        if choice < 0:
            return True
        if choice < len(rows):
            service = rows[choice]
            if service.linked():
                manage(service)
            else:
                link(service)
        elif choice == len(rows):
            if stremthru or dialog.yesno('StremThru', _t(
                    'Real-Debrid وAllDebrid ما يقولون للتطبيقات وش عندهم جاهز. Dex Hub يسأل StremThru بمفتاح حسابك '
                    'عشان يعرف الجاهز قبل ما تختار. تبي تشغّلها؟',
                    'Real-Debrid and AllDebrid no longer tell apps what is ready. Dex Hub asks StremThru with your '
                    'account\'s key to know before you pick. Turn it on?')):
                accounts.set_option('stremthru', not stremthru)
        elif choice == len(rows) + 1:
            accounts.set_option('cache_and_play', not cache_play)
        else:
            from . import autosources
            autosources.install(announce=True)


def manage(service):
    dialog = xbmcgui.Dialog()
    order = accounts.linked()
    options = [_t('فحص الحساب', 'Check the account'), _t('تسجيل الخروج', 'Unlink')]
    if len(order) > 1 and order[0] != service.id:
        options.append(_t('اجعله الأول', 'Make it the first one'))
    choice = dialog.select(service.name, options)
    if choice == 0:
        try:
            who = service.user()
            accounts.update(service.id, user=who)
            dialog.ok(service.name, _t('الحساب شغال ✓', 'The account works ✓') + ('\n%s' % who if who else ''))
        except Exception as exc:
            dialog.ok(service.name, _t('ما اشتغل الحساب: %s', 'The account did not answer: %s') % exc)
    elif choice == 1:
        if dialog.yesno(service.name, _t('تسجيل الخروج من %s؟', 'Unlink %s?') % service.name):
            accounts.remove(service.id)
            service.forget_library()
            _notify(_t('تم تسجيل الخروج', 'Unlinked'))
    elif choice == 2:
        data = accounts._load()
        data['order'] = [service.id] + [s for s in data['order'] if s != service.id]
        accounts._save(data)


def link(service):
    """Link one service. True when it is linked."""
    first = not accounts.any_linked()
    if service.auth == 'key':
        done = _link_key(service)
    else:
        done = _link_device(service)
    if done:
        try:
            accounts.update(service.id, user=service.user())
        except Exception:
            pass
        _notify(_t('تم ربط %s ✓', '%s linked ✓') % service.name)
        if first and accounts.option('auto_sources'):
            try:
                from . import autosources
                autosources.install(announce=True)
            except Exception as exc:
                accounts.log('sources not added: %s' % exc, True)
    return done


def _window(service, qr_url, code, url, extra=''):
    from ..plex_qr import QRLinkWindow, qr_png
    body = _t('امسح الباركود بكاميرا الجوال ووافق.\n\nأو افتح:\n[COLOR cyan]%s[/COLOR]\n',
              'Scan the code with your phone\'s camera and approve.\n\nOr open:\n[COLOR cyan]%s[/COLOR]\n') % url
    if code:
        body += _t('\nوأدخل الرمز:\n[B][COLOR yellow]%s[/COLOR][/B]\n', '\nand enter the code:\n[B][COLOR yellow]%s[/COLOR][/B]\n') % code
    body += extra
    window = QRLinkWindow(qr_path=qr_png(qr_url, family='debrid_qr') or '', code=code,
                          title=_t('ربط %s', 'Link %s') % service.name, verification_url=url, body_text=body)
    return window, body


def _link_device(service):
    try:
        start = service.device_start()
    except Exception as exc:
        xbmcgui.Dialog().ok(service.name, _t('ما قدرنا نبدأ الربط: %s', 'Linking did not start: %s') % exc)
        return False
    window, body = _window(service, start['qr'], start['code'], start['url'])
    window.show()
    monitor = xbmc.Monitor()
    began, interval = time.time(), max(2, int(start.get('interval') or 5))
    try:
        while time.time() - began < start.get('expires', 600) and not monitor.abortRequested():
            if getattr(window, 'cancelled', False):
                return False
            if monitor.waitForAbort(interval):
                return False
            try:
                account = service.device_poll(start['state'])
            except Exception as exc:
                accounts.log('%s sign-in: %s' % (service.name, exc), True)
                account = None
            if account and account.get('token'):
                accounts.save(service.id, account)
                return True
            left = int(start.get('expires', 600) - (time.time() - began))
            window.set_status(body + _t('\nبانتظار موافقتك… (%d ثانية)', '\nWaiting for you to approve… (%d s)') % max(0, left))
    finally:
        try:
            window.close()
        except Exception:
            pass
    return False


def _link_key(service):
    dialog = xbmcgui.Dialog()
    window, _body = _window(service, service.KEY_PAGE, '', service.KEY_PAGE,
                            _t('\nانسخ مفتاح API من الصفحة، ثم اضغط رجوع واكتبه.',
                               '\nCopy the API key from that page, then press Back and type it.'))
    window.doModal()
    key = dialog.input(_t('مفتاح API لـ %s', '%s API key') % service.name, type=xbmcgui.INPUT_ALPHANUM)
    return save_key(service, key)


def save_key(service, key):
    """Check and keep a pasted key (TV or phone page). True when it works."""
    key = str(key or '').strip()
    if not key:
        return False
    try:
        who = service.key_check(key)
    except Exception as exc:
        xbmcgui.Dialog().ok(service.name, _t('ما وصلنا للخدمة: %s', 'The service did not answer: %s') % exc)
        return False
    if not who:
        xbmcgui.Dialog().ok(service.name, _t('المفتاح ما اشتغل. تأكد منه.', 'That key did not work. Check it.'))
        return False
    accounts.save(service.id, {'token': key, 'user': who})
    return True
