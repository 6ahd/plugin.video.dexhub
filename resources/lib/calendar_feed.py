# -*- coding: utf-8 -*-
"""The calendar page's data (v5.10.453): a month back and four months ahead.

The titles are the ones Dex Hub already follows (the personal sources, Trakt's
"my shows"); TMDb gives the seasons and the art. The air time comes from the
calendars (Floppy, Trakt), else from TVmaze (free, no key). A date alone stays
a date: no time is made up for it.

The list is kept in the profile's homeui/calendar.json, so the page draws at
once and reads again behind it. The bell is the title's monitoring in
watch_sources: the same switch the sources page and the alerts use.
"""
import datetime
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import profile_scope as _scope

TTL = 3 * 3600.0
BACK, AHEAD = 30, 120
MAX_SHOWS, MAX_MOVIES = 60, 40
TRAKT_DAYS = 33
TVMAZE = 'https://api.tvmaze.com/'
_LOCK = threading.Lock()

DAYS_AR = ('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')
DAYS_EN = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
MONTHS_AR = ('يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر',
             'ديسمبر')
MONTHS_EN = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
             'November', 'December')


def english():
    try:
        from .i18n import is_english
        return bool(is_english())
    except Exception:
        return False


def _t(ar, en):
    return en if english() else ar


def _log(msg):
    import xbmc
    xbmc.log('[DexHub] calendar: %s' % msg, xbmc.LOGINFO)


# ---------------------------------------------------------------- dates
def week_first_day():
    """The weekday a week starts on: Sunday in Arabic, Monday in English."""
    return 0 if english() else 6


def week_start(day, first=None):
    first = week_first_day() if first is None else first
    return day - datetime.timedelta(days=(day.weekday() - first) % 7)


def month_cells(day, first=None):
    """The 42 days a month's grid shows."""
    start = week_start(day.replace(day=1), first)
    return [start + datetime.timedelta(days=n) for n in range(42)]


