# -*- coding: utf-8 -*-
"""Keeps the clock right on CoreELEC / LibreELEC boxes that have no RTC.

Such a box starts with the time it had when its image was built (or weeks
old), so HTTPS fails until the network clock catches up. Two quiet parts:

- a boot unit that moves the clock forward to the last time this box saved
  (never back) before the network and Kodi start, and saves it at shutdown;
- a thread that asks NTP servers by address (no DNS needed), steps the clock
  when it is off by more than 2 s, and saves the time every 10 minutes.

Nothing runs on boxes with an RTC, on other systems, or when
/storage/.config/dexhub_clock.off exists. No settings, no notifications.
"""

import ctypes
import ctypes.util
import glob
import os
import socket
import struct
import subprocess
import time

import xbmc

CONF = '/storage/.config'
OFF = os.path.join(CONF, 'dexhub_clock.off')
STAMP = '/storage/.cache/dexhub_clock'
HELPER = os.path.join(CONF, 'dexhub_clock.sh')
UNITS = os.path.join(CONF, 'system.d')
UNIT = os.path.join(UNITS, 'dexhub-clock.service')
LINK = os.path.join(UNITS, 'sysinit.target.wants', 'dexhub-clock.service')
OTHER_UNITS = ('abk-clock.service',)     # another add-on already keeps the clock

SERVERS = ('162.159.200.1', '216.239.35.0', '162.159.200.123', '216.239.35.4')
FLOOR = 1767225600       # 2026-01-01: a clock before this is stale
CEILING = 4102444800     # 2100-01-01: a clock after this is garbage
NTP_EPOCH = 2208988800
STEP_OVER = 2.0
SAVE_EVERY = 600
TRY_FOR = 240

HELPER_SH = '''#!/bin/sh
# Dex Hub: forward-only clock restore for boxes without an RTC.
S=%(stamp)s
[ -f %(off)s ] && exit 0
case "$1" in
  restore)
    [ -s "$S" ] || exit 0
    t=$(cat "$S" 2>/dev/null); n=$(date +%%s)
    case "$t" in ''|*[!0-9]*) exit 0;; esac
    [ "$t" -gt "$n" ] && date -u -s "@$t" >/dev/null 2>&1
    ;;
  save)
    n=$(date +%%s); o=$(cat "$S" 2>/dev/null)
    case "$o" in ''|*[!0-9]*) o=0;; esac
    [ "$n" -gt %(floor)d ] && [ "$n" -ge "$o" ] && echo "$n" > "$S.tmp" && mv "$S.tmp" "$S"
    ;;
esac
exit 0
''' % {'stamp': STAMP, 'off': OFF, 'floor': FLOOR}

UNIT_TEXT = '''[Unit]
Description=Dex Hub clock keeper
DefaultDependencies=no
After=local-fs.target
Before=sysinit.target connman.service kodi.service shutdown.target
Conflicts=shutdown.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh %(helper)s restore
ExecStop=/bin/sh %(helper)s save

[Install]
WantedBy=sysinit.target
''' % {'helper': HELPER}


def _debug(msg):
    xbmc.log('[DexHub] clock: %s' % msg, xbmc.LOGDEBUG)


def applies():
    if not os.path.isdir(CONF) or os.path.exists(OFF):
        return False
    try:
        with open('/etc/os-release') as fh:
            text = fh.read().lower()
    except Exception:
        return False
    if 'coreelec' not in text and 'libreelec' not in text:
        return False
    if glob.glob('/dev/rtc*'):
        return False
    return not any(os.path.exists(os.path.join(UNITS, n)) for n in OTHER_UNITS)


def _write(path, text, mode=None):
    try:
        with open(path) as fh:
            if fh.read() == text:
                return False
    except Exception:
        pass
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        fh.write(text)
    if mode:
        os.chmod(tmp, mode)
    os.replace(tmp, path)
    return True


def install():
    try:
        os.makedirs(os.path.dirname(LINK), exist_ok=True)
        _write(HELPER, HELPER_SH, 0o755)
        _write(UNIT, UNIT_TEXT)
        if os.path.realpath(LINK) != UNIT:
            if os.path.lexists(LINK):
                os.remove(LINK)
            os.symlink(UNIT, LINK)
    except Exception as exc:
        _debug('unit %s' % exc)


def save():
    now = int(time.time())
    if not FLOOR < now < CEILING:
        return
    try:
        with open(STAMP) as fh:
            if int(fh.read().strip() or 0) > now:
                return
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(STAMP), exist_ok=True)
        with open(STAMP + '.tmp', 'w') as fh:
            fh.write('%d\n' % now)
        os.replace(STAMP + '.tmp', STAMP)
    except Exception as exc:
        _debug('save %s' % exc)


def ask(server, timeout=2.0):
    """One SNTP query: the server's Unix time, corrected for the round trip."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sent = time.time()
        sock.sendto(b'\x23' + b'\0' * 47, (server, 123))      # v4, client
        data = sock.recv(64)
        back = time.time()
    except Exception:
        return None
    finally:
        sock.close()
    if len(data) < 48 or data[0] & 0x07 != 4 or data[1] in (0, 16):
        return None                                            # not a server / kiss-o'-death
    secs, frac = struct.unpack('!II', data[40:48])
    value = secs - NTP_EPOCH + frac / 4294967296.0
    if not FLOOR < value < CEILING:
        return None
    return value + (back - sent) / 2.0


class _Timespec(ctypes.Structure):
    _fields_ = [('tv_sec', ctypes.c_long), ('tv_nsec', ctypes.c_long)]


def _set(value):
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
        ts = _Timespec(int(value), int((value % 1) * 1e9))
        if libc.clock_settime(0, ctypes.byref(ts)) == 0:
            return True
    except Exception:
        pass
    try:
        return subprocess.call(['date', '-u', '-s', '@%d' % int(round(value))],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    except Exception:
        return False


def sync():
    for server in SERVERS:
        value = ask(server)
        if value is None:
            continue
        off = value - time.time()
        if abs(off) > STEP_OVER and _set(value):
            _debug('stepped %+.1f s' % off)
        return True
    return False


def run(monitor):
    if not applies():
        return
    install()
    started = time.time()
    while time.time() - started < TRY_FOR:
        if sync():
            break
        if monitor.waitForAbort(5):
            save()
            return
    save()
    while not monitor.waitForAbort(SAVE_EVERY):
        save()
    save()
