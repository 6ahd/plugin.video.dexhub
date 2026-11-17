# -*- coding: utf-8 -*-
"""The player's panels in skin.dexhub 3.1 (Arctic Fuse 3's OSD, v5.10.114).

AF3 asked script.skinvariables and TMDb Helper for these; skin.dexhub asks
Dex Hub, so the player works without either add-on:

  skin_osd_streams   the playing video's audio or subtitle streams (a list)
  skin_osd_stream    switch to one of them
  skin_osd_next      the next episode, for the "up next" panel (a list)
  skin_osd_playnext  play it
  skin_osd_episodes  the playing season's episodes (the panel under the OSD)
  skin_osd_info      the playing title's page

Lists end the call with SystemExit like the skin's widgets (serve.widget):
two panels can ask at the same instant.
"""
import json
import time
from urllib.parse import parse_qsl

import xbmc
import xbmcgui
import xbmcplugin

from . import common as C

STREAMS = {'audio': ('audiostreams', 'currentaudiostream'),
           'subtitle': ('subtitles', 'currentsubtitle')}
# the OSD and its panels (AF3's closeosd list)
OSD_WINDOWS = ('videoosd', 'musicosd', '1140', '1141', '1142', '1143', '1144', '1145', '1146',
               '1147', '1148', '1149')
NEXT_PROPERTY = 'dexhub.tidb.next_episode.v1'


def _rpc(method, params=None):
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params is not None:
        payload['params'] = params
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(payload))).get('result')
    except Exception:
        return None


def _video_player():
    for player in _rpc('Player.GetActivePlayers') or []:
        if player.get('type') == 'video':
            return player.get('playerid', 1)
    return 1


def _end(handle, entries, content=''):
    if handle < 0:
        return
    if content:
        try:
            xbmcplugin.setContent(handle, content)
        except Exception:
            pass
    if entries:
        xbmcplugin.addDirectoryItems(handle, entries, len(entries))
    xbmcplugin.endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=False)


# --------------------------------------------------------------- streams
def streams(params, handle):
    kind = params.get('stream_type') or 'audio'
    keys = STREAMS.get(kind)
    entries = []
    if keys and xbmc.Player().isPlayingVideo():
        result = _rpc('Player.GetProperties', {'playerid': _video_player(), 'properties': list(keys)}) or {}
        current = result.get(keys[1]) or {}
        current_index = current.get('index') if isinstance(current, dict) else None
        rows = [s for s in (result.get(keys[0]) or []) if isinstance(s, dict)]
        if kind == 'subtitle':
            rows, toggle = _filter_subtitles(rows, current_index)
            # v5.10.320: the Classic player design keeps its 3.28.56 list
            if xbmc.getInfoLabel('Skin.String(DexHub.OsdDesign)') != 'classic':
                entries.append(_style_row())
            if toggle:
                entries.append(toggle)
        for stream in rows:
            index = stream.get('index', 0)
            li = xbmcgui.ListItem(label=stream.get('language') or 'UND', label2=stream.get('name') or '',
                                  offscreen=True)
            props = dict(('%s' % k, '%s' % v) for k, v in stream.items() if v not in (None, '', False, 0))
            props['index'] = str(index)
            if current_index == index:
                props['iscurrent'] = 'true'
            props['isfolder'] = 'false'
            # v5.10.317: flag, language, full name, badges (skinui/streamrows.py)
            from . import streamrows
            props.update(streamrows.props(kind, stream))
            li.setProperties(props)
            path = C.url('skin_osd_stream', method='set_player_%s' % ('audiostream' if kind == 'audio' else 'subtitle'),
                         index=str(index))
            entries.append((path, li, False))
    _end(handle, entries)


# ------------------------------------------------------------ subtitle filter
FILTER_INDEX = -7   # v5.10.311: the list's first row toggles the filter


def _filter_mode():
    try:
        from .. import ui_preferences
        return ui_preferences.value('player_subtitle_filter')
    except Exception:
        return 'off'


