# -*- coding: utf-8 -*-
"""Torrents through the viewer's own Debrid account (v5.10.568).

Three jobs, all through resources/lib/debrid:

1. annotate(): every torrent row an add-on sends (Torrentio, Comet, any
   Stremio add-on that gives a hash) is marked with the linked service that
   plays it: [RD+] / [TB+] when it is ready, [RD] when the service cannot say
   beforehand. A torrent no linked service has ready is left out (unless
   "play once downloaded" is on, or a torrent player such as Elementum is
   installed for it).
2. Sources of their own: the account's own files ("My Debrid"), and TorBox's
   own search (torrents and Usenet) for a TorBox account.
3. resolve(): the picked row becomes a link: the linked services are tried,
   the likeliest first; what worked or failed is remembered (hashcache).
"""
import re
import threading

import xbmc

from .debrid import accounts, files, hashcache, ready, services

CLOUD_ID = '__debridcloud__'
TORBOX_ID = '__torboxsearch__'
COLOR = 'FF10B981'
_HASH = re.compile(r'^[a-f0-9]{40}$|^[a-z2-7]{32}$', re.I)
_WORD = re.compile(r'[a-z0-9]+')
_TORRENT_PLAYERS = ('plugin.video.elementum', 'plugin.video.quasar', 'plugin.video.torrest')


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] Debrid: %s' % msg, level)


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def active():
    try:
        return accounts.any_linked()
    except Exception:
        return False


def _hints(row):
    hints = row.get('behaviorHints')
    return hints if isinstance(hints, dict) else {}


def row_hash(row):
    """The torrent hash of a row that has no playable address of its own, else ''."""
    if not isinstance(row, dict):
        return ''
    url = str(row.get('url') or '').strip().lower()
    if url and not url.startswith('magnet:'):
        return ''
    value = row.get('infoHash') or row.get('infohash') or _hints(row).get('infoHash') or ''
    if not value and url.startswith('magnet:'):
        found = re.search(r'btih:([a-z0-9]+)', url, re.I)
        value = found.group(1) if found else ''
    value = str(value or '').strip().lower()
    return value if _HASH.match(value) else ''


def _torrent_player():
    for addon_id in _TORRENT_PLAYERS:
        try:
            if xbmc.getCondVisibility('System.AddonIsEnabled(%s)' % addon_id):
                return True
        except Exception:
            pass
    return False


def _owned(service_id, info_hash):
    try:
        return bool(services.service(service_id).owned(info_hash))     # the account's list, kept 5 minutes
    except Exception:
        return False


def _sid(request_id):
    text = str(request_id or '')
    return text if text.startswith('tt') else ''


# ================================================================ 1. annotating rows
def annotate(payload, provider=None, request_id=''):
    """A copy of an add-on's answer with its torrent rows marked for the linked service."""
    if not isinstance(payload, dict) or not active():
        return payload
    streams = payload.get('streams')
    if not isinstance(streams, list) or not streams:
        return payload
    if (provider or {}).get('id') in (CLOUD_ID, TORBOX_ID):
        return payload
    hashes = [h for h in (row_hash(r) for r in streams) if h]
    if not hashes:
        return payload
    try:
        chosen = ready.assign(hashes, sid=_sid(request_id))
    except Exception as exc:
        _log('cache check failed: %s' % exc, xbmc.LOGWARNING)
        return payload
    keep_unready = accounts.option('cache_and_play')
    player = None
    out, dropped, junk = [], 0, 0
    for row in streams:
        info_hash = row_hash(row)
        if not info_hash or info_hash not in chosen:
            out.append(row)
            continue
        service_id, state = chosen[info_hash]
        if state == 'junk':                 # no video inside (a fake .exe, an archive): never offered
            junk += 1
            continue
        badge = accounts.BADGES.get(service_id, 'DB')
        if state == 'uncached' and not keep_unready:
            if player is None:
                player = _torrent_player()
            if not player:
                dropped += 1
                continue
            out.append(row)             # left for the torrent player, as before
            continue
        row = dict(row)
        hints = dict(_hints(row))
        hints['dexhubDebrid'] = {'service': service_id, 'state': state}
        if state == 'cached' and _owned(service_id, info_hash):
            hints['dexhubDebrid']['owned'] = True          # shows "In your account"
        row['behaviorHints'] = hints
        mark = '[%s+]' % badge if state == 'cached' else ('[%s ⏳]' % badge if state == 'uncached' else '[%s]' % badge)
        row['name'] = '%s %s' % (mark, str(row.get('name') or '').strip())
        if state == 'cached':
            row['cached'] = True
        out.append(row)
    if dropped:
        _log('%s: %d torrents no linked service has ready were left out' % (
            (provider or {}).get('name') or '?', dropped))
    if junk:
        _log('%s: %d torrents with no video inside (a program, an archive) were left out' % (
            (provider or {}).get('name') or '?', junk))
    copy = dict(payload)
    copy['streams'] = out
    return copy


