# -*- coding: utf-8 -*-
"""Catalogs that come without posters (v5.10.117).

Some accounts' catalogs arrive with no posters, or with a few. Stremio and
Nuvio show such a title with the metadata of another add-on of the account
(the one that knows that kind of id); Dex Hub now does the same, filling
only what is missing:

  1. a title with an IMDb id needs nothing here (the art layer takes
     Stremio's own image service for it, without a request);
  2. other ids (kitsu:, tmdb:, tvdb:, mal:, an add-on's own): the /meta of
     the account's add-ons that declare that kind of id, the catalog's own
     add-on first, as Nuvio does;
  3. what TMDb Helper already holds on the device, then TMDb itself with
     the user's key (a tmdb: id, or the title and year).

What is still missing is said once, with its cause: a short dialog names it
(no add-on of the account knows these ids, no TMDb key, the source found
nothing) and offers the fix (a metadata source for the add-on, a TMDb key).
"""
import json
import os
import re
import threading
import time

import xbmc
import xbmcgui

RESCUE_ITEMS = 24        # the first titles of a page that are looked up
RESCUE_BUDGET = 3.5      # seconds a page waits for the lookups
MIN_MISSING = 3          # a page with fewer titles without poster is fine
MIN_SHARE = 0.3          # nor one where they are under a third
ALERT_EVERY = 3 * 86400  # a catalog's dialog shows again after three days
ALERT_GAP = 600.0        # and no two dialogs within ten minutes
MISS_TTL = 6 * 3600      # a title nothing could fill is not asked again sooner

_IMDB = re.compile(r'^tt\d{5,}$')
_POSTER_KEYS = ('poster', 'posterUrl', 'posterURL', 'thumbnail', 'thumb', 'image', 'imageUrl')
_LOCK = threading.Lock()
_MISS = {}               # (provider id, title id) -> time nothing was found


def _txt(value):
    return str(value or '').strip()


def poster_of(meta):
    meta = meta or {}
    for key in _POSTER_KEYS:
        value = _txt(meta.get(key))
        if value and value.lower().startswith(('http', 'image://', 'special://')):
            return value
    images = meta.get('images')
    if isinstance(images, dict):
        for key in ('poster', 'thumb', 'cover'):
            value = images.get(key)
            if isinstance(value, list):
                value = value[0] if value else ''
            if isinstance(value, dict):
                value = value.get('url') or ''
            if _txt(value).lower().startswith('http'):
                return _txt(value)
    return ''


def imdb_of(meta):
    try:
        from .art import extract_ids
        imdb = _txt((extract_ids(meta or {}) or {}).get('imdb_id'))
    except Exception:
        imdb = ''
    return imdb if _IMDB.match(imdb) else ''


def id_kind(meta_id):
    text = _txt(meta_id)
    if _IMDB.match(text.split(':')[0]):
        return 'imdb'
    if ':' in text:
        return text.split(':', 1)[0].lower()
    return 'id'


def _declares_meta(provider, media_type, meta_id):
    manifest = (provider or {}).get('manifest') or {}
    types = manifest.get('types') or []
    prefixes = manifest.get('idPrefixes')
    for resource in manifest.get('resources') or []:
        if resource == 'meta':
            r_types, r_prefixes = types, prefixes
        elif isinstance(resource, dict) and resource.get('name') == 'meta':
            r_types = resource.get('types') or types
            r_prefixes = resource.get('idPrefixes') if 'idPrefixes' in resource else prefixes
        else:
            continue
        if r_types and media_type not in r_types:
            continue
        if r_prefixes and not any(_txt(meta_id).startswith(str(p)) for p in r_prefixes if p):
            continue
        return True
    return False


def _meta_addons(provider, media_type):
    """The account's add-ons that may answer /meta for this media type: the
    catalog's own add-on first."""
    out, seen = [], set()
    try:
        from .dexhub import store
        from . import meta_source
        others = [p for p in store.list_providers() or []
                  if meta_source._provider_looks_like_meta_source(p)]
    except Exception:
        others = []
    for candidate in [provider] + others:
        pid = _txt((candidate or {}).get('id'))
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(candidate)
    return out


def _has_tmdb_key():
    try:
        from . import tmdb_direct
        return bool(tmdb_direct._api_key())
    except Exception:
        return False


