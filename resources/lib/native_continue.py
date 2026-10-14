"""Merge server progress into the existing shelf without persisting duplicates."""
import hashlib
import json
import os
import re
import threading
import time


def normalize(api, backend, server, item):
    ids = item.get('ids') or {}
    key = str((item.get('play_content_id') if backend == 'silo' else '') or item.get('rating_key') or '')
    episode = item.get('media_type') in ('episode', 'show', 'series')
    duration = float(item.get('duration_ms') or 0) / 1000
    position = float(item.get('resume_ms' if backend == 'silo' else 'view_offset_ms') or 0) / 1000
    art = (api._silo_art(item) if backend == 'silo' else
           api._plex_item_art(item) if backend == 'plex' else api._emby_art(item, server))
    canonical = ids.get('imdb_id') or ('tmdb:' + str(ids['tmdb_id']) if ids.get('tmdb_id') else '%s:%s:%s' % (backend, server.get('id'), key))
    return dict(ids, media_type='series' if episode else 'movie', canonical_id=canonical,
        video_id=key, title=item.get('raw_title') or item.get('title') or '',
        show_title=item.get('show_title') or '', season=item.get('season'), episode=item.get('index'),
        position=position, duration=duration, percent=min(100, position / duration * 100) if duration else 0,
        provider_name='%s • %s' % (backend.title(), server.get('name') or backend.title()),
        native_backend=backend, native_server_id=str(server.get('id') or ''), native_item_id=key,
        poster=art.get('poster') or '', background=art.get('fanart') or '', clearlogo=art.get('clearlogo') or '',
        plot=item.get('summary') or '', library_name=item.get('library_name') or '',
        updated_at=_activity_epoch(item),
        **_file_facts(item, server))


def _file_facts(item, server):
    """Server, quality, size and on-disk location of the version that plays.

    v5.10.78: Plex and Emby both return `versions` with the file path, size and
    video facts for every item, and normalize() dropped all of it, so a
    Continue Watching row could not say which server, library or folder it
    came from. The first version is the one a plain resume plays.
    """
    versions = item.get('versions') or []
    first = versions[0] if versions and isinstance(versions[0], dict) else {}
    path = str(first.get('file') or '').strip()
    cut = max(path.rfind('/'), path.rfind('\\'))
    folder, name = (path[:cut], path[cut + 1:]) if cut > 0 else ('', path)
    quality = ' • '.join(x for x in (
        str(first.get('resolution') or '').strip(),
        str(first.get('hdr') or '').strip(),
        str(first.get('video_codec') or '').strip().upper()) if x)
    return {
        'native_server_name': str(server.get('name') or ''),
        'native_quality': quality,
        'native_size': str(first.get('size_label') or ''),
        'native_folder': folder,
        'native_file': name,
        'native_versions': len(versions),
    }