def _my_languages(mode):
    try:
        from ..playback.subtitle_files import _preferred_subtitle_lang_keys
        keys = _preferred_subtitle_lang_keys()
    except Exception:
        keys = ['ar', 'en']
    return keys[:1] if mode == 'first' else keys


def _stream_lang(stream):
    try:
        from ..playback.subtitle_files import _normalize_lang
        for value in (stream.get('language'), stream.get('name')):
            key = _normalize_lang(value) if value else 'und'
            if key and key != 'und':
                return key
    except Exception:
        pass
    return 'und'


def _filter_subtitles(rows, current_index):
    """v5.10.311: only the tracks in your languages (the one playing always
    stays), and a first row that says how many are hidden and switches the
    filter. Only the list changes: sync, next subtitle and the automatic pick
    read Kodi's full list."""
    from ..i18n import tr
    mode = _filter_mode()
    langs = _my_languages('first' if mode == 'first' else 'mine')
    mine = [s for s in rows if _stream_lang(s) in langs or s.get('index') == current_index]
    others = len(rows) - len(mine)
    if not rows or (others == 0 and mode == 'off'):
        return rows, None
    names = ' · '.join(l.upper() for l in langs)
    if mode == 'off':
        label = tr('إظهار لغاتي فقط')
        label2 = '%s  ·  %d' % (names, len(mine))
        shown = rows
    elif mine:
        label = tr('إظهار كل الترجمات')
        label2 = tr('%d مخفية') % others
        shown = mine
    else:
        # nothing in your languages: everything stays visible
        label = tr('لا توجد ترجمة بلغتك')
        label2 = names
        shown = rows
    li = xbmcgui.ListItem(label=label, label2=label2, offscreen=True)
    li.setProperties({'index': str(FILTER_INDEX), 'isfolder': 'false', 'dh_filter': mode,
                      'language': '', 'name': label2, 'dh_icon': 'dexhub/osd/legacy/languages.png'})
    path = C.url('skin_osd_stream', method='set_player_subtitle', index=str(FILTER_INDEX))
    return shown, (path, li, False)


STYLE_INDEX = -8   # v5.10.314: the row that opens the subtitle look


def _style_row():
    from ..i18n import tr
    li = xbmcgui.ListItem(label=tr('شكل الترجمة وإعداداتها'), label2=tr('الخط والحجم واللون والتأخير'), offscreen=True)
    li.setProperties({'index': str(STYLE_INDEX), 'isfolder': 'false', 'dh_style': '1', 'language': '', 'name': '',
                      'dh_icon': 'dexhub/osd/legacy/style.png'})
    return ('substyle://open', li, False)


def _toggle_filter():
    from .. import ui_preferences
    mode = _filter_mode()
    ui_preferences.set_value('player_subtitle_filter', 'off' if mode != 'off' else 'mine')


def set_stream(params):
    method = params.get('method') or ''
    try:
        index = int(params.get('index') or 0)
    except ValueError:
        index = 0
    if index == STYLE_INDEX:
        from . import subwindow
        return subwindow.open_window({'section': 'look'})
    if index == FILTER_INDEX:
        _toggle_filter()
        xbmc.executebuiltin('SetProperty(UID,%s)' % time.time())
        return
    player = _video_player()
    if method == 'set_player_audiostream':
        _rpc('Player.SetAudioStream', {'playerid': player, 'stream': index})
    elif method == 'set_player_subtitle':
        _rpc('Player.SetSubtitle', {'playerid': player, 'subtitle': index, 'enable': True})
    # the panel's list reads its content again (its path names this property)
    xbmc.executebuiltin('SetProperty(UID,%s)' % time.time())
    # v5.10.140: custom badges follow the new track from the service
    # (CompanionPlayer.onAVChange, once Kodi has switched); starting one more
    # add-on process here read the labels before the switch anyway


# ------------------------------------------------------------ next episode
def _next_episode():
    try:
        data = json.loads(C.home().getProperty(NEXT_PROPERTY) or '{}')
    except Exception:
        data = {}
    return data if isinstance(data, dict) and data.get('url') else {}


def _info(label):
    try:
        return xbmc.getInfoLabel(label) or ''
    except Exception:
        return ''