def _from_addons(api, addons, media_type, meta, tried):
    meta_id = _txt(meta.get('id'))
    if not meta_id:
        return {}
    asked = 0
    for addon in addons:
        if asked >= 2:
            break
        if not _declares_meta(addon, media_type, meta_id):
            continue
        key = (_txt(addon.get('id')), meta_id)
        with _LOCK:
            missed = _MISS.get(key)
        if missed and time.time() - missed < MISS_TTL:
            continue
        asked += 1
        tried.add(_txt(addon.get('name')) or _txt(addon.get('id')))
        try:
            data = api.fetch_meta(addon, media_type, meta_id, timeout_override=4, retry=False,
                                  rate_wait=0.5) or {}
        except Exception:
            data = {}
        found = data.get('meta') if isinstance(data, dict) else None
        if isinstance(found, dict) and (poster_of(found) or imdb_of(found)):
            return found
        with _LOCK:
            _MISS[key] = time.time()
    return {}


def _from_tmdb(meta, found, media_type):
    """TMDb Helper's database, then TMDb with the user's key."""
    try:
        from .art import extract_ids, get_art_bundle_from_db
    except Exception:
        return {}
    ids = dict(extract_ids(found or {}) or {})
    for key, value in (extract_ids(meta or {}) or {}).items():
        if value and not ids.get(key):
            ids[key] = value
    kind = 'tv' if media_type in ('series', 'anime', 'tv', 'show') else 'movie'
    title = _txt(meta.get('name') or meta.get('title') or (found or {}).get('name'))
    year = _txt(meta.get('releaseInfo') or meta.get('year') or (found or {}).get('releaseInfo'))[:4]
    bundle = {}
    try:
        bundle = get_art_bundle_from_db(tmdb_id=ids.get('tmdb_id') or '', imdb_id=ids.get('imdb_id') or '',
                                        media_type=kind, title=title, year=year) or {}
    except Exception:
        bundle = {}
    if not bundle.get('poster') and _has_tmdb_key():
        try:
            from . import tmdb_direct
            bundle = tmdb_direct.art_for(tmdb_id=ids.get('tmdb_id') or '', imdb_id=ids.get('imdb_id') or '',
                                         media_type=kind, title=title, year=year) or {}
        except Exception:
            bundle = {}
    out = {}
    if bundle.get('poster'):
        out['poster'] = bundle['poster']
    if bundle.get('fanart') or bundle.get('landscape'):
        out['background'] = bundle.get('fanart') or bundle.get('landscape')
    if bundle.get('clearlogo'):
        out['logo'] = bundle['clearlogo']
    return out


def rescue(api, provider, media_type, metas):
    """(metas, report): a page's titles without a poster filled from the
    account's metadata add-ons, TMDb Helper and TMDb (only what is missing).
    The report says what stayed missing; None when nothing was."""
    metas = list(metas or [])
    todo = [(index, meta) for index, meta in enumerate(metas[:RESCUE_ITEMS])
            if isinstance(meta, dict) and not poster_of(meta) and not imdb_of(meta)]
    checked = len([m for m in metas[:RESCUE_ITEMS] if isinstance(m, dict)])
    if not todo:
        return metas, {'checked': checked, 'missing': 0}
    addons = _meta_addons(provider, media_type)
    tried = set()

    def one(pair):
        _index, meta = pair
        found = _from_addons(api, addons, media_type, meta, tried)
        if not poster_of(found):
            imdb = imdb_of(found)
            if imdb:
                found = dict(found, poster='https://images.metahub.space/poster/large/%s/img' % imdb)
        if not poster_of(found):
            extra = _from_tmdb(meta, found, media_type)
            if extra:
                found = dict(found or {}, **extra)
        return found

    started = time.monotonic()
    try:
        results = api.run_parallel(one, todo, workers=4, timeout=RESCUE_BUDGET, lane='art')
    except Exception:
        results = []
    filled = 0
    for pair, found in results or []:
        if not isinstance(found, dict) or not found:
            continue
        index, meta = pair
        merged = dict(meta)
        for key in ('poster', 'background', 'logo', 'description', 'imdbRating', 'releaseInfo', 'genres',
                    'imdb_id'):
            if not merged.get(key) and found.get(key):
                merged[key] = found[key]
        if not merged.get('imdb_id') and imdb_of(found):
            merged['imdb_id'] = imdb_of(found)
        if poster_of(merged):
            filled += 1
        metas[index] = merged
    missing = [m for m in metas[:RESCUE_ITEMS] if isinstance(m, dict) and not poster_of(m) and not imdb_of(m)]
    kinds = sorted(set(id_kind(m.get('id')) for m in missing))
    report = {'checked': checked, 'missing': len(missing), 'filled': filled,
              'kinds': kinds, 'addons': sorted(tried), 'key': _has_tmdb_key(),
              'ms': int((time.monotonic() - started) * 1000)}
    try:
        xbmc.log('[DexHub] posters: %d of %d title(s) came without one, %d filled (%d ms)%s'
                 % (len(todo), checked, filled, report['ms'],
                    (', still missing %d (%s)' % (len(missing), ', '.join(kinds))) if missing else ''),
                 xbmc.LOGINFO)
    except Exception:
        pass
    return metas, report


