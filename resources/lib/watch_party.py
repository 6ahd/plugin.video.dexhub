# -*- coding: utf-8 -*-
"""v5.10.366: watch parties (Dex Hub Extra).

v5.10.373: the host can share its own link as an option (party_share): the
guests get it as the source «Dex Hub+» and need no source of their own.

v5.10.375: a TV stays in its party until it leaves or the host ends it: the
party is kept in hub_link.json, and after a restart, a sleep or a lost
connection the TV joins it again by itself (resume). The TV that made the
party is the host again when it comes back (hub 2.11). A guest whose video
starts goes to the host's place, tried again until it holds (the player's own
resume could take it back to the start).

One TV makes a party and gets a six-letter code; family or friends join with
it from their own TVs. No video goes through the hub: every TV plays the
title from its own sources. The host's TV tells the hub what it does (a
title starts, pause, play, a seek, stop, and where it is every half minute);
the others are told at once (long-poll: Kodi's Python has no WebSocket) and
do the same: the title opens on their TV (each picks its own source), then
pauses, plays and seeks with the host.

Home window properties (the island and the skin read them):
  dhs.party.room / code / host     the party this TV is in ('1' for the host)
  dhs.party.count                  how many TVs are in it
  dhs.party.title                  what the party is watching
  dhs.party.since                  the last event this TV has seen

The loop runs in the service (hub_worker) only while this TV is in a party.
"""
import json
import threading
import time
from urllib.parse import quote

import xbmc
import xbmcgui

from .profile_scope import ADDON_ID

P_ROOM, P_CODE, P_HOST = 'dhs.party.room', 'dhs.party.code', 'dhs.party.host'
P_COUNT, P_TITLE, P_SINCE = 'dhs.party.count', 'dhs.party.title', 'dhs.party.since'
P_MEDIA = 'dhs.party.media'
SYNC_EVERY = 30.0
DRIFT = {'seek': 2.0, 'play': 3.0, 'pause': 2.0, 'sync': 4.0, 'join': 2.0}


def _home():
    return xbmcgui.Window(10000)


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _note(text, ms=4500):
    xbmcgui.Dialog().notification(_t('مشاهدة جماعية', 'Watch party'), text, xbmcgui.NOTIFICATION_INFO, ms, sound=False)


def room():
    return _home().getProperty(P_ROOM)


def is_host():
    return _home().getProperty(P_HOST) == '1'


def _island():
    try:
        from . import island
        island.changed()
    except Exception:
        pass


def _keep(view, since=None):
    """The party's state in the Home properties."""
    home = _home()
    if not view:
        for prop in (P_ROOM, P_CODE, P_HOST, P_COUNT, P_TITLE, P_SINCE, P_MEDIA, 'dhs.party.line',
                     'dhs.party.members', 'dhs.party.shared', 'dhs.party.sharemode'):
            home.clearProperty(prop)
        _island()
        return
    home.setProperty(P_ROOM, view.get('id') or '')
    home.setProperty(P_CODE, view.get('code') or '')
    _remember(view)
    home.setProperty(P_HOST, '1' if view.get('host') else '')
    count = len([m for m in view.get('members') or [] if m.get('online', True)])
    home.setProperty(P_COUNT, str(count))
    # v5.10.372: the island's second line
    home.setProperty('dhs.party.line', (_t('%d يشاهدون', '%d watching') % count) if count > 1 else _t('ننتظر الباقين', 'Waiting for the others'))
    home.setProperty(P_TITLE, _media_title(view.get('media') or {}))
    home.setProperty(P_MEDIA, json.dumps(view.get('media') or {}))
    home.setProperty('dhs.party.members', json.dumps([{'name': m.get('name'), 'host': bool(m.get('host'))}
                                                      for m in view.get('members') or []]))
    # v5.10.373: the host shares its own link (the guest's «Dex Hub+» source)
    if view.get('shared') and not view.get('host'):
        home.setProperty('dhs.party.shared', json.dumps(view['shared']))
    else:
        home.clearProperty('dhs.party.shared')
    if since is not None:
        home.setProperty(P_SINCE, str(int(since)))
    _island()