def _remaining():
    try:
        player = xbmc.Player()
        return max(0.0, float(player.getTotalTime()) - float(player.getTime()))
    except Exception:
        return 0.0


def _next_item():
    """(label, season, episode, path, art) of the episode after the one
    playing: Dex Hub's published next episode (its player's choice, same
    source), else the next card of the season's page (or the next season's
    first)."""
    data = _next_episode()
    if data:
        return {'title': data.get('title') or '', 'season': int(data.get('season') or 0),
                'episode': int(data.get('episode') or 0), 'path': data['url'], 'art': {}}
    playing = _playing()
    q = playing.get('q') or {}
    if not playing.get('series') or playing.get('season', -1) < 0 or playing.get('episode', -1) <= 0:
        return {}
    from . import title as T
    page = T.HeadlessTitle(q)
    page.load()
    season, episode = playing['season'], playing['episode']
    rows = page.episode_items(season) or []
    found = None
    for index, row in enumerate(rows):
        if _int((row.get('props') or {}).get('episode')) == episode:
            found = rows[index + 1] if index + 1 < len(rows) else None
            break
    if found is None:
        later = [_int(entry[0]) for entry in (page.seasons or []) if _int(entry[0]) > season]
        if later:
            rows = page.episode_items(min(later)) or []
            found = rows[0] if rows else None
    if not found:
        return {}
    props = found.get('props') or {}
    info = found.get('info') or {}
    return {'title': info.get('title') or found.get('label') or '', 'season': _int(props.get('season')),
            'episode': _int(props.get('episode')), 'path': props.get('dhs.target') or found.get('path') or '',
            'art': found.get('art') or {}}


def _int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def next_listing(params, handle):
    """The "up next" panel's list (AF3 window 1143): shown by the skin in the
    last ten minutes (or with a playlist after this video)."""
    entries = []
    try:
        if xbmc.Player().isPlayingVideo() and (_remaining() < 15 * 60 or _next_episode()):
            nxt = _next_item()
        else:
            nxt = {}
    except Exception as exc:
        C.log('osd next: %s' % exc, xbmc.LOGWARNING)
        nxt = {}
    if nxt and nxt.get('path'):
        season, episode = nxt.get('season') or 0, nxt.get('episode') or 0
        code = 'S%02dE%02d' % (season, episode) if season or episode else ''
        title = nxt.get('title') or code
        li = xbmcgui.ListItem(label=title, offscreen=True)
        tag = li.getVideoInfoTag()
        tag.setMediaType('episode')
        tag.setTitle(title)
        show = (_playing().get('q') or {}).get('title') or ''
        if show:
            tag.setTvShowTitle(show)
        if season:
            tag.setSeason(season)
        if episode:
            tag.setEpisode(episode)
        art = dict(nxt.get('art') or {})
        for key, label in (('landscape', 'Player.Art(landscape)'), ('fanart', 'Player.Art(fanart)'),
                           ('clearlogo', 'Player.Art(clearlogo)'), ('poster', 'Player.Art(poster)')):
            if not art.get(key):
                art[key] = _info(label)
        art['thumb'] = art.get('thumb') or art.get('landscape') or art.get('fanart') or ''
        li.setArt(dict((k, v) for k, v in art.items() if v))
        C.set_prop('dhs.osd.next', nxt['path'])
        entries.append((C.url('skin_osd_playnext'), li, False))
    _end(handle, entries, 'episodes')


def close_osd():
    for window in OSD_WINDOWS:
        xbmc.executebuiltin('Dialog.Close(%s)' % window)


def play_next(params):
    path = C.prop('dhs.osd.next')
    if not path:
        try:
            path = (_next_item() or {}).get('path') or ''
        except Exception:
            path = ''
    if not path:
        return
    close_osd()
    xbmc.executebuiltin('RunPlugin("%s")' % path.replace('"', '%22'))