def add_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    return datetime.date(index // 12, index % 12 + 1, 1)


def day_name(day):
    return (DAYS_EN if english() else DAYS_AR)[day.weekday()]


def day_short(day):
    return day_name(day)[:3] if english() else day_name(day)


def month_name(day):
    return (MONTHS_EN if english() else MONTHS_AR)[day.month - 1]


def day_title(day):
    delta = (day - datetime.date.today()).days
    if delta == 0:
        return _t('اليوم', 'Today')
    if delta == 1:
        return _t('بكرة', 'Tomorrow')
    if delta == -1:
        return _t('أمس', 'Yesterday')
    return '%s %d %s' % (day_name(day), day.day, month_name(day))


def local_stamp(value):
    """(day iso, 'HH:MM') here of an airtime with its zone, else ('', '')."""
    try:
        when = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return '', ''
    if when.tzinfo is None or len(str(value)) <= 10:
        return '', ''
    try:
        when = when.astimezone()
    except (ValueError, OverflowError, OSError):
        return '', ''
    return when.strftime('%Y-%m-%d'), when.strftime('%H:%M')


# ---------------------------------------------------------------- the store
def _path():
    return _scope.path('homeui/calendar.json')


def read():
    try:
        with open(_path(), encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    _scope.require_current()
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, path)


def stale(data=None):
    data = read() if data is None else data
    return not data or time.time() - float(data.get('t') or 0) > TTL \
        or data.get('day') != datetime.date.today().isoformat()


# ---------------------------------------------------------------- the titles
def _trakt_rows():
    """Trakt's "my shows" of the next month, with their exact air times."""
    try:
        from . import trakt
        if not (trakt.enabled() and trakt.authorized()):
            return []
        trakt._ensure_auth()
        start = datetime.date.today() - datetime.timedelta(days=1)
        return trakt._request('/calendars/my/shows/%s/%d' % (start.isoformat(), TRAKT_DAYS), auth=True, timeout=12) or []
    except Exception as exc:
        _log('trakt: %s' % exc.__class__.__name__)
        return []


def _exact_times(trakt_rows):
    """{(tmdb, season, episode): airtime}: Trakt's month, then the week's calendar."""
    out = {}
    for row in trakt_rows:
        ep, ids = row.get('episode') or {}, (row.get('show') or {}).get('ids') or {}
        if ids.get('tmdb') and row.get('first_aired'):
            out[(str(ids['tmdb']), int(ep.get('season') or 0), int(ep.get('number') or 0))] = row['first_aired']
    try:
        from .routes import library
        for ev in library._calendar_events() or []:
            if ev.get('tmdb') and ev.get('aired'):
                out[(str(ev['tmdb']), int(ev.get('season') or 0), int(ev.get('episode') or 0))] = ev['aired']
    except Exception as exc:
        _log('calendars: %s' % exc.__class__.__name__)
    return out


def titles(trakt_rows=()):
    """[{media, tmdb, title, aliases}]: what is followed here, each title once."""
    from . import reminders as RM, watch_sources as W
    out, seen = [], set()

    def add(media, tmdb, title, aliases):
        tmdb = str(tmdb or '')
        if not tmdb.isdigit():
            return
        known = next((r for r in out if r['media'] == media and r['tmdb'] == tmdb), None)
        if known is not None:
            known['aliases'] = list(dict.fromkeys(known['aliases'] + list(aliases)))
        elif (media, tmdb) not in seen:
            seen.add((media, tmdb))
            out.append({'media': media, 'tmdb': tmdb, 'title': title or '', 'aliases': list(aliases)})

    rows = []
    for purpose in ('display', 'monitor'):
        try:
            rows += W.selected(purpose, fresh=True)
        except Exception as exc:
            _log('sources: %s' % exc.__class__.__name__)
    for row in rows:
        media = 'series' if row.get('media') == 'series' else 'movie'
        tmdb = row.get('tmdb') or RM._tmdb_of(row.get('imdb'), media, cached_only=True)
        if not tmdb:
            tmdb = RM._tmdb_of(row.get('imdb'), media)
        add(media, tmdb, row.get('title'), W.aliases(row))
    for row in trakt_rows:
        show = row.get('show') or {}
        add('series', (show.get('ids') or {}).get('tmdb'), show.get('title'), ())
    shows = [r for r in out if r['media'] == 'series'][:MAX_SHOWS]
    movies = [r for r in out if r['media'] == 'movie'][:MAX_MOVIES]
    return shows + movies


# ---------------------------------------------------------------- TMDb, TVmaze
def _season(tmdb, season):
    from . import tmdb_direct as T
    key = 'cal_season1:%s:%s' % (tmdb, season)
    data = T._cache_get(key)
    if data is not None:
        return data
    try:
        raw = T._request('/tv/%s/season/%s' % (tmdb, season), {}, timeout=8, max_attempts=1) or {}
    except Exception:
        raw = {}
    eps = []
    for ep in raw.get('episodes') or []:
        if ep.get('air_date') and int(ep.get('episode_number') or 0) > 0:
            eps.append({'season': int(season), 'episode': int(ep['episode_number']), 'date': str(ep['air_date'])[:10],
                        'title': str(ep.get('name') or ''), 'plot': str(ep.get('overview') or ''),
                        'still': str(ep.get('still_path') or ''), 'type': str(ep.get('episode_type') or ''),
                        'runtime': int(ep.get('runtime') or 0)})
    data = {'eps': eps}
    T._cache_set(key, data, 12 * 3600 if raw else 1800)
    return data


def _maze_times(tmdb, imdb):
    """{'season:episode': airstamp} from TVmaze; {} when it does not know the show."""
    from . import tmdb_direct as T
    if not str(imdb or '').startswith('tt'):
        return {}
    key = 'cal_maze1:%s' % tmdb
    data = T._cache_get(key)
    if data is not None:
        return data
    from .homeui import live_sources as LS
    times = {}
    known = True
    try:
        show = LS.http_json('%slookup/shows?imdb=%s' % (TVMAZE, imdb), timeout=8) or {}
        for ep in LS.http_json('%sshows/%s/episodes' % (TVMAZE, int(show['id'])), timeout=8) or []:
            if ep.get('airtime') and ep.get('airstamp') and ep.get('season') and ep.get('number'):
                times['%d:%d' % (ep['season'], ep['number'])] = ep['airstamp']
    except Exception:
        known = False
    T._cache_set(key, times, 24 * 3600 if known else 6 * 3600)
    return times


def _movie(tmdb):
    from . import tmdb_direct as T
    key = 'cal_movie1:%s' % tmdb
    data = T._cache_get(key)
    if data is not None:
        return data
    try:
        raw = T._request('/movie/%s' % tmdb, {'append_to_response': 'release_dates'}, timeout=8, max_attempts=1) or {}
    except Exception:
        raw = {}
    digital = ''
    for country in (raw.get('release_dates') or {}).get('results') or []:
        for rel in country.get('release_dates') or []:
            day = str(rel.get('release_date') or '')[:10]
            if int(rel.get('type') or 0) == 4 and day and (not digital or day < digital):
                digital = day
    data = {'name': raw.get('title') or '', 'poster': raw.get('poster_path') or '',
            'backdrop': raw.get('backdrop_path') or '', 'imdb': str(raw.get('imdb_id') or ''),
            'plot': raw.get('overview') or '', 'digital': digital, 'cinema': str(raw.get('release_date') or '')[:10]}
    T._cache_set(key, data, 12 * 3600 if raw else 1800)
    return data


def _badge(ep):
    kind = str(ep.get('type') or '').lower()
    if kind == 'finale':
        return _t('ختام الموسم', 'Finale')
    if kind == 'mid_season':
        return _t('منتصف الموسم', 'Mid-season')
    if int(ep.get('episode') or 0) == 1:
        return _t('عرض أول', 'Premiere') if int(ep.get('season') or 0) > 1 else _t('حلقة أولى', 'Series premiere')
    return ''


def _show_events(row, exact, lo, hi):
    from . import reminders as RM
    from . import tmdb_direct as T
    tmdb = row['tmdb']
    show = RM._show(tmdb) or {}
    seasons = sorted(set(ep['season'] for ep in show.get('eps') or []))
    eps = []
    for season in seasons:
        eps += _season(tmdb, season).get('eps') or []
    maze = None
    out = []
    poster = RM._art(show.get('poster'), 'poster')
    fanart = RM._art(show.get('backdrop'), 'backdrop')
    for ep in eps:
        ident = (tmdb, ep['season'], ep['episode'])
        airtime = exact.get(ident)
        if not airtime and lo <= ep['date'] <= hi:
            if maze is None:
                maze = _maze_times(tmdb, show.get('imdb'))
            airtime = maze.get('%d:%d' % (ep['season'], ep['episode']))
        day, clock = local_stamp(airtime) if airtime else ('', '')
        day = day or ep['date']
        if not lo <= day <= hi:
            continue
        still = T._image_url(ep['still'], kind='backdrop') if ep.get('still') else fanart
        out.append({'kind': 'ep', 'key': 'ep|%s|%d|%d' % ident, 'tmdb': tmdb, 'imdb': show.get('imdb') or '',
                    'title': show.get('name') or row['title'], 'season': ep['season'], 'episode': ep['episode'],
                    'code': 'S%d:E%d' % (ep['season'], ep['episode']), 'sub': ep.get('title') or '',
                    'plot': ep.get('plot') or '', 'day': day, 'time': clock, 'network': show.get('network') or '',
                    'badge': _badge(ep), 'runtime': ep.get('runtime') or 0, 'still': still or '', 'poster': poster,
                    'fanart': fanart, 'aliases': row['aliases']})
    return out


def _movie_events(row, lo, hi):
    from . import reminders as RM
    data = _movie(row['tmdb']) or {}
    day = data.get('digital') or data.get('cinema') or ''
    if not day or not lo <= day <= hi:
        return []
    fanart = RM._art(data.get('backdrop'), 'backdrop')
    sub = _t('إصدار رقمي', 'Digital release') if data.get('digital') else _t('في السينما', 'In cinemas')
    return [{'kind': 'movie', 'key': 'movie|%s' % row['tmdb'], 'tmdb': row['tmdb'], 'imdb': data.get('imdb') or '',
             'title': data.get('name') or row['title'], 'season': 0, 'episode': 0, 'code': '', 'sub': sub,
             'plot': data.get('plot') or '', 'day': day, 'time': '', 'network': '', 'badge': sub, 'runtime': 0,
             'still': fanart, 'poster': RM._art(data.get('poster'), 'poster'), 'fanart': fanart,
             'aliases': row['aliases']}]


def sort_key(ev):
    return ev.get('day') or '', ev.get('time') or '99:99', (ev.get('title') or '').lower(), ev.get('season') or 0, \
        ev.get('episode') or 0


@_scope.bound
def refresh(force=False):
    """Read the list again when it is old. Returns the data; never raises."""
    if not _LOCK.acquire(False):
        return read()
    try:
        old = read()
        if not force and not stale(old):
            return old
        today = datetime.date.today()
        lo = (today - datetime.timedelta(days=BACK)).isoformat()
        hi = (today + datetime.timedelta(days=AHEAD)).isoformat()
        trakt_rows = _trakt_rows()
        exact = _exact_times(trakt_rows)
        rows = titles(trakt_rows)

        def one(row):
            try:
                return _show_events(row, exact, lo, hi) if row['media'] == 'series' else _movie_events(row, lo, hi)
            except Exception as exc:
                _log('%s %s: %s' % (row['media'], row['tmdb'], exc.__class__.__name__))
                return None
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix='DexHubCalendar') as pool:
            parts = list(pool.map(_scope.capture(one), rows))
        failed = sum(1 for part in parts if part is None)
        events, seen = [], set()
        for part in parts:
            for ev in part or []:
                if ev['key'] not in seen:
                    seen.add(ev['key'])
                    events.append(ev)
        if failed and old.get('events') and not events:
            return old      # no network: what was read last stays
        events.sort(key=sort_key)
        data = {'t': time.time(), 'day': today.isoformat(), 'titles': len(rows), 'events': events}
        _write(data)
        return data
    except Exception as exc:
        _log('refresh: %s' % exc.__class__.__name__)
        return read()
    finally:
        _LOCK.release()


