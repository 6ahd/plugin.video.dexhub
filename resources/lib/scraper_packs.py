# -*- coding: utf-8 -*-
"""Kodi scraper packs as a source of their own (v5.10.568).

CocoScrapers, FenomScrapers and OpenScrapers are found by themselves when
installed: nothing to pick or switch on. They find torrents (hashes); a
linked Debrid account plays them (debrid_source marks and resolves the rows,
like every other torrent). Without a Debrid account they are not asked.
The pack's own settings decide which of its providers run.

The way a pack is loaded follows Lucent Video's coco.py (MIT, djdirty60).
"""
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon

PROVIDER_ID = '__scraperpacks__'
WAIT = 25
# name -> (Kodi add-on, Python package)
PACKS = (('CocoScrapers', 'script.module.cocoscrapers', 'cocoscrapers'),
         ('FenomScrapers', 'script.module.fenomscrapers', 'fenomscrapers'),
         ('OpenScrapers', 'script.module.openscrapers', 'openscrapers'))
_BTIH = re.compile(r'btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})')
_LOADED = {}
_LOCK = threading.Lock()


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] scraper packs: %s' % msg, level)


def installed():
    return [(name, aid, pkg) for name, aid, pkg in PACKS
            if xbmc.getCondVisibility('System.HasAddon(%s)' % aid)]


def _add_paths(addon_id, done):
    """Kodi gives an add-on only the modules it asked for; the pack's own needs (requests…) are added too."""
    if addon_id in done or not xbmc.getCondVisibility('System.HasAddon(%s)' % addon_id):
        return
    done.add(addon_id)
    root = xbmcaddon.Addon(addon_id).getAddonInfo('path')
    try:
        manifest = ET.parse(os.path.join(root, 'addon.xml')).getroot()
    except (ET.ParseError, OSError):
        return
    for extension in manifest.iter('extension'):
        if extension.get('point') == 'xbmc.python.module':
            path = os.path.join(root, extension.get('library') or 'lib')
            if path not in sys.path:
                sys.path.append(path)
    for need in manifest.iter('import'):
        if not need.get('addon', '').startswith('xbmc.'):
            _add_paths(need.get('addon'), done)


def _providers(addon_id, package):
    with _LOCK:
        if package in _LOADED:
            return _LOADED[package]
        _add_paths(addon_id, set())
        import importlib
        sources = importlib.import_module(package).sources
        try:
            found = sources(specified_folders=['torrents'])
        except TypeError:                  # an older pack: all its providers
            found = sources()
        _LOADED[package] = list(found or [])
        return _LOADED[package]


def targets(meta, canonical_id, media_type, ids=None, season=None, episode=None):
    try:
        from . import debrid_source
        if not debrid_source.active():
            return []
        from .source_addons import skipped
        if PROVIDER_ID in skipped():
            return []
    except Exception:
        return []
    packs = installed()
    if not packs or not (ids or {}).get('imdb_id'):
        return []
    meta = meta or {}
    try:
        from .search.native_titles import candidates
        titles = [t for t in [meta.get('name') or meta.get('title') or ''] + list(candidates(meta, media_type) or []) if t]
    except Exception:
        titles = [meta.get('name') or meta.get('title') or '']
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    year = str(meta.get('year') or meta.get('releaseInfo') or meta.get('release_date') or meta.get('first_air_date') or '')[:4]
    return [({'id': PROVIDER_ID, 'name': packs[0][0] if len(packs) == 1 else 'Scrapers',
              '_packs': packs, '_titles': titles, '_year': year, '_ids': dict(ids or {}),
              '_premiered': str(meta.get('released') or meta.get('first_air_date') or ''),
              '_season': int(season) if episodic else None, '_episode': int(episode) if episodic else None},
             str(canonical_id))]


def _data(provider):
    titles = provider.get('_titles') or ['']
    ids = provider.get('_ids') or {}
    if provider.get('_season') is None:
        return {'imdb': ids.get('imdb_id') or '', 'title': titles[0], 'aliases': titles[1:], 'year': provider.get('_year') or ''}
    return {'imdb': ids.get('imdb_id') or '', 'tvshowtitle': titles[0], 'title': '', 'aliases': titles[1:],
            'year': provider.get('_year') or '', 'season': str(provider['_season']), 'episode': str(provider['_episode']),
            'premiered': provider.get('_premiered') or '', 'tvdb': str(ids.get('tvdb_id') or '')}


def _hash(row):
    if row.get('hash'):
        return str(row['hash']).lower()
    found = _BTIH.search(str(row.get('url') or ''))
    return found.group(1).lower() if found else ''


def payload(provider):
    data = _data(provider)
    episodic = provider.get('_season') is not None
    found, lock = [], threading.Lock()
    jobs = []
    for name, addon_id, package in provider.get('_packs') or []:
        try:
            entries = _providers(addon_id, package)
        except Exception as exc:          # a broken pack never stops the search
            _log('%s did not start: %s' % (name, type(exc).__name__), xbmc.LOGWARNING)
            continue
        for entry in entries:
            jobs.append((name, entry))

    def run(name, entry):
        label, cls = entry[0], entry[1]
        try:
            scraper = cls()
            if (episodic and not getattr(scraper, 'hasEpisodes', True)) or (not episodic and not getattr(scraper, 'hasMovies', True)):
                return
            rows = scraper.sources(data, []) or []
            with lock:
                found.extend((name, label, r) for r in rows if isinstance(r, dict))
        except Exception as exc:
            _log('%s %s: %s' % (name, label, type(exc).__name__), xbmc.LOGDEBUG)

    threads = [threading.Thread(target=run, args=job) for job in jobs]
    for thread in threads:
        thread.daemon = True
        thread.start()
    end = time.time() + WAIT
    for thread in threads:
        thread.join(max(0.1, end - time.time()))
    with lock:
        rows = list(found)
    streams, seen = [], set()
    for name, label, row in rows:
        info_hash = _hash(row)
        if not info_hash or info_hash in seen:
            continue
        seen.add(info_hash)
        title = str(row.get('name') or '')
        try:
            size = float(row.get('size') or 0) * 1073741824           # packs give gigabytes
        except (TypeError, ValueError):
            size = 0
        hints = {'bingeGroup': 'dexhub-packs', 'filename': title}
        if size:
            hints['videoSize'] = int(size)
        facts = ' · '.join(x for x in ['%.2f GB' % (size / 1073741824.0) if size else '',
                                        ('\U0001F464 %s' % row['seeders']) if row.get('seeders') else '',
                                        str(label or '').upper()] if x)
        streams.append({'name': '%s\n%s' % (name, row.get('quality') or ''), 'title': '%s\n%s' % (title, facts),
                        'infoHash': info_hash, 'behaviorHints': hints})
    _log('%d torrents from %d providers' % (len(streams), len(jobs)))
    return {'streams': streams}
