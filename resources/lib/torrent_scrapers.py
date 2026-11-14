# -*- coding: utf-8 -*-
"""Dex Hub's own torrent scrapers (v5.10.569).

Seven sites are asked together, straight from this device, with no add-on
and no key: YTS (films), EZTV (series), TheRARBG, TorrentGalaxy (both by
IMDb id), The Pirate Bay (apibay), Knaben (a search over many trackers) and
Zilean (the hashes Debrid Media Manager users shared). Their torrents join
every other source; debrid_source marks the ones the viewer's Debrid
account has ready and plays them through it.

Only asked when a Debrid account is linked (or a torrent player such as
Elementum is installed). Each site can be switched off on the Debrid page.
Written for Dex Hub from the sites' public JSON APIs.
"""
import json
import re
import threading
import time
import urllib.parse

import xbmc

from .debrid import accounts, files, net

PROVIDER_ID = '__dexscrapers__'
NAME = 'Dex Hub Scrapers'
WAIT = 15
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
_HASH = re.compile(r'^[a-f0-9]{40}$')
_WORD = re.compile(r'[a-z0-9]+')
_RES = re.compile(r'(?<![a-z0-9])(2160p|4k|uhd|1080p|720p|576p|480p)(?![a-z0-9])', re.I)
_BAD = re.compile(r'(?<![a-z0-9])(cam|camrip|hdcam|ts|telesync|hdts|tc|telecine|scr|screener|dvdscr)(?![a-z0-9])', re.I)

# id: (label, films, series)
SITES = (
    ('yts', 'YTS', True, False),
    ('eztv', 'EZTV', False, True),
    ('therarbg', 'TheRARBG', True, True),
    ('torrentgalaxy', 'TorrentGalaxy', True, True),
    ('tpb', 'The Pirate Bay', True, True),
    ('knaben', 'Knaben', True, True),
    ('zilean', 'Zilean (DMM)', True, True),
)
URLS = {
    'yts': 'https://yts.mx',
    'eztv': 'https://eztvx.to',
    'therarbg': 'https://therarbg.to',
    'torrentgalaxy': 'https://torrentgalaxy.online',
    'tpb': 'https://apibay.org',
    'knaben': 'https://api.knaben.org',
    'zilean': 'https://zileanfortheweebs.midnightignite.me',
}


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] scrapers: %s' % msg, level)


def disabled():
    value = accounts.option('scrapers_off') or []
    return set(value) if isinstance(value, list) else set()


def set_disabled(ids):
    accounts.set_option('scrapers_off', sorted(set(ids)))


def _get(url, timeout=12):
    return net.get(url, headers={'User-Agent': UA}, timeout=timeout)


# ================================================================ each site
def yts(q):
    if q['episodic'] or not q['imdb']:
        return []
    data = _get('%s/api/v2/list_movies.json?query_term=%s' % (URLS['yts'], q['imdb']))
    out = []
    for movie in ((data or {}).get('data') or {}).get('movies') or []:
        if str(movie.get('imdb_code') or '').lower() != q['imdb']:
            continue
        for t in movie.get('torrents') or []:
            name = '%s.%s.%s.%s.YTS' % (movie.get('title_long') or movie.get('title') or q['title'],
                                        t.get('quality') or '', t.get('type') or '', t.get('video_codec') or '')
            out.append((name.replace(' ', '.'), t.get('hash'), t.get('size_bytes'), t.get('seeds'), True))
    return out


def eztv(q):
    if not q['episodic'] or not q['imdb']:
        return []
    out = []
    for page in (1, 2, 3):
        data = _get('%s/api/get-torrents?imdb_id=%s&limit=100&page=%d' % (URLS['eztv'], q['imdb'][2:], page))
        rows = (data or {}).get('torrents') or []
        for t in rows:
            try:
                season, episode = int(t.get('season') or -1), int(t.get('episode') or -1)
            except ValueError:
                continue
            if season == q['season'] and episode in (q['episode'], 0):
                out.append((t.get('filename') or t.get('title'), t.get('hash'), t.get('size_bytes'), t.get('seeds'), True))
        if len(rows) < 100 or any(int(t.get('season') or 0) < q['season'] for t in rows if str(t.get('season') or '').isdigit()):
            break                         # newest first: older seasons reached
    return out


