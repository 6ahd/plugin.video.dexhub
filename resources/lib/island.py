# -*- coding: utf-8 -*-
"""skin.dexhub's island (v5.10.217, a bell since v5.10.218).

A small round button among the Home's top icons, never over the tabs:

  playing   a video plays behind the Home: a pulsing dot and a thin bar
  bell      something is waiting: carry on with what was stopped, today's
            new episodes; a badge says how many

When something new comes, a banner drops under it for a few seconds (the
toast), then it folds back into the bell. Pressing the bell opens the inbox
(dexhub_inbox.xml): every notice as a banner, OK opens its title.

Home window properties (the skin reads them):
  dhs.island.count               how many notices
  dhs.island.kind/title/sub/...  the first one (resume | newep | '')
  dhs.island.toast               1 while the banner shows
  dhs.island.toast.*             the banner's kicker, title, sub, poster
"""
import datetime
import json
import threading
import time
from urllib.parse import urlencode

import xbmc
import xbmcgui

RESUME_SECONDS = 15 * 60
TOAST_SECONDS = 7.0
LIST, BUTTONS = 300, 100
# v5.10.235: bumped by whatever changes what the notices are made of (in any
# interpreter); the service's every-second refresh reads this one property
# and works the notices out again only when it moved, once a minute (a
# notice running out, a new day), or while a banner shows or waits
VERSION = 'dhs.island.v'
_STATE = {'v': None, 'at': 0.0, 'busy': True}


def _home():
    return xbmcgui.Window(10000)


def _tr(text):
    try:
        from .i18n import tr
        return tr(text)
    except Exception:
        return text


def _english():
    from .i18n import is_english
    return is_english()


def _t(ar, en):
    return en if _english() else ar


def changed():
    """What the notices are made of changed: the next refresh reads it again."""
    try:
        _home().setProperty(VERSION, '%.6f' % time.time())
    except Exception:
        pass


def _json(prop):
    try:
        value = json.loads(_home().getProperty(prop) or 'null')
    except ValueError:
        value = None
    return value


def _set(kind, title='', sub='', poster='', action='', kicker=''):
    home = _home()
    if home.getProperty('dhs.island.kicker') != kicker:
        home.setProperty('dhs.island.kicker', kicker)
    if home.getProperty('dhs.island.kind') == kind and home.getProperty('dhs.island.title') == title \
            and home.getProperty('dhs.island.sub') == sub:
        return
    home.setProperty('dhs.island.title', title)
    home.setProperty('dhs.island.sub', sub)
    home.setProperty('dhs.island.poster', poster)
    home.setProperty('dhs.island.action', action)
    home.setProperty('dhs.island.kind', kind)


# ------------------------------------------------------------------ notices
def _resume_path(row):
    episode = bool(row.get('season'))
    direct = '0'
    try:
        from . import continue_playback
        direct = '1' if continue_playback.known(row) else '0'
    except Exception:
        pass
    params = [('action', 'cw_resume'), ('media_type', row.get('media_type') or ('series' if episode else 'movie')),
              ('canonical_id', row.get('canonical_id') or ''), ('video_id', row.get('video_id') or ''),
              ('season', row.get('season') or ''), ('episode', row.get('episode') or ''),
              ('title', (row.get('show_title') if episode else row.get('title')) or row.get('title') or ''),
              ('position', int(float(row.get('position') or 0))), ('resume_percent', row.get('percent') or ''),
              ('provider_name', row.get('provider_name') or ''), ('direct', direct),
              ('ui_poster', row.get('poster') or ''), ('ui_fanart', row.get('background') or ''),
              ('ui_clearlogo', row.get('clearlogo') or '')]
    return 'plugin://plugin.video.dexhub/?' + urlencode([(k, str(v)) for k, v in params])


