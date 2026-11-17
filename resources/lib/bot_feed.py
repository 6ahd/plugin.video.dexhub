# -*- coding: utf-8 -*-
"""v5.10.573: what DexCloud's Telegram bot shows of this TV (hub 2.18).

The bot asks the TV through the hub's relay (GET api/bot, the hub only: the
account page's own allowlist does not let it through) and the TV answers from
what its Home already holds, with no add-on asked:

  ?what=home      the Home's rows (name, count), and the first titles of
                  Continue Watching and Next episode
  ?what=row&key=  one row's titles (its saved copy, as the Home shows it)
  ?what=history   the last things watched: Trakt's history when Trakt is
                  linked, else Dex Hub's own playback log

Each title: its name, kind, TMDb and IMDb ids, season and episode, year,
progress, the line under it on the Home, and its public pictures (a picture
only this TV can reach is left out). A title of a row carries the row's own
id for it, so "Play" from the bot runs exactly what OK on the Home runs
(Continue Watching resumes where it stopped, with its source): the hub's
notice {kind: 'open', row, id, how: 'play'|'info'} (run()).
"""
import time
from urllib.parse import parse_qsl

import xbmc

PAGE = 'all'
MAX_ROWS = 40
MAX_ITEMS = 60
FIRST = MAX_ITEMS          # Continue Watching and Next episode come whole with the Home
_PRIVATE = ('token', 'api_key', 'apikey', 'x-emby', 'x-plex', 'password')


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] bot feed: %s' % msg, level)


def _public(url):
    """A picture the hub and Telegram can fetch: a public web address."""
    url = str(url or '')
    if url.startswith('image://'):
        from urllib.parse import unquote
        url = unquote(url[len('image://'):].rstrip('/'))
    low = url.lower()
    if not low.startswith(('https://', 'http://')):
        return ''
    host = low.split('://', 1)[1].split('/', 1)[0].rsplit('@', 1)[-1].split(':', 1)[0]
    if host in ('127.0.0.1', 'localhost', '[::1]', '0.0.0.0') or host.startswith(('192.168.', '10.')) or host.endswith('.local'):
        return ''
    if any(word in low for word in _PRIVATE):
        return ''
    return url[:400]


def _int(value):
    try:
        number = int(str(value).strip())
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _ids(item):
    ids = item.get('ids') or {}
    tmdb, imdb = str(ids.get('tmdb') or ''), str(ids.get('imdb') or '')
    if not tmdb or not imdb:
        props = item.get('props') or {}
        q = dict(parse_qsl(props.get('dhs.q') or ''))
        tmdb = tmdb or str(q.get('tmdb') or '')
        imdb = imdb or str(q.get('imdb') or '')
        ident = str(q.get('id') or '')
        if not tmdb and ident.startswith('tmdb:'):
            tmdb = ident[5:]
        if not imdb and ident.startswith('tt'):
            imdb = ident
    return (tmdb if tmdb.isdigit() and len(tmdb) <= 9 else ''), (imdb if imdb.startswith('tt') and len(imdb) <= 12 else '')


def _title(item, row_key):
    """One title of a row, as the bot shows it; None for what is not a title."""
    props = item.get('props') or {}
    if props.get('kind') != 'work':
        return None
    info = item.get('info') or {}
    art = item.get('art') or {}
    media = str(info.get('mediatype') or '')
    kind = 'e' if media == 'episode' else ('s' if media in ('tvshow', 'season') or props.get('dhs.media') == 'series' else 'm')
    tmdb, imdb = _ids(item)
    name = (info.get('tvshowtitle') if kind == 'e' else '') or info.get('title') or item.get('label') or ''
    out = {'id': str(props.get('dhs.id') or '')[:16], 'row': row_key, 'k': kind, 't': str(name)[:120],
           'tmdb': tmdb, 'imdb': imdb, 'y': str(info.get('year') or '')[:4],
           'sub': str(props.get('subtitle') or props.get('dhs.sub') or '')[:90],
           'left': str(props.get('dhs.remaining') or '')[:40],
           'pr': max(0, min(100, int(float(item.get('progress') or 0)))),
           'run': props.get('dhs.click') == 'run',
           'p': _public(art.get('poster') or art.get('thumb')),
           'b': _public(art.get('fanart') or art.get('landscape')),
           'l': _public(art.get('clearlogo')),
           'plot': str(info.get('plot') or '')[:400]}
    if kind == 'e':
        out['s'], out['e'] = _int(info.get('season')), _int(info.get('episode'))
    return out