def _remember(view):
    """v5.10.375: the party this TV is in, kept on disk (written when it changes)."""
    try:
        from . import hub_client
        saved = hub_client.state().get('party') or {}
        if saved.get('code') != view.get('code') or saved.get('room') != view.get('id'):
            hub_client.save(party={'code': view.get('code') or '', 'room': view.get('id') or '', 'at': time.time()})
    except Exception:
        pass


def _forget():
    """v5.10.375: this TV left the party (its choice, or the host ended it)."""
    try:
        from . import hub_client
        if hub_client.state().get('party'):
            hub_client.save(party=None)
    except Exception:
        pass


_RESUME = {'at': 0.0}
_GONE = ('no party with this code', 'the party is full', 'Dex Hub Extra is for supporters')


def resume(force=False):
    """v5.10.375: back in the kept party (Kodi restarted, the TV slept, the
    connection came back). True when this TV is in it again."""
    from . import hub_client
    saved = hub_client.state().get('party') or {}
    code = saved.get('code')
    if not code or room() or not hub_client.linked():
        return False
    if not force and time.time() - _RESUME['at'] < 60:
        return False
    _RESUME['at'] = time.time()
    try:
        answer = _call('join', {'name': _name(), 'code': code}, timeout=8)
    except Exception as exc:
        if str(exc) in _GONE:
            _forget()
            xbmc.log('[DexHub] watch party %s is over (%s)' % (code, exc), xbmc.LOGINFO)
        return False
    view = answer.get('room') or {}
    _keep(view, since=view.get('seq') or 0)
    hub_client.wake()
    _note(_t('رجعت للمشاهدة الجماعية %s', 'Back in watch party %s') % code)
    xbmc.log('[DexHub] watch party %s joined again (%s)' % (code, 'host' if view.get('host') else 'guest'), xbmc.LOGINFO)
    media, st = view.get('media') or {}, view.get('state') or {}
    if not view.get('host') and media and st.get('playing') and not _plays(media):
        if xbmcgui.Dialog().yesno(_t('مشاهدة جماعية', 'Watch party'),
                                  _t('يشاهدون الحين «%s». تشغّله؟', 'They are watching “%s”. Play it?') % _media_title(media)):
            _open(media)
    return True


def _media_title(media):
    title = media.get('title') or ''
    if title and media.get('season') and media.get('episode'):
        title += '  S%02dE%02d' % (int(media['season']), int(media['episode']))
    return title


# ------------------------------------------------------------------ what plays
def playing_media():
    """The title this TV plays now as the party knows it ({} when none)."""
    try:
        from .session_store import load_session
        ctx = load_session() or {}
    except Exception:
        ctx = {}
    media = {}
    imdb = str(ctx.get('show_imdb_id') or ctx.get('imdb_id') or '')
    if ':' in imdb:
        imdb = imdb.split(':', 1)[0]
    season, episode = ctx.get('season'), ctx.get('episode')
    media.update(media='series' if (season and episode) else 'movie',
                 canonical=str(ctx.get('canonical_id') or ''), imdb=imdb,
                 tmdb=str(ctx.get('show_tmdb_id') or ctx.get('tmdb_id') or ''),
                 title=str(ctx.get('show_title') or ctx.get('tvshowtitle') or ctx.get('title') or ''),
                 poster=str(ctx.get('poster') or ''), fanart=str(ctx.get('fanart') or ctx.get('background') or ''),
                 clearlogo=str(ctx.get('clearlogo') or _home().getProperty('dexhub.np.logo') or ''))
    if season and episode:
        media.update(season=int(season), episode=int(episode))
    if not (media['canonical'] or media['imdb'] or media['tmdb']):
        title = xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or xbmc.getInfoLabel('VideoPlayer.Title')
        if not title:
            return {}
        media.update(title=title, imdb=xbmc.getInfoLabel('VideoPlayer.IMDBNumber') or '')
        if xbmc.getInfoLabel('VideoPlayer.Episode'):
            media.update(media='series', season=int(xbmc.getInfoLabel('VideoPlayer.Season') or 0),
                         episode=int(xbmc.getInfoLabel('VideoPlayer.Episode') or 0))
    return dict((k, v) for k, v in media.items() if v not in ('', None))


