# -*- coding: utf-8 -*-
"""The end-of-episode panel (v5.10.217), as in the Nuvio and Netflix apps.

When the credits of an episode start, the video goes on in a frame on the
left and the next episode waits on the right, with a countdown that plays it
when it runs out. After a film the right side offers three titles like it
instead. «Watch credits» (or Back) gives the video its whole screen again.

When (Following and library: End of episode panel):
  'auto'  when the credits start: a chapter named for them (Credits, End
          Credits, Outro...), else a little before the end (about 3 % of the
          length: half a minute to a minute and a half for an episode, one to
          four minutes for a film)
  'end'   only in the last seconds
  'off'   never (the plain prompt at the end, or service.upnext, as before)

The service runs ``watch(monitor)``. It looks once a second while a video
plays full screen, and opens the panel once per video. Trailers, live
channels, short videos and files Dex Hub cannot name are left alone. With
the panel on, Up Next is not told (v5.10.218): one prompt, not two. The
credits start where TheIntroDB says, else at a chapter named for them, else
at the timing above.

The panel is a full window, not a dialog: only a window's videowindow makes
the playing video smaller (dexhub_credits.xml).
"""
import threading
import time

import xbmc
import xbmcgui

BUTTONS, PICKS, BAR = 100, 200, 310
CHAPTER_WORDS = ('credit', 'outro', 'ending', 'end titles', 'generique', 'générique', 'abspann',
                 'التترات', 'الشارة')
DONE_PROP = 'dexhub.credits.done'
_seen = {'shown': False}


def _addon():
    import xbmcaddon
    return xbmcaddon.Addon('plugin.video.dexhub')


def _setting(key, default=''):
    try:
        return (_addon().getSetting(key) or default).strip()
    except Exception:
        return default


def mode():
    value = _setting('credits_panel', 'auto').lower()
    return value if value in ('auto', 'end', 'off') else 'auto'


def _tr(text):
    try:
        from .i18n import tr
        return tr(text)
    except Exception:
        return text


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub][credits] %s' % msg, level)


def _cond(expr):
    try:
        return xbmc.getCondVisibility(expr)
    except Exception:
        return False


def _info(label):
    try:
        return xbmc.getInfoLabel(label) or ''
    except Exception:
        return ''


def _home():
    return xbmcgui.Window(10000)


def owns_end():
    """v5.10.218: with the panel on, the end of an episode is Dex Hub's:
    service.upnext gets no upnext_data (companion.py), so the two never
    open over each other. «Off» gives the end back to Up Next."""
    return mode() != 'off'


def _lead(duration, series):
    """Seconds before the end at which the credits are taken to start."""
    if series:
        return max(30.0, min(90.0, duration * 0.03))
    return max(60.0, min(240.0, duration * 0.03))


def _in_credits_chapter():
    try:
        if int(_info('Player.ChapterCount') or 0) < 2:
            return False
        name = _info('Player.ChapterName').strip().lower()
    except ValueError:
        return False
    if not name:
        return False
    # the last chapters only (a "Credits" chapter in the middle is the opening's)
    try:
        if int(_info('Player.Chapter') or 0) < int(_info('Player.ChapterCount') or 0) - 1:
            return False
    except ValueError:
        return False
    return any(word in name for word in CHAPTER_WORDS)


def _focus_next_in_playlist(was_open):
    """v5.10.221: the player's Episodes button opens the OSD's own episodes
    panel (1140, no extra window or calls): once its list has loaded, the
    episode after the playing one gets the focus. Returns whether it is open."""
    if not _cond('Window.IsVisible(1140)'):
        return False
    if was_open:
        return True
    try:
        current = int(_info('VideoPlayer.Episode') or 0)
    except ValueError:
        current = 0
    deadline = time.time() + 4.0
    monitor = xbmc.Monitor()
    while time.time() < deadline and _cond('Window.IsVisible(1140)'):
        try:
            count = int(_info('Container(7000).NumItems') or 0)
        except ValueError:
            count = 0
        if count and not _cond('Container(7000).IsUpdating'):
            if current <= 0:
                return True
            target = -1
            for i in range(count):
                try:
                    number = int(_info('Container(7000).ListItemAbsolute(%d).Episode' % i) or 0)
                except ValueError:
                    continue
                if number == current + 1:
                    target = i
                    break
                if number == current:
                    target = i
            # only while the user has not moved yet (the list opens on the first)
            if target > 0 and int(_info('Container(7000).CurrentItem') or 1) == 1:
                xbmc.executebuiltin('SetFocus(7000,%d,absolute)' % target)
            return True
        if monitor.waitForAbort(0.15):
            return True
    return True