# ------------------------------------------------------- playing title
def _playing():
    """What plays, as Dex Hub's title page names it: {'q': skin_open's query,
    'series', 'season', 'episode'}. Kodi 22's minimal playback item has
    neither the show nor the season in its info tag: Dex Hub's properties
    on the playing item and its source key say them."""
    player = xbmc.Player()
    try:
        tag = player.getVideoInfoTag()
    except Exception:
        return {}
    try:
        item = player.getPlayingItem()
    except Exception:
        item = None

    def prop(key):
        try:
            return (item.getProperty(key) if item is not None else '') or ''
        except Exception:
            return ''
    media = (tag.getMediaType() or '').lower()
    # Dex Hub's source key names what it plays: trusted while the playing
    # item is Dex Hub's (its properties are on it)
    dexhub_item = bool(prop('tmdb_type') or prop('imdb_id') or prop('tmdb_id'))
    key = (C.prop('dexhub.source.key') or '').split('|') if dexhub_item else []
    key_media = key[0] if key else ''
    series = (prop('tmdb_type') == 'tv' or media in ('episode', 'tvshow', 'season') or bool(tag.getTVShowTitle())
              or key_media in ('series', 'anime', 'tv'))

    def uid(name):
        try:
            return tag.getUniqueID(name) or ''
        except Exception:
            return ''
    # v5.10.209: an episode's own unique ids are the EPISODE's (Game of
    # Thrones S6E10 is TMDb 1187405, the show 1399): the title page asked TMDb
    # for /tv/1187405, got a 404 and showed no seasons. The show's ids come
    # from the item's show properties, else its Dex Hub ids, else the key.
    episode_tag = media == 'episode'
    imdb = (prop('tvshow.imdb_id') or prop('show_imdb_id') or prop('imdb_id')
            or ('' if episode_tag else uid('imdb')) or uid('tvshow.imdb'))
    if imdb and ':' in imdb:
        imdb = imdb.split(':')[0]
    tmdb = (prop('tvshow.tmdb_id') or prop('show_tmdb_id') or prop('tmdb_id')
            or ('' if episode_tag else uid('tmdb')) or uid('tvshow.tmdb'))
    if episode_tag and not tmdb and not imdb:
        tmdb = _show_of_episode(uid('imdb'))
    title = (tag.getTVShowTitle() if series else tag.getTitle()) or prop('tvshowtitle' if series else 'title') or ''
    q = {'m': 'series' if series else 'movie', 'title': title}
    if imdb.startswith('tt'):
        q['imdb'] = imdb
        q['id'] = imdb
    if tmdb:
        q['tmdb'] = tmdb
    canonical = key[1] if len(key) > 1 else ''
    if canonical and key_media in ('series', 'anime', 'movie', 'tv'):
        q['id'] = canonical
    try:
        year = tag.getYear()
    except Exception:
        year = 0
    if year and not series:
        q['year'] = str(year)
    data = _next_episode()
    if series and data and not canonical:
        nxt = dict(parse_qsl(data['url'].split('?', 1)[-1])).get('canonical_id') or ''
        if nxt:
            q['id'] = nxt
    season = _int(prop('season'), -1)
    if season < 0:
        season = _int(tag.getSeason(), -1)
    episode = _int(prop('episode'), -1)
    if episode < 0:
        episode = _int(tag.getEpisode(), -1)
    # a title Dex Hub can name (a file of the user's has no page here)
    if not (q.get('id') or q.get('tmdb')):
        return {}
    return {'q': q, 'series': series, 'season': season, 'episode': episode}


def _show_of_episode(episode_imdb):
    """The show's TMDb id from an episode's IMDb id (TMDb's /find), or ''."""
    episode_imdb = str(episode_imdb or '').split(':')[0]
    if not episode_imdb.startswith('tt'):
        return ''
    try:
        from .. import tmdb_direct as T
        found = T._request('/find/%s' % episode_imdb, {'external_source': 'imdb_id'}, timeout=6,
                           max_attempts=1) or {}
        rows = found.get('tv_episode_results') or []
        return str(rows[0].get('show_id') or '') if rows else ''
    except Exception:
        return ''