# ================================================================ 2. sources of their own
def _words(text):
    return _WORD.findall(str(text or '').lower())


def _fits(name, titles, year, season, episode):
    words = _words(name)
    joined = ' '.join(words)
    if not any(t and ' '.join(t) in joined for t in titles):
        return False
    if season is None:
        return not year or str(year) in words or not re.search(r'(?<![0-9])(19|20)\d{2}(?![0-9])', joined)
    found = files.episodes_in(name)
    if found:
        return any((s is None or s == season) and (first <= episode <= last or first == last == 0) for s, first, last in found)
    seasons = re.findall(r'(?<![a-z0-9])(?:s|season ?)(\d{1,2})(?![0-9])', str(name).lower())
    if seasons:
        numbers = [int(x) for x in seasons]
        return season in numbers or (len(numbers) >= 2 and min(numbers) <= season <= max(numbers))
    return True                       # "Show Complete Series"


def _size(value):
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return ''
    return '%.2f GB' % (size / 1073741824.0) if size >= 1073741824 else ('%d MB' % (size / 1048576.0) if size else '')


def _row(name, info_hash, size, service_id, label, url='', extra=None, state='cached'):
    badge = accounts.BADGES.get(service_id, 'DB')
    hints = {'bingeGroup': 'dexhub-debrid-%s' % service_id, 'filename': str(name).rsplit('/', 1)[-1],
             'dexhubDebrid': dict({'service': service_id, 'state': state}, **(extra or {}))}
    if size:
        hints['videoSize'] = int(float(size))
    row = {'name': '[%s+] [COLOR %s]%s[/COLOR]' % (badge, COLOR, label),
           'title': '%s\n%s' % (name, _size(size)) if size else str(name),
           'behaviorHints': hints, 'cached': True}
    if info_hash:
        row['infoHash'] = info_hash
    if url:
        row['url'] = url
    return row


def targets(meta, canonical_id, media_type, ids=None, season=None, episode=None):
    if not active():
        return []
    try:
        from .source_addons import skipped
        off = skipped()
    except Exception:
        off = set()
    try:
        from .search.native_titles import candidates
        titles = candidates(meta or {}, media_type)
    except Exception:
        titles = []
    meta = meta or {}
    titles = [meta.get('name') or meta.get('title') or ''] + list(titles or [])
    year = str(meta.get('year') or meta.get('releaseInfo') or meta.get('release_date') or meta.get('first_air_date') or '')[:4]
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    base = {'_titles': [t for t in titles if t], '_year': year, '_ids': dict(ids or {}),
            '_season': int(season) if episodic else None, '_episode': int(episode) if episodic else None}
    out = []
    if CLOUD_ID not in off:
        out.append((dict(base, id=CLOUD_ID, name=_t('ملفاتي في Debrid', 'My Debrid')), str(canonical_id)))
    if 'torbox' in accounts.linked() and TORBOX_ID not in off and (ids or {}).get('imdb_id'):
        out.append((dict(base, id=TORBOX_ID, name='TorBox'), str(canonical_id)))
    return out


def payload(provider):
    if provider.get('id') == TORBOX_ID:
        return _torbox_payload(provider)
    return _cloud_payload(provider)


