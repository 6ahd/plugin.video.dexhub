# -*- coding: utf-8 -*-
"""Blu-ray and DVD images played in their own menus (v5.10.567).

Kodi plays a disc with its menus in navigation mode, where the demuxer keeps
the disc's own timestamps (DVDDemuxFFmpeg::ConvertTimestamp leaves the start
time in for a NATIVE menu stream), while a subtitle file is timed from 0. So
a subtitle file never lines up and none of it shows: kodi.log of a LibreELEC
box, 13 Nov 2026, the player at 00:00:11 and the disc's clock at 26:31:06,
six subtitle files tried and none shown. The disc's own subtitles still show.
Played as its main movie, the disc is timed from 0 and subtitle files show.

Kodi applies Settings > Player > Discs > Blu-ray playback (main movie, simple
menu, disc menu) only to a disc started at its beginning; one opened at an
offset (a resume point) always goes to its menus. So a disc is never opened at
an offset (playback_engine) and its resume point is reached by a seek once it
plays its main movie (companion), never in its menus.

v5.10.572: the offer comes for a subtitle from anywhere, not only Dex Hub's own
subtitle page: Kodi's subtitle dialog with any subtitle add-on, or a file
added by another add-on (watch, in the service). And the replay opens the main
title even for a disc Dex Hub serves as bluray://.../menu: debrid/bdinfo follows
the Blu-ray playback setting this sets for the replay.
"""
import hashlib
import json
import time
from urllib.parse import urlencode

import xbmc
import xbmcgui

MAIN_TITLE = 2            # Kodi's BD_PLAYBACK_MAIN_TITLE (disc.playback)
OFFERED = 'dexhub.disc_menu.offered'


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return en


def _log(msg):
    xbmc.log('[DexHub] disc menu: %s' % msg, xbmc.LOGINFO)


def is_disc(url):
    """A disc image or disc folder Kodi may open in its menus (not one of its playlists)."""
    path = str(url or '').split('|', 1)[0].split('?', 1)[0].lower()
    if not path or '.mpls' in path:
        return False
    return (path.endswith(('.iso', '.img', '/index.bdmv', '/bdmv', '/video_ts.ifo'))
            or path.startswith(('udf://', 'bluray://')) or '/bdmv/' in path)


def in_menus():
    """The playing disc runs in its menus (Kodi's navigation mode)."""
    return bool(xbmc.getCondVisibility('Player.HasVideo + VideoPlayer.HasMenu'))


def _rpc(method, params):
    try:
        answer = json.loads(xbmc.executeJSONRPC(json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})))
        return answer.get('result')
    except Exception:
        return None


def _playback_setting():
    result = _rpc('Settings.GetSettingValue', {'setting': 'disc.playback'}) or {}
    try:
        return int(result.get('value'))
    except (TypeError, ValueError):
        return None


def _set_playback_setting(value):
    _rpc('Settings.SetSettingValue', {'setting': 'disc.playback', 'value': int(value)})


_SUB_EXT = ('.srt', '.ass', '.ssa', '.sub', '.vtt', '.smi', '.idx', '.sup')
_WATCHING = {'file': ''}
_HOLD = 4                    # seconds a subtitle file stays switched on before it counts


