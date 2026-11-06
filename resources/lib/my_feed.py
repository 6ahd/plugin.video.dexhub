# -*- coding: utf-8 -*-
"""The My page's data (v5.10.468): what you watched, when, and what comes.

Trakt's history (extended, with each title's runtime and genres) is kept in
the profile's homeui/my_history.json for the last DAYS days and read again
only from its newest entry on; the page shows the saved copy at once and
the new entries when they come. Without Trakt the Dex Hub watch log (the
last fifteen days, homeui/watch_week.json) stands in.

Everything is worked out per local day: minutes of series and films, the
episodes and films finished, and minutes per hour of the day.
"""
import datetime
import json
import os
import threading
import time
import uuid

from . import profile_scope as _scope

DAYS = 91
PAGE = 100
MAX_PAGES = 8
FRESH = 15 * 60
STATS_FRESH = 6 * 3600
_LOCK = threading.Lock()


def english():
    try:
        from .i18n import is_english
        return bool(is_english())
    except Exception:
        return False


def _t(ar, en):
    return en if english() else ar


def _log(msg):
    try:
        import xbmc
        xbmc.log('[DexHub] my: %s' % msg, xbmc.LOGINFO)
    except Exception:
        pass


# ------------------------------------------------------------ the saved copy
def _path():
    return _scope.path('homeui/my_history.json')


def _read():
    try:
        with open(_path(), encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%s.tmp' % (path, uuid.uuid4().hex)
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)


def linked():
    try:
        from . import trakt as K
        return bool(K.linked())
    except Exception:
        return False


def _compact(entry):
    kind = entry.get('type')
    body = entry.get('show') if kind == 'episode' else entry.get('movie')
    if kind not in ('episode', 'movie') or not body:
        return None
    runtime = ((entry.get('episode') or {}).get('runtime') if kind == 'episode' else body.get('runtime')) or body.get('runtime') or 0
    ids = body.get('ids') or {}
    return {'id': entry.get('id'), 'at': entry.get('watched_at') or '', 'k': 'e' if kind == 'episode' else 'm',
            'title': body.get('title') or '', 'tmdb': str(ids.get('tmdb') or ''), 'imdb': ids.get('imdb') or '',
            'genres': list(body.get('genres') or [])[:3], 'min': int(runtime or 0),
            'sn': (entry.get('episode') or {}).get('season'), 'en': (entry.get('episode') or {}).get('number'),
            'name': (entry.get('episode') or {}).get('title') or ''}


def stale(data=None):
    data = _read() if data is None else data
    return time.time() - float(data.get('t') or 0) > FRESH


def refresh(force=False):
    """Read Trakt's history from the newest saved entry on (the last DAYS
    days the first time); True when something new came."""
    if not linked():
        return False
    with _LOCK:
        data = _read()
        if not force and not stale(data):
            return False
        from . import trakt as K
        items = dict((str(i['id']), i) for i in data.get('items') or [] if i.get('id'))
        start = datetime.datetime.utcnow() - datetime.timedelta(days=DAYS)
        newest = data.get('newest') or ''
        since = newest if newest and items else start.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        added = 0
        for page in range(1, MAX_PAGES + 1):
            try:
                rows = K._authed('/users/me/history?extended=full&limit=%d&page=%d&start_at=%s' % (PAGE, page, since),
                                 timeout=20) or []
            except Exception as exc:
                _log('history page %d: %s' % (page, exc.__class__.__name__))
                break
            for row in rows if isinstance(rows, list) else []:
                item = _compact(row)
                if item and str(item['id']) not in items:
                    items[str(item['id'])] = item
                    added += 1
            if not isinstance(rows, list) or len(rows) < PAGE:
                break
        oldest = start.strftime('%Y-%m-%dT%H:%M:%S')
        kept = sorted((i for i in items.values() if (i.get('at') or '') >= oldest), key=lambda i: i['at'], reverse=True)
        stats = data.get('stats') or {}
        data = {'t': time.time(), 'items': kept, 'newest': kept[0]['at'] if kept else ''}
        if time.time() - float(stats.get('t') or 0) > STATS_FRESH:
            try:
                got = K._authed('/users/me/stats', timeout=15) or {}
                stats = {'t': time.time(), 'minutes': int(got.get('total_minutes') or 0),
                         'movies': int((got.get('movies') or {}).get('watched') or 0),
                         'episodes': int((got.get('episodes') or {}).get('watched') or 0),
                         'shows': int((got.get('shows') or {}).get('watched') or 0),
                         'started': int((got.get('progress') or {}).get('started') or 0)}
            except Exception as exc:
                _log('stats: %s' % exc.__class__.__name__)
        data['stats'] = stats
        _write(data)
        _log('history: %d new, %d kept' % (added, len(kept)))
        return bool(added)