def info(params):
    playing = _playing()
    q = playing.get('q') or {}
    close_osd()
    if not q:
        xbmc.executebuiltin('ActivateWindow(fullscreeninfo)')
        return
    from .serve import _title_page
    if C.served() == 'dexhub' or _title_page():
        from . import actions
        return actions.open_title(q, -1)
    # elsewhere the add-on's own title page
    xbmc.executebuiltin('RunPlugin("%s")' % C.url('home_details', **q))


def episodes(params, handle):
    """The playing season's episodes, for the panel under the OSD (1140)."""
    entries = []
    playing = _playing() if xbmc.Player().isPlayingVideo() else {}
    home = xbmcgui.Window(10000)
    if not str(params.get('pick') or '').strip():
        # v5.10.288: no chips of the last show while this one's are read
        # (they were cleared only after a successful read)
        _publish_seasons(home, [], -1)
    if playing.get('series'):
        season = playing.get('season', -1)
        # v5.10.221: a season chosen over the panel (dhs.osd.season)
        try:
            if str(params.get('pick') or '').strip():
                season = int(params['pick'])
        except (TypeError, ValueError):
            pass
        try:
            from . import title as T
            from . import items as I
            page = T.HeadlessTitle(playing['q'])
            page.load()
            if len(page.seasons or []) < 2:
                # v5.10.288: a page kept from TMDb alone (or none) named one
                # season or none: the provider's episode list names them all
                try:
                    page.ensure_episodes()
                except Exception as exc:
                    C.log('osd episodes: seasons: %s' % exc, xbmc.LOGDEBUG)
            if season < 0 and page.seasons:
                season = page.seasons[0][0]
            rows = page.episode_items(season) if season >= 0 else []
            for row in rows:
                item = I.kodi_click(row)
                li = I.listitem(item)
                props = row.get('props') or {}
                # v5.10.451: the episode playing says so on its card (skin 3.28.170)
                if (season == playing.get('season') and _int(props.get('season'), -1) == season
                        and _int(props.get('episode'), -1) == playing.get('episode')):
                    li.setProperty('dhs.now', '1')
                entries.append((item.get('path') or '', li, bool(item.get('folder'))))
            # v5.10.221: the seasons for the chips over the panel, with no call
            # of their own; v5.10.288: published after the episodes, which may
            # have read the seasons the page did not know yet
            seasons = list(page.seasons or [])
            if not seasons and rows and season >= 0:
                from ..homeui.details import season_label
                seasons = [(season, season_label(page.tr, season, '', page.hero.get('title')))]
            _publish_seasons(home, seasons, playing.get('season', -1))
        except Exception as exc:
            C.log('osd episodes: %s' % exc, xbmc.LOGWARNING)
    _end(handle, entries, 'episodes')


def _publish_seasons(home, seasons, now):
    """The season chips over the episodes panel (Custom_1140 list 7100)."""
    seasons = list(seasons or [])[:20]
    for i in range(len(seasons) + 1, 21):
        home.clearProperty('dhs.osd.s.%d' % i)
        home.clearProperty('dhs.osd.s.%d.label' % i)
    for i, (number, label) in enumerate(seasons, 1):
        home.setProperty('dhs.osd.s.%d' % i, str(number))
        home.setProperty('dhs.osd.s.%d.label' % i, label)
    home.setProperty('dhs.osd.s.count', str(len(seasons)))
    home.setProperty('dhs.osd.s.now', str(now))


def seasons(params, handle):
    """v5.10.221: the playing show's seasons, as chips over the episodes
    panel (1140); OK on one shows its episodes."""
    entries = []
    playing = _playing() if xbmc.Player().isPlayingVideo() else {}
    if playing.get('series'):
        try:
            from . import title as T
            page = T.HeadlessTitle(playing['q'])
            page.load()
            now = playing.get('season', -1)
            for number, label in page.seasons or []:
                li = xbmcgui.ListItem(label)
                li.setProperty('season', str(number))
                if number == now:
                    li.setProperty('on', '1')
                entries.append(('', li, False))
        except Exception as exc:
            C.log('osd seasons: %s' % exc, xbmc.LOGWARNING)
    _end(handle, entries, '')
