# -*- coding: utf-8 -*-
"""v5.10.365: the Home made ready while the screensaver runs.

The box is idle then. Once per screensaver (45 s after it starts, not while a
video plays) the service reads again the Home's rows that are old, the tab on
show first, gives the first rows their TMDb pass, and lists the pictures those
rows show. The screensaver window then loads those pictures one after another
in two hidden image controls, so Kodi has them in its picture cache: back on
the Home the rows and their posters are there at once.

Since v5.10.255 nothing is read while the screensaver runs (the rows were read
all night for nobody); this is one pass per screensaver, and it stops the
moment the screensaver ends.

Setting: Look, "Prepare the Home during the screensaver" (on).
"""
import os
import threading
import time

import xbmc

from . import common as C

START_AFTER = 45.0          # seconds of screensaver before the pass
CHECK_EVERY = 10.0
ROWS_ON_TAB = 8             # rows of the tab on show read again
ROWS_OTHER = 3              # first rows of the other main tabs
PICTURE_ROWS = 6            # rows whose pictures are loaded
PICTURES_PER_ROW = 16
MAX_PICTURES = 320
KEEP = 3 * 3600             # a list older than this is not used
FEED_GAP = 0.6              # seconds between two pictures on one control
CONTROLS = (990, 991)
_FILE = 'ss_preload.json'
_ART_KEYS = ('poster', 'landscape', 'fanart', 'clearlogo', 'thumb')


def enabled():
    try:
        from ..ui_preferences import enabled as on
        return on('homeui_ss_preload')
    except Exception:
        return True


def _path():
    return os.path.join(C.folder(), _FILE)


def _screensaver():
    try:
        return bool(xbmc.getCondVisibility('System.ScreenSaverActive')
                    and not xbmc.getCondVisibility('Player.HasVideo'))
    except Exception:
        return False


# ------------------------------------------------------------ the service side
def tick(worker, now):
    """Called by the worker's loop: one pass per screensaver."""
    state = worker.__dict__.setdefault('_ss_preload', {'since': 0.0, 'done': False, 'checked': 0.0})
    if now - state['checked'] < CHECK_EVERY:
        return
    state['checked'] = now
    if not _screensaver():
        state['since'], state['done'] = 0.0, False
        return
    if not state['since']:
        state['since'] = now
        return
    if state['done'] or now - state['since'] < START_AFTER:
        return
    state['done'] = True
    if not enabled():
        return
    try:
        run_pass(worker)
    except Exception:
        from .. import lazy_tb as traceback
        C.log('screensaver preload:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)


def _order():
    tab = C.prop('dhs.tab') or 'all'
    if tab not in C.TABS:
        tab = 'all'
    others = [t for t in ('all', 'movie', 'series') if t != tab]
    return [(tab, ROWS_ON_TAB)] + [(t, ROWS_OTHER) for t in others]


def run_pass(worker):
    from . import rows
    started = time.monotonic()
    read = 0
    for tab, depth in _order():
        for slot, spec in rows.published(tab)[:depth]:
            if worker.stopped() or worker.busy() or not _screensaver():
                C.log('screensaver preload stopped after %d row(s)' % read)
                return
            data = C.read_row(C.row_file(tab, spec['id']))
            if C.fresh_copy(data, rows.ttl_of(spec)):
                continue
            worker.refresh(tab, spec['id'], spec, enrich=slot <= 4)
            read += 1
            if worker.rest(0.2):
                return
    found = pictures()
    C.write_json(_path(), {'t': time.time(), 'urls': found})
    C.set_prop('dhs.ss.preload', '%.0f' % time.time())
    C.log('screensaver preload: %d row(s) read, %d picture(s) listed in %.1f s'
          % (read, len(found), time.monotonic() - started))


def _display(item, art):
    if (item.get('props') or {}).get('kind') == 'work':
        try:
            from ..home_art import display_art
            art = display_art(art)
            from ..art_choice import apply_item
            art = apply_item(item, art)
        except Exception:
            pass
    return art


def pictures():
    """The pictures of the first rows of the tab on show (and its spotlight),
    as the skin asks for them (the same addresses: Kodi's cache is by address)."""
    from . import rows
    from . import board
    tab = _order()[0][0]
    out, seen = [], set()

    def add(item):
        art = _display(item, dict((k, v) for k, v in (item.get('art') or {}).items() if v))
        for key in _ART_KEYS:
            url = art.get(key)
            if url and url not in seen and not str(url).startswith(('special://', 'resource://')):
                seen.add(url)
                out.append(url)

    spot = C.read_row(C.row_file(tab, board.SPOT)) or {}
    for item in (spot.get('items') or [])[:PICTURES_PER_ROW]:
        add(item)
    for _slot, spec in rows.published(tab)[:PICTURE_ROWS]:
        data = C.read_row(C.row_file(tab, spec['id'])) or {}
        for item in (data.get('items') or [])[:PICTURES_PER_ROW]:
            add(item)
        if len(out) >= MAX_PICTURES:
            break
    return out[:MAX_PICTURES]


# ------------------------------------------------------------ the screensaver side
def listed():
    """The pictures the service listed for this screensaver ([] when none or old)."""
    if not enabled():
        return []
    data = C.read_row(_path()) or {}
    if time.time() - float(data.get('t') or 0) > KEEP:
        return []
    return list(data.get('urls') or [])


def feed(window, done):
    """Load the listed pictures in the window's hidden image controls (one
    thread, until the screensaver ends). The list may appear a while after
    the screensaver opened (the service's pass): it is looked for again."""
    def job():
        given = set()
        turn = 0
        while not done.wait(2.0):
            urls = [u for u in listed() if u not in given]
            if not urls:
                continue
            for url in urls:
                if done.is_set():
                    return
                try:
                    window.getControl(CONTROLS[turn % len(CONTROLS)]).setImage(url, True)
                except Exception:
                    return          # this window has no hidden controls
                given.add(url)
                turn += 1
                if done.wait(FEED_GAP / len(CONTROLS)):
                    return
    threading.Thread(target=job, name='DexHub-ss-preload', daemon=True).start()


HIDDEN_XML = ''.join('<control type="image" id="%d"><left>0</left><top>0</top><width>4</width><height>4</height>'
                     '<colordiffuse>01FFFFFF</colordiffuse><aspectratio>scale</aspectratio>'
                     '<texture background="true"></texture></control>' % n for n in CONTROLS)
