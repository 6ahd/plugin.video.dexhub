from resources.lib.tvos_assets import resolve_assets as _tvos_assets
# -*- coding: utf-8 -*-
"""New episode reminders (v5.10.217).

On the day a new episode of a show the user follows comes out, Kodi shows a
notification once (Following and library: New episode reminders). Followed
shows come from the same calendar as My Library (Floppy, else Trakt); without
either, from the shows watched here lately (TMDb's next and last episode).

The service runs ``run(monitor)`` in a thread: a first look a minute and a
half after Kodi starts, then every half hour. Nothing is shown while a video
plays; it waits for the video to end. Each episode is announced once, and an
episode already started here is not announced at all.

The day's new episodes also go to the Home window (``dhs.newep.*``), where
skin.dexhub's island shows them until midnight.
"""
import datetime
from . import profile_scope as _scope
import json
import os
import time

import xbmc
import xbmcgui

_FIRST_WAIT = 90.0
_EVERY = 30 * 60.0
_KEEP_DAYS = 30
_MAX_SHOWS = 15


def _addon():
    import xbmcaddon
    return xbmcaddon.Addon('plugin.video.dexhub')


def enabled():
    try:
        return (_addon().getSetting('episode_reminders') or 'true').strip().lower() != 'false'
    except Exception:
        return True


def _tr(text):
    try:
        from .i18n import tr
        return tr(text)
    except Exception:
        return text


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub][reminders] %s' % msg, level)


def _path():
    import xbmcvfs
    return _scope.path('reminders.json')