def _trb(site, q):
    if not q['imdb']:
        return []
    category = 'Movies' if not q['episodic'] else 'TV'
    path = '/get-posts/keywords:%s:order:-a:category:%s:format:json/' % (q['imdb'], category)
    data = _get(URLS[site] + path)
    out = []
    for r in (data or {}).get('results') or []:
        imdb = str(r.get('i') or '')
        imdb = 'tt' + imdb if imdb.isdigit() else imdb.lower()
        if imdb and imdb != q['imdb']:
            continue
        out.append((r.get('n'), r.get('h'), r.get('s'), r.get('se'), bool(imdb)))
    return out


def therarbg(q):
    return _trb('therarbg', q)


def torrentgalaxy(q):
    return _trb('torrentgalaxy', q)


def tpb(q):
    out = []
    for text in q['queries'][:2]:
        rows = _get('%s/q.php?%s' % (URLS['tpb'], urllib.parse.urlencode({'q': text, 'cat': '200'})))
        for r in rows if isinstance(rows, list) else []:
            info_hash = str(r.get('info_hash') or '').lower()
            if info_hash and info_hash != '0' * 40:
                imdb = str(r.get('imdb') or '').lower()
                if imdb and q['imdb'] and imdb != q['imdb']:
                    continue
                out.append((r.get('name'), info_hash, r.get('size'), r.get('seeders'), bool(imdb)))
    return out


def knaben(q):
    out = []
    cats = '3000000' if not q['episodic'] else '2000000'
    for text in q['queries'][:2]:
        data = _get('%s/v2/search?%s' % (URLS['knaben'], urllib.parse.urlencode({'q': text, 's': '200', 'c': cats})))
        for r in (data or {}).get('hits') or []:
            out.append((r.get('title'), r.get('hash'), r.get('bytes'), r.get('seeders'), False))
    return out


def zilean(q):
    params = {'Query': q['title']}
    if q['episodic']:
        params.update(Season=q['season'], Episode=q['episode'])
    elif q['year']:
        params['Year'] = q['year']
    if q['imdb']:
        params['ImdbId'] = q['imdb']
    data = _get('%s/dmm/filtered?%s' % (URLS['zilean'], urllib.parse.urlencode(params)))
    out = []
    for r in data if isinstance(data, list) else []:
        out.append((r.get('raw_title') or r.get('rawTitle'), r.get('info_hash') or r.get('infoHash'),
                    r.get('size'), None, False))
    return out


RUN = {'yts': yts, 'eztv': eztv, 'therarbg': therarbg, 'torrentgalaxy': torrentgalaxy, 'tpb': tpb,
       'knaben': knaben, 'zilean': zilean}


# ================================================================ the source
def _words(text):
    return _WORD.findall(str(text or '').lower())


def active():
    try:
        from . import debrid_source
        return debrid_source.active() or debrid_source._torrent_player()
    except Exception:
        return False


def targets(meta, canonical_id, media_type, ids=None, season=None, episode=None):
    if not active():
        return []
    try:
        from .source_addons import skipped
        if PROVIDER_ID in skipped():
            return []
    except Exception:
        pass
    meta = meta or {}
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    sites = [s for s, _n, films, series in SITES if (series if episodic else films) and s not in disabled()]
    if not sites:
        return []
    titles = [meta.get('name') or meta.get('title') or '']
    try:
        from . import tmdb_direct
        tmdb_id = str((ids or {}).get('tmdb_id') or '')
        if tmdb_id:
            titles = list(tmdb_direct.english_titles_for(tmdb_id, 'tv' if episodic else 'movie') or []) + titles
    except Exception:
        pass
    titles = [t for t in dict.fromkeys(titles) if t and _words(t)]
    year = str(meta.get('year') or meta.get('releaseInfo') or meta.get('release_date') or meta.get('first_air_date') or '')[:4]
    return [({'id': PROVIDER_ID, 'name': NAME, '_sites': sites, '_titles': titles, '_year': year,
              '_imdb': str((ids or {}).get('imdb_id') or '').lower(),
              '_season': int(season) if episodic else None, '_episode': int(episode) if episodic else None},
             str(canonical_id))]


def _query(provider):
    titles = provider.get('_titles') or ['']
    title = ' '.join(_words(titles[0]))
    season, episode, year = provider.get('_season'), provider.get('_episode'), provider.get('_year')
    episodic = season is not None
    if episodic:
        queries = ['%s S%02dE%02d' % (title, season, episode), '%s S%02d' % (title, season)]
    else:
        queries = ['%s %s' % (title, year) if year else title]
    return {'title': title, 'titles': [_words(t) for t in titles], 'year': year, 'imdb': provider.get('_imdb') or '',
            'episodic': episodic, 'season': season, 'episode': episode, 'queries': queries}