# ---------------------------------------------------------------- what the page asks
def filtered(events, kind='all'):
    return [ev for ev in events if kind == 'all' or ev.get('kind') == kind]


def by_day(events):
    out = {}
    for ev in events:
        out.setdefault(ev.get('day') or '', []).append(ev)
    return out


def watched_keys():
    """The keys of the episodes and films already played here."""
    out = set()
    try:
        from .dexhub import playback_store
        from . import episode_titles
        for row in playback_store.list_recent_items(limit=400, include_watched=True):
            tmdb = episode_titles.show_ids(row)[0]
            if tmdb and row.get('season'):
                out.add('ep|%s|%d|%d' % (tmdb, int(row.get('season') or 0), int(row.get('episode') or 0)))
            elif tmdb:
                out.add('movie|%s' % tmdb)
    except Exception as exc:
        _log('watched: %s' % exc.__class__.__name__)
    return out


def bell_on(ev, paused=None):
    """On when the title is monitored; None when no source holds it (no switch)."""
    aliases = ev.get('aliases') or []
    if not aliases:
        return None
    if paused is None:
        from . import watch_sources as W
        paused = W.config().get('paused') or []
    return not set(aliases).intersection(paused)


@_scope.bound
def toggle_bell(ev):
    """Switch a title's monitoring. The new state, or None when it has no switch."""
    from . import watch_sources as W
    aliases = ev.get('aliases') or []
    if not aliases:
        return None
    cfg = W.config()
    if set(aliases).intersection(cfg['paused']):
        cfg['paused'] = [key for key in cfg['paused'] if key not in aliases]
        state = True
    else:
        cfg['paused'] = list(dict.fromkeys(cfg['paused'] + aliases))
        state = False
    W._write('watch_sources.json', cfg)
    W._changed()
    return state
