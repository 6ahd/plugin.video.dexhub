# -*- coding: utf-8 -*-
"""Nuvio TV-style account pairing for Dex Hub.

Dex Hub never asks for or receives the user's Nuvio password in this flow.
Kodi starts a Nuvio TV-login session, renders Nuvio's pairing URL as a QR
code, and polls until the user approves the device from nuvio.tv.
"""
from __future__ import absolute_import

import os
import time

import xbmc
import xbmcaddon
import xbmcgui
from . import pairing_ui

try:
    from .i18n import tr
except Exception:
    tr = lambda text: text

_PAIR_TTL = 300


class NuvioPairWindow(xbmcgui.WindowDialog):
    def __init__(self, *args, **kwargs):
        self.cancelled = False
        qr_path = kwargs.get('qr_path') or ''
        code = kwargs.get('code') or ''
        url = kwargs.get('url') or 'https://nuvio.tv/tv-login'

        w, h = 980, 500
        x, y = pairing_ui.surface(self, w, h)
        pairing_ui.qr(self, qr_path, x + 52, y + 75, 350)

        title_x = x + 430
        try:
            icon = os.path.join(xbmcaddon.Addon().getAddonInfo('path'), 'resources', 'media', 'nuvio.png')
            if os.path.exists(icon):
                self.addControl(xbmcgui.ControlImage(x + 430, y + 46, 58, 58, icon))
                title_x = x + 502
        except Exception:
            pass
        self.addControl(xbmcgui.ControlLabel(
            title_x, y + 55, x + w - 40 - title_x, 42, tr('ربط حساب Nuvio'),
            textColor='FFFFFFFF'))
        box = xbmcgui.ControlTextBox(x + 430, y + 115, w - 470, 320,
                                     textColor='FFF0F3FA')
        self.addControl(box)
        box.setText(tr(
            'امسح الرمز بالجوال، سجّل الدخول في موقع Nuvio، ثم وافق على ربط Dex Hub.\n\n'
            'Dex Hub لا يطلب كلمة مرور Nuvio ولا يستلمها.\n\n'
            'رمز الربط: [B]%s[/B]\n\n'
            'بانتظار موافقة Nuvio…\n\n'
            'إذا لم يعمل QR افتح [B]nuvio.tv/tv-login[/B] على جوالك واتبع صفحة الربط.'
        ) % code)
        self._body = box
        self._url = url

    def set_status(self, text):
        try:
            self._body.setText(text)
        except Exception:
            pass

    def onAction(self, action):  # noqa: N802 - Kodi API
        try:
            if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
                self.cancelled = True
                self.close()
        except Exception:
            self.cancelled = True
            self.close()


def pair():
    """Start Nuvio TV login and wait for browser approval."""
    from .dexhub.nuvio_stremio_sync import Nuvio
    from .plex_qr import qr_png

    try:
        state = Nuvio.start_tv_login(device_name='Dex Hub • Kodi')
    except Exception as exc:
        xbmcgui.Dialog().ok('Dex Hub', tr('تعذر بدء ربط Nuvio: %s') % exc)
        return False

    code = str(state.get('code') or '')
    url = str(state.get('url') or '')
    if not code or not url:
        xbmcgui.Dialog().ok('Dex Hub', tr('لم يُرجع Nuvio رمز ربط صالحاً.'))
        return False

    qr_path = ''
    try:
        qr_path = qr_png(url, box_size=8, border=4) or ''
    except Exception:
        qr_path = ''

    win = NuvioPairWindow(qr_path=qr_path, code=code, url=url)
    win.show()
    interval = max(2.0, min(5.0, float(state.get('poll_interval') or 3)))
    deadline = time.time() + _PAIR_TTL
    monitor = xbmc.Monitor()
    last_error = ''
    try:
        while time.time() < deadline and not monitor.abortRequested():
            if getattr(win, 'cancelled', False):
                return False
            try:
                result = Nuvio.poll_tv_login(state)
                status = str((result or {}).get('status') or 'pending').lower()
                if status == 'ok':
                    win.close()
                    xbmcgui.Dialog().notification(
                        'Dex Hub', tr('تم ربط Nuvio ✓'),
                        xbmcgui.NOTIFICATION_INFO, 4000)
                    return True
                if status in ('expired', 'cancelled', 'denied', 'rejected'):
                    last_error = status
                    break
                if status == 'exchange_failed':
                    last_error = str((result or {}).get('error') or status)
                    break
            except Exception as exc:
                # Pairing can survive one transient poll failure.
                last_error = str(exc)
            if monitor.waitForAbort(interval):
                break
    finally:
        try:
            win.close()
        except Exception:
            pass
        try:
            if qr_path and os.path.exists(qr_path):
                # Keep QR cleanup best effort; plex_qr also rotates filenames.
                pass
        except Exception:
            pass

    if last_error and last_error not in ('pending', ''):
        xbmcgui.Dialog().ok('Dex Hub', tr('فشل ربط Nuvio: %s') % last_error)
    else:
        xbmcgui.Dialog().ok('Dex Hub', tr('انتهت مهلة ربط Nuvio. حاول مرة أخرى.'))
    return False