def _rows():
    """[(row, spec, saved copy)] of the Home, in its order, hidden rows left out."""
    from .skinui import rows as SR
    from .skinui import common as C
    from . import phone_setup
    app = SR.app()
    out = []
    for row, hidden in app.layout.arrange(phone_setup._page_rows(PAGE), PAGE):
        if hidden:
            continue
        try:
            spec = SR.spec_of(row, PAGE)
            data = C.read_row(C.row_file(PAGE, spec['id'])) or {}
        except Exception:
            continue
        out.append((row, spec, data))
        if len(out) >= MAX_ROWS:
            break
    return out


def _titles(row, data, limit):
    items = []
    for item in data.get('items') or []:
        title = _title(item, row.key)
        if title:
            items.append(title)
            if len(items) >= limit:
                break
    return items


def _which(row, data):
    """'continue', 'next' or '' (Continue Watching and Next episode are both progress rows)."""
    key = str(data.get('key') or row.key or '')
    if key == 'nextup' or 'nextup' in str(row.key or ''):
        return 'next'
    if key == 'continue' or row.kind == 'continue':
        return 'continue'
    return ''


def home():
    rows, firsts = [], {'continue': [], 'next': []}
    for row, spec, data in _rows():
        items = _titles(row, data, MAX_ITEMS)
        if not items:
            continue                          # a row of folders (genres, studios) or not loaded yet
        which = _which(row, data)
        if which and not firsts[which]:
            firsts[which] = items[:FIRST]
        rows.append({'key': row.key, 'title': str(row.title or data.get('title') or row.key)[:80],
                     'count': len(items), 'kind': which or 'list',
                     'at': int(data.get('t') or 0)})
    return {'ok': True, 'v': 1, 'rows': rows, 'continue': firsts['continue'], 'next': firsts['next']}


def row(key):
    for row_, spec, data in _rows():
        if row_.key == key:
            return {'ok': True, 'key': key, 'title': str(row_.title or data.get('title') or key)[:80],
                    'items': _titles(row_, data, MAX_ITEMS), 'at': int(data.get('t') or 0)}
    return {'ok': False, 'error': 'row not found'}


def history(limit=40):
    out, seen = [], set()
    try:
        from . import my_feed
        if my_feed.linked():
            for item in my_feed.history(limit):
                kind = 'e' if item.get('k') == 'e' else 'm'
                out.append({'k': kind, 't': str(item.get('title') or '')[:120], 'tmdb': str(item.get('tmdb') or ''),
                            'imdb': str(item.get('imdb') or ''), 's': item.get('sn'), 'e': item.get('en'),
                            'sub': str(item.get('name') or '')[:90], 'at': str(item.get('at') or ''), 'pr': 100})
            if out:
                return {'ok': True, 'source': 'trakt', 'items': out}
    except Exception as exc:
        _log('Trakt history: %s' % exc)
    from . import playback_store
    for item in playback_store.list_recent_items(limit=300):
        media = str(item.get('media_type') or '')
        episode = media in ('tv', 'episode', 'series') and item.get('episode') not in (None, '', 'None')
        key = (item.get('canonical_id'), item.get('season') if episode else None, item.get('episode') if episode else None)
        if key in seen:
            continue
        seen.add(key)
        canonical = str(item.get('canonical_id') or '')
        tmdb = str((item.get('show_tmdb_id') if episode else '') or item.get('tmdb_id') or '')
        if not tmdb and canonical.startswith('tmdb:'):
            tmdb = canonical[5:].split(':', 1)[0]
        imdb = str(item.get('imdb_id') or (canonical if canonical.startswith('tt') else ''))
        name = (item.get('show_title') if episode else '') or item.get('title') or ''
        if not name or name == canonical:
            name = item.get('title') if item.get('title') != canonical else ''
        out.append({'k': 'e' if episode else 'm', 't': str(name or '')[:120],
                    'tmdb': tmdb if tmdb.isdigit() else '', 'imdb': imdb if imdb.startswith('tt') else '',
                    's': _int(item.get('season')) if episode else None, 'e': _int(item.get('episode')) if episode else None,
                    'pr': max(0, min(100, int(float(item.get('percent') or 0)))),
                    'at': int(item.get('updated_at') or 0),
                    'p': _public(item.get('poster')), 'b': _public(item.get('background')), 'l': _public(item.get('clearlogo'))})
        if len(out) >= limit:
            break
    return {'ok': True, 'source': 'dexhub', 'items': out}