# ------------------------------------------------------------- the issues
def _issues_path():
    from .dexhub.common import profile_path
    return os.path.join(profile_path(), 'poster_issues.json')


def load():
    try:
        with open(_issues_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    try:
        path = _issues_path()
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(data, ensure_ascii=False))
        os.replace(tmp, path)
    except Exception:
        pass


def issue_key(provider_id, media_type, catalog_id):
    return '%s|%s|%s' % (provider_id or '', media_type or '', catalog_id or '')


def record(provider, media_type, catalog_id, catalog_name, report):
    """Keep (or forget) a catalog's poster problem; the issue when there is one."""
    if report is None:
        return None
    pid = _txt((provider or {}).get('id'))
    key = issue_key(pid, media_type, catalog_id)
    severe = (report.get('missing', 0) >= MIN_MISSING
              and report.get('missing', 0) >= MIN_SHARE * max(1, report.get('checked', 0)))
    with _LOCK:
        data = load()
        old = data.get(key)
        if not severe:
            if old is not None:
                data.pop(key, None)
                _save(data)
            return None
        issue = dict(old or {})
        issue.update({
            'provider_id': pid,
            'provider_name': _txt((provider or {}).get('name') or ((provider or {}).get('manifest') or {}).get('name')),
            'media_type': media_type or '', 'catalog_id': catalog_id or '',
            'catalog_name': _txt(catalog_name), 'checked': report.get('checked', 0),
            'missing': report.get('missing', 0), 'kinds': report.get('kinds') or [],
            'addons': report.get('addons') or [], 'key': bool(report.get('key')), 'at': time.time(),
        })
        data[key] = issue
        if len(data) > 60:
            for stale in sorted(data, key=lambda k: data[k].get('at') or 0)[:len(data) - 60]:
                data.pop(stale, None)
        _save(data)
    return dict(issue, _key=key)


def _setting_on():
    try:
        import xbmcaddon
        return (xbmcaddon.Addon('plugin.video.dexhub').getSetting('poster_alerts') or 'true').lower() != 'false'
    except Exception:
        return True


def maybe_alert(issue):
    """Ask Kodi to show the dialog for this issue (a separate call of the
    add-on, so the listing it came from is never held)."""
    if not issue or issue.get('dismissed') or not _setting_on():
        return False
    now = time.time()
    if now - float(issue.get('alerted_at') or 0) < ALERT_EVERY:
        return False
    win = xbmcgui.Window(10000)
    try:
        last = float(win.getProperty('dexhub.posters.alerted') or 0)
    except ValueError:
        last = 0.0
    if now - last < ALERT_GAP:
        return False
    if xbmc.getCondVisibility('Player.HasVideo | System.HasActiveModalDialog | Window.IsActive(fullscreenvideo)'):
        return False
    win.setProperty('dexhub.posters.alerted', '%.0f' % now)
    xbmc.executebuiltin('RunPlugin(plugin://plugin.video.dexhub/?action=poster_doctor&key=%s)'
                        % _quote(issue.get('_key') or ''))
    return True


def _quote(text):
    from urllib.parse import quote
    return quote(str(text or ''), safe='')


def _mark(key, **fields):
    with _LOCK:
        data = load()
        if key in data:
            data[key].update(fields)
            _save(data)