def _newest_subtitle(since, stem=''):
    """The subtitle file Kodi just put on the player: the one named `stem` (Kodi's name for
    the stream) when a stem is given, else the one written last since `since`, where Kodi and subtitle add-ons keep
    them (special://temp, the custom subtitle folder, the add-ons' data folders); or ''."""
    import os
    import xbmcvfs
    roots = [(xbmcvfs.translatePath('special://temp/'), 3)]
    custom = (_rpc('Settings.GetSettingValue', {'setting': 'subtitles.custompath'}) or {}).get('value')
    if custom:
        roots.append((xbmcvfs.translatePath(custom), 2))
    roots.append((xbmcvfs.translatePath('special://profile/addon_data/'), 3))
    import re
    plain = lambda text: re.sub(r'[\W_]+', ' ', str(text or ''), flags=re.U).strip().lower()
    words = set(plain(stem).split())
    named, named_rank, best, best_time = '', None, '', since
    for root, depth in roots:
        top = root.rstrip('/\\').count(os.sep)
        for folder, dirs, files in os.walk(root):
            if folder.rstrip('/\\').count(os.sep) - top >= depth:
                dirs[:] = []
            for name in files:
                low = name.lower()
                if not low.endswith(_SUB_EXT):
                    continue
                full = os.path.join(folder, name)
                try:
                    stamp = os.path.getmtime(full)
                except OSError:
                    continue
                if words:
                    # Kodi's name for a file leaves out its language part ("- Arabic -")
                    own = set(plain(name.rsplit('.', 1)[0]).split())
                    if words <= own:
                        rank = (len(own - words), -stamp)
                        if named_rank is None or rank < named_rank:
                            named, named_rank = full, rank
                if stamp >= best_time:
                    best, best_time = full, stamp
    return named if words else best


def _playing_subtitle(player):
    """The file of the subtitle the player shows (Kodi gives its path), or ''."""
    import os
    try:
        import xbmcvfs
        path = str(player.getSubtitles() or '').strip()
        local = xbmcvfs.translatePath(path) if path else ''
        if local and os.path.isfile(local) and local.lower().endswith(_SUB_EXT):
            return local
    except Exception:
        pass
    return ''


def _external_names():
    """The names Kodi gives the playing video's subtitles ('name (External)' for a file) and
    the selected one's name."""
    players = _rpc('Player.GetActivePlayers', {}) or []
    video = [p for p in players if isinstance(p, dict) and p.get('type') == 'video']
    if not video:
        return [], ''
    found = _rpc('Player.GetProperties', {'playerid': video[0].get('playerid'),
                                          'properties': ['subtitles', 'currentsubtitle', 'subtitleenabled']}) or {}
    names = [str(s.get('name') or '') for s in found.get('subtitles') or [] if isinstance(s, dict)]
    current = str((found.get('currentsubtitle') or {}).get('name') or '') if found.get('subtitleenabled') else ''
    return names, current


def watch():
    """Run by the service when a video starts: while a disc plays in its menus, a subtitle
    file switched on (picked in Kodi's subtitle dialog with any add-on, or put on by another
    add-on) brings the offer. Files only added to the list (Dex Hub's background discovery,
    which keeps the disc's own subtitle on in its menus) do not."""
    import threading
    player = xbmc.Player()
    try:
        playing = player.getPlayingFile()
    except Exception:
        return
    if not is_disc(playing) or _WATCHING['file'] == playing:
        return
    _WATCHING['file'] = playing

    def run():
        monitor = xbmc.Monitor()
        shown, held, since = None, ('', 0), time.time()
        try:
            while not monitor.abortRequested() and player.isPlayingVideo() and player.getPlayingFile() == playing:
                if in_menus():
                    _names, current = _external_names()
                    if shown is None:
                        shown = current
                    elif current != shown and current.endswith(' (External)'):
                        # Dex Hub's attacher selects a file for a moment and puts the disc's
                        # own subtitle back: only a choice that stays counts
                        held = (current, held[1] + 1) if held[0] == current else (current, 1)
                        if held[1] >= _HOLD:
                            path = _playing_subtitle(player) or _newest_subtitle(since - 5, current[:-len(' (External)')]) \
                                or _newest_subtitle(since - 5)
                            _log('a subtitle file was switched on in the menus (%s)' % (path or 'no file found'))
                            offer(path, path.replace('\\', '/').rsplit('/', 1)[-1] if path else '')
                            shown, held = current, ('', 0)
                    else:
                        shown, held = current, ('', 0)
                if monitor.waitForAbort(1.0):
                    break
        except Exception as exc:
            _log('watch: %s' % exc)
        finally:
            if _WATCHING['file'] == playing:
                _WATCHING['file'] = ''

    threading.Thread(target=run, name='DexHubDiscSubtitles', daemon=True).start()


def _addon_id():
    try:
        import xbmcaddon
        return xbmcaddon.Addon().getAddonInfo('id') or 'plugin.video.dexhub'
    except Exception:
        return 'plugin.video.dexhub'