def _is_trailer(player):
    try:
        from . import trailer_mark
        return trailer_mark.playing_is_trailer(player)
    except Exception:
        return False


def _introdb_params(playing, total):
    """Build a v4 request without guessing an id namespace or episode order."""
    import math
    import re

    def number(value, minimum, maximum):
        text = str(value).strip()
        if not re.fullmatch(r'[0-9]+', text):
            return None
        value = int(text)
        return value if minimum <= value <= maximum else None

    try:
        total = float(total)
        if not math.isfinite(total) or total <= 0:
            return []
        q = playing.get('q') or {}
        # Existing Dex Hub coordinates come from TMDB. Keep that namespace:
        # v4 resolves its numbering too; relabelling a show id as TVDB is wrong.
        tmdb = number(q.get('tmdb'), 1, 10000000)
        params = [('tmdb_id', tmdb)] if tmdb is not None else []
        imdb = str(q.get('imdb') or '').split(':', 1)[0]
        if not params and re.fullmatch(r'tt[0-9]{7,8}', imdb):
            # v4 resolves IMDb server-side; no preliminary TMDB request needed.
            params = [('imdb_id', imdb)]
        if not params:
            tvdb = number(q.get('tvdb') or q.get('tvdb_id'), 1, 100000000)
            if tvdb is None:
                return []
            params = [('tvdb_id', tvdb)]
        if playing.get('series'):
            season = number(playing.get('season'), 0, 1000)
            episode = number(playing.get('episode'), 0, 10000)
            if season is None or episode is None:
                return []
            params += [('season', season), ('episode', episode)]
            order = (playing.get('episode_order') or q.get('episode_order')
                     or playing.get('displayorder') or q.get('displayorder') or '')
            if order:
                order = re.sub(r'[\s_-]', '', str(order)).lower()
                aliases = {'officialorder': 'official', 'aired': 'official', 'airedorder': 'official',
                           'altdvd': 'dvd', 'dvdorder': 'dvd', 'absoluteorder': 'absolute', 'abso': 'absolute',
                           'alternateorder': 'alternate', 'alttwo': 'alternate', 'streaming': 'alternate',
                           'streamingorder': 'alternate', 'regionalorder': 'regional', 'production': 'regional',
                           'productionorder': 'regional', 'defaultorder': 'default'}
                order = aliases.get(order, order)
                if order not in ('default', 'official', 'dvd', 'alternate', 'absolute', 'regional'):
                    return []       # don't silently request the wrong episode
                params.append(('mapping', order))
        params.append(('duration_ms', int(total * 1000)))
        return params
    except (AttributeError, TypeError, ValueError, OverflowError):
        return []


def _introdb_credits(playing, total):
    """When the credits start (seconds), from TheIntroDB, or 0. Asked once
    per video, ten minutes before its end; most episodes are not there
    yet, and then the chapters or the timing decide."""
    from .introdb_client import lookup
    rows = lookup(playing, total).get('credits', [])
    starts = [a for a, b in rows if total * 0.5 < a < total - 4]
    return int(max(starts)) if starts else 0


# ------------------------------------------------------------------ data
def _playing():
    try:
        from .skinui import osd
        playing = osd._playing() or {}
    except Exception as exc:
        _log('playing title unknown: %s' % exc, xbmc.LOGDEBUG)
        return {}
    if not playing:
        return {}
    try:
        # Only this late, once-per-video lookup needs the extra API metadata.
        # An episode's generic tvdb unique id is NOT the series' tvdb id.
        item = xbmc.Player().getPlayingItem()
        tag = item.getVideoInfoTag()
        series = bool(playing.get('series'))
        tvdb = ((item.getProperty('tvshow.tvdb_id') or item.getProperty('show_tvdb_id')
                 or tag.getUniqueID('tvshow.tvdb')) if series else tag.getUniqueID('tvdb'))
        if tvdb:
            playing = dict(playing, q=dict(playing.get('q') or {}))
            playing['q'].setdefault('tvdb', tvdb)
        if series:
            order = item.getProperty('episode_order') or item.getProperty('displayorder')
            if order:
                playing = dict(playing, episode_order=order)
    except Exception:
        pass
    return playing


