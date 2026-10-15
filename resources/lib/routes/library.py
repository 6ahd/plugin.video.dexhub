# -*- coding: utf-8 -*-
"""The Library: the skin's "My Library" tab and its rows (v5.10.153).

Routes (plugin://plugin.video.dexhub/?action=...):
  lib_favs&media=        Dex Hub's own favourites of one type
  lib_watchlist          the watchlists of the linked services together
  lib_floppy&status=     Floppy titles with one status
  lib_floppy_list&id=    one Floppy list
  lib_calendar           new episodes this week (Floppy's calendar, else Trakt's)
  track_panel            the tracking panel for one title (tracking_ui)
  lib_rate_mode          the rating prompt setting (Always, Movies only, Off)

skin_rows() gives the skin's Library tab (a Home tab of its own, "library",
next to All, Movies and Series): its rows are these listings, read and kept
by the service like every other Home row, so a title opens Dex Hub's
title page and the hero shows the focused title.
"""
import time
import threading
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui

from ..i18n import is_english


def _setting(key):
    """A Dex Hub setting through the short-lived cache (v5.10.184: each Home
    publish made a new Addon object per read, 10 to 30 ms each while Kodi
    draws)."""
    try:
        from ..settings_cache import cached_addon
        return cached_addon().getSetting(key)
    except Exception:
        import xbmcaddon
        return xbmcaddon.Addon('plugin.video.dexhub').getSetting(key)

ADDON = xbmcaddon.Addon()
BASE = 'plugin://plugin.video.dexhub/'
_refresh_lock = threading.Lock()
_retry_at = 0.0
_retry_owner = ''


def refresh_later(on_done=None, submit=None):
    """Headers and list names never wait for a network response."""
    global _retry_at, _retry_owner
    import datetime
    from .. import floppy_api
    month = datetime.date.today().strftime('%Y-%m')
    if not floppy_api.configured():
        return
    owner = floppy_api.account_key()
    if owner != _retry_owner:
        _retry_owner, _retry_at = owner, 0.0
    if time.time() < _retry_at:
        return
    if floppy_api.is_fresh('lists', 900) and floppy_api.is_fresh('stats:' + month, 3600):
        return
    if time.time() < _retry_at or not _refresh_lock.acquire(False):
        return
    _retry_at = time.time() + 30.0

    def run():
        try:
            before = (floppy_api.lists(cache_only=True), floppy_api.month_stats(cache_only=True))
            after = (floppy_api.lists(), floppy_api.month_stats())
            if floppy_api.account_key() == owner and after != before:
                from ..skinui import common as C
                C.ask_republish('library source')
                if on_done:
                    on_done()
        except Exception as exc:
            xbmc.log('[DexHub] library refresh: %s' % exc.__class__.__name__, xbmc.LOGDEBUG)
        finally:
            _refresh_lock.release()
    if submit:
        if not submit(run, priority=1, key=('library-summary', owner)):
            _refresh_lock.release()
    else:
        thread = threading.Thread(target=run, name='dexhub-library-summary')
        thread.daemon = True
        thread.start()



def _t(ar, en):
    return en if is_english() else ar


def _url(**params):
    return BASE + '?' + urlencode(params)


def _win():
    return xbmcgui.Window(10000)


# ---------------------------------------------------------------- rendering
def _render(rows, content='videos', track=True, source='', more=None):
    """Favourite-shaped rows ({media_type, canonical_id, title, poster, ...})
    as Dex Hub titles (the favourites page's own way)."""
    from .. import plugin as P
    from ..ui_core import add_item, end_dir, _merge_seed_ids, _get_tmdbh_seed_ids
    from ..art import extract_ids
    for row in rows:
        try:
            media_type = (row.get('media_type') or 'movie').lower()
            canonical = row.get('canonical_id') or ''
            if not canonical:
                continue
            title = row.get('title') or canonical
            year = _safe_year(row.get('year'))
            poster = row.get('poster') or (
                'https://images.metahub.space/poster/medium/%s/img' % canonical if canonical.startswith('tt') else '')
            art = {'poster': poster, 'thumb': poster, 'fanart': row.get('background') or '',
                   'landscape': row.get('background') or '', 'clearlogo': row.get('clearlogo') or '', '_seeded': True}
            ids = _merge_seed_ids(extract_ids({'id': canonical}), _get_tmdbh_seed_ids(canonical))
            if row.get('tmdb') and not ids.get('tmdb_id'):
                ids['tmdb_id'] = str(row['tmdb'])
            series = media_type in ('series', 'anime', 'tv', 'show')
            target_key = 'favorites_series' if series else 'favorites_movie'
            path, folder = P._content_click_path(
                media_type=media_type, canonical_id=canonical, title=title,
                tmdb_id=ids.get('tmdb_id') or '', imdb_id=ids.get('imdb_id') or '', tvdb_id=ids.get('tvdb_id') or '',
                source_provider_id=target_key, ui_seed=art)
            info = {'title': title, 'year': year, 'plot': row.get('plot') or '',
                    'mediatype': 'tvshow' if series else 'movie',
                    'genres': row.get('genres') or [], 'rating': row.get('rating') or 0}
            menu = []
            if track:
                menu.append((_t('تتبّع (الحالة والتقييم والقوائم)', 'Track (status, rating, lists)'),
                             'RunPlugin(%s)' % _url(action='track_panel', media='series' if series else 'movie',
                                                    canonical=canonical, tmdb=ids.get('tmdb_id') or '',
                                                    title=title, year=str(year or ''), poster=poster)))
            if source == 'local':
                menu.append((_t('إزالة من المفضلة', 'Remove from Favorites'),
                             'RunPlugin(%s)' % _url(action='fav_remove', media_type=media_type,
                                                    canonical_id=canonical, source='local')))
            art.pop('_seeded', None)
            props = {}
            if row.get('badge'):
                props['dhs.badge'] = row['badge']
            add_item(title if not year else '%s (%d)' % (title, year), path, is_folder=folder, info=info, art=art,
                     context_menu=menu, properties=props or None)
        except Exception as exc:
            xbmc.log('[DexHub] library row: %s' % exc, xbmc.LOGDEBUG)
    if more:
        add_item(_t('المزيد', 'More'), _url(**more), is_folder=True)
    end_dir(content=content, cache=False)


