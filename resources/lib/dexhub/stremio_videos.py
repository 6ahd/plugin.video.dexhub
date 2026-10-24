"""Bounded cached Stremio episode ordering for WatchedField encoding."""
import re
import time


def load(cid, fetch, read, write, scope):
    if not re.fullmatch(r'tt[0-9]+', cid):
        return []
    path = scope.ScopedPath('stremio_episode_order_389.json')
    cache = read(path, {}) or {}
    row = cache.get(cid) or {}
    if row.get('videos') and time.time() - float(row.get('at') or 0) < 86400:
        return row['videos']
    body = fetch('https://v3-cinemeta.strem.io/meta/series/%s.json' % cid, method='GET',timeout=10)
    entries = ((body or {}).get('meta') or {}).get('videos') or []
    ordered = []
    for entry in entries:
        try:
            s,e = int(entry['season']),int(entry['episode'])
            if s < 0 or e < 0 or entry.get('id') != '%s:%d:%d' % (cid,s,e):
                continue
            ordered.append((s,e,str(entry.get('released') or ''),entry['id']))
        except (KeyError,TypeError,ValueError):
            continue
    videos = list(dict.fromkeys(v[3] for v in sorted(ordered)))
    if videos:
        scope.require_current()
        cache[cid] = {'at':time.time(),'videos':videos}
        cache = dict(sorted(cache.items(),key=lambda pair:pair[1].get('at',0),reverse=True)[:128])
        write(path, cache)
    return videos