def rows(api, local, limit=500):
    clients = {'plex':api.plex_client, 'emby':api.emby_client, 'jellyfin':api.jellyfin_client, 'silo':api.silo_client}
    window = api.xbmcgui.Window(10000)
    # Account fingerprint prevents a warm interpreter returning another user's shelf.
    accounts = {name:client.account() for name,client in clients.items()}
    fingerprint = hashlib.sha256(json.dumps(accounts, sort_keys=True).encode()).hexdigest()
    prop = 'dexhub.native_continue.' + fingerprint
    try:
        cached = json.loads(window.getProperty(prop) or '{}')
    except Exception:
        cached = {}
    if not cached.get('rows'):
        # v5.10.81: after a Kodi restart the Home property is empty; start
        # from the last shelf saved on disk instead of an empty one.
        disk = _disk_load(fingerprint)
        if disk.get('rows') or (disk.get('at') and not cached.get('at')):
            cached = disk
    native = list(cached.get('rows') or [])
    # v5.10.188: a shelf read before is known even when it came back empty
    # (nothing on deck, or a server that refused): it is read again in the
    # background like any other. An empty one was read in the foreground at
    # every open, the row waiting on every server (4.85 s on a box with a
    # Plex server answering 401 and another slow).
    known = bool(cached.get('at'))

    def _refresh(native, background=False):
        jobs = []
        for backend, client in clients.items():
            if not client.is_signed_in():
                continue
            try:
                servers = api._plex_servers_budgeted() if backend == 'plex' else client.servers()
                jobs.extend((backend, server) for server in servers)
            except Exception:
                pass
        def fetch(job):
            backend, server = job
            client = clients[backend]
            timeout_client = api.emby_client if backend == 'silo' else client
            with timeout_client.bounded_timeout(5):
                if backend == 'plex':
                    items = client.on_deck(server, size=limit)
                elif backend == 'silo':
                    # Reuse the native home response; no per-title metadata requests.
                    unique = {}
                    for section in client.home_sections(server):
                        for item in section.get('items') or []:
                            position = float(item.get('resume_ms') or 0)
                            duration = float(item.get('duration_ms') or 0)
                            if position <= 0 or (duration > 0 and position >= duration):
                                continue
                            if item.get('media_type') not in ('movie', 'episode') and not item.get('play_content_id'):
                                continue
                            iid = item.get('play_content_id') or item.get('rating_key')
                            if iid:
                                unique[iid] = item
                    items = list(unique.values())[:limit]
                else:
                    items, _ = client.resume(server, size=limit)
            return [normalize(api, backend, server, item) for item in items if item.get('rating_key')]
        def server_key(backend, server_id):
            return backend, str(server_id or '')
        by_server = {}
        for row in native:
            key = server_key(row.get('native_backend'), row.get('native_server_id'))
            by_server.setdefault(key, []).append(row)
        for job, result in _gather(api, fetch, jobs, background):
            if isinstance(result, list):
                by_server[server_key(job[0], job[1].get('id'))] = result
        before = native
        native = [r for batch in by_server.values() for r in batch]
        window.setProperty(prop, json.dumps({'at':time.time(), 'rows':native}))
        _disk_save(fingerprint, native)
        if background and announce.get('on') and _shelf_key(before) != _shelf_key(native):
            # the rows already handed out lack this: the Home reads them again
            try:
                window.setProperty('dhs.progress.changed', '%.3f' % time.time())
            except Exception:
                pass
        return native

    announce = {'on': True}

    # v5.10.81: never make the listing wait for a server. kodi.log measured
    # the first Continue Watching open at 7.4s: the servers were fetched in
    # the foreground with a 7s timeout. Known rows (from this session or the
    # last one on disk) are shown at once and refreshed in the background;
    # only a shelf that has never been fetched is fetched in the foreground.
    if time.time() - float(cached.get('at') or 0) > 60:
        if native or known:
            if _claim_refresh(window, prop):
                _start_background(lambda: _refresh(list(native), background=True),
                                  window, prop)
        elif _claim_refresh(window, prop):
            # never read: the servers get a moment, then the rows they have
            # not answered for follow when they do (the Home is told)
            done, box = threading.Event(), {}
            announce['on'] = False

            def first():
                try:
                    box['rows'] = _refresh(list(native), background=True)
                finally:
                    done.set()
            _start_background(first, window, prop)
            if not done.wait(_FIRST_WAIT):
                announce['on'] = True
            if done.is_set():
                native = box.get('rows') or native
    local = [dict(r) for r in local]
    for row in local:
        if row.get('native_item_id') and row.get('native_server_id'):
            brand = str(row.get('provider_name') or '').split(' ', 1)[0].lower()
            if brand in clients:
                row.setdefault('native_backend', brand)
    seen = {(r.get('native_server_id'), r.get('native_item_id')) for r in native}
    extra = [r for r in local if not (r.get('native_item_id') and (r.get('native_server_id'),r.get('native_item_id')) in seen)]
    return _order_by_activity(native, extra, local)[:limit]


_FIRST_WAIT = 2.5      # v5.10.188: how long a shelf never read waits for its servers


