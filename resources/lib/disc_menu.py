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


def _addon_id():
    try:
        import xbmcaddon
        return xbmcaddon.Addon().getAddonInfo('id') or 'plugin.video.dexhub'
    except Exception:
        return 'plugin.video.dexhub'


def offer(subtitle_path='', name=''):
    """After a subtitle file is put on a disc in its menus: offer to play the
    disc again as its main movie, from the same place, with that subtitle."""
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
    """The question, and the replay without the menus (the disc_subtitle action)."""
    title = _t('ترجمة على قرص بقائمته', 'A subtitle on a disc in its menus')
    player = xbmc.Player()
    if not in_menus():
        return
    from .playback.session import load_session, save_session
    ctx = load_session() or {}
    stream_key = str(ctx.get('stream_key') or '') if isinstance(ctx, dict) else ''
    if not stream_key:
        xbmcgui.Dialog().ok(title, _t(
            'ملفات الترجمة ما تتزامن مع القرص وهو شغّال بقائمته. ترجمة القرص نفسه تشتغل.\n'
            'عشان ملفات الترجمة: إعدادات كودي ‹ المشغّل ‹ الأقراص ‹ تشغيل البلوراي ‹ الفيلم الرئيسي.',
            "Subtitle files don't line up with a disc playing in its menus; the disc's own subtitles work.\n"
            'For subtitle files: Kodi Settings › Player › Discs › Blu-ray playback › Play main movie.'))
        return
    if not xbmcgui.Dialog().yesno(title, _t(
            'ملفات الترجمة ما تتزامن مع القرص وهو شغّال بقائمته، فما تطلع. ترجمة القرص نفسه تشتغل.\n'
            'أعيد تشغيل الفيلم الرئيسي بدون القائمة من نفس المكان مع هذي الترجمة؟',
            "Subtitle files don't line up with a disc playing in its menus, so they don't show; the disc's own "
            'subtitles do.\nPlay the main movie again without the menus, from here, with this subtitle?'),
            nolabel=_t('لا', 'No'), yeslabel=_t('أعد التشغيل', 'Play again')):
        return
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
