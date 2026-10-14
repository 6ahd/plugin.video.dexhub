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


def _set(kind, title='', sub='', poster='', action=''):
    home = _home()
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
    left = max(0.0, float(row.get('duration') or 0) - float(row.get('position') or 0))
    parts = []
    if episode:
        parts.append('S%s:E%s' % (row.get('season'), row.get('episode')))
    if left >= 60:
        parts.append(_tr('باقي %d د') % int(round(left / 60.0)))
    notice = {'kind': 'resume', 'kicker': _tr('كمّل المشاهدة'), 'title': title, 'sub': '  ·  '.join(parts),
              'poster': poster, 'fanart': fanart,
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


def notices(soon=False):
    """Every notice, the newest kind first: [dict]. ``soon``: this week's
    coming episodes and films too (the inbox only, never the badge)."""
    home, out, now = _home(), [], time.time()
    resume = _json('dhs.island.resume')
    if isinstance(resume, dict):
        if float(resume.get('until') or 0) > now:
            out.append(resume)
        else:
            home.clearProperty('dhs.island.resume')
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
            out.append(dict(ev, kind='newep', kicker=kicker, key=key))
        if soon:
            for ev in _json('dhs.newep.soon') or []:
                if isinstance(ev, dict) and ev.get('title'):
                    kicker = _tr('فيلم جاي') if ev.get('kind') == 'movie' else _tr('حلقة جاية')
                    out.append(dict(ev, kind='soon', kicker=kicker, key='soon|%s' % ev.get('key')))
    return out


def _on_home():
    return xbmc.getCondVisibility('Window.IsActive(home) + !Window.IsActive(fullscreenvideo) + '
                                  '!System.HasModalDialog')


def refresh(force=False):
    """What the bell says now (the service calls it every second or so)."""
    home = _home()
    now = time.time()
    version = home.getProperty(VERSION)
    if not force and not _STATE['busy'] and version == _STATE['v'] and now - _STATE['at'] < 60:
        return
    _STATE['v'], _STATE['at'] = version, now
    items = notices()
    count = str(len(items)) if items else ''
    if home.getProperty('dhs.island.count') != count:
        home.setProperty('dhs.island.count', count)
    if items:
        first = items[0]
        _set(first['kind'], first.get('title') or '', first.get('sub') or '', first.get('poster') or '',
             first.get('action') or '')
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

    def __init__(self, *args, **kwargs):
        super(Inbox, self).__init__(*args, **kwargs)
        self.items = kwargs.get('items') or []
        self.chosen = None

    def onInit(self):
        self.setProperty('ib.head', _tr('التنبيهات'))
        self.setProperty('ib.count', str(len([i for i in self.items if i.get('kind') not in ('playing', 'soon')]) or ''))
        self.setProperty('ib.empty', _tr('ما فيه تنبيهات الحين'))
        rows = []
        for item in self.items:
            li = xbmcgui.ListItem(item.get('title') or '', item.get('sub') or '')
            li.setProperty('kicker', item.get('kicker') or '')
            li.setProperty('kind', item.get('kind') or '')
            li.setArt({'poster': item.get('poster') or '', 'fanart': item.get('fanart') or item.get('poster') or ''})
            rows.append(li)
        listing = self.getControl(LIST)
        listing.reset()
        listing.addItems(rows)
        buttons = self.getControl(BUTTONS)
        buttons.reset()
        if any(i.get('kind') not in ('playing', 'soon') for i in self.items):
            buttons.addItem(xbmcgui.ListItem(_tr('مسح الكل')))
        self.setFocusId(LIST if rows else BUTTONS)

    def onClick(self, control_id):
        if control_id == LIST:
            pos = self.getControl(LIST).getSelectedPosition()
            if 0 <= pos < len(self.items):
                self.chosen = self.items[pos]
                self.close()
        elif control_id == BUTTONS:
            self.chosen = {'kind': 'dismiss'}
            self.close()

    def onAction(self, action):
        if action.getId() in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU, 92, 10):
            self.close()


def dismiss():
    """«Clear all»: these notices go (a new episode later today still comes)."""
    home = _home()
    keys = [i['key'] for i in notices() if i.get('kind') == 'newep']
    home.clearProperty('dhs.island.resume')
    old = [k for k in (home.getProperty('dhs.island.dismissed') or '').split('\n') if k]
    home.setProperty('dhs.island.dismissed', '\n'.join((old + keys)[-60:]))
    changed()
    refresh(force=True)


def inbox():
    """The bell, pressed: every notice as a banner."""
    items = []
    if xbmc.getCondVisibility('Player.HasVideo'):
        title = xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or xbmc.getInfoLabel('VideoPlayer.Title')
        code = ''
        if xbmc.getInfoLabel('VideoPlayer.Episode'):
            code = 'S%s:E%s  ·  ' % (xbmc.getInfoLabel('VideoPlayer.Season'), xbmc.getInfoLabel('VideoPlayer.Episode'))
        items.append({'kind': 'playing', 'kicker': _tr('يعرض الحين'), 'title': title,
                      'sub': code + _tr('باقي %s') % xbmc.getInfoLabel('Player.TimeRemaining'),
                      'poster': xbmc.getInfoLabel('Player.Art(poster)') or xbmc.getInfoLabel('Player.Art(tvshow.poster)'),
                      'fanart': xbmc.getInfoLabel('Player.Art(fanart)') or xbmc.getInfoLabel('Player.Art(tvshow.fanart)')})
    items += notices(soon=True)
    _home().clearProperty('dhs.island.toast')
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    window = Inbox('dexhub_inbox.xml', path, 'Default', '1080i', items=items)
    window.doModal()
    chosen = window.chosen
    del window
    if not chosen:
        return None
    kind = chosen.get('kind')
    if kind == 'dismiss':
        dismiss()
    elif kind == 'playing':
        xbmc.executebuiltin('ActivateWindow(fullscreenvideo)')
    elif chosen.get('action'):
        if kind == 'resume':
            _home().clearProperty('dhs.island.resume')
            changed()
            refresh(force=True)
        xbmc.executebuiltin('RunPlugin("%s")' % chosen['action'].replace('"', '%22'))
    return None