def stopped(since):
    """A video was stopped half way: the newest Continue Watching row saved
    after ``since`` waits in the bell for a quarter of an hour."""
    try:
        from .dexhub import playback_store
        rows = playback_store.list_continue_items(limit=3)
    except Exception:
        return False
    row = next((r for r in rows if float(r.get('updated_at') or 0) >= since - 5), None)
    if not row:
        return False
    episode = bool(row.get('season'))
    title = (row.get('show_title') if episode else '') or row.get('title') or ''
    if not title or title.lower().startswith('tt'):
        return False
    poster, fanart = row.get('poster') or '', row.get('background') or ''
    ids = [str(row.get(k) or '') for k in ('canonical_id', 'video_id')]
    imdb = next((i.split(':')[0] for i in ids if i.startswith('tt')), '')
    tmdb = next((i.split(':')[1] for i in ids if i.startswith('tmdb:') and len(i.split(':')) > 1), '')
    query = dict(tmdb_id=tmdb, imdb_id=imdb, media_type='series' if episode else 'movie', title=title, year='')
    if not fanart:
        # v5.10.228: a row saved without a backdrop takes its title's from
        # Dex Hub's TMDb cache (no request: the notice must not wait)
        try:
            from .tmdb_direct import meta_for_cached_only
            found = meta_for_cached_only(**query) or {}
            fanart = found.get('fanart') or ''
            poster = poster or found.get('poster') or ''
        except Exception:
            pass
    from .watch_timing import progress
    timeline, percent = progress(row.get('position'), row.get('duration'), _english())
    parts = []
    if episode:
        parts.append('S%s:E%s' % (row.get('season'), row.get('episode')))
    if timeline:
        parts.append(timeline)
    notice = {'kind': 'resume', 'kicker': _tr('كمّل المشاهدة'), 'title': title, 'sub': '  ·  '.join(parts),
              'poster': poster, 'fanart': fanart, 'progress': percent, 'has_progress': bool(timeline),
              'detected_at': row.get('updated_at') or time.time(),
              'action': _resume_path(row), 'until': time.time() + RESUME_SECONDS}
    notice['key'] = 'resume|%s|%s' % (title, notice['sub'].split('  ·  ')[0])
    _home().setProperty('dhs.island.resume', json.dumps(notice))
    changed()
    refresh(force=True)
    if not fanart or not poster:
        # v5.10.230: not in the cache either: TMDb is asked in the background
        # and the notice (and its banner in the inbox) gets the picture after
        threading.Thread(target=_art_later, args=(notice['key'], query),
                         name='DexHub-island-art', daemon=True).start()
    return True


def _art_later(key, query):
    try:
        from .tmdb_direct import meta_for
        found = meta_for(**query) or {}
    except Exception:
        return
    if not (found.get('fanart') or found.get('poster')):
        return
    notice = _json('dhs.island.resume')
    if not isinstance(notice, dict) or notice.get('key') != key:
        return          # gone or replaced meanwhile
    notice['fanart'] = notice.get('fanart') or found.get('fanart') or ''
    notice['poster'] = notice.get('poster') or found.get('poster') or ''
    _home().setProperty('dhs.island.resume', json.dumps(notice))
    home = _home()
    if home.getProperty('dhs.island.kind') == 'resume' and not home.getProperty('dhs.island.poster'):
        home.setProperty('dhs.island.poster', notice['poster'])
    if home.getProperty('dhs.island.toast.title') == notice.get('title'):
        if not home.getProperty('dhs.island.toast.fanart'):
            home.setProperty('dhs.island.toast.fanart', notice['fanart'])
        if not home.getProperty('dhs.island.toast.poster'):
            home.setProperty('dhs.island.toast.poster', notice['poster'])


def clear():
    """A video plays: what was stopped is not waiting any more."""
    _home().clearProperty('dhs.island.resume')
    changed()


