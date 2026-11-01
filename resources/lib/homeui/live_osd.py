# -*- coding: utf-8 -*-
"""The live channel's panel in the player (v5.10.458), after Lucent's live TV.

While a channel watched from Live TV plays, Window(Home) dhl.* properties
describe it and skin.dexhub's VideoOSD shows its DhLiveOSD panel in place of
the usual design: the channel with its number and logo, LIVE, the programme
on now with its times, progress and what is left, what comes next, and the
channels watched last (OK on one switches to it, through dhl.switch).

  dhl.on, dhl.name, dhl.number, dhl.logo, dhl.group, dhl.top
  dhl.title, dhl.plot, dhl.start, dhl.end, dhl.left, dhl.step (0-40)
  dhl.next, dhl.next.title
  dhl.r1..dhl.r8 (.name, .logo): the channels watched last
  dhl.c1..dhl.c24 (.name, .logo, .on): the channels of the group on the guide,
  around the one playing (v5.10.460); dhl.groupline names the group
  dhl.live, dhl.recent, dhl.guide (words)
  dhl.switch: 'r3' or 'c5' (written by the skin) switches to that channel

The page's watch thread calls tick() 4 times a second, the page covered or not.
"""
import time

import xbmc
import xbmcgui

PREFIX = 'dhl.'
STEPS = 40
RECENT = 8
GROUP = 24
EVERY = 5.0