# ------------------------------------------------------------ per day
def _local(stamp):
    """A Trakt time ('2026-10-31T04:17:00.000Z') in local time."""
    try:
        moment = datetime.datetime.strptime(stamp[:19], '%Y-%m-%dT%H:%M:%S')
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)


def _blank():
    return {'tv': 0, 'film': 0, 'eps': 0, 'films': 0, 'hours': [0] * 24, 'shows': {}, 'genres': {}, 'titles': set()}


def days():
    """{date: bucket} of the saved history (or the watch log without Trakt)."""
    out = {}
    data = _read()
    items = data.get('items') or []
    if items:
        for item in items:
            moment = _local(item.get('at'))
            if moment is None:
                continue
            bucket = out.setdefault(moment.date(), _blank())
            minutes = int(item.get('min') or 0) or (45 if item.get('k') == 'e' else 110)
            if item.get('k') == 'e':
                bucket['tv'] += minutes
                bucket['eps'] += 1
                show = bucket['shows'].setdefault(item.get('title') or '?', [0, item.get('tmdb') or ''])
                show[0] += minutes
            else:
                bucket['film'] += minutes
                bucket['films'] += 1
            bucket['hours'][moment.hour] += minutes
            for genre in item.get('genres') or []:
                bucket['genres'][genre] = bucket['genres'].get(genre, 0) + minutes
            bucket['titles'].add('%s|%s' % (item.get('k'), item.get('tmdb') or item.get('title')))
        return out
    try:
        from . import watch_log
        log = watch_log._read()
    except Exception:
        log = {}
    for key, entry in log.items():
        try:
            day = datetime.date.fromisoformat(key)
        except ValueError:
            continue
        bucket = out.setdefault(day, _blank())
        done = entry.get('done') or []
        bucket['tv'] = int(entry.get('sec') or 0) // 60
        bucket['eps'] = len([d for d in done if d.startswith('ep|')])
        bucket['films'] = len([d for d in done if d.startswith('film|')])
        for title in entry.get('titles') or []:
            bucket['titles'].add(title)
    return out


def all_time():
    return (_read().get('stats') or {})


# ------------------------------------------------------------ the cards
def hours_text(minutes):
    hours = minutes / 60.0
    if hours >= 10:
        return '%d' % round(hours)
    return ('%.1f' % hours).rstrip('0').rstrip('.') if hours else '0'


def step(value, top, steps=20):
    """0..steps: a bar's height for value out of top."""
    if not value or not top:
        return 0
    return max(1, min(steps, int(round(steps * float(value) / top))))


def today(buckets, now=None):
    now = now or datetime.datetime.now()
    bucket = buckets.get(now.date()) or _blank()
    top = max(bucket['hours'] + [1])
    return {'minutes': bucket['tv'] + bucket['film'], 'films': bucket['films'], 'eps': bucket['eps'],
            'bars': [step(m, top) for m in bucket['hours']]}


def _month_days(first):
    nxt = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return (nxt - first).days