def _next_episode(playing):
    """{'path', 'season', 'episode', 'title', 'still', 'plot'} or {}."""
    try:
        from .skinui import osd
        nxt = osd._next_item() or {}
    except Exception as exc:
        _log('next episode unknown: %s' % exc, xbmc.LOGDEBUG)
        return {}
    if not nxt.get('path'):
        return {}
    q = playing.get('q') or {}
    season, episode = int(nxt.get('season') or 0), int(nxt.get('episode') or 0)
    art = nxt.get('art') or {}
    still, name, plot = art.get('thumb') or art.get('landscape') or '', '', ''
    try:
        from . import tmdb_direct, episode_titles
        tmdb, imdb = str(q.get('tmdb') or ''), str(q.get('imdb') or '')
        still = tmdb_direct.episode_still(tmdb_id=tmdb, imdb_id=imdb, season=season, episode=episode) or still
        # not begun yet: «Episodes you started» keeps it a number
        name = episode_titles.pick(nxt.get('title') or '', started=False, tmdb_id=tmdb, imdb_id=imdb,
                                   season=season, episode=episode)
        if episode_titles.wanted(False):
            rows = tmdb_direct.season_episodes(tmdb, imdb, season, True, tmdb_direct._card_language()) or {}
            plot = str((rows.get(str(episode)) or {}).get('overview') or '')
    except Exception:
        pass
    return {'path': nxt['path'], 'season': season, 'episode': episode, 'title': name, 'still': still,
            'plot': plot}


def _picks(playing):
    """Titles like the film that is ending (TMDb's picks): [item]."""
    q = playing.get('q') or {}
    tmdb = str(q.get('tmdb') or '')
    if not tmdb and q.get('imdb'):
        try:
            from . import tmdb_direct as T
            tmdb = str(T._resolve_tmdb_id(imdb_id=q['imdb'], media_type='movie') or '')
        except Exception:
            tmdb = ''
    if not tmdb.isdigit():
        return []
    try:
        from .homeui.follow import recommendations
        return [p for p in recommendations('movie', tmdb) if p.get('poster')][:3]
    except Exception as exc:
        _log('no picks: %s' % exc, xbmc.LOGDEBUG)
        return []


