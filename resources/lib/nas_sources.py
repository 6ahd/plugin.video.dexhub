# -*- coding: utf-8 -*-
"""NAS and network folders as sources (v5.10.543).

The viewer points Dex Hub at a NAS once: it is found on the network, a
guest sign-in is tried before any password is asked, and the shared folders
are ticked. Nothing else: films and episodes are told apart by their names
(S01E02, 1x02, a "Season 2" folder), so no folder needs a type.

The files are read into a small index (name, year, season, episode, size,
quality, disc) and every title's source search looks it up by name and year
or by an IMDb id in the path. A match is a normal source row whose address
is the file itself (smb://, nfs:// or a local path), which Kodi plays on its
own. Blu-ray discs (an .iso, or a folder holding BDMV) are rows too, marked
ISO, which CoreELEC plays with their menus.
"""
import json
import os
import re
import socket
import threading
import time
import unicodedata
from urllib.parse import quote

import xbmc
import xbmcgui
import xbmcvfs

PROVIDER_ID = '__nas__'
COLOR = 'FF2DD4BF'
RESCAN_HOURS = 12

_VIDEO = ('.mkv', '.mp4', '.m4v', '.avi', '.mov', '.ts', '.m2ts', '.mts', '.wmv', '.webm',
          '.mpg', '.mpeg', '.vob', '.iso')
_SKIP_DIRS = re.compile(r'^(?:\.|@|#recycle|\$recycle|system volume information|lost\+found|'
                        r'extras?|featurettes?|samples?|behind the scenes|deleted scenes|trailers?|'
                        r'subs?|subtitles?|metadata|\.?thumbnails?|@eadir)', re.I)
_SKIP_FILES = re.compile(r'(?:^|[ ._-])(?:sample|trailer|featurette)(?:[ ._-]|$)', re.I)
_SE = (re.compile(r'(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})(?!\d)', re.I),
       re.compile(r'(?<![a-z0-9])(\d{1,2})x(\d{2,3})(?!\d)', re.I))
_EP_ONLY = re.compile(r'(?:(?<![a-z])(?:e|ep|episode)[ ._-]*|الحلقة\s*)(\d{1,3})(?!\d)', re.I)
_SEASON_DIR = re.compile(r'^(?:season|series|staffel|saison|temporada|s|الموسم)[ ._-]*(\d{1,2})\b', re.I)
_YEAR = re.compile(r'(?<!\d)((?:19|20)\d{2})(?!\d)')
_TAGS = re.compile(r'(?<![a-z0-9])(?:2160p|1080p|720p|576p|480p|4k|uhd|blu[ ._-]?ray|bdrip|brrip|web[ ._-]?dl|'
                   r'webrip|hdtv|dvdrip|remux|x26[45]|h[ ._]?26[45]|hevc|avc|hdr10?|dv|dovi|proper|repack|'
                   r'extended|unrated|imax|remastered|complete|multi|dual|bd(?:25|50|66|100))(?![a-z0-9])', re.I)
_IMDB = re.compile(r'(tt\d{7,9})')
_TMDB = re.compile(r'tmdb(?:id)?[ =:_-]*(\d{2,8})', re.I)
_DISC_NAME = re.compile(r'(?<![a-z0-9])(?:iso|bdmv|br[ ._-]?disk|full[ ._-]?disc|bd(?:25|50|66|100)|'
                        r'complete[ ._-]?(?:uhd[ ._-]?)?blu[ ._-]?ray)(?![a-z0-9])', re.I)
_LOCK = threading.Lock()


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] NAS: %s' % msg, level)


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


# ── Storage ──────────────────────────────────────────────────────────────

def _path(name):
    folder = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/')
    if not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name)