def month(buckets, now=None):
    now = (now or datetime.datetime.now()).date()
    first = now.replace(day=1)
    last_first = (first - datetime.timedelta(days=1)).replace(day=1)
    count = _month_days(first)
    tv, film = [], []
    for n in range(count):
        bucket = buckets.get(first + datetime.timedelta(days=n)) or _blank()
        tv.append(bucket['tv'])
        film.append(bucket['film'])
    before = 0
    for n in range(_month_days(last_first)):
        bucket = buckets.get(last_first + datetime.timedelta(days=n))
        if bucket:
            before += bucket['tv'] + bucket['film']
    top = max([t + f for t, f in zip(tv, film)] + [1])
    total = sum(tv) + sum(film)
    # the shows watched most: the last 30 days (a month just begun has none)
    shows = {}
    for back in range(30):
        bucket = buckets.get(now - datetime.timedelta(days=back))
        for name, (minutes, tmdb) in (bucket or _blank())['shows'].items():
            shows.setdefault(name, [0, tmdb])[0] += minutes
    ranked = sorted(shows.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
    return {'minutes': total, 'before': before, 'delta': total - before, 'count': count,
            'all': [step(t + f, top) for t, f in zip(tv, film)], 'tv': [step(t, top) for t in tv],
            'first': first, 'top': [(name, minutes, tmdb) for name, (minutes, tmdb) in ranked]}


def genres(buckets):
    minutes, films, shows, mix = 0, set(), set(), {}
    for bucket in buckets.values():
        minutes += bucket['tv'] + bucket['film']
        for key in bucket['titles']:
            (shows if key.startswith('e|') or key.startswith('series|') else films).add(key)
        for name, value in bucket['genres'].items():
            mix[name] = mix.get(name, 0) + value
    whole = float(sum(mix.values()) or 1)
    ranked = sorted(mix.items(), key=lambda kv: kv[1], reverse=True)
    top = [(name, int(round(100 * value / whole))) for name, value in ranked[:3]]
    if ranked[3:]:
        top.append(('other', max(0, 100 - sum(p for _n, p in top))))
    return {'minutes': minutes, 'films': len(films), 'shows': len(shows), 'top': top}


def heat(buckets, weeks=13, now=None):
    """weeks x 7 levels (0-4), the oldest week first, Monday first; days
    after today are -1."""
    today_ = (now or datetime.datetime.now()).date()
    start = today_ - datetime.timedelta(days=today_.weekday() + 7 * (weeks - 1))
    values = []
    for n in range(weeks * 7):
        day = start + datetime.timedelta(days=n)
        if day > today_:
            values.append(None)
            continue
        bucket = buckets.get(day)
        values.append((bucket['tv'] + bucket['film']) if bucket else 0)
    seen = sorted(v for v in values if v)
    levels = []
    for value in values:
        if value is None:
            levels.append(-1)
        elif not value or not seen:
            levels.append(0)
        else:
            rank = seen.index(value) / float(max(1, len(seen) - 1))
            levels.append(1 + min(3, int(rank * 4)))
    return levels


# ------------------------------------------------------------ the week
def week(now=None):
    """[(date, [event])] of this week (the calendar's first day), from the calendar file."""
    from . import calendar_feed as CF
    today_ = (now or datetime.datetime.now()).date()
    first = CF.week_start(today_)
    by_day = dict((first + datetime.timedelta(days=n), []) for n in range(7))
    for ev in CF.read().get('events') or []:
        try:
            day = datetime.date.fromisoformat(ev.get('day') or '')
        except ValueError:
            continue
        if day in by_day:
            by_day[day].append(ev)
    return sorted(by_day.items())


# ------------------------------------------------------------ v5.10.537
def streak(buckets, now=None):
    """(days in a row up to today or yesterday, the last 7 days as 0-20 steps)."""
    today = (now or datetime.datetime.now()).date()
    seen = lambda day: bool(day in buckets and (buckets[day]['tv'] or buckets[day]['film']))
    day = today if seen(today) else today - datetime.timedelta(days=1)
    count = 0
    while seen(day):
        count += 1
        day -= datetime.timedelta(days=1)
    week = [today - datetime.timedelta(days=n) for n in range(6, -1, -1)]
    minutes = [buckets[d]['tv'] + buckets[d]['film'] if d in buckets else 0 for d in week]
    top = max(minutes) or 1
    return count, [(d, step(m, top)) for d, m in zip(week, minutes)]


def history(limit=18):
    """The last things watched, newest first, one card per title and episode."""
    out, seen = [], set()
    rows = sorted(_read().get('items') or [], key=lambda item: item.get('at') or '', reverse=True)
    for item in rows:
        key = (item.get('tmdb') or item.get('title'), item.get('sn'), item.get('en'))
        if key in seen or not item.get('tmdb'):
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def when(stamp, now=None):
    """'Today', 'Yesterday', a weekday or a date, for a card."""
    moment = _local(stamp)
    if moment is None:
        return ''
    days_ago = ((now or datetime.datetime.now()).date() - moment.date()).days
    if days_ago <= 0:
        return _t('اليوم', 'Today')
    if days_ago == 1:
        return _t('أمس', 'Yesterday')
    if days_ago < 7:
        return _t(('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')[moment.weekday()],
                  moment.strftime('%A'))
    return '%d/%d' % (moment.day, moment.month)


# ------------------------------------------------------------ your lists
def favourites():
    from . import favorites_store
    return favorites_store.list_favorites(limit=0, source='local') or []


def watchlist():
    from . import favorites_store
    return [r for r in favorites_store.list_favorites(limit=0) or [] if set(r.get('sources') or []) - {'local'}]


def watching():
    """(count, [(title, tmdb, kind)]) of the shows on the go: Trakt's count,
    the shows watched last for the posters."""
    data = _read()
    seen, shows = set(), []
    for item in data.get('items') or []:
        if item.get('k') != 'e' or item.get('title') in seen:
            continue
        seen.add(item.get('title'))
        shows.append((item.get('title') or '', item.get('tmdb') or '', 'series'))
    count = int((data.get('stats') or {}).get('started') or 0) or len(shows)
    return count, shows
