# -*- coding: utf-8 -*-
"""Public torrent add-ons, added for the viewer, only the ones that work today (v5.10.570).

Once a Debrid account is linked, Dex Hub tries every well-known torrent
add-on with a real search (a film everyone has) and adds the ones that
answer with torrents, without any key: they only name the torrents (their
hashes); Dex Hub asks the viewer's own service which ones are ready and
plays them through it. Nothing has to be pasted or configured.

Once a week (service.py) the add-ons Dex Hub added are tried again: one that
stopped answering is replaced by another address of it, or removed; one
that has come back is added. An add-on the viewer added is never touched,
and one already in the list (any address on the same host) is left as it is.
"""
import threading
import time
import urllib.parse

from . import accounts, net

# name: its addresses, the likeliest first (all keyless)
CANDIDATES = (
    ('Torrentio', ('https://torrentio.strem.fun/sort=qualitysize|qualityfilter=threed,480p,scr,cam/manifest.json',)),
    ('Comet', ('https://comet.feels.legal/manifest.json', 'https://comet.elfhosted.com/manifest.json')),
    ('Peerflix', ('https://addon.peerflix.mov/manifest.json',)),
    ('MediaFusion', ('https://mediafusion.elfhosted.com/manifest.json',)),
    ('Meteor', ('https://meteorfortheweebs.midnightignite.me/manifest.json',)),
    ('TorrentsDB', ('https://torrentsdb.com/manifest.json',)),
    ('Knightcrawler', ('https://knightcrawler.elfhosted.com/manifest.json',)),
)
PROBE_ID = 'tt0111161'          # The Shawshank Redemption: every torrent index has it
WEEK = 7 * 24 * 3600


def _host(url):
    try:
        return urllib.parse.urlparse(str(url or '')).netloc.lower()
    except Exception:
        return ''


def _providers():
    try:
        from ..dexhub import store
        return list(store.list_providers() or [])
    except Exception:
        return []


def present():
    return {_host(row.get('manifest_url') or row.get('base_url')) for row in _providers()}


def probe(manifest_url, timeout=12):
    """How many torrents the add-on gives for the probe film (0: it does not work now)."""
    base = manifest_url.rsplit('/manifest.json', 1)[0]
    try:
        answer = net.get('%s/stream/movie/%s.json' % (base, PROBE_ID), timeout=timeout)
    except Exception as exc:
        accounts.log('%s does not answer: %s' % (_host(manifest_url), exc))
        return 0
    count = 0
    for row in (answer or {}).get('streams') or []:
        if not isinstance(row, dict):
            continue
        hints = row.get('behaviorHints') if isinstance(row.get('behaviorHints'), dict) else {}
        if row.get('infoHash') or hints.get('infoHash') or str(row.get('url') or '').startswith('magnet:'):
            count += 1
    return count


def _working(urls):
    for url in urls:
        if probe(url):
            return url
    return ''


def _add(name, url):
    from ..dexhub import api
    api.add_provider_from_url(url, name=name)
    added = dict(accounts.option('auto_added') or {})
    added[name] = url
    accounts.set_option('auto_added', added)


def _remove(url):
    from ..dexhub import store
    for row in _providers():
        if row.get('manifest_url') == url:
            store.remove_provider(row.get('id'))


def install(announce=False):
    """Add every candidate that works now and is not in the list yet. The names added."""
    have = present()
    todo = [(name, urls) for name, urls in CANDIDATES if not any(_host(u) in have for u in urls)]
    found = {}

    def check(name, urls):
        found[name] = _working(urls)

    threads = [threading.Thread(target=check, args=job) for job in todo]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(40)
    added, failed = [], []
    for name, _urls in todo:
        url = found.get(name)
        if not url:
            failed.append(name)
            continue
        try:
            _add(name, url)
            added.append(name)
        except Exception as exc:
            accounts.log('%s not added: %s' % (name, exc), True)
            failed.append(name)
    accounts.set_option('auto_checked', int(time.time()))
    accounts.log('sources added: %s; not working now: %s' % (', '.join(added) or '-', ', '.join(failed) or '-'))
    if announce:
        _announce(added)
    return added


def refresh(force=False):
    """The weekly look: the add-ons Dex Hub added that stopped working are replaced or removed,
    and the candidates that work again are added."""
    if not accounts.any_linked() or not accounts.option('auto_sources'):
        return
    if not force and time.time() - float(accounts.option('auto_checked') or 0) < WEEK:
        return
    added = dict(accounts.option('auto_added') or {})
    listed = {row.get('manifest_url') for row in _providers()}
    for name, urls in CANDIDATES:
        url = added.get(name)
        if not url:
            continue
        if url not in listed:              # the viewer removed it: theirs to decide
            added.pop(name, None)
            continue
        if probe(url):
            continue
        replacement = _working([u for u in urls if u != url])
        _remove(url)
        added.pop(name, None)
        accounts.set_option('auto_added', added)
        if replacement:
            try:
                _add(name, replacement)
                added[name] = replacement
            except Exception as exc:
                accounts.log('%s not replaced: %s' % (name, exc), True)
        accounts.log('%s stopped working: %s' % (name, 'replaced' if added.get(name) else 'removed'))
    accounts.set_option('auto_added', added)
    install()


def _announce(added):
    try:
        import xbmcgui
        from .signin import _t
        if added:
            xbmcgui.Dialog().notification('Dex Hub', _t('أضفنا للمصادر: %s', 'Added to the sources: %s') % ', '.join(added),
                                          xbmcgui.NOTIFICATION_INFO, 4000)
        else:
            xbmcgui.Dialog().notification('Dex Hub', _t('المصادر الشغالة مضافة من قبل', 'The working sources are already added'),
                                          xbmcgui.NOTIFICATION_INFO, 3000)
    except Exception:
        pass
