# -*- coding: utf-8 -*-
"""The title page's island (v5.10.313): Up on the page's buttons opens it on
the right, as Nuvio Legacy's Saved panel (salvospainel.c):

  Saved      what you are watching (Continue) and what you keep (your
             favourites and the linked services' watchlists), sorted
             (recent, oldest, A to Z), grouped (progress, type, none) and
             drawn as a list with covers, a poster grid or landscape cards
  Alerts     what came out and can be played (the new episode reminders)
  Schedule   the next episodes of the shows you follow and the films you
             keep, this week and later, with the day, the network and a
             Finale / Premiere badge

skin.dexhub draws it (Custom_1195_DexPanel.xml); the window's choices are
skin strings (DexHub.PanelTab, PanelSort, PanelGroup, PanelStyle) that the
lists' addresses carry.

v5.10.314, speed (the box took 9 s for Saved and 8 s for Schedule): Saved is
read from the Home's own Continue Watching rows (already built, with their
art and progress) and the favourites database, in a few milliseconds.
Schedule is kept three hours and read again in the background by the
service (reminders.run, and the list when it finds it old), so opening the
panel never waits on TMDb or a calendar. The list publishes every tab's
count from the files: no second Python call when the window opens.

Home properties: dhs.panel.n.<tab> (the counts on the tabs),
dhs.panel.kick.<tab> (the line over the title).
"""
import datetime
import threading
import time
from urllib.parse import urlencode

import xbmc

from . import common as C

TABS = ('saved', 'alerts', 'schedule')
_TTL = {'saved': 60.0, 'schedule': 3 * 3600.0}
_BUSY = set()
_BUSY_LOCK = threading.Lock()
_WEEK = 6           # days ahead that are "this week"
_LATER = 60         # days ahead the schedule looks