def _load():
    try:
        with open(_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    cutoff = time.time() - _KEEP_DAYS * 86400
    data = dict((k, v) for k, v in data.items() if float(v or 0) > cutoff)
    try:
        _scope.require_current()
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    except Exception:
        pass


# ------------------------------------------------------------------ sources
def _local_day(event):
    """The local day an event airs on, and whether it is out already."""
    aired = str(event.get('aired') or '')
    if aired:
        try:
            when = datetime.datetime.fromisoformat(aired.replace('Z', '+00:00'))
            local = when.astimezone()
            return local.date(), local <= datetime.datetime.now().astimezone()
        except ValueError:
            pass
    day = event.get('date')
    if isinstance(day, str):
        try:
            day = datetime.date.fromisoformat(day[:10])
        except ValueError:
            return None, False
    return day, True


def _calendar():
    """The followed shows' episodes of this week (Floppy, else Trakt)."""
    try:
        from .routes import library
        return list(library._calendar_events() or [])
    except Exception as exc:
        _log('calendar unavailable: %s' % exc, xbmc.LOGDEBUG)
        return []


def _watched_shows():
    """{tmdb_id: name} of the series played here in the last six weeks."""
    out = {}
    try:
        from .dexhub import playback_store
        rows = playback_store.list_recent_items(limit=300, include_watched=True)
    except Exception:
        return out
    since = time.time() - 42 * 86400
    from . import episode_titles
    for row in rows:
        if not (row.get('season') or str(row.get('media_type') or '').lower() in ('series', 'tv', 'anime')):
            continue
        if float(row.get('updated_at') or 0) < since:
            continue
        tmdb, imdb = episode_titles.show_ids(row)
        if not tmdb and imdb:
            try:
                from . import tmdb_direct
                tmdb = tmdb_direct._season_show('', imdb, cached_only=False) or ''
            except Exception:
                tmdb = ''
        if tmdb and tmdb not in out:
            out[tmdb] = str(row.get('show_title') or '')
        if len(out) >= _MAX_SHOWS:
            break
    return out


# ------------------------------------------------------------------ v5.10.231
# One way for everything: the titles followed here (favourites and the
# linked services' watchlists), the shows watched lately and the calendar
# (Floppy, else Trakt) give the titles; TMDb gives the dates and the art;
# every source add-on is asked, the way playback asks them, whether the
# episode or the film can be played yet. Only then is it announced.
_SOON_DAYS = 7
_MOVIE_BACK = 10            # a film's digital release counts this many days
_CHECK_AGAIN = 2 * 3600.0   # a title out but without streams is asked again
_GIVE_UP = 3 * 86400.0      # and no longer after three days
_MAX_MOVIES = 30
_ASK_PER_PASS = 4        # source add-ons asked about this many titles per pass at most
_TYPE_DIGITAL, _TYPE_PHYSICAL = 4, 5


def _favourites():
    """[(media, canonical, title)] of the favourites (every service, and here)."""
    try:
        from . import favorites_store
        rows = favorites_store.list_favorites(limit=0) or []
    except Exception:
        return []
    out = []
    for row in rows:
        media = str(row.get('media_type') or '').lower()
        media = 'series' if media in ('series', 'tv', 'show', 'tvshow', 'anime') else 'movie'
        out.append((media, str(row.get('canonical_id') or ''), str(row.get('title') or '')))
    return out


def _tmdb_of(canonical, media, cached_only=False):
    from . import tmdb_direct as T
    canonical = str(canonical or '')
    if canonical.startswith('tmdb:'):
        return canonical.split(':')[1]
    base = canonical.split(':')[0]
    if base.startswith('tt'):
        if media == 'series':
            return str(T._season_show('', base, cached_only=cached_only) or '')
        key = 'movie_of_imdb:%s' % base
        known = T._cache_get(key)
        if known is not None:
            return str(known or '')
        if cached_only:
            return ''
        found = str(T._resolve_tmdb_from_imdb(base, 'movie') or '')
        T._cache_set(key, found, 30 * 86400 if found else 86400)
        return found
    return ''


def _show(tmdb):
    """A show's next and last episode, art and IMDb id (TMDb, cached until
    close to its next episode)."""
    from . import tmdb_direct as T
    key = 'tv_air3:%s' % tmdb
    data = T._cache_get(key)
    if data is not None:
        return data
    try:
        raw = T._request('/tv/%s' % tmdb, {'append_to_response': 'external_ids'},
                         timeout=6, max_attempts=1) or {}
    except Exception:
        raw = {}
    eps = []
    for name in ('last_episode_to_air', 'next_episode_to_air'):
        ep = raw.get(name)
        if isinstance(ep, dict) and ep.get('air_date'):
            eps.append({'season': int(ep.get('season_number') or 0), 'episode': int(ep.get('episode_number') or 0),
                        'date': str(ep['air_date'])[:10], 'title': str(ep.get('name') or ''),
                        # v5.10.313: the panel's Schedule badges (finale, mid_season)
                        'type': str(ep.get('episode_type') or '')})
    networks = [n.get('name') for n in raw.get('networks') or [] if isinstance(n, dict) and n.get('name')]
    # v5.10.334: each season's episodes (the island's "6 of 8 episodes")
    seasons = dict((str(s.get('season_number')), int(s.get('episode_count') or 0))
                   for s in raw.get('seasons') or [] if isinstance(s, dict) and s.get('season_number') is not None)
    data = {'seasons': seasons, 'name': raw.get('name') or '', 'poster': raw.get('poster_path') or '',
            'backdrop': raw.get('backdrop_path') or '', 'network': networks[0] if networks else '',
            'imdb': str(((raw.get('external_ids') or {}).get('imdb_id')) or ''), 'eps': eps}
    ttl = 12 * 3600 if raw else 1800
    soon = [e for e in eps if e['date'] >= datetime.date.today().isoformat()]
    if soon and soon[0]['date'] <= (datetime.date.today() + datetime.timedelta(days=1)).isoformat():
        ttl = 3600                      # out today or tomorrow: look again within the hour
    T._cache_set(key, data, ttl)
    return data


def _movie(tmdb):
    """A film's digital (else physical) release day, art and IMDb id."""
    from . import tmdb_direct as T
    key = 'mv_rel1:%s' % tmdb
    data = T._cache_get(key)
    if data is not None:
        return data
    try:
        raw = T._request('/movie/%s' % tmdb, {'append_to_response': 'release_dates'},
                         timeout=6, max_attempts=1) or {}
    except Exception:
        raw = {}
    days = {}
    for country in (raw.get('release_dates') or {}).get('results') or []:
        for rel in country.get('release_dates') or []:
            kind = int(rel.get('type') or 0)
            day = str(rel.get('release_date') or '')[:10]
            if kind in (_TYPE_DIGITAL, _TYPE_PHYSICAL) and day:
                days[kind] = min(days.get(kind, day), day)
    data = {'name': raw.get('title') or '', 'poster': raw.get('poster_path') or '',
            'backdrop': raw.get('backdrop_path') or '', 'imdb': str(raw.get('imdb_id') or ''),
            'date': days.get(_TYPE_DIGITAL) or days.get(_TYPE_PHYSICAL) or ''}
    T._cache_set(key, data, 12 * 3600 if raw else 1800)
    return data


def _art(path, kind):
    from . import tmdb_direct as T
    return T._image_url(path, kind=kind) if path else ''


def titles():
    """Personal monitoring sources, bounded per pass and rotated every half hour."""
    from . import watch_sources as W
    try:
        shows, names, movies = W.monitoring_titles()
        paused = W.paused_ids()
        shows = [v for v in shows if ('series', v) not in paused]
        movies = [v for v in movies if ('movie', v) not in paused]
        def batch(values, count):
            if len(values) <= count:
                return values
            start = (int(time.time()) // int(_EVERY) * count) % len(values)
            return (values + values)[start:start + count]
        return batch(shows, _MAX_SHOWS * 3), names, batch(movies, _MAX_MOVIES)
    except Exception as exc:
        _log('personal sources unavailable: %s' % exc.__class__.__name__)
        return [], {}, []


def scan():
    """(out, soon): the episodes and films out lately and those coming this
    week, each one dict (kind, key, title, season, episode, sub, day, tmdb,
    imdb, poster, fanart)."""
    today = datetime.date.today()
    shows, names, movies = titles()
    events = []
    for tmdb in shows:
        data = _show(tmdb)
        for ep in data.get('eps') or []:
            if ep['episode'] <= 0:
                continue
            events.append({'kind': 'ep', 'tmdb': tmdb, 'imdb': data.get('imdb') or '',
                           'title': data.get('name') or names.get(tmdb) or '', 'season': ep['season'],
                           'episode': ep['episode'], 'ep_title': ep['title'], 'date': ep['date'],
                           'poster': _art(data.get('poster'), 'poster'),
                           'fanart': _art(data.get('backdrop'), 'backdrop')})
    for tmdb in movies:
        data = _movie(tmdb)
        if data.get('date'):
            events.append({'kind': 'movie', 'tmdb': tmdb, 'imdb': data.get('imdb') or '',
                           'title': data.get('name') or '', 'date': data['date'],
                           'poster': _art(data.get('poster'), 'poster'),
                           'fanart': _art(data.get('backdrop'), 'backdrop')})
    # the calendar's exact air times (and shows TMDb did not know of)
    aired = {}
    for ev in _calendar():
        day, out_now = _local_day(ev)
        if day is None:
            continue
        aired[(str(ev.get('tmdb') or ''), int(ev.get('season') or 0), int(ev.get('episode') or 0))] = (day, out_now, ev.get('aired') or '')
    out, soon, seen = [], [], set()
    for ev in events:
        try:
            day = datetime.date.fromisoformat(ev['date'])
        except ValueError:
            continue
        out_now = day <= today
        if ev['kind'] == 'ep':
            exact = aired.get((ev['tmdb'], ev['season'], ev['episode']))
            if exact:
                day, out_now, ev['aired'] = exact
            ev['key'] = 'ep|%s|%d|%d' % (ev['tmdb'], ev['season'], ev['episode'])
            ev['code'] = 'S%d:E%d' % (ev['season'], ev['episode'])
            name = ev.get('ep_title') or ''
            ev['sub'] = '  ·  '.join(x for x in (ev['code'], name) if x and not name.lower().startswith('episode'))
            back = 1
        else:
            ev['key'] = 'movie|%s' % ev['tmdb']
            ev['sub'] = _tr('نزل رقمياً') if out_now else _tr('ينزل رقمياً')
            back = _MOVIE_BACK
        if ev['key'] in seen:
            continue
        seen.add(ev['key'])
        ev['day'] = day.isoformat()
        if out_now and day >= today - datetime.timedelta(days=back):
            out.append(ev)
        elif not out_now and day <= today + datetime.timedelta(days=_SOON_DAYS):
            soon.append(ev)
    soon.sort(key=lambda e: e['day'])
    return out, soon


# ------------------------------------------------------------------ can it play?
def _video_id(ev):
    imdb = str(ev.get('imdb') or '')
    if not imdb.startswith('tt'):
        return ''
    return '%s:%d:%d' % (imdb, ev['season'], ev['episode']) if ev['kind'] == 'ep' else imdb


def playable(ev, timeout=8):
    """True when one of the source add-ons has a stream for it (each asked
    at once, the way playback asks them); None when nothing can be asked."""
    vid = _video_id(ev)
    if not vid:
        return None
    media = 'series' if ev['kind'] == 'ep' else 'movie'
    try:
        from .dexhub import client, store
        providers = [p for p in store.list_providers() or []
                     if client.supports_resource(p, 'stream', media_type=media, item_id=vid)]
    except Exception:
        return None
    if not providers:
        return None
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def ask(provider):
        try:
            data = client.fetch_streams(provider, media, vid, timeout_override=timeout) or {}
        except Exception:
            return False
        return any((s or {}).get('url') or (s or {}).get('externalUrl') or (s or {}).get('infoHash')
                   for s in data.get('streams') or [])
    with ThreadPoolExecutor(max_workers=min(6, len(providers))) as pool:
        for future in as_completed([pool.submit(ask, p) for p in providers]):
            try:
                if future.result():
                    return True
            except Exception:
                continue
    return False


def _ready(out, state):
    """The titles out that can be played (asked again every two hours)."""
    now = time.time()
    checks = state.setdefault('checks', {})
    ready = []
    asked = 0
    for ev in out:
        c = checks.get(ev['key']) or {}
        if c.get('ok'):
            ready.append(ev)
            continue
        first = float(c.get('first') or now)
        if now - first > _GIVE_UP or now - float(c.get('at') or 0) < _CHECK_AGAIN and c:
            continue
        if asked >= _ASK_PER_PASS:
            continue                    # the rest at the next pass, half an hour later
        asked += 1
        found = playable(ev)
        checks[ev['key']] = {'first': first, 'at': now, 'ok': found is not False}
        if found is not False:
            ready.append(ev)
    return ready


def _started(event):
    """True when this episode (or film) was already played here."""
    try:
        from .dexhub import playback_store
        rows = playback_store.list_recent_items(limit=400, include_watched=True)
    except Exception:
        return False
    tmdb, imdb = str(event.get('tmdb') or ''), str(event.get('imdb') or '').lower()
    from . import episode_titles
    for row in rows:
        if event['kind'] == 'ep':
            if int(row.get('season') or 0) != event['season'] or int(row.get('episode') or 0) != event['episode']:
                continue
            rt, ri = episode_titles.show_ids(row)
            if (tmdb and rt == tmdb) or (imdb and ri == imdb):
                return True
        elif not row.get('season'):
            cid = str(row.get('canonical_id') or '').lower()
            if (imdb and cid.split(':')[0] == imdb) or (tmdb and cid == 'tmdb:%s' % tmdb):
                return True
    return False


# ------------------------------------------------------------------ showing
def _opener(ev, play=False):
    from urllib.parse import urlencode
    canonical = ev.get('imdb') or ('tmdb:%s' % ev['tmdb'] if ev.get('tmdb') else '')
    if play and ev.get('imdb'):
        if ev['kind'] == 'ep':
            q = [('action', 'episode_streams'), ('canonical_id', ev['imdb']), ('video_id', _video_id(ev)),
                 ('season', ev['season']), ('episode', ev['episode']), ('media_type', 'series'),
                 ('title', ev['title']), ('ui_poster', ev.get('poster') or ''), ('ui_fanart', ev.get('fanart') or '')]
        else:
            q = [('action', 'streams'), ('canonical_id', ev['imdb']), ('media_type', 'movie'),
                 ('title', ev['title']), ('ui_poster', ev.get('poster') or ''), ('ui_fanart', ev.get('fanart') or '')]
    else:
        q = [('action', 'skin_open'), ('m', 'series' if ev['kind'] == 'ep' else 'movie'), ('title', ev['title']),
             ('id', canonical), ('imdb', ev.get('imdb') or ''), ('tmdb', ev.get('tmdb') or '')]
    return 'plugin://%s/?' % _addon().getAddonInfo('id') + urlencode([(k, str(v)) for k, v in q])


def _when(day_iso):
    today = datetime.date.today()
    try:
        day = datetime.date.fromisoformat(day_iso)
    except ValueError:
        return ''
    delta = (day - today).days
    if delta <= 0:
        return _tr('اليوم')
    if delta == 1:
        return _tr('بكرة')
    try:
        from .routes.library import _day_name
        return _day_name(day)
    except Exception:
        return day_iso


@_scope.bound
def _publish(ready, soon=()):
    """What the island shows: what can be played now, and this week's."""
    _scope.require_current()
    home = xbmcgui.Window(10000)
    rows = []
    for ev in ready[:20]:
        rows.append({'title': ev['title'], 'code': ev.get('code') or '', 'sub': ev.get('sub') or '',
                     'kind': ev['kind'], 'poster': ev.get('poster') or '', 'fanart': ev.get('fanart') or '',
                     'action': _opener(ev, play=True), 'key': ev['key'],
                     'day': ev.get('day') or '', 'aired': ev.get('aired') or '', 'detected_at': ev.get('detected_at') or 0,
                     'monitor_aliases': [('series' if ev['kind'] == 'ep' else 'movie') + '|tmdb:' + str(ev.get('tmdb') or '')],
                     'monitor_key': ('series' if ev['kind'] == 'ep' else 'movie') + '|' + (ev.get('imdb') or 'tmdb:' + str(ev.get('tmdb') or ''))})
    later = []
    for ev in list(soon)[:15]:
        later.append({'title': ev['title'], 'sub': '  ·  '.join(x for x in (_when(ev['day']), ev.get('sub') or '') if x),
                      'kind': ev['kind'], 'poster': ev.get('poster') or '', 'fanart': ev.get('fanart') or '',
                      'action': _opener(ev), 'key': ev['key'], 'day': ev.get('day') or '', 'aired': ev.get('aired') or ''})
    home.setProperty('dhs.newep.list', json.dumps(rows))
    home.setProperty('dhs.newep.soon', json.dumps(later))
    home.setProperty('dhs.newep.day', datetime.date.today().isoformat())
    first = ready[0] if ready else {}
    home.setProperty('dhs.newep.title', first.get('title') or '')
    home.setProperty('dhs.newep.code', first.get('code') or '')
    home.setProperty('dhs.newep.poster', first.get('poster') or '')
    home.setProperty('dhs.newep.count', str(len(ready)) if ready else '')
    home.setProperty('dhs.newep.open', rows[0]['action'] if rows else '')
    try:
        from . import island
        island.changed()
        island.refresh(force=True)
    except Exception:
        pass


def _notify(events):
    icon = next((ev['poster'] for ev in events if ev.get('poster')), '')
    if len(events) == 1:
        ev = events[0]
        heading = _tr('🔔 حلقة جديدة متوفرة') if ev['kind'] == 'ep' else _tr('🔔 فيلم نزل ومتوفر')
        message = '%s  ·  %s' % (ev['title'], ev.get('code') or ev.get('sub') or '')
    else:
        heading = _tr('🔔 %d جديد متوفر') % len(events)
        names = [ev['title'] for ev in events]
        message = '، '.join(names[:3]) + ('…' if len(names) > 3 else '')
    try:
        xbmcgui.Dialog().notification(heading, message, icon or _tvos_assets('special://home/addons/plugin.video.dexhub/resources/media/icon.png'),
                                      9000, False)
    except Exception as exc:
        _log('notification failed: %s' % exc, xbmc.LOGWARNING)


@_scope.bound
def check(force=False):
    """Announce once what came out and can be played. Returns how many."""
    if not force and not enabled():
        return 0
    out, soon = scan()
    out = [ev for ev in out if not _started(ev)]
    state = _load_state()
    ready = _ready(out, state)
    from . import watch_sources as W
    dismissed = set(W.config().get('read') or [])
    ready = [ev for ev in ready if ev['key'] not in dismissed]
    seen = state.setdefault('seen', {})
    now = time.time()
    for ev in ready:
        ev['detected_at'] = seen.get(ev['key']) or now
    _publish(ready, soon)
    fresh = [ev for ev in ready if ev['key'] not in seen]
    if fresh:
        _notify(fresh)
        now = time.time()
        for ev in fresh:
            seen[ev['key']] = now
        _log('announced %d new title(s)' % len(fresh))
    _save_state(state)
    return len(fresh)


def _load_state():
    data = _load()
    if 'seen' not in data and 'checks' not in data:
        data = {'seen': dict((k, v) for k, v in data.items() if isinstance(v, (int, float))), 'checks': {}}
    return data


def _save_state(state):
    cutoff = time.time() - _KEEP_DAYS * 86400
    seen = dict((k, v) for k, v in (state.get('seen') or {}).items() if float(v or 0) > cutoff)
    checks = dict((k, v) for k, v in (state.get('checks') or {}).items()
                  if float((v or {}).get('at') or 0) > cutoff)
    try:
        _scope.require_current()
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump({'seen': seen, 'checks': checks}, handle)
        os.replace(tmp, path)
    except Exception:
        pass


@_scope.bound
def _panel_ahead(force=False):
    # v5.10.314: the panel's Schedule read ahead (never over a video)
    if xbmc.getCondVisibility('Player.HasVideo'):
        return
    try:
        from .skinui import panel
        panel.refresh_schedule(force=force)
    except Exception as exc:
        _log('panel schedule: %s' % exc, xbmc.LOGDEBUG)


def run(monitor):
    if monitor.waitForAbort(20.0):
        return
    try:
        # v5.10.314: the skin's Arabic faces for the subtitle look (once)
        from .skinui import substyle
        substyle.install_fonts()
    except Exception:
        pass
    if monitor.waitForAbort(_FIRST_WAIT - 20.0):
        return
    while not monitor.abortRequested():
        try:
            home = xbmcgui.Window(10000)
            requested = home.getProperty('dexhub.watch.refresh') == '1'
            home.clearProperty('dexhub.watch.refresh')
            if enabled():
                # never over a playing video: wait for it to end
                while xbmc.getCondVisibility('Player.HasVideo') and not monitor.abortRequested():
                    if monitor.waitForAbort(60):
                        return
                from . import profile_scope
                with profile_scope.bind():
                    check()
            from . import profile_scope
            with profile_scope.bind():
                _panel_ahead(force=requested)
        except Exception as exc:
            _log('check failed: %s' % exc, xbmc.LOGWARNING)
        deadline = time.monotonic() + _EVERY
        while time.monotonic() < deadline:
            if monitor.waitForAbort(min(15.0, max(0.0, deadline - time.monotonic()))):
                return
            if xbmcgui.Window(10000).getProperty('dexhub.watch.refresh') == '1':
                break
