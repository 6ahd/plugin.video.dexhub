# -*- coding: utf-8 -*-
"""Dex Hub as a Kodi subtitle service (v5.10.101).

Dex Hub is listed in Kodi's own subtitle search, next to the other subtitle
add-ons. Choosing it searches every subtitle add-on Dex Hub has (the Stremio
subtitle add-ons of the user's Nuvio or Stremio account, DexSubtitles
included) at once, for whatever is playing: a Dex Hub video, the Kodi
library, or another add-on. The results come back in one list, the languages
Kodi asks for first, and the chosen subtitle is downloaded and handed to the
player.

Kodi calls a subtitle service as a plugin:
  ?action=search&languages=English,Arabic&preferredlanguage=Arabic
  ?action=manualsearch&searchstring=<title>&languages=...
and then the path of the chosen result (?action=dexsub_download&...).
"""
import json
import os
import re
import sys
import time
from urllib.parse import quote_plus, urlencode

import xbmc
import xbmcgui
import xbmcplugin

ADDON_ID = 'plugin.video.dexhub'
ACTIONS = ('search', 'manualsearch', 'dexsub_download')
_MAX_RESULTS = 80


def wants(params):
    """True for the calls Kodi's subtitle dialog makes to Dex Hub."""
    action = str((params or {}).get('action') or '')
    if action == 'search':
        # the add-on has no other 'search' route; the language list marks
        # Kodi's subtitle dialog
        return 'languages' in params or 'preferredlanguage' in params
    return action in ('manualsearch', 'dexsub_download')


def _log(message, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] subtitle service: %s' % message, level)


def _handle():
    try:
        return int(sys.argv[1])
    except Exception:
        return -1


def _info(label):
    try:
        return (xbmc.getInfoLabel(label) or '').strip()
    except Exception:
        return ''


_NOT_LANGUAGES = ('unknown', 'none', 'original', 'default', 'forced_only', 'forced only')


def _lang_code(name):
    """Kodi's English language name (or a code) to a two-letter code.

    Kodi sends 'Unknown' as the preferred language when it is set to the
    audio's own language and the stream does not name it; that is no language.
    """
    text = str(name or '').strip()
    if not text or text.lower() in _NOT_LANGUAGES:
        return ''
    try:
        code = xbmc.convertLanguage(text, xbmc.ISO_639_1) or ''
    except Exception:
        code = ''
    if not code:
        from .subtitle_broker import _normalize_lang
        code = _normalize_lang(text)
    return str(code or '').lower()


def _lang_name(code):
    code = str(code or '').strip()
    if not code or code == 'und':
        return 'Unknown'
    try:
        name = xbmc.convertLanguage(code.split('-')[0], xbmc.ENGLISH_NAME) or ''
    except Exception:
        name = ''
    if code.lower() == 'pt-br':
        return 'Portuguese (Brazil)'
    return name or code


def _library_show_ids():
    """The TV show's ids for a Kodi library episode (subtitles key on the show)."""
    try:
        show_id = int(_info('VideoPlayer.TvShowDBID') or 0)
    except Exception:
        show_id = 0
    if show_id <= 0:
        return {}
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'VideoLibrary.GetTVShowDetails',
               'params': {'tvshowid': show_id, 'properties': ['uniqueid', 'imdbnumber']}}
    try:
        details = (json.loads(xbmc.executeJSONRPC(json.dumps(payload)) or '{}')
                   .get('result') or {}).get('tvshowdetails') or {}
    except Exception:
        return {}
    ids = dict(details.get('uniqueid') or {})
    if details.get('imdbnumber') and not ids.get('imdb'):
        ids['imdb'] = details['imdbnumber']
    return ids