def same(a, b):
    if not a or not b:
        return False
    ids = any(a.get(k) and a.get(k) == b.get(k) for k in ('imdb', 'tmdb', 'canonical'))
    return ids and int(a.get('season') or 0) == int(b.get('season') or 0) \
        and int(a.get('episode') or 0) == int(b.get('episode') or 0)


def _plays(media):
    """v5.10.373: this TV plays that title now (an older session of the same
    title with nothing playing is not it: the guest is asked again)."""
    return xbmc.Player().isPlayingVideo() and same(media, playing_media())


def _open(media):
    """The party's title on this TV: its own sources, its own pick."""
    from .play_with import dex_command
    # v5.10.375: what starts next is the party's title (its ids can read differently)
    _home().setProperty('dhs.party.opening', '%.0f' % time.time())
    series = media.get('media') == 'series' and media.get('season') and media.get('episode')
    canonical = media.get('canonical') or media.get('imdb') or ('tmdb:%s' % media['tmdb'] if media.get('tmdb') else '')
    params = {'media_type': 'series' if series else 'movie', 'canonical_id': canonical,
              'title': media.get('title') or '', 'tmdb_id': media.get('tmdb') or '', 'imdb_id': media.get('imdb') or ''}
    if series:
        params.update(season=str(media['season']), episode=str(media['episode']))
        base = media.get('imdb') or canonical
        params['video_id'] = '%s:%s:%s' % (base, media['season'], media['episode']) if base.startswith('tt') else canonical
    xbmc.executebuiltin(dex_command(params))


# ------------------------------------------------------------------ hub calls
def _call(path, body=None, method='POST', timeout=10):
    from . import hub_client
    return hub_client.request(method, '/api/device/party/' + path, body if method == 'POST' else None, timeout=timeout)


def _name():
    from . import hub_client
    return hub_client.state().get('label') or xbmc.getInfoLabel('System.FriendlyName') or 'Kodi'


def _ready():
    from . import hub_client
    if not hub_client.linked():
        if xbmcgui.Dialog().yesno(_t('مشاهدة جماعية', 'Watch party'), _t(
                'المشاهدة الجماعية من Dex Hub Extra: اربط التلفزيون بحسابك أول (باركود).',
                'Watch parties are part of Dex Hub Extra: link this TV to your account first (QR code).'),
                yeslabel=_t('اربط', 'Link'), nolabel=_t('لاحقًا', 'Later')):
            return hub_client.pair() and hub_client.active(network=True)
        return False
    if not hub_client.extra(force=True).get('active'):
        hub_client._about(locked=True)
        return False
    return True


def start(media=None):
    """A new party with this TV as the host."""
    if not _ready():
        return False
    try:
        answer = _call('create', {'name': _name(), 'media': media or {}})
    except Exception as exc:
        xbmcgui.Dialog().ok(_t('مشاهدة جماعية', 'Watch party'), str(exc))
        return False
    view = answer.get('room') or {}
    _keep(view, since=view.get('seq') or 0)
    from . import hub_client
    hub_client.wake()
    tg = _t('\n\nوالكود وصلك على تيليجرام مع زر مشاركة.', '\n\nThe code is on your Telegram too, with a Share button.') if answer.get('telegram') else ''
    # v5.10.372: the code, and sharing the invite link as an option
    if xbmcgui.Dialog().yesno(_t('مشاهدة جماعية', 'Watch party'), _t(
            'الكود: [B]%s[/B]\n\nعطه أهلك أو ربعك: يفتحون Dex Hub ← Dex Hub Extra ← المشاهدة الجماعية ← انضمام. '
            'بعدها شغّل اللي تبي، وتشغيلك وإيقافك وتقديمك يمشي على الكل.',
            'Code: [B]%s[/B]\n\nGive it to family or friends: Dex Hub → Dex Hub Extra → Watch party → Join. '
            'Then play what you like: your play, pause and seek reach everyone.') % view.get('code', '') + tg,
            nolabel=_t('تم', 'Done'), yeslabel=_t('شارك رابط الدعوة', 'Share the invite link')):
        invite()
    return True


def invite_url(code=''):
    from . import hub_client
    return '%s/party/%s' % (hub_client.base(), code or _home().getProperty(P_CODE))