# ------------------------------------------------------------------ window
class CreditsPanel(xbmcgui.WindowXML):

    def __init__(self, *args, **kwargs):
        super(CreditsPanel, self).__init__(*args, **kwargs)
        self.kind = kwargs.get('kind')
        self.data = kwargs.get('data') or {}
        self.seconds = int(kwargs.get('seconds') or 0)
        self.result = ''
        self.closed = False
        self._stop = threading.Event()

    def onInit(self):
        _seen['shown'] = True
        d = self.data
        self.setProperty('cp.kind', self.kind)
        self.setProperty('cp.backdrop', d.get('backdrop') or '')
        self.setProperty('cp.now', d.get('now') or '')
        self.setProperty('cp.credits', _tr('التترات شغالة هنا'))
        buttons = self.getControl(BUTTONS)
        buttons.reset()
        if self.kind == 'episode':
            self.setProperty('cp.kicker', _tr('الحلقة التالية'))
            self.setProperty('cp.show', d.get('show') or '')
            self.setProperty('cp.logo', d.get('logo') or '')
            code = 'S%d:E%d' % (d['season'], d['episode'])
            self.setProperty('cp.episode', '  ·  '.join(x for x in (code, d.get('title') or '') if x))
            self.setProperty('cp.still', d.get('still') or d.get('backdrop') or '')
            self.setProperty('cp.plot', d.get('plot') or '')
            buttons.addItems([xbmcgui.ListItem(_tr('▶  شغّل الحلقة')), xbmcgui.ListItem(_tr('شاهد التترات'))])
            self.setFocusId(BUTTONS)
            if self.seconds > 0:
                threading.Thread(target=self._countdown, name='DexHub-credits', daemon=True).start()
        else:
            self.setProperty('cp.kicker', _tr('شاهد بعدها'))
            self.setProperty('cp.title', _tr('أعمال تشبه %s') % (d.get('show') or ''))
            self.setProperty('cp.sub', _tr('اختر واحد يفتح صفحته، أو كمّل التترات'))
            picks = self.getControl(PICKS)
            picks.reset()
            items = []
            for p in d.get('picks') or []:
                li = xbmcgui.ListItem(p.get('title') or '', p.get('line') or '')
                li.setArt({'poster': p.get('poster') or '', 'thumb': p.get('poster') or ''})
                items.append(li)
            picks.addItems(items)
            buttons.addItems([xbmcgui.ListItem(_tr('شاهد التترات')), xbmcgui.ListItem(_tr('إغلاق'))])
            self.setFocusId(PICKS)
            threading.Thread(target=self._watch_end, name='DexHub-credits', daemon=True).start()

    def _countdown(self):
        start, total = time.time(), float(self.seconds)
        monitor = xbmc.Monitor()
        try:
            bar = self.getControl(BAR)
        except Exception:
            bar = None
        while not self._stop.is_set() and not monitor.abortRequested():
            left = total - (time.time() - start)
            if left <= 0:
                self._finish('next')
                return
            self.setProperty('cp.count', _tr('تبدأ خلال %d ث') % int(left + 0.99))
            if bar is not None:
                try:
                    bar.setPercent(100.0 * (total - left) / total)
                except Exception:
                    pass
            if self._stop.wait(0.2):
                return

    def _watch_end(self):
        """After a film: the panel goes when the video does."""
        while not self._stop.is_set():
            if not _cond('Player.HasVideo'):
                self._finish('closed')
                return
            if self._stop.wait(0.5):
                return

    def close(self):
        self.closed = True
        super(CreditsPanel, self).close()

    def _finish(self, result):
        if self.result:
            return
        self.result = result
        self._stop.set()
        self.close()

    def onClick(self, control_id):
        if control_id == BUTTONS:
            pos = self.getControl(BUTTONS).getSelectedPosition()
            if self.kind == 'episode':
                self._finish('next' if pos == 0 else 'credits')
            else:
                self._finish('credits' if pos == 0 else 'close')
        elif control_id == PICKS:
            pos = self.getControl(PICKS).getSelectedPosition()
            picks = self.data.get('picks') or []
            if 0 <= pos < len(picks):
                self.data['chosen'] = picks[pos]
                self._finish('open')

    def onAction(self, action):
        if action.getId() in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU, 92, 10):
            self._finish('credits')


# ------------------------------------------------------------------ showing
def show(kind, data, seconds=0):
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    window = CreditsPanel('dexhub_credits.xml', path, 'Default', '1080i', kind=kind, data=data, seconds=seconds)
    # v5.10.221: show() and a wait, not doModal(): Kodi refuses a window
    # while a modal dialog is up (TheIntroDB's skip button), and a refused
    # doModal() never returned (the watcher stopped for good)
    _seen['shown'] = False
    monitor = xbmc.Monitor()
    window.show()
    deadline = time.time() + 2.0
    refused = False
    while not window.closed and not monitor.abortRequested():
        if not _seen['shown'] and time.time() > deadline:
            refused = True
            try:
                window.close()
            except Exception:
                pass
            break
        if monitor.waitForAbort(0.1):
            break
    result, chosen = window.result or 'credits', window.data.get('chosen')
    window._stop.set()
    del window
    if refused:
        _log('panel %s: refused, will try again' % kind)
        return 'refused'
    _log('panel %s: %s' % (kind, result))
    if result == 'next':
        _home().setProperty(DONE_PROP, str(time.time()))
        xbmc.executebuiltin('RunPlugin("%s")' % data['path'].replace('"', '%22'))
    elif result == 'open' and chosen:
        from urllib.parse import urlencode
        q = dict(chosen.get('open') or {})
        try:
            xbmc.Player().stop()
        except Exception:
            pass
        query = urlencode([('action', 'skin_open')] + sorted(q.items()))
        xbmc.sleep(400)
        xbmc.executebuiltin('RunPlugin("plugin://plugin.video.dexhub/?%s")' % query)
    elif result == 'close':
        try:
            xbmc.Player().stop()
        except Exception:
            pass
    elif _cond('Player.HasVideo'):
        xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
    return result