def _playing_ctx(manual_title=''):
    """What is playing, as the subtitle broker's context."""
    season = _info('VideoPlayer.Season')
    episode = _info('VideoPlayer.Episode')
    is_episode = bool(_info('VideoPlayer.TVShowTitle') or (season and episode))
    ctx = {
        'media_type': 'series' if is_episode else 'movie',
        'title': _info('VideoPlayer.TVShowTitle') or _info('VideoPlayer.OriginalTitle')
                 or _info('VideoPlayer.Title'),
        'year': _info('VideoPlayer.Year'),
        'subtitle_service': True,
        'all_subtitle_addons': True,
    }
    if is_episode:
        ctx['season'] = season
        ctx['episode'] = episode
        show = _library_show_ids()
        ctx['show_imdb_id'] = show.get('imdb') or ''
        ctx['show_tmdb_id'] = show.get('tmdb') or ''
        ctx['show_tvdb_id'] = show.get('tvdb') or ''
    imdb = _info('VideoPlayer.UniqueID(imdb)') or _info('VideoPlayer.IMDBNumber')
    ctx['imdb_id'] = imdb if imdb.startswith('tt') else ''
    ctx['tmdb_id'] = _info('VideoPlayer.UniqueID(tmdb)')
    ctx['tvdb_id'] = _info('VideoPlayer.UniqueID(tvdb)')
    has_ids = any(ctx.get(key) for key in ('imdb_id', 'tmdb_id', 'tvdb_id', 'show_imdb_id',
                                           'show_tmdb_id', 'show_tvdb_id'))
    if not has_ids:
        # A Dex Hub video plays a minimal item without an info tag; the
        # player publishes its ids here for subtitle services (the series ids
        # for an episode).
        home = xbmcgui.Window(10000)
        prop = lambda key: (home.getProperty('dexhub.sub.%s' % key) or '').strip()
        if prop('imdb_id') or prop('tmdb_id') or prop('tvdb_id'):
            dex_episode = prop('mediatype') == 'episode'
            ctx.update({
                'media_type': 'series' if dex_episode else 'movie',
                'imdb_id': prop('imdb_id'), 'tmdb_id': prop('tmdb_id'),
                'tvdb_id': prop('tvdb_id'), 'title': prop('title') or ctx['title'],
            })
            if not dex_episode:
                # v5.10.247: a play published as a movie whose Dex Hub play
                # id names an episode ("tt26545992:1:2", a native Emby play)
                try:
                    from .playback.session import load_session
                    from .work_ids import as_episode_ctx
                    sess = as_episode_ctx(load_session() or {})
                    if sess.get('media_type') == 'series' and sess.get('episode'):
                        dex_episode = True
                        ctx['media_type'] = 'series'
                        home.setProperty('dexhub.sub.mediatype', 'episode')
                        home.setProperty('dexhub.sub.season', str(sess['season']))
                        home.setProperty('dexhub.sub.episode', str(sess['episode']))
                        for kind in ('imdb', 'tmdb'):
                            value = str(sess.get('show_%s_id' % kind) or '')
                            if value:
                                home.setProperty('dexhub.sub.%s_id' % kind, value)
                        if not sess.get('show_tvdb_id'):
                            home.setProperty('dexhub.sub.tvdb_id', '')   # the episode's own TVDb id
                        else:
                            home.setProperty('dexhub.sub.tvdb_id', str(sess['show_tvdb_id']))
                        ctx.update({'imdb_id': prop('imdb_id'), 'tmdb_id': prop('tmdb_id'),
                                    'tvdb_id': prop('tvdb_id')})
                except Exception:
                    pass
            if dex_episode:
                ctx.update({'season': prop('season'), 'episode': prop('episode'),
                            'show_imdb_id': prop('imdb_id'), 'show_tmdb_id': prop('tmdb_id'),
                            'show_tvdb_id': prop('tvdb_id')})
            else:
                ctx.pop('season', None)
                ctx.pop('episode', None)
            has_ids = True
    if ctx.get('media_type') != 'series':
        # v5.10.248: a Dex Hub play with an info tag (ids, but no season and
        # episode: a native Emby or Plex play) whose play id names an episode
        try:
            from .playback.session import load_session
            from .work_ids import as_episode_ctx, clean_imdb
            sess = load_session() or {}
            ep = as_episode_ctx(sess) if sess else {}
            same = True
            mine = clean_imdb(ctx.get('imdb_id'))
            if mine:
                theirs = set(filter(None, (clean_imdb(sess.get(k)) for k in
                                           ('imdb_id', 'show_imdb_id', 'canonical_id', 'video_id'))))
                same = not theirs or mine in theirs
            if ep.get('media_type') == 'series' and ep.get('episode') and same and xbmc.Player().isPlayingVideo():
                show = dict((k, str(ep.get('show_%s_id' % k) or '')) for k in ('imdb', 'tmdb', 'tvdb'))
                imdb = show['imdb'] or clean_imdb(ep.get('canonical_id')) or clean_imdb(ep.get('video_id')) or mine
                ctx.update({'media_type': 'series', 'season': str(ep['season']), 'episode': str(ep['episode']),
                            'show_imdb_id': imdb, 'imdb_id': imdb,
                            'show_tmdb_id': show['tmdb'] or ctx.get('show_tmdb_id') or '',
                            'show_tvdb_id': show['tvdb']})
                if not show['tvdb']:
                    ctx['tvdb_id'] = ''          # the episode's own TVDb id
                has_ids = True
        except Exception:
            pass
    if manual_title:
        # a title typed in Kodi's dialog: look it up, keep the episode numbers
        ctx.update({'title': manual_title, 'imdb_id': '', 'tmdb_id': '', 'tvdb_id': '',
                    'show_imdb_id': '', 'show_tmdb_id': '', 'show_tvdb_id': ''})
        has_ids = False
    if ctx.get('media_type') != 'series':
        # v5.10.242: no episode info tag, but the title or the file says S01E05
        from .work_ids import episode_from_text
        season, episode = episode_from_text(_info('VideoPlayer.Title'), _info('Player.Filename'),
                                            _info('Player.FilenameAndPath'))
        if episode:
            ctx.update({'media_type': 'series', 'season': str(season), 'episode': str(episode)})
            for kind in ('imdb', 'tmdb', 'tvdb'):
                if ctx.get('%s_id' % kind) and not ctx.get('show_%s_id' % kind):
                    ctx['show_%s_id' % kind] = ctx['%s_id' % kind]
            if ctx.get('title'):
                ctx['title'] = _strip_episode(ctx['title'])
    if not has_ids and ctx.get('title'):
        _lookup_ids(ctx)
    return ctx