def notices(soon=False, party=False):
    """Every notice, the newest kind first: [dict]. ``soon``: this week's
    coming episodes and films too (the inbox only, never the badge)."""
    home, out, now = _home(), [], time.time()
    if party:
        # v5.10.372: only the inbox lists the party; the island shows it itself
        try:
            from . import watch_party
            row = watch_party.notice()
            if row:
                out.append(row)
        except Exception:
            pass
    resume = _json('dhs.island.resume')
    if isinstance(resume, dict):
        if float(resume.get('until') or 0) > now:
            out.append(resume)
        else:
            home.clearProperty('dhs.island.resume')
    try:
        from . import silo_notifications
        out.extend(silo_notifications.notices())
    except Exception:
        pass
    try:
        # v5.10.453: a followed team's game live or just ended
        from . import sport_feed
        gone = set((home.getProperty('dhs.island.dismissed') or '').split('\n'))
        out.extend(row for row in sport_feed.island_notices(_json(sport_feed.ISLAND_PROP), now) if row['key'] not in gone)
    except Exception:
        pass
    today = datetime.date.today().isoformat()
    if home.getProperty('dhs.newep.day') == today:
        gone = set((home.getProperty('dhs.island.dismissed') or '').split('\n'))
        for ev in _json('dhs.newep.list') or []:
            if not isinstance(ev, dict) or not ev.get('title'):
                continue
            key = 'newep|%s' % (ev.get('key') or '%s|%s|%s' % (today, ev.get('title'), ev.get('code')))
            if key in gone:
                continue
            kicker = _tr('فيلم جديد متوفر') if ev.get('kind') == 'movie' else _tr('حلقة جديدة متوفرة')
            out.append(dict(ev, kind='newep', kicker=kicker, key=key, alert_key=ev.get('key') or ''))
        if soon:
            for ev in _json('dhs.newep.soon') or []:
                if isinstance(ev, dict) and ev.get('title'):
                    kicker = _tr('فيلم جاي') if ev.get('kind') == 'movie' else _tr('حلقة جاية')
                    out.append(dict(ev, kind='soon', kicker=kicker, key='soon|%s' % ev.get('key')))
    try:
        # v5.10.473: the kinds of alert the user switched off (alerts_prefs)
        from . import alerts_prefs
        out = [row for row in out if alerts_prefs.notice_on(row.get('kind') or '')]
    except Exception:
        pass
    return out


def _on_home():
    return xbmc.getCondVisibility('Window.IsActive(home) + !Window.IsActive(fullscreenvideo) + '
                                  '!System.HasModalDialog')


def _words(home):
    """v5.10.372: the island's own words, in the interface's language."""
    words = {'dhs.island.t.hint': _t('والتنبيهات، اضغط OK', 'and alerts, press OK'),
             'dhs.island.t.playing': _t('يعرض الحين', 'Now playing'),
             'dhs.island.t.party': _t('مشاهدة جماعية', 'Watch party')}
    for prop, text in words.items():
        if home.getProperty(prop) != text:
            home.setProperty(prop, text)


def refresh(force=False):
    """What the bell says now (the service calls it every second or so)."""
    home = _home()
    now = time.time()
    version = home.getProperty(VERSION)
    if not force and not _STATE['busy'] and version == _STATE['v'] and now - _STATE['at'] < 60:
        return
    _STATE['v'], _STATE['at'] = version, now
    _words(home)
    items = notices()
    count = str(len(items)) if items else ''
    if home.getProperty('dhs.island.count') != count:
        home.setProperty('dhs.island.count', count)
    # v5.10.473: what the bell's badge writes (two digits at most)
    badge = (count if len(items) < 10 else '9+') if items else ''
    if home.getProperty('dhs.island.badge') != badge:
        home.setProperty('dhs.island.badge', badge)
    if items:
        first = items[0]
        _set(first['kind'], first.get('title') or '', first.get('sub') or '', first.get('poster') or '',
             first.get('action') or '', first.get('kicker') or '')
    elif home.getProperty('dhs.island.kind'):
        _set('')
    # the banner: shown once for each new notice, while the Home is in front
    try:
        until = float(home.getProperty('dhs.island.toast.until') or 0)
    except ValueError:
        until = 0.0
    if until and now >= until:
        home.clearProperty('dhs.island.toast')
        home.clearProperty('dhs.island.toast.until')
        until = 0.0
    if not items:
        if home.getProperty('dhs.island.toast'):
            home.clearProperty('dhs.island.toast')
        _STATE['busy'] = False
        return
    seen = set((home.getProperty('dhs.island.seen') or '').split('\n'))
    fresh = [i for i in items if i['key'] not in seen]
    try:
        from . import alerts_prefs
        banner = alerts_prefs.on('banner')
    except Exception:
        banner = True
    if not banner:
        # v5.10.473: no pop-up banner: the bell's badge alone says something came
        home.setProperty('dhs.island.seen', '\n'.join(sorted(seen | set(i['key'] for i in items)))[-4000:])
        fresh = []
    if fresh and not until and _on_home():
        item = fresh[0]
        more = len(fresh) - 1
        home.setProperty('dhs.island.toast.kicker', item.get('kicker') or '')
        home.setProperty('dhs.island.toast.title', item.get('title') or '')
        home.setProperty('dhs.island.toast.sub', (item.get('sub') or '') + (('  +%d' % more) if more > 0 else ''))
        home.setProperty('dhs.island.toast.poster', item.get('poster') or '')
        # v5.10.234: skin.dexhub 3.28.9 draws the title's backdrop behind the banner
        home.setProperty('dhs.island.toast.fanart', item.get('fanart') or '')
        until = now + TOAST_SECONDS
        home.setProperty('dhs.island.toast.until', '%.1f' % until)
        home.setProperty('dhs.island.toast', '1')
        home.setProperty('dhs.island.seen', '\n'.join(sorted(seen | set(i['key'] for i in items)))[-4000:])
        fresh = []
    # a banner up (to fold at its time) or one waiting for the Home: every second
    _STATE['busy'] = bool(until or fresh)


