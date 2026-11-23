# -*- coding: utf-8 -*-
"""A Usenet release checked before it is shown (v5.10.607; the user, 23 Nov 2026: "check the best
10 to 20 releases in the background; the complete ones go up with a check mark, the incomplete
ones go down or are hidden").

A release used to be found incomplete only when it was picked: the engine asked the provider for
its first parts and got "430 no such article" (Mission: Impossible, SPHD, on the Mac), and the
player moved on to another source. Now, while the source search runs, the best releases are
checked the way Usenet Ultimate and UsenetStreamer do it:

  * the release's NZB is read (one download from the indexer, as a play would do);
  * five of its parts are sampled across the video (its first part, its last, three between);
  * the viewer's own provider is asked whether each exists (NNTP STAT: a yes or no, nothing is
    downloaded), over the same TLS as the engine (the server's name hidden when set); a part a
    main provider lacks is asked of the backup providers too.

A release whose sampled parts all exist is "complete"; one with a part no provider has is
"incomplete" (it would stop at that part); one that could not be checked (the NZB refused, the
provider out of reach, no time left) stays as it was. Verdicts are kept on the device: a complete
one for 12 hours, an incomplete one for 3 days.

Costs: one NZB download per checked release on the indexer (indexers count them; a few allow only
some a day), and a few lines on the provider. The number checked is set in the Usenet page
(off by default; 5 to 20 switch it on).
"""
import hashlib
import json
import os
import re
import socket
import threading
import time
import urllib.request

import xbmc
import xbmcvfs

_FILE = 'special://profile/addon_data/plugin.video.dexhub/usenet/health.json'
_LOCK = threading.Lock()
_KEEP = {'ok': 12 * 3600, 'dead': 3 * 24 * 3600}
_AGENT = 'Dex Hub (Kodi)'
SAMPLE = 5
DEFAULT = 0          # v5.10.607: off unless switched on (the server checks better; the user)
CHOICES = (0, 5, 8, 12, 20)


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] Usenet check: %s' % msg, level)


def key(release):
    title = str(release.get('title') or '').lower()
    size = int(release.get('size') or 0) // 10485760
    return hashlib.sha1(('%s|%d' % (title, size)).encode('utf-8')).hexdigest()[:20]