def _content_of(rows):
    kinds = set('series' if (r.get('media_type') or '') in ('series', 'anime', 'tv', 'show') else 'movie' for r in rows)
    return 'tvshows' if kinds == {'series'} else ('movies' if kinds == {'movie'} else 'videos')


def _note(text):
    from ..ui_core import add_item, end_dir
    # v5.10.167: marked, so the add-on's own Library page hides the row
    # (a blank card there) while a skin's widget still shows the line
    add_item('[COLOR grey]%s[/COLOR]' % text, _url(action='lib_favs'), is_folder=False,
             properties={'dexhub.note': '1'})
    end_dir(content='videos', cache=False)


# ---------------------------------------------------------------- the rows
def favs(media=''):
    from .. import favorites_store
    rows = favorites_store.list_favorites(limit=0, source='local') or []
    if media:
        want_series = media == 'series'
        rows = [r for r in rows if ((r.get('media_type') or '') in ('series', 'anime', 'tv', 'show')) == want_series]
    rows = _sorted(rows)[:100]
    if not rows:
        return _note(_t('ما فيه شي هنا للحين', 'Nothing here yet'))
    return _render(rows, _content_of(rows), source='local')


SORTS = (('added', 'آخر إضافة', 'Recently added'), ('title', 'الاسم', 'A to Z'),
         ('year', 'الأحدث سنة', 'Newest year'), ('oldest', 'الأقدم إضافة', 'Oldest added'))
TYPES = (('all', 'الكل', 'All'), ('movie', 'أفلام', 'Movies'), ('series', 'مسلسلات', 'Series'))


def _sorted(rows):
    order = (ADDON.getSetting('library_sort') or 'added').strip()
    pins = _pins()
    if order == 'title':
        rows = sorted(rows, key=lambda r: (r.get('title') or '').lower())
    elif order == 'year':
        rows = sorted(rows, key=lambda r: _safe_year(r.get('year')), reverse=True)
    elif order == 'oldest':
        rows = list(reversed(rows))
    return sorted(rows, key=lambda r: 0 if '%s|%s' % (r.get('media_type'), r.get('canonical_id')) in pins else 1)


def _pins():
    try:
        raw = ADDON.getSetting('library_pins') or ''
        return set(p for p in raw.split(',') if p)
    except Exception:
        return set()