def invite():
    """v5.10.372: the invite link as a QR: the phone opens it and shares it
    anywhere (WhatsApp, Telegram, a message); it shows the code and how to join."""
    code = _home().getProperty(P_CODE)
    if not code:
        return False
    url = invite_url(code)
    qr_path = ''
    try:
        from .plex_qr import qr_png
        qr_path = qr_png(url, box_size=8, border=4) or ''
    except Exception:
        pass
    from .hub_client import _PairWindow
    win = _PairWindow(qr_path=qr_path, title=_t('دعوة للمشاهدة', 'Watch party invite'), code=code, text=_t(
        'امسح الرمز بالجوال: يفتح رابط الدعوة، وتشاركه مع أهلك أو ربعك في أي مكان. الرابط فيه الكود وطريقة الانضمام.',
        'Scan the code with your phone: it opens the invite link to share anywhere. The link shows the code and how to join.'))
    win.doModal()
    del win
    return True


def player_button():
    """v5.10.372: the player bar's Watch party button: the party this TV is
    in, or a new one with what plays now (the others are told at once)."""
    if room():
        return menu()
    media = playing_media()
    return start(media)


def join(code=''):
    if not _ready():
        return False
    code = (code or xbmcgui.Dialog().input(_t('كود المشاهدة الجماعية', 'Watch party code')) or '').strip().upper()
    if not code:
        return False
    try:
        answer = _call('join', {'name': _name(), 'code': code})
    except Exception as exc:
        xbmcgui.Dialog().ok(_t('مشاهدة جماعية', 'Watch party'), {
            'no party with this code': _t('ما فيه مشاهدة بهذا الكود', 'No party with this code'),
            'the party is full': _t('المشاهدة مليانة', 'The party is full')}.get(str(exc), str(exc)))
        return False
    view = answer.get('room') or {}
    _keep(view, since=view.get('seq') or 0)
    from . import hub_client
    hub_client.wake()
    host = [m.get('name') for m in view.get('members') or [] if m.get('host')]
    _note(_t('دخلت مشاهدة %s', 'You joined %s\'s party') % (host[0] if host else view.get('code')))
    media, st = view.get('media') or {}, view.get('state') or {}
    if media and st.get('playing') and not _plays(media):
        if xbmcgui.Dialog().yesno(_t('مشاهدة جماعية', 'Watch party'),
                                  _t('يشاهدون الحين «%s». تشغّله؟', 'They are watching “%s”. Play it?') % _media_title(media)):
            _open(media)
    return True


def leave(confirm=True):
    rid = room()
    if not rid:
        return
    if confirm and not xbmcgui.Dialog().yesno(_t('مشاهدة جماعية', 'Watch party'),
                                              _t('تنهي المشاهدة للكل؟', 'End the party for everyone?') if is_host()
                                              else _t('تطلع من المشاهدة؟', 'Leave the party?')):
        return
    try:
        _call('leave', {'room': rid}, timeout=6)
    except Exception:
        pass
    _forget()               # v5.10.375: left by choice: never joined again by itself
    _keep(None)


def chat():
    rid = room()
    text = xbmcgui.Dialog().input(_t('رسالة للكل', 'Message to everyone')) if rid else ''
    if text:
        try:
            _call('event', {'room': rid, 'kind': 'chat', 'text': text[:200]}, timeout=6)
        except Exception as exc:
            _note(str(exc))


