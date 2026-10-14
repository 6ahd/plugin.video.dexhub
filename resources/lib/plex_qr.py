# -*- coding: utf-8 -*-
"""Plex QR sign-in for Dex Hub (ported from DPlex's plexsignin approach).

Renders the plex.tv/link URL as a QR code the user can scan with a phone,
so no code has to be typed on a TV remote.  The PNG is written locally with
a tiny raw encoder — no Pillow, no network service, no skin dependency.
"""
from __future__ import absolute_import

import os
import struct
import sys
import zlib

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from . import pairing_ui
# --- dexhub-403-patch ---
try:
    from .i18n import tr as tr
except Exception:
    from resources.lib.i18n import tr as tr


_LIB = os.path.dirname(os.path.abspath(__file__))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)


def _profile_dir():
    try:
        return xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    except Exception:
        return xbmc.translatePath('special://profile/addon_data/plugin.video.dexhub')


def qr_png(data, box_size=8, border=4):
    """Write a QR PNG for `data` and return its path ('' on failure)."""
    try:
        import qrcode  # bundled pure-Python encoder
        qr = qrcode.QRCode(version=None,
                           error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=1, border=border)
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        width = len(matrix) * box_size
        white, black = b'\xff\xff\xff', b'\x00\x00\x00'
        rows = []
        for row in matrix:
            expanded = b''.join((black if cell else white) * box_size for cell in row)
            for _ in range(box_size):
                rows.append(b'\x00' + expanded)
        raw = b''.join(rows)

        def chunk(kind, payload):
            crc = zlib.crc32(kind + payload) & 0xffffffff
            return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', crc)

        png = (b'\x89PNG\r\n\x1a\n' +
               chunk(b'IHDR', struct.pack('>IIBBBBB', width, width, 8, 2, 0, 0, 0)) +
               chunk(b'IDAT', zlib.compress(raw, 9)) +
               chunk(b'IEND', b''))
        # v4.6.7: filename derived from the ENCODED DATA. Kodi's texture
        # cache is keyed by path — the old fixed single-name QR file meant
        # the FIRST QR ever rendered on the box was shown forever (a stale,
        # expired PIN), for the Plex link AND the pairing QR which shared
        # the same file. Every payload now gets its own path and older
        # QR files (including the legacy fixed name) are purged.
        import glob as _glob
        import hashlib as _hashlib
        digest = _hashlib.sha1(
            data.encode('utf-8', 'replace') if isinstance(data, str) else bytes(data)
        ).hexdigest()[:10]
        prof = _profile_dir()
        os.makedirs(prof, exist_ok=True)
        path = os.path.join(prof, 'plex_link_qr_%s.png' % digest)
        for stale in _glob.glob(os.path.join(prof, 'plex_link_qr*.png')):
            if os.path.abspath(stale) != os.path.abspath(path):
                try:
                    os.remove(stale)
                except Exception:
                    pass
        tmp = path + '.tmp'
        with open(tmp, 'wb') as handle:
            handle.write(png)
        os.replace(tmp, path)
        return path
    except Exception as exc:
        try:
            xbmc.log('[DexHub] QR generation failed: %s' % exc, xbmc.LOGWARNING)
        except Exception:
            pass
        return ''


class QRLinkWindow(xbmcgui.WindowDialog):
    """Borderless dialog: QR on the left, code + instructions on the right.

    Uses plain xbmcgui controls so it renders on every skin (no pyxbmct, no
    custom XML).  show() is non-blocking: the caller polls the PIN and calls
    close() when linking completes; BACK/ESC sets `cancelled`.
    """

    def __init__(self, *args, **kwargs):
        self.cancelled = False
        try:
            qr_path = kwargs.get('qr_path') or ''
            code = kwargs.get('code') or ''
            title = kwargs.get('title') or 'ربط Plex'
            verification_url = kwargs.get('verification_url') or 'plex.tv/link'
            body_text = kwargs.get('body_text') or ''
            icon_path = kwargs.get('icon_path') or ''
        except Exception:
            qr_path, code, title, verification_url, body_text, icon_path = '', '', 'ربط Plex', 'plex.tv/link', '', ''
        w, h = 900, 460
        x, y = pairing_ui.surface(self, w, h)
        pairing_ui.qr(self, qr_path, x + 50, y + 60, 340)
        # v5.10.22: every device/link flow carries the service mark beside
        # the title.  Infer it for legacy callers so Plex/Silo benefit without
        # changing every route, while explicit icon_path remains authoritative.
        if not icon_path:
            try:
                media = os.path.join(xbmcaddon.Addon().getAddonInfo('path'), 'resources', 'media')
                low = str(title or '').lower()
                names = (
                    ('trakt', 'trakt.png'), ('simkl', 'simkl.png'),
                    ('silo', 'provider_silo.png'), ('plex', 'provider_plex.png'),
                    ('nuvio', 'nuvio.png'), ('stremio', 'stremio.png'),
                )
                for needle, filename in names:
                    if needle in low:
                        candidate = os.path.join(media, filename)
                        if os.path.exists(candidate):
                            icon_path = candidate
                        break
            except Exception:
                icon_path = ''
        head_x = x + 410
        if icon_path:
            self.addControl(xbmcgui.ControlImage(x + 410, y + 50, 54, 54, icon_path))
            head_x = x + 478
        head = xbmcgui.ControlLabel(head_x, y + 60, x + w - 40 - head_x, 40, title,
                                    textColor='FFFFFFFF')
        self.addControl(head)
        body = xbmcgui.ControlTextBox(x + 410, y + 120, w - 450, 280,
                                     textColor='FFF0F3FA')
        self.addControl(body)
        body.setText(body_text or (
            tr('امسح الباركود بكاميرا الجوال — تنفتح صفحة الربط مباشرة.\n\n'
            'أو افتح: %s\n'
            'وأدخل الرمز:\n\n'
            '[B]%s[/B]\n\n'
            'بانتظار التأكيد… (رجوع للإلغاء)') % (verification_url, code)))
        self._body = body

    def set_status(self, text):
        try:
            self._body.setText(text)
        except Exception:
            pass

    def onAction(self, action):  # noqa: N802 (Kodi API)
        try:
            if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
                self.cancelled = True
                self.close()
        except Exception:
            self.cancelled = True
            self.close()
