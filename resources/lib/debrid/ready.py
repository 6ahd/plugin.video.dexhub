# -*- coding: utf-8 -*-
"""Which linked service plays each torrent, and whether it is ready.

For every hash, each linked service is asked in this order, stopping at the
first answer: what it said before (hashcache), the account's own torrents,
the service itself (TorBox, Premiumize), then StremThru (Real-Debrid,
AllDebrid; when the viewer allows it). The first service that has a torrent
cached plays it; otherwise the first one that may have it.
"""
import threading

from . import accounts, hashcache, services, stremthru


def _states(service, hashes, sid=''):
    """{hash: 'cached' | 'uncached' | 'failed'} known for one service."""
    out = {}
    for info_hash in hashes:
        state = hashcache.get(service.id, info_hash)
        if state:
            out[info_hash] = state
    rest = [h for h in hashes if h not in out]
    if not rest:
        return out
    try:
        owned = {str(r.get('hash') or '').lower() for r in service.library() if r.get('ready') and r.get('hash')}
    except Exception:
        owned = set()
    for info_hash in rest:
        if info_hash in owned:
            out[info_hash] = 'cached'
    rest = [h for h in rest if h not in out]
    if not rest:
        return out
    found = {}
    try:
        if service.checks:
            found = service.check(rest)
        elif accounts.option('stremthru'):
            found = stremthru.check(service.id, service.token(), rest, sid=sid)
    except Exception as exc:
        accounts.log('%s cache check: %s' % (service.name, exc), True)
        found = {}
    if found:
        hashcache.put_many(service.id, found)
        out.update(found)
    return out


def assign(hashes, sid=''):
    """{hash: (service id, 'cached' | 'uncached' | None)} for every hash a linked service can take."""
    hashes = [str(h).lower() for h in dict.fromkeys(hashes or []) if h]
    linked = services.linked()
    if not hashes or not linked:
        return {}
    answers = {}

    def work(service):
        answers[service.id] = _states(service, hashes, sid)

    threads = [threading.Thread(target=work, args=(s,)) for s in linked]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(25)
    out = {}
    for info_hash in hashes:
        choice = None
        for service in linked:                     # cached somewhere
            if (answers.get(service.id) or {}).get(info_hash) == 'cached':
                choice = (service.id, 'cached')
                break
        if choice is None:
            for service in linked:                 # may be there (no answer)
                if (answers.get(service.id) or {}).get(info_hash) is None:
                    choice = (service.id, None)
                    break
        if choice is None:
            for service in linked:
                if (answers.get(service.id) or {}).get(info_hash) == 'uncached':
                    choice = (service.id, 'uncached')
                    break
        if choice is None:
            for service in linked:              # v5.10.571: ready, but nothing in it plays
                if (answers.get(service.id) or {}).get(info_hash) == 'junk':
                    choice = (service.id, 'junk')
                    break
        if choice:
            out[info_hash] = choice
    return out


def order_for(info_hash, first=''):
    """The services to try for one torrent at play time, the likeliest first."""
    info_hash = str(info_hash or '').lower()
    linked = services.linked()
    rank = {'cached': 0, None: 1, 'uncached': 2, 'failed': 3, 'junk': 4}
    ordered = sorted(linked, key=lambda s: (0 if s.id == first else 1, rank.get(hashcache.get(s.id, info_hash), 1)))
    return [s for s in ordered if hashcache.get(s.id, info_hash) not in ('failed', 'junk')] or ordered[:1]