def _read(name, default):
    try:
        with open(_path(name), 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return default


def _write(name, data):
    path = _path(name)
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(path + '.tmp', path)


def shares():
    return list((_read('nas.json', {}) or {}).get('shares') or [])


def _save_shares(rows):
    data = dict(_read('nas.json', {}) or {})       # v5.10.550: keeps 'ignored' with the shares
    data['shares'] = rows
    _write('nas.json', data)


def _ignored():
    return set((_read('nas.json', {}) or {}).get('ignored') or [])


def _ignore(share_id):
    data = dict(_read('nas.json', {}) or {})
    data['ignored'] = sorted(set(data.get('ignored') or []) | {share_id})
    _write('nas.json', data)


def _index():
    return _read('nas_index.json', {}) or {}


def is_set_up():
    return bool(shares())


# ── Addresses ────────────────────────────────────────────────────────────

def _with_login(root, user, password):
    """smb://host/share/ with the sign-in written into it, as Kodi reads it."""
    if not user or '://' not in root:
        return root
    scheme, rest = root.split('://', 1)
    login = quote(user, safe='')
    if password:
        login += ':' + quote(password, safe='')
    return '%s://%s@%s' % (scheme, login, rest)


def _join(base, name):
    return base if not name else (base.rstrip('/') + '/' + name)


def _share_url(share, relative=''):
    root = _with_login(share.get('root') or '', share.get('user') or '', share.get('password') or '')
    if not root.endswith('/'):
        root += '/'
    return root + relative.lstrip('/') if relative else root


def _listdir(path):
    try:
        dirs, files = xbmcvfs.listdir(path if path.endswith('/') or '://' not in path else path + '/')
        return list(dirs or []), list(files or [])
    except Exception:
        return None


# ── Finding a NAS ────────────────────────────────────────────────────────

def _local_ip():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('10.255.255.255', 1))
        return probe.getsockname()[0]
    except Exception:
        return ''
    finally:
        probe.close()


def discover(seconds=4.0):
    """Machines on this network that share files (SMB port 445, NFS 2049):
    [(address, name, scheme)]."""
    mine = _local_ip()
    if not mine or mine.startswith('127.'):
        return []
    base = mine.rsplit('.', 1)[0]
    found, lock = {}, threading.Lock()

    def probe(host):
        for port, scheme in ((445, 'smb'), (2049, 'nfs')):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.6)
            try:
                if sock.connect_ex((host, port)) == 0:
                    with lock:
                        found.setdefault(host, scheme)
                    return
            except Exception:
                pass
            finally:
                sock.close()

    hosts = ['%s.%d' % (base, n) for n in range(1, 255) if '%s.%d' % (base, n) != mine]
    workers = []
    for chunk in range(0, len(hosts), 32):
        for host in hosts[chunk:chunk + 32]:
            worker = threading.Thread(target=probe, args=(host,), daemon=True)
            worker.start()
            workers.append(worker)
    deadline = time.time() + seconds
    for worker in workers:
        worker.join(max(0.0, deadline - time.time()))
    names = {}

    def name_of(host):
        try:
            names[host] = socket.gethostbyaddr(host)[0].split('.')[0]
        except Exception:
            pass

    lookups = [threading.Thread(target=name_of, args=(h,), daemon=True) for h in found]
    for worker in lookups:
        worker.start()
    for worker in lookups:
        worker.join(0.8)
    return sorted(((h, names.get(h) or '', s) for h, s in found.items()),
                  key=lambda row: tuple(int(x) for x in row[0].split('.')))


# ── Reading names ────────────────────────────────────────────────────────