def _strip_episode(title):
    """'Lanterns S01E05 1080p' -> 'Lanterns' (for the title lookup)."""
    import re
    cut = re.split(r'(?i)[ ._-]+(?:s\d{1,3}[ ._-]?e\d{1,4}|\d{1,2}x\d{2,3})\b', str(title or ''), 1)[0]
    return cut.replace('.', ' ').strip() or title


def _lookup_ids(ctx):
    """IMDb id from the title (Stremio subtitle add-ons are keyed on IMDb)."""
    kind = 'series' if ctx.get('media_type') == 'series' else 'movie'
    try:
        from . import tmdb_direct
        tmdb_id = tmdb_direct._resolve_tmdb_id(media_type=kind, title=ctx.get('title') or '',
                                               year=ctx.get('year') or '')
        imdb = tmdb_direct.imdb_id_for(tmdb_id, media_type=kind, timeout=6) if tmdb_id else ''
    except Exception as exc:
        _log('title lookup failed: %s' % exc, xbmc.LOGWARNING)
        return
    prefix = 'show_' if kind == 'series' else ''
    if tmdb_id:
        ctx[prefix + 'tmdb_id'] = str(tmdb_id)
    if imdb:
        ctx[prefix + 'imdb_id'] = str(imdb)


def _wanted_languages(params):
    codes = []
    for name in str(params.get('languages') or '').split(','):
        code = _lang_code(name)
        if code and code not in codes:
            codes.append(code)
    preferred = _lang_code(params.get('preferredlanguage') or '')
    if preferred:
        codes = [preferred] + [c for c in codes if c != preferred]
    # v5.10.247: Dex Hub's own subtitle languages (Settings, Arabic first by
    # default) lead. Kodi asks only for the languages of its own player
    # setting (often English alone), and the list then showed no Arabic
    try:
        from .subtitle_broker import _preferred_languages
        own = [_lang_code(c) or c for c in _preferred_languages()]
    except Exception:
        own = []
    out = []
    for code in own + codes:
        if code and _base(code) not in [_base(c) for c in out]:
            out.append(code)
    return out


def _base(code):
    return str(code or '').lower().split('-')[0]


# v5.10.117: the results look and sort as in DexSubtitles: the title clean
# (no emoji, no stray brackets Kodi would read as formatting), the provider
# in its colour, AI subtitles tagged and after the others, OpenSubtitles,
# SubDL, Wyzie and SubSource in that order.
_PROVIDER_COLORS = (('opensubtitles', 'lime'), ('subdl', 'deepskyblue'), ('wyzie', 'gold'),
                    ('subsource', 'magenta'), ('pro', 'violet'))
_PROVIDER_SCORE = (('opensubtitles', 40), ('subdl', 30), ('wyzie', 20), ('subsource', 10))
_EMOJI = re.compile(u'[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF\U0001FA70-\U0001FAFF'
                    u'\U0001F600-\U0001F64F]')