def _shelf_key(rows):
    """What the Home shows of a shelf: its titles and where each one stands."""
    out = []
    for r in rows or []:
        try:
            position = int(float(r.get('position') or 0) // 30)
        except (TypeError, ValueError):
            position = 0
        out.append((str(r.get('native_server_id') or ''), str(r.get('native_item_id') or ''), position))
    return sorted(out)


_ISO = re.compile(r'(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:?\d\d)?')


def _epoch(value):
    """Seconds since epoch from a number or an ISO 8601 string, else 0."""
    if value in (None, ''):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    match = _ISO.match(str(value).strip())
    if not match:
        return 0.0
    import datetime
    tz = match.group(4) or 'Z'
    tz = '+00:00' if tz == 'Z' else (tz if ':' in tz else tz[:3] + ':' + tz[3:])
    frac = (match.group(3) or '')[:7]
    try:
        return datetime.datetime.fromisoformat(
            '%sT%s%s%s' % (match.group(1), match.group(2), frac, tz)).timestamp()
    except Exception:
        return 0.0


def _activity_epoch(item):
    """When this title was last watched on its own server, if the server says."""
    for key in ('last_viewed_at', 'last_played_date', 'last_played_at',
                'last_watched_at', 'updated_at', 'played_at'):
        stamp = _epoch(item.get(key))
        if stamp > 0:
            return stamp
    return 0.0


def _order_by_activity(native, extra, local):
    """Latest activity first across every source, the order Trakt uses.

    v5.10.81: rows came back grouped by server; each row now carries its own
    last-watched time and the list is sorted newest first. Rows with no time
    keep their relative order after the dated ones (the sort is stable).

    v5.10.84: one resume point per title. When Dex Hub's own record of the
    same item is newer than the server's, the server row takes Dex Hub's
    position too, not only its time; a local movie row that duplicates a
    server row by canonical id is folded into it, so the title appears once,
    still plays directly from its server, and resumes where it was left last.
    """
    by_native, by_canon = {}, {}
    for row in local:
        stamp = _epoch(row.get('updated_at'))
        key = (str(row.get('native_server_id') or ''), str(row.get('native_item_id') or ''))
        if key[1] and stamp >= _epoch((by_native.get(key) or {}).get('updated_at')):
            by_native[key] = row
        canon = str(row.get('canonical_id') or '')
        if (canon and str(row.get('media_type') or '') == 'movie'
                and stamp >= _epoch((by_canon.get(canon) or {}).get('updated_at'))):
            by_canon[canon] = row
    merged, absorbed = [], set()
    for row in native:
        key = (str(row.get('native_server_id') or ''), str(row.get('native_item_id') or ''))
        candidates = [by_native.get(key)]
        is_movie = str(row.get('media_type') or '') == 'movie'
        if is_movie:
            candidates.append(by_canon.get(str(row.get('canonical_id') or '')))
        newest = None
        for cand in candidates:
            if cand is not None and (newest is None or
                                     _epoch(cand.get('updated_at')) > _epoch(newest.get('updated_at'))):
                newest = cand
        own = _epoch(row.get('updated_at'))
        stamp = own
        if newest is not None:
            local_at = _epoch(newest.get('updated_at'))
            if local_at > own:
                stamp = local_at
                if float(newest.get('position') or 0) > 0:
                    row = dict(row, position=newest.get('position'),
                               duration=newest.get('duration') or row.get('duration'),
                               percent=newest.get('percent') or row.get('percent'),
                               resume_source='dexhub')
            if is_movie and newest is by_canon.get(str(row.get('canonical_id') or '')):
                absorbed.add(id(newest))
        merged.append(dict(row, updated_at=stamp) if stamp else row)
    merged.extend(r for r in extra if id(r) not in absorbed)
    merged.sort(key=lambda r: -_epoch(r.get('updated_at')))
    return merged


def _gather(api, fetch, jobs, background):
    """Foreground: the shared lane pool. Background: a private pool.

    The background refresh outlives the invocation that started it, and
    bootstrap shuts the lane pools down when that invocation ends, so it must
    not use them.
    """
    if not background:
        return api.run_parallel(fetch, jobs, workers=3, timeout=7, lane='browse')
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out = []
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='DexHubContinue')
    try:
        futures = {pool.submit(fetch, job): job for job in jobs}
        try:
            for future in as_completed(futures, timeout=20):
                try:
                    out.append((futures[future], future.result()))
                except Exception as exc:
                    out.append((futures[future], exc))
        except Exception:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def _claim_refresh(window, prop):
    """One background refresh at a time; a second open serves the stale rows."""
    flag = prop + '.refreshing'
    now = time.time()
    try:
        started = float(window.getProperty(flag) or 0)
    except Exception:
        started = 0.0
    if now - started < 30:
        return False
    try:
        window.setProperty(flag, '%.3f' % now)
    except Exception:
        pass
    return True