def offer(subtitle_path='', name=''):
    """After a subtitle file is put on a disc in its menus: play the disc again as
    its main movie, from the same place, with that subtitle (once per disc)."""
    if not in_menus():
        return False
    try:
        mark = hashlib.sha1(xbmc.Player().getPlayingFile().encode('utf-8', 'replace')).hexdigest()[:16]
    except Exception:
        mark = ''
    home = xbmcgui.Window(10000)
    if mark and home.getProperty(OFFERED) == mark:
        return False            # asked once for this disc
    home.setProperty(OFFERED, mark)
    query = urlencode({'action': 'disc_subtitle', 'path': subtitle_path or '', 'name': name or ''})
    xbmc.executebuiltin('RunPlugin(plugin://%s/?%s)' % (_addon_id(), query))
    return True


def replay(subtitle_path='', name=''):
    """The main movie again, without the menus, from the same place, with the subtitle file
    (the disc_subtitle action). v5.10.572: at once, with no question."""
    title = _t('ترجمة على قرص بقائمته', 'A subtitle on a disc in its menus')
    player = xbmc.Player()
    if not in_menus():
        return
    from .playback.session import load_session, save_session
    ctx = load_session() or {}
    stream_key = str(ctx.get('stream_key') or '') if isinstance(ctx, dict) else ''
    if not stream_key:
        # not a Dex Hub playback: nothing to play again; say how, without a dialog to answer
        xbmcgui.Dialog().notification(title, _t(
            'ملف الترجمة ما يطلع والقرص بقائمته. للترجمة: إعدادات كودي ‹ الأقراص ‹ تشغيل الفيلم الرئيسي',
            "A subtitle file doesn't show in a disc's menus. For it: Kodi Settings › Discs › Play main movie"),
            xbmcgui.NOTIFICATION_INFO, 7000)
        return
    xbmcgui.Dialog().notification(title, _t('يشغّل الفيلم الرئيسي من نفس المكان مع الترجمة',
                                            'Playing the main movie from here with the subtitle'),
                                  xbmcgui.NOTIFICATION_INFO, 4000)
    try:
        position = float(player.getTime() or 0.0)
    except Exception:
        position = 0.0
    before = _playback_setting()
    if before is not None and before != MAIN_TITLE:
        _set_playback_setting(MAIN_TITLE)       # for this start only, put back below
    snapshot = dict(ctx, source_switch_pending=True, source_switch_captured_at=int(time.time()))
    if position > 1.0:
        snapshot['resume_seconds'] = position
    save_session(snapshot)
    xbmcgui.Window(10000).setProperty('dexhub.switch.inplace', str(int(time.time())))
    _log('playing the main movie again at %.0f s with %s' % (position, name or 'the chosen subtitle'))
    query = {'action': 'play', 'stream_key': stream_key,
             'resume_seconds': '%.3f' % position if position > 1.0 else ''}
    if subtitle_path:
        query.update(switch_subtitle_path=subtitle_path, switch_subtitle_enabled='1',
                     switch_subtitle_name=name or '')
    xbmc.executebuiltin('RunPlugin(plugin://%s/?%s)' % (_addon_id(), urlencode(query)))
    # Kodi reads the setting when the new playback opens the disc; then it goes back
    monitor = xbmc.Monitor()
    started = time.monotonic()
    try:
        seen_stop = False
        while time.monotonic() - started < 120 and not monitor.abortRequested():
            if monitor.waitForAbort(0.5):
                break
            playing = xbmc.getCondVisibility('Player.HasVideo')
            if not playing:
                seen_stop = True
            elif seen_stop or time.monotonic() - started > 8:
                if not xbmc.getCondVisibility('VideoPlayer.HasMenu'):
                    try:
                        if float(player.getTime() or 0.0) > 0.0:
                            _log('the main movie plays without the menus')
                            break
                    except Exception:
                        pass
    finally:
        if before is not None and before != MAIN_TITLE:
            _set_playback_setting(before)