_TRAILING_LANG = re.compile(r'\s*[-\u2014\u2013|\u00b7\u2022]\s*(arabic|english|french|spanish)\s*$', re.I)


def _provider_color(provider):
    low = str(provider or '').lower()
    for key, colour in _PROVIDER_COLORS:
        if key in low:
            return colour
    return 'orange'


def _clean(name, fallback='Subtitle'):
    text = _EMOJI.sub('', str(name or '').strip())
    text = _TRAILING_LANG.sub('', text).strip()
    text = text.replace('[', '(').replace(']', ')')
    text = re.sub(r'\s+', ' ', text).strip(' -|_\u00b7\u2022')
    return text or fallback


def _is_ai(row):
    if row.get('is_ai') in (True, 'true', '1', 1) or str(row.get('kind') or '').lower() == 'ai':
        return True
    text = '%s %s' % (row.get('displayName') or '', row.get('sourceName') or '')
    return bool(re.search(r'\bAI\b|\U0001F916', text))


def _sort_key(row):
    provider = str(row.get('sourceName') or '').lower()
    group = 0 if 'uploaded' in provider else (2 if _is_ai(row) else 1)
    score = 0
    for key, value in _PROVIDER_SCORE:
        if key in provider:
            score = value
            break
    return (group, -score, str(row.get('displayName') or '').lower())


def _order(rows, wanted):
    """The languages Kodi asked for first, in its order (others only if none
    match), each language as DexSubtitles sorts it."""
    rows = sorted(rows or [], key=_sort_key)
    if not wanted:
        return rows
    rank = dict((_base(code), i) for i, code in enumerate(wanted))
    matched = [row for row in rows if _base(row.get('lang')) in rank]
    if not matched:
        return rows
    return sorted(matched, key=lambda row: rank.get(_base(row.get('lang')), 99))


def _row_name(row):
    from .subtitle_broker import _display_name_from_url
    name = str(row.get('displayName') or '').strip() or _display_name_from_url(row.get('url'))
    return _clean(name or row.get('sourceName') or 'Subtitle')


def _row_label2(row, name):
    provider = _clean(row.get('sourceName') or 'Dex Hub', 'Dex Hub')
    text = '%s [COLOR %s](%s)[/COLOR]' % (name, _provider_color(provider), provider)
    if _is_ai(row):
        text += ' [COLOR yellow](AI)[/COLOR]'
    return text


# ------------------------------------------------------- AutoSync (v5.10.117)
_DEXSUBS = 'service.subtitles.dexworld'


def _dexsubs_enabled():
    try:
        return bool(xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % _DEXSUBS))
    except Exception:
        return False


def _add_sync_button(handle):
    """First in the list, as in DexSubtitles: sync the subtitle on screen to
    the video's timing. Shown while a subtitle shows (an external one, or an
    embedded one with an external file to choose from). With DexSubtitles
    enabled the button is DexSubtitles' own (one AutoSync on the device)."""
    try:
        from .subsync import autosync, dexwatch
        if not autosync._setting_bool('autosync_enabled', True) and not _dexsubs_enabled():
            return False
        player = xbmc.Player()
        media = player.getPlayingFile() if player.isPlayingVideo() else ''
        if not media or not autosync._media_kind(media):
            return False
        cur = dexwatch.current_subtitle()
        if not cur or cur.get('index') is None:
            return False
        if not cur.get('external'):
            since = time.time() - 1800
            cands = dexwatch.collect(media, since=since, own_dir=autosync.TEMP_DIR, deep_since=since,
                                     network=False)
            if not any(c['where'] != 'self' for c in cands):
                return False
        lang = cur.get('language') or 'ara'
        code, label = dexwatch.lang2(lang), dexwatch.lang_name(lang)
    except Exception as exc:
        _log('sync button: %s' % exc, xbmc.LOGWARNING)
        return False
    item = xbmcgui.ListItem(label=label, label2='[B][COLOR FF33CCFF]%s[/COLOR][/B]' % _tr(
        'زامن الترجمة الشغالة على توقيت الفيديو'), offscreen=True)
    item.setArt({'icon': '5', 'thumb': code})
    item.setProperty('language', code)
    item.setProperty('sync', 'true')
    item.setProperty('hearing_imp', 'false')
    if _dexsubs_enabled():
        url = 'plugin://%s/?action=download&dexsync=1&url=&name=%s&lang=%s' % (
            _DEXSUBS, quote_plus('DexSubtitles Sync'), code)
    else:
        url = 'plugin://%s/?%s' % (ADDON_ID, urlencode({'action': 'dexsub_download', 'dexsync': '1',
                                                        'lang': code, 'name': 'Dex Hub Sync'}))
    xbmcplugin.addDirectoryItem(handle=handle, url=url, listitem=item, isFolder=False)
    return True