def answer(query):
    """The JSON answer of GET api/bot?<query>."""
    q = dict(parse_qsl(str(query or '').lstrip('?')))
    what = q.get('what') or 'home'
    started = time.time()
    try:
        if what == 'home':
            data = home()
        elif what == 'row':
            data = row(str(q.get('key') or '')[:200])
        elif what == 'history':
            data = history()
        else:
            data = {'ok': False, 'error': 'unknown'}
    except Exception as exc:
        import traceback
        _log('%s: %s' % (what, traceback.format_exc()), xbmc.LOGWARNING)
        data = {'ok': False, 'error': str(exc) or exc.__class__.__name__}
    _log('%s in %.2f s' % (what, time.time() - started))
    return data


def _wake():
    if xbmc.getCondVisibility('System.ScreenSaverActive'):
        xbmc.executebuiltin('Action(noop)')


def _page_of(item):
    """The title page's query (as a Home title's dhs.q) of {type, tmdb, title, year}."""
    from urllib.parse import urlencode
    tmdb = str((item or {}).get('tmdb') or '')
    if not tmdb.isdigit() or len(tmdb) > 9:
        return ''
    media = 'series' if item.get('type') in ('series', 'episode') else 'movie'
    q = {'m': media, 'id': 'tmdb:' + tmdb, 'tmdb': tmdb, 'title': str(item.get('title') or '')[:120]}
    year = str(item.get('year') or '')
    if year.isdigit() and len(year) == 4:
        q['year'] = year
    return urlencode(q)


def run(notice):
    """{kind: 'open', row, id, how, item}: a title of a Home row as OK on the
    Home runs it ('play'), or its title page ('info'); a title of no row
    (a search, TMDb's lists, the history) by its TMDb id: its sources
    ('play', as Play on Kodi from a message) or its page ('info')."""
    from .skinui import common as C
    key, ident = str(notice.get('row') or ''), str(notice.get('id') or '')
    how = 'info' if notice.get('how') == 'info' else 'play'
    item = notice.get('item') if isinstance(notice.get('item'), dict) else None
    if key and ident:
        for row_, spec, data in _rows():
            if row_.key != key:
                continue
            for entry in data.get('items') or []:
                props = entry.get('props') or {}
                if str(props.get('dhs.id') or '') != ident:
                    continue
                if how == 'play' and props.get('dhs.click') == 'run':
                    target = props.get('dhs.target') or entry.get('path') or ''
                    path = C.url('skin_go', do='run', u=target) if target else ''
                else:
                    path = C.url('skin_go', do='info', q=props.get('dhs.q') or '', u=entry.get('path') or '')
                if path:
                    _wake()
                    _log('%s from the bot: %s' % (how, entry.get('label') or ident))
                    xbmc.executebuiltin('RunPlugin(%s)' % path)
                    return True
            break
        _log('not on the Home any more: %s %s' % (key, ident))
    if not item:
        return False
    if how == 'info':
        q = _page_of(item)
        if not q:
            return False
        _wake()
        _log('title page from the bot: %s' % (item.get('title') or item.get('tmdb')))
        xbmc.executebuiltin('RunPlugin(%s)' % C.url('skin_go', do='info', q=q, u=''))
        return True
    from .remote_setup import _play
    _play(item)
    return True