# ------------------------------------------------------------------ the inbox
class Inbox(xbmcgui.WindowXMLDialog):
    """v5.10.473 (dexhub_inbox.xml by tools/s471/make_inbox.py): the notices
    in four tabs, and a fifth, Settings, with a switch for each kind of alert."""

    SETTINGS = 4

    def __init__(self, *args, **kwargs):
        super(Inbox, self).__init__(*args, **kwargs)
        self.items = kwargs.get('items') or []
        self.extra = kwargs.get('extra') or []
        self.start_settings = bool(kwargs.get('settings'))
        self.chosen = None
        self.switches = []

    def onInit(self):
        self.tab = self.SETTINGS if self.start_settings else 0
        self.setProperty('ib.head', _t('التنبيهات', 'Alerts'))
        self.setProperty('ib.empty', _t('ما فيه شي هنا', 'Nothing here'))
        self.setProperty('ib.hint', _t('موافق: فتح  ·  رجوع: إغلاق', 'OK: open  ·  Back: close'))
        buttons = self.getControl(BUTTONS)
        buttons.reset()
        for text in (_t('قراءة الكل', 'Read all'), _t('المصادر', 'Sources'),
                     _t('مشاهدة جماعية', 'Watch party'), _t('إغلاق', 'Close')):
            buttons.addItem(xbmcgui.ListItem(text))
        self._tabs()
        self._paint()
        self.setFocusId(LIST if self.getControl(LIST).size() else 200)

    def _tabs(self):
        tabs = self.getControl(200)
        pos = tabs.getSelectedPosition()
        tabs.reset()
        for n, text in enumerate((_t('الكل', 'All'), _t('الجديد', 'New'), _t('كمّل', 'Continue'), _t('القادم', 'Upcoming'),
                                  _t('الإعدادات', 'Settings'))):
            li = xbmcgui.ListItem(text)
            li.setProperty('sel', '1' if n == self.tab else '')
            tabs.addItem(li)
        if pos >= 0:
            tabs.selectItem(pos)

    def _counts(self):
        waiting = [i for i in self.items if i.get('kind') in ('newep', 'silo', 'sport')]
        self.setProperty('ib.count', str(len(waiting)) if waiting else '')
        try:
            from . import alerts_prefs
            off = [title for _key, title, _line, on in alerts_prefs.rows() if not on]
        except Exception:
            off = []
        sub = _t('%d تنبيه جديد', '%d new') % len(waiting) if waiting else _t('ما فيه جديد', 'Nothing new')
        if off:
            sub += '  ·  ' + _t('موقّف: %s', 'Off: %s') % u'، '.join(off) if not _english() else '  ·  Off: ' + ', '.join(off)
        self.setProperty('ib.sub', sub)

    def _paint(self):
        from .watch_timing import stamp, local_time
        self._counts()
        listing = self.getControl(LIST)
        if self.tab == self.SETTINGS:
            from . import alerts_prefs
            self.shown = []
            self.switches = alerts_prefs.rows()
            rows = []
            for key, title, line, on in self.switches:
                li = xbmcgui.ListItem(title, line)
                li.setProperty('setting', '1')
                li.setProperty('switch', '1' if on else '0')
                rows.append(li)
            # the screensaver's own choices, from here too
            li = xbmcgui.ListItem(_t('شاشة التوقف', 'Screensaver'), _t('شكلها ومحتواها ومتى تشتغل', 'Its look, what it shows and when'))
            li.setProperty('setting', '1')
            li.setProperty('more', '1')
            rows.append(li)
            pos = listing.getSelectedPosition()
            listing.reset()
            listing.addItems(rows)
            if 0 <= pos < len(rows):
                listing.selectItem(pos)
            self.setProperty('ib.hint', _t('موافق: تشغيل أو إيقاف', 'OK: switch on or off'))
            return
        self.setProperty('ib.hint', _t('موافق: فتح  ·  رجوع: إغلاق', 'OK: open  ·  Back: close'))
        kinds = (None, ('newep', 'silo', 'sport'), ('resume', 'playing'), ('soon',))
        self.shown = [i for i in self.items if kinds[self.tab] is None or i.get('kind') in kinds[self.tab]]
        self.shown.sort(key=lambda i: (i.get('kind') == 'soon', i.get('day') or ''))
        rows = []
        for item in self.shown:
            li = xbmcgui.ListItem(item.get('title') or '', item.get('sub') or '')
            for prop in ('kicker', 'kind'):
                li.setProperty(prop, item.get(prop) or '')
            detail = stamp(item.get('detected_at'), _english())
            if item.get('kind') == 'resume':
                detail = detail.replace('Detected ', 'Last watched ').replace('رُصد ', 'آخر مشاهدة ')
            if item.get('kind') == 'soon':
                detail = '  ·  '.join(x for x in (item.get('day'), local_time(item.get('aired'))) if x)
            li.setProperty('detail', detail)
            li.setProperty('progress', str(item.get('progress') or 0))
            li.setProperty('has_progress', '1' if item.get('has_progress') else '')
            li.setArt({'poster': item.get('poster') or '', 'fanart': item.get('fanart') or item.get('poster') or ''})
            rows.append(li)
        listing.reset()
        listing.addItems(rows)

    def onClick(self, control_id):
        if control_id == 200:
            self.tab = max(0, min(self.SETTINGS, self.getControl(200).getSelectedPosition()))
            self._tabs()
            self._paint()
            if self.getControl(LIST).size():
                self.setFocusId(LIST)
        elif control_id == LIST and self.tab == self.SETTINGS:
            pos = self.getControl(LIST).getSelectedPosition()
            if pos == len(self.switches):
                self.chosen = {'kind': 'screensaver'}
                self.close()
                return
            if 0 <= pos < len(self.switches):
                from . import alerts_prefs
                alerts_prefs.toggle(self.switches[pos][0])
                # the notices again, without the kinds now off (and the bell's badge)
                self.items = self.extra + notices(soon=True, party=True)
                changed()
                refresh(force=True)
                self._paint()
        elif control_id == LIST:
            pos = self.getControl(LIST).getSelectedPosition()
            if 0 <= pos < len(self.shown):
                self.chosen = self.shown[pos]
                self.close()
        elif control_id == BUTTONS:
            pos = self.getControl(BUTTONS).getSelectedPosition()
            self.chosen = {'kind': ('dismiss', 'manage', 'party', 'close')[max(0, min(3, pos))]}
            self.close()

    def onAction(self, action):
        if action.getId() in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU, 92, 10):
            self.close()