_WHERE_LABEL = {
    'temp': 'آخر ترجمة اخترتها', 'addon': 'من إضافة', 'video': 'جنب الفيديو', 'custom': 'مجلد الترجمات',
    'item': 'من المصدر', 'item-url': 'من المصدر (رابط)', 'self': 'المزامنة',
}


def _kodi_copies_to_temp(media):
    """Kodi copies a returned file to special://temp for a stream (when no
    subtitle folder is set); otherwise next to the video, over a file there."""
    try:
        import xbmcvfs
        custom = xbmcvfs.translatePath('special://subtitles')
    except Exception:
        custom = ''
    return str(media).lower().startswith(('http://', 'https://')) and not custom


def _sync_current():
    """DexSubtitles' sync button: the subtitle on screen (or one picked)
    synced to the video's timing. The files to hand back to Kodi."""
    from .subsync import autosync, dexwatch
    tr = _tr
    player = xbmc.Player()
    media = player.getPlayingFile() if player.isPlayingVideo() else ''
    if not media:
        _note(tr('شغّل الفيديو أول، بعدين زامن'))
        return []
    cur = dexwatch.current_subtitle()
    since = time.time() - 6 * 3600
    cands = dexwatch.collect(media, since=since, own_dir=autosync.TEMP_DIR, deep_since=since)
    pick = dexwatch.identify(cur, cands, media)[0] if cur else None
    if pick and pick['where'] == 'self' and os.path.dirname(pick['path']) == autosync.CACHE_DIR:
        if _kodi_copies_to_temp(media):
            _note(tr('الترجمة الشغالة متزامنة من قبل'))
            return [pick['path']]
        autosync.defer_notify(tr('الترجمة الشغالة متزامنة من قبل'), True)
        return []
    if not pick:
        others = [c for c in cands if c['where'] != 'self']
        others.sort(key=lambda c: (dexwatch._WHERE_RANK.get(c['where'], 0), c['mtime']), reverse=True)
        if not others:
            _note(tr('ما لقيت ترجمة خارجية أزامنها، والمدمجة هي المرجع نفسه'))
            return []
        head = (tr('الترجمة الشغالة مدمجة، اختر ترجمة خارجية') if (cur and not cur.get('external'))
                else tr('أي ترجمة تبي أزامنها؟'))
        labels = ['%s  [%s]' % (os.path.splitext(c['name'])[0][:70], tr(_WHERE_LABEL.get(c['where'], c['where'])))
                  for c in others[:25]]
        index = xbmcgui.Dialog().select(head, labels)
        if index < 0:
            return []
        pick = others[index]
    snap, _hash = dexwatch.snapshot(pick, autosync.WATCH_DIR, autosync._SESSION)
    if not snap:
        _note(tr('ما قدرت أقرأ ملف الترجمة'))
        return []
    dialog = None
    try:
        dialog = xbmcgui.DialogProgressBG()
        dialog.create('Dex Hub AutoSync', tr('أجهز الترجمة…'))
    except Exception:
        dialog = None

    def progress(pct, msg):
        if dialog:
            dialog.update(pct, 'Dex Hub AutoSync', msg)
    try:
        result = autosync.sync_now(snap, media, name=os.path.basename(pick['name']), progress=progress)
    finally:
        try:
            if dialog:
                dialog.close()
        except Exception:
            pass
    ok = result['status'] in ('synced', 'cached', 'in_sync')
    if _kodi_copies_to_temp(media):
        autosync.notify_always(result['msg'], ok)
        return [result['path'] if result.get('path') else snap]
    # a local or network video: applied here, so Kodi's dialog does not
    # write over a subtitle next to the video (it then says it could not
    # download: nothing came back to it; the result shows right after)
    if result.get('path'):
        try:
            player.setSubtitles(result['path'])
            player.showSubtitles(True)
        except Exception as exc:
            _log('sync apply failed: %s' % exc, xbmc.LOGWARNING)
    autosync.defer_notify(result['msg'], ok)
    return []


