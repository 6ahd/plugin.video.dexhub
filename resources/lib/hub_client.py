# -*- coding: utf-8 -*-
"""v5.10.366: Dex Hub Extra, the TV's side of hub.dexworld.cc.

The TV is linked once with a QR code (the hub's /api/pair/start and poll):
the phone signs in on the hub's page and approves it, the TV keeps the
device secret it gets back in hub_link.json (never in the settings, so a
backup never carries it to another TV). Nothing runs in the background for
this: the hub is asked when the add-on starts (the cloud look), when a
catalogue page is read (the shared cache) and while a watch party is on.

Extra (the cloud look, the shared catalogue cache, watch parties) is open to
every linked TV while the trial runs; later for supporters (Ko-fi) and
DexWorld subscribers. The answer is kept six hours, and three days more
when the hub cannot be reached, so a hub outage never takes a feature away
in the middle of an evening.
"""
import json
import os
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .profile_scope import ADDON_ID

DEFAULT_URL = 'https://hub.dexworld.cc'
EXTRA_TTL = 6 * 3600
EXTRA_GRACE = 3 * 24 * 3600
PAIR_TTL = 600
_LOCK = threading.RLock()
_STATE = {'data': None, 'mtime': None}


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _path():
    folder = xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('profile'))
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    return os.path.join(folder, 'hub_link.json')


def state():
    """hub_link.json: {url, device_id, secret, label, extra: {...}, look: {...}, cache: bool}."""
    with _LOCK:
        path = _path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if _STATE['data'] is not None and mtime == _STATE['mtime']:
            return _STATE['data']
        data = {}
        if mtime is not None:
            try:
                with open(path, encoding='utf-8') as fh:
                    data = json.load(fh) or {}
            except Exception:
                data = {}
        _STATE['data'], _STATE['mtime'] = (data if isinstance(data, dict) else {}), mtime
        return _STATE['data']


def save(**changes):
    with _LOCK:
        data = dict(state())
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        path = _path()
        tmp = path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
            _STATE['data'], _STATE['mtime'] = data, os.path.getmtime(path)
        except Exception as exc:
            xbmc.log('[DexHub] hub link not saved: %s' % exc, xbmc.LOGWARNING)
        return data


def base():
    return (state().get('url') or DEFAULT_URL).rstrip('/')


def linked():
    data = state()
    return bool(data.get('secret') and data.get('device_id'))


class HubError(Exception):
    def __init__(self, message, status=0):
        Exception.__init__(self, message)
        self.status = status


def _version():
    try:
        return xbmcaddon.Addon(ADDON_ID).getAddonInfo('version')
    except Exception:
        return ''