def menu():
    from .homeui.options import choose
    if room():
        code = _home().getProperty(P_CODE)
        title = _home().getProperty(P_TITLE) or _t('ينتظرون المضيف يشغّل', 'Waiting for the host to play')
        rows = [(_t('الكود: %s', 'Code: %s') % code, _t('%s متصلين · %s', '%s watching · %s') % (_home().getProperty(P_COUNT) or '1', title)),
                (_t('رسالة للكل', 'Message to everyone'), _t('تطلع عند الكل فوق الفيديو', 'Shows on every TV over the video')),
                (_t('شارك رابط الدعوة', 'Share the invite link'), _t('باركود تمسحه بالجوال وترسله لمن تبي', 'A QR code to scan and send to anyone'))]
        if is_host():
            # v5.10.373: the host's own link, as an option
            from . import party_share
            rows.append((_t('شارك تشغيلي مع الضيوف: %s', 'Share my playback with guests: %s') % party_share.mode_name(),
                         _t('يطلع لهم مصدر Dex Hub+ من رابطك', 'They get a Dex Hub+ source from your link')))
        else:
            rows.append((_t('شغّل اللي يشاهدونه', 'Play what they watch'), ''))
        rows.append((_t('إنهاء المشاهدة للكل', 'End the party for everyone') if is_host() else _t('خروج من المشاهدة', 'Leave the party'), ''))
        pick = choose(_t('مشاهدة جماعية', 'Watch party'), rows, subtitle=('[B]%s[/B]' % code))
        label = rows[pick][0] if pick >= 0 else ''
        if pick == 1:
            chat()
        elif pick == 2:
            invite()
        elif is_host() and pick == 3:
            share_changed()
        elif label == _t('شغّل اللي يشاهدونه', 'Play what they watch'):
            try:
                media = json.loads(_home().getProperty(P_MEDIA) or '{}') or {}
            except ValueError:
                media = {}
            if media:
                _open(media)
            else:
                _note(_t('المضيف ما شغّل شي للحين', 'The host has not played anything yet'))
        elif pick == len(rows) - 1:
            leave()
        return
    rows = [(_t('ابدأ مشاهدة', 'Start a party'), _t('تاخذ كود وتعطيه أهلك أو ربعك', 'Get a code and give it to family or friends')),
            (_t('انضمام بكود', 'Join with a code'), _t('الكود اللي عند المضيف', 'The code the host has'))]
    from . import hub_client
    saved = (hub_client.state().get('party') or {}).get('code') or ''
    if saved:
        # v5.10.375: the party this TV is still in (it was away)
        rows.insert(0, (_t('رجوع للمشاهدة %s', 'Back to party %s') % saved, _t('ما طلعت منها، ترجع لها على طول', 'You never left it: straight back in')))
    pick = choose(_t('مشاهدة جماعية', 'Watch party'), rows,
                  subtitle=_t('كل تلفزيون يشغّل من مصادره، والتشغيل والإيقاف مع بعض', 'Each TV plays from its own sources; play and pause together'))
    if saved:
        pick -= 1
        if pick == -1:
            if not resume(force=True):
                _note(_t('المشاهدة انتهت أو ما فيه اتصال', 'The party is over, or no connection'))
            return
    if pick == 0:
        start()
    elif pick == 1:
        join()


def share_changed():
    """v5.10.373: the host turned its link on or off: what plays goes again."""
    from . import party_share
    if not party_share.choose():
        return
    rid = room()
    player = xbmc.Player()
    try:
        if not party_share.mode():
            _call('event', {'room': rid, 'kind': 'unshare'}, timeout=8)
            _note(_t('وقفت مشاركة رابطك', 'Your link is no longer shared'))
            return
        if not player.isPlayingVideo():
            _note(_t('تمام: أول ما تشغّل شي يوصلهم رابطك', 'Done: what you play next goes to them with your link'))
            return
        share, why = party_share.host_share(player)
        _call('event', {'room': rid, 'kind': 'load', 'media': playing_media(), 'position': float(player.getTime()),
                        'playing': not xbmc.getCondVisibility('Player.Paused'), 'share': share}, timeout=8)
        _note(why or _t('رابطك وصلهم: Dex Hub+ أول مصادرهم', 'Your link is theirs now: Dex Hub+ tops their sources'))
    except Exception as exc:
        _note(str(exc))


def from_title(media):
    """The title page's «Watch together»: a party starts with this title."""
    if room():
        if is_host():
            _note(_t('أنت في مشاهدة جماعية: شغّل العمل ويمشي على الكل', 'You are in a party: play it and everyone follows'))
        else:
            menu()
        return
    start(media)