def dismiss():
    """«Clear all»: these notices go (a new episode later today still comes)."""
    home = _home()
    from . import silo_notifications
    silo_notifications.mark_read(notices(), all_visible=True)
    keys = [i['key'] for i in notices() if i.get('kind') == 'newep']
    home.clearProperty('dhs.island.resume')
    old = [k for k in (home.getProperty('dhs.island.dismissed') or '').split('\n') if k]
    home.setProperty('dhs.island.dismissed', '\n'.join((old + keys)[-60:]))
    changed()
    refresh(force=True)


def inbox(settings=False):
    """The bell, pressed: every notice as a banner (``settings``: open on the alert switches, v5.10.474)."""
    items = []
    if xbmc.getCondVisibility('Player.HasVideo'):
        title = xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or xbmc.getInfoLabel('VideoPlayer.Title')
        code = ''
        if xbmc.getInfoLabel('VideoPlayer.Episode'):
            code = 'S%s:E%s  ·  ' % (xbmc.getInfoLabel('VideoPlayer.Season'), xbmc.getInfoLabel('VideoPlayer.Episode'))
        from .watch_timing import progress
        try:
            timeline, percent = progress(xbmc.Player().getTime(), xbmc.Player().getTotalTime(), _english())
        except RuntimeError:
            timeline, percent = '', 0
        items.append({'progress': percent, 'has_progress': bool(timeline), 'kind': 'playing', 'kicker': _tr('يعرض الحين'), 'title': title,
                      'sub': code + timeline,
                      'poster': xbmc.getInfoLabel('Player.Art(poster)') or xbmc.getInfoLabel('Player.Art(tvshow.poster)'),
                      'fanart': xbmc.getInfoLabel('Player.Art(fanart)') or xbmc.getInfoLabel('Player.Art(tvshow.fanart)')})
    playing = list(items)
    items += notices(soon=True, party=True)
    _home().clearProperty('dhs.island.toast')
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    window = Inbox('dexhub_inbox.xml', path, 'Default', '1080i', items=items, extra=playing, settings=settings)
    window.doModal()
    chosen = window.chosen
    del window
    if not chosen:
        return None
    kind = chosen.get('kind')
    if kind == 'dismiss':
        from . import watch_sources
        watch_sources.mark_read([i.get('alert_key') for i in items if i.get('kind') == 'newep' and i.get('alert_key')])
        watch_sources.sync_alerts()
        dismiss()
    elif kind == 'screensaver':
        xbmc.executebuiltin('RunPlugin(plugin://plugin.video.dexhub/?action=home_screensaver)')
    elif kind == 'party' and not chosen.get('action'):
        from . import watch_party
        watch_party.menu()
    elif kind == 'manage':
        from . import watch_sources
        from .providers import silo_provider
        if silo_provider.is_signed_in():
            choice = xbmcgui.Dialog().select(_t('إدارة التنبيهات', 'Manage notifications'),
                                            [_t('مصادر العرض والمراقبة', 'Display and monitoring sources'),
                                             _t('تنبيهات بروفايل Silo', 'Silo profile notifications'),
                                             _t('قائمة مشاهدة Silo', 'Silo watchlist'),
                                             _t('كوليكشنات Silo', 'Silo collections')])
            if choice == 1:
                from . import silo_notifications
                silo_notifications.settings()
            elif choice == 0:
                watch_sources.menu()
            elif choice in (2, 3):
                from .profile_scope import ADDON_ID
                url = 'plugin://' + ADDON_ID + '/?' + (
                    'action=silo_personal&source=watchlist' if choice == 2 else 'action=silo_user_collections')
                xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % url)
        else:
            watch_sources.menu()
    elif kind == 'playing':
        xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
    elif chosen.get('action'):
        if kind == 'silo':
            from . import silo_notifications
            from .providers import silo_provider
            if chosen.get('silo_owner') != silo_notifications.owner(silo_provider.account() or {}):
                return None
            silo_notifications.mark_read([chosen])
            refresh(force=True)
        if chosen.get('alert_key'):
            from . import watch_sources
            watch_sources.mark_read([chosen['alert_key']])
            watch_sources.sync_alerts()
        if kind == 'resume':
            _home().clearProperty('dhs.island.resume')
            changed()
            refresh(force=True)
        if kind == 'silo' and chosen.get('folder'):
            xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % chosen['action'].replace('"', '%22'))
        else:
            xbmc.executebuiltin('RunPlugin("%s")' % chosen['action'].replace('"', '%22'))
    return None