def _fits(name, q, by_id):
    """The release is this title (and, for a series, this episode or a pack holding it)."""
    words = _words(name)
    if not words or _BAD.search(str(name)):
        return False
    joined = ' '.join(words)
    if not by_id and not any(t and ' '.join(t) in joined for t in q['titles']):
        return False
    if not q['episodic']:
        years = re.findall(r'(?<![0-9])((?:19|20)\d{2})(?![0-9])', joined)
        return not q['year'] or not years or str(q['year']) in years or by_id
    found = files.episodes_in(name)
    if found:
        return any((s is None or s == q['season']) and first <= q['episode'] <= last for s, first, last in found)
    seasons = [int(x) for x in re.findall(r'(?<![a-z0-9])(?:s|season ?)(\d{1,2})(?![0-9])', str(name).lower())]
    if seasons:
        return q['season'] in seasons or (len(seasons) >= 2 and min(seasons) <= q['season'] <= max(seasons))
    return bool(re.search(r'complete|collection|all seasons', joined))


def _size(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def payload(provider):
    q = _query(provider)
    found, lock = [], threading.Lock()
    took = {}

    def run(site):
        began = time.time()
        try:
            rows = RUN[site](q) or []
        except Exception as exc:
            _log('%s: %s' % (site, exc), xbmc.LOGDEBUG)
            rows = []
        with lock:
            found.extend((site, r) for r in rows)
            took[site] = (len(rows), time.time() - began)

    threads = [threading.Thread(target=run, args=(s,)) for s in provider.get('_sites') or []]
    for thread in threads:
        thread.daemon = True
        thread.start()
    end = time.time() + WAIT
    for thread in threads:
        thread.join(max(0.1, end - time.time()))
    labels = dict((s, n) for s, n, _f, _t in SITES)
    with lock:
        rows = list(found)
    streams, seen = [], {}
    for site, (name, info_hash, size, seeders, by_id) in rows:
        info_hash = str(info_hash or '').lower()
        if not _HASH.match(info_hash) or not name or not _fits(name, q, by_id):
            continue
        if info_hash in seen:
            seen[info_hash]['_sites'].add(labels[site])
            continue
        res = _RES.search(str(name))
        size = _size(size)
        hints = {'bingeGroup': 'dexhub-scrapers', 'filename': str(name)}
        if size:
            hints['videoSize'] = size
        row = {'name': 'Dex Hub\n%s' % (res.group(1).upper().replace('UHD', '2160P').replace('4K', '2160P') if res else ''),
               'title': str(name), 'infoHash': info_hash, 'behaviorHints': hints,
               '_sites': {labels[site]}, '_seeders': seeders}
        seen[info_hash] = row
        streams.append(row)
    for row in streams:
        facts = ['%.2f GB' % (row['behaviorHints']['videoSize'] / 1073741824.0) if row['behaviorHints'].get('videoSize') else '',
                 ('\U0001F464 %s' % row['_seeders']) if row.get('_seeders') else '',
                 '⚙ %s' % ', '.join(sorted(row.pop('_sites')))]
        row.pop('_seeders', None)
        row['title'] = '%s\n%s' % (row['title'], ' · '.join(x for x in facts if x))
    _log('%d torrents (%s)' % (len(streams), ', '.join('%s %d %.1fs' % (s, n, t) for s, (n, t) in sorted(took.items()))))
    return {'streams': streams}


def choose():
    """The Debrid page's list of the built-in sites, ticked ones are asked."""
    import xbmcgui
    from .debrid.signin import _t
    off = disabled()
    labels = ['%s  ·  %s' % (n, _t('أفلام ومسلسلات', 'films and series') if f and s_ else (_t('أفلام', 'films') if f else _t('مسلسلات', 'series')))
              for _s, n, f, s_ in SITES]
    pre = [i for i, (s, _n, _f, _t2) in enumerate(SITES) if s not in off]
    picked = xbmcgui.Dialog().multiselect(_t('مواقع التورنت المدمجة', 'Built-in torrent sites'), labels, preselect=pre)
    if picked is None:
        return False
    set_disabled([s for i, (s, _n, _f, _t2) in enumerate(SITES) if i not in picked])
    return True