# ------------------------------------------------------------ verdicts kept on the device
def _load():
    try:
        with open(xbmcvfs.translatePath(_FILE), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _store(found):
    if not found:
        return
    with _LOCK:
        data = _load()
        now = time.time()
        data = {k: v for k, v in data.items() if isinstance(v, list) and now - v[1] < _KEEP.get(v[0], 0)}
        for k, verdict in found.items():
            if verdict in _KEEP:
                data[k] = [verdict, now]
        path = xbmcvfs.translatePath(_FILE)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except Exception as exc:
            _log('not kept: %s' % exc, xbmc.LOGWARNING)


def known(releases):
    """{key: 'ok'|'dead'} of the releases checked lately."""
    data, now, out = _load(), time.time(), {}
    for release in releases:
        k = key(release)
        v = data.get(k)
        if isinstance(v, list) and len(v) == 2 and now - v[1] < _KEEP.get(v[0], 0):
            out[k] = v[0]
    return out


# ------------------------------------------------------------ the NZB
_FILE_RE = re.compile(rb'<file\b([^>]*)>(.*?)</file>', re.S)
_SUBJ_RE = re.compile(rb'subject="([^"]*)"')
_SEG_RE = re.compile(rb'<segment\b[^>]*?number="(\d+)"[^>]*>([^<]+)</segment>')


def _segments(raw):
    """[[message id, ...] per file] of the files that make the video (PAR2 left out), in order.
    Read with expressions, not a parser: a 4K remux's NZB is 12 MB and 113,000 parts."""
    files = []
    for head, body in _FILE_RE.findall(raw):
        subject = _SUBJ_RE.search(head)
        subject = (subject.group(1) if subject else b'').decode('utf-8', 'replace').replace('&quot;', '"')
        name = re.search(r'"([^"]+)"', subject)
        name = (name.group(1) if name else subject).lower()
        if '.par2' in name or name.endswith(('.nfo', '.sfv', '.nzb', '.txt', '.jpg', '.png')):
            continue
        parts = sorted((int(n), mid.strip().decode('utf-8', 'replace')) for n, mid in _SEG_RE.findall(body))
        if parts:
            files.append([mid.replace('&lt;', '').replace('&gt;', '') for _n, mid in parts])
    return files


def _sample(files, count=SAMPLE):
    """The first part, the last and the rest spread evenly between, across all the files."""
    flat = [mid for f in files for mid in f]
    if len(flat) <= count:
        return flat
    step = (len(flat) - 1) / float(count - 1)
    picks = sorted({int(round(i * step)) for i in range(count)})
    return [flat[i] for i in picks]


def _nzb(link, seconds):
    request = urllib.request.Request(link, headers={'User-Agent': _AGENT})
    with urllib.request.urlopen(request, timeout=seconds) as answer:
        raw = answer.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('NZB too big')
    return raw


# ------------------------------------------------------------ the provider
class _Session(object):
    """One NNTP connection, signed in, for STAT questions."""

    def __init__(self, provider, seconds):
        from . import usenet_source as U
        secure = provider.get('ssl', True)
        raw = socket.create_connection((provider.get('host'), int(provider.get('port') or (563 if secure else 119))),
                                       seconds)
        if secure:
            self.sock = U._hidden_name(raw, provider.get('host')) if provider.get('hide_name') \
                else U._tls().wrap_socket(raw, server_hostname=provider.get('host'))
        else:
            self.sock = raw
        self.sock.settimeout(seconds)
        self.reader = self.sock.makefile('rb')
        greeting = self._line()
        if not greeting.startswith(('200', '201')):
            raise IOError('greeting: %s' % greeting[:40])
        if provider.get('user'):
            self._send('AUTHINFO USER %s' % provider.get('user'))
            answer = self._line()
            if answer.startswith('381'):
                self._send('AUTHINFO PASS %s' % (provider.get('password') or ''))
                answer = self._line()
            if not answer.startswith('281'):
                raise IOError('sign-in refused')

    def _send(self, text):
        self.sock.sendall((text + '\r\n').encode('utf-8'))

    def _line(self):
        return self.reader.readline().decode('utf-8', 'replace').strip()

    def stat_many(self, mids):
        """[True/False] for each part, all asked at once (one round trip, not one each)."""
        self.sock.sendall(''.join('STAT <%s>\r\n' % m.strip('<>') for m in mids).encode('utf-8'))
        out = []
        for _m in mids:
            answer = self._line()
            if answer.startswith('223'):
                out.append(True)
            elif answer.startswith('430'):
                out.append(False)
            else:
                raise IOError('STAT: %s' % answer[:40])
        return out

    def stat(self, mid):
        """True when the part exists, False when the provider says it has none (430)."""
        self._send('STAT <%s>' % mid.strip('<>'))
        answer = self._line()
        if answer.startswith('223'):
            return True
        if answer.startswith('430'):
            return False
        raise IOError('STAT: %s' % answer[:40])

    def close(self):
        try:
            self._send('QUIT')
            self.sock.close()
        except Exception:
            pass


def _providers():
    from . import usenet_source as U
    rows = [p for p in U.config()['providers'] if not p.get('off')]
    return [p for p in rows if not p.get('backup')] + [p for p in rows if p.get('backup')]


def _check_one(release, deadline):
    """'ok', 'dead' or '' (not known)."""
    left = deadline - time.time()
    if left < 2:
        return ''
    try:
        mids = _sample(_segments(_nzb(release['link'], min(8, left))))
    except Exception as exc:
        _log('%s: NZB not read (%s)' % (str(release.get('title'))[:50], str(exc)[:60]))
        return ''
    if not mids:
        return ''
    missing, reached = list(mids), 0
    providers = _providers()
    for provider in providers:
        left = deadline - time.time()
        if left < 1:
            return ''
        try:
            session = _Session(provider, min(6, left))
        except Exception as exc:
            _log('%s not reached (%s)' % (provider.get('host'), str(exc)[:60]))
            continue
        try:
            have = session.stat_many(missing)
            missing = [mid for mid, ok in zip(missing, have) if not ok]
        except Exception:
            return ''
        finally:
            session.close()
        reached += 1
        if not missing:
            return 'ok'
    # incomplete only when every provider answered and none had the part
    return 'dead' if missing and reached == len(providers) else ''


def check(releases, seconds, count=DEFAULT):
    """{key: 'ok'|'dead'} for the best `count` releases (in the order given), within `seconds`.
    Verdicts kept from before are used as they are."""
    if count <= 0 or not releases or not _providers():
        return {}
    picked, seen = [], set()
    for release in releases:
        k = key(release)
        if k in seen:
            continue
        seen.add(k)
        picked.append(release)
        if len(picked) >= count:
            break
    out = known(picked)
    todo = [r for r in picked if key(r) not in out]
    started = time.time()
    deadline = started + seconds
    found = {}
    # a few at a time: each holds one connection to the provider while it asks
    width = max(1, min(4, int(min(int(p.get('connections') or 20) for p in _providers()) // 5)))
    gate = threading.Semaphore(width)

    def run(release):
        with gate:
            verdict = _check_one(release, deadline)
        if verdict:
            found[key(release)] = verdict

    threads = [threading.Thread(target=run, args=(r,), name='DexHubUsenetCheck') for r in todo]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(max(0.0, deadline - time.time()))
    _store(found)
    out.update(found)
    ok = sum(1 for v in out.values() if v == 'ok')
    dead = sum(1 for v in out.values() if v == 'dead')
    _log('%d of the best %d: %d complete, %d incomplete, %d not known (%.1fs, %d from before)'
         % (len(out), len(picked), ok, dead, len(picked) - ok - dead, time.time() - started, len(picked) - len(todo)))
    return out