def _norm(text):
    text = unicodedata.normalize('NFKD', str(text or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    text = (text.replace('&', ' and ').replace('ـ', '').replace('أ', 'ا').replace('إ', 'ا')
            .replace('آ', 'ا').replace('ة', 'ه').replace('ى', 'ي'))
    text = re.sub(r"['’`]", '', text)
    text = re.sub(r'[^\w]+', ' ', text, flags=re.U)
    return ' '.join(w for w in text.split() if w not in ('the', 'a', 'an', 'and'))


def _clean(base):
    """A release name up to its year or first technical word, as words."""
    text = re.sub(r'\[[^\]]*\]|\{[^}]*\}', ' ', base)
    text = re.sub(r'[._]+', ' ', text)
    cut = len(text)
    for found in (_YEAR.search(text), _TAGS.search(text)):
        if found and found.start() > 0:
            cut = min(cut, found.start())
    text = text[:cut]
    return re.sub(r'\s+', ' ', re.sub(r'[()\-–]+\s*$', '', text)).strip(' -–()')


def _year(text):
    years = [int(y) for y in _YEAR.findall(re.sub(r'(?:2160|1080|720|480)p', ' ', text, flags=re.I))
             if 1920 <= int(y) <= 2100]
    return years[-1] if years else None


def _quality(text):
    low = text.lower()
    if re.search(r'2160p|(?<![a-z0-9])(?:4k|uhd)(?![a-z0-9])', low):
        return '2160P'
    if '1080' in low:
        return '1080P'
    if '720' in low:
        return '720P'
    return ''


def parse(parents, filename):
    """What a file is, from its name and the folders above it."""
    base, ext = os.path.splitext(filename)
    if ext.lower() not in _VIDEO and filename.lower() != 'index.bdmv':
        return None
    folders = [p for p in parents if p and p.upper() not in ('BDMV', 'STREAM')]
    whole = ' '.join(folders[-3:] + [base])
    row = {'q': _quality(whole), 'disc': bool(ext.lower() == '.iso' or filename.lower() == 'index.bdmv'
                                             or _DISC_NAME.search(base))}
    found = _IMDB.search(whole)
    if found:
        row['imdb'] = found.group(1)
    found = _TMDB.search(whole)
    if found:
        row['tmdb'] = found.group(1)
    season = episode = None
    head = base
    for pattern in _SE:
        hit = pattern.search(base)
        if hit:
            season, episode, head = int(hit.group(1)), int(hit.group(2)), base[:hit.start()]
            break
    season_dir = None
    for folder in reversed(folders):
        hit = _SEASON_DIR.match(folder.strip())
        if hit:
            season_dir = int(hit.group(1))
            break
    if season is None and season_dir is not None:
        hit = _EP_ONLY.search(base)
        if hit:
            season, episode, head = season_dir, int(hit.group(1)), base[:hit.start()]
    if season is not None:
        show = _clean(head)
        if len(_norm(show)) < 2:
            show = next((_clean(f) for f in reversed(folders) if not _SEASON_DIR.match(f.strip())), '')
        row.update({'t': _norm(show), 'name': show, 'se': season, 'ep': episode})
        return row if row['t'] else None
    title = _clean(base)
    year = _year(base)
    if filename.lower() == 'index.bdmv' or len(_norm(title)) < 2 or _norm(title) in ('movie', 'video', 'film', 'main'):
        folder = folders[-1] if folders else ''
        title, year = _clean(folder), _year(folder) or year
    if year is None and folders:
        year = _year(folders[-1])
    row.update({'t': _norm(title), 'name': title, 'y': year})
    return row if row['t'] else None


# ── Scanning ─────────────────────────────────────────────────────────────

def _stat(path):
    """(size, modified time) of a file; v5.10.550 keeps the time for Recently added."""
    try:
        stat = xbmcvfs.Stat(path)
        return int(stat.st_size()), int(stat.st_mtime())
    except Exception:
        return 0, 0


def _walk(share, relative, parents, depth, out, stop):
    if depth > 7 or stop():
        return
    listed = _listdir(_share_url(share, relative))
    if listed is None:
        return
    dirs, files = listed
    # a Blu-ray folder: the disc as one row, its insides not walked
    if any(d.upper() == 'BDMV' for d in dirs):
        bdmv = next(d for d in dirs if d.upper() == 'BDMV')
        rel = _join(relative, bdmv) + '/index.bdmv'
        row = parse(parents, 'index.bdmv')
        if row:
            row.update({'p': rel, 's': 0, 'm': _stat(_share_url(share, rel))[1],
                        'f': (parents[-1] if parents else bdmv), 'sh': share['id']})
            out.append(row)
        return
    for name in files:
        if _SKIP_FILES.search(name):
            continue
        row = parse(parents, name)
        if not row:
            continue
        rel = _join(relative, name)
        size, modified = _stat(_share_url(share, rel))
        if size and size < 40 * 1024 * 1024 and not row.get('disc'):
            continue
        row.update({'p': rel, 's': size, 'm': modified, 'f': name, 'sh': share['id']})
        out.append(row)
    for name in dirs:
        if not _SKIP_DIRS.match(name):
            _walk(share, _join(relative, name), parents + [name], depth + 1, out, stop)


def scan(progress=None, monitor=None):
    """Reads every share again; returns (films, episodes)."""
    if not _LOCK.acquire(False):
        return None
    try:
        rows, started = [], time.time()
        stop = lambda: bool((monitor and monitor.abortRequested()) or (progress and progress.iscanceled()))
        for number, share in enumerate(shares()):
            if progress:
                progress.update(int(100 * number / max(1, len(shares()))),
                                _t('قراءة %s…', 'Reading %s…') % share.get('name', ''))
            found = []
            _walk(share, '', [], 0, found, stop)
            share['count'] = len(found)
            rows.extend(found)
        if stop() and not rows:
            return None
        _write('nas_index.json', {'at': time.time(), 'files': rows})
        threading.Thread(target=warm_catalog, name='DexHub-nas-match', daemon=True).start()
        films = sum(1 for r in rows if r.get('se') is None)
        _log('index: %d films, %d episodes, %d discs in %.1fs' % (
            films, len(rows) - films, sum(1 for r in rows if r.get('disc')), time.time() - started))
        return films, len(rows) - films
    finally:
        _LOCK.release()


def rescan_due():
    return is_set_up() and time.time() - float(_index().get('at') or 0) > RESCAN_HOURS * 3600


def service_loop(monitor):
    """Reads the shares again twice a day while Kodi runs."""
    if monitor.waitForAbort(90):
        return
    while not monitor.abortRequested():
        try:
            if import_kodi_sources() or rescan_due():
                scan(monitor=monitor)
        except Exception as exc:
            _log('scan failed: %s' % exc, xbmc.LOGWARNING)
        if monitor.waitForAbort(1800):
            return


# ── Searching ────────────────────────────────────────────────────────────

def targets(meta, canonical_id, media_type, ids=None, season=None, episode=None):
    if not is_set_up() or not _index().get('files'):
        return []
    try:
        from .source_addons import skipped
        if PROVIDER_ID in skipped():
            return []      # switched off in "Add-ons searched for sources"
    except Exception:
        pass
    from .search.native_titles import candidates
    meta = meta or {}
    year = str(meta.get('year') or meta.get('releaseInfo') or meta.get('release_date')
               or meta.get('first_air_date') or '')[:4]
    return [({'id': PROVIDER_ID, 'name': 'NAS', '_title': meta.get('name') or meta.get('title') or '',
              '_titles': candidates(meta, media_type), '_year': year, '_ids': dict(ids or {}),
              '_media_type': media_type, '_enrich_titles': True,
              '_season': season, '_episode': episode}, str(canonical_id))]


def _matches(files, names, imdb, year, season, episode):
    episodic = season not in (None, '', 0, '0') and episode not in (None, '', 0, '0')
    try:
        year = int(str(year)[:4]) if year else None
    except ValueError:
        year = None
    out = []
    for row in files:
        if episodic:
            if row.get('se') != int(season) or row.get('ep') != int(episode):
                continue
            if row.get('t') in names or (imdb and row.get('imdb') == imdb):
                out.append(row)
        elif row.get('se') is None:
            if imdb and row.get('imdb') == imdb:
                out.append(row)
            elif row.get('t') in names and (not year or not row.get('y') or abs(int(row['y']) - year) <= 1):
                out.append(row)
    return out


def _size_label(size):
    if not size:
        return ''
    return '%.1f GB' % (size / 1024.0 ** 3) if size >= 1024 ** 3 else '%d MB' % (size // 1024 ** 2)


def payload(provider):
    files = _index().get('files') or []
    if not files:
        return {'streams': []}
    ids = provider.get('_ids') or {}
    imdb = str(ids.get('imdb_id') or '')
    season, episode = provider.get('_season'), provider.get('_episode')
    titles = list(provider.get('_titles') or []) + [provider.get('_title') or '']
    names = {_norm(t) for t in titles if t}
    found = _matches(files, names, imdb, provider.get('_year'), season, episode)
    if not found and ids.get('tmdb_id'):
        # an Arabic page name against an English library: TMDB's English name
        try:
            from .search.native_titles import resolve
            from . import tmdb_direct
            more = resolve(provider, provider.get('_media_type') or 'movie', tmdb_direct) or []
            names |= {_norm(t) for t in more if t}
            found = _matches(files, names, imdb, provider.get('_year'), season, episode)
        except Exception as exc:
            _log('title lookup: %s' % exc, xbmc.LOGDEBUG)
    by_id = {s['id']: s for s in shares()}
    streams = []
    for row in found:
        share = by_id.get(row.get('sh'))
        if not share:
            continue
        facts = ' · '.join(x for x in (_size_label(row.get('s')), 'ISO' if row.get('disc') else '',
                                       share.get('name')) if x)
        quality = (row.get('q') or ('2160P' if row.get('disc') and 'uhd' in (row.get('f') or '').lower() else 'SD'))
        where = share.get('host') or ''
        if not where or '/' in where:
            where = share.get('name') or ''
        stream = {'name': '[COLOR %s]NAS[/COLOR] %s\n%s' % (COLOR, where, quality.replace('P', 'p')),
                  'title': '%s\n%s' % (row.get('f') or '', facts),
                  'url': _share_url(share, row['p']),
                  'behaviorHints': {'bingeGroup': 'dexhub-nas-%s' % share['id'], 'filename': row.get('f') or ''}}
        if row.get('s'):
            stream['behaviorHints']['videoSize'] = int(row['s'])
        streams.append(stream)
    _log('%d match(es) for %s' % (len(streams), provider.get('_title') or imdb or '?'))
    return {'streams': streams}


# ── Setting up ───────────────────────────────────────────────────────────

def _share_id(root):
    import hashlib
    return hashlib.sha1(root.lower().encode('utf-8')).hexdigest()[:10]


def _ask_address():
    typed = xbmcgui.Dialog().input(_t('عنوان الـNAS (مثل 192.168.1.20 أو smb://nas/movies)',
                                      'NAS address (like 192.168.1.20 or smb://nas/movies)'))
    typed = (typed or '').strip()
    if not typed:
        return None
    if '://' not in typed and not typed.startswith('/'):
        typed = 'smb://%s/' % typed.strip('/\\')
    return typed if typed.endswith('/') else typed + '/'


def _sign_in(address):
    """Guest first; a username and password only when the NAS asks."""
    listed = _listdir(address)
    if listed is not None and (listed[0] or listed[1]):
        return '', '', listed
    dialog = xbmcgui.Dialog()
    for _attempt in range(3):
        user = dialog.input(_t('اسم المستخدم في الـNAS', 'NAS username'))
        if not user:
            return None
        password = dialog.input(_t('كلمة المرور', 'Password'), option=xbmcgui.ALPHANUM_HIDE_INPUT)
        listed = _listdir(_with_login(address, user, password))
        if listed is not None and (listed[0] or listed[1]):
            return user, password, listed
        dialog.notification('Dex Hub', _t('ما قدرت أدخل، جرب مرة ثانية', 'Could not sign in, try again'),
                            xbmcgui.NOTIFICATION_WARNING, 3000)
    return None


def _add_nas():
    dialog = xbmcgui.Dialog()
    busy = xbmcgui.DialogProgress()
    busy.create('Dex Hub', _t('أدور على الـNAS في شبكتك…', 'Looking for a NAS on your network…'))
    try:
        found = discover()
    finally:
        busy.close()
    labels = ['%s%s' % (name + ' · ' if name else '', host) + ('  (NFS)' if scheme == 'nfs' else '')
              for host, name, scheme in found]
    labels.append(_t('أكتب العنوان بنفسي', 'Type the address'))
    pick = dialog.select(_t('اختر الـNAS', 'Choose your NAS'), labels)
    if pick < 0:
        return False
    if pick == len(found):
        address = _ask_address()
        if not address:
            return False
        host = re.sub(r'^\w+://(?:[^@/]*@)?', '', address).split('/')[0] or address
    else:
        host, _name, scheme = found[pick]
        address = '%s://%s/' % (scheme, host)
    login = _sign_in(address)
    if login is None:
        return False
    user, password, (dirs, _files) = login
    at_root = address.count('/') <= 3
    if at_root:
        folders = [d for d in dirs if not d.endswith('$')]
        if not folders:
            dialog.ok('Dex Hub', _t('ما فيه مجلدات مشاركة في هذا الجهاز.', 'This device shares no folders.'))
            return False
        media = re.compile(r'movie|film|series|show|tv|media|video|anime|kids|أفلام|مسلسلات|فيديو', re.I)
        preselect = [i for i, d in enumerate(folders) if media.search(d)]
        chosen = dialog.multiselect(_t('اختر مجلدات الأفلام والمسلسلات', 'Choose your film and series folders'),
                                    folders, preselect=preselect)
        if not chosen:
            return False
        roots = [(address + folders[i] + '/', folders[i]) for i in chosen]
    else:
        roots = [(address, address.rstrip('/').rsplit('/', 1)[-1])]
    rows = shares()
    known = {r['id'] for r in rows}
    for root, name in roots:
        share_id = _share_id(root)
        if share_id in known:
            continue
        rows.append({'id': share_id, 'root': root, 'name': name, 'host': host, 'user': user, 'password': password})
    _save_shares(rows)
    return True


def _scan_now():
    progress = xbmcgui.DialogProgress()
    progress.create('Dex Hub', _t('أقرأ ملفاتك…', 'Reading your files…'))
    try:
        counted = scan(progress=progress)
    finally:
        progress.close()
    if counted is None:
        xbmcgui.Dialog().notification('Dex Hub', _t('القراءة شغالة أصلًا', 'Already reading'), xbmcgui.NOTIFICATION_INFO, 2500)
        return
    films, episodes = counted
    xbmcgui.Dialog().notification('Dex Hub', _t('الـNAS: %d فيلم و%d حلقة', 'NAS: %d films, %d episodes') % (films, episodes),
                                  xbmcgui.NOTIFICATION_INFO, 4000)


def setup():
    """Settings > Sources > NAS. With nothing set up it goes straight to adding.
    v5.10.550: the network folders already added to Kodi (its file manager or
    video sources) are taken first, signed in as Kodi signs in."""
    if import_kodi_sources():
        _scan_now()
    if not is_set_up():
        if _add_nas():
            _scan_now()
        return
    dialog = xbmcgui.Dialog()
    while True:
        rows = shares()
        if not rows:
            return
        index = _index()
        when = time.strftime('%H:%M %d/%m', time.localtime(index.get('at'))) if index.get('at') else '—'
        labels = ['%s · %s  (%s)' % (r.get('host') or '', r.get('name') or '', r.get('count', '?')) for r in rows]
        labels += [_t('➕ إضافة NAS أو مجلد', '➕ Add a NAS or folder'),
                   _t('🔄 قراءة الملفات الآن (آخر مرة %s)', '🔄 Read the files now (last %s)') % when,
                   _t('🗑 إزالة مجلد', '🗑 Remove a folder')]
        pick = dialog.select(_t('الـNAS ومجلدات الشبكة', 'NAS and network folders'), labels)
        if pick < 0:
            return
        if pick == len(rows):
            if _add_nas():
                _scan_now()
        elif pick == len(rows) + 1:
            _scan_now()
        elif pick == len(rows) + 2:
            gone = dialog.select(_t('أي مجلد تشيل؟', 'Remove which folder?'),
                                 ['%s · %s' % (r.get('host') or '', r.get('name') or '') for r in rows])
            if gone >= 0:
                if rows[gone].get('from') == 'kodi':
                    _ignore(rows[gone]['id'])       # not brought back from Kodi's sources
                del rows[gone]
                _save_shares(rows)
                _scan_now()


def add_from_phone(address, user='', password=''):
    """The phone setup page: one address (a share, or a NAS whose shares are all
    taken). Returns (ok, message)."""
    address = (address or '').strip()
    if not address:
        return False, 'empty'
    if '://' not in address and not address.startswith('/'):
        address = 'smb://%s/' % address.strip('/\\')
    if not address.endswith('/'):
        address += '/'
    listed = _listdir(_with_login(address, user, password))
    if listed is None:
        return False, 'unreachable'
    host = re.sub(r'^\w+://(?:[^@/]*@)?', '', address).split('/')[0] or address
    roots = ([(address + d + '/', d) for d in listed[0] if not d.endswith('$')] if address.count('/') <= 3
             else [(address, address.rstrip('/').rsplit('/', 1)[-1])])
    rows = shares()
    known = {r['id'] for r in rows}
    for root, name in roots:
        if _share_id(root) not in known:
            rows.append({'id': _share_id(root), 'root': root, 'name': name, 'host': host,
                         'user': user, 'password': password})
    _save_shares(rows)
    threading.Thread(target=scan, name='DexHub-nas-scan', daemon=True).start()
    return True, '%d' % len(roots)


# ── Kodi's own network folders (v5.10.550) ───────────────────────────────

def kodi_network_sources():
    """smb:// and nfs:// folders added in Kodi itself (its file manager or its
    video sources), as (root, name). Kodi keeps their passwords and signs in
    by itself, so they need no login here."""
    from urllib.parse import unquote
    found, seen = [], set()
    for media in ('video', 'files'):
        try:
            answer = json.loads(xbmc.executeJSONRPC(json.dumps(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'Files.GetSources', 'params': {'media': media}})))
        except Exception:
            continue
        for source in ((answer.get('result') or {}).get('sources') or []):
            path, label = str(source.get('file') or ''), str(source.get('label') or '')
            paths = [path]
            if path.lower().startswith('multipath://'):
                paths = [unquote(part) for part in path[len('multipath://'):].split('/') if part]
            for one in paths:
                if not one.lower().startswith(('smb://', 'nfs://')):
                    continue
                root = one if one.endswith('/') else one + '/'
                if root.lower() in seen:
                    continue
                seen.add(root.lower())
                folder = unquote(root.rstrip('/').rsplit('/', 1)[-1])
                name = label or folder
                if len(paths) > 1 and folder and folder.lower() != name.lower():
                    name = '%s · %s' % (name, folder)     # one Kodi source over several folders
                found.append((root, name))
    return found


def import_kodi_sources():
    """Kodi's network folders join the shares (once; one removed here stays
    out). Returns how many joined."""
    try:
        found = kodi_network_sources()
    except Exception as exc:
        _log('Kodi sources: %s' % exc, xbmc.LOGDEBUG)
        return 0
    if not found:
        return 0
    rows = shares()
    known = {r['id'] for r in rows} | _ignored()
    known_roots = {re.sub(r'^(\w+://)[^@/]*@', r'\1', r.get('root') or '').lower() for r in rows}
    added = 0
    for root, name in found:
        share_id = _share_id(root)
        if share_id in known or root.lower() in known_roots:
            continue
        host = re.sub(r'^\w+://(?:[^@/]*@)?', '', root).split('/')[0]
        rows.append({'id': share_id, 'root': root, 'name': name, 'host': host, 'user': '', 'password': '',
                     'from': 'kodi'})
        known.add(share_id)
        added += 1
    if added:
        _save_shares(rows)
        _log('%d network folder(s) taken from Kodi\'s sources' % added)
    return added


# ── The files as catalogs (v5.10.550) ────────────────────────────────────

KINDS = ('movie', 'series', 'recent')
_META_LOCK = threading.Lock()


def _groups(share='', kind='movie'):
    """One entry per film (name and year) or per series, newest file first
    for 'recent'."""
    files = [r for r in (_index().get('files') or []) if not share or r.get('sh') == share]
    groups = {}
    for row in files:
        series = row.get('se') is not None
        if kind == 'movie' and series or kind == 'series' and not series:
            continue
        key = ('s', row.get('t')) if series else ('m', row.get('t'), row.get('y'))
        group = groups.get(key)
        if group is None:
            group = groups[key] = {'series': series, 't': row.get('t'), 'name': row.get('name') or row.get('t'),
                                   'y': None if series else row.get('y'), 'imdb': '', 'tmdb': '', 'm': 0,
                                   'n': 0, 'order': len(groups)}
        group['imdb'] = group['imdb'] or row.get('imdb') or ''
        group['tmdb'] = group['tmdb'] or row.get('tmdb') or ''
        group['m'] = max(group['m'], int(row.get('m') or 0))
        group['n'] += 1
    out = list(groups.values())
    if kind == 'recent':
        # an index read before 5.10.550 has no file times: the last read files first
        out.sort(key=lambda g: (g['m'], g['order']), reverse=True)
    else:
        out.sort(key=lambda g: _norm(g['name']))
    return out


def _meta_key(group):
    return '%s|%s|%s' % ('tv' if group['series'] else 'movie', group['t'], group.get('y') or '')


def _match(group):
    """{tmdb, imdb, title, year, poster} from TMDB for a film or series ('' ids
    when TMDB knows no such title)."""
    from . import tmdb_direct
    kind = 'tv' if group['series'] else 'movie'
    if group.get('tmdb'):
        return {'tmdb': str(group['tmdb']), 'imdb': group.get('imdb') or ''}
    if group.get('imdb'):
        try:
            found = tmdb_direct._resolve_tmdb_from_imdb(group['imdb'], 'series' if group['series'] else 'movie')
        except Exception:
            found = ''
        if found:
            return {'tmdb': str(found), 'imdb': group['imdb']}
    params = {'query': group.get('name') or group['t']}
    if group.get('y'):
        params['first_air_date_year' if group['series'] else 'year'] = str(group['y'])
    try:
        rows = (tmdb_direct._request('/search/%s' % kind, params) or {}).get('results') or []
        if not rows and group.get('y'):
            params.pop('year', None)
            params.pop('first_air_date_year', None)
            rows = (tmdb_direct._request('/search/%s' % kind, params) or {}).get('results') or []
    except Exception as exc:
        _log('TMDB lookup of %s: %s' % (group.get('name'), exc), xbmc.LOGDEBUG)
        return None                       # not cached: asked again next time
    if not rows:
        return {'tmdb': '', 'imdb': ''}
    best = rows[0]
    dated = best.get('release_date') or best.get('first_air_date') or ''
    return {'tmdb': str(best.get('id') or ''), 'imdb': '', 'title': best.get('title') or best.get('name') or '',
            'year': dated[:4], 'poster': tmdb_direct._image_url(best['poster_path'], 'poster')
            if best.get('poster_path') else ''}


def _matched(groups):
    """TMDB's answer for every group, from nas_meta.json or asked now (six at a time)."""
    with _META_LOCK:
        cache = _read('nas_meta.json', {}) or {}
    todo = [g for g in groups if _meta_key(g) not in cache]
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            answers = list(pool.map(_match, todo))
        with _META_LOCK:
            cache = _read('nas_meta.json', {}) or {}
            for group, answer in zip(todo, answers):
                if answer is not None:
                    cache[_meta_key(group)] = answer
            _write('nas_meta.json', cache)
    return cache


def catalog(share='', kind='movie', offset=0, limit=40):
    """A page of the files as id rows for the catalog renderer
    (plugin._render_idlist_rows), and the next offset or None. A title TMDB
    does not know is left out (its file still turns up in that title's
    sources by name)."""
    groups = _groups(share, kind if kind in KINDS else 'movie')
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    page = groups[offset:offset + max(1, int(limit or 40))]
    cache = _matched(page)
    rows = []
    for group in page:
        found = cache.get(_meta_key(group)) or {}
        if not found.get('tmdb') and not found.get('imdb'):
            continue
        rows.append({'_type': 'show' if group['series'] else 'movie',
                     'title': found.get('title') or group.get('name') or '',
                     'year': found.get('year') or group.get('y') or '',
                     'poster': found.get('poster') or '',
                     'ids': {'tmdb': found.get('tmdb') or '', 'imdb': found.get('imdb') or group.get('imdb') or ''}})
    following = offset + len(page)
    return rows, (following if following < len(groups) else None)


def counts(share=''):
    """(films, series) of a share, or of every share."""
    return len(_groups(share, 'movie')), len(_groups(share, 'series'))


def warm_catalog(limit=200):
    """After a read: the newest titles are matched ahead, so the rows open with
    posters at once."""
    try:
        _matched(_groups('', 'recent')[:limit])
    except Exception as exc:
        _log('matching ahead: %s' % exc, xbmc.LOGDEBUG)


KIND_TITLES = {'movie': ('الأفلام', 'Movies'), 'series': ('المسلسلات', 'Series'),
               'recent': ('أضيف مؤخراً', 'Recently added')}


def list_params(share, kind, share_name=''):
    title = _t(*KIND_TITLES[kind])
    if share_name:
        title = '%s  ·  %s' % (title, share_name)
    return {'action': 'nas_list', 'share': share or '', 'kind': kind, 'title': title}


def server_entries(media_path=None):
    """Each share with files, as a server of the Servers tab (routes.servers_tab)."""
    from urllib.parse import urlencode
    card = media_path('lib_nas.jpg') if media_path else ''
    out = []
    for share in shares():
        films, series = counts(share['id'])
        if not films and not series:
            continue
        libs = [{'title': _t(*KIND_TITLES[kind]), 'params': list_params(share['id'], kind)}
                for kind, count in (('movie', films), ('series', series)) if count]
        tiles = [{'kind': 'library', 'title': lib['title'], 'label': lib['title'], 'hint': share.get('name') or '',
                  'shape': 'landscape', 'poster': card, 'landscape': card, 'fanart': '',
                  'params': lib['params'], 'path': 'plugin://plugin.video.dexhub/?' + urlencode(lib['params']),
                  'folder': True, 'service': 'nas', 'brand': 'NAS', 'server': share.get('name') or '',
                  'plot': _t('%d فيلم · %d مسلسل', '%d films · %d series') % (films, series)}
                 for lib in libs]
        out.append({'service': 'nas', 'brand': 'NAS', 'id': share['id'], 'name': share.get('name') or 'NAS',
                    'key': 'nas:%s' % share['id'], 'libs': libs, 'sections': [], 'tiles': tiles})
    return out


def row_choices():
    """The rows a bar section can take (homeui.bar_editor's Add rows)."""
    out = []
    every = shares()
    if len(every) > 1:
        for kind in KINDS:
            params = list_params('', kind, 'NAS')
            out.append(({'key': 'nas:all:%s' % kind, 'title': params['title'], 'sub': 'NAS', 'shape': 'poster',
                         'params': params}, _t('كل المجلدات', 'Every folder')))
    for share in every:
        films, series = counts(share['id'])
        for kind in KINDS:
            if kind == 'movie' and not films or kind == 'series' and not series or \
                    kind == 'recent' and not (films or series):
                continue
            params = list_params(share['id'], kind, share.get('name') or '')
            out.append(({'key': 'nas:%s:%s' % (share['id'], kind), 'title': params['title'], 'sub': 'NAS',
                         'shape': 'poster', 'params': params}, share.get('name') or 'NAS'))
    return out