def watchlist(page='0', media='', source=''):
    from .. import favorites_store
    rows = _sorted([r for r in favorites_store.list_favorites(limit=0, source=source or None) or []
                    if source or set(r.get('sources') or []) - {'local'}])
    if media in ('movie', 'series'):
        rows = [r for r in rows if ((r.get('media_type') or '') in ('series', 'anime', 'tv', 'show')) == (media == 'series')]
    rows = _library_filter(rows, {'action': 'lib_watchlist', 'media': media, 'source': source})
    try:
        start = max(0, int(page)) * 100
    except (TypeError, ValueError):
        start = 0
    batch = rows[start:start + 100]
    if not batch:
        return _note(_t('قائمة المشاهدة فارغة', 'No watchlist items'))
    more = {'action': 'lib_watchlist', 'page': str(start // 100 + 1), 'media': media, 'source': source} if start + 100 < len(rows) else None
    return _render(batch, _content_of(batch), more=more)


def floppy_status(status, media=''):
    from .. import floppy_api
    if not floppy_api.configured():
        return _note(_t('Floppy مو مربوط', 'Floppy is not set up'))
    rows = [_floppy_row(floppy_api.parts(e)) for e in floppy_api.by_status(status, media)]
    rows = [r for r in rows if r]
    if not rows:
        return _note(_t('ما فيه شي هنا للحين', 'Nothing here yet'))
    return _render(rows, _content_of(rows))


def floppy_list(list_id, media=''):
    from .. import floppy_api
    rows = [_floppy_row(floppy_api.parts(e)) for e in floppy_api.list_items(list_id)]
    rows = [r for r in rows if r]
    if media in ('movie', 'series'):
        # v5.10.286 (issue #16): a Floppy list split into its movies and its series
        rows = [r for r in rows if (r.get('media_type') == 'series') == (media == 'series')]
    if not rows:
        return _note(_t('القائمة فاضية', 'This list is empty'))
    return _render(rows, _content_of(rows))


def _floppy_row(parts):
    if not parts.get('media') or (not parts.get('tmdb') and not parts.get('imdb')):
        return None
    canonical = parts.get('imdb') or 'tmdb:%s' % parts['tmdb']
    return {'media_type': parts['media'], 'canonical_id': canonical, 'title': parts.get('title') or '',
            'poster': parts.get('poster') or '', 'background': parts.get('fanart') or '',
            'year': parts.get('year') or 0, 'plot': parts.get('plot') or '', 'tmdb': parts.get('tmdb') or ''}


def calendar():
    """New episodes this week as episode cards (newest first in the week)."""
    import datetime
    from ..ui_core import add_item, end_dir
    events = _calendar_events()
    if not events:
        return _note(_t('ما فيه حلقات جديدة هالأسبوع', 'No new episodes this week'))
    from .. import plugin as P
    today = datetime.date.today()
    # v5.10.212: Trakt's calendar has no pictures: what Dex Hub holds, else TMDb's cached art
    try:
        from ..homeui.follow import known_show_art
        known = known_show_art()
    except Exception:
        known = {}
    for ev in events[:40]:
        if not ev.get('fanart'):
            art = known.get(str(ev.get('imdb') or '').lower()) or known.get(str(ev.get('show') or '').lower())
            if not art and (ev.get('tmdb') or ev.get('imdb')):
                try:
                    from .. import tmdb_direct
                    got = tmdb_direct.art_cached_only(tmdb_id=ev.get('tmdb') or '', imdb_id=ev.get('imdb') or '',
                                                      media_type='series') or {}
                    art = (got.get('fanart') or '', got.get('poster') or '', got.get('clearlogo') or '')
                except Exception:
                    art = None
            if art and art[0]:
                ev = dict(ev, fanart=art[0], poster=ev.get('poster') or art[1])
        try:
            day = ev['date']
            delta = (day - today).days
            if delta == 0:
                when = _t('اليوم', 'Today')
            elif delta == 1:
                when = _t('بكرة', 'Tomorrow')
            elif delta == -1:
                when = _t('أمس', 'Yesterday')
            else:
                when = _day_name(day)
            code = 'S%02dE%02d' % (ev['season'], ev['episode'])
            canonical = ev.get('imdb') or ('tmdb:%s' % ev['tmdb'] if ev.get('tmdb') else '')
            if not canonical:
                continue
            art = {'poster': ev.get('poster') or '', 'thumb': ev.get('still') or ev.get('poster') or '',
                   'fanart': ev.get('fanart') or '', 'landscape': ev.get('fanart') or ev.get('still') or ''}
            path, folder = P._content_click_path(
                media_type='series', canonical_id=canonical, title=ev['show'], tmdb_id=ev.get('tmdb') or '',
                imdb_id=ev.get('imdb') or '', season=str(ev['season']), episode=str(ev['episode']),
                source_provider_id='favorites_series', ui_seed=art)
            info = {'title': ev.get('title') or code, 'tvshowtitle': ev['show'], 'season': ev['season'],
                    'episode': ev['episode'], 'mediatype': 'episode', 'aired': day.isoformat(),
                    'plot': '%s  ·  %s' % (when, code)}
            props = {'dhs.badge': when if delta <= 0 else '', 'dhs.sub': '%s · %s' % (code, when)}
            add_item('%s  %s' % (ev['show'], code), path, is_folder=folder, info=info, art=art, properties=props)
        except Exception as exc:
            xbmc.log('[DexHub] library calendar row: %s' % exc, xbmc.LOGDEBUG)
    end_dir(content='episodes', cache=False)


def because(media):
    """v5.10.208: TMDb's picks for the last film or show watched (the Home's
    "Because you watched" rows; My watching shows the same)."""
    from ..homeui import follow
    media = 'series' if media == 'series' else 'movie'
    try:
        _seed, entries = follow.because(media)
    except Exception as exc:
        xbmc.log('[DexHub] because you watched: %s' % exc, xbmc.LOGINFO)
        entries = []
    rows = []
    for entry in entries:
        tmdb = (entry.get('open') or {}).get('tmdb')
        if not tmdb:
            continue
        year = str(entry.get('line') or '')
        rows.append({'media_type': media, 'canonical_id': 'tmdb:%s' % tmdb, 'title': entry.get('title') or '',
                     'poster': entry.get('poster') or '', 'background': entry.get('thumb') or '',
                     'year': int(year) if year.isdigit() else 0, 'tmdb': tmdb})
    if not rows:
        return _note(_t('شاهد فيلماً أو مسلسلاً لتظهر اقتراحات هنا', 'Watch something to get picks here'))
    return _render(rows, 'tvshows' if media == 'series' else 'movies')


_DAYS_AR = ('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')
_DAYS_EN = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def _day_name(day):
    return (_DAYS_EN if is_english() else _DAYS_AR)[day.weekday()]


def _calendar_events():
    """[{show, season, episode, date, tmdb, imdb, poster, fanart, title}] sorted by day."""
    import datetime
    out = []
    from .. import floppy_api
    if floppy_api.configured():
        for ev in floppy_api.calendar(days=7, back=1):
            item = ev.get('item') or {}
            if item.get('media_type') not in ('episode', 'season', 'tv'):
                continue
            try:
                day = datetime.date.fromisoformat(str(ev.get('datetime') or '')[:10])
                season = int(item.get('season_number') or 0)
                episode = int(item.get('episode_number') or ev.get('content_number') or 0)
            except (TypeError, ValueError):
                continue
            if episode <= 0:
                continue
            out.append({'show': item.get('title') or '', 'season': season, 'episode': episode, 'date': day,
                        'tmdb': str(item.get('media_id') or '') if item.get('source') == 'tmdb' else '',
                        'imdb': '', 'poster': item.get('image') or '', 'fanart': item.get('backdrop') or '',
                        'title': ''})
    if not out:
        out = _trakt_calendar()
    out.sort(key=lambda e: (e['date'], e['show']))
    return out


def _trakt_calendar():
    import datetime
    try:
        from .. import trakt
        if not (trakt.enabled() and trakt.authorized()):
            return []
        start = datetime.date.today() - datetime.timedelta(days=1)
        key = 'dhs.lib.trakt_cal'
        win = _win()
        cached = win.getProperty(key)
        import json
        if cached:
            stamp, raw = cached.split('|', 1)
            if time.time() - float(stamp) < 3600:
                data = json.loads(raw)
            else:
                cached = ''
        if not cached:
            trakt._ensure_auth()
            data = trakt._request('/calendars/my/shows/%s/8' % start.isoformat(), auth=True, timeout=12) or []
            win.setProperty(key, '%.0f|%s' % (time.time(), json.dumps(data)))
        out = []
        for row in data or []:
            ep, show = row.get('episode') or {}, row.get('show') or {}
            ids = show.get('ids') or {}
            try:
                day = datetime.date.fromisoformat(str(row.get('first_aired') or '')[:10])
            except ValueError:
                continue
            out.append({'show': show.get('title') or '', 'season': int(ep.get('season') or 0),
                        'episode': int(ep.get('number') or 0), 'date': day, 'tmdb': str(ids.get('tmdb') or ''),
                        'imdb': ids.get('imdb') or '', 'poster': '', 'fanart': '', 'title': ep.get('title') or '',
                        'aired': str(row.get('first_aired') or '')})
        return out
    except Exception as exc:
        xbmc.log('[DexHub] trakt calendar: %s' % exc.__class__.__name__, xbmc.LOGINFO)
        return []


# ---------------------------------------------------------------- the tab
def linked_sources():
    from .. import tracking, favorites_store
    import os
    import xbmcvfs
    linked = list(tracking.linked_services())
    counts = favorites_store.source_counts()
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    if counts.get('nuvio') or os.path.isfile(os.path.join(profile, 'nuvio_token.json')):
        linked.append('Nuvio')
    if counts.get('stremio'):
        linked.append('Stremio')
    return linked


def _merged():
    """v5.10.212: Following and library, Merge services: one watchlist row for
    every service (each service's chip still shows its own)."""
    return (_setting('follow_merge') or 'true').lower() != 'false'


def _split():
    """v5.10.212: Continue Watching as two rows (movies, series), as on the Home."""
    try:
        from ..homeui.layout import continue_is_split
        return continue_is_split()
    except Exception:
        return False


_WATCH_KEYS = ('lib:nuvio', 'lib:stremio', 'lib:trakt', 'lib:splan', 'lib:mdb')


def _apply_follow_settings(specs):
    """The Following and library choices on a list of the tab's rows."""
    out = []
    for spec in specs:
        key = spec[0]
        if key == 'continue' and _split():
            from ..homeui import rows as R
            out.append(('continue_movies', _t('كمّل المشاهدة • أفلام', 'Continue watching • Movies'), 'wide',
                        dict(R.OWN_ROWS['continue_movies'][0])))
            out.append(('continue_shows', _t('كمّل المشاهدة • مسلسلات', 'Continue watching • Series'), 'wide',
                        dict(R.OWN_ROWS['continue_shows'][0])))
            continue
        out.append(spec)
    return _split_media_rows(out)


def _split_media_rows(specs):
    if (_setting('library_split_media') or 'true') == 'false':
        return specs
    result = []
    for key, title, shape, params in specs:
        if key in _WATCH_KEYS or key == 'lib:watch':
            source = params.get('source') or ''
            for media, ar, en in (('movie', 'أفلام', 'Movies'), ('series', 'مسلسلات', 'Series')):
                route = {'action': 'lib_watchlist', 'source': source, 'media': media}
                result.append((key + ':' + media, title + ' · ' + _t(ar, en), shape, route))
        elif key == 'lib:fplan' or (key.startswith('lib:fl') and params.get('action') == 'lib_floppy_list'):
            # v5.10.286 (issue #16): Floppy's Plan to watch and its lists split
            # too (their routes already take or now take media=movie/series)
            for media, ar, en in (('movie', 'أفلام', 'Movies'), ('series', 'مسلسلات', 'Series')):
                result.append((key + ':' + media, title + ' · ' + _t(ar, en), shape, dict(params, media=media)))
        else:
            result.append((key, title, shape, params))
    return result


def _merge_watchlists(specs):
    if not _merged() or sum(1 for s in specs if s[0] in _WATCH_KEYS) < 2:
        return specs
    out, placed = [], False
    for spec in specs:
        if spec[0] in _WATCH_KEYS:
            if not placed:
                out.append(('lib:watch', _t('قائمة المشاهدة', 'Watchlist'), 'poster', {'action': 'lib_watchlist'}))
                placed = True
            continue
        out.append(spec)
    return out


def _type():
    value = (_setting('library_type') or 'all').strip()
    return value if value in ('movie', 'series') else 'all'


def _type_filter(specs):
    """v5.10.303: the Library's Type chip (nuvio-native-legacy's filter
    chips): only films or only series in every row of the tab."""
    kind = _type()
    if kind == 'all':
        return specs
    other = 'series' if kind == 'movie' else 'movie'
    out = []
    for key, title, shape, params in specs:
        if key.endswith(':' + other) or key == ('continue_shows' if kind == 'movie' else 'continue_movies'):
            continue
        if key == 'nextup' and kind == 'movie':
            continue
        if key.endswith(':' + kind) or key.startswith('continue') or key == 'nextup':
            out.append((key, title, shape, params))
            continue
        if params.get('action') in ('lib_favs', 'lib_watchlist', 'lib_floppy', 'lib_floppy_list') and not params.get('media'):
            label = _t('أفلام', 'Movies') if kind == 'movie' else _t('مسلسلات', 'Series')
            out.append((key + ':' + kind, title + ' · ' + label, shape, dict(params, media=kind)))
            continue
        out.append((key, title, shape, params))
    return out


def cycle(what):
    """The Type or Sort chip was clicked: its next value, the rows again."""
    from ..skinui import common as C
    rows = TYPES if what == 'type' else SORTS
    setting = 'library_type' if what == 'type' else 'library_sort'
    keys = [k for k, _a, _e in rows]
    current = (ADDON.getSetting(setting) or keys[0]).strip()
    value = keys[(keys.index(current) + 1) % len(keys)] if current in keys else keys[1]
    ADDON.setSetting(setting, value)
    try:
        from ..settings_cache import invalidate
        invalidate()
    except Exception:
        pass
    _chip_labels(_win())
    C.ask_republish('library %s' % what)


def _chip_labels(win):
    sort = (ADDON.getSetting('library_sort') or 'added').strip()
    kind = (ADDON.getSetting('library_type') or 'all').strip()
    sort_label = next((_t(a, e) for k, a, e in SORTS if k == sort), SORTS[0][2])
    type_label = next((_t(a, e) for k, a, e in TYPES if k == kind), TYPES[0][2])
    win.setProperty('dhs.lib.type.label', _t('النوع', 'Type'))
    win.setProperty('dhs.lib.type.value', type_label)
    win.setProperty('dhs.lib.sort.label', _t('الترتيب', 'Sort'))
    win.setProperty('dhs.lib.sort.value', sort_label)


def tab_specs(linked, src):
    """The rows of one chip of the Library (skin.dexhub's tab and the add-on's
    page), with the Following and library choices applied."""
    if src == 'follow':
        specs = _follow_specs(linked)
    elif src == 'all':
        specs = _merge_watchlists(_specs(linked))
    else:
        specs = _filtered(_specs(linked), src)
    if src == 'follow':
        return _apply_follow_settings(specs)
    return _type_filter(_apply_follow_settings(specs))


def _specs(linked):
    """The Library tab's rows: [(key, title, shape, params)] in order."""
    floppy = 'Floppy' in linked
    out = [('continue', _t('كمّل المشاهدة', 'Continue watching'), 'wide', {'action': 'continue'})]
    if floppy or 'Trakt' in linked:
        out.append(('lib:cal', _t('حلقات جديدة هالأسبوع', 'New episodes this week'), 'landscape',
                    {'action': 'lib_calendar'}))
    if floppy:
        out.append(('lib:fwatch', _t('أشوفه الحين', 'Watching'), 'poster',
                    {'action': 'lib_floppy', 'status': 'In progress'}))
        out.append(('lib:fplan', _t('أبي أشوفه', 'Plan to watch'), 'poster',
                    {'action': 'lib_floppy', 'status': 'Planning'}))
    out.append(('lib:favm', _t('مفضلتي · أفلام', 'My Favorites · Movies'), 'poster',
                {'action': 'lib_favs', 'media': 'movie'}))
    out.append(('lib:favs', _t('مفضلتي · مسلسلات', 'My Favorites · Series'), 'poster',
                {'action': 'lib_favs', 'media': 'series'}))
    for name in ('Nuvio', 'Stremio'):
        if name in linked:
            out.append(('lib:' + name.lower(), name + ' · ' + _t('قائمتي', 'My list'), 'poster',
                        {'action': 'favorites', 'source': name.lower()}))
    if 'Trakt' in linked:
        out.append(('lib:trakt', _t('قائمة المشاهدة في Trakt', 'Trakt watchlist'), 'poster',
                    {'action': 'favorites', 'source': 'trakt'}))
    if 'Simkl' in linked:
        out.append(('lib:swatch', _t('أشوفه في Simkl', 'Watching on Simkl'), 'poster',
                    {'action': 'simkl_list_browse', 'kind': 'shows', 'status': 'watching'}))
        out.append(('lib:splan', _t('Simkl: أبي أشوفه', 'Simkl: Plan to watch'), 'poster',
                    {'action': 'favorites', 'source': 'simkl'}))
    if 'MDBList' in linked:
        out.append(('lib:mdb', _t('قائمة المشاهدة في MDBList', 'MDBList watchlist'), 'poster',
                    {'action': 'favorites', 'source': 'mdblist'}))
    if floppy:
        from .. import floppy_api
        for row in floppy_api.lists(cache_only=True)[:4]:
            out.append(('lib:fl%s' % row['id'], row.get('name') or 'Floppy', 'poster',
                        {'action': 'lib_floppy_list', 'id': str(row['id'])}))
        out.append(('lib:fdone', _t('خلصته مؤخراً', 'Recently completed'), 'poster',
                    {'action': 'lib_floppy', 'status': 'Completed'}))
    return out


# v5.10.155: the source chips of the tab: which rows each one shows
_CHIP_ROWS = {
    'fav': ('lib:favm', 'lib:favs'),
    'floppy': ('lib:fwatch', 'lib:fplan', 'lib:cal', 'lib:fdone'),
    'trakt': ('lib:trakt', 'lib:cal'),
    'simkl': ('lib:swatch', 'lib:splan'),
    'mdblist': ('lib:mdb',),
    'nuvio': ('lib:nuvio',),
    'stremio': ('lib:stremio',),
}


def _follow_specs(linked):
    """v5.10.212: My watching as the Library tab's own rows (the skin draws
    them like any Home row, read and kept by the service: no window to load)."""
    from ..homeui import follow
    out = [('continue', _t('تابع من حيث وقفت', 'Pick up where you left off'), 'wide', {'action': 'continue'}),
           ('nextup', _t('الحلقة القادمة', 'Next episode'), 'wide', {'action': 'nextup'}),
           ('lib:watch', _t('قائمة المشاهدة', 'Watchlist'), 'poster', {'action': 'lib_watchlist'})]
    if 'Floppy' in linked or 'Trakt' in linked:
        out.append(('lib:cal', _t('القادم', 'Coming up'), 'landscape', {'action': 'lib_calendar'}))
    for media, key in (('series', 'lib:because_s'), ('movie', 'lib:because_m')):
        seed = follow.last_title(media)
        if seed:
            out.append((key, _t('لأنك شاهدت %s', 'Because you watched %s') % seed, 'poster',
                        {'action': 'lib_because', 'media': media}))
    return out


def current_source(linked):
    # the window property is what the chip set a moment ago (another process's
    # Addon object keeps the settings it read); the setting is for the next start
    src = (_win().getProperty('dhs.lib.want') or _setting('library_src') or 'all').strip()
    names = {'floppy': 'Floppy', 'trakt': 'Trakt', 'simkl': 'Simkl', 'mdblist': 'MDBList', 'nuvio': 'Nuvio', 'stremio': 'Stremio'}
    if src in names and names[src] not in linked:
        return 'all'
    return src if src in ('all', 'fav', 'follow') or src in names else 'all'


def _filtered(specs, src):
    if src == 'all':
        return specs
    keep = _CHIP_ROWS.get(src, ())
    out = [s for s in specs if s[0] in keep or (src == 'floppy' and s[0].startswith('lib:fl') and s[0] != 'lib:fdone')]
    order = {key: n for n, key in enumerate(keep)}
    return sorted(out, key=lambda s: order.get(s[0], 50))


def skin_rows(src=None, header=True):
    """The Library tab for skin.dexhub's Home (homeui Row objects), from
    the service; also publishes the tab's header numbers.

    v5.10.238: src='all', header=False gives every row of the tab without
    touching the screen (phone setup arranges them)."""
    from .. import tracking
    from ..homeui import rows as R
    linked = linked_sources()
    if header:
        if _win().getProperty('dhs.tab') == 'library':
            refresh_later()
        try:
            _publish_header(linked)
        except Exception as exc:
            xbmc.log('[DexHub] library header: %s' % exc, xbmc.LOGINFO)
    rows = []
    specs = tab_specs(linked, src or current_source(linked))
    for key, title, shape, params in specs:
        if key in ('continue', 'nextup', 'continue_movies', 'continue_shows'):
            rows.append(R.Row(key, title, shape='wide', kind='continue',
                              meta={'params': dict(params)}))
        else:
            rows.append(R.Row(key, title, shape=shape, kind='catalog', meta={'params': dict(params)}))
    return rows


class _Header(object):
    """The header's Home properties, written when they change (v5.10.188:
    every publish wrote its 30 again, each one waits for Kodi's frame), and
    once a minute whatever happened to them meanwhile."""
    _seen = {}

    def __init__(self, win):
        self.win = win
        self.now = time.monotonic()

    def setProperty(self, key, value):
        last = self._seen.get(key)
        if last is not None and last[0] == value and self.now - last[1] < 60.0:
            return
        self._seen[key] = (value, self.now)
        self.win.setProperty(key, value)


def _publish_header(linked):
    """The header of the tab (skin.dexhub 3.21.1): synced line, this month in
    Floppy (or what the Library holds without it) and the source chips."""
    from .. import favorites_store, floppy_api
    real = _win()
    win = _Header(real)
    floppy = 'Floppy' in linked
    win.setProperty('dhs.lib.floppy', '1' if floppy else '')
    others = [name for name in linked if name != 'Floppy']
    if floppy:
        win.setProperty('dhs.lib.synced', _t('متزامن مع Floppy', 'Synced with Floppy'))
    else:
        win.setProperty('dhs.lib.synced', _t('الخدمات المربوطة', 'Linked services') if linked else
                        _t('اربط Floppy أو Trakt من الإعدادات', 'Link Floppy or Trakt in Settings'))
    win.setProperty('dhs.lib.linked', u'  \u00b7  '.join(others if floppy else linked))
    stats = floppy_api.month_stats(cache_only=True) if floppy else None
    numbers = []
    if current_source(linked) == 'follow':
        # v5.10.212: My watching's numbers (this month's hours, films, episodes)
        try:
            from ..homeui import follow
            numbers = follow.stats()
        except Exception:
            numbers = []
    try:
        counts = favorites_store.source_counts()
        total = favorites_store.count()
    except Exception:
        counts, total = {}, 0
    blocks = []
    if numbers:
        blocks = list(numbers)
    elif stats:
        hours = float(stats.get('hours') or 0)
        whole, minutes = int(hours), int(round((hours - int(hours)) * 60))
        blocks.append(('%dh %02dm' % (whole, minutes) if is_english() else u'%d س %d د' % (whole, minutes),
                       _t('مشاهدة هالشهر', 'watched this month')))
        blocks.append((str(stats.get('episodes') or 0), _t('حلقة', 'episodes')))
        blocks.append((str(stats.get('movies') or 0), _t('فيلم', 'movies')))
        if stats.get('streak'):
            blocks.append((_t('%d يوم', '%d days') % stats['streak'], _t('أيام متتالية', 'watch streak')))
    else:
        blocks.append((str(counts.get('local', 0)), _t('في مفضلتي', 'in My Favorites')))
        watch = sum(v for k, v in counts.items() if k not in ('local',))
        if watch:
            blocks.append((str(watch), _t('في قوائم المشاهدة', 'in watchlists')))
    for n in range(1, 5):
        value, label = blocks[n - 1] if n <= len(blocks) else ('', '')
        win.setProperty('dhs.lib.s%d' % n, value)
        win.setProperty('dhs.lib.s%dl' % n, label)
    chips = [('all', _t('الكل', 'All'), total), ('fav', _t('مفضلتي', 'Favorites'), counts.get('local', 0))]
    for name in ('Floppy', 'Trakt', 'Simkl', 'MDBList', 'Nuvio', 'Stremio'):
        chips.append((name.lower(), name if name in linked else '', counts.get(name.lower(), 0)))
    for n, (src, label, count) in enumerate(chips, 1):
        win.setProperty('dhs.lib.chip.%d.label' % n, label)
        win.setProperty('dhs.lib.chip.%d.count' % n, str(count or '') if label else '')
    real.setProperty('dhs.lib.src', current_source(linked))     # a chip sets it too
    _chip_labels(win)     # v5.10.303: the Type and Sort chips
    # v5.10.208: the first chip opens My watching (homeui/follow.py; skin.dexhub 3.27.8)
    win.setProperty('dhs.lib.follow', _t('متابعتي', 'My watching'))


def set_source(src):
    """A chip was clicked: that source's rows (the service publishes them)."""
    from .. import tracking
    from ..skinui import common as C
    src = src if src in ('all', 'fav', 'follow', 'floppy', 'trakt', 'simkl', 'mdblist', 'nuvio', 'stremio') else 'all'
    _win().setProperty('dhs.lib.want', src)
    ADDON.setSetting('library_src', src)
    linked = linked_sources()
    _win().setProperty('dhs.lib.src', current_source(linked))
    C.ask_republish('library source')


# ---------------------------------------------------------------- actions
def track_panel(params):
    from .. import tracking, tracking_ui
    work = tracking.Work.from_params(params)
    if work.canonical:
        tracking_ui.open_panel(work)


def rate_mode():
    choices = (('movies', _t('بعد الأفلام', 'After movies')),
               ('all', _t('بعد الأفلام وآخر حلقة في الموسم', 'After movies and season finales')),
               ('off', _t('مطفأ', 'Off')))
    current = (ADDON.getSetting('tracking_rate_prompt') or 'movies')
    pick = xbmcgui.Dialog().select(_t('نافذة التقييم بعد النهاية', 'Ask for a rating at the end'),
                                   [label for _key, label in choices],
                                   preselect=[k for k, _l in choices].index(current) if current in ('movies', 'all', 'off') else 0)
    if pick >= 0:
        ADDON.setSetting('tracking_rate_prompt', choices[pick][0])


def pin(params):
    key = '%s|%s' % (params.get('media_type') or 'movie', params.get('canonical_id') or '')
    pins = _pins()
    pins.symmetric_difference_update([key])
    ADDON.setSetting('library_pins', ','.join(sorted(pins)))
    xbmc.executebuiltin('Container.Refresh')


def dispatch(action, params):
    if action == 'lib_favs':
        return favs(params.get('media', ''))
    if action == 'lib_watchlist':
        return watchlist(params.get('page', '0'), params.get('media', ''), params.get('source', ''))
    if action == 'lib_floppy':
        return floppy_status(params.get('status', 'In progress'), params.get('media', ''))
    if action == 'lib_floppy_list':
        return floppy_list(params.get('id', '0'), params.get('media', ''))
    if action == 'lib_calendar':
        return calendar()
    if action == 'lib_because':
        return because(params.get('media', 'movie'))
    if action == 'lib_rate_mode':
        return rate_mode()
    if action == 'lib_src':
        return set_source(params.get('src', 'all'))
    if action == 'lib_cycle':
        return cycle(params.get('what', 'sort'))
    if action == 'lib_pin':
        return pin(params)
    if action == 'track_panel':
        return track_panel(params)
    return None


def _safe_year(value):
    try:
        result = int(value or 0)
        return result if 0 <= result <= 9999 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _library_filter(rows, route):
    """Use known library facts across the complete local list before slicing pages."""
    from .. import profile_scope
    from ..homeui import grid_filters as GF
    facets = GF.describe_local(route)
    saved = GF.load_state(profile_scope.path('homeui'), facets['key'])
    rules, order = GF.local_rules(facets, saved), GF.local_sort(saved)
    tiles = [dict(row, kind='work') for row in rows]
    # Do not turn an imported library into an empty list for fields it lacks.
    if not any(t.get('genres') for t in tiles):
        rules.pop('genre', None)
    if not any(t.get('rating') for t in tiles):
        rules.pop('rating', None)
    return GF.sort_tiles([t for t in tiles if GF.passes(t, rules)], order)