def _tr(text):
    try:
        from .i18n import tr
        return tr(text)
    except Exception:
        return text


def _note(message):
    try:
        xbmcgui.Dialog().notification('Dex Hub', message, xbmcgui.NOTIFICATION_INFO, 3000)
    except Exception:
        pass


def _search(handle, params):
    started = time.monotonic()
    manual = str(params.get('action') or '') == 'manualsearch'
    ctx = _playing_ctx(manual_title=str(params.get('searchstring') or '').strip() if manual else '')
    wanted = _wanted_languages(params)
    from . import subtitle_broker
    try:
        rows = subtitle_broker.search_subtitles(ctx, max_results=_MAX_RESULTS)
    except Exception as exc:
        _log('search failed: %s' % exc, xbmc.LOGWARNING)
        rows = []
    rows = _order(rows, wanted)
    _add_sync_button(handle)
    for row in rows:
        url = str(row.get('url') or '')
        lang = str(row.get('lang') or 'und')
        name = _row_name(row)
        item = xbmcgui.ListItem(label=_lang_name(lang), label2=_row_label2(row, name), offscreen=True)
        item.setArt({'icon': '0', 'thumb': _base(lang) if lang != 'und' else ''})
        item.setProperty('language', _base(lang) if lang != 'und' else '')
        item.setProperty('sync', 'false')
        hearing = row.get('hearing_impaired') or row.get('hi') or row.get('sdh')
        item.setProperty('hearing_imp', 'true' if hearing in (True, 'true', '1', 1) else 'false')
        path = 'plugin://%s/?%s' % (ADDON_ID, urlencode({
            'action': 'dexsub_download', 'url': url, 'lang': lang, 'name': name[:120],
            'format': str(row.get('format') or row.get('codec') or '')}))
        xbmcplugin.addDirectoryItem(handle=handle, url=path, listitem=item, isFolder=False)
    xbmcplugin.endOfDirectory(handle)
    _log('%s for %s %s: %d result(s) in %.1fs (languages %s)' % (
        'manual search' if manual else 'search', ctx.get('media_type'),
        ctx.get('show_imdb_id') or ctx.get('imdb_id') or ctx.get('title') or '?',
        len(rows), time.monotonic() - started, ','.join(wanted) or 'any'))


def _download(handle, params):
    url = str(params.get('url') or '')
    lang = str(params.get('lang') or 'und')
    name = str(params.get('name') or 'subtitle')
    local = ''
    try:
        from .playback import subtitle_files as sf
        ext = sf._detect_subtitle_extension({'format': params.get('format') or ''}, url)
        stem = (sf._safe_subtitle_filename_part(name, limit=60) or 'subtitle').replace(' ', '.')
        target = os.path.join(sf._subs_tmp_dir(), 'dexhub.%s.%s.%s' % (
            stem, _base(lang) or 'und', ext))
        local = sf._download_remote_subtitle(url, target) or ''
    except Exception as exc:
        _log('download failed: %s' % exc, xbmc.LOGWARNING)
    if local:
        item = xbmcgui.ListItem(label=local)
        xbmcplugin.addDirectoryItem(handle=handle, url=local, listitem=item, isFolder=False)
        _log('downloaded %s (%s)' % (os.path.basename(local), lang))
    else:
        _log('download gave no subtitle: %s' % name, xbmc.LOGWARNING)
    xbmcplugin.endOfDirectory(handle, succeeded=bool(local))


def _sync_download(handle):
    files = []
    try:
        files = _sync_current()
    except Exception as exc:
        _log('sync failed: %s' % exc, xbmc.LOGWARNING)
        _note(_tr('ما قدرت أزامن الحين'))
    try:
        from .subsync import autosync
        for path in files:
            autosync.note_handled(path)
    except Exception:
        pass
    for path in files:
        xbmcplugin.addDirectoryItem(handle=handle, url=path, listitem=xbmcgui.ListItem(label=path), isFolder=False)
    # an empty answer is still an answer (DexSubtitles does the same): the
    # sync told the user why in its own note
    xbmcplugin.endOfDirectory(handle, cacheToDisc=False)


def run(params):
    handle = _handle()
    if handle < 0:
        return None
    if str(params.get('action') or '') == 'dexsub_download' and str(params.get('dexsync') or '') == '1':
        _sync_download(handle)
    elif str(params.get('action') or '') == 'dexsub_download':
        _download(handle, params)
    else:
        _search(handle, params)
    return None
