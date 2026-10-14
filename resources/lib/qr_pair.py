# -*- coding: utf-8 -*-
"""Legacy LAN QR credential entry for Stremio.

Nuvio is deliberately routed away from this module to its TV-style website
approval flow in :mod:`nuvio_tv_pair`, so a Nuvio password never enters Dex Hub.
The local form below remains only for Stremio compatibility.
"""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import xbmc
import xbmcaddon
import xbmcgui

from .i18n import tr


_PAIR_TTL = 300  # seconds the pairing window stays open


def _lan_ip():
    """Best-effort LAN IP of this device (works without internet)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return '127.0.0.1'


_PAGE = u'''<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ربط %(service_title)s — Dex Hub</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0b0d14;color:#e7e7ef;
      display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
 .card{background:#141824;border-radius:16px;padding:28px;width:min(420px,92vw);
       box-shadow:0 8px 40px rgba(0,0,0,.5)}
 h1{font-size:20px;margin:0 0 4px} p{color:#9aa0b4;font-size:14px;margin:0 0 20px}
 label{display:block;font-size:13px;margin:14px 0 6px;color:#c6cadb}
 input{width:100%%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #2a3044;
       background:#0e1120;color:#fff;font-size:16px}
 button{width:100%%;margin-top:22px;padding:14px;border:0;border-radius:12px;font-size:16px;
        font-weight:700;color:#fff;background:linear-gradient(135deg,#7c3aed,#06b6d4);cursor:pointer}
 .ok{color:#34d399;text-align:center;font-size:16px;margin-top:16px;display:none}
 .err{color:#f87171;text-align:center;font-size:14px;margin-top:16px;display:none}
</style></head><body><div class="card">
<h1>ربط حساب %(service_title)s</h1>
<p>اكتب بيانات حسابك — تُرسل مباشرة لجهاز Kodi على شبكتك المحلية فقط.</p>
<label>البريد الإلكتروني</label>
<input id="email" type="email" autocomplete="username" inputmode="email">
<label>كلمة المرور</label>
<input id="password" type="password" autocomplete="current-password">
<button onclick="go()">ربط الحساب</button>
<div class="ok" id="ok">تم الربط بنجاح ✓ — ارجع للتلفزيون</div>
<div class="err" id="err"></div>
<script>
async function go(){
  const e=document.getElementById('email').value.trim();
  const p=document.getElementById('password').value;
  const ok=document.getElementById('ok'), er=document.getElementById('err');
  ok.style.display='none'; er.style.display='none';
  if(!e||!p){er.textContent='اكتب البريد وكلمة المرور';er.style.display='block';return}
  try{
    const r=await fetch('/pair',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:'%(token)s',email:e,password:p})});
    const d=await r.json();
    if(d.ok){ok.style.display='block'}
    else{er.textContent=d.error||'فشل تسجيل الدخول';er.style.display='block'}
  }catch(x){er.textContent='تعذر الاتصال بالجهاز — تأكد أنكما على نفس الشبكة';er.style.display='block'}
}
</script></div></body></html>'''


def qr_pair(service):
    """Run QR pairing. Nuvio always uses its website-approval TV flow."""
    if service == 'nuvio':
        from .nuvio_tv_pair import pair
        return pair()

    from .dexhub import nuvio_stremio_sync as sync

    service_title = 'Nuvio' if service == 'nuvio' else 'Stremio'
    token = os.urandom(8).hex()          # anti-CSRF: page must echo it back
    result = {'done': False, 'ok': False, 'error': ''}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _send(self, code, body, ctype='application/json'):
            data = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, _PAGE % {'service_title': service_title, 'token': token},
                       ctype='text/html')

        def do_POST(self):
            if self.path != '/pair':
                return self._send(404, '{"ok":false}')
            try:
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
            except Exception:
                return self._send(400, '{"ok":false,"error":"bad request"}')
            if payload.get('token') != token:
                return self._send(403, '{"ok":false,"error":"expired page"}')
            email = (payload.get('email') or '').strip()
            password = payload.get('password') or ''
            try:
                if service == 'nuvio':
                    sync.Nuvio.login(email, password)
                else:
                    sync.Stremio.login(email, password)
                # v4.7.9: the password login enables the service's master
                # sync toggle; this QR path never did. The account linked,
                # the menu showed "connected", and every later sync run
                # found NO enabled targets and quietly did nothing — the
                # "Nuvio still doesn't sync, nor the addons" report.
                try:
                    import xbmcaddon
                    xbmcaddon.Addon().setSetting('%s_sync_enabled' % service, 'true')
                except Exception:
                    pass
                with lock:
                    result.update(done=True, ok=True)
                self._send(200, '{"ok":true}')
            except Exception as e:
                with lock:
                    result.update(error=str(e))
                self._send(200, json.dumps({'ok': False, 'error': str(e)},
                                           ensure_ascii=False))

    ip = _lan_ip()
    server = ThreadingHTTPServer((ip, 0), Handler)  # port 0 = OS picks a free one
    port = server.server_address[1]
    url = 'http://%s:%d/' % (ip, port)
    threading.Thread(target=server.serve_forever, name='DexHubQRPair',
                     daemon=True).start()

    # QR window on the TV
    qr_path = ''
    try:
        from .plex_qr import qr_png
        qr_path = qr_png(url, box_size=8, border=4) or ''
    except Exception:
        qr_path = ''

    win = _PairWindow(qr_path=qr_path, url=url, service_title=service_title)
    win.show()
    deadline = time.time() + _PAIR_TTL
    try:
        monitor = xbmc.Monitor()
        while time.time() < deadline and not monitor.abortRequested():
            with lock:
                if result['done']:
                    break
                if result['error']:
                    win.set_status(tr('فشل تسجيل الدخول:') + '\n' + result['error']
                                   + '\n\n' + tr('جرّب مرة أخرى من الجوال.'))
                    result['error'] = ''
            if win.cancelled:
                break
            if monitor.waitForAbort(0.4):
                break
    finally:
        try:
            win.close()
        except Exception:
            pass
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass

    if result['ok']:
        xbmcgui.Dialog().notification('Dex Hub',
                                      tr('تم ربط %s ✓') % service_title,
                                      xbmcgui.NOTIFICATION_INFO, 4000)
    return bool(result['ok'])


class _PairWindow(xbmcgui.WindowDialog):
    def __init__(self, *args, **kwargs):
        self.cancelled = False
        qr_path = kwargs.get('qr_path') or ''
        url = kwargs.get('url') or ''
        service_title = kwargs.get('service_title') or ''
        w, h = 980, 520
        from . import pairing_ui
        x, y = pairing_ui.surface(self, w, h)
        pairing_ui.qr(self, qr_path, x + 55, y + 70, 360)
        title_x = x + 440
        try:
            icon_name = 'stremio.png' if str(service_title).lower() == 'stremio' else ''
            icon = os.path.join(xbmcaddon.Addon().getAddonInfo('path'), 'resources', 'media', icon_name) if icon_name else ''
            if icon and os.path.exists(icon):
                self.addControl(xbmcgui.ControlImage(x + 440, y + 46, 58, 58, icon))
                title_x = x + 512
        except Exception:
            pass
        self.addControl(xbmcgui.ControlLabel(
            title_x, y + 55, x + w - 40 - title_x, 46,
            '%s %s' % (tr('ربط حساب'), service_title), textColor='FFFFFFFF'))
        self._body = xbmcgui.ControlTextBox(x + 440, y + 115, w - 485, 340,
                                           textColor='FFF0F3FA')
        self.addControl(self._body)
        self.set_status(
            tr('امسح الباركود بكاميرا الجوال.') + '\n\n'
            + tr('ستفتح صفحة تكتب فيها بريدك وكلمة المرور بكيبورد الجوال — تُرسل لجهازك مباشرة عبر شبكتك المحلية، لا تمر بأي خادم خارجي.') + '\n\n'
            + tr('أو افتح هذا الرابط يدوياً:') + '\n[B][COLOR cyan]%s[/COLOR][/B]\n\n' % url
            + tr('بانتظار الربط…'))

    def set_status(self, text):
        try:
            self._body.setText(text)
        except Exception:
            pass

    def onAction(self, action):
        try:
            if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
                self.cancelled = True
                self.close()
        except Exception:
            self.cancelled = True
            self.close()