def _cloud_payload(provider):
    titles = [w for w in (_words(t) for t in provider.get('_titles') or []) if w]
    season, episode, year = provider.get('_season'), provider.get('_episode'), provider.get('_year')
    rows, lock = [], threading.Lock()

    def scan(service):
        found = []
        try:
            for item in service.library():
                if not item.get('ready'):
                    continue
                name = item.get('name') or ''
                if not _fits(name, titles, year, season, episode):
                    continue
                if service.id == 'premiumize':
                    if item.get('link') and files.is_video(name):
                        found.append(_row(name, '', item.get('size'), service.id, _t('☁ ملفاتي', '☁ My files'),
                                          url=item['link'], extra={'owned': True}))
                elif item.get('hash'):
                    found.append(_row(name, item['hash'], item.get('size'), service.id, _t('☁ ملفاتي', '☁ My files'),
                                      extra={'owned': True}))
        except Exception as exc:
            _log('%s files: %s' % (service.name, exc), xbmc.LOGWARNING)
        with lock:
            rows.extend(found[:20])

    threads = [threading.Thread(target=scan, args=(s,)) for s in services.linked()]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(20)
    return {'streams': rows}


def _torbox_payload(provider):
    box = services.service('torbox')
    ids = provider.get('_ids') or {}
    season, episode = provider.get('_season'), provider.get('_episode')
    rows = []
    for kind in ('torrents', 'usenet'):
        try:
            found = box.search(kind, ids, season, episode)
        except Exception as exc:
            _log('TorBox %s search: %s' % (kind, exc), xbmc.LOGWARNING)
            continue
        for item in found:
            if not item.get('cached'):
                continue
            name = item.get('raw_title') or item.get('title') or ''
            if kind == 'torrents':
                info_hash = str(item.get('hash') or '').lower()
                if _HASH.match(info_hash):
                    rows.append(_row(name, info_hash, item.get('size'), 'torbox', 'TorBox'))
            elif item.get('nzb'):
                row = _row(name, '', item.get('size'), 'torbox', 'TorBox Usenet', url=item['nzb'],
                           extra={'kind': 'usenet', 'nzb': item['nzb']})
                rows.append(row)
    return {'streams': rows[:80]}


# ================================================================ 3. playing
def _ctx_hints(ctx):
    hints = (ctx or {}).get('behaviorHints')
    return hints if isinstance(hints, dict) else {}


def is_row(ctx):
    """A picked row Dex Hub plays through a Debrid account."""
    if not active():
        return False
    mark = _ctx_hints(ctx).get('dexhubDebrid')
    if isinstance(mark, dict) and (mark.get('kind') == 'usenet' or _HASH.match(str((ctx or {}).get('info_hash') or ''))):
        return True
    info_hash = str((ctx or {}).get('info_hash') or '').lower()
    url = str((ctx or {}).get('stream_url') or '').lower()
    torrent_url = (not url or url.startswith('magnet:')
                   or any(url.startswith('plugin://%s' % a) for a in _TORRENT_PLAYERS))
    return bool(_HASH.match(info_hash)) and torrent_url


def resolve(ctx):
    """The file's address through the viewer's service, '' when none could play it."""
    ctx = ctx or {}
    hints = _ctx_hints(ctx)
    mark = hints.get('dexhubDebrid') if isinstance(hints.get('dexhubDebrid'), dict) else {}
    episodic = ctx.get('season') not in (None, '', 0) and ctx.get('episode') not in (None, '', 0)
    want = {'season': ctx.get('season') if episodic else None, 'episode': ctx.get('episode') if episodic else None,
            'title': (ctx.get('show_title') if episodic else ctx.get('title')) or '',
            'year': ctx.get('year') or '', 'file_idx': ctx.get('file_idx'),
            'filename': hints.get('filename') or '', 'name': ctx.get('stream_title') or ''}
    if mark.get('owned'):
        want['file_idx'] = None
    wait = 90 if accounts.option('cache_and_play') else 8
    if mark.get('kind') == 'usenet':
        box = services.service('torbox')
        return box.resolve('', want, wait=max(wait, 20), kind='usenet', link=mark.get('nzb') or '') if box.linked() else ''
    info_hash = str(ctx.get('info_hash') or '').lower()
    for service in ready.order_for(info_hash, first=mark.get('service') or ''):
        try:
            link = service.resolve(info_hash, want, wait=wait)
        except Exception as exc:
            _log('%s: %s' % (service.name, exc), xbmc.LOGWARNING)
            link = ''
        if link:
            hashcache.put(service.id, info_hash, 'cached')
            _log('%s plays %s' % (service.name, info_hash[:8]))
            return link
        hashcache.put(service.id, info_hash, 'failed')
    return ''


def setup():
    from .debrid import signin
    return signin.menu()