# ------------------------------------------------------------------ the loop
class _Player(xbmc.Player):
    """Kodi's player in the service, while in a party."""

    def __init__(self, loop):
        xbmc.Player.__init__(self)
        self.loop = loop

    def _trailer(self):
        """v5.10.369: a trailer playing behind the Home (or in a title page)
        is not what the party watches: none of its events go anywhere."""
        try:
            # the playing item's own mark or its address (the Home's
            # dexhub.trailer.active can lag a real title starting)
            from .trailer_mark import playing_is_trailer
            return playing_is_trailer(self)
        except Exception:
            return False

    def _send(self, kind, **body):
        if not self.loop.host or self.loop.trailer:
            return
        threading.Thread(target=self.loop.send, args=(kind,), kwargs=body, daemon=True).start()

    def _pos(self):
        try:
            return float(self.getTime())
        except Exception:
            return 0.0

    def onAVStarted(self):  # noqa: N802
        self.loop.trailer = self._trailer()
        if self.loop.trailer:
            return
        if self.loop.host:
            threading.Thread(target=self.loop.host_started, daemon=True).start()
        else:
            threading.Thread(target=self.loop.guest_started, daemon=True).start()

    def onPlayBackPaused(self):  # noqa: N802
        self._send('pause', position=self._pos())

    def onPlayBackResumed(self):  # noqa: N802
        self._send('play', position=self._pos())

    def onPlayBackSeek(self, time_ms, offset):  # noqa: N802
        self._send('seek', position=max(0.0, time_ms / 1000.0))

    def onPlayBackSeekChapter(self, chapter):  # noqa: N802
        self._send('seek', position=self._pos())

    def onPlayBackStopped(self):  # noqa: N802
        self._send('stop', position=0)
        self.loop.trailer = False

    def onPlayBackEnded(self):  # noqa: N802
        self._send('stop', position=0)
        self.loop.trailer = False