def cause(issue, tr):
    """(what happened, how to fix it) in the user's language."""
    kinds = [k for k in issue.get('kinds') or [] if k not in ('imdb',)]
    kind_text = ', '.join(kinds) or tr('خاصة بالإضافة')
    addons = issue.get('addons') or []
    if not addons and not issue.get('key'):
        why = tr('الإضافة لا ترسل بوسترات، ومعرّفات عناوينها (%s) لا تعرفها أي إضافة ميتاداتا في حسابك، '
                 'ولا يوجد مفتاح TMDb.') % kind_text
        fix = tr('أدخل مفتاح TMDb (مجاني) أو اختر لهذه الإضافة مصدر ميتاداتا يعرف هذه المعرّفات.')
    elif addons and not issue.get('key'):
        why = tr('الإضافة لا ترسل بوسترات، وإضافات الميتاداتا في حسابك (%s) لم ترجع بوسترات لهذه العناوين، '
                 'ولا يوجد مفتاح TMDb.') % ', '.join(addons[:3])
        fix = tr('أدخل مفتاح TMDb (مجاني)، أو اختر مصدر ميتاداتا آخر لهذه الإضافة.')
    else:
        why = tr('الإضافة لا ترسل بوسترات، ولم يجدها TMDb ولا إضافات حسابك (معرّفات %s).') % kind_text
        fix = tr('اختر لهذه الإضافة مصدر ميتاداتا آخر (مثل TMDb Helper أو إضافة ميتاداتا من حسابك).')
    return why, fix


def show(api, key):
    """The dialog: what is missing, why, and the fix to pick."""
    tr = api.tr
    issue = load().get(key)
    if not issue:
        return None
    _mark(key, alerted_at=time.time())
    why, fix = cause(issue, tr)
    name = issue.get('catalog_name') or issue.get('catalog_id') or ''
    source = issue.get('provider_name') or ''
    heading = tr('بوسترات ناقصة • %s') % ('%s • %s' % (source, name) if source and name else (name or source))
    message = '%s\n[B]%s[/B] %s\n[B]%s[/B] %s' % (
        tr('%d من %d عنواناً وصلت بدون بوستر.') % (issue.get('missing', 0), issue.get('checked', 0)),
        tr('السبب:'), why, tr('الحل:'), fix)
    dialog = xbmcgui.Dialog()
    if not issue.get('key'):
        choice = dialog.yesnocustom(heading, message, customlabel=tr('مصدر الميتاداتا'),
                                    nolabel=tr('لاحقاً'), yeslabel=tr('مفتاح TMDb'))
        actions = {1: 'key', 2: 'source'}
    else:
        choice = dialog.yesnocustom(heading, message, customlabel=tr('لا تسألني عنه'),
                                    nolabel=tr('لاحقاً'), yeslabel=tr('مصدر الميتاداتا'))
        actions = {1: 'source', 2: 'dismiss'}
    picked = actions.get(choice)
    if picked == 'dismiss':
        _mark(key, dismissed=True)
        return None
    if picked == 'key':
        try:
            from .routes.simple_entry import tmdb_api_key_dialog
            tmdb_api_key_dialog()
        except Exception as exc:
            xbmc.log('[DexHub] posters: TMDb key dialog failed: %s' % exc, xbmc.LOGWARNING)
        if _has_tmdb_key():
            _fixed()
        return None
    if picked == 'source':
        pid = issue.get('provider_id') or ''
        if pid:
            api._meta_pick_target(pid, tr('مصدر الميتاداتا • %s') % (issue.get('provider_name') or pid))
            _fixed()
    return None


def _fixed():
    """A fix was made: the misses are asked again and the Home's rows read anew."""
    with _LOCK:
        _MISS.clear()
    try:
        from .skinui import common as C
        if C.served():
            from .skinui.actions import refresh_home
            refresh_home({}, -1)
    except Exception:
        pass


def review(api):
    """Settings: the catalogs that came without posters, each with its dialog."""
    tr = api.tr
    data = load()
    keys = sorted(data, key=lambda k: -(data[k].get('at') or 0))
    if not keys:
        xbmcgui.Dialog().ok(tr('فحص البوسترات'), tr('لا توجد كتالوجات ناقصة البوسترات حالياً.'))
        return None
    items = []
    for key in keys:
        issue = data[key]
        label = '%s • %s' % (issue.get('provider_name') or '', issue.get('catalog_name') or issue.get('catalog_id') or '')
        detail = tr('%d من %d بدون بوستر') % (issue.get('missing', 0), issue.get('checked', 0))
        if issue.get('dismissed'):
            detail += '  •  ' + tr('متجاهَل')
        item = xbmcgui.ListItem(label=label, label2=detail, offscreen=True)
        items.append(item)
    choice = xbmcgui.Dialog().select(tr('كتالوجات بدون بوسترات'), items, useDetails=True)
    if choice < 0:
        return None
    key = keys[choice]
    _mark(key, dismissed=False)
    return show(api, key)