def request(method, path, body=None, timeout=10, auth=True, raw=False):
    """One call to the hub: the JSON answer (or the bytes with ``raw``)."""
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    headers = {'User-Agent': 'DexHub/%s (Kodi)' % _version(), 'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if auth:
        secret = state().get('secret')
        if not secret:
            raise HubError('not linked', 401)
        headers['X-Dex-Device'] = secret
    req = Request(base() + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            if raw:
                return payload, dict(resp.headers.items())
    except HTTPError as exc:
        try:
            message = json.loads(exc.read().decode('utf-8', 'replace')).get('error') or str(exc.code)
        except Exception:
            message = 'HTTP %s' % exc.code
        if exc.code == 401 and auth:
            # the hub forgot this TV (unlinked from the web page): forget it too
            unlink(remote=False)
        raise HubError(message, exc.code)
    try:
        return json.loads(payload.decode('utf-8', 'replace')) or {}
    except ValueError:
        raise HubError('bad answer')


def long_poll(path, monitor, timeout=35):
    """v5.10.374: a GET the hub holds (relay, watch party): the JSON answer, or
    None when Kodi is closing. The call runs in a thread of its own and this
    one waits in Kodi; on Kodi's exit its socket is closed so the read ends at
    once (Kodi waits for every thread of the service before it exits, and a
    read held by the hub kept it waiting up to 25 s)."""
    import http.client
    import socket
    import ssl
    from urllib.parse import urlsplit
    secret = state().get('secret')
    if not secret:
        raise HubError('not linked', 401)
    parts = urlsplit(base() + path)
    if parts.scheme == 'https':
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    target = parts.path + ('?' + parts.query if parts.query else '')
    box = {}

    def call():
        try:
            conn.request('GET', target, headers={'User-Agent': 'DexHub/%s (Kodi)' % _version(),
                                                 'Accept': 'application/json', 'X-Dex-Device': secret})
            resp = conn.getresponse()
            box['status'], box['body'] = resp.status, resp.read()
        except Exception as exc:
            box['error'] = exc
    worker = threading.Thread(target=call, name='DexHub-long-poll', daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            if monitor.waitForAbort(0.25):
                try:
                    if conn.sock is not None:
                        conn.sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                worker.join(2)
                return None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if 'error' in box:
        raise HubError(str(box['error']) or box['error'].__class__.__name__)
    status = box.get('status') or 0
    try:
        payload = json.loads((box.get('body') or b'{}').decode('utf-8', 'replace')) or {}
    except ValueError:
        payload = {}
    if status >= 400:
        if status == 401:
            unlink(remote=False)
        raise HubError(payload.get('error') or 'HTTP %s' % status, status)
    return payload


# ------------------------------------------------------------------ Extra
def extra(force=False, network=True):
    """{active, mode, reason, until}; inactive when not linked."""
    if not linked():
        return {'active': False, 'reason': 'not linked'}
    data = state()
    cached = data.get('extra') or {}
    age = time.time() - float(cached.get('at') or 0)
    if cached and not force and age < EXTRA_TTL:
        return cached
    if not network:
        return cached if cached and age < EXTRA_TTL + EXTRA_GRACE else {'active': False, 'reason': 'unknown'}
    try:
        answer = request('GET', '/api/device/extra', timeout=6)
        fresh = {'active': bool(answer.get('active')), 'mode': answer.get('mode') or '',
                 'reason': answer.get('reason') or '', 'until': answer.get('until') or 0, 'at': time.time(),
                 # v5.10.367: who this TV is linked to, and Telegram
                 'account': answer.get('account') or '', 'telegram': answer.get('telegram') or {}}
        save(extra=fresh)
        return fresh
    except HubError as exc:
        if exc.status == 401:
            return {'active': False, 'reason': 'not linked'}
    except Exception:
        pass
    if cached and age < EXTRA_TTL + EXTRA_GRACE:
        return cached
    return {'active': False, 'reason': 'offline'}


def active(network=False):
    """Extra is on for this TV (no network call unless asked)."""
    return bool(extra(network=network).get('active'))


def option(key, default=True):
    """The TV's own Extra switches (in hub_link.json): cache, look."""
    value = state().get('opt_' + key)
    return default if value is None else bool(value)


# ------------------------------------------------------------------ linking
class _PairWindow(xbmcgui.WindowDialog):
    def __init__(self, *args, **kwargs):
        from . import pairing_ui
        self.cancelled = False
        w, h = 980, 500
        x, y = pairing_ui.surface(self, w, h)
        pairing_ui.qr(self, kwargs.get('qr_path') or '', x + 52, y + 75, 350)
        self.addControl(xbmcgui.ControlLabel(x + 430, y + 55, w - 470, 42, kwargs.get('title') or 'Dex Hub Extra', textColor='FFFFFFFF'))
        self._body = xbmcgui.ControlTextBox(x + 430, y + 115, w - 470, 340, textColor='FFF0F3FA')
        self.addControl(self._body)
        self._body.setText(
            kwargs.get('text') or _t('امسح الرمز بالجوال، ادخل بتيليجرام بضغطة (أو بالإيميل)، ووافق على ربط هذا التلفزيون.',
               'Scan the code with your phone, sign in with Telegram in one tap (or with email) and approve this TV.')
            + (('\n\n' + _t('الرمز: [B]%s[/B]', 'Code: [B]%s[/B]') % kwargs['code']) if kwargs.get('code') else '')
            + '\n[COLOR FF8FA0C0]%s[/COLOR]' % (base().replace('https://', ''))
            + '\n\n' + _t('بعد الربط: شكلك وترتيبك محفوظ في حسابك، والصفوف أسرع، والمشاهدة الجماعية.',
                          'Once linked: your look and order kept in your account, faster rows, and watch parties.'))

    def onAction(self, action):  # noqa: N802 - Kodi API
        if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
            self.cancelled = True
            self.close()


def pair():
    """Link this TV with a QR code: True once the phone approved it."""
    try:
        label = xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'
        start = request('POST', '/api/pair/start', {
            'label': label, 'addon_version': _version(),
            'kodi_version': xbmc.getInfoLabel('System.BuildVersion').split(' ')[0],
            'platform': _platform()}, timeout=10, auth=False)
    except Exception as exc:
        xbmcgui.Dialog().ok('Dex Hub Extra', _t('تعذر الوصول إلى %s', 'Could not reach %s') % base() + '\n' + str(exc))
        return False
    code, url = start.get('code') or '', start.get('url') or ''
    qr_path = ''
    try:
        from .plex_qr import qr_png
        qr_path = qr_png(url, box_size=8, border=4) or ''
    except Exception:
        qr_path = ''
    win = _PairWindow(qr_path=qr_path, code=code)
    win.show()
    monitor = xbmc.Monitor()
    deadline = time.time() + PAIR_TTL
    try:
        while time.time() < deadline and not monitor.abortRequested() and not win.cancelled:
            if monitor.waitForAbort(3):
                break
            try:
                answer = request('POST', '/api/pair/poll', {'device_id': start.get('device_id'),
                                                            'pair_token': start.get('pair_token')},
                                 timeout=8, auth=False)
            except HubError as exc:
                if exc.status in (404, 410):
                    break
                continue
            except Exception:
                continue
            if answer.get('status') == 'paired' and answer.get('device_secret'):
                save(device_id=start.get('device_id'), secret=answer['device_secret'], label=label,
                     linked_at=int(time.time()), extra=None, look=None)
                win.close()
                info = extra(force=True)
                who = info.get('account') or ''
                xbmcgui.Dialog().notification('Dex Hub Extra', (_t('انربط بحساب %s ✓', 'Linked to %s ✓') % who) if who
                                              else _t('تم ربط التلفزيون ✓', 'TV linked ✓'),
                                              xbmcgui.NOTIFICATION_INFO, 5000)
                wake()
                if info.get('active'):
                    _first_look()
                pull_accounts(force=True)       # v5.10.370: Nuvio and Stremio from the page
                return True
    finally:
        try:
            win.close()
        except Exception:
            pass
    if not win.cancelled:
        xbmcgui.Dialog().ok('Dex Hub Extra', _t('انتهت مهلة الربط. حاول مرة ثانية.', 'Linking timed out. Try again.'))
    return False


def _platform():
    for cond, name in (('System.Platform.Android', 'android'), ('System.Platform.TVOS', 'tvos'),
                       ('System.Platform.Linux', 'linux'), ('System.Platform.Windows', 'windows'),
                       ('System.Platform.OSX', 'macos'), ('System.Platform.IOS', 'ios')):
        if xbmc.getCondVisibility(cond):
            return name
    return 'kodi'


def _first_look():
    """Just linked: the account's whole setup (v5.10.371), else its look,
    comes to this TV; or this TV's goes up."""
    try:
        from . import cloud_profile
        if cloud_profile.offer(first=True):
            return
        cloud_profile.push(force=True)
    except Exception as exc:
        xbmc.log('[DexHub] first setup: %s' % exc, xbmc.LOGWARNING)
    try:
        from . import cloud_look
        cloud = cloud_look.remote()
        if cloud and cloud.get('look'):
            when = cloud.get('updated_at') or 0
            stamp = time.strftime('%Y-%m-%d', time.localtime(when / 1000.0)) if when else ''
            if xbmcgui.Dialog().yesno('Dex Hub Extra', _t(
                    'حسابك فيه شكل وترتيب محفوظ %s. تبي تطبقه على هذا التلفزيون؟\n(لا: يرتفع شكل هذا التلفزيون بداله)',
                    'Your account has a saved look and order %s. Apply it to this TV?\n(No: this TV\'s look goes up instead)')
                    % stamp, nolabel=_t('لا', 'No'), yeslabel=_t('طبّقه', 'Apply')):
                cloud_look.apply(cloud)
                return
        cloud_look.push(force=True)
    except Exception as exc:
        xbmc.log('[DexHub] first look: %s' % exc, xbmc.LOGWARNING)


def unlink(remote=True):
    if remote and linked():
        try:
            request('POST', '/api/device/unlink', {}, timeout=6)     # v5.10.367: the hub forgets this TV too
        except Exception:
            pass
    with _LOCK:
        save(device_id=None, secret=None, extra=None, look=None, linked_at=None)
    try:
        home = xbmcgui.Window(10000)
        for prop in ('dhs.party.room', 'dhs.party.code', 'dhs.party.host'):
            home.clearProperty(prop)
    except Exception:
        pass
    wake()


def wake():
    """The service's hub thread looks again (linked, unlinked, a party)."""
    try:
        xbmcgui.Window(10000).setProperty('dexhub.hub.wake', '%.3f' % time.time())
    except Exception:
        pass


# ------------------------------------------------------------------ the menu
def _status_line():
    if not linked():
        return _t('غير مربوط', 'Not linked')
    info = extra(network=True)
    who = info.get('account') or state().get('label') or 'Kodi'
    if info.get('active'):
        why = {'trial': _t('فترة تجريبية مفتوحة للكل', 'Open trial'),
               'kofi': _t('داعم على Ko-fi', 'Ko-fi supporter'),
               'subscriber': _t('مشترك DexWorld', 'DexWorld subscriber')}.get(info.get('reason'), '')
        tg = (info.get('telegram') or {})
        tg_line = (' · Telegram' + ((' @' + tg['username']) if tg.get('username') else '')) if tg.get('linked') else ''
        return _t('مربوط بحساب %s · Extra مفعّل', 'Linked to %s · Extra on') % who + ((' · ' + why) if why else '') + tg_line
    return _t('مربوط بحساب %s · Extra للداعمين', 'Linked to %s · Extra is for supporters') % who


def menu():
    """Dex Hub Extra: link, the cloud look, the shared cache, watch parties."""
    from .homeui.options import choose
    while True:
        if not linked():
            pick = choose('Dex Hub Extra', [
                (_t('ربط التلفزيون بالباركود', 'Link this TV with a QR code'),
                 _t('حساب واحد على hub.dexworld.cc', 'One account on hub.dexworld.cc')),
                (_t('وش فيه Extra؟', 'What is in Extra?'), '')], subtitle=_status_line())
            if pick == 0:
                if not pair():
                    return
                continue
            if pick == 1:
                _about()
                continue
            return
        info = extra(network=False)
        on = bool(info.get('active'))
        cache, look = option('cache'), option('look')
        tg = info.get('telegram') or {}
        rows = [
            ('party', _t('المشاهدة الجماعية', 'Watch party'), _t('تتفرجون مع بعض، كل واحد على تلفزيونه', 'Watch together, each on their own TV')),
            ('telegram', (_t('تيليجرام: مربوط %s', 'Telegram: linked %s') % (('@' + tg['username']) if tg.get('username') else ''))
             if tg.get('linked') else _t('ربط تيليجرام', 'Link Telegram'),
             _t('الحلقات الجديدة وأكواد المشاهدة توصلك على تيليجرام', 'New episodes and party codes on Telegram')),
            ('look', _t('الشكل والترتيب في السحابة: %s', 'Look and order in the cloud: %s') % (_t('شغّال', 'On') if look else _t('موقف', 'Off')),
             _t('ينحفظ في حسابك ويرجع لأي تلفزيون تربطه', 'Kept in your account, back on any TV you link')),
            ('push', _t('ارفع شكلي الحين', 'Upload my look now'), ''),
            ('pull', _t('رجّع الشكل من حسابي', 'Bring my look back from my account'), ''),
            ('cache', _t('كاش الصفوف المشترك: %s', 'Shared row cache: %s') % (_t('شغّال', 'On') if cache else _t('موقف', 'Off')),
             _t('صفحات الكتالوج تجي من السيرفر جاهزة', 'Catalogue pages come ready from the hub')),
            ('setup_down', _t('رجّع كل شي من حسابي', 'Bring everything back from my account'),
             _t('المصادر والرئيسية والكوليكشن والحسابات (Plex وEmby وSilo) واللغة والمشغل', 'Sources, Home, collections, accounts (Plex, Emby, Silo), language, player')),
            ('setup_up', _t('ارفع كل شي من هذا التلفزيون', 'Upload everything from this TV'),
             _t('ينرفع لحاله كل 6 ساعات لو تغير شي', 'Goes up by itself every six hours when something changed')),
            ('accounts', _t('سحب حساباتي من الصفحة', 'Bring my accounts from the page'),
             _t('Nuvio وStremio اللي ربطتها في hub.dexworld.cc: إضافاتها وواجهاتها وكوليكشنها', 'Nuvio and Stremio linked on hub.dexworld.cc: their add-ons, Home and collections')),
            ('about', _t('وش فيه Extra؟', 'What is in Extra?'), ''),
            ('unlink', _t('فك ربط هذا التلفزيون', 'Unlink this TV'), _t('يطلع من حسابك ويبقى شكله عليه', 'Leaves your account; its look stays on it')),
        ]
        pick = choose('Dex Hub Extra', [r[1:] for r in rows], subtitle=_status_line())
        if pick < 0:
            return
        key = rows[pick][0]
        if key in ('party', 'telegram', 'look', 'push', 'pull', 'cache', 'setup_down', 'setup_up') and not on and not extra(force=True).get('active'):
            _about(locked=True)
            continue
        if key == 'party':
            from . import watch_party
            watch_party.menu()
            return
        if key == 'telegram':
            telegram_menu(tg)
        elif key == 'look':
            save(opt_look=not look)
        elif key == 'push':
            from . import cloud_look
            ok = cloud_look.push(force=True)
            _note(_t('انرفع شكلك ✓', 'Your look is up ✓') if ok else _t('تعذر الرفع', 'Upload failed'))
        elif key == 'pull':
            from . import cloud_look
            cloud = cloud_look.remote()
            if cloud and cloud.get('look'):
                cloud_look.apply(cloud)
                _note(_t('رجع شكلك ✓', 'Your look is back ✓'))
            else:
                _note(_t('ما فيه شكل محفوظ في حسابك', 'No saved look in your account'))
        elif key == 'cache':
            save(opt_cache=not cache)
        elif key == 'setup_down':
            from . import cloud_profile
            cloud_profile.offer()
        elif key == 'setup_up':
            from . import cloud_profile
            _note(_t('انرفع إعدادك كله ✓', 'Your whole setup is up ✓') if cloud_profile.push(force=True)
                  else _t('تعذر الرفع', 'Upload failed'))
        elif key == 'accounts':
            took = pull_accounts(force=True, notify=True)
            if not took:
                _note(_t('ما فيه حساب جديد في الصفحة، أو التلفزيون مربوط بنفسه', 'Nothing new on the page, or this TV is linked by itself'))
        elif key == 'about':
            _about()
        elif key == 'unlink':
            if xbmcgui.Dialog().yesno('Dex Hub Extra', _t('تفك ربط هذا التلفزيون من حساب %s؟', 'Unlink this TV from %s?')
                                      % (info.get('account') or _t('حسابك', 'your account'))):
                unlink()
                _note(_t('انفك الربط', 'Unlinked'))


# ------------------------------------------------------------------ Telegram (v5.10.367)
def telegram_menu(tg):
    if tg.get('linked'):
        pick = xbmcgui.Dialog().select('Telegram' + ((' @' + tg['username']) if tg.get('username') else ''), [
            _t('غيّر التنبيهات من صفحة Dex Hub (باركود)', 'Change the messages on the Dex Hub page (QR)'),
            _t('فك ربط تيليجرام', 'Unlink Telegram')])
        if pick == 0:
            telegram_link()
        elif pick == 1 and xbmcgui.Dialog().yesno('Telegram', _t('توقف رسائل تيليجرام لهذا الحساب؟', 'Stop Telegram messages for this account?')):
            try:
                request('POST', '/api/device/telegram/unlink', {}, timeout=6)
            except Exception:
                pass
            extra(force=True)
        return
    telegram_link()


def telegram_link():
    """A QR to the hub's page: one tap on «Log in with Telegram» links it to
    this TV's account. The TV waits for it (15 minutes at most)."""
    try:
        start = request('POST', '/api/device/telegram/start', {}, timeout=8)
    except HubError as exc:
        xbmcgui.Dialog().ok('Telegram', _t('تيليجرام مو مفعّل في السيرفر للحين.', 'Telegram is not set up on the hub yet.')
                            if exc.status == 503 else str(exc))
        return False
    except Exception as exc:
        xbmcgui.Dialog().ok('Telegram', str(exc))
        return False
    qr_path = ''
    try:
        from .plex_qr import qr_png
        qr_path = qr_png(start.get('url') or '', box_size=8, border=4) or ''
    except Exception:
        pass
    win = _PairWindow(qr_path=qr_path, title='Telegram', text=_t(
        'امسح الرمز بالجوال واضغط «Log in with Telegram»، ووافق. بعدها توصلك على تيليجرام:\n• الحلقات الجديدة للمسلسلات اللي تتابعها\n• كود المشاهدة الجماعية مع زر مشاركة',
        'Scan the code, tap «Log in with Telegram» and accept. Then Telegram brings you:\n• new episodes of the shows you follow\n• your watch party code with a Share button'))
    win.show()
    monitor = xbmc.Monitor()
    deadline = time.time() + 15 * 60
    try:
        while time.time() < deadline and not win.cancelled and not monitor.abortRequested():
            if monitor.waitForAbort(3):
                break
            info = extra(force=True)
            if (info.get('telegram') or {}).get('linked'):
                win.close()
                tg = info['telegram']
                _note(_t('انربط تيليجرام ✓ %s', 'Telegram linked ✓ %s') % (('@' + tg['username']) if tg.get('username') else ''))
                follows(force=True)
                return True
    finally:
        try:
            win.close()
        except Exception:
            pass
    return False


def pull_accounts(force=False, notify=True, sync=True):
    """v5.10.370: Nuvio and Stremio linked on the hub's page come to this TV,
    and the TV's own sync brings their add-ons, Home and collections, as
    after a sign in on the TV. A service this TV signed in to by itself stays
    as it is. Nuvio's refresh token stays on the hub (only the hub refreshes
    it): this TV asks the hub for a new access token when its own runs out.
    Returns the services taken."""
    if not linked():
        return []
    mine = state().get('accounts') or {}
    path = '/api/device/accounts' + ('' if force or not mine.get('rev') else '?rev=' + mine['rev'])
    try:
        answer = request('GET', path, timeout=12)
    except Exception as exc:
        xbmc.log('[DexHub] accounts from the hub: %s' % exc, xbmc.LOGINFO)
        return []
    save(accounts={'rev': answer.get('rev') or '', 'at': time.time()})
    if answer.get('unchanged'):
        return []
    from .dexhub.nuvio_stremio_sync import Nuvio, Stremio
    took = []
    n = answer.get('nuvio') or {}
    if n.get('access_token'):
        cur = Nuvio.token()
        same = str(cur.get('user_id') or '') == str(n.get('user_id') or '')
        if not cur.get('access_token') or cur.get('via_hub'):
            data = {'access_token': n['access_token'], 'refresh_token': '', 'via_hub': True,
                    'expires_in': max(60, int(float(n.get('expires_at') or 0) / 1000 - time.time())),
                    'created_at': int(time.time()), 'user_id': n.get('user_id') or None, 'email': n.get('email') or ''}
            Nuvio.save_token(Nuvio._carry_profile(data))
            if not Nuvio.has_profile():
                _nuvio_profile(Nuvio, int(n.get('profile_index') or 0))
            if not (cur.get('via_hub') and same):
                took.append('Nuvio')
        elif not same:
            xbmc.log('[DexHub] accounts: this TV is signed in to another Nuvio account; left as it is', xbmc.LOGINFO)
    s = answer.get('stremio') or {}
    if s.get('authKey'):
        cur = Stremio.token()
        if not cur.get('authKey') or cur.get('via_hub'):
            if cur.get('authKey') != s['authKey']:
                Stremio.save_token({'authKey': s['authKey'], 'user_id': s.get('user_id') or '', 'email': s.get('email') or '',
                                    'created_at': int(time.time()), 'via_hub': True})
                took.append('Stremio')
    if took:
        xbmc.log('[DexHub] accounts from the hub: %s' % ', '.join(took), xbmc.LOGINFO)
        try:
            from .skinui.common import ask_republish
            ask_republish('accounts')
        except Exception:
            pass
        if notify:
            _note(_t('انسحب من حسابك: %s. الإضافات والواجهات والكوليكشن جاية الحين', 'From your account: %s. Add-ons, Home and collections are on their way') % '، '.join(took))
        if sync:
            xbmc.executebuiltin('RunPlugin(plugin://%s/?action=cloud_sync_now)' % ADDON_ID)
    return took


def _nuvio_profile(Nuvio, index):
    """The Nuvio profile the hub uses, or the only one; else the TV asks later."""
    try:
        rows = Nuvio.profiles(fresh=True) or []
        row = next((r for r in rows if index and int(r.get('profile_index', r.get('profileIndex', 0)) or 0) == index), None)
        if row is None and len(rows) == 1:
            row = rows[0]
        if row is not None:
            Nuvio.select_profile(row)
    except Exception as exc:
        xbmc.log('[DexHub] accounts: Nuvio profile: %s' % exc, xbmc.LOGINFO)


def nuvio_access():
    """A new Nuvio access token from the hub (for a sign in that came from it)."""
    try:
        answer = request('GET', '/api/device/accounts?fresh=1', timeout=12)
        return answer.get('nuvio') or None
    except Exception as exc:
        xbmc.log('[DexHub] Nuvio token from the hub: %s' % exc, xbmc.LOGINFO)
        return None


def follows(shows=None, names=None, force=False, movies=None):
    """The shows this TV follows go to the hub (new episode messages on
    Telegram): only while Telegram is linked, only when the list changed or
    once a day. Called from the reminders' pass (no thread of its own)."""
    try:
        if not linked():
            return False
        info = extra(network=False)
        if not info.get('active') or not (info.get('telegram') or {}).get('linked'):
            return False
        if shows is None:
            from . import watch_sources
            shows, names, movies = watch_sources.monitoring_titles()
        names = names or {}
        rows = sorted(set(str(s) for s in shows or [] if str(s).isdigit()))
        films = sorted(set(str(s) for s in movies or [] if str(s).isdigit()))   # v5.10.368: films too
        import hashlib
        sig = hashlib.sha1(('|'.join(rows) + '#' + '|'.join(films)).encode('utf-8')).hexdigest()
        mine = state().get('follows') or {}
        if not force and mine.get('sig') == sig and time.time() - float(mine.get('at') or 0) < 24 * 3600:
            return True
        from .i18n import is_english
        tz = -(time.altzone if time.localtime().tm_isdst > 0 else time.timezone) // 60
        request('PUT', '/api/device/follows', {'shows': [{'tmdb': s, 'name': names.get(s) or ''} for s in rows],
                                               'movies': [{'tmdb': s, 'name': names.get(s) or ''} for s in films],
                                               'lang': 'en' if is_english() else 'ar', 'tz_minutes': tz}, timeout=10)
        save(follows={'sig': sig, 'at': time.time(), 'count': len(rows), 'films': len(films)})
        return True
    except Exception as exc:
        xbmc.log('[DexHub] follows not sent: %s' % exc, xbmc.LOGINFO)
        return False


def _note(text):
    xbmcgui.Dialog().notification('Dex Hub Extra', text, xbmcgui.NOTIFICATION_INFO, 3500, sound=False)


def _about(locked=False):
    text = _t(
        '[B]Dex Hub Extra[/B]\n\n'
        '• الشكل والترتيب في السحابة: الثيم والواجهة وترتيب الصفوف ينحفظ في حسابك، وأي تلفزيون تربطه يجيه نفس الشكل.\n'
        '• كاش الصفوف المشترك: صفحات الكتالوج تجي من السيرفر جاهزة بدل ما ينتظر كل تلفزيون الإضافة.\n'
        '• المشاهدة الجماعية: تسوي غرفة وتعطي الكود لأهلك أو ربعك، تشغيل وإيقاف وتقديم مع بعض، وكل واحد يشغّل من مصادره.\n\n'
        'الحين تجريبي ومفتوح للكل. لاحقًا للداعمين على Ko-fi ومشتركي DexWorld.\n'
        'ما يشتغل شي في الخلفية: التلفزيون يسأل السيرفر بس لما يحتاجه.',
        '[B]Dex Hub Extra[/B]\n\n'
        '• Look and order in the cloud: the theme, the layout and the order of your rows are kept in your account; any TV you link gets the same look.\n'
        '• Shared row cache: catalogue pages come ready from the hub instead of every TV waiting for the add-on.\n'
        '• Watch party: make a room and give the code to family or friends; play, pause and seek together, each TV playing from its own sources.\n\n'
        'It is a trial now, open to everyone. Later for Ko-fi supporters and DexWorld subscribers.\n'
        'Nothing runs in the background: the TV asks the hub only when it needs it.')
    if locked:
        text = _t('Extra حاليًا للداعمين ومشتركي DexWorld.\n\n', 'Extra is for supporters and DexWorld subscribers right now.\n\n') + text
    xbmcgui.Dialog().textviewer('Dex Hub Extra', text)


# ------------------------------------------------------------------ shared catalogue cache
_CATALOG_DOWN = 'dexhub.hub.catalog.down'


def _catalog_ok(url):
    """Only public Stremio catalogue pages go through the hub (never a search,
    a local add-on, DexWorld's own server or an add-on asked with its own headers)."""
    import re
    from urllib.parse import urlsplit, unquote
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or parts.port not in (None, 443) or parts.username or not host:
        return False
    if host == 'localhost' or host.endswith(('.local', '.lan', '.home', '.internal')) or 'dexworld.cc' in host:
        return False
    if re.match(r'^[0-9.]+$', host) or ':' in host:
        return False
    path = unquote(parts.path)
    if 'search=' in path or parts.query:
        return False
    return bool(re.search(r'/catalog/[^/]+/[^/]+(/[^/]+)?\.json$', path))


def _down(seconds):
    try:
        xbmcgui.Window(10000).setProperty(_CATALOG_DOWN, '%.0f' % (time.time() + seconds))
    except Exception:
        pass


def catalog_text(url, timeout=10):
    """A catalogue page from the hub's shared cache, or None (the add-on is
    then asked as always). Never a network call to learn whether it may."""
    try:
        if not _catalog_ok(url):
            return None
        if float(xbmcgui.Window(10000).getProperty(_CATALOG_DOWN) or 0) > time.time():
            return None
        if not (linked() and option('cache') and active(network=False)):
            return None
    except Exception:
        return None
    from urllib.parse import quote
    started = time.time()
    try:
        body, headers = request('GET', '/api/device/catalog?u=' + quote(url, safe=''),
                                timeout=max(4.0, min(15.0, float(timeout or 10))), raw=True)
        text = body.decode('utf-8', 'replace')
        xbmc.log('[DexHub] catalogue from the hub (%s, %.0f ms)' % (
            headers.get('X-Dex-Cache') or headers.get('x-dex-cache') or '?', (time.time() - started) * 1000), xbmc.LOGDEBUG)
        return text
    except HubError as exc:
        if exc.status in (400, 502):
            return None                 # not shareable, or the add-on failed there too
        if exc.status == 402:
            save(extra={'active': False, 'reason': 'none', 'at': time.time()})
        _down(120 if exc.status == 429 else 600)
    except Exception:
        _down(300)
    return None