def _t(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _int(value):
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _cache_path(tab):
    return C.folder('panel') + '/%s.json' % tab


def _open_url(q):
    """A title's page (the related titles' route: the page closes, the
    other one opens). q: the query (a dict), or a row's dhs.q as it is."""
    if isinstance(q, str):
        return (C.BASE + '?' + urlencode([('action', 'skin_open')]) + '&' + q) if q else ''
    q = dict((k, v) for k, v in (q or {}).items() if v not in (None, ''))
    if not q:
        return ''
    return C.BASE + '?' + urlencode([('action', 'skin_open')] + sorted(q.items()))


_SERIES = ('series', 'show', 'tv', 'tvshow', 'anime', 'episode', 'season')
_SERVICES = (('trakt', 'Trakt'), ('simkl', 'Simkl'), ('nuvio', 'Nuvio'), ('mdblist', 'MDBList'),
             ('floppy', 'Floppy'), ('stremio', 'Stremio'))


def _query(media, title, canonical):
    """The title page's query (follow._query, without importing that page)."""
    canonical = str(canonical or '')
    imdb = canonical.split(':', 1)[0] if canonical.startswith('tt') else ''
    tmdb = canonical.split(':')[1] if canonical.lower().startswith('tmdb:') else ''
    q = {'m': 'series' if media == 'series' else 'movie', 'title': title or ''}
    if tmdb:
        q['tmdb'] = tmdb
    if imdb:
        q['imdb'] = imdb
    return q if (tmdb or imdb) else {}


def _continue_rows():
    """The Home's Continue Watching cards, newest first, as the rows hold
    them (rows/<tab>_<id>.json, written by the skin worker)."""
    import os
    folder = C.folder('rows')
    try:
        names = [n for n in os.listdir(folder) if n.endswith('.json')]
    except Exception:
        return None
    found = []
    for name in names:
        path = os.path.join(folder, name)
        data = C.read_row(path) or {}
        key = str(data.get('key') or '')
        if data.get('kind') != 'continue' and not key.startswith('continue'):
            continue
        items = [i for i in data.get('items') or [] if isinstance(i, dict)]
        if items:
            # the one row with every title first (Continue Watching), then
            # the movies and shows rows (their titles are in it already)
            found.append((0 if name.startswith('all_') and key == 'continue' else 1, -float(data.get('t') or 0), items))
    if not found:
        return None
    found.sort(key=lambda f: (f[0], f[1]))
    out = []
    for _rank, _when, items in found:
        out.extend(items)
    return out


def _from_row(item):
    props = item.get('props') or {}
    info = item.get('info') or {}
    art = item.get('art') or {}
    episode = info.get('mediatype') == 'episode' or bool(info.get('tvshowtitle'))
    title = (info.get('tvshowtitle') if episode else '') or item.get('label') or info.get('title') or ''
    line = []
    if episode and (info.get('season') or info.get('episode')):
        line.append(_t('م%s · ح%02d', 'S%s · E%02d') % (info.get('season') or 0, _int(info.get('episode'))))
        name = item.get('label') or ''
        if name and name != title:
            line.append(name)
    elif info.get('year'):
        line.append(str(info.get('year')))
    return {'title': title, 'media': 'series' if episode or props.get('dhs.media') == 'series' else 'movie',
            'line': '  ·  '.join(line), 'left': props.get('dhs.remaining') or '',
            'prog': _int(item.get('progress')), 'poster': art.get('poster') or '',
            'thumb': art.get('landscape') or art.get('fanart') or art.get('thumb') or '',
            'logo': art.get('clearlogo') or '', 'badge': props.get('dhs.badge') or '',
            'group': 'continue', 'added': 0, 'q': props.get('dhs.q') or ''}


# ------------------------------------------------------------------ the data
def _saved():
    out = []
    seen = set()
    resume = 0
    cards = _continue_rows()
    if cards is None:
        # no Home rows yet (another skin, a first start): the slow way
        try:
            from . import rows as RW
            from ..homeui import follow as F
            cards = []
            for entry in F.up_next(RW.app()):
                cards.append({'_entry': entry})
        except Exception as exc:
            C.log('panel: continue failed: %s' % exc, xbmc.LOGWARNING)
            cards = []
    for card in cards:
        if '_entry' in card:
            entry = card['_entry']
            row = {'title': entry['title'], 'media': entry.get('media') or 'movie',
                   'line': entry.get('line') or '', 'left': entry.get('left') or '',
                   'prog': _int(entry.get('prog')), 'poster': entry.get('poster') or '',
                   'thumb': entry.get('thumb') or '', 'logo': entry.get('logo') or '',
                   'badge': entry.get('badge') or '', 'group': 'continue', 'added': 0, 'q': entry.get('open') or {}}
        else:
            row = _from_row(card)
        key = (row.get('title') or '').lower()
        if not key or key in seen or (card.get('props') or {}).get('kind') in ('more', 'folder'):
            continue
        seen.add(key)
        row['order'] = len(out)
        out.append(row)
        resume += 1
    try:
        from .. import favorites_store
        names = dict(_SERVICES)
        for row in favorites_store.list_favorites(limit=0) or []:
            title = row.get('title') or ''
            key = title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            media = 'series' if str(row.get('media_type') or '').lower() in _SERIES else 'movie'
            canonical = row.get('canonical_id') or ''
            poster = row.get('poster') or (
                'https://images.metahub.space/poster/medium/%s/img' % canonical if canonical.startswith('tt') else '')
            sources = sorted(set(str(s).lower() for s in (row.get('sources') or [row.get('source')]) if s) - {'local'})
            year = str(row.get('year') or '')
            kind = _t('مسلسل', 'Series') if media == 'series' else _t('فيلم', 'Movie')
            out.append({'title': title, 'media': media,
                        'line': '  ·  '.join(x for x in (kind, year if year not in ('', '0') else '') if x),
                        'left': '', 'prog': 0, 'poster': poster, 'thumb': row.get('background') or '',
                        'logo': row.get('clearlogo') or '',
                        'badge': names.get(sources[0], sources[0].title()) if sources else 'Dex Hub',
                        'group': 'saved', 'order': len(out), 'added': float(row.get('added_at') or 0),
                        'q': _query(media, title, canonical)})
    except Exception as exc:
        C.log('panel: favourites failed: %s' % exc, xbmc.LOGWARNING)
    return {'rows': out, 'count': len(out), 'resume': resume}


def _air_word(ep):
    kind = str(ep.get('type') or '').lower()
    if kind == 'finale':
        return _t('ختام الموسم', 'Finale')
    if kind == 'mid_season':
        return _t('منتصف الموسم', 'Mid-season')
    if _int(ep.get('episode')) == 1:
        return _t('عرض أول', 'Premiere') if _int(ep.get('season')) > 1 else _t('حلقة أولى', 'Series premiere')
    return ''


def _schedule():
    from .. import reminders as RM
    today = datetime.date.today()
    shows, names, movies = RM.titles()
    shows = shows[:60]
    found = {}

    def one(tmdb, kind):
        try:
            found[(kind, tmdb)] = RM._show(tmdb) if kind == 'ep' else RM._movie(tmdb)
        except Exception:
            found[(kind, tmdb)] = {}

    threads = []
    for kind, ids in (('ep', shows), ('movie', movies[:30])):
        for tmdb in ids:
            th = threading.Thread(target=one, args=(tmdb, kind), name='DexHubPanel', daemon=True)
            th.start()
            threads.append(th)
    deadline = time.monotonic() + 8.0
    for th in threads:
        th.join(max(0.0, deadline - time.monotonic()))
    # the calendar's exact days (Floppy, else Trakt): an episode out tonight
    # in America is tomorrow here
    exact = {}
    for ev in RM._calendar():
        day, _out = RM._local_day(ev)
        if day is not None:
            exact[(str(ev.get('tmdb') or ''), _int(ev.get('season')), _int(ev.get('episode')))] = day
    events = []
    for (kind, tmdb), data in list(found.items()):
        data = data or {}
        if kind == 'ep':
            for ep in data.get('eps') or []:
                try:
                    day = datetime.date.fromisoformat(ep['date'])
                except Exception:
                    continue
                day = exact.get((tmdb, _int(ep.get('season')), _int(ep.get('episode'))), day)
                if day < today or day > today + datetime.timedelta(days=_LATER) or _int(ep.get('episode')) <= 0:
                    continue
                events.append({'kind': 'ep', 'tmdb': tmdb, 'imdb': data.get('imdb') or '',
                               'title': data.get('name') or names.get(tmdb) or '', 'day': day,
                               'season': _int(ep.get('season')), 'episode': _int(ep.get('episode')),
                               'ep_title': ep.get('title') or '', 'network': data.get('network') or '',
                               'badge': _air_word(ep),
                               'poster': RM._art(data.get('poster'), 'poster'),
                               'fanart': RM._art(data.get('backdrop'), 'backdrop')})
        elif data.get('date'):
            try:
                day = datetime.date.fromisoformat(data['date'])
            except Exception:
                continue
            if day < today or day > today + datetime.timedelta(days=_LATER):
                continue
            events.append({'kind': 'movie', 'tmdb': tmdb, 'imdb': data.get('imdb') or '',
                           'title': data.get('name') or '', 'day': day, 'season': 0, 'episode': 0,
                           'ep_title': '', 'network': '', 'badge': _t('رقمي', 'Digital'),
                           'poster': RM._art(data.get('poster'), 'poster'),
                           'fanart': RM._art(data.get('backdrop'), 'backdrop')})
    events.sort(key=lambda e: (e['day'], e['title'].lower(), e['season'], e['episode']))
    rows = []
    for ev in events[:80]:
        rows.append(dict(ev, day=ev['day'].isoformat()))
    eps = sum(1 for e in rows if e['kind'] == 'ep')
    return {'rows': rows, 'count': len(rows), 'eps': eps}


def _alerts():
    import json
    home = C.home()
    rows = []
    try:
        if home.getProperty('dhs.newep.day') == datetime.date.today().isoformat():
            rows = json.loads(home.getProperty('dhs.newep.list') or '[]') or []
    except Exception:
        rows = []
    return {'rows': [r for r in rows if isinstance(r, dict)], 'count': len(rows)}


_MAKERS = {'saved': _saved, 'schedule': _schedule, 'alerts': _alerts}


def _make(tab):
    path = _cache_path(tab)
    with C.file_lock(path, timeout=30.0):
        started = time.monotonic()
        got = _MAKERS[tab]()
        got['t'] = time.time()
        C.write_json(path, got)
        C.log('panel %s: %d rows in %.0f ms' % (tab, got.get('count') or 0, (time.monotonic() - started) * 1000))
        return got


def _later(tab):
    """Read a tab again on a thread of the service (never in a plugin call:
    Kodi waits for a call's threads before it is done)."""
    from . import items as I
    if I.captured() is None:
        return
    with _BUSY_LOCK:
        if tab in _BUSY:
            return
        _BUSY.add(tab)

    def run():
        try:
            got = _make(tab)
            _publish(tab, got)
            C.set_prop('dhs.panel.rev', str(int(time.time())))
        except Exception as exc:
            C.log('panel %s: %s' % (tab, exc), xbmc.LOGWARNING)
        finally:
            with _BUSY_LOCK:
                _BUSY.discard(tab)
    threading.Thread(target=run, name='DexHubPanel', daemon=True).start()


def data(tab, fresh=False, wait=True):
    """A tab's rows: from its file (an old one is shown and read again in
    the background), else read now (wait) or None."""
    if tab == 'alerts':
        return _alerts()
    got = None if fresh else C.read_json(_cache_path(tab))
    if got:
        if time.time() - float(got.get('t') or 0) > _TTL.get(tab, 60.0):
            _later(tab)
        return got
    return _make(tab) if wait else None


def refresh_schedule(force=False):
    """The service, after its reminders check: Schedule read ahead when old."""
    got = C.read_json(_cache_path('schedule'))
    if force or not got or time.time() - float(got.get('t') or 0) > _TTL['schedule'] * 0.8:
        try:
            _publish('schedule', _make('schedule'))
        except Exception as exc:
            C.log('panel schedule: %s' % exc, xbmc.LOGWARNING)


def _kicker(tab, got):
    n = _int(got.get('count'))
    if tab == 'saved':
        line = _t('%d عمل', '%d TITLES') % n
        if _int(got.get('resume')):
            line += '  ·  ' + _t('%d للإكمال', '%d TO RESUME') % _int(got.get('resume'))
        return line
    if tab == 'schedule':
        eps = _int(got.get('eps'))
        if n and eps == n:
            return _t('%d حلقة قادمة', '%d UPCOMING EPISODES') % n
        return _t('%d قادم', '%d UPCOMING') % n if n else _t('لا شيء قادم', 'NOTHING COMING UP')
    return _t('%d جديد', '%d NEW') % n if n else _t('لا جديد اليوم', 'NOTHING NEW TODAY')


def _publish(tab, got):
    C.set_prop('dhs.panel.n.%s' % tab, str(_int(got.get('count'))) if _int(got.get('count')) else '')
    C.set_prop('dhs.panel.kick.%s' % tab, _kicker(tab, got))


def counts(params, handle):
    """Every tab's count from its file (nothing read from the network)."""
    for tab in TABS:
        got = data(tab, wait=False)
        if got is not None:
            _publish(tab, got)
    return None


# ------------------------------------------------------------------ the lists
_GROUP_NAMES = {
    'continue': ('أكمل المشاهدة', 'Continue'), 'saved': ('محفوظ', 'Saved'),
    'movie': ('أفلام', 'Movies'), 'series': ('مسلسلات', 'Series'),
}


def _saved_items(got, sort, group, style):
    rows = list(got.get('rows') or [])
    if sort == 'az':
        rows.sort(key=lambda r: (r.get('title') or '').lower())
    elif sort == 'oldest':
        rows.reverse()
    if group == 'progress':
        order = ('continue', 'saved')
        key = lambda r: r['group']
    elif group == 'type':
        order = ('movie', 'series')
        key = lambda r: r['media']
    else:
        order, key = ('',), (lambda r: '')
    groups = dict((name, [r for r in rows if key(r) == name]) for name in order)
    items = []
    first_group = True
    for name in order:
        part = groups.get(name) or []
        if not part:
            continue
        label = _t(*_GROUP_NAMES[name]) if name else ''
        for index, row in enumerate(part):
            props = {'kind': 'work', 'media': row['media'], 'badge': row.get('badge') or '',
                     'left': row.get('left') or '', 'dhs.target': _open_url(row.get('q')),
                     'logo': row.get('logo') or ''}
            if index == 0 and label:
                props['kick'] = label
                props['kick_n'] = str(len(part))
                if not first_group:
                    props['sep'] = '1'
            line = row.get('line') or ''
            items.append({'label': row['title'], 'label2': line, 'path': '', 'folder': False,
                          'art': {'poster': row.get('poster') or row.get('thumb') or '',
                                  'thumb': row.get('thumb') or row.get('poster') or '',
                                  'landscape': row.get('thumb') or '', 'fanart': row.get('thumb') or ''},
                          'progress': row.get('prog') or 0, 'props': props})
        first_group = False
    return items


def _day_words(day_iso):
    """(the line's day, the day column's short name, the day of the month, when)."""
    try:
        day = datetime.date.fromisoformat(day_iso)
    except Exception:
        return '', '', '', ''
    today = datetime.date.today()
    delta = (day - today).days
    try:
        from ..routes.library import _day_name
        name = _day_name(day)
    except Exception:
        name = day.strftime('%A')
    english = _t('', 'en') == 'en'
    short = name[:3] if english else name
    if delta == 0:
        line = column = _t('اليوم', 'Today')
    elif delta == 1:
        line, column = _t('بكرة', 'Tomorrow'), short
    else:
        line, column = '%s %d' % (short, day.day), short
    when = 'today' if delta == 0 else ('week' if delta <= _WEEK else 'later')
    return line, column.upper() if english else column, str(day.day), when


def _schedule_items(got):
    items = []
    last = ''
    for ev in got.get('rows') or []:
        dayline, weekday, number, when = _day_words(ev.get('day') or '')
        props = {'kind': 'work', 'wday': weekday, 'mday': number, 'when': when,
                 'badge': ev.get('badge') or ''}
        section = 'later' if when == 'later' else 'week'
        if section != last:
            props['kick'] = _t('هذا الأسبوع', 'THIS WEEK') if section == 'week' else _t('لاحقاً', 'LATER')
            if last:
                props['sep'] = '1'
            last = section
        if ev['kind'] == 'ep':
            code = _t('م%d ح%d', 'S%d E%d') % (ev['season'], ev['episode'])
            line = '  ·  '.join(x for x in (dayline, code, ev.get('network') or '') if x)
            props['epname'] = ev.get('ep_title') or ''
            media = 'series'
        else:
            line = '  ·  '.join(x for x in (dayline, _t('فيلم', 'Movie')) if x)
            media = 'movie'
        canonical = ev.get('imdb') or ('tmdb:%s' % ev['tmdb'] if ev.get('tmdb') else '')
        props['dhs.target'] = _open_url({'m': media, 'title': ev.get('title') or '', 'id': canonical,
                                         'imdb': ev.get('imdb') or '', 'tmdb': ev.get('tmdb') or ''})
        items.append({'label': ev.get('title') or '', 'label2': line, 'path': '', 'folder': False,
                      'art': {'thumb': ev.get('fanart') or ev.get('poster') or '', 'poster': ev.get('poster') or '',
                              'landscape': ev.get('fanart') or '', 'fanart': ev.get('fanart') or ''},
                      'props': props})
    return items


def _alert_items(got):
    items = []
    for index, ev in enumerate(got.get('rows') or []):
        action = str(ev.get('action') or '')
        if action.startswith('plugin://') and not action.startswith(C.BASE):
            # the reminder's address names the add-on it was made for
            action = C.BASE + action[action.index('/?') + 1:] if '/?' in action else action
        props = {'kind': 'work', 'when': 'today', 'dhs.target': action,
                 'badge': _t('جديد', 'New') if ev.get('kind') == 'ep' else _t('نزل', 'Out now'),
                 'wday': _t('اليوم', 'TODAY'), 'mday': str(datetime.date.today().day)}
        if index == 0:
            props['kick'] = _t('متوفر الآن', 'READY TO WATCH')
        items.append({'label': ev.get('title') or '', 'label2': ev.get('sub') or ev.get('code') or '',
                      'path': '', 'folder': False,
                      'art': {'thumb': ev.get('fanart') or ev.get('poster') or '', 'poster': ev.get('poster') or '',
                              'landscape': ev.get('fanart') or '', 'fanart': ev.get('fanart') or ''},
                      'props': props})
    return items


def listing(params, handle):
    from . import items as I
    tab = params.get('tab') if params.get('tab') in TABS else 'saved'
    got = data(tab)
    _publish(tab, got)
    for other in TABS:
        if other != tab:
            known = data(other, wait=False)
            if known is not None:
                _publish(other, known)
            else:
                _later(other)
    if tab == 'saved':
        out = _saved_items(got, params.get('sort') or 'recent', params.get('group') or 'progress',
                           params.get('style') or 'list')
    elif tab == 'schedule':
        out = _schedule_items(got)
    else:
        out = _alert_items(got)
    I.directory(handle, out, content='videos')
    return None


def refresh(params, handle):
    """The pencil's long press and the panel's own refresh: read again."""
    for tab in ('saved', 'schedule'):
        try:
            _publish(tab, data(tab, fresh=True))
        except Exception:
            pass
    C.set_prop('dhs.panel.rev', str(int(time.time())))
    return None