def _start_background(job, window, prop):
    def _run():
        try:
            job()
        except Exception:
            pass
        finally:
            try:
                window.clearProperty(prop + '.refreshing')
            except Exception:
                pass
    try:
        threading.Thread(target=_run, name='DexHubContinueRefresh', daemon=True).start()
    except Exception:
        pass


def _disk_path():
    from .dexhub.common import profile_path
    return os.path.join(profile_path(), 'native_continue.json')


def _disk_load(fingerprint):
    try:
        with open(_disk_path(), 'r', encoding='utf-8') as fh:
            entry = (json.load(fh) or {}).get(fingerprint) or {}
        return entry if isinstance(entry, dict) else {}
    except Exception:
        return {}


def _disk_save(fingerprint, rows):
    """Only the current account's shelf is kept; a sign-out drops the rest."""
    try:
        path = _disk_path()
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({fingerprint: {'at': time.time(), 'rows': rows}}, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def direct_path(api, row):
    # Resolve the preference on click, so a cached shelf follows a toggle
    # immediately without fetching or rebuilding every catalog.
    if row.get('native_backend') not in ('plex','emby','jellyfin','silo') or not row.get('native_item_id'):
        return ''
    key = api.cache_store.put('native_resume', row, ttl_hours=24)
    return api.build_url(action='native_resume', key=key)


def play(api, key):
    row = api.cache_store.get('native_resume', key) or {}
    backend = row.get('native_backend')
    iid = row.get('native_item_id')
    if not iid:
        return api.error(api.tr('تعذر العثور على العنصر'))
    from . import continue_playback
    if not continue_playback.enabled(api):
        return continue_playback.picker(api, row)
    clients = {'plex': api.plex_client, 'silo': api.silo_client,
               'emby': api.emby_client, 'jellyfin': api.jellyfin_client}
    client = clients.get(backend)
    try:
        server = next((s for s in client.servers()
                       if str(s.get('id')) == str(row.get('native_server_id'))), None) if client else None
    except Exception:
        server = None
    if server is None:
        from . import continue_playback
        return continue_playback.picker(api, row)
    from . import playback_choices
    played, result = playback_choices.try_saved(api, row.get('media_type') or 'movie',
        row.get('canonical_id') or '', row.get('video_id') or '', row.get('season'), row.get('episode'),
        resume_seconds=row.get('position') or '0', resume_percent=row.get('percent') or '',
        provider_id='__%s__' % backend, server_id=row.get('native_server_id') or '')
    if played:
        return result
    if backend == 'plex':
        return api.plex_play(row.get('native_server_id'), iid, force_native=True,
                             resume_seconds=playback_choices.native_resume_seconds(row.get('position')))
    if backend == 'silo':
        return api.silo_play(iid, server=server, force_native=True,
                             resume_seconds=row.get('position'))
    if backend in ('emby','jellyfin'):
        return api.emby_play(iid, server=server, backend=backend, force_native=True,
                             resume_seconds=playback_choices.native_resume_seconds(row.get('position')))
    return api.error(api.tr('تعذر العثور على العنصر'))