def _t(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _clock(epoch):
    return time.strftime('%H:%M', time.localtime(int(epoch)))


def _left(seconds):
    minutes = max(0, int(seconds // 60))
    hours, rest = divmod(minutes, 60)
    if hours:
        return _t('باقي %dس %dد', '%dh %dm left') % (hours, rest)
    return _t('باقي %d د', '%dm left') % rest


class LiveOSD(object):
    def __init__(self, app):
        self.app = app
        self.home = xbmcgui.Window(10000)
        self._on = False
        self._read_at = 0.0
        self._file_at = 0.0
        self._playing = ''
        self._recent = []
        self._group = []
        self._first = 0

    def _set(self, name, value):
        self.home.setProperty(PREFIX + name, value if value else '')

    def clear(self):
        if not self._on:
            return
        self._on = False
        for name in ('on', 'name', 'number', 'logo', 'group', 'title', 'plot', 'start', 'end', 'left', 'step',
                     'next', 'next.title', 'switch', 'live', 'recent', 'guide', 'stop', 'top', 'chnum'):
            self._set(name, '')
        for n in range(1, RECENT + 1):
            self._set('r%d.name' % n, '')
            self._set('r%d.logo' % n, '')
        for n in range(1, GROUP + 1):
            for field in ('name', 'logo', 'on', 'num'):
                self._set('c%d.%s' % (n, field), '')
        self._set('groupline', '')

    def tick(self, window):
        watching = getattr(self.app, 'live_watching', None)
        if not watching:
            self.clear()
            return
        now = time.monotonic()
        if now - self._file_at >= 1.0:
            self._file_at = now
            try:
                player = xbmc.Player()
                self._playing = (player.getPlayingFile() or '').split('|', 1)[0] if player.isPlayingVideo() else ''
            except Exception:
                self._playing = ''
        if not self._playing or self._playing != watching.get('url'):
            self.clear()
            return
        if self._on and xbmc.getCondVisibility('Window.IsActive(videoosd)') and \
                not xbmc.getInfoLabel('System.CurrentControlId'):
            # v5.10.475: the panel opened with nothing focused (Kodi keeps the
            # OSD in memory and its onload can come too early): OK did nothing
            xbmc.executebuiltin('SetFocus(%d)' % (6101 if xbmc.getCondVisibility('Player.CanPause') else 6103))
        asked = self.home.getProperty(PREFIX + 'switch')
        if asked:
            self._set('switch', '')
            self._switch(window, asked)
            return
        if self._on and now - self._read_at < EVERY:
            return
        self._read_at = now
        try:
            self._publish(watching.get('tile') or {}, window)
        except Exception:
            xbmc.log('[DexHub] live osd failed', xbmc.LOGWARNING)

    def _switch(self, window, asked):
        if asked == 'guide':
            # v5.10.475: the panel's TV guide: out of full screen once the
            # panel has closed (in the same click Kodi ignores the switch)
            xbmc.executebuiltin('Dialog.Close(videoosd,true)')
            xbmc.sleep(250)
            if xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)'):
                xbmc.executebuiltin('Action(FullScreen)')
            return
        tiles = self._group if asked[:1] == 'c' else self._recent
        try:
            index = int(asked.lstrip('rc')) - 1
        except ValueError:
            return
        if 0 <= index < len(tiles) and hasattr(window, '_switch_channel'):
            tile = tiles[index]
            self.app.live_watching = None
            self.clear()
            window._switch_channel(tile)

    def _programmes(self, tile):
        from .guide import cached_guide
        items = cached_guide(tile.get('path')) or []
        if not items:
            items = [p for p in (tile.get('now'), tile.get('next')) if p and p.get('start') and p.get('end')]
        return sorted(items, key=lambda p: p.get('start') or 0)

    def _publish(self, tile, window=None):
        now = time.time()
        programmes = self._programmes(tile)
        current = next((p for p in programmes if p['start'] <= now < p['end']), None)
        coming = next((p for p in programmes if p['start'] >= now), None)
        self._set('name', tile.get('title') or '')
        # v5.10.461: the channel's number: its own, else its place in the
        # group on the guide (the number the guide's rows show)
        number = str(tile.get('number') or '') or self._place(tile, window)
        self._set('number', number)
        self._set('chnum', (_t('قناة %s', 'CH %s') % number) if number else '')
        self._set('logo', tile.get('logo') or '')
        self._set('group', tile.get('group') or '')
        # the top line names the channel over its programme; with no programme
        # known the channel is the title and its group goes on top
        self._set('top', (tile.get('title') if current else (tile.get('group') or '')) or '')
        if current:
            done = (now - current['start']) / float(max(1, current['end'] - current['start']))
            self._set('title', current.get('title') or tile.get('title') or '')
            self._set('plot', current.get('plot') or '')
            self._set('start', _clock(current['start']))
            self._set('end', _clock(current['end']))
            self._set('left', _left(current['end'] - now))
            self._set('step', str(max(1, min(STEPS, int(round(done * STEPS))))))
        else:
            self._set('title', tile.get('title') or '')
            self._set('plot', tile.get('desc') or '')
            for name in ('start', 'end', 'left', 'step'):
                self._set(name, '')
        if coming:
            self._set('next', '%s · %s' % (_t('بعده', 'Next'), _clock(coming['start'])))
            self._set('next.title', coming.get('title') or '')
        else:
            self._set('next', '')
            self._set('next.title', '')
        self._recent = self._recent_tiles(tile)
        for n in range(1, RECENT + 1):
            other = self._recent[n - 1] if n <= len(self._recent) else {}
            self._set('r%d.name' % n, other.get('title') or '')
            self._set('r%d.logo' % n, other.get('logo') or '')
        self._group = self._group_tiles(tile, window)
        playing = tile.get('path')
        for n in range(1, GROUP + 1):
            other = self._group[n - 1] if n <= len(self._group) else {}
            self._set('c%d.name' % n, other.get('title') or '')
            self._set('c%d.logo' % n, other.get('logo') or '')
            self._set('c%d.on' % n, '1' if other and other.get('path') == playing else '')
            self._set('c%d.num' % n, (str(other.get('number') or '') or str(self._first + n)) if other else '')
        cat = getattr(window, 'cat', None) or {}
        self._set('groupline', (cat.get('title') or _t('القنوات', 'Channels')) if self._group else '')
        self._set('live', _t('مباشر', 'LIVE'))
        self._set('recent', _t('آخر القنوات', 'RECENT'))
        self._set('guide', _t('الدليل', 'TV guide'))
        self._set('stop', _t('إيقاف', 'Stop'))
        self._set('on', '1')
        self._on = True

    def _group_tiles(self, tile, window):
        """The guide's channels around the one playing (its category on the guide)."""
        channels = list(getattr(window, 'channels', None) or [])
        if not channels:
            return []
        here = next((i for i, t in enumerate(channels) if t.get('path') == tile.get('path')), 0)
        start = max(0, min(here - 4, len(channels) - GROUP))
        self._first = start
        return channels[start:start + GROUP]

    @staticmethod
    def _place(tile, window):
        channels = list(getattr(window, 'channels', None) or [])
        index = next((i for i, t in enumerate(channels) if t.get('path') == tile.get('path')), -1)
        return str(index + 1) if index >= 0 else ''

    def _recent_tiles(self, tile):
        """The channels watched last from the same source, this one left out."""
        from . import live_sources
        src = tile.get('src') or ''
        if not src:
            return []
        try:
            tiles = live_sources.recent_tiles(self.app.profile, src, self.app.media_path('channel_card.jpg'))
        except Exception:
            return []
        return [t for t in tiles if t.get('path') != tile.get('path')][:RECENT]