def _open_for(playing, total):
    series = bool(playing.get('series'))
    q = playing.get('q') or {}
    now = q.get('title') or ''
    backdrop = _info('Player.Art(fanart)') or _info('Player.Art(landscape)')
    logo = _info('Player.Art(clearlogo)') or _info('Player.Art(tvshow.clearlogo)')
    if series:
        nxt = _next_episode(playing)
        if not nxt:
            return ''
        if playing.get('season', -1) >= 0 and playing.get('episode', -1) > 0:
            now = '%s  ·  S%d:E%d' % (now, playing['season'], playing['episode'])
        data = dict(nxt, show=q.get('title') or '', logo=logo, backdrop=nxt.get('still') or backdrop, now=now)
        auto = _setting('auto_next_episode', 'true').lower() == 'true'
        try:
            seconds = max(5, int(_setting('auto_next_prompt_seconds', '20') or 20)) if auto else 0
        except ValueError:
            seconds = 20 if auto else 0
        return show('episode', data, seconds)
    picks = _picks(playing)
    if not picks:
        return ''
    data = {'show': q.get('title') or '', 'picks': picks, 'backdrop': backdrop, 'now': now}
    return show('movie', data)


def watch(monitor):
    """The service's watcher: one panel per video, at its credits."""
    player = xbmc.Player()
    current, done, playing, total, credits_at, tries = '', False, None, 0.0, 0, 0
    playlist_open = False
    # v5.10.217: the island's «carry on» after a video stopped half way
    last, shown = {}, False
    from . import island
    while not monitor.abortRequested():
        if monitor.waitForAbort(1.0):
            return
        try:
            island.refresh()
        except Exception:
            pass
        try:
            video = player.isPlayingVideo()
        except Exception:
            video = False
        # v5.10.235: the OSD's episodes panel only opens over a playing video
        if video or playlist_open:
            try:
                playlist_open = _focus_next_in_playlist(playlist_open)
                video = player.isPlayingVideo()     # it may have waited for the panel
            except Exception:
                pass
        try:
            if not video:
                if last and last.get('total', 0) >= 600 and last.get('pos', 0) >= 60 \
                        and last['total'] - last['pos'] > 90 and not last.get('trailer') and not shown:
                    stopped_at = time.time()
                    if not monitor.waitForAbort(2.0):
                        try:
                            island.stopped(stopped_at - 10)
                        except Exception as exc:
                            _log('island: %s' % exc, xbmc.LOGDEBUG)
                last, shown = {}, False
                current, done, playing = '', False, None
                continue
            try:
                last = {'total': float(player.getTotalTime() or 0), 'pos': float(player.getTime() or 0),
                        'trailer': _home().getProperty('dexhub.trailer.active') == '1'}
            except Exception:
                pass
            # something else plays (not a trailer behind the Home): what was
            # stopped is not waiting any more
            if _home().getProperty('dhs.island.resume') and _home().getProperty('dexhub.trailer.active') != '1' \
                    and not _is_trailer(player):
                island.clear()
            try:
                path = player.getPlayingFile() or ''
            except Exception:
                path = ''
            if path != current:
                current, done, playing, shown = path, False, None, False
                total, credits_at, tries = 0.0, 0, 0
            if done or mode() == 'off':
                continue
            if _home().getProperty('dexhub.trailer.active') == '1' or _is_trailer(player):
                done = True
                continue
            if not total:
                try:
                    total = float(player.getTotalTime() or 0)
                except Exception:
                    total = 0.0
                if total < 600:          # live channels, clips, trailers
                    done = total > 0
                    total = 0.0 if not done else total
                    continue
            if not _cond('Window.IsActive(fullscreenvideo)') or _cond('System.HasModalDialog') \
                    or _cond('Window.IsActive(videoosd)') or _cond('Player.Paused'):
                continue
            try:
                left = total - float(player.getTime())
            except Exception:
                continue
            if playing is None:
                if left > 600:
                    continue
                playing = _playing()        # once per video, near its end
                if not playing:
                    done = True
                    continue
                credits_at = _introdb_credits(playing, total)
                if credits_at:
                    _log('credits from TheIntroDB at %ds of %ds' % (credits_at, total))
            if mode() == 'end':
                at = 4.0
            elif credits_at:
                at = max(4.0, total - credits_at)
            else:
                at = _lead(total, bool(playing.get('series')))
            if left <= at or (mode() == 'auto' and _in_credits_chapter()):
                done = shown = True
                if _open_for(playing, total) == 'refused' and tries < 6:
                    # another add-on's dialog was in front: close it, try again
                    tries += 1
                    done = shown = False
                    xbmc.executebuiltin('Dialog.Close(all)')
                    monitor.waitForAbort(1.5)
        except Exception as exc:
            _log('watch: %s' % exc, xbmc.LOGWARNING)
            done = True