class Loop(object):
    def __init__(self, monitor):
        self.monitor = monitor
        self.room = room()
        self.host = is_host()
        self.state = {}
        self.media = {}
        self.offset = 0.0          # the hub's clock minus ours (seconds)
        self.applying = 0.0        # our own pause/seek from an event: not sent back
        self.last_sync = 0.0
        self.trailer = False       # v5.10.369: what plays now is a trailer
        self.done = False
        # Kodi calls a Python player back only when the thread that made it
        # waits in Kodi (waitForAbort): the long-poll below holds its thread
        # for up to 25 s, so the player lives in a thread of its own
        self.player = None
        ready = threading.Event()
        threading.Thread(target=self._pump, args=(ready,), name='DexHub-party-player', daemon=True).start()
        ready.wait(5)
        if self.player is None:
            self.player = xbmc.Player()

    def _pump(self, ready):
        mon = xbmc.Monitor()
        self.player = _Player(self)
        ready.set()
        while not self.done and not mon.abortRequested():
            if mon.waitForAbort(0.25):
                break

    # -- sending (host)
    def send(self, kind, **body):
        if not self.host or time.time() < self.applying:
            return
        body.update(room=self.room, kind=kind)
        try:
            _call('event', body, timeout=8)
            self.last_sync = time.time()
        except Exception as exc:
            xbmc.log('[DexHub] party %s not sent: %s' % (kind, exc), xbmc.LOGINFO)

    def host_started(self):
        media = {}
        for _ in range(8):
            media = playing_media()
            if media:
                break
            if self.monitor.waitForAbort(0.5):
                return
        try:
            position = float(self.player.getTime())
        except Exception:
            position = 0.0
        share, why = None, ''
        try:
            from . import party_share
            share, why = party_share.host_share(self.player)
        except Exception as exc:
            xbmc.log('[DexHub] party share: %s' % exc, xbmc.LOGWARNING)
        if why:
            _note(why)
        self.send('load', media=media, position=position, playing=not xbmc.getCondVisibility('Player.Paused'), share=share)

    # -- applying (guests)
    def expected(self):
        st = self.state or {}
        position = float(st.get('position') or 0)
        if st.get('playing'):
            now_hub = time.time() + self.offset
            position += max(0.0, now_hub - float(st.get('at') or 0) / 1000.0)
        return position

    def _seek_if_off(self, kind):
        try:
            here = float(self.player.getTime())
        except Exception:
            return
        target = self.expected()
        if abs(here - target) > DRIFT.get(kind, 3.0):
            self.applying = time.time() + 1.5
            self.player.seekTime(target)

    def _paused(self):
        return xbmc.getCondVisibility('Player.Paused')

    def _watching_it(self):
        return self.player.isPlayingVideo() and not self.trailer \
            and (not self.media or same(self.media, playing_media()))

    def guest_started(self):
        if self.monitor.waitForAbort(1.5):
            return
        try:
            opened = time.time() - float(_home().getProperty('dhs.party.opening') or 0) < 180
        except ValueError:
            opened = False
        if not self.media or not (opened or same(self.media, playing_media())):
            xbmc.log('[DexHub] watch party: this video is not the party\'s (%s)' % ('no title yet' if not self.media else 'another title'), xbmc.LOGINFO)
            return
        _home().clearProperty('dhs.party.opening')
        try:
            xbmc.log('[DexHub] watch party: to the host\'s place, %.0f s (here %.0f s)' % (self.expected(), float(self.player.getTime())), xbmc.LOGINFO)
        except Exception:
            pass
        # v5.10.375: to the host's place, again until it holds (the player's
        # own resume, or a slow stream, could leave it at the start)
        for _try in range(5):
            self._seek_if_off('join')
            if self.monitor.waitForAbort(1.5):
                return
            try:
                if abs(float(self.player.getTime()) - self.expected()) <= DRIFT['join'] + 1.0:
                    break
            except Exception:
                break
        if not (self.state or {}).get('playing') and not self._paused():
            self.applying = time.time() + 1.5
            self.player.pause()

    def apply(self, ev):
        kind = ev.get('kind')
        if kind in ('load', 'play', 'pause', 'seek', 'sync', 'stop'):
            if self.host:
                return
            if kind == 'load':
                media = ev.get('media') or {}
                # v5.10.373: the host's link (the «Dex Hub+» source) before the search opens
                home = _home()
                home.setProperty(P_MEDIA, json.dumps(media))
                if ev.get('shared'):
                    home.setProperty('dhs.party.shared', json.dumps(ev['shared']))
                else:
                    home.clearProperty('dhs.party.shared')
                if media and not _plays(media):
                    self.media = media
                    extra = _t('\n\nومعه رابط المضيف: مصدر Dex Hub+ أول القائمة.', '\n\nWith the host\'s link: the Dex Hub+ source tops the list.') \
                        if ev.get('shared') else ''
                    if xbmcgui.Dialog().yesno(_t('مشاهدة جماعية', 'Watch party'), _t(
                            '%s شغّل «%s». تشغّله معهم؟', '%s started “%s”. Play it with them?')
                            % (ev.get('name') or _t('المضيف', 'The host'), _media_title(media)) + extra):
                        _open(media)
                    return
                self.media = media or self.media
            if not self._watching_it():
                return
            if kind == 'stop':
                self.player.stop()
                _note(_t('المضيف وقّف التشغيل', 'The host stopped'))
            elif kind == 'pause':
                if not self._paused():
                    self.applying = time.time() + 1.5
                    self.player.pause()
                self._seek_if_off('pause')
            elif kind == 'play':
                if self._paused():
                    self.applying = time.time() + 1.5
                    self.player.pause()
                self._seek_if_off('play')
            else:
                self._seek_if_off(kind)
        elif kind == 'unshare':
            _home().clearProperty('dhs.party.shared')
        elif kind == 'chat':
            _note('%s: %s' % (ev.get('name') or '', ev.get('text') or ''), 7000)
        elif kind == 'joined':
            _note(_t('%s دخل المشاهدة', '%s joined') % (ev.get('name') or ''))
        elif kind == 'left':
            _note(_t('%s طلع من المشاهدة', '%s left') % (ev.get('name') or ''))
        elif kind == 'host':
            _note(_t('%s صار المضيف', '%s is the host now') % (ev.get('name') or ''))

    def _wait(self, since):
        """v5.10.369: the hub holds this call up to 25 s; it runs in a thread
        of its own and this one waits in Kodi, so Kodi's exit never waits on
        it (v5.10.374: hub_client.long_poll closes its socket on exit: Kodi
        waits for every thread of the service). None when Kodi is closing."""
        from . import hub_client
        return hub_client.long_poll('/api/device/party/wait?room=%s&since=%d' % (quote(self.room), since),
                                    self.monitor, timeout=35)

    # -- the loop
    def run(self):
        if self.host and self.player.isPlayingVideo():
            # v5.10.372: started from the player bar: what plays goes out now
            threading.Thread(target=self.host_started, daemon=True).start()
        xbmc.log('[DexHub] watch party %s (%s)' % (_home().getProperty(P_CODE), 'host' if self.host else 'guest'),
                 xbmc.LOGINFO)
        failures = 0
        while not self.monitor.abortRequested() and room() == self.room:
            try:
                since = int(_home().getProperty(P_SINCE) or 0)
            except ValueError:
                since = 0
            try:
                answer = self._wait(since)
                if answer is None:
                    break           # Kodi is closing
                failures = 0
            except Exception as exc:
                failures += 1
                if 'not in this party' in str(exc):
                    # v5.10.375: dropped while away (asleep, no connection): back in by the code
                    code = _home().getProperty(P_CODE)
                    try:
                        again = (_call('join', {'name': _name(), 'code': code}, timeout=8).get('room') or {}) if code else {}
                    except Exception as exc2:
                        again = {}
                        if str(exc2) in _GONE:
                            _forget()
                    if again.get('id'):
                        self.room = again['id']
                        _keep(again, since=again.get('seq') or 0)
                        self.host = bool(again.get('host'))
                        failures = 0
                        xbmc.log('[DexHub] watch party: back in after being away', xbmc.LOGINFO)
                        continue
                    _note(_t('انتهت المشاهدة الجماعية', 'The watch party ended'))
                    _keep(None)
                    break
                if failures > 60:
                    # no connection for a long time: kept, joined again once it is back (resume)
                    _note(_t('انقطع الاتصال بالمشاهدة، ترجع لها لحالها', 'Lost the party: it comes back by itself'))
                    _keep(None)
                    break
                if self.monitor.waitForAbort(min(30, 3 * failures)):
                    break
                continue
            if room() != self.room:
                break
            if answer.get('now'):
                self.offset = float(answer['now']) / 1000.0 - time.time()
            view = answer.get('room')
            events = answer.get('events') or []
            if view:
                self.host = bool(view.get('host'))
                self.state = view.get('state') or {}
                self.media = view.get('media') or self.media
            for ev in events:
                since = max(since, int(ev.get('seq') or 0))
                if ev.get('kind') == 'closed':
                    _note(_t('المضيف أنهى المشاهدة', 'The host ended the party'))
                    _forget()
                    _keep(None)
                    self.done = True
                    return
                try:
                    self.apply(ev)
                except Exception as exc:
                    xbmc.log('[DexHub] party event %s: %s' % (ev.get('kind'), exc), xbmc.LOGWARNING)
            if view is None:
                _forget()
                _keep(None)
                self.done = True
                return
            if room() == self.room:
                _keep(view, since=since)
            if self.host and self.player.isPlayingVideo() and not self.trailer and not self._paused() \
                    and time.time() - self.last_sync >= SYNC_EVERY:
                try:
                    self.send('sync', position=float(self.player.getTime()))
                except Exception:
                    pass
        self.done = True
        xbmc.log('[DexHub] watch party loop ended', xbmc.LOGINFO)


def loop(monitor):
    """The service's hub thread, while this TV is in a party."""
    try:
        Loop(monitor).run()
    except Exception as exc:
        xbmc.log('[DexHub] watch party loop: %s' % exc, xbmc.LOGWARNING)


def notice():
    """The island's line for the party ({} when not in one)."""
    home = _home()
    code = home.getProperty(P_CODE)
    if not home.getProperty(P_ROOM) or not code:
        return {}
    count = home.getProperty(P_COUNT) or '1'
    title = home.getProperty(P_TITLE) or _t('ينتظرون المضيف يشغّل', 'Waiting for the host to play')
    return {'kind': 'party', 'key': 'party|%s' % code, 'kicker': _t('مشاهدة جماعية · %s', 'Watch party · %s') % code,
            'title': title, 'sub': _t('%s متصلين', '%s watching') % count,
            'action': 'plugin://%s/?action=party' % ADDON_ID}
